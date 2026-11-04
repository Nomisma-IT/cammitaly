"""Abbinamento dei cammini del catalogo (camminiditalia.org e MiTur) alle relazioni di percorso di OpenStreetMap.

Regola (soglie in config, sezione "Abbinamento con OSM"; nomi e quote con le primitive di matching.py):
- Cammino con tracce: sono candidate le relazioni con nome simile (punteggio >= OSM_MATCH_NAME_MIN_SCORE) e almeno
  OSM_MATCH_SHARE_MIN del cammino entro OSM_MATCH_NEAR_M dalla relazione, oppure della relazione dal cammino. Si
  scartano le candidate piu' lunghe del massimo dell'intervallo di lunghezza per la lunghezza di riferimento (dichiarata,
  altrimenti quella del tracciato: un contenitore non e' il cammino), poi quelle contenute in un'altra (resta la piu'
  esterna). L'unione delle tenute e' abbinata se entrambe le quote bastano, oppure se ne basta una e la lunghezza sta
  nell'intervallo; altrimenti e' "parziale" (copre un tratto) o "contenitore" (contiene il cammino) e non serve ad altri.
  Nome diverso ed entrambe le quote >= OSM_MATCH_GEOMETRY_ONLY_MIN: esito "solo geometria", che non e' un abbinamento.
- Cammino senza tracce: nome simile, almeno OSM_MATCH_REGION_SHARE_MIN della relazione nelle regioni dichiarate e,
  se il catalogo dichiara la lunghezza, lunghezza OSM / dichiarata nello stesso intervallo. Le relazioni che passano si
  riuniscono in gruppi (tappe a meno di OSM_MATCH_STAGE_GAP_M l'una dall'altra); piu' gruppi distinti che passano = ambiguo.
- Annidamento: dalla relazione piu' lunga, si scarta ogni relazione che sta per almeno OSM_MATCH_NESTED_MIN dentro una
  gia' tenuta (resta la piu' esterna). Le relazioni tenute si uniscono: tappe senza la relazione madre danno l'unione.
La geometria OSM di un cammino abbinato e' l'unione delle tratte (principale e varianti) delle relazioni tenute.
"""
import dataclasses
import time
from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import psutil
import pyogrio
import shapely
from shapely.geometry.base import BaseGeometry

from cammitaly import config, crossing, dedup, matching, tags
from cammitaly.geometry import common

Pair = tuple[str, str]  # (cammino del catalogo, relazione OSM)
TratteReader = Callable[[Collection[str] | None], Iterable[tuple[str, BaseGeometry]]]
NEAR = config.OSM_MATCH_NEAR_M
NAN_SHARES = (np.nan, np.nan)


# --- Campioni e quote ---

def osm_in_catalogue(lines: Mapping[str, BaseGeometry], relation_tratte: Iterable[tuple[str, BaseGeometry]]) -> dict[Pair, float]:
    """(cammino, relazione) -> quota della relazione entro NEAR dal cammino, per le coppie con quota positiva.

    Una sola passata sulle tratte OSM: i punti di ogni tratta interrogano un indice sui pezzi di tutte le linee del
    catalogo (invece di un confronto per coppia, che richiederebbe di rileggere le tratte per ogni cammino). Le tratte
    della stessa relazione (principale e varianti) si sommano.
    """
    ids = list(lines)
    pieces, owners = [], []
    for code, cammino_id in enumerate(ids):
        own = matching.line_pieces(lines[cammino_id], config.MATCH_PIECE_VERTICES)
        pieces.extend(own)
        owners.extend([code] * len(own))
    tree, owner_of = shapely.STRtree(pieces), np.array(owners, dtype=np.int64)
    near_weight: dict[Pair, float] = defaultdict(float)
    total: dict[str, float] = defaultdict(float)
    for relation_id, geom in relation_tratte:
        points, weights = matching.sample_points(geom, config.MATCH_SAMPLE_M)
        total[relation_id] += float(weights.sum())
        point_index, piece_index = tree.query(points, predicate="dwithin", distance=NEAR)
        if not len(point_index):
            continue
        # un punto vicino a piu' pezzi dello stesso cammino conta una volta
        hits = np.unique(np.stack([point_index, owner_of[piece_index]]), axis=1)
        sums = np.bincount(hits[1], weights=weights[hits[0]], minlength=len(ids))
        for code in np.flatnonzero(sums):
            near_weight[(ids[code], relation_id)] += float(sums[code])
    return {pair: weight / total[pair[1]] for pair, weight in near_weight.items()}


