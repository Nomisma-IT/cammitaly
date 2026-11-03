"""Calibrazione: normalizzazione dei nomi, omonimi, degradazione delle tracce e conteggi, su dati minuscoli."""
import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, MultiLineString, box

from cammitaly import __main__ as cli
from cammitaly import calibration as cal
from cammitaly import config

COMUNE = config.COMUNE_ID_COL


@pytest.fixture
def comuni_istat() -> pd.DataFrame:
    return pd.DataFrame({
        COMUNE: ["010001", "010002", "020001", "030001", "030002", "040001", "050001", "060001"],
        config.COMUNE_NAME_COL: [
            "Bolzano/Bozen", "Sant'Angelo Lodigiano", "Reggio nell'Emilia", "Castro", "Castro", "San Giovanni in Fiore",
            "Santangelo", "Forlì",
        ],
        config.COMUNE_REGION_COL: [4, 3, 8, 3, 16, 18, 16, 8],
    })


def match(text: object, comuni: pd.DataFrame, regions: set[int] | None = None) -> tuple[str, str | None]:
    return cal.match_place(text, cal.build_place_index(comuni), frozenset(regions) if regions else None)


# --- nomi dei luoghi ---

def test_chiavi_unificano_apostrofi_trattini_accenti_e_articoli() -> None:
    assert cal.place_keys("Sant'Angelo")[0] == cal.place_keys("Santangelo")[0]
    assert cal.place_keys("Reggio nell'Emilia")[0] == cal.place_keys("Reggio Emilia")[0]
    assert cal.place_keys("Castelbello-Ciardes")[0] == cal.place_keys("Castelbello Ciardes")[0]
    assert cal.place_keys("Forlì")[0] == cal.place_keys("FORLI")[0]


def test_chiave_dei_santi_unisce_s_san_sant_santa() -> None:
    assert cal.place_keys("S. Giovanni")[1] == cal.place_keys("San Giovanni")[1]
    assert cal.place_keys("Sant'Angelo")[1] == cal.place_keys("S. Angelo")[1]
    assert cal.place_keys("Santa Maria")[1] == cal.place_keys("S. Maria")[1]


def test_corrispondenza_ok_su_varianti_di_scrittura(comuni_istat: pd.DataFrame) -> None:
    assert match("Reggio Emilia", comuni_istat) == (config.MATCH_OK, "020001")
    assert match("Sant’Angelo Lodigiano", comuni_istat) == (config.MATCH_OK, "010002")
    assert match("S. Giovanni in Fiore", comuni_istat) == (config.MATCH_OK, "040001")
    assert match("forli", comuni_istat) == (config.MATCH_OK, "060001")


def test_nomi_bilingui_corrispondono_con_una_qualsiasi_delle_due_parti(comuni_istat: pd.DataFrame) -> None:
    assert match("Bolzano", comuni_istat) == (config.MATCH_OK, "010001")
    assert match("Bozen", comuni_istat) == (config.MATCH_OK, "010001")
    assert match("Bolzano/Bozen", comuni_istat) == (config.MATCH_OK, "010001")


def test_parentesi_e_virgole_dividono_il_testo_in_candidati(comuni_istat: pd.DataFrame) -> None:
    assert match("Bolzano (stazione)", comuni_istat) == (config.MATCH_OK, "010001")
    assert match("Forlì, piazza Saffi", comuni_istat) == (config.MATCH_OK, "060001")
    assert match(f"Bolzano {chr(0x2013)} stazione", comuni_istat) == (config.MATCH_OK, "010001")  # trattino lungo


def test_omonimo_ambiguo_e_scartato_ma_la_regione_lo_risolve(comuni_istat: pd.DataFrame) -> None:
    assert match("Castro", comuni_istat) == (config.MATCH_HOMONYM, None)
    assert match("Castro", comuni_istat, regions={3, 16}) == (config.MATCH_HOMONYM, None)
    assert match("Castro", comuni_istat, regions={16}) == (config.MATCH_OK, "030002")
    assert match("Castro", comuni_istat, regions={3}) == (config.MATCH_OK, "030001")


