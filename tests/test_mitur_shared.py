"""Zip condivisi da piu' schede e note per riga del CSV MiTur."""
from typing import Any

import pytest

from cammitaly import config
from cammitaly.sources import mitur

PREFIX = config.MITUR_ZIP_PREFIX


def _card(name: str, zip_name: str = "", scartati: list[str] | None = None) -> dict[str, Any]:
    return {
        "nome": name, "url_percorso": PREFIX + zip_name if zip_name else "", "sito_web": "",
        "href_scartati": scartati or [], "condiviso_con": [], "servizi": [],
    }


def _resolved(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mitur._resolve_shared_zips(cards)
    mitur._assign_slugs(cards)
    return cards


def test_shared_zip_goes_to_the_card_matching_the_file_stem_in_any_order() -> None:
    zip_name = "2026/08/26_102458-Georeferenziazione-Cammino-delle-Sette-Sorelle.zip"
    for order in (("Cammino degli Eremi", "Cammino delle Sette Sorelle"), ("Cammino delle Sette Sorelle", "Cammino degli Eremi")):
        eremi, sorelle = sorted(_resolved([_card(n, zip_name) for n in order]), key=lambda c: c["nome"])
        assert sorelle["url_percorso"].endswith("Sette-Sorelle.zip")
        assert eremi["url_percorso"] == ""
        assert mitur._row_note(sorelle) == ""
        assert mitur._row_note(eremi) == "zip condiviso con cammino-delle-sette-sorelle: percorso non attribuito"


def test_unclear_match_keeps_zip_on_first_card_and_notes_both() -> None:
    first, second = _resolved([_card("Via Alfa", "2024/01/Percorso.zip"), _card("Via Beta", "2024/01/Percorso.zip")])
    assert first["url_percorso"].endswith("Percorso.zip")
    assert second["url_percorso"] == ""
    assert mitur._row_note(first) == "zip condiviso con via-beta: percorso non attribuito"
    assert mitur._row_note(second) == "zip condiviso con via-alfa: percorso non attribuito"


def test_unshared_zip_has_no_note() -> None:
    (card,) = _resolved([_card("Via Uno", "2024/01/Via-Uno.zip")])
    assert card["url_percorso"] != ""
    assert mitur._row_note(card) == ""


def test_external_hrefs_become_visible_notes() -> None:
    external = _card("Via Esterna", scartati=["https://gigamail.example.org/getfile/x.zip"])
    wrong_path = _card("Via Sbagliata", scartati=[PREFIX.replace("wp-content/uploads/", "catalogo/") + "x.zip"])
    _resolved([external, wrong_path])
    assert mitur._row_note(external) == "percorso su host esterno: gigamail.example.org"
    assert mitur._row_note(wrong_path).startswith("link di download non ammesso: ")


def test_csv_row_carries_the_note_column(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MITUR_TRACKS_DIR", tmp_path / "tracce")  # type: ignore[operator]
    card = _card("Via Esterna", scartati=["https://gigamail.example.org/x.zip"]) | {
        "nota": "", "regioni": "Lazio", "pagina": 1,
    }
    _resolved([card])
    row = mitur._csv_row(card)
    assert set(row) == set(mitur.FIELDS)
    assert row["nota"] == "percorso su host esterno: gigamail.example.org"


def test_csv_row_joins_the_service_icons_with_the_list_separator(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(config, "MITUR_TRACKS_DIR", tmp_path / "tracce")  # type: ignore[operator]
    base = {"nota": "", "regioni": "Lazio", "pagina": 1}
    with_icons = _card("Via Uno") | base | {"servizi": ["Segnaletica", "Credenziali"]}
    without = _card("Via Due") | base
    _resolved([with_icons, without])
    assert mitur._csv_row(with_icons)["servizi"] == "Segnaletica|Credenziali"
    assert mitur._csv_row(without)["servizi"] == ""


P_ALL = sorted(config.MITUR_PAGES_DIR.glob(config.MITUR_PAGES_GLOB))


# I dati sono salvati a mano e non stanno nel repo: senza i file il test non ha un input reale
@pytest.mark.skipif(len(P_ALL) != 11, reason="le 11 pagine p01..p11 non sono presenti in data/raw/mitur/pagine")
def test_real_pages_keep_all_127_cards_and_both_reassignments() -> None:
    items = mitur._read_items()
    mitur._resolve_shared_zips(items)
    mitur._assign_slugs(items)
    by_name = {item["nome"]: item for item in items}
    assert len(items) == 127
    assert sum(bool(item["url_percorso"]) for item in items) == 99
    assert by_name["Cammino delle Sette Sorelle"]["url_percorso"].endswith("Sette-Sorelle.zip")
    assert by_name["Cammino di San Michele"]["url_percorso"].endswith("Cammino-di-San-Michele.zip")
    assert by_name["Cammino degli Eremi"]["url_percorso"] == ""
    assert by_name["Cammino di Padre Giovanni"]["url_percorso"] == ""
    assert sum("host esterno" in mitur._row_note(item) for item in items) == 3
