"""What the viewer's port holds: the Host check, the token and the headers (ADR 14, decision 3).

``ifc-sql view`` listens on 127.0.0.1 only, and :class:`Security` stands in front of every
route, of Starlette's own error pages too:

* **Host.** Only ``127.0.0.1:<port>`` and ``localhost:<port>`` are served; anything else
  gets 421 before any route runs. A page on another site whose name an attacker points at
  127.0.0.1 (DNS rebinding) sends its own name as the Host, so it never reaches the data.
  The MCP SDK's ``transport_security`` is not reused: it allows any port, and lets a
  request without an Origin through.
* **Methods.** GET and HEAD only: there is no route that writes.
* **Token.** Every ``/api/`` route needs ``Authorization: Bearer <token>``, a new
  :func:`new_token` on every start, compared in constant time. The page gets it from the
  URL's fragment, which no request, log or Referer carries. ``/`` and ``/static/`` hold
  no data and need none. There is no cookie: cookies are scoped by host, not by port, so
  one would reach every other server on 127.0.0.1.
* **Headers** on every response: the content security policy of
  :func:`content_security_policy`, ``nosniff`` and ``no-referrer``. The routes set
  ``Cache-Control`` themselves. There are no CORS headers, so no other origin may read a
  response.
"""

import base64
import hashlib
import hmac
import json
import re
import secrets

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

TOKEN_BYTES = 32
"""Random bytes in a token: ``secrets.token_urlsafe(32)``, 43 characters."""

API_PREFIX = "/api/"
"""The routes that hold data, and so need the token."""

METHODS = ("GET", "HEAD")

_IMPORT_MAP = re.compile(r'<script type="importmap">(.*?)</script>', re.DOTALL)


def new_token() -> str:
    """Return a new token for one run of ``ifc-sql view``."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def content_security_policy(page: str) -> str:
    """Return the policy for ``page``, the ``index.html`` the app serves.

    Scripts come only from the server itself, plus the page's one inline script, its
    import map, allowed by its SHA-256. The hash is of the text the browser sees: the
    HTML parser turns every CRLF into LF before it hashes, so ``page`` must already have
    LF line endings.

    Raises:
        ValueError: if the page has no import map.
    """
    match = _IMPORT_MAP.search(page)
    if match is None:
        raise ValueError('the page has no <script type="importmap">')
    digest = base64.b64encode(hashlib.sha256(match.group(1).encode("utf-8")).digest())
    return "; ".join(
        [
            "default-src 'none'",
            f"script-src 'self' 'sha256-{digest.decode('ascii')}'",
            "style-src 'self'",
            "img-src 'self'",
            "connect-src 'self'",
            "frame-ancestors 'none'",
            "base-uri 'none'",
            "form-action 'none'",
        ]
    )


class Security:
    """ASGI middleware: the Host check, the methods, the token and the headers.

    It wraps the whole Starlette app, outside Starlette's own error handling, so that a
    500 carries the headers too.

    Args:
        app: The app it guards.
        token: The token every ``/api/`` request must send.
        port: The port ``ifc-sql view`` listens on, which the Host must name.
        policy: The ``Content-Security-Policy`` of every response.
    """

    def __init__(self, app: ASGIApp, *, token: str, port: int, policy: str) -> None:
        self.app = app
        self._token = token.encode("ascii")
        self._hosts = frozenset({f"127.0.0.1:{port}", f"localhost:{port}"})
        self._headers = {
            "Content-Security-Policy": policy,
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        }

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Refuse the request, or pass it on; either way, add the headers."""
        if scope["type"] != "http":  # lifespan (uvicorn runs with lifespan and ws off)
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in self._headers.items():
                    headers[name] = value
            await send(message)

        refusal = self._refusal(scope)
        if refusal is None:
            await self.app(scope, receive, send_with_headers)
            return
        status, message, extra = refusal
        body = json.dumps({"error": message}).encode("utf-8")
        await send_with_headers(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                    *extra,
                ],
            }
        )
        await send_with_headers({"type": "http.response.body", "body": body})

    def _refusal(self, scope: Scope) -> tuple[int, str, list[tuple[bytes, bytes]]] | None:
        """The status, message and headers of a refused request; None to serve it."""
        headers = Headers(scope=scope)
        hosts = headers.getlist("host")
        if len(hosts) != 1 or hosts[0].lower() not in self._hosts:
            return 421, "this server answers only to 127.0.0.1 and localhost on its port", []
        if scope["method"] not in METHODS:
            return 405, "only GET and HEAD are allowed", [(b"allow", b"GET, HEAD")]
        if scope["path"].startswith(API_PREFIX) and not self._authorised(headers):
            return (
                401,
                "the token is missing or wrong: open the URL that ifc-sql view printed",
                [(b"www-authenticate", b"Bearer")],
            )
        return None

    def _authorised(self, headers: Headers) -> bool:
        scheme, _, credentials = headers.get("authorization", "").partition(" ")
        # Header values arrive as latin-1, which encodes back to the bytes that were sent.
        return scheme.lower() == "bearer" and hmac.compare_digest(
            credentials.encode("latin-1"), self._token
        )
