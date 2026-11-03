import re

import pytest

from cammitaly import config
from cammitaly.sources import mitur

CARD = """
<article class="cr-card">
  <h3 class="cr-card__title">{name}</h3>
  <div class="cr-card__meta">
    <div class="cr-meta-line"><span><svg aria-label="Distanza"><path d="M0"/></svg></span><span>400 km</span></div>
    <div class="cr-meta-line"><span><svg aria-label="Localizzazione"><path d="M0"/></svg></span><span>{regions}</span></div>
  </div>
  <ul class="cr-card__services-list">
    <li><img class="cr-ic cr-ic-service" src="segnaletica.svg" alt="Segnaletica"><span>Segnaletica</span></li>
    <li><img class="cr-ic cr-ic-service" src="credenziali.svg" alt="Credenziali"><span>Credenziali</span></li>
  </ul>
  <div class="cr-card__note">In catalogo dal 7 agosto 2026 (D. Dir. prot. prot. n. 124798/26)</div>
  <div class="cr-card__cta">
    <a class="btn" href="https://sito.example/">Visita sito web <svg></svg></a>
    {download}
  </div>
</article>
"""
ZIP_LINK = '<a class="btn" href="https://www.ministeroturismo.gov.it/wp-content/uploads/2023/06/X.zip">Scarica percorso</a>'
DISABLED_LINK = '<a class="btn disabled" href="https://www.ministeroturismo.gov.it/x/page/11/#">Scarica percorso</a>'
PAGE = '<link rel="canonical" href="https://www.ministeroturismo.gov.it/catalogo/page/11/">' + (
    CARD.format(name="Via Uno", regions="Basilicata, Calabria", download=ZIP_LINK)
    + CARD.format(name="Via Due", regions="Lazio", download=DISABLED_LINK)
)


def test_parse_listing_fixture() -> None:
    one, two = mitur.parse_listing(PAGE)
    assert one["nome"] == "Via Uno"
    assert one["regioni"] == "Basilicata, Calabria"
    assert one["url_percorso"].endswith("/2023/06/X.zip")
    assert one["sito_web"] == "https://sito.example/"
    assert two["url_percorso"] == ""  # link disattivato (href con '#'): nessuna traccia


def test_service_icons_are_read_from_alt_text_and_not_carried_to_the_next_card() -> None:
    expected = [config.MITUR_SERVICE_SIGNAGE, config.MITUR_SERVICE_CREDENTIAL]
    one, two = mitur.parse_listing(PAGE)
    assert one["servizi"] == expected
    assert two["servizi"] == expected  # non il doppio: l'elenco riparte vuoto a ogni scheda


def test_card_without_service_icons_has_an_empty_list() -> None:
    markup = re.sub(r"<ul.*?</ul>", "", PAGE, flags=re.DOTALL)
    one, two = mitur.parse_listing(markup)
    assert (one["servizi"], two["servizi"]) == ([], [])


def test_catalog_note_to_iso_date_and_decree() -> None:
    note = "In catalogo dal 7 agosto 2026 (D. Dir. prot. prot. n. 124798/26)"
    assert mitur.parse_catalog_note(note) == ("2026-08-07", "D. Dir. prot. prot. n. 124798/26")
    assert mitur.parse_catalog_note("niente") == ("", "")


def test_page_number_comes_from_canonical_link() -> None:
    assert mitur.parse_page_number(PAGE) == 11
    base = '<link rel="canonical" href="https://www.ministeroturismo.gov.it/catalogo/">'
    assert mitur.parse_page_number(base) == 1
    with pytest.raises(Exception, match="canonical"):
        mitur.parse_page_number("<html></html>")


def test_slugify() -> None:
    assert mitur.slugify("Cammino dell'Aquila: Sant’Angelo") == "cammino-dell-aquila-santangelo"


P01 = config.MITUR_PAGES_DIR / "p01.html"


# I dati sono salvati a mano e non stanno nel repo: senza il file il test non ha un input reale
@pytest.mark.skipif(not P01.exists(), reason="pagina p01.html non presente in data/raw/mitur/pagine")
def test_real_page_one() -> None:
    markup = P01.read_bytes().decode("utf-8", errors="replace")
    items = mitur.parse_listing(markup)
    assert mitur.parse_page_number(markup) == 1
    assert len(items) == 12
    by_name = {item["nome"]: item for item in items}
    cappuccini = by_name["Cammino dei cappuccini"]
    assert cappuccini["url_percorso"].endswith("/2023/06/Cammino-dei-Cappuccini.zip")
    assert cappuccini["regioni"] == "Marche"
    assert config.MITUR_SERVICE_SIGNAGE in by_name["Alta Via delle Grazie"]["servizi"]
    assert sum(bool(item["url_percorso"]) for item in items) == 11
