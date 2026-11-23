"""Finding the schema without the resource: describe_schema and run_sql's errors (A10).

In the audit's six Claude sessions, 20 of 72 tool calls failed, all on schema discovery
(docs/audit/evidence/llm_sessions.md): 14 guessed relation names, 4 queries of
information_schema and 2 guessed column names. The model never read ``ifc://schema``,
because in Claude Desktop the user attaches resources, not the model. The last test
replays those 20 calls and checks that each error now names what does exist.
"""

import pytest
from mcp.server.mcpserver import MCPServer
from mcp_client import call, call_error, connect
from sqlalchemy import Connection, Engine, text

from ifc_sql_mcp.server import catalogue
from ifc_sql_mcp.server.app import build_server

pytestmark = pytest.mark.anyio

PROPERTY = "property(object_id, pset_name, prop_name, value_text, value_num, value_bool, unit)"


@pytest.fixture
def server(mcp_engine: Engine) -> MCPServer:
    return build_server(mcp_engine)


def _relations(conn: Connection) -> list[tuple[str, str]]:
    """Every table and view of ifc as (name, kind), views first, as the catalogue says."""
    rows = conn.execute(
        text(
            "SELECT relname, CASE relkind WHEN 'v' THEN 'view' ELSE 'table' END "
            "FROM pg_class WHERE relnamespace = 'ifc'::regnamespace AND relkind IN ('r', 'v') "
            "ORDER BY relkind DESC, relname"
        )
    )
    return [(name, kind) for name, kind in rows]


# --------------------------------------------------------------------------- describe_schema


async def test_without_a_relation_it_lists_every_relation_in_brief(
    server: MCPServer, conn: Connection
) -> None:
    async with connect(server) as client:
        schema = await call(client, "describe_schema")

    assert [(r["name"], r["kind"]) for r in schema["relations"]] == _relations(conn)
    # Schema ifc_viewer, the viewer's scenes, is not described (ADR 14).
    assert not {"scene", "scene_chunk"} & {r["name"] for r in schema["relations"]}
    element = next(r for r in schema["relations"] if r["name"] == "v_element")
    assert element["description"].startswith("One flat row per physical element")
    assert {"ifc_class_base", "revit_type", "area_m2", "width_m"} <= set(element["column_names"])
    assert all(r["columns"] is None and r["keys"] is None for r in schema["relations"])
    assert "relation set to one name" in schema["note"]
    # Brief enough to read in one go: the names, not every column's comment.
    assert len(str(schema)) < 20_000


@pytest.mark.parametrize("asked", ["property", "PROPERTY", "ifc.property", '"property"'])
async def test_one_table_comes_with_types_meanings_and_keys(server: MCPServer, asked: str) -> None:
    async with connect(server) as client:
        schema = await call(client, "describe_schema", relation=asked)

    [table] = schema["relations"]
    assert (table["name"], table["kind"]) == ("property", "table")
    columns = {c["name"]: c for c in table["columns"]}
    assert list(columns) == table["column_names"]
    assert (columns["value_num"]["type"], columns["value_num"]["not_null"]) == (
        "double precision",
        False,
    )
    assert columns["prop_name"]["not_null"] is True
    assert all(c["description"] for c in table["columns"])
    assert any(key.startswith("FOREIGN KEY (object_id)") for key in table["keys"])
    assert schema["note"] == "run_sql reads this relation as property, without the ifc. prefix."


async def test_a_view_has_no_keys(server: MCPServer) -> None:
    async with connect(server) as client:
        schema = await call(client, "describe_schema", relation="v_element")

    [view] = schema["relations"]
    assert (view["kind"], view["keys"]) == ("view", None)
    assert "width_m" in [c["name"] for c in view["columns"]]


@pytest.mark.parametrize("asked", ["scene_chunk", "ifc_viewer.scene_chunk"])
async def test_the_viewers_scenes_are_not_described(server: MCPServer, asked: str) -> None:
    async with connect(server) as client:
        message = await call_error(client, "describe_schema", relation=asked)

    assert message.startswith(f"schema ifc has no table or view named {asked!r}.")


