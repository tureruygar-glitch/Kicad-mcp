"""A top-down picture of the live board so the model can see what it placed.

Rendered with Pillow straight from the IPC data - no save or kicad-cli round
trip - and shows what matters for placement and routing decisions: outline,
courtyards with reference labels, pads, tracks, vias, and airwires.
"""

from __future__ import annotations

import io
import math

from mcp.server.fastmcp import FastMCP, Image

from kicad10_mcp.board_query import (
    board_outline,
    footprint_courtyard,
    footprint_pads,
    footprint_side,
    merge_rects,
    pad_layers,
    pad_net_name,
    pad_rect,
    ratsnest,
)
from kicad10_mcp.connection import require_board
from kicad10_mcp.helpers import _try, fp_reference, layer_to_name, nm_to_mm

_BG = (18, 18, 26)
_OUTLINE = (230, 200, 60)
_FRONT = (200, 60, 60)
_BACK = (70, 110, 230)
_CRT_FRONT = (170, 170, 185)
_CRT_BACK = (150, 90, 170)
_VIA = (190, 190, 190)
_AIRWIRE = (240, 240, 110)
_HIGHLIGHT = (80, 230, 120)
_TEXT = (245, 245, 245)


def _xy(v) -> tuple[float, float]:
    return nm_to_mm(v.x), nm_to_mm(v.y)


def _font(size: int):
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def snapshot_board(side: str = "both", show_ratsnest: bool = True,
                       ratsnest_exclude_nets: list[str] | None = None,
                       highlight_net: str = "", width_px: int = 1200) -> Image:
        """Return a PNG picture of the open board (top view) to check placement and
        routing visually. Front copper is red, back copper blue, courtyards are grey
        (front) / purple (back) boxes labelled with references, airwires are yellow.

        Args:
            side: 'both', 'front', or 'back' - which side's parts and copper to draw.
            show_ratsnest: Draw airwires (straight lines between pads to be connected).
            ratsnest_exclude_nets: Nets to leave out of the airwires, e.g. ['GND'].
            highlight_net: Draw this net's pads, tracks, and airwires in green.
            width_px: Image width in pixels (height follows the board's aspect ratio).
        """
        from PIL import Image as PILImage, ImageDraw
        from kipy.board_types import ArcTrack
        from kipy.proto.board.board_types_pb2 import BoardLayer

        if side not in ("both", "front", "back"):
            raise ValueError("side must be 'both', 'front', or 'back'.")
        board = require_board()
        fps = list(board.get_footprints())
        tracks = list(board.get_tracks())
        vias = list(board.get_vias())
        edge_shapes = [s for s in _try(lambda: board.get_shapes(), []) or []
                       if _try(lambda s=s: s.layer, None) == BoardLayer.BL_Edge_Cuts]

        show = {"front": {"front"}, "back": {"back"}, "both": {"front", "back"}}[side]
        layer_ok = {"front": lambda l: l != "B.Cu", "back": lambda l: l != "F.Cu",
                    "both": lambda l: True}[side]

        courtyards = {fp_reference(fp): footprint_courtyard(board, fp) for fp in fps}
        bounds = board_outline(board) or merge_rects(courtyards.values())
        if bounds is None:
            raise RuntimeError("The board is empty; nothing to draw.")
        margin = 2.0
        x0, y0 = bounds[0] - margin, bounds[1] - margin
        span_x = (bounds[2] - bounds[0]) + 2 * margin
        span_y = (bounds[3] - bounds[1]) + 2 * margin
        scale = width_px / span_x
        height_px = max(100, int(span_y * scale))

        img = PILImage.new("RGB", (width_px, height_px), _BG)
        draw = ImageDraw.Draw(img)

        def P(p):
            return (p[0] - x0) * scale, (p[1] - y0) * scale

        def w(mm: float) -> int:
            return max(1, int(round(mm * scale)))

        # Board outline.
        for s in edge_shapes:
            center = _try(lambda s=s: _xy(s.center), None)
            rpt = _try(lambda s=s: _xy(s.radius_point), None)
            tl = _try(lambda s=s: _xy(s.top_left), None)
            polys = _try(lambda s=s: list(s.polygons), None)
            start = _try(lambda s=s: _xy(s.start), None)
            end = _try(lambda s=s: _xy(s.end), None)
            mid = _try(lambda s=s: _xy(s.mid), None)
            if center and rpt:
                r = math.dist(center, rpt)
                draw.ellipse([P((center[0] - r, center[1] - r)), P((center[0] + r, center[1] + r))],
                             outline=_OUTLINE, width=2)
            elif tl:
                draw.rectangle([P(tl), P(_xy(s.bottom_right))], outline=_OUTLINE, width=2)
            elif polys:
                for poly in polys:
                    pts = [P(_xy(n.point)) for n in poly.outline if n.has_point]
                    if len(pts) > 1:
                        draw.line(pts + [pts[0]], fill=_OUTLINE, width=2)
            elif start and end:
                pts = [start, mid, end] if mid else [start, end]
                draw.line([P(p) for p in pts], fill=_OUTLINE, width=2)

        # Courtyards and labels.
        font = _font(max(10, int(scale * 1.0)))
        for fp in fps:
            fside = footprint_side(fp)
            if fside not in show:
                continue
            box = courtyards[fp_reference(fp)]
            draw.rectangle([P((box[0], box[1])), P((box[2], box[3]))],
                           outline=_CRT_FRONT if fside == "front" else _CRT_BACK, width=1)

        # Tracks (back first so front sits on top).
        def track_color(t):
            if highlight_net and _try(lambda: t.net.name, "") == highlight_net:
                return _HIGHLIGHT
            return _FRONT if layer_to_name(t.layer) == "F.Cu" else _BACK

        for t in sorted(tracks, key=lambda t: layer_to_name(t.layer) == "F.Cu"):
            layer = layer_to_name(t.layer)
            if not layer_ok(layer):
                continue
            pts = ([_xy(t.start), _xy(t.mid), _xy(t.end)] if isinstance(t, ArcTrack)
                   else [_xy(t.start), _xy(t.end)])
            draw.line([P(p) for p in pts], fill=track_color(t), width=w(nm_to_mm(t.width)))

        # Pads.
        for fp in fps:
            if footprint_side(fp) not in show:
                continue
            for pad in footprint_pads(fp):
                layers = pad_layers(pad)
                color = (_HIGHLIGHT if highlight_net and pad_net_name(pad) == highlight_net
                         else _BACK if layers == {"B.Cu"} else _FRONT)
                r = pad_rect(pad)
                draw.rectangle([P((r[0], r[1])), P((r[2], r[3]))], fill=color)

        # Vias.
        for v in vias:
            c = _xy(v.position)
            r = nm_to_mm(v.diameter) / 2
            draw.ellipse([P((c[0] - r, c[1] - r)), P((c[0] + r, c[1] + r))], fill=_VIA)

        # Airwires.
        if show_ratsnest:
            for wire in ratsnest(board, ratsnest_exclude_nets or []):
                color = _HIGHLIGHT if wire["net"] == highlight_net else _AIRWIRE
                draw.line([P(wire["start"]), P(wire["end"])], fill=color, width=1)

        # Labels last so nothing covers them.
        for fp in fps:
            if footprint_side(fp) not in show:
                continue
            box = courtyards[fp_reference(fp)]
            draw.text(P(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)), fp_reference(fp),
                      fill=_TEXT, font=font, anchor="mm", stroke_width=2, stroke_fill=_BG)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Image(data=buf.getvalue(), format="png")
