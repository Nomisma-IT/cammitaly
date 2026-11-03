"""Tratte OSM: relazioni di percorso dell'estratto Geofabrik, lette con pyosmium in tre passate.

1. relazioni selezionate (route a piedi, oppure pilgrimage=yes) con tag e membri;
2. way membri, con le liste dei loro nodi;
3. nodi referenziati, con le coordinate.
Tiene in memoria solo cio' che serve a costruire le geometrie. Una relazione e' un percorso intero: una tratta.
"""
import os
import threading
import time
from array import array
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
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


@dataclass
class Relation:
    tags: Tags
    ways: array  # id dei way membri, ruoli esclusi quelli di config.OSM_SKIPPED_ROLES
    subrelations: array  # id delle relazioni membro


@dataclass
class RelationOutput:
    row: dict[str, object]
    record: dict[str, Any] | None  # None se la relazione non ha geometria
    limbo: dict[str, str] | None


class RamWatchdog(threading.Thread):
    """Campiona l'RSS del processo e lo termina oltre il limite: il PC ha pochi GB liberi."""

    def __init__(self, limit_mb: int) -> None:
        super().__init__(daemon=True)
        self.limit_mb = limit_mb
        self.peak_mb = 0.0
        self._process = psutil.Process()

    def run(self) -> None:
        while True:
            rss = self._process.memory_info().rss / 2**20
            self.peak_mb = max(self.peak_mb, rss)
            if rss > self.limit_mb:
                print(f"osm: ABORT, RSS {rss:.0f} MB oltre il limite di {self.limit_mb} MB", flush=True)
                os._exit(config.OSM_RSS_ABORT_EXIT_CODE)  # exit duro: da un thread non si puo' sollevare
            time.sleep(config.OSM_RSS_SAMPLE_S)


# --- passata 1: relazioni ---

def is_wanted(tags: Mapping[str, str]) -> bool:
    if tags.get("type") not in config.OSM_RELATION_TYPES:
        return False
    return tags.get("route") in config.OSM_WALK_ROUTES or tags.get(config.OSM_PILGRIMAGE_KEY) == config.OSM_PILGRIMAGE_YES


def read_relations(pbf: Path) -> dict[int, Relation]:
    relations: dict[int, Relation] = {}
    processor = osmium.FileProcessor(str(pbf), osmium.osm.RELATION).with_filter(
        osm_filter.KeyFilter(*config.OSM_KEY_FILTER))
    for relation in processor:
        tags = {tag.k: tag.v for tag in relation.tags}
        if not is_wanted(tags):
            continue
        ways, subrelations = array("q"), array("q")
        for member in relation.members:
            if member.type == "w" and member.role not in config.OSM_SKIPPED_ROLES:
                ways.append(member.ref)
            elif member.type == "r":
                subrelations.append(member.ref)
        relations[relation.id] = Relation(tags, ways, subrelations)
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

    def coords(self, way_id: int) -> np.ndarray | None:
        """Array (n, 2) dei nodi risolti; None se il way non e' nell'estratto. I nodi mancanti si saltano."""
        i = np.searchsorted(self.found, way_id)
        if i >= len(self.found) or self.found[i] != way_id:
            return None
        refs = self.refs[self.offsets[i]:self.offsets[i + 1]]
        j = np.searchsorted(self.node_ids, refs)
        j[j >= len(self.node_ids)] = 0
        known = self.node_ids[j] == refs
        return np.column_stack([self.xs[j[known]], self.ys[j[known]]]) / config.OSM_COORD_SCALE


# --- geometria di una relazione ---

