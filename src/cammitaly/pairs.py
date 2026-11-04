"""Coppie cammino-comune per i cammini distinti di dedup (distanza di 100 m, tracciati in bici inclusi, ordine lungo il percorso, etichette e deduplicazione).

Passi: le tracce di tutti i membri (anche bici) e, per i cammini senza tracce, la geometria OSM abbinata (la stessa regola
di dedup) si incrociano con i comuni a CROSSING_DISTANCE_M dal confine, una linea per traccia; ogni traccia passa al
cammino distinto del suo membro (una traccia elencata sotto due cammini conta per entrambi). Il mezzo di una coppia e'
la combinazione dei mezzi delle tracce entro d da quel comune; l'ordine dei comuni segue la dorsale del cammino: il
primo ingresso per i comuni che attraversa, la prima volta entro d per quelli solo vicini. Uscite in config.PAIRS_DIR; contratto delle colonne in config (sezione Coppie).
"""
import re
from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry.base import BaseGeometry

from cammitaly import calibration, config, crossing, dedup, matching, tags
from cammitaly.geometry import common

COMUNE = config.COMUNE_ID_COL
UNICO = config.PAIRS_UNICO_COL
NATURAL_PARTS = re.compile(config.NATURAL_SORT_PATTERN)
TrackRows = Mapping[str, pd.DataFrame]  # cammino_id del membro -> sue tracce


class PairsError(ValueError):
    """Un input manca o non e' coerente con gli altri (di solito un passo a monte da rilanciare)."""


@dataclass(frozen=True)
class Inputs:
    unici: pd.DataFrame  # cammini_unici.csv
    membri: pd.DataFrame  # membri.csv, principale per primo in ogni cammino distinto
    limbo: pd.DataFrame  # limbo_dedup.csv
    catalogue: pd.DataFrame  # indice cammino_id: regioni, dichiarata_km
    tracks: gpd.GeoDataFrame  # una riga per (traccia, membro), con la colonna mezzo
    labels: pd.DataFrame  # etichette_cammini.csv, indice cammino_id (anche le relazioni OSM)
    abbinamenti: pd.DataFrame  # osm_match/abbinamenti.csv

    @cached_property
    def members(self) -> dict[str, list[str]]:
        """Cammino distinto -> cammino_id dei membri, il principale per primo (calcolato una volta)."""
        return members_by_unico(self.membri)

    @cached_property
    def rows(self) -> dict[str, pd.DataFrame]:
        """cammino_id del membro -> sue tracce (calcolato una volta)."""
        return {member: frame for member, frame in self.tracks.groupby("cammino_id")}


# --- Lettura dei dati di partenza ---

def read_table(path: Path) -> pd.DataFrame:
    """CSV con tutte le celle come testo e i vuoti vuoti."""
    if not path.exists():
        raise PairsError(f"manca {path}: rilanciare il passo che lo produce (dedup, osm-match, tag)")
    return pd.DataFrame(common.read_csv(path))


def members_by_unico(membri: pd.DataFrame) -> dict[str, list[str]]:
    """Cammino distinto -> cammino_id dei membri, il principale per primo."""
    ordered = membri.assign(is_main=membri["ruolo"] != config.RUOLO_PRINCIPALE).sort_values("is_main", kind="stable")
    return ordered.groupby(UNICO, sort=False)["cammino_id"].agg(list).to_dict()


def load_tracks(catalogue_ids: Iterable[str]) -> gpd.GeoDataFrame:
    """Tracce dei tipi CROSSING_TIPI di cdi e MiTur, piu' la geometria OSM sostitutiva dei cammini senza tracce."""
    tratte = common.read_tratte(config.DEDUP_FONTI)
    substitutes = dedup.osm_substitute_tratte(catalogue_ids, tratte)
    tracks = pd.concat([tratte, substitutes], ignore_index=True)
    tracks = tracks[tracks["tipo"].isin(config.CROSSING_TIPI)].reset_index(drop=True)
    columns = ["tratta_id", "cammino_id", "fonte", "ordine", "tipo", "lunghezza_m", "geometry"]
    return gpd.GeoDataFrame(tracks[columns], geometry="geometry", crs=config.CRS_WORK)


def _standin_mezzo(tratta_id: str, by_relation: Mapping[str, Sequence[str]]) -> str | None:
    """Mezzo del sostituto OSM: l'id e' fatto delle relazioni abbinate unite da '|', se ne combinano i mezzi."""
    modes = [mode for relation in tratta_id.split(config.LIST_SEPARATOR) for mode in by_relation.get(relation, [])]
    return tags.combine_mezzo(modes) if modes else None


def track_mezzi(tracks: pd.DataFrame, labels: pd.DataFrame) -> list[str]:
    """Mezzo di ogni traccia da etichette_tratte.csv; il sostituto OSM (id = relazioni unite da '|') combina quelle delle relazioni."""
    catalogue_labels = labels[labels["fonte"] != config.OSM_FONTE]
    by_track = {(row.tratta_id, row.cammino_id): row.mezzo for row in catalogue_labels.itertuples()}
    osm_labels = labels[labels["fonte"] == config.OSM_FONTE]
    by_relation = osm_labels.groupby("cammino_id")["mezzo"].agg(list).to_dict()
    mezzi = [
        _standin_mezzo(row.tratta_id, by_relation) if row.fonte == config.OSM_FONTE else by_track.get((row.tratta_id, row.cammino_id))
        for row in tracks.itertuples()
    ]
    missing = [row.tratta_id for row, mezzo in zip(tracks.itertuples(), mezzi) if mezzo is None]
    if missing:
        raise PairsError(f"{len(missing)} tracce senza etichetta del mezzo (prima: {missing[0]}): rilanciare tag")
    return mezzi


