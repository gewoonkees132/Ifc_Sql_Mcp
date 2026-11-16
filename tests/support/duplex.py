"""The four Duplex sample files: where the local copies are, and the discipline of each."""

from pathlib import Path

from download_samples import DEFAULT_DEST, SAMPLES, ensure_sample

DISCIPLINES = {"arc": "ARC", "mep": "MEP", "ele": "ELE", "plu": "PLU"}


def sample_path(key: str) -> Path:
    """Return the local copy of a sample file, downloading and verifying it if needed."""
    path, _ = ensure_sample(SAMPLES[key], Path(DEFAULT_DEST))
    return path
