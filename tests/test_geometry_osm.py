"""Test offline delle tratte OSM: un piccolo pbf sintetico scritto con il writer di osmium."""
import csv
import threading
from array import array
from pathlib import Path

import geopandas as gpd
import osmium
import pytest
from osmium.osm import mutable

from cammitaly import config
from cammitaly.__main__ import main
from cammitaly.geometry import common, osm

NODE_LON, NODE_LAT = 9.0, 45.0


def _node(node_id: int) -> mutable.Node:
    return mutable.Node(id=node_id, location=(NODE_LON + 0.01 * node_id, NODE_LAT + 0.005 * node_id))


def _route(relation_id: int, members: list[tuple[str, int, str]], **tags: str) -> mutable.Relation:
    return mutable.Relation(id=relation_id, members=members, tags={"type": "route", **tags})


@pytest.fixture(scope="module")
def tiny_pbf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("osm") / "tiny.osm.pbf"
    with osmium.SimpleWriter(str(path)) as writer:
        for node_id in range(1, 11):  # il nodo 99 manca di proposito
            writer.add(_node(node_id))
        writer.add(mutable.Way(id=100, nodes=[1, 2, 3, 4]))
        writer.add(mutable.Way(id=101, nodes=[4, 5, 6]))
        writer.add(mutable.Way(id=102, nodes=[6, 7, 99, 8]))  # nodo mancante: si salta e si conta
        writer.add(mutable.Way(id=103, nodes=[9, 10]))
        writer.add(_route(1, [("w", 100, ""), ("w", 101, "main"), ("w", 102, ""), ("w", 777, "")],  # way 777 assente
                          route="hiking", name="Sentiero", distance="6.5",
                          **{"name:it": "Sentiero Prova", "network": "lwn", "ref": "S1"}))
        writer.add(_route(2, [("w", 103, "")], route="foot", pilgrimage="yes", ref="V2", distance="20"))
        writer.add(_route(3, [("r", 1, ""), ("r", 2, ""), ("r", 55, "")], route="hiking", name="Grande Cammino"))
        writer.add(_route(4, [("w", 100, "")], route="bicycle", pilgrimage="yes", name="In bici"))
        writer.add(_route(5, [("w", 100, ""), ("w", 103, "alternative")], route="hiking", name="Con Alternativa"))
        writer.add(_route(6, [("w", 100, "")], route="bicycle", name="Solo bici"))  # fuori: ne' a piedi ne' pellegrinaggio
        writer.add(_route(7, [("w", 888, "")], route="hiking", name="Senza way"))
        writer.add(_route(8, [("r", 2, ""), ("r", 5, "alternative:2")], route="hiking", name="Sotto-relazione alternativa"))
        writer.add(_route(9, [("w", 101, ""), ("r", 2, "approach"), ("w", 103, "excursion")], route="hiking", name="Raccordi"))
        writer.add(_route(10, [("r", 1, ""), ("r", 5, "")], route="hiking", name="Way condiviso"))
        writer.add(_route(11, [("w", 103, "alternate")], route="hiking", name="Solo variante"))
    return path


@pytest.fixture
def outputs(tiny_pbf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "TRATTE_GPKG_TEMPLATE", str(tmp_path / "tratte_{fonte}.gpkg"))
    monkeypatch.setattr(config, "CAMMINI_CSV_TEMPLATE", str(tmp_path / "cammini_{fonte}.csv"))
    monkeypatch.setattr(config, "LIMBO_CSV_TEMPLATE", str(tmp_path / "limbo_{fonte}.csv"))
    monkeypatch.setattr(config, "OSM_PBF_FILE", tiny_pbf)
    monkeypatch.setattr(config, "OSM_CHUNK_ROWS", 2)  # 12 tratte = sei blocchi: prova la scrittura in aggiunta
    assert main(["build", "osm"]) == 0
    return tmp_path


def _csv_by(path: Path, key: str) -> dict[str, dict[str, str]]:
    return {row[key]: row for row in common.read_csv(path)}


def _cammini(out: Path) -> dict[str, dict[str, str]]:
    return _csv_by(out / "cammini_osm.csv", "cammino_id")


def _tratte(out: Path) -> gpd.GeoDataFrame:
    return gpd.read_file(out / "tratte_osm.gpkg", layer=config.TRATTE_LAYER).set_index("tratta_id")


