#!/usr/bin/env python3
"""The Duplex demo from a fresh clone, on any OS, in one command.

Usage::

    make demo                                   # or: uv run python scripts/demo.py
    uv run python scripts/demo.py --check       # play and compare, no page (CI)
    uv run python scripts/demo.py --step        # Enter before each question

It runs, in order, stopping at the first that fails:

1. ``scripts/download_samples.py``'s download of the four Duplex files, pinned and
   checked against their SHA-256 (nothing is fetched for a file already there);
2. ``ifc-sql migrate``;
3. ``ifc-sql ingest … --viewer`` for each file: a file already loaded gets its scene in
   place, or keeps the one it has;
4. ``ifc-sql dq``, which checks every model loaded;
5. ``ifc-sql demo``, with the options given here: it opens the page and plays the
   questions, or with ``--check`` compares each with the script.

The commands read this shell's IFC_SQL_* variables, as any ifc-sql command does: set
IFC_SQL_DATA_DIR for the embedded database, or IFC_SQL_DATABASE_URL for a server.
"""

import subprocess
import sys
from pathlib import Path

from download_samples import DEFAULT_DEST, SAMPLES, DownloadError, ensure_sample

ROOT = Path(__file__).resolve().parent.parent
IFC_SQL = Path(sys.executable).with_name("ifc-sql")
DISCIPLINES = {"arc": "ARC", "mep": "MEP", "ele": "ELE", "plu": "PLU"}
"""The Duplex files, in the order they load: the architectural model first."""


def commands(paths: dict[str, Path], demo_options: list[str]) -> list[list[str]]:
    """The ifc-sql commands the demo runs, in order, for the files at ``paths``."""
    command = str(IFC_SQL)
    return [
        [command, "migrate"],
        *(
            [command, "ingest", str(paths[key]), "--discipline", discipline, "--viewer"]
            for key, discipline in DISCIPLINES.items()
        ),
        [command, "dq"],
        [command, "demo", *demo_options],
    ]


def main(argv: list[str] | None = None) -> int:
    """Download, load, check and play; the first failing command's exit code, else 0."""
    options = sys.argv[1:] if argv is None else argv
    paths = {}
    try:
        for key in DISCIPLINES:
            paths[key], downloaded = ensure_sample(SAMPLES[key], ROOT / DEFAULT_DEST)
            print(f"{'downloaded' if downloaded else 'have'} {paths[key].name}", flush=True)
    except DownloadError as exc:
        print(f"demo: {exc}", file=sys.stderr)
        return 1
    try:
        for command in commands(paths, options):
            print(f"$ ifc-sql {' '.join(command[1:])}", flush=True)
            # Our own command, with arguments from this script and its user.
            returned = subprocess.run(command, check=False).returncode  # noqa: S603
            if returned != 0:
                return returned
    except KeyboardInterrupt:
        print("demo: stopped", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
