"""Coppie cammino-comune su geometrie sintetiche in metri: nessun file reale."""
import inspect

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import shapely
from shapely.geometry import LineString, MultiLineString, box

from cammitaly import calibration, config, crossing, pairs
from cammitaly.geometry import common

COMUNE = config.COMUNE_ID_COL
UNICO = config.PAIRS_UNICO_COL


def strip_comuni(codes: list[str]) -> gpd.GeoDataFrame:
    """Comuni larghi 800 m in fila lungo l'asse x (uno ogni 1000 m): la linea y = 0 ne attraversa il centro."""
    geoms = [box(i * 1000, -500, i * 1000 + 800, 500) for i in range(len(codes))]
    return gpd.GeoDataFrame({COMUNE: codes}, geometry=geoms, crs=config.CRS_WORK)


def track(tratta_id: str, cammino_id: str, geometry, tipo: str = "tappa", ordine: int | None = None,
          fonte: str = "cdi", mezzo: str = "piedi") -> dict[str, object]:
    # l'id di una traccia porta sempre il prefisso della sua fonte (da cui si ricava la fonte della coppia)
    tratta_id = tratta_id if ":" in tratta_id else f"{fonte}:{tratta_id}"
    return {
        "tratta_id": tratta_id, "cammino_id": cammino_id, "fonte": fonte, "ordine": ordine, "tipo": tipo,
        "lunghezza_m": geometry.length, "geometry": geometry, "mezzo": mezzo,
    }


def tracks_of(*rows: dict[str, object]) -> gpd.GeoDataFrame:
    frame = pd.DataFrame(list(rows))
    frame["ordine"] = frame["ordine"].astype("Int64")
    return gpd.GeoDataFrame(frame, geometry="geometry", crs=config.CRS_WORK)


def membri_of(**unici: list[str]) -> pd.DataFrame:
    """membri_of(u001=["a", "b"]): il primo membro e' il principale, gli altri duplicati."""
    rows = [
        {UNICO: unico, "cammino_id": cammino, "fonte": cammino.split(":")[0], "nome": cammino,
         "ruolo": config.RUOLO_PRINCIPALE if i == 0 else config.RUOLO_DUPLICATO}
        for unico, ids in unici.items() for i, cammino in enumerate(ids)
    ]
    return pd.DataFrame(rows)


def make_inputs(
    membri: pd.DataFrame, tracks: gpd.GeoDataFrame, declared: dict[str, float] | None = None,
    regions: dict[str, str] | None = None, labels: dict[str, dict[str, str]] | None = None,
    limbo: list[dict[str, str]] | None = None, abbinamenti: list[dict[str, str]] | None = None,
) -> pairs.Inputs:
    unici = pd.DataFrame([
        {UNICO: unico, "nome": f"Cammino {unico}", "fonti": "cdi", "n_membri": len(group), "membri": "|".join(group["cammino_id"]),
         "alias": "", "stato": "ok", "catena": "False"}
        for unico, group in membri.groupby(UNICO)
    ])
    catalogue = pd.DataFrame(
        {"regioni": [(regions or {}).get(c, "lazio") for c in membri["cammino_id"]],
         "dichiarata_km": [(declared or {}).get(c, np.nan) for c in membri["cammino_id"]]},
        index=pd.Index(membri["cammino_id"], name="cammino_id"),
    )
    default = {"mezzo": "piedi", "religioso": config.NO, "evidenza_religioso": config.EVIDENZA_SILENTE}
    label_rows = {c: {**default, **(labels or {}).get(c, {})} for c in [*membri["cammino_id"], *(labels or {})]}
    label_frame = pd.DataFrame.from_dict(label_rows, orient="index")
    for field in config.PAIRS_MULTI_FIELDS:
        label_frame[field] = label_frame.get(field, "")
    label_frame = label_frame.fillna("")
    return pairs.Inputs(
        unici=unici, membri=membri,
        limbo=pd.DataFrame(limbo or [], columns=["oggetto", "motivo", "dettaglio"]),
        catalogue=catalogue, tracks=tracks, labels=label_frame,
        abbinamenti=pd.DataFrame(abbinamenti or [], columns=["cammino_id", "esito", "osm_ids"]),
    )


def pairs_of(inputs: pairs.Inputs, comuni: gpd.GeoDataFrame, starts: dict[str, str] | None = None) -> pd.DataFrame:
    table = pairs.aggregate_pairs(pairs.cross_tracks(inputs.tracks, comuni), inputs.tracks, inputs.membri)
    spines = pairs.build_spines(table[UNICO].unique(), inputs)
    polygons = dict(zip(comuni[COMUNE], comuni.geometry))
    anchors = {unico: polygons[code] for unico, code in (starts or {}).items()}
    table["ordine"] = pairs.route_order(table, spines, polygons, anchors)
    return table.set_index([UNICO, COMUNE])


# --- coppie: mezzo, distanza, dentro, tracce condivise ---

def test_mezzo_distanza_e_dentro_della_coppia() -> None:
    comuni = strip_comuni(["A", "B", "C"])
    tracks = tracks_of(
        track("t1", "cdi:1", LineString([(100, 0), (700, 0)])),
        track("t2", "cdi:1", LineString([(100, 100), (700, 100)]), tipo="bici", mezzo="bici"),
        track("t3", "cdi:1", LineString([(1100, 0), (1700, 0)]), tipo="bici", mezzo="bici"),
        track("t4", "cdi:1", LineString([(1950, 0), (1960, 0)])),
        track("t5", "cdi:1", LineString([(1920, 0), (1930, 0)])),
        track("t6", "mitur:1", LineString([(200, 0), (600, 0)])),
    )
    table = pairs_of(make_inputs(membri_of(u001=["cdi:1", "mitur:1"]), tracks), comuni).loc["u001"]
    assert table.loc["A", "mezzo"] == config.MEZZO_ENTRAMBI and table.loc["A", "dentro"] and table.loc["A", "distanza_m"] == 0
    assert table.loc["B", "mezzo"] == config.MEZZO_BICI
    # C: due tracce a piedi, a 40 e a 70 m dal confine: vale la piu' vicina, e nessuna entra
    assert table.loc["C", "mezzo"] == config.MEZZO_PIEDI
    assert table.loc["C", "distanza_m"] == pytest.approx(40) and not table.loc["C", "dentro"]
    assert sorted(table.index) == ["A", "B", "C"]


def test_la_bici_fa_parte_dell_incrocio() -> None:
    comuni = strip_comuni(["A"])
    tracks = tracks_of(track("t1", "cdi:1", LineString([(100, 0), (700, 0)]), tipo="bici", mezzo="bici"))
    assert config.TIPO_BICI in config.CROSSING_TIPI
    table = pairs_of(make_inputs(membri_of(u001=["cdi:1"]), tracks), comuni)
    assert table.loc[("u001", "A"), "mezzo"] == config.MEZZO_BICI


def test_una_traccia_elencata_sotto_due_cammini_conta_per_entrambi() -> None:
    comuni = strip_comuni(["A"])
    line = LineString([(100, 0), (700, 0)])
    tracks = tracks_of(track("cdi:9", "cdi:1", line), track("cdi:9", "cdi:2", line))
    table = pairs_of(make_inputs(membri_of(u001=["cdi:1"], u002=["cdi:2"]), tracks), comuni)
    assert sorted(table.index) == [("u001", "A"), ("u002", "A")]


def test_traccia_di_un_cammino_che_non_e_un_membro_interrompe() -> None:
    comuni = strip_comuni(["A"])
    tracks = tracks_of(track("t1", "cdi:9", LineString([(100, 0), (700, 0)])))
    with pytest.raises(pairs.PairsError):
        pairs.aggregate_pairs(pairs.cross_tracks(tracks, comuni), tracks, membri_of(u001=["cdi:1"]))


# --- ordine lungo il percorso ---

def test_ordine_per_posizione_lungo_la_dorsale_non_per_codice() -> None:
    comuni = strip_comuni(["C3", "C2", "C1"])  # il codice cresce all'indietro
    tracks = tracks_of(
        track("t2", "cdi:1", LineString([(1500, 0), (2900, 0)]), ordine=2),
        track("t1", "cdi:1", LineString([(0, 0), (1500, 0)]), ordine=1),
    )
    table = pairs_of(make_inputs(membri_of(u001=["cdi:1"]), tracks), comuni).loc["u001"]
    assert table["ordine"].to_dict() == {"C3": 1, "C2": 2, "C1": 3}


