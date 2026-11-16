"""Turn an IFC file into rows for the ``ifc`` schema, in three stages.

1. **Extract** (``extract.py``) reads the file with IfcOpenShell into plain values
   (``raw.py``). It is the only module that imports IfcOpenShell, so the library's
   partial typing stays behind one boundary.
2. **Transform** (``transform.py`` and the modules it calls) is pure: it takes those
   values and returns frozen records (``records.py``) that satisfy every constraint in
   ``db/schema.py``, keyed by GlobalId and material name rather than database ids.
3. **Load** (``load.py``) writes the records of one model in one transaction, mapping
   those keys to the generated ids.

``pipeline.py`` runs the three for one file and keeps the ``ingestion_run`` audit trail:
a file whose exact bytes are already loaded is skipped, and a failed run is recorded
with its error while nothing of the model is written (ADR 4).

Nothing that cannot be represented stops a load. Such values are dropped or left NULL
and reported as structured :class:`~ifc_sql_mcp.ingest.issues.Issue` objects, which
are stored in ``ifc.ingest_issue`` with the model for the data quality stage.
"""
