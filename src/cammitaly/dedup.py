"""Deduplicazione dei cammini di camminiditalia.org e del Catalogo MiTur: regola del committente.

Regola, con la quota del piu' corto entro DEDUP_NEAR_M metri dall'altro (soglie in config):
- nomi simili: quota >= DEDUP_OVERLAP_MIN duplicato; sotto DEDUP_DISTINCT_BELOW (due geometrie presenti) distinto; in
  mezzo limbo "da verificare"; con una geometria assente limbo. Se un nome e' contenuto nell'altro e la quota e'
  almeno DEDUP_SECTION_OVERLAP_MIN, la coppia e' una sezione e non un duplicato (cambia l'etichetta, non la fusione);
- nomi non simili: quota >= DEDUP_SECTION_OVERLAP_MIN e almeno una parola in comune tra le chiavi dei nomi = sezione
  (il corto e' una parte dell'altro, si fonde); senza parola in comune limbo.
Si confrontano tutte le coppie, anche dentro la stessa fonte. Le uscite sono in config.DEDUP_DIR; nulla qui cambia i
cammini o le tratte.

cammini_unici.csv e membri.csv: componenti connesse delle coppie duplicato e sezione, l'unica fonte dei gruppi; un
cammino unito tiene tutte le tracce delle sue versioni. Non c'e' un file di geometrie: chi serve le tratte (tag, passo
P5) le raccoglie per cammino_id dei membri, con le loro etichette.
"""
import itertools
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from rapidfuzz import fuzz
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from cammitaly import config, crossing, matching
from cammitaly.geometry import common

COMUNE = config.COMUNE_ID_COL
Lines = Mapping[str, BaseGeometry]
Shares = dict[tuple[str, str], list[float]]


def write_outputs(outputs: Mapping[str, pd.DataFrame]) -> None:
    """Scrive ogni uscita in DEDUP_DIR con lo scrittore comune (UTF-8, LF)."""
    for name, frame in outputs.items():
        common.write_frame(frame, config.DEDUP_DIR / config.DEDUP_FILES[name])


# --- Dati di partenza ---

def load_catalogue(extra_columns: Sequence[str] = ()) -> pd.DataFrame:
    """Tutti i cammini delle fonti, prima camminiditalia poi MiTur, ciascuna per id, con la chiave del nome.

    extra_columns: altre colonne di cammini_<fonte>.csv da tenere (l'abbinamento con OSM usa regioni e lunghezza dichiarata).
    """
    columns = ["cammino_id", "fonte", "nome", "stato_geometria", *extra_columns]
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


