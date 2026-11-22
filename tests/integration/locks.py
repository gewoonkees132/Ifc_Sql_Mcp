"""Wait for another session to block on a lock, for tests that stage a real race."""

import time

import pytest
from sqlalchemy import Engine, text


def wait_until_a_session_waits_on_a_lock(engine: Engine, timeout_s: float = 30.0) -> None:
    """Return once some session of this database waits on a lock; fail after the timeout."""
    deadline = time.monotonic() + timeout_s
    with engine.connect() as connection:
        while time.monotonic() < deadline:
            waiting = connection.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            ).scalar_one()
            # pg_stat_activity is read once per transaction; end it to see fresh values.
            connection.rollback()
            if waiting:
                return
            time.sleep(0.02)
    pytest.fail("no session ever started waiting for the other transaction")
