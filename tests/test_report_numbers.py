"""Numeri del rapporto: cartella data/ sintetica in tmp_path, controllo di id unici e di valori noti a mano."""
import csv
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from openpyxl import Workbook
from shapely.geometry import Point

from cammitaly import __main__ as cli
from cammitaly import config, report_numbers
from cammitaly.report_numbers import ReportNumbersError

AGLIE, TORINO, ROMA, CASTRO = "001001", "001272", "058091", "016066"
POINTS = {AGLIE: (7.77, 45.36), TORINO: (7.68, 45.07), ROMA: (12.5, 41.9), CASTRO: (9.9, 45.8)}
COMUNI = {AGLIE: ("Aglie", 1), TORINO: ("Torino", 1), ROMA: ("Roma", 12), CASTRO: ("Castro", 3)}
REGIONI = {"Piemonte": 1, "Lombardia": 3, "Lazio": 12}


def put(data_dir: Path, path: Path, header: list[str], rows: list[dict[str, object]]) -> None:
    """Scrive un CSV al posto di `path` sotto la cartella data/ di prova; le celle non indicate restano vuote."""
    target = data_dir / path.relative_to(config.DATA_DIR)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, restval="", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def put_shapefile(data_dir: Path, path: Path, rows: list[dict[str, object]], points: list[tuple[float, float]]) -> None:
    target = data_dir / path.relative_to(config.DATA_DIR)
    target.parent.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame(rows, geometry=[Point(xy) for xy in points], crs="EPSG:4326").to_file(target, encoding=config.ISTAT_ENCODING)


def put_tratte(data_dir: Path, fonte: str, tipi: list[str]) -> None:
    target = data_dir / Path(config.TRATTE_GPKG_TEMPLATE.format(fonte=fonte)).relative_to(config.DATA_DIR)
    target.parent.mkdir(parents=True, exist_ok=True)
    frame = gpd.GeoDataFrame(
        {"tipo": tipi, "lunghezza_m": [1000.0] * len(tipi), "spaziatura_media_m": [10.0 * (i + 1) for i in range(len(tipi))]},
        geometry=[Point(0, i) for i in range(len(tipi))], crs=config.CRS_WORK,
    )
    frame.to_file(target, layer=config.TRATTE_LAYER, driver="GPKG")


def build_sources(data_dir: Path) -> None:
    put(data_dir, config.CDI_CAMMINI_CSV, ["id", "tipologia", "webmapp_layer", "lunghezza_km", "partenza", "arrivo", "regioni"], [
        {"id": "1", "tipologia": "lineare", "webmapp_layer": "5", "lunghezza_km": "100", "partenza": "Aglie", "arrivo": "Roma", "regioni": "piemonte|lazio"},
        {"id": "2", "tipologia": "anello", "webmapp_layer": "", "lunghezza_km": "50", "partenza": "Torino", "arrivo": "Torino", "regioni": "piemonte"},
    ])
    put(data_dir, config.CDI_TAPPE_CSV, ["cammino_id", "ordine", "partenza", "arrivo"], [
        {"cammino_id": "1", "ordine": "1", "partenza": "Aglie", "arrivo": "Torino"},
        {"cammino_id": "1", "ordine": "2", "partenza": "Torino", "arrivo": "Roma"},
    ])
    put(data_dir, config.WEBMAPP_TRACKS_CSV, ["track_id", "layers", "has_geometry"], [
        {"track_id": "1", "layers": "5", "has_geometry": "True"}, {"track_id": "2", "layers": "5|6", "has_geometry": "True"},
    ])
    put(data_dir, config.MITUR_CAMMINI_CSV, ["nome", "url_percorso", "n_file", "tipi_file", "nota", "in_catalogo_dal"], [
        {"nome": "Uno", "url_percorso": "https://x/uno.zip", "n_file": "3", "tipi_file": "gpx:2|kml:1", "in_catalogo_dal": "2023-03-23"},
        {"nome": "Due", "url_percorso": "", "n_file": "0", "tipi_file": "", "nota": "zip condiviso", "in_catalogo_dal": "2025-01-10"},
    ])
    osm_path = Path(config.CAMMINI_CSV_TEMPLATE.format(fonte=config.OSM_FONTE))
    put(data_dir, osm_path, ["stato_geometria", "is_super", "route", "network", "pilgrimage"], [
        {"stato_geometria": "ok", "is_super": "False", "route": "hiking", "network": "lwn", "pilgrimage": "yes"},
        {"stato_geometria": "parziale", "is_super": "True", "route": "foot", "network": "Lwn", "pilgrimage": "no"},
        {"stato_geometria": "ok", "is_super": "False", "route": "hiking", "network": "", "pilgrimage": "Via X"},
    ])
    put_shapefile(
        data_dir, config.ISTAT_COMUNI_SHP,
        [{config.COMUNE_ID_COL: code, config.COMUNE_NAME_COL: name, config.COMUNE_REGION_COL: region} for code, (name, region) in COMUNI.items()],
        [POINTS[code] for code in COMUNI],
    )
    put_shapefile(
        data_dir, config.ISTAT_REGIONI_SHP,
        [{config.REGION_NAME_COL: name, config.REGION_CODE_COL: code} for name, code in REGIONI.items()], [(0, 0)] * len(REGIONI),
    )
    put(data_dir, config.REQUESTS_LOG, list(config.REQUESTS_LOG_FIELDS), [
        {"utc_iso": "2026-11-03T10:58:04+00:00", "host_key": "istat", "status": "200", "bytes": str(2 * 1024**2)},
        {"utc_iso": "2026-11-03T11:00:00+00:00", "host_key": "mitur", "status": "200", "bytes": "1048576"},
        {"utc_iso": "2026-11-04T09:00:00+00:00", "host_key": "mitur", "status": "200", "bytes": "1048576"},
        {"utc_iso": "2026-11-04T09:01:00+00:00", "host_key": "mitur", "status": "500", "bytes": "0"},
    ])


