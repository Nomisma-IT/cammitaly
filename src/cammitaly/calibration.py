"""Calibrazione della distanza d di attraversamento: produce i numeri con cui il committente sceglie d.

A: i luoghi pubblicati come estremi di tappa contro le linee dei cammini.
B: tracce dense degradate alla precisione di Webmapp, per isolare l'effetto della precisione.
C: le stesse tracce da due fonti (cdi e MiTur).
D: quante coppie (cammino, comune) in piu' costa ogni d.
Una linea attraversa un comune se dista dal suo poligono al massimo d metri (d = 0: la linea entra).
"""
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from rapidfuzz import fuzz, process
from shapely.geometry import LineString, MultiLineString
from shapely.geometry.base import BaseGeometry

from cammitaly import config, crossing
from cammitaly.geometry import common
from cammitaly.geometry.cdi import normalize_name

COMUNE = config.COMUNE_ID_COL
DISTANCES = config.CALIBRATION_DISTANCES_M
DISTANCE_INDEX = "d_m"


def _write(frame: pd.DataFrame, name: str) -> None:
    config.CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    frame.to_csv(config.CALIBRATION_DIR / name, index=False, encoding="utf-8", lineterminator="\n")


def share_within(values: pd.Series, d: float) -> float:
    """Quota dei valori <= d; NaN se non ci sono valori."""
    return round(float((values <= d).mean()), config.CALIBRATION_RATIO_DECIMALS) if len(values) else float("nan")


def _by_distance(columns: Mapping[str, Sequence[float]]) -> pd.DataFrame:
    return pd.DataFrame(columns, index=pd.Index(DISTANCES, name=DISTANCE_INDEX))


# --- Nomi dei luoghi: confronto con i comuni ISTAT ---

def split_pieces(text: str, pattern: str) -> list[str]:
    """Il testo intero e i pezzi che si ottengono dividendolo sul pattern, senza duplicati ne' pezzi vuoti."""
    pieces = [text, *re.split(pattern, text)]
    return list(dict.fromkeys(piece.strip() for piece in pieces if piece.strip()))


def place_keys(text: str) -> tuple[str, str]:
    """Chiave stretta (senza spazi ne' articoli) e chiave con "S.", "Sant", "Santa" ecc. ricondotti a "San".

    "Sant'Angelo" e "Santangelo" coincidono nella prima, "S. Giovanni" e "San Giovanni" nella seconda.
    """
    tokens = [token for token in normalize_name(text).split() if token not in config.PLACE_STOPWORDS]
    saints = (config.PLACE_SAINT_CANONICAL if token in config.PLACE_SAINT_TOKENS else token for token in tokens)
    return "".join(tokens), "".join(saints)


@dataclass(frozen=True)
class PlaceIndex:
    # Un dizionario per livello di confronto: chiave -> {PRO_COM_T: COD_REG}
    tiers: tuple[dict[str, dict[str, int]], dict[str, dict[str, int]]]


def build_place_index(comuni: pd.DataFrame) -> PlaceIndex:
    """Indice dei nomi comunali; i nomi bilingui (Bolzano/Bozen) entrano con ciascuna delle due parti."""
    tiers: tuple[dict, dict] = ({}, {})
    bilingual = re.escape(config.ISTAT_BILINGUAL_SEPARATOR)
    for pro_com, name, region in zip(comuni[COMUNE], comuni[config.COMUNE_NAME_COL], comuni[config.COMUNE_REGION_COL]):
        for piece in split_pieces(name, bilingual):
            for table, key in zip(tiers, place_keys(piece)):
                if key:
                    table.setdefault(key, {})[pro_com] = int(region)
    return PlaceIndex(tiers)


def _lookup(index: PlaceIndex, pieces: Sequence[str], regions: frozenset[int] | None) -> set[str]:
    """Comuni trovati al primo livello che ne trova almeno uno, nelle sole regioni indicate."""
    for position, table in enumerate(index.tiers):
        found = {
            pro_com
            for piece in pieces
            for pro_com, region in table.get(place_keys(piece)[position], {}).items()
            if regions is None or region in regions
        }
        if found:
            return found
    return set()