def osm_substitute_tratte(catalogue_ids: Iterable[str], tratte: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Per i cammini del catalogo senza tracce proprie, una tratta (fonte osm, tipo altro) con la geometria OSM abbinata.

    La geometria viene da geometrie_osm.gpkg (python -m cammitaly osm-match); se il file manca non c'e' nessun sostituto.
    tratta_id e nome sono gli id delle relazioni OSM.
    """
    path = config.OSM_MATCH_DIR / config.OSM_MATCH_FILES["geometrie"]
    if not path.exists():
        print(f"nessuna geometria OSM sostitutiva: manca {path}")
        return common.tratte_frame([])
    own = set(tratte.loc[tratte["tipo"].isin(config.DEDUP_TIPI), "cammino_id"])
    osm = gpd.read_file(path, layer=config.OSM_MATCH_LAYER, engine="pyogrio")
    chosen = osm[osm["cammino_id"].isin(set(catalogue_ids) - own)]
    return common.tratte_frame([
        {
            "tratta_id": row.osm_ids, "cammino_id": row.cammino_id, "fonte": config.OSM_FONTE, "nome": row.osm_ids,
            "tipo": config.TIPO_ALTRO, "lunghezza_m": row.geometry.length, "geometry": row.geometry,
        }
        for row in chosen.itertuples()
    ])


# --- Quote di sovrapposizione ---

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
    # un indice per bersaglio, riusato da tutte le sorgenti vicine
    needed = {t for targets in neighbours.values() for t in targets}
    indexes = {t: matching.LineIndex(geoms[t]) for t in needed}
    shares: Shares = {}
    for s, targets in neighbours.items():
        sample = matching.sample_line(geoms[s])
        for t in targets:
            shares[(ids[s], ids[t])] = matching.shares_within(sample, indexes[t], nears_desc)
    return shares


def share_column(direction: str, near_m: float) -> str:
    return f"quota_{direction}_{near_m:g}"


def _side_columns(catalogue: pd.DataFrame, lengths: np.ndarray, order: np.ndarray, suffix: str) -> dict[str, np.ndarray]:
    """Fonte, nome, stato e lunghezza del cammino di un lato della coppia (suffix "a" o "b"); order = posizioni nel catalogo."""
    columns = {
        f"{column}_{suffix}": catalogue[source].to_numpy()[order]
        for column, source in (("fonte", "fonte"), ("nome", "nome"), ("stato", "stato_geometria"))
    }
    columns[f"lunghezza_{suffix}_m"] = lengths[order]
    return columns


def _name_columns(keys: Sequence[str], first: np.ndarray, second: np.ndarray) -> dict[str, np.ndarray | list[bool]]:
    """Punteggi dei nomi (insieme di parole e ordinate), nome contenuto nell'altro e parola in comune di ogni coppia."""
    set_scores, sort_scores = matching.name_scores(keys, keys), matching.name_scores(keys, keys, fuzz.token_sort_ratio)
    words = [set(key.split()) for key in keys]
    scores = set_scores[first, second]
    return {
        "punteggio_nome": scores, "punteggio_nome_sort": sort_scores[first, second],
        "nome_contenuto": [score == 100 and words[a] != words[b] for a, b, score in zip(first, second, scores)],
        "parola_comune": [bool(words[a] & words[b]) for a, b in zip(first, second)],
    }


def _osm_side(pairs: pd.DataFrame, osm_ids: Collection[str]) -> np.ndarray:
    """Quale lato della coppia usa la geometria OSM sostitutiva."""
    side_a, side_b = pairs["cammino_a"].isin(osm_ids), pairs["cammino_b"].isin(osm_ids)
    return np.select(
        [side_a & side_b, side_a, side_b], [config.DEDUP_OSM_SIDE_BOTH, config.DEDUP_OSM_SIDE_A, config.DEDUP_OSM_SIDE_B],
        default=config.DEDUP_OSM_SIDE_NONE,
    )


def _share_columns(pairs: pd.DataFrame, lines: Lines, near_values: Sequence[float]) -> dict[str, list[float]]:
    """Le colonne delle quote, per direzione e distanza: NaN se manca una geometria, 0 se i riquadri non si toccano."""
    shares = overlap_shares(lines, near_values)
    nears_desc = sorted(near_values, reverse=True)
    far = [0.0] * len(nears_desc)
    columns = {}
    for direction, (own, other) in {"a_in_b": ("cammino_a", "cammino_b"), "b_in_a": ("cammino_b", "cammino_a")}.items():
        for position, near in enumerate(nears_desc):
            columns[share_column(direction, near)] = [
                shares.get((s, t), far)[position] if s in lines and t in lines else np.nan
                for s, t in zip(pairs[own], pairs[other])
            ]
    return columns


def pair_table(
    catalogue: pd.DataFrame, lines: Lines, near_values: Sequence[float], osm_ids: Collection[str] = frozenset()
) -> pd.DataFrame:
    """Una riga per coppia non ordinata di cammini (a prima di b nel catalogo), con nomi, lunghezze e quote per ogni distanza.

    Le quote sono NaN se uno dei due non ha geometria, 0 se entrambi l'hanno ma i riquadri non si toccano.
    osm_ids: cammini la cui linea e' la geometria OSM sostitutiva; la colonna geometria_osm dice quale lato la usa.
    """
    ids = catalogue["cammino_id"].to_numpy()
    first, second = np.triu_indices(len(ids), 1)
    lengths = np.array([lines[cammino_id].length if cammino_id in lines else np.nan for cammino_id in ids])
    pairs = pd.DataFrame({"cammino_a": ids[first], "cammino_b": ids[second]}).assign(
        **_side_columns(catalogue, lengths, first, "a"), **_side_columns(catalogue, lengths, second, "b"),
        **_name_columns(catalogue["chiave"].tolist(), first, second),
    )
    pairs["geometria_osm"] = _osm_side(pairs, osm_ids)
    return pairs.assign(**_share_columns(pairs, lines, near_values))


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

class Thresholds(NamedTuple):
    """Le soglie della regola: i valori di config sono quelli adottati, la griglia di sensibilita' li fa variare."""
    name_min: float = config.DEDUP_NAME_MIN_SCORE
    overlap_min: float = config.DEDUP_OVERLAP_MIN
    near_m: float = config.DEDUP_NEAR_M
    distinct_below: float = config.DEDUP_DISTINCT_BELOW


ADOPTED = Thresholds()


def classify(pairs: pd.DataFrame, thresholds: Thresholds = ADOPTED) -> pd.DataFrame:
    """Colonne classe e motivo di ogni coppia con le soglie date (la regola del committente e la griglia di sensibilita' usano questa).

    La soglia della sezione viene da config: non varia nella griglia. Ordine delle regole: la prima vera vince.
    """
    quota = minor_share(pairs, thresholds.near_m)
    similar, known = pairs["punteggio_nome"] >= thresholds.name_min, quota.notna()
    duplicate = similar & (quota >= thresholds.overlap_min)
    apart = similar & (quota < thresholds.distinct_below)
    # nome contenuto nell'altro ("Via Francigena" in "Magna Via Francigena nel Sud") e tracciato contenuto: e' una sezione
    # anche se il punteggio dice "simile"; la fusione e' la stessa del duplicato, cambia il ruolo in membri.csv
    section_of_whole = similar & pairs["nome_contenuto"] & (quota >= config.DEDUP_SECTION_OVERLAP_MIN)
    contained = ~similar & (quota >= config.DEDUP_SECTION_OVERLAP_MIN)
    shared = pairs["parola_comune"]
    reasons = [
        (section_of_whole, config.CLASSE_SEZIONE, config.MOTIVO_SEZIONE),
        (duplicate, config.CLASSE_DUPLICATO, config.MOTIVO_DUPLICATO),
        (apart, config.CLASSE_DISTINTO, config.MOTIVO_NOME_SI_TRACCIATO_NO),
        (similar & known & ~duplicate & ~apart, config.CLASSE_LIMBO, config.MOTIVO_NOME_SI_TRACCIATO_INTERMEDIO),
        (similar & ~known, config.CLASSE_LIMBO, config.MOTIVO_NOME_SI_GEOMETRIA_ASSENTE),
        (contained & shared, config.CLASSE_SEZIONE, config.MOTIVO_SEZIONE),
        (contained & ~shared, config.CLASSE_LIMBO, config.MOTIVO_TRACCIATO_SI_NOME_NO),
        (~similar & known & ~contained, config.CLASSE_DISTINTO, config.MOTIVO_DISTINTO),
        (~similar & ~known, config.CLASSE_DISTINTO, config.MOTIVO_DISTINTO_GEOMETRIA_ASSENTE),
    ]
    conditions = [condition.to_numpy() for condition, _, _ in reasons]
    return pd.DataFrame({
        "classe": np.select(conditions, [classe for _, classe, _ in reasons], default=""),
        "motivo": np.select(conditions, [motivo for _, _, motivo in reasons], default=""),
    }, index=pairs.index)


def coppie_frame(pairs: pd.DataFrame) -> pd.DataFrame:
    """coppie.csv: i candidati (nome simile alla soglia piu' bassa della griglia, o tracciati che si toccano) con la classe
    della regola adottata (soglie ADOPTED)."""
    near = ADOPTED.near_m
    quota_minore = minor_share(pairs, near)
    # minimum/maximum propagano NaN: senza una delle due geometrie il rapporto resta vuoto
    shorter, longer = np.minimum(pairs["lunghezza_a_m"], pairs["lunghezza_b_m"]), np.maximum(pairs["lunghezza_a_m"], pairs["lunghezza_b_m"])
    frame = pairs.assign(
        quota_a_in_b=pairs[share_column("a_in_b", near)], quota_b_in_a=pairs[share_column("b_in_a", near)],
        quota_minore=quota_minore, rapporto_lunghezza=shorter / longer,
    ).join(classify(pairs))
    frame = frame[(frame["punteggio_nome"] >= config.DEDUP_CANDIDATE_NAME_MIN) | (frame["quota_minore"] > 0)]
    rank = frame["classe"].map({config.CLASSE_DUPLICATO: 0, config.CLASSE_SEZIONE: 1, config.CLASSE_LIMBO: 2, config.CLASSE_DISTINTO: 3})
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
            f"quota minore {quota} (duplicato da {config.DEDUP_OVERLAP_MIN}, distinto sotto {config.DEDUP_DISTINCT_BELOW}, "
            f"entro {config.DEDUP_NEAR_M} m); "
            f"lunghezze {km_a} e {km_b}; geometria {pair.stato_a} e {pair.stato_b}"
        )
        rows.append(common.limbo_row(f"{pair.cammino_a}{config.DEDUP_PAIR_SEPARATOR}{pair.cammino_b}", pair.motivo, detail))
    return rows