def load_comune_table() -> pd.DataFrame:
    """PRO_COM_T con nome, sigla, codice e nome della provincia o citta' metropolitana, codice e nome della regione."""
    def read(path: Path, columns: list[str]) -> pd.DataFrame:
        frame = gpd.read_file(path, engine="pyogrio", encoding=config.ISTAT_ENCODING, ignore_geometry=True)
        return frame[columns]

    comuni = read(config.ISTAT_COMUNI_SHP, [COMUNE, config.COMUNE_NAME_COL, config.PROVINCE_CODE_COL, config.COMUNE_REGION_COL])
    province = read(config.ISTAT_PROVINCE_SHP, [config.PROVINCE_CODE_COL, config.PROVINCE_NAME_COL, config.PROVINCE_SIGLA_COL])
    regioni = read(config.ISTAT_REGIONI_SHP, [config.REGION_CODE_COL, config.REGION_NAME_COL])
    table = comuni.merge(province, on=config.PROVINCE_CODE_COL, how="left", validate="many_to_one")
    table = table.merge(regioni, on=config.REGION_CODE_COL, how="left", validate="many_to_one")
    if table.isna().any(axis=None):
        raise PairsError("comuni senza provincia o regione negli shapefile ISTAT")
    return table.rename(columns=config.PAIRS_COMUNE_COLUMNS)


def check_dedup_matches_catalogue(unici: pd.DataFrame, membri: pd.DataFrame, catalogue: pd.DataFrame) -> None:
    if set(membri["cammino_id"]) != set(catalogue.index) or set(membri[UNICO]) != set(unici[UNICO]):
        raise PairsError("dedup/membri.csv e cammini_unici.csv non coincidono con i cammini dei cataloghi: rilanciare dedup")


def load_inputs() -> Inputs:
    dedup_dir = config.DEDUP_DIR
    unici, membri = (read_table(dedup_dir / config.DEDUP_FILES[name]) for name in ("unici", "membri"))
    catalogue = dedup.load_catalogue(extra_columns=("regioni", "lunghezza_dichiarata_km")).set_index("cammino_id")
    catalogue["dichiarata_km"] = pd.to_numeric(catalogue["lunghezza_dichiarata_km"], errors="coerce")
    check_dedup_matches_catalogue(unici, membri, catalogue)
    tracks = load_tracks(catalogue.index)
    tracks["mezzo"] = track_mezzi(tracks, read_table(config.TAGS_TRATTE_CSV))
    return Inputs(
        unici=unici, membri=membri, limbo=read_table(dedup_dir / config.DEDUP_FILES["limbo"]),
        catalogue=catalogue, tracks=tracks, labels=read_table(config.TAGS_CAMMINI_CSV).set_index("cammino_id"),
        abbinamenti=read_table(config.OSM_MATCH_DIR / config.OSM_MATCH_FILES["abbinamenti"]),
    )


def first_place(texts: Iterable[str], cammino_id: str, index: calibration.PlaceIndex, regions: Mapping[str, object]) -> str | None:
    """PRO_COM_T del primo testo che indica un solo comune, per partenza o arrivo (regole di calibrazione; un nome uguale a quello di un solo comune vale anche se un altro comune comincia con lo stesso nome)."""
    for text in texts:
        outcome, code = calibration.match_place(text, index, regions.get(cammino_id), allow_prefix=True)  # type: ignore[arg-type]
        if outcome == config.MATCH_OK:
            return code
    return None


def member_starts(comuni: pd.DataFrame) -> dict[str, str]:
    """cammino_id camminiditalia.org -> PRO_COM_T del comune di partenza che pubblica, se il nome indica un solo comune:
    la partenza del cammino, poi quella della prima tappa."""
    cammini, tappe = read_table(config.CDI_CAMMINI_CSV), read_table(config.CDI_TAPPE_CSV)
    first_stage = tappe.assign(numero=tappe["ordine"].astype(int)).sort_values("numero").groupby("cammino_id")["partenza"].first()
    published = {config.CDI_ID_PREFIX + row.id: [row.partenza, first_stage.get(row.id, "")] for row in cammini.itertuples()}
    index = calibration.build_place_index(comuni)
    regions = calibration.regions_by_cammino(cammini, crossing.load_region_codes())
    resolved = {cid: first_place(texts, cid, index, regions) for cid, texts in published.items()}
    return {cid: code for cid, code in resolved.items() if code}


def published_starts(membri: pd.DataFrame, resolved: Mapping[str, str]) -> dict[str, str]:
    """Cammino distinto -> PRO_COM_T del comune di partenza pubblicato da un suo membro (principale prima).

    Contano solo i membri che rappresentano il cammino intero (principale, duplicato): la partenza di una sezione non e'
    la partenza del cammino. resolved: member_starts."""
    members = members_by_unico(membri[membri["ruolo"] != config.RUOLO_SEZIONE])
    found = {unico: next((resolved[cid] for cid in ids if cid in resolved), None) for unico, ids in members.items()}
    return {unico: code for unico, code in found.items() if code}


# --- Incrocio ---

def cross_tracks(tracks: gpd.GeoDataFrame, comuni: gpd.GeoDataFrame) -> pd.DataFrame:
    """Comuni a distanza <= d da ogni traccia; la colonna 'traccia' e' la riga della traccia in `tracks`."""
    lines = gpd.GeoSeries(tracks.geometry.to_numpy(), index=tracks.index, crs=config.CRS_WORK)
    found = crossing.comuni_entro(lines, comuni, config.CROSSING_DISTANCE_M)
    return found.rename(columns={"cammino_id": "traccia"})


