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
    sample = osm_match.sample_line(catalogue_line)
    assert osm_match.share_of_sample(sample, relation) == pytest.approx(matching.share_near(catalogue_line, relation, NEAR, STEP))
    shares = osm_match.osm_in_catalogue({"cdi:1": catalogue_line}, [("osm:r1", relation)])
    assert shares[("cdi:1", "osm:r1")] == pytest.approx(matching.share_near(relation, catalogue_line, NEAR, STEP))


def test_pezzi_della_linea_coprono_la_linea_con_al_massimo_n_vertici() -> None:
    coords = [(x * 100.0, (x % 3) * 10.0) for x in range(50)]
    line = MultiLineString([coords, [(0, 5000), (100, 5000)]])
    pieces = osm_match.line_pieces(line, 8)
    assert all(len(piece.coords) <= 8 for piece in pieces)
    assert sum(piece.length for piece in pieces) == pytest.approx(line.length)
    assert shapely.union_all(pieces).equals(shapely.union_all(line))


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
