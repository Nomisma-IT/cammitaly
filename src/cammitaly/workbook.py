"""Cartella di lavoro Excel: comuni attraversati da ogni cammino (fogli Comuni, Cammini, Coppie, Note).

Legge i due file del contratto P5 (coppie_comuni.csv, cammini.csv), controlla tutte le invarianti PRIMA di
costruire il file e non scrive nulla se una e' violata.
"""
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import geopandas as gpd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.worksheet.worksheet import Worksheet

from cammitaly import config
from cammitaly.geometry import common

Row = dict[str, Any]
COMUNE_CODE_PATTERN = re.compile(rf"\d{{{config.WORKBOOK_CODE_DIGITS[config.COMUNE_ID_COL]}}}")
CODE_KEYS = frozenset(config.WORKBOOK_CODE_DIGITS)
BOLD = Font(bold=True)


class WorkbookError(ValueError):
    """Dato di ingresso mancante o invariante violata: la cartella di lavoro non viene scritta."""


@dataclass(frozen=True)
class Detail:
    """Riga di dettaglio di un comune: cammini con lo stesso mezzo e lo stesso carattere religioso."""

    mezzo: str
    religioso: str
    n_cammini: int
    names: tuple[str, ...]


@dataclass(frozen=True)
class ComuneBlock:
    """Un comune: i suoi dati anagrafici (da una coppia), il totale dei cammini e le righe di dettaglio."""

    place: Row
    n_cammini: int
    names: tuple[str, ...]
    details: tuple[Detail, ...]


# --- lettura e conversione dei tipi ---

def load_istat_codes(path: Path = config.ISTAT_COMUNI_SHP) -> set[str]:
    """Codici PRO_COM_T dei comuni ISTAT, letti dalla sola tabella degli attributi."""
    if not path.is_file():
        raise WorkbookError(f"Manca il file dei confini comunali ISTAT: {path}")
    table = gpd.read_file(
        path, engine="pyogrio", encoding=config.ISTAT_ENCODING, ignore_geometry=True, columns=[config.COMUNE_ID_COL]
    )
    return set(table[config.COMUNE_ID_COL].astype(str))


def _read_table(path: Path, fields: Sequence[str]) -> list[Row]:
    if not path.is_file():
        raise WorkbookError(f"Manca il file {path.name} in {path.parent}: va prodotto prima con il comando 'pairs'.")
    rows = common.read_csv(path)
    if not rows:
        raise WorkbookError(f"Il file {path.name} è vuoto.")
    missing = [field for field in fields if field not in rows[0]]
    if missing:
        raise WorkbookError(f"Nel file {path.name} mancano le colonne: {', '.join(missing)}.")
    return rows


def _parse_code(key: str, text: str) -> str:
    if key == config.COMUNE_ID_COL:  # la chiave dei comuni non si ricostruisce: 6 cifre o errore
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


def _parse_value(key: str, text: str) -> Any:
    if key in config.WORKBOOK_CODE_DIGITS:
        return _parse_code(key, text)
    if key in config.WORKBOOK_INT_KEYS:
        return int(text)
    if key in config.WORKBOOK_FLOAT_KEYS:
        return float(text) if text else None
    if key == config.WORKBOOK_BOOL_KEY:
        return _parse_bool(text)
    if key in config.WORKBOOK_ENUM_KEYS and text not in config.WORKBOOK_VALUE_LABELS[key]:
        raise ValueError(text)
    return text


def _typed_row(raw: Mapping[str, str], fields: Sequence[str], file_name: str) -> Row:
    who = raw.get("cammino_unico_id", "?")
    typed: Row = {}
    for key in fields:
        try:
            typed[key] = _parse_value(key, raw[key].strip())
        except ValueError:
            comune = f", comune {raw[config.COMUNE_ID_COL]}" if config.COMUNE_ID_COL in raw else ""
            raise WorkbookError(
                f"{file_name}, cammino {who}{comune}: valore non valido nella colonna '{key}': {raw[key]!r}."
            ) from None
    return typed


# --- invarianti ---

