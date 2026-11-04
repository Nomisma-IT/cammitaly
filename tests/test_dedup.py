"""Deduplicazione: classi, cammini unici, sensibilita', segmenti lontani e confronto dei comuni, su geometrie sintetiche."""
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, MultiLineString, box

from cammitaly import __main__ as cli
from cammitaly import config, crossing, dedup, matching
from cammitaly.geometry import common

NEAR, STEP = config.DEDUP_NEAR_M, config.MATCH_SAMPLE_M
NEARS = config.DEDUP_NEAR_VALUES_M
DUPLICATO, LIMBO, DISTINTO = config.CLASSE_DUPLICATO, config.CLASSE_LIMBO, config.CLASSE_DISTINTO


def horizontal(x0: float, x1: float, y: float = 0.0) -> LineString:
    return LineString([(x0, y), (x1, y)])


def make_catalogue(entries: list[tuple[str, str, str]]) -> pd.DataFrame:
    """(cammino_id, fonte, nome) -> catalogo come load_catalogue, con la chiave del nome."""
    frame = pd.DataFrame(entries, columns=["cammino_id", "fonte", "nome"])
    frame["stato_geometria"] = "ok"
    frame["chiave"] = frame["nome"].map(matching.name_key)
    return frame


def coppie_of(entries: list[tuple[str, str, str]], lines: dict) -> pd.DataFrame:
    pairs = dedup.pair_table(make_catalogue(entries), lines, NEARS)
    return dedup.coppie_frame(pairs).set_index(["cammino_a", "cammino_b"])


# --- classi (regola D-12) ---

def test_stesso_nome_con_tracciato_identico_o_traslato_di_30_m_e_duplicato_di_80_m_e_distinto() -> None:
    entries = [("cdi:1", "cdi", "Via Alfa"), ("mitur:2", "mitur", "Via Alfa"), ("mitur:3", "mitur", "Cammino Alfa")]
    lines = {"cdi:1": horizontal(0, 10_000), "mitur:2": horizontal(0, 10_000, y=30), "mitur:3": horizontal(0, 10_000, y=-80)}
    coppie = coppie_of(entries, lines)
    identical_shifted = coppie.loc[("cdi:1", "mitur:2")]
    assert identical_shifted["classe"] == DUPLICATO and identical_shifted["motivo"] == config.MOTIVO_DUPLICATO
    assert identical_shifted["quota_minore"] == 1.0 and identical_shifted["rapporto_lunghezza"] == 1.0
    far = coppie.loc[("cdi:1", "mitur:3")]
    assert (far["classe"], far["motivo"]) == (DISTINTO, config.MOTIVO_NOME_SI_TRACCIATO_NO)
    assert far["quota_minore"] == 0.0 and far["punteggio_nome"] == 100


def test_linea_corta_contenuta_in_una_lunga_conta_la_quota_della_corta() -> None:
    entries = [("cdi:1", "cdi", "Via Alfa"), ("mitur:2", "mitur", "Via Alfa")]
    lines = {"cdi:1": horizontal(0, 10_000), "mitur:2": horizontal(2000, 4000, y=10)}
    pair = coppie_of(entries, lines).loc[("cdi:1", "mitur:2")]
    assert pair["classe"] == DUPLICATO and pair["quota_minore"] == 1.0
    assert pair["quota_b_in_a"] == 1.0 and pair["quota_a_in_b"] == pytest.approx(0.21, abs=0.01)
    assert pair["rapporto_lunghezza"] == pytest.approx(0.2)
    assert (pair["lunghezza_a_m"], pair["lunghezza_b_m"]) == (10_000, 2000)


def test_la_quota_minore_coincide_con_la_primitiva_di_matching() -> None:
    first, second = horizontal(0, 10_000), LineString([(1500, 0), (11_500, 0)])
    pair = coppie_of([("cdi:1", "cdi", "Via Alfa"), ("mitur:2", "mitur", "Via Alfa")], {"cdi:1": first, "mitur:2": second})
    expected = matching.overlap_share(first, second, NEAR, STEP)
    assert pair.iloc[0]["quota_minore"] == pytest.approx(expected, abs=1e-4)
    assert pair.iloc[0]["classe"] == DUPLICATO  # 0,85: sopra la soglia del 70%


@pytest.mark.parametrize(
    ("length_m", "expected_class", "expected_reason"),
    [
        (10_000, DUPLICATO, config.MOTIVO_DUPLICATO),  # quota 1,0
        (7100, DUPLICATO, config.MOTIVO_DUPLICATO),  # appena sopra il 70%
        (6900, LIMBO, config.MOTIVO_NOME_SI_TRACCIATO_INTERMEDIO),  # appena sotto il 70%
        (5100, LIMBO, config.MOTIVO_NOME_SI_TRACCIATO_INTERMEDIO),  # appena sopra il 50%
        (4900, DISTINTO, config.MOTIVO_NOME_SI_TRACCIATO_NO),  # appena sotto il 50%
        (0, DISTINTO, config.MOTIVO_NOME_SI_TRACCIATO_NO),
    ],
)
def test_nomi_simili_le_fasce_del_70_e_del_50_per_cento_della_linea_piu_corta(
    length_m: float, expected_class: str, expected_reason: str
) -> None:
    """La linea corta (10 km) ha `length_m` metri dentro i 50 m dalla lunga, il resto a 5 km di distanza."""
    short = MultiLineString([horizontal(0, length_m), horizontal(0, 10_000 - length_m, y=5000)]) if 0 < length_m < 10_000 else (
        horizontal(0, 10_000) if length_m else horizontal(0, 10_000, y=5000)
    )
    entries = [("cdi:1", "cdi", "Via Alfa"), ("mitur:2", "mitur", "Via Alfa")]
    long_line = horizontal(0, 100_000)
    pair = coppie_of(entries, {"cdi:1": long_line, "mitur:2": short}).loc[("cdi:1", "mitur:2")]
    assert (pair["classe"], pair["motivo"]) == (expected_class, expected_reason)
    assert pair["quota_minore"] == pytest.approx(length_m / 10_000, abs=0.01)