def region_share(sample: matching.Sample, regions: Sequence[BaseGeometry]) -> float:
    """Quota (in lunghezza) del campione dentro l'unione delle regioni date."""
    x, y = shapely.get_coordinates(sample.points).T
    inside = np.zeros(len(x), dtype=bool)
    for region in regions:
        inside |= shapely.contains_xy(region, x, y)
    return float(sample.weights[inside].sum() / sample.weights.sum())


def union_lines(geoms: Sequence[BaseGeometry]) -> BaseGeometry:
    return geoms[0] if len(geoms) == 1 else shapely.line_merge(shapely.union_all(geoms))


# --- Dati OSM ---

def osm_tratte_path() -> Path:
    return Path(config.TRATTE_GPKG_TEMPLATE.format(fonte=config.OSM_FONTE))


def load_relations(available: Collection[str] | None = None) -> pd.DataFrame:
    """Relazioni di cammini_osm.csv (indice = cammino_id) con la chiave del nome; solo quelle in `available`, se dato."""
    relations = pd.DataFrame(common.read_csv(Path(config.CAMMINI_CSV_TEMPLATE.format(fonte=config.OSM_FONTE))))
    if available is not None:
        relations = relations[relations["cammino_id"].isin(set(available))]
    relations = relations.set_index("cammino_id")[["nome", "pilgrimage"]]
    relations["chiave"] = relations["nome"].map(matching.name_key)
    return relations


def read_osm_index() -> pd.DataFrame:
    """fid, cammino_id e riquadro di ogni tratta OSM, senza leggere le geometrie."""
    path = osm_tratte_path()
    fids, bounds = pyogrio.read_bounds(path, layer=config.TRATTE_LAYER)
    attributes = pyogrio.read_dataframe(
        path, layer=config.TRATTE_LAYER, read_geometry=False, columns=["cammino_id"], fid_as_index=True
    )
    index = pd.DataFrame(bounds.T, columns=["min_x", "min_y", "max_x", "max_y"])
    index["fid"], index["cammino_id"] = fids, attributes.loc[fids, "cammino_id"].to_numpy()
    return index


def _read_chunks(fids: np.ndarray) -> Iterator[tuple[str, BaseGeometry]]:
    """Le tratte dei fid dati, a blocchi di OSM_MATCH_CHUNK_ROWS: in memoria c'e' un blocco alla volta."""
    fids = np.sort(fids)
    for start in range(0, len(fids), config.OSM_MATCH_CHUNK_ROWS):
        chunk = pyogrio.read_dataframe(
            osm_tratte_path(), layer=config.TRATTE_LAYER, fids=fids[start:start + config.OSM_MATCH_CHUNK_ROWS],
            columns=["cammino_id"],
        )
        yield from zip(chunk["cammino_id"], chunk.geometry)


def osm_tratte_reader(index: pd.DataFrame, lines: Mapping[str, BaseGeometry]) -> TratteReader:
    """Lettore delle tratte OSM: con None quelle il cui riquadro tocca il riquadro di una linea del catalogo allargato
    di NEAR (le sole che possono esserle vicine); con un insieme di relazioni, le tratte di quelle relazioni."""
    def read(relation_ids: Collection[str] | None) -> Iterator[tuple[str, BaseGeometry]]:
        if relation_ids is not None:
            return _read_chunks(index.loc[index["cammino_id"].isin(set(relation_ids)), "fid"].to_numpy())
        bounds = shapely.bounds(np.array(list(lines.values()), dtype=object))
        grown = shapely.box(bounds[:, 0] - NEAR, bounds[:, 1] - NEAR, bounds[:, 2] + NEAR, bounds[:, 3] + NEAR)
        boxes = shapely.box(index["min_x"], index["min_y"], index["max_x"], index["max_y"])
        touching = np.unique(shapely.STRtree(grown).query(boxes, predicate="intersects")[0])
        return _read_chunks(index["fid"].to_numpy()[touching])
    return read


def relation_geometries(tratte: Iterable[tuple[str, BaseGeometry]]) -> dict[str, BaseGeometry]:
    """Una geometria per relazione: la tratta principale, unita alla tratta delle varianti se c'e'."""
    parts: dict[str, list[BaseGeometry]] = defaultdict(list)
    for relation_id, geom in tratte:
        parts[relation_id].append(geom)
    return {relation_id: union_lines(geoms) for relation_id, geoms in parts.items()}


def region_polygons(comuni: gpd.GeoDataFrame) -> dict[str, BaseGeometry]:
    """Slug della regione -> poligono (unione dei comuni con lo stesso COD_REG), preparato per i test punto in poligono."""
    slug_of = {code: slug for slug, code in crossing.load_region_codes().items()}
    dissolved = comuni[[config.COMUNE_REGION_COL, "geometry"]].dissolve(by=config.COMUNE_REGION_COL).geometry
    polygons = {slug_of[int(code)]: geom for code, geom in dissolved.items()}
    for polygon in polygons.values():
        shapely.prepare(polygon)
    return polygons


