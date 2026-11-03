"""Guardia sugli URL, parser a profondita', cache dei zip e slug del Catalogo MiTur."""
import io
import os
import zipfile
from pathlib import Path
from typing import Any

import pytest

from cammitaly import config, http
from cammitaly.sources import mitur
from conftest import serve
from test_mitur import CARD, PAGE, ZIP_LINK

PREFIX = config.MITUR_ZIP_PREFIX


def _item(name: str, zip_url: str = "", site: str = "") -> dict[str, Any]:
    return {"nome": name, "url_percorso": zip_url, "sito_web": site}


@pytest.mark.parametrize("href", [
    PREFIX + "2023/06/X.zip",
    PREFIX + "2023/06/X.ZIP",  # il suffisso non distingue maiuscole
    PREFIX + "2023/06/X.zip?download=1",
])
def test_zip_guard_accepts_ministry_uploads(href: str) -> None:
    assert mitur.is_zip_url(href)


@pytest.mark.parametrize("href", [
    "https://evil.example/wp-content/uploads/2023/06/X.zip",  # altro host
    "https://www.ministeroturismo.gov.it.evil.example/wp-content/uploads/X.zip",
    "https://www.ministeroturismo.gov.it/catalogo/page/3/x.zip",  # percorso di pagina, non uploads
    "wp-content/uploads/2023/06/X.zip",  # relativo
    "/wp-content/uploads/2023/06/X.zip",
    PREFIX + "2023/06/pagina.html",  # sotto il prefisso ma non zip
    PREFIX + "../../x.zip",
    PREFIX + ".zip",
    "#",
    "",
])
def test_zip_guard_rejects_everything_else(href: str) -> None:
    assert not mitur.is_zip_url(href)


def _card_with_link(href: str) -> str:
    link = f'<a class="btn" href="{href}">Scarica percorso</a>'
    return '<link rel="canonical" href="https://x/">' + CARD.format(name="Via", regions="Lazio", download=link)


def test_rejected_hrefs_are_ignored_and_counted_not_fatal() -> None:
    (evil,) = mitur.parse_listing(_card_with_link("https://evil.example/uploads/X.zip"))
    (relative,) = mitur.parse_listing(_card_with_link("2023/06/X.zip"))
    (listing,) = mitur.parse_listing(_card_with_link("https://www.ministeroturismo.gov.it/catalogo/page/3/x.zip"))
    for item in (evil, relative, listing):
        assert item["url_percorso"] == ""
        assert len(item["href_scartati"]) == 1
        assert item["n_disattivati"] == 0


def test_disabled_button_is_counted_apart_from_ignored() -> None:
    _, disabled = mitur.parse_listing(PAGE)
    assert disabled["url_percorso"] == ""
    assert (disabled["n_disattivati"], disabled["href_scartati"]) == (1, [])


def test_zip_cache_name_comes_from_url_not_page_order() -> None:
    assert mitur.zip_file_name(PREFIX + "2023/06/Cammino-dei-Cappuccini.zip") == "2023_06_Cammino-dei-Cappuccini.zip"
    assert mitur.zip_file_name(PREFIX + "2024/05/Cammino%20di%20X.zip?v=2") == "2024_05_Cammino%20di%20X.zip"
    with pytest.raises(http.FetchError):
        mitur.zip_file_name("https://evil.example/X.zip")


def test_nested_div_does_not_end_the_field_early() -> None:
    markup = CARD.format(name="Via", regions="Lazio", download=ZIP_LINK).replace(
        "<span>Lazio</span>", "<span>Lazio, <div class='x'>Umbria</div>, Marche</span>"
    )
    (item,) = mitur.parse_listing('<link rel="canonical" href="https://x/">' + markup)
    assert item["regioni"] == "Lazio, Umbria, Marche"
    assert item["nota"].startswith("In catalogo dal")  # i campi successivi restano letti


def test_page_without_cards_is_an_error() -> None:
    with pytest.raises(http.FetchError, match="p99.html"):
        mitur.parse_listing("<html><body>Verifica del browser</body></html>", "p99.html")


def test_card_with_empty_name_is_an_error() -> None:
    with pytest.raises(http.FetchError, match="senza nome"):
        mitur.parse_listing(CARD.format(name="  ", regions="Lazio", download=""), "p01.html")


def test_slug_is_name_only_and_independent_of_order() -> None:
    items = [_item("Via Uno"), _item("Via Due")]
    mitur._assign_slugs(items)
    assert [i["slug"] for i in items] == ["via-uno", "via-due"]
    mitur._assign_slugs(items[::-1])
    assert {i["nome"]: i["slug"] for i in items} == {"Via Uno": "via-uno", "Via Due": "via-due"}


def test_homonyms_get_a_discriminator_from_zip_stem_or_site_host() -> None:
    first = _item("Via Sacra", PREFIX + "2023/06/Via-Sacra-A.zip")
    second = _item("Via Sacra", site="https://www.sito-b.example/percorso")
    for order in ([first, second], [second, first]):
        mitur._assign_slugs(order)
        assert first["slug"] == "via-sacra-2023-06-via-sacra-a"
        assert second["slug"] == "via-sacra-www-sito-b-example"


