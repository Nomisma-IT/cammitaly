"""Cartella di lavoro: fixture sintetica scritta secondo il contratto P5, riletta con openpyxl dal file salvato."""
import csv
import dataclasses
from datetime import date
from pathlib import Path

import geopandas as gpd
import pytest
from openpyxl import load_workbook
from openpyxl.workbook import Workbook
from shapely.geometry import Point

from cammitaly import __main__ as cli
from cammitaly import config, workbook

RUN_DATE = date(2026, 11, 4)
AGLIE, TORINO, CASTRO_BG, CASTRO_LE = "001001", "001272", "016066", "075014"
ISTAT = {AGLIE, TORINO, CASTRO_BG, CASTRO_LE, "058091"}
PLACES = {
    AGLIE: ("Agliè", "TO", "201", "Torino", "1", "Piemonte"),
    TORINO: ("Torino", "TO", "201", "Torino", "1", "Piemonte"),
    CASTRO_BG: ("Castro", "BG", "16", "Bergamo", "3", "Lombardia"),
    CASTRO_LE: ("Castro", "LE", "75", "Lecce", "16", "Puglia"),
}
TAGS = {
    "u001": ("lineare", "nazionale|regionale", "spirituale", "primavera|autunno", "si", "no", "5 maggio 2025"),
    "u002": ("anello", "", "arte-e-cultura|coast-to-coast", "", "no", "si", ""),
    "u003": ("", "", "", "", "", "", ""),
}


def pair(cid: str, nome: str, code: str, ordine: int, mezzo: str, religioso: str, dentro: str = "True") -> dict[str, str]:
    comune, sigla, cod_uts, provincia, cod_reg, regione = PLACES[code]
    return {
        "cammino_unico_id": cid, "nome": nome, "PRO_COM_T": code, "comune": comune, "sigla": sigla,
        "cod_uts": cod_uts, "provincia": provincia, "cod_reg": cod_reg, "regione": regione, "ordine": str(ordine),
        "mezzo": mezzo, "religioso": religioso, "distanza_m": "12.5", "dentro": dentro, "fonte_geometria": "catalogo",
    }


def coppie() -> list[dict[str, str]]:
    # Righe fuori ordine di proposito: il foglio le riordina
    return [
        pair("u002", "Cammino Beta", CASTRO_LE, 2, "entrambi", "no", "False"),
        pair("u001", "Cammino Alfa", CASTRO_BG, 3, "entrambi", "si"),
        pair("u001", "Cammino Alfa", TORINO, 2, "piedi", "si"),
        pair("u002", "Cammino Beta", TORINO, 1, "bici", "no"),
        pair("u001", "Cammino Alfa", AGLIE, 1, "piedi", "si"),
    ]


def cammino(cid: str, nome: str, n_comuni: int, **extra: str) -> dict[str, str]:
    tipologia, portata, tema, stagione, credenziale, segnaletica, data_catalogo = TAGS[cid]
    row = {
        "cammino_unico_id": cid, "nome": nome, "alias": "Alias del cammino|Altro nome" if cid == "u001" else "",
        "fonti": "cdi|mitur" if cid == "u001" else "osm", "membri": "cdi:1|mitur:uno" if cid == "u001" else "osm:r9",
        "mezzo": "entrambi", "religioso": "si" if cid == "u001" else "no",
        "evidenza_religioso": "Catalogo|OpenStreetMap: pellegrinaggio" if cid == "u001" else "nessuna fonte",
        "tipologia": tipologia, "portata": portata, "tema": tema, "stagione": stagione, "credenziale": credenziale,
        "segnaletica": segnaletica, "data_catalogo": data_catalogo, "regioni": "piemonte|lombardia",
        "lunghezza_dichiarata_km": "120", "lunghezza_tracciata_km": "118.4", "copertura": "0.99",
        "fonte_geometria": "catalogo" if cid != "u003" else "nessuna", "nota": "", "n_comuni": str(n_comuni),
    }
    return {**row, **extra}


def cammini() -> list[dict[str, str]]:
    return [
        cammino("u003", "Cammino Gamma", 0),
        cammino("u002", "Cammino Beta", 2),
        cammino("u001", "Cammino Alfa", 3),
    ]


def build(coppie_rows=None, cammini_rows=None, istat=None) -> Workbook:
    return workbook.build_workbook(
        coppie() if coppie_rows is None else coppie_rows,
        cammini() if cammini_rows is None else cammini_rows,
        ISTAT if istat is None else istat,
        RUN_DATE,
    )


