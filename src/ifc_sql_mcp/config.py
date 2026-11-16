"""Runtime settings, read from environment variables.

Why plain environment variables and a frozen dataclass rather than a settings framework:
there are only a few settings, they are set the same way in docker compose, CI and a
developer shell, and keeping the parsing rules in one short function makes them easy to
audit. The defaults match ``docker-compose.yml`` so a fresh clone works without a ``.env``.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass

ENV_PREFIX = "IFC_SQL_"

# Local development only: the compose service binds to 127.0.0.1 and this password is
# public in the repository. Override it with IFC_SQL_DATABASE_URL anywhere else.
DEFAULT_DATABASE_URL = "postgresql+psycopg://ifc:ifc-dev-only@localhost:5432/ifc"
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
        log_level: One of ``LOG_LEVELS``.
    """

    database_url: str
    log_level: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        """Build settings from ``env`` (defaults to ``os.environ``).

        Taking the mapping as a parameter keeps tests free of global state: they pass a
        plain dict instead of patching the process environment.
        """
        source = os.environ if env is None else env

        database_url = source.get(f"{ENV_PREFIX}DATABASE_URL", DEFAULT_DATABASE_URL).strip()
        if not database_url.startswith("postgresql"):
            raise SettingsError(
                f"{ENV_PREFIX}DATABASE_URL must be a PostgreSQL URL "
                "(e.g. postgresql+psycopg://user:password@host:5432/db)"
            )

        try:
            log_level = parse_log_level(source.get(f"{ENV_PREFIX}LOG_LEVEL", DEFAULT_LOG_LEVEL))
        except SettingsError as exc:
            raise SettingsError(f"{ENV_PREFIX}LOG_LEVEL: {exc}") from exc

        return cls(database_url=database_url, log_level=log_level)
