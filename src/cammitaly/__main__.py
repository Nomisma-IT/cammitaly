"""Riga di comando: python -m cammitaly all | fetch <fonte> [--limit N] [--refresh] | build <fonte> | calibra | dedup | osm-match | tag | pairs | workbook | report-numbers."""
import argparse
import sys
from collections.abc import Callable, Sequence
from functools import partial

from cammitaly import calibration, dedup, files, http, osm_match, pairs, report_numbers, tags, workbook
from cammitaly.geometry import cdi
from cammitaly.geometry import mitur as mitur_tratte
from cammitaly.geometry import osm as osm_tratte
from cammitaly.sources import camminiditalia, istat, mitur, osm, webmapp

SOURCES: dict[str, Callable[[int | None, bool], None]] = {
    "camminiditalia": camminiditalia.run,
    "webmapp": webmapp.run,
    "mitur": mitur.run,
    "istat": istat.run,
    "osm": osm.run,
}

BUILDERS: dict[str, Callable[[], None]] = {
    "cdi": cdi.run,
    "mitur": mitur_tratte.run,
    "osm": osm_tratte.run,
}

# Le lambda cercano run() al momento della chiamata: i test sostituiscono l'attributo del modulo
STEPS: dict[str, Callable[[], None]] = {
    "calibra": lambda: calibration.run(),
    "osm-match": lambda: osm_match.run(),
    "dedup": lambda: dedup.run(),
    "tag": lambda: tags.run(),
    "pairs": lambda: pairs.run(),
    "workbook": lambda: workbook.run(),
    "report-numbers": lambda: report_numbers.run(),
}

# Ordine di `all`. I download sono indipendenti tra loro: prima quelli che falliscono in pochi secondi (mitur senza le
# pagine salvate a mano, istat senza l'edizione dell'anno), poi i lunghi. Poi le tratte dei tre cataloghi (cdi richiede
# camminiditalia e webmapp, mitur le tracce mitur, osm il pbf). tag legge le tratte e le schede; osm-match legge le
# tratte dei tre cataloghi; dedup legge le geometrie scritte da osm-match; pairs legge etichette, dedup e abbinamenti;
# workbook legge pairs; report-numbers legge tutte le uscite, cartella di lavoro compresa.
ALL_FETCH = ("mitur", "istat", "camminiditalia", "webmapp", "osm")
ALL_BUILD = ("cdi", "mitur", "osm")
ALL_STEPS = ("tag", "osm-match", "dedup", "pairs", "workbook", "report-numbers")


class PipelineError(RuntimeError):
    """Un passo di `all` e' fallito: il messaggio porta il nome del passo."""


def pipeline(refresh: bool = False) -> list[tuple[str, Callable[[], None]]]:
    fetches = [(f"fetch {name}", partial(SOURCES[name], None, refresh)) for name in ALL_FETCH]
    builds = [(f"build {name}", BUILDERS[name]) for name in ALL_BUILD]
    return [*fetches, *builds, *((name, STEPS[name]) for name in ALL_STEPS)]


def run_all(refresh: bool = False) -> None:
    for name, step in pipeline(refresh):
        print(f"== {name} ==", flush=True)
        try:
            step()
        except Exception as error:
            raise PipelineError(f"passo '{name}' fallito ({type(error).__name__}): {error}") from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cammitaly")
    commands = parser.add_subparsers(dest="command", required=True)
    run_everything = commands.add_parser("all", help="esegue l'intera catena, dal download alla cartella di lavoro Excel")
    run_everything.add_argument("--refresh", action="store_true", help="scarica di nuovo tutte le fonti, ignorando la cache")
    fetch = commands.add_parser("fetch", help="scarica una fonte dati")
    fetch.add_argument("source", choices=sorted(SOURCES))
    fetch.add_argument(
        "--limit", type=int, metavar="N",
        help="webmapp: prime N tracce; mitur: primi N zip; osm: 0 = solo md5 e dimensione, senza pbf",
    )
    fetch.add_argument("--refresh", action="store_true", help="ignora la cache e scarica di nuovo")
    build = commands.add_parser("build", help="costruisce le tratte di una fonte")
    build.add_argument("source", choices=sorted(BUILDERS))
    commands.add_parser("calibra", help="calibra la distanza di attraversamento dei comuni")
    commands.add_parser("dedup", help="testa la regola di deduplicazione dei cammini tra le due fonti")
    commands.add_parser("osm-match", help="abbina i cammini del catalogo alle relazioni di percorso di OSM")
    commands.add_parser("tag", help="etichetta tratte e cammini per mezzo e carattere religioso")
    commands.add_parser("pairs", help="calcola i comuni attraversati da ogni cammino distinto, in ordine di percorso")
    commands.add_parser("workbook", help="scrive la cartella di lavoro Excel dei comuni attraversati dai cammini")
    commands.add_parser("report-numbers", help="calcola dai dati prodotti tutti i numeri citati dal rapporto metodologico")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "all":
            run_all(args.refresh)
        elif args.command == "build":
            BUILDERS[args.source]()
        elif args.command == "fetch":
            SOURCES[args.source](args.limit, args.refresh)
        else:
            STEPS[args.command]()
    except (
        PipelineError, http.FetchError, files.UnsafeArchiveError, tags.TagError, pairs.PairsError, workbook.WorkbookError,
        report_numbers.ReportNumbersError,
    ) as error:
        print(f"ERRORE: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
