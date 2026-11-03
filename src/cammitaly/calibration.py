"""Calibrazione della distanza d di attraversamento: produce i numeri con cui il committente sceglie d.

A: i luoghi pubblicati come estremi di tappa contro le linee dei cammini.
B: tracce dense degradate (vertici, errore laterale), per vedere quanto la precisione della traccia sposta i comuni.
C: le stesse tracce da due fonti (cdi e MiTur), a parita' di lunghezza.
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
from cammitaly.geometry.common import normalize_name

COMUNE = config.COMUNE_ID_COL
DISTANCES = config.CALIBRATION_DISTANCES_M
DISTANCE_INDEX = "d_m"
RATIO_DECIMALS = config.CALIBRATION_RATIO_DECIMALS
Degradation = Callable[[Sequence[BaseGeometry]], list[BaseGeometry]]


def _write(frame: pd.DataFrame, name: str) -> None:
    config.CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    frame.to_csv(config.CALIBRATION_DIR / name, index=False, encoding="utf-8", lineterminator="\n")


def share_within(values: pd.Series, d: float) -> float:
    """Quota dei valori <= d; NaN se non ci sono valori."""
    return round(float((values <= d).mean()), RATIO_DECIMALS) if len(values) else float("nan")


def _by_distance(columns: Mapping[str, Sequence[float]]) -> pd.DataFrame:
    return pd.DataFrame(columns, index=pd.Index(DISTANCES, name=DISTANCE_INDEX))


# --- Nomi dei luoghi: confronto con i comuni ISTAT ---

def split_pieces(text: str, pattern: str) -> list[str]:
    """Il testo intero e i pezzi che si ottengono dividendolo sul pattern, senza duplicati ne' pezzi vuoti."""
    pieces = [text, *re.split(pattern, text)]
    return list(dict.fromkeys(piece.strip() for piece in pieces if piece.strip()))


def place_tokens(text: str) -> list[str]:
    return [token for token in normalize_name(text).split() if token not in config.PLACE_STOPWORDS]


def keys_from_tokens(tokens: Sequence[str]) -> tuple[str, str]:
    """Chiave stretta (senza spazi ne' articoli) e chiave con "S.", "Sant", "Santa" ecc. ricondotti a "San".

    "Sant'Angelo" e "Santangelo" coincidono nella prima, "S. Giovanni" e "San Giovanni" nella seconda.
    """
    saints = (config.PLACE_SAINT_CANONICAL if token in config.PLACE_SAINT_TOKENS else token for token in tokens)
    return "".join(tokens), "".join(saints)


def place_keys(text: str) -> tuple[str, str]:
    return keys_from_tokens(place_tokens(text))


Table = dict[str, dict[str, int]]  # chiave -> {PRO_COM_T: COD_REG}


@dataclass(frozen=True)
class PlaceIndex:
    tiers: tuple[Table, Table]  # un livello per tipo di chiave: nome intero
    prefixes: tuple[Table, Table]  # lo stesso, ma per gli inizi propri del nome (parole intere)


def _add(tables: tuple[Table, Table], keys: tuple[str, str], pro_com: str, region: int) -> None:
    for table, key in zip(tables, keys):
        if key:
            table.setdefault(key, {})[pro_com] = region


def build_place_index(comuni: pd.DataFrame) -> PlaceIndex:
    """Indice dei nomi comunali; i nomi bilingui (Bolzano/Bozen) entrano con ciascuna delle due parti."""
    tiers: tuple[Table, Table] = ({}, {})
    prefixes: tuple[Table, Table] = ({}, {})
    bilingual = re.escape(config.ISTAT_BILINGUAL_SEPARATOR)
    for pro_com, name, region in zip(comuni[COMUNE], comuni[config.COMUNE_NAME_COL], comuni[config.COMUNE_REGION_COL]):
        for piece in split_pieces(name, bilingual):
            tokens = place_tokens(piece)
            _add(tiers, keys_from_tokens(tokens), pro_com, int(region))
            for size in range(1, len(tokens)):
                _add(prefixes, keys_from_tokens(tokens[:size]), pro_com, int(region))
    return PlaceIndex(tiers, prefixes)


def _in_regions(candidates: Mapping[str, int], regions: frozenset[int] | None) -> set[str]:
    return {pro_com for pro_com, region in candidates.items() if regions is None or region in regions}


