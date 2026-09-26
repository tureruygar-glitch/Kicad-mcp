"""Pad-aware routing: connect pads by name, draw multi-segment paths, and check
the result against nearby copper.

The coordinate-only ``add_track`` makes the model copy pad positions by hand and
gives no feedback when a track misses its pad or runs into another net. These
tools look the geometry up themselves, take widths and via sizes from the net's
netclass, and report clearance conflicts and dangling ends after every change.
"""

from __future__ import annotations

import math
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from kicad10_mcp.board_query import (
    find_pad,
    net_rules,
    pad_is_through_hole,
    pad_layers,
    pad_net_name,
    pad_rect,
    pad_xy,
    point_segment_distance,
    segment_rect_distance,
    segment_segment_distance,
)
from kicad10_mcp.connection import commit, require_board
from kicad10_mcp.helpers import (
    _try,
    find_net,
    fp_reference,
    item_id,
    layer_to_name,
    mm_to_nm,
    name_to_layer,
    nm_to_mm,
    vmm,
)

Point = tuple[float, float]
_EPS = 1e-4  # mm; ignore rounding noise from nm conversion


def _xy(v) -> Point:
    return nm_to_mm(v.x), nm_to_mm(v.y)


def _pt(p: Any) -> Point:
    if isinstance(p, dict):
        return float(p["x_mm"]), float(p["y_mm"])
    return float(p[0]), float(p[1])