def match_place(text: object, index: PlaceIndex, regions: frozenset[int] | None) -> tuple[str, str | None]:
    """(esito, PRO_COM_T). Un omonimo ancora ambiguo dentro le regioni e' scartato, come un luogo non trovato."""
    if not isinstance(text, str) or not text.strip():
        return config.MATCH_EMPTY, None
    plain = re.sub(config.NAME_DASHES_PATTERN, "-", text)  # i trattini tipografici dividono come quelli ASCII
    found = _lookup(index, split_pieces(plain, config.PLACE_SPLIT_PATTERN), regions)
    if not found:
        return config.MATCH_NOT_FOUND, None
    if len(found) > 1:
        return config.MATCH_HOMONYM, None
    return config.MATCH_OK, next(iter(found))


def regions_by_cammino(cammini: pd.DataFrame, codes: Mapping[str, int]) -> dict[str, frozenset[int] | None]:
    """cammino_id -> codici ISTAT delle sue regioni; None se il cammino non ne ha (allora si cerca in tutta Italia)."""
    result: dict[str, frozenset[int] | None] = {}
    for cammino_id, slugs in zip(config.CDI_ID_PREFIX + cammini["id"].astype(str), cammini["regioni"]):
        wanted = {codes[slug] for slug in str(slugs).split(config.LAYER_SEPARATOR) if slug in codes}
        result[cammino_id] = frozenset(wanted) or None
    return result


# --- A: estremi di tappa pubblicati ---

def reference_names(tappe: pd.DataFrame, tracks: pd.DataFrame, tratte: pd.DataFrame) -> pd.DataFrame:
    """Una riga per ogni estremo pubblicato: dalle tappe del sito (solo cammino) e dalle tracce Webmapp (anche tratta)."""
    stage_rows = tappe.melt(
        id_vars=["cammino_id", "ordine"], value_vars=["partenza", "arrivo"], var_name="ruolo", value_name="nome"
    )
    stage_rows["cammino_id"] = config.CDI_ID_PREFIX + stage_rows["cammino_id"].astype(str)
    stage_rows["origine"], stage_rows["tratta_id"] = config.SOURCE_TAPPE, None
    keyed = tracks.assign(tratta_id=config.CDI_ID_PREFIX + tracks["track_id"].astype(str))
    joined = keyed.merge(tratte[["tratta_id", "cammino_id", "ordine"]], on="tratta_id")
    track_rows = joined.melt(
        id_vars=["cammino_id", "tratta_id", "ordine"], value_vars=["from", "to"], var_name="ruolo", value_name="nome"
    )
    track_rows["origine"] = config.SOURCE_WEBMAPP
    columns = ["origine", "cammino_id", "tratta_id", "ordine", "ruolo", "nome"]
    return pd.concat([stage_rows[columns], track_rows[columns]], ignore_index=True)


def match_references(refs: pd.DataFrame, index: PlaceIndex, regions: Mapping[str, frozenset[int] | None]) -> pd.DataFrame:
    results = [match_place(name, index, regions.get(cammino)) for name, cammino in zip(refs["nome"], refs["cammino_id"])]
    matched = refs.copy()
    matched["esito"] = [outcome for outcome, _ in results]
    matched[COMUNE] = [pro_com for _, pro_com in results]
    return matched


