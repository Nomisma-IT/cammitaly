"""Ministero del Turismo, Catalogo dei Cammini religiosi: pagine salvate a mano + zip delle tracce.

Le pagine di elenco stanno dietro un controllo del browser (Radware) e non si scaricano da
script: le salva il proprietario in data/raw/mitur/pagine/. Gli zip sono file statici e si scaricano.
"""
import re
import unicodedata
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from rapidfuzz import fuzz

from cammitaly import config, files, http

FIELDS = [
    "nome", "slug", "regioni", "in_catalogo_dal", "decreto", "sito_web", "url_percorso",
    "pagina", "n_file", "tipi_file", "nota",
]


def _clean(parts: list[str]) -> str:
    return " ".join("".join(parts).split())


def _zip_relative_path(href: str) -> str:
    """Percorso del zip dopo il prefisso consentito; stringa vuota se l'href non e' un zip ammesso."""
    if not href.startswith(config.MITUR_ZIP_PREFIX):
        return ""
    prefix_path = urlparse(config.MITUR_ZIP_PREFIX).path
    relative = urlparse(href).path[len(prefix_path):]
    parts = relative.split("/")
    if ".." in parts or not relative.lower().endswith(config.ZIP_SUFFIX) or len(relative) <= len(config.ZIP_SUFFIX):
        return ""
    return relative


def is_zip_url(href: str) -> bool:
    """Un href diventa url_percorso solo se sta sotto il prefisso del ministero e finisce in .zip."""
    return bool(_zip_relative_path(href))


def zip_file_name(url: str) -> str:
    """Nome del zip in cache, derivato dall'URL e non dall'ordine delle pagine: '2023_06_X.zip'."""
    relative = _zip_relative_path(url)
    if not relative:
        raise http.FetchError(f"URL del zip non ammesso: {url!r}")
    return re.sub(config.MITUR_ZIP_NAME_UNSAFE_PATTERN, "_", relative)


class _ListingParser(HTMLParser):
    """Raccoglie nome, regioni, nota di catalogo e link di ogni scheda (article.cr-card)."""

    def __init__(self, source: str) -> None:
        super().__init__()
        self.source = source
        self.items: list[dict[str, Any]] = []
        self._item: dict[str, Any] | None = None
        self._capture: str | None = None
        self._capture_tag = ""
        self._depth = 0  # annidamento del tag catturato: un </div> interno non chiude il campo
        self._buffer: list[str] = []
        self._meta_kind = ""
        self._href: str | None = None
        self._link_text: list[str] = []

    def _begin(self, target: str, tag: str) -> None:
        self._capture, self._capture_tag, self._depth, self._buffer = target, tag, 1, []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        classes = attributes.get("class", "").split()
        if tag == config.MITUR_CARD_TAG and config.MITUR_CARD_CLASS in classes:
            self._item = {
                "nome": "", "regioni": "", "nota": "", "sito_web": "", "url_percorso": "",
                "href_scartati": [], "n_disattivati": 0,
            }
            self._capture = None
            return
        if self._item is None:
            return
        if self._capture is None:
            self._begin_capture(tag, classes)
        else:
            self._depth += tag == self._capture_tag
            if tag == "svg" and self._capture == "meta":
                self._meta_kind = attributes.get("aria-label", "")
        if tag == "a":
            self._href, self._link_text = attributes.get("href", ""), []

    def _begin_capture(self, tag: str, classes: list[str]) -> None:
        if tag == config.MITUR_TITLE_TAG and config.MITUR_TITLE_CLASS in classes:
            self._begin("nome", tag)
        elif tag == "div" and config.MITUR_META_CLASS in classes:
            self._meta_kind = ""
            self._begin("meta", tag)
        elif tag == "div" and config.MITUR_NOTE_CLASS in classes:
            self._begin("nota", tag)

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buffer.append(data)
        if self._href is not None:
            self._link_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._item is None:
            return
        if self._capture is not None and tag == self._capture_tag:
            self._depth -= 1
            if self._depth == 0:
                self._close_capture()
        if tag == "a" and self._href is not None:
            self._close_link()
        elif tag == config.MITUR_CARD_TAG:
            self._close_card()

    def _close_capture(self) -> None:
        assert self._item is not None and self._capture is not None
        text = _clean(self._buffer)
        if self._capture == "meta":
            if self._meta_kind == config.MITUR_LOCATION_LABEL:
                self._item["regioni"] = text
        else:
            self._item[self._capture] = text
        self._capture = None

    def _close_link(self) -> None:
        assert self._item is not None and self._href is not None
        label, href = _clean(self._link_text), self._href
        if config.MITUR_SITE_LABEL in label:
            self._item["sito_web"] = href
        elif config.MITUR_DOWNLOAD_LABEL in label:
            self._classify_download(href)
        self._href = None

    def _classify_download(self, href: str) -> None:
        assert self._item is not None
        if is_zip_url(href):
            self._item["url_percorso"] = href
        elif not href or href.endswith(config.MITUR_DISABLED_HREF_SUFFIX):
            self._item["n_disattivati"] += 1  # pulsante disattivato: la scheda resta senza url_percorso
        else:
            self._item["href_scartati"].append(href)  # fuori dal prefisso o non .zip: ignorato e segnalato

    def _close_card(self) -> None:
        assert self._item is not None
        if not self._item["nome"]:
            raise http.FetchError(f"scheda senza nome in {self.source}: marcatura della pagina cambiata?")
        self.items.append(self._item)
        self._item, self._capture, self._href = None, None, None


