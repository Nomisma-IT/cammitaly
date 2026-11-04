"""Comando `all` (ordine dei passi, arresto al primo errore) e anno dell'edizione ISTAT."""
import os
import subprocess
import sys
from datetime import date

import pytest

from cammitaly import __main__ as cli
from cammitaly import dedup, http, osm_match, pairs, report_numbers, tags, workbook

EXPECTED_ORDER = [
    "fetch mitur", "fetch istat", "fetch camminiditalia", "fetch webmapp", "fetch osm",
    "build cdi", "build mitur", "build osm",
    "tag", "osm-match", "dedup", "pairs", "workbook", "report-numbers",
]


def _record_all_steps(monkeypatch: pytest.MonkeyPatch, fail_at: str | None = None, error: Exception | None = None) -> list[str]:
    """Sostituisce ogni passo con un registratore; il passo fail_at solleva error."""
    calls: list[str] = []

    def recorder(name: str):
        def step(*_args: object) -> None:
            calls.append(name)
            if name == fail_at:
                raise error or http.FetchError("rete assente")
        return step

    for source in cli.ALL_FETCH:
        monkeypatch.setitem(cli.SOURCES, source, recorder(f"fetch {source}"))
    for source in cli.ALL_BUILD:
        monkeypatch.setitem(cli.BUILDERS, source, recorder(f"build {source}"))
    for module, name in ((tags, "tag"), (osm_match, "osm-match"), (dedup, "dedup"), (pairs, "pairs"), (workbook, "workbook"), (report_numbers, "report-numbers")):
        monkeypatch.setattr(module, "run", recorder(name))
    return calls


def test_all_esegue_i_passi_nell_ordine_delle_dipendenze(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _record_all_steps(monkeypatch)
    assert cli.main(["all"]) == 0
    assert calls == EXPECTED_ORDER


def test_all_non_include_calibra(monkeypatch: pytest.MonkeyPatch) -> None:
    _record_all_steps(monkeypatch)
    assert "calibra" not in [name for name, _ in cli.pipeline()]


@pytest.mark.parametrize("failing", ["fetch webmapp", "build mitur", "osm-match", "workbook"])
def test_all_si_ferma_al_primo_errore_e_nomina_il_passo(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], failing: str
) -> None:
    calls = _record_all_steps(monkeypatch, fail_at=failing)
    assert cli.main(["all"]) == 2
    assert calls == EXPECTED_ORDER[: EXPECTED_ORDER.index(failing) + 1]
    assert f"passo '{failing}' fallito" in capsys.readouterr().err


def test_all_ferma_anche_un_errore_non_previsto(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    calls = _record_all_steps(monkeypatch, fail_at="dedup", error=KeyError("colonna"))
    assert cli.main(["all"]) == 2
    assert calls[-1] == "dedup" and "workbook" not in calls
    assert "passo 'dedup' fallito (KeyError)" in capsys.readouterr().err


def test_all_passa_le_opzioni_di_default_ai_download(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[tuple[object, ...]] = []
    for source in cli.ALL_FETCH:
        monkeypatch.setitem(cli.SOURCES, source, lambda *args: received.append(args))
    for source in cli.ALL_BUILD:
        monkeypatch.setitem(cli.BUILDERS, source, lambda: None)
    for module in (tags, osm_match, dedup, pairs, workbook, report_numbers):
        monkeypatch.setattr(module, "run", lambda: None)
    assert cli.main(["all"]) == 0
    assert received == [(None, False)] * len(cli.ALL_FETCH)


def test_all_refresh_passa_refresh_a_ogni_download(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[tuple[object, ...]] = []
    for source in cli.ALL_FETCH:
        monkeypatch.setitem(cli.SOURCES, source, lambda *args: received.append(args))
    for source in cli.ALL_BUILD:
        monkeypatch.setitem(cli.BUILDERS, source, lambda: None)
    for module in (tags, osm_match, dedup, pairs, workbook, report_numbers):
        monkeypatch.setattr(module, "run", lambda: None)
    assert cli.main(["all", "--refresh"]) == 0
    assert received == [(None, True)] * len(cli.ALL_FETCH)


def _istat_year_in_fresh_interpreter(env_value: str | None) -> str:
    env = {**os.environ, "PYTHONUTF8": "1"}
    env.pop("CAMMITALY_ISTAT_YEAR", None)
    if env_value is not None:
        env["CAMMITALY_ISTAT_YEAR"] = env_value
    code = "from cammitaly import config; print(config.ISTAT_EDITION_YEAR); print(config.ISTAT_COMUNI_SHP.name)"
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
    return result.stdout.split()[0]


def test_anno_istat_di_default_e_quello_in_corso() -> None:
    assert _istat_year_in_fresh_interpreter(None) == str(date.today().year)


def test_anno_istat_si_fissa_con_la_variabile_d_ambiente() -> None:
    assert _istat_year_in_fresh_interpreter("2025") == "2025"


def test_anno_istat_vuoto_vale_come_non_impostato() -> None:
    assert _istat_year_in_fresh_interpreter("") == str(date.today().year)
