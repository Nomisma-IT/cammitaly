import csv
import io
import zipfile
from pathlib import Path

import pytest

from cammitaly import config, files


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _write_zip(path: Path, members: dict[str, bytes]) -> Path:
    path.write_bytes(_zip_bytes(members))
    return path


def _nested(levels: int) -> bytes:
    # levels = 1: un zip che contiene un file; levels = 3: zip dentro zip dentro zip
    data = _zip_bytes({"traccia.gpx": b"x"})
    for n in range(levels - 1):
        data = _zip_bytes({f"interno{n}.zip": data})
    return data


@pytest.mark.parametrize("member", ["a/x.gpx:stream", "a:b/x.gpx", "x.gpx::$DATA", "C:/disco.gpx"])
def test_colon_in_any_part_is_rejected(tmp_path: Path, member: str) -> None:
    archive = _write_zip(tmp_path / "t.zip", {member: b"x"})
    with pytest.raises(files.UnsafeArchiveError):
        files.safe_extract(archive, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_member_count_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "ZIP_MAX_MEMBERS", 2)
    archive = _write_zip(tmp_path / "t.zip", {"a.gpx": b"x", "b.gpx": b"x", "c.gpx": b"x"})
    with pytest.raises(files.UnsafeArchiveError, match="membri"):
        files.safe_extract(archive, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_uncompressed_size_cap_counts_real_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "ZIP_MAX_UNCOMPRESSED_BYTES", 10)
    archive = _write_zip(tmp_path / "t.zip", {"a.gpx": b"x" * 6, "b.gpx": b"x" * 6})
    with pytest.raises(files.UnsafeArchiveError, match="decompressi"):
        files.safe_extract(archive, tmp_path / "out")


def test_extract_atomically_failure_leaves_no_target_or_staging(tmp_path: Path) -> None:
    archive = _write_zip(tmp_path / "t.zip", {"ok.gpx": b"x", "../fuori.gpx": b"x"})
    target = tmp_path / "tracce" / "cammino"
    with pytest.raises(files.UnsafeArchiveError):
        files.extract_atomically(archive, target)
    assert not target.exists()
    assert not target.with_name("cammino.tmp").exists()


def test_extract_atomically_clears_stale_staging_and_replaces_target(tmp_path: Path) -> None:
    archive = _write_zip(tmp_path / "t.zip", {"nuovo.gpx": b"x"})
    target = tmp_path / "cammino"
    target.mkdir()
    (target / "vecchio.gpx").write_bytes(b"x")
    stale = tmp_path / "cammino.tmp"
    stale.mkdir()
    (stale / "residuo.gpx").write_bytes(b"x")
    files.extract_atomically(archive, target)
    assert sorted(p.name for p in target.iterdir()) == ["nuovo.gpx"]
    assert not stale.exists()


def _manifest(folder: Path) -> list[dict[str, str]]:
    with (folder / config.ZIP_MANIFEST_FILE).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_flat_extraction_stores_numbered_names_and_manifest(tmp_path: Path) -> None:
    archive = _write_zip(tmp_path / "t.zip", {
        "Cammino/TRATTO-CALABRO/a.gpx": b"aa", "Cammino/TRATTO-CALABRO/b.kml": b"b", "__MACOSX/Cammino/._a.gpx": b"x",
        "Cammino/cartella/": b"",
    })
    target = tmp_path / "tracce" / "cammino"
    files.extract_flat_atomically(archive, target)
    assert sorted(p.name for p in target.iterdir()) == ["0001_a.gpx", "0002_b.kml", "manifest.csv"]
    assert _manifest(target) == [
        {"stored_name": "0001_a.gpx", "original_member_path": "Cammino/TRATTO-CALABRO/a.gpx", "zip_name": "t.zip", "nesting_level": "0", "bytes": "2"},
        {"stored_name": "0002_b.kml", "original_member_path": "Cammino/TRATTO-CALABRO/b.kml", "zip_name": "t.zip", "nesting_level": "0", "bytes": "1"},
    ]
    assert not target.with_name("cammino.tmp").exists()


