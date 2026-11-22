#!/usr/bin/env python3
"""Download the five Revit 2019-2022 exports the November 2026 audit used, pinned and verified.

Usage::

    uv run python scripts/download_audit_samples.py          # all five (about 187 MB)
    uv run python scripts/download_audit_samples.py a c      # just files A and C
    uv run python scripts/download_audit_samples.py --list

The files and why each is in the audit (docs/audit/2026-11-modern-revit-exports.md):

* **A**, a house from Revit 2021 (IFC4 Reference View, millimetres), and **B**, a
  28-storey tower from Revit 2022 (IFC2X3 Coordination View 2.0, metres): the two
  primary files.
* **C**, an office from Revit 2021 with 163 spaces: the room schedule.
* **D**, file A re-exported with fire ratings added: "what changed".
* **E**, a school from Revit 2019 exported with Revit property sets: the workaround.

Each is fetched from one upstream commit and kept only if its SHA-256 matches, exactly as
``download_samples.py`` does for the Duplex files, whose functions this script reuses.
None of the files is in Git LFS, so they all come from ``raw.githubusercontent.com``.

Licences: engine_web-ifc (B, E) is MPL-2.0. IfcSampleFiles (A, C, D) states no licence
("A collection of IFC sample files for software test use"). The files are used here for
testing only and are never committed (``data/`` is git-ignored); see DATA_ATTRIBUTION.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from download_samples import RAW_HOST, DownloadError, Sample, ensure_sample

DEFAULT_DEST = Path("data/audit")

IFC_SAMPLE_FILES = ("youshengCode/IfcSampleFiles", "7e6927982d7e57b29409bb631ef93d6770ef0665")
ENGINE_WEB_IFC = ("ThatOpen/engine_web-ifc", "d1962b4178c0fbfa87368e0b415c71ab97c0220d")
ENGINE_WEB_IFC_FOLDER = "tests/ifcfiles/public"


def _sample(
    key: str,
    upstream: tuple[str, str],
    path: str,
    sha256: str,
    size_bytes: int,
    description: str,
) -> Sample:
    repository, commit = upstream
    return Sample(
        key=key,
        # engine_web-ifc prefixes its test files with the issue they came from; the audit,
        # its harness and this repository use the plain file name.
        file_name=Path(path).name.removeprefix("ISSUE_098_").removeprefix("ISSUE_068_"),
        url=f"{RAW_HOST}/{repository}/{commit}/{path}",
        sha256=sha256,
        size_bytes=size_bytes,
        description=description,
    )


AUDIT_SAMPLES: dict[str, Sample] = {
    sample.key: sample
    for sample in (
        _sample(
            "a",
            IFC_SAMPLE_FILES,
            "Ifc4_Revit_ARC.ifc",
            "c4dd3612f126a0ca5a57f0e75d28f3e5da62751def85a452d849883c2892b518",
            13_646_137,
            "A: house, Revit 2021, IFC4 Reference View, mm",
        ),
        _sample(
            "b",
            ENGINE_WEB_IFC,
            f"{ENGINE_WEB_IFC_FOLDER}/ISSUE_098_R8_F1_MAB_AR_M3_XX_XXX_MO_7000.ifc",
            "08ab44c7a0e68efe6c1722c54235b405c0d649195254d92f964eaa47438aa62e",
            72_882_778,
            "B: 28-storey tower, Revit 2022, IFC2X3 Coordination View 2.0, m",
        ),
        _sample(
            "c",
            IFC_SAMPLE_FILES,
            "Ifc4_Revit_MEP.ifc",
            "829c08715d56256e04d04fa7be40ec68928e0397631c3b846efd72d003f96495",
            29_165_277,
            "C: office with 163 spaces, Revit 2021, IFC4 Reference View",
        ),
        _sample(
            "d",
            IFC_SAMPLE_FILES,
            "Ifc4_Revit_ARC_FireRatingAdded.ifc",
            "e015b8314d5bb64291a3ad9f7e3807beea5714469de28bb5d4c34aacd7bf4fbf",
            13_650_061,
            "D: file A re-exported with fire ratings added",
        ),
        _sample(
            "e",
            ENGINE_WEB_IFC,
            f"{ENGINE_WEB_IFC_FOLDER}/ISSUE_068_ARK_NUS_skolebygg.ifc",
            "362f0f5e5ad35557014100a6c4de75ec166f6dbd9197933170dc1423f4af2a76",
            57_304_675,
            "E: school, Revit 2019, IFC2X3 CV 2.0 with Revit property sets",
        ),
    )
}


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download the pinned Revit exports of the November 2026 audit."
    )
    parser.add_argument(
        "keys",
        nargs="*",
        metavar="KEY",
        help=f"files to fetch: {', '.join(AUDIT_SAMPLES)} (default: all)",
    )
    parser.add_argument(
        "--dest", type=Path, default=DEFAULT_DEST, help=f"target folder (default: {DEFAULT_DEST})"
    )
    parser.add_argument("--list", action="store_true", help="list the pinned files and exit")
    args = parser.parse_args(argv)
    unknown = [key for key in args.keys if key not in AUDIT_SAMPLES]
    if unknown:
        parser.error(f"unknown key(s) {', '.join(unknown)}; choose from {', '.join(AUDIT_SAMPLES)}")
    return args


def main(argv: list[str] | None = None) -> int:
    """Run the command line interface; returns the process exit code."""
    args = _parse_args(argv)

    if args.list:
        for sample in AUDIT_SAMPLES.values():
            size = f"{sample.size_bytes:>11,} B"
            print(f"{sample.key:<2} {size}  {sample.file_name}  {sample.description}")
        return 0

    for key in args.keys or list(AUDIT_SAMPLES):
        try:
            path, downloaded = ensure_sample(AUDIT_SAMPLES[key], args.dest)
        except DownloadError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        status = "downloaded" if downloaded else "already present"
        print(f"{status:<15} {path}  sha256 ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
