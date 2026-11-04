"""Etichette per fonte: mezzo (piedi/bici/entrambi) e carattere religioso, piu' gli attributi extra dei cammini.

Il mezzo e' della tratta (come e' pubblicata); quello del cammino e' la sintesi delle sue tratte. Quando le fonti
si contraddicono o si aggiungono si etichetta nel modo piu' inclusivo e si conservano le etichette. Le funzioni
combine_* sono l'unica implementazione delle regole di combinazione: il resto della pipeline le richiama.
"""
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from pyogrio import read_dataframe

from cammitaly import config, files
from cammitaly.geometry import common
from cammitaly.geometry import mitur as mitur_geometry
from cammitaly.sources import webmapp

WEBMAPP_BIKE = re.compile(config.TAGS_WEBMAPP_BIKE_PATTERN)
WEBMAPP_WALK = re.compile(config.TAGS_WEBMAPP_WALK_PATTERN)
OSM_BIKE_NAME_EXCLUDED = re.compile(config.OSM_BIKE_NAME_EXCLUDE_PATTERN)
EXTRA_FIELDS = ("tipologia", "portata", "tema", "stagione", "credenziale", "segnaletica", "data_catalogo")
TRATTE_COLUMNS = ["tratta_id", "cammino_id", "tipo", "nome", "file_origine"]

Label = tuple[str, str]  # (valore, evidenza)


class TagError(ValueError):
    """Dato di origine mancante o illeggibile per un cammino o una traccia: il messaggio ne dice l'identificativo."""


# --- combinazione (pubblica: riusata per coppia cammino-comune, gemelli fusi e corrispondenze OSM) ---

def join_values(values: Iterable[str]) -> str:
    """Unione ordinata, senza vuoti ne' doppioni, di valori gia' separati."""
    return config.LIST_SEPARATOR.join(sorted({value for value in values if value}))


def combine_mezzo(modes: Iterable[str]) -> str:
    """{piedi} -> piedi, {bici} -> bici, ogni altro insieme -> entrambi. I vuoti non contano; nessun mezzo (un cammino
    senza tracce) resta vuoto: nessun dato dice come si percorre, quindi non si assume "a piedi"."""
    found = {mode for mode in modes if mode != config.MEZZO_NESSUNO}
    if not found:
        return config.MEZZO_NESSUNO
    if found == {config.MEZZO_PIEDI}:
        return config.MEZZO_PIEDI
    return config.MEZZO_BICI if found == {config.MEZZO_BICI} else config.MEZZO_ENTRAMBI


def combine_religioso(values: Iterable[str]) -> str:
    """Basta un si."""
    return config.SI if config.SI in set(values) else config.NO


def combine_multi(values: Iterable[str]) -> str:
    """Unione dei campi multivalore (stringhe separate da LIST_SEPARATOR): la scelta piu' inclusiva."""
    return join_values(part for value in values for part in value.split(config.LIST_SEPARATOR))


# --- mezzo di una tratta ---

def mezzo_mitur(tipo: str, nome: str, original: PurePosixPath) -> Label:
    """original: percorso del file nello zip del ministero. Stesse parole e stessi ingressi della classificazione."""
    if tipo == config.TIPO_BICI:
        return config.MEZZO_BICI, config.EVIDENZA_TIPO_BICI
    folder, stem = original.parent.as_posix(), original.stem
    if mitur_geometry.mentions_bike(nome, folder, stem) and mitur_geometry.mentions_walkers(nome, stem):
        return config.MEZZO_ENTRAMBI, config.EVIDENZA_NOME_ENTRAMBI
    if mitur_geometry.mentions_shared_path(nome, folder, stem):
        return config.MEZZO_PIEDI, config.EVIDENZA_PERCORSO_CONDIVISO
    return config.MEZZO_PIEDI, config.EVIDENZA_PIEDI


