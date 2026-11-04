"""Etichette: mezzo per fonte, regole di combinazione, religioso per fonte, attributi extra, lista di controllo."""
import csv
import json
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

from cammitaly import __main__ as cli
from cammitaly import config, tags

PIEDI, BICI, ENTRAMBI = config.MEZZO_PIEDI, config.MEZZO_BICI, config.MEZZO_ENTRAMBI


def _original(path: str) -> PurePosixPath:
    return PurePosixPath(path)


# --- MiTur ---

def test_mitur_bike_track_is_bici() -> None:
    assert tags.mezzo_mitur("bici", "Tappa 1 MTB", _original("GPX IN BICI/t1.gpx"))[0] == BICI


def test_mitur_file_for_walkers_and_bikes_is_entrambi() -> None:
    # Il nome del file porta la prova: classificato altro perche' si cammina, ma e' anche per la bici
    name = "csj-da-olbia-a-orosei-per-camminanti-e-mtb"
    assert tags.mezzo_mitur("altro", name, _original(f"{name}.gpx")) == (ENTRAMBI, config.EVIDENZA_NOME_ENTRAMBI)
    assert tags.mezzo_mitur("altro", "csj per camminanti e ciclisti", _original("0006_a.gpx"))[0] == ENTRAMBI


def test_mitur_bike_folder_with_a_walkers_file_is_entrambi_like_the_classifier() -> None:
    # Stessi ingressi della classificazione: anche la cartella dello zip conta per la bici
    assert tags.mezzo_mitur("altro", "Tappa 3", _original("GPX IN BICI/per camminanti.gpx"))[0] == ENTRAMBI


def test_mitur_walking_stage_on_cycle_path_stays_piedi_but_is_recorded() -> None:
    nome = "1° tappa - 3° tratto - strada pedonale ciclabile"
    assert tags.mezzo_mitur("tappa", nome, _original("t.gpx")) == (PIEDI, config.EVIDENZA_PERCORSO_CONDIVISO)
    assert tags.mezzo_mitur("tappa", "S5 Poppi (su ciclopedonale Arno)", _original("s5.gpx"))[0] == PIEDI


def test_mitur_plain_stage_is_piedi() -> None:
    assert tags.mezzo_mitur("tappa", "Tappa 3 Assisi", _original("zip/0001_t3.gpx")) == (PIEDI, config.EVIDENZA_PIEDI)


# --- cdi (Webmapp) ---

def _track(activities: object = None, taxonomy_names: tuple[str, ...] = ()) -> dict[str, object]:
    taxonomy = {"activity": [{"id": i, "name": {"it": name}} for i, name in enumerate(taxonomy_names)]}
    return {"activities": activities, "taxonomy": taxonomy if taxonomy_names else None}


def test_cdi_walking_stage_with_bike_activity_is_entrambi() -> None:
    mode, evidence = tags.mezzo_cdi("tappa", _track('{"11":["e-bike","touring-bike","cycling","hiking"]}'))
    assert mode == ENTRAMBI
    assert evidence == config.EVIDENZA_WEBMAPP.format("cycling|e-bike|touring-bike")


def test_cdi_bike_in_the_taxonomy_only_counts_too() -> None:
    assert tags.mezzo_cdi("tappa", _track('{"11":["walking","hiking"]}', ("Bici da turismo",)))[0] == ENTRAMBI


@pytest.mark.parametrize("activities", [None, "", '{"11":[]}', '{"11":["hiking"]}', {"11": ["walking", "hiking"]}])
def test_cdi_silent_or_walking_activities_are_piedi(activities: object) -> None:
    assert tags.mezzo_cdi("tappa", _track(activities)) == (PIEDI, config.EVIDENZA_PIEDI)


def test_cdi_bike_track_type() -> None:
    assert tags.mezzo_cdi("bici", _track('{"11":["mtb"]}'))[0] == BICI
    assert tags.mezzo_cdi("bici", _track('{"11":["mtb","hiking"]}'))[0] == ENTRAMBI


# --- OSM ---