def test_anello_il_comune_di_partenza_e_il_primo() -> None:
    loop = LineString([(0, 0), (3000, 0), (3000, 2000), (0, 2000), (0, 0)])
    comuni = gpd.GeoDataFrame(
        {COMUNE: ["9", "5", "1"]},
        geometry=[box(-200, -200, 500, 500), box(2800, 800, 3400, 1200), box(1200, 1800, 1800, 2400)],
        crs=config.CRS_WORK,
    )
    table = pairs_of(make_inputs(membri_of(u001=["cdi:1"]), tracks_of(track("t1", "cdi:1", loop))), comuni).loc["u001"]
    # "9" e' l'ultimo per codice ma la dorsale lo raggiunge per primo (e di nuovo alla fine)
    assert table["ordine"].to_dict() == {"9": 1, "5": 2, "1": 3}


def test_comune_raggiunto_solo_da_una_variante_va_al_punto_piu_vicino_della_dorsale() -> None:
    comuni = gpd.GeoDataFrame(
        {COMUNE: ["P", "V", "Q"]},
        geometry=[box(0, -500, 800, 500), box(1200, 1500, 1800, 2000), box(2000, -500, 2800, 500)],
        crs=config.CRS_WORK,
    )
    tracks = tracks_of(
        track("t1", "cdi:1", LineString([(0, 0), (3000, 0)]), ordine=1),
        track("t2", "cdi:1", LineString([(1500, 0), (1500, 1600)]), tipo="variante"),
    )
    table = pairs_of(make_inputs(membri_of(u001=["cdi:1"]), tracks), comuni).loc["u001"]
    assert table["ordine"].to_dict() == {"P": 1, "V": 2, "Q": 3}
    assert table.loc["V", "dentro"]  # il comune e' nell'elenco perche' la variante vi entra, non la dorsale


def test_senza_dorsale_l_ordine_e_per_codice() -> None:
    codes = pd.Series(["C3", "C1", "C2"])
    assert pairs.rank_comuni(codes, None).tolist() == [3, 1, 2]


@pytest.mark.parametrize("tipo", ["variante", "bici"])
def test_cammino_con_sole_varianti_o_bici_ha_dorsale_lunghezza_e_copertura(tipo: str) -> None:
    comuni = strip_comuni(["C3", "C2", "C1"])
    inputs = make_inputs(
        membri_of(u001=["cdi:1"]),
        tracks_of(track("t1", "cdi:1", LineString([(100, 0), (2700, 0)]), tipo=tipo)),
        declared={"cdi:1": 2.6},
    )
    table = pairs_of(inputs, comuni).loc["u001"]
    assert table["ordine"].to_dict() == {"C3": 1, "C2": 2, "C1": 3}  # lungo la traccia, non per codice
    assert pairs.build_spines(["u001"], inputs)["u001"]  # la dorsale esiste
    rows = {m: f for m, f in inputs.tracks.groupby("cammino_id")}
    result = pairs.geometry_columns(["cdi:1"], inputs.catalogue, rows)
    assert result["copertura"] == 1.0 and result["lunghezza_tracciata_km"] == 2.6


def test_copie_e_alternative_non_si_sommano_nella_lunghezza() -> None:
    main = LineString([(0, 0), (10_000, 0)])
    copy = LineString([(0, 20), (10_000, 20)])  # a 20 m: la stessa traccia in un altro file
    detour = LineString([(0, 30), (5000, 30), (5000, 3030)])  # metà in comune, poi 3 km nuovi
    assert pairs.union_length_m([copy, detour, main]) == pytest.approx(10_000 + 2980, abs=40)
    assert pairs.union_length_m([main]) == 10_000 and pairs.union_length_m([]) == 0
    inputs = make_inputs(
        membri_of(u001=["mitur:1"]),
        tracks_of(
            track("a", "mitur:1", main, tipo="altro", fonte="mitur"), track("b", "mitur:1", copy, tipo="altro", fonte="mitur"),
            track("c", "mitur:1", detour, tipo="altro", fonte="mitur"),
        ),
        declared={"mitur:1": 13.0},
    )
    rows = {m: f for m, f in inputs.tracks.groupby("cammino_id")}
    result = pairs.geometry_columns(["mitur:1"], inputs.catalogue, rows)
    assert result["lunghezza_tracciata_km"] == pytest.approx(13.0, abs=0.05) and result["copertura"] == pytest.approx(1.0, abs=0.01)


def test_tappe_consecutive_si_sommano_meno_la_giunzione() -> None:
    stages = [LineString([(i * 10_000, 0), ((i + 1) * 10_000, 0)]) for i in range(3)]
    union = pairs.union_length_m(stages)
    # i primi DEDUP_NEAR_M metri di ogni tappa dopo la prima toccano la precedente: 2 giunzioni
    assert union == pytest.approx(30_000 - 2 * config.DEDUP_NEAR_M, abs=40) and union < 30_000


def test_pari_posizione_si_ordina_per_codice() -> None:
    codes = pd.Series(["B", "A", "C"])
    assert pairs.rank_comuni(codes, np.array([5.0, 5.0, 1.0])).tolist() == [3, 2, 1]


def test_pari_posizione_prima_i_comuni_attraversati_poi_il_codice() -> None:
    codes = pd.Series(["A", "B", "C"])
    positions = np.array([5.0, 5.0, 5.0])
    entered = np.array([False, True, True])
    assert pairs.rank_comuni(codes, positions, entered).tolist() == [3, 1, 2]


def test_comune_attraversato_sta_al_primo_ingresso_non_alla_banda() -> None:
    # X e' attraversato dalla retta da x = 1000 (la banda di 100 m inizia a 900); Y non e' toccato dalla retta ma la sua
    # banda inizia a x ~ 929: Y e' raggiunto prima dell'ingresso in X, quindi lo precede
    comuni = gpd.GeoDataFrame(
        {COMUNE: ["X", "Y"]}, geometry=[box(1000, -300, 1500, 90), box(960, 95, 1300, 500)], crs=config.CRS_WORK,
    )
    tracks = tracks_of(track("t1", "cdi:1", LineString([(0, 0), (3000, 0)]), ordine=1))
    table = pairs_of(make_inputs(membri_of(u001=["cdi:1"]), tracks), comuni).loc["u001"]
    assert table["dentro"].to_dict() == {"X": True, "Y": False}
    assert table["ordine"].to_dict() == {"Y": 1, "X": 2}
    spine = pairs.sample_spine([tracks.geometry.iloc[0]])
    positions, entered = pairs.route_positions(spine, comuni.geometry.to_numpy())
    assert entered.tolist() == [True, False]
    assert positions[0] == pytest.approx(1000, abs=config.PAIRS_ROUTE_STEP_M) and positions[1] < positions[0]


# --- verso delle tappe ---

def test_tappe_disegnate_al_contrario_si_orientano_lungo_il_percorso() -> None:
    forward = [LineString([(0, 0), (1000, 0)]), LineString([(1000, 0), (2000, 0)]), LineString([(2000, 0), (3000, 0)])]
    drawn = [shapely.reverse(forward[0]), forward[1], shapely.reverse(forward[2])]  # tappe 1 e 3 disegnate da est a ovest
    stages = pairs.spine_stages(drawn)
    assert [(s[0].coords[0][0], s[-1].coords[-1][0]) for s in stages] == [(0, 1000), (1000, 2000), (2000, 3000)]


def test_tappa_a_piu_parti_disegnata_al_contrario_si_rovescia_intera() -> None:
    # Tappa 2 con un vuoto: disegnata da x = 3900 a x = 1100 (prima la parte finale); la tappa 1 finisce a x = 1000
    first = LineString([(0, 0), (1000, 0)])
    second = MultiLineString([[(3900, 0), (3000, 0)], [(2000, 0), (1100, 0)]])
    stages = pairs.spine_stages([first, second])
    assert len(stages) == 2 and len(stages[1]) == 2
    assert [line.coords[0][0] for line in stages[1]] == [1100, 3000]  # parti rovesciate e in ordine di percorso
    assert stages[1][-1].coords[-1][0] == 3900


def test_ordine_dei_comuni_con_tappe_disegnate_al_contrario() -> None:
    comuni = strip_comuni(["C3", "C2", "C1"])
    tracks = tracks_of(
        track("t1", "cdi:1", LineString([(1500, 0), (0, 0)]), ordine=1),  # da est a ovest
        track("t2", "cdi:1", LineString([(1500, 0), (2900, 0)]), ordine=2),
    )
    table = pairs_of(make_inputs(membri_of(u001=["cdi:1"]), tracks), comuni).loc["u001"]
    assert table["ordine"].to_dict() == {"C3": 1, "C2": 2, "C1": 3}


def test_una_sola_unita_resta_com_e() -> None:
    line = LineString([(5, 0), (0, 0)])
    assert pairs.orient_chain([[line]])[0][0].equals(line)
    assert pairs.orient_chain([]) == []