# --- Candidati ---

def name_candidates(catalogue: pd.DataFrame, relations: pd.DataFrame) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    """Per ogni cammino le relazioni con nome simile e il punteggio; e il punteggio piu' alto su tutte le relazioni."""
    scores = matching.name_scores(catalogue["chiave"].tolist(), relations["chiave"].tolist())
    relation_ids = relations.index.to_numpy()
    names, best = {}, {}
    for row, cammino_id in enumerate(catalogue["cammino_id"]):
        similar = np.flatnonzero(scores[row] >= config.OSM_MATCH_NAME_MIN_SCORE)
        names[cammino_id] = {relation_ids[col]: float(scores[row, col]) for col in similar}
        best[cammino_id] = float(scores[row].max()) if scores.shape[1] else np.nan
    return names, best


def strong_geometry(osm_in: Mapping[Pair, float], names: Mapping[str, Mapping[str, float]]) -> dict[str, list[str]]:
    """Per ogni cammino le relazioni con nome diverso che gli stanno sopra per almeno OSM_MATCH_GEOMETRY_ONLY_MIN."""
    strong: dict[str, list[str]] = defaultdict(list)
    for (cammino_id, relation_id), share in osm_in.items():
        if share >= config.OSM_MATCH_GEOMETRY_ONLY_MIN and relation_id not in names[cammino_id]:
            strong[cammino_id].append(relation_id)
    return strong


def needed_relations(
    lines: Mapping[str, BaseGeometry], names: Mapping[str, Mapping[str, float]], osm_in: Mapping[Pair, float]
) -> set[str]:
    """Relazioni di cui serve la geometria: nome simile (vicine al tracciato, se il cammino ne ha uno) e solo geometria."""
    needed = {relation for strong in strong_geometry(osm_in, names).values() for relation in strong}
    for cammino_id, similar in names.items():
        needed.update(r for r in similar if cammino_id not in lines or (cammino_id, r) in osm_in)
    return needed


# --- Lunghezze ---

def length_ratio(length_m: float, reference_km: float) -> float:
    """Lunghezza / riferimento; NaN senza un riferimento positivo (un tracciato di lunghezza zero e nessuna dichiarata)."""
    return length_m / 1000 / reference_km if reference_km > 0 else np.nan


def length_fits(length_m: float, declared_km: float) -> bool:
    """Con lunghezza di riferimento: rapporto nell'intervallo; senza: almeno OSM_MATCH_MIN_LENGTH_M."""
    if np.isnan(declared_km):
        return length_m >= config.OSM_MATCH_MIN_LENGTH_M
    return config.OSM_MATCH_LENGTH_RATIO_MIN <= length_ratio(length_m, declared_km) <= config.OSM_MATCH_LENGTH_RATIO_MAX


# --- Decisione ---

@dataclass
class Context:
    """Dati condivisi dalle decisioni: geometrie delle relazioni (con campioni e indici calcolati una volta), quote, regioni."""
    relations: pd.DataFrame
    geometries: Mapping[str, BaseGeometry]
    osm_in: Mapping[Pair, float]
    regions: Mapping[str, BaseGeometry]
    strong: Mapping[str, list[str]]  # cammino -> relazioni con nome diverso candidate a "solo geometria"
    samples: dict[str, matching.Sample] = field(default_factory=dict)
    indexes: dict[str, matching.LineIndex] = field(default_factory=dict)

    def sample(self, relation_id: str) -> matching.Sample:
        if relation_id not in self.samples:
            self.samples[relation_id] = matching.sample_line(self.geometries[relation_id])
        return self.samples[relation_id]

    def index(self, relation_id: str) -> matching.LineIndex:
        """L'indice spaziale della relazione, costruito una sola volta: l'annidamento la interroga per ogni altra relazione."""
        if relation_id not in self.indexes:
            self.indexes[relation_id] = matching.LineIndex(self.geometries[relation_id])
        return self.indexes[relation_id]

    def length(self, relation_id: str) -> float:
        return float(self.geometries[relation_id].length)

    def forget(self) -> None:
        """Svuota campioni e indici: servono a un cammino alla volta (le relazioni condivise sono poche) e tenerli per
        tutte le relazioni portava la memoria da 0,5 a 1,5 GB."""
        self.samples.clear()
        self.indexes.clear()


