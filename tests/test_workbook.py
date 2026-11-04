"""Cartella di lavoro: fixture sintetica scritta secondo il contratto delle coppie, riletta con openpyxl dal file salvato.

Nessun test legge data/: gli ingressi sono costruiti qui e le uscite vanno nelle cartelle temporanee di pytest."""
import csv
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest
from openpyxl import load_workbook
from openpyxl.workbook import Workbook

from cammitaly import __main__ as cli
from cammitaly import config, workbook

RUN_DATE = date(2026, 11, 4)
D = config.CROSSING_DISTANCE_M
AGLIE, TORINO, NOVARA, CASTRO_BG, CASTRO_LE, ROMA = "001001", "001272", "003106", "016066", "075014", "058091"
SHEETS = [
    "Legenda", "Comuni", "Cammini", "Territori", "Territori x Religioso", "Territori x Mezzo", "Territori x Forma",
    "Territori x Portata", "Territori x Tema", "Territori x Stagione", "Territori x Segnaletica",
    "Territori x Credenziale", "Territori x Catalogo", "Territori x Fonte", "Cammini per attributo", "Coppie",
]
PIVOTS = SHEETS[4:14]
LEVEL = {label: level for level, label in enumerate(("Italia", "Regione", "Provincia", "Comune"))}


def place(code: str, comune: str, sigla: str, uts: str, provincia: str, reg: str, regione: str) -> dict[str, str]:
    return {
        "PRO_COM_T": code, "comune": comune, "sigla": sigla, "cod_uts": uts, "provincia": provincia, "cod_reg": reg,
        "regione": regione,
    }


# Novara (provincia) e Lazio (regione) non sono attraversati: restano nei fogli per territorio con zero cammini
ISTAT = [
    place(AGLIE, "Agliè", "TO", "201", "Torino", "01", "Piemonte"), place(TORINO, "Torino", "TO", "201", "Torino", "01", "Piemonte"),
    place(NOVARA, "Novara", "NO", "003", "Novara", "01", "Piemonte"),
    place(CASTRO_BG, "Castro", "BG", "016", "Bergamo", "03", "Lombardia"),
    place(CASTRO_LE, "Castro", "LE", "075", "Lecce", "16", "Puglia"), place(ROMA, "Roma", "RM", "058", "Roma", "12", "Lazio"),
]
PLACES = {row["PRO_COM_T"]: row for row in ISTAT}
LINKS = {
    "cdi:1": "https://cdi.example/uno", "cdi:2": "https://cdi.example/due", "mitur:uno": "https://mitur.example/page/2/",
    "mitur:tre": "https://mitur.example/",
}
TAGS = {  # forma, portata, tema, stagione, credenziale, segnaletica, data
    "u001": ("anello|lineare", "nazionale|regionale", "spirituale", "primavera|autunno", "si", "no", "2025-05-05"),
    "u002": ("anello", "", "arte-e-cultura|coast-to-coast", "", "no", "si", "2023-06-16|2024-02-01"),
    "u003": ("", "", "", "", "", "", ""),
}


def pair(cid: str, nome: str, code: str, ordine: int, mezzo: str, religioso: str, fonte: str, dentro: str = "True") -> dict[str, str]:
    row = PLACES[code]
    return {
        "cammino_unico_id": cid, "nome": nome, "PRO_COM_T": code, "comune": row["comune"], "sigla": row["sigla"],
        "cod_uts": row["cod_uts"], "provincia": row["provincia"], "cod_reg": row["cod_reg"], "regione": row["regione"],
        "ordine": str(ordine), "mezzo": mezzo, "religioso": religioso, "distanza_m": "12.5", "dentro": dentro,
        "fonte_geometria": fonte,
    }


def coppie() -> list[dict[str, str]]:
    # Righe fuori ordine di proposito: i fogli le riordinano
    return [
        pair("u002", "Cammino Beta", CASTRO_LE, 2, "entrambi", "no", "osm", "False"),
        pair("u001", "Cammino Alfa", CASTRO_BG, 3, "entrambi", "si", "mitur"),
        pair("u001", "Cammino Alfa", TORINO, 2, "piedi", "si", "cdi"),
        pair("u002", "Cammino Beta", TORINO, 1, "bici", "no", "osm"),
        pair("u001", "Cammino Alfa", AGLIE, 1, "piedi", "si", "cdi|mitur"),
    ]


def cammino(cid: str, nome: str, n_comuni: int, **extra: str) -> dict[str, str]:
    forma, portata, tema, stagione, credenziale, segnaletica, data = TAGS[cid]
    alfa = cid == "u001"
    row = {
        "cammino_unico_id": cid, "nome": nome, "alias": "Alias del cammino|Altro nome" if alfa else "",
        "fonti": "cdi|mitur" if alfa else ("osm" if cid == "u002" else "mitur"),
        "membri": {"u001": "cdi:1|cdi:2|mitur:uno", "u002": "osm:r9", "u003": "mitur:tre"}[cid],
        "mezzo": {"u001": "entrambi", "u002": "entrambi", "u003": ""}[cid], "religioso": "si" if alfa else "no",
        "evidenza_religioso": "Catalogo|OpenStreetMap: pellegrinaggio" if alfa else "nessuna fonte",
        "tipologia": forma, "portata": portata, "tema": tema, "stagione": stagione, "credenziale": credenziale,
        "segnaletica": segnaletica, "data_catalogo": data, "regioni": "piemonte|lombardia",
        "lunghezza_dichiarata_km": "120", "lunghezza_tracciata_km": "" if cid == "u003" else "118.4",
        "copertura": "" if cid == "u003" else "0.99",
        "fonte_geometria": {"u001": "cdi|mitur", "u002": "osm", "u003": "nessuna"}[cid], "nota": "", "n_comuni": str(n_comuni),
    }
    return {**row, **extra}


def cammini() -> list[dict[str, str]]:
    return [cammino("u003", "Cammino Gamma", 0), cammino("u002", "Cammino Beta", 2), cammino("u001", "Cammino Alfa", 3)]


def prepare(coppie_rows=None, cammini_rows=None, istat=None) -> workbook.Prepared:
    return workbook.prepare(
        coppie() if coppie_rows is None else coppie_rows, cammini() if cammini_rows is None else cammini_rows,
        ISTAT if istat is None else istat, LINKS,
    )


def build(coppie_rows=None, cammini_rows=None, istat=None, downloads=None) -> Workbook:
    return workbook.build_workbook(
        coppie() if coppie_rows is None else coppie_rows, cammini() if cammini_rows is None else cammini_rows,
        ISTAT if istat is None else istat, LINKS, RUN_DATE, downloads,
    )


@pytest.fixture(scope="module")
def saved(tmp_path_factory: pytest.TempPathFactory) -> Workbook:
    path = tmp_path_factory.mktemp("xlsx") / "prova.xlsx"
    build().save(path)
    return load_workbook(path)


# --- lettura dei fogli ---

def sheet_rows(sheet, header_row: int = 1) -> list[dict[str, object]]:
    headers = [cell.value for cell in sheet[header_row]]
    return [dict(zip(headers, row)) for row in sheet.iter_rows(min_row=header_row + 1, values_only=True)]


def header_row_of(sheet) -> int:
    return next(number for number in range(1, sheet.max_row + 1) if sheet.cell(number, 1).value == "Livello")