def match_rates(matched: pd.DataFrame) -> pd.DataFrame:
    """Esiti per origine e ruolo, poi per origine e in totale; quota_ok sui nomi non vuoti."""
    outcomes = [config.MATCH_OK, config.MATCH_HOMONYM, config.MATCH_NOT_FOUND, config.MATCH_EMPTY]
    rows = []
    for origine, ruolo, subset in _match_groups(matched):
        counts = subset["esito"].value_counts()
        row = {"origine": origine, "ruolo": ruolo, **{o: int(counts.get(o, 0)) for o in outcomes}}
        row["n_nomi"] = len(subset)
        row["quota_ok"] = round(row[config.MATCH_OK] / max(len(subset) - row[config.MATCH_EMPTY], 1), config.CALIBRATION_RATIO_DECIMALS)
        rows.append(row)
    return pd.DataFrame(rows)


def _match_groups(matched: pd.DataFrame) -> Iterator[tuple[str, str, pd.DataFrame]]:
    for (origine, ruolo), subset in matched.groupby(["origine", "ruolo"]):
        yield origine, ruolo, subset
    for origine, subset in matched.groupby("origine"):
        yield origine, "tutti", subset
    yield "tutte", "tutti", matched


def pair_distances(keys: pd.Series, comuni_ids: pd.Series, lines: Mapping[str, BaseGeometry], comune_geoms: Mapping[str, BaseGeometry]) -> pd.Series:
    """Distanza (m) tra la linea di ogni riga e il suo comune; NaN se la riga non ha linea."""
    has_line = keys.map(lambda key: isinstance(key, str) and key in lines)
    cache: dict[tuple[str, str], float] = {}
    values = pd.Series(np.nan, index=keys.index)
    for position, (key, comune) in enumerate(zip(keys, comuni_ids)):
        if has_line.iloc[position]:
            if (key, comune) not in cache:
                cache[(key, comune)] = crossing.exact_distance(lines[key], comune_geoms[comune])
            values.iloc[position] = cache[(key, comune)]
    return values


def distance_distribution(distances: pd.Series, scope: str) -> dict[str, object]:
    values = distances.dropna()
    row: dict[str, object] = {"ambito": scope, "n_coppie": len(values), "quota_a_0": share_within(values, 0)}
    for percentile in config.CALIBRATION_PERCENTILES:
        row[f"p{percentile}"] = round(float(np.percentile(values, percentile)), config.METERS_DECIMALS)
    row["max"] = round(float(values.max()), config.METERS_DECIMALS)
    return row


def run_published_endpoints(comuni: gpd.GeoDataFrame, tratte_cdi: gpd.GeoDataFrame, lines: Mapping[str, BaseGeometry]) -> pd.DataFrame:
    tappe = pd.read_csv(config.CDI_TAPPE_CSV, encoding="utf-8")
    tracks = pd.read_csv(config.WEBMAPP_TRACKS_CSV, encoding="utf-8")
    cammini = pd.read_csv(config.CDI_CAMMINI_CSV, encoding="utf-8")
    walking = tratte_cdi[tratte_cdi["tipo"].isin(config.CROSSING_TIPI)]
    regions = regions_by_cammino(cammini, crossing.load_region_codes())
    matched = match_references(reference_names(tappe, tracks, walking), build_place_index(comuni), regions)
    ok = matched[matched["esito"] == config.MATCH_OK].copy()
    comune_geoms = dict(zip(comuni[COMUNE], comuni.geometry))
    ok["comune"] = ok[COMUNE].map(dict(zip(comuni[COMUNE], comuni[config.COMUNE_NAME_COL])))
    ok["cammino"] = ok["cammino_id"].map(dict(zip(config.CDI_ID_PREFIX + cammini["id"].astype(str), cammini["titolo"])))
    ok["dist_cammino_m"] = pair_distances(ok["cammino_id"], ok[COMUNE], lines, comune_geoms)
    tratta_geoms = dict(zip(walking["tratta_id"], walking.geometry))
    ok["dist_tratta_m"] = pair_distances(ok["tratta_id"], ok[COMUNE], tratta_geoms, comune_geoms)
    return _publish_endpoint_outputs(matched, ok)


