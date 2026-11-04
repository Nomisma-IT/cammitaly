"""Primitive di confronto: chiavi e punteggi dei nomi, campionamento e quota di una linea vicina a un'altra."""
import numpy as np
import pytest
import shapely
from rapidfuzz import fuzz
from shapely.geometry import LineString, MultiLineString

from cammitaly import config, matching

NEAR, STEP = config.DEDUP_NEAR_M, config.MATCH_SAMPLE_M


def horizontal(x0: float, x1: float, y: float = 0.0) -> LineString:
    return LineString([(x0, y), (x1, y)])


# --- nomi ---

def test_chiave_toglie_articoli_preposizioni_e_nomi_generici() -> None:
    assert matching.name_key("Cammino di Tindari") == "tindari"
    assert matching.name_key("Via dell'Essenza") == "essenza"
    assert matching.name_key("Il Cammino della Pace") == "pace"
    assert matching.name_key("Sentiero dei due conventi") == "due conventi"
    assert matching.name_key("Itinerario in Fiore") == "fiore"
    assert matching.name_key("Strade e percorsi del Nisi") == "nisi"


def test_i_santi_restano_nella_chiave_e_distinguono_cammini_diversi() -> None:
    assert matching.name_key("Cammino di San Francesco") == "san francesco"
    assert matching.name_key("Itinerario S. Maria in Fiore") == "s maria fiore"
    assert matching.name_key("Cammini di Santa Giulia") == "santa giulia"
    assert matching.name_key("Percorso SS. Trinita") == "ss trinita"
    carlo = matching.name_score(matching.name_key("Cammino di Carlo Magno"), matching.name_key("Cammino di San Carlo"))
    giovanni = matching.name_score(matching.name_key("Cammino di Padre Giovanni"), matching.name_key("Cammino di San Giovanni"))
    assert carlo == pytest.approx(80, abs=0.5) and giovanni < config.DEDUP_NAME_MIN_SCORE
    # "San Francesco" e "Francesco" restano compatibili: una chiave contenuta nell'altra vale 100
    assert matching.name_score("san francesco", "francesco") == 100


def test_chiave_tiene_le_parole_che_distinguono_percorsi_vicini() -> None:
    assert matching.name_key("Alta Via delle Grazie") == "alta grazie"
    assert matching.name_key("Via Romea Germanica Imperiale") == "romea germanica imperiale"


def test_chiave_normalizza_accenti_trattini_e_apostrofi_tipografici() -> None:
    dash, apostrophe = chr(0x2013), chr(0x2019)
    assert matching.name_key(f"Cammino 100 Torri {dash} Via degli Angeli") == "100 torri angeli"
    assert matching.name_key(f"Via dell{apostrophe}Essenza") == matching.name_key("Via dell'Essenza")
    assert matching.name_key("Via Francigena nel Sud") == "francigena sud"
    assert matching.name_key("FORLÌ") == "forli"


def test_chiave_senza_parole_utili_ricade_sul_nome_normalizzato() -> None:
    assert matching.name_key("La Via") == "la via"
    assert matching.name_key("Cammino") == "cammino"


def test_punteggio_cento_anche_se_una_chiave_e_contenuta_nell_altra() -> None:
    assert matching.name_score("francigena", "francigena sud") == 100
    assert matching.name_score("francesco", "francesco") == 100
    assert matching.name_score("tindari", "oropa") < 40


def test_punteggio_distingue_nomi_con_un_refuso_o_un_solo_carattere_diverso() -> None:
    assert 90 < matching.name_score("marino", "martino") < 95
    assert matching.name_score("normanno", "normanna") == pytest.approx(87.5)


def test_matrice_dei_punteggi_ha_la_forma_giusta_e_accetta_un_altro_punteggio() -> None:
    keys = ["francigena", "francigena sud", "oropa"]
    scores = matching.name_scores(keys, keys)
    assert scores.shape == (3, 3) and (np.diag(scores) == 100).all()
    assert scores[0, 1] == scores[1, 0] == 100 and scores[0, 2] < 40
    sorted_scores = matching.name_scores(keys, keys, fuzz.token_sort_ratio)
    assert sorted_scores[0, 1] < 100  # l'ordinamento dei token non ignora le parole in piu'


# --- linee ---

def test_campionamento_pesa_ogni_parte_con_la_sua_lunghezza() -> None:
    multi = MultiLineString([horizontal(0, 100).coords, horizontal(0, 35, y=500).coords])
    points, weights = matching.sample_points(multi, STEP)
    assert len(points) == len(weights) == 11 + 5  # ceil(100 / 10) + 1 e ceil(35 / 10) + 1
    assert weights.sum() == pytest.approx(135)
    xy = shapely.get_coordinates(points)
    assert xy[0].tolist() == [0, 0] and xy[10].tolist() == [100, 0] and xy[-1].tolist() == [35, 500]


def test_punti_vicini_alla_linea_rispettano_la_distanza_inclusa() -> None:
    line = horizontal(0, 1000)
    points = shapely.points([(500, 0), (500, 50), (500, 51), (-30, 40), (2000, 0)])
    assert matching.points_near(points, line, 50).tolist() == [True, True, False, True, False]
    assert matching.points_near(points[:0], line, 50).tolist() == []


def test_quota_identica_e_traslata_di_30_m_entro_50_m() -> None:
    line = horizontal(0, 5000)
    assert matching.overlap_share(line, line, NEAR, STEP) == pytest.approx(1.0)
    assert matching.overlap_share(line, horizontal(0, 5000, y=30), NEAR, STEP) == pytest.approx(1.0)