def dogleg(a: Point, b: Point, style: str = "45", bend: str = "straight_first") -> list[Point]:
    """Points from a to b using 45-degree, orthogonal ('manhattan'), or direct routing."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    if style == "direct" or abs(dx) < _EPS or abs(dy) < _EPS:
        return [a, b]
    if style == "manhattan":
        corner = (b[0], a[1]) if bend == "straight_first" else (a[0], b[1])
        return [a, corner, b]
    if style != "45":
        raise ValueError("style must be '45', 'manhattan', or 'direct'.")
    sx, sy = math.copysign(1, dx), math.copysign(1, dy)
    diag = min(abs(dx), abs(dy))
    if abs(abs(dx) - abs(dy)) < _EPS:
        return [a, b]
    if bend == "straight_first":
        corner = ((b[0] - sx * diag, a[1]) if abs(dx) > abs(dy)
                  else (a[0], b[1] - sy * diag))
    else:
        corner = (a[0] + sx * diag, a[1] + sy * diag)
    return [a, corner, b]


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

class Snapshot:
    """One read of the board's copper, so checks don't re-query KiCad per item."""

    def __init__(self, board):
        self.tracks = list(board.get_tracks())
        self.vias = list(board.get_vias())
        self.pads = [(fp_reference(fp), pad) for fp in board.get_footprints()
                     for pad in (_try(lambda fp=fp: fp.definition.pads, []) or [])]


def _obstacles(snap: Snapshot, layer: str, net_name: str, skip_ids: set[str]):
    """Copper on ``layer`` belonging to other nets: (kind, label, geometry, half_width)."""
    from kipy.board_types import ArcTrack

    obs: list[tuple[str, str, Any, float]] = []
    for t in snap.tracks:
        if item_id(t) in skip_ids or layer_to_name(t.layer) != layer:
            continue
        if _try(lambda t=t: t.net.name, "") == net_name and net_name:
            continue
        pts = ([_xy(t.start), _xy(t.mid), _xy(t.end)] if isinstance(t, ArcTrack)
               else [_xy(t.start), _xy(t.end)])
        label = f"track on net '{_try(lambda t=t: t.net.name, '')}'"
        for p, q in zip(pts, pts[1:]):
            obs.append(("segment", label, (p, q), nm_to_mm(t.width) / 2))
    for v in snap.vias:
        if item_id(v) in skip_ids:
            continue
        if _try(lambda v=v: v.net.name, "") == net_name and net_name:
            continue
        obs.append(("point", f"via on net '{_try(lambda v=v: v.net.name, '')}'",
                    _xy(v.position), nm_to_mm(v.diameter) / 2))
    for ref, pad in snap.pads:
        if pad_net_name(pad) == net_name and net_name:
            continue
        layers = pad_layers(pad)
        if layers and layer not in layers and not pad_is_through_hole(pad):
            continue
        obs.append(("rect", f"pad {ref}.{pad.number} (net '{pad_net_name(pad)}')",
                    pad_rect(pad), 0.0))
    return obs


def clearance_conflicts(snap: Snapshot, segments: list[tuple[Point, Point]], width_mm: float,
                        layer: str, net_name: str, clearance_mm: float,
                        skip_ids: Optional[set[str]] = None) -> list[str]:
    obs = _obstacles(snap, layer, net_name, skip_ids or set())
    found: dict[str, float] = {}
    for a, b in segments:
        for kind, label, geom, half in obs:
            if kind == "segment":
                d = segment_segment_distance(a, b, geom[0], geom[1]) - half
            elif kind == "point":
                d = point_segment_distance(geom, a, b) - half
            else:
                d = segment_rect_distance(a, b, geom)
            gap = d - width_mm / 2
            if gap < clearance_mm - _EPS:
                found[label] = min(found.get(label, math.inf), gap)
    return [f"{'Short' if g <= 0 else 'Clearance'} with {label}: "
            f"{max(g, 0):.3f} mm gap (need {clearance_mm:.3f} mm) on {layer}."
            for label, g in sorted(found.items(), key=lambda kv: kv[1])]


def _connected_at(snap: Snapshot, p: Point, layer: str, net_name: str,
                  skip_ids: set[str]) -> bool:
    """Is there same-net copper at point p on this layer (pad, via, or track)?"""
    for _ref, pad in snap.pads:
        if pad_net_name(pad) != net_name:
            continue
        layers = pad_layers(pad)
        if layers and layer not in layers and not pad_is_through_hole(pad):
            continue
        r = pad_rect(pad)
        if r[0] - _EPS <= p[0] <= r[2] + _EPS and r[1] - _EPS <= p[1] <= r[3] + _EPS:
            return True
    for v in snap.vias:
        if _try(lambda v=v: v.net.name, "") == net_name and \
                math.dist(p, _xy(v.position)) <= nm_to_mm(v.diameter) / 2 + _EPS:
            return True
    for t in snap.tracks:
        if item_id(t) in skip_ids or layer_to_name(t.layer) != layer:
            continue
        if _try(lambda t=t: t.net.name, "") != net_name:
            continue
        if point_segment_distance(p, _xy(t.start), _xy(t.end)) <= nm_to_mm(t.width) / 2 + _EPS:
            return True
    return False


def dangling_ends(snap: Snapshot, points: list[Point], layer: str, net_name: str,
                  created_ids: set[str]) -> list[str]:
    if not net_name:
        return ["Track has no net, so connectivity cannot be checked."]
    out = []
    for label, p in (("start", points[0]), ("end", points[-1])):
        if not _connected_at(snap, p, layer, net_name, created_ids):
            out.append(f"Path {label} ({p[0]:.3f}, {p[1]:.3f}) does not touch any "
                       f"'{net_name}' pad, via, or track on {layer}.")
    return out


# ---------------------------------------------------------------------------
# Creation helpers
# ---------------------------------------------------------------------------

def create_path(board, points: list[Point], width_mm: float, layer: str, net, message: str):
    from kipy.board_types import Track

    tracks = []
    for a, b in zip(points, points[1:]):
        if math.dist(a, b) < _EPS:
            continue
        t = Track()
        t.start, t.end = vmm(*a), vmm(*b)
        t.width = mm_to_nm(width_mm)
        t.layer = name_to_layer(layer)
        if net is not None:
            t.net = net
        tracks.append(t)
    if not tracks:
        return []
    with commit(board, message):
        return list(board.create_items(tracks))


def create_via(board, p: Point, diameter_mm: float, drill_mm: float, net, message: str):
    from kipy.board_types import Via

    v = Via()
    v.position = vmm(*p)
    v.diameter = mm_to_nm(diameter_mm)
    v.drill_diameter = mm_to_nm(drill_mm)
    if net is not None:
        v.net = net
    with commit(board, message):
        return list(board.create_items(v))


def _check(snap: Snapshot, legs: list[tuple[list[Point], str]], width_mm: float,
           net_name: str, clearance_mm: float, created: list) -> list[str]:
    ids = {item_id(c) for c in created}
    warnings: list[str] = []
    for points, layer in legs:
        segs = list(zip(points, points[1:]))
        warnings += clearance_conflicts(snap, segs, width_mm, layer, net_name,
                                        clearance_mm, ids)
    return warnings


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def route_pads(from_reference: str, from_pad: str, to_reference: str, to_pad: str,
                   layer: str = "", width_mm: float | None = None, style: str = "45",
                   bend: str = "straight_first", via_at: list[float] | None = None,
                   to_layer: str = "",
                   rollback_on_conflict: bool = False) -> dict[str, Any]:
        """Route a track between two pads by name - no coordinates needed.

        Looks up both pads, takes the net from them, uses the netclass track width
        unless overridden, draws a 45-degree (or orthogonal) path, and then reports
        clearance conflicts with other nets.

        Args:
            from_reference, from_pad: Start pad, e.g. 'U2', '7'.
            to_reference, to_pad: End pad, e.g. 'J3', '1'.
            layer: Copper layer. Empty = a layer both pads share (F.Cu preferred).
            width_mm: Track width; default is the net's netclass width.
            style: '45' (default), 'manhattan' (90-degree), or 'direct' (straight line).
            bend: 'straight_first' or 'diagonal_first' - which end gets the bend.
            via_at: [x_mm, y_mm] to change layer through a via on the way. Required when
                the pads have no copper layer in common (e.g. one SMD on each side);
                if omitted in that case the via goes at the midpoint.
            to_layer: Layer after the via (default: the other outer layer).
            rollback_on_conflict: Undo the whole route if any clearance problem is found.
        """
        board = require_board()
        _, pa = find_pad(board, from_reference, from_pad)
        _, pb = find_pad(board, to_reference, to_pad)
        net_a, net_b = pad_net_name(pa), pad_net_name(pb)
        if net_a and net_b and net_a != net_b:
            raise ValueError(f"{from_reference}.{from_pad} is on '{net_a}' but "
                             f"{to_reference}.{to_pad} is on '{net_b}'; refusing to short them.")
        net_name = net_a or net_b
        net = find_net(board, net_name) if net_name else None
        rules = net_rules(board, net)
        width = width_mm if width_mm is not None else rules.track_width_mm

        a, b = pad_xy(pa), pad_xy(pb)
        la, lb = pad_layers(pa), pad_layers(pb)
        if layer:
            start_layer = layer
            if la and layer not in la and not pad_is_through_hole(pa):
                raise ValueError(f"{from_reference}.{from_pad} is not on {layer} "
                                 f"(it is on {', '.join(sorted(la))}).")
        else:
            candidates = (la & lb) or la or {"F.Cu"}
            start_layer = next((l for l in ("F.Cu", "B.Cu") if l in candidates),
                               sorted(candidates)[0])
        needs_via = via_at is not None or (
            bool(lb) and start_layer not in lb and not pad_is_through_hole(pb))

        created: list = []
        legs: list[tuple[list[Point], str]] = []
        notes: list[str] = []
        if needs_via:
            end_layer = to_layer or next((l for l in lb if l != start_layer), None) \
                or ("B.Cu" if start_layer == "F.Cu" else "F.Cu")
            v = _pt(via_at) if via_at is not None else ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            if via_at is None:
                notes.append("Pads share no copper layer; via placed at the midpoint.")
            leg1 = dogleg(a, v, style, bend)
            leg2 = dogleg(v, b, style, bend)
            created += create_path(board, leg1, width, start_layer, net, "Route pads (leg 1)")
            created += create_via(board, v, rules.via_diameter_mm, rules.via_drill_mm, net,
                                  "Route pads (via)")
            created += create_path(board, leg2, width, end_layer, net, "Route pads (leg 2)")
            legs = [(leg1, start_layer), (leg2, end_layer)]
        else:
            path = dogleg(a, b, style, bend)
            created += create_path(board, path, width, start_layer, net, "Route pads")
            legs = [(path, start_layer)]

        snap = Snapshot(board)
        warnings = notes + _check(snap, legs, width, net_name, rules.clearance_mm, created)
        if needs_via:
            ids = {item_id(c) for c in created}
            for via_layer in {start_layer, end_layer}:
                warnings += [f"Via: {w}" for w in clearance_conflicts(
                    snap, [(v, v)], rules.via_diameter_mm, via_layer, net_name,
                    rules.clearance_mm, ids)]
        rolled_back = False
        if rollback_on_conflict and any(w.startswith(("Short", "Clearance", "Via:"))
                                        for w in warnings):
            with commit(board, "Roll back route"):
                board.remove_items(created)
            rolled_back = True
        return {
            "net": net_name or None,
            "netclass": rules.netclass,
            "width_mm": width,
            "legs": [{"layer": l, "points_mm": [[round(x, 4), round(y, 4)] for x, y in pts]}
                     for pts, l in legs],
            "created_ids": [] if rolled_back else [item_id(c) for c in created],
            "rolled_back": rolled_back,
            "warnings": warnings,
        }

    @mcp.tool()
    def add_track_path(points: list[Any], net_name: str, layer: str = "F.Cu",
                       width_mm: float | None = None,
                       rollback_on_conflict: bool = False) -> dict[str, Any]:
        """Draw a connected multi-segment track through a list of points in one step,
        then check both ends are connected and nothing is too close.

        Args:
            points: Vertices, each [x, y] or {"x_mm":.., "y_mm":..}; at least 2.
            net_name: Net to assign (must exist; e.g. 'GND', '/MOTOR_A').
            layer: Copper layer name.
            width_mm: Track width; default is the net's netclass width.
            rollback_on_conflict: Undo the path if any clearance problem is found.
        """
        pts = [_pt(p) for p in points]
        if len(pts) < 2:
            raise ValueError("A path needs at least 2 points.")
        board = require_board()
        net = find_net(board, net_name)
        rules = net_rules(board, net)
        width = width_mm if width_mm is not None else rules.track_width_mm
        created = create_path(board, pts, width, layer, net, "Add track path")
        snap = Snapshot(board)
        warnings = _check(snap, [(pts, layer)], width, net_name, rules.clearance_mm, created)
        warnings += dangling_ends(snap, pts, layer, net_name, {item_id(c) for c in created})
        rolled_back = False
        if rollback_on_conflict and any(w.startswith(("Short", "Clearance")) for w in warnings):
            with commit(board, "Roll back path"):
                board.remove_items(created)
            rolled_back = True
        return {"net": net_name, "netclass": rules.netclass, "width_mm": width,
                "created_ids": [] if rolled_back else [item_id(c) for c in created],
                "rolled_back": rolled_back, "warnings": warnings}

    @mcp.tool()
    def check_clearance(item_ids: list[str] | None = None) -> dict[str, Any]:
        """Quick clearance check of tracks against other nets' copper (tracks, vias,
        pads), using each net's netclass clearance. Pass track IDs to check just those,
        or nothing to check every track. Approximate - run_drc is authoritative.

        Args:
            item_ids: Track KIIDs to check; empty = all tracks on the board.
        """
        from kipy.board_types import ArcTrack

        board = require_board()
        snap = Snapshot(board)
        wanted = set(item_ids or [])
        rules_cache: dict[str, Any] = {}
        problems: list[dict[str, Any]] = []
        checked = 0
        for t in snap.tracks:
            tid = item_id(t)
            if wanted and tid not in wanted:
                continue
            checked += 1
            net_name = _try(lambda t=t: t.net.name, "") or ""
            if net_name not in rules_cache:
                rules_cache[net_name] = net_rules(board, _try(lambda t=t: t.net, None))
            pts = ([_xy(t.start), _xy(t.mid), _xy(t.end)] if isinstance(t, ArcTrack)
                   else [_xy(t.start), _xy(t.end)])
            found = clearance_conflicts(snap, list(zip(pts, pts[1:])), nm_to_mm(t.width),
                                        layer_to_name(t.layer), net_name,
                                        rules_cache[net_name].clearance_mm, {tid})
            if found:
                problems.append({"track_id": tid, "net": net_name, "issues": found})
        return {"tracks_checked": checked, "tracks_with_problems": len(problems),
                "problems": problems}
