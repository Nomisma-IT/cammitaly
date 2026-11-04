"""Incrocio linee-comuni su poligoni e linee minuscoli in metri: nessun file reale."""
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, MultiLineString, Polygon, box

from cammitaly import config, crossing


@pytest.fixture
def comuni() -> gpd.GeoDataFrame:
    """A e B confinanti, C a 300 m da B, D lontano."""
    return gpd.GeoDataFrame(
        {
            config.COMUNE_ID_COL: ["A", "B", "C", "D"],
            config.COMUNE_NAME_COL: ["Alfa", "Beta", "Gamma", "Delta"],
            config.COMUNE_REGION_COL: [1, 1, 2, 2],
        },
        geometry=[box(0, 0, 1000, 1000), box(1000, 0, 2000, 1000), box(2300, 0, 3300, 1000), box(5000, 0, 6000, 1000)],
        crs=config.CRS_WORK,
    )


def lines_of(**geoms: LineString) -> gpd.GeoSeries:
    return gpd.GeoSeries(list(geoms.values()), index=pd.Index(list(geoms), name="cammino_id"), crs=config.CRS_WORK)


def test_distanze_e_lunghezza_dentro(comuni: gpd.GeoDataFrame) -> None:
    # La linea attraversa A per 1000 m e B per 500 m; C dista 800 m dal suo ultimo vertice
    pairs = crossing.comuni_entro(lines_of(x=LineString([(-500, 500), (1500, 500)])), comuni, 800)
    by_comune = pairs.set_index(config.COMUNE_ID_COL)
    assert list(pairs.columns) == list(config.CROSSING_FIELDS)
    assert by_comune.loc["A", "distanza_m"] == 0 and by_comune.loc["A", "lunghezza_dentro_m"] == pytest.approx(1000)
    assert by_comune.loc["B", "distanza_m"] == 0 and by_comune.loc["B", "lunghezza_dentro_m"] == pytest.approx(500)
    assert by_comune.loc["C", "distanza_m"] == pytest.approx(800) and by_comune.loc["C", "lunghezza_dentro_m"] == 0
    assert by_comune["dentro"].to_dict() == {"A": True, "B": True, "C": False}
    assert "D" not in by_comune.index


def test_d_zero_tiene_solo_i_comuni_dove_la_linea_entra(comuni: gpd.GeoDataFrame) -> None:
    pairs = crossing.comuni_entro(lines_of(x=LineString([(-500, 500), (1500, 500)])), comuni, 0)
    assert set(pairs[config.COMUNE_ID_COL]) == {"A", "B"}


def test_candidati_dwithin_al_confine_della_banda(comuni: gpd.GeoDataFrame) -> None:
    # La linea corre a 500 m sopra C: con d = 500 il comune e' candidato (<= e' inclusivo), con 499 no
    line = lines_of(x=LineString([(2400, 1500), (2600, 1500)]))
    assert crossing.comuni_entro(line, comuni, 499).empty
    at_boundary = crossing.comuni_entro(line, comuni, 500)
    assert list(at_boundary[config.COMUNE_ID_COL]) == ["C"]
    assert at_boundary["distanza_m"].iloc[0] == pytest.approx(500)


def test_nessun_comune_vicino_da_dataframe_vuoto_con_le_colonne(comuni: gpd.GeoDataFrame) -> None:
    pairs = crossing.comuni_entro(lines_of(x=LineString([(10_000, 0), (11_000, 0)])), comuni, 800)
    assert pairs.empty and list(pairs.columns) == list(config.CROSSING_FIELDS)


def test_linea_che_tocca_soltanto_il_bordo_ha_distanza_zero_e_nessuna_lunghezza(comuni: gpd.GeoDataFrame) -> None:
    pairs = crossing.comuni_entro(lines_of(x=LineString([(-100, 0), (-100, 1000)])), comuni, 0)
    assert pairs.empty
    touching = crossing.comuni_entro(lines_of(x=LineString([(-100, 1000), (0, 1000)])), comuni, 0)
    assert list(touching[config.COMUNE_ID_COL]) == ["A"] and touching["lunghezza_dentro_m"].iloc[0] == 0


def test_distanza_esatta_con_ritaglio_che_deve_allargarsi() -> None:
    # Il vertice piu' vicino sta a 4000 m: il primo ritaglio (1000 m) non lo contiene, quelli successivi si
    line = LineString([(-1_000_000, 5000), (1_000_000, 5000)])
    assert crossing.exact_distance(line, box(0, 0, 1000, 1000)) == pytest.approx(4000)


def test_distanza_esatta_coincide_con_quella_diretta_su_una_linea_spezzata() -> None:
    polygon = Polygon([(0, 0), (800, 100), (900, 900), (100, 700)])
    line = MultiLineString([[(2000, 2000), (3000, 2500)], [(1500, 0), (1200, 400), (1100, 600)]])
    assert crossing.exact_distance(line, polygon) == pytest.approx(line.distance(polygon))