def _way_length(nodes: list[int]) -> float:
    geom = common.build_geometry([[(NODE_LON + 0.01 * n, NODE_LAT + 0.005 * n) for n in nodes]])
    return common.line_stats(geom)["lunghezza_m"]


def _ids(*numbers: int) -> set[str]:
    return {f"osm:r{n}" for n in numbers}


def test_selection_keeps_walking_and_pilgrimage_routes_only(outputs: Path):
    assert set(_cammini(outputs)) == _ids(1, 2, 3, 4, 5, 7, 8, 9, 10, 11)  # la 6 (bici senza pellegrinaggio) non c'e'


def test_cammini_columns_follow_the_contract_plus_osm_columns(outputs: Path):
    with (outputs / "cammini_osm.csv").open(encoding="utf-8", newline="") as handle:
        assert csv.DictReader(handle).fieldnames == config.CAMMINI_OSM_FIELDS
    row = _cammini(outputs)["osm:r1"]
    assert (row["fonte"], row["nome"], row["ref"], row["network"]) == ("osm", "Sentiero Prova", "S1", "lwn")
    assert row["lunghezza_dichiarata_km"] == "6.5" and row["n_tappe_dichiarate"] == ""
    assert _cammini(outputs)["osm:r3"]["lunghezza_dichiarata_km"] == ""


def test_name_falls_back_from_name_it_and_name_to_ref(outputs: Path):
    cammini = _cammini(outputs)
    assert cammini["osm:r2"]["nome"] == "V2"
    assert cammini["osm:r3"]["nome"] == "Grande Cammino"


def test_missing_way_makes_the_route_partial_and_missing_nodes_are_counted(outputs: Path):
    cammini = _cammini(outputs)
    assert (cammini["osm:r1"]["stato_geometria"], cammini["osm:r1"]["n_missing_ways"]) == (config.STATO_PARZIALE, "1")
    assert cammini["osm:r1"]["n_missing_nodes"] == "1"  # il nodo 99 del way 102
    assert (cammini["osm:r2"]["stato_geometria"], cammini["osm:r2"]["n_missing_nodes"]) == (config.STATO_OK, "0")
    assert _tratte(outputs).loc["osm:r1", "n_vertici"] == 8  # nodi 1..8: la linea continua da 7 a 8


def test_super_relation_is_the_union_of_its_children_and_children_stay(outputs: Path):
    cammini, tratte = _cammini(outputs), _tratte(outputs)
    sup = cammini["osm:r3"]
    assert (sup["is_super"], sup["n_children"], sup["n_children_not_selected"]) == ("True", "2", "1")
    assert (cammini["osm:r1"]["is_super"], cammini["osm:r2"]["is_super"]) == ("False", "False")
    assert sup["n_missing_ways"] == "1" and sup["stato_geometria"] == config.STATO_PARZIALE
    expected = tratte.loc["osm:r1", "lunghezza_m"] + tratte.loc["osm:r2", "lunghezza_m"]
    assert tratte.loc["osm:r3", "lunghezza_m"] == pytest.approx(expected, abs=0.02)


def test_alternative_way_goes_to_a_separate_variante_tratta(outputs: Path):
    tratte, cammino = _tratte(outputs), _cammini(outputs)["osm:r5"]
    assert tratte.loc["osm:r5", "lunghezza_m"] == pytest.approx(_way_length([1, 2, 3, 4]), abs=0.02)
    variante = tratte.loc["osm:r5:variante"]
    assert variante["lunghezza_m"] == pytest.approx(_way_length([9, 10]), abs=0.02)
    assert (variante["tipo"], variante["cammino_id"]) == (config.TIPO_VARIANTE, "osm:r5")
    assert tratte.loc["osm:r5", "tipo"] == config.TIPO_ALTRO
    assert (cammino["n_tratte"], cammino["n_membri_variante"]) == ("2", "1")
    assert float(cammino["lunghezza_m"]) == pytest.approx(_way_length([1, 2, 3, 4]) + _way_length([9, 10]), abs=0.05)


def test_variante_tratta_is_written_only_with_geometry(outputs: Path):
    tratte = _tratte(outputs)
    assert "osm:r1:variante" not in tratte.index and "osm:r2:variante" not in tratte.index
    assert sorted(tratte.index) == sorted([
        "osm:r1", "osm:r2", "osm:r3", "osm:r4", "osm:r5", "osm:r5:variante", "osm:r8", "osm:r8:variante",
        "osm:r9", "osm:r10", "osm:r10:variante", "osm:r11:variante",
    ])


