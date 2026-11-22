"""The MCP server: tools, resources and a prompt over the ``ifc`` schema, read-only.

``app.build_server`` wires everything into an SDK ``MCPServer``; ``ifc-sql serve`` runs it
over stdio. The design, and why each part is the way it is, is in ADR 6.
"""
