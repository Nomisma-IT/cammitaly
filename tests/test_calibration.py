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
        COMUNE: ["010001", "010002", "020001", "030001", "030002", "040001", "050001", "060001", "070001", "070002"],
        config.COMUNE_NAME_COL: [
            "Bolzano/Bozen", "Sant'Angelo Lodigiano", "Reggio nell'Emilia", "Castro", "Castro", "San Giovanni in Fiore",
            "Santangelo", "Forlì", "Trevignano", "Trevignano Romano",
        ],
        config.COMUNE_REGION_COL: [4, 3, 8, 3, 16, 18, 16, 8, 5, 12],
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


def test_due_comuni_diversi_in_pezzi_diversi_sono_due_luoghi_non_omonimi(comuni_istat: pd.DataFrame) -> None:
    assert match("Forlì / Bolzano", comuni_istat) == (config.MATCH_TWO_PLACES, None)
    assert match("Forlì - Bolzano", comuni_istat) == (config.MATCH_TWO_PLACES, None)
    assert match("Castro / Forlì", comuni_istat) == (config.MATCH_HOMONYM, None)  # un pezzo e' gia' ambiguo


def test_nome_che_e_l_inizio_di_un_altro_comune_delle_regioni_e_ambiguo(comuni_istat: pd.DataFrame) -> None:
    assert match("Trevignano", comuni_istat, regions={5, 12}) == (config.MATCH_PREFIX, None)
    assert match("Trevignano", comuni_istat) == (config.MATCH_PREFIX, None)
    assert match("Trevignano", comuni_istat, regions={5}) == (config.MATCH_OK, "070001")  # l'altro non e' nelle regioni
    assert match("Trevignano", comuni_istat, regions={12}) == (config.MATCH_NOT_FOUND, None)  # solo l'inizio: non basta
    assert match("Trevignano Romano", comuni_istat, regions={5, 12}) == (config.MATCH_OK, "070002")
    assert match("Bolzano", comuni_istat) == (config.MATCH_OK, "010001")  # "Bozen" e' la stessa riga, non un altro comune


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
    expected = ["k25", "k60", "s10", "s25", "j10", "j25", "j50", "o25sx", "o25dx", "o50sx", "o50dx"]
    assert list(cal.degradation_methods()) == expected


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
        "gruppo": ["g", "g", "g"], COMUNE: ["A", "B", "C"], "distanza_m": [0.0, 30.0, 300.0], "dentro": [True, False, False],
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
    candidates = pd.DataFrame({"gruppo": ["g1"], COMUNE: ["A"], "distanza_m": [0.0], "dentro": [True]})
    totals = cal.totals_by_distance(cal.compare_sets(reference, candidates))
    assert totals.loc[800, "recall"] == pytest.approx(1 / 3, abs=1e-4)
    assert totals.loc[800, "recall_media_gruppo"] == pytest.approx(0.25)


# --- C: coppie provvisorie ---

def test_sovrapposizione_conta_la_parte_della_linea_corta_entro_50_metri() -> None:
    longer = LineString([(0, 0), (2000, 0)])
    assert cal.overlap_share(LineString([(100, 30), (900, 30)]), longer) == pytest.approx(1.0)
    assert cal.overlap_share(LineString([(100, 100), (900, 100)]), longer) == pytest.approx(0.0)
    # 500 m sovrapposti e 50 m di tolleranza su 1000 m di linea corta
    assert cal.overlap_share(LineString([(1500, 0), (2500, 0)]), longer) == pytest.approx(0.55, abs=0.02)


def test_coppie_provvisorie_chiedono_nome_simile_e_tracciato_sovrapposto() -> None:
    cdi_names = pd.Series({"cdi:1": "Cammino di San Benedetto", "cdi:2": "Via Alfa"})
    mitur_names = pd.Series({"mitur:a": "San Benedetto Cammino di", "mitur:b": "Via Alfa", "mitur:c": "Altro"})
    lines = {
        "cdi:1": LineString([(0, 0), (5000, 0)]), "mitur:a": LineString([(0, 20), (5000, 20)]),
        "cdi:2": LineString([(0, 0), (5000, 0)]), "mitur:b": LineString([(0, 900), (5000, 900)]),  # stesso nome, altrove
    }
    pairs = cal.same_cammino_pairs(lines, cdi_names, mitur_names)
    assert pairs[["cdi_id", "mitur_id"]].values.tolist() == [["cdi:1", "mitur:a"]]
    assert pairs["rapporto_lunghezza"].tolist() == [1.0] and pairs["nel_rapporto"].tolist() == [True]