def webmapp_activity_names(properties: Mapping[str, Any]) -> set[str]:
    """Attivita' dichiarate da una traccia Webmapp: valori di 'activities' e nomi della tassonomia delle attivita'."""
    raw = properties.get("activities")
    groups = json.loads(raw) if isinstance(raw, str) and raw else raw
    names = {name for group in (groups or {}).values() for name in group}
    for activity in (properties.get("taxonomy") or {}).get("activity") or []:
        names.update((activity.get("name") or {}).values())
    return {name.casefold() for name in names}


def mezzo_cdi(tipo: str, properties: Mapping[str, Any]) -> Label:
    names = webmapp_activity_names(properties)
    bike = sorted(name for name in names if WEBMAPP_BIKE.search(name))
    walk = sorted(name for name in names if WEBMAPP_WALK.search(name))
    if tipo == config.TIPO_BICI:
        if walk:
            return config.MEZZO_ENTRAMBI, config.EVIDENZA_WEBMAPP.format(join_values(walk))
        return config.MEZZO_BICI, config.EVIDENZA_TIPO_BICI
    if bike:
        return config.MEZZO_ENTRAMBI, config.EVIDENZA_WEBMAPP.format(join_values(bike))
    return config.MEZZO_PIEDI, config.EVIDENZA_PIEDI


def _route_evidence(route: str) -> str:
    if not route:
        return config.EVIDENZA_ROUTE_ASSENTE
    known = route in (*config.OSM_WALK_ROUTES, *config.OSM_BIKE_ROUTES)
    return (config.EVIDENZA_ROUTE if known else config.EVIDENZA_ROUTE_ALTRA).format(route)


def osm_bike_signals(network: str, nome: str) -> list[str]:
    """Evidenze di bici di una relazione: rete ciclabile (lcn, rcn, ncn, icn) e parole di bici nel nome."""
    cycle = [value for value in network.split(config.OSM_VALUE_SEPARATOR) if value in config.OSM_CYCLE_NETWORKS]
    signals = [config.EVIDENZA_OSM_NETWORK.format(join_values(cycle))] if cycle else []
    name = OSM_BIKE_NAME_EXCLUDED.sub(" ", common.normalize_name(nome))
    if mitur_geometry.mentions_bike(name):
        signals.append(config.EVIDENZA_NOME_BICI)
    return signals


def mezzo_osm(route: str, network: str, nome: str) -> Label:
    """Una relazione bici e' bici; una a piedi (o di altro tipo) con un segnale di bici e' per entrambi."""
    signals = osm_bike_signals(network, nome)
    evidence = config.EVIDENZA_SEPARATOR.join([_route_evidence(route), *signals])
    if route in config.OSM_BIKE_ROUTES:
        return config.MEZZO_BICI, evidence
    return (config.MEZZO_ENTRAMBI if signals else config.MEZZO_PIEDI), evidence


# --- religioso di un cammino ---

def religioso_cdi(tema: Iterable[str], layer_themes: Iterable[str]) -> Label:
    checks = [
        (config.TAGS_THEME_RELIGIOUS in tema, config.EVIDENZA_TEMA_CDI),
        (config.TAGS_THEME_RELIGIOUS in layer_themes, config.EVIDENZA_TEMA_LAYER),
    ]
    value = combine_religioso(config.SI if hit else config.NO for hit, _ in checks)
    evidence = config.EVIDENZA_SEPARATOR.join(text for hit, text in checks if hit)
    return value, evidence or config.EVIDENZA_SILENTE


def religioso_osm(pilgrimage: str) -> Label:
    if not pilgrimage:
        return config.NO, config.EVIDENZA_SILENTE
    if pilgrimage in config.OSM_PILGRIMAGE_NEGATIVE:
        return config.NO, config.EVIDENZA_PILGRIMAGE_NEGATIVO.format(pilgrimage)
    if pilgrimage == config.OSM_PILGRIMAGE_YES:
        return config.SI, config.EVIDENZA_PILGRIMAGE
    return config.SI, config.EVIDENZA_PILGRIMAGE_NOMINATO.format(pilgrimage)


# --- attributi extra ---