@pytest.fixture(scope="module")
def saved(tmp_path_factory: pytest.TempPathFactory) -> Workbook:
    path = tmp_path_factory.mktemp("xlsx") / "prova.xlsx"
    build().save(path)
    return load_workbook(path)


def column(sheet, number: int) -> list:
    return [row[number - 1] for row in sheet.iter_rows(min_row=2, values_only=True)]


def test_sheets_in_order(saved: Workbook) -> None:
    assert saved.sheetnames == ["Comuni", "Cammini", "Coppie", "Note"]


def test_comuni_rows(saved: Workbook) -> None:
    sheet = saved["Comuni"]
    assert [cell.value for cell in sheet[1]] == [
        "Codice regione", "Regione", "Codice provincia", "Provincia", "Sigla", "Codice comune", "Comune",
        "Mezzo", "Religioso", "Numero cammini", "Cammini",
    ]
    rows = [tuple(row) for row in sheet.iter_rows(min_row=2, values_only=True)]
    assert rows == [
        ("01", "Piemonte", "201", "Torino", "TO", AGLIE, "Agliè", "Tutti", "Tutti", 1, "Cammino Alfa"),
        ("01", "Piemonte", "201", "Torino", "TO", AGLIE, "Agliè", "A piedi", "Sì", 1, "Cammino Alfa"),
        ("01", "Piemonte", "201", "Torino", "TO", TORINO, "Torino", "Tutti", "Tutti", 2, "Cammino Alfa; Cammino Beta"),
        ("01", "Piemonte", "201", "Torino", "TO", TORINO, "Torino", "A piedi", "Sì", 1, "Cammino Alfa"),
        ("01", "Piemonte", "201", "Torino", "TO", TORINO, "Torino", "In bici", "No", 1, "Cammino Beta"),
        ("03", "Lombardia", "016", "Bergamo", "BG", CASTRO_BG, "Castro", "Tutti", "Tutti", 1, "Cammino Alfa"),
        ("03", "Lombardia", "016", "Bergamo", "BG", CASTRO_BG, "Castro", "A piedi e in bici", "Sì", 1, "Cammino Alfa"),
        ("16", "Puglia", "075", "Lecce", "LE", CASTRO_LE, "Castro", "Tutti", "Tutti", 1, "Cammino Beta"),
        ("16", "Puglia", "075", "Lecce", "LE", CASTRO_LE, "Castro", "A piedi e in bici", "No", 1, "Cammino Beta"),
    ]


def test_comuni_numbers_are_numbers_and_codes_are_text(saved: Workbook) -> None:
    sheet = saved["Comuni"]
    for row in sheet.iter_rows(min_row=2):
        assert row[9].data_type == "n" and isinstance(row[9].value, int)
        for index in (0, 2, 5):
            assert row[index].data_type == "s" and row[index].number_format == "@"


def test_comuni_totals_bold_details_not(saved: Workbook) -> None:
    sheet = saved["Comuni"]
    for row in sheet.iter_rows(min_row=2):
        is_total = row[7].value == "Tutti"
        assert all(bool(cell.font.bold) == is_total for cell in row)
    assert all(cell.font.bold for cell in sheet[1])


def test_comuni_outline_totals_above_details_collapsed(saved: Workbook) -> None:
    sheet = saved["Comuni"]
    assert sheet.sheet_properties.outlinePr.summaryBelow is False
    assert sheet.sheet_format.outlineLevelRow == 1
    for row in sheet.iter_rows(min_row=2):
        dimension = sheet.row_dimensions[row[0].row]
        if row[7].value == "Tutti":
            assert dimension.outline_level == 0 and dimension.collapsed and not dimension.hidden
        else:
            assert dimension.outline_level == 1 and dimension.hidden


def test_comuni_freeze_and_widths(saved: Workbook) -> None:
    sheet = saved["Comuni"]
    assert sheet.freeze_panes == "A2"
    assert all(sheet.column_dimensions[letter].width >= config.WORKBOOK_WIDTH_MIN for letter in "ABCDEFGHIJK")