# --- Cammini unici: duplicato e sezione si fondono ---

def _length_of(lengths: Mapping[str, float], cammino_id: str) -> float:
    value = lengths.get(cammino_id, np.nan)
    return value if pd.notna(value) else -1.0


def _role(member: str, main: str, members: Sequence[str], edge_class: Mapping[frozenset, str]) -> str:
    """Classe della coppia con il principale; se non e' accoppiato a lui (catena), duplicato se ha un arco duplicato, altrimenti sezione."""
    if member == main:
        return config.RUOLO_PRINCIPALE
    classes = {edge_class[frozenset((member, other))] for other in members if frozenset((member, other)) in edge_class}
    kind = edge_class.get(frozenset((member, main))) or (config.CLASSE_DUPLICATO if config.CLASSE_DUPLICATO in classes else config.CLASSE_SEZIONE)
    return config.RUOLO_DUPLICATO if kind == config.CLASSE_DUPLICATO else config.RUOLO_SEZIONE


def _aliases(names: Iterable[str], main_name: str) -> list[str]:
    """Gli altri nomi del cammino unico, uno per chiave di nome: maiuscole, articoli e parole generiche non fanno un alias."""
    seen = {matching.name_key(main_name)}
    aliases = []
    for name in names:
        if (key := matching.name_key(name)) not in seen:
            seen.add(key)
            aliases.append(name)
    return aliases