def aggregate_pairs(found: pd.DataFrame, tracks: pd.DataFrame, membri: pd.DataFrame) -> pd.DataFrame:
    """Una riga per (cammino distinto, comune): distanza minima, dentro se una traccia entra, mezzo combinato."""
    repeated = membri.loc[membri["cammino_id"].duplicated(), "cammino_id"]
    if not repeated.empty:
        raise PairsError(f"membri.csv: il cammino {repeated.iloc[0]} e' membro di piu' cammini distinti: rilanciare dedup")
    unico_of = membri.set_index("cammino_id")[UNICO]
    rows = found.join(tracks[["cammino_id", "mezzo"]], on="traccia")
    rows[UNICO] = rows["cammino_id"].map(unico_of)
    if rows[UNICO].isna().any():
        raise PairsError("tracce di cammini che non sono membri di nessun cammino distinto: rilanciare dedup")
    grouped = rows.groupby([UNICO, COMUNE], sort=False)
    pairs = grouped.agg(distanza_m=("distanza_m", "min"), dentro=("dentro", "any"), mezzo=("mezzo", tags.combine_mezzo))
    return pairs.reset_index()


# --- Ordine dei comuni lungo il percorso ---

@dataclass(frozen=True)
class Spine:
    """Dorsale campionata: corde consecutive (al massimo PAIRS_ROUTE_STEP_M) con la posizione di inizio lungo la dorsale."""

    chords: np.ndarray
    starts: np.ndarray
    tree: shapely.STRtree


def natural_key(text: str) -> list[object]:
    """Chiave di ordinamento naturale: 'mitur:x:tappa 2' viene prima di 'mitur:x:tappa 10'."""
    return [int(part) if part.isdigit() else part for part in NATURAL_PARTS.split(text)]


def stage_key(row: object) -> tuple[bool, int, list[object]]:
    """Per ordine di tappa, le senza ordine in fondo; a parita' ordine naturale dell'id della traccia."""
    ordine = row.ordine  # type: ignore[attr-defined]
    missing = bool(pd.isna(ordine))
    return missing, 0 if missing else int(ordine), natural_key(row.tratta_id)  # type: ignore[attr-defined]


def _total_length(rows: pd.DataFrame) -> float:
    return float(rows["lunghezza_m"].sum())


def _pick(candidates: Mapping[str, pd.DataFrame], members: Sequence[str]) -> pd.DataFrame:
    """Il principale se ce l'ha, altrimenti il membro con piu' lunghezza."""
    chosen = members[0] if members[0] in candidates else max(candidates, key=lambda m: _total_length(candidates[m]))
    return candidates[chosen]


