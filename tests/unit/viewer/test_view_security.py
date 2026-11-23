"""What ``ifc-sql view``'s port holds, before any data: Host, methods, token, headers, files.

No database is needed: the engine points at port 1 on loopback, which refuses at once, so
a request that passes the checks and reaches the database gets 503. The data routes on a
real database are in ``tests/integration/test_view_http.py``.
"""

import base64
import hashlib
import json
import logging
import re
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
from starlette.testclient import TestClient

from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.db.engine import make_reader_engine
from ifc_sql_mcp.viewer import http
from ifc_sql_mcp.viewer.http import create_app
from ifc_sql_mcp.viewer.security import Security, content_security_policy, new_token

PORT = 8765
TOKEN = new_token()
BASE = f"http://127.0.0.1:{PORT}"
SECRET = "s3cret-reader-password"
API = [
    "/api/models",
    "/api/models/1/scene",
    "/api/models/1/scene/0.glb",
    "/api/models/1/objects/0123456789abcdefghijkl",
]
HEADERS = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
}


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    settings = Settings.from_env(
        {
            "IFC_SQL_DATABASE_URL": "postgresql+psycopg://ifc:owner@127.0.0.1:1/ifc",
            "IFC_SQL_READER_PASSWORD": SECRET,
        }
    )
    engine = make_reader_engine(settings, application="viewer")
    yield engine
    engine.dispose()


@pytest.fixture
def app(engine: Engine) -> Security:
    return create_app(engine, TOKEN, port=PORT)


@pytest.fixture
def client(app: Security) -> Iterator[TestClient]:
    with TestClient(app, base_url=BASE) as client:
        yield client


def _authorised(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _import_map(page: str) -> str:
    match = re.search(r'<script type="importmap">(.*?)</script>', page, re.DOTALL)
    assert match is not None
    return match.group(1)


def _policy_hash(policy: str) -> str:
    match = re.search(r"'sha256-([A-Za-z0-9+/=]+)'", policy)
    assert match is not None
    return match.group(1)


# --------------------------------------------------------------------------- the token


def test_a_token_is_new_on_every_start_and_url_safe() -> None:
    tokens = {new_token() for _ in range(20)}

    assert len(tokens) == 20
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{43}", token) for token in tokens)


# --------------------------------------------------------------------------- the policy


def test_the_policy_allows_the_import_map_by_its_hash_and_nothing_inline_else() -> None:
    page = '<head><script type="importmap">{"imports": {}}\n</script></head>'

    policy = content_security_policy(page)

    digest = base64.b64encode(hashlib.sha256(b'{"imports": {}}\n').digest()).decode()
    assert policy == (
        f"default-src 'none'; script-src 'self' 'sha256-{digest}'; style-src 'self'; "
        "img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
        "form-action 'none'"
    )


def test_a_page_without_an_import_map_has_no_policy() -> None:
    with pytest.raises(ValueError, match="no <script"):
        content_security_policy("<head><script src='/x.js'></script></head>")


def test_the_page_is_served_with_lf_whatever_git_wrote(client: TestClient) -> None:
    page = client.get("/").text

    # The browser hashes the import map after turning CRLF into LF; so must the server.
    assert "\r" not in page
    hashed = hashlib.sha256(_import_map(page).encode()).digest()
    policy = client.get("/").headers["content-security-policy"]
    assert _policy_hash(policy) == base64.b64encode(hashed).decode()


# --------------------------------------------------------------------------- the Host


@pytest.mark.parametrize("path", ["/", "/static/main.js", *API, "/nowhere"])
@pytest.mark.parametrize(
    "host",
    [
        "viewer.example",
        f"viewer.example:{PORT}",
        "127.0.0.1",
        "127.0.0.1:1",
        f"127.0.0.2:{PORT}",
        f"[::1]:{PORT}",
        f"localhost.viewer.example:{PORT}",
    ],
)
def test_a_foreign_host_gets_421_before_any_route(client: TestClient, path: str, host: str) -> None:
    response = client.get(path, headers={"Host": host, **_authorised()})

    assert response.status_code == 421
    assert response.json() == {
        "error": "this server answers only to 127.0.0.1 and localhost on its port"
    }
    assert response.headers["cache-control"] == "no-store"
    assert "content-security-policy" in response.headers


@pytest.mark.parametrize("host", [f"127.0.0.1:{PORT}", f"localhost:{PORT}", f"LocalHost:{PORT}"])
def test_the_two_names_of_loopback_are_served(client: TestClient, host: str) -> None:
    assert client.get("/", headers={"Host": host}).status_code == 200


@pytest.mark.anyio
async def test_two_host_headers_get_421(app: Security) -> None:
    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    host = f"127.0.0.1:{PORT}".encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", host), (b"host", host)],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", PORT),
    }

    await app(scope, receive, send)

    assert sent[0]["status"] == 421


