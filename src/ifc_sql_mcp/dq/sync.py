"""Make ``ifc.dq_rule`` match the catalogue, keeping every rule that findings refer to.

``ifc-sql dq`` calls :func:`sync_rules` before it evaluates anything, in a transaction of
its own, so ``dq_rule`` always describes the catalogue that the latest runs used. What a
change to ``rules/dq_rules.yaml`` does (ADR 5):

* **A new rule** is inserted.
* **An edited rule** (description, severity or dimension) is updated in place. Earlier
  findings keep the severity they were found with (``dq_result.severity``); later runs
  use the new one.
* **A removed rule that findings refer to** is kept and gets ``retired_at``.
  ``dq_result.rule_id`` deliberately has no ON DELETE CASCADE: results are kept per run,
  and each must go on naming the rule it broke. Later runs do not evaluate the rule.
* **A removed rule that nothing refers to** is deleted. That includes a retired rule
  whose last findings were deleted with their model.
* **A retired rule that is back in the file** is in use again: ``retired_at`` is cleared.

A changed query or issue code is not visible here; nothing about it is stored. A rule
whose meaning changes should therefore get a new ``rule_id``, so that its old findings
stay attached to the old rule.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Connection, Row, delete, func, insert, select, text, update

from ifc_sql_mcp.db import schema
from ifc_sql_mcp.dq.catalogue import Rule

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SyncResult:
    """The rule ids each kind of change touched, in catalogue or table order.

    Attributes:
        added: Rules inserted.
        updated: Rules whose description, severity or dimension changed, or that were
            retired and are back in the catalogue.
        retired: Rules removed from the catalogue but kept for their findings; only those
            retired by this sync.
        deleted: Rules removed from the catalogue that no finding referred to.
    """

    added: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    retired: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()


def sync_rules(connection: Connection, rules: Sequence[Rule]) -> SyncResult:
    """Bring ``ifc.dq_rule`` in line with ``rules``, inside the caller's transaction.

    Args:
        connection: A connection inside the transaction that should hold the change.
        rules: The catalogue, as :func:`~ifc_sql_mcp.dq.catalogue.load_catalogue` returns it.

    Returns:
        What changed.
    """
    # Two syncs at once would both see a new rule as missing and both insert it; the
    # second would fail on the primary key. The lock makes them take turns. It does not
    # block runs, which only read dq_rule through their foreign key checks.
    connection.execute(text("LOCK TABLE ifc.dq_rule IN SHARE ROW EXCLUSIVE MODE"))
    table = schema.dq_rule
    stored = {row.rule_id: row for row in connection.execute(select(table))}
    in_catalogue = {rule.rule_id for rule in rules}

    added = [rule for rule in rules if rule.rule_id not in stored]
    updated = [
        rule for rule in rules if rule.rule_id in stored and _changed(stored[rule.rule_id], rule)
    ]
    removed = [rule_id for rule_id in stored if rule_id not in in_catalogue]

    if added:
        connection.execute(insert(table), [_values(rule) for rule in added])
    for rule in updated:
        connection.execute(
            update(table)
            .where(table.c.rule_id == rule.rule_id)
            .values(_values(rule) | {"retired_at": None})
        )

    cited = _cited(connection, removed)
    deleted = [rule_id for rule_id in removed if rule_id not in cited]
    retired = [
        rule_id for rule_id in removed if rule_id in cited and stored[rule_id].retired_at is None
    ]
    if deleted:
        connection.execute(delete(table).where(table.c.rule_id.in_(deleted)))
    if retired:
        connection.execute(
            update(table).where(table.c.rule_id.in_(retired)).values(retired_at=func.now())
        )

    result = SyncResult(
        added=tuple(rule.rule_id for rule in added),
        updated=tuple(rule.rule_id for rule in updated),
        retired=tuple(retired),
        deleted=tuple(deleted),
    )
    logger.info(
        "rules synchronised",
        extra={
            "rules": len(rules),
            "added": len(result.added),
            "updated": len(result.updated),
            "retired": len(result.retired),
            "deleted": len(result.deleted),
        },
    )
    return result


def _values(rule: Rule) -> dict[str, str]:
    """The columns of ``dq_rule`` that the catalogue decides."""
    return {
        "rule_id": rule.rule_id,
        "description": rule.description,
        "severity": rule.severity,
        "dimension": rule.dimension,
    }


def _changed(stored: Row[tuple[object, ...]], rule: Rule) -> bool:
    return (
        stored.description != rule.description
        or stored.severity != rule.severity
        or stored.dimension != rule.dimension
        or stored.retired_at is not None
    )


def _cited(connection: Connection, rule_ids: Sequence[str]) -> set[str]:
    """Return those of ``rule_ids`` that at least one finding refers to."""
    if not rule_ids:
        return set()
    result = schema.dq_result
    return set(
        connection.execute(
            select(result.c.rule_id).where(result.c.rule_id.in_(rule_ids)).distinct()
        ).scalars()
    )
