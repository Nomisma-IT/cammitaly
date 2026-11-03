"""Tratte MiTur: legge i file GPS dei zip del ministero, classifica ogni traccia e scrive tratte, cammini, limbo."""
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np
import shapely

from cammitaly import config
from cammitaly.geometry import common
from cammitaly.geometry.common import count_by, limbo_row, normalize_name, print_spacing, read_csv, relative_origin

Coord = tuple[float, float]
Parts = list[list[Coord]]

STAGE_RULES = [(re.compile(pattern), suffix) for pattern, suffix in config.MITUR_STAGE_NUMBER_RULES]
STAGE_SUFFIX = re.compile(config.MITUR_STAGE_SUFFIX_PATTERN)
VARIANT_WORDS = re.compile(config.MITUR_VARIANT_PATTERN)
BIKE_WORDS = re.compile(config.MITUR_BIKE_PATTERN)
SHARED_PATH = re.compile(config.MITUR_SHARED_PATH_PATTERN)
WALKER_WORDS = re.compile(config.MITUR_WALKER_PATTERN)
DOCTYPE_SUBSET = re.compile(config.MITUR_XML_DOCTYPE_SUBSET_PATTERN)
COMPLETE_WORDS = re.compile(config.MITUR_COMPLETE_PATTERN)
EXTENSIONS_BLOCK = re.compile(config.MITUR_EXTENSIONS_BLOCK_PATTERN.encode(), re.DOTALL | re.IGNORECASE)
EXPORT_ID = re.compile(config.MITUR_EXPORT_ID_PATTERN)
ORDINALS = str.maketrans({char: " " for char in config.MITUR_ORDINAL_CHARS})
READERS = (config.MITUR_GPX_EXTENSION, config.MITUR_KML_EXTENSION, config.MITUR_KMZ_EXTENSION)
WALKING = (config.TIPO_TAPPA, config.TIPO_VARIANTE)


class UnreadableFile(ValueError):
    """File GPS non interpretabile: XML rotto, zip rotto, dichiarazioni vietate o file troppo grande."""


@dataclass
class Item:
    name: str | None  # nome del segnaposto KML; None per un GPX, che e' una sola traccia
    parts: Parts


@dataclass
class FileContent:
    items: list[Item] = field(default_factory=list)
    has_points: bool = False


@dataclass
class Tratta:
    record: dict[str, Any]
    extension: str
    stored_name: str


@dataclass
class CardResult:
    tratte: list[Tratta] = field(default_factory=list)
    limbo: list[dict[str, str]] = field(default_factory=list)
    n_duplicates: int = 0
    n_full_routes: int = 0
    n_too_long: int = 0


# --- lettura dei file ---

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _prolog_text(data: bytes) -> str:
    """Prologo del documento decodificato come dichiara la sua codifica (anche UTF-16), in minuscolo."""
    head = data[: config.MITUR_XML_PROLOG_BYTES]
    if head[:2] in config.MITUR_UTF16_BOMS:
        encoding = "utf-16"
    elif b"\x00" in head[:4]:
        encoding = "utf-16-le" if head[1:2] == b"\x00" else "utf-16-be"
    else:
        encoding = "utf-8"
    return head.decode(encoding, errors="ignore").lower()


def _repair_attempts(data: bytes) -> Iterator[bytes]:
    """Il documento com'e', poi senza i blocchi <extensions>, poi con i byte riletti come cp1252."""
    yield data
    stripped = EXTENSIONS_BLOCK.sub(b"", data)  # prefissi non dichiarati: la geometria non li usa
    yield stripped
    for candidate in (data, stripped):
        yield candidate.decode(config.MITUR_FALLBACK_ENCODING, errors="replace").encode("utf-8")


def _parse_xml(data: bytes) -> ET.Element:
    if len(data) > config.MITUR_MAX_XML_BYTES:
        raise UnreadableFile(f"file di {len(data)} byte oltre il tetto")
    prolog = _prolog_text(data)
    if config.MITUR_XML_FORBIDDEN_TOKEN in prolog or DOCTYPE_SUBSET.search(prolog):
        raise UnreadableFile("dichiara entita' XML")
    error: ET.ParseError | None = None
    for candidate in _repair_attempts(data):
        try:
            return ET.fromstring(candidate)
        except ET.ParseError as parse_error:
            error = parse_error
    raise UnreadableFile(str(error)[:100]) from error