def test_due_comuni_diversi_in_pezzi_diversi_sono_ambigui(comuni_istat: pd.DataFrame) -> None:
    assert match("Forlì / Bolzano", comuni_istat) == (config.MATCH_HOMONYM, None)


def test_luoghi_che_non_sono_comuni_e_nomi_vuoti(comuni_istat: pd.DataFrame) -> None:
    assert match("Abbazia di Montevergine", comuni_istat) == (config.MATCH_NOT_FOUND, None)
    assert match(float("nan"), comuni_istat) == (config.MATCH_EMPTY, None)
    assert match("   ", comuni_istat) == (config.MATCH_EMPTY, None)
    assert match("Bolzano", comuni_istat, regions={8}) == (config.MATCH_NOT_FOUND, None)


def test_regioni_del_cammino_da_slug() -> None:
    cammini = pd.DataFrame({"id": [1, 2, 3], "regioni": ["lazio|toscana", None, "narnia"]})
    result = cal.regions_by_cammino(cammini, {"lazio": 12, "toscana": 9})
    assert result == {"cdi:1": frozenset({12, 9}), "cdi:2": None, "cdi:3": None}


def test_nomi_di_riferimento_da_tappe_e_tracce() -> None:
    tappe = pd.DataFrame({"cammino_id": [7], "ordine": [1], "partenza": ["Roma"], "arrivo": ["Rieti"]})
    tracks = pd.DataFrame({"track_id": [5, 6], "from": ["Roma", "X"], "to": ["Rieti", "Y"]})
    tratte = pd.DataFrame({"tratta_id": ["cdi:5"], "cammino_id": ["cdi:7"], "ordine": [1]})
    refs = cal.reference_names(tappe, tracks, tratte)
    assert sorted(refs["origine"].unique()) == [config.SOURCE_TAPPE, config.SOURCE_WEBMAPP]
    assert len(refs) == 4  # la traccia 6 non ha tratta: non entra
    assert refs.loc[refs["origine"] == config.SOURCE_WEBMAPP, "tratta_id"].eq("cdi:5").all()
    assert refs.loc[refs["origine"] == config.SOURCE_TAPPE, "tratta_id"].isna().all()


def test_quote_di_corrispondenza_per_origine() -> None:
    matched = pd.DataFrame({
        "origine": [config.SOURCE_TAPPE] * 4, "ruolo": ["partenza"] * 4,
        "esito": [config.MATCH_OK, config.MATCH_OK, config.MATCH_NOT_FOUND, config.MATCH_EMPTY],
    })
    rates = cal.match_rates(matched)
    total = rates[(rates["origine"] == "tutte")].iloc[0]
    assert total["n_nomi"] == 4 and total[config.MATCH_OK] == 2 and total["quota_ok"] == pytest.approx(2 / 3, abs=1e-4)


# --- B: degradazione ---

def straight_line(n_vertices: int = 101, spacing: float = 10.0) -> LineString:
    return LineString([(i * spacing, 0.0) for i in range(n_vertices)])


def test_un_vertice_ogni_k_porta_la_spaziatura_al_valore_cercato() -> None:
    degraded = cal.subsample_every_k(straight_line(), 30)
    coords = np.asarray(degraded.coords)
    assert coords[0, 0] == 0 and coords[-1, 0] == 1000  # primo e ultimo vertice restano
    assert cal.mean_spacing([degraded]) == pytest.approx(1000 / 34, rel=0.01)  # 0, 30, ..., 990 e 1000
    assert degraded.length == pytest.approx(1000)


def test_sottocampionamento_conserva_le_parti_e_non_allunga() -> None:
    multi = MultiLineString([straight_line(41).coords, [(0, 100 + 10 * i) for i in range(41)]])
    degraded = cal.subsample_every_k(multi, 25)
    assert degraded.geom_type == "MultiLineString" and len(degraded.geoms) == 2
    assert all(len(part.coords) < 41 for part in degraded.geoms)
    assert cal.subsample_every_k(straight_line(), 5).equals(straight_line())  # spaziatura gia' sotto il traguardo


def test_semplificazione_toglie_i_vertici_entro_la_tolleranza() -> None:
    wiggly = LineString([(0, 0), (250, 0.5), (500, -0.5), (750, 0.5), (1000, 0)])
    assert len(cal.simplify_line(wiggly, 10).coords) == 2
    assert len(cal.simplify_line(wiggly, 0.1).coords) > 2


