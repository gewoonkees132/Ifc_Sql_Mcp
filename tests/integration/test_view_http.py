"""``ifc-sql view``'s data routes against a real database, through Starlette's TestClient.

The database is ``view_settings`` (``conftest.py``): models 1 and 2 of project ``G-1`` with
a scene each, model 3 of project ``0042`` without one. Every request reads as
``ifc_reader``, through the engine ``ifc-sql view`` itself makes.
"""

from collections.abc import Iterator
from urllib.parse import quote

import pytest
from geometry_model import WALL
from glb_file import read_glb
from sqlalchemy import Engine, text
from starlette.testclient import TestClient

from ifc_sql_mcp.viewer.http import GLB_MEDIA_TYPE, create_app
from ifc_sql_mcp.viewer.scene import FORMAT_VERSION
from ifc_sql_mcp.viewer.security import new_token

PORT = 8765
TOKEN = new_token()
HINT = "run ifc-sql ingest revit_quirks.ifc --discipline ELE --viewer"


def _client(engine: Engine, **options: object) -> TestClient:
    return TestClient(
        create_app(engine, TOKEN, port=PORT, **options),  # type: ignore[arg-type]
        base_url=f"http://127.0.0.1:{PORT}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


@pytest.fixture
def client(view_engine: Engine) -> Iterator[TestClient]:
    with _client(view_engine) as client:
        yield client


@pytest.fixture(scope="module")
def chunks(view_engine: Engine) -> dict[int, list[dict[str, object]]]:
    """Every chunk of the database, by model, read straight from its table."""
    with view_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT model_id, chunk_no, kind, element_count, glb, glb_sha256 "
                "FROM ifc_viewer.scene_chunk ORDER BY model_id, chunk_no"
            )
        ).mappings()
        found: dict[int, list[dict[str, object]]] = {}
        for row in rows:
            found.setdefault(row["model_id"], []).append(dict(row))
    return found


# --------------------------------------------------------------------------- /api/models


def test_models_lists_every_model_with_its_scene_or_the_command_that_builds_it(
    client: TestClient,
) -> None:
    response = client.get("/api/models")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert [(m["model_id"], m["discipline"], m["project_group"]) for m in body["models"]] == [
        (1, "ARC", "G-1"),
        (2, "MEP", "G-1"),
        (3, "ELE", "0042"),
    ]
    first, second, third = body["models"]
    assert first["elements"] == 11  # list_models' own fields are all there
    assert first["scene"] == {
        "format_version": FORMAT_VERSION,
        "built_at": first["scene"]["built_at"],
        "element_count": 9,
        "without_shape_count": 1,
        "opening_count": 1,
        "chunk_count": 3,
        "byte_count": first["scene"]["byte_count"],
        "summary": "11 elements: 9 drawn, 1 without shape, 1 openings not drawn",
    }
    assert first["scene_hint"] is None
    assert second["scene"]["chunk_count"] == 3
    assert third["scene"] is None
    assert third["scene_hint"] == HINT
    assert body["truncated"] is False


def test_models_groups_the_projects_and_shows_the_newest_with_a_scene_first(
    client: TestClient,
) -> None:
    body = client.get("/api/models").json()

    g1 = {"label": "G-1", "project_group": "G-1", "model_ids": [1, 2]}
    assert body["projects"] == [
        g1,
        {"label": "0042", "project_group": "0042", "model_ids": [3]},
    ]
    assert body["shown"] == g1


@pytest.mark.parametrize(
    ("options", "shown"),
    [
        ({"project": "0042"}, {"label": "0042", "project_group": "0042", "model_ids": [3]}),
        (
            {"model_ids": [2, 1]},
            {"label": "models 2, 1", "project_group": None, "model_ids": [2, 1]},
        ),
    ],
)
def test_models_shows_first_what_view_was_asked_for(
    view_engine: Engine, options: dict[str, object], shown: dict[str, object]
) -> None:
    with _client(view_engine, **options) as client:
        assert client.get("/api/models").json()["shown"] == shown


# --------------------------------------------------------------------------- the manifest


def test_the_manifest_lists_the_origin_the_counts_and_every_chunk(
    client: TestClient, chunks: dict[int, list[dict[str, object]]]
) -> None:
    response = client.get("/api/models/1/scene")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    manifest = response.json()
    assert manifest["model_id"] == 1
    assert manifest["discipline"] == "ARC"
    assert manifest["format_version"] == FORMAT_VERSION
    assert len(manifest["origin_m"]) == 3
    assert all(value == round(value) for value in manifest["origin_m"])  # whole metres
    assert (manifest["element_count"], manifest["without_shape_count"]) == (9, 1)
    assert manifest["opening_count"] == 1
    assert manifest["summary"] == "11 elements: 9 drawn, 1 without shape, 1 openings not drawn"
    assert manifest["chunks"] == [
        {
            "chunk_no": chunk["chunk_no"],
            "kind": chunk["kind"],
            "storey_id": manifest["chunks"][index]["storey_id"],
            "storey_name": "Level 1",
            "storey_elevation_m": None,  # the fixture's storey has no elevation
            "element_count": chunk["element_count"],
            "byte_count": len(chunk["glb"]),  # type: ignore[arg-type]
            "sha256": chunk["glb_sha256"],
        }
        for index, chunk in enumerate(chunks[1])
    ]
    assert [chunk["kind"] for chunk in manifest["chunks"]] == [
        "elements",
        "spaces",
        "space_copies",
    ]
    assert manifest["byte_count"] == sum(chunk["byte_count"] for chunk in manifest["chunks"])


