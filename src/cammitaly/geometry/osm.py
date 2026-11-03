"""Tratte OSM: relazioni di percorso dell'estratto Geofabrik, lette con pyosmium in tre passate.

1. relazioni selezionate (route a piedi, oppure pilgrimage=yes) con tag e membri;
2. way membri, con le liste dei loro nodi;
3. nodi referenziati, con le coordinate.
Tiene in memoria solo cio' che serve a costruire le geometrie. Una relazione e' un percorso intero: la tratta
principale (osm:r<id>); i membri con ruolo alternative, anche dentro le sotto-relazioni, formano la tratta delle varianti.
"""
import os
import re
import threading
import time
from array import array
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import osmium
import psutil
from osmium import filter as osm_filter
from shapely.geometry import MultiLineString
from shapely.ops import linemerge

from cammitaly import config
from cammitaly.geometry import common

Tags = dict[str, str]
IntColumns = tuple[array, array, array]  # tre colonne parallele; il significato e' nel docstring di ogni lettore
Log = Callable[[str], None]
VARIANT_ROLE = re.compile(config.OSM_VARIANT_ROLE_PATTERN)
DISTANCE = re.compile(config.OSM_DISTANCE_PATTERN, re.IGNORECASE)


def _ids() -> array:
    return array("q")


@dataclass
class Relation:
    tags: Tags
    main_ways: array = field(default_factory=_ids)
    variant_ways: array = field(default_factory=_ids)
    main_subs: array = field(default_factory=_ids)  # id delle sotto-relazioni con ruolo principale
    variant_subs: array = field(default_factory=_ids)  # id delle sotto-relazioni con ruolo alternative
    n_excluded: int = 0  # membri con ruolo approach o excursion

    @property
    def n_variant(self) -> int:
        return len(self.variant_ways) + len(self.variant_subs)


@dataclass
class Resolved:
    """Way effettivi di una relazione, inclusi quelli dei discendenti selezionati, divisi per ruolo ereditato."""
    main: set[int]
    variant: set[int]
    selected: int = 0  # sotto-relazioni selezionate e incluse
    not_selected: int = 0  # sotto-relazioni che non passano il filtro
    complete: bool = True  # False se profondita' o cicli hanno tagliato qualcosa: il risultato non si memorizza


@dataclass
class RouteGeometry:
    geom: Any  # MultiLineString in CRS_WORK, None se vuota
    stats: dict[str, float | int]
    missing_ways: int  # way assenti dall'estratto o con meno di 2 nodi risolti
    missing_nodes: int  # nodi assenti dentro way presenti


@dataclass
class RelationOutput:
    row: dict[str, object]
    records: list[dict[str, Any]]  # tratta principale e, se ha geometria, tratta delle varianti
    limbo: dict[str, str] | None


@dataclass
class BuildResult:
    rows: list[dict[str, object]]
    limbo: list[dict[str, str]]
    variant_flags: Counter  # True = tratta delle varianti


class RamWatchdog(threading.Thread):
    """Campiona l'RSS del processo e lo termina oltre il limite: il PC ha pochi GB liberi."""

    def __init__(self, limit_mb: int) -> None:
        super().__init__(daemon=True)
        self.limit_mb = limit_mb
        self.peak_mb = 0.0
        self._process = psutil.Process()
        self._halt = threading.Event()

    def run(self) -> None:
        while not self._halt.is_set():
            rss = self._process.memory_info().rss / 2**20
            self.peak_mb = max(self.peak_mb, rss)
            if rss > self.limit_mb:
                print(f"osm: ABORT, RSS {rss:.0f} MB oltre il limite di {self.limit_mb} MB", flush=True)
                os._exit(config.OSM_RSS_ABORT_EXIT_CODE)  # exit duro: da un thread non si puo' sollevare
                return
            self._halt.wait(config.OSM_RSS_SAMPLE_S)

    def stop(self) -> None:
        self._halt.set()
        self.join()


# --- passata 1: relazioni ---

def is_wanted(tags: Mapping[str, str]) -> bool:
    if tags.get(config.OSM_TYPE_KEY) not in config.OSM_RELATION_TYPES:
        return False
    is_walk = tags.get(config.OSM_ROUTE_KEY) in config.OSM_WALK_ROUTES
    return is_walk or tags.get(config.OSM_PILGRIMAGE_KEY) == config.OSM_PILGRIMAGE_YES


