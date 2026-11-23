"""Phase 0, item 3: NOTIFY as ifc_reader on the embedded database.

Usage: uv run python -I phase0_notify.py DATA_DIR

Prints one line per check.
"""

# ruff: noqa: T201

import json
import statistics
import sys
import threading
import time
from pathlib import Path

import psycopg
from sqlalchemy import text

from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.embedded import _server, embedded_database_url
from ifc_sql_mcp.db.engine import make_reader_engine, reader_database_url
from ifc_sql_mcp.db.migrate import upgrade

CHANNEL = "ifc_viewer_feed"
PASSWORD = "ifc-reader-dev-only"  # noqa: S105 (the public development default)


def libpq(url: str) -> str:
    """Return a SQLAlchemy URL as a libpq URL."""
    return url.replace("postgresql+psycopg://", "postgresql://")


class Listener(threading.Thread):
    """A LISTEN thread that reconnects with backoff, as view's listener would."""

    def __init__(self, url_of) -> None:
        super().__init__(daemon=True)
        self.url_of = url_of
        self.stop = threading.Event()
        self.received: list[tuple[float, str]] = []
        self.reconnects = 0
        self.errors: list[str] = []
        self.pids: list[int] = []
        self.listening = threading.Event()

    def run(self) -> None:
        """Listen until stopped, reconnecting after any error."""
        backoff = 0.2
        while not self.stop.is_set():
            try:
                with psycopg.connect(
                    libpq(self.url_of()), autocommit=True, connect_timeout=5
                ) as conn:
                    conn.execute(f"LISTEN {CHANNEL}")
                    self.pids.append(conn.info.backend_pid)
                    self.listening.set()
                    backoff = 0.2
                    while not self.stop.is_set():
                        for note in conn.notifies(timeout=1):
                            self.received.append((time.perf_counter(), note.payload))
            except Exception as exc:  # spike: also errors from re-resolving the embedded URL
                self.errors.append(type(exc).__name__ + ": " + (str(exc).splitlines() or [""])[0])
                self.listening.clear()
                self.reconnects += 1
                time.sleep(backoff)
                backoff = min(backoff * 2, 5)