# --- senza tappe: le tracce altro ---

def test_senza_tappe_le_tracce_altro_fanno_dorsale_lunghezza_e_copertura() -> None:
    comuni = strip_comuni(["C3", "C2", "C1"])
    inputs = make_inputs(
        membri_of(u001=["mitur:1"]),
        tracks_of(track("mitur:1:intero", "mitur:1", LineString([(100, 0), (2700, 0)]), tipo="altro", fonte="mitur")),
        declared={"mitur:1": 2.6},
    )
    table = pairs_of(inputs, comuni).loc["u001"]
    assert table["ordine"].to_dict() == {"C3": 1, "C2": 2, "C1": 3}  # per posizione sulla traccia, non per codice
    assert pairs.build_spines(["u001"], inputs)["u001"]  # la dorsale esiste
    rows = {m: f for m, f in inputs.tracks.groupby("cammino_id")}
    result = pairs.geometry_columns(["mitur:1"], inputs.catalogue, rows)
    assert result["copertura"] == 1.0 and result["lunghezza_tracciata_km"] == 2.6


def test_con_tappe_nel_cammino_le_tracce_altro_non_entrano_nella_lunghezza() -> None:
    inputs = make_inputs(
        membri_of(u001=["cdi:1", "mitur:2"]),
        tracks_of(
            track("t1", "cdi:1", LineString([(0, 0), (50_000, 0)])),
            track("t2", "mitur:2", LineString([(0, 9), (90_000, 9)]), tipo="altro", fonte="mitur"),
        ),
        declared={"cdi:1": 100.0, "mitur:2": 100.0},
    )
    rows = {m: f for m, f in inputs.tracks.groupby("cammino_id")}
    result = pairs.geometry_columns(["cdi:1", "mitur:2"], inputs.catalogue, rows)
    assert result["copertura"] == 0.5 and result["lunghezza_tracciata_km"] == 50.0


def test_la_scelta_della_dorsale_segue_la_catena() -> None:
    def line(i: int) -> LineString:
        return LineString([(i, 0), (i + 1, 0)])

    def rows(*items: dict[str, object]) -> pd.DataFrame:
        return tracks_of(*items)

    long_other = rows(track("o1", "cdi:2", LineString([(0, 0), (900, 0)])))
    principal_stage = rows(track("p1", "cdi:1", line(7)))
    standin = rows(track("osm:r1", "cdi:2", line(5), tipo="altro", fonte="osm"))
    # il principale ha tappe: vince anche se un altro membro e' piu' lungo
    chosen = pairs.spine_geometries(["cdi:1", "cdi:2"], {"cdi:1": principal_stage, "cdi:2": long_other})
    assert [g.equals(line(7)) for g in chosen] == [True]
    # il principale non ha tappe: il membro con tappe piu' lungo
    only_variant = rows(track("p2", "cdi:1", line(8), tipo="variante"))
    chosen = pairs.spine_geometries(["cdi:1", "cdi:2"], {"cdi:1": only_variant, "cdi:2": long_other})
    assert chosen[0].length == 900
    # nessuna tappa: le tracce altro, prima del principale e poi del membro con altro piu' lungo
    principal_other = rows(track("a1", "cdi:1", line(6), tipo="altro"))
    long_other_altro = rows(track("a2", "cdi:2", LineString([(0, 0), (900, 0)]), tipo="altro"))
    chosen = pairs.spine_geometries(["cdi:1", "cdi:2"], {"cdi:1": principal_other, "cdi:2": long_other_altro})
    assert [g.equals(line(6)) for g in chosen] == [True]
    chosen = pairs.spine_geometries(["cdi:1", "cdi:2"], {"cdi:1": only_variant, "cdi:2": long_other_altro})
    assert chosen[0].length == 900
    # una tappa di un altro membro vince sull'altro del principale
    chosen = pairs.spine_geometries(["cdi:1", "cdi:2"], {"cdi:1": principal_other, "cdi:2": long_other})
    assert chosen[0].length == 900 and chosen[0].equals(long_other.geometry.iloc[0])
    # le tracce altro di catalogo vincono sul sostituto OSM; senza altro: il sostituto; senza nulla: niente dorsale
    chosen = pairs.spine_geometries(["cdi:1", "cdi:2"], {"cdi:1": principal_other, "cdi:2": standin})
    assert [g.equals(line(6)) for g in chosen] == [True]
    assert pairs.spine_geometries(["cdi:1", "cdi:2"], {"cdi:2": standin})[0].equals(line(5))
    # qualunque tipo (varianti, bici) vince sul sostituto OSM; senza nessuna traccia: niente dorsale
    chosen = pairs.spine_geometries(["cdi:1", "cdi:2"], {"cdi:1": only_variant, "cdi:2": standin})
    assert [g.equals(line(8)) for g in chosen] == [True]
    assert pairs.spine_geometries(["cdi:1", "cdi:2"], {"cdi:1": only_variant})[0].equals(line(8))
    assert pairs.spine_geometries(["cdi:1", "cdi:2"], {}) == []


def test_tappe_per_ordine_poi_senza_ordine_in_ordine_naturale() -> None:
    def line(i: int) -> LineString:
        return LineString([(i, 0), (i + 1, 0)])

    stages = tracks_of(
        track("mitur:s:t 10", "mitur:1", line(3), fonte="mitur"),
        track("mitur:s:t 2", "mitur:1", line(2), fonte="mitur"),
        track("mitur:s:z", "mitur:1", line(1), fonte="mitur", ordine=1),
    )
    chosen = pairs.spine_geometries(["mitur:1"], {"mitur:1": stages})
    assert [g.coords[0][0] for g in chosen] == [1, 2, 3]
    assert pairs.natural_key("tappa 2") < pairs.natural_key("tappa 10")


def test_parti_nell_ordine_memorizzato_e_vuoti_non_contano() -> None:
    # Due parti lontane, la prima verso ovest: linemerge le riordinerebbe per posizione, qui restano come sono
    westward = MultiLineString([[(6000, 0), (5000, 0)], [(1000, 0), (0, 0)]])
    parts = pairs.merge_in_stored_order(westward)
    assert [p.coords[0][0] for p in parts] == [6000, 1000]
    spine = pairs.sample_spine([westward])
    assert spine is not None and spine.starts[-1] == pytest.approx(1000 + 990)  # il vuoto tra le parti non somma
    near_end = np.array([box(-100, -50, 100, 50)], dtype=object)
    # la seconda parte corre da x = 1000 a x = 0 e finisce dentro il comune, che occupa x <= 100: ingresso a 1000 + 900
    positions, entered = pairs.route_positions(spine, near_end)
    assert entered[0] and positions[0] == pytest.approx(1000 + 900, abs=config.PAIRS_ROUTE_STEP_M)


def test_parti_che_si_toccano_si_uniscono_tenendo_posto_e_verso() -> None:
    # P (500 -> 0) e R (1000 -> 500) si toccano; Q e' lontana. Unite: 1000 -> 0 al posto di P, poi Q col suo verso
    multi = MultiLineString([[(500, 0), (0, 0)], [(3000, 0), (2000, 0)], [(1000, 0), (500, 0)]])
    parts = pairs.merge_in_stored_order(multi)
    assert len(parts) == 2
    assert parts[0].coords[0][0] > parts[0].coords[-1][0] and parts[0].length == pytest.approx(1000)
    assert parts[1].coords[0][0] == 3000


def test_errore_di_posizione_entro_un_passo() -> None:
    spine = pairs.sample_spine([LineString([(0, 0), (3000, 0)])])
    polygon = np.array([box(1200, 50, 1300, 400)], dtype=object)
    exact = 1200 - np.sqrt(100**2 - 50**2)  # primo punto della retta a 100 m dallo spigolo (1200, 50)
    positions, entered = pairs.route_positions(spine, polygon)
    assert not entered[0]  # a 50 m dalla retta: solo vicino
    assert 0 <= exact - positions[0] <= config.PAIRS_ROUTE_STEP_M  # la posizione trovata e' l'inizio del tratto che contiene quella vera


# --- cammini: copertura, fonte, note, etichette ---

def test_copertura_e_lunghezza_tracciata_del_membro_migliore() -> None:
    inputs = make_inputs(
        membri_of(u001=["cdi:1", "mitur:2"]),
        tracks_of(
            track("t1", "cdi:1", LineString([(0, 0), (50_000, 0)])),
            track("t2", "cdi:1", LineString([(0, 5), (1_000, 5)]), tipo="variante"),  # la variante non e' tappa
            track("t3", "mitur:2", LineString([(0, 9), (80_340, 9)]), fonte="mitur"),
        ),
        declared={"cdi:1": 100.0, "mitur:2": 100.0},
    )
    rows = {m: f for m, f in inputs.tracks.groupby("cammino_id")}
    result = pairs.geometry_columns(["cdi:1", "mitur:2"], inputs.catalogue, rows)
    assert result["copertura"] == 0.80 and result["lunghezza_tracciata_km"] == 80.3
    assert result["lunghezza_dichiarata_km"] == 100.0 and "fonte_geometria" not in result  # la fonte e' per coppia


