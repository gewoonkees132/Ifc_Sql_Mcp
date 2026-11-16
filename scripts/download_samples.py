#!/usr/bin/env python3
"""Download the buildingSMART "Duplex Apartment" sample files, pinned and checksum-verified.

Usage::

    uv run python scripts/download_samples.py            # all files
    uv run python scripts/download_samples.py arc        # just the architectural model
    uv run python scripts/download_samples.py --list

Why it is built this way:

* **Pinned commit + SHA-256.** Every number in the docs must be reproducible, so we fetch
  one exact upstream commit and refuse any file whose bytes differ from the recorded hash.
* **Two hosts.** The ``.ifc`` files are stored in Git LFS. ``raw.githubusercontent.com``
  serves only the ~130-byte LFS *pointer* for those, so they come from
  ``media.githubusercontent.com``; ordinary blobs (the COBie ``.xlsx``) are not in LFS and
  only exist on the raw host.
* **Standard library only.** It runs on any Python 3.10+ before the project environment
  exists, and has nothing to keep in sync with the project's dependencies.
* **Atomic writes.** Data is streamed to ``<name>.part`` and renamed only after the hash
  matches, so an interrupted or corrupted download never looks like a good file.

The files are CC BY 4.0; see DATA_ATTRIBUTION.md for the required attribution.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

UPSTREAM_REPO = "buildingsmart-community/Community-Sample-Test-Files"
UPSTREAM_COMMIT = "31eb1dd0a0406fb9ebf0ebd823b48e1ec95afc08"
UPSTREAM_FOLDER = "IFC 2.3.0.1 (IFC 2x3)/Duplex Apartment"

LFS_HOST = "https://media.githubusercontent.com/media"
RAW_HOST = "https://raw.githubusercontent.com"

DEFAULT_DEST = Path("data/samples")
CHUNK_SIZE = 1024 * 1024
TIMEOUT_SECONDS = 60
LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/"


class DownloadError(RuntimeError):
    """A file could not be fetched or did not match its pinned checksum."""


@dataclass(frozen=True)
class Sample:
    """One pinned upstream file."""

    key: str
    file_name: str
    url: str
    sha256: str
    size_bytes: int
    description: str


def upstream_url(file_name: str, *, in_lfs: bool) -> str:
    """Build the download URL for a file in the pinned upstream folder.

    Parentheses are left unescaped so the URL matches the one GitHub shows in its UI.
    """
    host = LFS_HOST if in_lfs else RAW_HOST
    path = urllib.parse.quote(f"{UPSTREAM_FOLDER}/{file_name}", safe="/()")
    return f"{host}/{UPSTREAM_REPO}/{UPSTREAM_COMMIT}/{path}"


def _sample(
    key: str, file_name: str, sha256: str, size_bytes: int, description: str, *, in_lfs: bool
) -> Sample:
    return Sample(
        key=key,
        file_name=file_name,
        url=upstream_url(file_name, in_lfs=in_lfs),
        sha256=sha256,
        size_bytes=size_bytes,
        description=description,
    )


SAMPLES: dict[str, Sample] = {
    sample.key: sample
    for sample in (
        _sample(
            "arc",
            "Duplex_A_20110907.ifc",
            "b347a2c8aa8fff6db896a4417a9c50c22ac0ccd7c5cfc22b99b8d29336c606ed",
            2_380_763,
            "Architectural model (Revit Architecture 2011)",
            in_lfs=True,
        ),
        _sample(
            "mep",
            "Duplex_MEP_20110907.ifc",
            "13976a8e223f177a6d7123679e4b02e750cd90c13f7bb20c593be125d9407119",
            17_871_432,
            "Mechanical model (Revit MEP 2011)",
            in_lfs=True,
        ),
        _sample(
            "ele",
            "Duplex_Electrical_20121207.ifc",
            "d47b014ed5b3428496683ee75e4664013f6df8237ea571b8d13f877cea0b7a94",
            1_602_758,
            "Electrical model (Revit 2013)",
            in_lfs=True,
        ),
        _sample(
            "plu",
            "Duplex_Plumbing_20121113.ifc",
            "abfaf5c0979b6b4b05182aec7ada8945374bdb414db76c763724d549b41b212b",
            31_556_138,
            "Plumbing model (Revit 2013)",
            in_lfs=True,
        ),
        _sample(
            "cobie",
            "2012-03-23-Duplex-Handover.xlsx",
            "26cdaf0bbf34b67f0f68c78de5bc3c1c3a285df97223c2704c9175b1936fdd4a",
            233_186,
            "COBie 2.26 handover spreadsheet",
            in_lfs=False,
        ),
    )
}


def sha256_of(path: Path) -> str:
    """Return the hex SHA-256 of a file, reading it in chunks to bound memory use."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_verified(url: str, target: Path, expected_sha256: str) -> None:
    """Stream ``url`` to ``target``, keeping it only if its SHA-256 matches.

    Raises:
        DownloadError: on network failure or checksum mismatch. ``target`` is left
            untouched and the temporary ``.part`` file is removed.
    """
    if urllib.parse.urlsplit(url).scheme not in {"https", "file"}:
        raise DownloadError(f"refusing to download over a non-HTTPS URL: {url}")

    partial = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    first_bytes = b""
    try:
        # The scheme is checked above; `file` is allowed so tests run without a network.
        with (
            urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response,  # noqa: S310
            partial.open("wb") as out,
        ):
            while chunk := response.read(CHUNK_SIZE):
                if not first_bytes:
                    first_bytes = chunk[: len(LFS_POINTER_PREFIX)]
                digest.update(chunk)
                out.write(chunk)
    except OSError as exc:  # URLError, HTTPError and socket timeouts are all OSErrors.
        partial.unlink(missing_ok=True)
        raise DownloadError(f"could not download {url}: {exc}") from exc

    actual = digest.hexdigest()
    if actual != expected_sha256:
        partial.unlink(missing_ok=True)
        hint = ""
        if first_bytes.startswith(LFS_POINTER_PREFIX):
            hint = " (received a Git LFS pointer instead of the file; use the LFS media host)"
        raise DownloadError(
            f"checksum mismatch for {target.name}: expected {expected_sha256}, got {actual}{hint}"
        )
    partial.replace(target)


