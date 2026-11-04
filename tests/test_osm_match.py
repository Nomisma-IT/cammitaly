"""Abbinamento con OSM: quote, nome e geometria, regioni e lunghezza, annidamento, ambiguita', sostituto nella deduplica."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import shapely
from shapely.geometry import LineString, MultiLineString, box

from cammitaly import config, dedup, matching, osm_match

NEAR, STEP = config.OSM_MATCH_NEAR_M, config.MATCH_SAMPLE_M
LAZIO, UMBRIA = box(0, 0, 100_000, 100_000), box(100_000, 0, 200_000, 100_000)


def horizontal(x0: float, x1: float, y: float = 0.0) -> LineString:
    return LineString([(x0, y), (x1, y)])


def catalogue_of(entries: list[tuple[str, str, str, str]]) -> pd.DataFrame:
    """(cammino_id, nome, regioni, lunghezza dichiarata in km o "") -> catalogo come dedup.load_catalogue con le colonne in piu'."""
    frame = pd.DataFrame(entries, columns=["cammino_id", "nome", "regioni", "lunghezza_dichiarata_km"])
    frame["fonte"] = frame["cammino_id"].str.split(":").str[0]
    frame["stato_geometria"] = config.STATO_OK
    frame["chiave"] = frame["nome"].map(matching.name_key)
    return frame


def relations_of(entries: dict[str, tuple[str, str]]) -> pd.DataFrame:
    """id -> (nome, pilgrimage), come load_relations."""
    frame = pd.DataFrame.from_dict(entries, orient="index", columns=["nome", "pilgrimage"])
    frame["chiave"] = frame["nome"].map(matching.name_key)
    return frame


def regions() -> dict[str, shapely.Geometry]:
    polygons = {"lazio": LAZIO, "umbria": UMBRIA}
    for polygon in polygons.values():
        shapely.prepare(polygon)
    return polygons


def run_match(catalogue: pd.DataFrame, lines: dict, relations: pd.DataFrame, relation_lines: dict) -> tuple[pd.DataFrame, object]:
    def read(ids):
        return [(r, g) for r, g in relation_lines.items() if ids is None or r in ids]
    frame, osm_lines = osm_match.build_outputs(catalogue, lines, relations, read, regions())
    return frame.set_index("cammino_id"), osm_lines


# --- primitive ---

def test_quota_su_campione_e_passata_unica_coincidono_con_share_near() -> None:
    catalogue_line = LineString([(0, 0), (3000, 40), (6000, -30), (9000, 500)])
    relation = LineString([(2000, 20), (5000, 30), (8000, 2000)])
    sample = matching.sample_line(catalogue_line)
    assert matching.share_within(sample, relation, NEAR) == pytest.approx(matching.share_near(catalogue_line, relation, NEAR, STEP))
    shares = osm_match.osm_in_catalogue({"cdi:1": catalogue_line}, [("osm:r1", relation)])
    assert shares[("cdi:1", "osm:r1")] == pytest.approx(matching.share_near(relation, catalogue_line, NEAR, STEP))


def test_quote_osm_nel_catalogo_sommano_principale_e_variante_e_non_contano_due_volte_i_pezzi() -> None:
    catalogue_line = horizontal(0, 10_000)
    tratte = [("osm:r1", horizontal(0, 4000, y=10)), ("osm:r1", horizontal(0, 4000, y=3000))]
    shares = osm_match.osm_in_catalogue({"cdi:1": catalogue_line}, tratte)
    assert shares[("cdi:1", "osm:r1")] == pytest.approx(0.5, abs=0.01)
    assert ("cdi:1", "osm:r2") not in shares


# --- cammini con tracce ---

def test_nome_simile_e_quota_abbinano_nome_diverso_e_solo_geometria() -> None:
    catalogue = catalogue_of([("cdi:1", "Via Alfa", "lazio", "10"), ("mitur:2", "Cammino Gamma", "Lazio", "")])
    lines = {"cdi:1": horizontal(0, 10_000), "mitur:2": horizontal(0, 10_000, y=20_000)}
    relations = relations_of({
        "osm:r1": ("Via Alfa", "yes"), "osm:r2": ("Sentiero Beta", ""), "osm:r3": ("Via Alfa", ""),
        "osm:r4": ("Sentiero Delta", ""), "osm:r5": ("Cammino Gamma", ""),
    })
    relation_lines = {
        "osm:r1": horizontal(0, 10_000, y=20), "osm:r2": horizontal(0, 10_000, y=-10),
        "osm:r3": LineString([(5000, -5000), (5000, 5000)]),  # incrocia soltanto
        "osm:r4": horizontal(0, 10_000, y=20_010), "osm:r5": horizontal(50_000, 60_000),
    }
    frame, osm_lines = run_match(catalogue, lines, relations, relation_lines)
    alfa = frame.loc["cdi:1"]
    assert (alfa["esito"], alfa["osm_ids"], alfa["n_relazioni"]) == (config.ESITO_ABBINATO, "osm:r1", 1)
    assert alfa["quota_catalogo_in_osm"] == 1.0 and alfa["quota_osm_in_catalogo"] == 1.0
    assert alfa["pellegrinaggio_osm"] == config.SI and alfa["rapporto_dichiarata"] == pytest.approx(1.0)
    assert "osm:r2" in alfa["nota"] and "osm:r3" not in alfa["osm_ids"]
    gamma = frame.loc["mitur:2"]
    assert (gamma["esito"], gamma["osm_ids"]) == (config.ESITO_SOLO_GEOMETRIA, "osm:r4")
    assert gamma["pellegrinaggio_osm"] == "" and gamma["punteggio_nome"] < config.OSM_MATCH_NAME_MIN_SCORE
    assert list(osm_lines["cammino_id"]) == ["cdi:1"]  # solo geometria non e' un abbinamento