# --------------------------------------------------------------------------- methods


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
@pytest.mark.parametrize("path", ["/", "/static/main.js", "/api/models"])
def test_only_get_and_head_are_allowed(client: TestClient, method: str, path: str) -> None:
    response = client.request(method, path, headers={"Origin": "http://viewer.example"})

    assert response.status_code == 405
    assert response.headers["allow"] == "GET, HEAD"
    assert response.json() == {"error": "only GET and HEAD are allowed"}
    # No CORS: a preflight gets nothing that would let another origin read a response.
    assert not any(name.startswith("access-control-") for name in response.headers)


def test_head_is_answered_like_get(client: TestClient) -> None:
    response = client.head("/")

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/html; charset=utf-8"


# --------------------------------------------------------------------------- the token


@pytest.mark.parametrize("path", API)
@pytest.mark.parametrize(
    "authorization",
    [
        None,
        f"Bearer {new_token()}",
        f"Bearer {TOKEN[:-1]}",
        f"Bearer {TOKEN} ",
        f"Basic {TOKEN}",
        TOKEN,
        f"Bearer{TOKEN}",
        "Bearer",
    ],
)
def test_a_missing_or_wrong_token_gets_401_on_every_api_route(
    client: TestClient, path: str, authorization: str | None
) -> None:
    headers = {} if authorization is None else {"Authorization": authorization}

    response = client.get(path, headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {
        "error": "the token is missing or wrong: open the URL that ifc-sql view printed"
    }


def test_a_token_that_is_not_ascii_gets_401(client: TestClient) -> None:
    response = client.get("/api/models", headers={"Authorization": b"Bearer \xe9\xe9"})

    assert response.status_code == 401


def test_the_token_is_not_taken_from_the_query_or_a_cookie(client: TestClient) -> None:
    assert client.get(f"/api/models?t={TOKEN}").status_code == 401
    assert client.get("/api/models", headers={"Cookie": f"t={TOKEN}"}).status_code == 401


def test_the_scheme_of_the_token_is_case_insensitive(client: TestClient) -> None:
    response = client.get("/api/models", headers={"Authorization": f"bearer {TOKEN}"})

    assert response.status_code == 503  # past the token: the database is down


def test_a_database_that_does_not_answer_gets_503_without_its_password(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger=http.__name__)

    response = client.get("/api/models", headers=_authorised())

    assert response.status_code == 503
    assert response.json() == {
        "error": "the database did not answer; the log of ifc-sql view says why"
    }
    assert response.headers["cache-control"] == "no-store"
    [record] = caplog.records
    assert record.getMessage() == "viewer request failed"
    # Linux refuses port 1 at once; Windows lets the connection time out.
    assert "connection" in record.__dict__["error"].lower()
    assert SECRET not in caplog.text
    assert SECRET not in response.text


# --------------------------------------------------------------------------- the page


def test_the_page_and_its_files_need_no_token(client: TestClient) -> None:
    page = client.get("/")
    script = client.get("/static/main.js")
    three = client.get("/static/vendor/three/three.module.js")
    licence = client.get("/static/vendor/three/LICENSE")
    style = client.get("/static/viewer.css")
    icon = client.get("/static/favicon.svg")

    assert page.status_code == 200
    assert page.headers["content-type"] == "text/html; charset=utf-8"
    assert '<script type="importmap">' in page.text
    assert script.headers["content-type"] == "text/javascript; charset=utf-8"
    assert three.headers["content-type"] == "text/javascript; charset=utf-8"
    assert licence.headers["content-type"] == "text/plain; charset=utf-8"
    assert style.headers["content-type"] == "text/css; charset=utf-8"
    assert icon.headers["content-type"] == "image/svg+xml"
    assert three.content == (http.STATIC / "vendor/three/three.module.js").read_bytes()
    for response in (page, script, three, licence, style, icon):
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["etag"] == f'"{hashlib.sha256(response.content).hexdigest()}"'


@pytest.mark.parametrize("path", ["/", "/static/main.js", "/static/vendor/three/LICENSE"])
def test_a_file_the_browser_holds_gets_304(client: TestClient, path: str) -> None:
    etag = client.get(path).headers["etag"]

    for header in (etag, f"W/{etag}", f'"stale", {etag}'):
        response = client.get(path, headers={"If-None-Match": header})
        assert response.status_code == 304, header
        assert response.content == b""
        assert response.headers["etag"] == etag
    assert client.get(path, headers={"If-None-Match": '"stale"'}).status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/static/",
        "/static/vendor",
        "/static/vendor/three/",
        "/static/index.html",
        "/static/../http.py",
        "/static/%2e%2e/http.py",
        "/static/..%2fhttp.py",
        "/static/..%5chttp.py",
        "/static/vendor/../../http.py",
        "/static/%2e%2e/%2e%2e/viewer/http.py",
        "/static/C:%5cWindows%5cwin.ini",
        "/static//etc/passwd",
        "/static/missing.js",
    ],
)
def test_static_serves_the_pages_files_only_no_listing_and_no_dot_dot(
    client: TestClient, path: str
) -> None:
    response = client.get(path)

    assert response.status_code == 404
    # A path the client normalises (/static/../x is /x) gets the router's own 404.
    assert response.content in (b'{"error":"the page has no such file"}', b"Not Found")