def test_geometria_assente_da_limbo_se_il_nome_e_simile_e_distinto_se_e_diverso() -> None:
    entries = [("cdi:1", "cdi", "Via Alfa"), ("mitur:2", "mitur", "Via Alfa"), ("mitur:3", "mitur", "Via Zeta")]
    coppie = coppie_of(entries, {"cdi:1": horizontal(0, 10_000)})
    similar = coppie.loc[("cdi:1", "mitur:2")]
    assert (similar["classe"], similar["motivo"]) == (LIMBO, config.MOTIVO_NOME_SI_GEOMETRIA_ASSENTE)
    assert pd.isna(similar["quota_minore"]) and pd.isna(similar["lunghezza_b_m"]) and pd.isna(similar["rapporto_lunghezza"])
    for not_candidate in (("cdi:1", "mitur:3"), ("mitur:2", "mitur:3")):  # nome diverso e una geometria assente
        assert not_candidate not in coppie.index


def test_nome_diverso_e_geometria_assente_e_distinto_con_il_suo_motivo() -> None:
    entries = [("cdi:1", "cdi", "Via Alfa"), ("mitur:2", "mitur", "Via Zeta")]
    pairs = dedup.pair_table(make_catalogue(entries), {"cdi:1": horizontal(0, 10_000)}, NEARS)
    (row,) = dedup.classify(pairs).to_dict("records")
    assert row == {"classe": DISTINTO, "motivo": config.MOTIVO_DISTINTO_GEOMETRIA_ASSENTE}


# --- quota minore: parita' di lunghezza e valori mancanti ---