def _valid_coord(lon: float, lat: float) -> Coord | None:
    max_lon, max_lat = config.LON_LAT_LIMITS
    return (lon, lat) if abs(lon) <= max_lon and abs(lat) <= max_lat else None


def _attribute_point(element: ET.Element) -> Coord | None:
    try:
        return _valid_coord(float(element.attrib["lon"]), float(element.attrib["lat"]))
    except (KeyError, ValueError):
        return None


def _text_point(token: str, separator: str | None) -> Coord | None:
    fields = token.split(separator)
    try:
        return _valid_coord(float(fields[0]), float(fields[1]))
    except (IndexError, ValueError):
        return None


def _compact(points: Sequence[Coord | None]) -> list[Coord]:
    return [point for point in points if point is not None]


def read_gpx(root: ET.Element) -> FileContent:
    """Ogni trkseg e ogni rte e' una parte; i waypoint non sono geometria ma segnalano 'solo punti'."""
    parts: Parts = []
    for element in root.iter():
        if _local(element.tag) in config.GPX_LINE_TAGS:
            parts.append(_compact([_attribute_point(p) for p in element if _local(p.tag) in config.GPX_POINT_TAGS]))
    parts = [part for part in parts if len(part) >= 2]
    return FileContent([Item(None, parts)] if parts else [], any(_local(e.tag) == config.GPX_WAYPOINT_TAG for e in root.iter()))


def _placemark_parts(placemark: ET.Element) -> Parts:
    parts: Parts = []
    for element in placemark.iter():
        tag = _local(element.tag)
        if tag == config.KML_LINE_TAG:
            for child in element:
                if _local(child.tag) == config.KML_COORDINATES_TAG:
                    parts.append(_compact([_text_point(t, ",") for t in (child.text or "").split()]))
        elif tag == config.KML_TRACK_TAG:
            parts.append(_compact([
                _text_point(c.text or "", None) for c in element if _local(c.tag) == config.KML_TRACK_COORD_TAG
            ]))
    return [part for part in parts if len(part) >= 2]


def read_kml(root: ET.Element) -> FileContent:
    """Un elemento per ogni Placemark con linee (LineString o gx:Track); i Point segnalano 'solo punti'."""
    items = []
    for placemark in root.iter():
        if _local(placemark.tag) != config.KML_PLACEMARK_TAG:
            continue
        parts = _placemark_parts(placemark)
        name = next((c.text for c in placemark if _local(c.tag) == config.KML_NAME_TAG), None)
        if parts:
            items.append(Item((name or "").strip() or None, parts))
    return FileContent(items, any(_local(e.tag) == config.KML_POINT_TAG for e in root.iter()))


def read_kmz(path: Path) -> FileContent:
    """Legge i soli membri .kml dall'archivio in memoria: nulla viene estratto su disco."""
    merged = FileContent()
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if not info.filename.lower().endswith(config.MITUR_KML_EXTENSION):
                continue
            if info.file_size > config.MITUR_MAX_XML_BYTES:
                raise UnreadableFile(f"membro {info.filename} oltre il tetto")
            content = read_kml(_parse_xml(archive.read(info)))
            merged.items.extend(content.items)
            merged.has_points = merged.has_points or content.has_points
    return merged


def read_track_file(path: Path, extension: str) -> FileContent:
    try:
        if extension == config.MITUR_KMZ_EXTENSION:
            return read_kmz(path)
        root = _parse_xml(path.read_bytes())
        return read_gpx(root) if extension == config.MITUR_GPX_EXTENSION else read_kml(root)
    except (zipfile.BadZipFile, OSError) as error:
        raise UnreadableFile(str(error)[:100]) from error


# --- classificazione ---

def clean_text(text: str) -> str:
    return EXPORT_ID.sub("", normalize_name(text.translate(ORDINALS)))


def parse_stage(name: str) -> tuple[int | None, bool]:
    """Numero di tappa del nome e se e' seguito da un suffisso di variante (05a, 10bis); (None, False) senza numero."""
    text = clean_text(name)
    for rule, allows_suffix in STAGE_RULES:
        match = rule.search(text)
        if match:
            return int(match.group(1)), allows_suffix and STAGE_SUFFIX.match(text, match.end(1)) is not None
    return None, False