def build_tracks(data_dir: Path) -> None:
    for fonte, tipi in (("cdi", ["tappa", "tappa", "variante"]), ("mitur", ["tappa", "bici"]), ("osm", ["altro", "altro"])):
        put_tratte(data_dir, fonte, tipi)
        if fonte != config.OSM_FONTE:  # cammini_osm.csv e' gia' scritto da build_sources (stesso file)
            put(data_dir, Path(config.CAMMINI_CSV_TEMPLATE.format(fonte=fonte)), ["cammino_id", "stato_geometria"], [
                {"cammino_id": f"{fonte}:1", "stato_geometria": "ok"}, {"cammino_id": f"{fonte}:2", "stato_geometria": "assente"},
            ])
        put(data_dir, Path(config.LIMBO_CSV_TEMPLATE.format(fonte=fonte)), ["oggetto", "motivo", "dettaglio"], [
            {"oggetto": f"{fonte}:9", "motivo": "duplicato interno"},
        ])


def build_calibration(data_dir: Path) -> None:
    directory = config.CALIBRATION_DIR
    columns = [column for column, *_ in config.REPORT_CALIBRATION_COLUMNS]
    columns += [column for column, *_ in config.REPORT_CALIBRATION_FIXED]
    columns += [f"B_{method}_{measure}" for method in ("k25", "o50") for measure in ("recall", "extra_tot", "recall_marginale")]
    rows = [{"d_m": d, **{column: 0.5 for column in columns}} for d in (0, 100)]
    rows[1]["A_recall_cammino"] = 0.9787
    rows[1]["B_o50_recall"] = 0.9677
    rows[1]["D_aumento_vs_0_pct"] = 5.35
    put(data_dir, directory / config.CALIBRATION_FILE_SINTESI, ["d_m", *columns], rows)
    put(data_dir, directory / config.CALIBRATION_FILES_A["distribuzione"], ["ambito", "n_misurate", "quota_a_0", "p50", "p90", "p95", "p99", "max"], [
        {"ambito": "cammino", "n_misurate": 938, "quota_a_0": 0.9787, "p50": 0, "p90": 0, "p95": 0, "p99": 50094.17, "max": 98080.92},
        {"ambito": "tratta", "n_misurate": 1676, "quota_a_0": 0.9869, "p50": 0, "p90": 0, "p95": 0, "p99": 40.17, "max": 47832.21},
    ])
    put(data_dir, directory / config.CALIBRATION_FILES_A["match"], ["origine", "ruolo", *config.REPORT_CALIBRATION_MATCH_COLUMNS, "quota_ok"], [
        {"origine": "tappe", "ruolo": "arrivo", "ok": 1, "omonimo": 0, "due luoghi": 0, "nome_parziale": 0, "non_trovato": 1, "vuoto": 0, "n_nomi": 2, "quota_ok": 0.5},
        {"origine": "tutte", "ruolo": "tutti", "ok": 3382, "omonimo": 0, "due luoghi": 21, "nome_parziale": 66, "non_trovato": 1667, "vuoto": 184, "n_nomi": 5320, "quota_ok": 0.6585},
    ])
    put(data_dir, directory / config.CALIBRATION_FILES_A["lontane"], ["cammino_id", "motivo"], [
        {"cammino_id": "cdi:1", "motivo": "ramo senza traccia"}, {"cammino_id": "cdi:1", "motivo": "ramo senza traccia"}, {"cammino_id": "cdi:2", "motivo": ""},
    ])
    put(data_dir, directory / config.CALIBRATION_FILES_B["totale"], ["d_m", "spaziatura_originale_m"], [{"d_m": 0, "spaziatura_originale_m": 6.87}])
    put(data_dir, directory / config.CALIBRATION_FILES_C["coppie"], ["cdi_id", "nel_rapporto", "mitur_densa"], [
        {"cdi_id": "a", "nel_rapporto": "True", "mitur_densa": "False"}, {"cdi_id": "b", "nel_rapporto": "False", "mitur_densa": "True"},
        {"cdi_id": "c", "nel_rapporto": "True", "mitur_densa": "True"},
    ])
    put(data_dir, directory / config.CALIBRATION_FILES_D["grazing"], ["soglia_m", "coppie_sotto_soglia", "quota"], [
        {"soglia_m": 50, "coppie_sotto_soglia": 36, "quota": 0.006}, {"soglia_m": 100, "coppie_sotto_soglia": 83, "quota": 0.0138},
    ])


