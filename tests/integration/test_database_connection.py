from sqlalchemy import Engine, text

from ifc_sql_mcp.db.engine import APPLICATION_NAME


def test_server_is_postgresql_16(engine: Engine) -> None:
    with engine.connect() as conn:
        version_num = conn.execute(text("SHOW server_version_num")).scalar_one()

    assert int(version_num) // 10000 == 16


def test_sessions_are_labelled_with_application_name(engine: Engine) -> None:
    with engine.connect() as conn:
        name = conn.execute(text("SELECT current_setting('application_name')")).scalar_one()

    assert name == APPLICATION_NAME