def test_osm_bike_route_is_bici() -> None:
    assert tags.mezzo_osm("mtb", "", "Cammino X MTB")[0] == BICI
    assert tags.mezzo_osm("bicycle", "ncn", "Cammino Y") == (BICI, "route=bicycle; network=ncn")


def test_osm_hiking_route_without_bike_signal_is_piedi() -> None:
    assert tags.mezzo_osm("hiking", "lwn", "Sentiero dei ciclamini") == (PIEDI, "route=hiking")


@pytest.mark.parametrize(
    ("network", "expected"), [("rcn", "network=rcn"), ("rwn;rcn", "network=rcn"), ("lcn;ncn", "network=lcn|ncn")],
)
def test_osm_cycle_network_makes_a_hiking_route_entrambi(network: str, expected: str) -> None:
    assert tags.mezzo_osm("hiking", network, "Cammino dei Briganti") == (ENTRAMBI, f"route=hiking; {expected}")
    assert tags.mezzo_osm("foot", network, "Percorso")[0] == ENTRAMBI


def test_osm_bike_word_in_the_name_makes_a_hiking_route_entrambi() -> None:
    mode, evidence = tags.mezzo_osm("hiking", "lwn", "La rotta dei due Mari - Tappa 6A - Variante per ciclisti")
    assert (mode, evidence) == (ENTRAMBI, f"route=hiking; {config.EVIDENZA_NOME_BICI}")
    assert tags.mezzo_osm("hiking", "", "Ciclovia dei laghi")[0] == ENTRAMBI


@pytest.mark.parametrize("name", [
    "Sentiero delle Mura Ciclopiche", "Bicione", "Strada Passo delle Forbici - Passo delle Radici",
    "Sentiero dei ciclamini", "Ciclopedonale Terni-Narni",
])
def test_osm_name_false_positives_stay_piedi(name: str) -> None:
    assert tags.mezzo_osm("hiking", "lwn", name)[0] == PIEDI


def test_osm_non_cycle_network_values_do_not_count() -> None:
    assert tags.mezzo_osm("hiking", 'Pedibus scuola "Ada Negri"', "Percorso")[0] == PIEDI


def test_osm_route_evidence_for_missing_and_unusual_routes() -> None:
    assert tags.mezzo_osm("", "", "Percorso") == (PIEDI, config.EVIDENZA_ROUTE_ASSENTE)
    assert tags.mezzo_osm("worship", "", "Percorso") == (PIEDI, config.EVIDENZA_ROUTE_ALTRA.format("worship"))
    assert tags.mezzo_osm("foot", "", "Percorso") == (PIEDI, "route=foot")


# --- combinazione ---

@pytest.mark.parametrize(
    ("modes", "expected"),
    [
        ([], ""), ([""], ""), (["", ""], ""), (["", PIEDI], PIEDI), (["", BICI], BICI), (["", ENTRAMBI], ENTRAMBI),
        ([PIEDI, PIEDI], PIEDI), ([BICI], BICI), ([BICI, BICI], BICI),
        ([PIEDI, BICI], ENTRAMBI), ([ENTRAMBI], ENTRAMBI), ([PIEDI, ENTRAMBI], ENTRAMBI), ([BICI, ENTRAMBI], ENTRAMBI),
    ],
)
def test_combine_mezzo(modes: list[str], expected: str) -> None:
    assert tags.combine_mezzo(modes) == expected


def test_combine_religioso_any_si_wins() -> None:
    assert tags.combine_religioso(["no", "si", "no"]) == "si"
    assert tags.combine_religioso(["no", "no"]) == "no"
    assert tags.combine_religioso([]) == "no"


def test_combine_multi_is_a_sorted_union() -> None:
    assert tags.combine_multi(["storico|montano", "", "montano|arte-e-cultura"]) == "arte-e-cultura|montano|storico"
    assert tags.combine_multi([]) == ""


# --- religioso ---

