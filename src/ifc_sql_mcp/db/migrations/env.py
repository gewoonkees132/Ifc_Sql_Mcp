"""Alembic environment: run the migrations against the database named in Settings.

``ifc-sql migrate`` and the tests pass their Settings in through ``config.attributes``;
the plain ``alembic`` CLI falls back to the environment variables, like every other entry
point. The URL never comes from a config file, so no credentials live in one.
"""

from alembic import context

from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.migrate import AUTOGENERATE_OPTIONS, SETTINGS_ATTRIBUTE, migration_engine
from ifc_sql_mcp.db.schema import metadata

config = context.config
if SETTINGS_ATTRIBUTE not in config.attributes:
    config.attributes[SETTINGS_ATTRIBUTE] = Settings.from_env()
settings: Settings = config.attributes[SETTINGS_ATTRIBUTE]

if context.is_offline_mode():
    # Offline mode renders SQL instead of running it. Migration 0003 sets the reader
    # password from a SCRAM verifier computed on a live connection, which a script
    # cannot reproduce, and writing it into a .sql file would leak it.
    raise SystemExit("offline (--sql) migrations are not supported; run against a database")

engine = migration_engine(settings.database_url)
try:
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=metadata,
            # All pending migrations run in one transaction (PostgreSQL DDL is
            # transactional), so a failed upgrade leaves the database as it was.
            transaction_per_migration=False,
            **AUTOGENERATE_OPTIONS,
        )
        with context.begin_transaction():
            context.run_migrations()
finally:
    engine.dispose()
