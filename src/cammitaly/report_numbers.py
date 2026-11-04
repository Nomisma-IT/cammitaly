"""Numeri del rapporto metodologico: ogni numero citato dal rapporto e' calcolato qui dai dati gia' prodotti dalla catena.

Legge solo le uscite esistenti sotto data/ (mai rilancia un passo) e scrive data/output/numeri_rapporto.csv, una riga per
numero: id stabile, sezione, descrizione, valore, unita', fonte (file e filtro da cui viene). Si rigenera a ogni edizione.
"""
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from openpyxl import load_workbook

from cammitaly import calibration, config, crossing, pairs
from cammitaly.geometry import common

ID_PATTERN = re.compile(config.REPORT_ID_PATTERN)
UNICO = config.PAIRS_UNICO_COL
COMUNE = config.COMUNE_ID_COL
FONTE_LABELS = config.WORKBOOK_VALUE_LABELS["fonti"]
SI, NO = config.SI, config.NO
# Prefissi del testo delle evidenze, ricavati dai modelli di config
NAME_EVIDENCE_PREFIXES = tuple(template.split("{")[0] for template in (config.EVIDENZA_NOME_PAROLA, config.EVIDENZA_NOME_PAROLE))
NOTA_DUPLICATE_PREFIX = config.PAIRS_NOTA_LIMBO_GEOMETRIA.split("{", 1)[0]
PILGRIMAGE_NEGATIVE_PREFIX = config.EVIDENZA_PILGRIMAGE_NEGATIVO.split("(")[0].rstrip()
B_COLUMN_PATTERN = re.compile(r"^B_(?P<method>[a-z]\d+)_(?P<measure>recall_marginale|recall|extra_tot)$")


class ReportNumbersError(ValueError):
    """Dato di ingresso mancante o non riconosciuto: il file dei numeri non viene scritto."""


# --- formato dei valori e delle fonti ---

def slug(text: object) -> str:
    return common.normalize_name(str(text)).replace(" ", "_") or config.REPORT_EMPTY_SLUG


def source(path: Path, *filters: str) -> str:
    """File (da data/) e filtri da cui viene un numero."""
    shown = config.REPORT_DATA_PREFIX + path.relative_to(config.DATA_DIR).as_posix()
    return config.REPORT_SOURCE_NOTE_SEPARATOR.join((shown, *filters))


def config_source(name: str) -> str:
    return f"src/cammitaly/config.py{config.REPORT_SOURCE_NOTE_SEPARATOR}{name}"


def percent(part: float, whole: float) -> float:
    if not whole:
        raise ReportNumbersError("quota su un totale nullo")
    return 100 * part / whole


def format_value(value: object) -> str:
    if isinstance(value, (bool, np.bool_)):
        raise ReportNumbersError("valore booleano: usare un numero o un testo")
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)):
        if not np.isfinite(value):
            raise ReportNumbersError("valore non finito")
        decimals = config.REPORT_VALUE_DECIMALS
        return f"{round(float(value), decimals):.{decimals}f}".rstrip("0").rstrip(".")
    return str(value)


# --- raccolta ---

@dataclass
class Report:
    """Cartella data/ da cui si legge e righe raccolte finora (id -> riga)."""

    data_dir: Path
    rows: dict[str, dict[str, str]] = field(default_factory=dict)

    def section(self, name: str) -> "Section":
        if name not in config.REPORT_SEZIONI:
            raise ReportNumbersError(f"sezione sconosciuta: {name}")
        return Section(self, name)

    def locate(self, path: Path) -> Path:
        """Lo stesso file di config sotto la cartella data/ di questo rapporto."""
        return self.data_dir / path.relative_to(config.DATA_DIR)

    def _read(self, path: Path, **options: object) -> pd.DataFrame:
        located = self.locate(path)
        if not located.is_file():
            raise ReportNumbersError(f"manca il file {located}: rilanciare il passo che lo produce")
        return pd.read_csv(located, encoding="utf-8", **options)  # type: ignore[call-overload]

    def table(self, path: Path) -> pd.DataFrame:
        """Tutte stringhe, celle vuote = ''."""
        return self._read(path, dtype=str, keep_default_na=False)

    def numeric(self, path: Path) -> pd.DataFrame:
        return self._read(path)

    def read_shapefile(self, path: Path, **options: object) -> gpd.GeoDataFrame:
        located = self.locate(path)
        if not located.is_file():
            raise ReportNumbersError(f"manca il file {located}: rilanciare il passo che lo produce")
        return gpd.read_file(located, engine="pyogrio", encoding=config.ISTAT_ENCODING, **options)

    @cached_property
    def istat_attributes(self) -> pd.DataFrame:
        """Codice, nome e regione dei comuni ISTAT."""
        columns = [COMUNE, config.COMUNE_NAME_COL, config.COMUNE_REGION_COL]
        return pd.DataFrame(self.read_shapefile(config.ISTAT_COMUNI_SHP, ignore_geometry=True, columns=columns))

    @cached_property
    def istat_centroids(self) -> pd.DataFrame:
        """Coordinate metriche (x, y) del centroide di ogni comune, indicizzate per codice."""
        comuni = self.read_shapefile(config.ISTAT_COMUNI_SHP, columns=[COMUNE]).to_crs(config.CRS_WORK)
        centroids = comuni.geometry.centroid
        return pd.DataFrame({"x": centroids.x.to_numpy(), "y": centroids.y.to_numpy()}, index=comuni[COMUNE].to_numpy())

    @cached_property
    def region_codes(self) -> dict[str, int]:
        """Slug della regione -> codice ISTAT."""
        regioni = self.read_shapefile(config.ISTAT_REGIONI_SHP, ignore_geometry=True)
        return {crossing.region_slug(name): int(code) for name, code in zip(regioni[config.REGION_NAME_COL], regioni[config.REGION_CODE_COL])}


@dataclass(frozen=True)
class Section:
    report: Report
    name: str

    def add(self, id: str, descrizione: str, valore: object, unita: str, fonte: str) -> None:
        if not ID_PATTERN.fullmatch(id):
            raise ReportNumbersError(f"id non valido: {id!r}")
        if id in self.report.rows:
            raise ReportNumbersError(f"id duplicato: {id}")
        self.report.rows[id] = {
            "id": id, "sezione": self.name, "descrizione": descrizione, "valore": format_value(valore),
            "unita": unita, "fonte": fonte,
        }

    def share(self, id: str, descrizione: str, part: float, whole: float, fonte: str) -> None:
        self.add(id, descrizione, round(percent(part, whole), config.REPORT_PERCENT_DECIMALS), "%", fonte)

    def count(self, id: str, descrizione: str, mask: pd.Series, unita: str, fonte: str) -> None:
        self.add(id, descrizione, int(mask.sum()), unita, fonte)

    def distribution(self, id: str, descrizione: str, values: pd.Series, unita: str, fonte: str) -> None:
        """Minimo, mediana, 90o percentile e massimo dei valori."""
        self.add(f"{id}_min", f"{descrizione}: minimo", values.min(), unita, fonte)
        for name, quantile in config.REPORT_QUANTILES.items():
            self.add(f"{id}_{name}", f"{descrizione}: {name}", values.quantile(quantile), unita, fonte)
        self.add(f"{id}_max", f"{descrizione}: massimo", values.max(), unita, fonte)


def value_counts(series: pd.Series) -> list[tuple[str, int]]:
    """Valori e frequenze, dal piu' frequente; a pari frequenza in ordine alfabetico."""
    return sorted(((str(value), int(count)) for value, count in series.value_counts().items()), key=lambda item: (-item[1], item[0]))


