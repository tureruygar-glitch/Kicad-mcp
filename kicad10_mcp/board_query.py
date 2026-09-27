"""Board lookups and plain-geometry helpers shared by placement and routing tools.

Everything here works in millimetres. Rectangles are ``(x_min, y_min, x_max,
y_max)`` tuples and points are ``(x, y)`` tuples; KiCad's Y axis points down.
The geometry is deliberately approximate (pads and courtyards are treated as
axis-aligned boxes) - it is meant to catch obvious mistakes quickly, not to
replace KiCad's DRC.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from kicad10_mcp.helpers import _try, fp_reference, layer_to_name, nm_to_mm

Rect = tuple[float, float, float, float]
Point = tuple[float, float]

DEFAULT_TRACK_WIDTH_MM = 0.25
DEFAULT_CLEARANCE_MM = 0.2
DEFAULT_VIA_DIAMETER_MM = 0.6
DEFAULT_VIA_DRILL_MM = 0.3


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

def find_footprint(board, reference: str):
    fps = list(board.get_footprints())
    for fp in fps:
        if fp_reference(fp) == reference:
            return fp
    import difflib

    close = difflib.get_close_matches(reference, [fp_reference(f) for f in fps], n=5)
    hint = f" Did you mean: {', '.join(close)}?" if close else ""
    raise ValueError(f"Footprint '{reference}' not found on the board.{hint}")


def footprint_pads(fp) -> list:
    return list(_try(lambda: fp.definition.pads, []) or [])


def find_pad(board, reference: str, pad_number: str):
    fp = find_footprint(board, reference)
    pads = footprint_pads(fp)
    for pad in pads:
        if str(pad.number) == str(pad_number):
            return fp, pad
    numbers = sorted({str(p.number) for p in pads})
    raise ValueError(
        f"Pad '{pad_number}' not found on {reference}. Available pads: {', '.join(numbers)}"
    )


def pad_net_name(pad) -> str:
    return _try(lambda: pad.net.name, "") or ""


def pad_xy(pad) -> Point:
    return nm_to_mm(pad.position.x), nm_to_mm(pad.position.y)


def pad_layers(pad) -> set[str]:
    """Copper layer names a pad exists on (empty set if unknown)."""
    layers = _try(lambda: list(pad.padstack.layers), []) or []
    return {layer_to_name(l) for l in layers if layer_to_name(l).endswith(".Cu")}


def pad_is_through_hole(pad) -> bool:
    from kipy.proto.board.board_types_pb2 import PadType

    return _try(lambda: pad.pad_type == PadType.PT_PTH, False)


def pad_half_size(pad) -> Point:
    size = _try(lambda: pad.padstack.copper_layers[0].size, None)
    if size is None:
        return 0.5, 0.5
    w, h = nm_to_mm(size.x), nm_to_mm(size.y)
    # KiCad reports the pad's absolute orientation (footprint rotation included).
    angle = _try(lambda: pad.padstack.angle.degrees, 0.0) or 0.0
    if round(angle) % 180 == 90:
        w, h = h, w
    elif round(angle) % 90 != 0:
        # Rotated off-axis: use the circumscribed square.
        w = h = math.hypot(w, h)
    return w / 2, h / 2


def pad_rect(pad) -> Rect:
    x, y = pad_xy(pad)
    hw, hh = pad_half_size(pad)
    return x - hw, y - hh, x + hw, y + hh


# ---------------------------------------------------------------------------
# Net classes
# ---------------------------------------------------------------------------

@dataclass
class NetRules:
    netclass: str
    track_width_mm: float
    clearance_mm: float
    via_diameter_mm: float
    via_drill_mm: float


def net_rules(board, net) -> NetRules:
    """Netclass values for ``net`` with sane fallbacks for unset fields."""
    nc = None
    if net is not None:
        classes = _try(lambda: board.get_netclass_for_nets(net), {}) or {}
        nc = classes.get(net.name) or (next(iter(classes.values())) if classes else None)

    def val(attr: str, default: float) -> float:
        v = _try(lambda: getattr(nc, attr), None) if nc is not None else None
        return nm_to_mm(v) if isinstance(v, int) and v > 0 else default

    return NetRules(
        netclass=_try(lambda: nc.name, "") if nc is not None else "",
        track_width_mm=val("track_width", DEFAULT_TRACK_WIDTH_MM),
        clearance_mm=val("clearance", DEFAULT_CLEARANCE_MM),
        via_diameter_mm=val("via_diameter", DEFAULT_VIA_DIAMETER_MM),
        via_drill_mm=val("via_drill", DEFAULT_VIA_DRILL_MM),
    )


# ---------------------------------------------------------------------------
# Shapes -> points / boxes
# ---------------------------------------------------------------------------

def _vxy(v) -> Point:
    return nm_to_mm(v.x), nm_to_mm(v.y)


def shape_points(shape) -> list[Point]:
    """Points that bound a graphic shape (circles contribute their bounding box)."""
    pts: list[Point] = []
    center = _try(lambda: _vxy(shape.center), None)
    radius_pt = _try(lambda: _vxy(shape.radius_point), None)
    if center is not None and radius_pt is not None:
        r = math.dist(center, radius_pt)
        return [(center[0] - r, center[1] - r), (center[0] + r, center[1] + r)]
    for attr in ("start", "mid", "end", "top_left", "bottom_right"):
        p = _try(lambda a=attr: _vxy(getattr(shape, a)), None)
        if p is not None:
            pts.append(p)
    for poly in _try(lambda: list(shape.polygons), []) or []:
        for node in _try(lambda: list(poly.outline), []) or []:
            if _try(lambda: node.has_point, False):
                pts.append(_vxy(node.point))
    return pts


def bbox_of(points: Iterable[Point]) -> Optional[Rect]:
    pts = list(points)
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def merge_rects(rects: Iterable[Rect]) -> Optional[Rect]:
    rs = list(rects)
    if not rs:
        return None
    return (min(r[0] for r in rs), min(r[1] for r in rs),
            max(r[2] for r in rs), max(r[3] for r in rs))


def footprint_courtyard(board, fp) -> Rect:
    """Courtyard box of a footprint; falls back to pads, then KiCad's bounding box."""
    from kipy.proto.board.board_types_pb2 import BoardLayer

    crt = {BoardLayer.BL_F_CrtYd, BoardLayer.BL_B_CrtYd}
    shapes = [s for s in _try(lambda: fp.definition.shapes, []) or []
              if _try(lambda s=s: s.layer, None) in crt]
    box = bbox_of(p for s in shapes for p in shape_points(s))
    if box is not None:
        return box
    box = merge_rects(pad_rect(p) for p in footprint_pads(fp))
    if box is not None:
        return box
    b = _try(lambda: board.get_item_bounding_box(fp), None)
    if b is not None:
        x, y = nm_to_mm(b.pos.x), nm_to_mm(b.pos.y)
        return x, y, x + nm_to_mm(b.size.x), y + nm_to_mm(b.size.y)
    x, y = _vxy(fp.position)
    return x - 0.5, y - 0.5, x + 0.5, y + 0.5


