"""Load each IFC export saved into a folder, then check it (``ifc-sql watch``, audit A18).

An architect re-exports from Revit every week, and each export meant an ``ifc-sql
ingest`` with the right options and then an ``ifc-sql dq``. ``ifc-sql watch`` does both
for every ``.ifc`` file in a folder, so saving the export there is enough:

* **A file is loaded once it has stopped changing.** Revit writes a large file over
  many seconds, so a file is taken only when its size and modification time are the
  same on two polls in a row. ``--once`` takes every file as it is, for a script.
* **A new export of the same file replaces the previous one.** A file whose name and
  discipline match a current model, but whose bytes differ, is loaded with
  ``replaces`` set to that model (A12). Project-wide views then count only the new one,
  and ``compare_models`` shows what changed (A15).
* **Each new model is checked** against the data quality rules, as ``ifc-sql dq`` does.
* **A file already loaded** (the same bytes) is passed over without a new run.
* **A failure does not stop the watch.** It is reported, and the file is tried again
  only when it changes.

The server stays read-only (ADR 6): loading is this command's job, not a tool's.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Engine, text

from ifc_sql_mcp.dq.catalogue import Rule
from ifc_sql_mcp.dq.run import DqResult, DqRunError, check_model
from ifc_sql_mcp.ingest.extract import sha256_file
from ifc_sql_mcp.ingest.pipeline import IngestError, IngestResult, ingest_file

type Version = tuple[int, int]
"""A file's size and modification time in nanoseconds: what tells one save from the next."""


@dataclass(frozen=True, slots=True)
class WatchEvent:
    """What the watch did with one file.

    Attributes:
        path: The file.
        ingest: The ingest, when the file was new.
        check: Its data quality check, when the ingest loaded a model.
        error: Why the file could not be loaded or checked.
    """

    path: Path
    ingest: IngestResult | None = None
    check: DqResult | None = None
    error: str | None = None


@dataclass
class Watcher:
    """Polls one folder; :meth:`poll` loads and checks what is new since the last poll.

    Attributes:
        engine: Engine on a migrated database, as the owner role.
        folder: The folder to watch; files directly in it, not in subfolders.
        discipline: Discipline of every model loaded from the folder.
        rules: The data quality rules, already synchronised into ``dq_rule``.
        project_key: The project every model loaded from the folder belongs to.
        geometry: Measure the shapes as well (``ingest --geometry``).
    """

    engine: Engine
    folder: Path
    discipline: str
    rules: Sequence[Rule]
    project_key: str | None = None
    geometry: bool = False
    _done: dict[str, Version] = field(default_factory=dict)
    _pending: dict[str, Version] = field(default_factory=dict)

    def poll(self, *, settle: bool = True) -> list[WatchEvent]:
        """Load and check each file that is new or changed and, with ``settle``, stable.

        Args:
            settle: Take a file only once two polls have seen the same version of it.
                False takes every file as it is now.

        Returns:
            One event per file loaded, skipped as already loaded, or failed.
        """
        events = []
        for path in sorted(p for p in self.folder.iterdir() if p.suffix.lower() == ".ifc"):
            if not path.is_file():
                continue
            stat = path.stat()
            version = (stat.st_size, stat.st_mtime_ns)
            if self._done.get(path.name) == version:
                continue
            if settle and self._pending.get(path.name) != version:
                self._pending[path.name] = version
                continue
            self._done[path.name] = version
            event = self._load(path)
            if event is not None:
                events.append(event)
        return events

    def _load(self, path: Path) -> WatchEvent | None:
        """Load one file and check the model; ``None`` when the bytes are already loaded."""
        try:
            sha256 = sha256_file(path)
            with self.engine.connect() as connection:
                if connection.execute(
                    text("SELECT 1 FROM ifc.model WHERE file_sha256 = :s"), {"s": sha256}
                ).scalar():
                    return None
                previous = connection.execute(
                    text(
                        "SELECT max(model_id) FROM ifc.v_model_current "
                        "WHERE file_name = :name AND discipline = :discipline"
                    ),
                    {"name": path.name, "discipline": self.discipline},
                ).scalar()
            result = ingest_file(
                self.engine,
                path,
                self.discipline,
                project_key=self.project_key,
                replaces=previous,
                geometry=self.geometry,
            )
        except IngestError as exc:
            return WatchEvent(path=path, error=f"ingest run {exc.run_id}: {exc}")
        except OSError as exc:
            return WatchEvent(path=path, error=f"cannot read it: {exc.strerror or exc}")
        try:
            check = check_model(self.engine, result.model_id, self.rules)
        except DqRunError as exc:
            return WatchEvent(path=path, ingest=result, error=f"dq run {exc.run_id}: {exc}")
        return WatchEvent(path=path, ingest=result, check=check)
