"""Cartella di lavoro Excel: Legenda, Comuni, Cammini, Territori, Territori x <caratteristica>, Cammini per attributo, Coppie.

Legge i due file del contratto delle coppie (coppie_comuni.csv, cammini.csv), controlla tutte le invarianti PRIMA di scrivere il
file e non scrive nulla se una e' violata. Intestazioni, larghezze, formati e testi della Legenda vengono da un solo
registro delle colonne (config, WORKBOOK_*_COLUMNS); i "Valori" della Legenda si calcolano dai dati scritti. I fogli per
territorio sono numeri statici calcolati qui: un conteggio di cammini distinti non si somma tra territori, e la catena
non deve dipendere da Excel (niente formule ne' tabelle pivot).
"""
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from statistics import mean
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from cammitaly import config, pairs as pairs_step, tags
from cammitaly.geometry import common

Row = dict[str, Any]
Column = config.WorkbookColumn
COMUNE = config.COMUNE_ID_COL
UNICO = config.PAIRS_UNICO_COL
COMUNE_CODE_PATTERN = re.compile(rf"\d{{{config.WORKBOOK_CODE_DIGITS[COMUNE]}}}")
COMUNE_CAMMINO_PATTERN = re.compile(config.WORKBOOK_COMUNE_CAMMINO_KEY.format(k=r"\d+"))
CODE_KEYS = tuple(config.WORKBOOK_CODE_DIGITS)
ISTAT_KEYS = (COMUNE, "comune", "sigla", "cod_uts", "provincia", "cod_reg", "regione")
GROUP_K = config.WORKBOOK_GROUP_K
SEP = config.WORKBOOK_CELL_SEPARATOR
BOLD = Font(bold=True)
HEADER_ALIGNMENT = Alignment(horizontal="center", vertical="center", wrap_text=True)
WRAP_TOP = Alignment(vertical="top", wrap_text=True)
LINK_FONT = Font(color=config.WORKBOOK_LINK_COLOR, underline="single")
TITLE_FONT = Font(size=config.WORKBOOK_TITLE_SIZE, bold=True, color=config.WORKBOOK_TITLE_COLOR)
SECTION_FONT = Font(size=config.WORKBOOK_SECTION_SIZE, bold=True, color=config.WORKBOOK_TITLE_COLOR)
GREY_FONT = Font(color=config.WORKBOOK_SUBTITLE_COLOR)
REGION_FILL = PatternFill("solid", fgColor=config.WORKBOOK_REGION_FILL)
BAND_ALIGNMENT = Alignment(horizontal="center", vertical="center", wrap_text=True)
# Ordine dei valori nelle categorie: prima le etichette note nell'ordine di config, poi le altre, in fondo i vuoti
PREFERRED_VALUES = (
    *config.WORKBOOK_SI_NO_LABELS.values(), *config.WORKBOOK_MEZZO_LABELS.values(),
    *config.WORKBOOK_FORMA_LABELS.values(), config.WORKBOOK_PASSAGGIO_DENTRO, config.WORKBOOK_PASSAGGIO_VICINO,
    *config.WORKBOOK_LEVEL_LABELS,
)
LAST_VALUES = (config.WORKBOOK_EMPTY_LABEL, config.WORKBOOK_NO_TRACK_LABEL)


class WorkbookError(ValueError):
    """Dato di ingresso mancante o invariante violata: la cartella di lavoro non viene scritta."""


@dataclass(frozen=True)
class Table:
    """Una tabella da scrivere: colonne del registro, righe (chiave -> valore mostrato), righe di totale in grassetto."""

    columns: Sequence[Column]
    rows: Sequence[Row]
    totals: Sequence[Row] = ()
    title: str | None = None
    notes: Sequence[str] = ()  # righe grigie sotto il titolo (fogli per territorio) o sotto la tabella (riepiloghi)
    group_labels: Sequence[str] = ()  # etichetta di ogni k dei gruppi ripetuti (valori sopra le coppie di colonne)
    levels: Sequence[int] = ()  # livello di ogni riga nei fogli per territorio (0 = Italia ... 3 = Comune)


@dataclass
class Written:
    """Valori scritti per (foglio, chiave del registro): servono ai "Valori" della Legenda e ai gruppi ripetuti."""

    values: dict[tuple[str, str], list[Any]] = field(default_factory=lambda: defaultdict(list))
    group_size: dict[tuple[str, str], int] = field(default_factory=dict)
    sheets: dict[str, list[Column]] = field(default_factory=dict)


# --- lettura e conversione dei tipi ---

def _read_table(path: Path, fields: Sequence[str]) -> list[Row]:
    if not path.is_file():
        raise WorkbookError(f"Manca il file {path.name} in {path.parent}: va prodotto prima con il comando 'pairs'.")
    rows = common.read_csv(path)
    if not rows:
        raise WorkbookError(f"Il file {path.name} è vuoto.")
    missing = [name for name in fields if name not in rows[0]]
    if missing:
        raise WorkbookError(f"Nel file {path.name} mancano le colonne: {', '.join(missing)}.")
    return rows


def _parse_code(key: str, text: str) -> str:
    if key == COMUNE:  # la chiave dei comuni non si ricostruisce: 6 cifre o errore
        if not COMUNE_CODE_PATTERN.fullmatch(text):
            raise ValueError(text)
        return text
    digits = config.WORKBOOK_CODE_DIGITS[key]
    if not text.isdigit() or len(text) > digits:
        raise ValueError(text)
    return text.zfill(digits)


def _parse_bool(text: str) -> bool:
    lowered = text.lower()
    if lowered in config.WORKBOOK_TRUE_VALUES:
        return True
    if lowered in config.WORKBOOK_FALSE_VALUES:
        return False
    raise ValueError(text)


def _parse_value(key: str, text: str, blank_enum_keys: Sequence[str] = ()) -> Any:
    if key in config.WORKBOOK_CODE_DIGITS:
        return _parse_code(key, text)
    if key in config.WORKBOOK_INT_KEYS:
        return int(text)
    if key in config.WORKBOOK_FLOAT_KEYS:
        return float(text) if text else None
    if key == config.WORKBOOK_BOOL_KEY:
        return _parse_bool(text)
    if key in config.WORKBOOK_ENUM_KEYS and text not in config.WORKBOOK_VALUE_LABELS[key]:
        if text or key not in blank_enum_keys:
            raise ValueError(text)
    return text


def _typed_row(raw: Mapping[str, str], fields: Sequence[str], file_name: str, blank_enum_keys: Sequence[str] = ()) -> Row:
    who = raw.get(UNICO, "?")
    typed: Row = {}
    for key in fields:
        try:
            typed[key] = _parse_value(key, raw[key].strip(), blank_enum_keys)
        except ValueError:
            comune = f", comune {raw[COMUNE]}" if COMUNE in raw else ""
            raise WorkbookError(
                f"{file_name}, cammino {who}{comune}: valore non valido nella colonna '{key}': {raw[key]!r}."
            ) from None
    return typed


