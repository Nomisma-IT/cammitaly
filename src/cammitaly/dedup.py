"""Deduplicazione dei cammini di camminiditalia.org e del Catalogo MiTur: test della regola del committente.

Regola: due cammini sono lo stesso se i nomi sono simili E almeno DEDUP_OVERLAP_MIN del piu' corto sta entro
DEDUP_NEAR_M metri dall'altro. Con una sola condizione vera la coppia e' in limbo. Si confrontano tutte le coppie,
anche dentro la stessa fonte. Le uscite sono in config.DEDUP_DIR; nulla qui cambia i cammini o le tratte.
"""
from collections import defaultdict
from collections.abc import Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from rapidfuzz import fuzz
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from cammitaly import config, crossing, files, matching
from cammitaly.geometry import common

COMUNE = config.COMUNE_ID_COL
Lines = Mapping[str, BaseGeometry]
Shares = dict[tuple[str, str], list[float]]


def write_outputs(outputs: Mapping[str, pd.DataFrame]) -> None:
    """Scrive ogni uscita in DEDUP_DIR; il limbo con lo stesso scrittore e contratto di limbo_<fonte>.csv, le altre come tabelle."""
    for name, frame in outputs.items():
        path = config.DEDUP_DIR / config.DEDUP_FILES[name]
        if name == "limbo":
            files.write_csv(path, config.LIMBO_FIELDS, frame.to_dict("records"))
        else:
            common.write_frame(frame, path)


# --- Dati di partenza ---

def load_catalogue() -> pd.DataFrame:
    """Tutti i cammini delle fonti, prima camminiditalia poi MiTur, ciascuna per id, con la chiave del nome."""
    columns = ["cammino_id", "fonte", "nome", "stato_geometria"]
    frames = [
        pd.DataFrame(common.read_csv(Path(config.CAMMINI_CSV_TEMPLATE.format(fonte=fonte))))[columns].sort_values("cammino_id")
        for fonte in config.DEDUP_FONTI
    ]
    catalogue = pd.concat(frames, ignore_index=True)
    catalogue["chiave"] = catalogue["nome"].map(matching.name_key)
    return catalogue


def cammino_lines(tratte: gpd.GeoDataFrame) -> dict[str, BaseGeometry]:
    """Una linea per cammino: unione di tutte le tratte dei tipi DEDUP_TIPI, con le parti riunite dove si toccano.

    L'unione spezza le linee a ogni incrocio; riunirle da' parti lunghe (meno punti campionati a vuoto, segmenti lontani continui).
    """
    dissolved = crossing.dissolve_cammini(tratte, config.DEDUP_TIPI)
    return {cammino_id: shapely.line_merge(geom) for cammino_id, geom in dissolved.items()}


# --- Quote di sovrapposizione ---

def _shares_on_target(points: np.ndarray, weights: np.ndarray, target: BaseGeometry, nears_desc: Sequence[float]) -> list[float]:
    """Quota dei punti entro ciascuna distanza; dalla piu' grande, cosi' i punti gia' lontani non si ricontrollano."""
    alive = np.arange(len(points))
    shares = []
    for near in nears_desc:
        if alive.size:
            alive = alive[matching.points_near(points[alive], target, near)]
        shares.append(float(weights[alive].sum() / weights.sum()))
    return shares