def test_nome_simile_con_quota_sotto_la_soglia_non_abbina() -> None:
    catalogue = catalogue_of([("cdi:1", "Via Alfa", "lazio", "")])
    lines = {"cdi:1": horizontal(0, 10_000)}
    relations = relations_of({"osm:r1": ("Via Alfa", "")})
    relation_lines = {"osm:r1": LineString([(0, 0), (6000, 0), (6000, 20_000)])}  # 6 km su 26, 60% del catalogo
    frame, osm_lines = run_match(catalogue, lines, relations, relation_lines)
    row = frame.loc["cdi:1"]
    assert row["esito"] == config.ESITO_NESSUNO and "osm:r1" in row["nota"] and osm_lines.empty


def test_relazione_contenuta_nella_madre_si_scarta_e_le_sole_tappe_si_uniscono() -> None:
    catalogue = catalogue_of([("cdi:1", "Via Alfa", "lazio", ""), ("cdi:2", "Cammino Zeta", "lazio", "")])
    lines = {"cdi:1": horizontal(0, 10_000), "cdi:2": horizontal(0, 10_000, y=30_000)}
    relations = relations_of({
        "osm:r1": ("Via Alfa", ""), "osm:r2": ("Via Alfa - Tappa 1", ""),
        "osm:r3": ("Cammino Zeta tappa 1", ""), "osm:r4": ("Cammino Zeta tappa 2", ""),
    })
    relation_lines = {
        "osm:r1": horizontal(0, 10_000), "osm:r2": horizontal(0, 5000, y=5),
        "osm:r3": horizontal(0, 5000, y=30_000), "osm:r4": horizontal(5000, 10_000, y=30_000),
    }
    frame, osm_lines = run_match(catalogue, lines, relations, relation_lines)
    assert frame.loc["cdi:1", "osm_ids"] == "osm:r1" and "scartate 1" in frame.loc["cdi:1", "nota"]
    zeta = frame.loc["cdi:2"]
    assert (zeta["esito"], zeta["osm_ids"], zeta["n_relazioni"]) == (config.ESITO_ABBINATO, "osm:r3|osm:r4", 2)
    assert zeta["quota_catalogo_in_osm"] == 1.0 and zeta["lunghezza_osm_m"] == pytest.approx(10_000)
    geometry = osm_lines.set_index("cammino_id").loc["cdi:2", "geometry"]
    assert geometry.geom_type == "MultiLineString" and geometry.length == pytest.approx(10_000)


# --- cammini senza tracce ---

def test_senza_tracce_conta_la_regione_dichiarata() -> None:
    catalogue = catalogue_of([("mitur:1", "Via Omega", "Lazio", "10")])
    relations = relations_of({"osm:r1": ("Via Omega", ""), "osm:r2": ("Via Omega", "")})
    relation_lines = {"osm:r1": horizontal(10_000, 20_000, y=50_000), "osm:r2": horizontal(140_000, 150_000, y=50_000)}
    frame, osm_lines = run_match(catalogue, {}, relations, relation_lines)
    row = frame.loc["mitur:1"]
    assert (row["esito"], row["osm_ids"]) == (config.ESITO_ABBINATO, "osm:r1")
    assert row["quota_nelle_regioni"] == 1.0 and row["rapporto_dichiarata"] == pytest.approx(1.0)
    assert pd.isna(row["quota_catalogo_in_osm"]) and pd.isna(row["lunghezza_catalogo_m"])
    assert list(osm_lines["osm_ids"]) == ["osm:r1"]


def test_senza_tracce_relazione_a_cavallo_conta_la_quota_nelle_regioni() -> None:
    catalogue = catalogue_of([("mitur:1", "Via Omega", "Lazio", ""), ("cdi:2", "Via Omega", "lazio|umbria", "")])
    relations = relations_of({"osm:r1": ("Via Omega", "")})
    relation_lines = {"osm:r1": horizontal(90_000, 110_000, y=50_000)}  # meta' nel Lazio, meta' in Umbria
    frame, _ = run_match(catalogue, {}, relations, relation_lines)
    assert frame.loc["mitur:1", "esito"] == config.ESITO_NESSUNO
    assert "nessuna nelle regioni dichiarate (la migliore osm:r1: quota 0.49" in frame.loc["mitur:1", "nota"]
    assert frame.loc["cdi:2", "esito"] == config.ESITO_ABBINATO
    assert frame.loc["cdi:2", "quota_nelle_regioni"] == pytest.approx(1.0, abs=0.001)


