"""Ingest one IFC file: identity check, run bookkeeping, extract, transform, load.

The steps, and why they come in this order (ADR 4):

1. **Hash the file.** Its SHA-256 is its identity: a model with the same hash holds the
   same bytes. Hashing first means a repeat costs one read of the file and one query,
   not a parse.
2. **Already loaded?** Then record a ``skipped`` run that points at the existing model,
   and stop.
3. **Record a ``running`` run in its own, committed transaction**, before any work, so
   a run that fails is on record even though its load is rolled back.
4. **Extract and transform** outside any transaction: parsing takes seconds and needs no
   database.
5. **Load the model and mark the run ``succeeded`` in one transaction.** The run says
   succeeded exactly when the model committed.
6. **On any error** the load transaction has rolled back, so nothing of the model was
   written. The run is marked ``failed``, with the error, in a new transaction.

Two ingests of the same file can both get past step 2. The second to insert its model
row waits on the unique index on ``model.file_sha256`` until the first commits, then
fails on it. That loser records a ``skipped`` run pointing at the winner's model, so the
end state is the same as if the two had run one after the other.

A process killed between steps 3 and 6 leaves its run ``running`` for good; nothing
reaps such rows (ADR 4).
"""

import logging
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import psycopg
from sqlalchemy import Connection, Engine, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from ifc_sql_mcp.db import schema
from ifc_sql_mcp.db.engine import describe_error
from ifc_sql_mcp.ingest.extract import read_model, sha256_file
from ifc_sql_mcp.ingest.load import load
from ifc_sql_mcp.ingest.records import ModelRecords
from ifc_sql_mcp.ingest.transform import transform

logger = logging.getLogger(__name__)

_DISCIPLINE = re.compile(schema.DISCIPLINE_PATTERN)

DUPLICATE_FILE_CONSTRAINT = "uq_model_file_sha256"
"""The unique constraint a losing concurrent ingest of the same file runs into."""


class IngestError(Exception):
    """An ingest failed: its run is recorded as failed, and nothing of the model was written.

    Attributes:
        run_id: The ``ingestion_run`` row that records the failure.
    """

    def __init__(self, run_id: int, message: str) -> None:
        super().__init__(message)
        self.run_id = run_id


class FileChangedError(Exception):
    """The file's bytes changed between hashing it and parsing it."""


@dataclass(frozen=True, slots=True)
class IngestResult:
    """What one ingest did.

    Attributes:
        run_id: The ``ingestion_run`` row that records it.
        status: ``succeeded`` (this run loaded the model) or ``skipped`` (the file was
            already loaded, and nothing was written).
        model_id: The model this run loaded, or the one it found.
        discipline: That model's discipline.
        row_counts: Rows written per table; empty when skipped.
        issue_counts: Transform issues per code; empty when skipped.
        concurrent: True when skipped because another ingest of the same file committed
            while this one was running.
    """

    run_id: int
    status: Literal["succeeded", "skipped"]
    model_id: int
    discipline: str
    row_counts: dict[str, int] = field(default_factory=dict)
    issue_counts: dict[str, int] = field(default_factory=dict)
    concurrent: bool = False


def check_discipline(value: str) -> str:
    """Return ``value`` if it is a valid discipline code, else raise ``ValueError``.

    The same rule as the CHECK on ``model.discipline``, applied before anything is read,
    so a typo fails at once instead of after parsing the file. Lower case is refused
    rather than corrected: what is stored is exactly what was typed.
    """
    if not _DISCIPLINE.fullmatch(value):
        raise ValueError(f"discipline must be 2 to 8 capital letters, e.g. ARC; got {value!r}")
    return value


def ingest_file(engine: Engine, path: Path, discipline: str) -> IngestResult:
    """Load the IFC file at ``path`` unless the same bytes are already loaded.

    Args:
        engine: Engine on a database migrated to the latest revision.
        path: The IFC file.
        discipline: Discipline code for the model, e.g. ``ARC``.

    Returns:
        What the run did: loaded the file, or found it already loaded.

    Raises:
        ValueError: if ``discipline`` is not a valid code; nothing is written.
        OSError: if the file cannot be read; nothing is written.
        IngestError: if extract, transform or load fails; the run is recorded as failed.
    """
    check_discipline(discipline)
    sha256 = sha256_file(path)

    with engine.begin() as connection:
        existing = _loaded_model(connection, sha256)
        if existing is not None:
            run_id = _insert_run(connection, path.name, sha256, "skipped", existing[0])
            return _skipped(run_id, path, discipline, existing, concurrent=False)

    with engine.begin() as connection:
        run_id = _insert_run(connection, path.name, sha256, "running")
    try:
        records = _extract_and_transform(path, sha256, run_id)
        started = time.perf_counter()
        with engine.begin() as connection:
            loaded = load(connection, records, discipline)
            _finish_run(connection, run_id, "succeeded", loaded.model_id, loaded.row_counts)
        logger.info(
            "loaded",
            extra={
                "run_id": run_id,
                "model_id": loaded.model_id,
                "rows": sum(loaded.row_counts.values()),
                "seconds": round(time.perf_counter() - started, 3),
            },
        )
    except Exception as exc:
        winner = _concurrent_winner(engine, exc, sha256)
        if winner is not None:
            with engine.begin() as connection:
                _finish_run(connection, run_id, "skipped", winner[0])
            return _skipped(run_id, path, discipline, winner, concurrent=True)
        message = describe_error(exc)
        logger.error("ingest failed", extra={"run_id": run_id, "error": message})
        with engine.begin() as connection:
            _finish_run(connection, run_id, "failed", error=message)
        raise IngestError(run_id, message) from exc

    return IngestResult(
        run_id=run_id,
        status="succeeded",
        model_id=loaded.model_id,
        discipline=discipline,
        row_counts=loaded.row_counts,
        issue_counts=dict(sorted(Counter(i.code.value for i in records.issues).items())),
    )


