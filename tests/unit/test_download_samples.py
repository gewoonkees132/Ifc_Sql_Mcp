"""Tests for scripts/download_samples.py, using file:// URLs so no network is needed."""

import hashlib
from pathlib import Path

import download_samples as ds
import pytest

PAYLOAD = b"ISO-10303-21;\nHEADER;\n"
PAYLOAD_SHA256 = hashlib.sha256(PAYLOAD).hexdigest()

# The URL given in the project brief for the primary file, character for character.
BRIEF_PRIMARY_URL = (
    "https://media.githubusercontent.com/media/buildingsmart-community/"
    "Community-Sample-Test-Files/31eb1dd0a0406fb9ebf0ebd823b48e1ec95afc08/"
    "IFC%202.3.0.1%20(IFC%202x3)/Duplex%20Apartment/Duplex_A_20110907.ifc"
)


def _local_sample(source: Path, sha256: str = PAYLOAD_SHA256) -> ds.Sample:
    return ds.Sample(
        key="test",
        file_name="model.ifc",
        url=source.as_uri(),
        sha256=sha256,
        size_bytes=len(PAYLOAD),
        description="test",
    )


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "upstream" / "model.ifc"
    path.parent.mkdir()
    path.write_bytes(PAYLOAD)
    return path


# --------------------------------------------------------------------------- manifest


def test_primary_url_matches_the_pinned_upstream_url() -> None:
    assert ds.SAMPLES["arc"].url == BRIEF_PRIMARY_URL
    assert ds.SAMPLES["arc"].sha256 == (
        "b347a2c8aa8fff6db896a4417a9c50c22ac0ccd7c5cfc22b99b8d29336c606ed"
    )


def test_ifc_files_come_from_lfs_host_and_xlsx_from_raw_host() -> None:
    for sample in ds.SAMPLES.values():
        expected_host = ds.RAW_HOST if sample.file_name.endswith(".xlsx") else ds.LFS_HOST
        assert sample.url.startswith(expected_host + "/"), sample.key


def test_every_sample_is_pinned_to_the_same_commit_with_a_full_sha256() -> None:
    for sample in ds.SAMPLES.values():
        assert f"/{ds.UPSTREAM_COMMIT}/" in sample.url
        assert len(sample.sha256) == 64
        int(sample.sha256, 16)  # raises if not hex


# --------------------------------------------------------------------------- downloading


def test_ensure_sample_downloads_and_verifies(tmp_path: Path, source: Path) -> None:
    path, downloaded = ds.ensure_sample(_local_sample(source), tmp_path / "dest")

    assert downloaded is True
    assert path.read_bytes() == PAYLOAD
    assert not path.with_name(path.name + ".part").exists()


def test_ensure_sample_skips_a_file_that_is_already_valid(tmp_path: Path, source: Path) -> None:
    dest = tmp_path / "dest"
    ds.ensure_sample(_local_sample(source), dest)
    source.unlink()  # any second download attempt would now fail

    path, downloaded = ds.ensure_sample(_local_sample(source), dest)

    assert downloaded is False
    assert path.read_bytes() == PAYLOAD


def test_ensure_sample_replaces_a_corrupted_file(tmp_path: Path, source: Path) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "model.ifc").write_bytes(b"truncated")

    path, downloaded = ds.ensure_sample(_local_sample(source), dest)

    assert downloaded is True
    assert path.read_bytes() == PAYLOAD


def test_checksum_mismatch_raises_and_leaves_nothing_behind(tmp_path: Path, source: Path) -> None:
    dest = tmp_path / "dest"

    with pytest.raises(ds.DownloadError, match=r"checksum mismatch for model\.ifc"):
        ds.ensure_sample(_local_sample(source, sha256="0" * 64), dest)

    assert list(dest.iterdir()) == []


def test_lfs_pointer_gets_a_helpful_hint(tmp_path: Path) -> None:
    pointer = tmp_path / "pointer.ifc"
    pointer.write_bytes(b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 1\n")

    with pytest.raises(ds.DownloadError, match="Git LFS pointer"):
        ds.fetch_verified(pointer.as_uri(), tmp_path / "out.ifc", PAYLOAD_SHA256)


def test_network_errors_are_wrapped(tmp_path: Path) -> None:
    missing = (tmp_path / "does-not-exist.ifc").as_uri()

    with pytest.raises(ds.DownloadError, match="could not download"):
        ds.fetch_verified(missing, tmp_path / "out.ifc", PAYLOAD_SHA256)

    assert not (tmp_path / "out.ifc.part").exists()


def test_plain_http_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ds.DownloadError, match="non-HTTPS"):
        ds.fetch_verified("http://example.com/x.ifc", tmp_path / "x.ifc", PAYLOAD_SHA256)


def test_sha256_of_matches_hashlib(source: Path) -> None:
    assert ds.sha256_of(source) == PAYLOAD_SHA256


# --------------------------------------------------------------------------- command line


def test_list_prints_every_pinned_file(capsys: pytest.CaptureFixture[str]) -> None:
    assert ds.main(["--list"]) == 0

    out = capsys.readouterr().out
    for sample in ds.SAMPLES.values():
        assert sample.file_name in out


def test_unknown_key_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as exc_info:
        ds.main(["not-a-key"])

    assert exc_info.value.code == 2


def test_main_downloads_selected_keys(
    tmp_path: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ds, "SAMPLES", {"test": _local_sample(source)})

    assert ds.main(["test", "--dest", str(tmp_path / "dest")]) == 0
    assert (tmp_path / "dest" / "model.ifc").read_bytes() == PAYLOAD


def test_main_returns_1_on_checksum_failure(
    tmp_path: Path,
    source: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(ds, "SAMPLES", {"test": _local_sample(source, sha256="0" * 64)})

    assert ds.main(["--dest", str(tmp_path / "dest")]) == 1
    assert "checksum mismatch" in capsys.readouterr().err