def test_senza_tracce_lunghezza_fuori_intervallo_non_abbina() -> None:
    catalogue = catalogue_of([("cdi:1", "Via Omega", "lazio", "50")])
    relations = relations_of({"osm:r1": ("Via Omega", "")})
    frame, _ = run_match(catalogue, {}, relations, {"osm:r1": horizontal(10_000, 20_000, y=50_000)})
    row = frame.loc["cdi:1"]
    assert row["esito"] == config.ESITO_NESSUNO and "osm:r1 0.20" in row["nota"]


def test_senza_tracce_piu_gruppi_distinti_sono_ambigui_e_la_lunghezza_li_separa() -> None:
    entries = {"osm:r1": ("Via Omega", ""), "osm:r2": ("Sentiero Via Omega", "")}
    relation_lines = {"osm:r1": horizontal(10_000, 20_000, y=50_000), "osm:r2": horizontal(10_000, 40_000, y=80_000)}
    catalogue = catalogue_of([("mitur:1", "Via Omega", "Lazio", ""), ("cdi:2", "Via Omega", "lazio", "30")])
    frame, osm_lines = run_match(catalogue, {}, relations_of(entries), relation_lines)
    ambiguous = frame.loc["mitur:1"]
    assert (ambiguous["esito"], ambiguous["osm_ids"], ambiguous["n_relazioni"]) == (config.ESITO_AMBIGUO, "osm:r1|osm:r2", 2)
    assert pd.isna(ambiguous["lunghezza_osm_m"])
    chosen = frame.loc["cdi:2"]  # 30 km dichiarati: osm:r1 (10 km, rapporto 0,33) fuori, osm:r2 (30 km) dentro
    assert (chosen["esito"], chosen["osm_ids"]) == (config.ESITO_ABBINATO, "osm:r2")
    assert list(osm_lines["cammino_id"]) == ["cdi:2"]


def test_senza_tracce_tappe_vicine_si_uniscono() -> None:
    catalogue = catalogue_of([("mitur:1", "Via Omega Alta", "Lazio", "20")])
    relations = relations_of({"osm:r1": ("Via Omega Alta tappa 1", "yes"), "osm:r2": ("Via Omega Alta tappa 2", "")})
    relation_lines = {"osm:r1": horizontal(10_000, 20_000, y=50_000), "osm:r2": horizontal(20_100, 30_000, y=50_000)}
    frame, _ = run_match(catalogue, {}, relations, relation_lines)
    row = frame.loc["mitur:1"]
    assert (row["esito"], row["osm_ids"], row["pellegrinaggio_osm"]) == (config.ESITO_ABBINATO, "osm:r1|osm:r2", config.SI)
    assert row["lunghezza_osm_m"] == pytest.approx(19_900)


def test_senza_tracce_una_sola_parola_contenuta_non_basta_due_si() -> None:
    assert osm_match.strict_name_match("maria", "maria")
    assert not osm_match.strict_name_match("maria", "ferrata maria andrea ferrari")
    assert not osm_match.strict_name_match("antico fede madonna pollino", "pollino")
    assert osm_match.strict_name_match("omega alta", "omega alta tappa 3")
    assert not osm_match.strict_name_match("omega alta", "omega bassa")  # nessuna delle due contiene l'altra
    catalogue = catalogue_of([("mitur:1", "Sentiero di Maria", "Lazio", "20"), ("mitur:2", "Via Omega Alta", "Lazio", "20")])
    relations = relations_of({"osm:r1": ("Ferrata Maria e Andrea Ferrari", ""), "osm:r2": ("Cammino Omega Alta", "")})
    relation_lines = {"osm:r1": horizontal(10_000, 30_000, y=50_000), "osm:r2": horizontal(10_000, 30_000, y=60_000)}
    frame, _ = run_match(catalogue, {}, relations, relation_lines)
    assert frame.loc["mitur:1", "esito"] == config.ESITO_NESSUNO and "scartate 1" in frame.loc["mitur:1", "nota"]
    assert (frame.loc["mitur:2", "esito"], frame.loc["mitur:2", "osm_ids"]) == (config.ESITO_ABBINATO, "osm:r2")


def test_senza_tracce_lo_stesso_nome_esclude_le_relazioni_che_lo_contengono_soltanto() -> None:
    catalogue = catalogue_of([("mitur:1", "Cammino di San Pellegrino", "Lazio", "100")])
    relations = relations_of({
        "osm:r1": ("Cammino di San Pellegrino", ""),
        "osm:r2": ("Cammino di Santa Giulia - Rifugio Santi San Pellegrino in Alpe", ""),
    })
    # la tappa dell'altro cammino arriva al luogo omonimo, a 100 m dalla fine della relazione con lo stesso nome
    relation_lines = {"osm:r1": horizontal(0, 80_000, y=50_000), "osm:r2": horizontal(80_100, 99_000, y=50_000)}
    frame, _ = run_match(catalogue, {}, relations, relation_lines)
    row = frame.loc["mitur:1"]
    assert (row["esito"], row["osm_ids"]) == (config.ESITO_ABBINATO, "osm:r1")
    assert "scartate perche' c'e' lo stesso nome 1" in row["nota"]


def test_con_tracce_la_parola_contenuta_resta_simile() -> None:
    catalogue = catalogue_of([("cdi:1", "Sentiero di Maria", "lazio", "")])
    relations = relations_of({"osm:r1": ("Ferrata Maria e Andrea Ferrari", "")})
    frame, _ = run_match(catalogue, {"cdi:1": horizontal(0, 10_000)}, relations, {"osm:r1": horizontal(0, 10_000, y=10)})
    assert frame.loc["cdi:1", "esito"] == config.ESITO_ABBINATO  # la geometria conferma: la regola stretta vale senza tracce


