"""The committed fixture is reproducible, small and valid IFC2X3 (bar one deliberate defect)."""

from pathlib import Path

import ifcopenshell
import ifcopenshell.validate
import make_revit_quirks
import pytest

FIXTURE = make_revit_quirks.OUTPUT


def test_regenerating_the_fixture_gives_the_committed_bytes(tmp_path: Path) -> None:
    first, second = tmp_path / "first.ifc", tmp_path / "second.ifc"
    make_revit_quirks.write(first)
    make_revit_quirks.write(second)

    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes() == FIXTURE.read_bytes(), (
        "tests/fixtures/revit_quirks.ifc is out of date: "
        "run `uv run python tests/fixtures/make_revit_quirks.py` and commit the result"
    )


def test_the_fixture_is_far_below_the_pre_commit_size_limit() -> None:
    assert FIXTURE.stat().st_size < 50_000  # pre-commit refuses files over 500 KB


# IfcOpenShell's rule executor reads its rule files with open(...).read() and never closes
# them; the warning comes from the library, not from this project.
@pytest.mark.filterwarnings("ignore:unclosed file .*ifcopenshell.express.rules:ResourceWarning")
def test_the_fixture_breaks_only_the_rule_revit_2013_breaks() -> None:
    logger = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(ifcopenshell.open(str(FIXTURE)), logger, express_rules=True)

    # Revit 2013 repeats a property name within one set; IFC's WR32 forbids that.
    assert [(s["type"], s["attribute"]) for s in logger.statements] == [
        ("entity_rule", "IfcPropertySet.WR32")
    ]
    assert "'Other'" in str(logger.statements[0]["instance"])