async def test_an_unknown_relation_gets_the_closest_and_all_the_names(server: MCPServer) -> None:
    async with connect(server) as client:
        message = await call_error(client, "describe_schema", relation="v_property")

    assert message.startswith(
        "schema ifc has no table or view named 'v_property'. Closest: property. The tables "
    )
    assert "The tables and views of schema ifc are: v_dq_finding, v_element," in message
    assert PROPERTY in message


# --------------------------------------------------------------------------- run_sql errors


async def test_an_unknown_relation_in_run_sql_lists_the_real_ones(server: MCPServer) -> None:
    async with connect(server) as client:
        message = await call_error(client, "run_sql", sql="SELECT * FROM v_property LIMIT 1")
        after = await call(client, "run_sql", sql="SELECT count(*) FROM v_element")

    assert message.startswith(
        'the database refused the query: relation "v_property" does not exist '
        "(SQLSTATE 42P01). Closest: property. The tables and views of schema "
        "ifc are: v_dq_finding,"
    )
    assert message.endswith(
        f"Call describe_schema for their columns; property values are in {PROPERTY}."
    )
    assert after["rows"] == [[26]]


async def test_a_misspelt_qualified_column_lists_its_own_relations_columns(
    server: MCPServer,
) -> None:
    sql = (
        "SELECT p.pset_name, count(*) FROM property AS p JOIN v_element AS e "
        "USING (object_id) WHERE e.ifc_class_norm = 'IfcWall' GROUP BY 1"
    )
    async with connect(server) as client:
        message = await call_error(client, "run_sql", sql=sql)

    assert message.startswith(
        "the database refused the query: column e.ifc_class_norm does not exist "
        "(SQLSTATE 42703). Closest: ifc_class, ifc_class_base. Columns of v_element: "
        "object_id, model_id,"
    )
    assert "property:" not in message


async def test_an_unqualified_column_lists_every_relation_the_query_reads(
    server: MCPServer,
) -> None:
    sql = "SELECT property_name FROM property JOIN ifc.v_element USING (object_id)"
    async with connect(server) as client:
        message = await call_error(client, "run_sql", sql=sql)

    assert "Closest: prop_name, pset_name" in message
    assert "Columns of property: object_id, pset_name, prop_name," in message
    assert "; v_element: object_id, model_id," in message


async def test_a_qualifier_naming_a_cte_lists_the_relations_inside_it(server: MCPServer) -> None:
    sql = "WITH w AS (SELECT material_name FROM v_takeoff) SELECT w.ifc_class FROM w"
    async with connect(server) as client:
        message = await call_error(client, "run_sql", sql=sql)

    # The CTE has no column ifc_class; the relation it reads is what to look at.
    assert "column w.ifc_class does not exist" in message
    assert "Columns of v_takeoff: model_id, discipline," in message


async def test_a_column_of_no_relation_points_at_describe_schema(server: MCPServer) -> None:
    async with connect(server) as client:
        message = await call_error(
            client, "run_sql", sql="WITH t AS (SELECT 1 AS a) SELECT b FROM t"
        )

    assert message == (
        'the database refused the query: column "b" does not exist (SQLSTATE 42703). '
        "describe_schema lists every table and view with its columns."
    )


def test_nothing_is_added_to_other_errors_or_unexpected_messages(conn: Connection) -> None:
    explain = catalogue.explain_unknown_name

    assert explain(conn, "SELECT 1 / 0", "22012", "division by zero") == ""
    # A message in an unexpected wording still gets what the query reads.
    assert explain(conn, "SELECT x FROM v_takeoff", "42703", "unexpected").startswith(
        "Columns of v_takeoff: model_id,"
    )
    # An unknown relation whose name the message does not give still gets the list.
    assert explain(conn, "SELECT 1", "42P01", "an unexpected wording").startswith(
        "The tables and views of schema ifc are:"
    )