def build_dedup(data_dir: Path) -> None:
    directory, files = config.DEDUP_DIR, config.DEDUP_FILES
    put(data_dir, directory / files["membri"], ["cammino_unico_id", "cammino_id", "fonte", "ruolo"], [
        {"cammino_unico_id": "u001", "cammino_id": "cdi:1", "fonte": "cdi", "ruolo": "principale"},
        {"cammino_unico_id": "u001", "cammino_id": "mitur:uno", "fonte": "mitur", "ruolo": "duplicato"},
        {"cammino_unico_id": "u002", "cammino_id": "mitur:due", "fonte": "mitur", "ruolo": "principale"},
        {"cammino_unico_id": "u002", "cammino_id": "cdi:2", "fonte": "cdi", "ruolo": "sezione"},
        {"cammino_unico_id": "u003", "cammino_id": "cdi:3", "fonte": "cdi", "ruolo": "principale"},
        {"cammino_unico_id": "u004", "cammino_id": "mitur:quattro", "fonte": "mitur", "ruolo": "principale"},
    ])
    put(data_dir, directory / files["unici"], ["cammino_unico_id", "fonti", "n_membri", "alias", "stato", "catena"], [
        {"cammino_unico_id": "u001", "fonti": "cdi|mitur", "n_membri": 2, "alias": "Altro nome", "stato": "ok", "catena": "False"},
        {"cammino_unico_id": "u002", "fonti": "cdi|mitur", "n_membri": 2, "stato": "parziale", "catena": "True"},
        {"cammino_unico_id": "u003", "fonti": "cdi", "n_membri": 1, "stato": "ok", "catena": "False"},
        {"cammino_unico_id": "u004", "fonti": "mitur", "n_membri": 1, "stato": "assente", "catena": "False"},
    ])
    put(data_dir, directory / files["limbo"], ["oggetto", "motivo"], [{"oggetto": "a|b", "motivo": "nome simile, geometria assente"}])
    put(data_dir, directory / files["sensibilita"], [
        "soglia_nome", "soglia_quota", "soglia_distinto", "distanza_m", "n_duplicato", "n_sezione", "n_limbo", "n_distinto",
        "n_nome_100", "n_nome_100_tracciato_diverso", "n_nome_100_geometria_assente",
    ], [
        {"soglia_nome": 95, "soglia_quota": 0.7, "soglia_distinto": 0.5, "distanza_m": 50, "n_duplicato": 2, "n_sezione": 1, "n_limbo": 0, "n_distinto": 2, "n_nome_100": 3, "n_nome_100_tracciato_diverso": 0, "n_nome_100_geometria_assente": 0},
        {"soglia_nome": 80, "soglia_quota": 0.6, "soglia_distinto": 0.4, "distanza_m": 25, "n_duplicato": 3, "n_sezione": 1, "n_limbo": 1, "n_distinto": 0, "n_nome_100": 3, "n_nome_100_tracciato_diverso": 0, "n_nome_100_geometria_assente": 0},
    ])
    put(data_dir, directory / files["coppie"], ["punteggio_nome", "quota_minore", "classe"], [
        {"punteggio_nome": 100, "quota_minore": 0.9, "classe": "duplicato"}, {"punteggio_nome": 100, "quota_minore": 1.0, "classe": "duplicato"},
        {"punteggio_nome": 100, "quota_minore": 0.99, "classe": "sezione"}, {"punteggio_nome": 87.5, "quota_minore": 0.1, "classe": "distinto"},
        {"punteggio_nome": 80, "quota_minore": 0.0, "classe": "distinto"},
    ])
    put(data_dir, directory / files["quasi"], ["diagnosi"], [{"diagnosi": config.DIAGNOSI_VERSIONE}, {"diagnosi": config.DIAGNOSI_VARIANTE}])
    put(data_dir, directory / files["comuni"], ["classe", "jaccard"], [
        {"classe": "duplicato", "jaccard": 1.0}, {"classe": "quasi duplicato", "jaccard": 0.5},
    ])


