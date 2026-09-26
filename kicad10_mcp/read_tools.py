"""Read-only inspection tools for the open PCB."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from kicad10_mcp.connection import require_board
from kicad10_mcp.helpers import (
    fp_reference,
    item_id,
    layer_to_name,
    nm_to_mm,
    serialize_dimension,
    serialize_footprint,
    serialize_pad,
    serialize_shape,
    serialize_text,
    serialize_track,
    serialize_via,
    serialize_zone,
    vec_dict,
)


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def get_board_summary() -> dict[str, Any]:
        """Return counts and basic metadata for the open PCB.

        Includes element counts, copper layer count, board file name, and
        bounding box extents in millimetres.
        """
        board = require_board()
        footprints = board.get_footprints()
        tracks = board.get_tracks()
        return {
            "file": board.name,
            "footprints": len(footprints),
            "tracks_and_arcs": len(tracks),
            "vias": len(board.get_vias()),
            "pads": len(board.get_pads()),
            "zones": len(board.get_zones()),
            "shapes": len(board.get_shapes()),
            "text_objects": len(board.get_text()),
            "dimensions": len(board.get_dimensions()),
            "nets": len(board.get_nets()),
            "copper_layers": board.get_copper_layer_count(),
        }

    @mcp.tool()
    def list_footprints(reference_filter: str = "", value_filter: str = "",
                        include_pads: bool = False) -> list[dict[str, Any]]:
        """List footprints on the open PCB.

        Args:
            reference_filter: Case-insensitive substring match on the reference (e.g. 'R', 'U1').
            value_filter: Case-insensitive substring match on the component value.
            include_pads: Include each footprint's pads (number, net, position).
        """
        board = require_board()
        out: list[dict[str, Any]] = []
        for fp in board.get_footprints():
            ref = fp_reference(fp)
            val = ""
            try:
                val = fp.value_field.text.value
            except Exception:  # noqa: BLE001
                pass
            if reference_filter and reference_filter.lower() not in ref.lower():
                continue
            if value_filter and value_filter.lower() not in (val or "").lower():
                continue
            out.append(serialize_footprint(fp, include_pads=include_pads))
        return out

    @mcp.tool()
    def get_footprint(reference: str, include_pads: bool = True) -> dict[str, Any]:
        """Return full detail for a single footprint by reference designator.

        Args:
            reference: Reference designator, e.g. 'U1'.
            include_pads: Include the footprint's pads.
        """
        board = require_board()
        for fp in board.get_footprints():
            if fp_reference(fp) == reference:
                return serialize_footprint(fp, include_pads=include_pads)
        raise ValueError(f"Footprint '{reference}' not found on the board.")

    @mcp.tool()
    def list_pads(reference: str = "", net_filter: str = "") -> list[dict[str, Any]]:
        """List pads on the board, optionally filtered by footprint reference or net.

        Args:
            reference: Only pads belonging to this footprint reference.
            net_filter: Case-insensitive substring match on the pad's net name.
        """
        board = require_board()
        if reference:
            for fp in board.get_footprints():
                if fp_reference(fp) == reference:
                    from kipy.board_types import Pad

                    pads = [serialize_pad(p) for p in fp.definition.items
                            if isinstance(p, Pad)]
                    return _filter_net(pads, net_filter)
            raise ValueError(f"Footprint '{reference}' not found.")
        pads = [serialize_pad(p) for p in board.get_pads()]
        return _filter_net(pads, net_filter)

    @mcp.tool()
    def list_tracks(layer: str = "", net_filter: str = "") -> list[dict[str, Any]]:
        """List copper tracks and arc tracks on the board.

        Args:
            layer: Restrict to a layer name, e.g. 'F.Cu'.
            net_filter: Case-insensitive substring match on the net name.
        """
        board = require_board()
        out = [serialize_track(t) for t in board.get_tracks()]
        if layer:
            out = [t for t in out if t.get("layer") == layer]
        return _filter_net(out, net_filter)

    @mcp.tool()
    def list_vias(net_filter: str = "") -> list[dict[str, Any]]:
        """List vias on the board, optionally filtered by net."""
        board = require_board()
        return _filter_net([serialize_via(v) for v in board.get_vias()], net_filter)

    @mcp.tool()
    def list_zones() -> list[dict[str, Any]]:
        """List copper zones, rule areas, and graphic zones on the board."""
        board = require_board()
        return [serialize_zone(z) for z in board.get_zones()]

    @mcp.tool()
    def list_shapes(layer: str = "") -> list[dict[str, Any]]:
        """List graphic shapes (lines, arcs, circles, rectangles, polygons) on the board.

        Args:
            layer: Restrict to a layer name, e.g. 'Edge.Cuts' for the board outline.
        """
        board = require_board()
        out = [serialize_shape(s) for s in board.get_shapes()]
        if layer:
            out = [s for s in out if s.get("layer") == layer]
        return out

    @mcp.tool()
    def get_board_outline() -> list[dict[str, Any]]:
        """Return all graphic shapes on the Edge.Cuts layer (the board outline)."""
        board = require_board()
        return [serialize_shape(s) for s in board.get_shapes()
                if layer_to_name(s.layer) == "Edge.Cuts"]

    @mcp.tool()
    def list_text() -> list[dict[str, Any]]:
        """List free text and text-box objects on the board."""
        board = require_board()
        return [serialize_text(t) for t in board.get_text()]

    @mcp.tool()
    def list_dimensions() -> list[dict[str, Any]]:
        """List dimension annotations on the board."""
        board = require_board()
        return [serialize_dimension(d) for d in board.get_dimensions()]

    @mcp.tool()
    def list_groups() -> list[dict[str, Any]]:
        """List item groups on the board with their member item IDs."""
        board = require_board()
        out: list[dict[str, Any]] = []
        for g in board.get_groups():
            out.append({
                "id": item_id(g),
                "name": getattr(g, "name", ""),
                "item_ids": [item_id(i) for i in g.items],
            })
        return out

    @mcp.tool()
    def get_bounding_box(item_ids: list[str], include_text: bool = False) -> list[dict[str, Any]]:
        """Return KiCad-computed bounding boxes for the given item IDs.

        Args:
            item_ids: Item KIID strings (from any list_* tool).
            include_text: Include child reference/value text in footprint boxes.
        """
        board = require_board()
        from kicad10_mcp.helpers import kiid

        items = list(board.get_items_by_id([kiid(i) for i in item_ids]))
        out: list[dict[str, Any]] = []
        for it in items:
            box = board.get_item_bounding_box(it, include_text=include_text)
            if box is None:
                out.append({"id": item_id(it), "bbox": None})
            else:
                out.append({
                    "id": item_id(it),
                    "position_mm": vec_dict(box.pos),
                    "size_mm": {"w_mm": nm_to_mm(box.size.x), "h_mm": nm_to_mm(box.size.y)},
                })
        return out


def _filter_net(items: list[dict[str, Any]], net_filter: str) -> list[dict[str, Any]]:
    if not net_filter:
        return items
    needle = net_filter.lower()
    return [i for i in items if needle in (i.get("net") or "").lower()]
