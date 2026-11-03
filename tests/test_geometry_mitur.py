"""Test offline delle tratte MiTur: lettori GPX/KML/KMZ, tipo e ordine, duplicati interni, percorsi interi, stato."""
import csv
import zipfile
from pathlib import Path

import pytest

from cammitaly import config
from cammitaly.geometry import common, mitur

GPX_HEAD = '<?xml version="1.0" encoding="UTF-8"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">'


def trkpts(coords: list[tuple[float, float]]) -> str:
    return "".join(f'<trkpt lat="{lat}" lon="{lon}"><ele>10</ele></trkpt>' for lon, lat in coords)


def line(lon0: float, lat0: float, n: int = 10, step: float = 0.002) -> list[tuple[float, float]]:
    return [(lon0 + i * step, lat0) for i in range(n)]


def gpx_track(coords: list[tuple[float, float]]) -> str:
    return f"{GPX_HEAD}<trk><trkseg>{trkpts(coords)}</trkseg></trk></gpx>"


def kml_line(coords: list[tuple[float, float]], name: str = "linea") -> str:
    text = " ".join(f"{lon},{lat},0" for lon, lat in coords)
    return f"<Placemark><name>{name}</name><LineString><coordinates>{text}</coordinates></LineString></Placemark>"


def kml_doc(*placemarks: str) -> str:
    return f'<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>{"".join(placemarks)}</Document></kml>'


@pytest.fixture
def tracks_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "MITUR_TRACKS_DIR", tmp_path)
    return tmp_path


def put(tracks_dir: Path, slug: str, stored: str, original: str, content: str | bytes) -> None:
    folder = tracks_dir / slug
    folder.mkdir(exist_ok=True)
    data = content.encode("utf-8") if isinstance(content, str) else content
    (folder / stored).write_bytes(data)
    manifest = folder / config.ZIP_MANIFEST_FILE
    is_new = not manifest.exists()
    with manifest.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if is_new:
            writer.writerow(config.ZIP_MANIFEST_FIELDS)
        writer.writerow([stored, original, "x.zip", 0, len(data)])


def test_gpx_reader_takes_segments_and_routes_and_ignores_waypoints():
    xml = (
        f'{GPX_HEAD}<wpt lat="44.0" lon="11.0"/>'
        f"<trk><trkseg>{trkpts(line(11.0, 44.0, 3))}</trkseg><trkseg>{trkpts(line(11.1, 44.0, 3))}</trkseg></trk>"
        '<rte><rtept lat="44.1" lon="11.0"/><rtept lat="44.1" lon="11.01"/></rte></gpx>'
    )
    content = mitur.read_gpx(mitur._parse_xml(xml.encode()))
    assert len(content.items) == 1 and len(content.items[0].parts) == 3  # due trkseg e una rotta
    assert content.has_points


def test_gpx_with_only_waypoints_has_no_items():
    content = mitur.read_gpx(mitur._parse_xml(f'{GPX_HEAD}<wpt lat="44.0" lon="11.0"/></gpx>'.encode()))
    assert content.items == [] and content.has_points


def test_gpx_with_undeclared_prefix_in_extensions_is_recovered():
    xml = f'{GPX_HEAD}<wpt lat="44.0" lon="11.0"><extensions><wptx1:Ext wptx1:a="1"/></extensions></wpt></gpx>'
    content = mitur.read_gpx(mitur._parse_xml(xml.encode()))
    assert content.items == [] and content.has_points


def test_xml_with_entities_or_garbage_is_unreadable():
    with pytest.raises(mitur.UnreadableFile):
        mitur._parse_xml(b'<!DOCTYPE x [<!ENTITY a "b">]><gpx>&a;</gpx>')
    with pytest.raises(mitur.UnreadableFile):
        mitur._parse_xml(b"<gpx><trk>")


def test_kml_reader_one_item_per_placemark_with_lines_and_points_flagged():
    point = "<Placemark><name>Albergo</name><Point><coordinates>11.0,44.0,0</coordinates></Point></Placemark>"
    root = mitur._parse_xml(kml_doc(kml_line(line(11.0, 44.0), "TAPPA 01"), kml_line(line(11.1, 44.0), "TAPPA 02"), point).encode())
    content = mitur.read_kml(root)
    assert [item.name for item in content.items] == ["TAPPA 01", "TAPPA 02"] and content.has_points