def check_cammini_ids(cammini: Sequence[Row], pairs: Sequence[Row]) -> dict[str, Row]:
    """Ogni cammino compare una sola volta nell'elenco e ogni coppia rimanda a un cammino dell'elenco."""
    by_id: dict[str, Row] = {}
    for cammino in cammini:
        cid = cammino["cammino_unico_id"]
        if cid in by_id:
            raise WorkbookError(f"Il cammino {cid} compare più di una volta nell'elenco dei cammini.")
        by_id[cid] = cammino
    unknown = sorted({pair["cammino_unico_id"] for pair in pairs} - by_id.keys())
    if unknown:
        raise WorkbookError(f"Coppie con cammini assenti dall'elenco dei cammini: {', '.join(unknown)}.")
    return by_id


def check_comune_codes(pairs: Iterable[Row], istat_codes: set[str]) -> None:
    unknown = sorted({pair[config.COMUNE_ID_COL] for pair in pairs} - istat_codes)
    if unknown:
        raise WorkbookError(
            f"Codici comune assenti dai confini ISTAT {config.ISTAT_EDITION_YEAR}: {', '.join(unknown[:10])}"
            + (f" e altri {len(unknown) - 10}." if len(unknown) > 10 else ".")
        )


def check_duplicate_pairs(pairs: Iterable[Row]) -> None:
    seen: set[tuple[str, str]] = set()
    for pair in pairs:
        key = (pair["cammino_unico_id"], pair[config.COMUNE_ID_COL])
        if key in seen:
            raise WorkbookError(f"La coppia cammino {key[0]} e comune {key[1]} compare più di una volta.")
        seen.add(key)


def check_routes(by_id: Mapping[str, Row], pairs: Iterable[Row]) -> dict[str, list[Row]]:
    """Per cammino: ordine contiguo da 1 a n e numero di comuni uguale a n_comuni. Ritorna le coppie in ordine di percorso."""
    routes: dict[str, list[Row]] = defaultdict(list)
    for pair in pairs:
        routes[pair["cammino_unico_id"]].append(pair)
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


def check_blocks(blocks: Iterable[ComuneBlock], pairs: Iterable[Row]) -> None:
    """Per comune: il totale è il numero di cammini distinti nelle coppie e il dettaglio somma al totale."""
    expected: dict[str, set[str]] = defaultdict(set)
    for pair in pairs:
        expected[pair[config.COMUNE_ID_COL]].add(pair["cammino_unico_id"])
    for block in blocks:
        code = block.place[config.COMUNE_ID_COL]
        detail_sum = sum(detail.n_cammini for detail in block.details)
        if block.n_cammini != len(expected[code]) or detail_sum != block.n_cammini:
            raise WorkbookError(
                f"Comune {code} ({block.place['comune']}): totale {block.n_cammini}, "
                f"cammini distinti nelle coppie {len(expected[code])}, somma del dettaglio {detail_sum}."
            )


# --- costruzione dei dati dei fogli ---

def _sorted_names(names: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(names, key=lambda name: (common.normalize_name(name), name)))


def _comune_block(group: Sequence[Row]) -> ComuneBlock:
    names_by_id = {pair["cammino_unico_id"]: pair["nome"] for pair in group}
    details = []
    for mezzo in config.WORKBOOK_MEZZO_LABELS:
        for religioso in config.WORKBOOK_SI_NO_LABELS:
            combo = {
                pair["cammino_unico_id"]: pair["nome"]
                for pair in group if pair["mezzo"] == mezzo and pair["religioso"] == religioso
            }
            if combo:
                details.append(Detail(mezzo, religioso, len(combo), _sorted_names(combo.values())))
    return ComuneBlock(group[0], len(names_by_id), _sorted_names(names_by_id.values()), tuple(details))


def build_blocks(pairs: Iterable[Row]) -> list[ComuneBlock]:
    """Un blocco per comune, ordinato per codice regione, codice provincia e nome del comune."""
    groups: dict[str, list[Row]] = defaultdict(list)
    for pair in pairs:
        groups[pair[config.COMUNE_ID_COL]].append(pair)
    blocks = [_comune_block(group) for group in groups.values()]
    blocks.sort(key=lambda block: (
        block.place["cod_reg"], block.place["cod_uts"],
        common.normalize_name(block.place["comune"]), block.place[config.COMUNE_ID_COL],
    ))
    return blocks