def test_senza_tracce_e_senza_lunghezza_dichiarata_serve_la_lunghezza_minima() -> None:
    short_m = config.OSM_MATCH_MIN_LENGTH_M - 100
    catalogue = catalogue_of([("mitur:1", "Via Omega", "Lazio", ""), ("mitur:2", "Via Sigma", "Lazio", "")])
    relations = relations_of({"osm:r1": ("Via Omega", ""), "osm:r2": ("Via Sigma", "")})
    relation_lines = {
        "osm:r1": horizontal(10_000, 10_000 + short_m, y=50_000),
        "osm:r2": horizontal(10_000, 10_000 + config.OSM_MATCH_MIN_LENGTH_M, y=70_000),
    }
    frame, _ = run_match(catalogue, {}, relations, relation_lines)
    assert frame.loc["mitur:1", "esito"] == config.ESITO_NESSUNO and f"osm:r1 {short_m} m" in frame.loc["mitur:1", "nota"]
    assert frame.loc["mitur:2", "esito"] == config.ESITO_ABBINATO


# --- cammini con tracce: lunghezza, un solo verso, contenitori, unione ---

ABBINATO, PARZIALE, CONTENITORE = config.ESITO_ABBINATO, config.ESITO_PARZIALE, config.ESITO_CONTENITORE


def one_tracked(relation_line: LineString, declared: str = "10", relation_name: str = "Via Alfa") -> tuple[pd.Series, object]:
    """Un cammino "Via Alfa" di 10 km lungo l'asse x con una relazione OSM di nome dato: (riga di abbinamenti.csv, geometrie)."""
    catalogue = catalogue_of([("cdi:1", "Via Alfa", "lazio", declared)])
    frame, osm_lines = run_match(
        catalogue, {"cdi:1": horizontal(0, 10_000)}, relations_of({"osm:r1": (relation_name, "")}), {"osm:r1": relation_line}
    )
    return frame.loc["cdi:1"], osm_lines


def test_con_tracce_basta_una_direzione_se_la_lunghezza_sta_nell_intervallo() -> None:
    piece, _ = one_tracked(horizontal(0, 6000))  # 6 km del cammino: catalogo in OSM 0,6, OSM nel catalogo 1,0, rapporto 0,6
    assert piece["esito"] == ABBINATO and piece["quota_catalogo_in_osm"] < 0.7 <= piece["quota_osm_in_catalogo"]
    longer, _ = one_tracked(LineString([(0, 0), (10_000, 0), (10_000, 4500)]))  # 14,5 km: tutto il cammino ma OSM nel catalogo 0,69
    assert longer["esito"] == ABBINATO and longer["quota_catalogo_in_osm"] == 1.0 and longer["quota_osm_in_catalogo"] < 0.7


def test_con_tracce_un_tratto_troppo_corto_e_parziale_e_non_e_un_abbinamento() -> None:
    row, osm_lines = one_tracked(horizontal(0, 4000))  # rapporto 0,4 sotto 0,5: copre un tratto del cammino
    assert (row["esito"], row["osm_ids"], row["pellegrinaggio_osm"]) == (PARZIALE, "osm:r1", "")
    assert row["quota_osm_in_catalogo"] == 1.0 and row["quota_catalogo_in_osm"] < 0.7
    assert "copre soltanto un tratto del cammino" in row["nota"] and "0.40" in row["nota"] and osm_lines.empty


def test_con_tracce_una_relazione_troppo_lunga_e_contenitore_e_non_e_un_abbinamento() -> None:
    row, osm_lines = one_tracked(LineString([(0, 0), (10_000, 0), (10_000, 6100)]))  # 16,1 km: rapporto 1,61 oltre 1,5
    assert (row["esito"], row["osm_ids"]) == (CONTENITORE, "osm:r1")
    assert row["quota_catalogo_in_osm"] == 1.0 and "contiene il cammino" in row["nota"] and "1.61" in row["nota"]
    assert osm_lines.empty  # solo l'abbinato arriva alla geometria OSM


@pytest.mark.parametrize(("extra_m", "expected"), [(5000, ABBINATO), (5100, CONTENITORE)])  # rapporto 1,5 incluso, 1,51 no
def test_con_tracce_il_limite_di_lunghezza_1_5_e_incluso(extra_m: int, expected: str) -> None:
    row, _ = one_tracked(LineString([(0, 0), (10_000, 0), (10_000, extra_m)]))
    assert row["esito"] == expected


@pytest.mark.parametrize(("length_m", "expected"), [(5000, ABBINATO), (4990, PARZIALE)])  # rapporto 0,5 incluso, 0,499 no
def test_con_tracce_il_limite_di_lunghezza_0_5_e_incluso(length_m: int, expected: str) -> None:
    row, _ = one_tracked(horizontal(0, length_m))
    assert row["esito"] == expected


