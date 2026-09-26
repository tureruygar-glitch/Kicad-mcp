"""Full-control MCP server for KiCad 10.

Wraps the KiCad IPC API (kicad-python / ``kipy``) and ``kicad-cli`` to expose
the PCB editor, schematic editor, project settings, nets, layers, design data,
fabrication exports, and a raw scripting escape hatch as MCP tools.
"""

__version__ = "0.1.0"
