"""Tools that create new board items: tracks, vias, zones, graphics, text."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from kicad10_mcp.board_query import net_rules
from kicad10_mcp.connection import commit, require_board
from kicad10_mcp.helpers import find_net, item_id, mm_to_nm, name_to_layer, vmm
from kipy.geometry import PolygonWithHoles, PolyLineNode, Vector2


def _pt(p: Any) -> tuple[float, float]:
    if isinstance(p, dict):
        return float(p["x_mm"]), float(p["y_mm"])
    return float(p[0]), float(p[1])


def _polygon_from_points(points: list[Any]) -> PolygonWithHoles:
    if len(points) < 3:
        raise ValueError("A polygon/zone outline needs at least 3 points.")
    pwh = PolygonWithHoles()
    outline = pwh.outline
    for p in points:
        x, y = _pt(p)
        outline.append(PolyLineNode.from_point(vmm(x, y)))
    outline.closed = True
    return pwh


def register(mcp: FastMCP) -> None:

    # ----------------------------------------------------------------- routing

    @mcp.tool()
    def add_track(start_x_mm: float, start_y_mm: float, end_x_mm: float,
                  end_y_mm: float, width_mm: float | None = None, layer: str = "F.Cu",
                  net_name: str = "") -> dict[str, Any]:
        """Add a straight copper track segment. Prefer route_pads (connect pads by name)
        or add_track_path (multi-segment) - they avoid hand-copied coordinates.

        Args:
            start_x_mm, start_y_mm: Start point in mm.
            end_x_mm, end_y_mm: End point in mm.
            width_mm: Track width in mm; default is the net's netclass width.
            layer: Copper layer name, e.g. 'F.Cu' or 'B.Cu'.
            net_name: Net to assign (must exist on the board).
        """
        from kicad10_mcp.routing_tools import Snapshot, _check, create_path, dangling_ends

        board = require_board()
        net = find_net(board, net_name)
        rules = net_rules(board, net)
        width = width_mm if width_mm is not None else rules.track_width_mm
        pts = [(start_x_mm, start_y_mm), (end_x_mm, end_y_mm)]
        created = create_path(board, pts, width, layer, net, "Add track")
        snap = Snapshot(board)
        warnings = _check(snap, [(pts, layer, width)], net_name, rules.clearance_mm, created)
        warnings += dangling_ends(snap, pts, layer, net_name, {item_id(c) for c in created})
        return {"created_ids": [item_id(c) for c in created], "width_mm": width,
                "warnings": warnings}

    @mcp.tool()
    def add_arc_track(start_x_mm: float, start_y_mm: float, mid_x_mm: float,
                      mid_y_mm: float, end_x_mm: float, end_y_mm: float,
                      width_mm: float, layer: str = "F.Cu",
                      net_name: str = "") -> dict[str, Any]:
        """Add a curved (arc) copper track defined by start, mid, and end points.

        Args:
            start_x_mm, start_y_mm: Arc start in mm.
            mid_x_mm, mid_y_mm: A point on the arc between start and end, in mm.
            end_x_mm, end_y_mm: Arc end in mm.
            width_mm: Track width in mm.
            layer: Copper layer name.
            net_name: Optional net to assign.
        """
        from kipy.board_types import ArcTrack

        board = require_board()
        a = ArcTrack()
        a.start = vmm(start_x_mm, start_y_mm)
        a.mid = vmm(mid_x_mm, mid_y_mm)
        a.end = vmm(end_x_mm, end_y_mm)
        a.width = mm_to_nm(width_mm)
        a.layer = name_to_layer(layer)
        net = find_net(board, net_name)
        if net is not None:
            a.net = net
        with commit(board, "Add arc track"):
            created = board.create_items(a)
        return {"created_ids": [item_id(c) for c in created]}

    @mcp.tool()
    def add_via(x_mm: float, y_mm: float, diameter_mm: float | None = None,
                drill_mm: float | None = None, net_name: str = "") -> dict[str, Any]:
        """Add a through via at a position, sized from the net's netclass by default,
        and report clearance problems with other nets on the outer layers.

        Args:
            x_mm, y_mm: Via centre in mm.
            diameter_mm: Copper diameter in mm; default from netclass.
            drill_mm: Drill diameter in mm; default from netclass.
            net_name: Net to assign (must exist on the board).
        """
        from kicad10_mcp.routing_tools import Snapshot, clearance_conflicts, create_via

        board = require_board()
        net = find_net(board, net_name)
        rules = net_rules(board, net)
        dia = diameter_mm if diameter_mm is not None else rules.via_diameter_mm
        drill = drill_mm if drill_mm is not None else rules.via_drill_mm
        if drill >= dia:
            raise ValueError(f"Drill ({drill} mm) must be smaller than diameter ({dia} mm).")
        created = create_via(board, (x_mm, y_mm), dia, drill, net, "Add via")
        snap = Snapshot(board)
        ids = {item_id(c) for c in created}
        warnings = [w for layer in ("F.Cu", "B.Cu") for w in clearance_conflicts(
            snap, [((x_mm, y_mm), (x_mm, y_mm))], dia, layer, net_name,
            rules.clearance_mm, ids)]
        return {"created_ids": [item_id(c) for c in created], "diameter_mm": dia,
                "drill_mm": drill, "warnings": warnings}

    @mcp.tool()
    def add_zone(points: list[Any], layers: list[str], net_name: str = "",
                 name: str = "", priority: int = 0,
                 refill: bool = False) -> dict[str, Any]:
        """Add a copper zone (filled pour) bounded by a polygon outline.

        Args:
            points: Outline vertices, each {"x_mm": .., "y_mm": ..} or [x, y]. >= 3 points.
            layers: Layer names the zone exists on, e.g. ['F.Cu'].
            net_name: Net to connect the pour to (e.g. 'GND'). Empty for no net.
            name: Optional zone name.
            priority: Fill priority (higher fills first).
            refill: Refill all zones after creating (slower, reflects in editor).
        """
        from kipy.board_types import Zone

        board = require_board()
        z = Zone()
        z.layers = [name_to_layer(l) for l in layers]
        if name:
            z.name = name
        z.priority = int(priority)
        net = find_net(board, net_name)
        if net is not None:
            z.net = net
        z.outline = _polygon_from_points(points)
        with commit(board, "Add zone"):
            created = board.create_items(z)
        if refill:
            board.refill_zones()
        return {"created_ids": [item_id(c) for c in created], "refilled": refill}

    @mcp.tool()
    def add_zone_rect(x_min_mm: float, y_min_mm: float, x_max_mm: float,
                      y_max_mm: float, layers: list[str], net_name: str = "",
                      name: str = "", priority: int = 0,
                      refill: bool = False) -> dict[str, Any]:
        """Add a rectangular copper zone (convenience wrapper around add_zone).

        Args:
            x_min_mm, y_min_mm: Top-left corner in mm.
            x_max_mm, y_max_mm: Bottom-right corner in mm.
            layers: Layer names, e.g. ['F.Cu'].
            net_name: Net to connect (e.g. 'GND').
            name: Optional zone name.
            priority: Fill priority.
            refill: Refill zones after creating.
        """
        pts = [
            {"x_mm": x_min_mm, "y_mm": y_min_mm},
            {"x_mm": x_max_mm, "y_mm": y_min_mm},
            {"x_mm": x_max_mm, "y_mm": y_max_mm},
            {"x_mm": x_min_mm, "y_mm": y_max_mm},
        ]
        return add_zone(pts, layers, net_name=net_name, name=name,
                        priority=priority, refill=refill)

    @mcp.tool()
    def refill_zones() -> str:
        """Refill (recompute) all copper zones on the board. May take a few seconds."""
        require_board().refill_zones()
        return "All zones refilled."

    # ---------------------------------------------------------------- graphics

    @mcp.tool()
    def add_line(start_x_mm: float, start_y_mm: float, end_x_mm: float,
                 end_y_mm: float, layer: str = "F.SilkS",
                 width_mm: float = 0.15) -> dict[str, Any]:
        """Add a graphic line segment (silkscreen, fab, Edge.Cuts, etc.).

        Args:
            start_x_mm, start_y_mm: Start point in mm.
            end_x_mm, end_y_mm: End point in mm.
            layer: Layer name, e.g. 'F.SilkS' or 'Edge.Cuts'.
            width_mm: Line width in mm.
        """
        from kipy.board_types import BoardSegment

        board = require_board()
        seg = BoardSegment()
        seg.start = vmm(start_x_mm, start_y_mm)
        seg.end = vmm(end_x_mm, end_y_mm)
        seg.layer = name_to_layer(layer)
        attrs = seg.attributes
        attrs.stroke.width = mm_to_nm(width_mm)
        seg.attributes = attrs
        with commit(board, "Add line"):
            created = board.create_items(seg)
        return {"created_ids": [item_id(c) for c in created]}

    @mcp.tool()
    def add_rectangle(x_min_mm: float, y_min_mm: float, x_max_mm: float,
                      y_max_mm: float, layer: str = "F.SilkS",
                      width_mm: float = 0.15, filled: bool = False) -> dict[str, Any]:
        """Add a graphic rectangle.

        Args:
            x_min_mm, y_min_mm: Top-left corner in mm.
            x_max_mm, y_max_mm: Bottom-right corner in mm.
            layer: Layer name.
            width_mm: Outline width in mm.
            filled: Whether the rectangle is solid-filled.
        """
        from kipy.board_types import BoardRectangle

        board = require_board()
        r = BoardRectangle()
        r.top_left = vmm(x_min_mm, y_min_mm)
        r.bottom_right = vmm(x_max_mm, y_max_mm)
        r.layer = name_to_layer(layer)
        attrs = r.attributes
        attrs.stroke.width = mm_to_nm(width_mm)
        attrs.fill.filled = bool(filled)
        r.attributes = attrs
        with commit(board, "Add rectangle"):
            created = board.create_items(r)
        return {"created_ids": [item_id(c) for c in created]}

    @mcp.tool()
    def add_circle(center_x_mm: float, center_y_mm: float, radius_mm: float,
                   layer: str = "F.SilkS", width_mm: float = 0.15,
                   filled: bool = False) -> dict[str, Any]:
        """Add a graphic circle.

        Args:
            center_x_mm, center_y_mm: Centre in mm.
            radius_mm: Radius in mm.
            layer: Layer name.
            width_mm: Outline width in mm.
            filled: Whether the circle is solid-filled.
        """
        from kipy.board_types import BoardCircle

        board = require_board()
        c = BoardCircle()
        c.center = vmm(center_x_mm, center_y_mm)
        c.radius_point = vmm(center_x_mm + radius_mm, center_y_mm)
        c.layer = name_to_layer(layer)
        attrs = c.attributes
        attrs.stroke.width = mm_to_nm(width_mm)
        attrs.fill.filled = bool(filled)
        c.attributes = attrs
        with commit(board, "Add circle"):
            created = board.create_items(c)
        return {"created_ids": [item_id(x) for x in created]}

    @mcp.tool()
    def add_arc(start_x_mm: float, start_y_mm: float, mid_x_mm: float,
                mid_y_mm: float, end_x_mm: float, end_y_mm: float,
                layer: str = "F.SilkS", width_mm: float = 0.15) -> dict[str, Any]:
        """Add a graphic arc defined by start, mid, and end points.

        Args:
            start_x_mm, start_y_mm: Arc start in mm.
            mid_x_mm, mid_y_mm: Point on the arc, in mm.
            end_x_mm, end_y_mm: Arc end in mm.
            layer: Layer name.
            width_mm: Outline width in mm.
        """
        from kipy.board_types import BoardArc

        board = require_board()
        a = BoardArc()
        a.start = vmm(start_x_mm, start_y_mm)
        a.mid = vmm(mid_x_mm, mid_y_mm)
        a.end = vmm(end_x_mm, end_y_mm)
        a.layer = name_to_layer(layer)
        attrs = a.attributes
        attrs.stroke.width = mm_to_nm(width_mm)
        a.attributes = attrs
        with commit(board, "Add arc"):
            created = board.create_items(a)
        return {"created_ids": [item_id(c) for c in created]}

    @mcp.tool()
    def add_polygon(points: list[Any], layer: str = "F.SilkS",
                    width_mm: float = 0.15, filled: bool = True) -> dict[str, Any]:
        """Add a graphic polygon.

        Args:
            points: Vertices, each {"x_mm": .., "y_mm": ..} or [x, y]. >= 3 points.
            layer: Layer name.
            width_mm: Outline width in mm.
            filled: Whether the polygon is solid-filled.
        """
        from kipy.board_types import BoardPolygon

        board = require_board()
        poly = BoardPolygon()
        poly.layer = name_to_layer(layer)
        poly.polygons.append(_polygon_from_points(points))
        attrs = poly.attributes
        attrs.stroke.width = mm_to_nm(width_mm)
        attrs.fill.filled = bool(filled)
        poly.attributes = attrs
        with commit(board, "Add polygon"):
            created = board.create_items(poly)
        return {"created_ids": [item_id(c) for c in created]}

    @mcp.tool()
    def add_board_outline_rect(x_min_mm: float, y_min_mm: float, x_max_mm: float,
                               y_max_mm: float, width_mm: float = 0.1) -> dict[str, Any]:
        """Add a rectangular board outline on the Edge.Cuts layer.

        Args:
            x_min_mm, y_min_mm: Top-left corner in mm.
            x_max_mm, y_max_mm: Bottom-right corner in mm.
            width_mm: Outline width in mm.
        """
        return add_rectangle(x_min_mm, y_min_mm, x_max_mm, y_max_mm,
                             layer="Edge.Cuts", width_mm=width_mm, filled=False)

    @mcp.tool()
    def add_text(text: str, x_mm: float, y_mm: float, layer: str = "F.SilkS",
                 size_mm: float = 1.0, thickness_mm: float = 0.15,
                 angle_deg: float = 0.0) -> dict[str, Any]:
        """Add free text to the board.

        Args:
            text: The text string.
            x_mm, y_mm: Anchor position in mm.
            layer: Layer name, e.g. 'F.SilkS'.
            size_mm: Glyph height (and width) in mm.
            thickness_mm: Stroke thickness in mm.
            angle_deg: Rotation in degrees.
        """
        from kipy.board_types import BoardText

        board = require_board()
        bt = BoardText()
        bt.value = text
        bt.position = vmm(x_mm, y_mm)
        bt.layer = name_to_layer(layer)
        attrs = bt.attributes
        attrs.size = Vector2.from_xy_mm(float(size_mm), float(size_mm))
        attrs.stroke_width = mm_to_nm(thickness_mm)
        attrs.angle = float(angle_deg)
        bt.attributes = attrs
        with commit(board, "Add text"):
            created = board.create_items(bt)
        return {"created_ids": [item_id(c) for c in created]}