def overlap_shares(lines: Lines, near_values: Sequence[float]) -> Shares:
    """(sorgente, bersaglio) -> quota della sorgente entro ciascuna distanza, in ordine decrescente di distanza.

    Solo per le coppie i cui riquadri, allargati della distanza massima, si toccano: per le altre la quota e' 0.
    """
    ids = list(lines)
    geoms = np.array([lines[cammino_id] for cammino_id in ids], dtype=object)
    nears_desc = sorted(near_values, reverse=True)
    min_x, min_y, max_x, max_y = shapely.bounds(geoms).T
    tree = shapely.STRtree(shapely.box(min_x, min_y, max_x, max_y))
    grown = shapely.box(min_x - nears_desc[0], min_y - nears_desc[0], max_x + nears_desc[0], max_y + nears_desc[0])
    source, target = tree.query(grown, predicate="intersects")
    neighbours: dict[int, list[int]] = defaultdict(list)
    for s, t in zip(source, target):
        if s != t:
            neighbours[int(s)].append(int(t))
    shares: Shares = {}
    for s, targets in neighbours.items():
        points, weights = matching.sample_points(geoms[s], config.MATCH_SAMPLE_M)
        for t in targets:
            shares[(ids[s], ids[t])] = _shares_on_target(points, weights, geoms[t], nears_desc)
    return shares


def share_column(direction: str, near_m: float) -> str:
    return f"quota_{direction}_{near_m:g}"


def pair_table(catalogue: pd.DataFrame, lines: Lines, near_values: Sequence[float]) -> pd.DataFrame:
    """Una riga per coppia non ordinata di cammini (a prima di b nel catalogo), con nomi, lunghezze e quote per ogni distanza.

    Le quote sono NaN se uno dei due non ha geometria, 0 se entrambi l'hanno ma i riquadri non si toccano.
    """
    ids = catalogue["cammino_id"].to_numpy()
    first, second = np.triu_indices(len(ids), 1)
    keys = catalogue["chiave"].tolist()
    lengths = np.array([lines[cammino_id].length if cammino_id in lines else np.nan for cammino_id in ids])
    set_scores, sort_scores = matching.name_scores(keys, keys), matching.name_scores(keys, keys, fuzz.token_sort_ratio)
    pairs = pd.DataFrame({"cammino_a": ids[first], "cammino_b": ids[second]})
    for suffix, order in (("a", first), ("b", second)):
        for column, source in (("fonte", "fonte"), ("nome", "nome"), ("stato", "stato_geometria")):
            pairs[f"{column}_{suffix}"] = catalogue[source].to_numpy()[order]
        pairs[f"lunghezza_{suffix}_m"] = lengths[order]
    pairs["punteggio_nome"], pairs["punteggio_nome_sort"] = set_scores[first, second], sort_scores[first, second]
    pairs["nome_contenuto"] = [
        score == 100 and set(keys[a].split()) != set(keys[b].split()) for a, b, score in zip(first, second, pairs["punteggio_nome"])
    ]
    shares = overlap_shares(lines, near_values)
    nears_desc = sorted(near_values, reverse=True)
    for direction, (own, other) in {"a_in_b": ("cammino_a", "cammino_b"), "b_in_a": ("cammino_b", "cammino_a")}.items():
        for position, near in enumerate(nears_desc):
            pairs[share_column(direction, near)] = [
                shares.get((s, t), [0.0] * len(nears_desc))[position] if s in lines and t in lines else np.nan
                for s, t in zip(pairs[own], pairs[other])
            ]
    return pairs


def minor_is_a(pairs: pd.DataFrame, near_m: float) -> pd.Series:
    """Vero dove la linea "minore" e' quella di a: la piu' corta; a parita' esatta di lunghezza quella con la quota
    piu' bassa (a se sono uguali). Cosi' lo scambio di a e b non cambia la quota ne' la classe."""
    shorter_a = pairs["lunghezza_a_m"] < pairs["lunghezza_b_m"]
    tie = pairs["lunghezza_a_m"] == pairs["lunghezza_b_m"]
    return shorter_a | (tie & (pairs[share_column("a_in_b", near_m)] <= pairs[share_column("b_in_a", near_m)]))


def minor_share(pairs: pd.DataFrame, near_m: float) -> pd.Series:
    """Quota della linea piu' corta entro near_m dall'altra; a parita' esatta di lunghezza la piu' bassa delle due (regola prudente).

    Vuota se una delle due geometrie manca.
    """
    return pairs[share_column("a_in_b", near_m)].where(minor_is_a(pairs, near_m), pairs[share_column("b_in_a", near_m)])


