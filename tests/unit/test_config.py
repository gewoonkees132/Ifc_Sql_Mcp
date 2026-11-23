from pathlib import Path

import pytest

from ifc_sql_mcp.config import (
    DEFAULT_DATABASE_URL,
    DEFAULT_LOG_LEVEL,
    DEFAULT_READER_PASSWORD,
    Settings,
    SettingsError,
    parse_log_level,
)


def test_defaults_match_docker_compose_when_env_is_empty() -> None:
    settings = Settings.from_env({})

    assert settings.database_url == DEFAULT_DATABASE_URL
    assert settings.database_url == "postgresql+psycopg://ifc:ifc-dev-only@localhost:5432/ifc"
    assert settings.log_level == DEFAULT_LOG_LEVEL
    assert settings.reader_password == DEFAULT_READER_PASSWORD


def test_values_are_read_from_env_and_normalised() -> None:
    settings = Settings.from_env(
        {
            "IFC_SQL_DATABASE_URL": "  postgresql+psycopg://u:p@db:6543/x  ",
            "IFC_SQL_READER_PASSWORD": " spaces are kept ",
            "IFC_SQL_LOG_LEVEL": " debug ",
        }
    )

    assert settings.database_url == "postgresql+psycopg://u:p@db:6543/x"
    # A password is used exactly as given: stripping could silently change it.
    assert settings.reader_password == " spaces are kept "
    assert settings.log_level == "DEBUG"


def test_empty_reader_password_is_rejected() -> None:
    with pytest.raises(SettingsError, match="IFC_SQL_READER_PASSWORD"):
        Settings.from_env({"IFC_SQL_READER_PASSWORD": ""})


def test_non_postgres_url_is_rejected() -> None:
    with pytest.raises(SettingsError, match="IFC_SQL_DATABASE_URL"):
        Settings.from_env({"IFC_SQL_DATABASE_URL": "sqlite:///ifc.db"})


def test_unknown_log_level_names_the_variable() -> None:
    with pytest.raises(SettingsError, match=r"IFC_SQL_LOG_LEVEL: log level must be one of"):
        Settings.from_env({"IFC_SQL_LOG_LEVEL": "verbose"})


@pytest.mark.parametrize(("raw", "expected"), [("info", "INFO"), ("WARNING", "WARNING")])
def test_parse_log_level_accepts_known_levels(raw: str, expected: str) -> None:
    assert parse_log_level(raw) == expected


def test_parse_log_level_rejects_unknown_levels() -> None:
    with pytest.raises(SettingsError, match="'TRACE'"):
        parse_log_level("TRACE")


def test_settings_are_immutable() -> None:
    settings = Settings.from_env({})

    with pytest.raises(AttributeError):
        settings.log_level = "DEBUG"  # type: ignore[misc]


def test_a_data_dir_asks_for_the_embedded_database(tmp_path: Path) -> None:
    settings = Settings.from_env({"IFC_SQL_DATA_DIR": f" {tmp_path} "})

    assert settings.data_dir == tmp_path
    assert Settings.from_env({"IFC_SQL_DATA_DIR": "  "}).data_dir is None
    assert Settings.from_env({}).data_dir is None


def test_a_data_dir_and_a_database_url_are_refused_together(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="not both"):
        Settings.from_env(
            {"IFC_SQL_DATA_DIR": str(tmp_path), "IFC_SQL_DATABASE_URL": DEFAULT_DATABASE_URL}
        )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1", True), (" true ", True), ("Yes", True), ("on", True), ("0", False), ("false", False),
     ("NO", False), ("off", False), ("", False), ("  ", False)],
)  # fmt: skip
def test_the_viewer_feed_is_a_boolean(raw: str, expected: bool) -> None:
    assert Settings.from_env({"IFC_SQL_VIEWER_FEED": raw}).viewer_feed is expected


def test_the_viewer_feed_is_off_unless_asked_for() -> None:
    assert Settings.from_env({}).viewer_feed is False


def test_a_viewer_feed_that_is_no_boolean_names_the_variable() -> None:
    with pytest.raises(SettingsError, match=r"IFC_SQL_VIEWER_FEED must be 1 or 0 \(.*\), got '2'"):
        Settings.from_env({"IFC_SQL_VIEWER_FEED": "2"})
