"""Structured ``key=value`` logging on top of the standard library.

Callers log the usual way and attach fields with ``extra``::

    logger.info("table loaded", extra={"table": "element", "rows": 268})

which renders as one line (wrapped here)::

    ts=2026-11-16T19:05:14.964+00:00 level=info logger=ifc_sql_mcp.load
    msg="table loaded" table=element rows=268

Two design points matter:

* Logs go to **stderr**. The MCP stdio transport uses stdout for JSON-RPC messages, so a
  single log line on stdout would corrupt the protocol stream.
* Values are escaped. Element names and property values come from IFC files, which are
  untrusted input; escaping quotes and newlines stops a crafted name from forging extra
  log lines or fields.
"""

import logging
import sys
from datetime import UTC, datetime

# Every attribute a plain LogRecord carries. Anything else on a record came from `extra=`.
_STANDARD_RECORD_ATTRS = frozenset(
    vars(logging.LogRecord("", logging.INFO, "", 0, "", None, None)).keys()
) | {"message", "asctime"}

_CHARS_NEEDING_QUOTES = frozenset(' ="\\\n\r\t')


def format_value(value: object) -> str:
    """Render one value, quoting and escaping it only when necessary.

    Plain tokens stay unquoted so logs remain easy to grep (``rows=268``); anything with
    whitespace, ``=``, quotes or backslashes is wrapped in double quotes and escaped.
    """
    text = str(value)
    if text and not (_CHARS_NEEDING_QUOTES & set(text)):
        return text
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


class KeyValueFormatter(logging.Formatter):
    """Format records as a single line of ``key=value`` pairs.

    The fixed keys come first in a stable order (ts, level, logger, msg) so lines line up
    when read by eye; ``extra`` fields follow in the order they were given.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Return the record as one escaped ``key=value`` line."""
        fields: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD_RECORD_ATTRS and not key.startswith("_"):
                fields[key] = value
        if record.exc_info:
            fields["exc"] = self.formatException(record.exc_info)
        return " ".join(f"{key}={format_value(value)}" for key, value in fields.items())


def configure_logging(level: str) -> None:
    """Send all logging to stderr in ``key=value`` form at ``level``.

    ``force=True`` replaces any handlers installed earlier (for example by a library or a
    previous call), so calling this twice never duplicates log lines.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(KeyValueFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