# --- Classi ---

def classify(pairs: pd.DataFrame, name_min: float, overlap_min: float, near_m: float) -> pd.DataFrame:
    """Colonne classe e motivo di ogni coppia con le soglie date (la regola D-2 e la griglia di sensibilita' usano questa)."""
    quota = minor_share(pairs, near_m)
    similar, known, coincident = pairs["punteggio_nome"] >= name_min, quota.notna(), quota >= overlap_min
    reasons = [
        (similar & coincident, config.CLASSE_DUPLICATO, config.MOTIVO_DUPLICATO),
        (similar & known & ~coincident, config.CLASSE_LIMBO, config.MOTIVO_NOME_SI_TRACCIATO_NO),
        (similar & ~known, config.CLASSE_LIMBO, config.MOTIVO_NOME_SI_GEOMETRIA_ASSENTE),
        (~similar & coincident, config.CLASSE_LIMBO, config.MOTIVO_TRACCIATO_SI_NOME_NO),
        (~similar & known & ~coincident, config.CLASSE_DISTINTO, config.MOTIVO_DISTINTO),
        (~similar & ~known, config.CLASSE_DISTINTO, config.MOTIVO_DISTINTO_GEOMETRIA_ASSENTE),
    ]
    conditions = [condition.to_numpy() for condition, _, _ in reasons]
    return pd.DataFrame({
        "classe": np.select(conditions, [classe for _, classe, _ in reasons], default=""),
        "motivo": np.select(conditions, [motivo for _, _, motivo in reasons], default=""),
    }, index=pairs.index)


def coppie_frame(pairs: pd.DataFrame) -> pd.DataFrame:
    """coppie.csv: i candidati (nome simile alla soglia piu' bassa della griglia, o tracciati che si toccano) con la classe D-2."""
    near = config.DEDUP_NEAR_M
    quota_minore = minor_share(pairs, near)
    # minimum/maximum propagano NaN: senza una delle due geometrie il rapporto resta vuoto
    shorter, longer = np.minimum(pairs["lunghezza_a_m"], pairs["lunghezza_b_m"]), np.maximum(pairs["lunghezza_a_m"], pairs["lunghezza_b_m"])
    frame = pairs.assign(
        quota_a_in_b=pairs[share_column("a_in_b", near)], quota_b_in_a=pairs[share_column("b_in_a", near)],
        quota_minore=quota_minore, rapporto_lunghezza=shorter / longer,
    ).join(classify(pairs, config.DEDUP_NAME_MIN_SCORE, config.DEDUP_OVERLAP_MIN, near))
    frame = frame[(frame["punteggio_nome"] >= config.DEDUP_CANDIDATE_NAME_MIN) | (frame["quota_minore"] > 0)]
    rank = frame["classe"].map({config.CLASSE_DUPLICATO: 0, config.CLASSE_LIMBO: 1, config.CLASSE_DISTINTO: 2})
    frame = frame.assign(_rank=rank).sort_values(
        ["_rank", "quota_minore", "punteggio_nome"], ascending=[True, False, False], ignore_index=True
    )
    frame = frame[list(config.DEDUP_COPPIE_FIELDS)]
    ratios = ["quota_a_in_b", "quota_b_in_a", "quota_minore", "rapporto_lunghezza"]
    frame[ratios] = frame[ratios].round(config.RATIO_DECIMALS)
    frame[["lunghezza_a_m", "lunghezza_b_m"]] = frame[["lunghezza_a_m", "lunghezza_b_m"]].round(config.METERS_DECIMALS)
    frame[["punteggio_nome", "punteggio_nome_sort"]] = frame[["punteggio_nome", "punteggio_nome_sort"]].round(config.SCORE_DECIMALS)
    return frame


