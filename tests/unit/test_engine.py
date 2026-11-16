from ifc_sql_mcp.db.engine import make_engine, reader_database_url, redact


def test_make_engine_uses_psycopg3_driver_without_connecting() -> None:
    engine = make_engine("postgresql+psycopg://u:secret@localhost:5432/ifc")
    try:
        assert engine.dialect.driver == "psycopg"
        assert engine.url.database == "ifc"
    finally:
        engine.dispose()


def test_redact_hides_password() -> None:
    redacted = redact("postgresql+psycopg://ifc:s3cret@db.example:5432/ifc")

    assert "s3cret" not in redacted
    assert redacted == "postgresql+psycopg://ifc:***@db.example:5432/ifc"


def test_reader_database_url_swaps_in_the_reader_credentials() -> None:
    url = reader_database_url("postgresql+psycopg://ifc:owner-pw@db.example:6543/ifc", "r:e@d/er")

    # Special characters in the password are escaped, so the URL still parses.
    assert url == "postgresql+psycopg://ifc_reader:r%3Ae%40d%2Fer@db.example:6543/ifc"