def wait_for(predicate, seconds=10.0) -> bool:
    """Return True once ``predicate`` holds, False after ``seconds``."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def main() -> None:
    """Run the checks and print one line each."""
    data_dir = Path(sys.argv[1])
    owner_url = embedded_database_url(data_dir)
    settings = Settings(database_url=owner_url, reader_password=PASSWORD, log_level="INFO")
    upgrade(settings)
    reader_url = reader_database_url(owner_url, PASSWORD)
    print("owner url", owner_url.split("@")[1])

    with psycopg.connect(libpq(reader_url), autocommit=True) as conn:
        print("reader:", conn.execute("SELECT current_user, version()").fetchone()[0])
        print(
            "default_transaction_read_only:",
            conn.execute("SHOW default_transaction_read_only").fetchone()[0],
        )

    listener = Listener(lambda: reader_url)  # a fixed URL, as view would hold
    listener.start()
    if not listener.listening.wait(10):
        raise SystemExit("the listener did not start")

    # 1. pg_notify as ifc_reader, committed READ ONLY transaction (explicit BEGIN READ ONLY).
    with psycopg.connect(libpq(reader_url)) as conn:
        conn.read_only = True
        conn.execute("SELECT pg_notify(%s, %s)", (CHANNEL, "explicit-read-only"))
        ro = conn.execute("SHOW transaction_read_only").fetchone()[0]
        conn.commit()
    ok = wait_for(lambda: any(p == "explicit-read-only" for _, p in listener.received))
    print(
        "1. pg_notify in a committed READ ONLY transaction "
        f"(transaction_read_only={ro}): delivered={ok}"
    )

    # 1b. through the server's own reader engine (REPEATABLE READ READ ONLY), committed.
    engine = make_reader_engine(settings)
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_notify(:c, :p)"), {"c": CHANNEL, "p": "engine-begin"})
    ok = wait_for(lambda: any(p == "engine-begin" for _, p in listener.received))
    print(f"1b. through make_reader_engine, engine.begin(): delivered={ok}")

    # 1c. rolled back: not delivered (why a second, committed transaction is needed).
    with engine.connect() as connection:
        tx = connection.begin()
        connection.execute(text("SELECT pg_notify(:c, :p)"), {"c": CHANNEL, "p": "rolled-back"})
        tx.rollback()
    time.sleep(1.5)
    print(f"1c. rolled back: delivered={any(p == 'rolled-back' for _, p in listener.received)}")

    # 2. the 8,000-byte boundary, ASCII and multibyte.
    for label, payload in (
        ("7999 ASCII bytes", "a" * 7999),
        ("8000 ASCII bytes", "a" * 8000),
        ("7998 bytes of é (3999 chars)", "é" * 3999),
        ("8000 bytes of é (4000 chars)", "é" * 4000),
    ):
        try:
            with engine.begin() as connection:
                connection.execute(text("SELECT pg_notify(:c, :p)"), {"c": CHANNEL, "p": payload})
            print(f"2. {label}: accepted")
        except Exception as exc:
            orig = getattr(exc, "orig", exc)
            print(
                f"2. {label}: refused, SQLSTATE {getattr(orig, 'sqlstate', None)}: "
                f"{str(orig).splitlines()[0]}"
            )

    # 3. what a notify adds to a call: a new session + BEGIN READ ONLY + pg_notify + COMMIT.
    samples = []
    arrivals = []
    for n in range(30):
        tag = f"timing-{n}"
        started = time.perf_counter()
        with engine.begin() as connection:
            connection.execute(text("SELECT pg_notify(:c, :p)"), {"c": CHANNEL, "p": tag})
        sent = time.perf_counter()
        samples.append((sent - started) * 1000)
        wait_for(lambda tag=tag: any(p == tag for _, p in listener.received))
        arrived = next(t for t, p in listener.received if p == tag)
        arrivals.append((arrived - sent) * 1000)
    print(
        f"3. notify via a new session: median {statistics.median(samples):.1f} ms "
        f"(min {min(samples):.1f}, max {max(samples):.1f}, n=30); "
        f"send-to-receive median {statistics.median(arrivals):.1f} ms"
    )
    # For comparison: a bare new session (connect + close).
    bare = []
    for _ in range(30):
        started = time.perf_counter()
        with engine.connect():
            pass
        bare.append((time.perf_counter() - started) * 1000)
    print(f"3b. a bare new reader session: median {statistics.median(bare):.1f} ms")

    # 4. reconnect after the listener's backend is terminated (as the owner).
    before = listener.reconnects
    with psycopg.connect(libpq(owner_url), autocommit=True) as conn:
        conn.execute("SELECT pg_terminate_backend(%s)", (listener.pids[-1],))
    ok = wait_for(lambda: listener.reconnects > before and listener.listening.is_set(), 15)
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_notify(:c, :p)"), {"c": CHANNEL, "p": "after-terminate"})
    got = wait_for(lambda: any(p == "after-terminate" for _, p in listener.received))
    print(
        f"4. after pg_terminate_backend: reconnected={ok}, delivered={got}, "
        f"error={listener.errors[-1:]}"
    )

    # 5. reconnect after the database restarts on the same port (pg_ctl restart -m fast).
    from pixeltable_pgserver.pgexec import pgexec

    server = _server(data_dir)
    before = listener.reconnects
    stopped_at = time.perf_counter()
    pgexec(
        "pg_ctl",
        ("-w", "-D", str(server.pgdata), "restart", "-m", "fast"),
        bin_path=server.bin_path,
    )
    restarted = time.perf_counter() - stopped_at
    ok = wait_for(lambda: listener.reconnects > before and listener.listening.is_set(), 30)
    reconnected_after = time.perf_counter() - stopped_at
    engine.dispose()
    engine = make_reader_engine(settings)
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_notify(:c, :p)"), {"c": CHANNEL, "p": "after-restart"})
    got = wait_for(lambda: any(p == "after-restart" for _, p in listener.received))
    print(
        f"5. after pg_ctl restart -m fast ({restarted:.1f} s): reconnected={ok} "
        f"({reconnected_after:.1f} s after the restart began, "
        f"{listener.reconnects - before} failed attempts), "
        f"delivered={got}"
    )

    # 6. ordering: 200 events from one sender arrive in order.
    tags = [f"order-{n}" for n in range(200)]
    with engine.connect() as connection:
        for tag in tags:
            with connection.begin():
                connection.execute(text("SELECT pg_notify(:c, :p)"), {"c": CHANNEL, "p": tag})
    wait_for(lambda: sum(1 for _, p in listener.received if p.startswith("order-")) == 200)
    seen = [p for _, p in listener.received if p.startswith("order-")]
    print(f"6. 200 events in order: {seen == tags}")

    listener.stop.set()
    listener.join(5)
    engine.dispose()
    print(json.dumps({"errors_seen": listener.errors}))


if __name__ == "__main__":
    main()
