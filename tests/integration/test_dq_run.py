"""Data quality runs against a real database: success, re-runs, failures, views and deletes.

These tests commit, so they use ``ingest_engine`` (a separate database, emptied after
each test) rather than the rolled-back ``conn``. The fixture file is ingested first; its
18 findings per rule are checked in ``test_dq_rules.py``.
"""

import logging
from collections import Counter
from pathlib import Path

import make_revit_quirks
import pytest
from sqlalchemy import Engine, RowMapping, text

from ifc_sql_mcp.dq.catalogue import Rule, load_catalogue
from ifc_sql_mcp.dq.run import (
    DqRunError,
    UnknownModelError,
    check_model,
    loaded_model_ids,
)
from ifc_sql_mcp.dq.sync import sync_rules
from ifc_sql_mcp.ingest.pipeline import ingest_file

RULES = load_catalogue()
FIXTURE = make_revit_quirks.OUTPUT

DIVIDES_BY_ZERO = Rule(
    rule_id="divides_by_zero",
    description="A broken query.",
    severity="error",
    dimension="validity",
    sql="SELECT o.object_id, (1 / 0)::text AS message FROM ifc.object AS o "
    "WHERE o.model_id = :model_id",
)
NO_EVALUATION = Rule(
    rule_id="no_evaluation",
    description="Built by hand without issue_code or sql; the catalogue never allows this.",
    severity="error",
    dimension="validity",
)


@pytest.fixture
def model_id(ingest_engine: Engine) -> int:
    with ingest_engine.begin() as connection:
        sync_rules(connection, RULES)
    return ingest_file(ingest_engine, FIXTURE, "ARC").model_id


def _runs(engine: Engine) -> list[RowMapping]:
    with engine.connect() as connection:
        return list(
            connection.execute(text("SELECT * FROM ifc.dq_run ORDER BY dq_run_id")).mappings()
        )


def _findings(engine: Engine, run_id: int) -> Counter[tuple[object, ...]]:
    """A run's findings as a multiset of (rule, object, severity, message)."""
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT rule_id, object_id, severity, message FROM ifc.dq_result "
                "WHERE dq_run_id = :run"
            ),
            {"run": run_id},
        ).all()
    return Counter(tuple(row) for row in rows)


def _summary(engine: Engine, model_id: int) -> RowMapping:
    with engine.connect() as connection:
        return (
            connection.execute(
                text(
                    "SELECT dq_run_id, dq_finished_at, dq_error_count, dq_warning_count, "
                    "dq_info_count FROM ifc.v_model_summary WHERE model_id = :m"
                ),
                {"m": model_id},
            )
            .mappings()
            .one()
        )


# --------------------------------------------------------------------------- a run


def test_a_run_writes_its_findings_and_succeeds(ingest_engine: Engine, model_id: int) -> None:
    result = check_model(ingest_engine, model_id, RULES)

    assert (result.run_id, result.model_id, result.discipline, result.file_name) == (
        1,
        model_id,
        "ARC",
        "revit_quirks.ifc",
    )
    assert list(result.finding_counts) == [rule.rule_id for rule in RULES]
    assert result.severity_counts == {"error": 0, "warning": 11, "info": 7}
    # The one loader-coverage finding is the fixture's unsupported property set (A9).
    assert result.scope_counts == {
        "model_content": {"error": 0, "warning": 10, "info": 7},
        "loader_coverage": {"error": 0, "warning": 1, "info": 0},
    }
    stored = Counter(rule for rule, *_ in _findings(ingest_engine, result.run_id).elements())
    assert stored == {rule: n for rule, n in result.finding_counts.items() if n}
    (run,) = _runs(ingest_engine)
    assert (run["dq_run_id"], run["model_id"], run["status"], run["error"]) == (
        result.run_id,
        model_id,
        "succeeded",
        None,
    )
    assert run["finished_at"] >= run["started_at"]


def test_running_again_records_a_second_run_with_the_same_findings(
    ingest_engine: Engine, model_id: int
) -> None:
    first = check_model(ingest_engine, model_id, RULES)
    second = check_model(ingest_engine, model_id, RULES)

    assert second.run_id == first.run_id + 1
    assert _findings(ingest_engine, second.run_id) == _findings(ingest_engine, first.run_id)
    assert sum(_findings(ingest_engine, first.run_id).values()) == 18
    assert [run["status"] for run in _runs(ingest_engine)] == ["succeeded", "succeeded"]


def test_findings_and_a_summary_are_logged(
    ingest_engine: Engine, model_id: int, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="ifc_sql_mcp")

    result = check_model(ingest_engine, model_id, RULES)

    logged = {
        record.rule_id: (record.severity, record.count)
        for record in caplog.records
        if record.getMessage() == "findings"
    }
    # One line per rule that found something, none for the rest.
    assert logged == {
        rule.rule_id: (rule.severity, result.finding_counts[rule.rule_id])
        for rule in RULES
        if result.finding_counts[rule.rule_id]
    }
    assert len(logged) == 11
    (checked,) = [record for record in caplog.records if record.getMessage() == "checked"]
    assert (checked.dq_run_id, checked.rules, checked.findings) == (result.run_id, len(RULES), 18)


