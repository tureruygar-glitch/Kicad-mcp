"""Placement tools that think in terms of parts, pads, and board edges.

The raw ``move_footprint`` tool needs absolute coordinates, which forces the
model to do geometry by hand. These tools position footprints relative to other
parts, pads, or the board outline using courtyard boxes, and report overlaps
after every move so mistakes surface immediately.
"""

from __future__ import annotations

import math
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from kicad10_mcp.board_query import (
    board_outline,
    find_footprint,
    find_pad,
    footprint_courtyard,
    footprint_pads,
    footprint_side,
    pad_half_size,
    pad_net_name,
    pad_xy,
    ratsnest,
    rect_inside,
    rects_overlap,
)
from kicad10_mcp.connection import commit, get_kicad, require_board
from kicad10_mcp.helpers import _try, fp_reference, nm_to_mm, vmm

_SIDES = ("left", "right", "up", "down")


def _r(v: float) -> float:
    return round(v, 4)


def _rect_dict(r) -> dict[str, float]:
    return {"x_min_mm": _r(r[0]), "y_min_mm": _r(r[1]),
            "x_max_mm": _r(r[2]), "y_max_mm": _r(r[3])}


def _move(board, fp, x: float, y: float, angle_deg: Optional[float], message: str) -> None:
    from kipy.geometry import Angle

    with commit(board, message):
        fp.position = vmm(x, y)
        if angle_deg is not None:
            fp.orientation = Angle.from_degrees(float(angle_deg))
        board.update_items([fp])


def _set_angle(board, fp, angle_deg: Optional[float]) -> None:
    """Rotate in place first so the courtyard used for positioning is the final one."""
    if angle_deg is None:
        return
    pos = fp.position
    _move(board, fp, nm_to_mm(pos.x), nm_to_mm(pos.y), angle_deg, "Rotate before placement")


def _origin_offset(board, fp) -> tuple[float, float, float, float]:
    """Courtyard extents relative to the footprint origin: (left, top, right, bottom)."""
    x, y = nm_to_mm(fp.position.x), nm_to_mm(fp.position.y)
    c = footprint_courtyard(board, fp)
    return x - c[0], y - c[1], c[2] - x, c[3] - y


def placement_warnings(board, references: Optional[list[str]] = None) -> list[str]:
    """Courtyard overlaps (same side) and parts outside the board outline."""
    fps = list(board.get_footprints())
    info = [(fp_reference(fp), footprint_side(fp), footprint_courtyard(board, fp)) for fp in fps]
    watch = set(references) if references else None
    warnings: list[str] = []
    for i, (ref_a, side_a, box_a) in enumerate(info):
        for ref_b, side_b, box_b in info[i + 1:]:
            if watch is not None and ref_a not in watch and ref_b not in watch:
                continue
            if side_a == side_b and rects_overlap(box_a, box_b):
                warnings.append(f"Courtyard overlap: {ref_a} and {ref_b} ({side_a}).")
    outline = board_outline(board)
    if outline is not None:
        for ref, _side, box in info:
            if (watch is None or ref in watch) and not rect_inside(box, outline):
                warnings.append(f"{ref} extends outside the board outline.")
    return warnings