def test_il_rapporto_di_lunghezza_marca_le_coppie_con_tracciati_di_estensione_diversa() -> None:
    cdi_names = pd.Series({"cdi:1": "Via Beta", "cdi:2": "Via Gamma"})
    mitur_names = pd.Series({"mitur:a": "Via Beta", "mitur:b": "Via Gamma"})
    lines = {
        "cdi:1": LineString([(0, 0), (5000, 0)]), "mitur:a": LineString([(0, 0), (2000, 0)]),  # stesso nome, un terzo del percorso
        "cdi:2": LineString([(0, 0), (5000, 0)]), "mitur:b": LineString([(0, 10), (4800, 10)]),  # rapporto 1,04
    }
    pairs = cal.same_cammino_pairs(lines, cdi_names, mitur_names).set_index("cdi_id")
    assert pairs.loc["cdi:1", "rapporto_lunghezza"] == 2.5 and not pairs.loc["cdi:1", "nel_rapporto"]
    assert pairs.loc["cdi:2", "nel_rapporto"] and pairs.loc["cdi:2", "rapporto_lunghezza"] == pytest.approx(1.0417, abs=1e-4)


# --- percorsi di sintesi ---

def test_distribuzione_delle_distanze() -> None:
    row = cal.distance_distribution(pd.Series([0.0, 0.0, 0.0, 100.0, np.nan]), "cammino")
    assert (row["n_coppie"], row["n_senza_linea"], row["n_misurate"]) == (5, 1, 4)
    assert row["quota_a_0"] == 0.75 and row["max"] == 100.0 and row["p50"] == 0.0
    assert row["p99"] == pytest.approx(97.0)


