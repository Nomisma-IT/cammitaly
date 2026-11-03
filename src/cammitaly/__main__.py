"""Riga di comando: python -m cammitaly fetch <fonte> [--limit N] [--refresh] | build <fonte> | calibra."""
import argparse
import sys
from collections.abc import Callable, Sequence

from cammitaly import calibration, files, http
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cammitaly")
    commands = parser.add_subparsers(dest="command", required=True)
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "build":
            BUILDERS[args.source]()
        elif args.command == "calibra":
            calibration.run()
        else:
            SOURCES[args.source](args.limit, args.refresh)
    except (http.FetchError, files.UnsafeArchiveError) as error:
        print(f"ERRORE: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