def add_member(relation: Relation, member_type: str, role: str, ref: int) -> None:
    """Smista un membro per ruolo: principale, variante (alternative...) o escluso (approach, excursion)."""
    if member_type not in (config.OSM_MEMBER_WAY, config.OSM_MEMBER_RELATION):
        return
    role = role.casefold()
    if role in config.OSM_EXCLUDED_ROLES:
        relation.n_excluded += 1
        return
    variant = VARIANT_ROLE.match(role) is not None
    if member_type == config.OSM_MEMBER_WAY:
        (relation.variant_ways if variant else relation.main_ways).append(ref)
    else:
        (relation.variant_subs if variant else relation.main_subs).append(ref)


def read_relations(pbf: Path) -> dict[int, Relation]:
    relations: dict[int, Relation] = {}
    processor = osmium.FileProcessor(str(pbf), osmium.osm.RELATION).with_filter(
        osm_filter.KeyFilter(*config.OSM_KEY_FILTER))
    for item in processor:
        tags = {tag.k: tag.v for tag in item.tags}
        if not is_wanted(tags):
            continue
        relation = Relation(tags)
        for member in item.members:
            add_member(relation, member.type, member.role, member.ref)
        relations[item.id] = relation
    return relations


# --- passate 2 e 3: way e nodi ---

def read_ways(pbf: Path, way_ids: set[int]) -> IntColumns:
    """(id dei way trovati, spostamenti, riferimenti ai nodi): i riferimenti del way i stanno in refs[off[i]:off[i+1]]."""
    tracker = osmium.IdTracker()
    for way_id in way_ids:
        tracker.add_way(way_id)
    found, offsets, refs = array("q"), array("q", [0]), array("q")
    for way in osmium.FileProcessor(str(pbf), osmium.osm.WAY).with_filter(tracker.id_filter()):
        found.append(way.id)
        refs.extend(node.ref for node in way.nodes)
        offsets.append(len(refs))
    return found, offsets, refs


def read_nodes(pbf: Path, refs: array, log: Log) -> IntColumns:
    """(id, x, y) dei soli nodi in refs; x e y sono interi a 1e-7 gradi.

    Si lavora a fette di id: un solo IdTracker per milioni di id sparsi supera 1,5 GB. I nodi sono ordinati
    nel pbf, quindi la lettura di una fetta si ferma all'ultimo id della fetta.
    """
    needed = np.unique(np.asarray(refs, dtype=np.int64))
    ids, xs, ys = array("q"), array("i"), array("i")
    for part in np.array_split(needed, config.OSM_NODE_SLICES):
        if len(part) == 0:
            continue
        tracker = osmium.IdTracker()
        for node_id in part.tolist():
            tracker.add_node(node_id)
        last = int(part[-1])
        for node in osmium.FileProcessor(str(pbf), osmium.osm.NODE).with_filter(tracker.id_filter()):
            if node.location.valid():
                ids.append(node.id)
                xs.append(node.location.x)
                ys.append(node.location.y)
            if node.id >= last:
                break
        log(f"fetta fino all'id {last}: nodi totali {len(ids)}")
    return ids, xs, ys


class WayGeometry:
    """Coordinate (lon, lat) di un way per ricerca binaria su array ordinati per id (come nel pbf)."""

    def __init__(self, ways: IntColumns, nodes: IntColumns) -> None:
        self.found, self.offsets, self.refs = (np.asarray(a, dtype=np.int64) for a in ways)
        self.node_ids = np.asarray(nodes[0], dtype=np.int64)
        self.xs, self.ys = (np.asarray(a, dtype=np.int32) for a in nodes[1:])

    def coords(self, way_id: int) -> tuple[np.ndarray, int] | None:
        """(array (n, 2) dei nodi risolti, numero di nodi mancanti); None se il way non e' nell'estratto."""
        i = np.searchsorted(self.found, way_id)
        if i >= len(self.found) or self.found[i] != way_id:
            return None
        refs = self.refs[self.offsets[i]:self.offsets[i + 1]]
        j = np.searchsorted(self.node_ids, refs)
        j[j >= len(self.node_ids)] = 0
        known = self.node_ids[j] == refs
        points = np.column_stack([self.xs[j[known]], self.ys[j[known]]]) / config.OSM_COORD_SCALE
        return points, int(len(refs) - known.sum())


