#!/usr/bin/env python3
"""A scripted demo from a fresh clone, on any OS, in one command.

Usage::

    make demo                                   # or: uv run python scripts/demo.py
    uv run python scripts/demo.py --check       # play and compare, no page (CI)
    uv run python scripts/demo.py --step        # Enter before each question
    uv run python scripts/demo.py --script audit   # the audit's Revit exports, local only

It runs, in order, stopping at the first that fails:

1. the download of the script's files, pinned and checked against their SHA-256
   (nothing is fetched for a file already there): ``scripts/download_samples.py``'s four
   Duplex files, or with ``--script audit`` ``scripts/download_audit_samples.py``'s five
   Revit exports (about 187 MB);
2. ``ifc-sql migrate``;
3. ``ifc-sql ingest … --viewer`` for each file: a file already loaded gets its scene in
   place, or keeps the one it has. The audit's files load in the audit's order, A to E,
   with D as A's new version (``--replaces``, A's model id read from its ingest);
4. ``ifc-sql dq``, which checks every model loaded;
5. ``ifc-sql demo``, with the options given here: it opens the page and plays the
   questions, or with ``--check`` compares each with the script.

The commands read this shell's IFC_SQL_* variables, as any ifc-sql command does: set
IFC_SQL_DATA_DIR for the embedded database, or IFC_SQL_DATABASE_URL for a server.
"""

import re
import subprocess
import sys
from pathlib import Path

import download_audit_samples
from download_samples import DEFAULT_DEST, SAMPLES, DownloadError, Sample, ensure_sample

ROOT = Path(__file__).resolve().parent.parent
IFC_SQL = Path(sys.executable).with_name("ifc-sql")
DISCIPLINES = {"arc": "ARC", "mep": "MEP", "ele": "ELE", "plu": "PLU"}
"""The Duplex files, in the order they load: the architectural model first."""
AUDIT_DISCIPLINES = {"a": "ARC", "b": "ARC", "c": "MEP", "d": "ARC", "e": "ARC"}
"""The audit's files, in the order the audit loaded them (docs/audit/harness/README.md)."""
REPLACED = {"d": "a"}
"""File D is file A re-exported with fire ratings added: loaded as A's new version."""
LOADED_AS = re.compile(r" as model (\d+) \(")
"""How ``ifc-sql ingest`` names the model in its line on stdout, loaded or already loaded."""


class DemoError(Exception):
    """A step cannot go on; the message says why."""


def script_of(options: list[str]) -> str:
    """The ``--script`` the options name for ``ifc-sql demo``; ``duplex`` by default."""
    for index, option in enumerate(options):
        if option == "--script" and index + 1 < len(options):
            return options[index + 1]
        if option.startswith("--script="):
            return option.removeprefix("--script=")
    return "duplex"


def commands(paths: dict[str, Path], demo_options: list[str]) -> list[list[str]]:
    """The ifc-sql commands the Duplex demo runs, in order, for the files at ``paths``."""
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


def ingest_command(paths: dict[str, Path], key: str, models: dict[str, int]) -> list[str]:
    """The ingest of one audit file; D's names the model of A, which ``models`` holds."""
    command = [str(IFC_SQL), "ingest", str(paths[key]), "--discipline", AUDIT_DISCIPLINES[key]]
    if key in REPLACED:
        command += ["--replaces", str(models[REPLACED[key]])]
    return [*command, "--viewer"]


def model_of(stdout: str) -> int:
    """The model id an ``ifc-sql ingest`` line on stdout names.

    Raises:
        DemoError: The line names no model.
    """
    found = LOADED_AS.search(stdout)
    if found is None:
        raise DemoError(f"ifc-sql ingest named no model: {stdout.strip()!r}")
    return int(found.group(1))


def _download(samples: dict[str, Sample], keys: list[str], dest: Path) -> dict[str, Path]:
    paths = {}
    for key in keys:
        paths[key], downloaded = ensure_sample(samples[key], dest)
        print(f"{'downloaded' if downloaded else 'have'} {paths[key].name}", flush=True)
    return paths


def _run(command: list[str], *, capture: bool = False) -> tuple[int, str]:
    """Run one ifc-sql command, its output shown; return its exit code and its stdout.

    With ``capture``, stdout is read, then shown; stderr, the log, is shown as it comes.
    """
    print(f"$ ifc-sql {' '.join(command[1:])}", flush=True)
    stdout = subprocess.PIPE if capture else None
    # Our own command, with arguments from this script and its user.
    done = subprocess.run(command, check=False, stdout=stdout, text=True, errors="replace")  # noqa: S603
    if capture:
        print(done.stdout, end="", flush=True)
    return done.returncode, done.stdout or ""


def _duplex(options: list[str]) -> int:
    paths = _download(SAMPLES, list(DISCIPLINES), ROOT / DEFAULT_DEST)
    for command in commands(paths, options):
        returned, _ = _run(command)
        if returned != 0:
            return returned
    return 0


def _audit(options: list[str]) -> int:
    paths = _download(
        download_audit_samples.AUDIT_SAMPLES,
        list(AUDIT_DISCIPLINES),
        ROOT / download_audit_samples.DEFAULT_DEST,
    )
    returned, _ = _run([str(IFC_SQL), "migrate"])
    if returned != 0:
        return returned
    models: dict[str, int] = {}
    for key in AUDIT_DISCIPLINES:
        returned, stdout = _run(ingest_command(paths, key, models), capture=True)
        if returned != 0:
            return returned
        models[key] = model_of(stdout)
    for command in ([str(IFC_SQL), "dq"], [str(IFC_SQL), "demo", *options]):
        returned, _ = _run(command)
        if returned != 0:
            return returned
    return 0


def main(argv: list[str] | None = None) -> int:
    """Download, load, check and play; the first failing command's exit code, else 0."""
    options = sys.argv[1:] if argv is None else argv
    try:
        return _audit(options) if script_of(options) == "audit" else _duplex(options)
    except (DownloadError, DemoError) as exc:
        print(f"demo: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("demo: stopped", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