def test_a_query_that_does_not_parse_reads_no_relation() -> None:
    assert catalogue._relations_read("SELECT FROM (", {"v_element"}) == []


# --------------------------------------------------------------------------- the sessions

# The 20 failed calls of the audit's sessions (llm_sessions.md, by line), in order. The
# record cuts a query at 273 characters; the four cut here end just after the name that
# failed, which PostgreSQL rejects before reading further.
SESSION_FAILURES = [
    # Relations that do not exist: 14 calls.
    (9, "SELECT pset_name, prop_name, count(*) FROM v_property p JOIN v_element e USING "
        "(object_id) WHERE e.model_id=6 AND e.ifc_class_norm='IfcWall' GROUP BY 1, 2", "property"),
    (15, "SELECT * FROM property_value LIMIT 1", "property"),
    (37, "SELECT * FROM v_property LIMIT 1", "property"),
    (38, "SELECT * FROM v_property_value LIMIT 1", "property"),
    (39, "SELECT * FROM property_value LIMIT 1", "property"),
    (74, "SELECT p.pset_name, p.prop_name, count(*) FROM v_property p JOIN v_element e ON "
         "e.object_id=p.object_id WHERE e.model_id=5 AND e.ifc_class_norm='IfcWall' "
         "GROUP BY 1, 2", "property"),
    (115, "SELECT * FROM v_property LIMIT 1", "property"),
    (116, "SELECT * FROM property_value LIMIT 1", "property"),
    (117, "SELECT * FROM v_element_property LIMIT 1", "v_element"),
    (118, "SELECT * FROM v_space LIMIT 1", "v_space_schedule"),
    (119, "SELECT * FROM v_property_value LIMIT 1", "property"),
    (121, "SELECT * FROM space LIMIT 1", "spatial_element"),
    (167, "SELECT pset_name, property_name, count(DISTINCT object_id) FROM v_property WHERE "
          "model_id=6 AND property_name IN ('FireRating','IsExternal','LoadBearing',"
          "'AcousticRating') GROUP BY 1,2 ORDER BY 1,2", "property"),
    (171, "SELECT * FROM v_property_value LIMIT 1", "property"),
    # information_schema, refused: 4 calls.
    (10, "SELECT table_name, string_agg(column_name, ',') FROM information_schema.columns "
         "WHERE table_schema='ifc' GROUP BY 1", "describe_schema"),
    (34, "SELECT table_name, column_name FROM information_schema.columns WHERE "
         "table_schema='ifc' AND table_name IN ('v_element','v_property') ORDER BY "
         "table_name, ordinal_position", "describe_schema"),
    (77, "SELECT table_name FROM information_schema.tables WHERE table_schema='ifc' "
         "ORDER BY 1", "describe_schema"),
    (114, "SELECT table_name FROM information_schema.views WHERE table_schema='ifc'",
          "describe_schema"),
    # Columns that do not exist: 2 calls.
    (79, "WITH w AS (SELECT object_id, revit_family, revit_type FROM v_element WHERE "
         "model_id=5 AND ifc_class_norm='IfcWall') SELECT count(*) FROM w", "ifc_class_base"),
    (166, "SELECT material_name, ifc_class, count(DISTINCT object_id) FROM v_takeoff WHERE "
          "model_id=6 GROUP BY 1, 2", "ifc_class_base"),
]  # fmt: skip


async def test_every_failed_call_of_the_audit_sessions_now_names_what_exists(
    server: MCPServer,
) -> None:
    """Before A10, each of these errors named only what was wrong, or pointed at a
    resource the model could not read. Each must now name the thing to use instead."""
    assert len(SESSION_FAILURES) == 20
    async with connect(server) as client:
        for line, sql, answer in SESSION_FAILURES:
            message = await call_error(client, "run_sql", sql=sql)
            assert answer in message, (line, message)
            assert "ifc://schema" not in message, line
            if answer != "describe_schema":
                assert "describe_schema" in message or "Columns of" in message, line
