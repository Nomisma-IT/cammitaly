"""OpenStreetMap: estratto Italia di Geofabrik (pbf, circa 2 GB) con verifica md5."""
from pathlib import Path

from cammitaly import config, files, http


def _expected_md5() -> str:
    # Formato del file .md5: "<hash>  italy-latest.osm.pbf"
    return config.OSM_MD5_FILE.read_text(encoding="utf-8").split()[0].lower()


def verify_pbf(path: Path) -> None:
    """Solleva FetchError se l'md5 di path non e' quello atteso; usato come validate di fetch."""
    actual = files.file_digest(path, "md5")
    if actual != _expected_md5():
        raise http.FetchError(f"md5 non corrispondente per {path.name}: atteso {_expected_md5()}, trovato {actual}")


def run(limit: int | None = None, refresh: bool = False) -> None:
    """limit=0 scarica solo l'md5 e legge la dimensione del pbf con una HEAD, senza il pbf."""
    http.fetch(config.OSM_MD5_URL, config.OSM_MD5_FILE, config.HOST_GEOFABRIK, refresh)
    if limit == 0:
        size = http.head(config.OSM_PBF_URL, config.HOST_GEOFABRIK).get("content-length", "sconosciuta")
        print(f"osm: md5 atteso {_expected_md5()}, pbf non scaricato (dimensione {size} byte)")
        return
    cached = config.OSM_PBF_FILE.exists() and not refresh
    # Il controllo gira sul .part prima della promozione: un pbf corrotto non resta mai in cache
    http.fetch(config.OSM_PBF_URL, config.OSM_PBF_FILE, config.HOST_GEOFABRIK, refresh, validate=verify_pbf)
    if cached:
        verify_pbf(config.OSM_PBF_FILE)  # pbf gia' presente: nessun download lo ha validato in questa esecuzione
    print(f"osm: pbf pronto, md5 verificato {_expected_md5()}")