def test_metodi_di_degradazione_dai_parametri_di_config() -> None:
    assert list(cal.degradation_methods()) == ["k25", "k60", "s10", "s25"]


def test_tratte_dense_da_mitur_e_dalle_cdi_solo_se_mitur_ne_ha_troppo_poche(monkeypatch: pytest.MonkeyPatch) -> None:
    tratte = gpd.GeoDataFrame(
        {
            "fonte": ["mitur", "mitur", "cdi", "cdi", "mitur"], "tipo": ["tappa", "tappa", "tappa", "variante", "bici"],
            "spaziatura_media_m": [5.0, 40.0, 3.0, 2.0, 1.0], "cammino_id": ["m1", "m2", "c1", "c2", "m3"],
        },
        geometry=[straight_line()] * 5, crs=config.CRS_WORK,
    )
    monkeypatch.setattr(config, "CALIBRATION_DENSE_MIN_TRATTE", 1)
    assert cal.dense_tappe(tratte)["cammino_id"].tolist() == ["m1"]  # niente variante, niente bici, niente tratta rada
    monkeypatch.setattr(config, "CALIBRATION_DENSE_MIN_TRATTE", 2)
    assert cal.dense_tappe(tratte)["cammino_id"].tolist() == ["m1", "c1"]  # una sola MiTur: si aggiunge la cdi
    with pytest.raises(ValueError):
        cal.dense_tappe(tratte[tratte["spaziatura_media_m"] > 10])


# --- confronto tra insiemi ---

def test_recall_ed_extra_per_ogni_d() -> None:
    reference = pd.DataFrame({"gruppo": ["g", "g"], COMUNE: ["A", "B"]})
    candidates = pd.DataFrame({
        "gruppo": ["g", "g", "g"], COMUNE: ["A", "B", "C"], "distanza_m": [0.0, 30.0, 300.0],
    })
    detail = cal.compare_sets(reference, candidates).set_index("d_m")
    assert detail.loc[0, ["n_trovati", "n_extra", "recall"]].tolist() == [1, 0, 0.5]
    assert detail.loc[25, "n_trovati"] == 1
    assert detail.loc[50, ["n_trovati", "n_extra", "recall"]].tolist() == [2, 0, 1.0]
    assert detail.loc[200, "n_extra"] == 0
    assert detail.loc[400, ["n_trovati", "n_extra"]].tolist() == [2, 1]
    totals = cal.totals_by_distance(cal.compare_sets(reference, candidates))
    assert totals.loc[800, ["recall", "extra_tot"]].tolist() == [1.0, 1]


def test_una_tratta_non_trovata_a_nessuna_distanza_abbassa_il_recall() -> None:
    reference = pd.DataFrame({"gruppo": ["g1", "g1", "g2"], COMUNE: ["A", "B", "C"]})
    candidates = pd.DataFrame({"gruppo": ["g1"], COMUNE: ["A"], "distanza_m": [0.0]})
    totals = cal.totals_by_distance(cal.compare_sets(reference, candidates))
    assert totals.loc[800, "recall"] == pytest.approx(1 / 3, abs=1e-4)
    assert totals.loc[800, "recall_media_gruppo"] == pytest.approx(0.25)


# --- C: coppie provvisorie ---

def test_sovrapposizione_conta_la_parte_della_linea_corta_entro_50_metri() -> None:
    longer = LineString([(0, 0), (2000, 0)])
    assert cal.overlap_share(LineString([(100, 30), (900, 30)]), longer) == pytest.approx(1.0)
    assert cal.overlap_share(LineString([(100, 100), (900, 100)]), longer) == pytest.approx(0.0)
    assert cal.overlap_share(LineString([(1500, 0), (2500, 0)]), longer) == pytest.approx(0.55, abs=0.02)  # 500 m + 50 m di tolleranza su 1000