def load_istat_table() -> list[Row]:
    """Tutti i comuni ISTAT con provincia e regione (stessa tabella di pairs), codici come testo con gli zeri."""
    table = pairs_step.load_comune_table()[list(ISTAT_KEYS)].astype(str)
    rows = table.to_dict("records")
    try:
        return [{**row, **{key: _parse_code(key, row[key]) for key in CODE_KEYS}} for row in rows]
    except ValueError as error:
        raise WorkbookError(f"Codice ISTAT non valido negli shapefile: {error}") from None


def mitur_page_url(page: str) -> str:
    return config.MITUR_LISTING_URL if page == "1" else config.MITUR_LISTING_PAGE_URL.format(pagina=page)


def card_links() -> dict[str, str]:
    """cammino_id del catalogo -> indirizzo della sua scheda: la pagina camminiditalia.org; per MiTur la pagina di
    elenco del catalogo che contiene la scheda (colonna pagina di mitur_cammini.csv)."""
    cdi_path = Path(config.CAMMINI_CSV_TEMPLATE.format(fonte=config.CDI_FONTE))
    for path in (cdi_path, config.MITUR_CAMMINI_CSV):
        if not path.is_file():
            raise WorkbookError(f"Manca il file {path}: rilanciare il passo che lo produce.")
    links = {row["cammino_id"]: row["link"] for row in common.read_csv(cdi_path)}
    for row in common.read_csv(config.MITUR_CAMMINI_CSV):
        links[config.MITUR_ID_PREFIX + row["slug"]] = mitur_page_url(row["pagina"])
    return links


# --- invarianti sui dati di ingresso ---

def check_cammini_ids(cammini: Sequence[Row], pairs: Sequence[Row]) -> dict[str, Row]:
    """Ogni cammino compare una sola volta nell'elenco e ogni coppia rimanda a un cammino dell'elenco."""
    by_id: dict[str, Row] = {}
    for cammino in cammini:
        cid = cammino[UNICO]
        if cid in by_id:
            raise WorkbookError(f"Il cammino {cid} compare più di una volta nell'elenco dei cammini.")
        by_id[cid] = cammino
    unknown = sorted({pair[UNICO] for pair in pairs} - by_id.keys())
    if unknown:
        raise WorkbookError(f"Coppie con cammini assenti dall'elenco dei cammini: {', '.join(unknown)}.")
    return by_id


def check_mezzo_blank_only_without_pairs(cammini: Sequence[Row], pairs: Sequence[Row]) -> None:
    """Un cammino ha il mezzo vuoto se e solo se non ha coppie (fonte del tracciato nessuna, N. comuni 0)."""
    with_pairs = {pair[UNICO] for pair in pairs}
    for cammino in cammini:
        blank, paired = not cammino["mezzo"], cammino[UNICO] in with_pairs
        if blank == paired:
            raise WorkbookError(
                f"Il cammino {cammino[UNICO]} ha il mezzo {'vuoto ma ha coppie' if paired else 'indicato ma non ha coppie'}."
            )


def check_comune_codes(pairs: Iterable[Row], istat_codes: set[str]) -> None:
    unknown = sorted({pair[COMUNE] for pair in pairs} - istat_codes)
    if unknown:
        raise WorkbookError(
            f"Codici comune assenti dai confini ISTAT {config.ISTAT_EDITION_YEAR}: {', '.join(unknown[:10])}"
            + (f" e altri {len(unknown) - 10}." if len(unknown) > 10 else ".")
        )


def check_duplicate_pairs(pairs: Iterable[Row]) -> None:
    seen: set[tuple[str, str]] = set()
    for pair in pairs:
        key = (pair[UNICO], pair[COMUNE])
        if key in seen:
            raise WorkbookError(f"La coppia cammino {key[0]} e comune {key[1]} compare più di una volta.")
        seen.add(key)


def check_routes(by_id: Mapping[str, Row], pairs: Iterable[Row]) -> dict[str, list[Row]]:
    """Per cammino: ordine contiguo da 1 a n e numero di comuni uguale a n_comuni. Ritorna le coppie in ordine di percorso."""
    routes: dict[str, list[Row]] = defaultdict(list)
    for pair in pairs:
        routes[pair[UNICO]].append(pair)
    for cid, cammino in by_id.items():
        route = sorted(routes.get(cid, []), key=lambda pair: pair["ordine"])
        routes[cid] = route
        if [pair["ordine"] for pair in route] != list(range(1, len(route) + 1)):
            raise WorkbookError(f"Il cammino {cid} ({cammino['nome']}): l'ordine dei comuni non è 1, 2, ... {len(route)}.")
        if cammino["n_comuni"] != len(route):
            raise WorkbookError(
                f"Il cammino {cid} ({cammino['nome']}): {len(route)} comuni nelle coppie ma n_comuni vale {cammino['n_comuni']}."
            )
    return dict(routes)


# --- valori mostrati ---

def _sort_name(name: str) -> tuple[str, str]:
    return common.normalize_name(name), name


def join_names(names: Sequence[str]) -> str:
    """'A', 'A e B', 'A, B e C'."""
    if len(names) <= 1:
        return "".join(names)
    return config.WORKBOOK_NAMES_JOIN.join(names[:-1]) + config.WORKBOOK_NAMES_LAST_JOIN + names[-1]


def source_names(text: str) -> str:
    """Codici di fonte uniti da '|' ("cdi|osm") -> nomi nell'ordine di config ("Cammini d'Italia e OpenStreetMap")."""
    codes = set(text.split(config.LIST_SEPARATOR))
    unknown = codes - config.WORKBOOK_SOURCE_LABELS.keys()
    if unknown:
        raise WorkbookError(f"Fonte non riconosciuta: {', '.join(sorted(unknown))}.")
    return join_names([label for code, label in config.WORKBOOK_SOURCE_LABELS.items() if code in codes])


def _display_dates(parts: Sequence[str]) -> Any:
    """Una sola data ISO e' una vera data di Excel; piu' date sono testo GG/MM/AAAA; un testo non data resta com'e'."""
    try:
        dates = [date.fromisoformat(part) for part in parts]
    except ValueError:
        return SEP.join(parts) or None
    if len(dates) == 1:
        return dates[0]
    return SEP.join(day.strftime(config.WORKBOOK_DATE_DISPLAY_FORMAT) for day in dates) or None