def _resolve_piece(index: PlaceIndex, piece: str, regions: frozenset[int] | None) -> tuple[set[str], bool]:
    """Comuni del pezzo al primo livello che ne trova e se un altro comune delle regioni inizia con quel nome."""
    for table, prefixes, key in zip(index.tiers, index.prefixes, place_keys(piece)):
        found = _in_regions(table.get(key, {}), regions)
        if found:
            return found, bool(_in_regions(prefixes.get(key, {}), regions) - found)
    return set(), False


def match_place(text: object, index: PlaceIndex, regions: frozenset[int] | None) -> tuple[str, str | None]:
    """(esito, PRO_COM_T); solo ok porta il comune. Gli altri esiti scartano il nome: vedi i MATCH_* in config."""
    if not isinstance(text, str) or not text.strip():
        return config.MATCH_EMPTY, None
    plain = re.sub(config.NAME_DASHES_PATTERN, "-", text)  # i trattini tipografici dividono come quelli ASCII
    resolved = [_resolve_piece(index, piece, regions) for piece in split_pieces(plain, config.PLACE_SPLIT_PATTERN)]
    found = set().union(*(comuni for comuni, _ in resolved))
    if not found:
        return config.MATCH_NOT_FOUND, None
    if len(found) > 1:
        same_piece = any(len(comuni) > 1 for comuni, _ in resolved)
        return (config.MATCH_HOMONYM if same_piece else config.MATCH_TWO_PLACES), None
    if any(longer for _, longer in resolved):
        return config.MATCH_PREFIX, None
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


def _match_groups(matched: pd.DataFrame) -> Iterator[tuple[str, str, pd.DataFrame]]:
    for (origine, ruolo), subset in matched.groupby(["origine", "ruolo"]):
        yield origine, ruolo, subset
    for origine, subset in matched.groupby("origine"):
        yield origine, "tutti", subset
    yield "tutte", "tutti", matched


def match_rates(matched: pd.DataFrame) -> pd.DataFrame:
    """Esiti per origine e ruolo, poi per origine e in totale; quota_ok sui nomi non vuoti."""
    outcomes = [
        config.MATCH_OK, config.MATCH_HOMONYM, config.MATCH_TWO_PLACES, config.MATCH_PREFIX,
        config.MATCH_NOT_FOUND, config.MATCH_EMPTY,
    ]
    rows = []
    for origine, ruolo, subset in _match_groups(matched):
        counts = subset["esito"].value_counts()
        row = {"origine": origine, "ruolo": ruolo, **{o: int(counts.get(o, 0)) for o in outcomes}, "n_nomi": len(subset)}
        row["quota_ok"] = round(row[config.MATCH_OK] / max(len(subset) - row[config.MATCH_EMPTY], 1), RATIO_DECIMALS)
        rows.append(row)
    return pd.DataFrame(rows)


def pair_distances(
    keys: pd.Series, comuni_ids: pd.Series, lines: Mapping[str, BaseGeometry], comune_geoms: Mapping[str, BaseGeometry]
) -> pd.Series:
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
    """Distribuzione delle distanze delle coppie distinte; quelle senza linea non si misurano e si contano a parte."""
    values = distances.dropna()
    row: dict[str, object] = {
        "ambito": scope, "n_coppie": len(distances), "n_senza_linea": int(distances.isna().sum()),
        "n_misurate": len(values), "quota_a_0": share_within(values, 0),
    }
    for percentile in config.CALIBRATION_PERCENTILES:
        row[f"p{percentile}"] = round(float(np.percentile(values, percentile)), config.METERS_DECIMALS)
    row["max"] = round(float(values.max()), config.METERS_DECIMALS)
    return row


def far_stage_motive(ok: pd.DataFrame) -> pd.Series:
    """Per riga: MOTIVO_BRANCH se nel suo cammino le altre tappe sono tutte a distanza 0 e le lontane sono un blocco
    contiguo (per numero di tappa) di almeno CALIBRATION_BRANCH_MIN_STAGES tappe; altrimenti stringa vuota."""
    motive = pd.Series("", index=ok.index)
    measured = ok.dropna(subset=["ordine", "dist_cammino_m"])
    for _, group in measured.groupby("cammino_id"):
        worst = group.groupby("ordine")["dist_cammino_m"].max()
        far = (worst > config.CALIBRATION_FAR_M).to_numpy()
        positions = np.flatnonzero(far)
        contiguous = len(positions) >= config.CALIBRATION_BRANCH_MIN_STAGES and bool((np.diff(positions) == 1).all())
        if contiguous and bool((worst[~far] == 0).all()):
            motive[group.index[group["ordine"].isin(worst.index[far])]] = config.MOTIVO_BRANCH
    return motive


