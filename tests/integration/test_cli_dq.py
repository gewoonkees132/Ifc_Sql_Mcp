"""``ifc-sql dq`` end to end: which models it checks, exit codes, stdout and stderr."""

from collections.abc import Callable
from pathlib import Path

import make_revit_quirks
import pytest
from cli_output import plain
from sqlalchemy import Engine, text
from typer.testing import CliRunner, Result

from ifc_sql_mcp import cli
from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.migrate import head_revision, upgrade
from ifc_sql_mcp.dq.catalogue import CATALOGUE, load_catalogue
from ifc_sql_mcp.dq.run import UnknownModelError
from ifc_sql_mcp.ingest.pipeline import ingest_file

FIXTURE = make_revit_quirks.OUTPUT
MODEL_1 = (
    "checked model 1 (ARC, revit_quirks.ifc) in dq run {run}: 0 error, 11 warning, 5 info findings"
)
MODEL_2 = (
    "checked model 2 (MEP, revit_quirks.ifc) in dq run {run}: 0 error, 11 warning, 5 info findings"
)


# Every rule the shipped catalogue has; each run evaluates them all.
RULE_COUNT = len(load_catalogue())


def _dq(*model_ids: str) -> Result:
    return CliRunner().invoke(cli.app, ["dq", *model_ids])


def _count(engine: Engine, table: str) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text(f"SELECT count(*) FROM ifc.{table}")).scalar_one())  # noqa: S608


@pytest.fixture
def database(
    ingest_settings: Settings, ingest_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Engine:
    """Point the CLI at the ingest database (emptied after the test)."""
    monkeypatch.setenv("IFC_SQL_DATABASE_URL", ingest_settings.database_url)
    return ingest_engine


@pytest.fixture
def two_models(database: Engine, tmp_path: Path) -> Engine:
    """The fixture as model 1 (ARC), and one byte longer as model 2 (MEP)."""
    changed = tmp_path / "revit_quirks.ifc"
    changed.write_bytes(FIXTURE.read_bytes() + b"\n")
    ingest_file(database, FIXTURE, "ARC")
    ingest_file(database, changed, "MEP")
    return database


def test_dq_checks_every_loaded_model_and_exits_0(two_models: Engine) -> None:
    result = _dq()

    assert result.exit_code == 0, result.output
    assert result.stdout == f"{MODEL_1.format(run=1)}\n{MODEL_2.format(run=2)}\n"
    # Logs go to stderr: the sync, then one line per rule that found something.
    stderr = result.stderr
    assert (
        f'msg="rules synchronised" rules={RULE_COUNT} added={RULE_COUNT} updated=0 retired=0 '
        "deleted=0"
    ) in stderr
    assert "msg=findings dq_run_id=1 rule_id=value_is_empty severity=info count=3" in stderr
    assert f"msg=checked dq_run_id=2 model_id=2 rules={RULE_COUNT} findings=16" in stderr


def test_dq_checks_only_the_given_models_once_each(two_models: Engine) -> None:
    result = _dq("2", "2")

    assert result.exit_code == 0, result.output
    assert result.stdout == f"{MODEL_2.format(run=1)}\n"
    assert _count(two_models, "dq_run") == 1


def test_an_unknown_model_id_is_a_usage_error_and_nothing_is_written(two_models: Engine) -> None:
    result = _dq("1", "7")

    assert result.exit_code == 2
    assert "Invalid value for MODEL_ID: no model with id 7" in plain(result.output)
    # The rules were not synchronised either: that transaction was rolled back.
    assert (_count(two_models, "dq_rule"), _count(two_models, "dq_run")) == (0, 0)


def test_dq_with_no_models_says_so_and_exits_0(database: Engine) -> None:
    result = _dq()

    assert result.exit_code == 0, result.output
    assert result.stdout == "no models are loaded; nothing to check\n"
    assert _count(database, "dq_rule") == RULE_COUNT


def test_a_failed_run_is_reported_and_the_other_models_are_still_checked(
    two_models: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real failure: an extra rule that divides by zero for model 1 only."""
    catalogue = tmp_path / "dq_rules.yaml"
    catalogue.write_text(
        CATALOGUE.read_text(encoding="utf-8")
        + """
  - rule_id: fails_for_model_1
    description: Divides by zero when :model_id is 1.
    severity: error
    dimension: validity
    sql: SELECT NULL::bigint AS object_id, 'x' AS message WHERE 1 / (:model_id - 1) < 0
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "CATALOGUE", catalogue)

    result = _dq()

    assert result.exit_code == 1
    assert (
        "dq of model 1 failed (dq run 1): RuleError: rule fails_for_model_1 failed: "
        "DivisionByZero: division by zero"
    ) in plain(result.stderr)
    assert result.stdout == f"{MODEL_2.format(run=2)}\n"
    assert "Traceback" not in result.output


def test_an_invalid_catalogue_exits_1_with_the_reason_and_writes_nothing(
    two_models: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalogue = tmp_path / "dq_rules.yaml"
    catalogue.write_text(
        CATALOGUE.read_text(encoding="utf-8").replace("severity: info", "severity: notice", 1),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "CATALOGUE", catalogue)

    result = _dq()

    assert result.exit_code == 1
    assert (
        "invalid rule catalogue: dq_rules.yaml: rule 2 (value_is_not_applicable): severity must "
        "be one of error, warning, info; got 'notice'"
    ) in plain(result.stderr)
    assert (_count(two_models, "dq_rule"), _count(two_models, "dq_run")) == (0, 0)


def test_a_model_deleted_before_its_check_is_reported(
    two_models: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another session deletes the model between the id check and the run."""

    def deleted(engine: Engine, model_id: int, rules: object) -> None:
        raise UnknownModelError(f"no model with id {model_id}")

    monkeypatch.setattr(cli, "check_model", deleted)

    result = _dq("1")

    assert result.exit_code == 1
    assert "model 1 was deleted before it was checked: no model with id 1" in plain(result.stderr)


@pytest.mark.parametrize(
    ("revision", "found"), [(None, "not migrated"), ("0006", "at revision 0006")]
)
def test_dq_on_a_database_that_is_not_at_the_latest_revision_exits_1(
    create_database: Callable[[str], Settings],
    monkeypatch: pytest.MonkeyPatch,
    revision: str | None,
    found: str,
) -> None:
    settings = create_database("test_outdated")
    if revision is not None:
        upgrade(settings, revision)
    monkeypatch.setenv("IFC_SQL_DATABASE_URL", settings.database_url)

    result = _dq()

    assert result.exit_code == 1
    assert (
        f"is {found}, but dq needs revision {head_revision()}: run `ifc-sql migrate` first"
        in plain(result.stderr)
    )