def _number(value: float, decimals: int, unit: str = "") -> str:
    return f"{value:.{decimals}f}{unit}" if pd.notna(value) else config.DEDUP_NOT_AVAILABLE


def limbo_rows(coppie: pd.DataFrame) -> list[dict[str, str]]:
    """Righe nel contratto del limbo (oggetto, motivo, dettaglio con i numeri) per le coppie in limbo."""
    rows = []
    for pair in coppie[coppie["classe"] == config.CLASSE_LIMBO].itertuples():
        quota = _number(pair.quota_minore, config.RATIO_DECIMALS)
        score = _number(pair.punteggio_nome, config.DEDUP_DETAIL_DECIMALS)
        km_a = _number(pair.lunghezza_a_m / 1000, config.DEDUP_DETAIL_DECIMALS, " km")
        km_b = _number(pair.lunghezza_b_m / 1000, config.DEDUP_DETAIL_DECIMALS, " km")
        detail = (
            f"{pair.nome_a} / {pair.nome_b}; punteggio nome {score} (soglia {config.DEDUP_NAME_MIN_SCORE}); "
            f"quota minore {quota} (soglia {config.DEDUP_OVERLAP_MIN}, entro {config.DEDUP_NEAR_M} m); "
            f"lunghezze {km_a} e {km_b}; geometria {pair.stato_a} e {pair.stato_b}"
        )
        rows.append(common.limbo_row(f"{pair.cammino_a}{config.DEDUP_PAIR_SEPARATOR}{pair.cammino_b}", pair.motivo, detail))
    return rows


# --- Gruppi di duplicati ---

def connected_groups(edges: Iterable[tuple[Hashable, Hashable]]) -> list[list[Hashable]]:
    """Componenti connesse del grafo non orientato dato dagli archi (solo i nodi che compaiono in un arco)."""
    parent: dict[Hashable, Hashable] = {}

    def find(node: Hashable) -> Hashable:
        while parent.setdefault(node, node) != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for first, second in edges:
        parent[find(first)] = find(second)
    groups: dict[Hashable, list[Hashable]] = defaultdict(list)
    for node in list(parent):
        groups[find(node)].append(node)
    return list(groups.values())


def gruppi_frame(catalogue: pd.DataFrame, lines: Lines, duplicates: pd.DataFrame) -> pd.DataFrame:
    """Una riga per cammino di ogni gruppo di duplicati; `catena` vale vero se non tutte le coppie del gruppo sono duplicato."""
    position = {cammino_id: n for n, cammino_id in enumerate(catalogue["cammino_id"])}
    edges = list(zip(duplicates["cammino_a"], duplicates["cammino_b"]))
    groups = sorted((sorted(group, key=position.__getitem__) for group in connected_groups(edges)), key=lambda g: position[g[0]])
    info = catalogue.set_index("cammino_id")
    rows = []
    for number, members in enumerate(groups, start=1):
        n_edges = sum(1 for a, b in edges if a in members)
        chain = n_edges < len(members) * (len(members) - 1) // 2
        for cammino_id in members:
            length = round(lines[cammino_id].length, config.METERS_DECIMALS) if cammino_id in lines else np.nan
            rows.append({
                "gruppo": config.DEDUP_GROUP_ID_FORMAT.format(number=number), "cammino_id": cammino_id, "fonte": info.at[cammino_id, "fonte"],
                "nome": info.at[cammino_id, "nome"], "stato_geometria": info.at[cammino_id, "stato_geometria"],
                "lunghezza_m": length, "catena": chain,
            })
    return pd.DataFrame(rows, columns=list(config.DEDUP_GRUPPI_FIELDS))


# --- Sensibilita' ---