def far_pairs(ok: pd.DataFrame) -> pd.DataFrame:
    """Coppie di riferimento oltre CALIBRATION_FAR_M (per cammino o per tratta), una riga per coppia, dalla piu' lontana."""
    far = ok[(ok["dist_cammino_m"] > config.CALIBRATION_FAR_M) | (ok["dist_tratta_m"] > config.CALIBRATION_FAR_M)]
    grouped = far.groupby(["cammino_id", COMUNE, "tratta_id"], dropna=False, as_index=False)
    unique = grouped.agg(
        cammino=("cammino", "first"), origine=("origine", "first"), ruolo=("ruolo", "first"), nome=("nome", "first"),
        comune=("comune", "first"), dist_cammino_m=("dist_cammino_m", "first"), dist_tratta_m=("dist_tratta_m", "first"),
        motivo=("motivo", "first"), n_citazioni=("nome", "size"),
    )
    unique["massima_m"] = unique[["dist_cammino_m", "dist_tratta_m"]].max(axis=1)
    return unique.sort_values("massima_m", ascending=False, ignore_index=True)


def run_published_endpoints(
    comuni: gpd.GeoDataFrame, tratte_cdi: gpd.GeoDataFrame, lines: Mapping[str, BaseGeometry]
) -> pd.DataFrame:
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
    ok["motivo"] = far_stage_motive(ok)
    return _publish_endpoint_outputs(matched, ok)


def _publish_endpoint_outputs(matched: pd.DataFrame, ok: pd.DataFrame) -> pd.DataFrame:
    extra = ok[["comune", "dist_cammino_m", "dist_tratta_m"]]
    _write(matched.merge(extra, left_index=True, right_index=True, how="left"), config.CALIBRATION_FILES_A["riferimenti"])
    rates = match_rates(matched)
    _write(rates, config.CALIBRATION_FILES_A["match"])
    per_cammino = ok.drop_duplicates(["cammino_id", COMUNE])
    per_tratta = ok.dropna(subset=["tratta_id"]).drop_duplicates(["tratta_id", COMUNE])
    distributions = [
        distance_distribution(per_cammino["dist_cammino_m"], "cammino"),
        distance_distribution(per_tratta["dist_tratta_m"], "tratta"),
    ]
    _write(pd.DataFrame(distributions), config.CALIBRATION_FILES_A["distribuzione"])
    _write(far_pairs(ok), config.CALIBRATION_FILES_A["lontane"])
    print(rates.to_string(index=False))
    without_line = per_cammino[per_cammino["dist_cammino_m"].isna()]
    measured_cammino = per_cammino["dist_cammino_m"].dropna()
    measured_tratta = per_tratta["dist_tratta_m"].dropna()
    return _by_distance({
        "A_n_coppie_cammino": [len(measured_cammino)] * len(DISTANCES),
        "A_recall_cammino": [share_within(measured_cammino, d) for d in DISTANCES],
        "A_n_coppie_tratta": [len(measured_tratta)] * len(DISTANCES),
        "A_recall_tratta": [share_within(measured_tratta, d) for d in DISTANCES],
        "A_n_senza_linea_coppie": [len(without_line)] * len(DISTANCES),
        "A_n_senza_linea_cammini": [without_line["cammino_id"].nunique()] * len(DISTANCES),
    })


# --- Confronto tra insiemi di comuni (usato da B e C) ---