# --- way di una relazione ---

def _add_child(out: Resolved, child: Resolved, variant: bool) -> None:
    # Un figlio raggiunto con ruolo alternative e' tutto variante, qualunque ruolo abbiano i suoi membri
    out.selected += 1
    out.complete = out.complete and child.complete
    if variant:
        out.variant |= child.main | child.variant
    else:
        out.main |= child.main
        out.variant |= child.variant


def resolve_ways(
    relation_id: int, relations: Mapping[int, Relation], memo: dict[int, Resolved], stack: tuple[int, ...] = ()
) -> Resolved:
    """Unione dei way della relazione e dei discendenti selezionati, per ruolo; un way principale non e' variante.

    Si memorizza solo un risultato completo: se la profondita' o un ciclo hanno tagliato dei figli, lo stesso
    nodo raggiunto da un altro cammino potrebbe darne uno diverso.
    """
    if relation_id in memo:
        return memo[relation_id]
    relation = relations[relation_id]
    out = Resolved(set(relation.main_ways), set(relation.variant_ways))
    path = (*stack, relation_id)
    for subs, variant in ((relation.main_subs, False), (relation.variant_subs, True)):
        for sub in subs:
            if sub in path:  # ciclo: i way di quella relazione sono gia' contati
                out.complete = False
            elif sub not in relations:
                out.not_selected += 1
            elif len(stack) >= config.OSM_MAX_DEPTH:
                out.complete = False
            else:
                _add_child(out, resolve_ways(sub, relations, memo, path), variant)
    out.variant -= out.main
    if out.complete:
        memo[relation_id] = out
    return out


def route_geometry(way_ids: set[int], ways: WayGeometry) -> RouteGeometry:
    """Geometria dei way dati; i way assenti o con meno di 2 nodi risolti si contano, non si inventano."""
    lines, missing_ways, missing_nodes = [], 0, 0
    for way_id in sorted(way_ids):
        found = ways.coords(way_id)
        if found is None or len(found[0]) < 2:
            missing_ways += 1
        else:
            lines.append(found[0])
            missing_nodes += found[1]
    geom = _merge_lines(lines) if lines else None
    return RouteGeometry(geom, common.line_stats(geom) if geom is not None else {}, missing_ways, missing_nodes)


def _merge_lines(lines: list[np.ndarray]) -> Any:
    # I way sono pezzi corti: si uniscono dove condividono l'estremo, prima di pulire e proiettare
    merged = linemerge(MultiLineString(lines))
    geom = common.build_geometry([np.asarray(part.coords) for part in getattr(merged, "geoms", [merged])])
    if geom is not None and geom.geom_type == "LineString":
        return MultiLineString([geom])  # tipo unico nel layer, anche scrivendo a blocchi
    return geom


# --- righe di uscita ---

def route_name(tags: Mapping[str, str]) -> str:
    return next((tags[key] for key in config.OSM_NAME_TAGS if tags.get(key)), "")


def is_bike(tags: Mapping[str, str]) -> bool:
    return tags.get(config.OSM_ROUTE_KEY) in config.OSM_BIKE_ROUTES


def declared_km(tags: Mapping[str, str]) -> float | None:
    """Tag distance in km (unita' m e mi convertite, km se manca); None se assente, nulla o non interpretabile."""
    match = DISTANCE.match(tags.get(config.OSM_DISTANCE_KEY, ""))
    if not match:
        return None
    unit = (match.group(2) or config.OSM_DISTANCE_DEFAULT_UNIT).lower()
    km = float(match.group(1).replace(",", ".")) * config.OSM_DISTANCE_TO_KM[unit]
    return km or None