def test_quota_traslata_di_80_m_e_zero_a_50_m_ma_piena_a_100_m() -> None:
    line, shifted = horizontal(0, 5000), horizontal(0, 5000, y=80)
    assert matching.overlap_share(line, shifted, NEAR, STEP) == pytest.approx(0.0)
    assert matching.overlap_share(line, shifted, 100, STEP) == pytest.approx(1.0)


def test_quota_di_una_linea_corta_contenuta_in_una_lunga() -> None:
    long, short = horizontal(0, 10_000), horizontal(2000, 4000, y=10)
    assert matching.overlap_share(short, long, NEAR, STEP) == pytest.approx(1.0)
    assert matching.overlap_share(long, short, NEAR, STEP) == pytest.approx(1.0)  # conta sempre la piu' corta
    assert matching.share_near(short, long, NEAR, STEP) == pytest.approx(1.0)
    assert matching.share_near(long, short, NEAR, STEP) == pytest.approx(0.21, abs=0.01)  # 2000 m + 2 x 50 m su 10000


def test_quota_con_una_linea_a_piu_parti_e_con_soglia_variabile() -> None:
    reference = MultiLineString([horizontal(0, 1000).coords, horizontal(3000, 4000).coords])
    probe = horizontal(0, 4000)
    assert matching.share_near(probe, reference, NEAR, STEP) == pytest.approx(0.525, abs=0.01)  # 2 x (1000 + 50) m su 4000
    assert matching.share_near(probe, reference, 1000, STEP) == pytest.approx(1.0)


# --- una sola misura della quota: campione, indice, distanze ---

def test_indice_della_linea_da_lo_stesso_risultato_di_points_near_e_si_riusa() -> None:
    line = horizontal(0, 1000)
    points = shapely.points([(500, 0), (500, 50), (500, 51), (-30, 40), (2000, 0), (-51, 0)])
    index = matching.LineIndex(line)
    assert index.near(points, 50).tolist() == matching.points_near(points, line, 50).tolist() == [True, True, False, True, False, False]
    assert matching.points_near(points, index, 50).tolist() == index.near(points, 50).tolist()
    assert index.near(points, 900).tolist() == [True, True, True, True, False, True]  # lo stesso indice con un'altra distanza
    assert index.near(points[:0], 50).tolist() == []


def test_quota_del_campione_con_piu_distanze_coincide_con_share_near_a_ciascuna() -> None:
    line, other = horizontal(0, 5000), horizontal(1000, 4000, y=70)
    sample = matching.sample_line(line)
    nears = (100, 60, 25)
    shares = matching.shares_within(sample, other, nears)
    assert shares == [matching.share_near(line, other, near, STEP) for near in nears]
    assert shares[0] > 0.5 > shares[1] == shares[2] == 0.0  # a 70 m: dentro solo alla distanza maggiore
    assert matching.share_within(sample, matching.LineIndex(other), 100) == shares[0]


def test_le_distanze_della_quota_devono_essere_decrescenti() -> None:
    sample = matching.sample_line(horizontal(0, 1000))
    with pytest.raises(ValueError, match="decrescente"):
        matching.shares_within(sample, horizontal(0, 1000), (25, 50))


def test_quota_senza_punti_vicini_e_zero_anche_con_linea_lontana_dal_riquadro() -> None:
    sample = matching.sample_line(horizontal(0, 1000))
    assert matching.share_within(sample, horizontal(0, 1000, y=1_000_000), 50) == 0.0


def test_componenti_connesse_dagli_archi() -> None:
    groups = matching.connected_groups([("a", "b"), ("b", "c"), ("x", "y")])
    assert sorted(sorted(group) for group in groups) == [["a", "b", "c"], ["x", "y"]]
    assert matching.connected_groups([]) == []
    assert matching.connected_groups([("s", "s")]) == [["s"]]  # un nodo con un arco su se' stesso e' un gruppo


def test_pezzi_della_linea_coprono_la_linea_con_al_massimo_n_vertici() -> None:
    coords = [(x * 100.0, (x % 3) * 10.0) for x in range(50)]
    line = MultiLineString([coords, [(0, 5000), (100, 5000)]])
    pieces = matching.line_pieces(line, 8)
    assert all(len(piece.coords) <= 8 for piece in pieces)
    assert sum(piece.length for piece in pieces) == pytest.approx(line.length)
    assert shapely.union_all(pieces).equals(shapely.union_all(line))


def test_indice_sui_pezzi_coincide_con_la_distanza_esatta_su_linee_casuali(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pezzi di 4 vertici: molti giunti. Il risultato per ogni punto e' quello di shapely.distance <= near."""
    monkeypatch.setattr(config, "MATCH_PIECE_VERTICES", 4)
    rng = np.random.default_rng(1)
    for trial in range(12):
        coords = np.cumsum(rng.normal(0, 80, size=(int(rng.integers(2, 120)), 2)), axis=0)
        parts = [LineString(coords)] + ([LineString(coords[::-1] + 500)] if trial % 3 == 0 else [])
        geom = parts[0] if len(parts) == 1 else MultiLineString(parts)
        index = matching.LineIndex(geom)
        points = shapely.points(rng.uniform(coords.min() - 300, coords.max() + 300, size=(500, 2)))
        for near in (25, 50, 100):
            assert (index.near(points, near) == (shapely.distance(points, geom) <= near)).all()