def test_unresolvable_homonyms_fail_instead_of_guessing() -> None:
    with pytest.raises(http.FetchError, match="duplicati"):
        mitur._assign_slugs([_item("Via Sacra"), _item("Via Sacra")])


def test_different_urls_with_same_cache_name_are_rejected() -> None:
    items = [_item("A", PREFIX + "a/b.zip"), _item("B", PREFIX + "a_b.zip")]
    with pytest.raises(http.FetchError, match="stesso nome"):
        mitur._check_unique_zip_names(items)


def _zip_payload(member: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(member, "x")
    return buffer.getvalue()


@pytest.fixture
def mitur_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "MITUR_ZIP_DIR", tmp_path / "zip")
    monkeypatch.setattr(config, "MITUR_TRACKS_DIR", tmp_path / "tracce")
    return tmp_path


def test_download_names_zip_from_url_and_extracts_to_slug(
    mitur_dirs: Path, monkeypatch: pytest.MonkeyPatch, isolated_http: list[str]
) -> None:
    serve(monkeypatch, isolated_http, body=_zip_payload("dentro/traccia.gpx"))
    item = _item("Cammino dei Cappuccini", PREFIX + "2023/06/Cammino-dei-Cappuccini.zip") | {"slug": "cammino-dei-cappuccini"}
    mitur._download_and_extract(item, refresh=False)
    assert (mitur_dirs / "zip" / "2023_06_Cammino-dei-Cappuccini.zip").exists()
    assert (mitur_dirs / "tracce" / "cammino-dei-cappuccini" / "0001_traccia.gpx").exists()
    assert (mitur_dirs / "tracce" / "cammino-dei-cappuccini" / config.ZIP_MANIFEST_FILE).exists()
    assert not (mitur_dirs / "tracce" / "cammino-dei-cappuccini.tmp").exists()


def test_new_zip_for_same_slug_triggers_a_new_extraction(
    mitur_dirs: Path, monkeypatch: pytest.MonkeyPatch, isolated_http: list[str]
) -> None:
    old = _item("Via", PREFIX + "2023/06/Via.zip") | {"slug": "via"}
    serve(monkeypatch, isolated_http, body=_zip_payload("vecchia.gpx"))
    mitur._download_and_extract(old, refresh=False)
    old_stamp = 1_000_000_000  # il contenuto estratto risale a un anno prima
    os.utime(mitur_dirs / "tracce" / "via", (old_stamp, old_stamp))
    new = _item("Via", PREFIX + "2026/01/Via.zip") | {"slug": "via"}
    serve(monkeypatch, isolated_http, body=_zip_payload("nuova.gpx"))
    mitur._download_and_extract(new, refresh=False)
    assert sorted(p.name for p in (mitur_dirs / "tracce" / "via").iterdir()) == ["0001_nuova.gpx", "manifest.csv"]


def test_cached_zip_and_folder_mean_no_request_and_no_reextraction(
    mitur_dirs: Path, monkeypatch: pytest.MonkeyPatch, isolated_http: list[str]
) -> None:
    item = _item("Via", PREFIX + "2023/06/Via.zip") | {"slug": "via"}
    serve(monkeypatch, isolated_http, body=_zip_payload("a.gpx"))
    mitur._download_and_extract(item, refresh=False)
    marker = mitur_dirs / "tracce" / "via" / "marcatore.txt"
    marker.write_text("x", encoding="utf-8")
    mitur._download_and_extract(item, refresh=False)
    assert len(isolated_http) == 1
    assert marker.exists()


P_ALL = sorted(config.MITUR_PAGES_DIR.glob(config.MITUR_PAGES_GLOB))


# I dati sono salvati a mano e non stanno nel repo: senza i file il test non ha un input reale
@pytest.mark.skipif(len(P_ALL) != 11, reason="le 11 pagine p01..p11 non sono presenti in data/raw/mitur/pagine")
def test_real_pages_card_counts() -> None:
    counts = [len(mitur.parse_listing(p.read_bytes().decode("utf-8", errors="replace"), p.name)) for p in P_ALL]
    assert counts == [12] * 10 + [7]


def test_folder_without_manifest_from_old_scheme_is_extracted_again(
    mitur_dirs: Path, monkeypatch: pytest.MonkeyPatch, isolated_http: list[str]
) -> None:
    item = _item("Via", PREFIX + "2023/06/Via.zip") | {"slug": "via"}
    serve(monkeypatch, isolated_http, body=_zip_payload("sub/a.gpx"))
    mitur._download_and_extract(item, refresh=False)
    manifest = mitur_dirs / "tracce" / "via" / config.ZIP_MANIFEST_FILE
    manifest.unlink()  # come una cartella estratta con la struttura originale
    mitur._download_and_extract(item, refresh=False)
    assert manifest.exists()
    assert len(isolated_http) == 1  # lo zip e' in cache: nessuna nuova richiesta


def test_file_stats_ignore_the_manifest(mitur_dirs: Path, monkeypatch: pytest.MonkeyPatch, isolated_http: list[str]) -> None:
    item = _item("Via", PREFIX + "2023/06/Via.zip") | {"slug": "via"}
    serve(monkeypatch, isolated_http, body=_zip_payload("sub/a.gpx"))
    mitur._download_and_extract(item, refresh=False)
    assert mitur._file_stats("via") == (1, "gpx:1")
