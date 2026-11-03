"""Helper geometrici condivisi dai builder delle tratte: pulizia, proiezione, statistiche, lettura e scrittura."""
import csv
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from functools import lru_cache
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString
from shapely.geometry.base import BaseGeometry

from cammitaly import config, files

Coords = Sequence[Sequence[float]]

_DASHES = re.compile(config.NAME_DASHES_PATTERN)
_APOSTROPHES = re.compile(config.NAME_APOSTROPHES_PATTERN)


@lru_cache(maxsize=1)
def _transformer() -> Transformer:
    # always_xy: l'ordine e' sempre (lon, lat) -> (est, nord), come in GeoJSON
    return Transformer.from_crs(config.CRS_SOURCE, config.CRS_WORK, always_xy=True)


@lru_cache(maxsize=None)
def normalize_name(text: str) -> str:
    """Minuscolo, trattini e apostrofi tipografici ridotti ad ASCII, senza accenti, punteggiatura a spazi."""
    text = _APOSTROPHES.sub("'", _DASHES.sub("-", text.casefold()))
    text = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text.replace("'", " "))
    return text.strip()


def limbo_row(oggetto: str, motivo: str, dettaglio: str = "") -> dict[str, str]:
    return {"oggetto": oggetto, "motivo": motivo, "dettaglio": dettaglio}


def count_by(rows: Iterable[Mapping[str, object]], key: str) -> dict[str, int]:
    return dict(Counter(str(row[key]) for row in rows))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def print_spacing(label: str, walk: gpd.GeoDataFrame) -> None:
    """Percentili della spaziatura media per tratta e dei singoli segmenti delle tratte date (di tappa)."""
    if not len(walk):
        return
    q = np.percentile(walk["spaziatura_media_m"].to_numpy(), [10, 50, 90, 100])
    print(f"{label}: spaziatura media per tratta di tappa (m) p10/mediana/p90/max", [round(float(x), 1) for x in q])
    seg = np.concatenate([segment_lengths(g) for g in walk["geometry"]])
    qs = np.percentile(seg, [10, 50, 90, 100])
    print(f"{label}: segmenti di tappa (m) p10/mediana/p90/max", [round(float(x), 1) for x in qs])


def clean_line(coords: Coords) -> list[tuple[float, float]]:
    """Toglie la quota e i vertici consecutivi identici (lasciano segmenti di lunghezza zero)."""
    cleaned: list[tuple[float, float]] = []
    for point in coords:
        xy = (float(point[0]), float(point[1]))
        if not cleaned or xy != cleaned[-1]:
            cleaned.append(xy)
    return cleaned


def split_at_gaps(points: np.ndarray, max_gap_m: float) -> list[np.ndarray]:
    """Spezza una linea proiettata dove due vertici consecutivi distano piu' di max_gap_m."""
    jumps = np.flatnonzero(np.hypot(*np.diff(points, axis=0).T) > max_gap_m) + 1
    return np.split(points, jumps)


def build_geometry(parts: Iterable[Coords]) -> LineString | MultiLineString | None:
    """Pulisce ogni parte, la proietta in CRS_WORK, la spezza sui salti oltre GAP_SPLIT_M e scarta i pezzi con meno di 2 vertici.

    Ritorna None se non resta nessuna parte; MultiLineString solo se i pezzi sono piu' di uno.
    """
    pieces: list[np.ndarray] = []
    for line in map(clean_line, parts):
        if len(line) < 2:
            continue
        xs, ys = _transformer().transform([x for x, _ in line], [y for _, y in line])
        pieces.extend(split_at_gaps(np.column_stack([xs, ys]), config.GAP_SPLIT_M))
    lines = [piece for piece in pieces if len(piece) >= 2]
    if not lines:
        return None
    if len(lines) == 1:
        return LineString(lines[0])
    return MultiLineString([piece.tolist() for piece in lines])


