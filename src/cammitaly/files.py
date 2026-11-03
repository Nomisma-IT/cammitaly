"""Operazioni su file condivise dai fetcher: estrazione sicura, CSV, checksum."""
import csv
import hashlib
import re
import shutil
import zipfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from cammitaly import config


class UnsafeArchiveError(RuntimeError):
    """Il zip contiene un membro che uscirebbe dalla cartella di destinazione."""


def _safe_parts(member_name: str) -> tuple[str, ...]:
    # Alcuni zip creati su Windows usano il backslash come separatore
    path = PurePosixPath(member_name.replace("\\", "/"))
    parts = path.parts
    # I due punti in qualunque parte indicano unita' (C:) o flussi alternativi NTFS (file.gpx:flusso)
    if path.is_absolute() or ".." in parts or any(":" in part for part in parts):
        raise UnsafeArchiveError(f"membro non sicuro nel zip: {member_name!r}")
    return parts


def _write_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path, budget: int) -> int:
    """Scrive un membro rispettando il budget di byte; ritorna i byte scritti."""
    written = 0
    with archive.open(info) as source, target.open("wb") as out:
        while chunk := source.read(config.CHUNK_BYTES):
            written += len(chunk)
            if written > budget:  # la dimensione dichiarata nell'intestazione puo' mentire
                raise UnsafeArchiveError(f"zip oltre {config.ZIP_MAX_UNCOMPRESSED_BYTES} byte decompressi")
            out.write(chunk)
    return written