# --------------------------------------------------------------------------- failures


@pytest.mark.parametrize(
    ("broken", "error"),
    [
        (
            DIVIDES_BY_ZERO,
            "RuleError: rule divides_by_zero failed: DivisionByZero: division by zero",
        ),
        (
            NO_EVALUATION,
            "RuleError: rule no_evaluation failed: ValueError: rule no_evaluation has neither an "
            "issue_code nor sql",
        ),
    ],
    ids=["database error", "rule without a query"],
)
def test_a_failed_run_writes_nothing_but_the_failed_run(
    ingest_engine: Engine, model_id: int, broken: Rule, error: str
) -> None:
    """The broken rule comes last, after every shipped rule has inserted its findings."""
    rules = (*RULES, broken)
    with ingest_engine.begin() as connection:
        sync_rules(connection, rules)

    with pytest.raises(DqRunError) as excinfo:
        check_model(ingest_engine, model_id, rules)

    assert str(excinfo.value) == error
    (run,) = _runs(ingest_engine)
    assert (run["dq_run_id"], run["status"], run["error"]) == (
        excinfo.value.run_id,
        "failed",
        error,
    )
    assert run["finished_at"] >= run["started_at"]
    assert _findings(ingest_engine, run["dq_run_id"]) == Counter()


def test_an_unknown_model_is_refused_before_anything_is_written(
    ingest_engine: Engine, model_id: int
) -> None:
    with pytest.raises(UnknownModelError, match="no model with id 99"):
        check_model(ingest_engine, 99, RULES)

    assert _runs(ingest_engine) == []


# --------------------------------------------------------------------------- views


def test_the_model_summary_counts_the_latest_successful_run(
    ingest_engine: Engine, model_id: int
) -> None:
    never_checked = _summary(ingest_engine, model_id)
    assert dict(never_checked) == {
        "dq_run_id": None,
        "dq_finished_at": None,
        "dq_error_count": None,  # NULL, not "no errors"
        "dq_warning_count": None,
        "dq_info_count": None,
    }

    result = check_model(ingest_engine, model_id, RULES)
    checked = _summary(ingest_engine, model_id)
    assert (checked["dq_run_id"], checked["dq_error_count"]) == (result.run_id, 0)
    assert (checked["dq_warning_count"], checked["dq_info_count"]) == (11, 7)

    # A later failed run changes nothing: the summary still shows the last good one.
    rules = (*RULES, DIVIDES_BY_ZERO)
    with ingest_engine.begin() as connection:
        sync_rules(connection, rules)
    with pytest.raises(DqRunError):
        check_model(ingest_engine, model_id, rules)
    assert _summary(ingest_engine, model_id) == checked


def test_v_dq_finding_lists_the_latest_run_with_rule_and_object(
    ingest_engine: Engine, model_id: int
) -> None:
    check_model(ingest_engine, model_id, RULES)
    latest = check_model(ingest_engine, model_id, RULES)

    with ingest_engine.connect() as connection:
        rows = list(
            connection.execute(
                text("SELECT * FROM ifc.v_dq_finding WHERE model_id = :m ORDER BY rule_id"),
                {"m": model_id},
            ).mappings()
        )
        summary = _summary(ingest_engine, model_id)

    assert {row["dq_run_id"] for row in rows} == {latest.run_id}
    assert len(rows) == 18
    severities = Counter(row["severity"] for row in rows)
    assert (severities["error"], severities["warning"], severities["info"]) == (
        summary["dq_error_count"],
        summary["dq_warning_count"],
        summary["dq_info_count"],
    )
    slab = next(row for row in rows if row["rule_id"] == "negative_quantity")
    assert {key: slab[key] for key in ("dimension", "global_id", "ifc_class", "object_name")} == {
        "dimension": "validity",
        "global_id": make_revit_quirks.SLAB,
        "ifc_class": "IfcSlab",
        "object_name": "Floor:Generic 150mm:200401",
    }


# --------------------------------------------------------------------------- deletes


def test_deleting_a_model_removes_its_runs_and_findings_and_keeps_the_rules(
    ingest_engine: Engine, model_id: int
) -> None:
    check_model(ingest_engine, model_id, RULES)

    with ingest_engine.begin() as connection:
        connection.execute(text("DELETE FROM ifc.model WHERE model_id = :m"), {"m": model_id})
        counts = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM ifc.dq_run) AS runs, "
                "(SELECT count(*) FROM ifc.dq_result) AS results, "
                "(SELECT count(*) FROM ifc.dq_rule) AS rules"
            )
        ).one()

    assert tuple(counts) == (0, 0, len(RULES))


def test_loaded_model_ids_lists_every_model_oldest_first(
    ingest_engine: Engine, model_id: int, tmp_path: Path
) -> None:
    changed = tmp_path / "revit_quirks.ifc"
    changed.write_bytes(FIXTURE.read_bytes() + b"\n")  # another file version, another model
    second = ingest_file(ingest_engine, changed, "ARC").model_id

    with ingest_engine.connect() as connection:
        assert loaded_model_ids(connection) == [model_id, second]
