"""Tratte di camminiditalia: assegna ogni traccia Webmapp al suo cammino e scrive tratte, cammini, limbo."""
import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from rapidfuzz import fuzz

from cammitaly import config
from cammitaly.geometry import common
from cammitaly.geometry.common import limbo_row, normalize_name  # normalize_name e' usato anche da calibration
from cammitaly.geometry.common import read_csv as _read_csv
from cammitaly.sources.webmapp import track_path

STAGE_NUMBER = re.compile(config.CDI_STAGE_NUMBER_PATTERN)
STAGE_HEAD = re.compile(config.CDI_STAGE_HEAD_PATTERN)
STAGE_SUFFIX = re.compile(config.CDI_STAGE_SUFFIX_PATTERN)
STAGE_LABEL = re.compile(config.CDI_STAGE_LABEL_PATTERN)
STAGE_COMUNE = re.compile(config.CDI_STAGE_COMUNE_PATTERN)
AFTER_STAGE = re.compile(config.CDI_AFTER_STAGE_PATTERN)
VARIANT_WORDS = re.compile(config.CDI_VARIANT_WORDS_PATTERN)
BIKE_WORDS = re.compile(config.CDI_BIKE_PATTERN)
DECLARED_STAGES = re.compile(config.CDI_DECLARED_STAGES_PATTERN)

StageKey = tuple[int | None, str, bool, str]  # numero tappa, tipo, tappa comune, etichetta dopo il numero


@dataclass(frozen=True)
class Assignment:
    cammini: tuple[str, ...]  # id WordPress dei cammini; vuoto = non assegnata
    motivo: str | None  # motivo della riga di limbo, None se l'assegnazione e' pulita
    dettaglio: str = ""
    best: float | None = None  # punteggio del migliore, per la distribuzione riportata nelle note
    gap: float | None = None  # distanza dal primo escluso


@dataclass
class BuildResult:
    records: list[dict[str, object]] = field(default_factory=list)
    limbo: list[dict[str, str]] = field(default_factory=list)
    assignments: dict[str, Assignment] = field(default_factory=dict)  # per id traccia
    gap_split: list[str] = field(default_factory=list)  # tracce spezzate sui salti


def name_prefix(normalized: str) -> str:
    """Parte del nome prima di 'tappa'; senza quella parola, il nome intero."""
    match = re.search(r"\btappa\b", normalized)
    return normalized[: match.start()].strip() if match else normalized


def stage_number(name: str, ref: str) -> int | None:
    for text in (normalize_name(name), normalize_name(ref)):
        match = STAGE_NUMBER.search(text)
        if match:
            return int(match.group(1))
    return None


def _has_stage_suffix(name: str, ref: str) -> bool:
    # Conta solo la prima tappa citata (prima il nome, poi il ref): un ref come "Tappa 3 e Tappa 3bis"
    # descrive una tappa normale che ne cita anche una variante
    for text in (name.casefold(), ref.casefold()):
        match = STAGE_HEAD.search(text)
        if match:
            return STAGE_SUFFIX.fullmatch(re.sub(r"[^a-z0-9]+", "", match.group(1))) is not None
    return False


def _has_variant_words(name: str, ref: str) -> bool:
    # Prima di "Tappa" c'e' il nome del cammino, che puo' contenere "Variante" senza che la tappa lo sia
    for text in map(normalize_name, (name, ref)):
        match = AFTER_STAGE.search(text)
        if VARIANT_WORDS.search(match.group(0) if match else text):
            return True
    return False


def classify_tipo(name: str, ref: str) -> str:
    if _has_stage_suffix(name, ref) or _has_variant_words(name, ref):
        return config.TIPO_VARIANTE
    # Per la bici conta solo l'etichetta prima dei due punti: dopo ci sono i luoghi ("... - ciclovia a Roma")
    label = f"{normalize_name(name.split(':', 1)[0])} | {normalize_name(ref)}"
    if BIKE_WORDS.search(label):
        return config.TIPO_BICI
    if stage_number(name, ref) is None:
        return config.TIPO_ALTRO
    return config.TIPO_TAPPA