def far_pairs(ok: pd.DataFrame) -> pd.DataFrame:
    """Coppie di riferimento oltre CALIBRATION_FAR_M (per cammino o per tratta), una riga per coppia, dalla piu' lontana."""
    far = ok[(ok["dist_cammino_m"] > config.CALIBRATION_FAR_M) | (ok["dist_tratta_m"] > config.CALIBRATION_FAR_M)]
    grouped = far.groupby(["cammino_id", COMUNE, "tratta_id"], dropna=False, as_index=False)
    unique = grouped.agg(
        cammino=("cammino", "first"), origine=("origine", "first"), ruolo=("ruolo", "first"), nome=("nome", "first"),
        comune=("comune", "first"), dist_cammino_m=("dist_cammino_m", "first"), dist_tratta_m=("dist_tratta_m", "first"),
        n_citazioni=("nome", "size"),
    )
    unique["massima_m"] = unique[["dist_cammino_m", "dist_tratta_m"]].max(axis=1)
    return unique.sort_values("massima_m", ascending=False, ignore_index=True)


def _publish_endpoint_outputs(matched: pd.DataFrame, ok: pd.DataFrame) -> pd.DataFrame:
    _write(matched.merge(ok[["comune", "dist_cammino_m", "dist_tratta_m"]], left_index=True, right_index=True, how="left"), config.CALIBRATION_FILES_A["riferimenti"])
    rates = match_rates(matched)
    _write(rates, config.CALIBRATION_FILES_A["match"])
    per_cammino = ok.drop_duplicates(["cammino_id", COMUNE])["dist_cammino_m"].dropna()
    per_tratta = ok.dropna(subset=["tratta_id"]).drop_duplicates(["tratta_id", COMUNE])["dist_tratta_m"].dropna()
    _write(pd.DataFrame([distance_distribution(per_cammino, "cammino"), distance_distribution(per_tratta, "tratta")]), config.CALIBRATION_FILES_A["distribuzione"])
    _write(far_pairs(ok), config.CALIBRATION_FILES_A["lontane"])
    print(rates.to_string(index=False))
    return _by_distance({
        "A_n_coppie_cammino": [len(per_cammino)] * len(DISTANCES),
        "A_recall_cammino": [share_within(per_cammino, d) for d in DISTANCES],
        "A_n_coppie_tratta": [len(per_tratta)] * len(DISTANCES),
        "A_recall_tratta": [share_within(per_tratta, d) for d in DISTANCES],
    })


# --- Confronto tra insiemi di comuni (usato da B e C) ---

