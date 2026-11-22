"""Database values as JSON, and the cap on the size of each one."""

import json
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from ipaddress import IPv4Address
from uuid import UUID

import pytest

from ifc_sql_mcp.server.values import MAX_TEXT_CHARS, clip, to_cell, to_json


def test_clip_keeps_text_up_to_the_limit() -> None:
    assert clip("abc", limit=3) == "abc"
    assert clip("x" * MAX_TEXT_CHARS) == "x" * MAX_TEXT_CHARS


def test_clip_cuts_longer_text_and_says_so() -> None:
    assert clip("abcdef", limit=3) == "abc… [cut: 6 characters, the first 3 shown]"
    assert clip("x" * 12_345).endswith("… [cut: 12,345 characters, the first 2,000 shown]")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(None, None, id="null"),
        pytest.param(True, True, id="bool"),
        pytest.param(9_007_199_254_740_993, 9_007_199_254_740_993, id="bigint"),
        pytest.param("Level 1", "Level 1", id="text"),
        pytest.param(2.5, 2.5, id="float"),
        pytest.param(float("nan"), "NaN", id="nan"),
        pytest.param(float("inf"), "Infinity", id="infinity"),
        pytest.param(float("-inf"), "-Infinity", id="minus infinity"),
        pytest.param(Decimal("3.100"), 3.1, id="numeric a float holds"),
        pytest.param(Decimal("-1.250"), -1.25, id="negative numeric"),
        pytest.param(
            Decimal("12345678901234567890.123"),
            "12345678901234567890.123",
            id="numeric too precise for a float",
        ),
        pytest.param(Decimal("1E+400"), "1E+400", id="numeric too large for a float"),
        pytest.param(Decimal("1E-400"), "1E-400", id="numeric too small for a float"),
        pytest.param(Decimal("NaN"), "NaN", id="numeric nan"),
        pytest.param(Decimal("Infinity"), "Infinity", id="numeric infinity"),
        pytest.param(
            datetime(2026, 11, 17, 19, 5, 14, 964000, tzinfo=UTC),
            "2026-11-17T19:05:14.964000+00:00",
            id="timestamptz",
        ),
        pytest.param(date(2026, 11, 17), "2026-11-17", id="date"),
        pytest.param(time(12, 30), "12:30:00", id="time"),
        pytest.param(timedelta(seconds=90.5), "P0DT0H1M30.5S", id="interval"),
        pytest.param(timedelta(days=2, hours=3), "P2DT3H0M0S", id="interval in days"),
        pytest.param(timedelta(seconds=-1.25), "-P0DT0H0M1.25S", id="negative interval"),
        pytest.param(b"\x01\xff", "\\x01ff", id="bytea"),
        pytest.param(memoryview(b"\x02"), "\\x02", id="bytea as memoryview"),
        pytest.param([Decimal("1.5"), None, "a"], [1.5, None, "a"], id="array"),
        pytest.param((1, 2), [1, 2], id="tuple"),
        pytest.param(
            {"a": [Decimal("2.0")], "b": {"c": True}},
            {"a": [2.0], "b": {"c": True}},
            id="jsonb",
        ),
        pytest.param(
            UUID("e055385c-3b9a-4fb1-b470-f7acc1a6fb3f"),
            "e055385c-3b9a-4fb1-b470-f7acc1a6fb3f",
            id="uuid",
        ),
        pytest.param(IPv4Address("127.0.0.1"), "127.0.0.1", id="inet"),
    ],
)
def test_to_json_converts_each_kind_of_value(value: object, expected: object) -> None:
    converted = to_json(value)

    assert converted == expected
    assert type(converted) is type(expected)
    json.dumps(converted, allow_nan=False)


def test_to_cell_leaves_short_values_alone() -> None:
    assert to_cell("short", limit=10) == ("short", False)
    assert to_cell([1, 2], limit=10) == ([1, 2], False)
    assert to_cell(Decimal("3.100"), limit=1) == (3.1, False)
    assert to_cell(10**30, limit=2) == (10**30, False)
    assert to_cell(None, limit=0) == (None, False)


def test_to_cell_cuts_long_text() -> None:
    assert to_cell("abcdef", limit=3) == ("abc… [cut: 6 characters, the first 3 shown]", True)


def test_to_cell_turns_a_long_array_or_object_into_cut_json_text() -> None:
    assert to_cell([1, 2, 3], limit=4) == ("[1,2… [cut: 7 characters, the first 4 shown]", True)
    assert to_cell({"key": "é" * 10}, limit=8) == (
        '{"key":"… [cut: 20 characters, the first 8 shown]',
        True,
    )