@dataclass(frozen=True)
class Outcome:
    esito: str
    ids: list[str]
    geometry: BaseGeometry | None = None  # unione delle relazioni elencate, se l'esito ne ha una
    nota: str = ""
    shares: tuple[float, float] = NAN_SHARES  # (catalogo in OSM, OSM nel catalogo) sull'unione, per i cammini con tracce


def outermost(relation_ids: Iterable[str], ctx: Context) -> tuple[list[str], list[str]]:
    """(tenute, scartate): dalla piu' lunga, si scarta ogni relazione che sta per almeno OSM_MATCH_NESTED_MIN entro NEAR
    da una gia' tenuta."""
    kept, nested = [], []
    for relation_id in sorted(relation_ids, key=lambda r: (-ctx.length(r), r)):
        sample = ctx.sample(relation_id)
        inside = any(matching.share_within(sample, ctx.index(k), NEAR) >= config.OSM_MATCH_NESTED_MIN for k in kept)
        (nested if inside else kept).append(relation_id)
    return kept, nested


def stage_groups(relation_ids: Sequence[str], ctx: Context) -> list[list[str]]:
    """Relazioni riunite in gruppi: due stanno insieme se distano al massimo OSM_MATCH_STAGE_GAP_M (tappe di un percorso)."""
    edges = [(r, r) for r in relation_ids]
    edges += [
        (a, b) for n, a in enumerate(relation_ids) for b in relation_ids[n + 1:]
        if shapely.dwithin(ctx.geometries[a], ctx.geometries[b], config.OSM_MATCH_STAGE_GAP_M)
    ]
    return sorted((sorted(group) for group in matching.connected_groups(edges)), key=lambda group: group[0])


def _ids(relation_ids: Iterable[str]) -> str:
    return config.LIST_SEPARATOR.join(sorted(relation_ids))


def _ratio(value: float, decimals: int = config.RATIO_DECIMALS) -> str:
    return f"{value:.{decimals}f}"


# --- Cammini con tracce ---

@dataclass(frozen=True)
class Tracked:
    """Il cammino con tracce: linea, campione e indice costruiti una volta, e la lunghezza di riferimento."""
    cammino_id: str
    sample: matching.Sample
    index: matching.LineIndex
    reference_km: float
    reference_label: str  # da dove viene la lunghezza di riferimento, per le note


def track_of(cammino: Mapping[str, object], line: BaseGeometry) -> Tracked:
    """La lunghezza di riferimento e' la dichiarata dal catalogo; se manca, quella del tracciato del catalogo."""
    declared, line_km = float(cammino["lunghezza_dichiarata_km"]), line.length / 1000
    reference_km, label = (declared, "dichiarata") if declared > 0 else (line_km, "del tracciato")
    if not reference_km > 0:  # tracciato di lunghezza zero e nessuna dichiarata: vale il minimo assoluto di length_fits
        reference_km, label = np.nan, "assente"
    return Tracked(str(cammino["cammino_id"]), matching.sample_line(line), matching.LineIndex(line), reference_km, label)


def measured(tracked: Tracked, ids: Sequence[str], ctx: Context) -> Outcome:
    """Unione delle relazioni elencate con le quote misurate su di essa (esito e nota si aggiungono dopo)."""
    geometry = union_lines([ctx.geometries[r] for r in ids])
    # con una sola relazione l'unione e' la relazione: si riusano il suo indice e il suo campione, gia' calcolati
    index, sample = (ctx.index(ids[0]), ctx.sample(ids[0])) if len(ids) == 1 else (geometry, matching.sample_line(geometry))
    in_osm = matching.share_within(tracked.sample, index, NEAR)
    in_catalogue = matching.share_within(sample, tracked.index, NEAR)
    return Outcome("", list(ids), geometry, "", (in_osm, in_catalogue))


def union_verdict(tracked: Tracked, union: Outcome) -> str:
    """Abbinato se bastano entrambe le quote, oppure una e la lunghezza sta nell'intervallo. Altrimenti contenitore o
    parziale, come in rejected_label."""
    in_osm, in_catalogue = union.shares
    minimum = config.OSM_MATCH_SHARE_MIN
    if min(in_osm, in_catalogue) >= minimum:
        return config.ESITO_ABBINATO
    if max(in_osm, in_catalogue) >= minimum and length_fits(union.geometry.length, tracked.reference_km):
        return config.ESITO_ABBINATO
    return rejected_label(tracked, union)


