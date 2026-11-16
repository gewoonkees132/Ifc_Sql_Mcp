"""Create the read-only ``ifc_reader`` role and give it SELECT on the ifc schema.

The MCP server's ``run_sql`` tool connects as this role, so it is the privilege boundary
for LLM-written SQL: SELECT on the tables and views of ``ifc``, nothing else. Session
defaults below (read-only transactions, timeouts) are guard rails on top, not the
boundary, because a client can override them with SET.

PostgreSQL roles are cluster-wide but this migration runs once per database: the
development database and the throwaway test databases share one cluster and one role.

* Upgrade creates the role only if it is missing, then (re)applies its login settings,
  password and this database's grants, so running it in a second database is harmless.
* Downgrade revokes everything this database granted and resets this database's role
  settings, but keeps the role: another database on the cluster may still rely on it,
  and PostgreSQL would refuse to drop it while it holds privileges there. A cluster
  without such databases can ``DROP ROLE ifc_reader`` by hand afterwards.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from psycopg import sql

from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.migrate import SETTINGS_ATTRIBUTE, psycopg_connection

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE = "ifc_reader"

# Applied with ALTER ROLE ... IN DATABASE, so they hold for this database only and are
# reset by the downgrade (or dropped with the database).
ROLE_SETTINGS = (
    # Every transaction starts read-only, so even a privilege mistake cannot write.
    ("default_transaction_read_only", "on"),
    # A runaway LLM query is cancelled instead of holding a connection and CPU.
    ("statement_timeout", "5s"),
    # A client that opens a transaction and goes quiet cannot hold locks indefinitely.
    ("idle_in_transaction_session_timeout", "30s"),
    # Unqualified names (FROM v_element) resolve to the ifc schema, and only to it.
    ("search_path", "ifc"),
)


def _reader_password() -> str:
    config = op.get_context().config
    if config is None:  # pragma: no cover - env.py always runs with a Config
        raise RuntimeError("migration 0003 needs the Alembic Config built by env.py")
    settings: Settings = config.attributes[SETTINGS_ATTRIBUTE]
    return settings.reader_password


def upgrade() -> None:
    """Create or update the role, then grant it read access to the ifc schema."""
    # CREATE ROLE has no IF NOT EXISTS. Catching the error, rather than checking first,
    # also covers two databases on the cluster being migrated at the same moment.
    # PostgreSQL's defaults already make a new role NOSUPERUSER, NOCREATEDB, NOCREATEROLE,
    # NOREPLICATION and NOBYPASSRLS; the next statement proves an existing one is too.
    op.execute(
        """
        DO $$
        BEGIN
            CREATE ROLE ifc_reader;
        EXCEPTION
            WHEN duplicate_object OR unique_violation THEN
                NULL;
        END
        $$
        """
    )
    # A role of that name created by someone else might be powerful. Granting it "read
    # only" would then be a false promise, so refuse instead of continuing.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT FROM pg_roles
                WHERE rolname = 'ifc_reader'
                  AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication
                       OR rolbypassrls)
            ) OR EXISTS (
                SELECT FROM pg_auth_members AS am
                JOIN pg_roles AS r ON am.member = r.oid
                WHERE r.rolname = 'ifc_reader'
            ) THEN
                RAISE EXCEPTION 'ifc_reader is privileged or a member of other roles';
            END IF;
        END
        $$
        """
    )

    # The password is hashed here, on the client, into the SCRAM verifier the server
    # stores (what psql's \password does). The plaintext never reaches the server, so it
    # cannot end up in its logs even with log_statement = 'all'.
    connection = psycopg_connection(op.get_bind())
    verifier = connection.pgconn.encrypt_password(
        _reader_password().encode(), ROLE.encode(), b"scram-sha-256"
    )
    connection.execute(
        sql.SQL("ALTER ROLE {role} WITH LOGIN CONNECTION LIMIT 20 PASSWORD {verifier}").format(
            role=sql.Identifier(ROLE), verifier=sql.Literal(verifier.decode("ascii"))
        )
    )

    database = op.get_bind().execute(sa.text("SELECT current_database()")).scalar_one()
    for name, value in ROLE_SETTINGS:
        connection.execute(
            sql.SQL("ALTER ROLE {role} IN DATABASE {database} SET {name} = {value}").format(
                role=sql.Identifier(ROLE),
                database=sql.Identifier(database),
                name=sql.Identifier(name),
                value=sql.Literal(value),
            )
        )

    op.execute("GRANT USAGE ON SCHEMA ifc TO ifc_reader")
    # ALL TABLES includes views.
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA ifc TO ifc_reader")
    # Tables and views that later migrations create (as the same owning role) are covered
    # automatically, so a new view is never silently invisible to the MCP server.
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA ifc GRANT SELECT ON TABLES TO ifc_reader")
    # PostgreSQL 15+ already denies CREATE on public to PUBLIC; databases initialised by
    # older versions and upgraded in place do not. Downgrade leaves this revoked: it is
    # the secure default, and nothing in the project needs the old behaviour back.
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")


def downgrade() -> None:
    """Revoke everything this database granted the role; keep the cluster-wide role."""
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA ifc REVOKE SELECT ON TABLES FROM ifc_reader")
    op.execute("REVOKE SELECT ON ALL TABLES IN SCHEMA ifc FROM ifc_reader")
    op.execute("REVOKE USAGE ON SCHEMA ifc FROM ifc_reader")

    database = op.get_bind().execute(sa.text("SELECT current_database()")).scalar_one()
    psycopg_connection(op.get_bind()).execute(
        sql.SQL("ALTER ROLE {role} IN DATABASE {database} RESET ALL").format(
            role=sql.Identifier(ROLE), database=sql.Identifier(database)
        )
    )