def test_subrelation_with_alternative_role_is_all_variante(outputs: Path):
    tratte, cammino = _tratte(outputs), _cammini(outputs)["osm:r8"]
    # r2 (way 103) e' principale; r5 raggiunta con ruolo alternative:2 e' tutta variante, tolto il way gia' principale
    assert tratte.loc["osm:r8", "lunghezza_m"] == pytest.approx(_way_length([9, 10]), abs=0.02)
    assert tratte.loc["osm:r8:variante", "lunghezza_m"] == pytest.approx(_way_length([1, 2, 3, 4]), abs=0.02)
    assert (cammino["n_children"], cammino["n_membri_variante"]) == ("2", "1")


def test_approach_and_excursion_members_are_excluded(outputs: Path):
    tratte, cammino = _tratte(outputs), _cammini(outputs)["osm:r9"]
    assert tratte.loc["osm:r9", "lunghezza_m"] == pytest.approx(_way_length([4, 5, 6]), abs=0.02)
    assert (cammino["n_membri_esclusi"], cammino["is_super"], cammino["n_tratte"]) == ("2", "False", "1")


def test_way_shared_by_two_children_is_counted_once(outputs: Path):
    tratte = _tratte(outputs)
    # il way 100 sta sia in r1 sia in r5: il padre r10 ha la stessa linea principale di r1
    assert tratte.loc["osm:r10", "lunghezza_m"] == pytest.approx(tratte.loc["osm:r1", "lunghezza_m"], abs=0.02)
    assert tratte.loc["osm:r10:variante", "lunghezza_m"] == pytest.approx(_way_length([9, 10]), abs=0.02)


def test_relation_with_only_variant_members_is_partial_without_limbo(outputs: Path):
    cammino = _cammini(outputs)["osm:r11"]
    assert (cammino["n_tratte"], cammino["stato_geometria"]) == ("1", config.STATO_PARZIALE)
    assert "osm:r11" not in _csv_by(outputs / "limbo_osm.csv", "oggetto")


def test_bicycle_pilgrimage_route_is_tipo_bici_and_counts_its_length(outputs: Path):
    tratte, cammino = _tratte(outputs), _cammini(outputs)["osm:r4"]
    assert tratte.loc["osm:r4", "tipo"] == config.TIPO_BICI and tratte.loc["osm:r1", "tipo"] == config.TIPO_ALTRO
    assert float(cammino["lunghezza_m"]) == pytest.approx(_way_length([1, 2, 3, 4]), abs=0.02)
    assert cammino["stato_geometria"] == config.STATO_OK


def test_tratte_follow_the_contract_and_chunked_write_keeps_every_row(outputs: Path):
    tratte = gpd.read_file(outputs / "tratte_osm.gpkg", layer=config.TRATTE_LAYER)
    assert (tratte["cammino_id"] == tratte["tratta_id"].str.split(":").str[:2].str.join(":")).all()
    assert (tratte["fonte"] == "osm").all() and tratte["ordine"].isna().all()
    assert str(tratte.crs) == config.CRS_WORK
    assert set(tratte.geom_type) == {"MultiLineString"}
    assert list(tratte.columns) == [*config.TRATTE_ATTRIBUTES, "geometry"]
    assert common.read_tratte(["osm"]).shape[0] == 12


def test_limbo_has_the_relation_without_geometry_and_the_length_anomaly(outputs: Path):
    cammino = _cammini(outputs)["osm:r7"]
    assert (cammino["stato_geometria"], cammino["n_tratte"], cammino["n_missing_ways"]) == (config.STATO_ASSENTE, "0", "1")
    limbo = _csv_by(outputs / "limbo_osm.csv", "oggetto")
    assert set(limbo) == _ids(2, 7)
    assert limbo["osm:r7"]["motivo"] == config.MOTIVO_CAMMINO_SENZA_GEOMETRIA
    assert limbo["osm:r2"]["motivo"] == config.MOTIVO_LUNGHEZZA_ANOMALA  # 0,96 km contro 20 dichiarati
    assert "20.0 dichiarati" in limbo["osm:r2"]["dettaglio"]


