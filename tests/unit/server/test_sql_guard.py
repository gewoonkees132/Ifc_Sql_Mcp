"""The ``run_sql`` validator: every kind of query it accepts, every kind it refuses, and why.

The database refuses all of these on its own as well; tests/integration/test_run_sql.py
sends refused queries past this check to prove it. Here only the check itself is tested,
without a database.
"""

import pytest

from ifc_sql_mcp.server.sql_guard import (
    ALLOWED_UNTYPED_FUNCTIONS,
    MAX_QUERY_CHARS,
    QueryRefusedError,
    check_query,
)

NOT_A_QUERY = (
    "only a query that reads data is allowed "
    "(SELECT, WITH … SELECT, TABLE, UNION, INTERSECT, EXCEPT or VALUES); this is "
)


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("SELECT 1", id="select"),
        pytest.param("SELECT 1;", id="trailing semicolon"),
        pytest.param("SELECT 1; -- the last statement", id="comment after the semicolon"),
        pytest.param("-- leading comment\nSELECT 1 /* inline */", id="comments"),
        pytest.param("SELECT 1 /* /* nested */ ; SELECT 2 */", id="nested comment"),
        pytest.param("WITH a AS (SELECT 1 AS x) SELECT x FROM a", id="with select"),
        pytest.param("TABLE ifc.model", id="table"),
        pytest.param("SELECT 1 UNION SELECT 2", id="union"),
        pytest.param("SELECT 1 INTERSECT SELECT 1", id="intersect"),
        pytest.param("SELECT 1 EXCEPT SELECT 2", id="except"),
        pytest.param("VALUES (1), (2)", id="values"),
        pytest.param("(SELECT 1)", id="parenthesised"),
        pytest.param("SELECT * FROM v_element", id="unqualified view"),
        pytest.param("SELECT * FROM IFC.v_element", id="ifc schema in capitals"),
        pytest.param("WITH pg_x AS (SELECT 1 AS a) SELECT a FROM pg_x", id="cte named pg_"),
        pytest.param("SELECT ':x' AS a, '%s' AS b, '%' AS c", id="colon and percent literals"),
        pytest.param(
            "SELECT storey_name, count(*), sum(area_m2), round(avg(length_m)::numeric, 2) "
            "FROM v_element GROUP BY storey_name",
            id="standard functions",
        ),
        pytest.param(
            "SELECT string_agg(name, ', ' ORDER BY name), date_trunc('day', now()), "
            "extract(year FROM now()) FROM ifc.object",
            id="functions sqlglot types",
        ),
        pytest.param(
            "SELECT row_number() OVER (ORDER BY object_id), lag(name) OVER w "
            "FROM ifc.object WINDOW w AS (ORDER BY object_id)",
            id="window functions",
        ),
        pytest.param(
            "SELECT jsonb_build_object('a', 1), num_nonnulls(1, NULL), age(now())",
            id="allowed untyped functions",
        ),
        pytest.param("SELECT n FROM generate_series(1, 3) AS g(n)", id="function in from"),
        pytest.param(
            "SELECT j.key FROM ifc.v_element AS e "
            "JOIN LATERAL jsonb_each(jsonb_build_object('k', e.name)) AS j ON true",
            id="lateral allowed function",
        ),
        pytest.param("SELECT EXISTS (SELECT 1 FROM ifc.model)", id="exists"),
        pytest.param("SELECT CASE WHEN 1 = 1 THEN 'a' END", id="case"),
    ],
)
def test_accepts_a_query_that_only_reads(sql: str) -> None:
    check_query(sql)


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        pytest.param("", "the query is empty", id="empty"),
        pytest.param(";", "the query is empty", id="only a semicolon"),
        pytest.param("-- nothing", "the query is empty", id="only a comment"),
        pytest.param(
            "SELEC 1",
            "the query does not parse: Invalid expression / Unexpected token (line 1, column 7)",
            id="does not parse",
        ),
        pytest.param(
            "SELECT 'abc", "the query does not parse: Error tokenizing", id="unterminated string"
        ),
        pytest.param(
            "SELECT 1; SELECT 2", "send one statement at a time; this text holds 2", id="two"
        ),
        pytest.param(
            # With standard_conforming_strings (the default) a backslash does not escape
            # the quote: PostgreSQL also sees two statements here.
            r"SELECT '\'; SELECT 2; --'",
            "send one statement at a time; this text holds 2",
            id="backslash before a quote",
        ),
        pytest.param("SELECT 1\x00", "the query contains a NUL character", id="nul"),
    ],
)
def test_refuses_what_is_not_one_statement(sql: str, reason: str) -> None:
    with pytest.raises(QueryRefusedError) as excinfo:
        check_query(sql)

    assert str(excinfo.value).startswith(reason)


