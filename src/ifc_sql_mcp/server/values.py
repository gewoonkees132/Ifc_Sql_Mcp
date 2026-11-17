"""Turn database values into JSON, and keep each one to a bounded size.

Used by every tool. Text that came from an IFC file (names, property values, finding
messages) is untrusted and has no length limit in the format, so a crafted file could
otherwise put megabytes into one tool result. The longest real value in the four Duplex
files is a 904-character product specification, which ``MAX_TEXT_CHARS`` keeps whole.
"""

import json
import math
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from decimal import Decimal

MAX_TEXT_CHARS = 2_000
"""Longest text value a tool returns; a longer one is cut by :func:`clip`."""

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None


def clip(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    """Return ``text``, cut to ``limit`` characters if it is longer.

    A cut value ends with a note saying how long the original was, so neither an LLM nor
    a person mistakes it for the whole value.
    """
    if len(text) <= limit:
        return text
    return f"{text[:limit]}… [cut: {len(text):,} characters, the first {limit:,} shown]"


def to_json(value: object) -> JsonValue:
    r"""Convert one value as psycopg returns it into a JSON value.

    * ``float``: NaN and the infinities, which JSON cannot hold, become the strings
      PostgreSQL writes for them (``'NaN'``, ``'Infinity'``, ``'-Infinity'``).
    * ``Decimal`` (``numeric``): a JSON number when a float holds it exactly (every
      ``numeric(9,3)`` elevation does); otherwise its digits as a string, so no digit of a
      large sum is lost.
    * Dates and times: ISO 8601. An ``interval`` (``timedelta``) as an ISO 8601 duration,
      for example ``P0DT0H1M30.5S``.
    * ``bytea``: PostgreSQL's hex form, ``\x0102``.
    * Arrays and ``json``/``jsonb``: converted element by element.
    * Anything else (``uuid``, network addresses, ranges): its text form.
    """
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else _non_finite(value)
    if isinstance(value, Decimal):
        return _decimal(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, timedelta):
        return _duration(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return "\\x" + bytes(value).hex()
    if isinstance(value, list | tuple):
        return [to_json(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): to_json(item) for key, item in value.items()}
    return str(value)


def to_cell(value: object, limit: int = MAX_TEXT_CHARS) -> tuple[JsonValue, bool]:
    """Convert a value for a ``run_sql`` result and cut it to ``limit`` characters.

    Text is cut with :func:`clip`. An array or JSON value longer than ``limit`` as compact
    JSON text becomes that text, cut, so its type changes to a string. Numbers, booleans and
    NULL are never cut.

    Returns:
        The JSON value, and whether it was cut.
    """
    converted = to_json(value)
    if isinstance(converted, list | dict):
        text = json.dumps(converted, ensure_ascii=False, separators=(",", ":"))
        if len(text) > limit:
            return clip(text, limit), True
    if isinstance(converted, str) and len(converted) > limit:
        return clip(converted, limit), True
    return converted, False


def _non_finite(value: float) -> str:
    if math.isnan(value):
        return "NaN"
    return "Infinity" if value > 0 else "-Infinity"


def _decimal(value: Decimal) -> float | str:
    if value.is_finite():
        number = float(value)
        if math.isfinite(number) and Decimal(repr(number)) == value:
            return number
    return str(value)


def _duration(value: timedelta) -> str:
    sign = "-" if value < timedelta(0) else ""
    value = abs(value)
    hours, rest = divmod(value.seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    fraction = f".{value.microseconds:06d}".rstrip("0") if value.microseconds else ""
    return f"{sign}P{value.days}DT{hours}H{minutes}M{seconds}{fraction}S"
