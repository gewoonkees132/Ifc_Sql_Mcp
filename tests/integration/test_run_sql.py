"""``run_sql``: every safety layer, each tested on its own against PostgreSQL (ADR 6).

The layers, from the outside in:

1. the validator (``sql_guard``), unit-tested in ``tests/unit/server/test_sql_guard.py``
   and here only through the tool;
2. the executor: a cursor declared over the extended protocol, which PostgreSQL accepts
   only for one SELECT or VALUES, and which fetches at most ``max_rows + 1`` rows;
3. the transaction: read-only, with SET LOCAL timeouts, in a new session every call;
4. the privileges of ``ifc_reader``: the real boundary, tested with the read-only
   transaction switched off.

To test a deeper layer, a query goes past the ones above it: straight to
``execute_query``, or through a plain cursor.
"""

import logging
import re
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
import sqlglot
from mcp.server.mcpserver import MCPServer
from mcp_client import call, call_error, connect
from sqlalchemy import Connection, Engine, text
from sqlglot import exp

from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.engine import make_engine, psycopg_connection, reader_transaction
from ifc_sql_mcp.server import app as server_app
from ifc_sql_mcp.server.app import Limits, build_server
from ifc_sql_mcp.server.run_sql import MAX_RESULT_CHARS, execute_query
from ifc_sql_mcp.server.sql_guard import ALLOWED_UNTYPED_FUNCTIONS
from ifc_sql_mcp.server.values import MAX_TEXT_CHARS

pytestmark = pytest.mark.anyio

SHORT = Limits(statement_timeout_ms=200, lock_timeout_ms=200)
# As in the defaults (5 s and 1 s), a lock wait gives up before the statement would.
SHORT_LOCK = Limits(statement_timeout_ms=5_000, lock_timeout_ms=200)


@pytest.fixture
def server(mcp_engine: Engine) -> MCPServer:
    return build_server(mcp_engine)


@pytest.fixture
def reader(mcp_engine: Engine) -> Iterator[Connection]:
    """A transaction exactly as a tool call gets one, with short timeouts."""
    with reader_transaction(mcp_engine, statement_timeout_ms=200, lock_timeout_ms=200) as conn:
        yield conn


def _sqlstate(excinfo: pytest.ExceptionInfo[psycopg.Error]) -> str | None:
    return excinfo.value.sqlstate


def _message(excinfo: pytest.ExceptionInfo[psycopg.Error]) -> str | None:
    """The database's own message, without the LINE context psycopg adds to str()."""
    return excinfo.value.diag.message_primary


# --------------------------------------------------------------------------- 1. the validator


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("DELETE FROM ifc.property", "this is a DELETE statement"),
        (
            "WITH d AS (DELETE FROM ifc.property RETURNING *) SELECT count(*) FROM d",
            "the query contains DELETE",
        ),
        ("SELECT pg_sleep(10)", "function pg_sleep() is not allowed"),
        ("SELECT 1; SELECT 2", "send one statement at a time"),
        ("SELECT * FROM pg_stat_activity", "pg_stat_activity is a PostgreSQL system catalogue"),
    ],
)
async def test_the_tool_refuses_what_the_validator_refuses(
    server: MCPServer, sql: str, reason: str
) -> None:
    async with connect(server) as client:
        message = await call_error(client, "run_sql", sql=sql)

    assert message.startswith("query refused: ")
    assert reason in message


# --------------------------------------------------------------------------- 2. the executor


@pytest.mark.parametrize(
    ("sql", "sqlstate", "message"),
    [
        (
            "DELETE FROM ifc.property",
            "42601",
            'syntax error at or near "DELETE"',
        ),
        (
            "SELECT 1; DELETE FROM ifc.property",
            "42601",
            "cannot insert multiple commands into a prepared statement",
        ),
        (
            # PostgreSQL reads this as two statements (a backslash does not escape a quote
            # in a standard string); the extended protocol refuses the second.
            r"SELECT '\'; DELETE FROM ifc.property; --'",
            "42601",
            "cannot insert multiple commands into a prepared statement",
        ),
        (
            "WITH d AS (DELETE FROM ifc.property RETURNING *) SELECT count(*) FROM d",
            "0A000",
            "DECLARE CURSOR must not contain data-modifying statements in WITH",
        ),
        ("SELECT 1 AS a INTO scratch", "42601", "SELECT ... INTO is not allowed here"),
        ("SET statement_timeout = 0", "42601", 'syntax error at or near "SET"'),
        ("SHOW search_path", "42601", 'syntax error at or near "SHOW"'),
        ("EXPLAIN ANALYZE DELETE FROM ifc.property", "42601", 'syntax error at or near "EXPLAIN"'),
    ],
)
def test_the_executor_runs_nothing_but_one_query(
    reader: Connection, sql: str, sqlstate: str, message: str
) -> None:
    """Sent past the validator: DECLARE … CURSOR FOR accepts one SELECT or VALUES only."""
    with pytest.raises(psycopg.Error) as excinfo:
        execute_query(reader, sql, max_rows=10)

    assert (_sqlstate(excinfo), _message(excinfo)) == (sqlstate, message)