def rejected_label(tracked: Tracked, union: Outcome) -> str:
    """Contenitore se il cammino sta dentro la relazione e questa e' oltre il massimo dell'intervallo di lunghezza;
    altrimenti la relazione ne copre soltanto un tratto (parziale)."""
    too_long = length_ratio(union.geometry.length, tracked.reference_km) > config.OSM_MATCH_LENGTH_RATIO_MAX
    return config.ESITO_CONTENITORE if union.shares[0] >= config.OSM_MATCH_SHARE_MIN and too_long else config.ESITO_PARZIALE


def _why_not(tracked: Tracked, union: Outcome, esito: str) -> str:
    """Perche' l'unione non e' il cammino, con i numeri che lo mostrano."""
    in_osm, in_catalogue = union.shares
    ratio = length_ratio(union.geometry.length, tracked.reference_km)
    meaning = "contiene il cammino" if esito == config.ESITO_CONTENITORE else "copre soltanto un tratto del cammino"
    if np.isnan(ratio):
        length = f"lunghezza OSM {union.geometry.length:.0f} m senza lunghezza di riferimento (minimo {config.OSM_MATCH_MIN_LENGTH_M} m)"
    else:
        length = f"lunghezza OSM / {tracked.reference_label} {ratio:.2f} (intervallo {config.OSM_MATCH_LENGTH_RATIO_MIN}-{config.OSM_MATCH_LENGTH_RATIO_MAX})"
    return f"la relazione {meaning}: quota catalogo in OSM {_ratio(in_osm)}, OSM nel catalogo {_ratio(in_catalogue)}, {length}"


def judge_candidates(tracked: Tracked, candidates: Sequence[str], below: int, ctx: Context) -> Outcome:
    """Tra le candidate (nome simile e quota sufficiente): scarta le troppo lunghe, tiene le piu' esterne, giudica l'unione.

    Le troppo lunghe si scartano prima dell'annidamento: un contenitore non deve nascondere la relazione giusta che
    contiene. Se sono tutte troppo lunghe l'esito non e' mai abbinato (contenitore o parziale, come in rejected_label) e le elenca.
    """
    containers = [r for r in candidates if length_ratio(ctx.length(r), tracked.reference_km) > config.OSM_MATCH_LENGTH_RATIO_MAX]
    kept, nested = outermost([r for r in candidates if r not in containers], ctx)
    union = measured(tracked, kept or containers, ctx)
    esito = union_verdict(tracked, union) if kept else rejected_label(tracked, union)  # tutte troppo lunghe: mai abbinato
    note = (
        f"relazioni con nome simile e quota sufficiente {len(candidates)}, troppo lunghe per essere questo cammino {len(containers)}, "
        f"contenute in un'altra e scartate {len(nested)}, nome simile ma sotto la quota {below}"
    )
    if esito != config.ESITO_ABBINATO:
        note += "; " + _why_not(tracked, union, esito)
    return dataclasses.replace(union, esito=esito, nota=note)


def no_candidate(near: Sequence[str], names: Mapping[str, float], shares: Mapping[str, tuple[float, float]]) -> str:
    """Nota di un cammino con tracce senza candidate: perche' nessuna relazione ha insieme nome e tracciato."""
    if near:
        best = max(near, key=lambda r: (max(shares[r]), r))
        return (f"nome simile e vicine {len(near)}; la migliore {best}: quota catalogo in OSM "
                f"{_ratio(shares[best][0])}, OSM nel catalogo {_ratio(shares[best][1])}")
    if names:
        return f"nome simile {len(names)}, nessuna entro {NEAR} m dal tracciato"
    return "nessuna relazione con nome simile"


def relation_shares(tracked: Tracked, names: Mapping[str, float], ctx: Context) -> dict[str, tuple[float, float]]:
    """Per le relazioni con nome simile e vicine al tracciato: (quota del catalogo in OSM, quota di OSM nel catalogo)."""
    near = [r for r in names if (tracked.cammino_id, r) in ctx.osm_in]
    return {r: (matching.share_within(tracked.sample, ctx.index(r), NEAR), ctx.osm_in[(tracked.cammino_id, r)]) for r in near}


def geometry_only_relations(tracked: Tracked, ctx: Context) -> list[str]:
    """Relazioni con nome diverso che stanno sul cammino per almeno OSM_MATCH_GEOMETRY_ONLY_MIN."""
    return sorted(
        r for r in ctx.strong.get(tracked.cammino_id, [])
        if matching.share_within(tracked.sample, ctx.index(r), NEAR) >= config.OSM_MATCH_GEOMETRY_ONLY_MIN
    )


