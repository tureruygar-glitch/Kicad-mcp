"""The execute_kipy escape hatch: run arbitrary kipy Python against live KiCad.

This is the ultimate full-control tool. It executes whatever Python it is given
in-process, with the connected KiCad client and current documents pre-bound. Use
it for anything the structured tools do not cover.
"""

from __future__ import annotations

import contextlib
import io
import json
import traceback
from typing import Any

from mcp.server.fastmcp import FastMCP

from kicad10_mcp.connection import commit, get_kicad


def _json_safe(obj: Any) -> Any:
    try:
        return json.loads(json.dumps(obj, default=str))
    except Exception:  # noqa: BLE001
        return repr(obj)


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def execute_kipy(code: str, autosave: bool = False) -> dict[str, Any]:
        """Run arbitrary Python against the live KiCad session (FULL-CONTROL escape hatch).

        Use this for anything not covered by a dedicated tool. The code runs
        in-process with these names pre-bound:
          - kicad: connected kipy.KiCad instance
          - board: the open Board, or None if no PCB is open
          - schematic: the open Schematic, or None
          - kipy, board_types, geometry, commit
          - Vector2, Angle, BoardLayer, KiCadObjectType

        Conventions:
          - Internal units are nanometres; build points with Vector2.from_xy_mm(x, y).
          - Group board edits in a single undo step:
                with commit(board, "my change"):
                    board.create_items(item)
          - Assign a variable named `result` to return structured data; anything
            printed to stdout is also captured.

        Args:
            code: Python source to execute.
            autosave: If True and a board is open, save it after the code runs.

        Example:
            code = '''
            from kipy.board_types import Track
            from kipy.geometry import Vector2
            t = Track()
            t.start = Vector2.from_xy_mm(10, 10)
            t.end = Vector2.from_xy_mm(20, 10)
            t.width = 250000  # 0.25 mm in nm
            t.layer = BoardLayer.BL_F_Cu
            with commit(board, "api track"):
                created = board.create_items(t)
            result = [c.id.value for c in created]
            '''
        """
        kicad = get_kicad()

        board = None
        try:
            board = kicad.get_board()
        except Exception:  # noqa: BLE001 - no PCB open
            pass

        schematic = None
        try:
            from kicad10_mcp.connection import require_schematic

            schematic = require_schematic()
        except Exception:  # noqa: BLE001 - no schematic open
            pass

        import kipy
        from kipy import board_types, geometry
        from kipy.board_types import BoardLayer
        from kipy.geometry import Angle, Vector2
        from kipy.proto.common.types import KiCadObjectType

        namespace: dict[str, Any] = {
            "kicad": kicad,
            "board": board,
            "schematic": schematic,
            "kipy": kipy,
            "board_types": board_types,
            "geometry": geometry,
            "commit": commit,
            "Vector2": Vector2,
            "Angle": Angle,
            "BoardLayer": BoardLayer,
            "KiCadObjectType": KiCadObjectType,
            "result": None,
        }

        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                exec(code, namespace)  # noqa: S102 - intentional, user-authorized
        except Exception:  # noqa: BLE001
            return {
                "ok": False,
                "stdout": buffer.getvalue(),
                "error": traceback.format_exc(),
            }

        if autosave and namespace.get("board") is not None:
            try:
                namespace["board"].save()
            except Exception as exc:  # noqa: BLE001
                return {
                    "ok": True,
                    "stdout": buffer.getvalue(),
                    "result": _json_safe(namespace.get("result")),
                    "autosave_error": str(exc),
                }

        return {
            "ok": True,
            "stdout": buffer.getvalue(),
            "result": _json_safe(namespace.get("result")),
        }