def _ordered_groups(position: Mapping[str, int], merging: pd.DataFrame) -> list[list[str]]:
    """Componenti connesse degli archi di fusione piu' i cammini senza archi, ciascuna nell'ordine del catalogo, e in quell'ordine tra loro."""
    linked = matching.connected_groups(zip(merging["cammino_a"], merging["cammino_b"]))
    in_group = {cammino_id for group in linked for cammino_id in group}
    groups = [*linked, *([cammino_id] for cammino_id in position if cammino_id not in in_group)]
    return sorted((sorted(group, key=position.__getitem__) for group in groups), key=lambda group: position[group[0]])


def _is_chain(members: Sequence[str], position: Mapping[str, int], edge_class: Mapping[frozenset, str]) -> bool:
    """Vero se non tutte le coppie dei membri sono archi di fusione."""
    links = sum(1 for a in members for b in members if position[a] < position[b] and frozenset((a, b)) in edge_class)
    return links < len(members) * (len(members) - 1) // 2


def _unique_row(unico_id: str, members: Sequence[str], main: str, info: pd.DataFrame, chain: bool) -> dict[str, object]:
    return {
        "cammino_unico_id": unico_id, "nome": info.at[main, "nome"],
        "fonti": config.DEDUP_LIST_SEPARATOR.join(sorted(set(info.loc[members, "fonte"]))),
        "n_membri": len(members), "membri": config.DEDUP_LIST_SEPARATOR.join(members),
        "alias": config.DEDUP_LIST_SEPARATOR.join(_aliases(dict.fromkeys(info.loc[members, "nome"]), info.at[main, "nome"])),
        "stato": min(info.loc[members, "stato_geometria"], key=config.DEDUP_STATO_ORDER.index),
        "catena": chain,
    }


