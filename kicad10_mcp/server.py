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
    netclass_tools,
    placement_tools,
    project_tools,
    read_tools,
    routing_tools,
    schematic_tools,
    system_tools,
    view_tools,
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
- Placement: prefer place_near_pad / place_relative / place_on_edge / arrange_row
  over raw coordinates, then check_placement and snapshot_board to see the result.
- Routing: prefer route_pads (connect pads by name) and add_track_path over
  add_track. Widths and via sizes default to the net's netclass. Read the
  "warnings" in every result and fix shorts/clearance problems before moving on.
- Track sizing: calc_track_width gives the IPC-2221 width for a current.
  configure_netclasses writes classes (width from current_a) and net
  assignments to the .kicad_pro; the project must be closed in KiCad first.
- For anything not covered by a dedicated tool, use execute_kipy to run arbitrary
  kipy Python against the live document (the full-control escape hatch).
"""

mcp = FastMCP("kicad", instructions=INSTRUCTIONS)

for module in (
    system_tools,
    read_tools,
    edit_tools,
    placement_tools,
    create_tools,
    routing_tools,
    net_layer_tools,
    netclass_tools,
    project_tools,
    schematic_tools,
    export_tools,
    view_tools,
    exec_tools,
):
    module.register(mcp)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