def build_labels_and_matches(data_dir: Path) -> None:
    put(data_dir, config.OSM_MATCH_DIR / config.OSM_MATCH_FILES["abbinamenti"], [
        "cammino_id", "fonte", "stato_geometria", "esito", "n_relazioni", "rapporto_dichiarata", "quota_catalogo_in_osm", "quota_osm_in_catalogo", "pellegrinaggio_osm",
    ], [
        {"cammino_id": "cdi:1", "fonte": "cdi", "stato_geometria": "ok", "esito": "abbinato", "n_relazioni": 2, "rapporto_dichiarata": 1.0, "quota_catalogo_in_osm": 0.9, "quota_osm_in_catalogo": 0.9, "pellegrinaggio_osm": "si"},
        {"cammino_id": "cdi:3", "fonte": "cdi", "stato_geometria": "ok", "esito": "nessuno"},
        {"cammino_id": "mitur:due", "fonte": "mitur", "stato_geometria": "assente", "esito": "abbinato", "n_relazioni": 1, "rapporto_dichiarata": 0.8, "pellegrinaggio_osm": "non indicato"},
        {"cammino_id": "mitur:quattro", "fonte": "mitur", "stato_geometria": "assente", "esito": "parziale"},
    ])
    pilgrimage = "OpenStreetMap: indicato come pellegrinaggio"
    put(data_dir, config.TAGS_CAMMINI_CSV, ["cammino_id", "fonte", "religioso", "evidenza_religioso"], [
        {"cammino_id": "cdi:1", "fonte": "cdi", "religioso": "si"}, {"cammino_id": "cdi:3", "fonte": "cdi", "religioso": "no"},
        {"cammino_id": "mitur:uno", "fonte": "mitur", "religioso": "si"},
        {"cammino_id": "osm:r1", "fonte": "osm", "religioso": "si", "evidenza_religioso": pilgrimage},
        {"cammino_id": "osm:r2", "fonte": "osm", "religioso": "no", "evidenza_religioso": "OpenStreetMap: indicato come non di pellegrinaggio (pilgrimage=no)"},
        {"cammino_id": "osm:r3", "fonte": "osm", "religioso": "si", "evidenza_religioso": f"{pilgrimage} (Via X)"},
    ])
    put(data_dir, config.TAGS_TRATTE_CSV, ["tratta_id", "fonte", "mezzo"], [
        {"tratta_id": "cdi:1", "fonte": "cdi", "mezzo": "piedi"}, {"tratta_id": "mitur:1", "fonte": "mitur", "mezzo": "bici"},
    ])


def pair_row(unico: str, name: str, code: str, order: int, region: str, cod_reg: str, **extra: str) -> dict[str, str]:
    comune = COMUNI[code][0]
    row = {
        config.PAIRS_UNICO_COL: unico, "nome": name, COMUNE: code, "comune": comune, "sigla": "XX", "provincia": f"Provincia di {comune}",
        "cod_reg": cod_reg, "regione": region, "ordine": str(order), "mezzo": "piedi", "religioso": "si", "dentro": "True",
        "fonte_geometria": "catalogo",
    }
    return {**row, **extra}


COMUNE = config.COMUNE_ID_COL


