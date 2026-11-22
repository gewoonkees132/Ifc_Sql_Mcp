"""Runtime settings, read from environment variables.

Why plain environment variables and a frozen dataclass rather than a settings framework:
there are only a few settings, they are set the same way in docker compose, CI and a
developer shell, and keeping the parsing rules in one short function makes them easy to
audit. The defaults match ``docker-compose.yml`` so a fresh clone works without a ``.env``.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

ENV_PREFIX = "IFC_SQL_"

# Local development only: the compose service binds to 127.0.0.1 and this password is
# public in the repository. Override it with IFC_SQL_DATABASE_URL anywhere else.
DEFAULT_DATABASE_URL = "postgresql+psycopg://ifc:ifc-dev-only@localhost:5432/ifc"
# Password that migration 0003 gives the read-only ``ifc_reader`` role. Same caveat: it is
# public, so set IFC_SQL_READER_PASSWORD for any database reachable by anyone but you.
DEFAULT_READER_PASSWORD = "ifc-reader-dev-only"  # noqa: S105 (public dev default, above)
DEFAULT_LOG_LEVEL = "INFO"

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class SettingsError(ValueError):
    """Raised when an environment variable holds a value we cannot use."""


def parse_log_level(value: str) -> str:
    """Normalise a log level name, rejecting anything outside ``LOG_LEVELS``.

    Shared by the environment parser and the ``--log-level`` CLI flag so both accept
    exactly the same values.
    """
    level = value.strip().upper()
    if level not in LOG_LEVELS:
        raise SettingsError(f"log level must be one of {', '.join(LOG_LEVELS)}, got {value!r}")
    return level


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated settings for one process.

    Attributes:
        database_url: SQLAlchemy URL for the owning role, used by migrations and ingest.
        reader_password: Password for the read-only ``ifc_reader`` role. Migrations set
            it; read-only clients connect with it (see ``reader_database_url``).
        log_level: One of ``LOG_LEVELS``.
        data_dir: With ``IFC_SQL_DATA_DIR``, the folder of an embedded PostgreSQL that
            ifc-sql runs itself (``db/embedded.py``); ``database_url`` is then that
            server's, filled in when a command first needs it.
    """

    database_url: str
    reader_password: str
    log_level: str
    data_dir: Path | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        """Build settings from ``env`` (defaults to ``os.environ``).

        Taking the mapping as a parameter keeps tests free of global state: they pass a
        plain dict instead of patching the process environment.
        """
        source = os.environ if env is None else env

        data_dir_text = source.get(f"{ENV_PREFIX}DATA_DIR", "").strip()
        data_dir = Path(data_dir_text) if data_dir_text else None
        if data_dir is not None and f"{ENV_PREFIX}DATABASE_URL" in source:
            raise SettingsError(
                f"set {ENV_PREFIX}DATA_DIR (an embedded database) or {ENV_PREFIX}DATABASE_URL "
                "(a PostgreSQL server), not both"
            )

        database_url = source.get(f"{ENV_PREFIX}DATABASE_URL", DEFAULT_DATABASE_URL).strip()
        if not database_url.startswith("postgresql"):
            raise SettingsError(
                f"{ENV_PREFIX}DATABASE_URL must be a PostgreSQL URL "
                "(e.g. postgresql+psycopg://user:password@host:5432/db)"
            )

        reader_password = source.get(f"{ENV_PREFIX}READER_PASSWORD", DEFAULT_READER_PASSWORD)
        if not reader_password:
            raise SettingsError(f"{ENV_PREFIX}READER_PASSWORD must not be empty")

        try:
            log_level = parse_log_level(source.get(f"{ENV_PREFIX}LOG_LEVEL", DEFAULT_LOG_LEVEL))
        except SettingsError as exc:
            raise SettingsError(f"{ENV_PREFIX}LOG_LEVEL: {exc}") from exc

        return cls(
            database_url=database_url,
            reader_password=reader_password,
            log_level=log_level,
            data_dir=data_dir,
        )
