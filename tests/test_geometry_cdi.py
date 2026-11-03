"""Test offline delle tratte camminiditalia: assegnazione, tipo, pulizia dei vertici, proiezione, stato."""
import json
from pathlib import Path

import pytest

from cammitaly import config
from cammitaly.geometry import cdi, common

CAMMINI = [
    {"id": "1", "titolo": "Cammino Unico", "webmapp_layer": "10", "n_tappe": "2", "lunghezza_km": "20",
     "regioni": "lazio", "link": "http://x/1"},
    {"id": "2", "titolo": "Via Doppia \u2013 la Via dell\u2019Agogna", "webmapp_layer": "20", "n_tappe": "2",
     "lunghezza_km": "", "regioni": "", "link": ""},
    {"id": "3", "titolo": "Via Doppia \u2013 Cammino Devozionale", "webmapp_layer": "20", "n_tappe": "1",
     "lunghezza_km": "", "regioni": "", "link": ""},
    {"id": "4", "titolo": "Cammino Senza Tracce", "webmapp_layer": "", "n_tappe": "3/4",
     "lunghezza_km": "", "regioni": "", "link": ""},
    {"id": "5", "titolo": "Cammino dei Briganti", "webmapp_layer": "50", "n_tappe": "5",
     "lunghezza_km": "", "regioni": "", "link": ""},
    {"id": "6", "titolo": "Grande Cammino dei Briganti", "webmapp_layer": "", "n_tappe": "",
     "lunghezza_km": "", "regioni": "", "link": ""},
]
TITLES = {c["id"]: c["titolo"] for c in CAMMINI}
BY_LAYER = {"10": ["1"], "20": ["2", "3"], "50": ["5"]}


def assign(name: str, layers: list[str], ref: str = "", taken: dict | None = None) -> cdi.Assignment:
    return cdi.assign_track(name, ref, layers, BY_LAYER, TITLES, taken)