def _value(attribute: Any) -> str:
    return str(attribute.get("value") or "") if isinstance(attribute, dict) else ""


def _values(attribute: Any) -> list[str]:
    return [_value(item) for item in attribute or [] if _value(item)]


def layer_attributes(layer: Mapping[str, Any] | None) -> dict[str, Any]:
    attributes = (layer or {}).get("attributes")
    return attributes if isinstance(attributes, dict) else {}


def cdi_extras(cammino: Mapping[str, str], layer: Mapping[str, Any] | None) -> dict[str, str]:
    """Attributi di un cammino cdi (riga di camminiditalia_cammini.csv) e del suo strato Webmapp.

    tipologia e portata hanno un solo valore: vale cdi e Webmapp riempie solo un vuoto (la portata non si ricava
    da Webmapp). tema e stagione sono l'unione delle due fonti."""
    attributes = layer_attributes(layer)
    shape = config.WEBMAPP_SHAPE_VALUES.get(_value(attributes.get("shape")), _value(attributes.get("shape")))
    season = [config.WEBMAPP_SEASON_VALUES.get(value, value) for value in _values(attributes.get("season"))]
    return {
        "tipologia": cammino["tipologia"] or shape,
        "portata": config.TAGS_PORTATA_FIXES.get(cammino["portata"], cammino["portata"]),
        "tema": combine_multi([cammino["tema"], join_values(_values(attributes.get("themes")))]),
        "stagione": combine_multi([cammino["stagione"], join_values(season)]),
    }


def mitur_extras(card: Mapping[str, str]) -> dict[str, str]:
    services = card["servizi"].split(config.LIST_SEPARATOR)
    return {
        "credenziale": config.SI if config.MITUR_SERVICE_CREDENTIAL in services else config.NO,
        "segnaletica": config.SI if config.MITUR_SERVICE_SIGNAGE in services else config.NO,
        "data_catalogo": card["in_catalogo_dal"],
    }


# --- lista di controllo ---

def _religious_pattern(words: Iterable[str]) -> re.Pattern[str]:
    parts = [re.escape(word[:-1]) + r"\w*" if word.endswith("*") else re.escape(word) for word in words]
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(parts) + r")(?![a-z0-9])")


RELIGIOUS_WORDS = _religious_pattern(config.TAGS_RELIGIOUS_WORDS)


def religious_words(name: str) -> list[str]:
    """Parole del nome che fanno sospettare un cammino religioso (ordinate, senza doppioni)."""
    return sorted(set(RELIGIOUS_WORDS.findall(common.normalize_name(name))))


def religioso_by_name(label: Label, name: str) -> Label:
    """Cammino del catalogo (cdi, mitur): se nessuna fonte dice si, una parola religiosa nel nome vale si."""
    words = religious_words(name) if label[0] == config.NO else []
    if not words:
        return label
    template = config.EVIDENZA_NOME_PAROLE if len(words) > 1 else config.EVIDENZA_NOME_PAROLA
    return config.SI, template.format(", ".join(f"«{word}»" for word in words))


# --- lettura dei dati ---

def _tratte(fonte: str) -> list[dict[str, Any]]:
    path = Path(config.TRATTE_GPKG_TEMPLATE.format(fonte=fonte))
    # Solo gli attributi: la geometria non serve e il GeoPackage OSM e' grande
    frame = read_dataframe(path, layer=config.TRATTE_LAYER, columns=TRATTE_COLUMNS, read_geometry=False)
    return frame.to_dict("records")


def _cammini(fonte: str) -> list[dict[str, str]]:
    return common.read_csv(Path(config.CAMMINI_CSV_TEMPLATE.format(fonte=fonte)))


def _track_properties(track_id: str) -> dict[str, Any]:
    path = webmapp.track_path(track_id)
    if not path.exists():
        raise TagError(f"traccia Webmapp {track_id} (tratta {config.CDI_ID_PREFIX}{track_id}) non trovata: {path}")
    track = json.loads(path.read_text(encoding="utf-8"))
    return track.get("properties", track)