def stage_key(name: str, ref: str) -> StageKey:
    """Identifica la tappa dentro un cammino: due tracce con la stessa chiave sono la stessa tappa.

    L'etichetta dopo il numero ("(ESTIVA)", "bis", "N") distingue le varianti dello stesso numero.
    """
    head = STAGE_LABEL.search(name.casefold())
    label = normalize_name(head.group(1)) if head else ""
    comune = STAGE_COMUNE.search(normalize_name(name)) is not None
    return stage_number(name, ref), classify_tipo(name, ref), comune, label


def parse_declared_stages(raw: str) -> int | None:
    """Primo numero del testo ('3/4' -> 3); None se il campo non inizia con un numero."""
    match = DECLARED_STAGES.match(raw)
    return int(match.group(1)) if match else None


def split_layers(raw: str) -> list[str]:
    return [layer for layer in raw.split(config.LAYER_SEPARATOR) if layer]


def match_by_name(name: str, titles: dict[str, str], min_score: float, tie_margin: float) -> Assignment:
    """Confronta il prefisso del nome con i titoli candidati; i candidati vicini al migliore sono pari merito."""
    prefix = name_prefix(normalize_name(name))
    scores = {cid: fuzz.token_set_ratio(prefix, normalize_name(title)) for cid, title in titles.items()}
    best = max(scores.values())
    if best < min_score:
        return Assignment((), config.MOTIVO_NON_ASSEGNATA, f"miglior punteggio {best:.0f} sotto soglia", best)
    tied = tuple(cid for cid, score in scores.items() if best - score < tie_margin)
    others = [score for cid, score in scores.items() if cid not in tied]
    gap = best - max(others) if others else None
    motivo = config.MOTIVO_MULTIPLA if len(tied) > 1 else None
    detail = f"pari merito tra {', '.join(config.CDI_ID_PREFIX + t for t in tied)}" if motivo else ""
    return Assignment(tied, motivo, detail, best, gap)


def layer_candidates(layers: Sequence[str], by_layer: Mapping[str, Sequence[str]]) -> list[str]:
    return sorted({cid for layer in layers for cid in by_layer.get(layer, [])})


def assign_track(
    name: str, ref: str, layers: Sequence[str], by_layer: Mapping[str, Sequence[str]],
    titles: dict[str, str], taken: Mapping[tuple[str, StageKey], str] | None = None,
) -> Assignment:
    """Regole 1-3: livello con un solo cammino, livello condiviso, livello senza cammino."""
    candidates = layer_candidates(layers, by_layer)
    if len(candidates) == 1:
        return Assignment(tuple(candidates), None)
    if candidates:
        picked = {cid: titles[cid] for cid in candidates}
        return match_by_name(name, picked, config.CDI_MATCH_MIN_SCORE, config.CDI_MATCH_TIE_MARGIN)
    return _assign_without_layer(name, ref, layers, titles, taken or {})


def _prefer_exact_title(cammini: tuple[str, ...], name: str, titles: dict[str, str]) -> tuple[str, ...]:
    # Il titolo "Cammino dei Briganti" e' contenuto in "Grande Cammino dei Briganti": a parita' di punteggio
    # vince il cammino il cui titolo coincide con il prefisso del nome
    prefix = name_prefix(normalize_name(name))
    exact = tuple(cid for cid in cammini if normalize_name(titles[cid]) == prefix)
    return exact if len(cammini) > 1 and len(exact) == 1 else cammini


def _assign_without_layer(
    name: str, ref: str, layers: Sequence[str], titles: dict[str, str], taken: Mapping[tuple[str, StageKey], str]
) -> Assignment:
    # Estensione della regola 3: se il nome corrisponde (soglia severa) a uno o piu' cammini, la traccia
    # viene recuperata, ma solo se non ripete una tappa che il cammino ha gia' (duplicato di una traccia
    # con strato); la riga di limbo resta, con l'esito nel dettaglio, per poterla controllare
    where = f"layer {config.LAYER_SEPARATOR.join(layers)} senza cammino" if layers else "traccia senza layer"
    found = match_by_name(name, titles, config.CDI_FALLBACK_MIN_SCORE, config.CDI_MATCH_TIE_MARGIN)
    if not found.cammini:
        return Assignment((), config.MOTIVO_LAYER_ORFANO, f"{where}; nessun cammino con nome corrispondente")
    cammini = _prefer_exact_title(found.cammini, name, titles)
    key = stage_key(name, ref)
    twins = {cid: taken[(cid, key)] for cid in cammini if (cid, key) in taken}
    fresh = tuple(cid for cid in cammini if cid not in twins)
    if not fresh:
        pairs = "; ".join(f"cammino {config.CDI_ID_PREFIX}{c}, traccia piu' vicina {config.CDI_ID_PREFIX}{t}" for c, t in twins.items())
        return Assignment((), config.MOTIVO_DUPLICATO_SENZA_LAYER, f"{where}; {pairs}", found.best, found.gap)
    ids = ", ".join(config.CDI_ID_PREFIX + cid for cid in fresh)
    detail = f"{where}; assegnata per nome a {ids} (punteggio {found.best:.0f})"
    return Assignment(fresh, config.MOTIVO_LAYER_ORFANO, detail, found.best, found.gap)