def display(key: str, value: Any) -> Any:
    """Valore come appare nel foglio: etichette italiane, nomi delle fonti, date, elenchi con il separatore di cella."""
    if not isinstance(value, str):
        return value
    if key == "fonti":
        return source_names(value)
    if key == "fonte_geometria":
        return config.WORKBOOK_NO_TRACK_LABEL if value == config.FONTE_GEOMETRIA_NESSUNA else source_names(value)
    parts = [part for part in value.split(config.LIST_SEPARATOR) if part] if key in config.WORKBOOK_LIST_KEYS else [value]
    if key in config.WORKBOOK_DATE_KEYS:
        return _display_dates(parts)
    labels = config.WORKBOOK_VALUE_LABELS.get(key, {})
    shown = [labels.get(part, part) for part in parts if part]
    if key in config.WORKBOOK_WORD_KEYS:  # valori scritti come slug ("arte-e-cultura"): si leggono come parole
        shown = [text.replace(config.WORKBOOK_SLUG_SEPARATOR, " ") for text in shown]
    if not shown and key in config.WORKBOOK_EMPTY_LABEL_KEYS:
        return config.WORKBOOK_EMPTY_LABEL
    return SEP.join(shown) or None


def passaggio(pair: Row) -> str:
    return config.WORKBOOK_PASSAGGIO_DENTRO if pair["dentro"] else config.WORKBOOK_PASSAGGIO_VICINO


def _display_row(source: Row, keys: Iterable[str]) -> Row:
    return {key: display(key, source[key]) for key in keys}


# --- righe dei fogli di dati ---

def comuni_rows(pairs: Sequence[Row]) -> list[Row]:
    """Una riga per comune, ordinata per codice regione, provincia e comune; i cammini del comune in ordine alfabetico."""
    groups: dict[str, list[Row]] = defaultdict(list)
    for pair in pairs:
        groups[pair[COMUNE]].append(pair)
    rows = []
    for group in groups.values():
        group.sort(key=lambda pair: _sort_name(pair["nome"]))
        row = {key: group[0][key] for key in ISTAT_KEYS}
        row["n_cammini"] = len(group)
        for k, pair in enumerate(group, start=1):
            row.update({
                config.WORKBOOK_COMUNE_CAMMINO_KEY.format(k=k): pair["nome"], f"mezzo_{k}": display("mezzo", pair["mezzo"]),
                f"religioso_{k}": display("religioso", pair["religioso"]), f"passaggio_{k}": passaggio(pair),
            })
        rows.append(row)
    rows.sort(key=lambda row: (row["cod_reg"], *_sort_name(row["provincia"]), *_sort_name(row["comune"]), row[COMUNE]))
    return rows


def _ragged_comune(pair: Row) -> str:
    template = config.WORKBOOK_RAGGED_FORMAT if pair["dentro"] else config.WORKBOOK_RAGGED_FORMAT_VICINO
    return template.format(comune=pair["comune"], sigla=pair["sigla"])


def _card_urls(members: str, prefix: str, links: Mapping[str, str]) -> list[str] | None:
    ids = [member for member in members.split(config.LIST_SEPARATOR) if member.startswith(prefix)]
    missing = [member for member in ids if not links.get(member)]
    if missing:
        raise WorkbookError(f"Nessun indirizzo della scheda per {missing[0]}: rilanciare il passo build della sua fonte.")
    return [links[member] for member in ids] or None


CAMMINI_PLAIN_KEYS = (
    UNICO, "nome", "alias", "fonti", "mezzo", "religioso", "evidenza_religioso", *config.PAIRS_MULTI_FIELDS, "regioni",
    "lunghezza_dichiarata_km", "lunghezza_tracciata_km", "copertura", "fonte_geometria", "n_comuni", "nota",
)
COPPIE_PLAIN_KEYS = (UNICO, "nome", *ISTAT_KEYS, "ordine", "distanza_m", "mezzo", "fonte_geometria")
COPPIE_CAMMINO_KEYS = ("religioso", "tipologia", "portata", "tema", "stagione", "credenziale", "segnaletica")


def cammini_rows(ordered: Sequence[Row], routes: Mapping[str, Sequence[Row]], links: Mapping[str, str]) -> list[Row]:
    rows = []
    for cammino in ordered:
        route = routes[cammino[UNICO]]
        row = _display_row(cammino, CAMMINI_PLAIN_KEYS)
        row.update({
            "scheda_cdi": _card_urls(cammino["membri"], config.CDI_ID_PREFIX, links),
            "scheda_mitur": _card_urls(cammino["membri"], config.MITUR_ID_PREFIX, links),
            "n_dentro": sum(pair["dentro"] for pair in route), "n_vicino": sum(not pair["dentro"] for pair in route),
            **{f"comune_{k}": _ragged_comune(pair) for k, pair in enumerate(route, start=1)},
        })
        rows.append(row)
    return rows


def coppie_rows(ordered: Sequence[Row], routes: Mapping[str, Sequence[Row]]) -> list[Row]:
    rows = []
    for cammino in ordered:
        for pair in routes[cammino[UNICO]]:
            rows.append({
                **_display_row(pair, COPPIE_PLAIN_KEYS), "passaggio": passaggio(pair),
                **_display_row(cammino, COPPIE_CAMMINO_KEYS),
            })
    return rows


# --- Cammini per attributo ---

def _mean(values: Iterable[float | None]) -> float | None:
    known = [value for value in values if value is not None]
    return mean(known) if known else None


def category_key(value: str, count: int = 0) -> tuple[int, int, str]:
    if value in LAST_VALUES:
        return 2, LAST_VALUES.index(value), ""
    if value in PREFERRED_VALUES:
        return 0, PREFERRED_VALUES.index(value), ""
    return 1, -count, common.normalize_name(value)


def _summary_row(value: str, members: Sequence[Row], total: int, comuni_of: Mapping[str, set[str]]) -> Row:
    return {
        "valore": value, "n_cammini": len(members), "quota": len(members) / total,
        "media_dichiarata": _mean(row["lunghezza_dichiarata_km"] for row in members),
        "media_tracciata": _mean(row["lunghezza_tracciata_km"] for row in members),
        "media_comuni": _mean(row["n_comuni"] for row in members),
        "comuni_distinti": len(set().union(*(comuni_of[row[UNICO]] for row in members))),
    }


