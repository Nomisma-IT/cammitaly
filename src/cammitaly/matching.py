"""Primitive di confronto tra cammini: somiglianza dei nomi e quota di una linea vicina a un'altra.

Condivise da calibration.py, dedup.py e dal confronto con OSM. Tutte le distanze sono in metri nel CRS di lavoro.
"""
from collections import defaultdict
from collections.abc import Callable, Hashable, Iterable, Sequence
from dataclasses import dataclass

import numpy as np
import shapely
from rapidfuzz import fuzz, process
from shapely.geometry import LineString
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


@dataclass(frozen=True)
class Sample:
    """Punti equidistanti lungo una linea (geometrie Point) col peso in metri di ciascuno, da calcolare una volta."""
    points: np.ndarray
    weights: np.ndarray


def sample_line(geom: BaseGeometry, step_m: float = config.MATCH_SAMPLE_M) -> Sample:
    return Sample(*sample_points(geom, step_m))


def line_pieces(geom: BaseGeometry, max_vertices: int) -> list[LineString]:
    """La linea spezzata in pezzi consecutivi di al massimo max_vertices vertici (ogni pezzo riparte dall'ultimo del precedente)."""
    pieces = []
    for part in shapely.get_parts(geom):
        coords = shapely.get_coordinates(part)
        for start in range(0, len(coords) - 1, max_vertices - 1):
            pieces.append(LineString(coords[start:start + max_vertices]))
    return pieces


class LineIndex:
    """Indice spaziale sui pezzi di una linea: si costruisce una volta e serve a molte interrogazioni.

    Si interroga un indice invece di `dwithin` sulla linea intera preparata, e sui pezzi di al massimo
    MATCH_PIECE_VERTICES vertici invece che sulle parti: la distanza da una parte di decine di migliaia di vertici costa
    molto per ogni punto, da un pezzo corto poco. Ricostruire l'indice a ogni confronto sprecava il lavoro (l'abbinamento
    con OSM impiegava 20 minuti): chi confronta piu' volte la stessa linea tiene l'indice.
    """

    def __init__(self, line: BaseGeometry) -> None:
        self._tree = shapely.STRtree(line_pieces(line, config.MATCH_PIECE_VERTICES))
        self._bounds = line.bounds

    def near(self, points: np.ndarray, near_m: float) -> np.ndarray:
        """Per ogni punto, se dista dalla linea al massimo near_m.

        I punti fuori dal riquadro della linea allargato di near_m non possono esserle vicini: non si interrogano.
        """
        near = np.zeros(len(points), dtype=bool)
        if not len(points):
            return near
        x, y = shapely.get_coordinates(points).T
        min_x, min_y, max_x, max_y = self._bounds
        inside = np.flatnonzero((x >= min_x - near_m) & (x <= max_x + near_m) & (y >= min_y - near_m) & (y <= max_y + near_m))
        if inside.size:
            point_index, _ = self._tree.query(points[inside], predicate="dwithin", distance=near_m)
            near[inside[point_index]] = True
        return near


def points_near(points: np.ndarray, line: BaseGeometry | LineIndex, near_m: float) -> np.ndarray:
    """Per ogni punto, se dista dalla linea (o dal suo indice) al massimo near_m."""
    index = line if isinstance(line, LineIndex) else LineIndex(line)
    return index.near(points, near_m)


def shares_within(sample: Sample, target: BaseGeometry | LineIndex, nears_desc: Sequence[float]) -> list[float]:
    """Quota (in lunghezza) del campione entro ciascuna distanza da `target`.

    Le distanze vanno dalla piu' grande alla piu' piccola: i punti gia' lontani dalla distanza maggiore non si ricontrollano.
    """
    if list(nears_desc) != sorted(nears_desc, reverse=True):
        raise ValueError(f"le distanze devono essere in ordine decrescente: {list(nears_desc)}")
    index = target if isinstance(target, LineIndex) else LineIndex(target)
    alive = np.arange(len(sample.points))
    total, shares = sample.weights.sum(), []
    for near in nears_desc:
        if alive.size:
            alive = alive[index.near(sample.points[alive], near)]
        shares.append(float(sample.weights[alive].sum() / total))
    return shares


def share_within(sample: Sample, target: BaseGeometry | LineIndex, near_m: float) -> float:
    """Quota (in lunghezza) del campione entro near_m da `target`; e' l'unica misura di questo tipo, le altre la usano."""
    return shares_within(sample, target, (near_m,))[0]


def share_near(line: BaseGeometry, other: BaseGeometry, near_m: float, step_m: float) -> float:
    """Quota (0-1, in lunghezza) di `line` che sta entro near_m da `other`; si campiona `line`, mai si bufferizza `other`."""
    return share_within(sample_line(line, step_m), other, near_m)


def overlap_share(first: BaseGeometry, second: BaseGeometry, near_m: float, step_m: float) -> float:
    """Quota della linea più corta che sta entro near_m dall'altra.

    Si campiona la linea corta invece di bufferizzare quella lunga: l'unione dei buffer di tracce con
    decine di migliaia di vertici richiede secondi per coppia, il campionamento sbaglia di meno dell'1%.
    """
    shorter, longer = sorted((first, second), key=lambda geom: geom.length)
    return share_near(shorter, longer, near_m, step_m)


# --- Gruppi ---

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