@pytest.mark.parametrize(
    ("shares", "length_m", "expected"),
    [
        ((0.70, 0.70), 40_000, ABBINATO),  # entrambe le quote alla soglia (inclusa): la lunghezza non conta
        ((0.6999, 0.6999), 10_000, PARZIALE),  # nessuna delle due: la relazione non copre il cammino
        ((0.6999, 0.70), 10_000, ABBINATO),  # solo OSM nel catalogo, lunghezza nell'intervallo
        ((0.6999, 0.70), 4999, PARZIALE),  # ... ma 4999 m su 10 km sono sotto 0,5
        ((0.6999, 0.70), 5000, ABBINATO),
        ((0.70, 0.6999), 15_000, ABBINATO),  # solo catalogo in OSM, 1,5 incluso
        ((0.70, 0.6999), 15_001, CONTENITORE),
        ((0.70, 0.6999), 4000, PARZIALE),  # il cammino sta nella relazione ma questa non e' oltre 1,5: non e' un contenitore
    ],
)
def test_giudizio_dell_unione_alle_soglie_esatte(shares: tuple[float, float], length_m: float, expected: str) -> None:
    tracked = osm_match.track_of({"cammino_id": "cdi:1", "lunghezza_dichiarata_km": 10.0}, horizontal(0, 10_000))
    union = osm_match.Outcome("", ["osm:r1"], horizontal(0, length_m), "", shares)
    assert osm_match.union_verdict(tracked, union) == expected


def test_con_tracce_la_lunghezza_di_riferimento_e_la_dichiarata_e_senza_quella_del_tracciato() -> None:
    line = horizontal(0, 10_000)
    declared = osm_match.track_of({"cammino_id": "a", "lunghezza_dichiarata_km": 4.0}, line)
    assert (declared.reference_km, declared.reference_label) == (4.0, "dichiarata")
    for missing in (np.nan, 0.0):
        tracked = osm_match.track_of({"cammino_id": "a", "lunghezza_dichiarata_km": missing}, line)
        assert (tracked.reference_km, tracked.reference_label) == (10.0, "del tracciato")
    # linea del catalogo gonfiata dalle varianti (10 km su 4 dichiarati): conta la dichiarata, OSM di 4 km e' il cammino
    row, osm_lines = one_tracked(horizontal(0, 4000), declared="4")
    assert row["esito"] == ABBINATO and list(osm_lines["cammino_id"]) == ["cdi:1"]
    row, _ = one_tracked(horizontal(0, 4000), declared="")  # senza dichiarata il riferimento e' 10 km: 4 km sono un tratto
    assert row["esito"] == PARZIALE


def test_con_tracce_un_superpercorso_non_nasconde_la_relazione_giusta_che_contiene() -> None:
    catalogue = catalogue_of([("cdi:1", "Via Alfa", "lazio", "10")])
    relations = relations_of({"osm:r1": ("Via Alfa", ""), "osm:r2": ("Via Alfa completa", "")})
    relation_lines = {"osm:r1": horizontal(0, 10_000, y=5), "osm:r2": horizontal(-30_000, 40_000)}  # r2 contiene r1 ed e' 7 volte il cammino
    frame, osm_lines = run_match(catalogue, {"cdi:1": horizontal(0, 10_000)}, relations, relation_lines)
    row = frame.loc["cdi:1"]
    assert (row["esito"], row["osm_ids"]) == (ABBINATO, "osm:r1")  # prima dell'annidamento il superpercorso scartava r1 e vinceva lui
    assert "troppo lunghe per essere questo cammino 1" in row["nota"] and "contenute in un'altra e scartate 0" in row["nota"]
    assert osm_lines.iloc[0]["geometry"].length == pytest.approx(10_000)


def test_con_tracce_si_giudica_l_unione_delle_tappe_non_ogni_tappa() -> None:
    catalogue = catalogue_of([("cdi:1", "Via Alfa", "lazio", "10")])
    relations = relations_of({"osm:r1": ("Via Alfa tappa 1", ""), "osm:r2": ("Via Alfa tappa 2", "")})
    lines = {"cdi:1": horizontal(0, 10_000)}
    whole, _ = run_match(catalogue, lines, relations, {"osm:r1": horizontal(0, 5000, y=5), "osm:r2": horizontal(5000, 10_000, y=5)})
    assert (whole.loc["cdi:1", "esito"], whole.loc["cdi:1", "osm_ids"]) == (ABBINATO, "osm:r1|osm:r2")
    assert whole.loc["cdi:1", "quota_catalogo_in_osm"] == 1.0 and whole.loc["cdi:1", "quota_osm_in_catalogo"] == 1.0
    short, _ = run_match(catalogue, lines, relations, {"osm:r1": horizontal(0, 2000, y=5), "osm:r2": horizontal(2500, 4000, y=5)})
    assert short.loc["cdi:1", "esito"] == PARZIALE and short.loc["cdi:1", "lunghezza_osm_m"] == pytest.approx(3500)  # 0,35 del cammino


def test_con_tracce_tutte_le_candidate_troppo_lunghe_danno_contenitore_e_le_elenca() -> None:
    catalogue = catalogue_of([("cdi:1", "Via Alfa", "lazio", "10")])
    relations = relations_of({"osm:r1": ("Via Alfa", ""), "osm:r2": ("Via Alfa", "")})
    relation_lines = {"osm:r1": horizontal(-20_000, 30_000), "osm:r2": horizontal(-10_000, 20_000)}
    frame, osm_lines = run_match(catalogue, {"cdi:1": horizontal(0, 10_000)}, relations, relation_lines)
    row = frame.loc["cdi:1"]
    assert (row["esito"], row["osm_ids"], row["n_relazioni"]) == (CONTENITORE, "osm:r1|osm:r2", 2)
    assert osm_lines.empty