def footprint_side(fp) -> str:
    from kipy.proto.board.board_types_pb2 import BoardLayer

    return "back" if _try(lambda: fp.layer, None) == BoardLayer.BL_B_Cu else "front"


def board_outline(board) -> Optional[Rect]:
    from kipy.proto.board.board_types_pb2 import BoardLayer

    shapes = [s for s in _try(lambda: board.get_shapes(), []) or []
              if _try(lambda s=s: s.layer, None) == BoardLayer.BL_Edge_Cuts]
    return bbox_of(p for s in shapes for p in shape_points(s))


# ---------------------------------------------------------------------------
# Plain geometry
# ---------------------------------------------------------------------------

def rects_overlap(a: Rect, b: Rect, gap: float = 0.0) -> bool:
    return (a[0] < b[2] + gap and b[0] < a[2] + gap
            and a[1] < b[3] + gap and b[1] < a[3] + gap)


def rect_inside(inner: Rect, outer: Rect) -> bool:
    return (inner[0] >= outer[0] and inner[1] >= outer[1]
            and inner[2] <= outer[2] and inner[3] <= outer[3])


def point_segment_distance(p: Point, a: Point, b: Point) -> float:
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / length_sq))
    return math.dist(p, (ax + t * dx, ay + t * dy))


def _orient(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    d1, d2 = _orient(c, d, a), _orient(c, d, b)
    d3, d4 = _orient(a, b, c), _orient(a, b, d)
    return (d1 * d2 < 0) and (d3 * d4 < 0)


def segment_segment_distance(a: Point, b: Point, c: Point, d: Point) -> float:
    if segments_intersect(a, b, c, d):
        return 0.0
    return min(point_segment_distance(a, c, d), point_segment_distance(b, c, d),
               point_segment_distance(c, a, b), point_segment_distance(d, a, b))


def segment_rect_distance(a: Point, b: Point, r: Rect) -> float:
    x0, y0, x1, y1 = r
    if x0 <= a[0] <= x1 and y0 <= a[1] <= y1:
        return 0.0
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return min(segment_segment_distance(a, b, corners[i], corners[(i + 1) % 4])
               for i in range(4))


# ---------------------------------------------------------------------------
# Ratsnest
# ---------------------------------------------------------------------------

def _pads_by_net(board, exclude_nets: Iterable[str]) -> dict[str, list[tuple[Any, Point, str]]]:
    skip = set(exclude_nets)
    by_net: dict[str, list[tuple[Any, Point, str]]] = {}
    for fp in board.get_footprints():
        ref = fp_reference(fp)
        for pad in footprint_pads(fp):
            name = pad_net_name(pad)
            if not name or name in skip or name.startswith("unconnected-"):
                continue
            by_net.setdefault(name, []).append((pad, pad_xy(pad), f"{ref}.{pad.number}"))
    return by_net


def unrouted_airwires(board, exclude_nets: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Airwires for the connections that copper does NOT make yet.

    Pads already joined by tracks, vias, or zone fills (KiCad's own connectivity,
    GetConnectedItems - KiCad 10.0.1+) count as one node; the shortest wires
    between the remaining groups are returned. Falls back to plain ratsnest if
    KiCad can't report connectivity.
    """
    from kipy.proto.common.types import KiCadObjectType

    wires: list[dict[str, Any]] = []
    for net, nodes in _pads_by_net(board, exclude_nets).items():
        n = len(nodes)
        if n < 2:
            continue
        parent = list(range(n))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        index = {nodes[i][0].id.value: i for i in range(n)}
        grouped: set[int] = set()
        for i in range(n):
            if i in grouped:
                continue
            try:
                joined = board.get_connected_items(nodes[i][0], types=[KiCadObjectType.KOT_PCB_PAD])
            except Exception:  # noqa: BLE001 - older KiCad: treat every pad as its own node
                joined = []
            for item in joined:
                j = index.get(item.id.value)
                if j is not None:
                    parent[find(j)] = find(i)
                    grouped.add(j)
        # Kruskal over pad pairs; pads in one copper group are already merged.
        pairs = sorted((math.dist(nodes[a][1], nodes[b][1]), a, b)
                       for a in range(n) for b in range(a + 1, n))
        for d, a, b in pairs:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra
                wires.append({"net": net, "from": nodes[a][2], "to": nodes[b][2],
                              "start": nodes[a][1], "end": nodes[b][1],
                              "length_mm": round(d, 3)})
    return wires


def ratsnest(board, exclude_nets: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Minimum-spanning-tree airwires between pads of each net.

    Ignores existing tracks and zones, so it measures how "tangled" the
    placement is rather than what is still unrouted (see unrouted_airwires).
    """
    by_net = {net: [(xy, label) for _pad, xy, label in nodes]
              for net, nodes in _pads_by_net(board, exclude_nets).items()}

    wires: list[dict[str, Any]] = []
    for net, nodes in by_net.items():
        if len(nodes) < 2:
            continue
        # Prim's algorithm; nets are small so O(n^2) is fine.
        in_tree = [False] * len(nodes)
        best = [math.inf] * len(nodes)
        parent = [-1] * len(nodes)
        best[0] = 0.0
        for _ in range(len(nodes)):
            u = min((i for i in range(len(nodes)) if not in_tree[i]), key=lambda i: best[i])
            in_tree[u] = True
            if parent[u] >= 0:
                p = parent[u]
                wires.append({
                    "net": net,
                    "from": nodes[p][1], "to": nodes[u][1],
                    "start": nodes[p][0], "end": nodes[u][0],
                    "length_mm": round(best[u], 3),
                })
            for v in range(len(nodes)):
                if not in_tree[v]:
                    d = math.dist(nodes[u][0], nodes[v][0])
                    if d < best[v]:
                        best[v], parent[v] = d, u
    return wires