def dimension_table(key: str, multi: bool, cammini: Sequence[Row], comuni_of: Mapping[str, set[str]]) -> Table:
    """Cammini per attributo, in Italia: cammini per valore della caratteristica `key` (valori del foglio Cammini); multi: un cammino conta sotto ogni valore."""
    groups: dict[str, list[Row]] = defaultdict(list)
    for row in cammini:
        cell = row[key] or config.WORKBOOK_EMPTY_LABEL
        for value in cell.split(SEP) if multi else [cell]:
            groups[value].append(row)
    total = len(cammini)
    ordered = sorted(groups, key=lambda value: category_key(value, len(groups[value])))
    rows = [_summary_row(value, groups[value], total, comuni_of) for value in ordered]
    title = column_by_key(config.WORKBOOK_CAMMINI_COLUMNS, key).header
    if multi:
        return Table(config.WORKBOOK_SUMMARY_COLUMNS, rows, title=title, notes=(config.WORKBOOK_SUMMARY_MULTI_NOTE,))
    if sum(row["n_cammini"] for row in rows) != total:
        raise WorkbookError(f"Cammini per attributo, {title}: i cammini per valore non sommano a {total}.")
    totals = [_summary_row(config.WORKBOOK_TOTAL_LABEL, cammini, total, comuni_of)]
    return Table(config.WORKBOOK_SUMMARY_COLUMNS, rows, totals, title=title)


def cross_table(cammini: Sequence[Row]) -> Table:
    """Religioso per mezzo: numero di cammini; una colonna del mezzo che vale zero in tutte le righe non si scrive."""
    in_use = Counter(row["mezzo"] for row in cammini)
    modes = {mode: label for mode, label in config.WORKBOOK_CROSS_MODE_LABELS.items() if in_use[label]}
    keys = {mode: config.WORKBOOK_CROSS_MODE_KEY.format(mode=mode) for mode in config.WORKBOOK_CROSS_MODE_LABELS}
    unused = {key for mode, key in keys.items() if mode not in modes}
    columns = [column for column in config.WORKBOOK_SUMMARY_CROSS_COLUMNS if column.key not in unused]

    def counts(members: Sequence[Row]) -> Row:
        by_mode = Counter(row["mezzo"] for row in members)
        return {**{keys[mode]: by_mode[label] for mode, label in modes.items()}, "x_totale": len(members)}

    rows = [
        {"x_religioso": label, **counts([row for row in cammini if row["religioso"] == label])}
        for label in config.WORKBOOK_SI_NO_LABELS.values()
    ]
    total = {"x_religioso": config.WORKBOOK_TOTAL_LABEL, **counts(cammini)}
    if sum(row["x_totale"] for row in rows) != len(cammini) or sum(total[keys[mode]] for mode in modes) != len(cammini):
        raise WorkbookError(f"Religioso per mezzo: le righe o le colonne non sommano al numero dei cammini {len(cammini)}.")
    return Table(columns, rows, [total], title=config.WORKBOOK_SUMMARY_CROSS_TITLE)


# --- fogli per territorio ---

@dataclass(frozen=True)
class Territory:
    """Territorio di una riga: livello (0 = Italia ... 3 = Comune), codice, nome, comuni ISTAT e coppie per cammino."""

    level: int
    code: str | None
    name: str
    n_istat: int
    crossed: tuple[str, ...]  # codici dei comuni attraversati
    by_cammino: Mapping[str, Sequence[Row]]  # cammino -> sue coppie nei comuni del territorio


def _territory(level: int, code: str | None, name: str, codes: Sequence[str], by_comune: Mapping[str, Sequence[Row]]) -> Territory:
    crossed = tuple(code_ for code_ in codes if code_ in by_comune)
    by_cammino: dict[str, list[Row]] = defaultdict(list)
    for comune in crossed:
        for pair in by_comune[comune]:
            by_cammino[pair[UNICO]].append(pair)
    return Territory(level, code, name, len(codes), crossed, dict(by_cammino))


def territory_tree(istat: Sequence[Row], pairs: Sequence[Row]) -> list[Territory]:
    """Italia, poi ogni regione (ordine dei codici ISTAT) seguita dalle sue province, ogni provincia dai suoi comuni
    attraversati in ordine alfabetico. Regioni e province senza comuni attraversati restano, con zero cammini."""
    by_comune: dict[str, list[Row]] = defaultdict(list)
    for pair in pairs:
        by_comune[pair[COMUNE]].append(pair)
    regions: dict[str, dict[str, list[Row]]] = defaultdict(lambda: defaultdict(list))
    for row in sorted(istat, key=lambda row: (row["cod_reg"], row["cod_uts"])):
        regions[row["cod_reg"]][row["cod_uts"]].append(row)
    italia = config.WORKBOOK_LEVEL_LABELS[0]
    tree = [_territory(0, None, italia, [row[COMUNE] for row in istat], by_comune)]
    for cod_reg, provinces in regions.items():
        region_rows = [row for rows in provinces.values() for row in rows]
        tree.append(_territory(1, cod_reg, region_rows[0]["regione"], [row[COMUNE] for row in region_rows], by_comune))
        for cod_uts, rows in provinces.items():
            tree.append(_territory(2, cod_uts, rows[0]["provincia"], [row[COMUNE] for row in rows], by_comune))
            crossed = sorted((row for row in rows if row[COMUNE] in by_comune), key=lambda row: _sort_name(row["comune"]))
            tree += [_territory(3, row[COMUNE], row["comune"], [row[COMUNE]], by_comune) for row in crossed]
    return tree


def check_tree(tree: Sequence[Territory], comuni: Sequence[Row], with_pairs: int) -> None:
    """Righe dei comuni = foglio Comuni (stessi codici, Cammini = N. cammini), Cammini dell'Italia = cammini con coppie."""
    found = [(territory.code, len(territory.by_cammino)) for territory in tree if territory.level == 3]
    expected = [(row[COMUNE], row["n_cammini"]) for row in comuni]
    if len(found) != len(expected) or set(found) != set(expected):
        differing = sorted(set(found) ^ set(expected), key=str)[:3]
        raise WorkbookError(
            f"Territori: {len(found)} righe di comuni contro {len(expected)} del foglio Comuni; "
            f"codice e Cammini differiscono, ad esempio {differing}."
        )
    if len(tree[0].by_cammino) != with_pairs:
        raise WorkbookError(f"Territori: l'Italia ha {len(tree[0].by_cammino)} cammini, i cammini con coppie sono {with_pairs}.")


def _identity(territory: Territory) -> Row:
    return {"livello": config.WORKBOOK_LEVEL_LABELS[territory.level], "codice": territory.code, "territorio": territory.name}


def territori_table(tree: Sequence[Territory]) -> Table:
    rows = []
    for territory in tree:
        crossed = len(territory.crossed)
        passes = sum(len(found) for found in territory.by_cammino.values())  # somma di N. cammini sui suoi comuni
        rows.append(_identity(territory) | {
            "comuni_territorio": territory.n_istat, "comuni_attraversati": crossed,
            "quota_attraversati": crossed / territory.n_istat if territory.n_istat else None,
            "cammini": len(territory.by_cammino), "media_cammini": passes / crossed if crossed else None,
        })
    return Table(
        config.WORKBOOK_TERRITORI_COLUMNS, rows, title=config.WORKBOOK_TERRITORI_TITLE,
        notes=config.WORKBOOK_TERRITORI_NOTES, levels=[territory.level for territory in tree],
    )