def test_copertura_non_supera_uno_e_a_pari_quota_vince_il_membro_piu_vicino_al_dichiarato() -> None:
    def columns(declared: dict[str, float], lengths_km: dict[str, float]) -> dict[str, object]:
        tracks = tracks_of(*(track(f"t{i}", m, LineString([(0, 0), (km * 1000, 0)])) for i, (m, km) in enumerate(lengths_km.items())))
        inputs = make_inputs(membri_of(u001=list(lengths_km)), tracks, declared=declared)
        rows = {m: f for m, f in inputs.tracks.groupby("cammino_id")}
        return pairs.geometry_columns(list(lengths_km), inputs.catalogue, rows)

    # un solo tracciato piu' lungo della scheda: copertura 1, il disaccordo resta nelle due lunghezze
    only = columns({"cdi:1": 27.0}, {"cdi:1": 113.3})
    assert only["copertura"] == 1.0 and only["lunghezza_tracciata_km"] == 113.3 and only["lunghezza_dichiarata_km"] == 27.0
    # due membri con quota 1 dopo il tetto: vince quello con la lunghezza piu' vicina alla propria dichiarata
    tie = columns({"cdi:1": 100.0, "cdi:2": 100.0}, {"cdi:1": 120.0, "cdi:2": 100.0})
    assert tie["copertura"] == 1.0 and tie["lunghezza_tracciata_km"] == 100.0
    # una quota piu' bassa non vince solo perche' e' vicina: 1.66 (tetto 1) batte 0.99
    higher = columns({"cdi:1": 110.0, "cdi:2": 107.2}, {"cdi:1": 182.3, "cdi:2": 106.1})
    assert higher["copertura"] == 1.0 and higher["lunghezza_tracciata_km"] == 182.3
    partial = columns({"cdi:1": 100.0, "cdi:2": 100.0}, {"cdi:1": 60.0, "cdi:2": 80.0})
    assert partial["copertura"] == 0.8 and partial["lunghezza_tracciata_km"] == 80.0


def test_senza_lunghezza_dichiarata_copertura_vuota_e_tracciata_massima() -> None:
    inputs = make_inputs(
        membri_of(u001=["cdi:1", "mitur:2"]),
        tracks_of(
            track("t1", "cdi:1", LineString([(0, 0), (12_000, 0)])),
            track("t3", "mitur:2", LineString([(0, 9), (30_000, 9)]), fonte="mitur"),
        ),
    )
    rows = {m: f for m, f in inputs.tracks.groupby("cammino_id")}
    result = pairs.geometry_columns(["cdi:1", "mitur:2"], inputs.catalogue, rows)
    assert np.isnan(result["copertura"]) and np.isnan(result["lunghezza_dichiarata_km"])
    assert result["lunghezza_tracciata_km"] == 30.0


def test_lunghezza_dichiarata_massima_ignora_i_membri_senza_dato_in_qualunque_ordine() -> None:
    inputs = make_inputs(membri_of(u001=["cdi:2", "cdi:1"]), tracks_of(track("t1", "cdi:1", LineString([(0, 0), (1, 0)]))),
                         declared={"cdi:1": 40.0})
    result = pairs.geometry_columns(["cdi:2", "cdi:1"], inputs.catalogue, {})
    assert result["lunghezza_dichiarata_km"] == 40.0


def test_traccia_sostitutiva_osm_misura_lunghezza_e_copertura_e_senza_tracce_niente() -> None:
    standin = tracks_of(track("osm:r1", "cdi:1", LineString([(0, 0), (20_000, 0)]), tipo="altro", fonte="osm"))
    inputs = make_inputs(membri_of(u001=["cdi:1"], u002=["cdi:2"]), standin, declared={"cdi:1": 40.0})
    rows = {m: f for m, f in inputs.tracks.groupby("cammino_id")}
    with_standin = pairs.geometry_columns(["cdi:1"], inputs.catalogue, rows)
    assert with_standin["copertura"] == 0.5 and with_standin["lunghezza_tracciata_km"] == 20.0
    nothing = pairs.geometry_columns(["cdi:2"], inputs.catalogue, rows)
    assert np.isnan(nothing["lunghezza_tracciata_km"])


def test_cammino_senza_geometria_resta_con_zero_comuni_e_nota() -> None:
    inputs = make_inputs(
        membri_of(u001=["cdi:1"], u002=["cdi:2"]),
        tracks_of(track("t1", "cdi:1", LineString([(0, 0), (1000, 0)]))),
        labels={"cdi:2": {"mezzo": config.MEZZO_ENTRAMBI}},
        regions={"cdi:1": "Lazio", "cdi:2": "valle-daosta"},
    )
    names = {"lazio": "Lazio", "valle-daosta": "Valle d'Aosta/Vallée d'Aoste"}
    cammini = pairs.build_cammini(inputs, {"u001": 2}, names, {"u001": "cdi"}).set_index(UNICO)
    assert list(cammini.index) == ["u001", "u002"] and list(cammini.columns) == list(config.PAIRS_CAMMINI_FIELDS)[1:]
    empty = cammini.loc["u002"]
    assert empty["n_comuni"] == 0 and empty["fonte_geometria"] == config.FONTE_GEOMETRIA_NESSUNA
    assert cammini.loc["u001", "fonte_geometria"] == "cdi"
    assert empty["nota"] == config.PAIRS_NOTA_NESSUNA_GEOMETRIA
    assert empty["mezzo"] == config.MEZZO_ENTRAMBI  # senza tracce vale l'etichetta del membro
    assert empty["regioni"] == "Valle d'Aosta/Vallée d'Aoste" and cammini.loc["u001", "regioni"] == "Lazio"
    assert cammini.loc["u001", "nota"] == "" and cammini.loc["u001", "n_comuni"] == 2


def test_cammino_senza_tracce_non_ha_mezzo_e_i_vuoti_non_contano() -> None:
    inputs = make_inputs(
        membri_of(u001=["cdi:1"], u002=["cdi:2"], u003=["cdi:3", "cdi:4"]),
        tracks_of(
            track("t1", "cdi:1", LineString([(0, 0), (1000, 0)])),
            track("t4", "cdi:4", LineString([(0, 0), (1000, 0)]), tipo="bici", mezzo="bici"),
        ),
        labels={"cdi:2": {"mezzo": config.MEZZO_NESSUNO}, "cdi:3": {"mezzo": config.MEZZO_NESSUNO}},
    )
    names = {"lazio": "Lazio"}
    cammini = pairs.build_cammini(inputs, {"u001": 2, "u003": 2}, names, {"u001": "cdi", "u003": "cdi"}).set_index(UNICO)
    assert cammini.loc["u002", "mezzo"] == config.MEZZO_NESSUNO  # i 21 cammini senza tracce: nessun dato, nessun "a piedi"
    assert cammini.loc["u001", "mezzo"] == config.MEZZO_PIEDI
    assert cammini.loc["u003", "mezzo"] == config.MEZZO_BICI  # il membro senza tracce e senza mezzo non pesa


def test_nota_del_limbo_cita_l_altro_cammino_e_si_somma_alla_geometria_assente() -> None:
    limbo = [
        {"oggetto": "cdi:1|cdi:2", "motivo": config.MOTIVO_NOME_SI_GEOMETRIA_ASSENTE, "dettaglio": ""},
        {"oggetto": "cdi:2|cdi:3", "motivo": config.MOTIVO_NOME_SI_TRACCIATO_INTERMEDIO, "dettaglio": ""},
        {"oggetto": "cdi:4|cdi:5", "motivo": config.MOTIVO_NOME_SI_GEOMETRIA_ASSENTE, "dettaglio": ""},  # stesso distinto
    ]
    inputs = make_inputs(
        membri_of(u001=["cdi:1"], u002=["cdi:2"], u003=["cdi:3"], u004=["cdi:4", "cdi:5"]), tracks_of(
            track("t1", "cdi:1", LineString([(0, 0), (1000, 0)])), track("t3", "cdi:3", LineString([(0, 0), (1000, 0)])),
        ),
        limbo=limbo,
    )
    notes = pairs.limbo_notes(inputs)
    assert notes["u001"] == ["possibile duplicato di Cammino u002 (u002): manca la geometria per il confronto"]
    assert notes["u003"] == [f"possibile duplicato di Cammino u002 (u002): {config.MOTIVO_NOME_SI_TRACCIATO_INTERMEDIO}"]
    assert "u004" not in notes
    cammini = pairs.build_cammini(inputs, {}, {"lazio": "Lazio"}, {}).set_index(UNICO)
    assert cammini.loc["u002", "nota"] == "; ".join([
        "possibile duplicato di Cammino u001 (u001): manca la geometria per il confronto",
        f"possibile duplicato di Cammino u003 (u003): {config.MOTIVO_NOME_SI_TRACCIATO_INTERMEDIO}",
        config.PAIRS_NOTA_NESSUNA_GEOMETRIA,
    ])


