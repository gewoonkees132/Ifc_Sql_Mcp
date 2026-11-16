from ifc_sql_mcp.db.engine import make_engine, redact


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
