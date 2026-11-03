"""Incrocio tra le linee dei cammini e i confini comunali ISTAT: unica sede della logica.

Una linea "attraversa" un comune se la sua distanza dal poligono comunale e' al massimo d metri
(d = 0: la linea entra nel comune). Le distanze sono sempre in metri nel CRS di lavoro.
"""
import re
import unicodedata
from collections.abc import Iterable

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry.base import BaseGeometry

from cammitaly import config


def _polygonal_part(geom: BaseGeometry) -> BaseGeometry:
    """make_valid puo' restituire una collezione con linee o punti degeneri: del comune si tiene solo l'area."""
    polygons = [part for part in shapely.get_parts(geom) if part.geom_type in ("Polygon", "MultiPolygon")]
    return shapely.union_all(polygons) if polygons else geom


def repair_polygons(geoms: np.ndarray) -> np.ndarray:
    """Ripara i soli poligoni non validi; quelli validi restano gli stessi oggetti."""
    invalid = np.flatnonzero(~shapely.is_valid(geoms))
    repaired = geoms.copy()
    for position in invalid:
        repaired[position] = _polygonal_part(shapely.make_valid(geoms[position]))
    return repaired


def load_comuni() -> gpd.GeoDataFrame:
    """Comuni ISTAT in CRS_WORK, con le sole colonne usate e le geometrie non valide riparate."""
    comuni = gpd.read_file(config.ISTAT_COMUNI_SHP, engine="pyogrio", encoding=config.ISTAT_ENCODING)
    comuni = comuni[[config.COMUNE_ID_COL, config.COMUNE_NAME_COL, config.COMUNE_REGION_COL, "geometry"]].copy()
    if comuni.crs is None or comuni.crs != config.CRS_WORK:
        raise ValueError(f"i confini ISTAT non sono in {config.CRS_WORK}: {comuni.crs}")
    comuni["geometry"] = gpd.GeoSeries(repair_polygons(comuni.geometry.to_numpy()), index=comuni.index, crs=comuni.crs)
    return comuni.reset_index(drop=True)


def region_slug(name: str) -> str:
    """'Valle d'Aosta/Vallee d'Aoste' -> 'valle-daosta': la forma usata dagli slug di camminiditalia.org."""
    first = name.split(config.ISTAT_BILINGUAL_SEPARATOR)[0].casefold().replace("'", "")
    plain = "".join(ch for ch in unicodedata.normalize("NFKD", first) if not unicodedata.combining(ch))
    return re.sub(config.SLUG_STRIP_PATTERN, "-", plain).strip("-")


def load_region_codes() -> dict[str, int]:
    """Slug della regione -> codice ISTAT, dal file dei confini regionali."""
    regioni = gpd.read_file(
        config.ISTAT_REGIONI_SHP, engine="pyogrio", encoding=config.ISTAT_ENCODING, ignore_geometry=True
    )
    return {region_slug(row[config.REGION_NAME_COL]): int(row[config.REGION_CODE_COL]) for _, row in regioni.iterrows()}


def dissolve_cammini(tratte: gpd.GeoDataFrame, tipi: Iterable[str] = config.CROSSING_TIPI) -> gpd.GeoSeries:
    """Una geometria per cammino: unione delle tratte dei tipi indicati. Indice = cammino_id."""
    chosen = tratte[tratte["tipo"].isin(tuple(tipi))]
    return chosen[["cammino_id", "geometry"]].dissolve(by="cammino_id").geometry


def exact_distance(line: BaseGeometry, polygon: BaseGeometry) -> float:
    """Distanza minima linea-poligono. Si ritaglia la linea attorno al poligono e si allarga il riquadro finche'
    la distanza trovata non rientra nel raggio: allora il punto piu' vicino e' dentro il ritaglio e il valore e' esatto."""
    minx, miny, maxx, maxy = polygon.bounds
    line_minx, line_miny, line_maxx, line_maxy = line.bounds
    radius = config.CROSSING_CLIP_START_M
    while True:
        box = (minx - radius, miny - radius, maxx + radius, maxy + radius)
        covers_line = box[0] <= line_minx and box[1] <= line_miny and box[2] >= line_maxx and box[3] >= line_maxy
        local = line if covers_line else shapely.clip_by_rect(line, *box)
        if not local.is_empty:
            found = float(shapely.distance(local, polygon))
            if covers_line or found <= radius:
                return found
        elif covers_line:
            return float("inf")
        radius *= 2


def length_inside(line: BaseGeometry, polygon: BaseGeometry) -> float:
    """Lunghezza (m) della parte di linea dentro il poligono; il ritaglio al riquadro non cambia il risultato."""
    local = shapely.clip_by_rect(line, *polygon.bounds)
    return float(shapely.length(shapely.intersection(local, polygon)))


def comuni_entro(lines: gpd.GeoSeries, comuni: gpd.GeoDataFrame, d: float) -> pd.DataFrame:
    """Coppie (cammino_id, comune) con la linea a distanza <= d metri dal comune.

    Colonne CROSSING_FIELDS: distanza_m e' 0 se la linea entra nel comune, lunghezza_dentro_m e' 0 negli altri casi.
    I candidati vengono dall'indice spaziale (predicato dwithin); la distanza esatta si calcola solo su di essi.
    """
    line_geoms, comune_geoms = lines.to_numpy(), comuni.geometry.to_numpy()
    line_idx, comune_idx = comuni.sindex.query(line_geoms, predicate="dwithin", distance=d)
    inside = shapely.intersects(line_geoms[line_idx], comune_geoms[comune_idx])
    distance = np.zeros(len(line_idx))
    inside_length = np.zeros(len(line_idx))
    for pos in np.flatnonzero(inside):
        inside_length[pos] = length_inside(line_geoms[line_idx[pos]], comune_geoms[comune_idx[pos]])
    for pos in np.flatnonzero(~inside):
        distance[pos] = exact_distance(line_geoms[line_idx[pos]], comune_geoms[comune_idx[pos]])
    result = pd.DataFrame({
        "cammino_id": lines.index.to_numpy()[line_idx],
        config.COMUNE_ID_COL: comuni[config.COMUNE_ID_COL].to_numpy()[comune_idx],
        "distanza_m": distance.round(config.METERS_DECIMALS),
        "lunghezza_dentro_m": inside_length.round(config.METERS_DECIMALS),
    })
    return result.sort_values(["cammino_id", "distanza_m", config.COMUNE_ID_COL], ignore_index=True)[list(config.CROSSING_FIELDS)]


def within_distance(pairs: pd.DataFrame, d: float) -> pd.DataFrame:
    """Sottoinsieme delle coppie calcolate con una distanza maggiore: la distanza esatta non dipende da d."""
    return pairs[pairs["distanza_m"] <= d]
