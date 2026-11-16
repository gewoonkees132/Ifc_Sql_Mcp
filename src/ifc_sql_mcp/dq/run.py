"""Evaluate every rule against one model, as one audited run in ``ifc.dq_run``.

A run follows the same steps as an ingest (ADR 4), for the same reasons (ADR 5):

1. **Record a ``running`` run in its own, committed transaction**, so a run that fails
   is on record even though its findings are rolled back.
2. **Insert every rule's findings and mark the run ``succeeded`` in one transaction.**
   A run's findings are all there or none are, so ``v_model_summary`` never counts a
   half-finished run.
3. **On any error** that transaction has rolled back, so the run has no findings. It is
   marked ``failed``, with the error, in a new transaction.

Findings are written set-based: one ``INSERT ... SELECT`` per rule, whose SELECT is the
rule's query. For a SQL rule that is the catalogue's own SQL; for an issue rule it is
:data:`ISSUE_FINDINGS`. No finding passes through Python.

Each run evaluates every rule and writes new rows; earlier runs are never changed, so
quality can be compared over time. The rules must already be in ``ifc.dq_rule``
(:func:`~ifc_sql_mcp.dq.sync.sync_rules`), because each finding references its rule.
"""

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import (
    BigInteger,
    Connection,
    Engine,
    Text,
    TextClause,
    func,
    insert,
    literal,
    select,
    text,
    update,
)

from ifc_sql_mcp.db import schema
from ifc_sql_mcp.db.engine import describe_error
from ifc_sql_mcp.db.schema import SEVERITIES
from ifc_sql_mcp.dq.catalogue import Rule

logger = logging.getLogger(__name__)

ISSUE_FINDINGS = """
SELECT i.object_id,
       CASE
           WHEN i.object_id IS NULL AND i.global_id IS NOT NULL
               THEN format('%s (GlobalId %L: no object with it was loaded)',
                           i.message, i.global_id)
           ELSE i.message
       END AS message
FROM ifc.ingest_issue AS i
WHERE i.model_id = :model_id
  AND i.code = :issue_code
"""
"""The findings of an issue rule: the model's ``ingest_issue`` rows with the rule's code.

A finding keeps its issue's object and message. ``dq_result`` has no GlobalId column, so
for an object that was never loaded (its GlobalId was malformed) the message carries the
GlobalId as the file wrote it (ADR 5).
"""


class UnknownModelError(LookupError):
    """There is no model with the given id; nothing was written."""


class RuleError(Exception):
    """A rule's query failed. The message names the rule and gives the database's error."""


class DqRunError(Exception):
    """A run failed: it is recorded as failed, and none of its findings were written.

    Attributes:
        run_id: The ``dq_run`` row that records the failure.
    """

    def __init__(self, run_id: int, message: str) -> None:
        super().__init__(message)
        self.run_id = run_id


@dataclass(frozen=True, slots=True)
class DqResult:
    """What one successful run found.

    Attributes:
        run_id: The ``dq_run`` row that records it.
        model_id: The model that was checked.
        discipline: That model's discipline.
        file_name: That model's file name.
        finding_counts: Findings per rule, for every rule evaluated, in catalogue order.
        severity_counts: Findings per severity: ``error``, ``warning`` and ``info``.
    """

    run_id: int
    model_id: int
    discipline: str
    file_name: str
    finding_counts: dict[str, int]
    severity_counts: dict[str, int]


def loaded_model_ids(connection: Connection) -> list[int]:
    """Return the id of every model in the database, oldest first."""
    model = schema.model
    return list(connection.execute(select(model.c.model_id).order_by(model.c.model_id)).scalars())


