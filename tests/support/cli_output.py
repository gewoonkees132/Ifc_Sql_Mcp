"""Read CLI output as plain text, whatever the terminal: no colours, no boxes, no wrapping.

Typer forces coloured Rich output when it detects CI (GITHUB_ACTIONS, FORCE_COLOR), and
Rich draws usage errors in a box as wide as the terminal, wrapping long lines inside it.
A test that looks for a phrase must not depend on either: on a narrow terminal, or with a
long temporary path in the message, "does not exist" can land across two box lines.
"""

import re

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
# Rich's rounded box: the corners, edges and the joints of a titled top line.
_BOX_DRAWING = re.compile(r"[─-╿]")
_WHITESPACE = re.compile(r"\s+")


def plain(text: str) -> str:
    """Return ``text`` without ANSI codes or box drawing, every run of whitespace one space.

    Rich wraps at spaces, so a wrapped phrase reads whole again once the line breaks and
    the box's side borders between its parts are gone.
    """
    text = _BOX_DRAWING.sub(" ", _ANSI_ESCAPE.sub("", text))
    return _WHITESPACE.sub(" ", text).strip()
