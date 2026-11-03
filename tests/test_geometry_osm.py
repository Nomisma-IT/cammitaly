"""Test offline delle tratte OSM: un piccolo pbf sintetico scritto con il writer di osmium."""
import csv
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
        writer.add(mutable.Way(id=102, nodes=[6, 7, 99, 8]))  # nodo mancante: si salta
        writer.add(mutable.Way(id=103, nodes=[9, 10]))
        writer.add(_route(1, [("w", 100, ""), ("w", 101, "main"), ("w", 102, ""), ("w", 777, "")],  # way 777 assente
                          route="hiking", name="Sentiero", **{"name:it": "Sentiero Prova", "network": "lwn", "ref": "S1"}))
        writer.add(_route(2, [("w", 103, "")], route="foot", pilgrimage="yes", ref="V2"))
        writer.add(_route(3, [("r", 1, ""), ("r", 2, ""), ("r", 55, "")], route="hiking", name="Grande Cammino"))
        writer.add(_route(4, [("w", 100, "")], route="bicycle", pilgrimage="yes", name="In bici"))
        writer.add(_route(5, [("w", 100, ""), ("w", 103, "alternative")], route="hiking", name="Con Alternativa"))
        writer.add(_route(6, [("w", 100, "")], route="bicycle", name="Solo bici"))  # fuori: ne' a piedi ne' pellegrinaggio
        writer.add(_route(7, [("w", 888, "")], route="hiking", name="Senza way"))
    return path


@pytest.fixture
def outputs(tiny_pbf: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "TRATTE_GPKG_TEMPLATE", str(tmp_path / "tratte_{fonte}.gpkg"))
    monkeypatch.setattr(config, "CAMMINI_CSV_TEMPLATE", str(tmp_path / "cammini_{fonte}.csv"))
    monkeypatch.setattr(config, "LIMBO_CSV_TEMPLATE", str(tmp_path / "limbo_{fonte}.csv"))
    monkeypatch.setattr(config, "OSM_PBF_FILE", tiny_pbf)
    monkeypatch.setattr(config, "OSM_CHUNK_ROWS", 2)  # 5 tratte = tre blocchi: prova la scrittura in aggiunta
    assert main(["build", "osm"]) == 0
    return tmp_path


def _read_csv(path: Path) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return {row["cammino_id"] if "cammino_id" in row else row["oggetto"]: row for row in csv.DictReader(handle)}


def _way_length(nodes: list[int]) -> float:
    geom = common.build_geometry([[(NODE_LON + 0.01 * n, NODE_LAT + 0.005 * n) for n in nodes]])
    return common.line_stats(geom)["lunghezza_m"]


def test_selection_keeps_walking_and_pilgrimage_routes_only(outputs: Path):
    cammini = _read_csv(outputs / "cammini_osm.csv")
    assert set(cammini) == {f"osm:r{n}" for n in (1, 2, 3, 4, 5, 7)}  # la 6 (bici senza pellegrinaggio) non c'e'


def test_cammini_columns_follow_the_contract_plus_osm_columns(outputs: Path):
    with (outputs / "cammini_osm.csv").open(encoding="utf-8", newline="") as handle:
        assert csv.DictReader(handle).fieldnames == config.CAMMINI_OSM_FIELDS
    row = _read_csv(outputs / "cammini_osm.csv")["osm:r1"]
    assert (row["fonte"], row["nome"], row["ref"], row["network"]) == ("osm", "Sentiero Prova", "S1", "lwn")
    assert row["lunghezza_dichiarata_km"] == "" and row["n_tappe_dichiarate"] == ""


def test_name_falls_back_from_name_it_and_name_to_ref(outputs: Path):
    cammini = _read_csv(outputs / "cammini_osm.csv")
    assert cammini["osm:r2"]["nome"] == "V2"
    assert cammini["osm:r3"]["nome"] == "Grande Cammino"


def test_missing_way_makes_the_route_partial_and_missing_node_does_not(outputs: Path):
    cammini = _read_csv(outputs / "cammini_osm.csv")
    assert (cammini["osm:r1"]["stato_geometria"], cammini["osm:r1"]["n_missing_ways"]) == (config.STATO_PARZIALE, "1")
    assert (cammini["osm:r2"]["stato_geometria"], cammini["osm:r2"]["n_missing_ways"]) == (config.STATO_OK, "0")
    tratte = gpd.read_file(outputs / "tratte_osm.gpkg", layer=config.TRATTE_LAYER).set_index("tratta_id")
    # way 102 [6, 7, 99, 8]: il nodo 99 manca e la linea continua da 7 a 8
    assert tratte.loc["osm:r1", "n_vertici"] == 8  # nodi 1..8