# Caratteristiche territoriali: il valore si ricombina dalle sole coppie del cammino nel territorio
TERRITORIAL_COMBINE = {"mezzo": tags.combine_mezzo, "fonte_geometria": tags.combine_multi}


def value_labels(key: str, counts: Mapping[str, int]) -> list[str]:
    """Colonne di una caratteristica sui conteggi di tutte le righe: le etichette fisse (mezzo, religioso) nel loro ordine,
    le altre per frequenza, in fondo i vuoti; un valore che non compare in nessuna riga non ha colonna."""
    fixed = list(config.WORKBOOK_VALUE_LABELS[key].values()) if key in config.WORKBOOK_ENUM_KEYS else []
    others = sorted((value for value in counts if value not in fixed), key=lambda value: category_key(value, counts[value]))
    return [label for label in (*fixed, *others) if counts[label]]


def pivot_table(dimension: tuple[str, str, bool, bool], tree: Sequence[Territory], shown: Mapping[str, Row]) -> Table:
    """Territori x caratteristica: per riga, cammini distinti del territorio per valore e loro incidenza sui Cammini."""
    name, key, multi, territorial = dimension
    if territorial and key not in TERRITORIAL_COMBINE:
        raise WorkbookError(f"Caratteristica territoriale senza regola di combinazione: {key}.")

    def values_of(cid: str, found: Sequence[Row]) -> list[str]:
        if territorial:
            return [display(key, TERRITORIAL_COMBINE[key](pair[key] for pair in found))]
        cell = shown[cid][key] or config.WORKBOOK_EMPTY_LABEL
        return cell.split(SEP) if multi else [cell]

    counts = [Counter(v for cid, found in t.by_cammino.items() for v in values_of(cid, found)) for t in tree]
    labels = value_labels(key, sum(counts, Counter()))
    sheet = config.WORKBOOK_SHEET_PIVOT_TEMPLATE.format(nome=name)
    rows = []
    for territory, found in zip(tree, counts):
        total = len(territory.by_cammino)
        if not multi and sum(found.values()) != total:
            raise WorkbookError(f"{sheet}, {territory.name}: i valori non sommano ai {total} Cammini della riga.")
        row = _identity(territory)
        for k, label in enumerate(labels, start=1):
            row[config.WORKBOOK_PIVOT_N_KEY.format(k=k)] = found[label]
            row[config.WORKBOOK_PIVOT_SHARE_KEY.format(k=k)] = found[label] / total if total else None
        rows.append(row | {"cammini": total, "comuni_attraversati": len(territory.crossed)})
    notes = (
        *config.WORKBOOK_PIVOT_NOTES, *([config.WORKBOOK_PIVOT_NOTE_TERRITORIAL] if territorial else []),
        *([config.WORKBOOK_PIVOT_NOTE_MULTI] if multi else []),
    )
    title = config.WORKBOOK_PIVOT_TITLE_TEMPLATE.format(caratteristica=config.WORKBOOK_PIVOT_CHARACTERISTICS[name])
    return Table(
        config.WORKBOOK_PIVOT_COLUMNS, rows, title=title, notes=notes, group_labels=labels,
        levels=[territory.level for territory in tree],
    )


# --- invarianti sulle righe dei fogli ---

def check_comuni_rows(rows: Sequence[Row], n_pairs: int) -> None:
    """Somma di N. cammini = coppie; per riga i gruppi Cammino k pieni sono N. cammini."""
    if sum(row["n_cammini"] for row in rows) != n_pairs:
        raise WorkbookError(f"La somma di N. cammini del foglio Comuni non è il numero delle coppie ({n_pairs}).")
    for row in rows:
        filled = sum(1 for key, value in row.items() if COMUNE_CAMMINO_PATTERN.fullmatch(key) and value)
        if filled != row["n_cammini"]:
            raise WorkbookError(f"Comune {row[COMUNE]} ({row['comune']}): i cammini elencati non sono N. cammini.")


def column_by_key(columns: Sequence[Column], key: str) -> Column:
    return next(column for column in columns if column.key == key)


Expanded = tuple[Column, str, str, int | None]  # colonna del registro, chiave, intestazione, k del gruppo


def expand(columns: Sequence[Column], rows: Sequence[Row]) -> list[Expanded]:
    """Colonne scritte. Le voci ripetute consecutive formano un blocco che si ripete per k = 1, 2, ... fino al k massimo
    delle righe (Cammino 1, Mezzo 1, ..., Cammino 2, Mezzo 2, ...)."""
    keys = set().union(*(row.keys() for row in rows)) if rows else set()
    expanded: list[Expanded] = []
    index = 0
    while index < len(columns):
        if GROUP_K not in columns[index].key:
            expanded.append((columns[index], columns[index].key, columns[index].header, None))
            index += 1
            continue
        block = []
        while index < len(columns) and GROUP_K in columns[index].key:
            block.append(columns[index])
            index += 1
        k = 1
        while any(column.key.format(k=k) in keys for column in block):
            expanded += [(column, column.key.format(k=k), column.header.format(k=k), k) for column in block]
            k += 1
    return expanded


def check_registry(sheet: str, table: Table, expanded: Sequence[Expanded]) -> None:
    """Ogni colonna scritta e' nel registro e ogni voce del registro e' scritta (un gruppo ripetuto almeno una volta)."""
    rows = [*table.rows, *table.totals]
    registered = {key for _, key, _, _ in expanded}
    written = set().union(*(row.keys() for row in rows)) if rows else set()
    present = {column.key for column, _, _, _ in expanded}
    unwritten = sorted(registered - written) + [column.header for column in table.columns if column.key not in present]
    extra = sorted(written - registered)
    if extra or unwritten:
        raise WorkbookError(f"Foglio {sheet}: colonne fuori registro {extra}, voci del registro non scritte {unwritten}.")


# --- scrittura ---

def _write_cell(sheet: Worksheet, row: int, col: int, column: Column, value: Any) -> None:
    cell = sheet.cell(row, col)
    if column.link and isinstance(value, list):
        cell.value = config.WORKBOOK_LINK_SEPARATOR.join(value)
        cell.hyperlink = value[0]
        cell.font = LINK_FONT
    else:
        cell.value = value
    if isinstance(value, date):
        cell.number_format = config.WORKBOOK_FMT_DATE
    elif column.number_format == config.WORKBOOK_FMT_TEXT or isinstance(value, int | float) and not isinstance(value, bool):
        cell.number_format = column.number_format  # un'etichetta (Totale) in una colonna di numeri resta testo
    if column.wrap or column.link:
        cell.alignment = WRAP_TOP


