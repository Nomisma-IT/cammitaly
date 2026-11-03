"""Fixture comuni: nessun test deve toccare la rete ne' la cartella data/ reale."""
import io
import urllib.error
from pathlib import Path

import pytest

from cammitaly import config, http


class FakeResponse:
    """Risposta finta; fail_after interrompe il flusso con fail_with dopo N byte, declared_length mente."""

    def __init__(
        self, body: bytes = b"", status: int = 200, declared_length: int | None = None,
        fail_after: int | None = None, fail_with: Exception | None = None,
    ) -> None:
        self.status = status
        self.headers = {"Content-Length": str(len(body) if declared_length is None else declared_length)}
        self._stream = io.BytesIO(body)
        self._fail_after = fail_after
        self._fail_with = fail_with or ConnectionResetError("connessione chiusa dal server")

    def read(self, size: int = -1) -> bytes:
        if self._fail_after is not None and self._stream.tell() >= self._fail_after:
            raise self._fail_with
        if self._fail_after is not None:
            size = self._fail_after - self._stream.tell()
        return self._stream.read(size)

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


@pytest.fixture(autouse=True)
def isolated_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Registro in tmp, pause azzerate, stato del limitatore pulito; ritorna gli URL richiesti."""
    monkeypatch.setattr(config, "REQUESTS_LOG", tmp_path / "requests_log.csv")
    monkeypatch.setitem(config.HOSTS, config.HOST_S3, config.HostPolicy(0.0, 2))
    monkeypatch.setitem(config.HOSTS, config.HOST_MITUR, config.HostPolicy(0.0, 5))
    monkeypatch.setitem(config.HOSTS, config.HOST_CDI, config.HostPolicy(0.0, 6))
    monkeypatch.setattr(http, "_last_request_at", {})
    monkeypatch.setattr(http, "_request_count", {})
    requested: list[str] = []

    def fail_on_network(request: object, timeout: float) -> FakeResponse:
        raise AssertionError("rete non consentita nei test")

    monkeypatch.setattr(http, "_open", fail_on_network)
    return requested


def serve(
    monkeypatch: pytest.MonkeyPatch, requested: list[str], body: bytes = b"x",
    error_code: int | None = None, **response_options: object,
) -> None:
    """Sostituisce l'apertura di rete con una risposta finta, registrando gli URL."""

    def fake_open(request: object, timeout: float) -> FakeResponse:
        url = request.full_url  # type: ignore[attr-defined]
        requested.append(url)
        if error_code is not None:
            raise urllib.error.HTTPError(url, error_code, "errore finto", {}, None)  # type: ignore[arg-type]
        return FakeResponse(body, **response_options)  # type: ignore[arg-type]

    monkeypatch.setattr(http, "_open", fake_open)
