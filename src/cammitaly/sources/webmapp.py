"""Webmapp (app Cammini d'Italia): configurazione, elenco tracce e geometrie delle tappe."""
import json
from pathlib import Path
from typing import Any

from cammitaly import config, files, http

TRACKS_FIELDS = ["track_id", "layers", "ref", "from", "to", "name_it", "distance", "has_geometry"]


def _track_ids(hits: list[dict[str, Any]]) -> list[int]:
    # L'id entra in un percorso di file: deve essere un intero, mai una stringa con separatori
    ids = [hit.get("id") for hit in hits]
    bad = [i for i in ids if not isinstance(i, int) or isinstance(i, bool)]
    if bad:
        raise http.FetchError(f"elenco Webmapp con id traccia non interi: {bad[:5]}")
    return ids  # type: ignore[return-value]


def track_path(track_id: object) -> Path:
    return config.WEBMAPP_TRACKS_DIR / f"{track_id}.json"


def _download_tracks(track_ids: list[int], limit: int | None, refresh: bool) -> None:
    wanted = track_ids if limit is None else track_ids[:limit]
    for done, track_id in enumerate(wanted, start=1):
        url = config.WEBMAPP_TRACK_URL.format(track_id=track_id)
        http.fetch(url, track_path(track_id), config.HOST_S3, refresh)
        if done % 100 == 0 or done == len(wanted):
            print(f"webmapp: {done}/{len(wanted)} tracce")


def _track_row(hit: dict[str, Any]) -> dict[str, object]:
    # I campi start/end dell'elenco non sono affidabili e vengono volutamente ignorati
    return {
        "track_id": hit["id"],
        "layers": "|".join(str(layer) for layer in hit.get("layers") or []),
        "ref": hit.get("ref") or "",
        "from": hit.get("from") or "",
        "to": hit.get("to") or "",
        "name_it": (hit.get("name") or {}).get("it", ""),
        "distance": hit.get("distance") or "",
        "has_geometry": track_path(hit["id"]).exists(),
    }


def run(limit: int | None = None, refresh: bool = False) -> None:
    http.fetch(config.WEBMAPP_CONFIG_URL, config.WEBMAPP_RAW_DIR / "config.json", config.HOST_S3, refresh)
    listing_path = config.WEBMAPP_RAW_DIR / "listing.json"
    http.fetch(config.WEBMAPP_LISTING_URL, listing_path, config.HOST_MAPHUB, refresh)
    hits = json.loads(listing_path.read_text(encoding="utf-8"))["hits"]
    _download_tracks(_track_ids(hits), limit, refresh)
    count = files.write_csv(config.WEBMAPP_TRACKS_CSV, TRACKS_FIELDS, map(_track_row, hits))
    with_geometry = sum(track_path(hit["id"]).exists() for hit in hits)
    print(f"webmapp: {count} tracce in elenco, {with_geometry} con geometria scaricata")
