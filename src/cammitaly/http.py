"""Download cortese: cache su disco, pausa per host, tetto di richieste, registro CSV."""
import csv
import os
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
from typing import IO

from cammitaly import config


class FetchError(RuntimeError):
    """Errore che ferma l'esecuzione: nessun tentativo ripetuto, per scelta."""


# Stato per host della singola esecuzione: ultimo istante di richiesta e conteggio
_last_request_at: dict[str, float] = {}
_request_count: dict[str, int] = {}
# Tetto della sola esecuzione corrente, al posto di quello fisso di config.HOSTS (vedi set_request_cap)
_request_cap: dict[str, int] = {}


def set_request_cap(host_key: str, remaining: int) -> None:
    """Tetto per questa esecuzione: le richieste gia' fatte a host_key piu' `remaining`."""
    _request_cap[host_key] = _request_count.get(host_key, 0) + remaining


class RedirectBlockedError(FetchError):
    """Il server ha risposto con un reindirizzamento 3xx, che non si segue."""

    def __init__(self, code: int, url: str, target: str) -> None:
        super().__init__(f"HTTP {code} da {url} verso {target}: reindirizzamento non seguito")
        self.code = code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # Un redirect seguito sarebbe una richiesta senza pausa, senza registro e magari verso un altro host
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise RedirectBlockedError(code, req.full_url, newurl)


_OPENER = urllib.request.build_opener(_NoRedirect)


def _open(request: urllib.request.Request, timeout: float) -> IO[bytes]:
    # Punto unico di rete: i test lo sostituiscono per non uscire mai su Internet
    return _OPENER.open(request, timeout=timeout)


def _last_logged_request(host_key: str) -> float:
    # Il registro rende la pausa valida anche tra due esecuzioni ravvicinate del programma
    if not config.REQUESTS_LOG.exists():
        return 0.0
    last = 0.0
    with config.REQUESTS_LOG.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["host_key"] == host_key:
                last = datetime.fromisoformat(row["utc_iso"]).timestamp()
    return last


def _wait_turn(host_key: str) -> None:
    policy = config.HOSTS[host_key]
    done = _request_count.get(host_key, 0)
    cap = _request_cap.get(host_key, policy.max_requests)
    if done >= cap:
        raise FetchError(f"tetto di {cap} richieste raggiunto per {host_key}")
    if host_key not in _last_request_at:
        _last_request_at[host_key] = _last_logged_request(host_key)
    pause = policy.min_interval_s - (time.time() - _last_request_at[host_key])
    if pause > 0:
        time.sleep(pause)
    _request_count[host_key] = done + 1


def _log_request(host_key: str, url: str, status: int | str, size: int, seconds: float) -> None:
    config.REQUESTS_LOG.parent.mkdir(parents=True, exist_ok=True)
    is_new = not config.REQUESTS_LOG.exists()
    with config.REQUESTS_LOG.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if is_new:
            writer.writerow(config.REQUESTS_LOG_FIELDS)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        writer.writerow([stamp, host_key, url, status, size, f"{seconds:.2f}"])


def _request(url: str, method: str) -> urllib.request.Request:
    return urllib.request.Request(url, method=method, headers={"User-Agent": config.USER_AGENT})


@dataclass
class _Transfer:
    size: int = 0  # byte ricevuti finora: resta valido anche se il flusso si interrompe


def _stream_to(response: IO[bytes], tmp: Path, transfer: _Transfer) -> None:
    with tmp.open("wb") as out:
        while chunk := response.read(config.CHUNK_BYTES):
            out.write(chunk)
            transfer.size += len(chunk)


def _check_length(url: str, headers: dict[str, str], received: int) -> None:
    # urllib restituisce in silenzio un corpo piu' corto di Content-Length: lo si controlla qui
    declared = headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) != received:
        raise FetchError(f"download troncato da {url}: Content-Length {declared}, ricevuti {received} byte")


def _perform(url: str, host_key: str, method: str, tmp: Path | None) -> tuple[int, int, dict[str, str]]:
    """Esegue una richiesta paced e registrata; ritorna (status, byte scaricati, intestazioni)."""
    _wait_turn(host_key)
    started = time.time()
    status: int | str = "errore"  # diventa il codice reale solo a trasferimento completo e verificato
    transfer = _Transfer()
    try:
        with _open(_request(url, method), config.REQUEST_TIMEOUT_S) as response:
            headers = {k.lower(): v for k, v in response.headers.items()}
            if tmp is not None:
                _stream_to(response, tmp, transfer)
                _check_length(url, headers, transfer.size)
            status = response.status
    except RedirectBlockedError as exc:
        status = exc.code
        raise
    except urllib.error.HTTPError as exc:
        status = exc.code
        raise FetchError(f"HTTP {exc.code} da {url}: esecuzione fermata") from exc
    except (OSError, HTTPException) as exc:  # URLError e IncompleteRead rientrano qui
        status = "timeout" if isinstance(exc, (socket.timeout, TimeoutError)) else "errore"
        raise FetchError(f"richiesta fallita per {url}: {exc}") from exc
    finally:
        _last_request_at[host_key] = time.time()
        _log_request(host_key, url, status, transfer.size, time.time() - started)
    if status != 200:
        raise FetchError(f"HTTP {status} inatteso da {url}")
    return status, transfer.size, headers


def fetch(
    url: str, dest: Path, host_key: str, refresh: bool = False, validate: Callable[[Path], None] | None = None
) -> Path:
    """Scarica url in dest; se dest esiste e non e' refresh non fa nessuna richiesta.

    validate riceve il file .part e solleva FetchError se e' da scartare: dest non viene creato.
    """
    if dest.exists() and not refresh:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    try:
        _perform(url, host_key, "GET", tmp)
        if validate is not None:
            validate(tmp)
        os.replace(tmp, dest)  # scrittura atomica: un download interrotto non lascia file parziali
    finally:
        tmp.unlink(missing_ok=True)
    return dest


def head(url: str, host_key: str) -> dict[str, str]:
    """Una richiesta HEAD paced e registrata; ritorna le intestazioni in minuscolo."""
    return _perform(url, host_key, "HEAD", None)[2]