def test_religioso_cdi_from_tema_or_webmapp_layer() -> None:
    assert tags.religioso_cdi(["storico", "spirituale"], []) == ("si", config.EVIDENZA_TEMA_CDI)
    assert tags.religioso_cdi([], ["spirituale"]) == ("si", config.EVIDENZA_TEMA_LAYER)
    both = tags.religioso_cdi(["spirituale"], ["spirituale"])
    assert both == ("si", config.EVIDENZA_SEPARATOR.join([config.EVIDENZA_TEMA_CDI, config.EVIDENZA_TEMA_LAYER]))
    assert tags.religioso_cdi(["storico"], []) == ("no", config.EVIDENZA_SILENTE)
    assert tags.religioso_cdi([""], []) == ("no", config.EVIDENZA_SILENTE)


@pytest.mark.parametrize(
    ("pilgrimage", "expected"),
    [("yes", "si"), ("Via di Francesco", "si"), ("Stations of the Cross", "si"), ("no", "no"), ("n", "no"), ("", "no")],
)
def test_religioso_osm(pilgrimage: str, expected: str) -> None:
    assert tags.religioso_osm(pilgrimage)[0] == expected


def test_religioso_osm_evidence() -> None:
    assert tags.religioso_osm("") == ("no", config.EVIDENZA_SILENTE)
    assert tags.religioso_osm("no") == ("no", "OpenStreetMap: indicato come non di pellegrinaggio (pilgrimage=no)")
    assert tags.religioso_osm("yes") == ("si", "OpenStreetMap: indicato come pellegrinaggio")
    assert tags.religioso_osm("Via di Francesco") == ("si", "OpenStreetMap: indicato come pellegrinaggio (Via di Francesco)")


# --- attributi extra ---

def _layer(**attributes: object) -> dict[str, object]:
    return {"id": 1, "attributes": attributes}


def _scheda(**values: str) -> dict[str, str]:
    return {"tipologia": "", "portata": "", "tema": "", "stagione": "", **values}


def test_cdi_extras_take_single_values_from_cdi_and_union_the_lists() -> None:
    scheda = _scheda(tipologia="anello", portata="internazinale", tema="storico", stagione="primavera")
    layer = _layer(
        shape={"value": "linear"}, walking_network={"value": "nwn"},
        themes=[{"value": "montano"}], season=[{"value": "spring"}, {"value": "winter"}],
    )
    assert tags.cdi_extras(scheda, layer) == {
        "tipologia": "anello",  # Webmapp "linear" e' il default: non aggiunge un secondo valore
        "portata": "internazionale",  # refuso normalizzato; walking_network ignorata
        "tema": "montano|storico", "stagione": "inverno|primavera",
    }


def test_cdi_extras_webmapp_fills_only_a_blank_tipologia_and_never_the_portata() -> None:
    layer = _layer(shape={"value": "roundtrip"}, walking_network={"value": "iwn"})
    extras = tags.cdi_extras(_scheda(), layer)
    assert (extras["tipologia"], extras["portata"]) == ("anello", "")
    assert tags.cdi_extras(_scheda(), _layer(shape={"value": "linear"}))["tipologia"] == "lineare"


def test_cdi_extras_tolerate_missing_sources() -> None:
    assert set(tags.cdi_extras(_scheda(), None).values()) == {""}
    empty_layer = {"id": 2, "attributes": []}  # Webmapp usa [] per "nessun attributo"
    assert tags.cdi_extras(_scheda(tema="storico"), empty_layer)["tema"] == "storico"


def test_mitur_extras_from_service_icons() -> None:
    card = {"servizi": "Segnaletica|Servizi al turista", "in_catalogo_dal": "2023-06-16"}
    assert tags.mitur_extras(card) == {"credenziale": "no", "segnaletica": "si", "data_catalogo": "2023-06-16"}
    assert tags.mitur_extras({"servizi": "", "in_catalogo_dal": ""})["credenziale"] == "no"


# --- parole religiose e regola del nome ---

def test_religious_words_match_whole_words_and_prefixes() -> None:
    assert tags.religious_words("Cammino dei Santuari del Mare") == ["santuari"]
    assert tags.religious_words("Il Cammino di Sant’Ercolano") == ["sant"]
    assert tags.religious_words("Via Francesco e Frati") == ["francesco", "frati"]
    assert tags.religious_words("Sentiero Santorini Santiago") == []  # nessuna parola intera ne' prefisso ammesso
    assert tags.religious_words("Cammino della Pace") == []