def test_kml_gx_track_is_a_line():
    xml = (
        '<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2"><Placemark>'
        "<gx:Track><gx:coord>11.0 44.0 0</gx:coord><gx:coord>11.01 44.0 0</gx:coord></gx:Track></Placemark></kml>"
    )
    content = mitur.read_kml(mitur._parse_xml(xml.encode()))
    assert len(content.items) == 1 and len(content.items[0].parts[0]) == 2


def test_kmz_is_read_in_memory_and_only_kml_members(tmp_path: Path):
    path = tmp_path / "mappa.kmz"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("doc.kml", kml_doc(kml_line(line(11.0, 44.0), "A")))
        archive.writestr("images/icon.png", b"not a kml")
    content = mitur.read_track_file(path, config.MITUR_KMZ_EXTENSION)
    assert len(content.items) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["mappa.kmz"]  # niente estratto su disco
    (tmp_path / "rotto.kmz").write_bytes(b"non e' uno zip")
    with pytest.raises(mitur.UnreadableFile):
        mitur.read_track_file(tmp_path / "rotto.kmz", config.MITUR_KMZ_EXTENSION)


@pytest.mark.parametrize(
    ("name", "folder", "tipo", "ordine"),
    [
        ("Tappa-10-San-Severino", "", config.TIPO_TAPPA, 10),
        ("Tappa1-Fossombrone", "", config.TIPO_TAPPA, 1),
        ("tappa_10_Asolo", "", config.TIPO_TAPPA, 10),
        ("CSC tappa 05a _ GUARDABOSONE", "", config.TIPO_VARIANTE, 5),
        ("1 Tappa - Cammino della Madonna Nera", "", config.TIPO_TAPPA, 1),
        ("Fiore 07-1b tappa - da Cabiate a Carimate", "", config.TIPO_TAPPA, 7),
        ("T10_Balze", "", config.TIPO_TAPPA, 10),
        ("celestino_t10", "", config.TIPO_TAPPA, 10),
        ("10 - Valfabbrica", "", config.TIPO_TAPPA, 10),
        ("01.Molfetta-Ruvo", "", config.TIPO_TAPPA, 1),
        ("1) Castel di Tusa - TUSA", "", config.TIPO_TAPPA, 1),
        ("10º MANDELA-SUBIACO", "", config.TIPO_TAPPA, 10),  # il grado non e' un suffisso
        ("10bis_Mandela variante diretta", "", config.TIPO_VARIANTE, 10),
        ("cb-61-bianco-samo-traccia", "", config.TIPO_TAPPA, 61),
        ("t231091934_sdf 02 venarotta", "", config.TIPO_TAPPA, 2),
        ("t004v-vicopisano-buti", "", config.TIPO_TAPPA, 4),  # la v non e' un suffisso di variante
        ("Foglia Occidentale FW03 - da Saronno", "", config.TIPO_TAPPA, 3),
        ("Tappa 4- VARIANTE Oneta", "", config.TIPO_VARIANTE, 4),
        ("cb-16-paludi-longobucco-(opzione-2)", "", config.TIPO_VARIANTE, 16),
        ("celestino_var_bugl", "", config.TIPO_VARIANTE, None),
        ("Tappa 2 Selvino", "TRACCE GPX 8 TAPPE MTB", config.TIPO_BICI, 2),
        ("T1_Rimini", "Percorsi in mountain bike", config.TIPO_BICI, 1),
        ("CFM unica traccia mountainbike 2022", "", config.TIPO_BICI, None),
        ("Tappa 12 Sulmona IN BICI", "", config.TIPO_BICI, 12),
        ("1 tappa - 3 tratto - strada pedonale ciclabile", "", config.TIPO_TAPPA, 1),  # si cammina anche a piedi
        ("S5 Poppi - Bibbiena (su ciclopedonale Arno)", "", config.TIPO_TAPPA, 5),
        ("Cammino del Santo Marino - Full", "", config.TIPO_ALTRO, None),
        ("Tappa 1 percorso completo", "", config.TIPO_ALTRO, None),
        ("Cammino di San Bartolomeo", "", config.TIPO_ALTRO, None),
        ("Traccia 2024 definitiva", "", config.TIPO_ALTRO, None),  # un anno non e' un numero di tappa
    ],
)
def test_classify(name, folder, tipo, ordine):
    assert mitur.classify(name, folder) == (tipo, ordine)