def test_the_executor_reads_no_parameter_in_the_text(reader: Connection) -> None:
    """SQLAlchemy's text() would read ':x' as a bind parameter, psycopg '%s' as a placeholder."""
    result = execute_query(reader, "SELECT ':x' AS a, '%s' AS b, '%' AS c, 'a::b' AS d", 10)

    assert result.rows == [[":x", "%s", "%", "a::b"]]


def test_the_executor_computes_at_most_one_row_more_than_it_returns(reader: Connection) -> None:
    """Row 7 divides by zero: had the cursor computed it, the query would fail."""
    result = execute_query(reader, "SELECT 1 / (7 - n) AS x FROM generate_series(1, 10) AS n", 5)

    assert result.row_count == 5
    assert (result.truncated, result.truncated_by) == (True, "max_rows")


# --------------------------------------------------------------------------- 3. the transaction


def test_every_call_runs_read_only_with_its_own_limits(reader: Connection) -> None:
    result = execute_query(
        reader,
        "SELECT current_setting('transaction_read_only'), "
        "current_setting('transaction_isolation'), "
        "current_setting('statement_timeout'), current_setting('lock_timeout'), "
        "current_setting('search_path'), current_setting('jit'), current_user",
        10,
    )

    assert result.rows == [["on", "repeatable read", "200ms", "200ms", "ifc", "off", "ifc_reader"]]


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        (
            "SELECT model_id FROM ifc.model FOR UPDATE",
            "cannot execute SELECT FOR UPDATE in a read-only transaction",
        ),
        ("SELECT lo_create(0)", "cannot execute lo_create() in a read-only transaction"),
    ],
)
def test_the_read_only_transaction_refuses_writes_a_select_can_make(
    reader: Connection, sql: str, message: str
) -> None:
    with pytest.raises(psycopg.Error) as excinfo:
        execute_query(reader, sql, max_rows=10)

    assert (_sqlstate(excinfo), _message(excinfo)) == ("25006", message)


def test_the_statement_timeout_cancels_a_long_query(reader: Connection) -> None:
    with pytest.raises(psycopg.errors.QueryCanceled, match="statement timeout"):
        execute_query(reader, "SELECT pg_sleep(5)", max_rows=1)


def test_set_config_in_the_query_cannot_lift_its_own_timeout(reader: Connection) -> None:
    """PostgreSQL arms the timer when the statement starts; a later change does not disarm it."""
    with pytest.raises(psycopg.errors.QueryCanceled, match="statement timeout"):
        execute_query(
            reader, "SELECT set_config('statement_timeout', '0', true), pg_sleep(5)", max_rows=1
        )


def test_nothing_a_call_sets_reaches_the_next_one(mcp_engine: Engine) -> None:
    """A session-level change and a session advisory lock both end with the call's session."""
    with reader_transaction(mcp_engine, statement_timeout_ms=200, lock_timeout_ms=200) as first:
        execute_query(first, "SELECT set_config('statement_timeout', '0', false)", 1)
        execute_query(first, "SELECT pg_advisory_lock(4242)", 1)

    with reader_transaction(mcp_engine, statement_timeout_ms=300, lock_timeout_ms=200) as second:
        timeout = execute_query(second, "SELECT current_setting('statement_timeout')", 1)
        locks = execute_query(
            second, "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND objid = 4242", 1
        )

    assert timeout.rows == [["300ms"]]
    assert locks.rows == [[0]]


async def test_a_long_query_comes_back_as_a_timeout_error(mcp_engine: Engine) -> None:
    async with connect(build_server(mcp_engine, SHORT)) as client:
        message = await call_error(
            client, "run_sql", sql="SELECT count(*) FROM generate_series(1, 1000000000)"
        )

    assert message == (
        "the query was cancelled after 0.2 seconds (the statement timeout); narrow it down, "
        "aggregate, or add a LIMIT"
    )