def to_numbers(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


# --- fonti ---

def _cdi(s: Section) -> None:
    path = config.CDI_CAMMINI_CSV
    cammini, origin = s.report.table(path), source(path)
    s.add("cdi_cammini", "Cammini pubblicati da camminiditalia.org", len(cammini), "cammini", origin)
    for tipologia in config.REPORT_CDI_TIPOLOGIE:
        s.count(f"cdi_cammini_{tipologia}", f"Cammini di camminiditalia.org di tipologia {tipologia}",
                cammini["tipologia"] == tipologia, "cammini", source(path, f"tipologia = {tipologia}"))
    s.count("cdi_cammini_senza_tipologia", "Cammini di camminiditalia.org senza tipologia pubblicata",
            ~cammini["tipologia"].isin(config.REPORT_CDI_TIPOLOGIE), "cammini", origin)
    has_layer = cammini["webmapp_layer"] != ""
    s.count("cdi_cammini_con_layer_webmapp", "Cammini di camminiditalia.org con uno strato sulla mappa Webmapp", has_layer, "cammini", source(path, "webmapp_layer non vuoto"))
    s.count("cdi_cammini_senza_layer_webmapp", "Cammini di camminiditalia.org senza strato sulla mappa Webmapp", ~has_layer, "cammini", source(path, "webmapp_layer vuoto"))
    s.add("cdi_lunghezza_dichiarata_km", "Somma delle lunghezze dichiarate dai cammini di camminiditalia.org", to_numbers(cammini["lunghezza_km"]).sum(), "km", source(path, "somma di lunghezza_km"))
    s.add("cdi_tappe", "Tappe pubblicate dai cammini di camminiditalia.org", len(s.report.table(config.CDI_TAPPE_CSV)), "tappe", source(config.CDI_TAPPE_CSV))


def _webmapp(s: Section) -> None:
    path = config.WEBMAPP_TRACKS_CSV
    tracks, origin = s.report.table(path), source(path)
    s.add("webmapp_tracce", "Tracce elencate dalla mappa Cammini d'Italia (Webmapp)", len(tracks), "tracce", origin)
    s.count("webmapp_tracce_con_geometria", "Tracce Webmapp con la geometria scaricata", tracks["has_geometry"] == "True", "tracce", source(path, "has_geometry = True"))
    s.count("webmapp_tracce_piu_strati", "Tracce Webmapp che appartengono a piu' di uno strato (cammino)", tracks["layers"].str.contains(config.LAYER_SEPARATOR, regex=False), "tracce", source(path, "layers con piu' valori"))


def _mitur_formats(cards: pd.DataFrame) -> Counter[str]:
    formats: Counter[str] = Counter()
    for text in cards["tipi_file"]:
        for item in filter(None, text.split(config.LIST_SEPARATOR)):
            name, count = item.rsplit(config.REPORT_MITUR_FORMAT_SEPARATOR, 1)
            formats[name] += int(count)
    return formats


def _mitur(s: Section) -> None:
    path = config.MITUR_CAMMINI_CSV
    cards, origin = s.report.table(path), source(path)
    n_files = to_numbers(cards["n_file"]).fillna(0).astype(int)
    s.add("mitur_schede", "Schede del Catalogo dei cammini religiosi del Ministero del Turismo", len(cards), "schede", origin)
    s.count("mitur_schede_con_file", "Schede del Ministero con almeno un file nello zip del percorso", n_files > 0, "schede", source(path, "n_file > 0"))
    s.count("mitur_schede_senza_file", "Schede del Ministero senza file di percorso", n_files == 0, "schede", source(path, "n_file = 0"))
    s.count("mitur_schede_con_link_zip", "Schede del Ministero con un link di download del percorso", cards["url_percorso"] != "", "schede", source(path, "url_percorso non vuoto"))
    s.add("mitur_zip_distinti", "Zip distinti indicati dalle schede del Ministero", cards.loc[cards["url_percorso"] != "", "url_percorso"].nunique(), "zip", source(path, "url_percorso distinti"))
    s.count("mitur_schede_con_nota", "Schede del Ministero con una nota (percorso su host esterno, zip condiviso)", cards["nota"] != "", "schede", source(path, "nota non vuota"))
    s.add("mitur_file_totali", "File di percorso nei zip del Ministero", n_files.sum(), "file", source(path, "somma di n_file"))
    formats = _mitur_formats(cards)
    s.add("mitur_file_formati", "Formati distinti dei file nei zip del Ministero", len(formats), "formati", source(path, "tipi_file"))
    for name, count in sorted(formats.items(), key=lambda item: (-item[1], item[0])):
        s.add(f"mitur_file_formato_{slug(name)}", f"File di formato {name} nei zip del Ministero", count, "file", source(path, "tipi_file"))
    dates = cards.loc[cards["in_catalogo_dal"] != "", "in_catalogo_dal"]
    s.add("mitur_catalogo_prima_data", "Prima data di ingresso nel Catalogo del Ministero", dates.min(), "data", source(path, "min di in_catalogo_dal"))
    s.add("mitur_catalogo_ultima_data", "Ultima data di ingresso nel Catalogo del Ministero", dates.max(), "data", source(path, "max di in_catalogo_dal"))


def _osm_catalogue(s: Section) -> None:
    path = Path(config.CAMMINI_CSV_TEMPLATE.format(fonte=config.OSM_FONTE))
    relations, origin = s.report.table(path), source(path)
    s.add("osm_relazioni", "Relazioni di percorso di OpenStreetMap lette dall'estratto", len(relations), "relazioni", origin)
    for stato in (config.STATO_OK, config.STATO_PARZIALE, config.STATO_ASSENTE):
        s.count(f"osm_relazioni_stato_{stato}", f"Relazioni OpenStreetMap con geometria in stato {stato}", relations["stato_geometria"] == stato, "relazioni", source(path, f"stato_geometria = {stato}"))
    s.count("osm_relazioni_super", "Relazioni OpenStreetMap che sono super-relazioni (contengono altre relazioni)", relations["is_super"] == "True", "relazioni", source(path, "is_super = True"))
    for value, count in value_counts(relations["route"]):
        shown = value or "non indicato"
        s.add(f"osm_relazioni_route_{slug(value)}", f"Relazioni OpenStreetMap con route = {shown}", count, "relazioni", source(path, f"route = {shown}"))
    network = relations["network"]
    for code in config.REPORT_OSM_NETWORKS:
        s.count(f"osm_relazioni_network_{code}", f"Relazioni OpenStreetMap con network = {code}", network == code, "relazioni", source(path, f"network = {code}"))
    s.count("osm_relazioni_network_non_indicato", "Relazioni OpenStreetMap senza network", network == "", "relazioni", source(path, "network vuoto"))
    s.count("osm_relazioni_network_altro", "Relazioni OpenStreetMap con un network non standard (testo libero, maiuscole, valori composti)", ~network.isin(("", *config.REPORT_OSM_NETWORKS)), "relazioni", source(path, "network altro"))


def _istat(s: Section) -> None:
    comuni, origin = s.report.istat_attributes, source(config.ISTAT_COMUNI_SHP)
    s.add("istat_comuni", "Comuni nei confini ISTAT", len(comuni), "comuni", origin)
    s.add("istat_regioni", "Regioni nei confini ISTAT", comuni[config.COMUNE_REGION_COL].nunique(), "regioni", origin)
    s.add("istat_edizione", "Anno dell'edizione dei confini ISTAT (al 1 gennaio)", int(config.ISTAT_EDITION_YEAR), "anno", config_source("ISTAT_EDITION_YEAR"))


def _requests(s: Section) -> None:
    path = config.REQUESTS_LOG
    log, origin = s.report.table(path), source(path)
    ok = log[log["status"] == config.WORKBOOK_LOG_OK_STATUS]
    s.add("richieste_totali", "Richieste HTTP registrate in tutto il progetto", len(log), "richieste", origin)
    s.add("richieste_non_riuscite", "Richieste HTTP registrate con esito diverso da 200", len(log) - len(ok), "richieste", source(path, f"status diverso da {config.WORKBOOK_LOG_OK_STATUS}"))
    for host, count in value_counts(ok["host_key"]):
        s.add(f"richieste_host_{slug(host)}", f"Richieste riuscite all'host {host}", count, "richieste", source(path, f"host_key = {host}", "status = 200"))
    for key, hosts in config.WORKBOOK_SOURCE_HOSTS.items():
        name, rows = key.removeprefix("data_"), ok[ok["host_key"].isin(hosts)]
        filters = (f"host_key in {', '.join(hosts)}", "status = 200")
        s.add(f"richieste_{name}", f"Richieste riuscite per la fonte {name}", len(rows), "richieste", source(path, *filters))
        if rows.empty:
            continue
        days = rows["utc_iso"].str[: config.REPORT_LOG_DATE_CHARS]
        s.add(f"scarico_{name}_prima_data", f"Prima data di scarico della fonte {name}", days.min(), "data", source(path, *filters))
        s.add(f"scarico_{name}_ultima_data", f"Ultima data di scarico della fonte {name}", days.max(), "data", source(path, *filters))
        s.add(f"scarico_{name}_mb", f"Dati scaricati dalla fonte {name}", to_numbers(rows["bytes"]).sum() / config.REPORT_BYTES_PER_MB, "MB", source(path, *filters, "somma di bytes"))


def fonti(report: Report) -> None:
    s = report.section("fonti")
    for part in (_cdi, _webmapp, _mitur, _osm_catalogue, _istat, _requests):
        part(s)


# --- tracciati ---

def _tratte(s: Section, fonte: str) -> None:
    path = Path(config.TRATTE_GPKG_TEMPLATE.format(fonte=fonte))
    tratte = pd.DataFrame(gpd.read_file(
        s.report.locate(path), engine="pyogrio", layer=config.TRATTE_LAYER, ignore_geometry=True,
        columns=["tipo", "lunghezza_m", "spaziatura_media_m"],
    ))
    tipi = (config.TIPO_TAPPA, config.TIPO_VARIANTE, config.TIPO_BICI, config.TIPO_ALTRO)
    if not set(tratte["tipo"]) <= set(tipi):
        raise ReportNumbersError(f"tipi di tratta sconosciuti in {path.name}: {sorted(set(tratte['tipo']) - set(tipi))}")
    label, origin = FONTE_LABELS[fonte], source(path)
    s.add(f"tratte_{fonte}", f"Tratte di {label}", len(tratte), "tratte", origin)
    s.add(f"tratte_{fonte}_lunghezza_km", f"Somma delle lunghezze delle tratte di {label}", tratte["lunghezza_m"].sum() / 1000, "km", source(path, "somma di lunghezza_m"))
    for tipo in tipi:
        rows = tratte[tratte["tipo"] == tipo]
        filters = source(path, f"tipo = {tipo}")
        s.add(f"tratte_{fonte}_{tipo}", f"Tratte di tipo {tipo} di {label}", len(rows), "tratte", filters)
        spacing = rows["spaziatura_media_m"].dropna()
        for name, quantile in config.REPORT_QUANTILES.items():
            if len(spacing):
                s.add(f"spaziatura_{fonte}_{tipo}_{name}", f"Spaziatura media dei vertici delle tratte di tipo {tipo} di {label}: {name}", spacing.quantile(quantile), "m", source(path, f"tipo = {tipo}", "spaziatura_media_m"))


def _cammini_and_limbo(s: Section, fonte: str) -> None:
    label = FONTE_LABELS[fonte]
    path = Path(config.CAMMINI_CSV_TEMPLATE.format(fonte=fonte))
    cammini = s.report.table(path)
    s.add(f"cammini_{fonte}", f"Cammini di {label}", len(cammini), "cammini", source(path))
    for stato in (config.STATO_OK, config.STATO_PARZIALE, config.STATO_ASSENTE):
        s.count(f"cammini_{fonte}_stato_{stato}", f"Cammini di {label} con geometria in stato {stato}", cammini["stato_geometria"] == stato, "cammini", source(path, f"stato_geometria = {stato}"))
    limbo_path = Path(config.LIMBO_CSV_TEMPLATE.format(fonte=fonte))
    limbo = s.report.table(limbo_path)
    s.add(f"limbo_{fonte}", f"Oggetti di {label} messi da parte nel limbo (scartati o da verificare)", len(limbo), "oggetti", source(limbo_path))
    for motivo, count in value_counts(limbo["motivo"]):
        s.add(f"limbo_{fonte}_{slug(motivo)}", f"Oggetti di {label} nel limbo per il motivo: {motivo}", count, "oggetti", source(limbo_path, f"motivo = {motivo}"))


def tracciati(report: Report) -> None:
    s = report.section("tracciati")
    for fonte in config.FONTI_GEOMETRIA:
        _tratte(s, fonte)
        _cammini_and_limbo(s, fonte)


# --- calibrazione ---

def method_text(method: str) -> str:
    return config.REPORT_CALIBRATION_B_METHOD_TEXT[method[0]].format(n=method[1:])


def _calibration_by_distance(s: Section, summary: pd.DataFrame, origin: Path) -> None:
    for record in summary.to_dict("records"):
        d = int(record["d_m"])
        for column, stem, text, unit, factor in config.REPORT_CALIBRATION_COLUMNS:
            if column in record and not pd.isna(record[column]):
                s.add(f"calib_{stem}_d{d}", text.format(d=d), record[column] * factor, unit, source(origin, f"d_m = {d}", column))
        _calibration_b(s, record, d, origin)


def _calibration_b(s: Section, record: Mapping[str, float], d: int, origin: Path) -> None:
    by_measure: dict[str, list[float]] = {}
    for column, value in record.items():
        found = B_COLUMN_PATTERN.match(column)
        if not found or pd.isna(value):
            continue
        method, measure = found["method"], found["measure"]
        for name, stem, _, text, unit, factor in config.REPORT_CALIBRATION_B_MEASURES:
            if name == measure:
                s.add(f"calib_b_{method}_{stem}_d{d}", f"Calibrazione B ({method_text(method)}): {text} a d = {d} m", value * factor, unit, source(origin, f"d_m = {d}", column))
                by_measure.setdefault(measure, []).append(value)
    for name, stem, worst, text, unit, factor in config.REPORT_CALIBRATION_B_MEASURES:
        if name in by_measure:
            value = (min if worst == "min" else max)(by_measure[name])
            s.add(f"calib_b_{stem}_peggiore_d{d}", f"Calibrazione B, caso peggiore tra i metodi: {text} a d = {d} m", value * factor, unit, source(origin, f"d_m = {d}", f"{worst} delle colonne B_*_{name}"))


def _calibration_fixed(s: Section, first: Mapping[str, float], origin: Path) -> None:
    for column, id, text, unit, factor in config.REPORT_CALIBRATION_FIXED:
        s.add(id, text, first[column] * factor, unit, source(origin, column))
    s.add("calib_d_scelta_m", "Distanza d scelta dal committente per associare un cammino a un comune", config.CROSSING_DISTANCE_M, "m", config_source("CROSSING_DISTANCE_M"))
    s.add("calib_tipi_usati", "Tipi di tratta misurati dalla calibrazione", config.LIST_SEPARATOR.join(config.CALIBRATION_TIPI), "testo", config_source("CALIBRATION_TIPI"))
    excluded = [tipo for tipo in config.CROSSING_TIPI if tipo not in config.CALIBRATION_TIPI]
    s.add("calib_tipi_esclusi", "Tipi di tratta usati dall'incrocio finale ma esclusi dalla calibrazione (le tracce per bici)", config.LIST_SEPARATOR.join(excluded), "testo", config_source("CALIBRATION_TIPI, CROSSING_TIPI"))


def _calibration_a(s: Section, report: Report) -> None:
    directory = config.CALIBRATION_DIR
    path = directory / config.CALIBRATION_FILES_A["distribuzione"]
    for row in report.numeric(path).to_dict("records"):
        ambito = row["ambito"]
        for column, stem, text, unit, factor in config.REPORT_CALIBRATION_DISTRIBUTION:
            s.add(f"calib_a_{ambito}_{stem}", f"Calibrazione A: {text.format(ambito=ambito)}", row[column] * factor, unit, source(path, f"ambito = {ambito}", column))
    path = directory / config.CALIBRATION_FILES_A["match"]
    match = report.numeric(path)
    row = match[(match["origine"] == config.REPORT_CALIBRATION_MATCH_ROW[0]) & (match["ruolo"] == config.REPORT_CALIBRATION_MATCH_ROW[1])].iloc[0]
    origin = source(path, "origine = tutte", "ruolo = tutti")
    for column in config.REPORT_CALIBRATION_MATCH_COLUMNS:
        s.add(f"calib_a_nomi_{slug(column)}", f"Calibrazione A: nomi di luogo con esito «{column}» nel confronto con i comuni ISTAT", row[column], "nomi", origin)
    s.add("calib_a_nomi_quota_ok", "Calibrazione A: quota di nomi di luogo che indicano un solo comune ISTAT", row["quota_ok"] * 100, "%", origin)
    path = directory / config.CALIBRATION_FILES_A["lontane"]
    far = report.table(path)
    far_text = f"oltre {config.CALIBRATION_FAR_M} m dalla linea del cammino"
    s.add("calib_a_lontane_coppie", f"Calibrazione A: coppie (cammino, luogo) {far_text}", len(far), "coppie", source(path))
    s.add("calib_a_lontane_cammini", f"Calibrazione A: cammini con almeno un luogo {far_text}", far["cammino_id"].nunique(), "cammini", source(path, "cammino_id distinti"))
    for motivo, count in value_counts(far["motivo"]):
        s.add(f"calib_a_lontane_motivo_{slug(motivo)}", f"Calibrazione A: coppie {far_text} con motivo «{motivo or 'non ricavato dai dati'}»", count, "coppie", source(path, f"motivo = {motivo}"))


def _calibration_bcd(s: Section, report: Report) -> None:
    directory = config.CALIBRATION_DIR
    path = directory / config.CALIBRATION_FILES_B["totale"]
    totals = report.numeric(path)
    s.add("calib_b_spaziatura_originale_m", "Calibrazione B: spaziatura media dei vertici delle tracce dense prima del degrado", totals["spaziatura_originale_m"].iloc[0], "m", source(path, "spaziatura_originale_m"))
    path = directory / config.CALIBRATION_FILES_C["coppie"]
    coppie = report.table(path)
    s.add("calib_c_coppie", "Calibrazione C: coppie cdi-MiTur dello stesso cammino trovate (nome simile e tracciati sovrapposti)", len(coppie), "coppie", source(path))
    s.count("calib_c_coppie_nel_rapporto", "Calibrazione C: coppie con lunghezze simili (rapporto cdi/MiTur nei limiti)", coppie["nel_rapporto"] == "True", "coppie", source(path, "nel_rapporto = True"))
    s.count("calib_c_coppie_mitur_densa", "Calibrazione C: coppie con traccia MiTur densa", coppie["mitur_densa"] == "True", "coppie", source(path, "mitur_densa = True"))
    path = directory / config.CALIBRATION_FILES_D["grazing"]
    for row in report.numeric(path).to_dict("records"):
        limit, origin = int(row["soglia_m"]), source(path, f"soglia_m = {row['soglia_m']}")
        text = f"con meno di {limit} m dentro il comune (la linea lo sfiora soltanto)"
        s.add(f"calib_d_sfiorate_{limit}m", f"Calibrazione D: coppie cammino-comune {text}", row["coppie_sotto_soglia"], "coppie", origin)
        s.add(f"calib_d_sfiorate_{limit}m_quota", f"Calibrazione D: quota di coppie {text} sulle coppie a d = 0", row["quota"] * 100, "%", origin)


def calibrazione(report: Report) -> None:
    s = report.section("calibrazione")
    path = config.CALIBRATION_DIR / config.CALIBRATION_FILE_SINTESI
    summary = report.numeric(path)
    _calibration_by_distance(s, summary, path)
    _calibration_fixed(s, summary.iloc[0].to_dict(), path)
    _calibration_a(s, report)
    _calibration_bcd(s, report)


# --- deduplicazione ---

def adopted_sensitivity(sensitivity: pd.DataFrame) -> pd.Series:
    """Riga della griglia di sensibilita' con i parametri adottati."""
    mask = (
        np.isclose(sensitivity["soglia_nome"], config.DEDUP_NAME_MIN_SCORE) & np.isclose(sensitivity["soglia_quota"], config.DEDUP_OVERLAP_MIN)
        & np.isclose(sensitivity["soglia_distinto"], config.DEDUP_DISTINCT_BELOW) & (sensitivity["distanza_m"] == config.DEDUP_NEAR_M)
    )
    if mask.sum() != 1:
        raise ReportNumbersError("sensibilita.csv non ha una riga sola con i parametri adottati: rilanciare dedup")
    return sensitivity[mask].iloc[0]


def _dedup_comparisons(s: Section, report: Report) -> None:
    directory, files = config.DEDUP_DIR, config.DEDUP_FILES
    path = directory / files["sensibilita"]
    sensitivity = report.numeric(path)
    adopted, origin = adopted_sensitivity(sensitivity), source(path, "riga dei parametri adottati")
    counts = {name: int(adopted[f"n_{name}"]) for name in config.REPORT_DEDUP_CLASSES}
    s.add("dedup_confronti", "Confronti tra cammini del catalogo (cdi e MiTur) fatti dalla deduplicazione", sum(counts.values()), "coppie", origin)
    for name, count in counts.items():
        s.add(f"dedup_confronti_{name}", f"Confronti classificati «{name}» con i parametri adottati", count, "coppie", origin)
    for column, id, text in (
        ("n_nome_100", "dedup_nome_100", "Confronti con punteggio del nome 100"),
        ("n_nome_100_tracciato_diverso", "dedup_nome_100_tracciato_diverso", "Confronti con punteggio del nome 100 ma tracciato diverso (cammini distinti)"),
        ("n_nome_100_geometria_assente", "dedup_nome_100_geometria_assente", "Confronti con punteggio del nome 100 e una geometria assente (limbo)"),
    ):
        s.add(id, text, adopted[column], "coppie", source(path, "riga dei parametri adottati", column))
    s.add("dedup_sens_configurazioni", "Combinazioni di parametri provate nella griglia di sensibilita'", len(sensitivity), "combinazioni", source(path))
    for name in (config.CLASSE_DUPLICATO, config.CLASSE_LIMBO):
        column = f"n_{name}"
        s.add(f"dedup_sens_{name}_min", f"Confronti «{name}» nella combinazione della griglia che ne trova meno", sensitivity[column].min(), "coppie", source(path, f"min di {column}"))
        s.add(f"dedup_sens_{name}_max", f"Confronti «{name}» nella combinazione della griglia che ne trova di piu'", sensitivity[column].max(), "coppie", source(path, f"max di {column}"))


def _dedup_candidates(s: Section, report: Report) -> None:
    path = config.DEDUP_DIR / config.DEDUP_FILES["coppie"]
    coppie, origin = report.numeric(path), source(path)
    s.add("dedup_coppie_candidate", f"Coppie candidate tenute in coppie.csv (nome con punteggio almeno {config.DEDUP_CANDIDATE_NAME_MIN} o tracciati che si toccano)", len(coppie), "coppie", origin)
    for name in config.REPORT_DEDUP_CLASSES:
        s.count(f"dedup_coppie_candidate_{name}", f"Coppie candidate classificate «{name}»", coppie["classe"] == name, "coppie", source(path, f"classe = {name}"))
    scores = coppie["punteggio_nome"]
    top = scores.max()
    below = scores[scores < top].max()
    note = f"punteggio_nome; i confronti non in coppie.csv hanno punteggio sotto {config.DEDUP_CANDIDATE_NAME_MIN}"
    s.add("dedup_nome_punteggio_massimo", "Punteggio massimo del nome tra due cammini del catalogo", top, "punteggio", source(path, note))
    s.add("dedup_nome_punteggio_successivo", "Punteggio del nome piu' alto sotto il massimo", below, "punteggio", source(path, note))
    s.add("dedup_nome_distacco", "Distacco tra il punteggio massimo e il successivo", top - below, "punteggio", source(path, note))
    s.add("dedup_nome_soglia", "Soglia del punteggio del nome adottata", config.DEDUP_NAME_MIN_SCORE, "punteggio", config_source("DEDUP_NAME_MIN_SCORE"))
    s.count("dedup_coppie_nome_sopra_soglia", "Coppie candidate con punteggio del nome pari o sopra la soglia", scores >= config.DEDUP_NAME_MIN_SCORE, "coppie", source(path, "punteggio_nome >= soglia"))
    merged = coppie[coppie["classe"] == config.CLASSE_DUPLICATO]
    s.count("dedup_duplicati_sotto_quota_prima_prova", f"Duplicati con tracciato coincidente per meno del {round(100 * config.DEDUP_QUASI_OVERLAP_MAX)}% (lasciati distinti dalla prima prova al {round(100 * config.DEDUP_QUASI_OVERLAP_MAX)}%, fusi con la soglia adottata)", merged["quota_minore"] < config.DEDUP_QUASI_OVERLAP_MAX, "coppie", source(path, "classe = duplicato", f"quota_minore < {config.DEDUP_QUASI_OVERLAP_MAX}"))
    s.add("dedup_duplicati_quota_minima", "Quota minima del tracciato piu' corto entro la distanza dall'altro tra le coppie duplicate", percent(merged["quota_minore"].min(), 1), "%", source(path, "classe = duplicato", "min di quota_minore"))


def _dedup_quasi(s: Section, report: Report) -> None:
    path = config.DEDUP_DIR / config.DEDUP_FILES["quasi"]
    quasi = report.table(path)
    s.add("dedup_quasi_duplicati", "Quasi duplicati: coppie dal nome simile con tracciato coincidente per il 50-95%, diagnosticate tratto per tratto", len(quasi), "coppie", source(path))
    for diagnosi, stem in config.REPORT_DEDUP_DIAGNOSI_IDS.items():
        s.count(f"dedup_quasi_{stem}", f"Quasi duplicati con diagnosi: {diagnosi}", quasi["diagnosi"] == diagnosi, "coppie", source(path, f"diagnosi = {diagnosi}"))
    path = config.DEDUP_DIR / config.DEDUP_FILES["comuni"]
    compared = report.numeric(path)
    s.add("dedup_confronto_comuni_coppie", "Coppie duplicate e quasi duplicate confrontate sull'insieme dei comuni attraversati", len(compared), "coppie", source(path))
    for classe, rows in compared.groupby("classe"):
        stem, origin = slug(classe), source(path, f"classe = {classe}")
        s.add(f"dedup_confronto_comuni_{stem}", f"Coppie «{classe}» confrontate sull'insieme dei comuni attraversati", len(rows), "coppie", origin)
        s.add(f"dedup_confronto_comuni_{stem}_jaccard_min", f"Indice di Jaccard minimo tra i comuni delle coppie «{classe}»", rows["jaccard"].min(), "indice", origin)
        s.add(f"dedup_confronto_comuni_{stem}_jaccard_mediana", f"Indice di Jaccard mediano tra i comuni delle coppie «{classe}»", rows["jaccard"].median(), "indice", origin)


def _dedup_distinct(s: Section, report: Report) -> None:
    directory, files = config.DEDUP_DIR, config.DEDUP_FILES
    path = directory / files["membri"]
    members = report.table(path)
    s.add("dedup_cammini_catalogo", "Cammini del catalogo (camminiditalia.org e Ministero) prima della deduplicazione", len(members), "cammini", source(path))
    for fonte in config.DEDUP_FONTI:
        s.count(f"dedup_cammini_catalogo_{fonte}", f"Cammini del catalogo provenienti da {FONTE_LABELS[fonte]}", members["fonte"] == fonte, "cammini", source(path, f"fonte = {fonte}"))
    for ruolo in (config.RUOLO_PRINCIPALE, config.RUOLO_DUPLICATO, config.RUOLO_SEZIONE):
        s.count(f"dedup_membri_{ruolo}", f"Cammini del catalogo con ruolo «{ruolo}» nel proprio cammino distinto", members["ruolo"] == ruolo, "cammini", source(path, f"ruolo = {ruolo}"))
    path = directory / files["unici"]
    unique, origin = report.table(path), source(path)
    n_members = to_numbers(unique["n_membri"])
    s.add("dedup_cammini_distinti", "Cammini distinti dopo la deduplicazione", len(unique), "cammini", origin)
    s.count("dedup_gruppi_piu_membri", "Cammini distinti formati da piu' di un cammino del catalogo", n_members > 1, "cammini", source(path, "n_membri > 1"))
    for size in sorted(n_members.unique()):
        s.count(f"dedup_distinti_con_{int(size)}_membri", f"Cammini distinti formati da {int(size)} cammini del catalogo", n_members == size, "cammini", source(path, f"n_membri = {int(size)}"))
    s.count("dedup_catene", "Cammini distinti le cui tappe formano una catena (sezioni attaccate)", unique["catena"] == "True", "cammini", source(path, "catena = True"))
    s.count("dedup_distinti_con_alias", "Cammini distinti con altri nomi nelle fonti", unique["alias"] != "", "cammini", source(path, "alias non vuoto"))
    for stato in config.DEDUP_STATO_ORDER:
        s.count(f"dedup_distinti_stato_{stato}", f"Cammini distinti con geometria in stato {stato}", unique["stato"] == stato, "cammini", source(path, f"stato = {stato}"))
    for fonti, count in value_counts(unique["fonti"]):
        s.add(f"dedup_distinti_fonti_{slug(fonti)}", f"Cammini distinti presenti in: {fonti}", count, "cammini", source(path, f"fonti = {fonti}"))
    path = directory / files["limbo"]
    limbo = report.table(path)
    s.add("dedup_limbo", "Coppie in limbo, da verificare a mano", len(limbo), "coppie", source(path))
    for motivo, count in value_counts(limbo["motivo"]):
        s.add(f"dedup_limbo_{slug(motivo)}", f"Coppie in limbo per il motivo: {motivo}", count, "coppie", source(path, f"motivo = {motivo}"))


def _dedup_parameters(s: Section) -> None:
    rows = (
        ("soglia_nome", "Punteggio minimo del nome per dirlo simile (0-100)", config.DEDUP_NAME_MIN_SCORE, "punteggio", "DEDUP_NAME_MIN_SCORE"),
        ("quota_duplicato", "Quota minima del tracciato piu' corto entro la distanza dall'altro per essere lo stesso cammino", 100 * config.DEDUP_OVERLAP_MIN, "%", "DEDUP_OVERLAP_MIN"),
        ("quota_distinto", "Quota sotto cui due cammini con nome simile sono distinti", 100 * config.DEDUP_DISTINCT_BELOW, "%", "DEDUP_DISTINCT_BELOW"),
        ("quota_sezione", "Quota minima del tracciato contenuto in un altro per essere una sezione dell'intero", 100 * config.DEDUP_SECTION_OVERLAP_MIN, "%", "DEDUP_SECTION_OVERLAP_MIN"),
        ("distanza_m", "Distanza entro cui due tracciati si dicono coincidenti", config.DEDUP_NEAR_M, "m", "DEDUP_NEAR_M"),
        ("quasi_quota_min", "Quasi duplicati: quota minima del tracciato coincidente", 100 * config.DEDUP_QUASI_OVERLAP_MIN, "%", "DEDUP_QUASI_OVERLAP_MIN"),
        ("quasi_quota_max", "Quasi duplicati: quota massima (la soglia della prima prova)", 100 * config.DEDUP_QUASI_OVERLAP_MAX, "%", "DEDUP_QUASI_OVERLAP_MAX"),
        ("candidati_nome_min", "Punteggio minimo del nome per tenere una coppia candidata in coppie.csv", config.DEDUP_CANDIDATE_NAME_MIN, "punteggio", "DEDUP_CANDIDATE_NAME_MIN"),
    )
    for stem, text, value, unit, name in rows:
        s.add(f"dedup_param_{stem}", text, value, unit, config_source(name))


def deduplicazione(report: Report) -> None:
    s = report.section("deduplicazione")
    for part in (_dedup_distinct, _dedup_comparisons, _dedup_candidates, _dedup_quasi):
        part(s, report)
    _dedup_parameters(s)


# --- abbinamento con OSM ---

def _osm_outcomes(s: Section, matches: pd.DataFrame, origin: Path) -> None:
    for fonte in (*config.DEDUP_FONTI, None):
        part = matches if fonte is None else matches[matches["fonte"] == fonte]
        name, label = ("tutti", "i cammini del catalogo") if fonte is None else (fonte, f"i cammini di {FONTE_LABELS[fonte]}")
        s.add(f"osm_match_cammini_{name}", f"Cammini confrontati con le relazioni OSM: {label}", len(part), "cammini", source(origin))
        for esito in config.REPORT_OSM_ESITI:
            s.count(f"osm_esito_{name}_{slug(esito)}", f"Abbinamento OSM con esito «{esito}»: {label}", part["esito"] == esito, "cammini", source(origin, f"esito = {esito}"))
        matched = part[part["esito"] == config.ESITO_ABBINATO]
        with_tracks = matched["stato_geometria"] != config.STATO_ASSENTE
        s.count(f"osm_abbinati_{name}_con_tracce", f"Cammini abbinati a OSM che hanno tracce proprie (geometria ok o parziale): {label}", with_tracks, "cammini", source(origin, "esito = abbinato", "stato_geometria diverso da assente"))
        s.count(f"osm_abbinati_{name}_senza_tracce", f"Cammini abbinati a OSM senza tracce proprie (geometria assente): {label}", ~with_tracks, "cammini", source(origin, "esito = abbinato", "stato_geometria = assente"))
        for value in config.REPORT_OSM_PILGRIMAGE_VALUES:
            s.count(f"osm_abbinati_{name}_pellegrinaggio_{slug(value)}", f"Cammini abbinati a OSM con pellegrinaggio «{value}» nelle relazioni: {label}", matched["pellegrinaggio_osm"] == value, "cammini", source(origin, "esito = abbinato", f"pellegrinaggio_osm = {value}"))


def _osm_quality(s: Section, matches: pd.DataFrame, origin: Path) -> None:
    matched = matches[matches["esito"] == config.ESITO_ABBINATO]
    s.add("osm_abbinati_relazioni", "Relazioni OSM usate dagli abbinamenti", to_numbers(matched["n_relazioni"]).sum(), "relazioni", source(origin, "esito = abbinato", "somma di n_relazioni"))
    ratio = to_numbers(matched["rapporto_dichiarata"]).dropna()
    s.distribution("osm_abbinati_rapporto_lunghezza", "Lunghezza OSM sulla lunghezza dichiarata nei cammini abbinati", ratio, "rapporto", source(origin, "esito = abbinato", "rapporto_dichiarata"))
    tracked = matched[matched["stato_geometria"] != config.STATO_ASSENTE]
    for column, text in (("quota_catalogo_in_osm", "quota del tracciato del catalogo sulla relazione OSM"), ("quota_osm_in_catalogo", "quota della relazione OSM sul tracciato del catalogo")):
        values = to_numbers(tracked[column]).dropna()
        s.add(f"osm_abbinati_{column}_mediana", f"Cammini abbinati a OSM con tracce: {text}, mediana", 100 * values.median(), "%", source(origin, "esito = abbinato", "stato_geometria diverso da assente", column))


def _osm_tags(s: Section, report: Report) -> None:
    path = Path(config.CAMMINI_CSV_TEMPLATE.format(fonte=config.OSM_FONTE))
    relations = report.table(path)
    tag = relations["pilgrimage"]
    s.count("osm_tag_pilgrimage_yes", "Relazioni OSM con pilgrimage=yes", tag == config.REPORT_OSM_TAG_YES, "relazioni", source(path, "pilgrimage = yes"))
    s.count("osm_tag_pilgrimage_no", "Relazioni OSM con pilgrimage=no", tag == config.NO, "relazioni", source(path, "pilgrimage = no"))
    s.count("osm_tag_pilgrimage_altro", "Relazioni OSM con un altro valore di pilgrimage (nome del cammino, o valore negativo diverso da no)", ~tag.isin(("", config.REPORT_OSM_TAG_YES, config.NO)), "relazioni", source(path, "pilgrimage altro"))
    s.count("osm_tag_pilgrimage_assente", "Relazioni OSM senza il tag pilgrimage", tag == "", "relazioni", source(path, "pilgrimage vuoto"))
    path = config.TAGS_CAMMINI_CSV
    labels = report.table(path)
    osm = labels[labels["fonte"] == config.OSM_FONTE]
    evidence = osm["evidenza_religioso"]
    plain = evidence == config.EVIDENZA_PILGRIMAGE
    named = evidence.str.startswith(config.EVIDENZA_PILGRIMAGE + " (")
    negative = evidence.str.startswith(PILGRIMAGE_NEGATIVE_PREFIX)
    origin = source(path, "fonte = osm")
    s.count("osm_etichette_pellegrinaggio", "Relazioni OSM etichettate come di pellegrinaggio (religioso = si)", plain | named, "relazioni", origin)
    s.count("osm_etichette_pellegrinaggio_semplice", "Relazioni OSM etichettate come di pellegrinaggio per pilgrimage=yes", plain, "relazioni", origin)
    s.count("osm_etichette_pellegrinaggio_nominato", "Relazioni OSM etichettate come di pellegrinaggio per un pilgrimage col nome del cammino", named, "relazioni", origin)
    s.count("osm_etichette_non_pellegrinaggio", "Relazioni OSM indicate come non di pellegrinaggio", negative, "relazioni", origin)


def _osm_standins(s: Section, report: Report) -> None:
    path = config.PAIRS_DIR / config.PAIRS_FILES["cammini"]
    unique = report.table(path)
    only_osm = unique["fonte_geometria"] == config.FONTE_GEOMETRIA_OSM
    both = unique["fonte_geometria"] == config.FONTE_GEOMETRIA_CATALOGO_OSM
    s.count("osm_cammini_solo_osm", "Cammini distinti con il solo tracciato di OpenStreetMap (nessuna traccia del catalogo)", only_osm, "cammini", source(path, "fonte_geometria = osm"))
    s.count("osm_cammini_catalogo_e_osm", "Cammini distinti con tracce del catalogo per un membro e il sostituto OSM per un altro", both, "cammini", source(path, "fonte_geometria = catalogo e osm"))
    s.count("osm_sostituti_usati", "Cammini distinti in cui si usa una geometria OSM sostitutiva", only_osm | both, "cammini", source(path, "fonte_geometria contiene osm"))
    path = config.PAIRS_DIR / config.PAIRS_FILES["coppie"]
    coppie = report.table(path)
    for value, stem in ((config.FONTE_GEOMETRIA_OSM, "osm"), (config.FONTE_GEOMETRIA_CATALOGO_OSM, "catalogo_e_osm")):
        s.count(f"osm_coppie_fonte_{stem}", f"Coppie cammino-comune ricavate da una geometria con fonte «{value}»", coppie["fonte_geometria"] == value, "coppie", source(path, f"fonte_geometria = {value}"))


def osm(report: Report) -> None:
    s = report.section("osm")
    path = config.OSM_MATCH_DIR / config.OSM_MATCH_FILES["abbinamenti"]
    matches = report.table(path)
    _osm_outcomes(s, matches, path)
    _osm_quality(s, matches, path)
    _osm_tags(s, report)
    _osm_standins(s, report)
    parameters = (
        ("soglia_nome", "Punteggio minimo del nome per l'abbinamento con OSM", config.OSM_MATCH_NAME_MIN_SCORE, "punteggio", "OSM_MATCH_NAME_MIN_SCORE"),
        ("quota_minima", "Quota minima di sovrapposizione tra catalogo e relazione OSM", 100 * config.OSM_MATCH_SHARE_MIN, "%", "OSM_MATCH_SHARE_MIN"),
        ("distanza_m", "Distanza entro cui un tracciato sta sulla relazione OSM", config.OSM_MATCH_NEAR_M, "m", "OSM_MATCH_NEAR_M"),
        ("quota_regioni", "Senza tracce: quota minima della relazione nelle regioni dichiarate", 100 * config.OSM_MATCH_REGION_SHARE_MIN, "%", "OSM_MATCH_REGION_SHARE_MIN"),
        ("rapporto_min", "Rapporto minimo tra lunghezza OSM e lunghezza di riferimento", config.OSM_MATCH_LENGTH_RATIO_MIN, "rapporto", "OSM_MATCH_LENGTH_RATIO_MIN"),
        ("rapporto_max", "Rapporto massimo tra lunghezza OSM e lunghezza di riferimento", config.OSM_MATCH_LENGTH_RATIO_MAX, "rapporto", "OSM_MATCH_LENGTH_RATIO_MAX"),
        ("lunghezza_min_m", "Senza tracce e senza lunghezza dichiarata: lunghezza minima del gruppo OSM", config.OSM_MATCH_MIN_LENGTH_M, "m", "OSM_MATCH_MIN_LENGTH_M"),
    )
    for stem, text, value, unit, name in parameters:
        s.add(f"osm_param_{stem}", text, value, unit, config_source(name))


# --- etichette ---

def evidence_kinds(text: str) -> frozenset[str]:
    """Da quali fonti risulta il carattere religioso, dal testo di evidenza_religioso di un cammino distinto."""
    kinds: set[str] = set()
    parts = [part for chunk in text.split(config.LIST_SEPARATOR) for part in chunk.split(config.EVIDENZA_SEPARATOR)]
    for part in parts:
        if part == config.EVIDENZA_CATALOGO_RELIGIOSO:
            kinds.add("catalogo")
        elif part == config.EVIDENZA_TEMA_CDI:
            kinds.add("tema_cdi")
        elif part == config.EVIDENZA_TEMA_LAYER:
            kinds.add("tema_mappa")
        elif part.startswith(config.EVIDENZA_PILGRIMAGE):
            kinds.add("pellegrinaggio_osm")
        elif part.startswith(NAME_EVIDENCE_PREFIXES):
            kinds.add("nome")
        else:
            raise ReportNumbersError(f"evidenza del carattere religioso non riconosciuta: {part!r}")
    return frozenset(kinds)


def _tag_catalogue(s: Section, report: Report) -> None:
    path = config.TAGS_CAMMINI_CSV
    labels = report.table(path)
    for fonte in config.FONTI_GEOMETRIA:
        part = labels[labels["fonte"] == fonte]
        for value in (SI, NO):
            s.count(f"etichette_{fonte}_religioso_{value}", f"Cammini di {FONTE_LABELS[fonte]} etichettati religioso = {value}", part["religioso"] == value, "cammini", source(path, f"fonte = {fonte}", f"religioso = {value}"))
    path = config.TAGS_TRATTE_CSV
    tratte = report.table(path)
    for fonte in config.FONTI_GEOMETRIA:
        part = tratte[tratte["fonte"] == fonte]
        for mezzo in (config.MEZZO_PIEDI, config.MEZZO_BICI, config.MEZZO_ENTRAMBI):
            s.count(f"etichette_tratte_{fonte}_{mezzo}", f"Tratte di {FONTE_LABELS[fonte]} con mezzo «{mezzo}»", part["mezzo"] == mezzo, "tratte", source(path, f"fonte = {fonte}", f"mezzo = {mezzo}"))


def _tag_religious(s: Section, unique: pd.DataFrame, path: Path) -> None:
    religious = unique[unique["religioso"] == SI]
    n_all = len(unique)
    for value in (SI, NO):
        part = unique["religioso"] == value
        s.count(f"etichette_distinti_religioso_{value}", f"Cammini distinti con religioso = {value}", part, "cammini", source(path, f"religioso = {value}"))
        s.share(f"etichette_distinti_religioso_{value}_quota", f"Quota di cammini distinti con religioso = {value}", part.sum(), n_all, source(path, f"religioso = {value}"))
    kinds = religious["evidenza_religioso"].map(evidence_kinds)
    if (kinds.map(len) == 0).any():
        raise ReportNumbersError("cammini religiosi senza evidenza")
    for kind, text in config.REPORT_EVIDENCE_KINDS:
        has = kinds.map(lambda found: kind in found)
        only = kinds.map(lambda found: found == {kind})
        s.count(f"etichette_si_da_{kind}", f"Cammini religiosi {text}", has, "cammini", source(path, "religioso = si", f"evidenza contiene {kind}"))
        s.count(f"etichette_si_solo_{kind}", f"Cammini religiosi {text}, e solo per questa indicazione", only, "cammini", source(path, "religioso = si", f"evidenza solo {kind}"))
    s.count("etichette_si_da_tema_spirituale", "Cammini religiosi con tema spirituale su camminiditalia.org o sulla mappa", kinds.map(lambda found: bool(found & {"tema_cdi", "tema_mappa"})), "cammini", source(path, "religioso = si", "evidenza tema_cdi o tema_mappa"))
    sizes = kinds.map(len)
    for size in sorted(sizes.unique()):
        s.count(f"etichette_si_con_{size}_indicazioni", f"Cammini religiosi che risultano tali da {size} tipi di indicazione", sizes == size, "cammini", source(path, "religioso = si", "tipi di evidenza distinti"))


def _tag_attributes(s: Section, unique: pd.DataFrame, path: Path) -> None:
    for value, count in value_counts(unique["mezzo"]):
        s.add(f"etichette_distinti_mezzo_{slug(value)}", f"Cammini distinti con mezzo «{value}»", count, "cammini", source(path, f"mezzo = {value}"))
    for field_name in config.REPORT_TAG_MULTI_FIELDS:
        values = unique[field_name].map(lambda text: text.split(config.LIST_SEPARATOR) if text else [config.REPORT_EMPTY_SLUG])
        for value, count in sorted(Counter(item for items in values for item in items).items(), key=lambda item: (-item[1], item[0])):
            s.add(f"etichette_{field_name}_{slug(value)}", f"Cammini distinti con {field_name} «{value}» (anche insieme ad altri valori)", count, "cammini", source(path, f"{field_name} contiene {value}"))
    for field_name in config.REPORT_TAG_FLAG_FIELDS:
        has = unique[field_name].map(lambda text: SI in text.split(config.LIST_SEPARATOR))
        s.count(f"etichette_{field_name}_si", f"Cammini distinti con {field_name} = si in almeno una fonte", has, "cammini", source(path, f"{field_name} contiene si"))


def _tag_pairs(s: Section, report: Report) -> None:
    path = config.PAIRS_DIR / config.PAIRS_FILES["coppie"]
    coppie = report.table(path)
    for mezzo, count in value_counts(coppie["mezzo"]):
        s.share(f"etichette_coppie_mezzo_{slug(mezzo)}", f"Quota di coppie cammino-comune con mezzo «{mezzo}»", count, len(coppie), source(path, f"mezzo = {mezzo}"))
        s.add(f"etichette_coppie_mezzo_{slug(mezzo)}_n", f"Coppie cammino-comune con mezzo «{mezzo}»", count, "coppie", source(path, f"mezzo = {mezzo}"))
    religious = coppie["religioso"] == SI
    s.count("etichette_coppie_religioso_si", "Coppie cammino-comune di cammini religiosi", religious, "coppie", source(path, "religioso = si"))
    s.count("etichette_coppie_religioso_no", "Coppie cammino-comune di cammini non religiosi", ~religious, "coppie", source(path, "religioso = no"))
    s.add("etichette_comuni_con_cammino_religioso", "Comuni attraversati da almeno un cammino religioso", coppie.loc[religious, COMUNE].nunique(), "comuni", source(path, "religioso = si", "comuni distinti"))


def etichette(report: Report) -> None:
    s = report.section("etichette")
    path = config.PAIRS_DIR / config.PAIRS_FILES["cammini"]
    unique = report.table(path)
    _tag_catalogue(s, report)
    _tag_religious(s, unique, path)
    _tag_attributes(s, unique, path)
    _tag_pairs(s, report)


# --- comuni ---

def _region_slug(name: str) -> str:
    return crossing.region_slug(name).replace("-", "_")


def _comuni_totals(s: Section, report: Report, coppie: pd.DataFrame, path: Path) -> None:
    s.add("comuni_coppie", "Coppie cammino distinto-comune", len(coppie), "coppie", source(path))
    crossed = coppie[COMUNE].nunique()
    s.add("comuni_attraversati", "Comuni attraversati da almeno un cammino", crossed, "comuni", source(path, "comuni distinti"))
    s.share("comuni_attraversati_quota", "Quota dei comuni ISTAT attraversati da almeno un cammino", crossed, len(report.istat_attributes), source(path, "comuni distinti su comuni ISTAT"))
    inside = coppie["dentro"] == "True"
    s.count("comuni_coppie_dentro", "Coppie in cui il tracciato tocca o entra nel comune (dentro = True)", inside, "coppie", source(path, "dentro = True"))
    s.count("comuni_coppie_solo_fascia", "Coppie in cui il tracciato passa fuori dal comune ma entro la distanza d (dentro = False)", ~inside, "coppie", source(path, "dentro = False"))
    s.share("comuni_coppie_solo_fascia_quota", "Quota di coppie in cui il tracciato passa fuori dal comune ma entro la distanza d", (~inside).sum(), len(coppie), source(path, "dentro = False"))
    for value, count in value_counts(coppie["fonte_geometria"]):
        s.add(f"comuni_coppie_fonte_geometria_{slug(value)}", f"Coppie ricavate da una geometria con fonte «{value}»", count, "coppie", source(path, f"fonte_geometria = {value}"))
    per_comune = coppie.groupby(COMUNE)[UNICO].nunique()
    s.add("comuni_con_un_solo_cammino", "Comuni attraversati da un solo cammino", int((per_comune == 1).sum()), "comuni", source(path, "cammini distinti per comune"))
    s.distribution("comuni_cammini_per_comune", "Cammini per comune attraversato", per_comune, "cammini", source(path, "cammini distinti per comune"))


def _comuni_regions(s: Section, report: Report, coppie: pd.DataFrame, path: Path) -> None:
    totals = report.istat_attributes.groupby(config.COMUNE_REGION_COL).size()
    s.add("comuni_regioni_con_cammini", "Regioni con almeno un comune attraversato", coppie["regione"].nunique(), "regioni", source(path, "regioni distinte"))
    for name, rows in coppie.groupby("regione"):
        stem, origin = _region_slug(str(name)), source(path, f"regione = {name}")
        crossed = rows[COMUNE].nunique()
        s.add(f"regione_{stem}_comuni", f"{name}: comuni attraversati", crossed, "comuni", origin)
        s.add(f"regione_{stem}_cammini", f"{name}: cammini distinti che attraversano la regione", rows[UNICO].nunique(), "cammini", origin)
        s.add(f"regione_{stem}_coppie", f"{name}: coppie cammino-comune", len(rows), "coppie", origin)
        s.share(f"regione_{stem}_quota_comuni", f"{name}: quota dei comuni della regione attraversati", crossed, totals[int(rows['cod_reg'].iloc[0])], origin)


def _comuni_top(s: Section, coppie: pd.DataFrame, path: Path) -> None:
    per_comune = coppie.groupby(COMUNE).agg(n=(UNICO, "nunique"), comune=("comune", "first"), sigla=("sigla", "first"), provincia=("provincia", "first"))
    ranked = per_comune.reset_index().sort_values(["n", "comune", COMUNE], ascending=[False, True, True]).head(config.REPORT_TOP_COMUNI)
    for rank, row in enumerate(ranked.itertuples(), start=1):
        s.add(f"top_comune_{rank:02d}_cammini", f"{rank}o comune per numero di cammini: {row.comune} ({row.sigla}), provincia di {row.provincia}", row.n, "cammini", source(path, "cammini distinti per comune"))
    tenth = ranked["n"].iloc[-1]
    s.add("top_comuni_pari_merito_ultimo", f"Comuni con lo stesso numero di cammini del {len(ranked)}o in classifica (a pari merito l'ordine e' alfabetico)", int((per_comune["n"] == tenth).sum()), "comuni", source(path, "cammini distinti per comune"))


def _comuni_cammini(s: Section, unique: pd.DataFrame, path: Path) -> None:
    n_comuni = to_numbers(unique["n_comuni"])
    s.add("cammini_distinti", "Cammini distinti nella cartella di lavoro", len(unique), "cammini", source(path))
    s.count("cammini_con_comuni", "Cammini distinti con almeno un comune", n_comuni > 0, "cammini", source(path, "n_comuni > 0"))
    s.distribution("cammini_n_comuni", "Comuni per cammino (tutti i cammini distinti)", n_comuni, "comuni", source(path, "n_comuni"))
    s.distribution("cammini_n_comuni_con_comuni", "Comuni per cammino (solo cammini con almeno un comune)", n_comuni[n_comuni > 0], "comuni", source(path, "n_comuni", "n_comuni > 0"))
    longest = unique.loc[n_comuni.idxmax()]
    s.add("cammini_n_comuni_max_cammino", f"Cammino con piu' comuni: {longest['nome']}", n_comuni.max(), "comuni", source(path, "max di n_comuni"))
    s.add("cammini_comuni_totale", "Somma dei comuni dei cammini (coincide con le coppie)", n_comuni.sum(), "coppie", source(path, "somma di n_comuni"))
    for value, count in value_counts(unique["fonte_geometria"]):
        s.add(f"cammini_fonte_geometria_{slug(value)}", f"Cammini distinti con fonte del tracciato «{value}»", count, "cammini", source(path, f"fonte_geometria = {value}"))
    none = unique["fonte_geometria"] == config.FONTE_GEOMETRIA_NESSUNA
    s.count("cammini_senza_geometria", "Cammini distinti senza alcuna geometria (comuni non determinabili)", none, "cammini", source(path, "fonte_geometria = nessuna"))
    for fonti, count in value_counts(unique.loc[none, "fonti"]):
        s.add(f"cammini_senza_geometria_fonti_{slug(fonti)}", f"Cammini senza geometria presenti in: {fonti}", count, "cammini", source(path, "fonte_geometria = nessuna", f"fonti = {fonti}"))
    s.count("cammini_con_comuni_senza_geometria", "Cammini senza geometria ma con comuni (deve essere zero)", none & (n_comuni > 0), "cammini", source(path, "fonte_geometria = nessuna", "n_comuni > 0"))


def _coverage_and_length(s: Section, unique: pd.DataFrame, path: Path) -> None:
    coverage = to_numbers(unique["copertura"])
    low, high = config.REPORT_COPERTURA_BANDS
    tag_low, tag_high = round(low * 100), round(high * 100)
    s.count("copertura_non_calcolabile", "Cammini distinti con copertura non calcolabile (senza tracciato o senza lunghezza dichiarata)", coverage.isna(), "cammini", source(path, "copertura vuota"))
    s.count(f"copertura_sotto_{tag_low}", f"Cammini distinti con copertura sotto {low}", coverage < low, "cammini", source(path, f"copertura < {low}"))
    s.count(f"copertura_da_{tag_low}_a_{tag_high}", f"Cammini distinti con copertura da {low} a meno di {high}", (coverage >= low) & (coverage < high), "cammini", source(path, f"{low} <= copertura < {high}"))
    s.count(f"copertura_da_{tag_high}", f"Cammini distinti con copertura da {high} in su", coverage >= high, "cammini", source(path, f"copertura >= {high}"))
    s.add("copertura_mediana", "Copertura mediana dei cammini distinti (dove calcolabile)", 100 * coverage.median(), "%", source(path, "mediana di copertura"))
    traced, declared = to_numbers(unique["lunghezza_tracciata_km"]), to_numbers(unique["lunghezza_dichiarata_km"])
    ratio = (traced / declared).where(declared > 0).dropna()
    s.add("lunghezza_rapporto_calcolabile", "Cammini distinti con lunghezza tracciata e dichiarata entrambe note", len(ratio), "cammini", source(path, "lunghezza_tracciata_km / lunghezza_dichiarata_km"))
    s.distribution("lunghezza_rapporto", "Lunghezza tracciata sulla dichiarata, prima del tetto a 1 della copertura", ratio, "rapporto", source(path, "lunghezza_tracciata_km / lunghezza_dichiarata_km"))
    for threshold in config.REPORT_LENGTH_RATIO_THRESHOLDS:
        s.count(f"lunghezza_rapporto_sopra_{round(threshold * 100)}", f"Cammini distinti con tracciato piu' lungo di {threshold} volte il dichiarato", ratio > threshold, "cammini", source(path, f"rapporto > {threshold}"))


def _comuni_notes(s: Section, unique: pd.DataFrame, path: Path) -> None:
    notes = unique["nota"].map(lambda text: [part for part in text.split(config.PAIRS_NOTA_SEPARATOR) if part])
    s.count("note_cammini_con_nota", "Cammini distinti con una nota", notes.map(len) > 0, "cammini", source(path, "nota non vuota"))

    def kind_of(part: str) -> str:
        if part.startswith(NOTA_DUPLICATE_PREFIX):
            return "possibile_duplicato"
        if part == config.PAIRS_NOTA_NESSUNA_GEOMETRIA:
            return "nessuna_geometria"
        raise ReportNumbersError(f"nota non riconosciuta: {part!r}")

    kinds = notes.map(lambda parts: {kind_of(part) for part in parts})
    for kind, label in config.REPORT_NOTA_KINDS:
        s.count(f"note_{kind}", f"Cammini distinti con una nota «{label}»", kinds.map(lambda found: kind in found), "cammini", source(path, f"nota contiene {label}"))
    s.count("note_possibile_duplicato_e_nessuna_geometria", "Cammini distinti con entrambe le note", kinds.map(lambda found: len(found) == 2), "cammini", source(path, "nota contiene entrambe"))


def comuni(report: Report) -> None:
    s = report.section("comuni")
    pairs_path, unique_path = (config.PAIRS_DIR / config.PAIRS_FILES[name] for name in ("coppie", "cammini"))
    coppie, unique = report.table(pairs_path), report.table(unique_path)
    _comuni_totals(s, report, coppie, pairs_path)
    _comuni_regions(s, report, coppie, pairs_path)
    _comuni_top(s, coppie, pairs_path)
    _comuni_cammini(s, unique, unique_path)
    _coverage_and_length(s, unique, unique_path)
    _comuni_notes(s, unique, unique_path)


# --- ordine dei comuni ---

def consecutive_jumps(coppie: pd.DataFrame, centroids: pd.DataFrame) -> pd.DataFrame:
    """Per ogni coppia di comuni consecutivi nell'ordine di un cammino: distanza tra i centroidi in km.

    coppie: colonne cammino_unico_id, PRO_COM_T, ordine (testo). Colonne in uscita: unico, da, a, km."""
    ordered = coppie.assign(posizione=to_numbers(coppie["ordine"])).sort_values([UNICO, "posizione"])
    missing = set(ordered[COMUNE]) - set(centroids.index)
    if missing:
        raise ReportNumbersError(f"comuni senza confine ISTAT: {sorted(missing)[:5]}")
    xy = centroids.loc[ordered[COMUNE]].to_numpy()
    same = (ordered[UNICO] == ordered[UNICO].shift()).to_numpy()
    km = np.hypot(*(xy[1:] - xy[:-1]).T) / 1000
    frame = pd.DataFrame({
        "unico": ordered[UNICO].to_numpy()[1:], "da": ordered[COMUNE].to_numpy()[:-1], "a": ordered[COMUNE].to_numpy()[1:],
        "km": km, "stesso": same[1:],
    })
    return frame[frame["stesso"]].drop(columns="stesso").reset_index(drop=True)


def _order_jumps(s: Section, report: Report, coppie: pd.DataFrame, path: Path) -> None:
    jumps = consecutive_jumps(coppie, report.istat_centroids)
    origin = "centroidi dei comuni ISTAT in " + config.CRS_WORK
    names = coppie.drop_duplicates(COMUNE).set_index(COMUNE)["comune"]
    unique_names = coppie.drop_duplicates(UNICO).set_index(UNICO)["nome"]
    s.add("ordine_salti_totali", "Passaggi tra comuni consecutivi nell'ordine dei cammini", len(jumps), "passaggi", source(path, origin))
    s.add("ordine_salto_mediana_km", "Distanza mediana tra i centroidi di due comuni consecutivi", jumps["km"].median(), "km", source(path, origin))
    for threshold in config.REPORT_JUMP_THRESHOLDS_KM:
        far = jumps[jumps["km"] > threshold]
        text = f"con un passaggio tra comuni consecutivi oltre {threshold} km"
        s.add(f"ordine_cammini_salto_oltre_{threshold}km", f"Cammini distinti {text} (ordine ramo per ramo: il cambio di ramo e' un salto)", far["unico"].nunique(), "cammini", source(path, origin, f"km > {threshold}"))
        s.add(f"ordine_passaggi_oltre_{threshold}km", f"Passaggi tra comuni consecutivi oltre {threshold} km", len(far), "passaggi", source(path, origin, f"km > {threshold}"))
    biggest = jumps.loc[jumps["km"].idxmax()]
    s.add("ordine_salto_massimo_km", f"Passaggio piu' lungo: {unique_names[biggest['unico']]}, da {names[biggest['da']]} a {names[biggest['a']]}", biggest["km"], "km", source(path, origin, "max di km"))


def endpoint_counts(frame: pd.DataFrame) -> dict[str, int]:
    """Conteggi di concordanza tra estremi pubblicati e primo e ultimo comune dell'ordine.

    frame: colonne start_code, end_code (comune pubblicato, mancante se non risolto), first_code, last_code."""
    has_start, has_end = frame["start_code"].notna(), frame["end_code"].notna()
    both = has_start & has_end
    start_ok = has_start & (frame["start_code"] == frame["first_code"])
    end_ok = has_end & (frame["end_code"] == frame["last_code"])
    swapped = both & (frame["start_code"] == frame["last_code"]) & (frame["end_code"] == frame["first_code"]) & ~(start_ok & end_ok)
    return {
        "cammini": len(frame), "partenza_risolta": int(has_start.sum()), "arrivo_risolto": int(has_end.sum()),
        "entrambi_risolti": int(both.sum()), "partenza_concorde": int(start_ok.sum()), "arrivo_concorde": int(end_ok.sum()),
        "entrambi_concordi": int((both & start_ok & end_ok).sum()), "invertiti": int(swapped.sum()),
    }


def published_endpoints(report: Report) -> pd.DataFrame:
    """Per ogni cammino di camminiditalia.org: comuni di partenza e arrivo pubblicati (partenza o arrivo del cammino, poi
    della prima o ultima tappa); None se il nome non indica un solo comune. Indice = cammino_id con prefisso."""
    cammini, tappe = report.table(config.CDI_CAMMINI_CSV), report.table(config.CDI_TAPPE_CSV)
    stages = tappe.assign(numero=to_numbers(tappe["ordine"])).sort_values("numero").groupby("cammino_id")
    first_start, last_end = stages["partenza"].first(), stages["arrivo"].last()
    index = calibration.build_place_index(report.istat_attributes)
    regions = calibration.regions_by_cammino(cammini, report.region_codes)
    rows = {}
    for row in cammini.itertuples():
        cid = config.CDI_ID_PREFIX + row.id
        rows[cid] = {
            "tipologia": row.tipologia,
            "start_code": pairs.first_place([row.partenza, first_start.get(row.id, "")], cid, index, regions),
            "end_code": pairs.first_place([row.arrivo, last_end.get(row.id, "")], cid, index, regions),
        }
    return pd.DataFrame.from_dict(rows, orient="index")


def _order_endpoints(s: Section, report: Report, coppie: pd.DataFrame, unique: pd.DataFrame, path: Path) -> None:
    members = report.table(config.DEDUP_DIR / config.DEDUP_FILES["membri"])
    members = members[(members["ruolo"] != config.RUOLO_SEZIONE) & (members["fonte"] == config.CDI_FONTE)]
    published = published_endpoints(report)
    endpoints = published.join(members.set_index("cammino_id")[UNICO], how="inner")
    ordered = coppie.assign(posizione=to_numbers(coppie["ordine"])).sort_values([UNICO, "posizione"]).groupby(UNICO)[COMUNE]
    ends = pd.DataFrame({"first_code": ordered.first(), "last_code": ordered.last()})
    # Origine dell'orientamento: pairs.py parte dalla partenza pubblicata quando e' risolta, quindi la concordanza della
    # partenza e' in parte costruita; l'arrivo e' la verifica indipendente
    linear = endpoints[endpoints["tipologia"] == config.REPORT_ENDPOINT_TIPOLOGIA]
    frame = linear.join(ends, on=UNICO, how="inner")
    counts = endpoint_counts(frame)
    origin = source(config.CDI_CAMMINI_CSV, "tipologia = lineare", "cammini cdi non sezione con almeno un comune", "nomi risolti come in pairs.py")
    texts = {
        "cammini": "Cammini lineari di camminiditalia.org (non sezioni) con almeno un comune",
        "partenza_risolta": "di cui con la partenza pubblicata riconosciuta come un solo comune",
        "arrivo_risolto": "di cui con l'arrivo pubblicato riconosciuto come un solo comune",
        "entrambi_risolti": "di cui con partenza e arrivo riconosciuti",
        "partenza_concorde": "con la partenza pubblicata uguale al primo comune dell'ordine (l'ordine parte dalla partenza pubblicata: verifica in parte costruita)",
        "arrivo_concorde": "con l'arrivo pubblicato uguale all'ultimo comune dell'ordine (verifica indipendente dall'orientamento)",
        "entrambi_concordi": "con partenza e arrivo pubblicati uguali al primo e all'ultimo comune dell'ordine",
        "invertiti": "con partenza e arrivo pubblicati scambiati rispetto al primo e all'ultimo comune",
    }
    for key, text in texts.items():
        s.add(f"ordine_estremi_{key}", text if key == "cammini" else f"Cammini lineari cdi {text}", counts[key], "cammini", origin)
    for key, base in (("partenza_concorde", "partenza_risolta"), ("arrivo_concorde", "arrivo_risolto"), ("entrambi_concordi", "entrambi_risolti")):
        if counts[base]:
            s.share(f"ordine_estremi_{key}_quota", f"Quota di concordanza: {texts[key]}, sui cammini con l'estremo riconosciuto", counts[key], counts[base], origin)
    only_osm = unique.loc[unique["fonte_geometria"] == config.FONTE_GEOMETRIA_OSM, UNICO]
    with_start = set(endpoints.loc[endpoints["start_code"].notna(), UNICO])
    s.add("ordine_solo_osm_senza_partenza", "Cammini con il solo tracciato OSM e nessuna partenza pubblicata riconosciuta (il verso non e' garantito)", int((~only_osm.isin(with_start)).sum()), "cammini", source(path, "fonte_geometria = osm", "nessun membro cdi con partenza risolta"))
    s.add("ordine_solo_osm_totale", "Cammini con il solo tracciato OSM", len(only_osm), "cammini", source(path, "fonte_geometria = osm"))


def ordine(report: Report) -> None:
    s = report.section("ordine")
    pairs_path, unique_path = (config.PAIRS_DIR / config.PAIRS_FILES[name] for name in ("coppie", "cammini"))
    coppie, unique = report.table(pairs_path), report.table(unique_path)
    _order_jumps(s, report, coppie, pairs_path)
    _order_endpoints(s, report, coppie, unique, unique_path)


# --- cartella di lavoro ---

def latest_workbook(report: Report) -> Path:
    folder = report.locate(config.WORKBOOK_OUTPUT_DIR)
    found = sorted(folder.glob(config.REPORT_WORKBOOK_GLOB))
    if not found:
        raise ReportNumbersError(f"nessuna cartella di lavoro in {folder}: lanciare workbook")
    return found[-1]


def sheet_rows(workbook_file: Path) -> dict[str, list[tuple[object, ...]]]:
    """Righe non vuote di ogni foglio, intestazione compresa."""
    book = load_workbook(workbook_file, read_only=True, data_only=True)
    try:
        return {
            sheet.title: [row for row in sheet.iter_rows(values_only=True) if any(value is not None for value in row)]
            for sheet in book.worksheets
        }
    finally:
        book.close()


def cartella(report: Report) -> None:
    s = report.section("cartella")
    workbook_file = latest_workbook(report)
    origin = f"{config.REPORT_DATA_PREFIX}{config.WORKBOOK_OUTPUT_DIR.relative_to(config.DATA_DIR).as_posix()}/{workbook_file.name}"
    found = re.search(config.REPORT_WORKBOOK_DATE_PATTERN, workbook_file.name)
    if not found:
        raise ReportNumbersError(f"nome della cartella di lavoro senza data: {workbook_file.name}")
    sheets = sheet_rows(workbook_file)
    s.add("cartella_data_elaborazione", "Data di elaborazione della cartella di lavoro", found[1], "data", origin)
    s.add("cartella_dimensione_mb", "Dimensione del file della cartella di lavoro", workbook_file.stat().st_size / config.REPORT_BYTES_PER_MB, "MB", origin)
    s.add("cartella_fogli", "Fogli della cartella di lavoro", len(sheets), "fogli", origin)
    for name in config.REPORT_WORKBOOK_DATA_SHEETS:
        if name not in sheets:
            raise ReportNumbersError(f"manca il foglio {name} nella cartella di lavoro")
        header, rows = sheets[name][0], sheets[name][1:]
        stem = slug(name)
        s.add(f"cartella_righe_{stem}", f"Righe di dati del foglio {name} (intestazione esclusa)", len(rows), "righe", f"{origin}; foglio {name}")
        s.add(f"cartella_colonne_{stem}", f"Colonne del foglio {name}", sum(value is not None for value in header), "colonne", f"{origin}; foglio {name}")
    comuni_sheet = sheets[config.WORKBOOK_SHEET_COMUNI]
    mezzo_column = comuni_sheet[0].index(config.WORKBOOK_HEADERS["mezzo"])
    totals = sum(row[mezzo_column] == config.WORKBOOK_ALL_LABEL for row in comuni_sheet[1:])
    s.add("cartella_comuni_righe_totale", "Righe di totale del foglio Comuni (una per comune)", totals, "righe", f"{origin}; foglio {config.WORKBOOK_SHEET_COMUNI}; Mezzo = {config.WORKBOOK_ALL_LABEL}")
    s.add("cartella_comuni_righe_dettaglio", "Righe di dettaglio del foglio Comuni (per mezzo e carattere religioso)", len(comuni_sheet) - 1 - totals, "righe", f"{origin}; foglio {config.WORKBOOK_SHEET_COMUNI}; Mezzo diverso da {config.WORKBOOK_ALL_LABEL}")
    s.add("cartella_righe_note", "Righe di testo del foglio Note", len(sheets.get(config.WORKBOOK_SHEET_NOTE, [])), "righe", f"{origin}; foglio {config.WORKBOOK_SHEET_NOTE}")


# --- insieme ---

SECTION_BUILDERS: dict[str, Callable[[Report], None]] = {
    "fonti": fonti, "tracciati": tracciati, "calibrazione": calibrazione, "deduplicazione": deduplicazione,
    "osm": osm, "etichette": etichette, "comuni": comuni, "ordine": ordine, "cartella": cartella,
}


def collect(data_dir: Path = config.DATA_DIR) -> pd.DataFrame:
    """Tutti i numeri del rapporto, una riga ciascuno, nell'ordine delle sezioni di config.REPORT_SEZIONI."""
    report = Report(data_dir)
    for name in config.REPORT_SEZIONI:
        SECTION_BUILDERS[name](report)
    return pd.DataFrame(list(report.rows.values()), columns=list(config.REPORT_NUMBERS_FIELDS))


def run(data_dir: Path = config.DATA_DIR) -> None:
    frame = collect(data_dir)
    destination = data_dir / config.REPORT_NUMBERS_FILE.relative_to(config.DATA_DIR)
    common.write_frame(frame, destination)
    print(f"{len(frame)} numeri del rapporto scritti in {destination}", flush=True)