def match_tracked(cammino: Mapping[str, object], line: BaseGeometry, names: Mapping[str, float], ctx: Context) -> Outcome:
    """Cammino con tracce: nome simile e quota in una delle due direzioni, poi lunghezza e unione; altrimenti solo geometria o nessuno."""
    tracked = track_of(cammino, line)
    shares = relation_shares(tracked, names, ctx)
    candidates = [r for r, pair in shares.items() if max(pair) >= config.OSM_MATCH_SHARE_MIN]
    geometry_only = geometry_only_relations(tracked, ctx)
    only_note = [f"solo geometria (nome diverso): {_ids(geometry_only)}"] if geometry_only else []
    if candidates:
        outcome = judge_candidates(tracked, candidates, len(shares) - len(candidates), ctx)
        return dataclasses.replace(outcome, nota="; ".join([outcome.nota, *only_note]))
    if geometry_only:
        return dataclasses.replace(measured(tracked, geometry_only, ctx), esito=config.ESITO_SOLO_GEOMETRIA, nota=only_note[0])
    return Outcome(config.ESITO_NESSUNO, [], None, no_candidate(list(shares), names, shares))


# --- Cammini senza tracce ---

def declared_regions(regioni: str) -> list[str]:
    """Slug delle regioni dichiarate dal catalogo (cdi le scrive come slug, MiTur come nomi)."""
    return [crossing.region_slug(name) for name in regioni.split(config.LIST_SEPARATOR) if name]


def strict_name_match(key: str, other: str) -> bool:
    """Per i cammini senza tracce: stesse parole, oppure una chiave contenuta nell'altra con almeno
    OSM_MATCH_CONTAINED_MIN_TOKENS parole (una parola sola contenuta non basta)."""
    first, second = set(key.split()), set(other.split())
    if first == second:
        return True
    contained = first if first <= second else second if second <= first else None
    return contained is not None and len(contained) >= config.OSM_MATCH_CONTAINED_MIN_TOKENS


@dataclass
class Selection:
    """Le relazioni candidate di un cammino senza tracce dopo i filtri su nome, regioni e annidamento (con i conteggi per la nota)."""
    loose: int  # nome simile (punteggio)
    strict: int = 0  # nome simile in senso stretto
    in_regions: int = 0
    contained_dropped: int = 0  # scartate perche' c'e' una relazione con lo stesso nome
    kept: list[str] = field(default_factory=list)
    nested: list[str] = field(default_factory=list)
    nota: str = ""  # se kept e' vuota, perche'


def _same_name_first(key: str, passing: list[str], ctx: Context) -> tuple[list[str], int]:
    """Con una relazione dallo stesso nome nelle regioni, quelle che contengono soltanto il nome (tappe di altri cammini
    che attraversano un luogo omonimo, "San Pellegrino in Alpe") non si aggiungono. Ritorna (relazioni, scartate)."""
    exact = [r for r in passing if set(key.split()) == set(ctx.relations.at[r, "chiave"].split())]
    return (exact, len(passing) - len(exact)) if exact else (passing, 0)


def select_trackless(cammino: Mapping[str, object], names: Mapping[str, float], ctx: Context) -> Selection:
    """Nome simile in senso stretto, relazione nelle regioni dichiarate, la piu' esterna tra quelle con lo stesso nome."""
    key, loose = str(cammino["chiave"]), len(names)
    strict = [r for r in names if strict_name_match(key, ctx.relations.at[r, "chiave"])]
    if not strict:
        return Selection(loose, nota=f"nessuna relazione con nome simile in senso stretto (scartate {loose}: una sola parola contenuta o nome solo somigliante)")
    regions = [ctx.regions[slug] for slug in declared_regions(str(cammino["regioni"]))]
    if not regions:
        return Selection(loose, len(strict), nota=f"nome simile {len(strict)}; regioni non dichiarate, controllo impossibile")
    shares = {r: region_share(ctx.sample(r), regions) for r in strict}
    passing = [r for r in strict if shares[r] >= config.OSM_MATCH_REGION_SHARE_MIN]
    if not passing:
        best = max(strict, key=lambda r: (shares[r], r))
        return Selection(loose, len(strict), nota=(
            f"nome simile {len(strict)}, nessuna nelle regioni dichiarate (la migliore {best}: quota {_ratio(shares[best])})"
        ))
    candidates, dropped = _same_name_first(key, passing, ctx)
    kept, nested = outermost(candidates, ctx)
    return Selection(loose, len(strict), len(passing), dropped, kept, nested)