def read_track_parts(path: Path) -> list[list[list[float]]] | None:
    """Parti (liste di coordinate) della traccia GeoJSON; None se la geometria non e' una linea.

    Solleva ValueError (JSONDecodeError e UnicodeDecodeError ne derivano) se il file non e' leggibile.
    """
    geometry = json.loads(path.read_text(encoding="utf-8")).get("geometry") or {}
    if geometry.get("type") == "LineString":
        return [geometry["coordinates"]]
    if geometry.get("type") == "MultiLineString":
        return list(geometry["coordinates"])
    return None


def _track_record(track: dict[str, str], cammino_id: str, tipo: str, geom: Any, origin: str) -> dict[str, object]:
    return {
        "tratta_id": f"{config.CDI_ID_PREFIX}{track['track_id']}",
        "cammino_id": f"{config.CDI_ID_PREFIX}{cammino_id}",
        "fonte": config.CDI_FONTE,
        "nome": track["name_it"],
        "ordine": stage_number(track["name_it"], track["ref"]),
        "tipo": tipo,
        **common.line_stats(geom),
        "file_origine": origin,
        "geometry": geom,
    }


def _read_geometry(track: dict[str, str]) -> tuple[Any, int, dict[str, str] | None]:
    """Geometria pulita, numero di parti nel file e, se manca, la riga di limbo che lo spiega."""
    oggetto = f"{config.CDI_ID_PREFIX}{track['track_id']}"
    path = track_path(track["track_id"])
    if not path.exists():
        return None, 0, limbo_row(oggetto, config.MOTIVO_TRACCIA_SENZA_GEOMETRIA, "file non scaricato")
    try:
        parts = read_track_parts(path) or []
    except ValueError as error:
        return None, 0, limbo_row(oggetto, config.MOTIVO_GEOMETRIA_ILLEGGIBILE, str(error)[:100])
    geom = common.build_geometry(parts)
    if geom is None:
        return None, len(parts), limbo_row(oggetto, config.MOTIVO_TRACCIA_SENZA_GEOMETRIA, "geometria assente o non valida")
    return geom, len(parts), None


def _declared_km(track: dict[str, str], cammino_id: str, ordine: int | None, by_stage: Mapping[tuple[str, int], float]) -> float | None:
    """Distanza dichiarata: quella della traccia nell'elenco Webmapp, altrimenti la tabella delle tappe.

    La tabella delle tappe e' per numero di tappa e non coincide con la numerazione delle tracce quando una
    tappa e' divisa in parti o numerata in modo diverso: per questo e' solo il ripiego.
    """
    try:
        listed = float(track["distance"])
    except ValueError:
        listed = 0.0
    if listed:
        return listed
    return by_stage.get((cammino_id, ordine)) if ordine is not None else None


def _length_anomaly(record: dict[str, object], declared: float | None) -> dict[str, str] | None:
    if record["tipo"] != config.TIPO_TAPPA or not declared:
        return None
    ratio = float(record["lunghezza_m"]) / 1000 / declared  # type: ignore[arg-type]
    if config.CDI_LENGTH_RATIO_MIN <= ratio <= config.CDI_LENGTH_RATIO_MAX:
        return None
    detail = f"{record['cammino_id']} tappa {record['ordine']}: {float(record['lunghezza_m']) / 1000:.1f} km contro {declared:.1f} dichiarati (rapporto {ratio:.2f})"  # type: ignore[arg-type]
    return limbo_row(str(record["tratta_id"]), config.MOTIVO_LUNGHEZZA_ANOMALA, detail)


