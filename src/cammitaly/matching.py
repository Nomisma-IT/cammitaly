"""Primitive di confronto tra cammini: somiglianza dei nomi e quota di una linea vicina a un'altra.

Condivise da calibration.py, dedup.py e dal confronto con OSM. Tutte le distanze sono in metri nel CRS di lavoro.
"""
from collections.abc import Callable, Sequence

import numpy as np
import shapely
from rapidfuzz import fuzz, process
from shapely.geometry.base import BaseGeometry

from cammitaly import config
from cammitaly.geometry.common import normalize_name

Scorer = Callable[..., float]


# --- Nomi ---

def name_key(text: str) -> str:
    """Nome normalizzato senza le parole generiche di config.NAME_GENERIC_WORDS.

    Se non resta nessuna parola ("La Via") si tiene il nome normalizzato intero.
    """
    normalized = normalize_name(text)
    kept = [word for word in normalized.split() if word not in config.NAME_GENERIC_WORDS]
    return " ".join(kept) or normalized


def name_score(key_a: str, key_b: str, scorer: Scorer = fuzz.token_set_ratio) -> float:
    """Somiglianza 0-100 tra due chiavi. token_set_ratio dà 100 anche se una chiave è contenuta nell'altra."""
    return float(scorer(key_a, key_b))


def name_scores(keys_a: Sequence[str], keys_b: Sequence[str], scorer: Scorer = fuzz.token_set_ratio) -> np.ndarray:
    """Matrice len(keys_a) x len(keys_b) dei punteggi."""
    return process.cdist(keys_a, keys_b, scorer=scorer, dtype=np.float64)


# --- Linee ---

def sample_points(geom: BaseGeometry, step_m: float) -> tuple[np.ndarray, np.ndarray]:
    """Punti equidistanti (circa step_m) lungo ogni parte della linea, col peso in metri di ciascuno."""
    points, weights = [], []
    for part in shapely.get_parts(geom):
        count = max(2, int(np.ceil(part.length / step_m)) + 1)
        points.append(shapely.line_interpolate_point(part, np.linspace(0, part.length, count)))
        weights.append(np.full(count, part.length / count))
    return np.concatenate(points), np.concatenate(weights)


def points_near(points: np.ndarray, line: BaseGeometry, near_m: float) -> np.ndarray:
    """Per ogni punto, se dista dalla linea al massimo near_m.

    Si interroga un indice sulle parti della linea invece di `dwithin` sulla linea intera preparata: su tracce
    reali da decine di migliaia di vertici l'indice è circa venti volte più veloce (misurato).
    """
    tree = shapely.STRtree(shapely.get_parts(line))
    point_index, _ = tree.query(points, predicate="dwithin", distance=near_m)
    near = np.zeros(len(points), dtype=bool)
    near[point_index] = True
    return near


def share_near(line: BaseGeometry, other: BaseGeometry, near_m: float, step_m: float) -> float:
    """Quota (0-1, in lunghezza) di `line` che sta entro near_m da `other`; si campiona `line`, mai si bufferizza `other`."""
    points, weights = sample_points(line, step_m)
    return float(weights[points_near(points, other, near_m)].sum() / weights.sum())


def overlap_share(first: BaseGeometry, second: BaseGeometry, near_m: float, step_m: float) -> float:
    """Quota della linea più corta che sta entro near_m dall'altra.

    Si campiona la linea corta invece di bufferizzare quella lunga: l'unione dei buffer di tracce con
    decine di migliaia di vertici richiede secondi per coppia, il campionamento sbaglia di meno dell'1%.
    """
    shorter, longer = sorted((first, second), key=lambda geom: geom.length)
    return share_near(shorter, longer, near_m, step_m)