def test_cammini_rows_sorted_with_ragged_comuni(saved: Workbook) -> None:
    sheet = saved["Cammini"]
    headers = [cell.value for cell in sheet[1]]
    assert headers[:3] == ["Codice cammino", "Cammino", "Altri nomi"]
    assert headers[-4:] == ["Numero comuni", "Comune 1", "Comune 2", "Comune 3"]
    assert column(sheet, 2) == ["Cammino Alfa", "Cammino Beta", "Cammino Gamma"]
    rows = {row[1]: dict(zip(headers, row)) for row in sheet.iter_rows(min_row=2, values_only=True)}
    alfa, beta, gamma = rows["Cammino Alfa"], rows["Cammino Beta"], rows["Cammino Gamma"]
    assert [alfa[f"Comune {n}"] for n in (1, 2, 3)] == ["Agliè (TO)", "Torino (TO)", "Castro (BG)"]
    assert [beta["Comune 1"], beta["Comune 2"], beta["Comune 3"]] == ["Torino (TO)", "Castro (LE)", None]
    assert gamma["Comune 1"] is None and gamma["Numero comuni"] == 0


def test_cammini_display_values(saved: Workbook) -> None:
    sheet = saved["Cammini"]
    headers = [cell.value for cell in sheet[1]]
    alfa = dict(zip(headers, next(row for row in sheet.iter_rows(min_row=2, values_only=True) if row[1] == "Cammino Alfa")))
    assert alfa["Altri nomi"] == "Alias del cammino; Altro nome"
    assert alfa["Fonti"] == "camminiditalia.org; Catalogo dei cammini religiosi del Ministero del Turismo"
    assert alfa["Mezzo"] == "A piedi e in bici" and alfa["Religioso"] == "Sì"
    assert alfa["Portata"] == "nazionale; regionale" and alfa["Stagione"] == "primavera; autunno"
    assert alfa["Lunghezza dichiarata (km)"] == 120 and alfa["Copertura del tracciato"] == 0.99
    assert alfa["Numero comuni"] == 3 and alfa["Fonte del tracciato"] == "Catalogo"


def test_cammini_and_coppie_have_filter_and_frozen_header(saved: Workbook) -> None:
    for name in ("Comuni", "Cammini", "Coppie"):
        sheet = saved[name]
        assert sheet.freeze_panes == "A2"
        assert sheet.auto_filter.ref == sheet.dimensions
        assert all(cell.font.bold for cell in sheet[1])


def test_coppie_rows_tags_and_display(saved: Workbook) -> None:
    sheet = saved["Coppie"]
    headers = [cell.value for cell in sheet[1]]
    assert headers[-7:] == [
        "Tipologia", "Portata", "Tema", "Stagione", "Credenziale", "Segnaletica", "Data nel catalogo",
    ]
    rows = [dict(zip(headers, row)) for row in sheet.iter_rows(min_row=2, values_only=True)]
    assert [(r["Cammino"], r["Ordine lungo il cammino"]) for r in rows] == [
        ("Cammino Alfa", 1), ("Cammino Alfa", 2), ("Cammino Alfa", 3), ("Cammino Beta", 1), ("Cammino Beta", 2),
    ]
    assert rows[0]["Portata"] == "nazionale; regionale" and rows[0]["Credenziale"] == "Sì" and rows[0]["Segnaletica"] == "No"
    assert rows[4]["Dentro il comune"] == "No" and rows[0]["Dentro il comune"] == "Sì"
    assert rows[3]["Mezzo"] == "In bici" and rows[3]["Religioso"] == "No"
    assert rows[0]["Distanza dal comune (m)"] == 12.5 and isinstance(rows[0]["Ordine lungo il cammino"], int)
    assert (rows[0]["Codice regione"], rows[0]["Codice provincia"], rows[0]["Codice comune"]) == ("01", "201", AGLIE)
    assert rows[1]["Codice comune"] == TORINO and rows[2]["Codice provincia"] == "016"


def test_note_has_parameters_and_credits(saved: Workbook) -> None:
    text = "\n".join(str(row[0]) for row in saved["Note"].iter_rows(values_only=True) if row[0])
    assert f"{config.CROSSING_DISTANCE_M} m" in text
    assert f"1 gennaio {config.ISTAT_EDITION_YEAR}" in text
    assert "04/11/2026" in text
    assert "© OpenStreetMap contributors, ODbL" in text and "CC BY 4.0" in text
    for source in ("camminiditalia.org", "Catalogo dei cammini religiosi del Ministero del Turismo", "Geofabrik"):
        assert source in text
    assert "{" not in text