def test_coppie_provvisorie_chiedono_nome_simile_e_tracciato_sovrapposto() -> None:
    cdi_names = pd.Series({"cdi:1": "Cammino di San Benedetto", "cdi:2": "Via Alfa"})
    mitur_names = pd.Series({"mitur:a": "San Benedetto Cammino di", "mitur:b": "Via Alfa", "mitur:c": "Altro"})
    lines = {
        "cdi:1": LineString([(0, 0), (5000, 0)]), "mitur:a": LineString([(0, 20), (5000, 20)]),
        "cdi:2": LineString([(0, 0), (5000, 0)]), "mitur:b": LineString([(0, 900), (5000, 900)]),  # stesso nome, altrove
    }
    pairs = cal.same_cammino_pairs(lines, cdi_names, mitur_names)
    assert pairs[["cdi_id", "mitur_id"]].values.tolist() == [["cdi:1", "mitur:a"]]


# --- percorsi di sintesi ---

def test_distribuzione_delle_distanze() -> None:
    row = cal.distance_distribution(pd.Series([0.0, 0.0, 0.0, 100.0, np.nan]), "cammino")
    assert row["n_coppie"] == 4 and row["quota_a_0"] == 0.75 and row["max"] == 100.0 and row["p50"] == 0.0
    assert row["p99"] == pytest.approx(97.0)


def test_coppie_lontane_sono_uniche_e_ordinate_dalla_piu_distante() -> None:
    ok = pd.DataFrame({
        "cammino_id": ["c1", "c1", "c2", "c3"], COMUNE: ["A", "A", "B", "C"], "tratta_id": [None, None, "t", None],
        "cammino": ["Uno", "Uno", "Due", "Tre"], "origine": ["tappe"] * 4, "ruolo": ["partenza", "arrivo", "to", "to"],
        "nome": ["Alfa", "Alfa", "Beta", "Gamma"], "comune": ["Alfa", "Alfa", "Beta", "Gamma"],
        "dist_cammino_m": [900.0, 900.0, 0.0, 10.0], "dist_tratta_m": [np.nan, np.nan, 5000.0, np.nan],
    })
    far = cal.far_pairs(ok)
    assert far["cammino_id"].tolist() == ["c2", "c1"]  # c3 e' sotto la soglia
    assert far["massima_m"].tolist() == [5000.0, 900.0] and far["n_citazioni"].tolist() == [1, 2]


def test_distanze_delle_coppie_con_chiave_assente_sono_nan() -> None:
    lines = {"k": LineString([(-300, 500), (-200, 500)])}
    comune_geoms = {"A": box(0, 0, 1000, 1000)}
    result = cal.pair_distances(pd.Series(["k", None, "z"]), pd.Series(["A", "A", "A"]), lines, comune_geoms)
    assert result.iloc[0] == pytest.approx(200) and result.iloc[1:].isna().all()


def test_costi_e_comuni_sfiorati() -> None:
    all_pairs = pd.DataFrame({
        "cammino_id": ["cdi:1", "cdi:1", "cdi:1", "mitur:a", "mitur:a"],
        COMUNE: ["A", "B", "C", "A", "D"],
        "distanza_m": [0.0, 0.0, 120.0, 0.0, 30.0],
        "lunghezza_dentro_m": [900.0, 40.0, 0.0, 70.0, 0.0],
    })
    costs = cal.cost_table(all_pairs, pd.Series({"cdi": 1, "mitur": 1}))
    assert costs.loc[0, "coppie_totali"] == 3 and costs.loc[0, "aumento_vs_0"] == 0
    assert costs.loc[50, ["coppie_totali", "coppie_cdi", "coppie_mitur", "aumento_vs_0"]].tolist() == [4, 2, 2, 1]
    assert costs.loc[800, "aumento_vs_0_pct"] == pytest.approx(66.67)
    grazing = cal.grazing_table(all_pairs).set_index("soglia_m")
    assert grazing.loc[50, "coppie_sotto_soglia"] == 1 and grazing.loc[100, "coppie_sotto_soglia"] == 2
    assert grazing.loc[100, "sotto_soglia_cdi"] == 1 and grazing.loc[100, "sotto_soglia_mitur"] == 1


def test_comando_calibra_chiama_run(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []
    monkeypatch.setattr(cal, "run", lambda: called.append(True))
    assert cli.main(["calibra"]) == 0 and called == [True]