async def test_a_query_behind_a_lock_comes_back_as_a_timeout_error(
    mcp_engine: Engine, mcp_settings: Settings
) -> None:
    """An owner's transaction holds the table, as a migration would; the query gives up."""
    owner = make_engine(mcp_settings.database_url)
    try:
        with owner.connect() as connection, connection.begin():
            connection.execute(text("LOCK TABLE ifc.model IN ACCESS EXCLUSIVE MODE"))
            async with connect(build_server(mcp_engine, SHORT_LOCK)) as client:
                message = await call_error(client, "run_sql", sql="SELECT count(*) FROM ifc.model")
            connection.rollback()
    finally:
        owner.dispose()

    assert message == (
        "the query waited too long for a lock, probably behind a migration or a load in "
        "progress; try again shortly"
    )


# --------------------------------------------------------------------------- 4. the privileges


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        (
            "SELECT nextval('ifc.model_model_id_seq')",
            "permission denied for sequence model_model_id_seq",
        ),
        ("SELECT model_id FROM ifc.model FOR UPDATE", "permission denied for table model"),
        ("SELECT pg_read_file('postgresql.conf')", "permission denied for function pg_read_file"),
        (
            "SELECT * FROM public.alembic_version FOR UPDATE",
            "permission denied for table alembic_version",
        ),
    ],
)
def test_the_privileges_refuse_what_the_query_itself_could_write(
    reader: Connection, sql: str, message: str
) -> None:
    """With the read-only transaction switched off, as a hostile client could."""
    reader.execute(text("SET TRANSACTION READ WRITE"))

    with pytest.raises(psycopg.Error) as excinfo:
        execute_query(reader, sql, max_rows=10)

    assert (_sqlstate(excinfo), _message(excinfo)) == ("42501", message)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO ifc.dq_rule VALUES ('r', 'd', 'info', 'validity')",
        "UPDATE ifc.model SET discipline = 'STR'",
        "DELETE FROM ifc.property",
        "TRUNCATE ifc.quantity",
        "CREATE TABLE ifc.scratch (a int)",
        "CREATE TEMP TABLE scratch (a int)",
    ],
)
def test_the_privileges_refuse_a_write_sent_past_every_other_layer(
    reader: Connection, statement: str
) -> None:
    """No validator, no cursor, no read-only transaction: the role alone refuses."""
    reader.execute(text("SET TRANSACTION READ WRITE"))

    with pytest.raises(psycopg.Error) as excinfo:
        psycopg_connection(reader).execute(statement)

    assert _sqlstate(excinfo) == "42501"


# --------------------------------------------------------------------------- results


async def test_results_carry_column_names_types_and_json_values(server: MCPServer) -> None:
    async with connect(server) as client:
        result = await call(
            client,
            "run_sql",
            sql="SELECT model_id, name, elevation_m, 'NaN'::float8 AS nan, "
            "12345678901234567890.123::numeric AS big, '\\x01ff'::bytea AS raw, "
            "ARRAY[1.5, 2]::numeric[] AS arr, '{\"a\": [1]}'::jsonb AS doc, "
            "interval '90.5 seconds' AS duration, true AS flag, NULL::text AS nothing "
            "FROM ifc.object JOIN ifc.spatial_element USING (object_id) "
            "WHERE global_id = '0level2_______________' AND model_id = 1",
        )

    assert result["columns"] == [
        {"name": "model_id", "type": "int8"},
        {"name": "name", "type": "text"},
        {"name": "elevation_m", "type": "numeric(9,3)"},
        {"name": "nan", "type": "float8"},
        {"name": "big", "type": "numeric"},
        {"name": "raw", "type": "bytea"},
        {"name": "arr", "type": "numeric[]"},
        {"name": "doc", "type": "jsonb"},
        {"name": "duration", "type": "interval"},
        {"name": "flag", "type": "bool"},
        {"name": "nothing", "type": "text"},
    ]
    assert result["rows"] == [
        [1, "Level 2", 3.0, "NaN", "12345678901234567890.123", "\\x01ff", [1.5, 2.0], {"a": [1]},
         "P0DT0H1M30.5S", True, None],
    ]  # fmt: skip
    assert _summary(result) == (1, False, None, 0, None)


def _summary(result: dict[str, Any]) -> tuple[object, ...]:
    return (
        result["row_count"],
        result["truncated"],
        result["truncated_by"],
        result["values_cut"],
        result["note"],
    )