def test_lunghezza_dentro_con_linea_che_esce_e_rientra() -> None:
    polygon = box(0, 0, 1000, 1000)
    line = LineString([(-100, 500), (300, 500), (300, 1500), (700, 1500), (700, 500), (1100, 500)])
    # Dentro: orizzontale 0-300, verticale 500-1000 in salita e in discesa, orizzontale 700-1000
    assert crossing.length_inside(line, polygon) == pytest.approx(300 + 500 + 500 + 300)


def test_poligono_non_valido_viene_riparato() -> None:
    bowtie = Polygon([(0, 0), (10, 10), (10, 0), (0, 10)])
    assert not bowtie.is_valid
    repaired = crossing.repair_polygons(np.array([bowtie, box(0, 0, 1, 1)], dtype=object))
    assert all(geom.is_valid and geom.geom_type in ("Polygon", "MultiPolygon") for geom in repaired)
    assert repaired[0].area == pytest.approx(50)


def test_dissolve_tiene_di_default_anche_la_bici_e_filtra_sui_tipi_richiesti() -> None:
    tratte = gpd.GeoDataFrame(
        {"cammino_id": ["c1", "c1", "c1", "c2"], "tipo": ["tappa", "variante", "bici", "tappa"]},
        geometry=[LineString([(0, 0), (100, 0)]), LineString([(100, 0), (300, 0)]), LineString([(0, 50), (5000, 50)]),
                  LineString([(0, 0), (10, 0)])],
        crs=config.CRS_WORK,
    )
    dissolved = crossing.dissolve_cammini(tratte)
    assert dissolved.index.name == "cammino_id" and set(dissolved.index) == {"c1", "c2"}
    # Il tipo predefinito (CROSSING_TIPI) comprende la bici dal giorno in cui il committente l'ha voluta nel cammino
    assert dissolved["c1"].length == pytest.approx(300 + 5000)
    without_bike = crossing.dissolve_cammini(tratte, tipi=(config.TIPO_TAPPA, config.TIPO_VARIANTE))
    assert without_bike["c1"].length == pytest.approx(300)
    assert crossing.dissolve_cammini(tratte, tipi=("bici",)).index.tolist() == ["c1"]


def test_filtro_per_distanza_coincide_con_il_calcolo_diretto(comuni: gpd.GeoDataFrame) -> None:
    line = lines_of(x=LineString([(-500, 500), (1500, 500)]))
    wide = crossing.comuni_entro(line, comuni, 800)
    for d in (0, 799, 800):
        direct = crossing.comuni_entro(line, comuni, d)
        assert crossing.within_distance(wide, d).reset_index(drop=True).equals(direct)


def test_dentro_non_dipende_dall_arrotondamento_della_distanza(comuni: gpd.GeoDataFrame) -> None:
    # A 4 mm dal comune la distanza arrotondata a due decimali vale 0, ma la linea non entra
    line = lines_of(x=LineString([(-0.004, 100), (-0.004, 200)]))
    assert crossing.comuni_entro(line, comuni, 0).empty
    near = crossing.comuni_entro(line, comuni, 1)
    assert near["distanza_m"].iloc[0] == 0 and not near["dentro"].iloc[0]
    assert crossing.within_distance(near, 0).empty and len(crossing.within_distance(near, 1)) == 1


def test_slug_regione_come_su_camminiditalia() -> None:
    assert crossing.region_slug("Valle d'Aosta/Vallée d'Aoste") == "valle-daosta"
    assert crossing.region_slug("Trentino-Alto Adige/Südtirol") == "trentino-alto-adige"
    assert crossing.region_slug("Friuli-Venezia Giulia") == "friuli-venezia-giulia"


def test_load_comuni_legge_utf8_ripara_e_controlla_il_crs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    frame = gpd.GeoDataFrame(
        {config.COMUNE_ID_COL: ["001001"], config.COMUNE_NAME_COL: ["Agliè"], config.COMUNE_REGION_COL: [1]},
        geometry=[Polygon([(0, 0), (10, 10), (10, 0), (0, 10)])],
        crs=config.CRS_WORK,
    )
    shapefile = tmp_path / "comuni.shp"
    frame.to_file(shapefile, driver="ESRI Shapefile", encoding="utf-8")
    monkeypatch.setattr(config, "ISTAT_COMUNI_SHP", shapefile)
    loaded = crossing.load_comuni()
    assert loaded[config.COMUNE_NAME_COL].iloc[0] == "Agliè" and loaded.geometry.is_valid.all()

    wrong = tmp_path / "wgs84.shp"
    frame.set_crs(config.CRS_WORK, allow_override=True).to_crs("EPSG:4326").to_file(wrong, driver="ESRI Shapefile", encoding="utf-8")
    monkeypatch.setattr(config, "ISTAT_COMUNI_SHP", wrong)
    with pytest.raises(ValueError, match="non sono in"):
        crossing.load_comuni()