def resolve_ways(
    relation_id: int, relations: Mapping[int, Relation], memo: dict[int, tuple[set[int], int, int]], stack: tuple[int, ...] = ()
) -> tuple[set[int], int, int]:
    """(way effettivi, sotto-relazioni selezionate, sotto-relazioni non selezionate): unione dei discendenti."""
    if relation_id in memo:
        return memo[relation_id]
    relation = relations[relation_id]
    ways, selected, not_selected = set(relation.ways), 0, 0
    if len(stack) < config.OSM_MAX_DEPTH:
        for sub in relation.subrelations:
            if sub == relation_id or sub in stack:  # cicli: si ignorano
                continue
            if sub in relations:
                selected += 1
                ways |= resolve_ways(sub, relations, memo, (*stack, relation_id))[0]
            else:
                not_selected += 1
    memo[relation_id] = (ways, selected, not_selected)
    return memo[relation_id]


def route_geometry(way_ids: set[int], ways: WayGeometry) -> tuple[Any, int]:
    """MultiLineString in CRS_WORK (None se vuota) e numero di way assenti dall'estratto o con meno di 2 nodi."""
    lines, missing = [], 0
    for way_id in sorted(way_ids):
        coords = ways.coords(way_id)
        if coords is None or len(coords) < 2:
            missing += 1
        else:
            lines.append(coords)
    if not lines:
        return None, missing
    # I way di una relazione sono pezzi corti: si uniscono dove condividono l'estremo, prima di pulire e proiettare
    merged = linemerge(MultiLineString(lines))
    geom = common.build_geometry([np.asarray(part.coords) for part in getattr(merged, "geoms", [merged])])
    if geom is not None and geom.geom_type == "LineString":
        geom = MultiLineString([geom])  # tipo unico nel layer, anche scrivendo a blocchi
    return geom, missing


# --- righe di uscita ---

def route_name(tags: Mapping[str, str]) -> str:
    return next((tags[key] for key in config.OSM_NAME_TAGS if tags.get(key)), "")


def route_tipo(tags: Mapping[str, str]) -> str:
    return config.TIPO_BICI if tags.get("route") in config.OSM_BIKE_ROUTES else config.TIPO_ALTRO


def _stato(geom: Any, missing: int) -> str:
    if geom is None:
        return config.STATO_ASSENTE
    return config.STATO_PARZIALE if missing else config.STATO_OK


def _notes(missing: int, not_selected: int) -> str:
    notes = []
    if missing:
        notes.append(f"{missing} way mancanti dall'estratto")
    if not_selected:
        notes.append(f"{not_selected} sotto-relazioni non selezionate")
    return "; ".join(notes)


def relation_output(
    relation_id: int, relations: Mapping[int, Relation], ways: WayGeometry, memo: dict[int, tuple[set[int], int, int]]
) -> RelationOutput:
    way_ids, selected, not_selected = resolve_ways(relation_id, relations, memo)
    geom, missing = route_geometry(way_ids, ways)
    tags = relations[relation_id].tags
    cid, nome, tipo = f"{config.OSM_ID_PREFIX}{relation_id}", route_name(tags), route_tipo(tags)
    stats = common.line_stats(geom) if geom is not None else None
    walked = stats["lunghezza_m"] if stats and tipo != config.TIPO_BICI else 0.0  # la bici non e' cammino a piedi
    row: dict[str, object] = {
        "cammino_id": cid, "fonte": config.OSM_FONTE, "nome": nome, "regioni": "",
        "link": config.OSM_RELATION_URL.format(relation_id=relation_id),
        "lunghezza_dichiarata_km": "", "n_tappe_dichiarate": "", "n_tratte": int(geom is not None),
        "lunghezza_m": walked, "stato_geometria": _stato(geom, missing), "note": _notes(missing, not_selected),
        **{key: tags.get(key, "") for key in config.OSM_TAG_COLUMNS},
        "is_super": selected > 0, "n_children": selected, "n_children_not_selected": not_selected,
        "n_missing_ways": missing,
    }
    if geom is None:
        detail = f"nessun way con geometria ({len(way_ids)} way, {missing} mancanti)"
        return RelationOutput(row, None, common.limbo_row(cid, config.MOTIVO_CAMMINO_SENZA_GEOMETRIA, detail))
    record = {
        "tratta_id": cid, "cammino_id": cid, "fonte": config.OSM_FONTE, "nome": nome, "ordine": None, "tipo": tipo,
        **stats, "file_origine": _origin(config.OSM_PBF_FILE), "geometry": geom,
    }
    return RelationOutput(row, record, None)