def test_refuses_a_query_over_the_length_limit() -> None:
    sql = "SELECT " + "1 + " * (MAX_QUERY_CHARS // 4) + "1"

    with pytest.raises(QueryRefusedError) as excinfo:
        check_query(sql)

    assert str(excinfo.value) == (
        f"the query is {len(sql):,} characters long; the limit is {MAX_QUERY_CHARS:,}"
    )


@pytest.mark.parametrize(
    ("sql", "kind"),
    [
        ("INSERT INTO ifc.dq_rule VALUES ('r', 'd', 'info', 'validity')", "an INSERT"),
        ("UPDATE ifc.model SET discipline = 'STR'", "an UPDATE"),
        ("DELETE FROM ifc.property", "a DELETE"),
        ("MERGE INTO ifc.model USING ifc.model AS s ON true WHEN MATCHED THEN DELETE", "a MERGE"),
        ("WITH x AS (SELECT 1) INSERT INTO ifc.scratch SELECT * FROM x", "an INSERT"),
        ("COPY ifc.model TO STDOUT", "a COPY"),
        ("COPY (SELECT 1) TO STDOUT", "a COPY"),
        ("CREATE TABLE ifc.scratch (a int)", "a CREATE"),
        ("CREATE TEMP TABLE scratch AS SELECT 1", "a CREATE"),
        ("DROP TABLE ifc.model", "a DROP"),
        ("ALTER TABLE ifc.model ADD COLUMN c int", "an ALTER"),
        ("TRUNCATE ifc.quantity", "a TRUNCATE"),
        ("GRANT SELECT ON ifc.model TO PUBLIC", "a GRANT"),
        ("SET statement_timeout = 0", "a SET"),
        ("SET LOCAL statement_timeout = 0", "a SET"),
        ("RESET ALL", "a RESET"),
        ("SHOW search_path", "a SHOW"),
        ("EXPLAIN SELECT 1", "an EXPLAIN"),
        ("EXPLAIN ANALYZE DELETE FROM ifc.model", "an EXPLAIN"),
        ("DO $$ BEGIN PERFORM 1; END $$", "a DO"),
        ("CALL ifc.procedure()", "a CALL"),
        ("BEGIN", "a BEGIN"),
        ("START TRANSACTION", "a START"),
        ("COMMIT", "a COMMIT"),
        ("ROLLBACK", "a ROLLBACK"),
        ("SAVEPOINT s", "a SAVEPOINT"),
        ("PREPARE p AS SELECT 1", "a PREPARE"),
        ("EXECUTE p", "an EXECUTE"),
        ("DECLARE c CURSOR FOR SELECT 1", "a DECLARE"),
        ("FETCH 1 FROM c", "a FETCH"),
        ("LOCK TABLE ifc.model", "a LOCK"),
        ("LISTEN channel", "a LISTEN"),
        ("NOTIFY channel", "a NOTIFY"),
        ("VACUUM", "a VACUUM"),
        ("ANALYZE ifc.model", "an ANALYZE"),
    ],
)
def test_refuses_every_statement_that_is_not_a_query(sql: str, kind: str) -> None:
    with pytest.raises(QueryRefusedError) as excinfo:
        check_query(sql)

    assert str(excinfo.value) == f"{NOT_A_QUERY}{kind} statement"


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        (
            "WITH d AS (DELETE FROM ifc.property RETURNING *) SELECT count(*) FROM d",
            "the query contains DELETE: statements that change data are not allowed, also "
            "not inside WITH",
        ),
        (
            "WITH u AS (UPDATE ifc.model SET discipline = 'STR' RETURNING *) SELECT * FROM u",
            "the query contains UPDATE",
        ),
        (
            "WITH i AS (INSERT INTO ifc.scratch VALUES (1) RETURNING *) SELECT * FROM i",
            "the query contains INSERT",
        ),
        (
            "SELECT * INTO scratch FROM ifc.model",
            "SELECT … INTO creates a table and is not allowed; leave out the INTO clause",
        ),
        (
            "SELECT * FROM ifc.model FOR UPDATE",
            "locking clauses (FOR UPDATE, FOR SHARE, …) are not allowed in a read-only query",
        ),
        ("SELECT * FROM ifc.model FOR NO KEY UPDATE", "locking clauses"),
        ("SELECT * FROM ifc.model FOR SHARE", "locking clauses"),
        ("SELECT * FROM ifc.model FOR KEY SHARE", "locking clauses"),
        ("SELECT * FROM (SELECT * FROM ifc.model FOR UPDATE) AS s", "locking clauses"),
    ],
)
def test_refuses_a_query_that_writes_or_locks_anywhere_inside(sql: str, reason: str) -> None:
    with pytest.raises(QueryRefusedError) as excinfo:
        check_query(sql)

    assert str(excinfo.value).startswith(reason)


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        (
            "SELECT * FROM pg_catalog.pg_class",
            "only schema ifc can be read, not pg_catalog.pg_class; the ifc://schema resource "
            "lists its tables and views",
        ),
        ("SELECT * FROM information_schema.columns", "only schema ifc can be read"),
        ("SELECT * FROM public.alembic_version", "only schema ifc can be read"),
        (
            "SELECT query FROM pg_stat_activity",
            "pg_stat_activity is a PostgreSQL system catalogue; only schema ifc can be read",
        ),
        ("SELECT * FROM PG_ROLES", "PG_ROLES is a PostgreSQL system catalogue"),
        ("SELECT * FROM ifc_db.ifc.model", "name tables without a database: ifc_db.ifc.model"),
    ],
)
def test_refuses_relations_outside_the_ifc_schema(sql: str, reason: str) -> None:
    with pytest.raises(QueryRefusedError) as excinfo:
        check_query(sql)

    assert str(excinfo.value).startswith(reason)