def tratta_records(relation_id: int, tags: Mapping[str, str], main: RouteGeometry, variant: RouteGeometry) -> list[dict[str, Any]]:
    cid = f"{config.OSM_ID_PREFIX}{relation_id}"
    bike = is_bike(tags)
    pieces = (
        (cid, config.TIPO_BICI if bike else config.TIPO_ALTRO, main),
        (cid + config.OSM_VARIANT_SUFFIX, config.TIPO_BICI if bike else config.TIPO_VARIANTE, variant),
    )
    return [
        {
            "tratta_id": tratta_id, "cammino_id": cid, "fonte": config.OSM_FONTE, "nome": route_name(tags),
            "ordine": None, "tipo": tipo, **route.stats, "file_origine": common.relative_origin(config.OSM_PBF_FILE),
            "geometry": route.geom,
        }
        for tratta_id, tipo, route in pieces if route.geom is not None
    ]


def _stato(records: list[dict[str, Any]], main: RouteGeometry, variant: RouteGeometry) -> str:
    if not records:
        return config.STATO_ASSENTE
    incomplete = main.geom is None or main.missing_ways or variant.missing_ways
    return config.STATO_PARZIALE if incomplete else config.STATO_OK


def _notes(missing_ways: int, missing_nodes: int, not_selected: int) -> str:
    notes = []
    if missing_ways:
        notes.append(f"{missing_ways} way mancanti dall'estratto")
    if missing_nodes:
        notes.append(f"{missing_nodes} nodi mancanti dentro way presenti")
    if not_selected:
        notes.append(f"{not_selected} sotto-relazioni non selezionate")
    return "; ".join(notes)


def cammino_row(
    relation_id: int, relation: Relation, resolved: Resolved, routes: tuple[RouteGeometry, RouteGeometry],
    records: list[dict[str, Any]],
) -> dict[str, object]:
    main, variant = routes
    missing_ways, missing_nodes = main.missing_ways + variant.missing_ways, main.missing_nodes + variant.missing_nodes
    declared = declared_km(relation.tags)
    return {
        "cammino_id": f"{config.OSM_ID_PREFIX}{relation_id}", "fonte": config.OSM_FONTE,
        "nome": route_name(relation.tags), "regioni": "",
        "link": config.OSM_RELATION_URL.format(relation_id=relation_id),
        "lunghezza_dichiarata_km": round(declared, config.OSM_DECLARED_DECIMALS) if declared else "",
        "n_tappe_dichiarate": "", "n_tratte": len(records),
        "lunghezza_m": round(sum(r["lunghezza_m"] for r in records), config.METERS_DECIMALS),
        "stato_geometria": _stato(records, main, variant),
        "note": _notes(missing_ways, missing_nodes, resolved.not_selected),
        **{key: relation.tags.get(key, "") for key in config.OSM_TAG_COLUMNS},
        "is_super": resolved.selected > 0, "n_children": resolved.selected,
        "n_children_not_selected": resolved.not_selected, "n_missing_ways": missing_ways,
        "n_missing_nodes": missing_nodes, "n_membri_variante": relation.n_variant, "n_membri_esclusi": relation.n_excluded,
    }


def relation_limbo(row: dict[str, object], resolved: Resolved, main: RouteGeometry) -> dict[str, str] | None:
    """Cammino senza alcuna geometria, oppure lunghezza della tratta principale lontana da quella dichiarata."""
    cid, declared = str(row["cammino_id"]), row["lunghezza_dichiarata_km"]
    if row["stato_geometria"] == config.STATO_ASSENTE:
        n_ways = len(resolved.main) + len(resolved.variant)
        detail = f"nessun way con geometria ({n_ways} way, {row['n_missing_ways']} mancanti)"
        return common.limbo_row(cid, config.MOTIVO_CAMMINO_SENZA_GEOMETRIA, detail)
    if main.geom is None or not declared:
        return None
    km = main.stats["lunghezza_m"] / 1000
    ratio = km / float(declared)  # type: ignore[arg-type]
    if config.CDI_LENGTH_RATIO_MIN <= ratio <= config.CDI_LENGTH_RATIO_MAX:
        return None
    return common.limbo_row(cid, config.MOTIVO_LUNGHEZZA_ANOMALA, f"{km:.1f} km contro {float(declared):.1f} dichiarati (rapporto {ratio:.2f})")  # type: ignore[arg-type]


