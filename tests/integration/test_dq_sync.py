"""Synchronising the catalogue into ifc.dq_rule, including every way the YAML can change.

All but the last test run in the rolled-back ``conn`` transaction, where ``now()`` is the
transaction's start time, so a ``retired_at`` set there equals ``now()``. The last one
commits, to stage two syncs at once.
"""

import dataclasses
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from locks import wait_until_a_session_waits_on_a_lock
from rows import add_dq_run, add_model
from sqlalchemy import Connection, Engine, insert, text

from ifc_sql_mcp.db import schema
from ifc_sql_mcp.dq.catalogue import Rule, load_catalogue
from ifc_sql_mcp.dq.sync import SyncResult, sync_rules

RULES = load_catalogue()
EMPTY = RULES[[rule.rule_id for rule in RULES].index("value_is_empty")]


def _stored(conn: Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute(text("SELECT * FROM ifc.dq_rule ORDER BY rule_id")).mappings()
    return {row["rule_id"]: dict(row) for row in rows}


def _without(rule_id: str) -> tuple[Rule, ...]:
    return tuple(rule for rule in RULES if rule.rule_id != rule_id)


def _add_finding(conn: Connection, rule: Rule) -> int:
    """Record one finding of ``rule`` in a new run on a new model; return the model id."""
    model_id = add_model(conn)
    conn.execute(
        insert(schema.dq_result).values(
            dq_run_id=add_dq_run(conn, model_id),
            rule_id=rule.rule_id,
            severity=rule.severity,
            message="Other.Comment = '': no value",
        )
    )
    return model_id


@pytest.fixture
def synced(conn: Connection) -> SyncResult:
    return sync_rules(conn, RULES)


def test_the_first_sync_adds_every_rule_as_written(conn: Connection, synced: SyncResult) -> None:
    assert synced == SyncResult(added=tuple(rule.rule_id for rule in RULES))
    stored = _stored(conn)
    assert sorted(stored) == sorted(rule.rule_id for rule in RULES)
    for rule in RULES:
        assert stored[rule.rule_id] == {
            "rule_id": rule.rule_id,
            "description": rule.description,
            "severity": rule.severity,
            "dimension": rule.dimension,
            "retired_at": None,
        }


def test_syncing_an_unchanged_catalogue_changes_nothing(
    conn: Connection, synced: SyncResult
) -> None:
    before = _stored(conn)

    assert sync_rules(conn, RULES) == SyncResult()
    assert _stored(conn) == before


def test_an_edited_rule_is_updated_and_its_findings_keep_their_severity(
    conn: Connection, synced: SyncResult
) -> None:
    _add_finding(conn, EMPTY)
    edited = dataclasses.replace(
        EMPTY, description="A text property is empty.", severity="warning", dimension="validity"
    )
    catalogue = tuple(edited if rule is EMPTY else rule for rule in RULES)

    assert sync_rules(conn, catalogue) == SyncResult(updated=("value_is_empty",))
    assert _stored(conn)["value_is_empty"] == {
        "rule_id": "value_is_empty",
        "description": "A text property is empty.",
        "severity": "warning",
        "dimension": "validity",
        "retired_at": None,
    }
    severities = conn.execute(text("SELECT severity FROM ifc.dq_result")).scalars().all()
    assert severities == ["info"]


def test_a_removed_rule_that_nothing_cites_is_deleted(conn: Connection, synced: SyncResult) -> None:
    assert sync_rules(conn, _without("value_is_empty")) == SyncResult(deleted=("value_is_empty",))
    assert "value_is_empty" not in _stored(conn)


def test_a_removed_rule_with_findings_is_retired_and_kept(
    conn: Connection, synced: SyncResult
) -> None:
    """dq_result.rule_id does not cascade: the finding must go on naming its rule."""
    _add_finding(conn, EMPTY)

    assert sync_rules(conn, _without("value_is_empty")) == SyncResult(retired=("value_is_empty",))
    now = conn.execute(text("SELECT now()")).scalar_one()
    retired = _stored(conn)["value_is_empty"]
    assert retired["retired_at"] == now
    assert retired["description"] == EMPTY.description
    # Already retired: a later sync leaves it, and its date, alone.
    assert sync_rules(conn, _without("value_is_empty")) == SyncResult()
    assert _stored(conn)["value_is_empty"]["retired_at"] == now


def test_a_retired_rule_back_in_the_catalogue_is_in_use_again(
    conn: Connection, synced: SyncResult
) -> None:
    _add_finding(conn, EMPTY)
    sync_rules(conn, _without("value_is_empty"))

    assert sync_rules(conn, RULES) == SyncResult(updated=("value_is_empty",))
    assert _stored(conn)["value_is_empty"]["retired_at"] is None


def test_a_retired_rule_is_deleted_once_its_last_findings_are_gone(
    conn: Connection, synced: SyncResult
) -> None:
    model_id = _add_finding(conn, EMPTY)
    sync_rules(conn, _without("value_is_empty"))

    # Deleting the model cascades to its runs and their findings.
    conn.execute(text("DELETE FROM ifc.model WHERE model_id = :m"), {"m": model_id})

    assert sync_rules(conn, _without("value_is_empty")) == SyncResult(deleted=("value_is_empty",))
    assert "value_is_empty" not in _stored(conn)


# --------------------------------------------------------------------------- concurrency


def test_two_syncs_at_once_take_turns_instead_of_colliding(ingest_engine: Engine) -> None:
    """Without the table lock, the second would insert the same rules and fail on the key.

    A second connection plays the other ``ifc-sql dq``: it syncs into an empty table and
    keeps its transaction open. The sync under test must wait, then find nothing to do.
    """
    other = ingest_engine.connect()
    transaction = other.begin()
    try:
        sync_rules(other, RULES)
        with ThreadPoolExecutor(max_workers=1) as pool:
            waiting = pool.submit(_sync_in_own_transaction, ingest_engine)
            try:
                wait_until_a_session_waits_on_a_lock(ingest_engine)
                transaction.commit()
            finally:
                if transaction.is_active:
                    transaction.rollback()  # also lets the other sync finish if this failed
            result = waiting.result(timeout=60)
    finally:
        other.close()

    assert result == SyncResult()


def _sync_in_own_transaction(engine: Engine) -> SyncResult:
    with engine.begin() as connection:
        return sync_rules(connection, RULES)
