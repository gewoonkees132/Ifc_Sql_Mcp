"""Check SQL an LLM wrote before ``run_sql`` executes it: one query that only reads ``ifc``.

This is not the security boundary; the database is (ADR 6). ``ifc_reader`` may only
SELECT in schema ``ifc``, every transaction is read-only, and the executor declares a
cursor for the query, which PostgreSQL accepts only for a single SELECT or VALUES. The
check here adds a layer in front of that and, above all, refusals an LLM can act on:
each rule has its own message, and a query that does not parse is refused with
sqlglot's reason.

The query is executed exactly as written, never as sqlglot would regenerate it. Where
sqlglot and PostgreSQL read a text differently, the worst outcome is therefore a query
that passes here and is refused by the database, not a different query being run.

The rules, in the order they are checked:

1. At most ``MAX_QUERY_CHARS`` characters, and no NUL character.
2. It parses with sqlglot's ``postgres`` dialect.
3. Exactly one statement.
4. That statement is a query: SELECT (with or without WITH, or written as TABLE), a set
   operation (UNION, INTERSECT, EXCEPT) or VALUES. Everything else is refused, including
   SHOW and EXPLAIN: they answer no question about a building, sqlglot parses them as an
   opaque command whose contents cannot be checked, and EXPLAIN ANALYZE executes the
   statement it explains.
5. Nothing in it writes or locks: no INSERT, UPDATE, DELETE, MERGE or COPY anywhere
   (PostgreSQL allows the first four inside WITH), no SELECT … INTO, no FOR UPDATE or
   FOR SHARE.
6. It reads only schema ``ifc``. A schema-qualified name must be in ``ifc``, and an
   unqualified ``pg_*`` name, which PostgreSQL would resolve in ``pg_catalog`` before the
   search path, must be a CTE. Every catalogue relation starts with ``pg_``; the
   ``ifc://schema`` resource describes the data instead.
7. Functions come from an allowlist. sqlglot parses the standard SQL functions (count,
   sum, round, lower, date_trunc, string_agg, …) into typed nodes; they are allowed. Of
   the 2,644 functions in ``pg_catalog`` that ``ifc_reader`` may execute, sqlglot types
   173, all without side effects (``tests/integration/test_run_sql.py`` pins that set).
   Every other function, which sqlglot leaves as an anonymous call, must be in
   ``ALLOWED_UNTYPED_FUNCTIONS``. That keeps out what the role can execute but must not:
   ``set_config``, ``pg_sleep``, advisory locks (a session lock outlives the
   transaction), ``pg_terminate_backend`` (allowed on other ``ifc_reader`` sessions),
   ``query_to_xml`` (runs SQL text this check never sees) and anything an extension adds.
   A denylist could never be complete. Schema-qualified calls are refused too.
"""

import logging

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

from ifc_sql_mcp.db.schema import SCHEMA

# sqlglot logs a warning that quotes the start of a statement it can only parse as an
# opaque command (SHOW, DO, ...). Such a statement is refused anyway, and the server's log
# is not meant to carry query text (ADR 6), so only its errors are let through.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

DIALECT = "postgres"

MAX_QUERY_CHARS = 20_000
"""Longest query accepted. A question about a model fits in far less."""

ALLOWED_UNTYPED_FUNCTIONS = frozenset(
    {
        # JSON
        "json_array_elements", "json_array_length", "json_build_array", "json_build_object",
        "json_each", "json_typeof", "jsonb_agg", "jsonb_array_elements",
        "jsonb_array_elements_text", "jsonb_array_length", "jsonb_build_array",
        "jsonb_build_object", "jsonb_each", "jsonb_each_text", "jsonb_extract_path",
        "jsonb_extract_path_text", "jsonb_object_keys", "jsonb_path_query", "jsonb_pretty",
        "jsonb_strip_nulls", "jsonb_typeof", "to_json", "to_jsonb",
        # arrays
        "array_dims", "array_fill", "array_lower", "array_positions", "array_replace",
        "array_upper", "cardinality", "generate_subscripts",
        # text and regular expressions
        "octet_length", "quote_ident", "quote_literal", "quote_nullable", "regexp_match",
        "regexp_matches", "regexp_split_to_array", "regexp_split_to_table",
        "string_to_table", "unistr",
        # numbers, dates, aggregates
        "age", "clock_timestamp", "every", "gcd", "isfinite", "lcm", "make_date",
        "num_nonnulls", "num_nulls", "scale", "statement_timestamp", "timeofday",
        "transaction_timestamp", "trim_scale",
    }
)  # fmt: skip
"""PostgreSQL functions sqlglot does not type, allowed by name: JSON, arrays, regular
expressions, date and number helpers. None has a side effect."""

QUERY_KINDS = "SELECT, WITH … SELECT, TABLE, UNION, INTERSECT, EXCEPT or VALUES"


