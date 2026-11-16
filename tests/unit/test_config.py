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