def relation_output(
    relation_id: int, relations: Mapping[int, Relation], ways: WayGeometry, memo: dict[int, Resolved]
) -> RelationOutput:
    relation, resolved = relations[relation_id], resolve_ways(relation_id, relations, memo)
    main, variant = route_geometry(resolved.main, ways), route_geometry(resolved.variant, ways)
    records = tratta_records(relation_id, relation.tags, main, variant)
    row = cammino_row(relation_id, relation, resolved, (main, variant), records)
    return RelationOutput(row, records, relation_limbo(row, resolved, main))


# --- costruzione ed esecuzione ---

def _write_chunk(records: list[dict[str, Any]], append: bool) -> None:
    common.write_tratte(common.tratte_frame(records), config.OSM_FONTE, append=append)


def build_outputs(relations: Mapping[int, Relation], ways: WayGeometry) -> Iterator[RelationOutput]:
    memo: dict[int, Resolved] = {}
    for relation_id in sorted(relations):
        yield relation_output(relation_id, relations, ways, memo)


def write_outputs(outputs: Iterator[RelationOutput]) -> BuildResult:
    """Scrive le tratte a blocchi man mano che arrivano; ritorna le righe dei cammini e del limbo."""
    result = BuildResult([], [], Counter())
    pending: list[dict[str, Any]] = []
    written = False
    for output in outputs:
        result.rows.append(output.row)
        if output.limbo:
            result.limbo.append(output.limbo)
        pending.extend(output.records)
        result.variant_flags.update(r["tratta_id"].endswith(config.OSM_VARIANT_SUFFIX) for r in output.records)
        if len(pending) >= config.OSM_CHUNK_ROWS:
            _write_chunk(pending, append=written)
            written, pending = True, []
    if pending or not written:  # senza alcuna tratta si scrive comunque il file vuoto: niente file vecchi
        _write_chunk(pending, append=written)
    return result


def build(pbf: Path, log: Log = print) -> BuildResult:
    """Le tre passate sul pbf, poi geometrie e file di uscita."""
    relations = read_relations(pbf)
    log(f"passata 1: {len(relations)} relazioni selezionate")
    way_ids = {w for r in relations.values() for w in (*r.main_ways, *r.variant_ways)}
    log(f"way unici referenziati: {len(way_ids)}")
    found, offsets, refs = read_ways(pbf, way_ids)
    del way_ids
    log(f"passata 2: {len(found)} way trovati, {len(refs)} riferimenti ai nodi")
    nodes = read_nodes(pbf, refs, log)
    log(f"passata 3: {len(nodes[0])} nodi caricati")
    result = write_outputs(build_outputs(relations, WayGeometry((found, offsets, refs), nodes)))
    common.write_cammini(result.rows, config.OSM_FONTE, config.CAMMINI_OSM_FIELDS)
    common.write_limbo(result.limbo, config.OSM_FONTE)
    return result


def run() -> None:
    if not config.OSM_PBF_FILE.exists():
        raise FileNotFoundError(f"manca {config.OSM_PBF_FILE}: eseguire prima 'fetch osm'")
    watchdog = RamWatchdog(config.OSM_MAX_RSS_MB)
    watchdog.start()
    started = time.time()

    def log(message: str) -> None:
        print(f"osm: [{time.time() - started:6.1f}s] picco {watchdog.peak_mb:5.0f} MB  {message}", flush=True)

    try:
        result = build(config.OSM_PBF_FILE, log)
        log(f"scritti tratte, cammini ({len(result.rows)}) e limbo ({len(result.limbo)})")
    finally:
        watchdog.stop()
    _print_summary(result, watchdog.peak_mb)


def _print_summary(result: BuildResult, peak_mb: float) -> None:
    rows = result.rows
    print("osm: tratte principali", result.variant_flags[False], "varianti", result.variant_flags[True])
    print("osm: stato_geometria", common.count_by(rows, "stato_geometria"))
    print("osm: valore di route", common.count_by(rows, "route"))
    print("osm: super-relazioni", sum(bool(row["is_super"]) for row in rows),
          "con way mancanti", sum(bool(row["n_missing_ways"]) for row in rows))
    print("osm: limbo", common.count_by(result.limbo, "motivo"))
    print(f"osm: picco RSS {peak_mb:.0f} MB")
