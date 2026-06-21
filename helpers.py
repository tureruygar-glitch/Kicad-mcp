"""Unit conversion, layer name mapping, and object serialization helpers.

All public tool inputs/outputs use millimetres and degrees; the KiCad API works
in nanometres internally, so conversion happens here in one place.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from kipy.geometry import Vector2
from kipy.proto.common.types import KIID
from kipy.proto.board.board_types_pb2 import BoardLayer

# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------

NM_PER_MM = 1_000_000


def mm_to_nm(mm: float) -> int:
    return int(round(float(mm) * NM_PER_MM))


def nm_to_mm(nm: int) -> float:
    return round(nm / NM_PER_MM, 6)


def vmm(x_mm: float, y_mm: float) -> Vector2:
    """Build a Vector2 from millimetre coordinates."""
    return Vector2.from_xy_mm(float(x_mm), float(y_mm))


def vec_dict(v: Vector2) -> dict[str, float]:
    return {"x_mm": nm_to_mm(v.x), "y_mm": nm_to_mm(v.y)}


def _try(fn: Callable[[], Any], default: Any = None) -> Any:
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return default


# ---------------------------------------------------------------------------
# Layers
# ---------------------------------------------------------------------------

def layer_to_name(layer: int) -> str:
    """Convert a BoardLayer enum value to a KiCad layer name, e.g. ``F.Cu``."""
    name = _try(lambda: BoardLayer.Name(layer), None)
    if name is None:
        return str(layer)
    if name.startswith("BL_"):
        name = name[3:]
    return name.replace("_", ".")


def name_to_layer(name: str) -> int:
    """Convert a KiCad layer name (``F.Cu``) or enum name (``BL_F_Cu``) to a value."""
    raw = (name or "").strip()
    key = raw if raw.startswith("BL_") else "BL_" + raw.replace(".", "_")
    try:
        return BoardLayer.Value(key)
    except ValueError as exc:
        raise ValueError(
            f"Unknown layer '{name}'. Use names like F.Cu, B.Cu, In1.Cu, "
            "Edge.Cuts, F.SilkS, B.SilkS, F.Mask, B.Mask, F.Paste, F.Fab, "
            "F.Courtyard, Dwgs.User, Cmts.User. Call list_board_layers for the "
            "exact set enabled on this board."
        ) from exc


def all_layer_names() -> list[str]:
    return [layer_to_name(v) for _, v in sorted(BoardLayer.items(), key=lambda kv: kv[1])]


# ---------------------------------------------------------------------------
# Nets / IDs
# ---------------------------------------------------------------------------

def find_net(board, name: str):
    """Look up an existing net by name; fall back to a fresh Net with that name."""
    from kipy.board_types import Net

    if not name:
        return None
    for net in board.get_nets():
        if net.name == name:
            return net
    return Net(name=name)


def kiid(value: str) -> KIID:
    k = KIID()
    k.value = value
    return k


def item_id(item) -> Optional[str]:
    return _try(lambda: item.id.value, None)


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------

def fp_reference(fp) -> str:
    return _try(lambda: fp.reference_field.text.value, "") or ""


def serialize_footprint(fp, include_pads: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": item_id(fp),
        "reference": fp_reference(fp),
        "value": _try(lambda: fp.value_field.text.value, ""),
        "position_mm": _try(lambda: vec_dict(fp.position), {}),
        "orientation_deg": _try(lambda: fp.orientation.degrees, 0.0),
        "layer": _try(lambda: layer_to_name(fp.layer), "unknown"),
        "locked": _try(lambda: fp.locked, False),
        "library_id": _try(lambda: str(fp.definition.id), ""),
    }
    if include_pads:
        data["pads"] = [serialize_pad(p) for p in _try(lambda: _fp_pads(fp), [])]
    return data


def _fp_pads(fp):
    from kipy.board_types import Pad

    return [it for it in fp.definition.items if isinstance(it, Pad)]


def serialize_pad(pad) -> dict[str, Any]:
    from kipy.proto.board.board_types_pb2 import PadType

    return {
        "id": item_id(pad),
        "number": _try(lambda: pad.number, ""),
        "net": _try(lambda: pad.net.name, ""),
        "position_mm": _try(lambda: vec_dict(pad.position), {}),
        "type": _try(lambda: PadType.Name(pad.pad_type), ""),
    }


def serialize_track(t) -> dict[str, Any]:
    from kipy.board_types import ArcTrack

    if isinstance(t, ArcTrack):
        return {
            "id": item_id(t),
            "kind": "arc",
            "start_mm": _try(lambda: vec_dict(t.start), {}),
            "mid_mm": _try(lambda: vec_dict(t.mid), {}),
            "end_mm": _try(lambda: vec_dict(t.end), {}),
            "width_mm": _try(lambda: nm_to_mm(t.width), 0),
            "layer": _try(lambda: layer_to_name(t.layer), ""),
            "net": _try(lambda: t.net.name, ""),
            "locked": _try(lambda: t.locked, False),
        }
    return {
        "id": item_id(t),
        "kind": "track",
        "start_mm": _try(lambda: vec_dict(t.start), {}),
        "end_mm": _try(lambda: vec_dict(t.end), {}),
        "width_mm": _try(lambda: nm_to_mm(t.width), 0),
        "length_mm": _try(lambda: nm_to_mm(int(t.length())), 0),
        "layer": _try(lambda: layer_to_name(t.layer), ""),
        "net": _try(lambda: t.net.name, ""),
        "locked": _try(lambda: t.locked, False),
    }


def serialize_via(v) -> dict[str, Any]:
    from kipy.proto.board.board_types_pb2 import ViaType

    return {
        "id": item_id(v),
        "position_mm": _try(lambda: vec_dict(v.position), {}),
        "diameter_mm": _try(lambda: nm_to_mm(v.diameter), 0),
        "drill_mm": _try(lambda: nm_to_mm(v.drill_diameter), 0),
        "net": _try(lambda: v.net.name, ""),
        "type": _try(lambda: ViaType.Name(v.type), ""),
        "locked": _try(lambda: v.locked, False),
    }


def serialize_zone(z) -> dict[str, Any]:
    from kipy.proto.board.board_types_pb2 import ZoneType

    return {
        "id": item_id(z),
        "name": _try(lambda: z.name, ""),
        "type": _try(lambda: ZoneType.Name(z.type), ""),
        "net": _try(lambda: (z.net.name if z.net is not None else ""), ""),
        "layers": _try(lambda: [layer_to_name(l) for l in z.layers], []),
        "priority": _try(lambda: z.priority, 0),
        "filled": _try(lambda: z.filled, False),
        "locked": _try(lambda: z.locked, False),
    }


def serialize_shape(s) -> dict[str, Any]:
    base = {
        "id": item_id(s),
        "type": type(s).__name__,
        "layer": _try(lambda: layer_to_name(s.layer), ""),
        "locked": _try(lambda: s.locked, False),
    }
    for attr in ("start", "mid", "end", "center", "radius_point", "top_left",
                 "bottom_right", "control1", "control2"):
        val = _try(lambda a=attr: vec_dict(getattr(s, a)), None)
        if val is not None:
            base[attr + "_mm"] = val
    polys = _try(lambda: getattr(s, "polygons"), None)
    if polys is not None:
        base["polygon_points_mm"] = _try(
            lambda: [
                [vec_dict(n.point) for n in poly.outline if n.has_point]
                for poly in polys
            ],
            [],
        )
    return base


def serialize_text(t) -> dict[str, Any]:
    return {
        "id": item_id(t),
        "type": type(t).__name__,
        "value": _try(lambda: t.value, ""),
        "position_mm": _try(lambda: vec_dict(t.position), None),
        "layer": _try(lambda: layer_to_name(t.layer), ""),
        "locked": _try(lambda: t.locked, False),
    }


def serialize_dimension(d) -> dict[str, Any]:
    return {
        "id": item_id(d),
        "type": type(d).__name__,
        "layer": _try(lambda: layer_to_name(d.layer), ""),
        "text": _try(lambda: d.text.value, ""),
        "override_text": _try(lambda: d.override_text, ""),
    }


def serialize_net(n) -> dict[str, Any]:
    return {"name": _try(lambda: n.name, "")}