@pytest.mark.parametrize(("name", "words"), [
    ("Cammino delle Colline Novaresi - Cammino Devozionale", ["devozionale"]),
    ("Via dei Monasteri", ["monasteri"]),
    ("Via dei Monti Sacri", ["sacri"]),
    ("Sacro Monte di Ghiffa", ["sacro"]),
    ("Pieve di Cadore", []),  # toponimo: pieve non e' nell'elenco
    ("Sentiero Sacchetti", []),  # "sacr" a inizio parola, non "sacc"
])
def test_added_religious_words(name: str, words: list[str]) -> None:
    assert tags.religious_words(name) == words


def test_name_rule_fires_without_other_evidence() -> None:
    silent = tags.religioso_cdi(["storico"], [])
    assert tags.religioso_by_name(silent, "Cammino di San Tommaso") == ("si", "il nome contiene la parola «san»")
    assert tags.religioso_by_name(silent, "Via Francesco e Frati") == ("si", "il nome contiene le parole «francesco», «frati»")
    assert tags.religioso_by_name(silent, "Anello del Lago") == silent


def test_name_rule_keeps_a_cammino_already_si() -> None:
    label = tags.religioso_cdi(["spirituale"], [])
    assert tags.religioso_by_name(label, "Cammino di San Tommaso") == label


def test_name_rule_does_not_apply_to_osm_rows() -> None:
    assert tags.religioso_osm("") == ("no", config.EVIDENZA_SILENTE)  # _osm_cammini non chiama la regola del nome
    row = tags._osm_cammini({"osm:1": "piedi"}, [{"cammino_id": "osm:1", "fonte": "osm", "nome": "Via di San Pietro", "pilgrimage": ""}])[0]
    assert (row["religioso"], row["evidenza_religioso"]) == ("no", config.EVIDENZA_SILENTE)


def test_run_writes_only_the_two_label_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "INTERIM_DIR", tmp_path)
    monkeypatch.setattr(config, "TAGS_TRATTE_CSV", tmp_path / "etichette_tratte.csv")
    monkeypatch.setattr(config, "TAGS_CAMMINI_CSV", tmp_path / "etichette_cammini.csv")
    monkeypatch.setattr(tags, "_cammini", lambda fonte: [])
    monkeypatch.setattr(tags, "label_tratte", lambda osm: [])
    monkeypatch.setattr(tags, "label_cammini", lambda tratte, osm: [])
    tags.run()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["etichette_cammini.csv", "etichette_tratte.csv"]


# --- lettura e collegamento dei dati (fixture piccole) ---

def _write_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def _patch_tratte(monkeypatch: pytest.MonkeyPatch, by_source: dict[str, list[dict[str, Any]]]) -> None:
    monkeypatch.setattr(tags, "_tratte", lambda fonte: by_source[fonte])


def _tratta(tratta_id: str, cammino_id: str, tipo: str = "tappa", origin: str = "x/0001_t.gpx") -> dict[str, Any]:
    return {"tratta_id": tratta_id, "cammino_id": cammino_id, "tipo": tipo, "nome": "Tappa", "file_origine": origin}