def mentions_bike(name: str, folder: str = "", stem: str = "") -> bool:
    """Parole di bici nel nome, nel file o nella cartella; un percorso ciclopedonale non conta."""
    return bool(BIKE_WORDS.search(SHARED_PATH.sub(" ", clean_text(f"{folder} {stem} {name}"))))


def mentions_walkers(name: str, stem: str = "") -> bool:
    """Il nome o il file dice che si cammina ('per camminanti e mtb'); la cartella non conta."""
    return bool(WALKER_WORDS.search(clean_text(f"{stem} {name}")))


def mentions_shared_path(name: str, folder: str = "", stem: str = "") -> bool:
    """Percorso ciclopedonale o 'strada pedonale ciclabile': si cammina anche a piedi."""
    return bool(SHARED_PATH.search(clean_text(f"{folder} {stem} {name}")))


def classify(name: str, folder: str, stem: str = "") -> tuple[str, int | None]:
    """(tipo, ordine). Il numero si cerca nel nome e, se manca, nel nome del file (KMZ con piu' segnaposto);
    le parole chiave anche nelle cartelle dello zip."""
    number, suffix = parse_stage(name)
    if number is None and stem:
        number, suffix = parse_stage(stem)
    context = clean_text(f"{folder} {stem} {name}")
    if mentions_bike(name, folder, stem):
        # "per camminanti e mtb": si cammina, quindi non e' una traccia solo per la bici
        if mentions_walkers(name, stem):
            return config.TIPO_ALTRO, None
        return config.TIPO_BICI, number
    if COMPLETE_WORDS.search(context):
        return config.TIPO_ALTRO, None
    if suffix or VARIANT_WORDS.search(context):
        return config.TIPO_VARIANTE, number
    return (config.TIPO_ALTRO, None) if number is None else (config.TIPO_TAPPA, number)


# --- tratte di un file ---

def outside_italy(parts: Parts) -> bool:
    points = np.asarray([point for part in parts for point in part])
    lon_min, lat_min, lon_max, lat_max = config.MITUR_ITALY_BBOX
    return bool(
        points[:, 0].max() < lon_min or points[:, 0].min() > lon_max
        or points[:, 1].max() < lat_min or points[:, 1].min() > lat_max
    )


def sniff_extension(path: Path) -> str | None:
    """Formato dai primi byte, per i file senza estensione o con un'estensione che non e' di una traccia."""
    try:
        with path.open("rb") as handle:
            head = handle.read(config.MITUR_SNIFF_BYTES)
    except OSError:
        return None
    if head.startswith(config.MITUR_ZIP_MAGIC):
        return config.MITUR_KMZ_EXTENSION
    lowered = head.lower()
    return next((extension for marker, extension in config.MITUR_SNIFF_MARKERS if marker in lowered), None)


def _unique_id(base: str, used: set[str]) -> str:
    candidate, n = base, 1
    while candidate in used:
        n += 1
        candidate = f"{base}:{n}"
    used.add(candidate)
    return candidate


def _item_record(slug: str, tratta_id: str, name: str, original: PurePosixPath, geom: Any, origin: str) -> dict[str, Any]:
    tipo, ordine = classify(name, original.parent.as_posix(), original.stem)
    return {
        "tratta_id": tratta_id, "cammino_id": f"{config.MITUR_ID_PREFIX}{slug}", "fonte": config.MITUR_FONTE,
        "nome": name, "ordine": ordine, "tipo": tipo, **common.line_stats(geom), "file_origine": origin, "geometry": geom,
    }


def _empty_reason(content: FileContent) -> tuple[str, str]:
    if content.has_points:
        return config.MOTIVO_SOLO_PUNTI, "waypoint o punti senza linee"
    return config.MOTIVO_TRACCIA_SENZA_GEOMETRIA, "nessuna traccia o rotta nel file"