def _write_header(sheet: Worksheet, row: int, col: int, headers: Sequence[str]) -> None:
    for offset, header in enumerate(headers):
        cell = sheet.cell(row, col + offset, header)
        cell.font = BOLD
        cell.alignment = HEADER_ALIGNMENT
    sheet.row_dimensions[row].height = config.WORKBOOK_HEADER_HEIGHT


def legend_name(sheet: str) -> str:
    """Voce della Legenda di un foglio: i fogli Territori x si descrivono insieme."""
    return config.WORKBOOK_PIVOT_LEGEND_NAME if sheet in config.WORKBOOK_PIVOT_SHEETS else sheet


def write_table(sheet: Worksheet, table: Table, top: int, left: int, written: Written) -> int:
    """Scrive intestazione, righe e totali da (top, left); registra i valori per la Legenda. Ritorna l'ultima riga scritta."""
    expanded = expand(table.columns, table.rows)
    check_registry(sheet.title, table, expanded)
    _write_header(sheet, top, left, [header for _, _, header, _ in expanded])
    legend = legend_name(sheet.title)
    described = written.sheets.setdefault(legend, [])
    # una colonna ripetuta in piu' tabelle dello stesso foglio (Religioso, Totale) si descrive una volta
    described += [column for column in table.columns if column.key not in {known.key for known in described}]
    for index, row in enumerate([*table.rows, *table.totals], start=1):
        is_total = index > len(table.rows)
        for offset, (column, key, _, _) in enumerate(expanded):
            value = row.get(key)
            _write_cell(sheet, top + index, left + offset, column, value)
            if is_total:
                sheet.cell(top + index, left + offset).font = BOLD
            else:
                written.values[(legend, column.key)].append(value)
    for column, size in Counter(column for column, _, _, k in expanded if k is not None).items():
        written.group_size[(legend, column.key)] = max(size, written.group_size.get((legend, column.key), 0))
    return top + len(table.rows) + len(table.totals)


def _set_widths(sheet: Worksheet, expanded: Sequence[Expanded], left: int = 1) -> None:
    for offset, (column, _, _, _) in enumerate(expanded):
        sheet.column_dimensions[get_column_letter(left + offset)].width = column.width


def write_data_sheet(sheet: Worksheet, table: Table, written: Written) -> None:
    """Foglio di dati: intestazione in riga 1, righe sotto, filtro su tutto, blocco sotto l'intestazione e a destra dell'identita'."""
    last_row = write_table(sheet, table, 1, 1, written)
    expanded = expand(table.columns, table.rows)
    _set_widths(sheet, expanded)
    sheet.freeze_panes = f"{get_column_letter(config.WORKBOOK_FROZEN_COLUMNS[sheet.title] + 1)}2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(expanded))}{last_row}"


def _subtitle(run_date: date) -> str:
    return config.WORKBOOK_SUBTITLE_TEMPLATE.format(data=run_date.strftime(config.WORKBOOK_DATE_DISPLAY_FORMAT))


def _write_title(sheet: Worksheet, title: str, run_date: date) -> None:
    sheet.sheet_view.showGridLines = False
    sheet.column_dimensions["A"].width = config.WORKBOOK_MARGIN_WIDTH
    sheet.cell(1, 2, title).font = TITLE_FONT
    sheet.cell(2, 2, _subtitle(run_date)).font = GREY_FONT


def write_summary_sheet(sheet: Worksheet, tables: Sequence[Table], run_date: date, written: Written) -> None:
    """Tabelle impilate dalla colonna B, ciascuna con il titolo, separate da una riga vuota."""
    _write_title(sheet, config.WORKBOOK_ATTRIBUTI_TITLE, run_date)
    row = 4
    widths: dict[int, float] = defaultdict(float)
    for table in tables:
        sheet.cell(row, 2, table.title).font = BOLD
        row = write_table(sheet, table, row + 1, 2, written)
        for note in table.notes:
            row += 1
            sheet.cell(row, 2, note).font = GREY_FONT
        for offset, column in enumerate(table.columns):
            widths[offset] = max(widths[offset], column.width)
        row += 2
    for offset, width in widths.items():
        sheet.column_dimensions[get_column_letter(2 + offset)].width = width


def _write_bands(sheet: Worksheet, row: int, expanded: Sequence[Expanded], labels: Sequence[str]) -> None:
    """Riga sopra le intestazioni: l'etichetta di ogni valore unita sopra la sua coppia di colonne (come i breakdown)."""
    spans: list[tuple[str, int, int]] = []  # (etichetta, prima colonna, ultima colonna)
    previous: tuple[str, int | None] | None = None
    for col, (column, _, _, k) in enumerate(expanded, start=1):
        label = labels[k - 1] if k is not None else column.band
        if label and previous == (label, k):
            spans[-1] = (label, spans[-1][1], col)
        elif label:
            spans.append((label, col, col))
        previous = (label, k) if label else None
    for label, first, last in spans:
        cell = sheet.cell(row, first, label)
        cell.font = BOLD
        cell.alignment = BAND_ALIGNMENT
        if last > first:
            sheet.merge_cells(start_row=row, start_column=first, end_row=row, end_column=last)
    sheet.row_dimensions[row].height = config.WORKBOOK_BAND_HEIGHT


def _style_levels(sheet: Worksheet, first_row: int, table: Table, width: int) -> None:
    """Rientro del Territorio, Italia in grassetto, Regioni colorate; schema a righe chiuso al livello Regione
    (province livello 1, comuni livello 2, riepilogo sopra il dettaglio)."""
    levels = table.levels
    sheet.sheet_properties.outlinePr.summaryBelow = False
    sheet.sheet_format.outlineLevelRow = max(levels, default=1) - 1
    territorio = 1 + [column.key for column in table.columns].index("territorio")
    for index, level in enumerate(levels):
        row = first_row + index
        sheet.cell(row, territorio).alignment = Alignment(indent=level)
        for col in range(1, width + 1):
            if level == 0:
                sheet.cell(row, col).font = BOLD
            elif level == 1:
                sheet.cell(row, col).fill = REGION_FILL
        if level >= 2:
            sheet.row_dimensions[row].outline_level = level - 1
            sheet.row_dimensions[row].hidden = True
        if index + 1 < len(levels) and levels[index + 1] > level >= 1:
            sheet.row_dimensions[row].collapsed = True


