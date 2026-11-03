import csv
import urllib.request
import zipfile
from http.client import IncompleteRead
from pathlib import Path

import pytest

from cammitaly import config, files, http
from conftest import serve

URL = "https://example.invalid/a.json"


def test_existing_dest_means_no_request(tmp_path: Path) -> None:
    dest = tmp_path / "a.json"
    dest.write_bytes(b"gia' qui")
    # _open della fixture solleva se viene chiamato: nessuna richiesta deve partire
    assert http.fetch(URL, dest, config.HOST_S3) == dest
    assert dest.read_bytes() == b"gia' qui"
    assert not config.REQUESTS_LOG.exists()


def test_fetch_writes_atomically_and_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_http: list[str]) -> None:
    serve(monkeypatch, isolated_http, body=b"contenuto")
    dest = tmp_path / "sub" / "a.json"
    http.fetch(URL, dest, config.HOST_S3)
    assert dest.read_bytes() == b"contenuto"
    assert not list(dest.parent.glob("*.part"))
    rows = list(csv.DictReader(config.REQUESTS_LOG.open(encoding="utf-8")))
    assert [(r["host_key"], r["url"], r["status"], r["bytes"]) for r in rows] == [(config.HOST_S3, URL, "200", "9")]


def test_refresh_downloads_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_http: list[str]) -> None:
    serve(monkeypatch, isolated_http, body=b"nuovo")
    dest = tmp_path / "a.json"
    dest.write_bytes(b"vecchio")
    http.fetch(URL, dest, config.HOST_S3, refresh=True)
    assert dest.read_bytes() == b"nuovo"


@pytest.mark.parametrize("code", [403, 429, 503])
def test_blocking_status_stops_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_http: list[str], code: int) -> None:
    serve(monkeypatch, isolated_http, error_code=code)
    with pytest.raises(http.FetchError, match=str(code)):
        http.fetch(URL, tmp_path / "a.json", config.HOST_S3)
    assert not (tmp_path / "a.json").exists()


def test_request_cap_stops_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_http: list[str]) -> None:
    serve(monkeypatch, isolated_http)
    http.fetch(URL + "1", tmp_path / "1", config.HOST_S3)
    http.fetch(URL + "2", tmp_path / "2", config.HOST_S3)
    with pytest.raises(http.FetchError, match="tetto"):
        http.fetch(URL + "3", tmp_path / "3", config.HOST_S3)
    assert len(isolated_http) == 2


def _log_rows() -> list[dict[str, str]]:
    return list(csv.DictReader(config.REQUESTS_LOG.open(encoding="utf-8")))


def test_cap_adds_no_log_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_http: list[str]) -> None:
    serve(monkeypatch, isolated_http)
    for n in (1, 2):
        http.fetch(URL + str(n), tmp_path / str(n), config.HOST_S3)
    with pytest.raises(http.FetchError, match="tetto"):
        http.fetch(URL + "3", tmp_path / "3", config.HOST_S3)
    assert len(_log_rows()) == 2  # la richiesta oltre il tetto non parte e non si registra


@pytest.mark.parametrize("failure", [ConnectionResetError("reset"), IncompleteRead(b"abc", 10)])
def test_mid_stream_failure_is_error_not_200(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_http: list[str], failure: Exception
) -> None:
    serve(monkeypatch, isolated_http, body=b"0123456789", fail_after=4, fail_with=failure)
    dest = tmp_path / "a.json"
    with pytest.raises(http.FetchError):
        http.fetch(URL, dest, config.HOST_S3)
    assert not dest.exists()
    assert not (tmp_path / "a.json.part").exists()
    (row,) = _log_rows()
    assert (row["status"], row["bytes"]) == ("errore", "4")


def test_content_length_mismatch_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_http: list[str]) -> None:
    serve(monkeypatch, isolated_http, body=b"0123456789", declared_length=100)
    dest = tmp_path / "a.json"
    with pytest.raises(http.FetchError, match="Content-Length"):
        http.fetch(URL, dest, config.HOST_S3)
    assert not dest.exists()
    assert not (tmp_path / "a.json.part").exists()
    (row,) = _log_rows()
    assert (row["status"], row["bytes"]) == ("errore", "10")


@pytest.mark.parametrize("code", [301, 302, 307, 308])
def test_redirect_handler_raises_on_any_3xx(code: int) -> None:
    handler = http._NoRedirect()
    request = urllib.request.Request(URL)
    with pytest.raises(http.RedirectBlockedError) as caught:
        handler.redirect_request(request, None, code, "Moved", {}, "https://altro.example/")
    assert caught.value.code == code
    assert any(isinstance(h, http._NoRedirect) for h in http._OPENER.handlers)


def test_redirect_is_logged_with_its_3xx_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def redirecting(request: object, timeout: float) -> None:
        raise http.RedirectBlockedError(302, URL, "https://altro.example/")

    monkeypatch.setattr(http, "_open", redirecting)
    with pytest.raises(http.FetchError, match="302"):
        http.fetch(URL, tmp_path / "a.json", config.HOST_S3)
    assert [r["status"] for r in _log_rows()] == ["302"]
    assert not (tmp_path / "a.json").exists()


def test_validate_rejection_leaves_no_dest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_http: list[str]) -> None:
    serve(monkeypatch, isolated_http, body=b"corrotto")
    dest = tmp_path / "a.pbf"
    seen: list[bytes] = []

    def reject(part: Path) -> None:
        seen.append(part.read_bytes())  # il controllo vede il .part completo, prima della promozione
        raise http.FetchError("md5 errato")

    with pytest.raises(http.FetchError, match="md5"):
        http.fetch(URL, dest, config.HOST_S3, validate=reject)
    assert seen == [b"corrotto"]
    assert not dest.exists()
    assert not (tmp_path / "a.pbf.part").exists()


def test_validate_acceptance_promotes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_http: list[str]) -> None:
    serve(monkeypatch, isolated_http, body=b"buono")
    dest = tmp_path / "a.pbf"
    http.fetch(URL, dest, config.HOST_S3, validate=lambda part: None)
    assert dest.read_bytes() == b"buono"


def _zip_with(path: Path, member: str) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(member, "x")


@pytest.mark.parametrize("member", ["../fuori.gpx", "/assoluto.gpx", "a/../../fuori.gpx", "C:/disco.gpx", "a\\..\\..\\fuori.gpx"])
def test_zip_slip_is_rejected(tmp_path: Path, member: str) -> None:
    archive = tmp_path / "t.zip"
    _zip_with(archive, member)
    with pytest.raises(files.UnsafeArchiveError):
        files.safe_extract(archive, tmp_path / "out")
    assert not (tmp_path / "fuori.gpx").exists()


def test_safe_extract_skips_macos_metadata(tmp_path: Path) -> None:
    archive = tmp_path / "t.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("cartella/traccia.gpx", "x")
        z.writestr("__MACOSX/cartella/._traccia.gpx", "x")
    extracted = files.safe_extract(archive, tmp_path / "out")
    assert [p.name for p in extracted] == ["traccia.gpx"]
