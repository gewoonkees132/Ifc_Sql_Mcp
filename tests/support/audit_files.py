"""The five Revit exports of the November 2026 audit: local copies and how each is loaded."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from download_audit_samples import AUDIT_SAMPLES, DEFAULT_DEST
from download_samples import ensure_sample
from sqlalchemy import Engine, text

from ifc_sql_mcp.config import Settings
from ifc_sql_mcp.ingest.pipeline import IngestResult

# The order and disciplines the audit loaded them in (docs/audit/harness/README.md).
AUDIT_DISCIPLINES = {"a": "ARC", "b": "ARC", "c": "MEP", "d": "ARC", "e": "ARC"}


def audit_path(key: str) -> Path:
    """Return the local copy of an audit file, downloading and verifying it if needed."""
    path, _ = ensure_sample(AUDIT_SAMPLES[key], Path(DEFAULT_DEST))
    return path


@dataclass(frozen=True)
class Modern:
    """The loaded database: its engine and settings, and each file's ingest result."""

    engine: Engine
    settings: Settings
    ingested: dict[str, IngestResult]

    def model(self, key: str) -> int:
        """The model id of one file: ``a`` to ``e``, or ``duplex``."""
        return self.ingested[key].model_id

    def scalar(self, sql: str, **params: object) -> object:
        """Run one query as the owner and return its single value."""
        with self.engine.connect() as connection:
            return connection.execute(text(sql), params).scalar_one()

    def rows(self, sql: str, **params: object) -> list[tuple[Any, ...]]:
        """Run one query as the owner and return its rows as tuples."""
        with self.engine.connect() as connection:
            return [tuple(row) for row in connection.execute(text(sql), params)]

    def findings(self, key: str, rule_id: str) -> int:
        """Findings of one rule in the model's latest data quality run."""
        return int(
            self.scalar(  # type: ignore[call-overload]
                "SELECT count(*) FROM ifc.v_dq_finding WHERE model_id = :m AND rule_id = :r",
                m=self.model(key),
                r=rule_id,
            )
        )