def test_no_dashes_in_any_cell(saved: Workbook) -> None:
    forbidden = ("—", "–")
    for sheet in saved.worksheets:
        for row in sheet.iter_rows(values_only=True):
            assert not any(isinstance(value, str) and any(dash in value for dash in forbidden) for value in row)


# --- invarianti: ognuna deve fermare la costruzione con un messaggio italiano ---

def with_pair(rows: list[dict[str, str]], cid: str, code: str, **changes: str) -> list[dict[str, str]]:
    return [{**row, **changes} if (row["cammino_unico_id"], row["PRO_COM_T"]) == (cid, code) else row for row in rows]


def test_code_not_six_digits_raises() -> None:
    with pytest.raises(workbook.WorkbookError, match="PRO_COM_T"):
        build(coppie_rows=with_pair(coppie(), "u001", TORINO, PRO_COM_T="1272"))


def test_code_not_in_istat_raises() -> None:
    with pytest.raises(workbook.WorkbookError, match=TORINO):
        build(istat=ISTAT - {TORINO})


def test_duplicate_pair_raises() -> None:
    rows = coppie() + [pair("u001", "Cammino Alfa", AGLIE, 4, "piedi", "si")]
    with pytest.raises(workbook.WorkbookError, match="più di una volta"):
        build(coppie_rows=rows)


def test_duplicate_cammino_raises() -> None:
    with pytest.raises(workbook.WorkbookError, match="u001"):
        build(cammini_rows=cammini() + [cammino("u001", "Cammino Alfa", 3)])


def test_cammino_missing_from_cammini_raises() -> None:
    rows = [row for row in cammini() if row["cammino_unico_id"] != "u002"]
    with pytest.raises(workbook.WorkbookError, match="u002"):
        build(cammini_rows=rows)


def test_n_comuni_mismatch_raises() -> None:
    rows = [cammino("u001", "Cammino Alfa", 4) if r["cammino_unico_id"] == "u001" else r for r in cammini()]
    with pytest.raises(workbook.WorkbookError, match="n_comuni"):
        build(cammini_rows=rows)


def test_gamma_with_comuni_in_cammini_but_none_in_coppie_raises() -> None:
    rows = [cammino("u003", "Cammino Gamma", 1) if r["cammino_unico_id"] == "u003" else r for r in cammini()]
    with pytest.raises(workbook.WorkbookError, match="u003"):
        build(cammini_rows=rows)


@pytest.mark.parametrize("ordine", ["3", "0", "5"])
def test_non_contiguous_order_raises(ordine: str) -> None:
    rows = with_pair(coppie(), "u001", TORINO, ordine=ordine)  # u001 ha ordini 1, <ordine>, 3
    with pytest.raises(workbook.WorkbookError, match="ordine"):
        build(coppie_rows=rows)


def test_block_totals_must_match_pairs() -> None:
    pairs = [workbook._typed_row(row, config.WORKBOOK_COPPIE_FIELDS, "coppie") for row in coppie()]
    blocks = workbook.build_blocks(pairs)
    workbook.check_blocks(blocks, pairs)  # integri: nessun errore
    wrong_total = [dataclasses.replace(block, n_cammini=block.n_cammini + 1) for block in blocks]
    with pytest.raises(workbook.WorkbookError, match="totale"):
        workbook.check_blocks(wrong_total, pairs)
    wrong_detail = [dataclasses.replace(blocks[0], details=())] + blocks[1:]
    with pytest.raises(workbook.WorkbookError, match="somma del dettaglio"):
        workbook.check_blocks(wrong_detail, pairs)


@pytest.mark.parametrize("change", [{"mezzo": "treno"}, {"religioso": "forse"}, {"dentro": "boh"}, {"ordine": "x"}])
def test_invalid_value_raises(change: dict[str, str]) -> None:
    with pytest.raises(workbook.WorkbookError, match="non valido"):
        build(coppie_rows=with_pair(coppie(), "u001", TORINO, **change))


# --- file, riga di comando ---

def write_inputs(folder: Path, coppie_rows: list[dict[str, str]], cammini_rows: list[dict[str, str]]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, rows in ((config.PAIRS_FILES["coppie"], coppie_rows), (config.PAIRS_FILES["cammini"], cammini_rows)):
        with (folder / name).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)