def parse_listing(markup: str, source: str = "") -> list[dict[str, Any]]:
    parser = _ListingParser(source)
    parser.feed(markup)
    parser.close()
    if not parser.items:
        raise http.FetchError(f"nessuna scheda in {source}: marcatura della pagina cambiata?")
    return parser.items


def parse_catalog_note(note: str) -> tuple[str, str]:
    """Da 'In catalogo dal 23 marzo 2023 (D. Dir. prot. 5745/23)' a (data ISO, decreto)."""
    match = re.search(config.MITUR_CATALOG_DATE_PATTERN, note)
    if not match:
        return "", ""
    day, month_name, year, decree = match.groups()
    month = config.MESI_IT.get(month_name.lower())
    iso = f"{year}-{month:02d}-{int(day):02d}" if month else ""
    return iso, (decree or "").strip()


def slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(config.SLUG_STRIP_PATTERN, "-", ascii_name.lower()).strip("-")


def parse_page_number(markup: str, source: str = "") -> int:
    """Pagina dell'elenco dal link canonical: '.../page/11/' = 11, l'URL base del catalogo = 1."""
    canonical = re.search(config.MITUR_CANONICAL_PATTERN, markup)
    if not canonical:
        raise http.FetchError(f"link canonical assente in {source}: pagina non riconoscibile")
    numbered = re.search(config.MITUR_PAGE_NUMBER_PATTERN, canonical.group(1))
    return int(numbered.group(1)) if numbered else 1


def _read_pages() -> list[tuple[int, str, str]]:
    paths = sorted(config.MITUR_PAGES_DIR.glob(config.MITUR_PAGES_GLOB))
    if not paths:
        raise http.FetchError(f"nessuna pagina salvata in {config.MITUR_PAGES_DIR}")
    # Il file salvato dal browser puo' avere byte non utf-8: si sostituiscono invece di fermarsi
    texts = [(p.name, p.read_bytes().decode("utf-8", errors="replace")) for p in paths]
    pages = sorted((parse_page_number(text, name), name, text) for name, text in texts)
    numbers = [number for number, _, _ in pages]
    if len(set(numbers)) != len(numbers):
        raise http.FetchError(f"due file salvati sono la stessa pagina di elenco: {numbers}")
    return pages


def _read_items() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for number, name, markup in _read_pages():
        for item in parse_listing(markup, name):
            key = (item["url_percorso"], item["nome"].casefold())  # stessa scheda ripetuta, non stesso zip
            if key not in seen:
                seen.add(key)
                items.append({**item, "pagina": number, "condiviso_con": []})
    return items


def _zip_name_score(item: dict[str, Any]) -> float:
    stem = slugify(Path(urlparse(item["url_percorso"]).path).stem).replace("-", " ")
    return fuzz.token_set_ratio(slugify(item["nome"]).replace("-", " "), stem)


def _clear_owner(members: list[dict[str, Any]]) -> dict[str, Any] | None:
    ranked = sorted(members, key=_zip_name_score, reverse=True)
    best, second = _zip_name_score(ranked[0]), _zip_name_score(ranked[1])
    clear = best >= config.MITUR_SHARED_ZIP_MIN_SCORE and best - second >= config.MITUR_SHARED_ZIP_MARGIN
    return ranked[0] if clear else None


def _resolve_shared_zips(items: list[dict[str, Any]]) -> None:
    """Due schede con lo stesso zip: lo tiene quella che somiglia al nome del file, l'altra resta senza percorso."""
    groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        if item["url_percorso"]:
            groups[item["url_percorso"]].append(item)
    for members in filter(lambda group: len(group) > 1, groups.values()):
        owner = _clear_owner(members)
        keeper = owner or members[0]  # attribuzione incerta: lo zip resta alla prima scheda, con nota su tutte
        for item in members:
            if owner is None:
                item["condiviso_con"] = [m for m in members if m is not item]
            elif item is not owner:
                item["condiviso_con"] = [owner]
            if item is not keeper:
                item["url_percorso"] = ""


def _slug_discriminator(item: dict[str, Any]) -> str:
    # Parte del nome che distingue due cammini omonimi, ricavata da dati stabili e mai dall'ordine di pagina
    if item["url_percorso"]:
        return slugify(Path(zip_file_name(item["url_percorso"])).stem)
    return slugify(urlparse(item["sito_web"]).hostname or "")