def test_file_tratte_reasons_for_limbo(tracks_dir: Path):
    put(tracks_dir, "c", "0001_pts.gpx", "pts.gpx", f'{GPX_HEAD}<wpt lat="44.0" lon="11.0"/></gpx>')
    put(tracks_dir, "c", "0002_rotto.gpx", "rotto.gpx", "<gpx><trk>")
    put(tracks_dir, "c", "0003_abroad.gpx", "abroad.gpx", gpx_track(line(2.0, 48.0)))  # Francia
    put(tracks_dir, "c", "0004_note.pdf", "note.pdf", b"%PDF")
    put(tracks_dir, "c", "0005_dati.geojson", "dati.geojson", "{}")
    put(tracks_dir, "c", "0006_tiny.gpx", "tiny.gpx", gpx_track([(11.0, 44.0), (11.0, 44.0)]))
    result = mitur.build_card("c")
    motivi = {row["oggetto"].rsplit(":", 1)[-1]: row["motivo"] for row in result.limbo}
    assert motivi == {
        "pts": config.MOTIVO_SOLO_PUNTI, "rotto": config.MOTIVO_FILE_ILLEGGIBILE, "abroad": config.MOTIVO_FUORI_ITALIA,
        "dati": config.MOTIVO_FORMATO_NON_LETTO, "tiny": config.MOTIVO_TRACCIA_SENZA_GEOMETRIA,
    }
    assert result.tratte == []  # il pdf e' rumore: nessuna riga di limbo


def test_tratte_contract_fields_and_ids(tracks_dir: Path):
    put(tracks_dir, "c", "0001_Tappa 1.gpx", "cartella/Tappa 1.gpx", gpx_track(line(11.0, 44.0, 20)))
    result = mitur.build_card("c")
    record = result.tratte[0].record
    assert record["tratta_id"] == "mitur:c:Tappa 1" and record["cammino_id"] == "mitur:c"
    assert record["fonte"] == "mitur" and record["tipo"] == config.TIPO_TAPPA and record["ordine"] == 1
    assert record["n_vertici"] == 20 and record["geometry"].geom_type == "LineString"
    assert record["file_origine"].endswith("c/0001_Tappa 1.gpx") or record["file_origine"].endswith("0001_Tappa 1.gpx")
    frame = common.tratte_frame([t.record for t in result.tratte])
    assert list(frame.columns) == [*config.TRATTE_ATTRIBUTES, "geometry"]


def test_internal_duplicate_across_formats_keeps_gpx(tracks_dir: Path):
    coords = line(11.0, 44.0, 30)
    shifted = [(lon, lat + 0.00005) for lon, lat in coords]  # circa 5.5 m di scarto
    put(tracks_dir, "c", "0001_Tappa 1.kml", "Tappa 1.kml", kml_doc(kml_line(shifted)))
    put(tracks_dir, "c", "0002_Tappa 1.gpx", "Tappa 1.gpx", gpx_track(coords))
    put(tracks_dir, "c", "0003_Tappa 2.gpx", "Tappa 2.gpx", gpx_track(line(11.2, 44.0, 30)))
    result = mitur.build_card("c")
    ids = sorted(t.record["tratta_id"] for t in result.tratte)
    assert ids == ["mitur:c:Tappa 1", "mitur:c:Tappa 2"]
    assert {t.extension for t in result.tratte if t.record["ordine"] == 1} == {".gpx"}
    dup = [row for row in result.limbo if row["motivo"] == config.MOTIVO_DUPLICATO_INTERNO]
    assert len(dup) == 1 and dup[0]["oggetto"] == "mitur:c:Tappa 1:2" and "mitur:c:Tappa 1" in dup[0]["dettaglio"]


def test_similar_but_different_length_is_not_a_duplicate(tracks_dir: Path):
    put(tracks_dir, "c", "0001_Tappa 1.gpx", "Tappa 1.gpx", gpx_track(line(11.0, 44.0, 30)))
    put(tracks_dir, "c", "0002_Tappa 2.gpx", "Tappa 2.gpx", gpx_track(line(11.0, 44.0, 20)))  # stesso inizio, 2/3 della lunghezza
    result = mitur.build_card("c")
    assert len(result.tratte) == 2 and result.n_duplicates == 0