def compare_sets(reference: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    """Per gruppo e per ogni d: comuni di riferimento, ritrovati e in piu'.

    reference: colonne gruppo, PRO_COM_T. candidates: gruppo, PRO_COM_T, distanza_m (una riga per coppia).
    """
    ref = reference.drop_duplicates()
    best = candidates.groupby(["gruppo", COMUNE], as_index=False)["distanza_m"].min()
    sizes = ref.groupby("gruppo").size().rename("n_riferimento")
    frames = []
    for d in DISTANCES:
        found = best[best["distanza_m"] <= d]
        hits = found.merge(ref, on=["gruppo", COMUNE]).groupby("gruppo").size()
        frame = sizes.to_frame()
        frame[DISTANCE_INDEX] = d
        frame["n_trovati"] = hits.reindex(frame.index, fill_value=0)
        frame["n_extra"] = found.groupby("gruppo").size().reindex(frame.index, fill_value=0) - frame["n_trovati"]
        frames.append(frame)
    detail = pd.concat(frames).reset_index()
    detail["recall"] = (detail["n_trovati"] / detail["n_riferimento"]).round(config.CALIBRATION_RATIO_DECIMALS)
    return detail


def totals_by_distance(detail: pd.DataFrame) -> pd.DataFrame:
    """Per d: recall totale (ritrovati / riferimenti), recall medio per gruppo, extra totale e medio per gruppo."""
    grouped = detail.groupby(DISTANCE_INDEX)
    return pd.DataFrame({
        "n_gruppi": grouped.size(),
        "recall": (grouped["n_trovati"].sum() / grouped["n_riferimento"].sum()).round(config.CALIBRATION_RATIO_DECIMALS),
        "recall_media_gruppo": grouped["recall"].mean().round(config.CALIBRATION_RATIO_DECIMALS),
        "extra_tot": grouped["n_extra"].sum(),
        "extra_media_gruppo": grouped["n_extra"].mean().round(config.METERS_DECIMALS),
    })


# --- B: degradazione sintetica ---

def subsample_part(coords: np.ndarray, step: int) -> np.ndarray:
    """Un vertice ogni step, sempre compreso l'ultimo."""
    keep = np.unique(np.append(np.arange(0, len(coords), step), len(coords) - 1))
    return coords[keep]


def _lines_from_parts(parts: Sequence[np.ndarray]) -> LineString | MultiLineString:
    return LineString(parts[0]) if len(parts) == 1 else MultiLineString([part.tolist() for part in parts])


def subsample_every_k(geom: BaseGeometry, target_spacing_m: float) -> BaseGeometry:
    """Tiene un vertice ogni k, con k scelto perche' la spaziatura media diventi circa target_spacing_m."""
    spacing = common.line_stats(geom)["spaziatura_media_m"]
    step = max(1, round(target_spacing_m / spacing))
    parts = [subsample_part(np.asarray(part.coords)[:, :2], step) for part in shapely.get_parts(geom)]
    return _lines_from_parts(parts)


def simplify_line(geom: BaseGeometry, tolerance_m: float) -> BaseGeometry:
    # Senza preserve_topology: serve la semplificazione di Douglas-Peucker pura, come un tracciato "pulito" a mano
    return geom.simplify(tolerance_m, preserve_topology=False)


def degradation_methods() -> dict[str, Callable[[BaseGeometry], BaseGeometry]]:
    methods: dict[str, Callable[[BaseGeometry], BaseGeometry]] = {}
    for spacing in config.CALIBRATION_SUBSAMPLE_SPACINGS_M:
        methods[f"{config.DEGRADE_SUBSAMPLE_PREFIX}{spacing}"] = partial(subsample_every_k, target_spacing_m=spacing)
    for tolerance in config.CALIBRATION_SIMPLIFY_TOLERANCES_M:
        methods[f"{config.DEGRADE_SIMPLIFY_PREFIX}{tolerance}"] = partial(simplify_line, tolerance_m=tolerance)
    return methods


def mean_spacing(geoms: Sequence[BaseGeometry]) -> float:
    """Spaziatura media ponderata sulla lunghezza: lunghezza totale / numero totale di segmenti."""
    stats = [common.line_stats(geom) for geom in geoms]
    length = sum(s["lunghezza_m"] for s in stats)
    segments = sum(s["lunghezza_m"] / s["spaziatura_media_m"] for s in stats)
    return round(length / segments, config.METERS_DECIMALS)


def dense_tappe(tratte: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Tratte a tappa dense (spaziatura media <= soglia) di MiTur; se sono troppo poche si aggiungono quelle cdi.

    La colonna fonte dice da dove viene ogni tratta.
    """
    dense = tratte[(tratte["tipo"] == config.TIPO_TAPPA) & (tratte["spaziatura_media_m"] <= config.CALIBRATION_DENSE_SPACING_M)]
    pool = dense[dense["fonte"] == config.MITUR_FONTE]
    if len(pool) < config.CALIBRATION_DENSE_MIN_TRATTE:
        pool = pd.concat([pool, dense[dense["fonte"] == config.CDI_FONTE]])
    if pool.empty:
        raise ValueError(f"nessuna tratta a tappa con spaziatura media <= {config.CALIBRATION_DENSE_SPACING_M} m")
    return pool.reset_index(drop=True)


def line_pairs(lines: gpd.GeoSeries, cammino_of: pd.Series, comuni: gpd.GeoDataFrame) -> pd.DataFrame:
    """Coppie (cammino, comune) a distanza <= d massimo, calcolate tratta per tratta e riferite al cammino."""
    pairs = crossing.comuni_entro(lines, comuni, max(DISTANCES))
    pairs["gruppo"] = pairs["cammino_id"].map(cammino_of)
    return pairs[["gruppo", COMUNE, "distanza_m"]]


def run_synthetic_degradation(comuni: gpd.GeoDataFrame, tratte: gpd.GeoDataFrame) -> pd.DataFrame:
    dense = dense_tappe(tratte)
    fonte = "+".join(sorted(dense["fonte"].unique()))
    counts = dense.groupby("fonte").agg(tratte=("tratta_id", "size"), cammini=("cammino_id", "nunique"))
    print(f"B: {len(dense)} tratte a tappa dense, {dense['cammino_id'].nunique()} cammini\n{counts.to_string()}")
    original = line_pairs(dense.geometry, dense["cammino_id"], comuni)
    reference = original.loc[original["distanza_m"] == 0, ["gruppo", COMUNE]]
    details, totals, summary = [], [], []
    for method, transform in degradation_methods().items():
        degraded = gpd.GeoSeries([transform(geom) for geom in dense.geometry], index=dense.index, crs=dense.crs)
        detail = compare_sets(reference, line_pairs(degraded, dense["cammino_id"], comuni))
        details.append(detail.assign(metodo=method))
        by_d = totals_by_distance(detail).assign(metodo=method, spaziatura_media_m=mean_spacing(list(degraded)))
        totals.append(by_d.reset_index())
        summary.append(by_d[["recall", "extra_tot"]].add_prefix(f"B_{method}_"))
    _write(pd.concat(details, ignore_index=True).rename(columns={"gruppo": "cammino_id"}), config.CALIBRATION_FILES_B["dettaglio"])
    original_spacing = mean_spacing(list(dense.geometry))
    _write(pd.concat(totals, ignore_index=True).assign(fonte=fonte, spaziatura_originale_m=original_spacing), config.CALIBRATION_FILES_B["totale"])
    return pd.concat(summary, axis=1).assign(B_n_tratte=len(dense), B_n_cammini=reference["gruppo"].nunique(), B_n_riferimento=len(reference.drop_duplicates()), B_fonte=fonte)


# --- C: coppie reali cdi / MiTur ---

def cammino_names(fonte: str) -> pd.Series:
    frame = pd.read_csv(Path(config.CAMMINI_CSV_TEMPLATE.format(fonte=fonte)), encoding="utf-8")
    return frame.set_index("cammino_id")["nome"].fillna("")


def cammino_spacing(tratte: pd.DataFrame) -> pd.Series:
    """Spaziatura media del cammino: lunghezza totale / segmenti totali delle sue tratte a piedi."""
    walking = tratte[tratte["tipo"].isin(config.CROSSING_TIPI)]
    segments = walking["lunghezza_m"] / walking["spaziatura_media_m"]
    return walking["lunghezza_m"].groupby(walking["cammino_id"]).sum() / segments.groupby(walking["cammino_id"]).sum()


def sample_points(geom: BaseGeometry, step_m: float) -> tuple[np.ndarray, np.ndarray]:
    """Punti equidistanti (circa step_m) lungo ogni parte della linea, col peso in metri di ciascuno."""
    points, weights = [], []
    for part in shapely.get_parts(geom):
        count = max(2, int(np.ceil(part.length / step_m)) + 1)
        points.append(shapely.line_interpolate_point(part, np.linspace(0, part.length, count)))
        weights.append(np.full(count, part.length / count))
    return np.concatenate(points), np.concatenate(weights)


def overlap_share(first: BaseGeometry, second: BaseGeometry) -> float:
    """Quota della linea piu' corta che sta entro CALIBRATION_PAIR_NEAR_M dall'altra.

    Si campiona la linea corta invece di bufferizzare quella lunga: l'unione dei buffer di tracce con
    decine di migliaia di vertici richiede secondi per coppia, il campionamento sbaglia di meno dell'1%.
    """
    shorter, longer = sorted((first, second), key=lambda geom: geom.length)
    points, weights = sample_points(shorter, config.CALIBRATION_PAIR_SAMPLE_M)
    shapely.prepare(longer)
    near = shapely.dwithin(points, longer, config.CALIBRATION_PAIR_NEAR_M)
    return float(weights[near].sum() / weights.sum())


def candidate_pairs(cdi_names: pd.Series, mitur_names: pd.Series) -> pd.DataFrame:
    """Coppie di cammini con nomi normalizzati simili (token_sort_ratio >= soglia)."""
    scores = process.cdist(
        [normalize_name(name) for name in cdi_names], [normalize_name(name) for name in mitur_names],
        scorer=fuzz.token_sort_ratio, score_cutoff=config.CALIBRATION_PAIR_NAME_MIN_SCORE,
    )
    first, second = np.nonzero(scores)
    return pd.DataFrame({
        "cdi_id": cdi_names.index[first], "mitur_id": mitur_names.index[second], "punteggio_nome": scores[first, second],
    })


def same_cammino_pairs(lines: Mapping[str, BaseGeometry], cdi_names: pd.Series, mitur_names: pd.Series) -> pd.DataFrame:
    """Coppie provvisorie: nome simile E sovrapposizione del tracciato; servono solo alla calibrazione."""
    pairs = candidate_pairs(cdi_names[cdi_names.index.isin(lines)], mitur_names[mitur_names.index.isin(lines)])
    pairs["sovrapposizione"] = [
        round(overlap_share(lines[cdi_id], lines[mitur_id]), config.CALIBRATION_RATIO_DECIMALS)
        for cdi_id, mitur_id in zip(pairs["cdi_id"], pairs["mitur_id"])
    ]
    accepted = pairs[pairs["sovrapposizione"] >= config.CALIBRATION_PAIR_OVERLAP_MIN]
    return accepted.assign(
        nome_cdi=cdi_names.reindex(accepted["cdi_id"]).to_numpy(), nome_mitur=mitur_names.reindex(accepted["mitur_id"]).to_numpy()
    ).reset_index(drop=True)


def run_cross_source_pairs(tratte: gpd.GeoDataFrame, lines: Mapping[str, BaseGeometry], all_pairs: pd.DataFrame) -> pd.DataFrame:
    pairs = same_cammino_pairs(lines, cammino_names(config.CDI_FONTE), cammino_names(config.MITUR_FONTE))
    pairs["spaziatura_mitur_m"] = pairs["mitur_id"].map(cammino_spacing(tratte[tratte["fonte"] == config.MITUR_FONTE])).round(config.METERS_DECIMALS)
    pairs["mitur_densa"] = pairs["spaziatura_mitur_m"] <= config.CALIBRATION_DENSE_SPACING_M
    pairs.index.name = "gruppo"
    _write(pairs.reset_index(drop=True), config.CALIBRATION_FILES_C["coppie"])
    print(f"C: {len(pairs)} coppie cdi-MiTur, {int(pairs['mitur_densa'].sum())} con MiTur denso")
    labelled = pairs.reset_index()
    reference = labelled.merge(all_pairs[all_pairs["distanza_m"] == 0], left_on="mitur_id", right_on="cammino_id")[["gruppo", COMUNE]]
    candidates = labelled.merge(all_pairs, left_on="cdi_id", right_on="cammino_id")[["gruppo", COMUNE, "distanza_m"]]
    detail = compare_sets(reference, candidates).merge(labelled[["gruppo", "cdi_id", "mitur_id", "mitur_densa"]], on="gruppo")
    _write(detail, config.CALIBRATION_FILES_C["dettaglio"])
    everything = totals_by_distance(detail).add_prefix("C_")
    dense_only = totals_by_distance(detail[detail["mitur_densa"]]).add_prefix("C_densa_")
    return everything.join(dense_only)


# --- D: costo di ogni d ---

def fonte_of(cammino_ids: pd.Series) -> pd.Series:
    return cammino_ids.str.split(":").str[0]


def cost_table(all_pairs: pd.DataFrame, n_cammini: pd.Series) -> pd.DataFrame:
    """Per d: coppie totali e per fonte, aumento rispetto a d = 0, comuni medi per cammino."""
    fonte = fonte_of(all_pairs["cammino_id"])
    rows = []
    for d in DISTANCES:
        selected = all_pairs["distanza_m"] <= d
        by_fonte = fonte[selected].value_counts()
        rows.append({
            DISTANCE_INDEX: d, "coppie_totali": int(selected.sum()),
            **{f"coppie_{f}": int(by_fonte.get(f, 0)) for f in config.CALIBRATION_FONTI},
            "comuni_per_cammino_medio": round(selected.sum() / n_cammini.sum(), config.METERS_DECIMALS),
        })
    costs = pd.DataFrame(rows).set_index(DISTANCE_INDEX)
    costs["aumento_vs_0"] = costs["coppie_totali"] - costs["coppie_totali"].iloc[0]
    costs["aumento_vs_0_pct"] = (100 * costs["aumento_vs_0"] / costs["coppie_totali"].iloc[0]).round(config.METERS_DECIMALS)
    return costs


def grazing_table(all_pairs: pd.DataFrame) -> pd.DataFrame:
    """A d = 0: coppie in cui la linea entra nel comune per meno di una soglia (comuni sfiorati)."""
    inside = all_pairs[all_pairs["distanza_m"] == 0]
    fonte = fonte_of(inside["cammino_id"])
    rows = []
    for limit in config.CALIBRATION_GRAZING_M:
        short = inside["lunghezza_dentro_m"] < limit
        rows.append({
            "soglia_m": limit, "coppie_a_0": len(inside), "coppie_sotto_soglia": int(short.sum()),
            **{f"sotto_soglia_{f}": int((short & (fonte == f)).sum()) for f in config.CALIBRATION_FONTI},
            "quota": round(float(short.mean()), config.CALIBRATION_RATIO_DECIMALS),
        })
    return pd.DataFrame(rows)


def run_cost_table(lines: gpd.GeoSeries, all_pairs: pd.DataFrame) -> pd.DataFrame:
    n_cammini = fonte_of(pd.Series(lines.index)).value_counts()
    costs, grazing = cost_table(all_pairs, n_cammini), grazing_table(all_pairs)
    _write(costs.reset_index(), config.CALIBRATION_FILES_D["costi"])
    _write(grazing, config.CALIBRATION_FILES_D["grazing"])
    summary = costs.add_prefix("D_")
    for limit, count in zip(grazing["soglia_m"], grazing["coppie_sotto_soglia"]):
        summary[f"D_grazing_sotto_{limit}m"] = pd.Series({0: count})
    summary["D_n_cammini"] = int(n_cammini.sum())
    return summary


def run() -> None:
    comuni = crossing.load_comuni()
    tratte = common.read_tratte(config.CALIBRATION_FONTI)
    lines = crossing.dissolve_cammini(tratte)
    all_pairs = crossing.comuni_entro(lines, comuni, max(DISTANCES))
    line_map = dict(lines.items())
    parts = [
        run_published_endpoints(comuni, tratte[tratte["fonte"] == config.CDI_FONTE], line_map),
        run_synthetic_degradation(comuni, tratte),
        run_cross_source_pairs(tratte, line_map, all_pairs),
        run_cost_table(lines, all_pairs),
    ]
    summary = pd.concat(parts, axis=1)
    _write(summary.reset_index(), config.CALIBRATION_FILE_SINTESI)
    print(summary.T.to_string())