def test_coppie_lontane_sono_uniche_e_ordinate_dalla_piu_distante() -> None:
    ok = pd.DataFrame({
        "cammino_id": ["c1", "c1", "c2", "c3"], COMUNE: ["A", "A", "B", "C"], "tratta_id": [None, None, "t", None],
        "cammino": ["Uno", "Uno", "Due", "Tre"], "origine": ["tappe"] * 4, "ruolo": ["partenza", "arrivo", "to", "to"],
        "nome": ["Alfa", "Alfa", "Beta", "Gamma"], "comune": ["Alfa", "Alfa", "Beta", "Gamma"],
        "dist_cammino_m": [900.0, 900.0, 0.0, 10.0], "dist_tratta_m": [np.nan, np.nan, 5000.0, np.nan],
        "motivo": ["", "", "", ""],
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
    all_pairs["dentro"] = all_pairs["distanza_m"] == 0
    costs = cal.cost_table(all_pairs, pd.Series({"cdi": 1, "mitur": 1}))
    assert costs.loc[0, "coppie_totali"] == 3 and costs.loc[0, "aumento_vs_0"] == 0
    assert costs.loc[50, ["coppie_totali", "coppie_cdi", "coppie_mitur", "aumento_vs_0"]].tolist() == [4, 2, 2, 1]
    assert costs.loc[800, "aumento_vs_0_pct"] == pytest.approx(66.67)
    assert costs.loc[50, ["comuni_per_cammino_cdi", "comuni_per_cammino_mitur"]].tolist() == [2.0, 2.0]
    grazing = cal.grazing_table(all_pairs).set_index("soglia_m")
    assert grazing.loc[50, "coppie_sotto_soglia"] == 1 and grazing.loc[100, "coppie_sotto_soglia"] == 2
    assert grazing.loc[100, "sotto_soglia_cdi"] == 1 and grazing.loc[100, "sotto_soglia_mitur"] == 1


def test_comando_calibra_chiama_run(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []
    monkeypatch.setattr(cal, "run", lambda: called.append(True))
    assert cli.main(["calibra"]) == 0 and called == [True]


# --- errore laterale e comuni a rischio ---

def test_disturbo_gaussiano_e_riproducibile_con_lo_stesso_seme() -> None:
    methods = cal.degradation_methods()
    lines = [straight_line(2001, 5.0)]
    first, second = methods["j25"](lines), methods["j25"](lines)
    assert first[0].equals(second[0]) and not first[0].equals(lines[0])
    shifts = np.asarray(first[0].coords) - np.asarray(lines[0].coords)
    assert shifts.std() == pytest.approx(25, rel=0.05) and abs(shifts.mean()) < 2
    assert len(first[0].coords) == len(lines[0].coords)  # nessun vertice aggiunto o tolto


def test_traslazione_di_lato_porta_tutta_la_linea_alla_distanza_chiesta() -> None:
    line = LineString([(0, 0), (1000, 0)])
    left, right = cal.shift_line(line, 25), cal.shift_line(line, -25)
    assert left.bounds == (0.0, 25.0, 1000.0, 25.0) and right.bounds == (0.0, -25.0, 1000.0, -25.0)
    methods = cal.degradation_methods()
    assert methods["o25sx"]([line])[0].bounds[1] == 25 and methods["o25dx"]([line])[0].bounds[1] == -25


def test_riferimento_e_comuni_a_rischio_sommano_la_lunghezza_dentro_per_cammino() -> None:
    original = pd.DataFrame({
        "gruppo": ["g", "g", "g", "g"], COMUNE: ["A", "A", "B", "C"], "distanza_m": [0.0, 0.0, 0.0, 30.0],
        "dentro": [True, True, True, False], "lunghezza_dentro_m": [120.0, 150.0, 80.0, 0.0],
    })
    reference, marginal = cal.reference_sets(original)
    assert sorted(reference[COMUNE]) == ["A", "B"]  # C non e' attraversato
    assert marginal[COMUNE].tolist() == ["B"]  # A: 120 + 150 m, oltre la soglia di 200


def test_lato_peggiore_della_traslazione_prende_recall_minimo_ed_extra_massimo() -> None:
    sx = pd.DataFrame({"recall": [1.0, 0.9], "extra_tot": [0, 3], "recall_marginale": [1.0, 0.5]}, index=[0, 25])
    dx = pd.DataFrame({"recall": [0.95, 1.0], "extra_tot": [1, 2], "recall_marginale": [0.8, 1.0]}, index=[0, 25])
    merged = cal.worse_side({"o25sx": sx, "o25dx": dx, "k25": sx})
    assert set(merged) == {"o25", "k25"}
    assert merged["o25"].loc[0].tolist() == [0.95, 1, 0.8] and merged["o25"].loc[25].tolist() == [0.9, 3, 0.5]


def test_ramo_senza_traccia_solo_con_blocco_contiguo_e_altre_tappe_a_zero() -> None:
    def rows(cammino: str, distances: list[float]) -> pd.DataFrame:
        return pd.DataFrame({"cammino_id": cammino, "ordine": range(1, len(distances) + 1), "dist_cammino_m": distances})

    ok = pd.concat([
        rows("a", [900.0, 800.0, 0.0, 0.0]),  # blocco di due tappe lontane, il resto a 0
        rows("b", [900.0, 0.0, 800.0, 0.0]),  # lontane non contigue
        rows("c", [900.0, 0.0, 0.0, 0.0]),  # una sola tappa lontana: non e' un ramo
        rows("d", [900.0, 800.0, 100.0, 0.0]),  # un'altra tappa e' vicina ma non a zero
    ], ignore_index=True)
    motive = cal.far_stage_motive(ok)
    assert motive[ok["cammino_id"] == "a"].tolist() == [config.MOTIVO_BRANCH, config.MOTIVO_BRANCH, "", ""]
    assert (motive[ok["cammino_id"].isin(["b", "c", "d"])] == "").all()