def test_full_route_next_to_its_stages_is_kept_as_altro(tracks_dir: Path):
    for i in range(4):
        put(tracks_dir, "c", f"{i + 1:04d}_Tappa {i + 1}.gpx", f"Tappa {i + 1}.gpx", gpx_track(line(11.0 + i * 0.018, 44.0, 10)))
    put(tracks_dir, "c", "0005_Percorso completo.gpx", "Percorso completo.gpx", gpx_track(line(11.0, 44.0, 37)))  # unione delle quattro
    result = mitur.build_card("c")
    tipi = {t.record["nome"]: t.record["tipo"] for t in result.tratte}
    assert tipi["Percorso completo"] == config.TIPO_ALTRO  # per nome
    assert list(tipi.values()).count(config.TIPO_TAPPA) == 4  # le tappe restano 4: l'intero non e' un loro duplicato
    assert result.n_duplicates == 0 and result.n_full_routes == 0


def test_full_route_found_by_coverage_when_name_gives_no_hint(tracks_dir: Path):
    for i in range(4):
        put(tracks_dir, "c", f"{i + 1:04d}_Tappa {i + 1}.gpx", f"Tappa {i + 1}.gpx", gpx_track(line(11.0 + i * 0.018, 44.0, 10)))
    put(tracks_dir, "c", "0005_Tappa 9.gpx", "Tappa 9.gpx", gpx_track(line(11.0, 44.0, 37)))
    result = mitur.build_card("c")
    by_name = {t.record["nome"]: t.record for t in result.tratte}
    assert by_name["Tappa 9"]["tipo"] == config.TIPO_ALTRO and by_name["Tappa 9"]["ordine"] is None
    assert result.n_full_routes == 1


def test_multi_placemark_kmz_gives_numbered_ids_and_loses_to_gpx_copies(tracks_dir: Path):
    kmz = tracks_dir / "k.kmz"
    with zipfile.ZipFile(kmz, "w") as archive:
        archive.writestr("doc.kml", kml_doc(kml_line(line(11.0, 44.0, 30), "TAPPA 01"), kml_line(line(11.3, 44.0, 30), "TAPPA 02")))
    put(tracks_dir, "c", "0001_Mappa.kmz", "Mappa.kmz", kmz.read_bytes())
    put(tracks_dir, "c", "0002_Tappa 1.gpx", "Tappa 1.gpx", gpx_track(line(11.0, 44.0, 30)))
    result = mitur.build_card("c")
    ids = sorted(t.record["tratta_id"] for t in result.tratte)
    assert ids == ["mitur:c:Mappa:2", "mitur:c:Tappa 1"]  # TAPPA 01 del KMZ e' copia del GPX
    assert {t.record["nome"]: t.record["ordine"] for t in result.tratte} == {"TAPPA 02": 2, "Tappa 1": 1}


def test_build_all_cammini_rows_stato_and_limbo(tracks_dir: Path):
    put(tracks_dir, "ok", "0001_Tappa 1.gpx", "Tappa 1.gpx", gpx_track(line(11.0, 44.0)))
    put(tracks_dir, "solo-altro", "0001_Percorso.gpx", "Percorso.gpx", gpx_track(line(12.0, 44.0)))
    put(tracks_dir, "punti", "0001_wpt.gpx", "wpt.gpx", f'{GPX_HEAD}<wpt lat="44.0" lon="11.0"/></gpx>')
    base = {"regioni": "Lazio", "sito_web": "http://x", "nota": ""}
    cards = [
        {**base, "nome": "Ok", "slug": "ok", "url_percorso": "http://z/ok.zip"},
        {**base, "nome": "Solo altro", "slug": "solo-altro", "url_percorso": "http://z/a.zip"},
        {**base, "nome": "Punti", "slug": "punti", "url_percorso": "http://z/p.zip"},
        {**base, "nome": "Manca", "slug": "manca", "url_percorso": "http://z/m.zip"},
        {**base, "nome": "Senza link", "slug": "senza-link", "url_percorso": "", "nota": "link esterno"},
    ]
    frame, rows, limbo = mitur.build_all(cards)
    stato = {r["cammino_id"]: r["stato_geometria"] for r in rows}
    assert stato == {
        "mitur:ok": config.STATO_OK, "mitur:solo-altro": config.STATO_PARZIALE, "mitur:punti": config.STATO_ASSENTE,
        "mitur:manca": config.STATO_ASSENTE, "mitur:senza-link": config.STATO_ASSENTE,
    }
    assert len(rows) == 5 and set(rows[0]) == set(config.CAMMINI_FIELDS)
    assert rows[0]["n_tappe_dichiarate"] == "" and rows[0]["n_tratte"] == 1 and rows[0]["lunghezza_m"] > 0
    motivi = {(r["oggetto"], r["motivo"]) for r in limbo}
    assert ("mitur:senza-link", config.MOTIVO_SENZA_PERCORSO) in motivi
    assert ("mitur:manca", config.MOTIVO_CAMMINO_SENZA_GEOMETRIA) in motivi
    assert ("mitur:punti", config.MOTIVO_CAMMINO_SENZA_GEOMETRIA) in motivi
    assert ("mitur:punti:wpt", config.MOTIVO_SOLO_PUNTI) in motivi
    assert next(r for r in limbo if r["oggetto"] == "mitur:senza-link")["dettaglio"] == "link esterno"
    assert len(frame) == 2


