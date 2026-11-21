"""Execute a query an LLM wrote, after :mod:`~ifc_sql_mcp.server.sql_guard` has passed it.

The query runs in the call's transaction (:func:`~ifc_sql_mcp.db.engine.reader_transaction`):
a new session as ``ifc_reader``, ``REPEATABLE READ READ ONLY``, with ``statement_timeout``
and ``lock_timeout`` set by ``SET LOCAL``, always rolled back.

It runs through a server-side cursor: psycopg sends ``DECLARE run_sql CURSOR FOR <query>``
over the extended protocol, without parameters, and then ``FETCH FORWARD n``. That one
choice gives three guarantees, each checked by ``tests/integration/test_run_sql.py``:

* **PostgreSQL refuses anything but one SELECT or VALUES.** The extended protocol takes
  one statement ("cannot insert multiple commands into a prepared statement"), and
  DECLARE takes only a query: it refuses DML, a data-modifying WITH and SELECT … INTO by
  itself. This holds even if the validator and PostgreSQL parse a text differently.
* **Nothing in the text is a parameter.** SQLAlchemy's ``text()`` would read ``:word`` as
  a bind parameter even inside a string literal, and psycopg reads ``%`` once parameters
  are passed. The query is handed to psycopg as it is, with none.
* **At most ``max_rows + 1`` rows leave the database.** A client-side cursor would
  receive the whole result before the first row could be dropped.

A query that names a relation or column that does not exist is the commonest failure
(audit finding A10: 20 of 72 calls in six sessions). The query runs inside a savepoint,
so after such an error the transaction can still read the catalogue, and the error
carries the names that do exist (:func:`~ifc_sql_mcp.server.catalogue.explain_unknown_name`).

``set_config('statement_timeout', '0', true)`` inside a query cannot lift the timeout of
the statement it runs in (PostgreSQL arms the timer when the statement starts; a test
shows the cancellation), only of later statements in the transaction. The only one
after the FETCH is the cursor's CLOSE, and the rollback then ends the transaction and
undoes the setting. The validator refuses ``set_config`` anyway.
"""

import json
from typing import Literal

import psycopg
from pydantic import BaseModel, Field
from sqlalchemy import Connection

from ifc_sql_mcp.db.engine import psycopg_connection
from ifc_sql_mcp.server.catalogue import explain_unknown_name
from ifc_sql_mcp.server.tools import ToolResult
from ifc_sql_mcp.server.values import MAX_TEXT_CHARS, JsonValue, to_cell

DEFAULT_MAX_ROWS = 100
MAX_ROWS = 500
MAX_RESULT_CHARS = 30_000
"""Most characters of rows (as compact JSON) one result carries.

Claude Code warns at 10,000 tokens of tool output and saves text results over 50,000
characters to a file instead of showing them to the model; 30,000 characters of rows
keep a result in the conversation.
"""

CURSOR_NAME = "run_sql"


class ExplainedDatabaseError(Exception):
    """The database refused the query, and the catalogue says what would have worked.

    Attributes:
        error: The database's own error.
        explanation: The relations or columns that do exist, to append to its message.
    """

    def __init__(self, error: psycopg.Error, explanation: str) -> None:
        super().__init__(str(error))
        self.error = error
        self.explanation = explanation


class Column(BaseModel):
    """A column of the result."""

    name: str
    type: str = Field(description="PostgreSQL type, e.g. int8, text, numeric(9,3).")


class SqlResult(ToolResult):
    """The rows a query returned, as JSON arrays in column order."""

    columns: list[Column]
    rows: list[list[JsonValue]]
    row_count: int = Field(description="Rows returned here.")
    truncated: bool = Field(description="True when the query had more rows than returned.")
    truncated_by: Literal["max_rows", "size"] | None = Field(
        description=f"max_rows: more rows than max_rows. size: the rows would pass "
        f"{MAX_RESULT_CHARS:,} characters."
    )
    values_cut: int = Field(
        description=f"Values longer than {MAX_TEXT_CHARS:,} characters, cut and ending with "
        "a note that gives their length."
    )
    note: str | None = Field(description="What to do when the result was truncated.")

    def logged_rows(self) -> int:
        """Return the number of rows returned."""
        return self.row_count


def execute_query(connection: Connection, sql: str, max_rows: int) -> SqlResult:
    """Run ``sql`` in ``connection``'s transaction and return at most ``max_rows`` rows.

    ``sql`` is executed as written; call :func:`~ifc_sql_mcp.server.sql_guard.check_query`
    first. Database errors propagate as psycopg exceptions, except an unknown relation or
    column.

    Raises:
        ExplainedDatabaseError: ``sql`` names a relation or column that does not exist.
    """
    savepoint = connection.begin_nested()
    cursor = psycopg_connection(connection).cursor(name=CURSOR_NAME)
    try:
        cursor.execute(sql)
        fetched = cursor.fetchmany(max_rows + 1)
        columns = [Column(name=c.name, type=c.type_display) for c in cursor.description or []]
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn) as exc:
        savepoint.rollback()
        message = exc.diag.message_primary or str(exc)
        explanation = explain_unknown_name(connection, sql, exc.sqlstate or "", message)
        raise ExplainedDatabaseError(exc, explanation) from exc
    finally:
        cursor.close()
    savepoint.commit()

    rows: list[list[JsonValue]] = []
    size = 0
    values_cut = 0
    truncated_by: Literal["max_rows", "size"] | None = None
    for record in fetched[:max_rows]:
        cells = [to_cell(value) for value in record]
        row = [value for value, _ in cells]
        row_size = len(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        if size + row_size > MAX_RESULT_CHARS:
            truncated_by = "size"
            break
        rows.append(row)
        size += row_size
        values_cut += sum(cut for _, cut in cells)
    else:
        if len(fetched) > max_rows:
            truncated_by = "max_rows"
    return SqlResult(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=truncated_by is not None,
        truncated_by=truncated_by,
        values_cut=values_cut,
        note=_note(truncated_by, max_rows),
    )


def _note(truncated_by: Literal["max_rows", "size"] | None, max_rows: int) -> str | None:
    if truncated_by == "max_rows":
        return (
            f"The query returned more than {max_rows} rows. Aggregate (count, sum, GROUP BY), "
            f"raise max_rows (at most {MAX_ROWS}), or page with ORDER BY … LIMIT … OFFSET."
        )
    if truncated_by == "size":
        return (
            f"The rows would pass {MAX_RESULT_CHARS:,} characters. Select fewer or shorter "
            "columns (substr() reads part of a long text), aggregate, or page with "
            "ORDER BY … LIMIT … OFFSET."
        )
    return None