def test_an_unknown_route_is_404(client: TestClient) -> None:
    assert client.get("/api/nowhere", headers=_authorised()).status_code == 404
    assert client.get("/favicon.ico").status_code == 404


@pytest.mark.parametrize(
    ("method", "path", "headers"),
    [
        ("GET", "/", {}),
        ("GET", "/static/main.js", {}),
        ("GET", "/static/missing.js", {}),
        ("GET", "/nowhere", {}),
        ("GET", "/", {"Host": "viewer.example"}),
        ("POST", "/", {}),
        ("GET", "/api/models", {}),
        ("GET", "/api/models", _authorised()),
    ],
)
def test_every_response_carries_every_header(
    client: TestClient, method: str, path: str, headers: dict[str, str]
) -> None:
    response = client.request(method, path, headers=headers)

    for name, value in HEADERS.items():
        assert response.headers[name] == value
    assert response.headers["content-security-policy"] == content_security_policy(
        (http.STATIC / http.PAGE).read_text(encoding="utf-8").replace("\r\n", "\n")
    )
    assert "set-cookie" not in response.headers
    assert not any(name.startswith("access-control-") for name in response.headers)


def test_a_server_error_carries_the_headers_too(engine: Engine) -> None:
    async def broken(request: Request) -> Response:
        raise RuntimeError("a bug")

    app = create_app(engine, TOKEN, port=PORT)
    app.app.router.routes.append(Route("/broken", broken))
    with TestClient(app, base_url=BASE, raise_server_exceptions=False) as client:
        response = client.get("/broken")

    assert response.status_code == 500
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" in response.headers


# --------------------------------------------------------------------------- the routes


def test_the_routes_are_pinned(app: Security) -> None:
    routes = [(route.path, sorted(route.methods)) for route in app.app.routes]

    assert routes == [
        ("/", ["GET", "HEAD"]),
        ("/static/{path:path}", ["GET", "HEAD"]),
        ("/api/models", ["GET", "HEAD"]),
        ("/api/models/{model_id:int}/scene", ["GET", "HEAD"]),
        ("/api/models/{model_id:int}/scene/{chunk_no:int}.glb", ["GET", "HEAD"]),
        ("/api/models/{model_id:int}/objects/{global_id}", ["GET", "HEAD"]),
    ]


def test_the_api_answers_json(client: TestClient) -> None:
    response = client.get("/api/models", headers=_authorised())

    assert response.headers["content-type"] == "application/json"
    assert json.loads(response.content)["error"]