class QueryRefusedError(ValueError):
    """The query was refused before it reached the database; the message says why."""


def check_query(sql: str) -> None:
    """Refuse ``sql`` unless it is one query that only reads schema ``ifc``.

    Raises:
        QueryRefusedError: The first rule (in the module docstring's order) it breaks.
    """
    if len(sql) > MAX_QUERY_CHARS:
        raise QueryRefusedError(
            f"the query is {len(sql):,} characters long; the limit is {MAX_QUERY_CHARS:,}"
        )
    if "\x00" in sql:
        raise QueryRefusedError("the query contains a NUL character")
    statement = _single_statement(sql)
    if not isinstance(statement, exp.Query | exp.Values):
        raise QueryRefusedError(
            f"only a query that reads data is allowed ({QUERY_KINDS}); "
            f"this is {_statement_kind(sql, statement)}"
        )
    _check_nothing_writes(statement)
    _check_relations(statement)
    _check_functions(statement)


def _single_statement(sql: str) -> exp.Expr:
    try:
        parsed = sqlglot.parse(sql, read=DIALECT)
    except ParseError as exc:
        error = exc.errors[0]
        raise QueryRefusedError(
            f"the query does not parse: {error['description']} "
            f"(line {error['line']}, column {error['col']})"
        ) from exc
    except TokenError as exc:
        raise QueryRefusedError(f"the query does not parse: {exc}") from exc
    # An empty statement (a stray semicolon) parses as None, and a comment after the last
    # semicolon as a Semicolon node that carries it. Neither is a statement.
    statements = [s for s in parsed if s is not None and not isinstance(s, exp.Semicolon)]
    if not statements:
        raise QueryRefusedError("the query is empty")
    if len(statements) > 1:
        raise QueryRefusedError(f"send one statement at a time; this text holds {len(statements)}")
    return statements[0]


def _statement_kind(sql: str, statement: exp.Expr) -> str:
    """Name a statement that is not a query, for the refusal: an INSERT, a SHOW, ...

    A WITH in front of a data-modifying statement is named by that statement. Otherwise
    the first word is the name: sqlglot parses some statements (START TRANSACTION,
    LISTEN) as stray expressions whose node type would mean nothing to the reader.
    """
    if isinstance(statement, exp.DML | exp.DDL):
        keyword = statement.key.upper()
    else:
        keyword = sqlglot.tokenize(sql, read=DIALECT)[0].text.upper()
    article = "an" if keyword[0] in "AEIOU" else "a"
    return f"{article} {keyword} statement"


def _check_nothing_writes(statement: exp.Expr) -> None:
    for node in statement.walk():
        if isinstance(node, exp.DML):
            raise QueryRefusedError(
                f"the query contains {node.key.upper()}: statements that change data are "
                "not allowed, also not inside WITH"
            )
        if isinstance(node, exp.Select) and node.args.get("into"):
            raise QueryRefusedError(
                "SELECT … INTO creates a table and is not allowed; leave out the INTO clause"
            )
        if isinstance(node, exp.Select) and node.args.get("locks"):
            raise QueryRefusedError(
                "locking clauses (FOR UPDATE, FOR SHARE, …) are not allowed in a read-only query"
            )


def _check_relations(statement: exp.Expr) -> None:
    cte_names = {cte.alias_or_name for cte in statement.find_all(exp.CTE)}
    for table in statement.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            continue  # a function in FROM, such as generate_series(): see _check_functions
        if table.catalog:
            raise QueryRefusedError(
                f"name tables without a database: {table.catalog}.{table.db}.{table.name}"
            )
        if table.db and table.db.lower() != SCHEMA:
            raise QueryRefusedError(
                f"only schema {SCHEMA} can be read, not {table.db}.{table.name}; "
                "the ifc://schema resource lists its tables and views"
            )
        if not table.db and table.name.lower().startswith("pg_") and table.name not in cte_names:
            raise QueryRefusedError(
                f"{table.name} is a PostgreSQL system catalogue; only schema {SCHEMA} can be "
                "read, and the ifc://schema resource lists its tables and views"
            )


def _check_functions(statement: exp.Expr) -> None:
    for function in statement.find_all(exp.Func):
        if isinstance(function.parent, exp.Dot) and function.parent.expression is function:
            raise QueryRefusedError(
                f"call functions without a schema: {function.parent.this}.{function.name}() "
                "is not allowed"
            )
        if (
            isinstance(function, exp.Anonymous)
            and function.name.lower() not in ALLOWED_UNTYPED_FUNCTIONS
        ):
            raise QueryRefusedError(
                f"function {function.name}() is not allowed. Allowed are the standard SQL "
                "functions (count, sum, avg, round, lower, coalesce, date_trunc, string_agg, "
                "row_number, …) and these PostgreSQL functions: "
                f"{', '.join(sorted(ALLOWED_UNTYPED_FUNCTIONS))}"
            )
