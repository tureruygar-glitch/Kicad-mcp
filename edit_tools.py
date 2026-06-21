"""Tools that modify existing board items: move, rotate, lock, delete, select."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from kicad10_mcp.connection import commit, require_board
from kicad10_mcp.helpers import fp_reference, item_id, kiid, vmm
from kipy.geometry import Angle


def _find_footprint(board, reference: str):
    for fp in board.get_footprints():
        if fp_reference(fp) == reference:
            return fp
    raise ValueError(f"Footprint '{reference}' not found on the board.")


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def move_footprint(reference: str, x_mm: float, y_mm: float,
                       angle_deg: float | None = None) -> str:
        """Move a footprint to an absolute position, optionally setting its rotation.

        Args:
            reference: Reference designator, e.g. 'R1'.
            x_mm: Target X in millimetres.
            y_mm: Target Y in millimetres.
            angle_deg: Optional absolute rotation in degrees.
        """
        board = require_board()
        fp = _find_footprint(board, reference)
        with commit(board, f"Move {reference}"):
            fp.position = vmm(x_mm, y_mm)
            if angle_deg is not None:
                fp.orientation = Angle.from_degrees(float(angle_deg))
            board.update_items([fp])
        suffix = f" @ {angle_deg} deg" if angle_deg is not None else ""
        return f"Moved {reference} to ({x_mm}, {y_mm}) mm{suffix}."

    @mcp.tool()
    def rotate_footprint(reference: str, angle_deg: float) -> str:
        """Set a footprint's absolute rotation in degrees.

        Args:
            reference: Reference designator, e.g. 'U1'.
            angle_deg: Absolute angle in degrees (0, 90, 180, 270, ...).
        """
        board = require_board()
        fp = _find_footprint(board, reference)
        with commit(board, f"Rotate {reference}"):
            fp.orientation = Angle.from_degrees(float(angle_deg))
            board.update_items([fp])
        return f"Rotated {reference} to {angle_deg} deg."

    @mcp.tool()
    def set_footprint_locked(reference: str, locked: bool) -> str:
        """Lock or unlock a footprint.

        Args:
            reference: Reference designator.
            locked: True to lock, False to unlock.
        """
        board = require_board()
        fp = _find_footprint(board, reference)
        with commit(board, f"{'Lock' if locked else 'Unlock'} {reference}"):
            fp.locked = bool(locked)
            board.update_items([fp])
        return f"{reference} {'locked' if locked else 'unlocked'}."

    @mcp.tool()
    def set_footprint_value(reference: str, value: str) -> str:
        """Set the value field text of a footprint (e.g. '10k', '100nF').

        Args:
            reference: Reference designator.
            value: New value string.
        """
        board = require_board()
        fp = _find_footprint(board, reference)
        with commit(board, f"Set value of {reference}"):
            field = fp.value_field
            field.text.value = value
            fp.value_field = field
            board.update_items([fp])
        return f"Set {reference} value to '{value}'."

    @mcp.tool()
    def batch_move_footprints(moves: list[dict[str, Any]]) -> dict[str, Any]:
        """Move/rotate several footprints in a single undo step.

        Args:
            moves: List of objects with keys 'reference', 'x_mm', 'y_mm', and
                optional 'angle_deg'. Example:
                [{"reference": "R1", "x_mm": 100, "y_mm": 95},
                 {"reference": "C1", "x_mm": 102, "y_mm": 95, "angle_deg": 90}]
        """
        board = require_board()
        fp_map = {fp_reference(fp): fp for fp in board.get_footprints()}
        moved, skipped, to_update = [], [], []
        for mv in moves:
            ref = mv.get("reference", "")
            fp = fp_map.get(ref)
            if fp is None:
                skipped.append(ref)
                continue
            fp.position = vmm(float(mv["x_mm"]), float(mv["y_mm"]))
            if mv.get("angle_deg") is not None:
                fp.orientation = Angle.from_degrees(float(mv["angle_deg"]))
            to_update.append(fp)
            moved.append(ref)
        if to_update:
            with commit(board, "Batch move footprints"):
                board.update_items(to_update)
        return {"moved": moved, "skipped": skipped}

    @mcp.tool()
    def set_items_locked(item_ids: list[str], locked: bool) -> dict[str, Any]:
        """Lock or unlock arbitrary board items by ID.

        Args:
            item_ids: Item KIID strings.
            locked: True to lock, False to unlock.
        """
        board = require_board()
        items = list(board.get_items_by_id([kiid(i) for i in item_ids]))
        updated = []
        with commit(board, "Set lock state"):
            for it in items:
                try:
                    it.locked = bool(locked)
                    updated.append(it)
                except Exception:  # noqa: BLE001 - item type may not support lock
                    pass
            if updated:
                board.update_items(updated)
        return {"updated": [item_id(i) for i in updated]}

    @mcp.tool()
    def delete_items(item_ids: list[str]) -> str:
        """Delete board items by their KIID strings (single undo step).

        Args:
            item_ids: Item KIID strings from any list_* tool.
        """
        board = require_board()
        with commit(board, "Delete items"):
            board.remove_items_by_id([kiid(i) for i in item_ids])
        return f"Deleted {len(item_ids)} item(s)."

    @mcp.tool()
    def select_items(item_ids: list[str], add_to_existing: bool = False) -> dict[str, Any]:
        """Select board items by ID in the PCB editor.

        Args:
            item_ids: Item KIID strings.
            add_to_existing: Keep the current selection and add to it.
        """
        board = require_board()
        if not add_to_existing:
            board.clear_selection()
        items = list(board.get_items_by_id([kiid(i) for i in item_ids]))
        selection = board.add_to_selection(items)
        return {"selected_count": len(selection)}

    @mcp.tool()
    def clear_selection() -> str:
        """Clear the current selection in the PCB editor."""
        require_board().clear_selection()
        return "Selection cleared."

    @mcp.tool()
    def get_selection() -> list[dict[str, Any]]:
        """Return the items currently selected in the PCB editor (id + type)."""
        board = require_board()
        return [{"id": item_id(i), "type": type(i).__name__}
                for i in board.get_selection()]