def compare_sets(reference: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    """Per gruppo e per ogni d: comuni di riferimento, ritrovati e in piu'.

    reference: colonne gruppo, PRO_COM_T. candidates: gruppo, PRO_COM_T, distanza_m, dentro (una o piu' righe per coppia).
    """
    ref = reference.drop_duplicates()
    best = candidates.groupby(["gruppo", COMUNE], as_index=False).agg(distanza_m=("distanza_m", "min"), dentro=("dentro", "any"))
    sizes = ref.groupby("gruppo").size().rename("n_riferimento")
    frames = []
    for d in DISTANCES:
        found = crossing.within_distance(best, d)
        hits = found.merge(ref, on=["gruppo", COMUNE]).groupby("gruppo").size()
        frame = sizes.to_frame()
        frame[DISTANCE_INDEX] = d
        frame["n_trovati"] = hits.reindex(frame.index, fill_value=0)
        frame["n_extra"] = found.groupby("gruppo").size().reindex(frame.index, fill_value=0) - frame["n_trovati"]
        frames.append(frame)
    detail = pd.concat(frames).reset_index()
    detail["recall"] = (detail["n_trovati"] / detail["n_riferimento"]).round(RATIO_DECIMALS)
    return detail


def totals_by_distance(detail: pd.DataFrame) -> pd.DataFrame:
    """Per d: recall totale (ritrovati / riferimenti), recall medio per gruppo, extra totale e medio per gruppo."""
    grouped = detail.groupby(DISTANCE_INDEX)
    return pd.DataFrame({
        "n_gruppi": grouped.size(),
        "recall": (grouped["n_trovati"].sum() / grouped["n_riferimento"].sum()).round(RATIO_DECIMALS),
        "recall_media_gruppo": grouped["recall"].mean().round(RATIO_DECIMALS),
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


def _part_coords(geom: BaseGeometry) -> list[np.ndarray]:
    return [np.asarray(part.coords)[:, :2] for part in shapely.get_parts(geom)]


def subsample_every_k(geom: BaseGeometry, target_spacing_m: float) -> BaseGeometry:
    """Tiene un vertice ogni k, con k scelto perche' la spaziatura media diventi circa target_spacing_m."""
    spacing = common.line_stats(geom)["spaziatura_media_m"]
    step = max(1, round(target_spacing_m / spacing))
    return _lines_from_parts([subsample_part(coords, step) for coords in _part_coords(geom)])


def simplify_line(geom: BaseGeometry, tolerance_m: float) -> BaseGeometry:
    # Senza preserve_topology: serve la semplificazione di Douglas-Peucker pura, come un tracciato "pulito" a mano
    return geom.simplify(tolerance_m, preserve_topology=False)


def jitter_line(geom: BaseGeometry, sigma_m: float, rng: np.random.Generator) -> BaseGeometry:
    """Sposta ogni vertice di un errore gaussiano indipendente (sigma_m in est e in nord)."""
    return _lines_from_parts([coords + rng.normal(0.0, sigma_m, coords.shape) for coords in _part_coords(geom)])


def shift_line(geom: BaseGeometry, offset_m: float) -> BaseGeometry:
    """Linea traslata di lato (positivo = a sinistra); giunti arrotondati per non creare punte agli angoli."""
    shifted = shapely.offset_curve(geom, offset_m, join_style="round")
    return geom if shifted.is_empty else shifted


def _each(func: Callable[[BaseGeometry], BaseGeometry]) -> Degradation:
    return lambda geoms: [func(geom) for geom in geoms]


def _jitter_all(geoms: Sequence[BaseGeometry], sigma_m: float, seed: int) -> list[BaseGeometry]:
    rng = np.random.default_rng(seed)  # un generatore per metodo: stesso seme, stesso disturbo
    return [jitter_line(geom, sigma_m, rng) for geom in geoms]


def degradation_methods() -> dict[str, Degradation]:
    """Nome -> funzione che degrada tutte le linee. k, s: densita' dei vertici; j, o: errore laterale (o = un lato)."""
    methods: dict[str, Degradation] = {}
    for spacing in config.CALIBRATION_SUBSAMPLE_SPACINGS_M:
        methods[f"{config.DEGRADE_SUBSAMPLE_PREFIX}{spacing}"] = _each(partial(subsample_every_k, target_spacing_m=spacing))
    for tolerance in config.CALIBRATION_SIMPLIFY_TOLERANCES_M:
        methods[f"{config.DEGRADE_SIMPLIFY_PREFIX}{tolerance}"] = _each(partial(simplify_line, tolerance_m=tolerance))
    for sigma in config.CALIBRATION_JITTER_SIGMAS_M:
        methods[f"{config.DEGRADE_JITTER_PREFIX}{sigma}"] = partial(_jitter_all, sigma_m=sigma, seed=config.CALIBRATION_SEED)
    for offset in config.CALIBRATION_SHIFT_OFFSETS_M:
        for side, sign in config.SHIFT_SIDES.items():
            methods[f"{config.DEGRADE_SHIFT_PREFIX}{offset}{side}"] = _each(partial(shift_line, offset_m=sign * offset))
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
    return pairs[["gruppo", COMUNE, "distanza_m", "dentro", "lunghezza_dentro_m"]]


def reference_sets(original: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Riferimento R (comuni in cui la traccia densa entra) e sottoinsieme a rischio (dentro per meno di MARGINAL_M)."""
    inside = original[original["dentro"]].groupby(["gruppo", COMUNE], as_index=False)["lunghezza_dentro_m"].sum()
    marginal = inside[inside["lunghezza_dentro_m"] < config.CALIBRATION_MARGINAL_M]
    return inside[["gruppo", COMUNE]], marginal[["gruppo", COMUNE]]


def worse_side(summaries: Mapping[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Unisce i due lati di ogni traslazione: recall minimo ed extra massimo, cioe' il lato peggiore per metrica."""
    merged: dict[str, list[pd.DataFrame]] = {}
    for method, frame in summaries.items():
        base = method[:-2] if method[-2:] in config.SHIFT_SIDES else method
        merged.setdefault(base, []).append(frame)
    worst = {"recall": "min", "extra_tot": "max", "recall_marginale": "min"}
    return {base: pd.concat(frames).groupby(level=0).agg(worst) for base, frames in merged.items()}


def run_synthetic_degradation(comuni: gpd.GeoDataFrame, tratte: gpd.GeoDataFrame) -> pd.DataFrame:
    dense = dense_tappe(tratte)
    fonte = "+".join(sorted(dense["fonte"].unique()))
    counts = dense.groupby("fonte").agg(tratte=("tratta_id", "size"), cammini=("cammino_id", "nunique"))
    print(f"B: {len(dense)} tratte a tappa dense, {dense['cammino_id'].nunique()} cammini\n{counts.to_string()}")
    reference, marginal = reference_sets(line_pairs(dense.geometry, dense["cammino_id"], comuni))
    details, totals, summaries = [], [], {}
    for method, degrade in degradation_methods().items():
        degraded = gpd.GeoSeries(degrade(list(dense.geometry)), index=dense.index, crs=dense.crs)
        candidates = line_pairs(degraded, dense["cammino_id"], comuni)
        detail, marginal_detail = compare_sets(reference, candidates), compare_sets(marginal, candidates)
        details += [detail.assign(metodo=method, insieme="tutti"), marginal_detail.assign(metodo=method, insieme="marginali")]
        by_d = totals_by_distance(detail)
        by_d["recall_marginale"] = totals_by_distance(marginal_detail)["recall"]
        summaries[method] = by_d[["recall", "extra_tot", "recall_marginale"]]
        totals.append(by_d.assign(metodo=method, spaziatura_media_m=mean_spacing(list(degraded))).reset_index())
    _write(pd.concat(details, ignore_index=True).rename(columns={"gruppo": "cammino_id"}), config.CALIBRATION_FILES_B["dettaglio"])
    original_spacing = mean_spacing(list(dense.geometry))
    totals_frame = pd.concat(totals, ignore_index=True).assign(fonte=fonte, spaziatura_originale_m=original_spacing)
    _write(totals_frame, config.CALIBRATION_FILES_B["totale"])
    merged = {base: frame.add_prefix(f"B_{base}_") for base, frame in worse_side(summaries).items()}
    return pd.concat(merged.values(), axis=1).assign(
        B_n_tratte=len(dense), B_n_cammini=reference["gruppo"].nunique(), B_n_riferimento=len(reference),
        B_n_marginali=len(marginal), B_fonte=fonte,
    )


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
    """Coppie provvisorie: nome simile E sovrapposizione del tracciato E rapporto di lunghezza, con le misure in colonna."""
    pairs = candidate_pairs(cdi_names[cdi_names.index.isin(lines)], mitur_names[mitur_names.index.isin(lines)])
    pairs["sovrapposizione"] = [
        round(overlap_share(lines[cdi_id], lines[mitur_id]), RATIO_DECIMALS)
        for cdi_id, mitur_id in zip(pairs["cdi_id"], pairs["mitur_id"])
    ]
    accepted = pairs[pairs["sovrapposizione"] >= config.CALIBRATION_PAIR_OVERLAP_MIN]
    accepted = accepted.assign(
        nome_cdi=cdi_names.reindex(accepted["cdi_id"]).to_numpy(), nome_mitur=mitur_names.reindex(accepted["mitur_id"]).to_numpy()
    ).reset_index(drop=True)
    low, high = config.CALIBRATION_PAIR_LENGTH_RATIO
    accepted["rapporto_lunghezza"] = [
        round(lines[cdi_id].length / lines[mitur_id].length, RATIO_DECIMALS)
        for cdi_id, mitur_id in zip(accepted["cdi_id"], accepted["mitur_id"])
    ]
    accepted["nel_rapporto"] = accepted["rapporto_lunghezza"].between(low, high)
    return accepted


def run_cross_source_pairs(tratte: gpd.GeoDataFrame, lines: Mapping[str, BaseGeometry], all_pairs: pd.DataFrame) -> pd.DataFrame:
    pairs = same_cammino_pairs(lines, cammino_names(config.CDI_FONTE), cammino_names(config.MITUR_FONTE))
    mitur_spacing = cammino_spacing(tratte[tratte["fonte"] == config.MITUR_FONTE])
    pairs["spaziatura_mitur_m"] = pairs["mitur_id"].map(mitur_spacing).round(config.METERS_DECIMALS)
    pairs["mitur_densa"] = pairs["spaziatura_mitur_m"] <= config.CALIBRATION_DENSE_SPACING_M
    _write(pairs, config.CALIBRATION_FILES_C["coppie"])
    kept = int(pairs["nel_rapporto"].sum())
    dense = int(pairs["mitur_densa"].sum())
    print(f"C: {len(pairs)} coppie cdi-MiTur, {kept} con rapporto di lunghezza nei limiti, {dense} con MiTur denso")
    labelled = pairs.rename_axis("gruppo").reset_index()
    reference = labelled.merge(all_pairs[all_pairs["dentro"]], left_on="mitur_id", right_on="cammino_id")[["gruppo", COMUNE]]
    candidates = labelled.merge(all_pairs, left_on="cdi_id", right_on="cammino_id")[["gruppo", COMUNE, "distanza_m", "dentro"]]
    meta = labelled[["gruppo", "cdi_id", "mitur_id", "mitur_densa", "nel_rapporto"]]
    detail = compare_sets(reference, candidates).merge(meta, on="gruppo")
    _write(detail, config.CALIBRATION_FILES_C["dettaglio"])
    within = detail[detail["nel_rapporto"]]
    return (
        totals_by_distance(within).add_prefix("C_")
        .join(totals_by_distance(within[within["mitur_densa"]]).add_prefix("C_densa_"))
        .join(totals_by_distance(detail)[["n_gruppi", "recall", "extra_tot"]].add_prefix("C_tutte_"))
    )


# --- D: costo di ogni d ---

def fonte_of(cammino_ids: pd.Series) -> pd.Series:
    return cammino_ids.str.split(config.ID_SEPARATOR).str[0]


def cost_table(all_pairs: pd.DataFrame, n_cammini: pd.Series) -> pd.DataFrame:
    """Per d: coppie totali e per fonte, aumento rispetto a d = 0, comuni medi per cammino (totale e per fonte)."""
    rows = []
    for d in DISTANCES:
        selected = crossing.within_distance(all_pairs, d)
        by_fonte = fonte_of(selected["cammino_id"]).value_counts()
        row: dict[str, object] = {DISTANCE_INDEX: d, "coppie_totali": len(selected)}
        for fonte in config.CALIBRATION_FONTI:
            row[f"coppie_{fonte}"] = int(by_fonte.get(fonte, 0))
            row[f"comuni_per_cammino_{fonte}"] = round(row[f"coppie_{fonte}"] / n_cammini[fonte], config.METERS_DECIMALS)
        rows.append(row)
    costs = pd.DataFrame(rows).set_index(DISTANCE_INDEX)
    costs["aumento_vs_0"] = costs["coppie_totali"] - costs["coppie_totali"].iloc[0]
    costs["aumento_vs_0_pct"] = (100 * costs["aumento_vs_0"] / costs["coppie_totali"].iloc[0]).round(config.METERS_DECIMALS)
    return costs


def grazing_table(all_pairs: pd.DataFrame) -> pd.DataFrame:
    """A d = 0: coppie in cui la linea entra nel comune per meno di una soglia (comuni sfiorati)."""
    inside = crossing.within_distance(all_pairs, 0)
    fonte = fonte_of(inside["cammino_id"])
    rows = []
    for limit in config.CALIBRATION_GRAZING_M:
        short = inside["lunghezza_dentro_m"] < limit
        rows.append({
            "soglia_m": limit, "coppie_a_0": len(inside), "coppie_sotto_soglia": int(short.sum()),
            **{f"sotto_soglia_{f}": int((short & (fonte == f)).sum()) for f in config.CALIBRATION_FONTI},
            "quota": round(float(short.mean()), RATIO_DECIMALS),
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