def _cammino_order(cammini: Iterable[Row]) -> list[Row]:
    return sorted(cammini, key=lambda cammino: (common.normalize_name(cammino["nome"]), cammino["cammino_unico_id"]))


def _display_dates(parts: Sequence[str]) -> Any:
    """Una sola data ISO (AAAA-MM-GG) e' una vera data di Excel (formato GG/MM/AAAA); piu' date sono testo GG/MM/AAAA unito
    dal separatore di cella; un testo che non e' una data resta com'e'."""
    try:
        dates = [date.fromisoformat(part) for part in parts]
    except ValueError:
        return config.WORKBOOK_CELL_SEPARATOR.join(parts) or None
    if len(dates) == 1:
        return dates[0]
    return config.WORKBOOK_CELL_SEPARATOR.join(day.strftime(config.WORKBOOK_DATE_DISPLAY_FORMAT) for day in dates) or None


def _display(key: str, value: Any) -> Any:
    """Valore come appare nel foglio: Sì/No, etichette italiane, date, elenchi con il separatore di cella."""
    if isinstance(value, bool):
        return config.WORKBOOK_SI_NO_LABELS[config.SI if value else config.NO]
    if not isinstance(value, str):
        return value
    labels = config.WORKBOOK_VALUE_LABELS.get(key, {})
    parts = value.split(config.LIST_SEPARATOR) if key in config.WORKBOOK_LIST_KEYS else [value]
    if key in config.WORKBOOK_DATE_KEYS:
        return _display_dates([part for part in parts if part])
    shown = [labels.get(part, part) for part in parts if part]
    if key in config.WORKBOOK_WORD_KEYS:  # valori scritti come slug ("arte-e-cultura"): si leggono come parole
        shown = [text.replace(config.WORKBOOK_SLUG_SEPARATOR, " ") for text in shown]
    return config.WORKBOOK_CELL_SEPARATOR.join(shown) or None


# --- scrittura dei fogli ---

def _append(sheet: Worksheet, keys: Sequence[str], row: Mapping[str, Any]) -> int:
    """Aggiunge una riga; i codici ISTAT restano testo. Ritorna il numero di riga."""
    sheet.append([_display(key, row.get(key)) for key in keys])
    for column, key in enumerate(keys, start=1):
        cell = sheet.cell(sheet.max_row, column)
        if key in CODE_KEYS:
            cell.number_format = "@"
        elif isinstance(cell.value, date):
            cell.number_format = config.WORKBOOK_DATE_NUMBER_FORMAT
    return sheet.max_row


def _write_header(sheet: Worksheet, headers: Sequence[str]) -> None:
    sheet.append(list(headers))
    for cell in sheet[1]:
        cell.font = BOLD
    sheet.freeze_panes = config.WORKBOOK_FREEZE_CELL


def _fit_widths(sheet: Worksheet) -> None:
    for column in sheet.columns:
        longest = max((len(str(cell.value)) for cell in column if cell.value is not None), default=0)
        width = min(max(longest + config.WORKBOOK_WIDTH_PADDING, config.WORKBOOK_WIDTH_MIN), config.WORKBOOK_WIDTH_MAX)
        sheet.column_dimensions[column[0].column_letter].width = width