def _rejected_note(unions: Mapping[tuple[str, ...], BaseGeometry], fitting: Sequence[tuple[str, ...]], declared: float) -> list[str]:
    """Nota dei gruppi la cui lunghezza non sta nell'intervallo (o e' sotto il minimo, senza lunghezza dichiarata)."""
    rejected = [group for group in unions if group not in fitting]
    if rejected and np.isnan(declared):
        return [f"piu' corte di {config.OSM_MATCH_MIN_LENGTH_M} m: " + ", ".join(f"{_ids(g)} {unions[g].length:.0f} m" for g in rejected)]
    if rejected:
        return ["lunghezza / dichiarata fuori intervallo: " + ", ".join(f"{_ids(g)} {length_ratio(unions[g].length, declared):.2f}" for g in rejected)]
    return []


def decide_groups(cammino: Mapping[str, object], selection: Selection, ctx: Context) -> Outcome:
    """Le relazioni tenute si riuniscono in gruppi di tappe; abbinato se un solo gruppo ha la lunghezza giusta, ambiguo se piu' d'uno."""
    groups = stage_groups(selection.kept, ctx)
    unions = {tuple(group): union_lines([ctx.geometries[r] for r in group]) for group in groups}
    declared = float(cammino["lunghezza_dichiarata_km"])
    fitting = [group for group in unions if length_fits(unions[group].length, declared)]
    notes = [
        f"nome simile {selection.strict} (scartate in senso stretto {selection.loose - selection.strict}), nelle regioni {selection.in_regions}, "
        f"scartate perche' c'e' lo stesso nome {selection.contained_dropped}, "
        f"contenute in un'altra e scartate {len(selection.nested)}, "
        f"gruppi {len(groups)}",
        *_rejected_note(unions, fitting, declared),
    ]
    if not fitting:
        return Outcome(config.ESITO_NESSUNO, [], None, "; ".join(notes))
    if len(fitting) == 1:
        return Outcome(config.ESITO_ABBINATO, list(fitting[0]), unions[fitting[0]], "; ".join(notes))
    notes.append("gruppi distinti: " + " / ".join(_ids(group) for group in fitting))
    return Outcome(config.ESITO_AMBIGUO, [r for group in fitting for r in group], None, "; ".join(notes))


def match_trackless(cammino: Mapping[str, object], names: Mapping[str, float], ctx: Context) -> Outcome:
    """Cammino senza tracce: sceglie le relazioni candidate (nome, regioni, annidamento), poi decide sui gruppi e sulla lunghezza."""
    selection = select_trackless(cammino, names, ctx)
    if not selection.kept:
        return Outcome(config.ESITO_NESSUNO, [], None, selection.nota)
    return decide_groups(cammino, selection, ctx)


# --- Uscite ---

def _rounded(value: float, decimals: int) -> float:
    return round(value, decimals) if pd.notna(value) else np.nan


def pilgrimage_flag(values: Iterable[str]) -> str:
    """Pellegrinaggio dalle relazioni abbinate: si se una e' si, no se almeno una dice di no; se nessuna dice nulla
    "non indicato" (il silenzio non e' un no). Il valore di ogni relazione e' quello di tags.religioso_osm."""
    raw = list(values)
    if tags.combine_religioso(tags.religioso_osm(value)[0] for value in raw) == config.SI:
        return config.SI
    return config.NO if any(raw) else config.OSM_MATCH_PILGRIMAGE_SILENT


def outcome_row(
    cammino: Mapping[str, object], line: BaseGeometry | None, outcome: Outcome, best_score: float, ctx: Context
) -> dict[str, object]:
    """Riga di abbinamenti.csv: quote e lunghezze si misurano sull'unione delle relazioni elencate, se l'esito ne ha una."""
    geom = outcome.geometry
    key = str(cammino["chiave"])
    score = max((matching.name_score(key, ctx.relations.at[r, "chiave"]) for r in outcome.ids), default=best_score)
    declared = float(cammino["lunghezza_dichiarata_km"])
    osm_length = geom.length if geom is not None else np.nan
    in_osm, in_catalogue = outcome.shares
    in_regions = np.nan
    if geom is not None and line is None:
        regions = [ctx.regions[slug] for slug in declared_regions(str(cammino["regioni"]))]
        in_regions = region_share(matching.sample_line(geom), regions)
    pilgrimage = ""
    if outcome.esito == config.ESITO_ABBINATO:
        pilgrimage = pilgrimage_flag(ctx.relations.at[r, "pilgrimage"] for r in outcome.ids)
    return {
        "cammino_id": cammino["cammino_id"], "fonte": cammino["fonte"], "nome": cammino["nome"],
        "stato_geometria": cammino["stato_geometria"], "esito": outcome.esito, "osm_ids": _ids(outcome.ids),
        "n_relazioni": len(outcome.ids), "punteggio_nome": _rounded(score, config.SCORE_DECIMALS),
        "quota_catalogo_in_osm": _rounded(in_osm, config.RATIO_DECIMALS),
        "quota_osm_in_catalogo": _rounded(in_catalogue, config.RATIO_DECIMALS),
        "lunghezza_osm_m": _rounded(osm_length, config.METERS_DECIMALS),
        "lunghezza_catalogo_m": _rounded(line.length if line is not None else np.nan, config.METERS_DECIMALS),
        "lunghezza_dichiarata_km": declared,
        "rapporto_dichiarata": _rounded(osm_length / 1000 / declared, config.RATIO_DECIMALS),
        "quota_nelle_regioni": _rounded(in_regions, config.RATIO_DECIMALS),
        "pellegrinaggio_osm": pilgrimage, "nota": outcome.nota,
    }