def test_long_stage_with_short_variants_on_it_is_not_a_full_route(tracks_dir: Path):
    for i in range(4):
        put(tracks_dir, "c", f"{i + 1:04d}_Tappa {i + 1}.gpx", f"Tappa {i + 1}.gpx", gpx_track(line(12.0 + i * 0.05, 44.0, 10)))
    put(tracks_dir, "c", "0005_Tappa 5.gpx", "Tappa 5.gpx", gpx_track(line(11.0, 44.0, 30)))
    for k, start in enumerate((11.0, 11.02, 11.04)):  # tre varianti brevi sovrapposte alla tappa 5
        put(tracks_dir, "c", f"{6 + k:04d}_Tappa 5 variante {k}.gpx", f"Tappa 5 variante {k}.gpx", gpx_track(line(start, 44.0, 4)))
    result = mitur.build_card("c")
    assert result.n_full_routes == 0
    assert {t.record["nome"]: t.record["tipo"] for t in result.tratte}["Tappa 5"] == config.TIPO_TAPPA


@pytest.mark.parametrize(
    ("name", "folder", "stem", "tipo", "ordine"),
    [
        ("1-CamminoSantuJacu-ASSE_CENTRALE-porto-torres-cagliari", "", "", config.TIPO_ALTRO, None),  # asse, non tappa
        ("csj-da-olbia-a-orosei-per-camminanti-e-mtb", "", "", config.TIPO_ALTRO, None),  # si cammina: non e' bici
        ("csj-da-porto-torres-a-orosei-per-camminanti-e-ciclisti", "", "", config.TIPO_ALTRO, None),
        ("Percorso per ciclisti", "", "", config.TIPO_BICI, None),
        ("Via-del-Sud-tappa-4-da-Rieti-a-Piediluco", "Gpx/in-bici", "", config.TIPO_BICI, 4),  # Piediluco non e' "a piedi"
        ("Percorso a piedi e in mtb", "", "", config.TIPO_ALTRO, None),
        ("Tappa 3", "Percorsi a piedi/mountain bike", "", config.TIPO_BICI, 3),  # "piedi" solo nella cartella: resta bici
        ("tappa-msa-01-da-troia-lucera", "", "", config.TIPO_TAPPA, 1),
        ("csj-del-nord-48-da-chiaramonti-a-perfugas", "", "", config.TIPO_TAPPA, 48),
        ("Chiavenna Dascio", "", "T. 03 CHIAVENNA_DASCIO", config.TIPO_TAPPA, 3),  # numero dal nome del file (KMZ)
        ("Chiavenna Dascio", "", "Percorso completo", config.TIPO_ALTRO, None),
    ],
)
def test_classify_review_cases(name, folder, stem, tipo, ordine):
    assert mitur.classify(name, folder, stem) == (tipo, ordine)