def build_pairs(data_dir: Path) -> None:
    religious_evidence = f"{config.EVIDENZA_CATALOGO_RELIGIOSO}|{config.EVIDENZA_TEMA_CDI}; {config.EVIDENZA_TEMA_LAYER}"
    base = {"alias": "", "mezzo": "piedi", "religioso": "si", "tipologia": "", "portata": "", "credenziale": "", "segnaletica": "", "nota": ""}
    cammini = [
        {**base, "cammino_unico_id": "u001", "nome": "Cammino Alfa", "fonti": "cdi|mitur", "evidenza_religioso": religious_evidence, "tipologia": "lineare",
         "portata": "nazionale", "credenziale": "si", "segnaletica": "no", "lunghezza_dichiarata_km": "100", "lunghezza_tracciata_km": "120",
         "copertura": "1.0", "fonte_geometria": "catalogo", "n_comuni": "3"},
        {**base, "cammino_unico_id": "u002", "nome": "Cammino Beta", "fonti": "mitur", "mezzo": "entrambi", "evidenza_religioso": "il nome contiene la parola «san»",
         "tipologia": "anello", "lunghezza_dichiarata_km": "50", "lunghezza_tracciata_km": "20", "copertura": "0.4", "fonte_geometria": "catalogo",
         "nota": "possibile duplicato di Cammino Alfa (u001): manca la geometria per il confronto", "n_comuni": "2"},
        {**base, "cammino_unico_id": "u003", "nome": "Cammino Gamma", "fonti": "cdi", "religioso": "no", "evidenza_religioso": config.EVIDENZA_SILENTE,
         "lunghezza_tracciata_km": "10", "fonte_geometria": "osm", "n_comuni": "1"},
        {**base, "cammino_unico_id": "u004", "nome": "Cammino Delta", "fonti": "mitur", "religioso": "no", "evidenza_religioso": config.EVIDENZA_SILENTE,
         "fonte_geometria": "nessuna", "nota": config.PAIRS_NOTA_NESSUNA_GEOMETRIA, "n_comuni": "0"},
    ]
    put(data_dir, config.PAIRS_DIR / config.PAIRS_FILES["cammini"], list(config.PAIRS_CAMMINI_FIELDS), cammini)
    pairs = [
        pair_row("u001", "Cammino Alfa", AGLIE, 1, "Piemonte", "01"),
        pair_row("u001", "Cammino Alfa", TORINO, 2, "Piemonte", "01", dentro="False"),
        pair_row("u001", "Cammino Alfa", ROMA, 3, "Lazio", "12"),
        pair_row("u002", "Cammino Beta", TORINO, 1, "Piemonte", "01", mezzo="bici"),
        pair_row("u002", "Cammino Beta", AGLIE, 2, "Piemonte", "01", mezzo="entrambi"),
        pair_row("u003", "Cammino Gamma", CASTRO, 1, "Lombardia", "03", religioso="no", fonte_geometria="osm"),
    ]
    put(data_dir, config.PAIRS_DIR / config.PAIRS_FILES["coppie"], list(config.PAIRS_COPPIE_FIELDS), pairs)


def build_workbook(data_dir: Path) -> None:
    book = Workbook()
    comuni = book.active
    comuni.title = config.WORKBOOK_SHEET_COMUNI
    comuni.append(["Comune", config.WORKBOOK_HEADERS["mezzo"]])
    for row in (["Torino", config.WORKBOOK_ALL_LABEL], ["Torino", "A piedi"], ["Torino", "In bici"], ["Roma", config.WORKBOOK_ALL_LABEL], ["Roma", "A piedi"]):
        comuni.append(row)
    for name, rows in ((config.WORKBOOK_SHEET_CAMMINI, 2), (config.WORKBOOK_SHEET_COPPIE, 6), (config.WORKBOOK_SHEET_NOTE, 3)):
        sheet = book.create_sheet(name)
        if name != config.WORKBOOK_SHEET_NOTE:  # il foglio Note e' solo testo, senza intestazione
            sheet.append(["a", "b", "c"])
        for index in range(rows):
            sheet.append([index, "x", "y"])
    target = data_dir / config.WORKBOOK_OUTPUT_DIR.relative_to(config.DATA_DIR) / config.WORKBOOK_FILE_TEMPLATE.format(data="2026-11-04")
    target.parent.mkdir(parents=True, exist_ok=True)
    book.save(target)


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # conftest sposta il registro in tmp: qui deve stare sotto data/, come in produzione
    monkeypatch.setattr(config, "REQUESTS_LOG", config.RAW_DIR / "requests_log.csv")
    root = tmp_path / "data"
    for build in (build_sources, build_tracks, build_calibration, build_dedup, build_labels_and_matches, build_pairs, build_workbook):
        build(root)
    return root


@pytest.fixture
def numbers(data_dir: Path) -> dict[str, str]:
    frame = report_numbers.collect(data_dir)
    return dict(zip(frame["id"], frame["valore"]))


