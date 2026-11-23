"""The viewer: 3D scenes of the loaded models, and what the MCP server's calls found (ADR 14).

``ifc-sql ingest --viewer`` builds a scene per model while the file is open: ``scene.py``
turns IfcOpenShell's meshes into chunks, storey by storey, and ``glb.py`` writes each
chunk as glTF binary. Both are pure, so the bytes depend on the file alone.

``ifc-sql view`` serves them (``http.py``, behind ``security.py``), and lists the calls
of every ``ifc-sql serve`` with ``IFC_SQL_VIEWER_FEED=1``: ``event.py`` is the
notification each call sends, ``listener.py`` receives them, ``feed.py`` checks and
keeps them, and ``selection.py`` resolves what each found with the tools' own SQL.
``ifc-sql demo`` plays the questions of ``demo_script.py`` through the real server
(``demo.py``).
"""