def _row_note(item: dict[str, Any]) -> str:
    notes = []
    if item["condiviso_con"]:
        peers = ", ".join(peer["slug"] for peer in item["condiviso_con"])
        notes.append(f"zip condiviso con {peers}: percorso non attribuito")
    ministry_host = urlparse(config.MITUR_ZIP_PREFIX).hostname
    for href in item["href_scartati"]:
        host = urlparse(href).hostname
        notes.append(f"percorso su host esterno: {host}" if host and host != ministry_host else f"link di download non ammesso: {href}")
    return "; ".join(notes)


def _assign_slugs(items: list[dict[str, Any]]) -> None:
    """Slug = slugify(nome), stabile negli anni; gli omonimi ricevono tutti un discriminatore."""
    groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        groups[slugify(item["nome"])].append(item)
    for base, members in groups.items():
        if not base:
            raise http.FetchError(f"nome non utilizzabile come slug: {members[0]['nome']!r}")
        for item in members:
            item["slug"] = base if len(members) == 1 else "-".join(filter(None, [base, _slug_discriminator(item)]))
    repeated = [slug for slug, n in Counter(item["slug"] for item in items).items() if n > 1]
    if repeated:
        raise http.FetchError(f"slug ancora duplicati dopo il discriminatore: {repeated}")


def _check_unique_zip_names(items: list[dict[str, Any]]) -> None:
    owners: dict[str, str] = {}
    for item in items:
        name = zip_file_name(item["url_percorso"])
        if owners.setdefault(name, item["url_percorso"]) != item["url_percorso"]:
            raise http.FetchError(f"due URL diversi danno lo stesso nome di zip in cache: {name}")


def _needs_extraction(target: Path, zip_path: Path, refresh: bool) -> bool:
    # Una cartella piu' vecchia del zip viene da un altro zip (stesso slug, anno nuovo): va rifatta
    if refresh or not (target / config.ZIP_MANIFEST_FILE).exists():  # senza manifesto: schema di estrazione precedente
        return True
    return target.stat().st_mtime < zip_path.stat().st_mtime


def _download_and_extract(item: dict[str, Any], refresh: bool) -> None:
    zip_path = config.MITUR_ZIP_DIR / zip_file_name(item["url_percorso"])
    http.fetch(item["url_percorso"], zip_path, config.HOST_MITUR, refresh)
    target = config.MITUR_TRACKS_DIR / item["slug"]
    if _needs_extraction(target, zip_path, refresh):
        files.extract_flat_atomically(zip_path, target)


def _file_stats(slug: str) -> tuple[int, str]:
    folder = config.MITUR_TRACKS_DIR / slug
    found = [p for p in folder.rglob("*") if p.is_file() and p.name != config.ZIP_MANIFEST_FILE] if folder.exists() else []
    kinds = Counter(p.suffix.lower().lstrip(".") or config.MITUR_NO_EXTENSION for p in found)
    return len(found), "|".join(f"{kind}:{n}" for kind, n in kinds.most_common())


def _csv_row(item: dict[str, Any]) -> dict[str, object]:
    iso_date, decree = parse_catalog_note(item["nota"])
    n_files, kinds = _file_stats(item["slug"])
    regions = "|".join(r.strip() for r in item["regioni"].split(",") if r.strip())
    return {
        "nome": item["nome"], "slug": item["slug"], "regioni": regions,
        "in_catalogo_dal": iso_date, "decreto": decree, "sito_web": item["sito_web"],
        "url_percorso": item["url_percorso"], "pagina": item["pagina"],
        "n_file": n_files, "tipi_file": kinds, "nota": _row_note(item),
    }


def _warn_ignored_hrefs(items: list[dict[str, Any]]) -> None:
    ignored = [(item["nome"], href) for item in items for href in item["href_scartati"]]
    if ignored:
        print(f"mitur: ATTENZIONE {len(ignored)} href di download ignorati (fuori da {config.MITUR_ZIP_PREFIX} o non .zip): {ignored}")


def run(limit: int | None = None, refresh: bool = False) -> None:
    items = _read_items()
    _resolve_shared_zips(items)
    _assign_slugs(items)
    _warn_ignored_hrefs(items)
    with_zip = [item for item in items if item["url_percorso"]]
    _check_unique_zip_names(with_zip)
    for done, item in enumerate(with_zip[:limit], start=1):
        print(f"mitur: zip {done}/{len(with_zip[:limit])} {item['slug']}")
        _download_and_extract(item, refresh)
    count = files.write_csv(config.MITUR_CAMMINI_CSV, FIELDS, map(_csv_row, items))
    print(f"mitur: {count} cammini in {len(with_zip)} con zip, CSV in {config.MITUR_CAMMINI_CSV}")
