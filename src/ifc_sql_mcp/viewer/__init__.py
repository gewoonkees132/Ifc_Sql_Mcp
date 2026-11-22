"""The viewer: 3D scenes of the loaded models (ADR 14).

``ifc-sql ingest --viewer`` builds a scene per model while the file is open: ``scene.py``
turns IfcOpenShell's meshes into chunks, storey by storey, and ``glb.py`` writes each
chunk as glTF binary. Both are pure, so the bytes depend on the file alone.
"""