def sensitivity_frame(pairs: pd.DataFrame) -> pd.DataFrame:
    """Conteggi per classe a ogni combinazione di soglia sul nome, quota minima e distanza, su tutte le coppie."""
    rows = []
    identical = pairs["punteggio_nome"] == 100
    for name_min in config.DEDUP_SENS_NAME_SCORES:
        for overlap_min in config.DEDUP_SENS_OVERLAPS:
            for near_m in config.DEDUP_SENS_NEAR_M:
                counts = classify(pairs, name_min, overlap_min, near_m)["classe"].value_counts()
                quota = minor_share(pairs, near_m)
                rows.append({
                    "soglia_nome": name_min, "soglia_quota": overlap_min, "distanza_m": near_m,
                    "n_duplicato": int(counts.get(config.CLASSE_DUPLICATO, 0)),
                    "n_limbo": int(counts.get(config.CLASSE_LIMBO, 0)),
                    "n_distinto": int(counts.get(config.CLASSE_DISTINTO, 0)),
                    "n_nome_100": int(identical.sum()),
                    "n_nome_100_tracciato_diverso": int((identical & (quota < overlap_min)).sum()),
                    "n_nome_100_geometria_assente": int((identical & quota.isna()).sum()),
                })
    return pd.DataFrame(rows, columns=list(config.DEDUP_SENSIBILITA_FIELDS))


# --- Quasi duplicati: dove il piu' corto si allontana dall'altro ---

@dataclass(frozen=True)
class Stretch:
    length_m: float
    max_distance_m: float
    middle: Point


