"""camminiditalia.org: catalogo dei cammini e relative tappe via REST WordPress."""
import html
import json
import re
from collections.abc import Iterator
from typing import Any

from cammitaly import config, files, http

CAMMINI_FIELDS = [
    "id", "slug", "titolo", "link", "modified", "lunghezza_km", "n_tappe", "partenza",
    "arrivo", "tipologia", "portata", "regioni", "webmapp_layer",
]
TAPPE_FIELDS = ["cammino_id", "ordine", "titolo", "partenza", "arrivo", "distanza_km"]

_TAG = re.compile(r"<[^>]+>")
_BREAK = re.compile(r"<br\s*/?>|</p>|</h\d>", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def _plain_lines(markup: str) -> str:
    # I <br> separano le voci "Partenza/Arrivo/Distanza": vanno trasformati in a capo prima di togliere i tag
    return html.unescape(_TAG.sub("", _BREAK.sub("\n", markup))).replace("\xa0", " ")


def _label_value(text: str, label: str) -> str:
    match = re.search(rf"^\s*{label}\s*:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
    return match.group(1).strip() if match else ""


def _number(text: str) -> str:
    match = _NUMBER.search(text)
    return match.group(0).replace(",", ".") if match else ""


def parse_tappa_text(markup: str) -> dict[str, str]:
    """Estrae partenza, arrivo e distanza (km, punto decimale) dal testo HTML di una tappa."""
    text = _plain_lines(markup)
    return {
        "partenza": _label_value(text, "Partenza"),
        "arrivo": _label_value(text, "Arrivo"),
        "distanza_km": _number(_label_value(text, "Distanza")),
    }


def _total_pages() -> int:
    # Una HEAD sulla pagina 1 dice quante pagine esistono: oltre l'ultima il server risponde 400
    headers = http.head(config.CDI_PAGE_URL.format(page=1), config.HOST_CDI)
    value = headers.get(config.CDI_TOTAL_PAGES_HEADER, "")
    if not value.isdigit():
        raise http.FetchError(f"intestazione {config.CDI_TOTAL_PAGES_HEADER} assente o non numerica: {value!r}")
    return int(value)


def _download_pages(refresh: bool) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    total: int | None = None  # si chiede solo se serve scaricare una pagina: con la cache nessuna richiesta
    page = 1
    while True:
        dest = config.CDI_RAW_DIR / f"cammini_p{page:02d}.json"
        if refresh or not dest.exists():
            total = _total_pages() if total is None else total
            if page > total:
                return items
            http.fetch(config.CDI_PAGE_URL.format(page=page), dest, config.HOST_CDI, refresh)
        batch = json.loads(dest.read_text(encoding="utf-8"))
        items.extend(batch)
        if len(batch) < config.CDI_PAGE_SIZE:  # ultima pagina: meno elementi del massimo
            return items
        page += 1


def _cammino_row(item: dict[str, Any]) -> dict[str, str]:
    acf = item.get("acf") or {}
    scheda = acf.get("scheda_tecnica") or {}
    iframe = (acf.get("maps") or {}).get("map") or ""
    layer = re.search(config.CDI_LAYER_PATTERN, iframe)
    prefix = config.CDI_REGION_CLASS_PREFIX
    regions = [c[len(prefix):] for c in item.get("class_list", []) if c.startswith(prefix)]
    return {
        "id": item["id"],
        "slug": item["slug"],
        "titolo": html.unescape(item["title"]["rendered"]),
        "link": item["link"],
        "modified": item["modified"],
        "lunghezza_km": _number(str(scheda.get("lunghezza", ""))),
        "n_tappe": scheda.get("n_tappe", ""),
        "partenza": scheda.get("partenza", ""),
        "arrivo": scheda.get("arrivo", ""),
        "tipologia": scheda.get("tipologia", ""),
        "portata": scheda.get("portata", ""),
        "regioni": "|".join(regions),
        "webmapp_layer": layer.group(1) if layer else "",
    }


def _tappe_rows(item: dict[str, Any]) -> Iterator[dict[str, Any]]:
    tappe = ((item.get("acf") or {}).get("tappe") or {}).get("tappa") or []
    for order, tappa in enumerate(tappe, start=1):
        parsed = parse_tappa_text(tappa.get("text") or "")
        yield {"cammino_id": item["id"], "ordine": order, "titolo": tappa.get("title", ""), **parsed}


def run(limit: int | None = None, refresh: bool = False) -> None:
    # limit non si applica: il catalogo intero costa solo 2 richieste
    items = _download_pages(refresh)
    n_cammini = files.write_csv(config.CDI_CAMMINI_CSV, CAMMINI_FIELDS, map(_cammino_row, items))
    tappe = (row for item in items for row in _tappe_rows(item))
    n_tappe = files.write_csv(config.CDI_TAPPE_CSV, TAPPE_FIELDS, tappe)
    print(f"camminiditalia: {n_cammini} cammini, {n_tappe} tappe")
