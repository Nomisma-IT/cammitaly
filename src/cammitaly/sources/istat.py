"""ISTAT: confini delle unita amministrative a fini statistici (file statici, non il servizio SDMX)."""
import re
from datetime import datetime, timezone
from pathlib import Path

from cammitaly import config, files, http


def _find_zip_url(index_markup: str, year: str) -> str | None:
    match = re.search(config.ISTAT_ZIP_LINK_PATTERN.format(year=year), index_markup)
    return match.group(1) if match else None


def resolve_zip_url(index_markup: str, year: str) -> str:
    """Cerca nella pagina indice il link al zip non generalizzato dell'edizione richiesta."""
    url = _find_zip_url(index_markup, year)
    if url is None:
        raise http.FetchError(
            f"nessun zip non generalizzato {year} nella pagina {config.ISTAT_INDEX_URL}; "
            f"se l'edizione non è ancora pubblicata, fissare {config.ISTAT_YEAR_ENV} all'anno precedente"
        )
    return url


def _read_index(refresh: bool) -> str:
    http.fetch(config.ISTAT_INDEX_URL, config.ISTAT_INDEX_FILE, config.HOST_ISTAT, refresh)
    return config.ISTAT_INDEX_FILE.read_text(encoding="utf-8", errors="replace")


def _write_source_note(url: str, zip_path: Path) -> None:
    # La data e' quella di scrittura del file in cache (mtime): un zip gia' presente non prende la data di oggi
    downloaded = datetime.fromtimestamp(zip_path.stat().st_mtime, timezone.utc).date()
    lines = [
        f"url: {url}",
        f"sha256: {files.file_digest(zip_path, 'sha256')}",
        f"byte: {zip_path.stat().st_size}",
        f"scaricato_il: {downloaded.isoformat()}",
        f"pagina_indice: {config.ISTAT_INDEX_URL}",
    ]
    config.ISTAT_SOURCE_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(limit: int | None = None, refresh: bool = False) -> None:
    # limit non si applica: un solo file
    index_markup = _read_index(refresh)
    if not refresh and _find_zip_url(index_markup, config.ISTAT_EDITION_YEAR) is None:
        index_markup = _read_index(True)  # l'indice in cache e' di prima della pubblicazione dell'edizione
    url = resolve_zip_url(index_markup, config.ISTAT_EDITION_YEAR)
    zip_path = http.fetch(url, config.ISTAT_RAW_DIR / url.rsplit("/", 1)[-1], config.HOST_ISTAT, refresh)
    # Si guarda lo shapefile dell'anno richiesto, non la cartella: con un anno nuovo la cartella c'e' gia'
    if refresh or not config.ISTAT_COMUNI_SHP.exists():
        files.extract_atomically(zip_path, config.ISTAT_EXTRACT_DIR)
    _write_source_note(url, zip_path)
    print(f"istat: {url} -> {zip_path} ({zip_path.stat().st_size} byte)")