def _extract_and_transform(path: Path, sha256: str, run_id: int) -> ModelRecords:
    """Parse the file into records, logging how long each stage took and what it found."""
    started = time.perf_counter()
    raw = read_model(path)
    if raw.file.sha256 != sha256:
        raise FileChangedError(f"{path.name} changed while it was being ingested; try again")
    extracted = time.perf_counter()
    logger.info(
        "extracted",
        extra={
            "run_id": run_id,
            "objects": len(raw.spatial_elements) + len(raw.elements),
            "seconds": round(extracted - started, 3),
        },
    )

    records = transform(raw)
    logger.info(
        "transformed",
        extra={
            "run_id": run_id,
            "properties": len(records.properties),
            "issues": len(records.issues),
            "seconds": round(time.perf_counter() - extracted, 3),
        },
    )
    for code, count in sorted(Counter(i.code.value for i in records.issues).items()):
        logger.info("transform issues", extra={"run_id": run_id, "code": code, "count": count})
    return records


def _skipped(
    run_id: int, path: Path, discipline: str, model: tuple[int, str], *, concurrent: bool
) -> IngestResult:
    model_id, stored_discipline = model
    logger.info(
        "already loaded; nothing written",
        extra={"run_id": run_id, "model_id": model_id, "concurrent": concurrent},
    )
    if stored_discipline != discipline:
        # The file's identity is its bytes, not the discipline it was given.
        logger.warning(
            "already loaded with another discipline; delete the model to load it again",
            extra={"file": path.name, "stored": stored_discipline, "given": discipline},
        )
    return IngestResult(
        run_id=run_id,
        status="skipped",
        model_id=model_id,
        discipline=stored_discipline,
        concurrent=concurrent,
    )


def _concurrent_winner(engine: Engine, exc: Exception, sha256: str) -> tuple[int, str] | None:
    """If ``exc`` says another ingest committed this file first, return that model."""
    if not (
        isinstance(exc, IntegrityError)
        and isinstance(exc.orig, psycopg.errors.UniqueViolation)
        and exc.orig.diag.constraint_name == DUPLICATE_FILE_CONSTRAINT
    ):
        return None
    with engine.begin() as connection:
        return _loaded_model(connection, sha256)


# --------------------------------------------------------------------------- ingestion_run


def _loaded_model(connection: Connection, sha256: str) -> tuple[int, str] | None:
    """Return ``(model_id, discipline)`` of the model with these bytes, if there is one."""
    model = schema.model
    row = connection.execute(
        select(model.c.model_id, model.c.discipline).where(model.c.file_sha256 == sha256)
    ).one_or_none()
    return None if row is None else (row.model_id, row.discipline)


def _insert_run(
    connection: Connection,
    file_name: str,
    sha256: str,
    status: Literal["running", "skipped"],
    model_id: int | None = None,
) -> int:
    run = schema.ingestion_run
    statement = (
        insert(run)
        .values(
            file_name=file_name,
            file_sha256=sha256,
            status=status,
            model_id=model_id,
            # A skipped run is over as soon as it starts.
            finished_at=func.clock_timestamp() if status == "skipped" else None,
        )
        .returning(run.c.ingestion_run_id)
    )
    return int(connection.execute(statement).scalar_one())


def _finish_run(
    connection: Connection,
    run_id: int,
    status: Literal["succeeded", "failed", "skipped"],
    model_id: int | None = None,
    row_counts: dict[str, int] | None = None,
    error: str | None = None,
) -> None:
    """Close a ``running`` run.

    ``clock_timestamp()`` rather than ``now()``: inside the load transaction ``now()`` is
    the time the transaction began, which would leave the load out of the run's duration.
    """
    run = schema.ingestion_run
    values: dict[str, object] = {
        "status": status,
        "finished_at": func.clock_timestamp(),
        "model_id": model_id,
        "error": error,
    }
    # Only when there are counts: SQLAlchemy writes None into a JSONB column as JSON
    # null, not SQL NULL, and the CHECK on row_counts accepts only a JSON object.
    if row_counts is not None:
        values["row_counts"] = row_counts
    connection.execute(update(run).where(run.c.ingestion_run_id == run_id).values(values))