async def test_results_stop_at_max_rows_and_say_what_to_do(server: MCPServer) -> None:
    sql = "SELECT n FROM generate_series(1, 150) AS n"
    async with connect(server) as client:
        default = await call(client, "run_sql", sql=sql)
        raised = await call(client, "run_sql", sql=sql, max_rows=150)

    assert default["rows"] == [[n] for n in range(1, 101)]
    assert _summary(default) == (
        100,
        True,
        "max_rows",
        0,
        "The query returned more than 100 rows. Aggregate (count, sum, GROUP BY), raise "
        "max_rows (at most 500), or page with ORDER BY … LIMIT … OFFSET.",
    )
    assert _summary(raised) == (150, False, None, 0, None)


async def test_results_cut_a_long_value_and_count_it(server: MCPServer) -> None:
    async with connect(server) as client:
        result = await call(client, "run_sql", sql="SELECT repeat('x', 5000) AS long, 'short'")

    [[long, short]] = result["rows"]
    assert long == "x" * MAX_TEXT_CHARS + "… [cut: 5,000 characters, the first 2,000 shown]"
    assert short == "short"
    assert result["values_cut"] == 1


async def test_results_stop_before_they_pass_the_size_limit(server: MCPServer) -> None:
    async with connect(server) as client:
        many = await call(
            client, "run_sql", sql="SELECT repeat('x', 1900) FROM generate_series(1, 100)"
        )
        wide = await call(
            client,
            "run_sql",
            sql="SELECT " + ", ".join(f"repeat('x', 1999) AS c{i}" for i in range(20)),
        )

    # Each row is ["xxx…"], 1,904 characters of JSON: 15 fit in 30,000.
    assert many["row_count"] == MAX_RESULT_CHARS // 1904 == 15
    assert many["truncated_by"] == "size"
    assert many["note"].startswith("The rows would pass 30,000 characters.")
    # A first row that alone is too large leaves no row, with the same advice.
    assert (wide["row_count"], wide["truncated_by"], len(wide["columns"])) == (0, "size", 20)


async def test_a_database_error_comes_back_with_its_reason_and_hint(server: MCPServer) -> None:
    async with connect(server) as client:
        unknown = await call_error(client, "run_sql", sql="SELECT nope FROM v_element")
        division = await call_error(client, "run_sql", sql="SELECT 1 / 0")

    assert unknown == (
        'the database refused the query: column "nope" does not exist Hint: Perhaps you meant '
        'to reference the column "v_element.name". (SQLSTATE 42703). Columns of '
        "v_element: object_id, model_id, discipline, global_id, ifc_class, ifc_class_base, "
        "is_feature, name, revit_family, revit_type, host_revit_family, host_revit_type, "
        "revit_element_id, type_name, predefined_type, tag, storey_id, storey_name, "
        "storey_elevation_m, space_id, space_number, space_name, length_m, area_m2, volume_m3, "
        "perimeter_m, height_m, width_m, materials."
    )
    assert division == "the database refused the query: division by zero (SQLSTATE 22012)"


async def test_run_sql_refuses_an_empty_query_and_bad_max_rows(server: MCPServer) -> None:
    async with connect(server) as client:
        empty = await call_error(client, "run_sql", sql="")
        too_many = await call_error(client, "run_sql", sql="SELECT 1", max_rows=501)

    assert "\nsql\n  String should have at least 1 character" in empty
    assert "\nmax_rows\n  Input should be less than or equal to 500" in too_many


# --------------------------------------------------------------------------- logging


async def test_each_call_logs_one_line_with_a_fingerprint_not_the_query(
    server: MCPServer, caplog: pytest.LogCaptureFixture
) -> None:
    sql = "SELECT 'secret-looking literal' AS x"
    caplog.set_level(logging.INFO, logger=server_app.__name__)

    async with connect(server) as client:
        await call(client, "run_sql", sql=sql)
        await call_error(client, "run_sql", sql="SELECT pg_sleep(1)")

    ok, refused = [r for r in caplog.records if r.name == server_app.__name__]
    assert (ok.getMessage(), ok.tool, ok.outcome, ok.rows, ok.sql_chars) == (  # type: ignore[attr-defined]
        "tool call", "run_sql", "ok", 1, len(sql),
    )  # fmt: skip
    assert re.fullmatch("[0-9a-f]{16}", ok.sql_sha256)  # type: ignore[attr-defined]
    assert refused.outcome == "refused"  # type: ignore[attr-defined]
    assert refused.error.startswith("query refused: function pg_sleep()")  # type: ignore[attr-defined]
    assert "secret-looking literal" not in caplog.text


async def test_the_query_text_is_logged_at_debug_only(
    server: MCPServer, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=server_app.__name__)

    async with connect(server) as client:
        await call(client, "run_sql", sql="SELECT 42 AS answer")

    [debug] = [
        r for r in caplog.records if r.levelno == logging.DEBUG and r.name == server_app.__name__
    ]
    assert debug.sql == "SELECT 42 AS answer"  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- the function allowlist

