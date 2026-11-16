import logging
import sys
from collections.abc import Iterator

import pytest

from ifc_sql_mcp.logging_config import KeyValueFormatter, configure_logging, format_value


@pytest.fixture
def restore_root_logger() -> Iterator[None]:
    """configure_logging mutates the global root logger; put it back after the test."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def _record(msg: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("ifc_sql_mcp.test", logging.INFO, __file__, 1, msg, None, None)
    record.created = 0.0  # 1970-01-01T00:00:00Z, so the timestamp is predictable
    for key, value in extra.items():
        setattr(record, key, value)
    return record


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (268, "268"),
        ("element", "element"),
        (0.25, "0.25"),
        (None, "None"),
        ("", '""'),
        ("table loaded", '"table loaded"'),
        ("a=b", '"a=b"'),
        ('say "hi"', '"say \\"hi\\""'),
        ("C:\\path", '"C:\\\\path"'),
        ("tab\there", '"tab\\there"'),
    ],
)
def test_format_value_quotes_only_when_needed(value: object, expected: str) -> None:
    assert format_value(value) == expected


def test_formatter_puts_fixed_keys_first_then_extra_fields_in_order() -> None:
    line = KeyValueFormatter().format(_record("table loaded", table="element", rows=268))

    assert line == (
        'ts=1970-01-01T00:00:00.000+00:00 level=info logger=ifc_sql_mcp.test msg="table loaded" '
        "table=element rows=268"
    )


def test_untrusted_newlines_cannot_forge_a_second_log_line() -> None:
    # A wall name taken from an IFC file, crafted to look like a separate log entry.
    malicious = "Basic Wall\nts=1970-01-01 level=error msg=forged"

    line = KeyValueFormatter().format(_record("parsed name", raw_name=malicious))

    assert "\n" not in line
    assert 'raw_name="Basic Wall\\nts=1970-01-01 level=error msg=forged"' in line


def test_exception_traceback_is_kept_on_one_line() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord(
            "x", logging.ERROR, __file__, 1, "failed", None, exc_info=sys.exc_info()
        )

    line = KeyValueFormatter().format(record)

    assert "\n" not in line
    assert "exc=" in line
    assert "ValueError: boom" in line


@pytest.mark.usefixtures("restore_root_logger")
def test_configure_logging_writes_to_stderr_not_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")

    logging.getLogger("ifc_sql_mcp.test").info("hello", extra={"n": 1})

    captured = capsys.readouterr()
    assert captured.out == ""  # stdout is reserved for the MCP stdio transport
    assert "level=info logger=ifc_sql_mcp.test msg=hello n=1" in captured.err


@pytest.mark.usefixtures("restore_root_logger")
def test_configure_logging_twice_does_not_duplicate_lines(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")
    configure_logging("WARNING")

    logging.getLogger("x").warning("once")
    logging.getLogger("x").info("filtered out")

    err = capsys.readouterr().err
    assert err.count("msg=once") == 1
    assert "filtered out" not in err