def _nonempty(members: Sequence[str], rows: TrackRows, select: Callable[[pd.DataFrame], pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Per ogni membro con tracce la selezione `select`, tenendo solo quelle non vuote."""
    frames = {member: select(rows[member]) for member in members if member in rows}
    return {member: frame for member, frame in frames.items() if not frame.empty}


def catalogue_rows(frame: pd.DataFrame, tipo: str | None) -> pd.DataFrame:
    """Tracce di catalogo (non sostitute OSM) del tipo dato; PAIRS_TIPO_QUALUNQUE le prende tutte, None nessuna."""
    own = frame[frame["fonte"] != config.OSM_FONTE]
    return own if tipo == config.PAIRS_TIPO_QUALUNQUE else own[own["tipo"] == tipo]


def stage_tipo(members: Sequence[str], rows: TrackRows) -> str | None:
    """Il tipo di traccia che fa le tappe del cammino distinto: il primo di PAIRS_SPINE_TIPI che almeno un membro ha
    (None se nessun membro ha tracce di catalogo)."""
    for tipo in config.PAIRS_SPINE_TIPI:
        if any(not catalogue_rows(rows[member], tipo).empty for member in members if member in rows):
            return tipo
    return None


def spine_geometries(members: Sequence[str], rows: TrackRows) -> list[BaseGeometry]:
    """Linee della dorsale in ordine di percorso (members: il principale per primo).

    Le tracce di tappa del principale; se non ne ha, quelle del membro che le ha piu' lunghe; se nessun membro ha tappe
    lo stesso con le tracce altro e poi con qualunque tipo (PAIRS_SPINE_TIPI); se non c'e' alcuna traccia di catalogo la
    geometria OSM sostitutiva (principale prima, poi la piu' lunga); altrimenti nessuna dorsale.
    """
    tipo = stage_tipo(members, rows)
    if tipo is not None:
        stages = _nonempty(members, rows, lambda frame: catalogue_rows(frame, tipo))
        return [row.geometry for row in sorted(_pick(stages, members).itertuples(), key=stage_key)]
    standins = _nonempty(members, rows, lambda frame: frame[frame["fonte"] == config.OSM_FONTE])
    return list(_pick(standins, members).geometry) if standins else []


def build_spines(unici: Iterable[str], inputs: Inputs) -> dict[str, list[BaseGeometry]]:
    """Le linee della dorsale di ogni cammino distinto dato, una volta sola (vuota se non ha alcuna traccia)."""
    return {unico: spine_geometries(inputs.members[unico], inputs.rows) for unico in unici}


def merge_in_stored_order(geom: BaseGeometry) -> list[BaseGeometry]:
    """Parti della linea riunite dove si toccano (linemerge), nell'ordine e nel verso in cui erano memorizzate.

    linemerge da solo riordina le parti per posizione e puo' invertirne il verso: su una dorsale che va verso ovest o
    nord scambierebbe i tratti. Dove non c'e' nulla da unire restano le parti memorizzate; altrimenti ogni parte unita
    prende il posto e il verso della prima parte memorizzata che contiene.
    """
    stored = list(shapely.get_parts(geom))
    merged = list(shapely.get_parts(shapely.line_merge(geom)))
    if len(merged) == len(stored):
        return stored
    tree = shapely.STRtree(stored)
    keyed = []
    for line in merged:
        covered = tree.query(line, predicate="covers")
        first = int(covered.min()) if len(covered) else len(stored)
        if len(covered):
            ends = shapely.line_locate_point(line, shapely.points([stored[first].coords[0], stored[first].coords[-1]]))
            line = shapely.reverse(line) if ends[0] > ends[1] else line
        keyed.append((first, line))
    return [line for _, line in sorted(keyed, key=lambda item: item[0])]


Unit = list[BaseGeometry]  # linee consecutive nel verso del percorso: una tappa (le sue parti) o una parte sola


def _unit_ends(unit: Unit) -> tuple[np.ndarray, np.ndarray]:
    return np.array(unit[0].coords[0][:2]), np.array(unit[-1].coords[-1][:2])


def _reversed(unit: Unit) -> Unit:
    return [shapely.reverse(line) for line in reversed(unit)]


def orient_chain(units: Sequence[Unit]) -> list[Unit]:
    """Ogni unita' nel verso che continua dalla fine della precedente; la prima nel verso che guarda la seconda.

    Serve per le parti dentro una tappa, che stanno nell'ordine memorizzato (le tappe tra loro seguono chain_stages).
    Una tappa disegnata al contrario ha le parti in ordine inverso: si rovescia l'unita' intera, ordine delle sue linee
    e verso di ciascuna.
    """
    if len(units) < 2:
        return list(units)
    first_start, first_end = _unit_ends(units[0])
    next_ends = _unit_ends(units[1])

    def toward_next(point: np.ndarray) -> float:
        return min(float(np.linalg.norm(point - other)) for other in next_ends)

    oriented = [units[0] if toward_next(first_end) <= toward_next(first_start) else _reversed(units[0])]
    for unit in units[1:]:
        previous_end = _unit_ends(oriented[-1])[1]
        start, end = _unit_ends(unit)
        keep = np.linalg.norm(previous_end - start) <= np.linalg.norm(previous_end - end)
        oriented.append(unit if keep else _reversed(unit))
    return oriented


def _begin_near(unit: Unit, polygon: BaseGeometry) -> Unit:
    """L'unita' nel verso che comincia piu' vicino al poligono (il comune di partenza pubblicato)."""
    start, end = _unit_ends(unit)
    near_start = shapely.distance(shapely.Point(start), polygon)
    return unit if near_start <= shapely.distance(shapely.Point(end), polygon) else _reversed(unit)


def _begin_toward(unit: Unit, others: Sequence[Unit]) -> Unit:
    """L'unita' nel verso che finisce piu' vicino agli estremi delle altre (senza partenza pubblicata)."""
    if not others:
        return unit
    targets = np.array([end for other in others for end in _unit_ends(other)])
    start, end = _unit_ends(unit)

    def gap(point: np.ndarray) -> float:
        return float(np.min(np.linalg.norm(targets - point, axis=1)))

    return unit if gap(end) <= gap(start) else _reversed(unit)


def _nearest_unit(end: np.ndarray, stages: Sequence[Unit], remaining: Sequence[int]) -> int:
    """La tappa non ancora usata con l'estremo piu' vicino a `end`. Le tappe attaccate alla fine della catena (a meno di
    PAIRS_CHAIN_TIE_M) sono a pari merito e vince la prima, quella di ordine minore; se nessuna e' attaccata (cambio di
    ramo) vince la piu' vicina, e a distanza uguale ancora la prima."""
    gaps = {i: min(float(np.linalg.norm(end - point)) for point in _unit_ends(stages[i])) for i in remaining}
    attached = [i for i in remaining if gaps[i] <= config.PAIRS_CHAIN_TIE_M]
    return attached[0] if attached else min(remaining, key=gaps.__getitem__)


def chain_stages(stages: Sequence[Unit], start: BaseGeometry | None = None) -> list[Unit]:
    """Le tappe in catena geografica. `stages` e' in ordine di tappa (ordine, poi id naturale).

    Si parte dalla tappa di ordine minore; se un membro pubblica un comune di partenza, dalla tappa piu' vicina a quel
    comune, nel verso che comincia vicino ad esso. Poi si aggiunge ogni volta la tappa non usata con l'estremo piu'
    vicino alla fine della catena, nel verso che comincia da quell'estremo: i rami che ricominciano la numerazione non
    si intrecciano. A pari distanza vince l'ordine di tappa.
    """
    if not stages:
        return []
    remaining = list(range(len(stages)))
    if start is None:
        first = remaining.pop(0)
        chain = [_begin_toward(stages[first], [stages[i] for i in remaining])]
    else:
        first = min(remaining, key=lambda i: min(float(shapely.distance(line, start)) for line in stages[i]))
        remaining.remove(first)
        chain = [_begin_near(stages[first], start)]
    while remaining:
        end = _unit_ends(chain[-1])[1]
        chosen = _nearest_unit(end, stages, remaining)
        remaining.remove(chosen)
        first_point, last_point = _unit_ends(stages[chosen])
        keep = np.linalg.norm(end - first_point) <= np.linalg.norm(end - last_point)
        chain.append(stages[chosen] if keep else _reversed(stages[chosen]))
    return chain


def spine_stages(geoms: Sequence[BaseGeometry], start: BaseGeometry | None = None) -> list[Unit]:
    """Le tappe della dorsale in catena geografica (chain_stages); di ognuna le parti riunite (merge_in_stored_order),
    in ordine memorizzato e orientate in sequenza (orient_chain)."""
    stages = []
    for geom in geoms:
        parts = [[part] for part in merge_in_stored_order(geom) if part.length > 0]
        if parts:
            stages.append([line for unit in orient_chain(parts) for line in unit])
    return chain_stages(stages, start)


def sample_spine(geoms: Sequence[BaseGeometry], start: BaseGeometry | None = None) -> Spine | None:
    """Dorsale in corde. La posizione somma le lunghezze delle parti precedenti, quindi i vuoti tra una parte e la
    successiva non contano."""
    chords, starts, offset = [], [], 0.0
    for part in (line for stage in spine_stages(geoms, start) for line in stage):
        distances = np.append(np.arange(0.0, part.length, config.PAIRS_ROUTE_STEP_M), part.length)
        coords = shapely.get_coordinates(shapely.line_interpolate_point(part, distances))
        chords.append(shapely.linestrings(np.stack([coords[:-1], coords[1:]], axis=1)))
        starts.append(offset + distances[:-1])
        offset += part.length
    if not chords:
        return None
    all_chords = np.concatenate(chords)
    return Spine(all_chords, np.concatenate(starts), shapely.STRtree(all_chords))


def nearest_positions(spine: Spine, polygons: np.ndarray) -> np.ndarray:
    """Posizione del punto della dorsale piu' vicino a ciascun poligono (per i comuni mai entro d dalla dorsale)."""
    polygon_idx, chord_idx = spine.tree.query_nearest(polygons, all_matches=False)
    chords = spine.chords[chord_idx]
    nearest = shapely.get_point(shapely.shortest_line(chords, polygons[polygon_idx]), 0)
    positions = np.full(len(polygons), np.nan)
    positions[polygon_idx] = spine.starts[chord_idx] + shapely.line_locate_point(chords, nearest)
    return positions


def first_chord_positions(spine: Spine, polygons: np.ndarray, predicate: str, **options: float) -> np.ndarray:
    """Per ogni poligono l'inizio della prima corda che soddisfa il predicato (intersects, dwithin); NaN se nessuna."""
    polygon_idx, chord_idx = spine.tree.query(polygons, predicate=predicate, **options)
    positions = np.full(len(polygons), np.nan)
    if len(polygon_idx):
        first = pd.Series(spine.starts[chord_idx]).groupby(polygon_idx).min()
        positions[first.index.to_numpy()] = first.to_numpy()
    return positions


def route_positions(spine: Spine, polygons: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(posizione, attraversato) lungo la dorsale per ogni poligono.

    Attraversato (la dorsale tocca il comune): posizione = primo ingresso. Solo vicino: la prima posizione entro d.
    Mai entro d dalla dorsale (lo raggiungono varianti, bici o altri membri): il punto piu' vicino della dorsale.
    """
    positions = first_chord_positions(spine, polygons, "intersects")
    entered = ~np.isnan(positions)
    band = np.flatnonzero(~entered)
    if len(band):
        positions[band] = first_chord_positions(spine, polygons[band], "dwithin", distance=config.CROSSING_DISTANCE_M)
    missing = np.flatnonzero(np.isnan(positions))
    if len(missing):
        positions[missing] = nearest_positions(spine, polygons[missing])
    return positions, entered


def rank_comuni(codes: pd.Series, positions: np.ndarray | None, entered: np.ndarray | None = None) -> np.ndarray:
    """1..n per posizione crescente; a parita' prima i comuni attraversati, poi il codice PRO_COM_T. Senza dorsale solo per codice."""
    frame = pd.DataFrame({
        COMUNE: codes.to_numpy(), "posizione": 0.0 if positions is None else positions,
        "solo_vicino": False if entered is None else ~entered,
    })
    ranks = np.empty(len(frame), dtype=int)
    ranks[frame.sort_values(["posizione", "solo_vicino", COMUNE]).index.to_numpy()] = np.arange(1, len(frame) + 1)
    return ranks


def split_starts(
    membri: pd.DataFrame, resolved: Mapping[str, str], comuni_of: Mapping[str, Collection[str]]
) -> tuple[dict[str, str], dict[str, str]]:
    """(accettate, scartate): una partenza pubblicata vale solo se quel comune sta tra i comuni del cammino distinto del
    membro (entro d dalle sue tracce); altrimenti e' un omonimo lontano dal percorso (calibrazione: "Trevignano"). I membri
    di cammini senza comuni non si considerano: non c'e' niente da ancorare."""
    unico_of = membri.set_index("cammino_id")[UNICO]
    accepted: dict[str, str] = {}
    rejected: dict[str, str] = {}
    for cammino_id, code in resolved.items():
        unico = unico_of.get(cammino_id)
        if unico in comuni_of:
            (accepted if code in comuni_of[unico] else rejected)[cammino_id] = code
    return accepted, rejected


def dangling_endpoints(geoms: Sequence[BaseGeometry]) -> np.ndarray:
    """Estremi (x, y) delle tappe che non hanno l'estremo di un'altra tappa entro PAIRS_CHAIN_TIE_M: i capi liberi della
    rete di tappe. Gli estremi della stessa tappa non si contano tra loro."""
    units = spine_stages(geoms)
    ends = np.array([point for unit in units for point in _unit_ends(unit)]).reshape(-1, 2)
    owner = np.repeat(np.arange(len(units)), 2)
    gaps = np.linalg.norm(ends[:, None, :] - ends[None, :, :], axis=2)
    gaps[owner[:, None] == owner[None, :]] = np.inf
    return ends[gaps.min(axis=1) > config.PAIRS_CHAIN_TIE_M] if len(units) else ends


def _section_start(member: str, rows: TrackRows, polygons: Mapping[str, BaseGeometry], resolved: Mapping[str, str]) -> BaseGeometry | None:
    """Punto d'inizio della dorsale del membro (sezione): dalla sua partenza pubblicata o dalla sua tappa di ordine minore."""
    geoms = spine_geometries([member], rows)
    if not geoms:
        return None
    start = polygons[resolved[member]] if member in resolved else None
    return shapely.Point(_unit_ends(spine_stages(geoms, start)[0])[0])


def section_anchors(
    membri: pd.DataFrame, rows: TrackRows, polygons: Mapping[str, BaseGeometry], resolved: Mapping[str, str],
    spines: Mapping[str, Sequence[BaseGeometry]], skip: Iterable[str] = (),
) -> dict[str, BaseGeometry]:
    """Per i cammini distinti senza partenza pubblicata da un membro intero: il punto d'inizio della dorsale della sezione
    piu' lunga (tra quelle con tracce) che sta a un capo libero della rete di tappe del cammino intero (spines, entro
    PAIRS_ANCHOR_MAX_M); se la piu' lunga parte da un incrocio a meta' rete si prova la successiva; se nessuna va bene,
    nessuna ancora (la dorsale parte dalla tappa di ordine minore).

    Per geometria, non per nome. `skip`: i cammini distinti che hanno gia' una partenza pubblicata."""
    skipped = set(skip)
    anchors: dict[str, BaseGeometry] = {}
    for unico, group in membri[membri["ruolo"] == config.RUOLO_SEZIONE].groupby(UNICO):
        if unico in skipped or not spines.get(unico):
            continue
        free_ends = dangling_endpoints(spines[unico])
        for member in sorted((m for m in group["cammino_id"] if m in rows), key=lambda m: _total_length(rows[m]), reverse=True):
            point = _section_start(member, rows, polygons, resolved)
            if point is not None and len(free_ends) and np.min(np.linalg.norm(free_ends - [point.x, point.y], axis=1)) <= config.PAIRS_ANCHOR_MAX_M:
                anchors[unico] = point
                break
    return anchors


def chain_anchors(
    membri: pd.DataFrame, rows: TrackRows, polygons: Mapping[str, BaseGeometry], resolved: Mapping[str, str],
    spines: Mapping[str, Sequence[BaseGeometry]],
) -> dict[str, BaseGeometry]:
    """Dove comincia la dorsale di ogni cammino distinto che ha un'indicazione: il comune di partenza pubblicato da un
    membro intero (vince sempre), altrimenti il punto d'inizio di una sezione a un capo libero della rete. Gli altri
    partono dalla tappa di ordine minore."""
    starts = published_starts(membri, resolved)
    anchors = section_anchors(membri, rows, polygons, resolved, spines, skip=starts)
    return {**anchors, **{unico: polygons[code] for unico, code in starts.items()}}


def route_order(
    pairs: pd.DataFrame, spines: Mapping[str, Sequence[BaseGeometry]], polygons: Mapping[str, BaseGeometry],
    anchors: Mapping[str, BaseGeometry] | None = None,
) -> pd.Series:
    """Colonna ordine (Int64) allineata a `pairs`: i comuni di ogni cammino distinto numerati lungo la dorsale.

    spines: build_spines; anchors: chain_anchors (dove comincia la dorsale, se lo si sa)."""
    anchors = anchors or {}
    ordine = pd.Series(pd.NA, index=pairs.index, dtype="Int64")
    for unico, group in pairs.groupby(UNICO):
        spine = sample_spine(spines[unico], anchors.get(unico))
        positions = entered = None
        if spine is not None:
            positions, entered = route_positions(spine, np.array([polygons[code] for code in group[COMUNE]], dtype=object))
        ordine.loc[group.index] = rank_comuni(group[COMUNE], positions, entered)
    return ordine


# --- Tabella dei cammini ---

def osm_relations_counted(abbinamenti: pd.DataFrame) -> dict[str, list[str]]:
    """Cammino del catalogo -> relazioni OSM le cui etichette (religioso e le altre) contano per lui.

    UNICO punto in cui si decide quali righe OSM contano: oggi tutte quelle dell'abbinamento (esito abbinato).
    Una correzione del flag di pellegrinaggio OSM va fatta qui e solo qui.
    """
    matched = abbinamenti[abbinamenti["esito"] == config.ESITO_ABBINATO]
    return {row.cammino_id: row.osm_ids.split(config.LIST_SEPARATOR) for row in matched.itertuples()}


def label_columns(members: Sequence[str], labels: pd.DataFrame, counted: Mapping[str, list[str]]) -> dict[str, str]:
    """religioso (basta un si), la sua evidenza e le etichette multivalore (unione) sulle righe dei membri e delle
    relazioni OSM contate. L'evidenza e' quella delle sole righe che dicono si, altrimenti EVIDENZA_SILENTE: unire anche
    quelle dei no darebbe testi contraddittori ("Catalogo dei cammini religiosi" accanto a "nessuna fonte")."""
    ids = [*members, *(relation for member in members for relation in counted.get(member, []))]
    missing = [label_id for label_id in ids if label_id not in labels.index]
    if missing:
        raise PairsError(f"nessuna etichetta per {missing[0]}: rilanciare tag")
    rows = labels.loc[ids]
    religious = rows[rows["religioso"] == config.SI]
    evidence = tags.combine_multi(religious["evidenza_religioso"]) if not religious.empty else config.EVIDENZA_SILENTE
    columns = {field: tags.combine_multi(rows[field]) for field in config.PAIRS_MULTI_FIELDS}
    return {"religioso": tags.combine_religioso(rows["religioso"]), "evidenza_religioso": evidence, **columns}


def cammino_mezzo(members: Sequence[str], rows: TrackRows, labels: pd.DataFrame) -> str:
    """Mezzo su tutte le tracce dei membri; un membro senza tracce porta il mezzo della sua etichetta di cammino."""
    modes = [mode for member in members if member in rows for mode in rows[member]["mezzo"]]
    modes += [labels.at[member, "mezzo"] for member in members if member not in rows]
    return tags.combine_mezzo(modes)


def region_names(comune_table: pd.DataFrame) -> dict[str, str]:
    """Slug della regione -> nome ISTAT."""
    return {crossing.region_slug(name): name for name in comune_table["regione"].unique()}


def canonical_regions(texts: Iterable[str], names: Mapping[str, str]) -> str:
    """Unione delle regioni dei membri come nomi ISTAT (cdi le scrive come slug, MiTur come nomi)."""
    found = set()
    for text in texts:
        for part in filter(None, text.split(config.LIST_SEPARATOR)):
            slug = crossing.region_slug(part)
            if slug not in names:
                raise PairsError(f"regione non riconosciuta: {part!r}")
            found.add(names[slug])
    return tags.join_values(found)


def union_length_m(geoms: Sequence[BaseGeometry]) -> float:
    """Lunghezza dell'unione di linee che possono essere copie o alternative l'una dell'altra.

    Ogni traccia si scompone nelle sue linee (parti), cosi' anche le copie dentro una sola traccia (parti ripetute) si
    contano una volta. La piu' lunga per intero; delle altre (dalla piu' lunga) solo i punti piu' lontani di DEDUP_NEAR_M
    da quanto e' gia' contato, campionati come nel confronto tra cammini (matching.sample_line, MATCH_SAMPLE_M). Due
    parti che si toccano (tappe consecutive, segmenti di una stessa traccia) perdono ciascuna giunzione al piu'
    DEDUP_NEAR_M metri: il difetto e' sotto l'1% sulle tappe, fino al 3% sui sostituti OSM fatti di molte parti."""
    parts = sorted((part for geom in geoms for part in shapely.get_parts(geom)), key=lambda part: part.length, reverse=True)
    if not parts:
        return 0.0
    total, counted = float(parts[0].length), [parts[0]]
    for part in parts[1:]:
        sample = matching.sample_line(part)
        near = matching.points_near(sample.points, shapely.multilinestrings(counted), config.DEDUP_NEAR_M)
        total += float(sample.weights[~near].sum())
        counted.append(part)
    return total


def tappa_length_km(rows: pd.DataFrame, tipo: str | None) -> float:
    """Lunghezza delle tracce di tappa del membro, del tipo che fa le tappe del cammino distinto (stage_tipo), come
    unione (union_length_m); per il sostituto OSM la sua tratta unica."""
    stages = rows[rows["fonte"].eq(config.OSM_FONTE) | rows.index.isin(catalogue_rows(rows, tipo).index)]
    return union_length_m(list(stages.geometry)) / 1000


def best_member(ratios: Mapping[str, float]) -> str:
    """Il membro con la quota di copertura piu' alta (al massimo 1: un tracciato piu' lungo della scheda e' un disaccordo
    tra le fonti, visibile in dichiarata contro tracciata, non una copertura oltre il 100%); a pari quota quello la cui
    lunghezza e' piu' vicina alla propria dichiarata."""
    return max(ratios, key=lambda member: (min(ratios[member], 1.0), -abs(ratios[member] - 1.0)))


def length_columns(members: Sequence[str], catalogue: pd.DataFrame, rows: TrackRows) -> dict[str, float]:
    """Lunghezza dichiarata, tracciata e copertura, tutte e tre dello stesso membro (il migliore, best_member), cosi'
    le colonne visibili concordano. Senza un membro con tracce e lunghezza dichiarata: la dichiarata massima dei membri
    (se i membri non hanno tracce) e la tracciata massima, copertura vuota."""
    declared = catalogue.loc[list(members), "dichiarata_km"].to_dict()
    tipo = stage_tipo(members, rows)
    traced = {member: tappa_length_km(rows[member], tipo) for member in members if member in rows}
    ratios = {member: km / declared[member] for member, km in traced.items() if declared[member] > 0}
    if not ratios:
        known = [km for km in declared.values() if km > 0]
        return {"dichiarata": max(known, default=np.nan), "tracciata": max(traced.values(), default=np.nan), "copertura": np.nan}
    best = best_member(ratios)
    return {"dichiarata": declared[best], "tracciata": traced[best], "copertura": min(ratios[best], 1.0)}


def geometry_source(members: Sequence[str], rows: TrackRows) -> str:
    """catalogo, osm (solo il sostituto di un membro senza tracce), catalogo e osm (tracce di catalogo di un membro e
    sostituto OSM di un altro), nessuna."""
    fonti = {fonte for member in members if member in rows for fonte in rows[member]["fonte"]}
    own, standin = bool(fonti - {config.OSM_FONTE}), config.OSM_FONTE in fonti
    return {
        (True, False): config.FONTE_GEOMETRIA_CATALOGO, (False, True): config.FONTE_GEOMETRIA_OSM,
        (True, True): config.FONTE_GEOMETRIA_CATALOGO_OSM, (False, False): config.FONTE_GEOMETRIA_NESSUNA,
    }[(own, standin)]


def geometry_columns(members: Sequence[str], catalogue: pd.DataFrame, rows: TrackRows) -> dict[str, object]:
    """Lunghezze, copertura e provenienza della geometria di un cammino distinto."""
    lengths = length_columns(members, catalogue, rows)
    return {
        "lunghezza_dichiarata_km": lengths["dichiarata"],
        "lunghezza_tracciata_km": round(lengths["tracciata"], config.PAIRS_LUNGHEZZA_DECIMALS),
        "copertura": round(lengths["copertura"], config.PAIRS_COPERTURA_DECIMALS),
        "fonte_geometria": geometry_source(members, rows),
    }


def _limbo_pair(oggetto: str, unico_of: pd.Series) -> tuple[str, str]:
    """I due cammini distinti di una riga di limbo ('cammino_a|cammino_b'); errore chiaro se la riga e' malformata o
    cita un cammino che dedup/membri.csv non conosce."""
    ids = oggetto.split(config.DEDUP_PAIR_SEPARATOR)
    if len(ids) != 2:
        raise PairsError(f"limbo_dedup.csv: la riga {oggetto!r} non e' una coppia 'cammino_a{config.DEDUP_PAIR_SEPARATOR}cammino_b'")
    unknown = [cammino for cammino in ids if cammino not in unico_of.index]
    if unknown:
        raise PairsError(f"limbo_dedup.csv: il cammino {unknown[0]} non e' in membri.csv: rilanciare dedup")
    return unico_of[ids[0]], unico_of[ids[1]]


def limbo_notes(inputs: Inputs) -> dict[str, list[str]]:
    """Cammino distinto -> note per ogni coppia di limbo di dedup in cui compare (ciascuno cita l'altro)."""
    unico_of = inputs.membri.set_index("cammino_id")[UNICO]
    name_of = inputs.unici.set_index(UNICO)["nome"]
    notes: dict[str, list[str]] = defaultdict(list)
    for row in inputs.limbo.itertuples():
        first, second = _limbo_pair(row.oggetto, unico_of)
        if first == second:
            continue
        template = (
            config.PAIRS_NOTA_LIMBO_GEOMETRIA if row.motivo == config.MOTIVO_NOME_SI_GEOMETRIA_ASSENTE
            else config.PAIRS_NOTA_LIMBO_ALTRO
        )
        for this, other in ((first, second), (second, first)):
            notes[this].append(template.format(nome=name_of[other], unico=other, motivo=row.motivo))
    return notes


def nota(limbo: Sequence[str], source: object) -> str:
    notes = [*limbo, *([config.PAIRS_NOTA_NESSUNA_GEOMETRIA] if source == config.FONTE_GEOMETRIA_NESSUNA else [])]
    return config.PAIRS_NOTA_SEPARATOR.join(dict.fromkeys(notes))


def build_cammini(inputs: Inputs, n_comuni: Mapping[str, int], names: Mapping[str, str]) -> pd.DataFrame:
    """Una riga per ogni cammino distinto di cammini_unici.csv, anche con zero comuni."""
    rows, counted, limbo = inputs.rows, osm_relations_counted(inputs.abbinamenti), limbo_notes(inputs)
    records = []
    for unico in inputs.unici.to_dict("records"):
        ids = inputs.members[unico[UNICO]]
        geometry = geometry_columns(ids, inputs.catalogue, rows)
        records.append({
            **{field: unico[field] for field in (UNICO, "nome", "alias", "fonti", "membri")},
            "mezzo": cammino_mezzo(ids, rows, inputs.labels),
            **label_columns(ids, inputs.labels, counted),
            "regioni": canonical_regions(inputs.catalogue.loc[ids, "regioni"], names),
            **geometry,
            "nota": nota(limbo.get(unico[UNICO], []), geometry["fonte_geometria"]),
            "n_comuni": n_comuni.get(unico[UNICO], 0),
        })
    return pd.DataFrame(records, columns=list(config.PAIRS_CAMMINI_FIELDS))


def build_coppie(pairs: pd.DataFrame, cammini: pd.DataFrame, comune_table: pd.DataFrame) -> pd.DataFrame:
    """Coppie con nomi dei comuni e le etichette del cammino (religioso, fonte della geometria), ordinate per percorso."""
    per_cammino = cammini[[UNICO, "nome", "religioso", "fonte_geometria"]]
    coppie = pairs.merge(comune_table, on=COMUNE, how="left", validate="many_to_one")
    coppie = coppie.merge(per_cammino, on=UNICO, how="left", validate="many_to_one")
    return coppie.sort_values([UNICO, "ordine"], ignore_index=True)[list(config.PAIRS_COPPIE_FIELDS)]


# --- Percorso completo ---

def summary(coppie: pd.DataFrame, cammini: pd.DataFrame, without_spine: Sequence[str]) -> str:
    counts = cammini["n_comuni"]
    lines = [
        f"coppie {len(coppie)}, comuni distinti {coppie[COMUNE].nunique()}, cammini {len(cammini)}",
        f"cammini per fonte della geometria: {cammini['fonte_geometria'].value_counts().to_dict()}",
        f"comuni per cammino: min {counts.min()}, mediana {counts.median():.0f}, massimo {counts.max()}, con zero comuni {(counts == 0).sum()}",
        f"cammini con comuni ma senza dorsale (ordine per codice): {len(without_spine)} {list(without_spine)}",
    ]
    return "\n".join(lines)


def run() -> None:
    inputs = load_inputs()
    comuni = crossing.load_comuni()
    comune_table = load_comune_table()
    pairs = aggregate_pairs(cross_tracks(inputs.tracks, comuni), inputs.tracks, inputs.membri)
    spines = build_spines(pairs[UNICO].unique(), inputs)
    polygons = dict(zip(comuni[COMUNE], comuni.geometry))
    comuni_of = pairs.groupby(UNICO)[COMUNE].agg(set).to_dict()
    accepted, rejected = split_starts(inputs.membri, member_starts(comuni), comuni_of)
    anchors = chain_anchors(inputs.membri, inputs.rows, polygons, accepted, spines)
    pairs["ordine"] = route_order(pairs, spines, polygons, anchors)
    cammini = build_cammini(inputs, pairs.groupby(UNICO).size().to_dict(), region_names(comune_table))
    coppie = build_coppie(pairs, cammini, comune_table)
    common.write_frame(coppie, config.PAIRS_DIR / config.PAIRS_FILES["coppie"])
    common.write_frame(cammini, config.PAIRS_DIR / config.PAIRS_FILES["cammini"])
    print(summary(coppie, cammini, [unico for unico, geoms in spines.items() if not geoms]))
    names = dict(zip(comuni[COMUNE], comuni[config.COMUNE_NAME_COL]))
    print(f"partenze pubblicate scartate (comune non attraversato dal cammino): {len(rejected)} "
          f"{[f'{cid} ({names[code]})' for cid, code in rejected.items()]}")
