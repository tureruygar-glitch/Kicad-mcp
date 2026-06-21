"""Net, net class, connectivity, layer, and stackup tools."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from kicad10_mcp.connection import require_board
from kicad10_mcp.helpers import (
    _try,
    find_net,
    item_id,
    kiid,
    layer_to_name,
    name_to_layer,
    nm_to_mm,
)


def register(mcp: FastMCP) -> None:

    # -------------------------------------------------------------------- nets

    @mcp.tool()
    def list_nets(netclass_filter: str = "") -> list[dict[str, Any]]:
        """List all nets on the board, optionally filtered by net class name.

        Args:
            netclass_filter: Restrict to nets belonging to this net class.
        """
        board = require_board()
        nets = (board.get_nets(netclass_filter) if netclass_filter
                else board.get_nets())
        return [{"name": n.name} for n in nets]

    @mcp.tool()
    def list_netclasses() -> list[dict[str, Any]]:
        """List the project's net classes and their key parameters (mm)."""
        board = require_board()
        project = board.get_project()
        out: list[dict[str, Any]] = []
        for nc in project.get_net_classes():
            entry: dict[str, Any] = {
                "name": _try(lambda nc=nc: nc.name, ""),
                "priority": _try(lambda nc=nc: nc.priority, None),
            }
            for attr in ("clearance", "track_width", "via_diameter", "via_drill",
                         "microvia_diameter", "microvia_drill",
                         "diff_pair_track_width", "diff_pair_gap"):
                val = _try(lambda nc=nc, a=attr: getattr(nc, a), None)
                if isinstance(val, int):
                    entry[attr + "_mm"] = nm_to_mm(val)
                elif val is not None:
                    entry[attr] = val
            out.append(entry)
        return out

    @mcp.tool()
    def get_items_by_net(net_name: str) -> dict[str, Any]:
        """Summarise every board item belonging to a net (counts by type + IDs).

        Args:
            net_name: The net name, e.g. 'GND'.
        """
        board = require_board()
        net = find_net(board, net_name)
        items = list(board.get_items_by_net(net))
        by_type: dict[str, int] = {}
        for it in items:
            by_type[type(it).__name__] = by_type.get(type(it).__name__, 0) + 1
        return {
            "net": net_name,
            "total": len(items),
            "by_type": by_type,
            "item_ids": [item_id(it) for it in items],
        }

    @mcp.tool()
    def get_connected_items(item_id_str: str) -> dict[str, Any]:
        """Find items copper-connected to a given item (by KIID).

        Args:
            item_id_str: KIID string of the source track/via/pad.
        """
        board = require_board()
        items = list(board.get_connected_items(kiid(item_id_str)))
        by_type: dict[str, int] = {}
        for it in items:
            by_type[type(it).__name__] = by_type.get(type(it).__name__, 0) + 1
        return {
            "source": item_id_str,
            "connected_count": len(items),
            "by_type": by_type,
            "item_ids": [item_id(it) for it in items],
        }

    # ------------------------------------------------------------------ layers

    @mcp.tool()
    def list_board_layers() -> dict[str, Any]:
        """Report copper layer count plus enabled, visible, and active layers (names)."""
        board = require_board()
        return {
            "copper_layer_count": board.get_copper_layer_count(),
            "enabled_layers": [layer_to_name(l) for l in board.get_enabled_layers()],
            "visible_layers": [layer_to_name(l) for l in board.get_visible_layers()],
            "active_layer": layer_to_name(board.get_active_layer()),
        }

    @mcp.tool()
    def set_active_layer(layer: str) -> str:
        """Set the active drawing layer in the PCB editor.

        Args:
            layer: Layer name, e.g. 'B.Cu'.
        """
        board = require_board()
        board.set_active_layer(name_to_layer(layer))
        return f"Active layer set to {layer}."

    @mcp.tool()
    def set_visible_layers(layers: list[str]) -> str:
        """Set exactly which layers are visible in the editor.

        Args:
            layers: Full list of layer names to make visible (others are hidden).
        """
        board = require_board()
        board.set_visible_layers([name_to_layer(l) for l in layers])
        return f"Visible layers set to: {', '.join(layers)}."

    @mcp.tool()
    def set_copper_layer_count(count: int, confirm: bool = False) -> str:
        """Set the number of copper layers (must be even, >= 2).

        WARNING: removing layers deletes any content on them and cannot be undone.
        Pass confirm=True to proceed.

        Args:
            count: New copper layer count.
            confirm: Must be True to apply the change.
        """
        if not confirm:
            raise ValueError(
                "Refusing to change copper layer count without confirm=True "
                "(this can irreversibly delete content on removed layers)."
            )
        board = require_board()
        existing = [l for l in board.get_enabled_layers()
                    if "_Cu" not in layer_to_name(l).replace(".", "_")]
        result = board.set_enabled_layers(int(count), existing)
        return f"Copper layer count set to {count}. Now {len(result)} layers enabled."

    @mcp.tool()
    def get_stackup() -> list[dict[str, Any]]:
        """Return the board stackup: ordered layers with type, thickness, material."""
        board = require_board()
        stackup = board.get_stackup()
        out: list[dict[str, Any]] = []
        for layer in stackup.layers:
            out.append({
                "user_name": _try(lambda layer=layer: layer.user_name, ""),
                "layer": _try(lambda layer=layer: layer_to_name(layer.layer), ""),
                "type": _try(lambda layer=layer: int(layer.type), None),
                "thickness_mm": _try(lambda layer=layer: nm_to_mm(layer.thickness), 0),
                "material": _try(lambda layer=layer: layer.material_name, ""),
                "enabled": _try(lambda layer=layer: layer.enabled, True),
            })
        return out

    @mcp.tool()
    def get_design_rules() -> dict[str, Any]:
        """Return the board's minimum design-rule constraints in mm (best effort).

        Falls back to an explanatory message if the running KiCad build does not
        expose design rules over the API.
        """
        board = require_board()
        try:
            from kipy.board_rules import BoardDesignRulesResponse
            from kipy.proto.board import board_commands_pb2

            cmd = board_commands_pb2.GetBoardDesignRules()
            cmd.board.CopyFrom(board.document)
            resp = board.client.send(
                cmd, board_commands_pb2.BoardDesignRulesResponse
            )
            constraints = BoardDesignRulesResponse(resp).rules.constraints
        except Exception as exc:  # noqa: BLE001
            return {"error": f"Design rules not available over the API: {exc}. "
                             "Use execute_kipy or the kicad-cli DRC tools instead."}
        fields = [
            "min_clearance", "min_track_width", "min_via_size", "min_via_annular_width",
            "min_through_drill", "min_microvia_size", "min_microvia_drill",
            "min_connection_width", "copper_edge_clearance", "hole_clearance",
            "hole_to_hole_min", "silk_clearance",
        ]
        return {f + "_mm": nm_to_mm(_try(lambda f=f: getattr(constraints, f), 0))
                for f in fields}