def _result(board, fp, extra: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    ref = fp_reference(fp)
    fp = find_footprint(board, ref)  # re-read the committed state
    out: dict[str, Any] = {
        "reference": ref,
        "position_mm": {"x_mm": _r(nm_to_mm(fp.position.x)), "y_mm": _r(nm_to_mm(fp.position.y))},
        "orientation_deg": _try(lambda: fp.orientation.degrees, 0.0),
        "side": footprint_side(fp),
        "courtyard_mm": _rect_dict(footprint_courtyard(board, fp)),
        "warnings": placement_warnings(board, [ref]),
    }
    if extra:
        out.update(extra)
    return out


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def get_footprint_geometry(reference: str) -> dict[str, Any]:
        """Position, rotation, side, courtyard box, and every pad (number, net, position,
        size) of one footprint. Use this before placing or routing around a part.

        Args:
            reference: Reference designator, e.g. 'U1'.
        """
        board = require_board()
        fp = find_footprint(board, reference)
        pads = []
        for pad in footprint_pads(fp):
            x, y = pad_xy(pad)
            hw, hh = pad_half_size(pad)
            pads.append({"number": str(pad.number), "net": pad_net_name(pad),
                         "x_mm": _r(x), "y_mm": _r(y),
                         "width_mm": _r(2 * hw), "height_mm": _r(2 * hh)})
        return {
            "reference": reference,
            "value": _try(lambda: fp.value_field.text.value, ""),
            "position_mm": {"x_mm": _r(nm_to_mm(fp.position.x)), "y_mm": _r(nm_to_mm(fp.position.y))},
            "orientation_deg": _try(lambda: fp.orientation.degrees, 0.0),
            "side": footprint_side(fp),
            "courtyard_mm": _rect_dict(footprint_courtyard(board, fp)),
            "pads": pads,
        }

    @mcp.tool()
    def place_relative(reference: str, anchor_reference: str, dx_mm: float, dy_mm: float,
                       angle_deg: float | None = None) -> dict[str, Any]:
        """Place a footprint at an offset from another footprint's origin.

        Args:
            reference: Footprint to move.
            anchor_reference: Footprint to measure from.
            dx_mm, dy_mm: Offset in mm (Y grows downward).
            angle_deg: Optional absolute rotation for the moved part.
        """
        board = require_board()
        fp = find_footprint(board, reference)
        anchor = find_footprint(board, anchor_reference)
        x = nm_to_mm(anchor.position.x) + dx_mm
        y = nm_to_mm(anchor.position.y) + dy_mm
        _move(board, fp, x, y, angle_deg, f"Place {reference} relative to {anchor_reference}")
        return _result(board, fp)

    @mcp.tool()
    def place_near_pad(reference: str, target_reference: str, target_pad: str,
                       side: str = "auto", gap_mm: float = 0.3,
                       angle_deg: float | None = None,
                       auto_orient: bool = True) -> dict[str, Any]:
        """Place a part right next to a specific pad, just outside the target part's
        courtyard and aligned with the pad. Ideal for decoupling capacitors, pull-ups,
        and series resistors.

        Args:
            reference: Footprint to move, e.g. 'C3'.
            target_reference: Part that owns the pad, e.g. 'U2'.
            target_pad: Pad number on the target, e.g. '13'.
            side: 'left', 'right', 'up', 'down', or 'auto' (the courtyard edge nearest
                the pad).
            gap_mm: Courtyard-to-courtyard gap in mm.
            angle_deg: Optional absolute rotation for the moved part (applied first).
            auto_orient: If the moved part has a pad on the target pad's net, turn it
                180 degrees when that makes the same-net pad face the target (so the
                connecting track doesn't cross the part's other pad).
        """
        board = require_board()
        fp = find_footprint(board, reference)
        target_fp, pad = find_pad(board, target_reference, target_pad)
        px, py = pad_xy(pad)
        tbox = footprint_courtyard(board, target_fp)

        if side == "auto":
            dist = {"left": px - tbox[0], "right": tbox[2] - px,
                    "up": py - tbox[1], "down": tbox[3] - py}
            side = min(dist, key=dist.get)
        if side not in _SIDES:
            raise ValueError(f"side must be one of {_SIDES} or 'auto'.")

        _set_angle(board, fp, angle_deg)
        fp = find_footprint(board, reference)
        ox, oy = nm_to_mm(fp.position.x), nm_to_mm(fp.position.y)
        extents = _origin_offset(board, fp)

        def spot(ext) -> tuple[float, float]:
            left, top, right, bottom = ext
            if side == "left":
                return tbox[0] - gap_mm - right, py
            if side == "right":
                return tbox[2] + gap_mm + left, py
            if side == "up":
                return px, tbox[1] - gap_mm - bottom
            return px, tbox[3] + gap_mm + top

        x, y = spot(extents)
        new_angle = None
        net = pad_net_name(pad)
        same_net = [pad_xy(p) for p in footprint_pads(fp) if net and pad_net_name(p) == net]
        if auto_orient and angle_deg is None and same_net:
            # A 180-degree turn negates pad offsets and swaps opposite courtyard extents,
            # so both candidates can be scored without touching the board.
            offs = [(sx - ox, sy - oy) for sx, sy in same_net]
            fx, fy = spot((extents[2], extents[3], extents[0], extents[1]))
            keep = min(math.dist((x + dx, y + dy), (px, py)) for dx, dy in offs)
            turn = min(math.dist((fx - dx, fy - dy), (px, py)) for dx, dy in offs)
            if turn < keep - 1e-6:
                x, y = fx, fy
                new_angle = (_try(lambda: fp.orientation.degrees, 0.0) + 180) % 360
        _move(board, fp, x, y, new_angle, f"Place {reference} near {target_reference}.{target_pad}")
        return _result(board, fp, {"placed_side": side, "turned_180": new_angle is not None,
                                   "target_pad_mm": {"x_mm": _r(px), "y_mm": _r(py)},
                                   "target_pad_net": pad_net_name(pad)})

    @mcp.tool()
    def place_on_edge(reference: str, edge: str, inset_mm: float = 0.5,
                      along_mm: float | None = None,
                      angle_deg: float | None = None) -> dict[str, Any]:
        """Place a part against a board edge (connectors, sensors, switches).

        Args:
            reference: Footprint to move.
            edge: 'left', 'right', 'top', or 'bottom' edge of the board outline.
            inset_mm: Distance from the board edge to the part's courtyard.
            along_mm: Absolute coordinate along the edge (X for top/bottom, Y for
                left/right). Defaults to the middle of that edge.
            angle_deg: Optional absolute rotation (applied first).
        """
        board = require_board()
        outline = board_outline(board)
        if outline is None:
            raise RuntimeError("The board has no Edge.Cuts outline; add one first "
                               "(add_board_outline_rect).")
        fp = find_footprint(board, reference)
        _set_angle(board, fp, angle_deg)
        fp = find_footprint(board, reference)
        left, top, right, bottom = _origin_offset(board, fp)
        x0, y0, x1, y1 = outline
        if edge == "left":
            x, y = x0 + inset_mm + left, along_mm if along_mm is not None else (y0 + y1) / 2
        elif edge == "right":
            x, y = x1 - inset_mm - right, along_mm if along_mm is not None else (y0 + y1) / 2
        elif edge == "top":
            x, y = along_mm if along_mm is not None else (x0 + x1) / 2, y0 + inset_mm + top
        elif edge == "bottom":
            x, y = along_mm if along_mm is not None else (x0 + x1) / 2, y1 - inset_mm - bottom
        else:
            raise ValueError("edge must be 'left', 'right', 'top', or 'bottom'.")
        _move(board, fp, x, y, None, f"Place {reference} on {edge} edge")
        return _result(board, fp)

    @mcp.tool()
    def arrange_row(references: list[str], start_x_mm: float, start_y_mm: float,
                    pitch_mm: float | None = None, direction: str = "x",
                    gap_mm: float = 0.5, angle_deg: float | None = None) -> dict[str, Any]:
        """Line several parts up in a row or column in one undo step.

        Args:
            references: Parts in order, e.g. ['R1', 'R2', 'R3'].
            start_x_mm, start_y_mm: Origin of the first part.
            pitch_mm: Fixed origin-to-origin spacing. If omitted, parts are packed
                courtyard-to-courtyard with ``gap_mm`` between them.
            direction: 'x' (row, left to right) or 'y' (column, top to bottom).
            gap_mm: Gap used when pitch_mm is omitted.
            angle_deg: Optional absolute rotation applied to every part.
        """
        from kipy.geometry import Angle

        if direction not in ("x", "y"):
            raise ValueError("direction must be 'x' or 'y'.")
        board = require_board()
        fps = [find_footprint(board, r) for r in references]
        if angle_deg is not None:
            with commit(board, "Rotate row"):
                for fp in fps:
                    fp.orientation = Angle.from_degrees(float(angle_deg))
                board.update_items(fps)
            fps = [find_footprint(board, r) for r in references]

        offsets = [_origin_offset(board, fp) for fp in fps]
        cursor = start_x_mm if direction == "x" else start_y_mm
        with commit(board, "Arrange row"):
            for i, fp in enumerate(fps):
                left, top, right, bottom = offsets[i]
                if i > 0 and pitch_mm is None:
                    prev = offsets[i - 1]
                    cursor += (prev[2] + gap_mm + left) if direction == "x" else (prev[3] + gap_mm + top)
                elif i > 0:
                    cursor += pitch_mm
                fp.position = vmm(cursor, start_y_mm) if direction == "x" else vmm(start_x_mm, cursor)
            board.update_items(fps)
        return {"arranged": references, "warnings": placement_warnings(board, references)}

    @mcp.tool()
    def flip_footprint(reference: str) -> dict[str, Any]:
        """Move a footprint to the other side of the board (front <-> back), mirrored
        in place.

        Args:
            reference: Footprint to flip.
        """
        board = require_board()
        fp = find_footprint(board, reference)
        before = footprint_side(fp)
        flipped = False
        if hasattr(board, "flip_items"):
            try:
                with commit(board, f"Flip {reference}"):
                    flipped = bool(board.flip_items(fp))
            except Exception:  # noqa: BLE001 - needs KiCad 10.0.6+, fall back below
                flipped = False
        if not flipped:
            # Older KiCad: flip through the editor's own action on the selection.
            board.clear_selection()
            board.add_to_selection([fp])
            get_kicad().run_action("pcbnew.InteractiveEdit.flip")
            board.clear_selection()
        fp = find_footprint(board, reference)
        if footprint_side(fp) == before:
            raise RuntimeError(f"Flipping {reference} had no effect; KiCad may need the "
                               "PCB editor focused. Try again or flip it manually (F key).")
        return _result(board, fp)

    @mcp.tool()
    def check_placement(exclude_nets: list[str] | None = None,
                        top_airwires: int = 10) -> dict[str, Any]:
        """Score the current placement: courtyard overlaps, parts off the board, and
        total airwire (ratsnest) length. Lower airwire length usually means easier
        routing; compare before/after when moving parts.

        Args:
            exclude_nets: Nets to ignore in the airwire total, typically ['GND'] when a
                ground pour will connect it.
            top_airwires: How many of the longest airwires to list.
        """
        board = require_board()
        wires = ratsnest(board, exclude_nets or [])
        per_net: dict[str, float] = {}
        for w in wires:
            per_net[w["net"]] = per_net.get(w["net"], 0.0) + w["length_mm"]
        longest = sorted(wires, key=lambda w: w["length_mm"], reverse=True)[:top_airwires]
        return {
            "warnings": placement_warnings(board),
            "total_airwire_mm": _r(sum(per_net.values())),
            "airwire_mm_by_net": {k: _r(v) for k, v in
                                  sorted(per_net.items(), key=lambda kv: -kv[1])},
            "longest_airwires": [{k: w[k] for k in ("net", "from", "to", "length_mm")}
                                 for w in longest],
            "board_outline_mm": (_rect_dict(board_outline(board))
                                 if board_outline(board) else None),
        }