def pivot(sheet) -> dict[tuple[str, str | None], dict[str, object]]:
    """Riga per (livello, codice): nome, Cammini, Comuni attraversati e {etichetta del valore: (N. cammini, Incidenza)}."""
    header = header_row_of(sheet)
    labels = {sheet.cell(header - 1, col).value: col for col in range(4, sheet.max_column + 1) if sheet.cell(header - 1, col).value}
    total = labels.pop(config.WORKBOOK_TOTAL_LABEL)
    rows = {}
    for number in range(header + 1, sheet.max_row + 1):
        cell = lambda col: sheet.cell(number, col).value  # noqa: E731
        rows[(cell(1), cell(2))] = {
            "name": cell(3), "cammini": cell(total), "crossed": cell(total + 1),
            "values": {label: (cell(col), cell(col + 1)) for label, col in labels.items()},
        }
    return rows


def summary_blocks(sheet) -> dict[str, dict[str, list]]:
    """Tabelle impilate di Cammini per attributo: titolo -> intestazioni, righe, note."""
    blocks: dict[str, dict[str, list]] = {}
    state, title = None, ""
    for number in range(4, sheet.max_row + 1):
        cells = [sheet.cell(number, col).value for col in range(2, sheet.max_column + 1)]
        filled = [value for value in cells if value is not None]
        if not filled:
            state = None
        elif state is None and len(filled) == 1 and sheet.cell(number, 2).font.bold:
            title, state = filled[0], "title"
            blocks[title] = {"headers": [], "rows": [], "notes": []}
        elif state == "title":
            blocks[title]["headers"], state = [value for value in cells if value is not None], "rows"
        elif len(filled) == 1:
            blocks[title]["notes"].append(filled[0])
        else:
            blocks[title]["rows"].append(tuple(cells[: len(blocks[title]["headers"])]))
    return blocks


def legend_sections(sheet) -> dict[str, list[tuple]]:
    """Dizionario delle colonne della Legenda: nome del foglio della riga di separazione -> (colonna, contiene, valori, lettura)."""
    start = next(number for number in range(1, sheet.max_row + 1) if sheet.cell(number, 2).value == "Colonna")
    sections: dict[str, list[tuple]] = {}
    current = ""
    for number in range(start + 1, sheet.max_row + 1):
        texts = [sheet.cell(number, col).value for col in (2, 3, 4, 5)]
        if texts[0] is None:
            continue
        if texts[1:] == [None] * 3:
            current = texts[0].split(": ", 1)[0]
            sections[current] = []
        else:
            sections[current].append(tuple(texts))
    return sections


def stem(name: str) -> str:
    """Nome di colonna senza il numero del gruppo ripetuto: 'Cammino 2' e 'Cammino 1 ... Cammino 3' diventano 'Cammino'."""
    return re.sub(r" (\d+|\{k\})$", "", re.sub(r" \.\.\. .*$", "", name))


def stems(names) -> list[str]:
    return list(dict.fromkeys(stem(name) for name in names))


# --- nome del file e ordine dei fogli ---

def test_file_name_pattern_has_day_month_year_and_no_underscore(tmp_path: Path) -> None:
    target = workbook.output_path(tmp_path, RUN_DATE)
    assert target == tmp_path / "Cammini Comuni 04.11.2026.xlsx" and "_" not in target.name
    assert workbook.output_path(tmp_path, date(2027, 1, 9)).name == "Cammini Comuni 09.01.2027.xlsx"


def test_sheets_in_the_agreed_order(saved: Workbook) -> None:
    assert saved.sheetnames == SHEETS == list(config.WORKBOOK_SHEET_ORDER)


def test_wrong_sheet_order_stops_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    swapped = (*config.WORKBOOK_SHEET_ORDER[:1], config.WORKBOOK_SHEET_CAMMINI, config.WORKBOOK_SHEET_COMUNI, *config.WORKBOOK_SHEET_ORDER[3:])
    monkeypatch.setattr(config, "WORKBOOK_SHEET_ORDER", swapped)
    with pytest.raises(workbook.WorkbookError, match="Ordine dei fogli"):
        build()


# --- registro delle colonne = intestazioni = Legenda ---

def test_registry_headers_and_legenda_describe_the_same_columns(saved: Workbook) -> None:
    legend = legend_sections(saved["Legenda"])
    assert list(legend) == ["Comuni", "Cammini", "Territori", config.WORKBOOK_PIVOT_LEGEND_NAME, "Cammini per attributo", "Coppie"]
    blocks = summary_blocks(saved["Cammini per attributo"])
    unused = {config.WORKBOOK_MEZZO_LABELS["bici"], config.WORKBOOK_MEZZO_LABELS["piedi"]}  # nessun cammino dell'esempio le usa
    registry_summary = [*config.WORKBOOK_SUMMARY_COLUMNS, *config.WORKBOOK_SUMMARY_CROSS_COLUMNS]
    written = {
        "Comuni": ([c.value for c in saved["Comuni"][1]], config.WORKBOOK_COMUNI_COLUMNS),
        "Cammini": ([c.value for c in saved["Cammini"][1]], config.WORKBOOK_CAMMINI_COLUMNS),
        "Coppie": ([c.value for c in saved["Coppie"][1]], config.WORKBOOK_COPPIE_COLUMNS),
        "Territori": ([c.value for c in saved["Territori"][header_row_of(saved["Territori"])]], config.WORKBOOK_TERRITORI_COLUMNS),
        config.WORKBOOK_PIVOT_LEGEND_NAME: (
            [c.value for c in saved[PIVOTS[0]][header_row_of(saved[PIVOTS[0]])]], config.WORKBOOK_PIVOT_COLUMNS,
        ),
        "Cammini per attributo": ([header for block in blocks.values() for header in block["headers"]], registry_summary),
    }
    for name, (headers, registry) in written.items():
        described = stems(entry[0] for entry in legend[name])
        in_registry = stems(column.header for column in registry if not (name == "Cammini per attributo" and column.header in unused))
        assert stems(headers) == described == in_registry, name


def test_every_pivot_sheet_has_the_same_headers(saved: Workbook) -> None:
    expected = [c.value for c in saved[PIVOTS[0]][header_row_of(saved[PIVOTS[0]])]][:3]
    for name in PIVOTS:
        sheet = saved[name]
        headers = [c.value for c in sheet[header_row_of(sheet)]]
        assert headers[:3] == expected == ["Livello", "Codice", "Territorio"]
        assert headers[-2:] == ["Cammini", "Comuni attraversati"] and set(headers[3:-2]) == {"N. cammini", "Incidenza"}


def test_headers_of_the_data_sheets_follow_the_agreed_columns(saved: Workbook) -> None:
    assert [c.value for c in saved["Cammini"][1]] == [
        "Codice cammino", "Cammino", "Altri nomi", "Catalogo di provenienza", "Scheda Cammini d'Italia", "Scheda MiTur",
        "Mezzo", "Religioso", "Perché religioso", "Forma", "Portata", "Tema", "Stagione", "Credenziale del pellegrino",
        "Segnaletica", "Data della scheda", "Regioni", "Lunghezza dichiarata (km)", "Lunghezza del tracciato (km)",
        "Copertura del tracciato", "Fonte del tracciato", "N. comuni", "di cui dentro", f"di cui solo entro {D} m", "Nota",
        "Comune 1", "Comune 2", "Comune 3",
    ]
    assert [c.value for c in saved["Coppie"][1]] == [
        "Codice cammino", "Cammino", "Codice comune", "Comune", "Sigla", "Codice provincia", "Provincia", "Codice regione",
        "Regione", "Ordine lungo il cammino", "Passaggio", "Distanza dal confine (m)", "Mezzo", "Fonte del tracciato",
        "Religioso", "Forma", "Portata", "Tema", "Stagione", "Credenziale del pellegrino", "Segnaletica",
    ]
    assert [c.value for c in saved["Comuni"][1]] == [
        "Codice regione", "Regione", "Codice provincia", "Provincia", "Sigla", "Codice comune", "Comune", "N. cammini",
        "Cammino 1", "Mezzo 1", "Religioso 1", "Passaggio 1", "Cammino 2", "Mezzo 2", "Religioso 2", "Passaggio 2",
    ]