def file_tratte(
    slug: str, path: Path, original: str, used_ids: set[str]
) -> tuple[list[Tratta], list[dict[str, str]]]:
    """Tratte e righe di limbo di un file; il rumore (pdf, .DS_Store...) non produce nulla."""
    pure = PurePosixPath(original)
    base = f"{config.MITUR_ID_PREFIX}{slug}:{pure.stem}"
    extension = Path(path.name).suffix.lower()
    if extension in config.MITUR_NOISE_EXTENSIONS:
        return [], []
    if extension not in READERS:
        sniffed = sniff_extension(path)
        if sniffed is None:
            return [], [limbo_row(base, config.MOTIVO_FORMATO_NON_LETTO, f"estensione {extension or 'assente'}")]
        extension = sniffed
    try:
        content = read_track_file(path, extension)
    except UnreadableFile as error:
        return [], [limbo_row(base, config.MOTIVO_FILE_ILLEGGIBILE, str(error))]
    if not content.items:
        return [], [limbo_row(base, *_empty_reason(content))]
    tratte, limbo = [], []
    many = len(content.items) > 1
    for index, item in enumerate(content.items, start=1):
        tratta_id = _unique_id(f"{base}:{index}" if many else base, used_ids)
        geom = common.build_geometry(item.parts)
        if geom is None:
            limbo.append(limbo_row(tratta_id, config.MOTIVO_TRACCIA_SENZA_GEOMETRIA, "meno di 2 vertici dopo la pulizia"))
        elif outside_italy(item.parts):
            limbo.append(limbo_row(tratta_id, config.MOTIVO_FUORI_ITALIA, "riquadro interamente fuori dall'Italia"))
        else:
            name = (item.name if many and item.name else pure.stem)
            record = _item_record(slug, tratta_id, name, pure, geom, relative_origin(path))
            tratte.append(Tratta(record, extension, path.name))
    return tratte, limbo


# --- duplicati e percorsi interi dentro un cammino ---

def _format_position(extension: str) -> int:
    preference = config.MITUR_FORMAT_PREFERENCE
    return preference.index(extension) if extension in preference else len(preference)


def _rank(tratta: Tratta) -> tuple[bool, int, str]:
    return tratta.record["tipo"] == config.TIPO_BICI, _format_position(tratta.extension), tratta.stored_name


def hausdorff_if_duplicate(a: dict[str, Any], b: dict[str, Any]) -> float | None:
    """Distanza di Hausdorff se le lunghezze sono entro la tolleranza e la distanza sotto la soglia, altrimenti None."""
    la, lb = float(a["lunghezza_m"]), float(b["lunghezza_m"])
    if abs(la - lb) > config.MITUR_DUPLICATE_LENGTH_TOLERANCE * max(la, lb):
        return None
    distance = float(shapely.hausdorff_distance(a["geometry"], b["geometry"]))
    return distance if distance < config.MITUR_DUPLICATE_HAUSDORFF_M else None


def drop_duplicates(tratte: list[Tratta]) -> tuple[list[Tratta], list[dict[str, str]]]:
    """Tiene la prima copia per preferenza di formato (GPX prima); le altre vanno in limbo."""
    kept: list[Tratta] = []
    limbo: list[dict[str, str]] = []
    for tratta in sorted(tratte, key=_rank):
        for other in kept:
            distance = hausdorff_if_duplicate(other.record, tratta.record)
            if distance is not None:
                detail = (
                    f"copia di {other.record['tratta_id']} (Hausdorff {distance:.1f} m, "
                    f"{tratta.record['lunghezza_m'] / 1000:.2f} e {other.record['lunghezza_m'] / 1000:.2f} km)"
                )
                limbo.append(limbo_row(str(tratta.record["tratta_id"]), config.MOTIVO_DUPLICATO_INTERNO, detail))
                break
        else:
            kept.append(tratta)
    return kept, limbo


def covers(big: Any, small: Any) -> bool:
    """True se i vertici campionati di small stanno tutti entro la distanza soglia da big."""
    margin = config.MITUR_FULL_ROUTE_MAX_DISTANCE_M
    sx0, sy0, sx1, sy1 = small.bounds
    bx0, by0, bx1, by1 = big.bounds
    if sx0 < bx0 - margin or sy0 < by0 - margin or sx1 > bx1 + margin or sy1 > by1 + margin:
        return False
    coords = shapely.get_coordinates(small)
    sample = np.linspace(0, len(coords) - 1, min(len(coords), config.MITUR_COVERAGE_SAMPLE_POINTS)).astype(int)
    return bool(shapely.distance(shapely.points(coords[sample]), big).max() <= margin)


