"""The page's files are in the built wheel, so ``ifc-sql view`` works from an install too."""

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from ifc_sql_mcp.viewer.http import STATIC

ROOT = Path(__file__).resolve().parents[3]


def test_the_wheel_holds_every_file_of_the_page_byte_for_byte(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.fail("this test builds the wheel with uv, which is not on PATH")

    subprocess.run(  # noqa: S603 - uv, found above, with fixed arguments
        [uv, "build", "--wheel", "--out-dir", str(tmp_path), str(ROOT)],
        check=True,
        capture_output=True,
    )

    [wheel] = tmp_path.glob("ifc_sql_mcp-*.whl")
    with zipfile.ZipFile(wheel) as archive:
        packed = {
            name.removeprefix("ifc_sql_mcp/viewer/static/"): archive.read(name)
            for name in archive.namelist()
            if name.startswith("ifc_sql_mcp/viewer/static/") and not name.endswith("/")
        }
    on_disk = {
        path.relative_to(STATIC).as_posix(): path.read_bytes()
        for path in STATIC.rglob("*")
        if path.is_file()
    }
    assert packed == on_disk
    assert "vendor/three/three.core.js" in packed
    assert "index.html" in packed
