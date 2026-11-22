"""Tests for scripts/download_audit_samples.py: the pinned manifest and the command line."""

import hashlib
from pathlib import Path

import download_audit_samples as das
import download_samples as ds
import pytest

# The checksums the audit recorded (docs/audit/harness/README.md), file by file.
AUDIT_CHECKSUMS = {
    "Ifc4_Revit_ARC.ifc": "c4dd3612f126a0ca5a57f0e75d28f3e5da62751def85a452d849883c2892b518",
    "R8_F1_MAB_AR_M3_XX_XXX_MO_7000.ifc": (
        "08ab44c7a0e68efe6c1722c54235b405c0d649195254d92f964eaa47438aa62e"
    ),
    "Ifc4_Revit_MEP.ifc": "829c08715d56256e04d04fa7be40ec68928e0397631c3b846efd72d003f96495",
    "Ifc4_Revit_ARC_FireRatingAdded.ifc": (
        "e015b8314d5bb64291a3ad9f7e3807beea5714469de28bb5d4c34aacd7bf4fbf"
    ),
    "ARK_NUS_skolebygg.ifc": "362f0f5e5ad35557014100a6c4de75ec166f6dbd9197933170dc1423f4af2a76",
}


def test_the_manifest_pins_the_files_and_checksums_the_audit_used() -> None:
    assert list(das.AUDIT_SAMPLES) == ["a", "b", "c", "d", "e"]
    assert {s.file_name: s.sha256 for s in das.AUDIT_SAMPLES.values()} == AUDIT_CHECKSUMS


def test_every_file_comes_from_a_pinned_commit_on_the_raw_host() -> None:
    commits = {das.IFC_SAMPLE_FILES[1], das.ENGINE_WEB_IFC[1]}
    for sample in das.AUDIT_SAMPLES.values():
        assert sample.url.startswith(ds.RAW_HOST + "/"), sample.key
        assert any(f"/{commit}/" in sample.url for commit in commits), sample.key
        assert sample.url.endswith(sample.file_name), sample.key


def test_the_attribution_lists_every_pinned_file() -> None:
    attribution = (Path(__file__).parents[2] / "DATA_ATTRIBUTION.md").read_text(encoding="utf-8")
    for sample in das.AUDIT_SAMPLES.values():
        assert sample.sha256 in attribution, sample.key
        assert f"{sample.size_bytes:,}" in attribution, sample.key


def test_list_prints_every_file(capsys: pytest.CaptureFixture[str]) -> None:
    assert das.main(["--list"]) == 0

    output = capsys.readouterr().out
    assert all(sample.file_name in output for sample in das.AUDIT_SAMPLES.values())


def test_main_downloads_a_file_and_then_finds_it_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    upstream = tmp_path / "upstream.ifc"
    upstream.write_bytes(b"ISO-10303-21;\n")
    sample = ds.Sample(
        key="a",
        file_name="model.ifc",
        url=upstream.as_uri(),
        sha256=hashlib.sha256(upstream.read_bytes()).hexdigest(),
        size_bytes=upstream.stat().st_size,
        description="test",
    )
    monkeypatch.setattr(das, "AUDIT_SAMPLES", {"a": sample})
    dest = tmp_path / "audit"

    assert das.main(["a", "--dest", str(dest)]) == 0
    assert das.main(["--dest", str(dest)]) == 0

    output = capsys.readouterr().out
    assert "downloaded" in output
    assert "already present" in output
    assert (dest / "model.ifc").read_bytes() == b"ISO-10303-21;\n"


def test_main_reports_a_failed_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sample = ds.Sample("a", "model.ifc", (tmp_path / "missing.ifc").as_uri(), "0" * 64, 1, "x")
    monkeypatch.setattr(das, "AUDIT_SAMPLES", {"a": sample})

    assert das.main(["--dest", str(tmp_path / "audit")]) == 1
    assert "error: could not download" in capsys.readouterr().err


def test_an_unknown_key_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as raised:
        das.main(["z"])

    assert raised.value.code == 2