def test_con_tracce_tutte_le_candidate_troppo_lunghe_ma_con_poco_cammino_dentro_sono_parziale() -> None:
    """Una relazione di 24 km che va avanti e indietro su 6 km del cammino: lunga (2,4) ma contiene il 60% del cammino."""
    catalogue = catalogue_of([("cdi:1", "Via Alfa", "lazio", "10")])
    zigzag = LineString([(0, 0), (6000, 5), (0, 10), (6000, 15), (0, 20)])
    frame, osm_lines = run_match(catalogue, {"cdi:1": horizontal(0, 10_000)}, relations_of({"osm:r1": ("Via Alfa", "")}), {"osm:r1": zigzag})
    row = frame.loc["cdi:1"]
    assert (row["esito"], row["osm_ids"]) == (PARZIALE, "osm:r1") and row["quota_catalogo_in_osm"] < 0.7 <= row["quota_osm_in_catalogo"]
    assert "copre soltanto un tratto" in row["nota"] and "contiene il cammino" not in row["nota"] and osm_lines.empty


def test_riferimento_senza_lunghezza_positiva_non_solleva_e_vale_il_minimo_assoluto() -> None:
    point_line = LineString([(0, 0), (0, 0)])
    for declared in (np.nan, 0.0, -1.0):
        tracked = osm_match.track_of({"cammino_id": "a", "lunghezza_dichiarata_km": declared}, point_line)
        assert np.isnan(tracked.reference_km) and tracked.reference_label == "assente"
    assert np.isnan(osm_match.length_ratio(100.0, tracked.reference_km)) and np.isnan(osm_match.length_ratio(100.0, 0.0))
    short, long_ = config.OSM_MATCH_MIN_LENGTH_M - 1, config.OSM_MATCH_MIN_LENGTH_M
    for length_m, expected in ((short, PARZIALE), (long_, ABBINATO)):  # una sola quota: decide la lunghezza minima
        union = osm_match.Outcome("", ["osm:r1"], horizontal(0, length_m), "", (0.6999, 0.70))
        assert osm_match.union_verdict(tracked, union) == expected
    assert "senza lunghezza di riferimento" in osm_match._why_not(tracked, union, PARZIALE)


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_cammino_con_tracciato_di_lunghezza_zero_e_senza_dichiarata_non_solleva() -> None:
    catalogue = catalogue_of([("cdi:1", "Via Alfa", "lazio", "")])
    frame, _ = run_match(catalogue, {"cdi:1": LineString([(0, 0), (0, 0)])}, relations_of({"osm:r1": ("Via Alfa", "")}), {"osm:r1": horizontal(-30, 30)})
    assert frame.loc["cdi:1", "esito"] in {ABBINATO, PARZIALE, CONTENITORE, config.ESITO_NESSUNO}


# --- soglie esatte con campioni dati (n punti di peso uguale: 19 su 20 = 0,95 esatto) ---

def fixed_sample(near_points: int) -> matching.Sample:
    """20 punti di peso 1: `near_points` sul cammino (500, 0), gli altri lontani (500, 5000)."""
    return matching.Sample(shapely.points([(500, 0)] * near_points + [(500, 5000)] * (20 - near_points)), np.ones(20))


@pytest.mark.parametrize(("osm_in", "expected"), [(0.70, PARZIALE), (0.6999, config.ESITO_NESSUNO)])
def test_con_tracce_e_candidata_con_una_quota_70_inclusa_in_una_sola_direzione(osm_in: float, expected: str) -> None:
    relation = horizontal(0, 4000)  # catalogo in OSM 0,405: la quota in questo verso non basta, decide l'altro
    cammino = {"cammino_id": "cdi:1", "lunghezza_dichiarata_km": 10.0}
    ctx = osm_match.Context(relations_of({"osm:r1": ("Via Alfa", "")}), {"osm:r1": relation}, {("cdi:1", "osm:r1"): osm_in}, regions(), {})
    assert osm_match.match_tracked(cammino, horizontal(0, 10_000), {"osm:r1": 100.0}, ctx).esito == expected
    reverse = osm_match.Context(relations_of({"osm:r1": ("Via Alfa", "")}), {"osm:r1": relation}, {("cdi:1", "osm:r1"): 0.0}, regions(), {})
    assert osm_match.match_tracked(cammino, horizontal(0, 4000), {"osm:r1": 100.0}, reverse).esito == ABBINATO  # catalogo in OSM 1,0


@pytest.mark.parametrize(("near_points", "nested"), [(19, True), (18, False)])
def test_annidamento_95_incluso(near_points: int, nested: bool) -> None:
    geometries = {"osm:a": horizontal(0, 10_000), "osm:b": horizontal(0, 1000)}
    ctx = osm_match.Context(relations_of({}), geometries, {}, {}, {}, samples={"osm:b": fixed_sample(near_points)})
    expected = (["osm:a"], ["osm:b"]) if nested else (["osm:a", "osm:b"], [])
    assert osm_match.outermost(["osm:a", "osm:b"], ctx) == expected


