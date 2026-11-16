#!/usr/bin/env python3
"""Time the load stage on the MEP sample: the measurement behind COPY in ``ingest/load.py``.

Usage (after ``make up`` and ``make samples``)::

    uv run python scripts/bench_load.py [--runs 5]

It creates a throwaway database (``<configured database>_bench``), migrates it,
extracts and transforms ``Duplex_MEP_20110907.ifc`` once, and then times, each in its own
transaction that is rolled back afterwards:

* the project's ``load()`` for the whole model, including the deferred reference checks
  that COMMIT would run;
* inserting MEP's property rows (63,683, the largest table of the four samples) in three
  ways: SQLAlchemy Core executemany, SQLAlchemy's insertmanyvalues (multi-row INSERT,
  which SQLAlchemy uses with psycopg only when there is a RETURNING clause) and COPY
  through ``load.copy_rows``;
* the same COPY after dropping the table's foreign key, and after dropping every
  constraint and index, to show what the integrity rules cost. PostgreSQL DDL is
  transactional, so the dropped constraints come back with the rollback.

It prints the median of each as a Markdown table, then drops the database.
"""

import argparse
import dataclasses
import statistics
import sys
import time
from collections.abc import Callable
from pathlib import Path

from download_samples import DEFAULT_DEST, SAMPLES, ensure_sample
from sqlalchemy import Connection, Engine, create_engine, insert, make_url, text
from sqlalchemy.pool import NullPool

from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db import schema
from ifc_sql_mcp.db.engine import make_engine
from ifc_sql_mcp.db.migrate import upgrade
from ifc_sql_mcp.ingest.extract import read_model
from ifc_sql_mcp.ingest.load import copy_rows, load
from ifc_sql_mcp.ingest.records import ModelRecords
from ifc_sql_mcp.ingest.transform import transform

type Rows = list[dict[str, object]]
type Step = Callable[[Connection], None]


def _bench_database(settings: Settings) -> tuple[Settings, Engine]:
    """Create and migrate ``<db>_bench``; return its settings and an autocommit server engine."""
    base_url = make_url(settings.database_url)
    name = f"{base_url.database}_bench"
    server = create_engine(settings.database_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    quoted = server.dialect.identifier_preparer.quote(name)
    with server.connect() as connection:
        connection.execute(text(f"DROP DATABASE IF EXISTS {quoted} WITH (FORCE)"))
        connection.execute(text(f"CREATE DATABASE {quoted}"))
    url = base_url.set(database=name).render_as_string(hide_password=False)
    bench = dataclasses.replace(settings, database_url=url)
    upgrade(bench)
    return bench, server


def _property_rows(connection: Connection, records: ModelRecords) -> Rows:
    """Load everything but the properties; return the property rows with real object ids."""
    result = load(connection, dataclasses.replace(records, properties=()), "MEP")
    object_ids: dict[str, int] = dict(
        connection.execute(
            text("SELECT global_id, object_id FROM ifc.object WHERE model_id = :m"),
            {"m": result.model_id},
        ).all()
    )
    rows: Rows = []
    for record in records.properties:
        row: dict[str, object] = {"object_id": object_ids[record.global_id]}
        row |= {k: v for k, v in dataclasses.asdict(record).items() if k != "global_id"}
        rows.append(row)
    return rows


def _drop_property_constraints(connection: Connection, *, all_of_them: bool) -> None:
    """Drop the property table's foreign key, or every constraint and index (rolled back later)."""
    table = schema.property_
    for constraint in table.constraints:
        if all_of_them or constraint in table.foreign_key_constraints:
            drop = f'ALTER TABLE ifc.property DROP CONSTRAINT "{constraint.name}"'
            connection.execute(text(drop))
    if all_of_them:
        for index in table.indexes:
            connection.execute(text(f'DROP INDEX ifc."{index.name}"'))


def _time(engine: Engine, runs: int, setup: Step, step: Step, check: Step) -> float:
    """Median seconds of ``step`` over ``runs`` rolled-back transactions; setup is not timed."""
    seconds = []
    for _ in range(runs):
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                setup(connection)
                start = time.perf_counter()
                step(connection)
                seconds.append(time.perf_counter() - start)
                check(connection)
            finally:
                transaction.rollback()
    return statistics.median(seconds)


def main(argv: list[str] | None = None) -> int:
    """Run the benchmark and print the results."""
    parser = argparse.ArgumentParser(description="Time the load stage on the MEP sample.")
    parser.add_argument("--runs", type=int, default=5, help="runs per measurement (default 5)")
    runs: int = parser.parse_args(argv).runs

    path, _ = ensure_sample(SAMPLES["mep"], Path(DEFAULT_DEST))
    records = transform(read_model(path))
    bench, server = _bench_database(Settings.from_env())
    engine = make_engine(bench.database_url)
    rows: Rows = []

    def prepare(connection: Connection) -> None:
        rows[:] = _property_rows(connection, records)

    def no_setup(connection: Connection) -> None:
        pass

    def all_properties_present(connection: Connection) -> None:
        count = connection.execute(text("SELECT count(*) FROM ifc.property")).scalar_one()
        if count != len(records.properties):
            raise RuntimeError(f"expected {len(records.properties)} property rows, found {count}")

    def whole_model(connection: Connection) -> None:
        load(connection, records, "MEP")
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

    def executemany(connection: Connection) -> None:
        connection.execute(insert(schema.property_), rows)

    def insertmanyvalues(connection: Connection) -> None:
        statement = insert(schema.property_).returning(schema.property_.c.object_id)
        connection.execute(statement, rows).all()

    def copy(connection: Connection) -> None:
        copy_rows(connection, schema.property_, rows)

    def without_foreign_key(connection: Connection) -> None:
        prepare(connection)
        _drop_property_constraints(connection, all_of_them=False)

    def without_anything(connection: Connection) -> None:
        prepare(connection)
        _drop_property_constraints(connection, all_of_them=True)

    measurements = [
        ("`load()`, whole model, with the checks COMMIT runs", no_setup, whole_model),
        ("property rows: SQLAlchemy Core executemany", prepare, executemany),
        ("property rows: SQLAlchemy insertmanyvalues (RETURNING)", prepare, insertmanyvalues),
        ("property rows: COPY (`load.copy_rows`)", prepare, copy),
        ("property rows: COPY, table without its foreign key", without_foreign_key, copy),
        ("property rows: COPY, table without constraints or indexes", without_anything, copy),
    ]
    try:
        print(
            f"MEP: {len(records.objects):,} objects, {len(records.properties):,} property rows; "
            f"median of {runs} runs, each in a rolled-back transaction\n"
        )
        print("| Measurement | Median (s) |\n|---|---:|")
        for label, setup, step in measurements:
            seconds = _time(engine, runs, setup, step, all_properties_present)
            print(f"| {label} | {seconds:.2f} |", flush=True)
    finally:
        engine.dispose()
        with server.connect() as connection:
            name = make_url(bench.database_url).database or ""
            quoted = server.dialect.identifier_preparer.quote(name)
            connection.execute(text(f"DROP DATABASE IF EXISTS {quoted} WITH (FORCE)"))
        server.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
