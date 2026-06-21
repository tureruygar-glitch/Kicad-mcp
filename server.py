"""FastMCP server exposing full control over KiCad 10.

Registers every tool module on a single :class:`FastMCP` instance and runs it
over stdio. Launch with ``python -m kicad10_mcp`` or the ``kicad10-mcp`` script.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as a loose script (e.g. ``python run_server.py``) by ensuring the
# package's parent directory is importable.
_pkg_parent = str(Path(__file__).resolve().parent.parent)
if _pkg_parent not in sys.path:
    sys.path.insert(0, _pkg_parent)

from mcp.server.fastmcp import FastMCP  # noqa: E402

from kicad10_mcp import (  # noqa: E402
    create_tools,
    edit_tools,
    exec_tools,
    export_tools,
    net_layer_tools,
    project_tools,
    read_tools,
    schematic_tools,
    system_tools,
)

INSTRUCTIONS = """\
Full control over a running KiCad 10 instance via its IPC API plus kicad-cli.

Conventions:
- All positions, sizes, and widths are in MILLIMETRES; angles are in DEGREES.
- Layers are named like F.Cu, B.Cu, In1.Cu, Edge.Cuts, F.SilkS, F.Mask, F.Paste,
  F.Fab. Call list_board_layers to see what is enabled on the current board.
- KiCad must be open with the API server enabled (Preferences > Plugins >
  'Enable the KiCad API server'). Most tools act on the currently OPEN board or
  schematic; open the relevant document first.
- Board edits are grouped into single undo steps. Call save_board to persist to
  disk; export tools save automatically unless told otherwise.
- For anything not covered by a dedicated tool, use execute_kipy to run arbitrary
  kipy Python against the live document (the full-control escape hatch).
"""

mcp = FastMCP("kicad", instructions=INSTRUCTIONS)

for module in (
    system_tools,
    read_tools,
    edit_tools,
    create_tools,
    net_layer_tools,
    project_tools,
    schematic_tools,
    export_tools,
    exec_tools,
):
    module.register(mcp)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