def test_stage_longer_than_the_cap_becomes_altro_with_a_note(tracks_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "MITUR_MAX_STAGE_LENGTH_M", 3000)
    put(tracks_dir, "c", "0001_Tappa 1.gpx", "Tappa 1.gpx", gpx_track(line(11.0, 44.0, 10)))  # circa 1.3 km
    put(tracks_dir, "c", "0002_Tappa 2.gpx", "Tappa 2.gpx", gpx_track(line(11.5, 44.0, 40)))  # circa 5.6 km
    result = mitur.build_card("c")
    by_name = {t.record["nome"]: t.record for t in result.tratte}
    assert by_name["Tappa 1"]["tipo"] == config.TIPO_TAPPA
    assert by_name["Tappa 2"]["tipo"] == config.TIPO_ALTRO and by_name["Tappa 2"]["ordine"] is None
    assert result.n_too_long == 1
    frame = common.tratte_frame([t.record for t in result.tratte])
    card = {"nome": "C", "slug": "c", "regioni": "", "sito_web": "", "nota": ""}
    assert "oltre 3 km" in mitur.cammino_row(card, frame, result)["note"]


def test_extensionless_gpx_and_kmz_are_sniffed(tracks_dir: Path):
    put(tracks_dir, "c", "0001_tappa-15a-variante-gpx", "cartella/tappa-15a-variante-gpx", gpx_track(line(11.0, 44.0)))
    kmz = tracks_dir / "k.kmz"
    with zipfile.ZipFile(kmz, "w") as archive:
        archive.writestr("doc.kml", kml_doc(kml_line(line(12.0, 44.0), "A")))
    put(tracks_dir, "c", "0002_Tappa 2", "Tappa 2", kmz.read_bytes())
    put(tracks_dir, "c", "0003_dati.geojson", "dati.geojson", '{"type": "FeatureCollection"}')
    put(tracks_dir, "c", "0004_senza", "senza", "testo qualunque")
    result = mitur.build_card("c")
    assert sorted(t.record["nome"] for t in result.tratte) == ["Tappa 2", "tappa-15a-variante-gpx"]
    assert {t.record["nome"]: t.extension for t in result.tratte} == {
        "tappa-15a-variante-gpx": ".gpx", "Tappa 2": ".kmz",
    }
    motivi = {row["oggetto"].rsplit(":", 1)[-1]: row["motivo"] for row in result.limbo}
    assert motivi == {"dati": config.MOTIVO_FORMATO_NON_LETTO, "senza": config.MOTIVO_FORMATO_NON_LETTO}


def test_utf16_entity_declaration_and_internal_doctype_subset_are_rejected():
    bomb = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE gpx [<!ENTITY a "b">]><gpx>&a;</gpx>'
    with pytest.raises(mitur.UnreadableFile):
        mitur._parse_xml(bomb.encode("utf-16"))
    with pytest.raises(mitur.UnreadableFile):
        mitur._parse_xml(bomb.encode("utf-16-le"))  # senza BOM
    with pytest.raises(mitur.UnreadableFile):
        mitur._parse_xml(b"<!DOCTYPE gpx [ <!ELEMENT gpx ANY> ]><gpx/>")
    assert mitur._parse_xml(b"<!DOCTYPE gpx><gpx/>").tag == "gpx"  # un DOCTYPE senza sottoinsieme e' innocuo


def test_utf8_file_with_cp1252_byte_is_read_by_the_last_resort():
    xml = f'{GPX_HEAD}<metadata><name>Gerace X Ardore</name></metadata><trk><trkseg>{trkpts(line(11.0, 44.0, 3))}</trkseg></trk></gpx>'
    data = xml.encode("utf-8").replace(b"X", b"\x96")  # 0x96 e' un trattino lungo in cp1252 ma un byte non valido in UTF-8
    with pytest.raises(UnicodeDecodeError):
        data.decode("utf-8")
    content = mitur.read_gpx(mitur._parse_xml(data))
    assert len(content.items[0].parts[0]) == 3


def test_outside_italy_needs_the_whole_bbox_outside():
    assert mitur.outside_italy([[(2.0, 48.0), (2.1, 48.1)]])  # Francia
    assert mitur.outside_italy([[(11.0, 52.0), (11.1, 52.1)]])  # a nord del riquadro
    assert not mitur.outside_italy([[(9.0, 44.0), (9.1, 44.1)]])
    assert not mitur.outside_italy([[(5.0, 46.0), (7.0, 46.0)]])  # un cammino in parte all'estero va bene
    assert not mitur.outside_italy([[(12.5, 35.5), (12.6, 35.6)]])  # Lampedusa