def ensure_sample(sample: Sample, dest_dir: Path) -> tuple[Path, bool]:
    """Make sure ``sample`` exists in ``dest_dir`` with the right checksum.

    Returns:
        The file path and whether it had to be downloaded (``False`` = already valid).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / sample.file_name
    if target.exists() and sha256_of(target) == sample.sha256:
        return target, False
    # A missing file and a corrupted one are handled the same way: fetch a fresh copy.
    fetch_verified(sample.url, target, sample.sha256)
    return target, True


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download the pinned Duplex Apartment sample files (CC BY 4.0)."
    )
    parser.add_argument(
        "keys",
        nargs="*",
        metavar="KEY",
        help=f"files to fetch: {', '.join(SAMPLES)} (default: all)",
    )
    parser.add_argument(
        "--dest", type=Path, default=DEFAULT_DEST, help=f"target folder (default: {DEFAULT_DEST})"
    )
    parser.add_argument("--list", action="store_true", help="list the pinned files and exit")
    args = parser.parse_args(argv)
    unknown = [key for key in args.keys if key not in SAMPLES]
    if unknown:
        parser.error(f"unknown key(s) {', '.join(unknown)}; choose from {', '.join(SAMPLES)}")
    return args


def main(argv: list[str] | None = None) -> int:
    """Run the command line interface; returns the process exit code."""
    args = _parse_args(argv)

    if args.list:
        for sample in SAMPLES.values():
            size = f"{sample.size_bytes:>11,} B"
            print(f"{sample.key:<6} {size}  {sample.file_name}  {sample.description}")
        return 0

    keys: list[str] = args.keys or list(SAMPLES)
    for key in keys:
        sample = SAMPLES[key]
        try:
            path, downloaded = ensure_sample(sample, args.dest)
        except DownloadError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        status = "downloaded" if downloaded else "already present"
        print(f"{status:<15} {path}  sha256 ok")

    print("Sample data: BSI (2020) CC BY 4.0, see DATA_ATTRIBUTION.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
