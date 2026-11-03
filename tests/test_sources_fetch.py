"""istat, osm, camminiditalia e webmapp: cache onesta, validazione prima della cache, paginazione."""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cammitaly import config, http
from cammitaly.sources import camminiditalia, istat, osm, webmapp
from conftest import FakeResponse


def test_istat_note_uses_cached_file_date_not_today(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    zip_path = tmp_path / "Limiti01012026.zip"
    zip_path.write_bytes(b"zip")
    stamp = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc).timestamp() - 40 * 86400
    os.utime(zip_path, (stamp, stamp))
    monkeypatch.setattr(config, "ISTAT_SOURCE_FILE", tmp_path / "SOURCE.txt")
    istat._write_source_note("https://www.istat.it/x.zip", zip_path)
    note = (tmp_path / "SOURCE.txt").read_text(encoding="utf-8")
    assert "scaricato_il: 2026-09-24" in note
    assert f"sha256: {hashlib.sha256(b'zip').hexdigest()}" in note


@pytest.fixture
def osm_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "OSM_RAW_DIR", tmp_path)
    monkeypatch.setattr(config, "OSM_PBF_FILE", tmp_path / "italy.osm.pbf")
    monkeypatch.setattr(config, "OSM_MD5_FILE", tmp_path / "italy.osm.pbf.md5")
    monkeypatch.setitem(config.HOSTS, config.HOST_GEOFABRIK, config.HostPolicy(0.0, 5))
    return tmp_path


def _serve_by_url(monkeypatch: pytest.MonkeyPatch, bodies: dict[str, bytes]) -> None:
    monkeypatch.setattr(http, "_open", lambda request, timeout: FakeResponse(bodies[request.full_url]))


def test_osm_md5_mismatch_leaves_no_cached_pbf(osm_files: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _serve_by_url(monkeypatch, {
        config.OSM_MD5_URL: b"0" * 32 + b"  italy-latest.osm.pbf\n",
        config.OSM_PBF_URL: b"contenuto corrotto",
    })
    with pytest.raises(http.FetchError, match="md5"):
        osm.run(None, False)
    assert not config.OSM_PBF_FILE.exists()
    assert not list(osm_files.glob("*.part"))


def test_osm_md5_match_is_cached(osm_files: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    body = b"pbf corretto"
    md5 = hashlib.md5(body).hexdigest().encode()
    _serve_by_url(monkeypatch, {config.OSM_MD5_URL: md5 + b"  x\n", config.OSM_PBF_URL: body})
    osm.run(None, False)
    assert config.OSM_PBF_FILE.read_bytes() == body


def _page(count: int) -> bytes:
    return json.dumps([{"id": n} for n in range(count)]).encode()


def test_camminiditalia_exact_multiple_of_page_size_does_not_request_extra_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "CDI_RAW_DIR", tmp_path)
    requested: list[tuple[str, str]] = []

    def fake_open(request: object, timeout: float) -> FakeResponse:
        url, method = request.full_url, request.get_method()  # type: ignore[attr-defined]
        requested.append((method, url))
        response = FakeResponse(b"" if method == "HEAD" else _page(config.CDI_PAGE_SIZE))
        response.headers = {config.CDI_TOTAL_PAGES_HEADER: "2"}
        return response

    monkeypatch.setattr(http, "_open", fake_open)
    items = camminiditalia._download_pages(refresh=False)
    assert len(items) == 2 * config.CDI_PAGE_SIZE
    assert [m for m, _ in requested] == ["HEAD", "GET", "GET"]  # niente pagina 3, che darebbe 400


def test_camminiditalia_fully_cached_pages_need_no_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "CDI_RAW_DIR", tmp_path)
    (tmp_path / "cammini_p01.json").write_bytes(_page(config.CDI_PAGE_SIZE))
    (tmp_path / "cammini_p02.json").write_bytes(_page(7))
    assert len(camminiditalia._download_pages(refresh=False)) == config.CDI_PAGE_SIZE + 7
    assert not config.REQUESTS_LOG.exists()


def test_camminiditalia_missing_total_pages_header_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "CDI_RAW_DIR", tmp_path)
    response = FakeResponse(b"")
    response.headers = {}
    monkeypatch.setattr(http, "_open", lambda request, timeout: response)
    with pytest.raises(http.FetchError, match="x-wp-totalpages"):
        camminiditalia._download_pages(refresh=False)


@pytest.mark.parametrize("bad_id", ["../fuori", "12", 1.5, None, True])
def test_webmapp_track_ids_must_be_integers(bad_id: object) -> None:
    with pytest.raises(http.FetchError, match="non interi"):
        webmapp._track_ids([{"id": 1}, {"id": bad_id}])


def test_webmapp_integer_ids_pass() -> None:
    assert webmapp._track_ids([{"id": 1}, {"id": 22}]) == [1, 22]