def _cdi_label(track_id: str, tipo: str) -> Label:
    properties = _track_properties(track_id)
    try:
        return mezzo_cdi(tipo, properties)
    except (ValueError, AttributeError, TypeError) as error:  # activities non e' un JSON del tipo atteso
        raise TagError(f"attivita' illeggibili nella traccia Webmapp {track_id}: {error}") from error


def _mitur_originals(records: Iterable[Mapping[str, Any]]) -> dict[str, PurePosixPath]:
    """file_origine -> percorso nello zip, dal manifesto di ogni cartella delle tracce."""
    slugs = {record["cammino_id"].removeprefix(config.MITUR_ID_PREFIX) for record in records}
    return {
        common.relative_origin(path): PurePosixPath(original)
        for slug in sorted(slugs) for path, original in mitur_geometry.card_files(slug)
    }


def _tratta_row(record: Mapping[str, Any], fonte: str, label: Label) -> dict[str, str]:
    return {
        "tratta_id": record["tratta_id"], "cammino_id": record["cammino_id"], "fonte": fonte,
        "mezzo": label[0], "evidenza_mezzo": label[1],
    }


def _cdi_tratte() -> list[dict[str, str]]:
    labels: dict[tuple[str, str], Label] = {}
    rows = []
    for record in _tratte(config.CDI_FONTE):
        key = (record["tratta_id"].removeprefix(config.CDI_ID_PREFIX), record["tipo"])
        if key not in labels:  # il mezzo e' della traccia: uguale per i cammini che la condividono
            labels[key] = _cdi_label(*key)
        rows.append(_tratta_row(record, config.CDI_FONTE, labels[key]))
    return rows


def _mitur_tratte() -> list[dict[str, str]]:
    records = _tratte(config.MITUR_FONTE)
    originals = _mitur_originals(records)
    rows = []
    for record in records:
        origin = record["file_origine"]
        original = originals.get(origin, PurePosixPath(Path(origin).name))  # senza manifesto: solo il nome del file
        rows.append(_tratta_row(record, config.MITUR_FONTE, mezzo_mitur(record["tipo"], record["nome"], original)))
    return rows


def _osm_tratte(cammini: Mapping[str, Mapping[str, str]]) -> list[dict[str, str]]:
    rows = []
    for record in _tratte(config.OSM_FONTE):
        cammino = cammini.get(record["cammino_id"])
        if cammino is None:
            raise TagError(f"tratta {record['tratta_id']}: il cammino {record['cammino_id']} non e' in cammini_osm.csv")
        # Le tratte variante ereditano il mezzo della loro relazione
        label = mezzo_osm(cammino["route"], cammino["network"], cammino["nome"])
        rows.append(_tratta_row(record, config.OSM_FONTE, label))
    return rows


def label_tratte(osm_cammini: Iterable[Mapping[str, str]]) -> list[dict[str, str]]:
    osm_by_id = {cammino["cammino_id"]: cammino for cammino in osm_cammini}
    return _cdi_tratte() + _mitur_tratte() + _osm_tratte(osm_by_id)


def _cammino_row(cammino: Mapping[str, str], mezzo: str, religioso: Label, extras: Mapping[str, str]) -> dict[str, str]:
    return {
        "cammino_id": cammino["cammino_id"], "fonte": cammino["fonte"], "nome": cammino["nome"], "mezzo": mezzo,
        "religioso": religioso[0], "evidenza_religioso": religioso[1], **dict.fromkeys(EXTRA_FIELDS, ""), **extras,
    }


def _webmapp_layers() -> dict[str, Any]:
    layers = json.loads(config.WEBMAPP_CONFIG_FILE.read_text(encoding="utf-8"))["MAP"]["layers"]
    return {str(layer["id"]): layer for layer in layers}


