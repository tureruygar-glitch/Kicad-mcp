"""Fabrication / output tools driven by kicad-cli.

These operate on files on disk. Because kicad-cli reads the saved file (not the
in-memory editor state), tools save the open document first by default so the
output reflects recent edits. Pass save_first=False to export the last saved
state instead.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from kicad10_mcp.connection import get_kicad, require_board, require_schematic

_CLI_TIMEOUT = 600  # seconds


def _kicad_cli() -> str:
    """Locate kicad-cli: ask KiCad, then PATH, then the default Windows install."""
    try:
        path = get_kicad().get_kicad_binary_path("kicad-cli")
        if path and os.path.exists(path):
            return path
    except Exception:  # noqa: BLE001
        pass
    which = shutil.which("kicad-cli") or shutil.which("kicad-cli.exe")
    if which:
        return which
    for candidate in (
        r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe",
        r"C:\Program Files\KiCad\bin\kicad-cli.exe",
    ):
        if os.path.exists(candidate):
            return candidate
    raise RuntimeError(
        "kicad-cli was not found. Provide its path or ensure KiCad 10 is installed."
    )


def _run(args: list[str]) -> dict[str, Any]:
    cli = _kicad_cli()
    proc = subprocess.run(
        [cli, *args], capture_output=True, text=True, timeout=_CLI_TIMEOUT
    )
    return {
        "command": " ".join([os.path.basename(cli), *args]),
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "ok": proc.returncode == 0,
    }


def _pcb_path(save_first: bool) -> str:
    board = require_board()
    if save_first:
        board.save()
    path = board.name
    if not path:
        raise RuntimeError(
            "The open board has no file path yet. Save it once in KiCad first."
        )
    return path


def _sch_path(save_first: bool, override: str = "") -> str:
    if override:
        return override
    # Save the open schematic if there is one.
    try:
        sch = require_schematic()
        if save_first:
            sch.save()
    except Exception:  # noqa: BLE001 - no schematic open; derive from the PCB
        pass
    pcb = require_board().name
    if pcb:
        base, _ = os.path.splitext(pcb)
        candidate = base + ".kicad_sch"
        if os.path.exists(candidate):
            return candidate
    raise RuntimeError(
        "Could not locate a .kicad_sch file. Pass schematic_path explicitly."
    )


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def run_kicad_cli(args: list[str]) -> dict[str, Any]:
        """Run an arbitrary kicad-cli command (escape hatch for any export).

        Args:
            args: Argument list after the binary, e.g.
                ["pcb", "export", "gerbers", "board.kicad_pcb", "-o", "out/"] or
                ["version"]. Use the currently open board's path from get_board_summary.
        """
        return _run(args)

    # ------------------------------------------------------------- PCB outputs

    @mcp.tool()
    def export_gerbers(output_dir: str, layers: str = "",
                       save_first: bool = True) -> dict[str, Any]:
        """Export Gerber files for the open PCB into a directory.

        Args:
            output_dir: Destination directory (created if needed).
            layers: Optional comma-separated layer list, e.g. 'F.Cu,B.Cu,Edge.Cuts'.
                Empty exports all enabled plottable layers.
            save_first: Save the board before exporting.
        """
        os.makedirs(output_dir, exist_ok=True)
        args = ["pcb", "export", "gerbers", _pcb_path(save_first), "-o", output_dir]
        if layers:
            args += ["--layers", layers]
        return _run(args)

    @mcp.tool()
    def export_drill(output_dir: str, save_first: bool = True) -> dict[str, Any]:
        """Export drill files (Excellon) for the open PCB into a directory.

        Args:
            output_dir: Destination directory (created if needed).
            save_first: Save the board before exporting.
        """
        os.makedirs(output_dir, exist_ok=True)
        return _run(["pcb", "export", "drill", _pcb_path(save_first), "-o", output_dir])

    @mcp.tool()
    def export_step(output_path: str, save_first: bool = True) -> dict[str, Any]:
        """Export a 3D STEP model of the open PCB.

        Args:
            output_path: Destination .step/.stp file.
            save_first: Save the board before exporting.
        """
        return _run(["pcb", "export", "step", _pcb_path(save_first),
                     "-o", output_path, "--force"])

    @mcp.tool()
    def export_pdf(output_path: str, layers: str = "F.Cu,B.Cu,Edge.Cuts",
                   save_first: bool = True) -> dict[str, Any]:
        """Export a PDF plot of selected PCB layers.

        Args:
            output_path: Destination .pdf file.
            layers: Comma-separated layers to plot.
            save_first: Save the board before exporting.
        """
        args = ["pcb", "export", "pdf", _pcb_path(save_first), "-o", output_path]
        if layers:
            args += ["--layers", layers]
        return _run(args)

    @mcp.tool()
    def export_svg(output_path: str, layers: str = "F.Cu,B.Cu,Edge.Cuts",
                   save_first: bool = True) -> dict[str, Any]:
        """Export an SVG plot of selected PCB layers.

        Args:
            output_path: Destination .svg file.
            layers: Comma-separated layers to plot.
            save_first: Save the board before exporting.
        """
        args = ["pcb", "export", "svg", _pcb_path(save_first), "-o", output_path]
        if layers:
            args += ["--layers", layers]
        return _run(args)

    @mcp.tool()
    def export_pos(output_path: str, fmt: str = "csv", side: str = "both",
                   save_first: bool = True) -> dict[str, Any]:
        """Export a component placement (pick-and-place) file.

        Args:
            output_path: Destination file.
            fmt: 'csv', 'ascii', or 'gerber'.
            side: 'front', 'back', or 'both'.
            save_first: Save the board before exporting.
        """
        return _run(["pcb", "export", "pos", _pcb_path(save_first),
                     "-o", output_path, "--format", fmt, "--side", side])

    @mcp.tool()
    def render_3d(output_path: str, side: str = "top", width: int = 1600,
                  height: int = 900, save_first: bool = True) -> dict[str, Any]:
        """Render a 3D image (PNG) of the open PCB.

        Args:
            output_path: Destination .png file.
            side: 'top', 'bottom', 'left', 'right', 'front', 'back'.
            width, height: Image dimensions in pixels.
            save_first: Save the board before rendering.
        """
        return _run(["pcb", "render", _pcb_path(save_first), "-o", output_path,
                     "--side", side, "-w", str(width), "-r", str(height)])

    @mcp.tool()
    def run_drc(output_path: str = "", save_first: bool = True) -> dict[str, Any]:
        """Run Design Rule Check on the open PCB and summarise the results.

        Args:
            output_path: Optional .json report path (a temp file is used if empty).
            save_first: Save the board before checking.
        """
        report = output_path or os.path.join(tempfile.gettempdir(), "kicad_drc_report.json")
        result = _run(["pcb", "drc", _pcb_path(save_first), "-o", report,
                       "--format", "json", "--severity-all"])
        result["report_path"] = report
        result.update(_summarize_report(report))
        return result

    # -------------------------------------------------------- schematic outputs

    @mcp.tool()
    def export_bom(output_path: str, schematic_path: str = "",
                   save_first: bool = True) -> dict[str, Any]:
        """Export a bill of materials (CSV) from the open schematic.

        Args:
            output_path: Destination .csv file.
            schematic_path: Explicit .kicad_sch path (defaults to the open project's).
            save_first: Save the schematic before exporting.
        """
        return _run(["sch", "export", "bom", _sch_path(save_first, schematic_path),
                     "-o", output_path])

    @mcp.tool()
    def export_netlist(output_path: str, schematic_path: str = "",
                       save_first: bool = True) -> dict[str, Any]:
        """Export a netlist from the open schematic.

        Args:
            output_path: Destination netlist file (e.g. .net).
            schematic_path: Explicit .kicad_sch path (defaults to the open project's).
            save_first: Save the schematic before exporting.
        """
        return _run(["sch", "export", "netlist",
                     _sch_path(save_first, schematic_path), "-o", output_path])

    @mcp.tool()
    def run_erc(output_path: str = "", schematic_path: str = "",
                save_first: bool = True) -> dict[str, Any]:
        """Run Electrical Rule Check on the open schematic and summarise the results.

        Args:
            output_path: Optional .json report path (a temp file is used if empty).
            schematic_path: Explicit .kicad_sch path (defaults to the open project's).
            save_first: Save the schematic before checking.
        """
        report = output_path or os.path.join(tempfile.gettempdir(), "kicad_erc_report.json")
        result = _run(["sch", "erc", _sch_path(save_first, schematic_path),
                       "-o", report, "--format", "json", "--severity-all"])
        result["report_path"] = report
        result.update(_summarize_report(report))
        return result


def _summarize_report(path: str) -> dict[str, Any]:
    """Parse a kicad-cli DRC/ERC JSON report into severity counts."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        return {"summary_error": f"Could not parse report: {exc}"}
    counts: dict[str, int] = {}
    total = 0
    for key in ("violations", "unconnected_items", "schematic_parity"):
        for entry in data.get(key, []) or []:
            total += 1
            sev = str(entry.get("severity", "unknown"))
            counts[sev] = counts.get(sev, 0) + 1
    return {"total_issues": total, "issues_by_severity": counts}