def segment_lengths(geom: BaseGeometry) -> np.ndarray:
    """Lunghezze (m) di tutti i segmenti tra vertici consecutivi, parti comprese."""
    parts = geom.geoms if hasattr(geom, "geoms") else [geom]
    chunks = [np.hypot(*np.diff(np.asarray(part.coords)[:, :2], axis=0).T) for part in parts]
    return np.concatenate(chunks)


def line_stats(geom: BaseGeometry) -> dict[str, float | int]:
    """n_vertici, lunghezza_m e spaziatura media (lunghezza / numero di segmenti).

    Con piu' parti i segmenti sono n_vertici meno il numero di parti: i salti tra parti non contano.
    """
    segments = segment_lengths(geom)
    length = float(segments.sum())
    return {
        "n_vertici": int(shapely.get_num_coordinates(geom)),
        "lunghezza_m": round(length, config.METERS_DECIMALS),
        "spaziatura_media_m": round(length / len(segments), config.METERS_DECIMALS),
    }


def stato_geometria(n_tappe_distinte: int, n_tappe_dichiarate: int | None, n_tratte: int) -> str:
    """assente senza alcuna tratta; ok se le tappe distinte coprono quelle dichiarate; altrimenti parziale.

    Una sola tratta di tipo diverso da tappa (altro, variante) basta a non essere assente ma non a essere ok.
    """
    if n_tratte == 0:
        return config.STATO_ASSENTE
    if n_tappe_distinte == 0:
        return config.STATO_PARZIALE
    if n_tappe_dichiarate is None or n_tappe_distinte >= n_tappe_dichiarate:
        return config.STATO_OK
    return config.STATO_PARZIALE


def tratte_frame(records: Sequence[Mapping[str, object]]) -> gpd.GeoDataFrame:
    """GeoDataFrame con lo schema del contratto; ogni record porta gli attributi piu' 'geometry'."""
    frame = pd.DataFrame(records, columns=[*config.TRATTE_ATTRIBUTES, "geometry"])
    frame["ordine"] = frame["ordine"].astype("Int64")
    return gpd.GeoDataFrame(frame, geometry="geometry", crs=config.CRS_WORK)


def write_tratte(frame: gpd.GeoDataFrame, fonte: str, append: bool = False) -> Path:
    """Scrive il GPKG della fonte; con append=True aggiunge al file esistente (scrittura a blocchi)."""
    path = Path(config.TRATTE_GPKG_TEMPLATE.format(fonte=fonte))
    path.parent.mkdir(parents=True, exist_ok=True)
    if not append:
        path.unlink(missing_ok=True)  # il GPKG si aggiunge ai layer esistenti: si riparte da zero
    frame.to_file(path, layer=config.TRATTE_LAYER, driver="GPKG", engine="pyogrio", mode="a" if append else "w")
    return path


def read_tratte(fonti: Iterable[str] | None = None) -> gpd.GeoDataFrame:
    """Concatena i tratte_<fonte>.gpkg presenti (tutte le fonti se non indicate)."""
    wanted = tuple(fonti) if fonti is not None else config.FONTI_GEOMETRIA
    paths = [Path(config.TRATTE_GPKG_TEMPLATE.format(fonte=f)) for f in wanted]
    frames = [
        gpd.read_file(p, layer=config.TRATTE_LAYER, engine="pyogrio") for p in paths if p.exists()
    ]
    if not frames:
        raise FileNotFoundError(f"nessun file tratte per le fonti: {', '.join(wanted)}")
    merged = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=config.CRS_WORK)
    merged["ordine"] = merged["ordine"].astype("Int64")  # il driver restituisce float quando ci sono nulli
    return merged


def write_cammini(rows: Iterable[Mapping[str, object]], fonte: str, fields: list[str] = config.CAMMINI_FIELDS) -> int:
    return files.write_csv(Path(config.CAMMINI_CSV_TEMPLATE.format(fonte=fonte)), fields, rows)


def write_limbo(rows: Iterable[Mapping[str, object]], fonte: str) -> int:
    return files.write_csv(Path(config.LIMBO_CSV_TEMPLATE.format(fonte=fonte)), config.LIMBO_FIELDS, rows)