def test_run_leaves_no_watchdog_thread_behind(outputs: Path):
    assert [t for t in threading.enumerate() if isinstance(t, osm.RamWatchdog)] == []


# --- funzioni isolate ---

def test_is_wanted_checks_type_and_route_or_pilgrimage():
    assert osm.is_wanted({"type": "route", "route": "hiking"})
    assert osm.is_wanted({"type": "superroute", "route": "foot"})
    assert osm.is_wanted({"type": "route", "route": "bicycle", "pilgrimage": "yes"})
    assert not osm.is_wanted({"type": "route", "route": "bicycle"})
    assert not osm.is_wanted({"type": "multipolygon", "route": "hiking"})


@pytest.mark.parametrize("role, bucket", [
    ("alternative", "variant"), ("alternate", "variant"), ("alternative:2", "variant"), ("Alternative", "variant"),
    ("approach", "excluded"), ("excursion", "excluded"),
    ("", "main"), ("main", "main"), ("forward", "main"), ("backward", "main"), ("qualcosa", "main"),
])
def test_member_roles_are_sorted_into_main_variant_or_excluded(role: str, bucket: str):
    relation = osm.Relation({})
    osm.add_member(relation, "w", role, 5)
    osm.add_member(relation, "r", role, 6)
    osm.add_member(relation, "n", role, 7)  # i nodi non contano
    expected = {"variant": (0, 2, 0), "main": (2, 0, 0), "excluded": (0, 0, 2)}[bucket]
    assert (len(relation.main_ways) + len(relation.main_subs), relation.n_variant, relation.n_excluded) == expected


@pytest.mark.parametrize("raw, km", [
    ("12.5", 12.5), ("12,5 km", 12.5), ("800 m", 0.8), ("10 MI", 16.09344), (" 7km ", 7.0),
    ("abc", None), ("10;20", None), ("", None), ("0", None), ("1.234,5", None), ("12 miglia", None),
])
def test_declared_km_parses_units_and_rejects_the_rest(raw: str, km: float | None):
    result = osm.declared_km({"distance": raw})
    assert result == (pytest.approx(km) if km is not None else None)


def test_declared_km_is_none_without_the_tag():
    assert osm.declared_km({}) is None


def _relation(ways: list[int], subs: list[int], variant_subs: list[int] = ()) -> osm.Relation:
    return osm.Relation({}, array("q", ways), array("q"), array("q", subs), array("q", variant_subs))


def test_resolve_ways_survives_cycles_and_does_not_memoise_the_cut_result():
    relations = {1: _relation([10], [2]), 2: _relation([20], [1, 1])}
    memo: dict = {}
    top = osm.resolve_ways(1, relations, memo)
    assert top.main == {10, 20} and (top.selected, top.not_selected) == (1, 0)
    assert not top.complete and memo == {}


def test_resolve_ways_depth_limit_does_not_poison_the_memo(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "OSM_MAX_DEPTH", 2)
    relations = {1: _relation([10], [2]), 2: _relation([20], [3]), 3: _relation([30], [4]), 4: _relation([40], [])}
    memo: dict = {}
    assert osm.resolve_ways(1, relations, memo).main == {10, 20, 30}  # la relazione 4 e' oltre il limite
    assert osm.resolve_ways(3, relations, memo).main == {30, 40}  # ma dalla 3 si arriva alla 4
    assert osm.resolve_ways(4, relations, memo).complete and 4 in memo


def test_resolve_ways_variant_role_is_inherited_and_main_wins():
    relations = {
        1: _relation([10], [2], variant_subs=[3]),
        2: _relation([20], []),
        3: _relation([30, 20], [], []),
    }
    result = osm.resolve_ways(1, relations, {})
    assert result.main == {10, 20} and result.variant == {30}  # il way 20 e' principale via la 2


def test_watchdog_stops_on_request():
    watchdog = osm.RamWatchdog(limit_mb=10**6)
    watchdog.start()
    watchdog.stop()
    assert not watchdog.is_alive()


def test_watchdog_exits_the_process_over_the_limit(monkeypatch: pytest.MonkeyPatch):
    codes: list[int] = []
    monkeypatch.setattr(osm.os, "_exit", codes.append)
    watchdog = osm.RamWatchdog(limit_mb=1)
    watchdog.start()
    watchdog.join(timeout=10)
    assert codes == [config.OSM_RSS_ABORT_EXIT_CODE] and not watchdog.is_alive()
