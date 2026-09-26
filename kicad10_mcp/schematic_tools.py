"""Schematic tools.

Schematic API coverage in KiCad 10 is narrower than the PCB API (full symbol /
sheet / hierarchy reads land in KiCad 11). These tools degrade gracefully: each
returns an explanatory error rather than crashing if the running build does not
support a given call. For anything unsupported, use execute_kipy.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from kicad10_mcp.connection import commit, require_schematic
from kicad10_mcp.helpers import _try, item_id, vec_dict, vmm


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def get_schematic_summary() -> dict[str, Any]:
        """Return counts of symbols, labels, text, lines, and sheets in the open schematic."""
        sch = require_schematic()
        return {
            "name": _try(lambda: sch.name, ""),
            "symbols": _try(lambda: len(sch.get_symbols()), "unsupported"),
            "labels": _try(lambda: len(sch.get_labels()), "unsupported"),
            "text": _try(lambda: len(sch.get_text()), "unsupported"),
            "lines": _try(lambda: len(sch.get_lines()), "unsupported"),
            "shapes": _try(lambda: len(sch.get_shapes()), "unsupported"),
            "sheet_symbols": _try(lambda: len(sch.get_sheet_symbols()), "unsupported"),
        }

    @mcp.tool()
    def list_symbols() -> list[dict[str, Any]]:
        """List symbol instances in the open schematic (reference, value, position).

        Note: requires KiCad's schematic symbol API (KiCad 11+); on KiCad 10 this
        may return an error — fall back to execute_kipy or read the .kicad_sch file.
        """
        sch = require_schematic()
        try:
            symbols = sch.get_symbols()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "Symbol enumeration is not available in this KiCad build "
                f"({exc}). It requires the KiCad 11 schematic API."
            )
        out: list[dict[str, Any]] = []
        for sym in symbols:
            out.append({
                "id": item_id(sym),
                "reference": _try(lambda sym=sym: sym.definition.reference_field.text.value, ""),
                "value": _try(lambda sym=sym: sym.definition.value_field.text.value, ""),
                "library_id": _try(lambda sym=sym: str(sym.definition.id), ""),
                "position_mm": _try(lambda sym=sym: vec_dict(sym.position), {}),
            })
        return out

    @mcp.tool()
    def list_labels() -> list[dict[str, Any]]:
        """List labels (local/global/hierarchical) in the open schematic."""
        sch = require_schematic()
        out: list[dict[str, Any]] = []
        for lbl in sch.get_labels():
            out.append({
                "id": item_id(lbl),
                "type": type(lbl).__name__,
                "text": _try(lambda lbl=lbl: lbl.text.value, ""),
                "position_mm": _try(lambda lbl=lbl: vec_dict(lbl.position), {}),
            })
        return out

    @mcp.tool()
    def list_schematic_text() -> list[dict[str, Any]]:
        """List free text objects in the open schematic."""
        sch = require_schematic()
        out: list[dict[str, Any]] = []
        for txt in sch.get_text():
            out.append({
                "id": item_id(txt),
                "type": type(txt).__name__,
                "value": _try(lambda txt=txt: txt.value, ""),
                "position_mm": _try(lambda txt=txt: vec_dict(txt.position), None),
            })
        return out

    @mcp.tool()
    def get_schematic_hierarchy() -> list[dict[str, Any]]:
        """Return the sheet hierarchy of the open schematic (KiCad 11+ feature)."""
        sch = require_schematic()
        try:
            sheets = sch.get_hierarchy()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Schematic hierarchy is not available in this KiCad build ({exc})."
            )

        def serialize(node) -> dict[str, Any]:
            return {
                "name": _try(lambda: node.name, ""),
                "page_number": _try(lambda: node.page_number, ""),
                "filename": _try(lambda: node.filename, ""),
                "children": [serialize(c) for c in _try(lambda: node.children, [])],
            }

        return [serialize(s) for s in sheets]

    @mcp.tool()
    def add_schematic_text(text: str, x_mm: float, y_mm: float) -> dict[str, Any]:
        """Add a free text object to the open schematic.

        Args:
            text: Text string.
            x_mm, y_mm: Position in mm.
        """
        from kipy.schematic_types import SchematicText

        sch = require_schematic()
        st = SchematicText()
        st.value = text
        st.position = vmm(x_mm, y_mm)
        with commit(sch, "Add schematic text"):
            created = sch.create_items(st)
        return {"created_ids": [item_id(c) for c in created]}

    @mcp.tool()
    def add_local_label(text: str, x_mm: float, y_mm: float) -> dict[str, Any]:
        """Add a local net label to the open schematic.

        Args:
            text: Label text (the net name).
            x_mm, y_mm: Position in mm.
        """
        from kipy.common_types import Text
        from kipy.schematic_types import LocalLabel

        sch = require_schematic()
        label = LocalLabel()
        t = Text()
        t.value = text
        t.position = vmm(x_mm, y_mm)
        label.text = t
        label.position = vmm(x_mm, y_mm)
        with commit(sch, "Add local label"):
            created = sch.create_items(label)
        return {"created_ids": [item_id(c) for c in created]}

    @mcp.tool()
    def save_schematic() -> str:
        """Save the open schematic to disk."""
        require_schematic().save()
        return "Schematic saved."