def safe_extract(zip_path: Path, dest: Path) -> list[Path]:
    """Estrae il zip in dest rifiutando percorsi assoluti, '..', ':' (zip-slip) e archivi fuori tetto."""
    dest_root = dest.resolve()
    extracted: list[Path] = []
    with zipfile.ZipFile(zip_path) as archive:
        infos = archive.infolist()
        if len(infos) > config.ZIP_MAX_MEMBERS:
            raise UnsafeArchiveError(f"{zip_path.name}: {len(infos)} membri, tetto {config.ZIP_MAX_MEMBERS}")
        # Prima si controllano tutti i membri, poi si scrive: nessuna estrazione parziale
        plan = [(info, _safe_parts(info.filename)) for info in infos]
        budget = config.ZIP_MAX_UNCOMPRESSED_BYTES
        for info, parts in plan:
            if info.is_dir() or not parts or any(p in config.ZIP_IGNORED_PARTS for p in parts):
                continue
            target = dest.joinpath(*parts)
            if not target.resolve().is_relative_to(dest_root):
                raise UnsafeArchiveError(f"membro fuori destinazione: {info.filename!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            budget -= _write_member(archive, info, target, budget)
            extracted.append(target)
    return extracted


@dataclass
class _FlatExtraction:
    """Stato di una estrazione piatta: cartella di lavoro, byte residui, indice e righe del manifesto."""

    staging: Path
    budget: int
    name_limit: int  # lunghezza massima del nome file perche' il percorso completo resti sotto il tetto
    stored: int = 0
    records: list[dict[str, object]] = field(default_factory=list)


def _stored_name(index: int, basename: str, name_limit: int) -> str:
    clean = re.sub(config.ZIP_UNSAFE_NAME_PATTERN, "_", basename)
    prefix = f"{index:04d}_"
    extension = PurePosixPath(clean).suffix[: config.ZIP_MAX_EXTENSION_CHARS]
    stem = clean[: len(clean) - len(extension)]
    room = name_limit - len(prefix) - len(extension)
    if room < 1:
        raise UnsafeArchiveError(f"percorso di destinazione troppo lungo per estrarre {basename!r}")
    return (prefix + stem[:room] + extension).rstrip(" .")  # Windows non ammette spazio o punto finale


def _store_member(
    archive: zipfile.ZipFile, info: zipfile.ZipInfo, parts: tuple[str, ...], label: str, level: int, state: _FlatExtraction
) -> None:
    state.stored += 1
    if state.stored > config.ZIP_MAX_MEMBERS:
        raise UnsafeArchiveError(f"oltre {config.ZIP_MAX_MEMBERS} file estratti da {label}")
    name = _stored_name(state.stored, parts[-1], state.name_limit)
    written = _write_member(archive, info, state.staging / name, state.budget)
    state.budget -= written
    state.records.append({
        "stored_name": name, "original_member_path": "/".join(parts), "zip_name": label,
        "nesting_level": level, "bytes": written,
    })


def _expand_nested_member(
    archive: zipfile.ZipFile, info: zipfile.ZipInfo, parts: tuple[str, ...], level: int, state: _FlatExtraction
) -> None:
    if level >= config.ZIP_MAX_NESTING:
        raise UnsafeArchiveError(f"zip annidati oltre il limite: {'/'.join(parts)!r}")
    inner = state.staging / f"annidato{level}.part"  # un nome per livello: il contenitore esterno e' ancora aperto
    try:
        state.budget -= _write_member(archive, info, inner, state.budget)
        _extract_flat(inner, "/".join(parts), level + 1, state)
    finally:
        inner.unlink(missing_ok=True)  # il contenitore interno e' ridondante: l'originale resta in raw/.../zip


def _extract_flat(source: Path, label: str, level: int, state: _FlatExtraction) -> None:
    try:
        archive_file = zipfile.ZipFile(source)
    except zipfile.BadZipFile as error:
        raise UnsafeArchiveError(f"zip non valido: {label}") from error
    with archive_file as archive:
        infos = archive.infolist()
        if len(infos) > config.ZIP_MAX_MEMBERS:
            raise UnsafeArchiveError(f"{label}: {len(infos)} membri, tetto {config.ZIP_MAX_MEMBERS}")
        # Prima si controllano tutti i membri, poi si scrive
        plan = [(info, _safe_parts(info.filename)) for info in infos]
        for info, parts in plan:
            if info.is_dir() or not parts or any(p in config.ZIP_IGNORED_PARTS for p in parts):
                continue
            if PurePosixPath(parts[-1]).suffix.lower() == config.ZIP_SUFFIX:
                _expand_nested_member(archive, info, parts, level, state)
            else:
                _store_member(archive, info, parts, label, level, state)


def _stage_and_swap(target: Path, fill: Callable[[Path], None]) -> None:
    """Riempie <target>.tmp con fill e lo rinomina in target: mai un target a meta'."""
    staging = target.with_name(target.name + ".tmp")
    if staging.exists():
        shutil.rmtree(staging)  # residuo di un'esecuzione interrotta
    staging.mkdir(parents=True)  # esiste anche se il zip non ha file
    try:
        fill(staging)
        if target.exists():
            shutil.rmtree(target)  # rifacimento (refresh o zip piu' recente)
        staging.rename(target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def extract_atomically(zip_path: Path, target: Path) -> None:
    """Estrae con la struttura originale in <target>.tmp e rinomina."""
    _stage_and_swap(target, lambda staging: safe_extract(zip_path, staging))


def extract_flat_atomically(zip_path: Path, target: Path) -> None:
    """Estrazione piatta con manifest.csv: nomi corti (limite MAX_PATH), zip annidati nella stessa cartella.

    Ogni file diventa <NNNN>_<nome>; percorso originale, zip di provenienza (anche annidato) e livello
    di annidamento restano nel manifesto, cosi' la struttura in cartelle e' un dato e non un percorso.
    """

    def fill(staging: Path) -> None:
        limit = config.ZIP_MAX_STORED_PATH_CHARS - len(str(staging.resolve())) - 1
        state = _FlatExtraction(staging, config.ZIP_MAX_UNCOMPRESSED_BYTES, limit)
        _extract_flat(zip_path, zip_path.name, 0, state)
        write_csv(staging / config.ZIP_MANIFEST_FILE, config.ZIP_MANIFEST_FIELDS, state.records)

    _stage_and_swap(target, fill)


def write_csv(path: Path, fieldnames: list[str], rows: Iterable[Mapping[str, object]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
            count += 1
    return count


def file_digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        while chunk := handle.read(config.CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()