def test_ids_unici_sezioni_e_colonne(data_dir: Path) -> None:
    frame = report_numbers.collect(data_dir)
    assert list(frame.columns) == list(config.REPORT_NUMBERS_FIELDS)
    assert frame["id"].is_unique
    assert frame["id"].str.fullmatch(config.REPORT_ID_PATTERN).all()
    assert (frame["valore"] != "").all() and (frame["descrizione"] != "").all() and (frame["fonte"] != "").all()
    # Le sezioni escono nell'ordine di config e sono tutte presenti
    order = list(dict.fromkeys(frame["sezione"]))
    assert order == list(config.REPORT_SEZIONI)
    dashes = (chr(0x2014), chr(0x2013))  # niente lineette lunghe nei testi
    assert not frame.apply(lambda row: any(dash in "".join(row) for dash in dashes), axis=1).any()


def test_fonti(numbers: dict[str, str]) -> None:
    assert numbers["cdi_cammini"] == "2"
    assert numbers["cdi_cammini_lineare"] == "1" and numbers["cdi_cammini_con_layer_webmapp"] == "1"
    assert numbers["cdi_lunghezza_dichiarata_km"] == "150"
    assert numbers["webmapp_tracce_piu_strati"] == "1"
    assert numbers["mitur_schede"] == "2" and numbers["mitur_schede_con_file"] == "1"
    assert numbers["mitur_file_totali"] == "3" and numbers["mitur_file_formato_gpx"] == "2"
    assert numbers["mitur_catalogo_ultima_data"] == "2025-01-10"
    assert numbers["osm_relazioni"] == "3" and numbers["osm_relazioni_super"] == "1"
    assert numbers["osm_relazioni_network_lwn"] == "1" and numbers["osm_relazioni_network_altro"] == "1"
    assert numbers["istat_comuni"] == "4" and numbers["istat_regioni"] == "3"


def test_richieste_e_date_di_scarico(numbers: dict[str, str]) -> None:
    assert numbers["richieste_totali"] == "4" and numbers["richieste_non_riuscite"] == "1"
    assert numbers["richieste_mitur"] == "2"
    assert numbers["scarico_mitur_prima_data"] == "2026-11-03" and numbers["scarico_mitur_ultima_data"] == "2026-11-04"
    assert numbers["scarico_mitur_mb"] == "2" and numbers["scarico_istat_mb"] == "2"


def test_tracciati(numbers: dict[str, str]) -> None:
    assert numbers["tratte_cdi"] == "3" and numbers["tratte_cdi_tappa"] == "2" and numbers["tratte_cdi_bici"] == "0"
    assert numbers["tratte_cdi_lunghezza_km"] == "3" and numbers["tratte_mitur_bici"] == "1"
    assert numbers["spaziatura_cdi_tappa_mediana"] == "15" and numbers["spaziatura_osm_altro_p90"] == "19"
    assert numbers["cammini_osm_stato_parziale"] == "1" and numbers["limbo_cdi_duplicato_interno"] == "1"


def test_calibrazione(numbers: dict[str, str]) -> None:
    assert numbers["calib_a_recall_cammino_d100"] == "97.87"
    assert numbers["calib_b_o50_recall_d100"] == "96.77" and numbers["calib_b_recall_peggiore_d100"] == "50"
    assert numbers["calib_d_aumento_pct_d100"] == "5.35"
    assert numbers["calib_c_gruppi_tutti"] == "0.5" and numbers["calib_c_coppie"] == "3"
    assert numbers["calib_c_coppie_nel_rapporto"] == "2" and numbers["calib_c_coppie_mitur_densa"] == "2"
    assert numbers["calib_d_sfiorate_100m"] == "83" and numbers["calib_d_sfiorate_100m_quota"] == "1.38"
    assert numbers["calib_a_nomi_quota_ok"] == "65.85" and numbers["calib_a_lontane_motivo_ramo_senza_traccia"] == "2"
    assert numbers["calib_a_lontane_cammini"] == "2" and numbers["calib_b_spaziatura_originale_m"] == "6.87"
    assert numbers["calib_d_scelta_m"] == "100" and numbers["calib_tipi_esclusi"] == "bici"


def test_deduplicazione(numbers: dict[str, str]) -> None:
    assert numbers["dedup_cammini_catalogo"] == "6" and numbers["dedup_cammini_distinti"] == "4"
    assert numbers["dedup_gruppi_piu_membri"] == "2" and numbers["dedup_catene"] == "1"
    assert numbers["dedup_confronti"] == "5" and numbers["dedup_confronti_duplicato"] == "2"
    assert numbers["dedup_coppie_candidate"] == "5" and numbers["dedup_nome_punteggio_successivo"] == "87.5"
    assert numbers["dedup_nome_distacco"] == "12.5" and numbers["dedup_coppie_nome_sopra_soglia"] == "3"
    assert numbers["dedup_duplicati_sotto_quota_prima_prova"] == "1"
    assert numbers["dedup_quasi_versione"] == "1" and numbers["dedup_quasi_sfasamento"] == "0"
    assert numbers["dedup_confronto_comuni_quasi_duplicato_jaccard_min"] == "0.5"
    assert numbers["dedup_distinti_fonti_cdi_mitur"] == "2" and numbers["dedup_param_quota_duplicato"] == "70"


