#!/usr/bin/env python3
"""Start ``ifc-sql view`` as a user would, fetch one chunk over its port, and stop it.

Usage::

    make view-smoke     # or: uv run python scripts/view_smoke.py

It starts ``ifc-sql view --no-open`` with this shell's IFC_SQL_* variables and reads the
URL it prints. Then, over a real socket on 127.0.0.1, with the URL's token: it lists the
models, reads the manifest of the first model shown that has a scene, and fetches that
scene's chunk 0, whose first bytes must be glTF 2's header; it fetches the chunk again
with its ETag and expects 304, and sends a foreign Host and expects 421. It stops the
viewer and checks that the token never reached its stderr. It exits 1 at the first check
that fails, with what it found. CI runs it on Windows, on the embedded database.
"""

import http.client
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SERVER = Path(sys.executable).with_name("ifc-sql")
URL_LINE = re.compile(r"http://127\.0\.0\.1:(\d+)/#t=([A-Za-z0-9_-]{43})")
START_SECONDS = 120
"""How long ``ifc-sql view`` may take to print its URL: the embedded database may start."""


class SmokeError(Exception):
    """A check failed; the message says what was found."""


@dataclass
class Viewer:
    """A running ``ifc-sql view``: its port and token, and its stderr once it stopped."""

    port: int
    token: str
    stderr: str = field(default="")

    def get(self, path: str, **headers: str) -> tuple[int, dict[str, str], bytes]:
        """GET ``path`` with the token; return the status, the headers and the body."""
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            connection.request(
                "GET", path, headers={"Authorization": f"Bearer {self.token}", **headers}
            )
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def get_json(self, path: str) -> Any:
        """GET ``path``, which must answer 200 with JSON."""
        status, _, body = self.get(path)
        if status != 200:
            raise SmokeError(f"GET {path} answered {status}: {body[:300]!r}")
        return json.loads(body)


@contextmanager
def running_view(*arguments: str) -> Iterator[Viewer]:
    """Run ``ifc-sql view --no-open`` with ``arguments`` until the block ends.

    The viewer inherits this process's environment. Its stdout's first line must be the
    URL; its stderr goes to a temporary file, read into ``Viewer.stderr`` once it stopped.
    """
    with (
        tempfile.TemporaryFile("w+", encoding="utf-8") as log,
        subprocess.Popen(  # noqa: S603 - our own command, with fixed arguments
            [str(SERVER), "view", "--no-open", *arguments],
            stdout=subprocess.PIPE,
            stderr=log,
            text=True,
            encoding="utf-8",
        ) as process,
    ):
        # A viewer that neither prints its URL nor exits must not hang the caller.
        watchdog = threading.Timer(START_SECONDS, process.kill)
        watchdog.start()
        viewer = None
        try:
            stdout = process.stdout
            if stdout is None:  # stdout=PIPE above
                raise SmokeError("ifc-sql view's stdout is not a pipe")
            line = stdout.readline().strip()
            watchdog.cancel()
            match = URL_LINE.fullmatch(line)
            if match is None:
                process.wait(timeout=30)
                log.seek(0)
                raise SmokeError(
                    f"ifc-sql view printed no URL (exit code {process.returncode}): "
                    f"{log.read().strip()}"
                )
            viewer = Viewer(port=int(match.group(1)), token=match.group(2))
            yield viewer
        finally:
            watchdog.cancel()
            _stop(process)
            log.seek(0)
            if viewer is not None:
                viewer.stderr = log.read()


def _stop(process: subprocess.Popen[str]) -> None:
    """Stop the viewer: Ctrl+C where a signal can say it, else end the process."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        # Ctrl+C reaches every process on the console, this one too: end it instead.
        process.terminate()
    else:
        process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def smoke(viewer: Viewer) -> list[str]:
    """Run the checks against ``viewer``; return the lines to print."""
    base = f"http://127.0.0.1:{viewer.port}/"
    catalogue = viewer.get_json("/api/models")
    models = {model["model_id"]: model for model in catalogue["models"]}
    shown = catalogue["shown"]
    drawn = [models[model_id] for model_id in shown["model_ids"] if models[model_id]["scene"]]
    with_scene = sum(1 for model in models.values() if model["scene"])
    lines = [
        f"ifc-sql view on {base}: {len(models)} models, {with_scene} with a scene; shown "
        f"first: {shown['label']} (models {', '.join(map(str, shown['model_ids']))})"
    ]
    if not drawn:
        raise SmokeError("no model shown first has a scene: run ifc-sql ingest … --viewer")
    model = drawn[0]
    manifest = viewer.get_json(f"/api/models/{model['model_id']}/scene")
    status, headers, body = viewer.get(f"/api/models/{model['model_id']}/scene/0.glb")
    if status != 200 or body[:8] != b"glTF\x02\x00\x00\x00":
        raise SmokeError(f"chunk 0 answered {status}, starting {body[:8]!r}, not glTF 2")
    lines.append(
        f"model {model['model_id']} ({model['discipline']}): {len(manifest['chunks'])} chunks, "
        f"{manifest['element_count']:,} elements drawn; chunk 0: {len(body):,} bytes of glTF 2"
    )
    again, _, rest = viewer.get(
        f"/api/models/{model['model_id']}/scene/0.glb", **{"If-None-Match": headers["etag"]}
    )
    if again != 304 or rest:
        raise SmokeError(f"chunk 0 with its ETag answered {again}, not 304")
    lines.append("chunk 0 again, with its ETag: 304")
    foreign, _, _ = viewer.get("/", Host="viewer.example:80")
    if foreign != 421:
        raise SmokeError(f"a foreign Host answered {foreign}, not 421")
    lines.append("a foreign Host: 421")
    return lines


def main() -> int:
    """Run the smoke test with this shell's settings; 0 if every check passed."""
    try:
        with running_view() as viewer:
            lines = smoke(viewer)
        if viewer.token in viewer.stderr:
            raise SmokeError("the token reached stderr")
    except SmokeError as exc:
        print(f"view smoke test failed: {exc}", file=sys.stderr)
        return 1
    lines.append("stopped; the token never reached stderr")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