def planted_table(extra: dict | None = None, drop: str | None = None) -> workbook.Table:
    rows = [{column.key: 1 for column in config.WORKBOOK_TERRITORI_COLUMNS}]
    if extra:
        rows[0].update(extra)
    if drop:
        del rows[0][drop]
    return workbook.Table(config.WORKBOOK_TERRITORI_COLUMNS, rows)


def test_registry_check_passes_on_a_complete_table_and_fails_on_planted_errors() -> None:
    table = planted_table()
    workbook.check_registry("Prova", table, workbook.expand(table.columns, table.rows))
    for broken, pattern in ((planted_table(extra={"colonna_non_registrata": 1}), "fuori registro"), (planted_table(drop="cammini"), "non scritte")):
        with pytest.raises(workbook.WorkbookError, match=pattern):
            workbook.check_registry("Prova", broken, workbook.expand(broken.columns, broken.rows))


def test_build_stops_when_the_registry_lists_a_column_nobody_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    ghost = config.WorkbookColumn("fantasma", "Fantasma", 10, "x", "y", "testo")
    monkeypatch.setattr(config, "WORKBOOK_COMUNI_COLUMNS", (*config.WORKBOOK_COMUNI_COLUMNS, ghost))
    with pytest.raises(workbook.WorkbookError, match="fantasma"):
        build()


def test_legenda_must_cover_every_sheet_that_was_written() -> None:
    written = workbook.Written()
    sheet = Workbook().active
    with pytest.raises(workbook.WorkbookError, match="La Legenda descrive"):
        workbook.write_legenda(sheet, RUN_DATE, {}, written)


# --- Legenda: testi e valori calcolati dai dati ---

def legend_text(saved: Workbook) -> str:
    return "\n".join(str(cell.value) for row in saved["Legenda"].iter_rows() for cell in row if cell.value is not None)


def test_legenda_texts_point_to_sheets_that_exist(saved: Workbook) -> None:
    text = legend_text(saved)
    assert "Riepilogo" not in text and "{" not in text
    legend = legend_sections(saved["Legenda"])["Cammini"]
    by_name = {entry[0]: entry for entry in legend}
    assert "Territori x Tema" in by_name["Tema"][3] and "Cammini per attributo" in by_name["Tema"][3]
    assert "Territori x Stagione" in by_name["Stagione"][3] and "Cammini per attributo" in by_name["Stagione"][3]


def test_legenda_explains_cards_with_two_values_and_cards_without_a_date(saved: Workbook) -> None:
    by_name = {entry[0]: entry for entry in legend_sections(saved["Legenda"])["Cammini"]}
    assert "ad anello; lineare" in by_name["Forma"][3] and "conta come quella combinazione" in by_name["Forma"][3]
    assert "nazionale; regionale" in by_name["Portata"][3] and "conta come quella combinazione" in by_name["Portata"][3]
    assert "non riportano la data" in by_name["Data della scheda"][3] and "senza scheda MiTur" in by_name["Data della scheda"][3]


def test_legenda_values_come_from_the_written_data(saved: Workbook) -> None:
    by_name = {entry[0]: entry for entry in legend_sections(saved["Legenda"])["Cammini"]}
    assert by_name["N. comuni"][2] == "da 0 a 3 comuni"
    assert by_name["Data della scheda"][2] == "da 16/06/2023 a 05/05/2025"
    assert set(by_name["Forma"][2].split(", ")) == {"ad anello", "ad anello; lineare", "non indicato"}
    assert by_name["Fonte del tracciato"][2] == "Cammini d'Italia e MiTur, OpenStreetMap, nessun tracciato"


def test_a_value_column_that_is_zero_everywhere_is_not_described(saved: Workbook) -> None:
    assert "da 0 a 0" not in legend_text(saved)
    names = [entry[0] for entry in legend_sections(saved["Legenda"])["Cammini per attributo"]]
    assert config.WORKBOOK_MEZZO_LABELS["bici"] not in names


def test_legenda_head_sources_and_dates(saved: Workbook) -> None:
    sheet = saved["Legenda"]
    assert sheet["B1"].value == "Cammini Comuni" and sheet["B2"].value == "Aggiornato: 04/11/2026"
    assert sheet.sheet_view.showGridLines is False
    sources = {row[1].value: [cell.value for cell in row] for row in sheet.iter_rows(min_row=9, max_row=12)}
    assert list(sources) == ["Cammini d'Italia", "MiTur (Catalogo dei Cammini religiosi)", "OpenStreetMap", "ISTAT confini comunali"]
    assert "Commercial Data License Agreement and API access term" in sources["Cammini d'Italia"][4]
    assert sources["OpenStreetMap"][3] == config.WORKBOOK_DOWNLOAD_MISSING  # senza registro delle richieste