def _is_union_of_stages(big: Tratta, stages: list[Tratta]) -> bool:
    big_length = float(big.record["lunghezza_m"])
    parts = [
        float(s.record["lunghezza_m"]) for s in stages
        if s is not big and float(s.record["lunghezza_m"]) <= config.MITUR_FULL_ROUTE_PART_RATIO * big_length
        and covers(big.record["geometry"], s.record["geometry"])
    ]
    return len(parts) >= config.MITUR_FULL_ROUTE_MIN_COVERED and sum(parts) >= config.MITUR_FULL_ROUTE_SUM_RATIO * big_length


def full_route_ids(tratte: list[Tratta]) -> set[str]:
    """Tratte a piedi che sono l'unione di almeno MITUR_FULL_ROUTE_MIN_COVERED tappe dello stesso cammino."""
    walk = [t for t in tratte if t.record["tipo"] in WALKING]
    stages = [t for t in walk if t.record["tipo"] == config.TIPO_TAPPA]
    if len(stages) < config.MITUR_FULL_ROUTE_MIN_COVERED:
        return set()
    threshold = config.MITUR_FULL_ROUTE_LENGTH_FACTOR * float(np.median([t.record["lunghezza_m"] for t in walk]))
    return {
        str(t.record["tratta_id"]) for t in walk
        if float(t.record["lunghezza_m"]) >= threshold and _is_union_of_stages(t, stages)
    }


# --- cammini ---

def _read_manifest(folder: Path) -> dict[str, str]:
    path = folder / config.ZIP_MANIFEST_FILE
    if not path.exists():
        return {}
    return {row["stored_name"]: row["original_member_path"] for row in read_csv(path)}


def card_files(slug: str) -> list[tuple[Path, str]]:
    """(file su disco, percorso originale nello zip) in ordine di preferenza di formato e poi di nome."""
    folder = config.MITUR_TRACKS_DIR / slug
    if not folder.exists():
        return []
    manifest = _read_manifest(folder)
    found = [p for p in folder.iterdir() if p.is_file() and p.name != config.ZIP_MANIFEST_FILE]
    pairs = [(p, manifest.get(p.name) or p.name.split("_", 1)[-1]) for p in found]
    return sorted(pairs, key=lambda pair: (_format_position(Path(pair[0].name).suffix.lower()), pair[0].name))


def _demote(tratte: list[Tratta], tipi: tuple[str, ...], condition: Callable[[Tratta], bool]) -> int:
    """Le tratte dei tipi indicati che soddisfano la condizione diventano altro, senza ordine; ritorna quante."""
    hit = [t for t in tratte if t.record["tipo"] in tipi and condition(t)]
    for tratta in hit:
        tratta.record.update(tipo=config.TIPO_ALTRO, ordine=None)
    return len(hit)


def build_card(slug: str) -> CardResult:
    result = CardResult()
    used_ids: set[str] = set()
    for path, original in card_files(slug):
        tratte, limbo = file_tratte(slug, path, original, used_ids)
        result.tratte.extend(tratte)
        result.limbo.extend(limbo)
    result.tratte, duplicates = drop_duplicates(result.tratte)
    result.limbo.extend(duplicates)
    result.n_duplicates = len(duplicates)
    result.n_too_long = _demote(result.tratte, (config.TIPO_TAPPA,), lambda t: t.record["lunghezza_m"] > config.MITUR_MAX_STAGE_LENGTH_M)
    full = full_route_ids(result.tratte)
    result.n_full_routes = _demote(result.tratte, WALKING, lambda t: t.record["tratta_id"] in full)
    return result