def test_flat_extraction_keeps_long_member_paths_under_the_limit(tmp_path: Path) -> None:
    long_name = "cb-26-wild-lago-ampollino-trepido-(cotronei)-gariglione-(rifugio-grandinetti)-" + "x" * 150 + "-trc-wpt.gpx"
    member = "\\".join(["CamminoBasiliano-tracciati-GPS", "TRACCIATI-SINGOLI-TRATTO-CALABRO"] * 2 + [long_name])
    archive = _write_zip(tmp_path / "t.zip", {member: b"gpx"})
    target = tmp_path / "tracce" / "cammino-basiliano"
    files.extract_flat_atomically(archive, target)
    (record,) = _manifest(target)
    stored = target / record["stored_name"]
    assert stored.read_bytes() == b"gpx"
    assert record["stored_name"].startswith("0001_") and record["stored_name"].endswith(".gpx")
    assert len(str(stored.resolve())) <= config.ZIP_MAX_STORED_PATH_CHARS
    assert record["original_member_path"].endswith(long_name)  # il nome intero resta nel manifesto
    assert "TRATTO-CALABRO" in record["original_member_path"]


def test_flat_extraction_sanitises_names_windows_rejects(tmp_path: Path) -> None:
    archive = _write_zip(tmp_path / "t.zip", {"d/a*b?.gpx": b"x", "d/finale. ": b"y"})
    files.extract_flat_atomically(archive, tmp_path / "out")
    assert sorted(r["stored_name"] for r in _manifest(tmp_path / "out")) == ["0001_a_b_.gpx", "0002_finale"]


def test_flat_extraction_expands_nested_zips_into_the_same_folder(tmp_path: Path) -> None:
    inner = _zip_bytes({"sotto/traccia.gpx": b"t", "altro.gpx": b"o"})
    archive = _write_zip(tmp_path / "t.zip", {"primo.gpx": b"p", "esportazione/interno.zip": inner})
    target = tmp_path / "cammino"
    files.extract_flat_atomically(archive, target)
    assert sorted(p.name for p in target.iterdir()) == ["0001_primo.gpx", "0002_traccia.gpx", "0003_altro.gpx", "manifest.csv"]
    nested = _manifest(target)[1:]
    assert [(r["zip_name"], r["nesting_level"], r["original_member_path"]) for r in nested] == [
        ("esportazione/interno.zip", "1", "sotto/traccia.gpx"),
        ("esportazione/interno.zip", "1", "altro.gpx"),
    ]
    assert not list(target.glob("*.part")) and not list(target.glob("*.zip"))


def test_flat_nesting_within_limit_passes(tmp_path: Path) -> None:
    archive = _write_zip(tmp_path / "t.zip", {"e.zip": _nested(config.ZIP_MAX_NESTING)})
    files.extract_flat_atomically(archive, tmp_path / "out")
    (record,) = _manifest(tmp_path / "out")
    assert record["nesting_level"] == str(config.ZIP_MAX_NESTING)


def test_flat_nesting_beyond_limit_raises_and_leaves_nothing(tmp_path: Path) -> None:
    archive = _write_zip(tmp_path / "t.zip", {"e.zip": _nested(config.ZIP_MAX_NESTING + 2)})
    target = tmp_path / "out"
    with pytest.raises(files.UnsafeArchiveError, match="annidati"):
        files.extract_flat_atomically(archive, target)
    assert not target.exists()
    assert not target.with_name("out.tmp").exists()


@pytest.mark.parametrize("member", ["../fuori.gpx", "/assoluto.gpx", "a/x.gpx:stream", "C:/disco.gpx", "a\\..\\fuori.gpx"])
def test_flat_extraction_keeps_zip_slip_and_colon_rules(tmp_path: Path, member: str) -> None:
    archive = _write_zip(tmp_path / "t.zip", {"ok.gpx": b"x", member: b"x"})
    with pytest.raises(files.UnsafeArchiveError):
        files.extract_flat_atomically(archive, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_flat_extraction_applies_member_and_size_caps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = _write_zip(tmp_path / "t.zip", {"a.gpx": b"x" * 6, "b.gpx": b"x" * 6})
    monkeypatch.setattr(config, "ZIP_MAX_UNCOMPRESSED_BYTES", 10)
    with pytest.raises(files.UnsafeArchiveError, match="decompressi"):
        files.extract_flat_atomically(archive, tmp_path / "out")
    monkeypatch.setattr(config, "ZIP_MAX_UNCOMPRESSED_BYTES", 100)
    monkeypatch.setattr(config, "ZIP_MAX_MEMBERS", 1)
    with pytest.raises(files.UnsafeArchiveError, match="membri"):
        files.extract_flat_atomically(archive, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_flat_extraction_rejects_a_corrupt_nested_zip(tmp_path: Path) -> None:
    archive = _write_zip(tmp_path / "t.zip", {"rotto.zip": b"non e' un zip"})
    with pytest.raises(files.UnsafeArchiveError, match="non valido"):
        files.extract_flat_atomically(archive, tmp_path / "out")
    assert not (tmp_path / "out").exists()
