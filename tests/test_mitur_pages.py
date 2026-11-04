"""Insieme delle pagine di elenco salvate a mano: complete, aggiornate, senza doppioni (HTML sintetico, senza rete)."""
from pathlib import Path

import pytest

from cammitaly import config, http
from cammitaly.sources import mitur

BASE = "https://www.ministeroturismo.gov.it/catalogo-dei-cammini-religiosi-italiani/"


def _page(number: int, last: int | None) -> str:
    """Pagina con link canonical e, se last non e' None, la paginazione WordPress (numeri, puntini, ultima pagina)."""
    canonical = BASE if number == 1 else f"{BASE}page/{number}/"
    html = f'<link rel="canonical" href="{canonical}">'
    if last is not None:
        html += '<nav class="pagination"><span class="page-numbers disabled" aria-disabled="true"></span>'
        html += f'<span class="page-numbers current">{number}</span>'
        html += f'<a class="page-numbers" href="{BASE}page/{last}/">{last}</a>'
        html += '<span class="page-numbers dots">…</span>'
        html += f'<a class="page-numbers next" href="{BASE}page/2/" rel="next"></a></nav>'
    return html


def _write(folder: Path, monkeypatch: pytest.MonkeyPatch, pages: dict[str, str]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, markup in pages.items():
        (folder / name).write_text(markup, encoding="utf-8")
    monkeypatch.setattr(config, "MITUR_PAGES_DIR", folder)


def _complete(last: int, declared: int | None = None) -> dict[str, str]:
    return {f"p{n:02d}.html": _page(n, last if declared is None else declared) for n in range(1, last + 1)}


def test_complete_set_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path / "pagine", monkeypatch, _complete(4))
    assert [number for number, _, _ in mitur._read_pages()] == [1, 2, 3, 4]


def test_file_names_do_not_matter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path / "pagine", monkeypatch, {"pb.html": _page(2, 2), "pa.html": _page(1, 2)})
    assert [number for number, _, _ in mitur._read_pages()] == [1, 2]


def test_missing_middle_page_is_named(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pages = _complete(5)
    del pages["p03.html"]
    _write(tmp_path / "pagine", monkeypatch, pages)
    with pytest.raises(http.FetchError, match=r"mancanti: 3 \(l'elenco ne ha 5\)"):
        mitur._read_pages()


def test_missing_last_pages_are_detected_through_the_pagination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pages = {name: markup for name, markup in _complete(5).items() if name in ("p01.html", "p02.html", "p03.html")}
    _write(tmp_path / "pagine", monkeypatch, pages)
    with pytest.raises(http.FetchError, match="mancanti: 4, 5"):
        mitur._read_pages()


def test_duplicated_page_names_both_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pages = _complete(3)
    pages["p02_copia.html"] = pages["p02.html"]
    _write(tmp_path / "pagine", monkeypatch, pages)
    with pytest.raises(http.FetchError, match=r"stessa pagina di elenco \(pagina 2: p02.html, p02_copia.html\)"):
        mitur._read_pages()


def test_leftover_from_a_longer_previous_listing_is_named(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Il catalogo di quest'anno ha 3 pagine; p04.html e' dell'anno scorso e dichiara 4 pagine: i file non concordano
    pages = _complete(3)
    pages["p04.html"] = _page(4, 4)
    _write(tmp_path / "pagine", monkeypatch, pages)
    with pytest.raises(http.FetchError, match="non concordano"):
        mitur._read_pages()


def test_page_beyond_the_declared_last_is_extra(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Tutti i file dichiarano 3 pagine, ma uno ha il canonical della pagina 4 (paginazione copiata da un'altra pagina)
    pages = _complete(3)
    pages["p04.html"] = _page(4, 3).replace('<span class="page-numbers current">4</span>', "")
    _write(tmp_path / "pagine", monkeypatch, pages)
    with pytest.raises(http.FetchError, match=r"oltre l'ultima pagina dell'elenco \(3\): p04.html"):
        mitur._read_pages()


def test_without_pagination_gaps_and_duplicates_are_still_caught(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    contiguous = {f"p0{n}.html": _page(n, None) for n in (1, 2, 3)}
    _write(tmp_path / "ok", monkeypatch, contiguous)
    assert len(mitur._read_pages()) == 3
    gap = {f"p0{n}.html": _page(n, None) for n in (1, 3)}
    _write(tmp_path / "gap", monkeypatch, gap)
    with pytest.raises(http.FetchError, match="mancanti: 2"):
        mitur._read_pages()


def test_pagination_in_only_some_files_is_a_disagreement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path / "pagine", monkeypatch, {"p01.html": _page(1, 2), "p02.html": _page(2, None)})
    with pytest.raises(http.FetchError, match="non concordano"):
        mitur._read_pages()


REAL_PAGES = sorted((Path(config.ROOT) / "data" / "raw" / "mitur" / "pagine").glob("p*.html"))


@pytest.mark.skipif(len(REAL_PAGES) != 11, reason="le 11 pagine p01..p11 non sono presenti in data/raw/mitur/pagine")
def test_real_saved_pages_pass() -> None:
    assert [number for number, _, _ in mitur._read_pages()] == list(range(1, 12))
