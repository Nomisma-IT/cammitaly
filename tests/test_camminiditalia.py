from cammitaly.sources.camminiditalia import _cammino_row, parse_tappa_text

FULL = (
    '<h2><strong>TAPPA 01: Da Piazza S.Pietro (Roma) ad Albano Laziale\xa0</strong></h2>\n'
    '<p><strong>Partenza</strong>: Roma<br />\n<strong>Arrivo</strong>: Albano Laziale<br />\n'
    '<strong>Distanza</strong>: <span style="font-weight: 400;">29.5 </span>km<br />\n'
    '<strong>Dislivello</strong>: +710 m</p>\n<p>Partenza da Piazza San Pietro.</p>'
)
ONLY_DISTANCE = (
    '<h2><strong>TAPPA 07: <b><i>Villafranca Volta Mantovana (e Goito)</i></b></strong></h2>\n'
    '<p><strong>Distanza</strong>: 23 km</p>\n<p>Percorrendo ancora ciclabili.</p>'
)
DECIMAL_COMMA = (
    '<p><strong>Partenza</strong>: Sant&#8217;Angelo<br /><strong>Arrivo</strong>: Cassino<br />'
    '<strong>Distanza</strong>: 18,4 km</p>'
)


def test_full_stage_text() -> None:
    assert parse_tappa_text(FULL) == {"partenza": "Roma", "arrivo": "Albano Laziale", "distanza_km": "29.5"}


def test_stage_with_distance_only() -> None:
    assert parse_tappa_text(ONLY_DISTANCE) == {"partenza": "", "arrivo": "", "distanza_km": "23"}


def test_decimal_comma_and_html_entities() -> None:
    parsed = parse_tappa_text(DECIMAL_COMMA)
    assert parsed == {"partenza": "Sant’Angelo", "arrivo": "Cassino", "distanza_km": "18.4"}


def test_cammino_row_carries_tema_and_stagione_as_pipe_lists() -> None:
    item = {
        "id": 1, "slug": "x", "title": {"rendered": "X"}, "link": "u", "modified": "m", "class_list": [],
        "acf": {"scheda_tecnica": {"tema": ["storico", "spirituale"], "stagione_ideale": ["estate"]}},
    }
    row = _cammino_row(item)
    assert (row["tema"], row["stagione"]) == ("storico|spirituale", "estate")
    bare = _cammino_row({**item, "acf": {"scheda_tecnica": {}}})
    assert (bare["tema"], bare["stagione"]) == ("", "")