def test_osm(numbers: dict[str, str]) -> None:
    assert numbers["osm_esito_tutti_abbinato"] == "2" and numbers["osm_esito_tutti_parziale"] == "1"
    assert numbers["osm_abbinati_tutti_con_tracce"] == "1" and numbers["osm_abbinati_tutti_senza_tracce"] == "1"
    assert numbers["osm_abbinati_tutti_pellegrinaggio_si"] == "1" and numbers["osm_abbinati_relazioni"] == "3"
    assert numbers["osm_tag_pilgrimage_yes"] == "1" and numbers["osm_tag_pilgrimage_altro"] == "1"
    assert numbers["osm_etichette_pellegrinaggio"] == "2" and numbers["osm_etichette_pellegrinaggio_nominato"] == "1"
    assert numbers["osm_etichette_non_pellegrinaggio"] == "1"
    assert numbers["osm_cammini_solo_osm"] == "1" and numbers["osm_coppie_fonte_osm"] == "1"


def test_etichette(numbers: dict[str, str]) -> None:
    assert numbers["etichette_distinti_religioso_si"] == "2" and numbers["etichette_distinti_religioso_si_quota"] == "50"
    assert numbers["etichette_si_da_catalogo"] == "1" and numbers["etichette_si_da_tema_mappa"] == "1"
    assert numbers["etichette_si_solo_nome"] == "1" and numbers["etichette_si_solo_catalogo"] == "0"
    assert numbers["etichette_si_con_3_indicazioni"] == "1" and numbers["etichette_si_da_tema_spirituale"] == "1"
    assert numbers["etichette_distinti_mezzo_entrambi"] == "1" and numbers["etichette_credenziale_si"] == "1"
    assert numbers["etichette_tipologia_vuoto"] == "2" and numbers["etichette_coppie_mezzo_bici_n"] == "1"
    assert numbers["etichette_coppie_religioso_si"] == "5" and numbers["etichette_comuni_con_cammino_religioso"] == "3"
    assert numbers["etichette_tratte_mitur_bici"] == "1"


def test_comuni(numbers: dict[str, str]) -> None:
    assert numbers["comuni_coppie"] == "6" and numbers["comuni_attraversati"] == "4"
    assert numbers["comuni_attraversati_quota"] == "100"
    assert numbers["comuni_coppie_dentro"] == "5" and numbers["comuni_coppie_solo_fascia"] == "1"
    assert numbers["regione_piemonte_comuni"] == "2" and numbers["regione_piemonte_cammini"] == "2"
    assert numbers["regione_piemonte_coppie"] == "4" and numbers["regione_piemonte_quota_comuni"] == "100"
    assert numbers["regione_lazio_comuni"] == "1" and numbers["regione_lombardia_coppie"] == "1"
    assert numbers["top_comune_01_cammini"] == "2" and numbers["top_comune_03_cammini"] == "1"
    assert numbers["cammini_senza_geometria"] == "1" and numbers["cammini_senza_geometria_fonti_mitur"] == "1"
    assert numbers["cammini_n_comuni_max"] == "3" and numbers["cammini_con_comuni"] == "3"
    assert numbers["copertura_non_calcolabile"] == "2" and numbers["copertura_sotto_50"] == "1" and numbers["copertura_da_80"] == "1"
    assert numbers["lunghezza_rapporto_sopra_100"] == "1" and numbers["lunghezza_rapporto_sopra_150"] == "0"
    assert numbers["note_cammini_con_nota"] == "2" and numbers["note_possibile_duplicato"] == "1"
    assert numbers["note_nessuna_geometria"] == "1"


def test_top_comuni_descrizione_con_nome_e_provincia(data_dir: Path) -> None:
    frame = report_numbers.collect(data_dir).set_index("id")
    description = frame.loc["top_comune_01_cammini", "descrizione"]
    assert "Aglie (XX)" in description and "Provincia di Aglie" in description