def _write_comuni(sheet: Worksheet, blocks: Iterable[ComuneBlock]) -> None:
    keys = config.WORKBOOK_COMUNI_KEYS
    _write_header(sheet, [config.WORKBOOK_HEADERS[key] for key in keys])
    sheet.sheet_properties.outlinePr.summaryBelow = False  # il totale sta SOPRA il suo dettaglio
    sheet.sheet_format.outlineLevelRow = config.WORKBOOK_OUTLINE_LEVEL
    for block in blocks:
        base = {**block.place, "n_cammini": block.n_cammini}
        total_row = _append(sheet, keys, {
            **base, "mezzo": config.WORKBOOK_ALL_LABEL, "religioso": config.WORKBOOK_ALL_LABEL,
            "cammini": config.WORKBOOK_CELL_SEPARATOR.join(block.names),
        })
        for cell in sheet[total_row]:
            cell.font = BOLD
        sheet.row_dimensions[total_row].collapsed = True
        for detail in block.details:
            row = _append(sheet, keys, {
                **base, "mezzo": detail.mezzo, "religioso": detail.religioso, "n_cammini": detail.n_cammini,
                "cammini": config.WORKBOOK_CELL_SEPARATOR.join(detail.names),
            })
            sheet.row_dimensions[row].outline_level = config.WORKBOOK_OUTLINE_LEVEL
            sheet.row_dimensions[row].hidden = True
    sheet.auto_filter.ref = sheet.dimensions
    _fit_widths(sheet)


def _ragged_comuni(route: Sequence[Row]) -> dict[str, str]:
    return {
        config.WORKBOOK_RAGGED_HEADER.format(numero=number): config.WORKBOOK_RAGGED_FORMAT.format(
            comune=pair["comune"], sigla=pair["sigla"]
        )
        for number, pair in enumerate(route, start=1)
    }


def _write_cammini(sheet: Worksheet, ordered: Sequence[Row], routes: Mapping[str, Sequence[Row]]) -> None:
    keys = config.WORKBOOK_CAMMINI_FIELDS
    widest = max((len(routes[cammino["cammino_unico_id"]]) for cammino in ordered), default=0)
    ragged_headers = [config.WORKBOOK_RAGGED_HEADER.format(numero=number) for number in range(1, widest + 1)]
    _write_header(sheet, [*(config.WORKBOOK_HEADERS[key] for key in keys), *ragged_headers])
    for cammino in ordered:
        ragged = _ragged_comuni(routes[cammino["cammino_unico_id"]])
        _append(sheet, [*keys, *ragged_headers], {**cammino, **ragged})
    sheet.auto_filter.ref = sheet.dimensions
    _fit_widths(sheet)


def _write_coppie(sheet: Worksheet, ordered: Sequence[Row], routes: Mapping[str, Sequence[Row]]) -> None:
    keys = config.WORKBOOK_COPPIE_KEYS
    _write_header(sheet, [config.WORKBOOK_HEADERS[key] for key in keys])
    for cammino in ordered:
        for pair in routes[cammino["cammino_unico_id"]]:
            _append(sheet, keys, {**pair, **{tag: cammino[tag] for tag in config.WORKBOOK_TAG_FIELDS}})
    sheet.auto_filter.ref = sheet.dimensions
    _fit_widths(sheet)


def _period_text(days: Sequence[str]) -> str:
    """'il 03/11/2026' o 'dal 03/11/2026 al 05/11/2026' dai giorni (AAAA-MM-GG) delle richieste di una fonte."""
    if not days:
        return config.WORKBOOK_DOWNLOAD_MISSING
    first, last = (date.fromisoformat(day).strftime(config.WORKBOOK_DATE_DISPLAY_FORMAT) for day in (min(days), max(days)))
    template = config.WORKBOOK_DOWNLOAD_ONE if first == last else config.WORKBOOK_DOWNLOAD_RANGE
    return template.format(first=first, last=last)


def download_periods(log_path: Path) -> dict[str, str]:
    """Per ogni fonte (segnaposto del foglio Note) il periodo di scarico, dal registro delle richieste riuscite.

    Si legge solo il registro che c'e': senza registro o senza richieste di una fonte vale WORKBOOK_DOWNLOAD_MISSING."""
    days: dict[str, list[str]] = defaultdict(list)
    for row in common.read_csv(log_path) if log_path.is_file() else []:
        for placeholder, hosts in config.WORKBOOK_SOURCE_HOSTS.items():
            if row["host_key"] in hosts and row["status"] == config.WORKBOOK_LOG_OK_STATUS:
                days[placeholder].append(row["utc_iso"].split("T")[0])
    return {placeholder: _period_text(days[placeholder]) for placeholder in config.WORKBOOK_SOURCE_HOSTS}