def test_evidenza_solo_dalle_righe_si_altrimenti_nessuna_fonte() -> None:
    labels = pd.DataFrame(
        {"religioso": ["si", "no", "no"], "evidenza_religioso": ["Catalogo", config.EVIDENZA_SILENTE, config.EVIDENZA_SILENTE],
         "mezzo": ["piedi"] * 3, **{field: ["", "", ""] for field in config.PAIRS_MULTI_FIELDS}},
        index=["mitur:1", "cdi:1", "cdi:2"],
    )
    mixed = pairs.label_columns(["mitur:1", "cdi:1"], labels, {})
    assert mixed["religioso"] == config.SI and mixed["evidenza_religioso"] == "Catalogo"
    none = pairs.label_columns(["cdi:1", "cdi:2"], labels, {})
    assert none["religioso"] == config.NO and none["evidenza_religioso"] == config.EVIDENZA_SILENTE


def test_etichette_osm_abbinate_contano_e_le_multivalore_si_uniscono() -> None:
    labels = pd.DataFrame(
        {"religioso": ["no", "si", "no"], "evidenza_religioso": [config.EVIDENZA_SILENTE, "pilgrimage=yes", config.EVIDENZA_SILENTE],
         "mezzo": ["piedi"] * 3, "tema": ["storico", "", "naturalistico|storico"],
         **{field: ["", "", ""] for field in config.PAIRS_MULTI_FIELDS if field != "tema"}},
        index=["cdi:1", "osm:r1", "osm:r2"],
    )
    abbinamenti = pd.DataFrame([
        {"cammino_id": "cdi:1", "esito": config.ESITO_ABBINATO, "osm_ids": "osm:r1|osm:r2"},
        {"cammino_id": "cdi:9", "esito": config.ESITO_NESSUNO, "osm_ids": "osm:r1"},
    ])
    counted = pairs.osm_relations_counted(abbinamenti)
    assert counted == {"cdi:1": ["osm:r1", "osm:r2"]}
    result = pairs.label_columns(["cdi:1"], labels, counted)
    assert result["religioso"] == config.SI and result["evidenza_religioso"] == "pilgrimage=yes"
    assert result["tema"] == "naturalistico|storico"


def test_etichetta_mancante_interrompe() -> None:
    labels = pd.DataFrame({"religioso": ["no"], "evidenza_religioso": [""], "mezzo": ["piedi"]}, index=["cdi:1"])
    with pytest.raises(pairs.PairsError):
        pairs.label_columns(["cdi:1", "cdi:2"], labels, {})


def test_mezzo_della_traccia_sostitutiva_combina_le_relazioni() -> None:
    labels = pd.DataFrame([
        {"tratta_id": "cdi:5", "cammino_id": "cdi:1", "fonte": "cdi", "mezzo": "piedi"},
        {"tratta_id": "osm:r1", "cammino_id": "osm:r1", "fonte": "osm", "mezzo": "piedi"},
        {"tratta_id": "osm:r2", "cammino_id": "osm:r2", "fonte": "osm", "mezzo": "bici"},
        {"tratta_id": "osm:r2:variante", "cammino_id": "osm:r2", "fonte": "osm", "mezzo": "bici"},
    ])
    tracks = tracks_of(
        track("cdi:5", "cdi:1", LineString([(0, 0), (1, 0)])),
        track("osm:r1|osm:r2", "cdi:3", LineString([(0, 0), (1, 0)]), tipo="altro", fonte="osm"),
        track("osm:r1", "cdi:4", LineString([(0, 0), (1, 0)]), tipo="altro", fonte="osm"),
    )
    assert pairs.track_mezzi(tracks, labels) == ["piedi", config.MEZZO_ENTRAMBI, "piedi"]
    with pytest.raises(pairs.PairsError):
        pairs.track_mezzi(tracks_of(track("cdi:77", "cdi:1", LineString([(0, 0), (1, 0)]))), labels)


def test_regioni_di_cdi_e_mitur_diventano_nomi_istat_senza_doppioni() -> None:
    names = pairs.region_names(pd.DataFrame({"regione": ["Lazio", "Valle d'Aosta/Vallée d'Aoste", "Trentino-Alto Adige/Südtirol"]}))
    merged = pairs.canonical_regions(["lazio|valle-daosta", "Lazio|Trentino-Alto Adige", ""], names)
    assert merged == "Lazio|Trentino-Alto Adige/Südtirol|Valle d'Aosta/Vallée d'Aoste"
    with pytest.raises(pairs.PairsError):
        pairs.canonical_regions(["Atlantide"], names)


def test_coppie_con_le_colonne_del_contratto_ordinate_per_percorso() -> None:
    table = pd.DataFrame({
        UNICO: ["u002", "u001", "u001"], COMUNE: ["001", "003", "002"], "distanza_m": [0.0, 10.0, 0.0],
        "dentro": [True, False, True], "mezzo": ["piedi"] * 3, "ordine": pd.array([1, 2, 1], dtype="Int64"),
        "fonte_geometria": ["osm", "cdi|mitur", "cdi"],
    })
    cammini = pd.DataFrame({UNICO: ["u001", "u002"], "nome": ["Uno", "Due"], "religioso": ["si", "no"]})
    comune_table = pd.DataFrame({
        COMUNE: ["001", "002", "003"], "comune": ["Alfa", "Beta", "Gamma"], "sigla": ["NA", "RM", "TO"],
        "cod_uts": [1, 2, 3], "provincia": ["P1", "P2", "P3"], "cod_reg": [1, 2, 3], "regione": ["R1", "R2", "R3"],
    })
    coppie = pairs.build_coppie(table, cammini, comune_table)
    assert list(coppie.columns) == list(config.PAIRS_COPPIE_FIELDS)
    assert coppie[[UNICO, COMUNE]].values.tolist() == [["u001", "002"], ["u001", "003"], ["u002", "001"]]
    assert coppie["sigla"].tolist() == ["RM", "TO", "NA"] and coppie["religioso"].tolist() == ["si", "si", "no"]
    assert coppie["fonte_geometria"].tolist() == ["cdi", "cdi|mitur", "osm"]  # la fonte e' della coppia, non del cammino


# --- calibrazione: i numeri pubblicati non cambiano con la bici nell'incrocio ---

def test_calibrazione_tiene_i_suoi_tipi_senza_bici() -> None:
    assert config.CALIBRATION_TIPI == (config.TIPO_TAPPA, config.TIPO_VARIANTE, config.TIPO_ALTRO)
    assert config.TIPO_BICI in config.CROSSING_TIPI and config.TIPO_BICI not in config.CALIBRATION_TIPI
    assert "CROSSING_TIPI" not in inspect.getsource(calibration)


def test_calibrazione_spaziatura_e_linee_ignorano_le_tracce_per_bici() -> None:
    tratte = gpd.GeoDataFrame(
        {
            "cammino_id": ["c1", "c1"], "tipo": [config.TIPO_TAPPA, config.TIPO_BICI],
            "lunghezza_m": [1000.0, 500.0], "spaziatura_media_m": [10.0, 50.0],
        },
        geometry=[LineString([(0, 0), (1000, 0)]), LineString([(0, 5000), (500, 5000)])], crs=config.CRS_WORK,
    )
    assert calibration.cammino_spacing(tratte)["c1"] == pytest.approx(10.0)
    lines = crossing.dissolve_cammini(tratte, config.CALIBRATION_TIPI)
    assert lines["c1"].bounds == (0.0, 0.0, 1000.0, 0.0)
    assert crossing.dissolve_cammini(tratte)["c1"].bounds == (0.0, 0.0, 1000.0, 5000.0)


# --- lunghezza dichiarata, tracciata e copertura dello stesso membro ---