def far_stretches(shorter: BaseGeometry, other: BaseGeometry, near_m: float, step_m: float) -> list[Stretch]:
    """Tratti consecutivi della linea corta piu' lontani di near_m dall'altra, con lunghezza, distanza massima e punto di mezzo."""
    points, weights = matching.sample_points(shorter, step_m)
    far = ~matching.points_near(points, other, near_m)
    if not far.any():
        return []
    xy = shapely.get_coordinates(points)
    jump = np.hypot(*np.diff(xy, axis=0).T) > step_m * config.DEDUP_STEP_TOLERANCE
    run = np.concatenate([[0], np.cumsum(jump | (far[1:] != far[:-1]))])
    positions = np.flatnonzero(far)
    nearest = shapely.STRtree(shapely.get_parts(other)).query_nearest(points[positions], return_distance=True, all_matches=False)
    distances = np.zeros(len(points))
    distances[positions[nearest[0][0]]] = nearest[1]
    runs = pd.DataFrame({"run": run[positions], "position": positions, "weight": weights[positions], "distance": distances[positions]})
    summary = runs.groupby("run").agg(
        start=("position", "min"), n_points=("position", "size"), length_m=("weight", "sum"), max_distance_m=("distance", "max")
    )
    return [
        Stretch(float(row.length_m), float(row.max_distance_m), points[int(row.start) + int(row.n_points) // 2])
        for row in summary.itertuples()
    ]


def nearest_tratta(point: Point, tratte: pd.DataFrame) -> tuple[str, str]:
    """(nome, tipo) della tratta piu' vicina al punto."""
    row = tratte.iloc[int(np.argmin(shapely.distance(point, tratte.geometry.to_numpy())))]
    return str(row["nome"]), str(row["tipo"])


def diagnose(max_distance_m: float, km_non_tappa: float, km_total: float) -> str:
    """Il motivo piu' probabile per cui il cammino corto esce dall'altro, in parole; le soglie sono in config."""
    if max_distance_m <= config.DEDUP_OFFSET_MAX_M:
        return config.DIAGNOSI_SFASAMENTO
    if km_non_tappa > km_total / 2:
        return config.DIAGNOSI_VARIANTE
    if max_distance_m > config.DEDUP_MISSING_MIN_M:
        return config.DIAGNOSI_MANCANTI
    return config.DIAGNOSI_VERSIONE


def quasi_row(pair: pd.Series, lines: Lines, tratte_of: Mapping[str, pd.DataFrame]) -> dict[str, object]:
    """Riga di quasi_duplicati.csv: i segmenti del cammino corto lontani dall'altro, e la diagnosi.

    pair: riga della tabella delle coppie con quota_minore e a_minore (la linea corta e' quella di a) aggiunte.
    """
    short_id, long_id = (pair["cammino_a"], pair["cammino_b"]) if pair["a_minore"] else (pair["cammino_b"], pair["cammino_a"])
    stretches = far_stretches(lines[short_id], lines[long_id], config.DEDUP_NEAR_M, config.MATCH_SAMPLE_M)
    attributed = [(stretch, *nearest_tratta(stretch.middle, tratte_of[short_id])) for stretch in stretches]
    longest = max(attributed, key=lambda item: item[0].length_m)
    km_total = sum(stretch.length_m for stretch in stretches)
    km_non_tappa = sum(stretch.length_m for stretch, _, tipo in attributed if tipo != config.TIPO_TAPPA)
    max_distance = max(stretch.max_distance_m for stretch in stretches)
    return {
        **{column: pair[column] for column in ("cammino_a", "cammino_b", "nome_a", "nome_b")},
        "punteggio_nome": round(pair["punteggio_nome"], config.SCORE_DECIMALS),
        "quota_minore": round(pair["quota_minore"], config.RATIO_DECIMALS), "cammino_corto": short_id,
        "lunghezza_a_m": round(pair["lunghezza_a_m"], config.METERS_DECIMALS),
        "lunghezza_b_m": round(pair["lunghezza_b_m"], config.METERS_DECIMALS),
        "n_segmenti_lontani": len(stretches), "km_lontani": round(km_total / 1000, config.KM_DECIMALS),
        "km_segmento_max": round(longest[0].length_m / 1000, config.KM_DECIMALS),
        "distanza_max_m": round(max_distance, config.METERS_DECIMALS),
        "tratta_segmento_max": longest[1], "tipo_segmento_max": longest[2],
        "km_lontani_non_tappa": round(km_non_tappa / 1000, config.KM_DECIMALS),
        "diagnosi": diagnose(max_distance, km_non_tappa, km_total),
    }


def quasi_frame(pairs: pd.DataFrame, lines: Lines, tratte: gpd.GeoDataFrame) -> pd.DataFrame:
    """Coppie con nome simile e quota minore tra DEDUP_QUASI_OVERLAP_MIN e la soglia, con la diagnosi dei tratti lontani.

    Si parte dalla tabella delle coppie (quote non arrotondate): il motivo "nome simile, tracciato diverso" e'
    nome alla soglia, geometria presente e quota sotto la soglia.
    """
    near = config.DEDUP_NEAR_M
    quota = minor_share(pairs, near)
    reason = classify(pairs, config.DEDUP_NAME_MIN_SCORE, config.DEDUP_OVERLAP_MIN, near)["motivo"]
    mask = (reason == config.MOTIVO_NOME_SI_TRACCIATO_NO) & (quota >= config.DEDUP_QUASI_OVERLAP_MIN)
    selected = pairs[mask].assign(quota_minore=quota[mask], a_minore=minor_is_a(pairs, near)[mask])
    selected = selected.sort_values(["quota_minore", "punteggio_nome"], ascending=False)
    walking = tratte[tratte["tipo"].isin(config.DEDUP_TIPI)]
    tratte_of = {cammino_id: group for cammino_id, group in walking.groupby("cammino_id")}
    rows = [quasi_row(pair, lines, tratte_of) for _, pair in selected.iterrows()]
    return pd.DataFrame(rows, columns=list(config.DEDUP_QUASI_FIELDS))


# --- Comuni delle due versioni ---

def comuni_sets(cammino_ids: Iterable[str], lines: Lines, comuni: gpd.GeoDataFrame) -> dict[str, frozenset[str]]:
    """Comuni a distanza <= CROSSING_DISTANCE_M dalla linea di ogni cammino (stessa logica del foglio finale)."""
    ids = sorted(set(cammino_ids))
    series = gpd.GeoSeries([lines[cammino_id] for cammino_id in ids], index=ids, crs=config.CRS_WORK)
    found = crossing.within_distance(crossing.comuni_entro(series, comuni, config.CROSSING_DISTANCE_M), config.CROSSING_DISTANCE_M)
    grouped = found.groupby("cammino_id")[COMUNE].agg(frozenset)
    return {cammino_id: grouped.get(cammino_id, frozenset()) for cammino_id in ids}


def comuni_frame(selected: pd.DataFrame, lines: Lines, comuni: gpd.GeoDataFrame) -> pd.DataFrame:
    """Per ogni coppia (colonne cammino_a, cammino_b, nomi, classe, quota_minore) i comuni delle due versioni a confronto."""
    sets = comuni_sets([*selected["cammino_a"], *selected["cammino_b"]], lines, comuni)
    rows = []
    for pair in selected.itertuples():
        first, second = sets[pair.cammino_a], sets[pair.cammino_b]
        union = len(first | second)
        rows.append({
            "cammino_a": pair.cammino_a, "cammino_b": pair.cammino_b, "nome_a": pair.nome_a, "nome_b": pair.nome_b,
            "classe": pair.classe, "quota_minore": pair.quota_minore,
            "n_a": len(first), "n_b": len(second), "n_entrambi": len(first & second), "n_unione": union,
            "jaccard": round(len(first & second) / union, config.RATIO_DECIMALS) if union else np.nan,
        })
    return pd.DataFrame(rows, columns=list(config.DEDUP_COMUNI_FIELDS))


def comuni_selection(coppie: pd.DataFrame, quasi: pd.DataFrame) -> pd.DataFrame:
    """Le coppie duplicato e quelle quasi duplicate, con le colonne che servono a comuni_frame."""
    duplicates = coppie[coppie["classe"] == config.CLASSE_DUPLICATO]
    columns = ["cammino_a", "cammino_b", "nome_a", "nome_b", "quota_minore"]
    near = quasi[columns].assign(classe=config.CLASSE_QUASI_DUPLICATO)
    return pd.concat([duplicates[[*columns, "classe"]], near], ignore_index=True)


# --- Percorso completo ---

def build_outputs(
    catalogue: pd.DataFrame, tratte: gpd.GeoDataFrame, lines: Lines, comuni: gpd.GeoDataFrame
) -> dict[str, pd.DataFrame]:
    """Tutte le uscite per nome (chiavi di config.DEDUP_FILES) a partire dai dati letti."""
    pairs = pair_table(catalogue, lines, config.DEDUP_NEAR_VALUES_M)
    coppie = coppie_frame(pairs)
    quasi = quasi_frame(pairs, lines, tratte)
    return {
        "coppie": coppie,
        "limbo": pd.DataFrame(limbo_rows(coppie), columns=config.LIMBO_FIELDS),
        "gruppi": gruppi_frame(catalogue, lines, coppie[coppie["classe"] == config.CLASSE_DUPLICATO]),
        "sensibilita": sensitivity_frame(pairs),
        "quasi": quasi,
        "comuni": comuni_frame(comuni_selection(coppie, quasi), lines, comuni),
    }


def run() -> None:
    catalogue = load_catalogue()
    tratte = common.read_tratte(config.DEDUP_FONTI)
    lines = cammino_lines(tratte)
    outputs = build_outputs(catalogue, tratte, lines, crossing.load_comuni())
    write_outputs(outputs)
    coppie = outputs["coppie"]
    print(f"cammini {len(catalogue)}, con linea {len(lines)}; coppie candidate {len(coppie)}")
    print(coppie["classe"].value_counts().to_string())
    print(f"limbo per motivo\n{coppie.loc[coppie['classe'] == config.CLASSE_LIMBO, 'motivo'].value_counts().to_string()}")
    print(f"gruppi {outputs['gruppi']['gruppo'].nunique()}, quasi duplicati {len(outputs['quasi'])}")