def check_model(engine: Engine, model_id: int, rules: Sequence[Rule]) -> DqResult:
    """Evaluate ``rules`` against one model and record the run.

    Args:
        engine: Engine on a database migrated to the latest revision.
        model_id: The model to check.
        rules: The catalogue, already synchronised into ``ifc.dq_rule``.

    Returns:
        The run and its finding counts.

    Raises:
        UnknownModelError: if there is no such model; nothing is written.
        DqRunError: if evaluating a rule fails; the run is recorded as failed.
    """
    with engine.begin() as connection:
        discipline, file_name = _model(connection, model_id)
        run_id = _insert_run(connection, model_id)
    logger.info("checking", extra={"dq_run_id": run_id, "model_id": model_id})

    started = time.perf_counter()
    try:
        with engine.begin() as connection:
            counts = evaluate(connection, run_id, model_id, rules)
            _finish_run(connection, run_id, "succeeded")
    except Exception as exc:
        message = describe_error(exc)
        logger.error("dq run failed", extra={"dq_run_id": run_id, "error": message})
        with engine.begin() as connection:
            _finish_run(connection, run_id, "failed", error=message)
        raise DqRunError(run_id, message) from exc

    for rule in rules:
        if counts[rule.rule_id]:
            logger.info(
                "findings",
                extra={
                    "dq_run_id": run_id,
                    "rule_id": rule.rule_id,
                    "severity": rule.severity,
                    "count": counts[rule.rule_id],
                },
            )
    logger.info(
        "checked",
        extra={
            "dq_run_id": run_id,
            "model_id": model_id,
            "rules": len(rules),
            "findings": sum(counts.values()),
            "seconds": round(time.perf_counter() - started, 3),
        },
    )
    severities = dict.fromkeys(SEVERITIES, 0)
    for rule in rules:
        severities[rule.severity] += counts[rule.rule_id]
    return DqResult(
        run_id=run_id,
        model_id=model_id,
        discipline=discipline,
        file_name=file_name,
        finding_counts=counts,
        severity_counts=severities,
    )


def evaluate(
    connection: Connection, run_id: int, model_id: int, rules: Sequence[Rule]
) -> dict[str, int]:
    """Insert the findings of every rule for one model, in the caller's transaction.

    Args:
        connection: A connection inside the transaction that should hold the findings.
        run_id: The ``dq_run`` the findings belong to.
        model_id: The model to check.
        rules: The rules to evaluate, all present in ``ifc.dq_rule``.

    Returns:
        The number of findings per rule, in the order of ``rules``.

    Raises:
        RuleError: if a rule's query fails. The transaction is then unusable and must be
            rolled back.
    """
    counts: dict[str, int] = {}
    for rule in rules:
        try:
            counts[rule.rule_id] = _insert_findings(connection, run_id, model_id, rule)
        except Exception as exc:
            raise RuleError(f"rule {rule.rule_id} failed: {describe_error(exc)}") from exc
    return counts


def _finding_query(rule: Rule, model_id: int) -> TextClause:
    """The rule's SELECT of ``object_id`` and ``message``, with its parameters bound."""
    if rule.issue_code is not None:
        return text(ISSUE_FINDINGS).bindparams(model_id=model_id, issue_code=rule.issue_code.value)
    if rule.sql is not None:
        return text(rule.sql).bindparams(model_id=model_id)
    raise ValueError(f"rule {rule.rule_id} has neither an issue_code nor sql")


def _insert_findings(connection: Connection, run_id: int, model_id: int, rule: Rule) -> int:
    """Run ``INSERT INTO dq_result ... SELECT`` over the rule's query; return the row count.

    The findings are ordered by object and message, so the same model always gives its
    findings the same order. SQLAlchemy keeps the driver's row count only for UPDATE and
    DELETE unless asked to (``preserve_rowcount``); without it, this INSERT reports -1.
    """
    finding = (
        _finding_query(rule, model_id)
        .columns(object_id=BigInteger, message=Text)
        .subquery("finding")
    )
    statement = insert(schema.dq_result).from_select(
        ["dq_run_id", "rule_id", "object_id", "severity", "message"],
        select(
            literal(run_id, BigInteger),
            literal(rule.rule_id),
            finding.c.object_id,
            literal(rule.severity),
            finding.c.message,
        ).order_by(finding.c.object_id, finding.c.message),
    )
    return connection.execute(statement, execution_options={"preserve_rowcount": True}).rowcount


# --------------------------------------------------------------------------- dq_run


def _model(connection: Connection, model_id: int) -> tuple[str, str]:
    """Return the model's discipline and file name."""
    model = schema.model
    row = connection.execute(
        select(model.c.discipline, model.c.file_name).where(model.c.model_id == model_id)
    ).one_or_none()
    if row is None:
        raise UnknownModelError(f"no model with id {model_id}")
    return row.discipline, row.file_name


def _insert_run(connection: Connection, model_id: int) -> int:
    run = schema.dq_run
    statement = insert(run).values(model_id=model_id).returning(run.c.dq_run_id)
    return int(connection.execute(statement).scalar_one())


def _finish_run(
    connection: Connection,
    run_id: int,
    status: Literal["succeeded", "failed"],
    error: str | None = None,
) -> None:
    """Close a ``running`` run.

    ``clock_timestamp()`` rather than ``now()``: inside the evaluation transaction
    ``now()`` is the time the transaction began, which would leave the evaluation out of
    the run's duration.
    """
    run = schema.dq_run
    connection.execute(
        update(run)
        .where(run.c.dq_run_id == run_id)
        .values(status=status, finished_at=func.clock_timestamp(), error=error)
    )