def test_dichiarata_tracciata_e_copertura_sono_dello_stesso_membro() -> None:
    inputs = make_inputs(
        membri_of(u001=["cdi:1", "mitur:2"]),
        tracks_of(
            track("t1", "cdi:1", LineString([(0, 0), (64_300, 0)])),
            track("t2", "mitur:2", LineString([(0, 9), (80_000, 9)]), fonte="mitur"),
        ),
        declared={"cdi:1": 64.0, "mitur:2": 221.0},
    )
    rows = {m: f for m, f in inputs.tracks.groupby("cammino_id")}
    result = pairs.geometry_columns(["cdi:1", "mitur:2"], inputs.catalogue, rows)
    # il membro migliore e' il primo (quota 1): la dichiarata e' la sua, non il massimo 221 dell'altro
    assert (result["lunghezza_dichiarata_km"], result["lunghezza_tracciata_km"], result["copertura"]) == (64.0, 64.3, 1.0)


def test_senza_tracce_la_dichiarata_e_il_massimo_dei_membri() -> None:
    inputs = make_inputs(
        membri_of(u001=["cdi:1", "mitur:2"]), tracks_of(track("t1", "cdi:9", LineString([(0, 0), (1, 0)]))),
        declared={"cdi:1": 40.0, "mitur:2": 55.0},
    )
    result = pairs.geometry_columns(["cdi:1", "mitur:2"], inputs.catalogue, {})
    assert result["lunghezza_dichiarata_km"] == 55.0 and np.isnan(result["copertura"]) and np.isnan(result["lunghezza_tracciata_km"])


# --- lunghezza dell'unione: parti ripetute dentro una traccia ---

def test_parti_ripetute_dentro_una_sola_traccia_si_contano_una_volta() -> None:
    main = [(0, 0), (10_000, 0)]
    duplicates = MultiLineString([main, main, [(0, 10), (10_000, 10)], [(20_000, 0), (25_000, 0)]])
    assert pairs.union_length_m([duplicates]) == pytest.approx(15_000, abs=30)
    assert pairs.union_length_m([MultiLineString([main])]) == 10_000


# --- fonte del tracciato: per coppia e per cammino ---

def test_fonte_della_traccia_dal_prefisso_dell_id_anche_per_il_sostituto_osm() -> None:
    assert [pairs.track_source(t) for t in ("cdi:12", "mitur:slug:tappa 2", "osm:r1|osm:r2")] == ["cdi", "mitur", "osm"]
    with pytest.raises(pairs.PairsError, match="fonte non riconosciuta"):
        pairs.track_source("gpx:1")


def test_fonte_per_coppia_unisce_le_fonti_delle_tracce_vicine_a_quel_comune() -> None:
    comuni = strip_comuni(["A", "B", "C"])
    tracks = tracks_of(
        track("cdi:9", "cdi:1", LineString([(100, 0), (700, 0)])),  # A: cdi
        track("mitur:1:t", "mitur:1", LineString([(200, 0), (600, 0)]), tipo="altro", fonte="mitur"),  # A: anche mitur
        track("cdi:8", "cdi:1", LineString([(1100, 0), (1700, 0)])),  # B: solo cdi
        track("osm:r1", "mitur:1", LineString([(2100, 0), (2700, 0)]), tipo="altro", fonte="osm"),  # C: solo osm
    )
    table = pairs_of(make_inputs(membri_of(u001=["cdi:1", "mitur:1"]), tracks), comuni).loc["u001"]
    assert table["fonte_geometria"].to_dict() == {"A": "cdi|mitur", "B": "cdi", "C": "osm"}
    # la fonte di un cammino e' l'unione sulle sue coppie, in ordine alfabetico dei codici
    assert pairs.pair_sources(table.reset_index().assign(**{UNICO: "u001"})) == {"u001": "cdi|mitur|osm"}


def test_fonte_nessuna_per_il_cammino_senza_coppie_e_osm_per_chi_ha_solo_il_sostituto() -> None:
    comuni = strip_comuni(["A"])
    tracks = tracks_of(track("osm:r1", "cdi:1", LineString([(100, 0), (700, 0)]), tipo="altro", fonte="osm"))
    inputs = make_inputs(membri_of(u001=["cdi:1"], u002=["cdi:2"]), tracks)
    sources = pairs.pair_sources(pairs_of(inputs, comuni).reset_index())
    assert sources == {"u001": "osm"}  # u002 non compare: senza coppie
    cammini = pairs.build_cammini(inputs, {"u001": 1}, {"lazio": "Lazio"}, sources).set_index(UNICO)
    assert cammini.loc["u001", "fonte_geometria"] == "osm" and cammini.loc["u001", "nota"] == ""
    assert cammini.loc["u002", "fonte_geometria"] == config.FONTE_GEOMETRIA_NESSUNA
    assert cammini.loc["u002", "nota"] == config.PAIRS_NOTA_NESSUNA_GEOMETRIA


def test_la_nota_nessuna_geometria_segue_le_tracce_del_cammino_non_le_coppie() -> None:
    # u001 ha tracce ma nessun comune entro d: niente nota, la fonte resta nessuna (nessuna coppia)
    comuni = strip_comuni(["A"])
    tracks = tracks_of(track("t1", "cdi:1", LineString([(50_000, 0), (51_000, 0)])))
    inputs = make_inputs(membri_of(u001=["cdi:1"]), tracks)
    assert pairs_of(inputs, comuni).empty
    cammini = pairs.build_cammini(inputs, {}, {"lazio": "Lazio"}, {}).set_index(UNICO)
    assert cammini.loc["u001", "fonte_geometria"] == config.FONTE_GEOMETRIA_NESSUNA and cammini.loc["u001", "nota"] == ""


# --- catena geografica delle tappe e partenza pubblicata ---

def test_rami_che_ricominciano_la_numerazione_non_si_intrecciano() -> None:
    def unit(x0: float, x1: float, y: float) -> list[LineString]:
        return [LineString([(x0, y), (x1, y)])]

    # In ordine di tappa i due rami si alternano: A1, B1, A2, B2 (il ramo B sta 5 km a nord)
    stages = [unit(0, 1000, 0), unit(0, 1000, 5000), unit(1000, 2000, 0), unit(1000, 2000, 5000)]
    chain = pairs.chain_stages(stages)
    assert [(u[0].coords[0], u[-1].coords[-1]) for u in chain] == [
        ((0, 0), (1000, 0)), ((1000, 0), (2000, 0)), ((2000, 5000), (1000, 5000)), ((1000, 5000), (0, 5000)),
    ]


def test_a_pari_distanza_vince_la_tappa_di_ordine_minore() -> None:
    # Dalla fine della prima tappa (1000, 0) due tappe cominciano a distanza 0: vince la seconda in ordine
    stages = [[LineString([(0, 0), (1000, 0)])], [LineString([(1000, 0), (1000, 2000)])], [LineString([(1000, 0), (3000, 0)])]]
    chain = pairs.chain_stages(stages)
    assert chain[1][-1].coords[-1] == (1000.0, 2000.0)


def test_partenza_pubblicata_sceglie_la_tappa_e_il_verso_della_catena() -> None:
    stages = [[LineString([(0, 0), (1000, 0)])], [LineString([(1000, 0), (2000, 0)])], [LineString([(2000, 0), (3000, 0)])]]
    start = box(2900, -50, 3100, 50)  # il comune di partenza sta all'altro capo
    chain = pairs.chain_stages(stages, start)
    assert [(u[0].coords[0][0], u[-1].coords[-1][0]) for u in chain] == [(3000, 2000), (2000, 1000), (1000, 0)]


def test_traccia_osm_unica_prende_il_verso_dalla_partenza_pubblicata() -> None:
    comuni = strip_comuni(["P", "M", "B"])  # Prato a ovest, Bologna a est
    standin = tracks_of(track("osm:r1", "cdi:1", LineString([(100, 0), (2700, 0)]), tipo="altro", fonte="osm"))
    inputs = make_inputs(membri_of(u001=["cdi:1"]), standin)
    assert pairs_of(inputs, comuni).loc["u001", "ordine"].to_dict() == {"P": 1, "M": 2, "B": 3}  # verso della relazione
    published = pairs_of(inputs, comuni, starts={"u001": "B"}).loc["u001", "ordine"].to_dict()
    assert published == {"B": 1, "M": 2, "P": 3}


