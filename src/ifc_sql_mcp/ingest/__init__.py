"""Turn an IFC file into rows for the ``ifc`` schema, in three stages.

1. **Extract** (``extract.py``) reads the file with IfcOpenShell into plain values
   (``raw.py``). It is the only module that imports IfcOpenShell, so the library's
   partial typing stays behind one boundary.
2. **Transform** (``transform.py`` and the modules it calls) is pure: it takes those
   values and returns frozen records (``records.py``) that satisfy every constraint in
   ``db/schema.py``, keyed by GlobalId and material name rather than database ids.
3. **Load** inserts the records (Phase 4).

Nothing that cannot be represented stops a load. Such values are dropped or left NULL
and reported as structured :class:`~ifc_sql_mcp.ingest.issues.Issue` objects, which
the data quality stage turns into findings.
"""