def test_label_tratte_wires_every_source(monkeypatch: pytest.MonkeyPatch) -> None:
    by_source = {
        config.CDI_FONTE: [_tratta("cdi:7", "cdi:1"), _tratta("cdi:7", "cdi:2"), _tratta("cdi:8", "cdi:1")],
        config.MITUR_FONTE: [
            _tratta("mitur:a:t1", "mitur:a", "bici", origin="raw/a/0001_t1.gpx"),
            _tratta("mitur:a:t2", "mitur:a", origin="raw/a/0002_t2.gpx"),
        ],
        config.OSM_FONTE: [
            _tratta("osm:r1", "osm:r1", "altro"), _tratta("osm:r1:variante", "osm:r1", "variante"),
            _tratta("osm:r2", "osm:r2", "bici"),
        ],
    }
    _patch_tratte(monkeypatch, by_source)
    tracks = {"7": _track('{"11":["mtb","hiking"]}'), "8": _track('{"11":["hiking"]}')}
    monkeypatch.setattr(tags, "_track_properties", lambda track_id: tracks[track_id])
    monkeypatch.setattr(tags, "_mitur_originals", lambda records: {})
    osm = [
        {"cammino_id": "osm:r1", "nome": "Sentiero", "route": "hiking", "network": "rcn"},
        {"cammino_id": "osm:r2", "nome": "Giro", "route": "mtb", "network": ""},
    ]
    rows = {row["tratta_id"] + "@" + row["cammino_id"]: row["mezzo"] for row in tags.label_tratte(osm)}
    assert rows == {
        "cdi:7@cdi:1": ENTRAMBI, "cdi:7@cdi:2": ENTRAMBI,  # la traccia condivisa ha lo stesso mezzo nei due cammini
        "cdi:8@cdi:1": PIEDI,
        "mitur:a:t1@mitur:a": BICI, "mitur:a:t2@mitur:a": PIEDI,
        "osm:r1@osm:r1": ENTRAMBI, "osm:r1:variante@osm:r1": ENTRAMBI,  # la variante eredita dalla relazione
        "osm:r2@osm:r2": BICI,
    }