def share_table(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """(lunghezza a, lunghezza b, quota a in b, quota b in a) -> colonne della tabella delle coppie a 50 m."""
    frame = pd.DataFrame(rows, columns=["lunghezza_a_m", "lunghezza_b_m", "a_in_b", "b_in_a"])
    frame[dedup.share_column("a_in_b", NEAR)] = frame.pop("a_in_b")
    frame[dedup.share_column("b_in_a", NEAR)] = frame.pop("b_in_a")
    frame["punteggio_nome"] = 100.0
    frame["parola_comune"] = True
    frame["nome_contenuto"] = False
    return frame


def test_quota_minore_e_quella_della_linea_piu_corta() -> None:
    pairs = share_table([(10, 20, 0.9, 0.3), (20, 10, 0.9, 0.3)])
    assert dedup.minor_share(pairs, NEAR).tolist() == [0.9, 0.3]
    assert dedup.minor_is_a(pairs, NEAR).tolist() == [True, False]


def test_a_parita_di_lunghezza_la_quota_minore_e_la_piu_bassa_e_non_dipende_dall_ordine() -> None:
    forward = share_table([(10, 10, 0.96, 0.50), (10, 10, 0.50, 0.96), (10, 10, 0.70, 0.70)])
    assert dedup.minor_share(forward, NEAR).tolist() == [0.50, 0.50, 0.70]
    assert dedup.minor_is_a(forward, NEAR).tolist() == [False, True, True]
    # classe identica scambiando a e b: prima (a in b 0,96) diventava duplicato o limbo secondo l'ordine del catalogo
    classes = dedup.classify(forward)["classe"]
    assert classes.tolist() == [LIMBO, LIMBO, DUPLICATO]  # quota minore 0,50 / 0,50 / 0,70: le prime due da verificare


def test_quota_minore_vuota_se_manca_una_geometria() -> None:
    nan = float("nan")
    pairs = share_table([(nan, 10, nan, nan), (10, nan, nan, nan)])
    assert dedup.minor_share(pairs, NEAR).isna().all()
    assert not dedup.minor_is_a(pairs, NEAR).any()


# --- riquadri ---

def test_riquadri_oltre_la_distanza_massima_non_si_confrontano_e_la_quota_vale_zero() -> None:
    near_max = max(NEARS)
    lines = {
        "a": horizontal(0, 1000), "close": horizontal(0, 1000, y=near_max - 10), "far": horizontal(0, 1000, y=near_max + 1),
    }
    shares = dedup.overlap_shares(lines, NEARS)
    assert ("a", "close") in shares and ("close", "a") in shares
    assert ("a", "far") not in shares and ("far", "a") not in shares
    assert shares[("a", "close")] == pytest.approx([1.0, 0.0, 0.0])  # a 90 m: dentro solo alla distanza maggiore (100 m)
    entries = [(name, "cdi", "Via Alfa") for name in lines]
    pairs = dedup.pair_table(make_catalogue(entries), lines, NEARS).set_index(["cammino_a", "cammino_b"])
    assert pairs.loc[("a", "far"), dedup.share_column("a_in_b", near_max)] == 0.0  # geometrie presenti: 0, non vuoto


def test_stesso_tracciato_con_nome_diverso_da_limbo() -> None:
    entries = [("cdi:1", "cdi", "Via Alfa"), ("cdi:2", "cdi", "Cammino Zeta")]
    line = horizontal(0, 10_000)
    pair = coppie_of(entries, {"cdi:1": line, "cdi:2": line}).loc[("cdi:1", "cdi:2")]
    assert (pair["classe"], pair["motivo"]) == (LIMBO, config.MOTIVO_TRACCIATO_SI_NOME_NO)
    assert pair["quota_minore"] == 1.0 and pair["punteggio_nome"] < 40


def test_nome_diverso_e_tracciati_lontani_non_sono_candidati_e_i_vicini_sono_distinti() -> None:
    entries = [("cdi:1", "cdi", "Via Alfa"), ("cdi:2", "cdi", "Cammino Zeta"), ("cdi:3", "cdi", "Sentiero Beta")]
    lines = {"cdi:1": horizontal(0, 10_000), "cdi:2": horizontal(0, 10_000, y=500_000), "cdi:3": horizontal(8000, 20_000, y=10)}
    coppie = coppie_of(entries, lines)
    assert list(coppie.index) == [("cdi:1", "cdi:3")]  # le altre due coppie non hanno nome simile ne' tracciati che si toccano
    pair = coppie.iloc[0]
    assert (pair["classe"], pair["motivo"]) == (DISTINTO, config.MOTIVO_DISTINTO)
    assert 0 < pair["quota_minore"] < 0.5


def test_punteggio_cento_per_inclusione_e_segnalato_e_il_tracciato_decide() -> None:
    entries = [("cdi:1", "cdi", "Via Francigena"), ("mitur:2", "mitur", "Magna Via Francigena nel Sud"), ("mitur:3", "mitur", "Via Francigena")]
    lines = {"cdi:1": horizontal(0, 10_000), "mitur:2": horizontal(0, 10_000, y=900), "mitur:3": horizontal(0, 10_000, y=5)}
    coppie = coppie_of(entries, lines)
    contained = coppie.loc[("cdi:1", "mitur:2")]
    assert contained["punteggio_nome"] == 100 and contained["nome_contenuto"] and contained["classe"] == DISTINTO
    assert contained["punteggio_nome_sort"] < 100
    identical = coppie.loc[("cdi:1", "mitur:3")]
    assert identical["punteggio_nome"] == 100 and not identical["nome_contenuto"] and identical["classe"] == DUPLICATO


# --- sezioni (D-12, regola 2) ---

def test_tracciato_contenuto_al_95_con_una_parola_in_comune_e_sezione_senza_parole_in_comune_e_limbo() -> None:
    entries = [
        ("cdi:1", "cdi", "Via di Francesco da Firenze a La Verna"), ("mitur:2", "mitur", "Via di Francesco in Toscana"),
        ("cdi:3", "cdi", "Cammino Zeta"),
    ]
    whole = horizontal(0, 100_000)
    lines = {"cdi:1": horizontal(0, 40_000, y=10), "mitur:2": whole, "cdi:3": horizontal(0, 40_000, y=-10)}
    coppie = coppie_of(entries, lines)
    section = coppie.loc[("cdi:1", "mitur:2")]
    assert section["punteggio_nome"] < config.DEDUP_NAME_MIN_SCORE and section["parola_comune"]
    assert (section["classe"], section["motivo"]) == (config.CLASSE_SEZIONE, config.MOTIVO_SEZIONE)
    assert section["quota_minore"] == 1.0
    no_word = coppie.loc[("mitur:2", "cdi:3")]
    assert not no_word["parola_comune"]
    assert (no_word["classe"], no_word["motivo"]) == (LIMBO, config.MOTIVO_TRACCIATO_SI_NOME_NO)


def test_sezione_sotto_il_95_per_cento_o_senza_una_geometria_non_si_fonde() -> None:
    entries = [("cdi:1", "cdi", "Via di Francesco da Firenze"), ("mitur:2", "mitur", "Via di Francesco in Toscana")]
    partial = MultiLineString([horizontal(0, 9000, y=10), horizontal(0, 1000, y=5000)])  # 90% dentro
    pair = coppie_of(entries, {"cdi:1": partial, "mitur:2": horizontal(0, 100_000)}).loc[("cdi:1", "mitur:2")]
    assert pair["classe"] == DISTINTO and 0.85 < pair["quota_minore"] < 0.95
    pair = coppie_of(entries, {"mitur:2": horizontal(0, 100_000)})
    assert pair.empty  # nome non simile e una geometria assente: nemmeno candidata


def test_la_regola_della_sezione_non_cambia_con_lo_scambio_di_a_e_b() -> None:
    entries = [("cdi:1", "cdi", "Via di Francesco da Firenze a La Verna"), ("mitur:2", "mitur", "Via di Francesco in Toscana")]
    lines = {"cdi:1": horizontal(0, 40_000, y=10), "mitur:2": horizontal(0, 100_000)}
    forward = coppie_of(entries, lines).iloc[0]
    swapped = coppie_of(entries[::-1], lines).iloc[0]
    assert forward["classe"] == swapped["classe"] == config.CLASSE_SEZIONE


# --- limbo ---

def test_riga_di_limbo_ha_oggetto_motivo_e_numeri_nel_dettaglio() -> None:
    entries = [("cdi:1", "cdi", "Via Alfa"), ("mitur:2", "mitur", "Via Alfa")]
    short = MultiLineString([horizontal(0, 6000), horizontal(0, 4000, y=5000)])  # quota 0,60: da verificare
    coppie = coppie_of(entries, {"cdi:1": horizontal(0, 10_000), "mitur:2": short}).reset_index()
    (row,) = dedup.limbo_rows(coppie)
    assert list(row) == config.LIMBO_FIELDS
    assert row["oggetto"] == "cdi:1|mitur:2" and row["motivo"] == config.MOTIVO_NOME_SI_TRACCIATO_INTERMEDIO
    for expected in ("punteggio nome 100.0", "quota minore 0.6000", "10.0 km e 10.0 km", "Via Alfa / Via Alfa"):
        assert expected in row["dettaglio"]


# --- soglie esatte ---

def threshold_pairs(rows: list[tuple[float, float, bool, bool]]) -> pd.DataFrame:
    """(punteggio del nome, quota minore, nome contenuto, parola in comune) -> tabella delle coppie a 50 m, lunghezze uguali."""
    frame = share_table([(10, 10, quota, quota) for _, quota, _, _ in rows])
    frame["punteggio_nome"] = [score for score, _, _, _ in rows]
    frame["nome_contenuto"] = [contained for _, _, contained, _ in rows]
    frame["parola_comune"] = [shared for _, _, _, shared in rows]
    return frame


def test_soglia_del_nome_95_inclusa_e_nome_poco_sotto_non_e_simile() -> None:
    pairs = threshold_pairs([(95.0, 0.80, False, True), (94.99, 0.80, False, True), (100.0, 0.80, False, True)])
    assert dedup.classify(pairs)["classe"].tolist() == [DUPLICATO, DISTINTO, DUPLICATO]


def test_sezione_al_95_per_cento_inclusa_e_poco_sotto_no() -> None:
    """Nome non simile con una parola in comune: 0,95 e' sezione, 0,9499 resta distinto; senza parola in comune e' limbo."""
    pairs = threshold_pairs([
        (60.0, 0.95, False, True), (60.0, 0.9499, False, True), (60.0, 0.95, False, False), (60.0, 0.9499, False, False),
    ])
    classes = dedup.classify(pairs)
    assert classes["classe"].tolist() == [config.CLASSE_SEZIONE, DISTINTO, LIMBO, DISTINTO]
    assert classes["motivo"].tolist()[0] == config.MOTIVO_SEZIONE


def test_nome_contenuto_con_il_tracciato_contenuto_al_95_e_sezione_prima_del_duplicato() -> None:
    """Il nome di una e' contenuto nell'altro (punteggio 100) e la quota e' >= 0,95: sezione; tra 0,70 e 0,95 resta duplicato."""
    pairs = threshold_pairs([
        (100.0, 0.95, True, True), (100.0, 0.9499, True, True), (100.0, 0.70, True, True), (100.0, 1.0, False, True),
    ])
    classes = dedup.classify(pairs)
    assert classes["classe"].tolist() == [config.CLASSE_SEZIONE, DUPLICATO, DUPLICATO, DUPLICATO]
    assert classes["motivo"].tolist() == [config.MOTIVO_SEZIONE, *[config.MOTIVO_DUPLICATO] * 3]  # l'ultima: nome uguale, non contenuto


def test_la_sezione_con_nome_contenuto_ha_il_ruolo_sezione_e_la_fusione_non_cambia() -> None:
    entries = [("cdi:1", "cdi", "Via Francigena"), ("mitur:2", "mitur", "Magna Via Francigena nel Sud"), ("mitur:3", "mitur", "Via Francigena")]
    lines = {"cdi:1": horizontal(0, 10_000), "mitur:2": horizontal(0, 100_000, y=5), "mitur:3": horizontal(0, 10_000, y=-5)}
    catalogue = make_catalogue(entries)
    coppie = dedup.coppie_frame(dedup.pair_table(catalogue, lines, NEARS))
    by_pair = coppie.set_index(["cammino_a", "cammino_b"])["classe"].to_dict()
    assert by_pair[("cdi:1", "mitur:2")] == by_pair[("mitur:2", "mitur:3")] == config.CLASSE_SEZIONE
    assert by_pair[("cdi:1", "mitur:3")] == DUPLICATO
    merging = coppie[coppie["classe"].isin([DUPLICATO, config.CLASSE_SEZIONE])]
    unici, membri = dedup.unique_frames(catalogue, {name: line.length for name, line in lines.items()}, merging)
    assert len(unici) == 1 and unici.iloc[0]["nome"] == "Magna Via Francigena nel Sud"
    roles = membri.set_index("cammino_id")["ruolo"].to_dict()
    assert roles == {"mitur:2": config.RUOLO_PRINCIPALE, "cdi:1": config.RUOLO_SEZIONE, "mitur:3": config.RUOLO_SEZIONE}


# --- catene ---

def test_catena_di_tre_cammini_e_segnalata_e_la_coppia_completa_no() -> None:
    entries = [
        ("cdi:1", "cdi", "Via Alfa"), ("cdi:2", "cdi", "Via Alfa"), ("mitur:3", "mitur", "Via Alfa"),
        ("cdi:4", "cdi", "Cammino Zeta"), ("mitur:5", "mitur", "Cammino Zeta"),
    ]
    lines = {
        "cdi:1": horizontal(0, 4000), "cdi:2": horizontal(0, 10_000), "mitur:3": horizontal(6000, 10_000),  # 1 e 3 non si toccano
        "cdi:4": horizontal(0, 5000, y=200_000), "mitur:5": horizontal(0, 5000, y=200_010),
    }
    catalogue = make_catalogue(entries)
    coppie = dedup.coppie_frame(dedup.pair_table(catalogue, lines, NEARS))
    assert coppie.set_index(["cammino_a", "cammino_b"]).loc[("cdi:1", "mitur:3"), "classe"] == DISTINTO
    unici, _ = dedup.unique_frames(catalogue, {name: line.length for name, line in lines.items()}, coppie[coppie["classe"] == DUPLICATO])
    groups = {tuple(row.membri.split("|")): row.catena for row in unici.itertuples() if row.n_membri > 1}
    assert groups == {("cdi:1", "cdi:2", "mitur:3"): True, ("cdi:4", "mitur:5"): False}


# --- cammini unici e membri ---

def merging_of(edges: list[tuple[str, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(edges, columns=["cammino_a", "cammino_b", "classe"])


def toy_unique_catalogue() -> pd.DataFrame:
    entries = [
        ("cdi:1", "cdi", "Via Alfa"), ("mitur:2", "mitur", "Via Alfa Grande"), ("cdi:3", "cdi", "Alfa tratto"),
        ("cdi:4", "cdi", "Cammino Zeta"), ("mitur:5", "mitur", "Via Beta"), ("mitur:6", "mitur", "Via Beta bis"),
    ]
    catalogue = make_catalogue(entries)
    catalogue["stato_geometria"] = ["parziale", "ok", "ok", "assente", "ok", "parziale"]
    return catalogue


def test_cammini_unici_componenti_con_nome_del_piu_lungo_alias_ruoli_e_singoli() -> None:
    lengths = {"cdi:1": 40.0, "mitur:2": 100.0, "cdi:3": 10.0, "mitur:5": 50.0, "mitur:6": 50.0}
    edges = [("cdi:1", "mitur:2", DUPLICATO), ("cdi:3", "mitur:2", config.CLASSE_SEZIONE), ("mitur:5", "mitur:6", DUPLICATO)]
    unici, membri = dedup.unique_frames(toy_unique_catalogue(), lengths, merging_of(edges))
    assert list(unici.columns) == list(config.DEDUP_UNICI_FIELDS) and list(membri.columns) == list(config.DEDUP_MEMBRI_FIELDS)
    assert unici["n_membri"].tolist() == [3, 1, 2] and len(unici) == 3
    alfa = unici.iloc[0]
    assert alfa["nome"] == "Via Alfa Grande" and alfa["membri"] == "cdi:1|mitur:2|cdi:3"
    assert alfa["alias"] == "Via Alfa|Alfa tratto" and alfa["fonti"] == "cdi|mitur" and alfa["stato"] == "ok"
    assert alfa["catena"]  # cdi:1 e cdi:3 non sono legati tra loro
    roles = membri.set_index("cammino_id")["ruolo"].to_dict()
    assert roles["mitur:2"] == config.RUOLO_PRINCIPALE and roles["cdi:3"] == config.RUOLO_SEZIONE and roles["cdi:1"] == config.RUOLO_DUPLICATO
    zeta = unici[unici["nome"] == "Cammino Zeta"].iloc[0]
    assert (zeta["n_membri"], zeta["alias"], zeta["stato"], zeta["catena"]) == (1, "", "assente", False)
    beta = unici.iloc[2]
    assert beta["nome"] == "Via Beta" and beta["alias"] == "Via Beta bis"  # a pari lunghezza il primo del catalogo
    assert beta["stato"] == "ok"


def test_ogni_cammino_del_catalogo_compare_una_volta_in_membri_e_i_nomi_uguali_non_sono_alias() -> None:
    catalogue = toy_unique_catalogue()
    catalogue.loc[catalogue["cammino_id"] == "mitur:6", "nome"] = "Via Beta"
    edges = [("mitur:5", "mitur:6", DUPLICATO), ("cdi:1", "mitur:2", DUPLICATO)]
    unici, membri = dedup.unique_frames(catalogue, {}, merging_of(edges))  # senza lunghezze: vince il primo del catalogo
    assert membri["cammino_id"].is_unique and set(membri["cammino_id"]) == set(catalogue["cammino_id"])
    assert unici["n_membri"].sum() == len(catalogue) == len(membri)
    assert set(membri["cammino_unico_id"]) == set(unici["cammino_unico_id"])
    assert unici.set_index("nome").loc["Via Beta", "alias"] == ""
    assert unici.iloc[0]["nome"] == "Via Alfa"


def test_gli_alias_sono_uno_per_chiave_di_nome_senza_maiuscole_articoli_e_parole_generiche() -> None:
    entries = [
        ("cdi:1", "cdi", "Cammino di San Vili"), ("mitur:2", "mitur", "Cammino San Vili"), ("cdi:3", "cdi", "CAMMINO DI SAN VILI"),
        ("mitur:4", "mitur", "Via di San Vili - Alta"), ("cdi:5", "cdi", "Alta Via di San Vili"),
    ]
    catalogue = make_catalogue(entries)
    edges = [("cdi:1", other, DUPLICATO) for other in ("mitur:2", "cdi:3", "mitur:4", "cdi:5")]
    unici, _ = dedup.unique_frames(catalogue, {"cdi:1": 10.0}, merging_of(edges))
    # le prime tre hanno la chiave "san vili" del principale: nessun alias; le altre due hanno chiavi diverse e restano
    assert unici.iloc[0]["nome"] == "Cammino di San Vili"
    assert unici.iloc[0]["alias"] == "Via di San Vili - Alta|Alta Via di San Vili"


def test_senza_nessun_arco_ogni_cammino_e_il_proprio_gruppo_e_la_catena_si_segnala() -> None:
    catalogue = toy_unique_catalogue()
    unici, membri = dedup.unique_frames(catalogue, {}, merging_of([]))
    assert len(unici) == len(membri) == len(catalogue) and (membri["ruolo"] == config.RUOLO_PRINCIPALE).all()
    chain = [("cdi:1", "mitur:2", DUPLICATO), ("mitur:2", "cdi:3", DUPLICATO)]  # cdi:1 e cdi:3 non legati
    unici, _ = dedup.unique_frames(catalogue, {"cdi:1": 1.0, "mitur:2": 3.0, "cdi:3": 2.0}, merging_of(chain))
    assert unici.iloc[0]["catena"] and not unici.iloc[1:]["catena"].any()
    complete = chain + [("cdi:1", "cdi:3", DUPLICATO)]
    unici, _ = dedup.unique_frames(catalogue, {}, merging_of(complete))
    assert not unici["catena"].any()


# --- sensibilita' ---

def toy_sensitivity_pairs() -> pd.DataFrame:
    """Tre cammini con lo stesso nome, di pari lunghezza e traslati lungo l'asse, e un quarto lontano di nome diverso.

    Le tre coppie dello stesso nome hanno quota ~0,715 (1-2), ~0,475 (1-3) e ~0,765 (2-3), a meno di 0,01 alle tre
    distanze; le altre tre coppie (col quarto) sono distinte.
    """
    entries = [(f"cdi:{n}", "cdi", "Via Alfa") for n in (1, 2, 3)] + [("cdi:4", "cdi", "Via Zeta")]
    lines = {
        "cdi:1": horizontal(0, 10_000), "cdi:2": horizontal(2900, 12_900), "cdi:3": horizontal(5300, 15_300),
        "cdi:4": horizontal(0, 10_000, y=300_000),
    }
    return dedup.pair_table(make_catalogue(entries), lines, NEARS)


def test_sensibilita_conta_le_classi_a_ogni_combinazione_di_soglie_e_i_conteggi_sommano_alle_coppie() -> None:
    pairs = toy_sensitivity_pairs()
    table = dedup.sensitivity_frame(pairs)
    assert list(table.columns) == list(config.DEDUP_SENSIBILITA_FIELDS)
    axes = (config.DEDUP_SENS_NAME_SCORES, config.DEDUP_SENS_OVERLAPS, config.DEDUP_SENS_DISTINCT_BELOW, config.DEDUP_SENS_NEAR_M)
    assert len(table) == np.prod([len(axis) for axis in axes])
    assert config.DEDUP_OVERLAP_MIN in config.DEDUP_SENS_OVERLAPS and config.DEDUP_DISTINCT_BELOW in config.DEDUP_SENS_DISTINCT_BELOW
    for row in table.itertuples():
        duplicates = sum(share >= row.soglia_quota for share in (0.715, 0.765))
        apart = int(0.475 < row.soglia_distinto)
        assert (row.n_duplicato, row.n_sezione, row.n_limbo, row.n_distinto) == (duplicates, 0, 3 - duplicates - apart, 3 + apart)
        assert row.n_duplicato + row.n_sezione + row.n_limbo + row.n_distinto == len(pairs)
        assert row.n_nome_100 == 3 and row.n_nome_100_geometria_assente == 0
        assert row.n_nome_100_tracciato_diverso == 3 - duplicates


def test_sensibilita_conta_le_sezioni_e_la_riga_adottata_coincide_con_coppie() -> None:
    entries = [("cdi:1", "cdi", "Via di Francesco da Firenze"), ("mitur:2", "mitur", "Via di Francesco in Toscana"), ("cdi:3", "cdi", "Cammino Zeta")]
    lines = {"cdi:1": horizontal(0, 40_000, y=10), "mitur:2": horizontal(0, 100_000), "cdi:3": horizontal(0, 10_000, y=500_000)}
    pairs = dedup.pair_table(make_catalogue(entries), lines, NEARS)
    table = dedup.sensitivity_frame(pairs)
    assert (table["n_sezione"] == 1).all() and (table["n_duplicato"] == 0).all()
    adopted = table[
        (table["soglia_nome"] == config.DEDUP_NAME_MIN_SCORE) & (table["soglia_quota"] == config.DEDUP_OVERLAP_MIN)
        & (table["soglia_distinto"] == config.DEDUP_DISTINCT_BELOW) & (table["distanza_m"] == NEAR)
    ]
    counts = dedup.coppie_frame(pairs)["classe"].value_counts()
    assert adopted.iloc[0]["n_sezione"] == counts.get(config.CLASSE_SEZIONE, 0) == 1


def test_sensibilita_con_geometria_assente_conta_i_nomi_identici_a_parte() -> None:
    entries = [("cdi:1", "cdi", "Via Alfa"), ("cdi:2", "cdi", "Via Alfa"), ("cdi:3", "cdi", "Via Alfa")]
    pairs = dedup.pair_table(make_catalogue(entries), {"cdi:1": horizontal(0, 1000)}, NEARS)
    row = dedup.sensitivity_frame(pairs).iloc[0]
    assert (row["n_duplicato"], row["n_limbo"], row["n_distinto"]) == (0, 3, 0)  # tutte e tre: nome uguale, una senza geometria
    assert (row["n_nome_100"], row["n_nome_100_tracciato_diverso"], row["n_nome_100_geometria_assente"]) == (3, 0, 3)


# --- segmenti lontani ---

def test_segmento_lontano_con_lunghezza_distanza_massima_e_punto_di_mezzo() -> None:
    other = horizontal(0, 10_000)
    shorter = LineString([(0, 0), (6000, 0), (6000, 400), (8000, 400), (8000, 0)])
    (stretch,) = dedup.far_stretches(shorter, other, NEAR, STEP)
    assert stretch.length_m == pytest.approx(2000 + 2 * 350, rel=0.03)  # i due tratti verticali oltre i 50 m e il tratto a y = 400
    assert stretch.max_distance_m == pytest.approx(400)
    assert stretch.middle.x == pytest.approx(7000, abs=1000) and stretch.middle.y == pytest.approx(400)
    assert dedup.far_stretches(horizontal(100, 900, y=20), other, NEAR, STEP) == []


def test_parti_non_contigue_lontane_sono_due_segmenti_e_parti_contigue_uno_solo() -> None:
    other = horizontal(0, 10_000)
    separate = MultiLineString([horizontal(0, 3000, y=500).coords, horizontal(5000, 8000, y=500).coords])
    touching = MultiLineString([horizontal(0, 3000, y=500).coords, horizontal(3000, 8000, y=500).coords])
    assert len(dedup.far_stretches(separate, other, NEAR, STEP)) == 2
    (joined,) = dedup.far_stretches(touching, other, NEAR, STEP)
    assert joined.length_m == pytest.approx(8000, rel=0.01)


@pytest.mark.parametrize(
    ("max_distance_m", "km_non_tappa", "km_total", "expected"),
    [
        (config.DEDUP_OFFSET_MAX_M, 0.0, 5.0, config.DIAGNOSI_SFASAMENTO),
        (config.DEDUP_OFFSET_MAX_M, 5.0, 5.0, config.DIAGNOSI_SFASAMENTO),  # lo sfasamento vale anche per le varianti
        (config.DEDUP_OFFSET_MAX_M + 1, 3.0, 5.0, config.DIAGNOSI_VARIANTE),
        (config.DEDUP_MISSING_MIN_M + 1, 1.0, 5.0, config.DIAGNOSI_MANCANTI),
        (config.DEDUP_MISSING_MIN_M, 1.0, 5.0, config.DIAGNOSI_VERSIONE),
        (config.DEDUP_OFFSET_MAX_M + 1, 2.5, 5.0, config.DIAGNOSI_VERSIONE),  # esattamente meta' non basta per la variante
    ],
)
def test_diagnosi_in_parole(max_distance_m: float, km_non_tappa: float, km_total: float, expected: str) -> None:
    assert dedup.diagnose(max_distance_m, km_non_tappa, km_total) == expected


def tratta_rows(cammino_id: str, parts: list[tuple[str, LineString]]) -> list[dict[str, object]]:
    return [
        {"tratta_id": f"{cammino_id}:{n}", "cammino_id": cammino_id, "nome": f"{tipo} {n}", "tipo": tipo, "geometry": geom}
        for n, (tipo, geom) in enumerate(parts)
    ]


def toy_tratte() -> gpd.GeoDataFrame:
    rows = tratta_rows("cdi:1", [(config.TIPO_TAPPA, horizontal(0, 10_000))])
    rows += tratta_rows(
        "mitur:2",
        [(config.TIPO_TAPPA, horizontal(0, 6000)), (config.TIPO_VARIANTE, LineString([(6000, 0), (6000, 500), (8000, 500)]))],
    )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=config.CRS_WORK)


def test_quasi_duplicato_con_variante_in_una_sola_fonte() -> None:
    tratte = toy_tratte()
    entries = [("cdi:1", "cdi", "Via Alfa"), ("mitur:2", "mitur", "Via Alfa")]
    lines = dedup.cammino_lines(tratte)
    pairs = dedup.pair_table(make_catalogue(entries), lines, NEARS)
    coppie = dedup.coppie_frame(pairs)
    assert coppie.iloc[0]["classe"] == DUPLICATO and config.DEDUP_QUASI_OVERLAP_MIN <= coppie.iloc[0]["quota_minore"] < config.DEDUP_QUASI_OVERLAP_MAX
    (row,) = dedup.quasi_frame(pairs, lines, tratte).to_dict("records")
    assert list(row) == list(config.DEDUP_QUASI_FIELDS)
    assert row["quota_minore"] == coppie.iloc[0]["quota_minore"]
    assert row["cammino_corto"] == "mitur:2"
    assert row["n_segmenti_lontani"] == 1
    assert row["tipo_segmento_max"] == config.TIPO_VARIANTE and row["tratta_segmento_max"] == f"{config.TIPO_VARIANTE} 1"
    assert row["km_lontani"] == pytest.approx(2.4, abs=0.1) and row["km_lontani_non_tappa"] == row["km_lontani"]
    assert row["distanza_max_m"] == pytest.approx(500)
    assert row["diagnosi"] == config.DIAGNOSI_VARIANTE


def test_i_duplicati_e_le_coppie_lontane_non_sono_quasi_duplicati() -> None:
    tratte = toy_tratte()
    lines = dedup.cammino_lines(tratte)
    lines["mitur:2"] = horizontal(0, 6000, y=20)  # quota minore 1: duplicato
    entries = [("cdi:1", "cdi", "Via Alfa"), ("mitur:2", "mitur", "Via Alfa")]
    pairs = dedup.pair_table(make_catalogue(entries), lines, NEARS)
    assert dedup.coppie_frame(pairs).iloc[0]["classe"] == DUPLICATO
    assert dedup.quasi_frame(pairs, lines, tratte).empty


def test_i_quasi_duplicati_si_scelgono_sulla_quota_non_arrotondata() -> None:
    """Una quota di 0,49996 si arrotonda a 0,5000 nei CSV ma e' sotto il limite: non e' un quasi duplicato."""
    pairs = share_table([(10, 20, 0.49996, 0.2)])
    assert dedup.quasi_frame(pairs, {}, toy_tratte()).empty
    assert round(0.49996, config.RATIO_DECIMALS) >= config.DEDUP_QUASI_OVERLAP_MIN


# --- comuni ---

def toy_comuni() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            config.COMUNE_ID_COL: ["001", "002", "003"], config.COMUNE_NAME_COL: ["Uno", "Due", "Tre"],
            config.COMUNE_REGION_COL: [1, 1, 1],
        },
        geometry=[box(0, -1000, 3000, 1000), box(3000, -1000, 6000, 1000), box(6000, -1000, 9000, 1000)],
        crs=config.CRS_WORK,
    )


def test_confronto_dei_comuni_delle_due_versioni() -> None:
    lines = {"cdi:1": horizontal(500, 5000), "mitur:2": horizontal(4000, 8000)}  # {001, 002} e {002, 003}
    selected = pd.DataFrame({
        "cammino_a": ["cdi:1"], "cammino_b": ["mitur:2"], "nome_a": ["Via Alfa"], "nome_b": ["Via Alfa"],
        "classe": [DUPLICATO], "quota_minore": [0.97],
    })
    (row,) = dedup.comuni_frame(selected, lines, toy_comuni()).to_dict("records")
    assert (row["n_a"], row["n_b"], row["n_entrambi"], row["n_unione"]) == (2, 2, 1, 3)
    assert row["jaccard"] == pytest.approx(1 / 3, abs=1e-4)


def test_comuni_vicini_entro_la_distanza_del_committente_contano_e_oltre_no() -> None:
    d = config.CROSSING_DISTANCE_M
    comuni = toy_comuni()
    near_edge = {"x": horizontal(100, 2900, y=1000 + d - 1)}  # a d - 1 metri dal bordo nord del comune 001
    too_far = {"x": horizontal(100, 2900, y=1000 + d + 1)}
    assert dedup.comuni_sets(["x"], near_edge, comuni)["x"] == frozenset({"001"})
    assert dedup.comuni_sets(["x"], too_far, comuni)["x"] == frozenset()


def test_selezione_per_il_confronto_unisce_duplicati_e_quasi_duplicati() -> None:
    columns = ["cammino_a", "cammino_b", "nome_a", "nome_b", "quota_minore"]
    coppie = pd.DataFrame(
        [["a", "b", "A", "B", 1.0, DUPLICATO], ["a", "c", "A", "C", 0.7, LIMBO]], columns=[*columns, "classe"],
    )
    quasi = pd.DataFrame([["a", "c", "A", "C", 0.7]], columns=columns)
    selected = dedup.comuni_selection(coppie, quasi)
    assert selected["classe"].tolist() == [DUPLICATO, config.CLASSE_QUASI_DUPLICATO]


# --- percorso completo e comando ---

def test_run_scrive_una_uscita_per_file_di_config_con_le_colonne_del_contratto_e_fine_riga_lf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tratte = toy_tratte()
    entries = [("cdi:1", "cdi", "Via Alfa"), ("mitur:2", "mitur", "Via Alfa"), ("mitur:3", "mitur", "Via Alfa Zeta")]
    monkeypatch.setattr(config, "DEDUP_DIR", tmp_path)
    monkeypatch.setattr(dedup, "load_catalogue", lambda: make_catalogue(entries))
    monkeypatch.setattr(common, "read_tratte", lambda fonti: tratte)
    monkeypatch.setattr(crossing, "load_comuni", toy_comuni)
    dedup.run()
    fields = {
        "coppie": config.DEDUP_COPPIE_FIELDS, "limbo": config.LIMBO_FIELDS,
        "sensibilita": config.DEDUP_SENSIBILITA_FIELDS, "quasi": config.DEDUP_QUASI_FIELDS, "comuni": config.DEDUP_COMUNI_FIELDS,
        "unici": config.DEDUP_UNICI_FIELDS, "membri": config.DEDUP_MEMBRI_FIELDS,
    }
    assert set(fields) == set(config.DEDUP_FILES)  # nessun file senza contratto e nessuno in piu'
    for name, columns in fields.items():
        frame = pd.read_csv(tmp_path / config.DEDUP_FILES[name], encoding="utf-8")
        assert list(frame.columns) == list(columns)
    grid = (config.DEDUP_SENS_NAME_SCORES, config.DEDUP_SENS_OVERLAPS, config.DEDUP_SENS_DISTINCT_BELOW, config.DEDUP_SENS_NEAR_M)
    assert len(pd.read_csv(tmp_path / config.DEDUP_FILES["sensibilita"])) == np.prod([len(axis) for axis in grid])
    limbo = pd.read_csv(tmp_path / config.DEDUP_FILES["limbo"])
    assert sorted(limbo["oggetto"]) == ["cdi:1|mitur:3", "mitur:2|mitur:3"]  # nome simile, geometria assente
    for name in config.DEDUP_FILES:  # un solo fine riga (LF) per tutte le uscite, limbo compreso
        assert b"\r" not in (tmp_path / config.DEDUP_FILES[name]).read_bytes()
    unici = pd.read_csv(tmp_path / config.DEDUP_FILES["unici"], keep_default_na=False)
    membri = pd.read_csv(tmp_path / config.DEDUP_FILES["membri"])
    assert len(unici) == 2 and len(membri) == 3 and membri["cammino_id"].is_unique  # cdi:1 con mitur:2 (duplicato), mitur:3 da solo
    comuni = pd.read_csv(tmp_path / config.DEDUP_FILES["comuni"])
    assert comuni["classe"].tolist() == [config.CLASSE_QUASI_DUPLICATO]
    assert not np.isnan(comuni.loc[0, "jaccard"])


def test_scrittura_di_una_tabella_crea_la_cartella_e_lascia_vuoti_i_valori_mancanti(tmp_path: Path) -> None:
    path = tmp_path / "nuova" / "t.csv"
    common.write_frame(pd.DataFrame({"a": [1.5, float("nan")], "b": ["x", "y"]}), path)
    assert path.read_bytes() == b"a,b\n1.5,x\n,y\n"


def test_comando_dedup_chiama_run(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []
    monkeypatch.setattr(dedup, "run", lambda: called.append(True))
    assert cli.main(["dedup"]) == 0 and called == [True]