def test_ordine_salti_e_estremi(numbers: dict[str, str]) -> None:
    assert numbers["ordine_salti_totali"] == "3"
    assert numbers["ordine_cammini_salto_oltre_30km"] == "2"
    assert numbers["ordine_cammini_salto_oltre_60km"] == "1" and numbers["ordine_passaggi_oltre_60km"] == "1"
    assert float(numbers["ordine_salto_massimo_km"]) > 500
    assert numbers["ordine_estremi_cammini"] == "1" and numbers["ordine_estremi_entrambi_concordi"] == "1"
    assert numbers["ordine_estremi_arrivo_concorde_quota"] == "100"
    assert numbers["ordine_solo_osm_totale"] == "1" and numbers["ordine_solo_osm_senza_partenza"] == "1"


def test_cartella(numbers: dict[str, str]) -> None:
    assert numbers["cartella_data_elaborazione"] == "2026-11-04" and numbers["cartella_fogli"] == "4"
    assert numbers["cartella_righe_comuni"] == "5" and numbers["cartella_comuni_righe_totale"] == "2"
    assert numbers["cartella_comuni_righe_dettaglio"] == "3" and numbers["cartella_righe_coppie"] == "6"
    assert numbers["cartella_righe_cammini"] == "2" and numbers["cartella_righe_note"] == "3"


def test_run_scrive_il_csv(data_dir: Path) -> None:
    report_numbers.run(data_dir)
    written = pd.read_csv(data_dir / config.REPORT_NUMBERS_FILE.relative_to(config.DATA_DIR), dtype=str, keep_default_na=False, encoding="utf-8")
    assert list(written.columns) == list(config.REPORT_NUMBERS_FIELDS)
    assert written.set_index("id").loc["dedup_cammini_distinti", "valore"] == "4"


def test_file_mancante_segnala_il_passo_da_rilanciare(data_dir: Path) -> None:
    (data_dir / config.DEDUP_DIR.relative_to(config.DATA_DIR) / config.DEDUP_FILES["membri"]).unlink()
    with pytest.raises(ReportNumbersError, match="rilanciare"):
        report_numbers.collect(data_dir)


def test_evidenza_non_riconosciuta_e_un_errore() -> None:
    assert report_numbers.evidence_kinds(f"{config.EVIDENZA_CATALOGO_RELIGIOSO}|il nome contiene le parole «san», «santa»") == {"catalogo", "nome"}
    with pytest.raises(ReportNumbersError):
        report_numbers.evidence_kinds("una fonte nuova")


def test_id_duplicato_e_valori_non_validi() -> None:
    section = report_numbers.Report(Path(".")).section("fonti")
    section.add("uno", "x", 1, "n", "f")
    with pytest.raises(ReportNumbersError, match="duplicato"):
        section.add("uno", "x", 2, "n", "f")
    with pytest.raises(ReportNumbersError, match="id non valido"):
        section.add("Due", "x", 2, "n", "f")
    with pytest.raises(ReportNumbersError):
        section.add("tre", "x", float("nan"), "n", "f")
    assert report_numbers.format_value(97.8700) == "97.87" and report_numbers.format_value(100.0) == "100"


def test_concordanza_degli_estremi() -> None:
    frame = pd.DataFrame({
        "start_code": ["a", "a", "z", None, "x"], "end_code": ["z", "y", "a", "z", None],
        "first_code": ["a", "a", "a", "a", "x"], "last_code": ["z", "q", "z", "z", "q"],
    })
    counts = report_numbers.endpoint_counts(frame)
    assert counts["cammini"] == 5 and counts["entrambi_risolti"] == 3
    assert counts["entrambi_concordi"] == 1 and counts["invertiti"] == 1
    assert counts["partenza_concorde"] == 3 and counts["arrivo_concorde"] == 2


def test_salti_consecutivi_solo_dentro_lo_stesso_cammino() -> None:
    centroids = pd.DataFrame({"x": [0.0, 100_000.0, 0.0], "y": [0.0, 0.0, 0.0]}, index=["a", "b", "c"])
    pairs = pd.DataFrame({
        config.PAIRS_UNICO_COL: ["u1", "u1", "u2", "u2"], COMUNE: ["a", "b", "c", "a"], "ordine": ["1", "2", "1", "2"],
    })
    jumps = report_numbers.consecutive_jumps(pairs, centroids)
    assert list(jumps["unico"]) == ["u1", "u2"] and list(jumps["km"]) == [100.0, 0.0]


def test_comando_report_numbers_nel_cli_e_in_all(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(report_numbers, "run", lambda: calls.append("report-numbers"))
    assert cli.main(["report-numbers"]) == 0 and calls == ["report-numbers"]
    assert cli.ALL_STEPS[-2:] == ("workbook", "report-numbers")
