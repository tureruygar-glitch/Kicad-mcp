"""System / connection / document-lifecycle tools."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from kicad10_mcp.connection import get_kicad, require_board
from kipy.proto.common.types import DocumentType


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def kicad_status() -> dict[str, Any]:
        """Report whether KiCad is reachable, its version, and which documents are open.

        Use this first to confirm connectivity before other operations.
        """
        try:
            kicad = get_kicad(force_reconnect=True)
        except RuntimeError as exc:
            return {"connected": False, "error": str(exc)}
        version = kicad.get_version()
        pcbs = kicad.get_open_documents(DocumentType.DOCTYPE_PCB)
        schs = kicad.get_open_documents(DocumentType.DOCTYPE_SCHEMATIC)
        return {
            "connected": True,
            "version": str(version),
            "api_library_version": str(kicad.get_api_version()),
            "open_pcbs": [d.board_filename for d in pcbs],
            "open_schematic_count": len(schs),
        }

    @mcp.tool()
    def get_version() -> dict[str, str]:
        """Return the connected KiCad version and the kicad-python API version."""
        kicad = get_kicad()
        return {
            "kicad_version": str(kicad.get_version()),
            "api_library_version": str(kicad.get_api_version()),
        }

    @mcp.tool()
    def ping() -> str:
        """Ping the KiCad API server. Returns 'pong' on success."""
        get_kicad().ping()
        return "pong"

    @mcp.tool()
    def list_open_documents() -> dict[str, Any]:
        """List all documents currently open in KiCad, grouped by type."""
        kicad = get_kicad()
        result: dict[str, Any] = {}
        for label, doc_type in (
            ("pcb", DocumentType.DOCTYPE_PCB),
            ("schematic", DocumentType.DOCTYPE_SCHEMATIC),
            ("project", DocumentType.DOCTYPE_PROJECT),
        ):
            docs = kicad.get_open_documents(doc_type)
            result[label] = [
                getattr(d, "board_filename", "") or getattr(d, "project", "") or str(d)
                for d in docs
            ]
        return result

    @mcp.tool()
    def save_board() -> str:
        """Save the currently open PCB to disk."""
        require_board().save()
        return "Board saved."

    @mcp.tool()
    def save_board_as(file_path: str, overwrite: bool = False,
                      include_project: bool = True) -> str:
        """Save a copy of the open PCB to a new path.

        Args:
            file_path: Destination .kicad_pcb path.
            overwrite: Overwrite if the file already exists.
            include_project: Also write the associated project file.
        """
        require_board().save_as(file_path, overwrite=overwrite,
                                include_project=include_project)
        return f"Board saved to {file_path}."

    @mcp.tool()
    def revert_board() -> str:
        """Discard unsaved changes and reload the open PCB from disk."""
        require_board().revert()
        return "Board reverted to last saved state."

    @mcp.tool()
    def run_action(action: str) -> dict[str, Any]:
        """Run an arbitrary KiCad tool action by name (power user / unstable API).

        Example actions: 'pcbnew.InteractiveRouter.routeSingleTrack',
        'pcbnew.EditTool.Rotate', 'common.Control.zoomFitScreen'. Action names are
        not guaranteed stable across KiCad versions and may have side effects.

        Args:
            action: The KiCad TOOL_ACTION name to invoke.
        """
        response = get_kicad().run_action(action)
        return {"action": action, "status": str(getattr(response, "status", response))}

    @mcp.tool()
    def get_kicad_binary_path(binary_name: str = "kicad-cli") -> str:
        """Return the full path to a bundled KiCad binary (e.g. 'kicad-cli').

        Args:
            binary_name: Short binary name; '.exe' is assumed on Windows.
        """
        return get_kicad().get_kicad_binary_path(binary_name)