def _add_geometry(result: BuildResult, track: dict[str, str], assignment: Assignment, by_stage: Mapping[tuple[str, int], float]) -> None:
    if not assignment.cammini:
        return
    geom, n_parts, missing = _read_geometry(track)
    if missing:
        result.limbo.append(missing)
        return
    if len(getattr(geom, "geoms", [geom])) > n_parts:
        result.gap_split.append(track["track_id"])
    tipo = classify_tipo(track["name_it"], track["ref"])
    path = track_path(track["track_id"])
    origin = (path.relative_to(config.ROOT) if path.is_relative_to(config.ROOT) else path).as_posix()
    for cammino_id in assignment.cammini:
        record = _track_record(track, cammino_id, tipo, geom, origin)
        result.records.append(record)
        anomaly = _length_anomaly(record, _declared_km(track, cammino_id, record["ordine"], by_stage))  # type: ignore[arg-type]
        if anomaly:
            result.limbo.append(anomaly)


def declared_by_stage(tappe: Sequence[Mapping[str, str]]) -> dict[tuple[str, int], float]:
    """(id cammino, numero tappa) -> km dichiarati; le coppie ripetute sono ambigue e si scartano."""
    seen: dict[tuple[str, int], list[float]] = defaultdict(list)
    for row in tappe:
        try:
            seen[(row["cammino_id"], int(row["ordine"]))].append(float(row["distanza_km"]))
        except ValueError:
            continue
    return {key: values[0] for key, values in seen.items() if len(values) == 1}


def _process_track(result: BuildResult, track: dict[str, str], assignment: Assignment, by_stage: Mapping[tuple[str, int], float]) -> None:
    result.assignments[track["track_id"]] = assignment
    if assignment.motivo:
        oggetto = f"{config.CDI_ID_PREFIX}{track['track_id']}"
        result.limbo.append(limbo_row(oggetto, assignment.motivo, assignment.dettaglio))
    _add_geometry(result, track, assignment, by_stage)


def build_tratte(
    tracks: list[dict[str, str]], cammini: list[dict[str, str]], tappe: Sequence[Mapping[str, str]] = ()
) -> BuildResult:
    """Prima le tracce con strato, poi quelle senza: queste ultime sono recuperate solo se non duplicano una tappa."""
    titles = {c["id"]: c["titolo"] for c in cammini}
    by_layer: dict[str, list[str]] = defaultdict(list)
    for c in cammini:
        for layer in split_layers(c["webmapp_layer"]):
            by_layer[layer].append(c["id"])
    by_stage = declared_by_stage(tappe)
    result = BuildResult()
    taken: dict[tuple[str, StageKey], str] = {}
    ordered = sorted(tracks, key=lambda t: not layer_candidates(split_layers(t["layers"]), by_layer))
    for track in ordered:
        assignment = assign_track(track["name_it"], track["ref"], split_layers(track["layers"]), by_layer, titles, taken)
        key = stage_key(track["name_it"], track["ref"])
        for cid in assignment.cammini:
            taken.setdefault((cid, key), track["track_id"])
        _process_track(result, track, assignment, by_stage)
    return result


def cammini_rows(
    cammini: list[dict[str, str]], frame: Any, layers_with_tracks: set[str]
) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    """Righe di cammini_cdi.csv e righe di limbo per i cammini senza alcuna tratta."""
    rows, limbo = [], []
    for c in cammini:
        cid = f"{config.CDI_ID_PREFIX}{c['id']}"
        own = frame[frame["cammino_id"] == cid]
        tappa = own[own["tipo"] == config.TIPO_TAPPA]
        declared = parse_declared_stages(c["n_tappe"])
        distinct = int(tappa["ordine"].nunique())
        stato = common.stato_geometria(distinct, declared, len(own))
        walk = own[own["tipo"].isin([config.TIPO_TAPPA, config.TIPO_VARIANTE])]
        rows.append({
            "cammino_id": cid, "fonte": config.CDI_FONTE, "nome": c["titolo"], "regioni": c["regioni"], "link": c["link"],
            "lunghezza_dichiarata_km": c["lunghezza_km"], "n_tappe_dichiarate": declared if declared is not None else "",
            "n_tratte": len(own), "lunghezza_m": round(float(walk["lunghezza_m"].sum()), config.METERS_DECIMALS),
            "stato_geometria": stato, "note": "; ".join(_notes(c, declared, distinct, len(tappa), len(own))),
        })
        if stato == config.STATO_ASSENTE:
            limbo.append(limbo_row(cid, config.MOTIVO_CAMMINO_SENZA_GEOMETRIA, _missing_reason(c, layers_with_tracks)))
    return rows, limbo