def unique_frames(catalogue: pd.DataFrame, lengths: Mapping[str, float], merging: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(cammini_unici, membri): componenti connesse delle coppie duplicato e sezione; ogni cammino del catalogo compare una volta.

    lengths: lunghezza della linea per cammino (assente se senza geometria). merging: coppie con cammino_a, cammino_b e classe.
    Il principale e' il membro piu' lungo (a parita' il primo del catalogo), il nome del cammino unico e' il suo; alias sono
    i nomi degli altri membri con una chiave di nome diversa. `catena` e' vero se non tutte le coppie dei membri sono
    archi. Stato: il migliore dei membri.
    """
    position = {cammino_id: n for n, cammino_id in enumerate(catalogue["cammino_id"])}
    edge_class = {frozenset(pair[:2]): pair[2] for pair in zip(merging["cammino_a"], merging["cammino_b"], merging["classe"])}
    info = catalogue.set_index("cammino_id")
    unici, membri = [], []
    for number, members in enumerate(_ordered_groups(position, merging), start=1):
        unico_id = config.DEDUP_UNICO_ID_FORMAT.format(number=number)
        main = max(members, key=lambda m: (_length_of(lengths, m), -position[m]))
        unici.append(_unique_row(unico_id, members, main, info, _is_chain(members, position, edge_class)))
        membri.extend(
            {"cammino_unico_id": unico_id, "cammino_id": member, "fonte": info.at[member, "fonte"],
             "nome": info.at[member, "nome"], "ruolo": _role(member, main, members, edge_class)}
            for member in members
        )
    return pd.DataFrame(unici, columns=list(config.DEDUP_UNICI_FIELDS)), pd.DataFrame(membri, columns=list(config.DEDUP_MEMBRI_FIELDS))


# --- Sensibilita' ---

def sensitivity_frame(pairs: pd.DataFrame) -> pd.DataFrame:
    """Conteggi per classe a ogni combinazione di soglia sul nome, quota minima, quota del distinto e distanza, su tutte le coppie.

    I quattro conteggi di classe sommano al numero di tutte le coppie.
    """
    rows = []
    identical = pairs["punteggio_nome"] == 100
    grid = itertools.product(
        config.DEDUP_SENS_NAME_SCORES, config.DEDUP_SENS_OVERLAPS, config.DEDUP_SENS_DISTINCT_BELOW, config.DEDUP_SENS_NEAR_M
    )
    for name_min, overlap_min, distinct_below, near_m in grid:
        counts = classify(pairs, Thresholds(name_min, overlap_min, near_m, distinct_below))["classe"].value_counts()
        quota = minor_share(pairs, near_m)
        rows.append({
            "soglia_nome": name_min, "soglia_quota": overlap_min, "soglia_distinto": distinct_below, "distanza_m": near_m,
            "n_duplicato": int(counts.get(config.CLASSE_DUPLICATO, 0)),
            "n_sezione": int(counts.get(config.CLASSE_SEZIONE, 0)),
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
    """Coppie con nome simile e quota minore tra DEDUP_QUASI_OVERLAP_MIN e DEDUP_QUASI_OVERLAP_MAX, con la diagnosi dei tratti lontani.

    Si parte dalla tabella delle coppie (quote non arrotondate). Non dipende dalla classe: con la regola del committente molte di
    queste coppie sono duplicati, ma il perche' del tratto lontano resta utile.
    """
    near = config.DEDUP_NEAR_M
    quota = minor_share(pairs, near)
    similar = pairs["punteggio_nome"] >= config.DEDUP_NAME_MIN_SCORE
    mask = similar & (quota >= config.DEDUP_QUASI_OVERLAP_MIN) & (quota < config.DEDUP_QUASI_OVERLAP_MAX)
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
    """Le coppie duplicato e quelle quasi duplicate, ciascuna una volta (se e' in entrambi gli insiemi resta quasi duplicata)."""
    columns = ["cammino_a", "cammino_b", "nome_a", "nome_b", "quota_minore"]
    key = ["cammino_a", "cammino_b"]
    in_quasi = pd.MultiIndex.from_frame(coppie[key]).isin(pd.MultiIndex.from_frame(quasi[key]))
    # solo duplicati e quasi duplicati: le sezioni sono per definizione un sottoinsieme dell'intero, il confronto dei comuni non le riguarda
    duplicates = coppie[(coppie["classe"] == config.CLASSE_DUPLICATO) & ~in_quasi]
    near = quasi[columns].assign(classe=config.CLASSE_QUASI_DUPLICATO)
    return pd.concat([duplicates[[*columns, "classe"]], near], ignore_index=True)


# --- Percorso completo ---

def build_outputs(
    catalogue: pd.DataFrame, tratte: gpd.GeoDataFrame, lines: Lines, comuni: gpd.GeoDataFrame,
    osm_ids: Collection[str] = frozenset(),
) -> dict[str, pd.DataFrame]:
    """Tutte le uscite per nome (chiavi di config.DEDUP_FILES) a partire dai dati letti; osm_ids come in pair_table."""
    pairs = pair_table(catalogue, lines, config.DEDUP_NEAR_VALUES_M, osm_ids)
    coppie = coppie_frame(pairs)
    quasi = quasi_frame(pairs, lines, tratte)
    merging = coppie[coppie["classe"].isin([config.CLASSE_DUPLICATO, config.CLASSE_SEZIONE])]
    unici, membri = unique_frames(catalogue, {cammino_id: line.length for cammino_id, line in lines.items()}, merging)
    return {
        "coppie": coppie,
        "limbo": pd.DataFrame(limbo_rows(coppie), columns=config.LIMBO_FIELDS),
        "sensibilita": sensitivity_frame(pairs),
        "quasi": quasi,
        "comuni": comuni_frame(comuni_selection(coppie, quasi), lines, comuni),
        "unici": unici,
        "membri": membri,
    }


def run() -> None:
    catalogue = load_catalogue()
    tratte = common.read_tratte(config.DEDUP_FONTI)
    substitutes = osm_substitute_tratte(catalogue["cammino_id"], tratte)
    tratte = gpd.GeoDataFrame(pd.concat([tratte, substitutes], ignore_index=True), crs=config.CRS_WORK)
    lines = cammino_lines(tratte)
    osm_ids = frozenset(substitutes["cammino_id"])
    outputs = build_outputs(catalogue, tratte, lines, crossing.load_comuni(), osm_ids)
    write_outputs(outputs)
    coppie = outputs["coppie"]
    print(f"cammini {len(catalogue)}, con linea {len(lines)} (geometria OSM sostitutiva {len(osm_ids)}); coppie candidate {len(coppie)}")
    print(coppie["classe"].value_counts().to_string())
    print(f"limbo per motivo\n{coppie.loc[coppie['classe'] == config.CLASSE_LIMBO, 'motivo'].value_counts().to_string()}")
    print(f"quasi duplicati {len(outputs['quasi'])}")
    unici = outputs["unici"]
    print(f"cammini distinti {len(unici)} (da {len(catalogue)}), con piu' membri {int((unici['n_membri'] > 1).sum())}, catene {int(unici['catena'].sum())}")