@pytest.mark.parametrize(
    ("sql", "function"),
    [
        ("SELECT pg_sleep(1)", "pg_sleep"),
        ("SELECT set_config('statement_timeout', '0', false)", "set_config"),
        ("SELECT current_setting('search_path')", "current_setting"),
        ("SELECT pg_advisory_lock(1)", "pg_advisory_lock"),
        ("SELECT pg_terminate_backend(1)", "pg_terminate_backend"),
        ("SELECT query_to_xml('SELECT 1', true, true, '')", "query_to_xml"),
        ("SELECT lo_import('/etc/passwd')", "lo_import"),
        ("SELECT pg_read_file('postgresql.conf')", "pg_read_file"),
        ("SELECT pg_notify('channel', 'x')", "pg_notify"),
        ("SELECT txid_current()", "txid_current"),
        ("SELECT dblink('host=example.org', 'SELECT 1')", "dblink"),
        ("SELECT * FROM pg_ls_dir('.')", "pg_ls_dir"),
        ("WITH a AS (SELECT pg_sleep(1)) SELECT * FROM a", "pg_sleep"),
        ("SELECT (SELECT pg_sleep(1))", "pg_sleep"),
        ('SELECT "pg_sleep"(1)', "pg_sleep"),
        ("SELECT PG_SLEEP(1)", "PG_SLEEP"),
    ],
)
def test_refuses_functions_outside_the_allowlist(sql: str, function: str) -> None:
    with pytest.raises(QueryRefusedError) as excinfo:
        check_query(sql)

    message = str(excinfo.value)
    assert message.startswith(
        f"function {function}() is not allowed. Allowed are the standard SQL functions"
    )
    assert message.endswith(", ".join(sorted(ALLOWED_UNTYPED_FUNCTIONS)))


@pytest.mark.parametrize(
    ("sql", "call"),
    [
        ("SELECT pg_catalog.pg_sleep(1)", "pg_catalog.pg_sleep()"),
        ("SELECT pg_catalog.lower('A')", "pg_catalog.lower()"),
        ("SELECT ifc.f(1)", "ifc.f()"),
    ],
)
def test_refuses_schema_qualified_function_calls(sql: str, call: str) -> None:
    with pytest.raises(QueryRefusedError) as excinfo:
        check_query(sql)

    assert str(excinfo.value) == f"call functions without a schema: {call} is not allowed"
