"""Tetto di richieste a S3: segue la dimensione dell'elenco Webmapp, senza perdere la protezione da cicli fuori controllo."""
import json
from pathlib import Path

import pytest

from cammitaly import config, http
from cammitaly.sources import webmapp
from conftest import FakeResponse


@pytest.fixture
def webmapp_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "WEBMAPP_RAW_DIR", tmp_path)
    monkeypatch.setattr(config, "WEBMAPP_TRACKS_DIR", tmp_path / "tracks")
    monkeypatch.setattr(config, "WEBMAPP_CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "WEBMAPP_TRACKS_CSV", tmp_path / "webmapp_tracks.csv")
    monkeypatch.setitem(config.HOSTS, config.HOST_MAPHUB, config.HostPolicy(0.0, 5))
    monkeypatch.setattr(http, "_request_cap", {})
    return tmp_path


def _serve_listing(monkeypatch: pytest.MonkeyPatch, n_tracks: int) -> list[str]:
    listing = json.dumps({"hits": [{"id": n} for n in range(n_tracks)]}).encode()
    requested: list[str] = []

    def fake_open(request: object, timeout: float) -> FakeResponse:
        url = request.full_url  # type: ignore[attr-defined]
        requested.append(url)
        return FakeResponse(listing if url == config.WEBMAPP_LISTING_URL else b"{}")

    monkeypatch.setattr(http, "_open", fake_open)
    return requested


def test_cap_follows_listing_size_beyond_the_static_policy(webmapp_dirs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # La policy statica di S3 nei test e' 2: 5 tracce + configurazione la superano, l'elenco allarga il tetto
    requested = _serve_listing(monkeypatch, 5)
    webmapp.run()
    assert sum(url.startswith("https://wmfe.") for url in requested) == 6
    assert http._request_cap[config.HOST_S3] == 1 + 5 + config.WEBMAPP_REQUEST_MARGIN


def test_cap_counts_requests_already_made_in_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http, "_request_cap", {})
    monkeypatch.setattr(http, "_request_count", {config.HOST_S3: 3})
    http.set_request_cap(config.HOST_S3, 10)
    assert http._request_cap[config.HOST_S3] == 13


def test_runaway_loop_still_stops_at_the_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http, "_request_cap", {})
    monkeypatch.setattr(http, "_open", lambda request, timeout: FakeResponse(b"x"))
    http.set_request_cap(config.HOST_S3, 3)
    for n in range(3):
        http.fetch(f"https://example.test/{n}", tmp_path / f"{n}.bin", config.HOST_S3)
    with pytest.raises(http.FetchError, match="tetto di 3 richieste"):
        http.fetch("https://example.test/4", tmp_path / "4.bin", config.HOST_S3)


def test_without_override_the_static_policy_applies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http, "_request_cap", {})
    monkeypatch.setattr(http, "_open", lambda request, timeout: FakeResponse(b"x"))
    for n in range(2):
        http.fetch(f"https://example.test/{n}", tmp_path / f"{n}.bin", config.HOST_S3)
    with pytest.raises(http.FetchError, match="tetto di 2 richieste"):
        http.fetch("https://example.test/2", tmp_path / "2.bin", config.HOST_S3)