def _notes(c: dict[str, str], declared: int | None, distinct: int, n_tappa_rows: int, n_rows: int) -> list[str]:
    notes = []
    if declared is None:
        notes.append("tappe dichiarate non indicate: stato non verificabile")
    elif c["n_tappe"].strip() != str(declared):
        notes.append(f"tappe dichiarate come '{c['n_tappe'].strip()}', usato {declared}")
    if declared is not None and distinct > declared:
        notes.append(f"{distinct} tappe distinte su {declared} dichiarate")
    if n_rows and not distinct:
        notes.append("nessuna tratta di tipo tappa (solo altro o variante)")
    if n_tappa_rows > distinct > 0:
        notes.append(f"{n_tappa_rows - distinct} tratte di tappa con numero ripetuto")
    return notes


def _missing_reason(c: dict[str, str], layers_with_tracks: set[str]) -> str:
    layers = split_layers(c["webmapp_layer"])
    if not layers:
        return "nessun layer Webmapp"
    if not set(layers) & layers_with_tracks:
        return f"layer {c['webmapp_layer']} senza tracce"
    return f"layer {c['webmapp_layer']}: nessuna traccia assegnata o con geometria"


def run() -> None:
    cammini = _read_csv(config.CDI_CAMMINI_CSV)
    tracks = _read_csv(config.WEBMAPP_TRACKS_CSV)
    result = build_tratte(tracks, cammini, _read_csv(config.CDI_TAPPE_CSV))
    frame = common.tratte_frame(result.records)
    layers_with_tracks = {layer for t in tracks for layer in split_layers(t["layers"])}
    rows, missing = cammini_rows(cammini, frame, layers_with_tracks)
    limbo = result.limbo + missing
    common.write_tratte(frame, config.CDI_FONTE)
    common.write_cammini(rows, config.CDI_FONTE)
    common.write_limbo(limbo, config.CDI_FONTE)
    _print_summary(tracks, result, frame, rows, limbo)


def _print_summary(
    tracks: list[dict[str, str]], result: BuildResult, frame: Any, rows: list[dict], limbo: list[dict]
) -> None:
    on_disk = sum(track_path(t["track_id"]).exists() for t in tracks)
    print(f"cdi: {len(tracks)} tracce in elenco, {on_disk} con file; {len(frame)} righe tratte")
    outcomes: dict[str, int] = defaultdict(int)
    for a in result.assignments.values():
        outcomes["non assegnata" if not a.cammini else "multipla" if len(a.cammini) > 1 else "singola"] += 1
    print("cdi: assegnazioni", dict(outcomes))
    print("cdi: tipo", frame["tipo"].value_counts().to_dict())
    print("cdi: stato_geometria", common.count_by(rows, "stato_geometria"))
    print("cdi: limbo", common.count_by(limbo, "motivo"))
    print("cdi: tracce spezzate sui salti", result.gap_split)
    _print_score_distribution(result)
    common.print_spacing("cdi", frame[frame["tipo"] == config.TIPO_TAPPA])


def _print_score_distribution(result: BuildResult) -> None:
    scored = [a for a in result.assignments.values() if a.best is not None]
    gaps = [a.gap for a in scored if a.gap is not None]
    if scored:
        q = np.percentile([a.best for a in scored], [0, 10, 50, 100])
        print("cdi: punteggio del migliore (min/p10/mediana/max)", [round(float(x), 1) for x in q], f"su {len(scored)} tracce")
    if gaps:
        q = np.percentile(gaps, [0, 10, 50, 100])
        print("cdi: distanza dal primo escluso (min/p10/mediana/max)", [round(float(x), 1) for x in q], f"su {len(gaps)} tracce")