def write_log(path: Path, rows: list[tuple[str, str, str]]) -> Path:
    lines = ["utc_iso,host_key,url,status,bytes,seconds", *(f"{when},{host},https://x/{host},{status},1,0.1" for when, host, status in rows)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_download_dates_come_from_the_successful_requests(tmp_path: Path) -> None:
    log = write_log(tmp_path / "log.csv", [
        ("2026-11-03T10:00:00+00:00", config.HOST_CDI, "200"), ("2026-11-05T10:00:00+00:00", config.HOST_S3, "200"),
        ("2026-11-04T10:00:00+00:00", config.HOST_MITUR, "403"), ("2026-11-04T09:00:00+00:00", config.HOST_ISTAT, "200"),
    ])
    periods = workbook.download_periods(log)
    assert periods["Cammini d'Italia"] == "dal 03/11/2026 al 05/11/2026" and periods["ISTAT confini comunali"] == "04/11/2026"
    assert periods["MiTur (Catalogo dei Cammini religiosi)"] == periods["OpenStreetMap"] == config.WORKBOOK_DOWNLOAD_MISSING
    assert set(workbook.download_periods(tmp_path / "manca.csv").values()) == {config.WORKBOOK_DOWNLOAD_MISSING}
    shown = build(downloads=periods)["Legenda"]
    assert shown["D9"].value == "dal 03/11/2026 al 05/11/2026"


# --- titoli ---

def test_sheet_titles_are_plain_sentences_not_the_sheet_name(saved: Workbook) -> None:
    assert saved["Territori"]["A1"].value == "Comuni attraversati e cammini per territorio"
    assert saved["Territori x Religioso"]["A1"].value == "Cammini per territorio e carattere religioso"
    assert saved["Territori x Fonte"]["A1"].value == "Cammini per territorio e fonte del tracciato"
    assert saved["Cammini per attributo"]["B1"].value == config.WORKBOOK_ATTRIBUTI_TITLE
    for name in ["Territori", *PIVOTS]:
        title = saved[name]["A1"].value
        assert name not in title and ": " not in title and name in saved.sheetnames
    assert saved["Territori x Mezzo"]["A2"].value == "Aggiornato: 04/11/2026"
    assert saved["Territori x Mezzo"]["A1"].font.bold and saved["Territori x Mezzo"]["A1"].font.size == config.WORKBOOK_TITLE_SIZE


# --- Comuni: gruppi Cammino k ---

def test_comuni_rows_and_ragged_groups(saved: Workbook) -> None:
    rows = sheet_rows(saved["Comuni"])
    assert [(r["Comune"], r["Sigla"], r["N. cammini"]) for r in rows] == [
        ("Agliè", "TO", 1), ("Torino", "TO", 2), ("Castro", "BG", 1), ("Castro", "LE", 1),
    ]
    agglie, torino, bergamo, lecce = rows
    assert (agglie["Codice regione"], agglie["Codice provincia"], agglie["Codice comune"]) == ("01", "201", AGLIE)
    assert (agglie["Cammino 1"], agglie["Mezzo 1"], agglie["Religioso 1"], agglie["Passaggio 1"]) == (
        "Cammino Alfa", "a piedi", "sì", "dentro il comune",
    )
    assert agglie["Cammino 2"] is None and agglie["Mezzo 2"] is None and agglie["Passaggio 2"] is None
    assert (torino["Cammino 1"], torino["Cammino 2"]) == ("Cammino Alfa", "Cammino Beta")  # in ordine alfabetico
    assert (torino["Mezzo 2"], torino["Religioso 2"]) == ("in bici", "no")
    assert bergamo["Mezzo 1"] == "a piedi e in bici" and lecce["Passaggio 1"] == f"entro {D} m dal confine"
    assert sum(r["N. cammini"] for r in rows) == len(coppie())  # una coppia per cammino e comune
    for row in rows:  # i gruppi pieni sono N. cammini
        assert sum(1 for k in (1, 2) if row[f"Cammino {k}"]) == row["N. cammini"]


def test_comuni_group_width_follows_the_busiest_comune() -> None:
    more = coppie() + [pair("u003", "Cammino Gamma", TORINO, 1, "piedi", "no", "cdi")]
    rows = [cammino("u003", "Cammino Gamma", 1, mezzo="piedi"), cammino("u002", "Cammino Beta", 2), cammino("u001", "Cammino Alfa", 3)]
    headers = [c.value for c in build(coppie_rows=more, cammini_rows=rows)["Comuni"][1]]
    assert headers[-4:] == ["Cammino 3", "Mezzo 3", "Religioso 3", "Passaggio 3"]


# --- Cammini e Coppie ---

def test_cammini_rows_sorted_with_ragged_comuni_and_display_values(saved: Workbook) -> None:
    rows = {r["Cammino"]: r for r in sheet_rows(saved["Cammini"])}
    assert list(rows) == ["Cammino Alfa", "Cammino Beta", "Cammino Gamma"]
    alfa, beta, gamma = rows.values()
    assert [alfa[f"Comune {k}"] for k in (1, 2, 3)] == ["Agliè (TO)", "Torino (TO)", "Castro (BG)"]
    assert [beta[f"Comune {k}"] for k in (1, 2, 3)] == ["Torino (TO)", f"Castro (LE, entro {D} m)", None]
    assert gamma["Comune 1"] is None and gamma["N. comuni"] == 0
    assert (alfa["N. comuni"], alfa["di cui dentro"], alfa[f"di cui solo entro {D} m"]) == (3, 3, 0)
    assert (beta["di cui dentro"], beta[f"di cui solo entro {D} m"]) == (1, 1)
    assert alfa["Altri nomi"] == "Alias del cammino; Altro nome"
    assert alfa["Catalogo di provenienza"] == "Cammini d'Italia e MiTur" and beta["Catalogo di provenienza"] == "OpenStreetMap"
    assert (alfa["Mezzo"], alfa["Religioso"], alfa["Forma"], alfa["Portata"]) == ("a piedi e in bici", "sì", "ad anello; lineare", "nazionale; regionale")
    assert (alfa["Tema"], alfa["Stagione"], gamma["Tema"], gamma["Portata"]) == ("spirituale", "primavera; autunno", "non indicato", "non indicato")
    assert beta["Tema"] == "arte e cultura; coast to coast"
    assert (alfa["Credenziale del pellegrino"], alfa["Segnaletica"], gamma["Segnaletica"]) == ("sì", "no", "non indicato")
    assert alfa["Perché religioso"] == "Catalogo; OpenStreetMap: pellegrinaggio"
    assert (alfa["Fonte del tracciato"], beta["Fonte del tracciato"], gamma["Fonte del tracciato"]) == (
        "Cammini d'Italia e MiTur", "OpenStreetMap", "nessun tracciato",
    )
    assert alfa["Copertura del tracciato"] == 0.99 and gamma["Lunghezza del tracciato (km)"] is None


def test_card_links_one_url_per_line_with_the_hyperlink_on_the_first(saved: Workbook) -> None:
    sheet = saved["Cammini"]
    headers = [c.value for c in sheet[1]]
    cdi, mitur = headers.index("Scheda Cammini d'Italia") + 1, headers.index("Scheda MiTur") + 1
    assert [sheet.cell(number, 2).value for number in (2, 3, 4)] == ["Cammino Alfa", "Cammino Beta", "Cammino Gamma"]
    cell = sheet.cell(2, cdi)
    assert cell.value == "https://cdi.example/uno\nhttps://cdi.example/due" and cell.hyperlink.target == "https://cdi.example/uno"
    assert cell.font.underline == "single" and cell.font.color.rgb.endswith(config.WORKBOOK_LINK_COLOR)
    assert sheet.cell(2, mitur).hyperlink.target == "https://mitur.example/page/2/"
    assert sheet.cell(3, cdi).value is None and sheet.cell(3, mitur).value is None  # Beta e' solo in OSM
    assert sheet.cell(4, mitur).value == "https://mitur.example/"  # Gamma: pagina 1 dell'elenco


def test_mitur_card_is_the_listing_page_that_contains_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert workbook.mitur_page_url("1") == config.MITUR_LISTING_URL
    assert workbook.mitur_page_url("3") == config.MITUR_LISTING_URL + "page/3/"
    cdi_csv, mitur_csv = tmp_path / "cdi.csv", tmp_path / "mitur.csv"
    cdi_csv.write_text("cammino_id,link\ncdi:7,https://cdi.example/sette\n", encoding="utf-8")
    mitur_csv.write_text("slug,pagina\nalta-via,3\n", encoding="utf-8")
    monkeypatch.setattr(config, "CAMMINI_CSV_TEMPLATE", str(tmp_path / "{fonte}.csv"))
    monkeypatch.setattr(config, "MITUR_CAMMINI_CSV", mitur_csv)
    (tmp_path / f"{config.CDI_FONTE}.csv").write_text(cdi_csv.read_text(encoding="utf-8"), encoding="utf-8")
    assert workbook.card_links() == {"cdi:7": "https://cdi.example/sette", "mitur:alta-via": config.MITUR_LISTING_URL + "page/3/"}
    mitur_csv.unlink()
    with pytest.raises(workbook.WorkbookError, match="Manca il file"):
        workbook.card_links()


def test_card_without_an_address_stops_the_build() -> None:
    with pytest.raises(workbook.WorkbookError, match="Nessun indirizzo della scheda per mitur:tre"):
        workbook.prepare(coppie(), cammini(), ISTAT, {key: value for key, value in LINKS.items() if key != "mitur:tre"})


def test_coppie_rows_per_pair_source_and_tags(saved: Workbook) -> None:
    rows = sheet_rows(saved["Coppie"])
    assert [(r["Cammino"], r["Ordine lungo il cammino"]) for r in rows] == [
        ("Cammino Alfa", 1), ("Cammino Alfa", 2), ("Cammino Alfa", 3), ("Cammino Beta", 1), ("Cammino Beta", 2),
    ]
    # la fonte del tracciato e' della coppia: lo stesso cammino mostra fonti diverse nei suoi comuni
    assert [r["Fonte del tracciato"] for r in rows] == [
        "Cammini d'Italia e MiTur", "Cammini d'Italia", "MiTur", "OpenStreetMap", "OpenStreetMap",
    ]
    assert rows[0]["Passaggio"] == "dentro il comune" and rows[4]["Passaggio"] == f"entro {D} m dal confine"
    assert rows[3]["Mezzo"] == "in bici" and rows[3]["Religioso"] == "no" and rows[0]["Forma"] == "ad anello; lineare"
    assert rows[0]["Distanza dal confine (m)"] == 12.5 and isinstance(rows[0]["Ordine lungo il cammino"], int)
    assert (rows[0]["Codice regione"], rows[0]["Codice provincia"], rows[0]["Codice comune"]) == ("01", "201", AGLIE)


def test_data_sheets_have_filter_frozen_header_and_plain_data_rows(saved: Workbook) -> None:
    for name, frozen in (("Comuni", "H2"), ("Cammini", "C2"), ("Coppie", "E2")):
        sheet = saved[name]
        assert sheet.freeze_panes == frozen and sheet.auto_filter.ref == sheet.dimensions
        assert all(cell.font.bold for cell in sheet[1])
        assert not any(cell.font.bold for row in sheet.iter_rows(min_row=2) for cell in row)
        assert all(sheet.column_dimensions[letter].width > 0 for letter in "ABCDEFG")


def test_number_formats_codes_as_text_and_dates_as_dates(saved: Workbook) -> None:
    comuni = saved["Comuni"]
    for row in comuni.iter_rows(min_row=2):
        for index in (0, 2, 5):
            assert row[index].data_type == "s" and row[index].number_format == "@"
        assert row[7].number_format == config.WORKBOOK_FMT_COUNT and isinstance(row[7].value, int)
    sheet = saved["Cammini"]
    headers = [c.value for c in sheet[1]]
    first = {header: sheet.cell(2, number) for number, header in enumerate(headers, start=1)}
    assert first["Data della scheda"].value == datetime(2025, 5, 5) and first["Data della scheda"].number_format == "DD/MM/YYYY"
    assert first["Lunghezza dichiarata (km)"].number_format == "0.0" and first["Copertura del tracciato"].number_format == "0.0%"
    second = sheet.cell(3, headers.index("Data della scheda") + 1)
    assert second.value == "16/06/2023; 01/02/2024"  # piu' date: testo GG/MM/AAAA
    assert sheet.cell(4, headers.index("Data della scheda") + 1).value is None


def test_no_dashes_no_pipes_no_placeholders_in_any_cell(saved: Workbook) -> None:
    for sheet in saved.worksheets:
        for row in sheet.iter_rows(values_only=True):
            for value in row:
                if isinstance(value, str):
                    assert not any(dash in value for dash in ("—", "–")) and config.LIST_SEPARATOR not in value, sheet.title
                    assert "{" not in value, (sheet.title, value)


# --- Territori: gerarchia, numeri, schema a righe ---

def test_territori_hierarchy_row_counts_and_numbers(saved: Workbook) -> None:
    sheet = saved["Territori"]
    rows = sheet_rows(sheet, header_row_of(sheet))
    levels = [r["Livello"] for r in rows]
    assert Counter(levels) == {"Italia": 1, "Regione": 4, "Provincia": 5, "Comune": 4} and len(rows) == 14
    assert [r["Territorio"] for r in rows] == [
        "Italia", "Piemonte", "Novara", "Torino", "Agliè", "Torino", "Lombardia", "Bergamo", "Castro", "Lazio", "Roma",
        "Puglia", "Lecce", "Castro",
    ]
    assert [r["Codice"] for r in rows][:5] == [None, "01", "003", "201", AGLIE]
    got = {(r["Livello"], r["Codice"]): (r["Comuni del territorio"], r["Comuni attraversati"], r["% comuni attraversati"], r["Cammini"], r["Media cammini per comune attraversato"]) for r in rows}
    assert got[("Italia", None)] == (6, 4, pytest.approx(4 / 6), 2, 1.25)
    assert got[("Regione", "01")] == (3, 2, pytest.approx(2 / 3), 2, 1.5)
    assert got[("Provincia", "003")] == (1, 0, 0.0, 0, None) and got[("Regione", "12")] == (1, 0, 0.0, 0, None)  # niente cammini: zero, non vuoto
    assert got[("Comune", TORINO)] == (1, 1, 1.0, 2, 2.0) and got[("Comune", AGLIE)] == (1, 1, 1.0, 1, 1.0)


def test_territori_comune_rows_are_the_comuni_sheet(saved: Workbook) -> None:
    territori = sheet_rows(saved["Territori"], header_row_of(saved["Territori"]))
    comuni = {r["Codice comune"]: r["N. cammini"] for r in sheet_rows(saved["Comuni"])}
    assert {r["Codice"]: r["Cammini"] for r in territori if r["Livello"] == "Comune"} == comuni


@pytest.mark.parametrize("name", ["Territori", *PIVOTS])
def test_outline_levels_summary_above_and_collapsed_to_regione(saved: Workbook, name: str) -> None:
    sheet = saved[name]
    header = header_row_of(sheet)
    levels = [LEVEL[sheet.cell(number, 1).value] for number in range(header + 1, sheet.max_row + 1)]
    assert sheet.sheet_properties.outlinePr.summaryBelow is False and sheet.sheet_format.outlineLevelRow == 2
    for index, level in enumerate(levels):
        dimension = sheet.row_dimensions[header + 1 + index]
        assert dimension.outline_level == max(level - 1, 0) and dimension.hidden == (level >= 2)
        has_details = index + 1 < len(levels) and levels[index + 1] > level
        assert bool(dimension.collapsed) == (has_details and level >= 1)  # Italia resta aperta


@pytest.mark.parametrize("name", ["Territori", *PIVOTS])
def test_territorial_sheets_style_freeze_and_filter(saved: Workbook, name: str) -> None:
    sheet = saved[name]
    header = header_row_of(sheet)
    assert sheet.sheet_view.showGridLines is False
    assert sheet.freeze_panes == f"D{header + 1}"
    assert sheet.auto_filter.ref == f"A{header}:{sheet.cell(header, sheet.max_column).column_letter}{sheet.max_row}"
    assert all(cell.font.bold and cell.alignment.wrap_text for cell in sheet[header])
    for number in range(header + 1, sheet.max_row + 1):
        level = LEVEL[sheet.cell(number, 1).value]
        assert sheet.cell(number, 3).alignment.indent == level
        assert bool(sheet.cell(number, 3).font.bold) == (level == 0)  # solo Italia in grassetto
        fill = sheet.cell(number, 1).fill
        assert (fill.fill_type == "solid" and fill.fgColor.rgb.endswith(config.WORKBOOK_REGION_FILL)) == (level == 1)


def test_pivot_value_labels_are_merged_over_their_pair_of_columns(saved: Workbook) -> None:
    sheet = saved["Territori x Religioso"]
    band = header_row_of(sheet) - 1
    merged = {(r.min_row, r.min_col, r.max_col): sheet.cell(r.min_row, r.min_col).value for r in sheet.merged_cells.ranges}
    spans = {value: (col0, col1) for (row, col0, col1), value in merged.items() if row == band}
    assert spans == {"sì": (4, 5), "no": (6, 7), "Totale": (8, 9)}


# --- Territori x D ---

def test_single_valued_sheets_sum_to_the_row_cammini_and_comune_rows_are_their_pairs(saved: Workbook) -> None:
    for name in ("Religioso", "Mezzo", "Forma", "Portata", "Segnaletica", "Credenziale", "Catalogo", "Fonte"):
        rows = pivot(saved[f"Territori x {name}"])
        assert len(rows) == 14, name
        for key, row in rows.items():
            assert sum(n for n, _ in row["values"].values()) == row["cammini"], (name, key)
        assert rows[("Italia", None)]["cammini"] == 2  # i cammini con almeno una coppia: Gamma non ha comuni
        assert rows[("Comune", TORINO)]["cammini"] == 2 and rows[("Comune", AGLIE)]["cammini"] == 1


def test_religioso_counts_and_incidence(saved: Workbook) -> None:
    rows = pivot(saved["Territori x Religioso"])
    assert rows[("Italia", None)]["values"] == {"sì": (1, 0.5), "no": (1, 0.5)}
    assert rows[("Regione", "03")]["values"] == {"sì": (1, 1.0), "no": (0, 0.0)}
    assert rows[("Regione", "12")]["values"] == {"sì": (0, None), "no": (0, None)}  # zero cammini: nessuna incidenza
    assert rows[("Italia", None)]["crossed"] == 4 and rows[("Regione", "01")]["crossed"] == 2


def test_mezzo_is_territorial_and_keeps_a_column_only_a_comune_row_uses(saved: Workbook) -> None:
    rows = pivot(saved["Territori x Mezzo"])
    # in Italia ogni cammino ha anche un tracciato per entrambi: nessuno vale "in bici", ma 24 comuni (qui Torino) si
    assert rows[("Italia", None)]["values"] == {"a piedi": (0, 0.0), "in bici": (0, 0.0), "a piedi e in bici": (2, 1.0)}
    assert rows[("Comune", TORINO)]["values"] == {"a piedi": (1, 0.5), "in bici": (1, 0.5), "a piedi e in bici": (0, 0.0)}
    # Piemonte: Alfa solo a piedi nei suoi due comuni, Beta solo in bici: la regola territoriale non dice "entrambi"
    assert rows[("Regione", "01")]["values"] == {"a piedi": (1, 0.5), "in bici": (1, 0.5), "a piedi e in bici": (0, 0.0)}
    assert rows[("Comune", CASTRO_BG)]["values"]["a piedi e in bici"] == (1, 1.0)
    assert list(rows[("Italia", None)]["values"]) == ["a piedi", "in bici", "a piedi e in bici"]  # ordine fisso


def test_fonte_is_territorial_the_union_of_the_pair_sources_in_the_territory(saved: Workbook) -> None:
    rows = pivot(saved["Territori x Fonte"])
    names = ("Cammini d'Italia e MiTur", "Cammini d'Italia", "MiTur", "OpenStreetMap")
    assert {label for label in rows[("Italia", None)]["values"]} == set(names)

    def present(key: tuple[str, str | None]) -> set[str]:
        return {label for label, (n, _) in rows[key]["values"].items() if n}

    assert present(("Italia", None)) == {"Cammini d'Italia e MiTur", "OpenStreetMap"}  # Alfa: unione dei suoi comuni
    assert present(("Comune", TORINO)) == {"Cammini d'Italia", "OpenStreetMap"}  # nel solo Torino Alfa e' cdi
    assert present(("Comune", CASTRO_BG)) == {"MiTur"} and present(("Comune", AGLIE)) == {"Cammini d'Italia e MiTur"}
    assert present(("Regione", "01")) == {"Cammini d'Italia e MiTur", "OpenStreetMap"}


def test_forma_with_two_published_values_counts_as_that_combination(saved: Workbook) -> None:
    values = pivot(saved["Territori x Forma"])[("Italia", None)]["values"]
    assert values["ad anello; lineare"] == (1, 0.5) and values["ad anello"] == (1, 0.5)
    assert "lineare" not in values and "non indicato" not in values  # Gamma non ha comuni: la sua colonna sarebbe vuota
    portata = pivot(saved["Territori x Portata"])[("Italia", None)]["values"]
    assert portata == {"nazionale; regionale": (1, 0.5), "non indicato": (1, 0.5)}


def test_multi_valued_sheets_count_a_cammino_under_each_value(saved: Workbook) -> None:
    tema = pivot(saved["Territori x Tema"])[("Italia", None)]
    assert tema["cammini"] == 2
    assert tema["values"] == {"spirituale": (1, 0.5), "arte e cultura": (1, 0.5), "coast to coast": (1, 0.5)}
    assert sum(n for n, _ in tema["values"].values()) == 3 > tema["cammini"]  # Beta conta due volte
    assert sum(share for _, share in tema["values"].values()) == pytest.approx(1.5)  # le incidenze superano il 100%
    stagione = pivot(saved["Territori x Stagione"])[("Italia", None)]["values"]
    assert stagione == {"primavera": (1, 0.5), "autunno": (1, 0.5), "non indicato": (1, 0.5)}
    notes = [saved["Territori x Tema"].cell(number, 1).value for number in range(3, header_row_of(saved["Territori x Tema"]))]
    assert config.WORKBOOK_PIVOT_NOTE_MULTI in notes and config.WORKBOOK_PIVOT_NOTE_TERRITORIAL not in notes
    mezzo_notes = [saved["Territori x Mezzo"].cell(number, 1).value for number in range(3, header_row_of(saved["Territori x Mezzo"]))]
    assert config.WORKBOOK_PIVOT_NOTE_TERRITORIAL in mezzo_notes and config.WORKBOOK_PIVOT_NOTE_MULTI not in mezzo_notes


def test_credenziale_segnaletica_e_catalogo(saved: Workbook) -> None:
    assert pivot(saved["Territori x Credenziale"])[("Italia", None)]["values"] == {"sì": (1, 0.5), "no": (1, 0.5)}
    assert pivot(saved["Territori x Segnaletica"])[("Italia", None)]["values"] == {"no": (1, 0.5), "sì": (1, 0.5)}
    assert pivot(saved["Territori x Catalogo"])[("Italia", None)]["values"] == {
        "Cammini d'Italia e MiTur": (1, 0.5), "OpenStreetMap": (1, 0.5),
    }


def test_value_labels_fixed_order_by_frequency_empty_last_and_zero_dropped() -> None:
    assert workbook.value_labels("mezzo", Counter({"in bici": 2})) == ["in bici"]
    assert workbook.value_labels("mezzo", Counter({"a piedi e in bici": 1, "a piedi": 3, "in bici": 0})) == ["a piedi", "a piedi e in bici"]
    assert workbook.value_labels("tema", Counter({"non indicato": 5, "x": 1, "y": 3})) == ["y", "x", "non indicato"]


def test_a_territorial_characteristic_needs_a_combination_rule() -> None:
    data = prepare()
    shown = {row["cammino_unico_id"]: row for row in data.cammini}
    with pytest.raises(workbook.WorkbookError, match="senza regola di combinazione"):
        workbook.pivot_table(("Tema", "tema", True, True), data.tree, shown)


# --- Cammini per attributo ---

def test_cammini_per_attributo_blocks_counts_shares_and_averages(saved: Workbook) -> None:
    blocks = summary_blocks(saved["Cammini per attributo"])
    assert list(blocks) == [
        "Religioso", "Mezzo", "Forma", "Portata", "Tema", "Stagione", "Segnaletica", "Credenziale del pellegrino",
        "Catalogo di provenienza", "Fonte del tracciato", config.WORKBOOK_SUMMARY_CROSS_TITLE,
    ]
    religioso = blocks["Religioso"]
    assert religioso["headers"][:3] == ["Valore", "N. cammini", "Incidenza sul totale"]
    rows = {r[0]: r for r in religioso["rows"]}
    # sì: Alfa; no: Beta e Gamma (Gamma senza lunghezza del tracciato e senza comuni)
    assert rows["sì"][1:] == (1, pytest.approx(1 / 3), 120, 118.4, 3, 3)
    assert rows["no"][1:] == (2, pytest.approx(2 / 3), 120, 118.4, 1.0, 2)
    assert rows["Totale"][1:3] == (3, 1.0)
    for title in ("Religioso", "Mezzo", "Forma", "Portata", "Segnaletica", "Credenziale del pellegrino", "Catalogo di provenienza", "Fonte del tracciato"):
        block = blocks[title]
        assert sum(r[1] for r in block["rows"] if r[0] != "Totale") == 3 and block["rows"][-1][0] == "Totale", title
    assert [r[0] for r in blocks["Forma"]["rows"]] == ["ad anello", "ad anello; lineare", "non indicato", "Totale"]
    assert [r[0] for r in blocks["Fonte del tracciato"]["rows"][:-1]][-1] == "nessun tracciato"  # in fondo


def test_cammini_per_attributo_multi_valued_blocks_have_no_total_and_say_so(saved: Workbook) -> None:
    blocks = summary_blocks(saved["Cammini per attributo"])
    tema = {r[0]: r[1:3] for r in blocks["Tema"]["rows"]}
    assert tema == {
        "spirituale": (1, pytest.approx(1 / 3)), "arte e cultura": (1, pytest.approx(1 / 3)),
        "coast to coast": (1, pytest.approx(1 / 3)), "non indicato": (1, pytest.approx(1 / 3)),
    }
    assert "Totale" not in tema and blocks["Tema"]["notes"] == [config.WORKBOOK_SUMMARY_MULTI_NOTE]
    assert blocks["Stagione"]["notes"] == [config.WORKBOOK_SUMMARY_MULTI_NOTE] and blocks["Religioso"]["notes"] == []


def test_religioso_per_mezzo_drops_the_column_that_is_zero_in_every_row(saved: Workbook) -> None:
    block = summary_blocks(saved["Cammini per attributo"])[config.WORKBOOK_SUMMARY_CROSS_TITLE]
    assert block["headers"] == ["Religioso", "a piedi e in bici", "non indicato", "Totale"]  # "a piedi" e "in bici" non ci sono
    assert block["rows"] == [("sì", 1, 0, 1), ("no", 1, 1, 2), ("Totale", 2, 1, 3)]


def test_religioso_per_mezzo_keeps_a_mezzo_column_when_a_cammino_has_it() -> None:
    shown = [{**row, "mezzo": config.WORKBOOK_MEZZO_LABELS["bici"]} if row["cammino_unico_id"] == "u003" else row for row in prepare().cammini]
    table = workbook.cross_table(shown)
    assert [column.header for column in table.columns] == ["Religioso", "in bici", "a piedi e in bici", "Totale"]
    assert table.totals == [{"x_religioso": "Totale", "x_bici": 1, "x_entrambi": 2, "x_totale": 3}]


def test_cammino_senza_mezzo_si_mostra_non_indicato_e_ha_la_sua_colonna() -> None:
    assert workbook.display("mezzo", "") == config.WORKBOOK_EMPTY_LABEL
    rows = [cammino("u003", "Cammino Gamma", 0, mezzo=""), cammino("u002", "Cammino Beta", 2), cammino("u001", "Cammino Alfa", 3)]
    shown = prepare(cammini_rows=rows).cammini
    assert next(row for row in shown if row["cammino_unico_id"] == "u003")["mezzo"] == config.WORKBOOK_EMPTY_LABEL
    table = workbook.cross_table(shown)
    assert [column.header for column in table.columns] == ["Religioso", "a piedi e in bici", "non indicato", "Totale"]
    assert table.totals == [{"x_religioso": "Totale", "x_entrambi": 2, "x_nessuno": 1, "x_totale": 3}]


# --- invarianti: ognuna deve fermare la costruzione con un messaggio italiano (prove con un errore piantato) ---

def with_pair(rows: list[dict[str, str]], cid: str, code: str, **changes: str) -> list[dict[str, str]]:
    return [{**row, **changes} if (row["cammino_unico_id"], row["PRO_COM_T"]) == (cid, code) else row for row in rows]


def test_the_intact_fixture_builds() -> None:
    assert prepare().tree and build().sheetnames == SHEETS


def test_code_not_six_digits_raises() -> None:
    with pytest.raises(workbook.WorkbookError, match="PRO_COM_T"):
        build(coppie_rows=with_pair(coppie(), "u001", TORINO, PRO_COM_T="1272"))


def test_code_not_in_istat_raises() -> None:
    with pytest.raises(workbook.WorkbookError, match=TORINO):
        build(istat=[row for row in ISTAT if row["PRO_COM_T"] != TORINO])


def test_duplicate_pair_raises() -> None:
    with pytest.raises(workbook.WorkbookError, match="più di una volta"):
        build(coppie_rows=coppie() + [pair("u001", "Cammino Alfa", AGLIE, 4, "piedi", "si", "cdi")])


def test_duplicate_cammino_raises() -> None:
    with pytest.raises(workbook.WorkbookError, match="u001"):
        build(cammini_rows=cammini() + [cammino("u001", "Cammino Alfa", 3)])


def test_pair_of_a_cammino_missing_from_the_list_raises() -> None:
    with pytest.raises(workbook.WorkbookError, match="u002"):
        build(cammini_rows=[row for row in cammini() if row["cammino_unico_id"] != "u002"])


def test_n_comuni_mismatch_raises() -> None:
    rows = [cammino("u001", "Cammino Alfa", 4) if r["cammino_unico_id"] == "u001" else r for r in cammini()]
    with pytest.raises(workbook.WorkbookError, match="n_comuni"):
        build(cammini_rows=rows)


def test_cammino_with_comuni_declared_but_none_in_the_pairs_raises() -> None:
    rows = [cammino("u003", "Cammino Gamma", 1) if r["cammino_unico_id"] == "u003" else r for r in cammini()]
    with pytest.raises(workbook.WorkbookError, match="u003"):
        build(cammini_rows=rows)


@pytest.mark.parametrize("ordine", ["3", "0", "5"])
def test_non_contiguous_order_raises(ordine: str) -> None:
    with pytest.raises(workbook.WorkbookError, match="ordine"):
        build(coppie_rows=with_pair(coppie(), "u001", TORINO, ordine=ordine))  # u001 ha ordini 1, <ordine>, 3


@pytest.mark.parametrize("change", [{"mezzo": "treno"}, {"mezzo": ""}, {"religioso": "forse"}, {"dentro": "boh"}, {"ordine": "x"}])
def test_invalid_value_raises(change: dict[str, str]) -> None:
    with pytest.raises(workbook.WorkbookError, match="non valido"):
        build(coppie_rows=with_pair(coppie(), "u001", TORINO, **change))


def test_cammino_without_pairs_must_have_blank_mezzo_and_the_reverse() -> None:
    with pytest.raises(workbook.WorkbookError, match="u003.*indicato ma non ha coppie"):
        build(cammini_rows=[cammino("u003", "Cammino Gamma", 0, mezzo="piedi") if r["cammino_unico_id"] == "u003" else r for r in cammini()])
    with pytest.raises(workbook.WorkbookError, match="u001.*vuoto ma ha coppie"):
        build(cammini_rows=[cammino("u001", "Cammino Alfa", 3, mezzo="") if r["cammino_unico_id"] == "u001" else r for r in cammini()])


def test_only_mezzo_may_be_blank_in_cammini() -> None:
    assert config.WORKBOOK_CAMMINI_BLANK_ENUM_KEYS == ("mezzo",)
    with pytest.raises(workbook.WorkbookError, match="valore non valido nella colonna 'religioso'"):
        build(cammini_rows=[cammino("u003", "Cammino Gamma", 0, religioso="") if r["cammino_unico_id"] == "u003" else r for r in cammini()])


def test_unknown_source_raises() -> None:
    with pytest.raises(workbook.WorkbookError, match="Fonte non riconosciuta: gpx"):
        build(coppie_rows=with_pair(coppie(), "u001", TORINO, fonte_geometria="gpx"))


def test_comuni_sheet_checks_sum_of_n_cammini_and_filled_groups() -> None:
    data = prepare()
    workbook.check_comuni_rows(data.comuni, len(coppie()))
    with pytest.raises(workbook.WorkbookError, match="N. cammini del foglio Comuni"):
        workbook.check_comuni_rows(data.comuni, len(coppie()) + 1)
    emptied = [{**row, "cammino_1": None} if row[config.COMUNE_ID_COL] == AGLIE else row for row in data.comuni]
    with pytest.raises(workbook.WorkbookError, match="i cammini elencati non sono N. cammini"):
        workbook.check_comuni_rows(emptied, len(coppie()))
    inflated = [{**row, "n_cammini": 2} if row[config.COMUNE_ID_COL] == AGLIE else row for row in data.comuni]
    with pytest.raises(workbook.WorkbookError, match="N. cammini"):
        workbook.check_comuni_rows(inflated, len(coppie()) + 1)


def test_tree_check_compares_codes_and_per_comune_cammini_to_the_comuni_sheet() -> None:
    data = prepare()
    workbook.check_tree(data.tree, data.comuni, 2)
    code = config.COMUNE_ID_COL
    wrong_code = [{**row, code: "999999"} if row[code] == AGLIE else row for row in data.comuni]  # stesso numero di righe
    wrong_count = [{**row, "n_cammini": row["n_cammini"] + 1} if row[code] == TORINO else row for row in data.comuni]
    with pytest.raises(workbook.WorkbookError, match="999999|001001"):
        workbook.check_tree(data.tree, wrong_code, 2)
    with pytest.raises(workbook.WorkbookError, match="Territori"):
        workbook.check_tree(data.tree, wrong_count, 2)
    with pytest.raises(workbook.WorkbookError, match="righe di comuni"):
        workbook.check_tree(data.tree, data.comuni[:-1], 2)
    with pytest.raises(workbook.WorkbookError, match="l'Italia ha 2 cammini"):
        workbook.check_tree(data.tree, data.comuni, 3)


def test_religioso_per_mezzo_must_sum_to_the_cammini() -> None:
    shown = prepare().cammini
    workbook.cross_table(shown)
    with pytest.raises(workbook.WorkbookError, match="non sommano"):
        workbook.cross_table([{**shown[0], "religioso": "forse"}, *shown[1:]])
    with pytest.raises(workbook.WorkbookError, match="non sommano"):
        workbook.cross_table([{**shown[0], "mezzo": "tram"}, *shown[1:]])


# --- visualizzazione: elenchi, parole, fonti, date ---

def test_display_values() -> None:
    assert workbook.display("fonti", "cdi|mitur|osm") == "Cammini d'Italia, MiTur e OpenStreetMap"
    assert workbook.display("fonti", "osm|cdi") == "Cammini d'Italia e OpenStreetMap"
    assert workbook.display("fonte_geometria", config.FONTE_GEOMETRIA_NESSUNA) == "nessun tracciato"
    assert workbook.display("tema", "arte-e-cultura|coast-to-coast") == "arte e cultura; coast to coast"
    assert workbook.display("tema", "") == "non indicato" and workbook.display("alias", "") is None
    assert workbook.display("data_catalogo", "2023-06-16") == date(2023, 6, 16)
    assert workbook.display("data_catalogo", "2024-02-01|2023-06-16") == "01/02/2024; 16/06/2023"
    assert workbook.join_names([]) == "" and workbook.join_names(["A"]) == "A" and workbook.join_names(["A", "B", "C"]) == "A, B e C"


def test_legend_values_are_ranges_for_numbers_and_present_values_for_categories() -> None:
    by_key = {column.key: column for column in config.WORKBOOK_CAMMINI_COLUMNS}
    assert workbook.legend_values(by_key["n_comuni"], [3, 0, 12]) == "da 0 a 12 comuni"
    assert workbook.legend_values(by_key["copertura"], [0.5, 1.0, None]) == "da 50,0% a 100,0%"
    assert workbook.legend_values(by_key["n_comuni"], [None]) == config.WORKBOOK_VALUES_NONE
    assert workbook.legend_values(by_key["religioso"], ["no", "sì", "sì"]) == "sì, no"


def test_legend_date_not_iso_names_the_column_and_the_value() -> None:
    column = {column.key: column for column in config.WORKBOOK_CAMMINI_COLUMNS}["data_catalogo"]
    assert workbook.legend_values(column, [date(2023, 6, 16), "01/02/2024; 16/06/2023"]) == "da 16/06/2023 a 01/02/2024"
    with pytest.raises(workbook.WorkbookError, match=f"{column.header}.*«ieri»"):
        workbook.legend_values(column, ["ieri"])


# --- file e riga di comando ---

def write_inputs(folder: Path, coppie_rows: list[dict[str, str]], cammini_rows: list[dict[str, str]]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, rows in ((config.PAIRS_FILES["coppie"], coppie_rows), (config.PAIRS_FILES["cammini"], cammini_rows)):
        with (folder / name).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)


@pytest.fixture
def fake_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    """ISTAT e indirizzi delle schede finti: nessun file di data/ viene letto."""
    monkeypatch.setattr(workbook, "load_istat_table", lambda: ISTAT)
    monkeypatch.setattr(workbook, "card_links", lambda: LINKS)


def test_run_writes_the_dated_file_and_reports_every_sheet(
    tmp_path: Path, fake_sources: None, capsys: pytest.CaptureFixture[str]
) -> None:
    write_inputs(tmp_path / "in", coppie(), cammini())
    target = workbook.run(tmp_path / "in", tmp_path / "out", RUN_DATE, tmp_path / "manca.csv")
    assert target == tmp_path / "out" / "Cammini Comuni 04.11.2026.xlsx"
    assert load_workbook(target).sheetnames == SHEETS
    out = capsys.readouterr().out
    assert "workbook: foglio Comuni: 5 righe" in out and "workbook: scritto" in out


def test_run_writes_nothing_on_breach(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_sources: None) -> None:
    write_inputs(tmp_path / "in", coppie(), cammini())
    monkeypatch.setattr(workbook, "load_istat_table", lambda: [row for row in ISTAT if row["PRO_COM_T"] != AGLIE])
    with pytest.raises(workbook.WorkbookError, match=AGLIE):
        workbook.run(tmp_path / "in", tmp_path / "out", RUN_DATE, tmp_path / "manca.csv")
    assert not (tmp_path / "out").exists()


def test_run_missing_input_and_missing_column_are_workbook_errors(tmp_path: Path, fake_sources: None) -> None:
    with pytest.raises(workbook.WorkbookError, match="pairs"):
        workbook.run(tmp_path / "in", tmp_path / "out", RUN_DATE, tmp_path / "manca.csv")
    write_inputs(tmp_path / "in", coppie(), [{k: v for k, v in row.items() if k != "n_comuni"} for row in cammini()])
    with pytest.raises(workbook.WorkbookError, match="n_comuni"):
        workbook.run(tmp_path / "in", tmp_path / "out", RUN_DATE, tmp_path / "manca.csv")


def test_run_tells_when_the_file_is_open_in_excel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_sources: None) -> None:
    write_inputs(tmp_path / "in", coppie(), cammini())

    def locked(self: Workbook, filename: object) -> None:
        raise PermissionError(filename)

    monkeypatch.setattr(Workbook, "save", locked)
    with pytest.raises(workbook.WorkbookError, match="aperto in Excel"):
        workbook.run(tmp_path / "in", tmp_path / "out", RUN_DATE, tmp_path / "manca.csv")


def test_cli_workbook_calls_run_and_reports_errors(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[str] = []
    monkeypatch.setattr(workbook, "run", lambda: calls.append("run"))
    assert cli.main(["workbook"]) == 0 and calls == ["run"]

    def broken() -> None:
        raise workbook.WorkbookError("Comune 001001: prova")

    monkeypatch.setattr(workbook, "run", broken)
    assert cli.main(["workbook"]) == 2
    assert "ERRORE: Comune 001001: prova" in capsys.readouterr().err