def line_json(coords: list[list[float]]) -> str:
    return json.dumps({"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords}})


def test_unique_layer_takes_all_tracks_whatever_the_name():
    result = assign("Nome Qualsiasi - Tappa 01: A - B", ["10"])
    assert result.cammini == ("1",) and result.motivo is None


def test_shared_layer_clear_winner_with_unicode_dashes_and_quotes():
    # il titolo ha U+2013 e U+2019, il nome della traccia ASCII
    result = assign("Via Doppia - la Via dell'Agogna - Tappa 01: A - B", ["20"])
    assert result.cammini == ("2",) and result.motivo is None
    result = assign("Via Doppia - Cammino Devozionale - Tappa 01: A - B", ["20"])
    assert result.cammini == ("3",)


def test_shared_layer_common_stage_ties_every_candidate():
    result = assign("Via Doppia - Tappa comune 03: A - B", ["20"])
    assert set(result.cammini) == {"2", "3"}
    assert result.motivo == config.MOTIVO_MULTIPLA


def test_shared_layer_no_acceptable_match():
    result = assign("Qualcosa di Completamente Diverso - Tappa 01: A - B", ["20"])
    assert result.cammini == () and result.motivo == config.MOTIVO_NON_ASSEGNATA


def test_orphan_layer_is_recovered_by_name_only_with_strict_score():
    recovered = assign("Cammino Senza Tracce - Tappa 01: A - B", ["99"])
    assert recovered.cammini == ("4",) and recovered.motivo == config.MOTIVO_LAYER_ORFANO
    lost = assign("Altro Cammino Mai Visto - Tappa 01: A - B", [])
    assert lost.cammini == () and lost.motivo == config.MOTIVO_LAYER_ORFANO


def test_fallback_skips_a_stage_the_cammino_already_has_from_its_layer():
    name, ref = "Cammino dei Briganti - Tappa 02: A - B", "Tappa 2"
    taken = {("5", cdi.stage_key(name, ref)): "777"}
    duplicate = assign(name, [], ref, taken)
    assert duplicate.cammini == () and duplicate.motivo == config.MOTIVO_DUPLICATO_SENZA_LAYER
    assert "cdi:5" in duplicate.dettaglio and "cdi:777" in duplicate.dettaglio
    # un'altra tappa dello stesso cammino non e' un duplicato: colma un vuoto e viene recuperata
    other = assign("Cammino dei Briganti - Tappa 03: A - B", [], "Tappa 3", taken)
    assert other.cammini == ("5",) and other.motivo == config.MOTIVO_LAYER_ORFANO


def test_stage_key_tells_apart_variants_of_the_same_number():
    estiva = cdi.stage_key("X - Tappa 05 (ESTIVA): A - B", "Tappa 5")
    invernale = cdi.stage_key("X - Tappa 05 (INVERNALE): A - B", "Tappa 5")
    assert estiva == cdi.stage_key("X - Tappa 05 (ESTIVA): C - D", "Tappa 5") and estiva != invernale
    assert cdi.stage_key("X - Tappa 04: A - B", "") != cdi.stage_key("X - Tappa 04 N: A - B", "")


def test_fallback_prefers_the_title_that_equals_the_name_prefix():
    # "Cammino dei Briganti" e' un sottoinsieme di "Grande Cammino dei Briganti": token_set li da' a pari merito
    result = assign("Cammino dei Briganti - Tappa 07: A - B", [], "Tappa 7")
    assert result.cammini == ("5",)


@pytest.mark.parametrize(
    ("name", "ref", "tipo"),
    [
        ("X - Tappa 03: A - B", "Tappa 03", config.TIPO_TAPPA),
        ("X - Tappa comune 03: A - B", "", config.TIPO_TAPPA),
        ("X - Tappa 03a: A - B", "Tappa 03a", config.TIPO_VARIANTE),
        ("X | Tappa 04a:Muccia - Belforte", "", config.TIPO_VARIANTE),
        ("X - Tappa 03bis: A - B", "", config.TIPO_VARIANTE),
        ("X - Tappa 28 bis : A - B", "Tappa 28 bis", config.TIPO_VARIANTE),
        ("X - Tappa 09 Bis: A - B", "Tappa 9 Bis", config.TIPO_VARIANTE),
        ("X - Tappa 04 N: A - B", "Tappa 4 Nord", config.TIPO_VARIANTE),
        ("X - Tappa 04 S: A - B", "", config.TIPO_VARIANTE),
        ("X - Tappa 05/A: A - B", "Tappa 5/A", config.TIPO_VARIANTE),
        ("X - Tappa 05/B: A - B", "", config.TIPO_VARIANTE),
        ("X - Variante Tenuta dei Ciclamini", "", config.TIPO_VARIANTE),
        ("X - Tappa 1 ALTERNATIVA", "", config.TIPO_VARIANTE),
        ("X - Tappa 05 (INVERNALE): A - B", "", config.TIPO_VARIANTE),
        ("X - Tappa 05 (ESTIVA): A - B", "", config.TIPO_VARIANTE),
        ("X - Tappa 06: A - B", "Tappa 6 (opzionale)", config.TIPO_VARIANTE),
        ("X - Tappa 04 (Percorso Breve): A - B", "", config.TIPO_VARIANTE),
        ("X - Tappa 04 (Percorso Lungo): A - B", "", config.TIPO_VARIANTE),
        ("X - Tappa 05 Via diretta: A - B", "", config.TIPO_VARIANTE),
        ("X - Tappa 03 MTB: A - B", "", config.TIPO_BICI),
        ("X - Percorso in bici: A - B", "", config.TIPO_BICI),
        ("X - Tappa 03: da Lecce a Otranto", "", config.TIPO_TAPPA),
        ("X - Tappa 03: S. Maria - San Martino", "", config.TIPO_TAPPA),
        ("X - Tappa 03: A - B", "Tappa 3 e Tappa 3bis", config.TIPO_TAPPA),
        ("X - Tappa 04: A - B", "Tappa 4 Variante Frana", config.TIPO_VARIANTE),
        ("X - Tappa 47: La Storta - ciclovia a Roma", "Tappa 47", config.TIPO_TAPPA),
        ("X - Percorso Completo: A - A", "", config.TIPO_ALTRO),
        # la parola Variante nel nome del cammino non rende variante la tappa
        ("Cammino di San Vili (Variante Bassa) - Tappa 01: A - B", "Tappa 1", config.TIPO_TAPPA),
    ],
)
def test_classify_tipo(name, ref, tipo):
    assert cdi.classify_tipo(name, ref) == tipo


def test_stage_number_and_declared_stages():
    assert cdi.stage_number("X - Tappa comune 05: A - B", "") == 5
    assert cdi.stage_number("X senza numero", "Tappa 07a") == 7
    assert cdi.stage_number("X senza numero", "") is None
    assert [cdi.parse_declared_stages(v) for v in ("12", "3/4", "7 (vedi)", "", "Tre percorsi")] == [12, 3, 7, None, None]


def test_clean_line_drops_elevation_and_consecutive_duplicates():
    line = common.clean_line([[11.0, 44.0, 100], [11.0, 44.0, 101], [11.1, 44.0, 102], [11.0, 44.0, 103]])
    assert line == [(11.0, 44.0), (11.1, 44.0), (11.0, 44.0)]  # il ritorno al punto iniziale non e' consecutivo


def test_build_geometry_drops_short_parts_and_keeps_multi():
    assert common.build_geometry([[[11.0, 44.0], [11.0, 44.0]]]) is None
    single = common.build_geometry([[[11.0, 44.0, 5], [11.01, 44.0, 6]], [[11.0, 44.0]]])
    assert single.geom_type == "LineString"
    multi = common.build_geometry([[[11.0, 44.0], [11.01, 44.0]], [[11.1, 44.0], [11.11, 44.0]]])
    assert multi.geom_type == "MultiLineString"
    stats = common.line_stats(multi)
    assert stats["n_vertici"] == 4
    assert stats["spaziatura_media_m"] == pytest.approx(stats["lunghezza_m"] / 2, abs=0.01)


def test_build_geometry_splits_a_part_at_a_gap_longer_than_the_limit():
    # 0.3 gradi di longitudine a 45 N sono circa 23 km: oltre GAP_SPLIT_M
    coords = [[9.0, 45.0], [9.001, 45.0], [9.3, 45.0], [9.301, 45.0]]
    geom = common.build_geometry([coords])
    assert geom.geom_type == "MultiLineString" and len(geom.geoms) == 2
    stats = common.line_stats(geom)
    assert stats["lunghezza_m"] < 300  # il salto non entra nella lunghezza
    assert stats["spaziatura_media_m"] == pytest.approx(stats["lunghezza_m"] / 2, abs=0.01)
    # un salto sotto il limite non spezza
    near = common.build_geometry([[[9.0, 45.0], [9.001, 45.0], [9.05, 45.0]]])
    assert near.geom_type == "LineString"


def test_reprojection_to_utm_32n_within_one_meter():
    # Meridiano centrale della zona 32N (9 E): est = 500000 m. Il nord vale k0 * arco di meridiano WGS84:
    # 0.9996 * 4984944.38 m a 45 N (valore di tabella) = 4982950.40 m
    # (due segmenti brevi: un solo salto da 0 a 45 N supererebbe il limite di spezzatura)
    equator = common.build_geometry([[[9.0, 0.0], [9.0, 0.01]]])
    mid = common.build_geometry([[[9.0, 45.0], [9.0, 45.01]]])
    assert list(equator.coords)[0] == pytest.approx((500000.0, 0.0), abs=1.0)
    assert list(mid.coords)[0] == pytest.approx((500000.0, 4982950.40), abs=1.0)


def test_stato_geometria_counts_distinct_stages_not_rows():
    assert common.stato_geometria(0, 5, 0) == config.STATO_ASSENTE
    assert common.stato_geometria(3, 5, 3) == config.STATO_PARZIALE
    assert common.stato_geometria(5, 5, 5) == config.STATO_OK
    assert common.stato_geometria(2, None, 2) == config.STATO_OK
    assert common.stato_geometria(0, 5, 2) == config.STATO_PARZIALE  # solo altro/variante: c'e' geometria


def _frame(rows: list[tuple[str, str, int | None]]):
    geom = common.build_geometry([[[11.0, 44.0], [11.01, 44.0]]])
    records = [
        {"tratta_id": f"cdi:t{i}", "cammino_id": cid, "fonte": "cdi", "nome": "n", "ordine": ordine, "tipo": tipo,
         **common.line_stats(geom), "file_origine": "x", "geometry": geom}
        for i, (cid, tipo, ordine) in enumerate(rows)
    ]
    return common.tratte_frame(records)


def test_cammini_rows_distinct_stages_and_only_other_tracks():
    frame = _frame([
        ("cdi:1", config.TIPO_TAPPA, 1), ("cdi:1", config.TIPO_TAPPA, 1),  # stessa tappa due volte
        ("cdi:5", config.TIPO_ALTRO, None),  # solo tracce "altro"
    ])
    cammini = [CAMMINI[0], CAMMINI[4], CAMMINI[3]]
    rows, limbo = cdi.cammini_rows(cammini, frame, {"10", "50"})
    by_id = {r["cammino_id"]: r for r in rows}
    assert by_id["cdi:1"]["stato_geometria"] == config.STATO_PARZIALE  # 1 tappa distinta su 2 dichiarate
    assert "numero ripetuto" in by_id["cdi:1"]["note"]
    assert by_id["cdi:5"]["stato_geometria"] == config.STATO_PARZIALE and "solo altro" in by_id["cdi:5"]["note"]
    assert by_id["cdi:4"]["stato_geometria"] == config.STATO_ASSENTE
    assert [row["oggetto"] for row in limbo] == ["cdi:4"]


def test_length_anomaly_outside_ratio_range():
    record = {"tratta_id": "cdi:9", "cammino_id": "cdi:1", "ordine": 3, "tipo": config.TIPO_TAPPA, "lunghezza_m": 48000.0}
    row = cdi._length_anomaly(record, 18.7)
    assert row is not None and row["motivo"] == config.MOTIVO_LUNGHEZZA_ANOMALA and "48.0 km" in row["dettaglio"]
    assert cdi._length_anomaly(record, 30.0) is None  # rapporto 1.6: nei limiti
    assert cdi._length_anomaly({**record, "lunghezza_m": 8000.0}, 20.0) is not None  # rapporto 0.4
    assert cdi._length_anomaly({**record, "tipo": config.TIPO_VARIANTE}, 18.7) is None
    assert cdi._length_anomaly(record, None) is None


def test_declared_km_prefers_the_listed_distance_over_the_stage_table():
    by_stage = {("1", 3): 20.0}
    assert cdi._declared_km({"distance": "18.7"}, "1", 3, by_stage) == 18.7
    assert cdi._declared_km({"distance": ""}, "1", 3, by_stage) == 20.0
    assert cdi._declared_km({"distance": ""}, "1", 4, by_stage) is None


def test_build_tratte_end_to_end_on_synthetic_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "WEBMAPP_TRACKS_DIR", tmp_path)
    (tmp_path / "7.json").write_text(line_json([[11.0, 44.0, 1], [11.0, 44.0, 2], [11.01, 44.0, 3]]), encoding="utf-8")
    (tmp_path / "9.json").write_text(line_json([[11.0, 44.0], [11.01, 44.0]]), encoding="utf-8")  # circa 800 m
    (tmp_path / "10.json").write_text("{non json", encoding="utf-8")
    tracks = [
        {"track_id": "7", "layers": "20", "ref": "Tappa 1", "name_it": "Via Doppia - Tappa comune 01: A - B", "distance": ""},
        {"track_id": "8", "layers": "10", "ref": "", "name_it": "Cammino Unico - Tappa 01: A - B", "distance": ""},  # file mancante
        {"track_id": "9", "layers": "10", "ref": "Tappa 2", "name_it": "Cammino Unico - Tappa 02: A - B", "distance": ""},
        {"track_id": "10", "layers": "10", "ref": "Tappa 3", "name_it": "Cammino Unico - Tappa 03: A - B", "distance": ""},
    ]
    tappe = [{"cammino_id": "1", "ordine": "2", "titolo": "t", "distanza_km": "20"}]  # 0.8 km contro 20: anomala
    result = cdi.build_tratte(tracks, CAMMINI, tappe)
    assert sorted(r["cammino_id"] for r in result.records if r["tratta_id"] == "cdi:7") == ["cdi:2", "cdi:3"]
    assert all(r["n_vertici"] == 2 for r in result.records if r["tratta_id"] == "cdi:7")
    motivi = {row["motivo"] for row in result.limbo}
    assert {config.MOTIVO_MULTIPLA, config.MOTIVO_TRACCIA_SENZA_GEOMETRIA, config.MOTIVO_GEOMETRIA_ILLEGGIBILE,
            config.MOTIVO_LUNGHEZZA_ANOMALA} <= motivi


def test_build_tratte_reports_tracks_split_at_a_gap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "WEBMAPP_TRACKS_DIR", tmp_path)
    coords = [[9.0, 45.0], [9.001, 45.0], [9.3, 45.0], [9.301, 45.0]]
    (tmp_path / "7.json").write_text(line_json(coords), encoding="utf-8")
    tracks = [{"track_id": "7", "layers": "10", "ref": "", "name_it": "Cammino Unico - Tappa 01: A - B", "distance": ""}]
    result = cdi.build_tratte(tracks, CAMMINI)
    assert result.gap_split == ["7"] and result.records[0]["geometry"].geom_type == "MultiLineString"


def test_tratte_roundtrip_through_geopackage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "TRATTE_GPKG_TEMPLATE", str(tmp_path / "tratte_{fonte}.gpkg"))
    geom = common.build_geometry([[[11.0, 44.0], [11.01, 44.0]]])
    record = {"tratta_id": "cdi:1", "cammino_id": "cdi:2", "fonte": "cdi", "nome": "n", "ordine": None,
              "tipo": "tappa", **common.line_stats(geom), "file_origine": "x", "geometry": geom}
    common.write_tratte(common.tratte_frame([record]), "cdi")
    back = common.read_tratte(["cdi"])
    assert back.crs.to_epsg() == 32632 and back.loc[0, "tratta_id"] == "cdi:1"
    assert back["ordine"].isna().all()