def _card_notes(card: dict[str, str], result: CardResult, distinct: int, n_rows: int) -> list[str]:
    notes = [card.get("nota", "")] if card.get("nota") else []
    if n_rows and not distinct:
        notes.append("nessuna tratta di tipo tappa (solo altro, variante o bici)")
    if result.n_duplicates:
        notes.append(f"{result.n_duplicates} duplicati interni scartati")
    if result.n_too_long:
        limit_km = config.MITUR_MAX_STAGE_LENGTH_M / 1000
        notes.append(f"{result.n_too_long} tratte di tappa oltre {limit_km:.0f} km riclassificate altro")
    if result.n_full_routes:
        notes.append(f"{result.n_full_routes} percorsi interi riconosciuti dalla copertura delle tappe")
    return notes


def cammino_row(card: dict[str, str], frame: Any, result: CardResult) -> dict[str, object]:
    cid = f"{config.MITUR_ID_PREFIX}{card['slug']}"
    own = frame[frame["cammino_id"] == cid]
    distinct = int(own[own["tipo"] == config.TIPO_TAPPA]["ordine"].nunique())
    walk = own[own["tipo"].isin(WALKING)]
    return {
        "cammino_id": cid, "fonte": config.MITUR_FONTE, "nome": card["nome"], "regioni": card["regioni"],
        "link": card["sito_web"], "lunghezza_dichiarata_km": "", "n_tappe_dichiarate": "",
        "n_tratte": len(own), "lunghezza_m": round(float(walk["lunghezza_m"].sum()), config.METERS_DECIMALS),
        "stato_geometria": common.stato_geometria(distinct, None, len(own)),
        "note": "; ".join(_card_notes(card, result, distinct, len(own))),
    }


def _missing_limbo(card: dict[str, str], row: dict[str, object]) -> dict[str, str] | None:
    cid = str(row["cammino_id"])
    if not card["url_percorso"]:
        return limbo_row(cid, config.MOTIVO_SENZA_PERCORSO, card.get("nota") or "scheda senza pulsante di download")
    if row["stato_geometria"] != config.STATO_ASSENTE:
        return None
    folder = config.MITUR_TRACKS_DIR / card["slug"]
    reason = "cartella dei file non presente" if not folder.exists() else "nessuna traccia utilizzabile nei file dello zip"
    return limbo_row(cid, config.MOTIVO_CAMMINO_SENZA_GEOMETRIA, reason)


def build_all(cards: list[dict[str, str]]) -> tuple[Any, list[dict[str, object]], list[dict[str, str]]]:
    """(GeoDataFrame tratte, righe cammini, righe limbo) per tutte le schede."""
    results = {card["slug"]: build_card(card["slug"]) for card in cards}
    frame = common.tratte_frame([t.record for r in results.values() for t in r.tratte])
    rows, limbo = [], [row for r in results.values() for row in r.limbo]
    for card in cards:
        row = cammino_row(card, frame, results[card["slug"]])
        rows.append(row)
        missing = _missing_limbo(card, row)
        if missing:
            limbo.append(missing)
    return frame, rows, limbo


# --- esecuzione e riepilogo ---

def run() -> None:
    cards = read_csv(config.MITUR_CAMMINI_CSV)
    frame, rows, limbo = build_all(cards)
    common.write_tratte(frame, config.MITUR_FONTE)
    common.write_cammini(rows, config.MITUR_FONTE)
    common.write_limbo(limbo, config.MITUR_FONTE)
    _print_summary(cards, frame, rows, limbo)


def _print_summary(cards: list[dict[str, str]], frame: Any, rows: list[dict], limbo: list[dict]) -> None:
    with_files = sum(1 for c in cards if (config.MITUR_TRACKS_DIR / c["slug"]).exists())
    print(f"mitur: {len(cards)} schede, {with_files} con cartella; {len(frame)} righe tratte")
    print("mitur: tipo", frame["tipo"].value_counts().to_dict())
    print("mitur: stato_geometria", count_by(rows, "stato_geometria"))
    print("mitur: limbo", count_by(limbo, "motivo"))
    _print_examples(frame)
    print_spacing("mitur", frame[frame["tipo"] == config.TIPO_TAPPA])


def _print_examples(frame: Any) -> None:
    for tipo in (config.TIPO_VARIANTE, config.TIPO_BICI, config.TIPO_ALTRO):
        sample = frame[frame["tipo"] == tipo].head(10)
        print(f"mitur: esempi {tipo}:", [f"{r.cammino_id[6:]}/{r.nome}" for r in sample.itertuples()])