def test_label_tratte_names_the_missing_osm_cammino(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_tratte(monkeypatch, {
        config.CDI_FONTE: [], config.MITUR_FONTE: [], config.OSM_FONTE: [_tratta("osm:r9", "osm:r9", "altro")],
    })
    monkeypatch.setattr(tags, "_mitur_originals", lambda records: {})
    with pytest.raises(tags.TagError, match="osm:r9"):
        tags.label_tratte([])


def test_missing_webmapp_track_names_the_track(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "WEBMAPP_TRACKS_DIR", tmp_path)
    with pytest.raises(tags.TagError, match="traccia Webmapp 123"):
        tags._track_properties("123")
    (tmp_path / "5.json").write_text(json.dumps({"properties": {"id": 5}}), encoding="utf-8")
    assert tags._track_properties("5") == {"id": 5}


def test_malformed_activities_name_the_track(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tags, "_track_properties", lambda track_id: {"activities": "{non json"})
    with pytest.raises(tags.TagError, match="traccia Webmapp 42"):
        tags._cdi_label("42", "tappa")


def test_mitur_originals_come_from_the_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    path = config.ROOT / "data" / "raw" / "mitur" / "tracce" / "a" / "0001_t1.gpx"
    monkeypatch.setattr(tags.mitur_geometry, "card_files", lambda slug: [(path, "GPX IN BICI/t1.gpx")])
    originals = tags._mitur_originals([{"cammino_id": "mitur:a"}])
    assert originals == {"data/raw/mitur/tracce/a/0001_t1.gpx": PurePosixPath("GPX IN BICI/t1.gpx")}


def _cammino(cammino_id: str, fonte: str, nome: str) -> dict[str, str]:
    return {"cammino_id": cammino_id, "fonte": fonte, "nome": nome}


def test_cdi_cammini_join_scheda_layer_and_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scheda = {
        "id": "1", "tipologia": "anello", "portata": "internazinale", "tema": "storico", "stagione": "estate",
        "webmapp_layer": "55",
    }
    other = {**scheda, "id": "2", "tema": "", "stagione": "", "tipologia": "", "portata": "", "webmapp_layer": ""}
    monkeypatch.setattr(config, "CDI_CAMMINI_CSV", _write_csv(tmp_path / "schede.csv", [scheda, other]))
    layers = [{"id": 55, "attributes": {"themes": [{"value": "spirituale"}], "season": [{"value": "winter"}]}}]
    layers_file = tmp_path / "config.json"
    layers_file.write_text(json.dumps({"MAP": {"layers": layers}}), encoding="utf-8")
    monkeypatch.setattr(config, "WEBMAPP_CONFIG_FILE", layers_file)
    cammini = [_cammino("cdi:1", "cdi", "Uno"), _cammino("cdi:2", "cdi", "Due")]
    monkeypatch.setattr(tags, "_cammini", lambda fonte: cammini)
    one, two = tags._cdi_cammini({"cdi:1": ENTRAMBI, "cdi:2": PIEDI})
    assert (one["mezzo"], one["religioso"], one["portata"], one["tema"], one["stagione"]) == (
        ENTRAMBI, "si", "internazionale", "spirituale|storico", "estate|inverno",
    )
    assert (two["mezzo"], two["religioso"], two["evidenza_religioso"], two["tema"]) == (PIEDI, "no", config.EVIDENZA_SILENTE, "")
    assert one["credenziale"] == ""  # campi che la fonte non ha: vuoti


def test_cdi_cammini_name_a_missing_layer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scheda = {"id": "1", "tipologia": "", "portata": "", "tema": "", "stagione": "", "webmapp_layer": "999"}
    monkeypatch.setattr(config, "CDI_CAMMINI_CSV", _write_csv(tmp_path / "schede.csv", [scheda]))
    layers_file = tmp_path / "config.json"
    layers_file.write_text(json.dumps({"MAP": {"layers": []}}), encoding="utf-8")
    monkeypatch.setattr(config, "WEBMAPP_CONFIG_FILE", layers_file)
    monkeypatch.setattr(tags, "_cammini", lambda fonte: [_cammino("cdi:1", "cdi", "Uno")])
    with pytest.raises(tags.TagError, match=r"cdi:1.*999"):
        tags._cdi_cammini({"cdi:1": PIEDI})


def test_mitur_cammini_join_the_card(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    card = {"slug": "a", "servizi": "Credenziali|Segnaletica", "in_catalogo_dal": "2023-03-23"}
    bare = {"slug": "b", "servizi": "", "in_catalogo_dal": ""}
    monkeypatch.setattr(config, "MITUR_CAMMINI_CSV", _write_csv(tmp_path / "mitur.csv", [card, bare]))
    cammini = [_cammino("mitur:a", "mitur", "A"), _cammino("mitur:b", "mitur", "B")]
    monkeypatch.setattr(tags, "_cammini", lambda fonte: cammini)
    a, b = tags._mitur_cammini({"mitur:a": BICI, "mitur:b": PIEDI})
    assert (a["religioso"], a["credenziale"], a["segnaletica"], a["data_catalogo"], a["mezzo"]) == (
        "si", "si", "si", "2023-03-23", BICI,
    )
    assert (b["credenziale"], b["segnaletica"], b["data_catalogo"]) == ("no", "no", "")
    monkeypatch.setattr(tags, "_cammini", lambda fonte: [_cammino("mitur:zzz", "mitur", "Z")])
    with pytest.raises(tags.TagError, match="mitur:zzz"):
        tags._mitur_cammini({})


# --- sintesi del cammino dalle sue tratte ---

def test_cammino_mode_is_the_roll_up_of_its_tratte(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tags, "_cdi_cammini", lambda mezzi: [])
    monkeypatch.setattr(tags, "_mitur_cammini", lambda mezzi: [])
    osm = [
        {"cammino_id": cid, "nome": cid, "fonte": "osm", "pilgrimage": ""}
        for cid in ("osm:r1", "osm:r2", "osm:r3", "osm:r4")
    ]
    tratte = [
        {"cammino_id": "osm:r1", "mezzo": PIEDI}, {"cammino_id": "osm:r1", "mezzo": PIEDI},
        {"cammino_id": "osm:r2", "mezzo": BICI},
        {"cammino_id": "osm:r3", "mezzo": PIEDI}, {"cammino_id": "osm:r3", "mezzo": BICI},
    ]
    rows = tags.label_cammini(tratte, osm)
    assert {row["cammino_id"]: row["mezzo"] for row in rows} == {
        "osm:r1": PIEDI, "osm:r2": BICI, "osm:r3": ENTRAMBI, "osm:r4": "",  # r4 senza tratte: nessun mezzo, non "piedi"
    }


# --- comando ---

def test_comando_tag_riporta_l_errore_con_l_identificativo_ed_esce_con_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fails() -> None:
        raise tags.TagError("il cammino cdi:1 non ha la scheda")

    monkeypatch.setattr(tags, "run", fails)
    assert cli.main(["tag"]) == 2
    assert "ERRORE: il cammino cdi:1 non ha la scheda" in capsys.readouterr().err