def _write_note(sheet: Worksheet, run_date: date, downloads: Mapping[str, str]) -> None:
    parameters = {
        "distanza_m": config.CROSSING_DISTANCE_M, "anno_istat": config.ISTAT_EDITION_YEAR,
        "data": run_date.strftime(config.WORKBOOK_DATE_DISPLAY_FORMAT),
        **{placeholder: downloads.get(placeholder, config.WORKBOOK_DOWNLOAD_MISSING) for placeholder in config.WORKBOOK_SOURCE_HOSTS},
    }
    for index, (title, lines) in enumerate(config.WORKBOOK_NOTE_SECTIONS):
        if index:
            sheet.append([None])
        sheet.append([title])
        sheet.cell(sheet.max_row, 1).font = BOLD
        for line in lines:
            sheet.append([line.format(**parameters)])
            sheet.cell(sheet.max_row, 1).alignment = Alignment(wrap_text=True, vertical="top")
    sheet.column_dimensions["A"].width = config.WORKBOOK_NOTE_WIDTH


# --- insieme ---

def build_workbook(
    coppie_rows: Sequence[Mapping[str, str]], cammini_rows: Sequence[Mapping[str, str]],
    istat_codes: set[str], run_date: date, downloads: Mapping[str, str] | None = None,
) -> Workbook:
    """Controlla le invarianti e costruisce la cartella di lavoro in memoria; non scrive file."""
    cammini = [_typed_row(row, config.WORKBOOK_CAMMINI_FIELDS, config.PAIRS_FILES["cammini"]) for row in cammini_rows]
    pairs = [_typed_row(row, config.WORKBOOK_COPPIE_FIELDS, config.PAIRS_FILES["coppie"]) for row in coppie_rows]
    by_id = check_cammini_ids(cammini, pairs)
    for pair in pairs:  # il nome del cammino è quello dell'elenco dei cammini, in tutti i fogli
        pair["nome"] = by_id[pair["cammino_unico_id"]]["nome"]
    check_comune_codes(pairs, istat_codes)
    check_duplicate_pairs(pairs)
    routes = check_routes(by_id, pairs)
    blocks = build_blocks(pairs)
    check_blocks(blocks, pairs)
    ordered = _cammino_order(cammini)
    workbook = Workbook()
    comuni = workbook.active
    comuni.title = config.WORKBOOK_SHEET_COMUNI
    _write_comuni(comuni, blocks)
    _write_cammini(workbook.create_sheet(config.WORKBOOK_SHEET_CAMMINI), ordered, routes)
    _write_coppie(workbook.create_sheet(config.WORKBOOK_SHEET_COPPIE), ordered, routes)
    _write_note(workbook.create_sheet(config.WORKBOOK_SHEET_NOTE), run_date, downloads or {})
    return workbook


def run(
    input_dir: Path = config.PAIRS_DIR, output_dir: Path = config.WORKBOOK_OUTPUT_DIR,
    istat_shp: Path = config.ISTAT_COMUNI_SHP, run_date: date | None = None, requests_log: Path | None = None,
) -> Path:
    run_date = run_date or date.today()
    coppie = _read_table(input_dir / config.PAIRS_FILES["coppie"], config.WORKBOOK_COPPIE_FIELDS)
    cammini = _read_table(input_dir / config.PAIRS_FILES["cammini"], config.WORKBOOK_CAMMINI_FIELDS)
    workbook = build_workbook(coppie, cammini, load_istat_codes(istat_shp), run_date, download_periods(requests_log or config.REQUESTS_LOG))
    target = output_dir / config.WORKBOOK_FILE_TEMPLATE.format(data=run_date.isoformat())
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        workbook.save(target)
    except PermissionError:
        raise WorkbookError(f"Impossibile scrivere {target}: il file è aperto in Excel? Chiuderlo e rilanciare.") from None
    for sheet in workbook.worksheets:
        print(f"workbook: foglio {sheet.title}: {sheet.max_row} righe (intestazione compresa)")
    print(f"workbook: scritto {target}")
    return target