@pytest.mark.parametrize(("near_points", "found"), [(19, ["osm:r1"]), (18, [])])
def test_solo_geometria_95_incluso(near_points: int, found: list[str]) -> None:
    tracked = osm_match.Tracked("cdi:1", fixed_sample(near_points), matching.LineIndex(horizontal(0, 10_000)), 10.0, "dichiarata")
    ctx = osm_match.Context(relations_of({}), {"osm:r1": horizontal(0, 10_000)}, {}, {}, {"cdi:1": ["osm:r1"]})
    assert osm_match.geometry_only_relations(tracked, ctx) == found


# --- pellegrinaggio: il silenzio non e' un no ---

@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["yes"], config.SI), (["Via di Francesco"], config.SI),  # qualunque valore tranne no / n e' un si, come tags.religioso_osm
        (["no"], config.NO), (["n"], config.NO),
        ([""], config.OSM_MATCH_PILGRIMAGE_SILENT), ([], config.OSM_MATCH_PILGRIMAGE_SILENT),
        (["", "no"], config.NO), (["", "yes"], config.SI), (["no", "yes"], config.SI), (["", ""], config.OSM_MATCH_PILGRIMAGE_SILENT),
    ],
)
def test_pellegrinaggio_distingue_il_silenzio_da_un_no_esplicito(values: list[str], expected: str) -> None:
    assert osm_match.pilgrimage_flag(values) == expected


def test_pellegrinaggio_in_abbinamenti_csv_per_silenzio_no_e_si() -> None:
    names = ["Via Alfa", "Via Beta", "Via Gamma", "Via Delta"]
    pilgrimages = ["", "no", "yes", "Via di Francesco"]
    catalogue = catalogue_of([(f"cdi:{n}", name, "lazio", "10") for n, name in enumerate(names)])
    lines = {f"cdi:{n}": horizontal(0, 10_000, y=20_000 * n) for n in range(4)}
    relations = relations_of({f"osm:r{n}": (name, pilgrimage) for n, (name, pilgrimage) in enumerate(zip(names, pilgrimages))})
    relation_lines = {f"osm:r{n}": horizontal(0, 10_000, y=20_000 * n + 10) for n in range(4)}
    frame, _ = run_match(catalogue, lines, relations, relation_lines)
    assert frame["pellegrinaggio_osm"].tolist() == [config.OSM_MATCH_PILGRIMAGE_SILENT, config.NO, config.SI, config.SI]
    assert (frame["esito"] == ABBINATO).all()


# --- cammini senza tracce: soglie esatte ---

def seeded_context(sample: matching.Sample) -> osm_match.Context:
    """Contesto con una sola relazione "Via Omega" il cui campione e' dato (per provare le soglie esatte)."""
    relations = relations_of({"osm:r1": ("Via Omega", "")})
    return osm_match.Context(relations, {"osm:r1": horizontal(0, 1000)}, {}, regions(), {}, samples={"osm:r1": sample})


@pytest.mark.parametrize(("last_weight", "kept"), [(1.0, ["osm:r1"]), (1.01, [])])  # 4 punti su 5 nel Lazio: 0,80 esatto, poi 0,798
def test_senza_tracce_la_quota_nelle_regioni_0_80_e_inclusa(last_weight: float, kept: list[str]) -> None:
    inside, outside = [(1000, 1000)] * 4, [(150_000, 1000)]  # il quinto punto e' in Umbria
    sample = matching.Sample(shapely.points(inside + outside), np.array([1.0, 1.0, 1.0, 1.0, last_weight]))
    cammino = catalogue_of([("mitur:1", "Via Omega", "Lazio", "10")]).iloc[0].to_dict()
    selection = osm_match.select_trackless(cammino, {"osm:r1": 100.0}, seeded_context(sample))
    assert selection.kept == kept


@pytest.mark.parametrize(("end_x", "expected"), [(109_990, ABBINATO), (110_030, config.ESITO_NESSUNO)])  # nel Lazio 0,8002 e 0,7994
def test_senza_tracce_la_quota_nelle_regioni_vicino_a_0_80_su_dati_veri(end_x: int, expected: str) -> None:
    catalogue = catalogue_of([("mitur:1", "Via Omega", "Lazio", str((end_x - 60_000) / 1000))])
    frame, _ = run_match(catalogue, {}, relations_of({"osm:r1": ("Via Omega", "")}), {"osm:r1": horizontal(60_000, end_x, y=50_000)})
    assert frame.loc["mitur:1", "esito"] == expected


@pytest.mark.parametrize(
    ("length_m", "expected"),
    [(5000, ABBINATO), (4990, config.ESITO_NESSUNO), (15_000, ABBINATO), (15_010, config.ESITO_NESSUNO)],  # intervallo 0,5-1,5 su 10 km
)
def test_senza_tracce_l_intervallo_di_lunghezza_e_lo_stesso_dei_cammini_con_tracce(length_m: int, expected: str) -> None:
    catalogue = catalogue_of([("mitur:1", "Via Omega", "Lazio", "10")])
    frame, _ = run_match(catalogue, {}, relations_of({"osm:r1": ("Via Omega", "")}), {"osm:r1": horizontal(10_000, 10_000 + length_m, y=50_000)})
    assert frame.loc["mitur:1", "esito"] == expected
    assert (config.OSM_MATCH_LENGTH_RATIO_MIN, config.OSM_MATCH_LENGTH_RATIO_MAX) == (0.5, 1.5)