def _progress(label: str, started: float) -> None:
    rss_mb = psutil.Process().memory_info().rss / 2**20
    print(f"{label}: {time.time() - started:.0f} s, RSS {rss_mb:.0f} MB", flush=True)


def build_outputs(
    catalogue: pd.DataFrame, lines: Mapping[str, BaseGeometry], relations: pd.DataFrame,
    read_tratte: TratteReader, regions: Mapping[str, BaseGeometry],
) -> tuple[pd.DataFrame, gpd.GeoDataFrame]:
    """abbinamenti.csv (una riga per cammino del catalogo) e le geometrie OSM dei cammini abbinati.

    catalogue: come dedup.load_catalogue con regioni e lunghezza_dichiarata_km; relations: come load_relations;
    read_tratte: come osm_tratte_reader; regions: slug -> poligono, come region_polygons.
    """
    started = time.time()
    declared = pd.to_numeric(catalogue["lunghezza_dichiarata_km"], errors="coerce")
    catalogue = catalogue.assign(lunghezza_dichiarata_km=declared)
    names, best = name_candidates(catalogue, relations)
    _progress("nomi", started)
    osm_in = osm_in_catalogue(lines, read_tratte(None))
    _progress(f"quote OSM nel catalogo ({len(osm_in)} coppie vicine)", started)
    geometries = relation_geometries(read_tratte(needed_relations(lines, names, osm_in)))
    _progress(f"geometrie di {len(geometries)} relazioni", started)
    ctx = Context(relations, geometries, osm_in, regions, strong_geometry(osm_in, names))
    rows, matched = [], []
    for cammino in catalogue.to_dict("records"):
        cammino_id, line = cammino["cammino_id"], lines.get(cammino["cammino_id"])
        if line is not None:
            outcome = match_tracked(cammino, line, names[cammino_id], ctx)
        else:
            outcome = match_trackless(cammino, names[cammino_id], ctx)
        rows.append(outcome_row(cammino, line, outcome, best[cammino_id], ctx))
        ctx.forget()
        if outcome.esito == config.ESITO_ABBINATO:
            multi = shapely.MultiLineString(list(shapely.get_parts(outcome.geometry)))
            matched.append({"cammino_id": cammino_id, "osm_ids": _ids(outcome.ids), "geometry": multi})
    _progress("decisioni", started)
    frame = pd.DataFrame(rows, columns=list(config.OSM_MATCH_FIELDS))
    columns = [*config.OSM_MATCH_GEOMETRY_FIELDS, "geometry"]
    osm_lines = gpd.GeoDataFrame(pd.DataFrame(matched, columns=columns), geometry="geometry", crs=config.CRS_WORK)
    return frame, osm_lines


def write_outputs(frame: pd.DataFrame, osm_lines: gpd.GeoDataFrame) -> None:
    common.write_frame(frame, config.OSM_MATCH_DIR / config.OSM_MATCH_FILES["abbinamenti"])
    path = config.OSM_MATCH_DIR / config.OSM_MATCH_FILES["geometrie"]
    path.unlink(missing_ok=True)  # il GPKG si aggiunge ai layer esistenti: si riparte da zero
    osm_lines.to_file(path, layer=config.OSM_MATCH_LAYER, driver="GPKG", engine="pyogrio")


def run() -> None:
    catalogue = dedup.load_catalogue(extra_columns=("regioni", "lunghezza_dichiarata_km"))
    lines = dedup.cammino_lines(common.read_tratte(config.DEDUP_FONTI))
    index = read_osm_index()
    relations = load_relations(set(index["cammino_id"]))
    regions = region_polygons(crossing.load_comuni())
    frame, osm_lines = build_outputs(catalogue, lines, relations, osm_tratte_reader(index, lines), regions)
    write_outputs(frame, osm_lines)
    print(f"cammini {len(frame)}, relazioni OSM con geometria {len(relations)}")
    print(frame.groupby(["fonte", "esito"]).size().to_string())