def test_two_models_of_one_file_share_one_origin(client: TestClient) -> None:
    arc = client.get("/api/models/1/scene").json()
    mep = client.get("/api/models/2/scene").json()

    assert arc["origin_m"] == mep["origin_m"]


# --------------------------------------------------------------------------- a chunk


def test_a_chunk_is_the_databases_bytes_with_its_hash_as_etag(
    client: TestClient, chunks: dict[int, list[dict[str, object]]]
) -> None:
    for chunk in chunks[1]:
        response = client.get(f"/api/models/1/scene/{chunk['chunk_no']}.glb")

        assert response.status_code == 200
        assert response.headers["content-type"] == GLB_MEDIA_TYPE
        assert response.content == chunk["glb"]
        assert response.headers["etag"] == f'"{chunk["glb_sha256"]}"'
        assert response.headers["cache-control"] == "private, no-cache"
        read_glb(response.content)


def test_a_reload_revalidates_a_chunk_and_gets_304(client: TestClient) -> None:
    etag = client.get("/api/models/2/scene/0.glb").headers["etag"]

    for header in (etag, f"W/{etag}", f'"0000", {etag}'):
        response = client.get("/api/models/2/scene/0.glb", headers={"If-None-Match": header})
        assert response.status_code == 304, header
        assert response.content == b""
        assert response.headers["etag"] == etag
        assert response.headers["cache-control"] == "private, no-cache"
    stale = client.get("/api/models/2/scene/0.glb", headers={"If-None-Match": '"0000"'})
    assert stale.status_code == 200
    assert stale.content[:4] == b"glTF"


def test_head_on_a_chunk_answers_its_headers(client: TestClient) -> None:
    response = client.head("/api/models/1/scene/0.glb")

    assert response.status_code == 200
    assert response.headers["content-type"] == GLB_MEDIA_TYPE
    assert response.headers["etag"].startswith('"')


# --------------------------------------------------------------------------- what is not there


@pytest.mark.parametrize(
    "path",
    [
        "/api/models/99/scene",
        "/api/models/99/scene/0.glb",
        "/api/models/99/objects/0123456789abcdefghijkl",
        "/api/models/0/scene",
        "/api/models/99999999999999999999/scene",
        "/api/models/99999999999999999999/scene/99999999999999999999.glb",
    ],
)
def test_an_unknown_model_gets_404_saying_what_to_do(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 404
    model_id = path.split("/")[3]
    assert response.json() == {
        "error": f"no model with id {model_id}: reload the page for those loaded"
    }
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/api/models/3/scene", "/api/models/3/scene/0.glb"])
def test_a_model_without_a_scene_gets_404_with_the_command_that_builds_it(
    client: TestClient, path: str
) -> None:
    response = client.get(path)

    assert response.status_code == 404
    assert response.json() == {"error": f"model 3 has no scene: {HINT}"}


def test_a_chunk_past_the_last_gets_404(client: TestClient) -> None:
    response = client.get("/api/models/1/scene/3.glb")

    assert response.status_code == 404
    assert response.json() == {
        "error": "model 1's scene has no chunk 3: it has 3 chunks, numbered from 0"
    }


@pytest.mark.parametrize(
    "path",
    ["/api/models/one/scene", "/api/models/1/scene/-1.glb", "/api/models/1/scene/0.gltf"],
)
def test_ids_that_are_not_numbers_match_no_route(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 404


# --------------------------------------------------------------------------- an element


def _global_id(engine: Engine, model_id: int, name: str) -> str:
    with engine.connect() as connection:
        found: str = connection.execute(
            text("SELECT global_id FROM ifc.object WHERE model_id = :m AND name = :n"),
            {"m": model_id, "n": name},
        ).scalar_one()
    return found


def test_an_element_is_get_element_with_its_usual_fields(
    client: TestClient, view_engine: Engine
) -> None:
    global_id = _global_id(view_engine, 1, WALL)

    response = client.get(f"/api/models/1/objects/{quote(global_id, safe='')}")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    detail = response.json()
    assert (detail["model_id"], detail["global_id"], detail["name"]) == (1, global_id, WALL)
    assert detail["ifc_class"] == "IfcWall"
    assert detail["storey_name"] == "Level 1"
    assert {"quantities", "property_sets", "materials", "data_quality"} <= set(detail)


def test_an_element_is_looked_up_in_its_own_model(client: TestClient, view_engine: Engine) -> None:
    # Models 1 and 2 hold the same file, so the same GlobalIds: each answers for its own.
    global_id = _global_id(view_engine, 1, WALL)

    detail = client.get(f"/api/models/2/objects/{quote(global_id, safe='')}").json()

    assert (detail["model_id"], detail["discipline"]) == (2, "MEP")


def test_an_unknown_global_id_gets_404(client: TestClient) -> None:
    response = client.get("/api/models/1/objects/0123456789abcdefghijkl")

    assert response.status_code == 404
    assert response.json() == {
        "error": "model 1 has no object with GlobalId '0123456789abcdefghijkl'"
    }


@pytest.mark.parametrize(
    "global_id",
    ["short", "4123456789abcdefghijkl", "0123456789abcdefghijk!", "0123456789abcdefghijklm"],
)
def test_a_global_id_that_is_not_one_gets_404_before_the_database(
    client: TestClient, global_id: str
) -> None:
    response = client.get(f"/api/models/1/objects/{quote(global_id, safe='')}")

    assert response.status_code == 404
    assert response.json() == {
        "error": "not an IFC GlobalId: 22 characters of 0-9, A-Z, a-z, _ and $"
    }