# The functions in pg_catalog that ifc_reader may execute and that sqlglot parses into a
# typed node, which the validator lets through. Reviewed: none has a side effect. A new
# sqlglot or PostgreSQL that changes the set fails this test until it is reviewed again.
# A block of names reads better in review than a 173-line literal list.
TYPED_FUNCTIONS = """
abs acos acosh any_value array_agg array_append array_cat array_length array_position
array_prepend array_remove array_to_string ascii asin asinh atan atan2 atanh avg bit_and
bit_length bit_or bit_xor bool_and bool_or btrim cbrt ceil ceiling char char_length
character_length chr concat concat_ws convert corr cos cosh cot count covar_pop covar_samp
cume_dist current_database current_schema current_schemas current_user date date_add
date_bin date_part date_trunc decode degrees dense_rank div encode exp extract factorial
first_value floor format gen_random_uuid generate_series get_bit host initcap json_agg
json_extract_path json_extract_path_text json_object json_object_agg json_strip_nulls
jsonb_contains jsonb_exists jsonb_object_agg justify_days justify_hours justify_interval lag
last_value lead left length ln log log10 lower lpad ltrim make_interval make_time
make_timestamp max md5 min mode normalize now nth_value ntile overlay percent_rank
percentile_cont percentile_disc pi position pow power radians random rank regexp_count
regexp_instr regexp_like regexp_replace regexp_substr regr_avgx regr_avgy regr_count
regr_intercept regr_r2 regr_slope regr_sxx regr_sxy regr_syy repeat replace reverse right
round row_number rpad rtrim session_user sha256 sha384 sha512 sign sin sinh split_part sqrt
starts_with stddev stddev_pop stddev_samp string_agg string_to_array strpos substr
substring sum tan tanh time timestamp to_char to_date to_hex to_number to_timestamp
translate trunc unnest upper var_pop var_samp variance version width_bucket
""".split()  # noqa: SIM905


def _executable_functions(reader: Connection) -> list[str]:
    return list(
        reader.execute(
            text(
                "SELECT DISTINCT p.proname FROM pg_catalog.pg_proc AS p "
                "WHERE p.pronamespace = 'pg_catalog'::regnamespace "
                "AND p.prokind IN ('f', 'a', 'w') AND has_function_privilege(p.oid, 'EXECUTE') "
                "ORDER BY 1"
            )
        ).scalars()
    )


def _typed_by_sqlglot(name: str) -> bool:
    """Whether sqlglot parses a call of ``name`` into a typed node (with any arity it takes)."""
    for arguments in ("()", "(1)", "(1, 2)", "(1, 2, 3)"):
        try:
            function = sqlglot.parse_one(f"SELECT {name}{arguments}", read="postgres").find(
                exp.Func
            )
        except sqlglot.errors.ParseError:
            continue
        return function is not None and not isinstance(function, exp.Anonymous)
    return False


def test_the_functions_sqlglot_types_are_the_reviewed_ones(reader: Connection) -> None:
    # The size of pg_catalog differs between builds of one major version (2,644 functions
    # on the postgres:16-alpine image, 2,643 on Ubuntu's PostgreSQL 16), so it is not
    # pinned. What the review protects is that no function sqlglot lets through is
    # unreviewed; a reviewed function this build lacks lets nothing through.
    executable = _executable_functions(reader)
    typed = [name for name in executable if _typed_by_sqlglot(name)]

    assert set(typed) <= set(TYPED_FUNCTIONS), sorted(set(typed) - set(TYPED_FUNCTIONS))
    # Nearly all of them must exist, or the check would pass on a catalogue it never saw.
    assert len(typed) >= len(TYPED_FUNCTIONS) - 5, sorted(set(TYPED_FUNCTIONS) - set(typed))


def test_the_reviewed_function_list_is_unchanged() -> None:
    # Adding a name is a review decision: change this count in the same commit.
    assert len(TYPED_FUNCTIONS) == len(set(TYPED_FUNCTIONS)) == 173
    assert sorted(TYPED_FUNCTIONS) == TYPED_FUNCTIONS


def test_every_allowed_untyped_function_exists_and_ifc_reader_may_run_it(
    reader: Connection,
) -> None:
    executable = set(_executable_functions(reader))

    assert ALLOWED_UNTYPED_FUNCTIONS.issubset(executable)
    assert not any(_typed_by_sqlglot(name) for name in ALLOWED_UNTYPED_FUNCTIONS)