def test_super_relation_is_the_union_of_its_children_and_children_stay(outputs: Path):
    cammini = _read_csv(outputs / "cammini_osm.csv")
    sup = cammini["osm:r3"]
    assert (sup["is_super"], sup["n_children"], sup["n_children_not_selected"]) == ("True", "2", "1")
    assert (cammini["osm:r1"]["is_super"], cammini["osm:r2"]["is_super"]) == ("False", "False")
    assert sup["n_missing_ways"] == "1" and sup["stato_geometria"] == config.STATO_PARZIALE
    tratte = gpd.read_file(outputs / "tratte_osm.gpkg", layer=config.TRATTE_LAYER).set_index("tratta_id")
    expected = tratte.loc["osm:r1", "lunghezza_m"] + tratte.loc["osm:r2", "lunghezza_m"]
    assert tratte.loc["osm:r3", "lunghezza_m"] == pytest.approx(expected, abs=0.02)


def test_alternative_role_is_skipped(outputs: Path):
    tratte = gpd.read_file(outputs / "tratte_osm.gpkg", layer=config.TRATTE_LAYER).set_index("tratta_id")
    assert tratte.loc["osm:r5", "lunghezza_m"] == pytest.approx(_way_length([1, 2, 3, 4]), abs=0.02)


def test_bicycle_pilgrimage_route_is_tipo_bici_and_not_walked(outputs: Path):
    tratte = gpd.read_file(outputs / "tratte_osm.gpkg", layer=config.TRATTE_LAYER).set_index("tratta_id")
    assert tratte.loc["osm:r4", "tipo"] == config.TIPO_BICI
    assert tratte.loc["osm:r1", "tipo"] == config.TIPO_ALTRO
    assert float(_read_csv(outputs / "cammini_osm.csv")["osm:r4"]["lunghezza_m"]) == 0.0


def test_tratte_follow_the_contract_and_chunked_write_keeps_every_row(outputs: Path):
    tratte = gpd.read_file(outputs / "tratte_osm.gpkg", layer=config.TRATTE_LAYER)
    assert sorted(tratte["tratta_id"]) == [f"osm:r{n}" for n in (1, 2, 3, 4, 5)]
    assert (tratte["tratta_id"] == tratte["cammino_id"]).all() and (tratte["fonte"] == "osm").all()
    assert tratte["ordine"].isna().all()
    assert str(tratte.crs) == config.CRS_WORK
    assert set(tratte.geom_type) == {"MultiLineString"}
    assert list(tratte.columns) == [*config.TRATTE_ATTRIBUTES, "geometry"]
    assert common.read_tratte(["osm"]).shape[0] == 5


def test_relation_without_geometry_goes_to_limbo_as_assente(outputs: Path):
    cammino = _read_csv(outputs / "cammini_osm.csv")["osm:r7"]
    assert (cammino["stato_geometria"], cammino["n_tratte"], cammino["n_missing_ways"]) == (config.STATO_ASSENTE, "0", "1")
    limbo = _read_csv(outputs / "limbo_osm.csv")
    assert list(limbo) == ["osm:r7"] and limbo["osm:r7"]["motivo"] == config.MOTIVO_CAMMINO_SENZA_GEOMETRIA


def test_is_wanted_checks_type_and_route_or_pilgrimage():
    assert osm.is_wanted({"type": "route", "route": "hiking"})
    assert osm.is_wanted({"type": "superroute", "route": "foot"})
    assert osm.is_wanted({"type": "route", "route": "bicycle", "pilgrimage": "yes"})
    assert not osm.is_wanted({"type": "route", "route": "bicycle"})
    assert not osm.is_wanted({"type": "multipolygon", "route": "hiking"})


def test_resolve_ways_survives_cycles():
    relations = {
        1: osm.Relation({}, array("q", [10]), array("q", [2])),
        2: osm.Relation({}, array("q", [20]), array("q", [1, 1])),
    }
    ways, selected, not_selected = osm.resolve_ways(1, relations, {})
    assert ways == {10, 20} and (selected, not_selected) == (1, 0)
