"""The ingest pipeline against a real database: runs, skips, failures, races and deletes.

These tests commit, so they use ``ingest_engine`` (a separate database, emptied after
each test) rather than the rolled-back ``conn``.
"""

import dataclasses
import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import make_revit_quirks
import pytest
from counts import FIXTURE_ROWS, table_counts, total_rows
from geometry_model import BEAM, SLAB, SPACE, WALL, share_global_ids, write_measured_model
from locks import wait_until_a_session_waits_on_a_lock
from rows import add_model
from sqlalchemy import Engine, RowMapping, text

from ifc_sql_mcp.ingest import pipeline
from ifc_sql_mcp.ingest.load import LoadResult, load
from ifc_sql_mcp.ingest.pipeline import IngestError, IngestResult, ingest_file
from ifc_sql_mcp.ingest.records import ModelRecords

FIXTURE = make_revit_quirks.OUTPUT
NOTHING = dict.fromkeys(FIXTURE_ROWS, 0)


def _runs(engine: Engine) -> list[RowMapping]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                text("SELECT * FROM ifc.ingestion_run ORDER BY ingestion_run_id")
            ).mappings()
        )


def _totals(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return total_rows(connection)


def _refuse_to_parse(path: Path) -> Any:
    raise AssertionError(f"{path.name} was parsed")


# --------------------------------------------------------------------------- loading


def test_a_new_file_is_loaded_and_its_run_succeeds(ingest_engine: Engine) -> None:
    result = ingest_file(ingest_engine, FIXTURE, "ARC")

    assert (result.status, result.model_id, result.discipline) == ("succeeded", 1, "ARC")
    assert result.row_counts == FIXTURE_ROWS
    with ingest_engine.connect() as connection:
        assert table_counts(connection, result.model_id) == FIXTURE_ROWS
    (run,) = _runs(ingest_engine)
    assert {key: run[key] for key in ("status", "model_id", "file_name", "error")} == {
        "status": "succeeded",
        "model_id": result.model_id,
        "file_name": "revit_quirks.ifc",
        "error": None,
    }
    assert run["row_counts"] == FIXTURE_ROWS
    assert run["finished_at"] >= run["started_at"]


def test_issue_counts_are_returned_and_logged_per_code(
    ingest_engine: Engine, fixture_records: ModelRecords, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="ifc_sql_mcp")
    expected = Counter(issue.code.value for issue in fixture_records.issues)

    result = ingest_file(ingest_engine, FIXTURE, "ARC")

    logged = {
        record.code: record.count
        for record in caplog.records
        if record.getMessage() == "transform issues"
    }
    assert logged == result.issue_counts == dict(expected)
    assert sum(logged.values()) == FIXTURE_ROWS["ingest_issue"]


# --------------------------------------------------------------------------- idempotence


def test_the_same_file_again_is_skipped_without_parsing_or_writing(
    ingest_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = ingest_file(ingest_engine, FIXTURE, "ARC")
    monkeypatch.setattr(pipeline, "read_model", _refuse_to_parse)

    second = ingest_file(ingest_engine, FIXTURE, "ARC")

    assert (second.status, second.model_id, second.concurrent) == ("skipped", first.model_id, False)
    assert second.row_counts == {}
    assert _totals(ingest_engine) == FIXTURE_ROWS
    skipped = _runs(ingest_engine)[1]
    assert {key: skipped[key] for key in ("status", "model_id", "row_counts", "error")} == {
        "status": "skipped",
        "model_id": first.model_id,
        "row_counts": None,
        "error": None,
    }
    assert skipped["finished_at"] >= skipped["started_at"]


def test_another_discipline_for_a_loaded_file_is_skipped_with_a_warning(
    ingest_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    ingest_file(ingest_engine, FIXTURE, "ARC")

    result = ingest_file(ingest_engine, FIXTURE, "MEP")

    assert (result.status, result.discipline) == ("skipped", "ARC")
    warning = next(record for record in caplog.records if record.levelno == logging.WARNING)
    assert "another discipline" in warning.getMessage()


def test_a_changed_file_is_loaded_as_a_new_model(ingest_engine: Engine, tmp_path: Path) -> None:
    """One more newline changes the hash, so it is a new version of the model."""
    changed = tmp_path / "revit_quirks.ifc"
    changed.write_bytes(FIXTURE.read_bytes() + b"\n")

    first = ingest_file(ingest_engine, FIXTURE, "ARC")
    second = ingest_file(ingest_engine, changed, "ARC")

    assert (first.status, second.status) == ("succeeded", "succeeded")
    assert second.model_id != first.model_id
    assert _totals(ingest_engine) == {table: 2 * n for table, n in FIXTURE_ROWS.items()}


def test_deleting_a_model_removes_its_rows_and_keeps_its_runs(ingest_engine: Engine) -> None:
    loaded = ingest_file(ingest_engine, FIXTURE, "ARC")
    ingest_file(ingest_engine, FIXTURE, "ARC")  # a skipped run that also points at it

    with ingest_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM ifc.model WHERE model_id = :m"), {"m": loaded.model_id}
        )

    assert _totals(ingest_engine) == NOTHING
    runs = _runs(ingest_engine)
    assert [(run["status"], run["model_id"]) for run in runs] == [
        ("succeeded", None),
        ("skipped", None),
    ]
    # The bytes are no longer loaded, so the same file loads again, as a new model.
    again = ingest_file(ingest_engine, FIXTURE, "ARC")
    assert (again.status, again.model_id) == ("succeeded", loaded.model_id + 1)


# --------------------------------------------------------------------------- failures


def test_invalid_discipline_is_refused_before_anything_is_written(ingest_engine: Engine) -> None:
    with pytest.raises(ValueError, match="capital letters"):
        ingest_file(ingest_engine, FIXTURE, "arc")

    assert _runs(ingest_engine) == []


@pytest.mark.parametrize(
    ("content", "error"),
    [
        (b"this is not an IFC file\n", "not a readable IFC file"),
        (b"", "the file is empty"),
        (FIXTURE.read_bytes()[:6000], "truncated"),
    ],
    ids=["garbage", "empty", "truncated"],
)
def test_an_unreadable_file_gives_a_failed_run_and_no_model(
    ingest_engine: Engine, tmp_path: Path, content: bytes, error: str
) -> None:
    path = tmp_path / "corrupt.ifc"
    path.write_bytes(content)

    with pytest.raises(IngestError, match=error) as excinfo:
        ingest_file(ingest_engine, path, "ARC")

    (run,) = _runs(ingest_engine)
    assert (run["ingestion_run_id"], run["status"], run["model_id"]) == (
        excinfo.value.run_id,
        "failed",
        None,
    )
    assert run["error"].startswith("ExtractError: corrupt.ifc: ")
    assert error in run["error"]
    assert run["finished_at"] is not None
    assert _totals(ingest_engine) == NOTHING


def test_a_failure_late_in_the_load_rolls_back_everything(
    ingest_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every row is inserted before the failure; none of them may survive it."""

    def load_then_fail(*args: Any, **options: Any) -> LoadResult:
        load(*args, **options)
        raise RuntimeError("disk full")

    monkeypatch.setattr(pipeline, "load", load_then_fail)

    with pytest.raises(IngestError, match="RuntimeError: disk full"):
        ingest_file(ingest_engine, FIXTURE, "ARC")

    assert [(run["status"], run["error"]) for run in _runs(ingest_engine)] == [
        ("failed", "RuntimeError: disk full")
    ]
    assert _totals(ingest_engine) == NOTHING


def test_a_database_error_is_recorded_with_the_driver_message(
    ingest_engine: Engine, fixture_records: ModelRecords, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the SQL and parameters SQLAlchemy would add to the message."""
    broken = dataclasses.replace(
        fixture_records, model=dataclasses.replace(fixture_records.model, file_size_bytes=-1)
    )
    monkeypatch.setattr(pipeline, "transform", lambda raw: broken)

    with pytest.raises(IngestError, match="ck_model_file_size_bytes_positive"):
        ingest_file(ingest_engine, FIXTURE, "ARC")

    (run,) = _runs(ingest_engine)
    assert run["error"].startswith('CheckViolation: new row for relation "model"')
    assert "[SQL:" not in run["error"]


def test_a_file_that_changes_while_it_is_ingested_fails(
    ingest_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run is recorded under the first hash; the model would have had another."""
    monkeypatch.setattr(pipeline, "sha256_file", lambda path: "0" * 64)

    with pytest.raises(IngestError, match="changed while it was being ingested"):
        ingest_file(ingest_engine, FIXTURE, "ARC")

    assert _totals(ingest_engine) == NOTHING


# --------------------------------------------------------------------------- concurrency


def _race(engine: Engine, sha256: str, *, winner_commits: bool) -> tuple[IngestResult, int]:
    """Run an ingest of the fixture while another transaction holds its model row.

    A second connection plays the other ingest: it inserts the model row for the
    fixture's bytes and keeps its transaction open. The ingest under test sees no
    committed model, records a running run, parses the file and inserts the same row,
    where PostgreSQL makes it wait on the unique index. Once it waits, the other
    transaction commits or rolls back.

    Returns:
        The ingest's result, and the model_id the other transaction used.
    """
    other = engine.connect()
    transaction = other.begin()
    try:
        other_id = add_model(other, file_name="revit_quirks.ifc", file_sha256=sha256)
        with ThreadPoolExecutor(max_workers=1) as pool:
            ingest = pool.submit(ingest_file, engine, FIXTURE, "ARC")
            try:
                wait_until_a_session_waits_on_a_lock(engine)
                if winner_commits:
                    transaction.commit()
            finally:
                if transaction.is_active:
                    transaction.rollback()  # also lets the ingest finish if the test failed
            result = ingest.result(timeout=60)
    finally:
        other.close()
    return result, other_id


def test_the_loser_of_two_concurrent_ingests_is_recorded_as_skipped(
    ingest_engine: Engine, fixture_records: ModelRecords
) -> None:
    """The other ingest commits first: the wait ends in a unique violation."""
    result, winner_id = _race(ingest_engine, fixture_records.model.file_sha256, winner_commits=True)

    assert (result.status, result.model_id, result.concurrent) == ("skipped", winner_id, True)
    (run,) = _runs(ingest_engine)
    assert {key: run[key] for key in ("status", "model_id", "row_counts", "error")} == {
        "status": "skipped",
        "model_id": winner_id,
        "row_counts": None,
        "error": None,
    }
    assert _totals(ingest_engine) == NOTHING | {"model": 1}


def test_an_ingest_that_waited_loads_the_file_when_the_other_one_fails(
    ingest_engine: Engine, fixture_records: ModelRecords
) -> None:
    """The other ingest rolls back: the wait ends, and this one loads the file itself."""
    result, failed_id = _race(
        ingest_engine, fixture_records.model.file_sha256, winner_commits=False
    )

    assert (result.status, result.concurrent) == ("succeeded", False)
    assert result.model_id != failed_id
    assert _totals(ingest_engine) == FIXTURE_ROWS


# --------------------------------------------------------------------------- geometry


def test_geometry_quantities_rank_last_and_the_run_records_the_time(
    ingest_engine: Engine, tmp_path: Path
) -> None:
    path = write_measured_model(tmp_path / "measured.ifc")

    result = ingest_file(ingest_engine, path, "ARC", geometry=True)

    with ingest_engine.connect() as connection:
        areas = dict(
            connection.execute(
                text(
                    "SELECT name, area_m2 FROM ifc.v_element WHERE model_id = :m "
                    "AND area_m2 IS NOT NULL"
                ),
                {"m": result.model_id},
            ).all()
        )
        sources = (
            connection.execute(
                text(
                    "SELECT DISTINCT q.source FROM ifc.quantity AS q JOIN ifc.object AS o "
                    "USING (object_id) WHERE o.model_id = :m"
                ),
                {"m": result.model_id},
            )
            .scalars()
            .all()
        )
        seconds = connection.execute(
            text("SELECT geometry_seconds FROM ifc.ingestion_run WHERE ingestion_run_id = :r"),
            {"r": result.run_id},
        ).scalar_one()

    assert areas == pytest.approx({WALL: 13.0, SLAB: 16.0})
    assert sources == ["geometry"]
    assert seconds is not None
    assert seconds >= 0
    assert result.issue_counts["geometry_not_measured"] == 1  # the beam


def _geometry_rows(engine: Engine, model_id: int) -> tuple[dict[str, float], list[str]]:
    """Each element's geometry area, and the model's geometry_not_measured messages."""
    with engine.connect() as connection:
        areas = dict(
            connection.execute(
                text(
                    "SELECT o.name, q.value FROM ifc.quantity AS q JOIN ifc.object AS o "
                    "USING (object_id) WHERE o.model_id = :m AND q.source = 'geometry' "
                    "AND q.name = 'area_m2'"
                ),
                {"m": model_id},
            ).all()
        )
        issues = (
            connection.execute(
                text(
                    "SELECT message FROM ifc.ingest_issue WHERE model_id = :m "
                    "AND code = 'geometry_not_measured' ORDER BY issue_index"
                ),
                {"m": model_id},
            )
            .scalars()
            .all()
        )
    return areas, list(issues)


def test_a_global_id_used_twice_is_measured_once_from_the_object_kept(
    ingest_engine: Engine, tmp_path: Path
) -> None:
    """The space keeps the wall's GlobalId and the slab the column's (``share_global_ids``).
    Measuring the wall and the column too would give their GlobalIds a second area and
    volume, which ifc.quantity's key refuses, and the load would fail."""
    path = share_global_ids(write_measured_model(tmp_path / "measured.ifc"))

    result = ingest_file(ingest_engine, path, "ARC", geometry=True)

    areas, _ = _geometry_rows(ingest_engine, result.model_id)
    assert result.status == "succeeded"
    assert areas == pytest.approx({SLAB: 16.0, SPACE: 6.0})


def test_geometry_on_a_file_loaded_without_it_measures_the_same_model(
    ingest_engine: Engine, tmp_path: Path
) -> None:
    """Re-check finding R4: the advice "ingest --geometry" works on a model already loaded."""
    path = write_measured_model(tmp_path / "measured.ifc")
    plain = ingest_file(ingest_engine, path, "ARC")

    measured = ingest_file(ingest_engine, path, "ARC", geometry=True)
    again = ingest_file(ingest_engine, path, "ARC", geometry=True)

    areas, issues = _geometry_rows(ingest_engine, plain.model_id)
    assert (measured.status, measured.model_id) == ("measured", plain.model_id)
    assert areas == pytest.approx({WALL: 13.0, SLAB: 16.0, SPACE: 6.0})
    # Area and volume of the wall, slab and space, and the column's volume.
    assert measured.row_counts == {"quantity": 7, "ingest_issue": 1}
    assert measured.issue_counts == {"geometry_not_measured": 1}  # the beam
    assert issues == [
        f"IfcBeam {BEAM!r} has a body that could not be triangulated; no geometry quantity"
    ]
    runs = {r["ingestion_run_id"]: r for r in _runs(ingest_engine)}
    assert runs[measured.run_id]["status"] == "succeeded"
    assert runs[measured.run_id]["geometry_seconds"] is not None
    # Measured once: a third run changes nothing.
    assert again.status == "skipped"
    assert again.warnings[0].endswith("that model is unchanged")


def test_measuring_in_place_applies_no_project_and_says_so(
    ingest_engine: Engine, tmp_path: Path
) -> None:
    path = write_measured_model(tmp_path / "measured.ifc")
    plain = ingest_file(ingest_engine, path, "ARC")

    measured = ingest_file(ingest_engine, path, "ARC", geometry=True, project_key="house")

    assert measured.status == "measured"
    assert measured.warnings == (
        f"measured.ifc is already loaded as model {plain.model_id}, so --project and "
        "--replaces were not applied; its shapes were measured",
    )


def test_a_model_measured_by_another_run_meanwhile_is_left_alone(
    ingest_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_measured_model(tmp_path / "measured.ifc")
    plain = ingest_file(ingest_engine, path, "ARC")
    answers = iter([False, True])  # not measured when the run starts; measured under the lock
    monkeypatch.setattr(pipeline, "_measured", lambda connection, model_id: next(answers))

    result = ingest_file(ingest_engine, path, "ARC", geometry=True)

    assert (result.status, result.concurrent) == ("skipped", True)
    assert _geometry_rows(ingest_engine, plain.model_id) == ({}, [])


def test_a_failed_measure_is_recorded_and_the_model_is_unchanged(
    ingest_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_measured_model(tmp_path / "measured.ifc")
    plain = ingest_file(ingest_engine, path, "ARC")

    def refuse(*args: object) -> dict[str, int]:
        raise RuntimeError("disk full")

    monkeypatch.setattr(pipeline, "add_geometry", refuse)

    with pytest.raises(IngestError, match="disk full") as failure:
        ingest_file(ingest_engine, path, "ARC", geometry=True)

    run = next(r for r in _runs(ingest_engine) if r["ingestion_run_id"] == failure.value.run_id)
    assert (run["status"], run["model_id"]) == ("failed", plain.model_id)
    assert _geometry_rows(ingest_engine, plain.model_id) == ({}, [])


def test_without_geometry_the_run_measures_nothing(ingest_engine: Engine, tmp_path: Path) -> None:
    result = ingest_file(ingest_engine, write_measured_model(tmp_path / "m.ifc"), "ARC")

    with ingest_engine.connect() as connection:
        seconds = connection.execute(
            text("SELECT geometry_seconds FROM ifc.ingestion_run WHERE ingestion_run_id = :r"),
            {"r": result.run_id},
        ).scalar_one()
    assert (seconds, result.row_counts["quantity"]) == (None, 0)