def write_istat(path: Path, codes: set[str]) -> Path:
    frame = gpd.GeoDataFrame(
        {config.COMUNE_ID_COL: sorted(codes), config.COMUNE_NAME_COL: ["Agliè"] * len(codes)},
        geometry=[Point(0, 0)] * len(codes), crs="EPSG:32632",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_file(path, engine="pyogrio", encoding="utf-8")
    return path


def test_run_writes_dated_file(tmp_path: Path) -> None:
    write_inputs(tmp_path / "in", coppie(), cammini())
    shapefile = write_istat(tmp_path / "istat" / "comuni.shp", ISTAT)
    target = workbook.run(tmp_path / "in", tmp_path / "out", shapefile, RUN_DATE)
    assert target == tmp_path / "out" / "cammini_comuni_2026-11-04.xlsx"
    assert load_workbook(target).sheetnames == ["Comuni", "Cammini", "Coppie", "Note"]


def test_run_writes_nothing_on_breach(tmp_path: Path) -> None:
    write_inputs(tmp_path / "in", coppie(), cammini())
    shapefile = write_istat(tmp_path / "istat" / "comuni.shp", ISTAT - {AGLIE})
    with pytest.raises(workbook.WorkbookError):
        workbook.run(tmp_path / "in", tmp_path / "out", shapefile, RUN_DATE)
    assert not (tmp_path / "out").exists()


def test_run_missing_input_is_a_workbook_error(tmp_path: Path) -> None:
    shapefile = write_istat(tmp_path / "istat" / "comuni.shp", ISTAT)
    with pytest.raises(workbook.WorkbookError, match="pairs"):
        workbook.run(tmp_path / "in", tmp_path / "out", shapefile, RUN_DATE)


def test_run_missing_column_is_a_workbook_error(tmp_path: Path) -> None:
    rows = [{key: value for key, value in row.items() if key != "n_comuni"} for row in cammini()]
    write_inputs(tmp_path / "in", coppie(), rows)
    shapefile = write_istat(tmp_path / "istat" / "comuni.shp", ISTAT)
    with pytest.raises(workbook.WorkbookError, match="n_comuni"):
        workbook.run(tmp_path / "in", tmp_path / "out", shapefile, RUN_DATE)


def test_cli_workbook_calls_run_and_reports_errors(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    calls: list[str] = []
    monkeypatch.setattr(workbook, "run", lambda: calls.append("run"))
    assert cli.main(["workbook"]) == 0 and calls == ["run"]

    def broken() -> None:
        raise workbook.WorkbookError("Comune 001001: prova")

    monkeypatch.setattr(workbook, "run", broken)
    assert cli.main(["workbook"]) == 2
    assert "ERRORE: Comune 001001: prova" in capsys.readouterr().err


# --- visualizzazione: elenchi, parole, Sì/No, fonti ---

def test_nessuna_barra_verticale_in_nessun_foglio(saved: Workbook) -> None:
    for sheet in saved.worksheets:
        for row in sheet.iter_rows(values_only=True):
            assert not any(isinstance(value, str) and config.LIST_SEPARATOR in value for value in row), sheet.title


def test_evidenza_e_tema_si_leggono_come_elenchi_di_parole(saved: Workbook) -> None:
    sheet = saved["Cammini"]
    headers = [cell.value for cell in sheet[1]]
    rows = {row[1]: dict(zip(headers, row)) for row in sheet.iter_rows(min_row=2, values_only=True)}
    assert rows["Cammino Alfa"]["Indicazione del carattere religioso"] == "Catalogo; OpenStreetMap: pellegrinaggio"
    assert rows["Cammino Beta"]["Indicazione del carattere religioso"] == "nessuna fonte"
    assert rows["Cammino Beta"]["Tema"] == "arte e cultura; coast to coast" and rows["Cammino Alfa"]["Tema"] == "spirituale"


def test_credenziale_e_segnaletica_come_si_no(saved: Workbook) -> None:
    for name in ("Cammini", "Coppie"):
        sheet = saved[name]
        headers = [cell.value for cell in sheet[1]]
        rows = [dict(zip(headers, row)) for row in sheet.iter_rows(min_row=2, values_only=True)]
        pairs_shown = {(r["Credenziale"], r["Segnaletica"]) for r in rows}
        assert pairs_shown <= {("Sì", "No"), ("No", "Sì"), (None, None)} and ("Sì", "No") in pairs_shown


def test_fonte_del_tracciato_catalogo_e_osm_ha_la_sua_etichetta() -> None:
    sheet = build(cammini_rows=[cammino("u003", "Cammino Gamma", 0), cammino("u002", "Cammino Beta", 2),
                                cammino("u001", "Cammino Alfa", 3, fonte_geometria="catalogo e osm")])["Cammini"]
    headers = [cell.value for cell in sheet[1]]
    shown = {row[1]: dict(zip(headers, row))["Fonte del tracciato"] for row in sheet.iter_rows(min_row=2, values_only=True)}
    assert shown == {"Cammino Alfa": "Catalogo e OpenStreetMap", "Cammino Beta": "Catalogo", "Cammino Gamma": "Nessuna"}


# --- foglio Note: date di scarico e frasi richieste ---

def write_log(path: Path, rows: list[tuple[str, str, str]]) -> Path:
    lines = ["utc_iso,host_key,url,status,bytes,seconds", *(f"{when},{host},https://x/{host},{status},1,0.1" for when, host, status in rows)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_date_di_scarico_dal_registro_delle_richieste(tmp_path: Path) -> None:
    log = write_log(tmp_path / "log.csv", [
        ("2026-11-03T10:00:00+00:00", "camminiditalia", "200"), ("2026-11-05T10:00:00+00:00", "s3", "200"),
        ("2026-11-04T10:00:00+00:00", "mitur", "403"), ("2026-11-04T09:00:00+00:00", "istat", "200"),
    ])
    periods = workbook.download_periods(log)
    assert periods["data_cdi"] == "dal 03/11/2026 al 05/11/2026" and periods["data_istat"] == "il 04/11/2026"
    assert periods["data_mitur"] == periods["data_osm"] == config.WORKBOOK_DOWNLOAD_MISSING  # richiesta fallita o assente
    assert set(workbook.download_periods(tmp_path / "manca.csv").values()) == {config.WORKBOOK_DOWNLOAD_MISSING}


def note_text(sheet) -> str:
    return "\n".join(str(row[0]) for row in sheet.iter_rows(values_only=True) if row[0])


def test_foglio_note_con_date_e_frasi_richieste() -> None:
    downloads = {"data_cdi": "il 03/11/2026", "data_mitur": "il 03/11/2026", "data_osm": "il 02/11/2026", "data_istat": "il 01/11/2026"}
    text = note_text(workbook.build_workbook(coppie(), cammini(), ISTAT, RUN_DATE, downloads)["Note"])
    assert "Scaricato il 02/11/2026. © OpenStreetMap" in text and "Scaricato il 01/11/2026." in text
    assert "{" not in text
    for sentence in (
        "I comuni seguono il percorso dalla partenza all'arrivo; per i cammini con più rami seguono un ramo dopo l'altro.",
        "Per i tracciati presi da OpenStreetMap senza una partenza pubblicata il verso non è garantito.",
        "un cammino presente in più fonti è contato una sola volta",
        "se il nome del cammino contiene una parola religiosa (ad esempio San, Santa, Romea)",
        "sulla mappa Cammini d'Italia",
        "Catalogo e OpenStreetMap",
        "Catalogo dei cammini religiosi del Ministero del Turismo",
    ):
        assert sentence in text
    assert "Catalogo del Ministero del Turismo" not in text
    assert "Scaricato in data non registrata" in note_text(build()["Note"])  # senza registro


def test_data_nel_catalogo_e_una_vera_data_di_excel() -> None:
    sheet = build(cammini_rows=[
        cammino("u003", "Cammino Gamma", 0, data_catalogo="2023-06-16"),
        cammino("u002", "Cammino Beta", 2, data_catalogo="2023-06-16|2024-02-01"),
        cammino("u001", "Cammino Alfa", 3, data_catalogo="5 maggio 2025"),
    ])["Cammini"]
    headers = [cell.value for cell in sheet[1]]
    column_index = headers.index("Data nel catalogo")
    cells = {row[1].value: row[column_index] for row in sheet.iter_rows(min_row=2)}
    single = cells["Cammino Gamma"]
    assert single.value == date(2023, 6, 16)  # data vera (non testo): Excel la ordina e la filtra come data
    assert single.number_format == "DD/MM/YYYY"
    assert cells["Cammino Beta"].value == "16/06/2023; 01/02/2024"  # piu' date: testo GG/MM/AAAA
    assert cells["Cammino Alfa"].value == "5 maggio 2025"  # un testo che non e' una data resta com'e'