def test_comuni_di_partenza_pubblicati(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    cammini = tmp_path / "cammini.csv"
    cammini.write_text(
        "id,titolo,partenza,arrivo,regioni\n1,Uno,Alfa,Beta,lazio\n2,Due,Luogo ignoto,Gamma,lazio\n3,Tre,,,lazio\n"
        "4,Quattro,Beta,Alfa,lazio\n5,Cinque,Gamma,Alfa,lazio\n",
        encoding="utf-8",
    )
    tappe = tmp_path / "tappe.csv"
    tappe.write_text(
        "cammino_id,ordine,titolo,partenza,arrivo,distanza_km\n3,2,T2,Delta,Beta,5\n3,1,T1,Gamma,Delta,5\n", encoding="utf-8"
    )
    monkeypatch.setattr(config, "CDI_CAMMINI_CSV", cammini)
    monkeypatch.setattr(config, "CDI_TAPPE_CSV", tappe)
    monkeypatch.setattr(pairs.crossing, "load_region_codes", lambda: {"lazio": 12})
    comuni = pd.DataFrame(
        {COMUNE: ["001", "002", "003"], config.COMUNE_NAME_COL: ["Alfa", "Beta", "Gamma"], config.COMUNE_REGION_COL: [12, 12, 12]}
    )
    roles = {
        "mitur:x": "principale", "cdi:1": "duplicato", "cdi:2": "principale", "cdi:3": "principale",
        "mitur:y": "principale", "cdi:5": "sezione", "cdi:4": "duplicato",  # la sezione e' elencata prima del cammino intero
    }
    owners = {"mitur:x": "u001", "cdi:1": "u001", "cdi:2": "u002", "cdi:3": "u003", "mitur:y": "u004", "cdi:5": "u004", "cdi:4": "u004"}
    membri = pd.DataFrame(
        [{UNICO: owners[m], "cammino_id": m, "fonte": m.split(":")[0], "nome": m, "ruolo": role} for m, role in roles.items()]
    )
    # u001: il principale e' MiTur (nessuna partenza), vale quella del duplicato cdi; u002: nome non risolto;
    # u003: partenza del cammino vuota, vale quella della prima tappa (ordine 1, Gamma);
    # u004: il principale MiTur non ha partenza; il duplicato cdi (cammino intero) parte da Beta, la sezione cdi da Gamma:
    # vale la partenza del cammino intero
    resolved = pairs.member_starts(comuni)
    assert pairs.published_starts(membri, resolved) == {"u001": "001", "u003": "003", "u004": "002"}
    only_section = membri[~membri["cammino_id"].isin(["cdi:4"])]
    # se l'unico membro con partenza e' una sezione non si usa: la dorsale parte dalla tappa di ordine minore
    assert "u004" not in pairs.published_starts(only_section, resolved)


# --- percorsi di errore ---

def test_limbo_malformato_o_con_cammino_ignoto_da_errore_chiaro() -> None:
    membri = membri_of(u001=["cdi:1"], u002=["cdi:2"])
    tracks = tracks_of(track("t", "cdi:1", LineString([(0, 0), (1, 0)])))
    for oggetto in ("cdi:1", "cdi:1|cdi:2|cdi:3"):
        inputs = make_inputs(membri, tracks, limbo=[{"oggetto": oggetto, "motivo": "x", "dettaglio": ""}])
        with pytest.raises(pairs.PairsError, match="non e' una coppia"):
            pairs.limbo_notes(inputs)
    inputs = make_inputs(membri, tracks, limbo=[{"oggetto": "cdi:1|cdi:99", "motivo": "x", "dettaglio": ""}])
    with pytest.raises(pairs.PairsError, match="cdi:99 non e' in membri.csv"):
        pairs.limbo_notes(inputs)


def test_cammino_membro_di_due_cammini_distinti_interrompe() -> None:
    comuni = strip_comuni(["A"])
    tracks = tracks_of(track("t1", "cdi:1", LineString([(100, 0), (700, 0)])))
    membri = pd.concat([membri_of(u001=["cdi:1"]), membri_of(u002=["cdi:1"])], ignore_index=True)
    with pytest.raises(pairs.PairsError, match="cdi:1 e' membro di piu' cammini distinti"):
        pairs.aggregate_pairs(pairs.cross_tracks(tracks, comuni), tracks, membri)


# --- Napoli ("NA") dall'inizio alla fine ---

def test_sigla_na_di_napoli_arriva_intatta_nel_csv(tmp_path) -> None:
    table = pd.DataFrame({
        UNICO: ["u001"], COMUNE: ["063049"], "distanza_m": [0.0], "dentro": [True], "mezzo": ["piedi"],
        "ordine": pd.array([1], dtype="Int64"), "fonte_geometria": ["cdi"],
    })
    cammini = pd.DataFrame({UNICO: ["u001"], "nome": ["Uno"], "religioso": ["si"]})
    comune_table = pd.DataFrame({
        COMUNE: ["063049"], "comune": ["Napoli"], "sigla": ["NA"], "cod_uts": [63], "provincia": ["Napoli"],
        "cod_reg": [15], "regione": ["Campania"],
    })
    target = tmp_path / "coppie_comuni.csv"
    common.write_frame(pairs.build_coppie(table, cammini, comune_table), target)
    assert ",NA," in target.read_text(encoding="utf-8")
    assert common.read_csv(target)[0]["sigla"] == "NA" and common.read_csv(target)[0][COMUNE] == "063049"


def test_rumore_del_gps_non_scavalca_la_numerazione(monkeypatch: pytest.MonkeyPatch) -> None:
    # Dalla fine della prima tappa la seconda comincia a 300 m, una tappa di un altro ramo (ordine 3) a 100 m
    stages = [[LineString([(0, 0), (1000, 0)])], [LineString([(1300, 0), (2300, 0)])], [LineString([(1000, 100), (1000, 3000)])]]
    assert pairs.chain_stages(stages)[1][0].coords[0] == (1300.0, 0.0)  # entro PAIRS_CHAIN_TIE_M vince l'ordine
    monkeypatch.setattr(config, "PAIRS_CHAIN_TIE_M", 0)
    assert pairs.chain_stages(stages)[1][0].coords[0] == (1000.0, 100.0)  # senza tolleranza vince la piu' vicina


def test_senza_partenza_pubblicata_la_catena_parte_dall_inizio_della_sezione_piu_lunga() -> None:
    comuni = strip_comuni(["W", "M", "E"])  # il cammino va da ovest (W) a est (E)
    # Il principale (intero, senza partenza pubblicata) ha due rami che ricominciano la numerazione: la tappa di ordine
    # minore e' a meta' percorso. La sezione (W -> E per intero) dice dove comincia il cammino.
    tracks = tracks_of(
        track("a", "mitur:1", LineString([(1000, 0), (2000, 0)]), ordine=1, fonte="mitur"),
        track("b", "mitur:1", LineString([(0, 0), (1000, 0)]), ordine=1, fonte="mitur"),
        track("c", "mitur:1", LineString([(2000, 0), (3000, 0)]), ordine=2, fonte="mitur"),
        track("s1", "cdi:9", LineString([(0, 0), (1500, 0)]), ordine=1),
        track("s2", "cdi:9", LineString([(1500, 0), (2900, 0)]), ordine=2),
        track("s3", "cdi:8", LineString([(2900, 0), (3000, 0)]), ordine=1),
    )
    membri = pd.DataFrame([
        {UNICO: "u001", "cammino_id": "mitur:1", "fonte": "mitur", "nome": "x", "ruolo": "principale"},
        {UNICO: "u001", "cammino_id": "cdi:8", "fonte": "cdi", "nome": "x", "ruolo": "sezione"},
        {UNICO: "u001", "cammino_id": "cdi:9", "fonte": "cdi", "nome": "x", "ruolo": "sezione"},
    ])
    inputs = make_inputs(membri, tracks)
    polygons = dict(zip(comuni[COMUNE], comuni.geometry))
    anchors = pairs.chain_anchors(membri, inputs.rows, polygons, {}, pairs.build_spines(["u001"], inputs))
    # la sezione piu' lunga e' cdi:9 (2.9 km contro 0.1): comincia a (0, 0)
    assert anchors["u001"].equals(shapely.Point(0, 0))
    table = pairs.aggregate_pairs(pairs.cross_tracks(inputs.tracks, comuni), inputs.tracks, membri)
    spines = pairs.build_spines(["u001"], inputs)
    ordine = lambda given: pairs.route_order(table, spines, polygons, given).set_axis(table[COMUNE]).to_dict()  # noqa: E731
    assert ordine(anchors) == {"W": 1, "M": 2, "E": 3}
    assert ordine(None)["W"] != 1  # senza ancora parte dalla tappa di ordine minore, a meta' percorso


def test_la_sezione_con_partenza_pubblicata_orienta_la_propria_dorsale_e_l_ancora_non_sostituisce_il_cammino_intero() -> None:
    comuni = strip_comuni(["W", "M", "E"])
    tracks = tracks_of(
        track("w", "mitur:1", LineString([(0, 0), (3000, 0)]), ordine=1, fonte="mitur"),
        track("s", "cdi:9", LineString([(0, 0), (3000, 0)]), ordine=1),  # disegnata da ovest a est
    )
    membri = pd.DataFrame([
        {UNICO: "u001", "cammino_id": "mitur:1", "fonte": "mitur", "nome": "x", "ruolo": "principale"},
        {UNICO: "u001", "cammino_id": "cdi:9", "fonte": "cdi", "nome": "x", "ruolo": "sezione"},
    ])
    rows = make_inputs(membri, tracks).rows
    polygons = dict(zip(comuni[COMUNE], comuni.geometry))
    # la sezione dichiara che parte da E (est): la sua dorsale comincia a est e l'ancora e' quel punto
    spines = pairs.build_spines(["u001"], make_inputs(membri, tracks))
    anchors = pairs.chain_anchors(membri, rows, polygons, {"cdi:9": "E"}, spines)
    assert anchors["u001"].x == pytest.approx(3000, abs=1)
    # se il cammino intero ha una partenza pubblicata, vale quella (il comune), non la sezione
    whole = pairs.chain_anchors(membri, rows, polygons, {"mitur:1": "W", "cdi:9": "E"}, spines)
    assert whole["u001"].equals(polygons["W"])


def _two_level_network(section_starts: list[float]) -> tuple[pd.DataFrame, pairs.Inputs, dict[str, object]]:
    """Cammino intero in tre tappe da 5 km (0 -> 15 km); una sezione per ogni ascissa di partenza, la i-esima lunga 8 km, 7 km, ..."""
    stage = lambda i: track(f"w{i}", "mitur:1", LineString([(i * 5000, 0), ((i + 1) * 5000, 0)]), ordine=i + 1, fonte="mitur")  # noqa: E731
    sections = [
        track(f"s{k}", f"cdi:{k}", LineString([(x, 0), (x + 8000 - k * 1000, 0)])) for k, x in enumerate(section_starts, start=1)
    ]
    membri = pd.DataFrame(
        [{UNICO: "u001", "cammino_id": "mitur:1", "fonte": "mitur", "nome": "x", "ruolo": "principale"}]
        + [{UNICO: "u001", "cammino_id": f"cdi:{k}", "fonte": "cdi", "nome": "x", "ruolo": "sezione"} for k in range(1, len(sections) + 1)]
    )
    inputs = make_inputs(membri, tracks_of(stage(0), stage(1), stage(2), *sections))
    return membri, inputs, {}


def test_la_sezione_che_parte_da_un_incrocio_a_meta_rete_non_ancora_la_catena() -> None:
    membri, inputs, resolved = _two_level_network([5000])  # parte dalla giunzione tra la tappa 1 e la tappa 2
    spines = pairs.build_spines(["u001"], inputs)
    free = pairs.dangling_endpoints(spines["u001"])
    assert sorted(free[:, 0].tolist()) == [0.0, 15000.0]  # i capi liberi sono solo i due capi del cammino
    assert pairs.chain_anchors(membri, inputs.rows, {}, resolved, spines) == {}  # nessuna ancora: tappa di ordine minore


def test_se_la_sezione_piu_lunga_e_a_un_incrocio_si_prova_la_successiva() -> None:
    # La piu' lunga (8 km) parte da una giunzione, la seconda (6 km) dal capo libero a 0 km
    membri, inputs, resolved = _two_level_network([5000, 0])
    spines = pairs.build_spines(["u001"], inputs)
    anchors = pairs.chain_anchors(membri, inputs.rows, {}, resolved, spines)
    assert anchors["u001"].equals(shapely.Point(0, 0))


def test_la_partenza_del_cammino_intero_vince_sulla_sezione_anche_con_un_nome_prefisso_di_un_altro_comune(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    # Forma di Colline Novaresi: il cammino intero (Via della Sesia) parte da Oleggio e arriva a Novara, la sezione
    # (Devozionale) parte da Novara. "Oleggio" e' anche l'inizio di "Oleggio Castello": vale comunque il comune Oleggio
    cammini = tmp_path / "cammini.csv"
    cammini.write_text(
        "id,titolo,partenza,arrivo,regioni" + chr(10) + "92258,Sesia,Oleggio,Novara,piemonte" + chr(10)
        + "92463,Devozionale,Novara,Borgomanero,piemonte" + chr(10), encoding="utf-8",
    )
    tappe = tmp_path / "tappe.csv"
    tappe.write_text("cammino_id,ordine,titolo,partenza,arrivo,distanza_km" + chr(10) + "92258,1,T1,Oleggio,Novara,5" + chr(10), encoding="utf-8")
    monkeypatch.setattr(config, "CDI_CAMMINI_CSV", cammini)
    monkeypatch.setattr(config, "CDI_TAPPE_CSV", tappe)
    monkeypatch.setattr(pairs.crossing, "load_region_codes", lambda: {"piemonte": 1})
    names = ["Oleggio", "Oleggio Castello", "Novara", "Borgomanero"]
    comuni = gpd.GeoDataFrame(
        {COMUNE: ["001", "002", "003", "004"], config.COMUNE_NAME_COL: names, config.COMUNE_REGION_COL: [1] * 4},
        geometry=[box(i * 1000, -500, i * 1000 + 800, 500) for i in range(4)], crs=config.CRS_WORK,
    )
    index = calibration.build_place_index(comuni)
    assert calibration.match_place("Oleggio", index, frozenset({1}))[0] == config.MATCH_PREFIX  # il difetto di partenza
    resolved = pairs.member_starts(comuni)
    assert resolved["cdi:92258"] == "001" and resolved["cdi:92463"] == "003"
    tracks = tracks_of(
        track("w1", "cdi:92258", LineString([(0, 0), (1500, 0)]), ordine=1),
        track("w2", "cdi:92258", LineString([(1500, 0), (2900, 0)]), ordine=2),
        track("s1", "cdi:92463", LineString([(2000, 0), (3000, 0)]), ordine=1),
    )
    membri = pd.DataFrame([
        {UNICO: "u122", "cammino_id": "cdi:92258", "fonte": "cdi", "nome": "Sesia", "ruolo": "principale"},
        {UNICO: "u122", "cammino_id": "cdi:92463", "fonte": "cdi", "nome": "Devozionale", "ruolo": "sezione"},
    ])
    inputs = make_inputs(membri, tracks)
    polygons = dict(zip(comuni[COMUNE], comuni.geometry))
    anchors = pairs.chain_anchors(membri, inputs.rows, polygons, resolved, pairs.build_spines(["u122"], inputs))
    assert anchors["u122"].equals(polygons["001"])  # Oleggio, non l'inizio della sezione


def test_partenza_pubblicata_su_un_omonimo_lontano_dal_percorso_si_scarta() -> None:
    # "Trevignano": il nome indica un comune (H) a 50 km dal percorso W-M-E; senza guardia la tappa si gira verso H
    codes = ["W", "M", "E", "H"]
    comuni = gpd.GeoDataFrame(
        {COMUNE: codes}, crs=config.CRS_WORK,
        geometry=[*(box(i * 1000, -500, i * 1000 + 800, 500) for i in range(3)), box(50_000, -500, 50_800, 500)],
    )
    tracks = tracks_of(track("t1", "cdi:1", LineString([(100, 0), (2700, 0)]), ordine=1))
    membri = membri_of(u001=["cdi:1"])
    inputs = make_inputs(membri, tracks)
    polygons = dict(zip(comuni[COMUNE], comuni.geometry))
    table = pairs.aggregate_pairs(pairs.cross_tracks(inputs.tracks, comuni), inputs.tracks, membri)
    spines = pairs.build_spines(["u001"], inputs)
    resolved = {"cdi:1": "H"}
    comuni_of = table.groupby(UNICO)[COMUNE].agg(set).to_dict()
    accepted, rejected = pairs.split_starts(membri, resolved, comuni_of)
    assert accepted == {} and rejected == {"cdi:1": "H"}
    unguarded = pairs.chain_anchors(membri, inputs.rows, polygons, resolved, spines)
    guarded = pairs.chain_anchors(membri, inputs.rows, polygons, accepted, spines)
    order = lambda anchors: pairs.route_order(table, spines, polygons, anchors).set_axis(table[COMUNE]).to_dict()  # noqa: E731
    assert order(unguarded)["E"] == 1  # la trappola: la traccia viene girata verso l'omonimo
    assert order(guarded) == {"W": 1, "M": 2, "E": 3}
    # un comune attraversato dal cammino vale; i cammini senza comuni non si considerano
    assert pairs.split_starts(membri, {"cdi:1": "E"}, comuni_of)[0] == {"cdi:1": "E"}
    assert pairs.split_starts(membri, resolved, {}) == ({}, {})


def test_standin_mezzo_without_labelled_relation_raises() -> None:
    assert pairs._standin_mezzo("r1|r2", {"r1": ["piedi"], "r2": ["bici"]}) == "entrambi"
    with pytest.raises(pairs.PairsError, match="r9"):
        pairs._standin_mezzo("r9", {})