def _origin(path: Path) -> str:
    return (path.relative_to(config.ROOT) if path.is_relative_to(config.ROOT) else path).as_posix()


# --- costruzione ed esecuzione ---

def _write_chunk(records: list[dict[str, Any]], append: bool) -> None:
    common.write_tratte(common.tratte_frame(records), config.OSM_FONTE, append=append)


def build_outputs(
    relations: Mapping[int, Relation], ways: WayGeometry
) -> Iterator[RelationOutput]:
    memo: dict[int, tuple[set[int], int, int]] = {}
    for relation_id in sorted(relations):
        yield relation_output(relation_id, relations, ways, memo)


def write_outputs(outputs: Iterator[RelationOutput]) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    """Scrive le tratte a blocchi man mano che arrivano; ritorna le righe dei cammini e del limbo."""
    rows: list[dict[str, object]] = []
    limbo: list[dict[str, str]] = []
    pending: list[dict[str, Any]] = []
    written = False
    for output in outputs:
        rows.append(output.row)
        if output.limbo:
            limbo.append(output.limbo)
        if output.record:
            pending.append(output.record)
        if len(pending) >= config.OSM_CHUNK_ROWS:
            _write_chunk(pending, append=written)
            written, pending = True, []
    if pending or not written:  # senza alcuna tratta si scrive comunque il file vuoto: niente file vecchi
        _write_chunk(pending, append=written)
    return rows, limbo


def build(pbf: Path, log: Log = print) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    """Le tre passate sul pbf, poi geometrie e file di uscita; ritorna (righe cammini, righe limbo)."""
    relations = read_relations(pbf)
    log(f"passata 1: {len(relations)} relazioni selezionate")
    way_ids = {way_id for relation in relations.values() for way_id in relation.ways}
    log(f"way unici referenziati: {len(way_ids)}")
    found, offsets, refs = read_ways(pbf, way_ids)
    del way_ids
    log(f"passata 2: {len(found)} way trovati, {len(refs)} riferimenti ai nodi")
    nodes = read_nodes(pbf, refs, log)
    log(f"passata 3: {len(nodes[0])} nodi caricati")
    rows, limbo = write_outputs(build_outputs(relations, WayGeometry((found, offsets, refs), nodes)))
    common.write_cammini(rows, config.OSM_FONTE, config.CAMMINI_OSM_FIELDS)
    common.write_limbo(limbo, config.OSM_FONTE)
    return rows, limbo


def run() -> None:
    if not config.OSM_PBF_FILE.exists():
        raise FileNotFoundError(f"manca {config.OSM_PBF_FILE}: eseguire prima 'fetch osm'")
    watchdog = RamWatchdog(config.OSM_MAX_RSS_MB)
    watchdog.start()
    started = time.time()

    def log(message: str) -> None:
        print(f"osm: [{time.time() - started:6.1f}s] picco {watchdog.peak_mb:5.0f} MB  {message}", flush=True)

    rows, limbo = build(config.OSM_PBF_FILE, log)
    log(f"scritti tratte, cammini ({len(rows)}) e limbo ({len(limbo)})")
    print("osm: stato_geometria", common.count_by(rows, "stato_geometria"))
    print("osm: valore di route", common.count_by(rows, "route"))
    print("osm: super-relazioni", sum(bool(row["is_super"]) for row in rows),
          "con way mancanti", sum(bool(row["n_missing_ways"]) for row in rows))
    print("osm: limbo", common.count_by(limbo, "motivo"))
