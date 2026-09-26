"""Project-level tools: text variables, title block, project info."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from kicad10_mcp.connection import require_board


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def get_project_info() -> dict[str, Any]:
        """Return the open project's name and path."""
        project = require_board().get_project()
        return {"name": project.name, "path": project.path}

    @mcp.tool()
    def get_text_variables() -> dict[str, str]:
        """Return the project's text variables (used in ${VAR} substitutions)."""
        tv = require_board().get_project().get_text_variables()
        return dict(tv.items())

    @mcp.tool()
    def set_text_variable(name: str, value: str) -> dict[str, str]:
        """Set (or add) one project text variable, merging with existing variables.

        Args:
            name: Variable name (referenced as ${name}).
            value: Variable value.
        """
        project = require_board().get_project()
        tv = project.get_text_variables()
        tv[name] = value
        project.set_text_variables(tv)
        return dict(tv.items())

    @mcp.tool()
    def expand_text(text: str) -> str:
        """Expand ${...} text variables in a string using the project's values.

        Args:
            text: Text possibly containing ${VAR} references.
        """
        return require_board().expand_text_variables(text)

    @mcp.tool()
    def get_title_block() -> dict[str, Any]:
        """Return the board's title block fields (title, date, revision, company, comments)."""
        tb = require_board().get_title_block_info()
        proto = tb.proto
        out: dict[str, Any] = {}
        for field in proto.DESCRIPTOR.fields:
            try:
                value = getattr(proto, field.name)
                if isinstance(value, (str, int, float, bool)):
                    out[field.name] = value
            except Exception:  # noqa: BLE001
                pass
        return out

    @mcp.tool()
    def set_title_block(fields: dict[str, str]) -> dict[str, Any]:
        """Update title block string fields on the board.

        Args:
            fields: Map of field name to value, e.g. {"title": "My Board",
                "revision": "B", "company": "Acme", "date": "2026-05-23"}.
                Valid field names are returned by get_title_block.
        """
        board = require_board()
        tb = board.get_title_block_info()
        proto = tb.proto
        valid = {f.name for f in proto.DESCRIPTOR.fields}
        applied: dict[str, Any] = {}
        for key, value in fields.items():
            if key not in valid:
                continue
            try:
                setattr(proto, key, value)
                applied[key] = value
            except Exception:  # noqa: BLE001 - skip non-scalar fields
                pass
        board.set_title_block_info(tb)
        return {"applied": applied}