def write_pivot_sheet(sheet: Worksheet, table: Table, run_date: date, written: Written) -> None:
    """Foglio per territorio: titolo, note grigie, etichette dei valori, intestazioni, righe Italia > Regione > Provincia > Comune."""
    sheet.sheet_view.showGridLines = False
    sheet.cell(1, 1, table.title).font = TITLE_FONT
    for offset, line in enumerate([_subtitle(run_date), *table.notes]):
        sheet.cell(2 + offset, 1, line).font = GREY_FONT
    band_row = 3 + len(table.notes) + 1  # una riga vuota tra le note e le intestazioni
    header_row = band_row + 1
    last_row = write_table(sheet, table, header_row, 1, written)
    expanded = expand(table.columns, table.rows)
    _write_bands(sheet, band_row, expanded, table.group_labels)
    _set_widths(sheet, expanded)
    _style_levels(sheet, header_row + 1, table, len(expanded))
    sheet.freeze_panes = f"{get_column_letter(config.WORKBOOK_PIVOT_FROZEN_COLUMNS + 1)}{header_row + 1}"
    sheet.auto_filter.ref = f"A{header_row}:{get_column_letter(len(expanded))}{last_row}"


# --- Legenda ---

def _number_text(value: float, number_format: str) -> str:
    """Numero in formato italiano come lo mostra il formato di Excel della colonna."""
    if number_format == config.WORKBOOK_FMT_SHARE:
        return f"{value * 100:.1f}".replace(".", ",") + "%"
    decimals = 1 if number_format == config.WORKBOOK_FMT_DECIMAL else 0
    return f"{value:,.{decimals}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _parse_date(column: Column, text: str) -> date:
    try:
        return datetime.strptime(text, config.WORKBOOK_DATE_DISPLAY_FORMAT).date()
    except ValueError:
        raise WorkbookError(
            f"Colonna {column.header}: «{text}» non è una data ISO (AAAA-MM-GG) né nel formato GG/MM/AAAA."
        ) from None


def _dates_in(column: Column, values: Iterable[Any]) -> list[date]:
    found = []
    for value in values:
        if isinstance(value, date):
            found.append(value)
        elif isinstance(value, str):
            found += [_parse_date(column, part) for part in value.split(SEP)]
    return found


def legend_values(column: Column, values: Sequence[Any]) -> str:
    """Testo "Valori" della Legenda dai valori scritti: intervallo per numeri e date, valori presenti per le categorie."""
    present = [value for value in values if value not in (None, "")]
    if column.values == config.WORKBOOK_VALUES_NUMBER:
        numbers = [value for value in present if isinstance(value, int | float) and not isinstance(value, bool)]
        if not numbers:
            return config.WORKBOOK_VALUES_NONE
        low, high = (_number_text(value, column.number_format) for value in (min(numbers), max(numbers)))
        return config.WORKBOOK_VALUES_RANGE.format(low=low, high=high) + (f" {column.unit}" if column.unit else "")
    if column.values == config.WORKBOOK_VALUES_DATE:
        dates = _dates_in(column, present)
        if not dates:
            return config.WORKBOOK_VALUES_NONE
        low, high = (day.strftime(config.WORKBOOK_DATE_DISPLAY_FORMAT) for day in (min(dates), max(dates)))
        return config.WORKBOOK_VALUES_RANGE.format(low=low, high=high)
    if column.values == config.WORKBOOK_VALUES_CATEGORY:
        parts = Counter(part for value in present for part in (value.split(SEP) if column.multi else [value]))
        ordered = sorted(parts, key=category_key)
        return config.WORKBOOK_VALUES_JOIN.join(ordered) if ordered else config.WORKBOOK_VALUES_NONE
    return column.values


def _legend_name(column: Column, size: int) -> str:
    if GROUP_K not in column.key or GROUP_K not in column.header:  # N. cammini, Incidenza: stessa intestazione per ogni k
        return column.header
    first, last = column.header.format(k=1), column.header.format(k=size)
    return first if size == 1 else config.WORKBOOK_GROUP_LABEL.format(first=first, last=last)


def download_periods(log_path: Path) -> dict[str, str]:
    """Per ogni fonte della Legenda la data (o il periodo) di scaricamento, dal registro delle richieste riuscite."""
    days: dict[str, list[str]] = defaultdict(list)
    for row in common.read_csv(log_path) if log_path.is_file() else []:
        for name, _, hosts, _ in config.WORKBOOK_LEGENDA_SOURCES:
            if row["host_key"] in hosts and row["status"] == config.WORKBOOK_LOG_OK_STATUS:
                days[name].append(row["utc_iso"].split("T")[0])
    periods = {}
    for name, *_ in config.WORKBOOK_LEGENDA_SOURCES:
        if not days[name]:
            periods[name] = config.WORKBOOK_DOWNLOAD_MISSING
            continue
        first, last = (date.fromisoformat(day).strftime(config.WORKBOOK_DATE_DISPLAY_FORMAT) for day in (min(days[name]), max(days[name])))
        template = config.WORKBOOK_DOWNLOAD_ONE if first == last else config.WORKBOOK_DOWNLOAD_RANGE
        periods[name] = template.format(first=first, last=last)
    return periods


def _legend_block(sheet: Worksheet, row: int, title: str, headers: Sequence[str]) -> int:
    sheet.cell(row, 2, title).font = SECTION_FONT
    _write_header(sheet, row + 1, 2, headers)
    return row + 2


def _legend_line(sheet: Worksheet, row: int, texts: Sequence[str]) -> None:
    for offset, text in enumerate(texts):
        sheet.cell(row, 2 + offset, text).alignment = WRAP_TOP


def write_legenda(sheet: Worksheet, run_date: date, downloads: Mapping[str, str], written: Written) -> None:
    """Scopo, fonti e dizionario di tutte le colonne dei fogli, foglio per foglio nell'ordine dei fogli."""
    expected = list(dict.fromkeys(legend_name(name) for name in config.WORKBOOK_SHEET_ORDER[1:]))
    if set(written.sheets) != set(expected):
        raise WorkbookError(f"La Legenda descrive {sorted(written.sheets)}, i fogli da descrivere sono {expected}.")
    _write_title(sheet, config.WORKBOOK_TITLE, run_date)
    for offset, width in enumerate(config.WORKBOOK_LEGENDA_WIDTHS):
        sheet.column_dimensions[get_column_letter(2 + offset)].width = width
    sheet.cell(4, 2, config.WORKBOOK_LEGENDA_SCOPE_TITLE).font = SECTION_FONT
    sheet.cell(5, 2, config.WORKBOOK_LEGENDA_SCOPE).alignment = WRAP_TOP
    sheet.merge_cells(start_row=5, start_column=2, end_row=5, end_column=1 + len(config.WORKBOOK_LEGENDA_WIDTHS))
    sheet.row_dimensions[5].height = config.WORKBOOK_HEADER_HEIGHT
    row = _legend_block(sheet, 7, config.WORKBOOK_LEGENDA_SOURCES_TITLE, config.WORKBOOK_LEGENDA_SOURCES_HEADERS)
    for name, provides, _, licence in config.WORKBOOK_LEGENDA_SOURCES:
        _legend_line(sheet, row, (name, provides, downloads.get(name, config.WORKBOOK_DOWNLOAD_MISSING), licence))
        row += 1
    row = _legend_block(sheet, row + 1, config.WORKBOOK_LEGENDA_COLUMNS_TITLE, config.WORKBOOK_LEGENDA_HEADERS)
    for name in expected:
        separator = config.WORKBOOK_LEGENDA_SEPARATOR.format(sheet=name, description=config.WORKBOOK_SHEET_DESCRIPTIONS[name])
        sheet.cell(row, 2, separator).font = BOLD
        row += 1
        for column in written.sheets[name]:
            size = written.group_size.get((name, column.key), 1)
            values = legend_values(column, written.values[(name, column.key)])
            _legend_line(sheet, row, (_legend_name(column, size), column.contains, values, column.reading))
            row += 1