# --- indici e campioni condivisi ---

def test_l_indice_di_ogni_relazione_si_costruisce_una_volta_anche_se_l_annidamento_la_interroga_molte(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[int] = []
    original = matching.LineIndex

    class CountingIndex(original):  # type: ignore[misc, valid-type]
        def __init__(self, line: object) -> None:
            built.append(1)
            super().__init__(line)

    monkeypatch.setattr(matching, "LineIndex", CountingIndex)
    geometries = {"osm:a": horizontal(0, 10_000), "osm:b": horizontal(0, 3000, y=5), "osm:c": horizontal(4000, 8000, y=5)}
    ctx = osm_match.Context(relations_of({}), geometries, {}, {}, {})
    assert osm_match.outermost(["osm:b", "osm:a", "osm:c"], ctx) == (["osm:a"], ["osm:c", "osm:b"])
    assert len(built) == 1  # due relazioni interrogano l'indice di osm:a: una costruzione sola
    assert ctx.index("osm:a") is ctx.index("osm:a") and ctx.sample("osm:b") is ctx.sample("osm:b")
    ctx.forget()  # a ogni cammino si svuotano: la memoria resta quella di un cammino
    assert not ctx.indexes and not ctx.samples


# --- geometria sostitutiva nella deduplica ---

def test_dedup_usa_la_geometria_osm_solo_per_i_cammini_senza_tracce(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "OSM_MATCH_DIR", tmp_path / "osm_match")
    catalogue = catalogue_of([("cdi:1", "Via Alfa", "lazio", ""), ("mitur:2", "Via Alfa", "Lazio", ""), ("mitur:3", "Via Beta", "Lazio", "")])
    catalogue.loc[catalogue["cammino_id"] != "cdi:1", "stato_geometria"] = config.STATO_ASSENTE
    lines = {"cdi:1": horizontal(0, 10_000)}
    relations = relations_of({"osm:r1": ("Via Alfa", ""), "osm:r2": ("Via Beta", "")})
    relation_lines = {"osm:r1": horizontal(0, 10_000, y=20), "osm:r2": horizontal(0, 10_000, y=40_000)}
    frame, osm_lines = run_match(catalogue, lines, relations, relation_lines)
    assert set(osm_lines["cammino_id"]) == {"cdi:1", "mitur:2", "mitur:3"}
    osm_match.write_outputs(frame.reset_index(), osm_lines)
    own_tratte = osm_match.gpd.GeoDataFrame(
        {"tratta_id": ["cdi:t1"], "cammino_id": ["cdi:1"], "tipo": [config.TIPO_TAPPA]}, geometry=[lines["cdi:1"]], crs=config.CRS_WORK,
    )
    substitutes = dedup.osm_substitute_tratte(catalogue["cammino_id"], own_tratte)
    assert sorted(substitutes["cammino_id"]) == ["mitur:2", "mitur:3"]  # cdi:1 ha tracce proprie
    assert set(substitutes["fonte"]) == {config.OSM_FONTE} and set(substitutes["tipo"]) == {config.TIPO_ALTRO}
    tratte = osm_match.gpd.GeoDataFrame(pd.concat([own_tratte, substitutes], ignore_index=True), crs=config.CRS_WORK)
    dedup_lines = dedup.cammino_lines(tratte)
    pairs = dedup.pair_table(catalogue, dedup_lines, config.DEDUP_NEAR_VALUES_M, frozenset(substitutes["cammino_id"]))
    coppie = dedup.coppie_frame(pairs).set_index(["cammino_a", "cammino_b"])
    alfa = coppie.loc[("cdi:1", "mitur:2")]
    assert (alfa["geometria_osm"], alfa["classe"]) == (config.DEDUP_OSM_SIDE_B, config.CLASSE_DUPLICATO)
    assert alfa["stato_b"] == config.STATO_ASSENTE  # lo stato resta quello del catalogo
    flags = pairs.set_index(["cammino_a", "cammino_b"])["geometria_osm"]
    assert flags[("mitur:2", "mitur:3")] == config.DEDUP_OSM_SIDE_BOTH
    assert flags[("cdi:1", "mitur:3")] == config.DEDUP_OSM_SIDE_B


def test_dedup_senza_file_osm_non_sostituisce(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "OSM_MATCH_DIR", tmp_path / "manca")
    tratte = osm_match.gpd.GeoDataFrame({"cammino_id": [], "tipo": []}, geometry=[], crs=config.CRS_WORK)
    assert dedup.osm_substitute_tratte(["mitur:1"], tratte).empty
    pairs = dedup.pair_table(catalogue_of([("cdi:1", "Via Alfa", "", ""), ("mitur:2", "Via Alfa", "", "")]), {}, (NEAR,))
    assert list(pairs["geometria_osm"]) == [config.DEDUP_OSM_SIDE_NONE]
    assert np.isnan(pairs[dedup.share_column("a_in_b", NEAR)]).all()