def _cdi_cammini(mezzi: Mapping[str, str]) -> list[dict[str, str]]:
    layers = _webmapp_layers()
    schede = {f"{config.CDI_ID_PREFIX}{row['id']}": row for row in common.read_csv(config.CDI_CAMMINI_CSV)}
    rows = []
    for cammino in _cammini(config.CDI_FONTE):
        scheda = schede.get(cammino["cammino_id"])
        if scheda is None:
            raise TagError(f"il cammino {cammino['cammino_id']} non e' in {config.CDI_CAMMINI_CSV.name}")
        layer = layers.get(scheda["webmapp_layer"])
        if scheda["webmapp_layer"] and layer is None:  # senza strato (campo vuoto) e' normale; con uno inesistente no
            raise TagError(f"cammino {cammino['cammino_id']}: lo strato Webmapp {scheda['webmapp_layer']} non esiste")
        tema = scheda["tema"].split(config.LIST_SEPARATOR)
        religioso = religioso_by_name(religioso_cdi(tema, _values(layer_attributes(layer).get("themes"))), cammino["nome"])
        rows.append(_cammino_row(cammino, mezzi[cammino["cammino_id"]], religioso, cdi_extras(scheda, layer)))
    return rows


def _mitur_cammini(mezzi: Mapping[str, str]) -> list[dict[str, str]]:
    cards = {row["slug"]: row for row in common.read_csv(config.MITUR_CAMMINI_CSV)}
    religioso = (config.SI, config.EVIDENZA_CATALOGO_RELIGIOSO)  # il catalogo e' dei cammini religiosi: il nome non conta
    rows = []
    for cammino in _cammini(config.MITUR_FONTE):
        card = cards.get(cammino["cammino_id"].removeprefix(config.MITUR_ID_PREFIX))
        if card is None:
            raise TagError(f"il cammino {cammino['cammino_id']} non ha la scheda in {config.MITUR_CAMMINI_CSV.name}")
        rows.append(_cammino_row(cammino, mezzi[cammino["cammino_id"]], religioso, mitur_extras(card)))
    return rows


def _osm_cammini(mezzi: Mapping[str, str], cammini: list[dict[str, str]]) -> list[dict[str, str]]:
    return [_cammino_row(c, mezzi[c["cammino_id"]], religioso_osm(c["pilgrimage"]), {}) for c in cammini]


def label_cammini(tratte: list[dict[str, str]], osm: list[dict[str, str]]) -> list[dict[str, str]]:
    modes: dict[str, set[str]] = {}
    for row in tratte:
        modes.setdefault(row["cammino_id"], set()).add(row["mezzo"])
    # Un cammino senza tratte (nel limbo) prende il mezzo di un insieme vuoto, cioe' nessun mezzo
    mezzi: defaultdict[str, str] = defaultdict(lambda: combine_mezzo(()))
    mezzi.update({cid: combine_mezzo(found) for cid, found in modes.items()})
    return _cdi_cammini(mezzi) + _mitur_cammini(mezzi) + _osm_cammini(mezzi, osm)


def _summary(label: str, rows: Iterable[Mapping[str, str]], key: str) -> None:
    by_source: dict[str, Counter[str]] = {}
    for row in rows:
        by_source.setdefault(row["fonte"], Counter())[row[key]] += 1
    counts = "; ".join(f"{source} {dict(sorted(count.items()))}" for source, count in by_source.items())
    print(f"tag: {label}: {counts}")


def run() -> None:
    osm = _cammini(config.OSM_FONTE)
    tratte = label_tratte(osm)
    cammini = label_cammini(tratte, osm)
    n_tratte = files.write_csv(config.TAGS_TRATTE_CSV, config.TAGS_TRATTE_FIELDS, tratte)
    projected = ({key: row[key] for key in config.TAGS_CAMMINI_FIELDS} for row in cammini)
    n_cammini = files.write_csv(config.TAGS_CAMMINI_CSV, config.TAGS_CAMMINI_FIELDS, projected)
    _summary("mezzo delle tratte", tratte, "mezzo")
    _summary("mezzo dei cammini", cammini, "mezzo")
    _summary("religioso dei cammini", cammini, "religioso")
    print(f"tag: {n_tratte} tratte, {n_cammini} cammini in {config.INTERIM_DIR}")