# --- insieme ---

@dataclass(frozen=True)
class Prepared:
    """Dati controllati e pronti da scrivere: righe dei fogli di dati, albero dei territori, tabelle di Cammini per attributo."""

    comuni: list[Row]
    cammini: list[Row]
    coppie: list[Row]
    tree: list[Territory]
    attributes: list[Table]


def prepare(
    coppie_raw: Sequence[Mapping[str, str]], cammini_raw: Sequence[Mapping[str, str]], istat: Sequence[Row],
    links: Mapping[str, str],
) -> Prepared:
    """Legge i tipi, controlla le invarianti sui dati di ingresso e calcola le righe di ogni foglio."""
    cammini = [
        _typed_row(row, config.WORKBOOK_CAMMINI_FIELDS, config.PAIRS_FILES["cammini"], config.WORKBOOK_CAMMINI_BLANK_ENUM_KEYS)
        for row in cammini_raw
    ]
    pairs = [_typed_row(row, config.WORKBOOK_COPPIE_FIELDS, config.PAIRS_FILES["coppie"]) for row in coppie_raw]
    by_id = check_cammini_ids(cammini, pairs)
    check_mezzo_blank_only_without_pairs(cammini, pairs)
    for pair in pairs:  # il nome del cammino è quello dell'elenco dei cammini, in tutti i fogli
        pair["nome"] = by_id[pair[UNICO]]["nome"]
    check_comune_codes(pairs, {row[COMUNE] for row in istat})
    check_duplicate_pairs(pairs)
    routes = check_routes(by_id, pairs)
    ordered = sorted(cammini, key=lambda cammino: (*_sort_name(cammino["nome"]), cammino[UNICO]))
    comuni = comuni_rows(pairs)
    check_comuni_rows(comuni, len(pairs))
    shown = cammini_rows(ordered, routes, links)
    tree = territory_tree(istat, pairs)
    check_tree(tree, comuni, sum(1 for route in routes.values() if route))
    comuni_of = {cid: {pair[COMUNE] for pair in route} for cid, route in routes.items()}
    attributes = [dimension_table(key, multi, shown, comuni_of) for _, key, multi, _ in config.WORKBOOK_DIMENSIONS]
    return Prepared(comuni, shown, coppie_rows(ordered, routes), tree, [*attributes, cross_table(shown)])


def build_workbook(
    coppie_raw: Sequence[Mapping[str, str]], cammini_raw: Sequence[Mapping[str, str]], istat: Sequence[Row],
    links: Mapping[str, str], run_date: date, downloads: Mapping[str, str] | None = None,
) -> Workbook:
    """Controlla le invarianti e costruisce la cartella di lavoro in memoria; non scrive file."""
    data = prepare(coppie_raw, cammini_raw, istat, links)
    workbook = Workbook()
    legenda = workbook.active
    legenda.title = config.WORKBOOK_SHEET_LEGENDA
    written = Written()
    write_data_sheet(workbook.create_sheet(config.WORKBOOK_SHEET_COMUNI), Table(config.WORKBOOK_COMUNI_COLUMNS, data.comuni), written)
    write_data_sheet(workbook.create_sheet(config.WORKBOOK_SHEET_CAMMINI), Table(config.WORKBOOK_CAMMINI_COLUMNS, data.cammini), written)
    write_pivot_sheet(workbook.create_sheet(config.WORKBOOK_SHEET_TERRITORI), territori_table(data.tree), run_date, written)
    shown_by_id = {row[UNICO]: row for row in data.cammini}
    for dimension, sheet_name in zip(config.WORKBOOK_DIMENSIONS, config.WORKBOOK_PIVOT_SHEETS):
        write_pivot_sheet(workbook.create_sheet(sheet_name), pivot_table(dimension, data.tree, shown_by_id), run_date, written)
    write_summary_sheet(workbook.create_sheet(config.WORKBOOK_SHEET_ATTRIBUTI), data.attributes, run_date, written)
    write_data_sheet(workbook.create_sheet(config.WORKBOOK_SHEET_COPPIE), Table(config.WORKBOOK_COPPIE_COLUMNS, data.coppie), written)
    write_legenda(legenda, run_date, downloads or {}, written)
    if tuple(workbook.sheetnames) != config.WORKBOOK_SHEET_ORDER:
        raise WorkbookError(f"Ordine dei fogli {workbook.sheetnames} diverso da {list(config.WORKBOOK_SHEET_ORDER)}.")
    return workbook


def output_path(output_dir: Path, run_date: date) -> Path:
    return output_dir / config.WORKBOOK_FILE_TEMPLATE.format(data=run_date.strftime(config.WORKBOOK_FILE_DATE_FORMAT))


def run(
    input_dir: Path = config.PAIRS_DIR, output_dir: Path = config.WORKBOOK_OUTPUT_DIR,
    run_date: date | None = None, requests_log: Path | None = None,
) -> Path:
    run_date = run_date or date.today()
    coppie = _read_table(input_dir / config.PAIRS_FILES["coppie"], config.WORKBOOK_COPPIE_FIELDS)
    cammini = _read_table(input_dir / config.PAIRS_FILES["cammini"], config.WORKBOOK_CAMMINI_FIELDS)
    workbook = build_workbook(
        coppie, cammini, load_istat_table(), card_links(), run_date, download_periods(requests_log or config.REQUESTS_LOG)
    )
    target = output_path(output_dir, run_date)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        workbook.save(target)
    except PermissionError:
        raise WorkbookError(f"Impossibile scrivere {target}: il file è aperto in Excel? Chiuderlo e rilanciare.") from None
    for sheet in workbook.worksheets:
        print(f"workbook: foglio {sheet.title}: {sheet.max_row} righe")
    print(f"workbook: scritto {target}")
    return target
