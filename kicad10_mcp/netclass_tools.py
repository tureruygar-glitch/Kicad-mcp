"""Net class setup driven by current: IPC-2221 track sizing plus .kicad_pro editing.

KiCad's IPC API can read net classes but cannot assign nets to them (the
assignment patterns only live in the project file), so classes and patterns are
written to ``<project>.kicad_pro`` directly. KiCad keeps project settings in
memory and writes them back on save, so the file is only edited while the
project is closed in KiCad; otherwise the change would be silently overwritten.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

# IPC-2221 constants.
_K_EXTERNAL = 0.048
_K_INTERNAL = 0.024
_MIL_PER_OZ = 1.378            # copper thickness per oz/ft^2, in mils
_UM_PER_OZ = 35.0              # same, in micrometres
_MM_PER_MIL = 0.0254
_RHO_CU = 1.72e-8              # ohm*m at 20 C
_ALPHA_CU = 0.00393            # 1/C

# Defaults KiCad writes for a new class; anything not given falls back to these.
_CLASS_TEMPLATE = {
    "bus_width": 12,
    "clearance": 0.2,
    "diff_pair_gap": 0.25,
    "diff_pair_via_gap": 0.25,
    "diff_pair_width": 0.2,
    "line_style": 0,
    "microvia_diameter": 0.3,
    "microvia_drill": 0.1,
    "pcb_color": "rgba(0, 0, 0, 0.000)",
    "priority": 0,
    "schematic_color": "rgba(0, 0, 0, 0.000)",
    "track_width": 0.25,
    "tuning_profile": "",
    "via_diameter": 0.6,
    "via_drill": 0.3,
    "wire_width": 6,
}


def ipc2221_width_mm(current_a: float, temp_rise_c: float = 10.0, copper_oz: float = 1.0,
                     internal: bool = False) -> float:
    """Minimum track width (mm) to carry ``current_a`` with ``temp_rise_c`` of heating."""
    if current_a <= 0 or temp_rise_c <= 0 or copper_oz <= 0:
        raise ValueError("current, temperature rise and copper weight must be positive.")
    k = _K_INTERNAL if internal else _K_EXTERNAL
    area_mils2 = (current_a / (k * temp_rise_c ** 0.44)) ** (1 / 0.725)
    return area_mils2 / (_MIL_PER_OZ * copper_oz) * _MM_PER_MIL


def track_resistance_ohm(width_mm: float, length_mm: float, copper_oz: float = 1.0,
                         temp_c: float = 20.0) -> float:
    thickness_m = _UM_PER_OZ * copper_oz * 1e-6
    rho = _RHO_CU * (1 + _ALPHA_CU * (temp_c - 20.0))
    return rho * (length_mm / 1000) / ((width_mm / 1000) * thickness_m)


def _round_up(value: float, step: float = 0.05) -> float:
    return round(math.ceil(value / step - 1e-9) * step, 3)


# ---------------------------------------------------------------------------
# Project file
# ---------------------------------------------------------------------------

def _project_file(project_path: str) -> Path:
    if project_path:
        p = Path(project_path)
        if p.is_dir():
            found = sorted(p.glob("*.kicad_pro"))
            if not found:
                raise FileNotFoundError(f"No .kicad_pro file in {p}.")
            p = found[0]
        elif p.suffix != ".kicad_pro":
            p = p.with_suffix(".kicad_pro")
        if not p.exists():
            raise FileNotFoundError(f"{p} does not exist.")
        return p
    try:
        from kicad10_mcp.connection import require_board

        proj = require_board().get_project()
        p = Path(proj.path) / f"{proj.name}.kicad_pro"
        if p.exists():
            return p
    except Exception:  # noqa: BLE001 - fall through to a clear message
        pass
    raise ValueError("Pass project_path (the .kicad_pro file or its folder); it could not "
                     "be taken from KiCad.")


def _open_in_kicad(pro: Path) -> Optional[str]:
    """Name of the editor that has this project open, or None."""
    try:
        from kicad10_mcp.connection import get_kicad
        from kipy.proto.common.types import DocumentType

        kicad = get_kicad()
    except Exception:  # noqa: BLE001 - KiCad not running: safe to edit
        return None
    for doc_type, label in ((DocumentType.DOCTYPE_PCB, "PCB Editor"),
                            (DocumentType.DOCTYPE_SCHEMATIC, "Schematic Editor")):
        try:
            docs = kicad.get_open_documents(doc_type)
        except Exception:  # noqa: BLE001 - editor not open / no handler
            continue
        for d in docs:
            proj_path = getattr(getattr(d, "project", None), "path", "") or ""
            if proj_path and Path(proj_path).resolve() == pro.parent.resolve():
                return label
            if not proj_path:
                return label  # can't tell which project; be safe
    return None


def _load(pro: Path) -> dict[str, Any]:
    text = pro.read_text(encoding="utf-8").strip()
    return json.loads(text) if text else {}


def _net_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    ns = cfg.setdefault("net_settings", {})
    ns.setdefault("classes", [])
    ns.setdefault("meta", {"version": 5})
    ns.setdefault("net_colors", None)
    ns.setdefault("netclass_assignments", None)
    if ns.get("netclass_patterns") is None:
        ns["netclass_patterns"] = []
    if not any(c.get("name") == "Default" for c in ns["classes"]):
        default = dict(_CLASS_TEMPLATE, name="Default", priority=2147483647)
        ns["classes"].insert(0, default)
    return ns


def _patterns_for(net: str) -> list[str]:
    """KiCad names root-sheet label nets '/NAME'; match both spellings for plain names."""
    if any(ch in net for ch in "*?/"):
        return [net]
    return [net, f"/{net}"]


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def calc_track_width(current_a: float, temp_rise_c: float = 10.0, copper_oz: float = 1.0,
                         internal_layer: bool = False,
                         length_mm: float | None = None) -> dict[str, Any]:
        """IPC-2221 minimum track width for a current, plus resistance and voltage drop
        when a length is given.

        Args:
            current_a: Continuous (or worst-case, e.g. motor stall) current in amps.
            temp_rise_c: Allowed temperature rise above ambient (10 C is conservative).
            copper_oz: Copper weight (1 oz = 35 um is the usual default).
            internal_layer: True for inner layers (they cool worse, need ~2.5x width).
            length_mm: Optional track length to report resistance, drop, and loss.
        """
        width = ipc2221_width_mm(current_a, temp_rise_c, copper_oz, internal_layer)
        out: dict[str, Any] = {
            "min_width_mm": round(width, 3),
            "suggested_width_mm": _round_up(max(width, 0.2)),
            "inputs": {"current_a": current_a, "temp_rise_c": temp_rise_c,
                       "copper_oz": copper_oz, "internal_layer": internal_layer},
        }
        if width > 3.0:
            out["note"] = ("Wider than 3 mm: use a copper zone/pour for this path instead "
                           "of a track, and keep it short.")
        if length_mm:
            w = out["suggested_width_mm"]
            r = track_resistance_ohm(w, length_mm, copper_oz, 20 + temp_rise_c)
            out.update({"length_mm": length_mm, "resistance_mohm": round(r * 1000, 2),
                        "voltage_drop_mv": round(r * current_a * 1000, 1),
                        "power_loss_mw": round(r * current_a ** 2 * 1000, 1)})
        return out

    @mcp.tool()
    def get_netclass_config(project_path: str = "") -> dict[str, Any]:
        """Read net classes and net-to-class patterns from the project file (works with
        KiCad closed). list_netclasses reads the live values from a running KiCad.

        Args:
            project_path: .kicad_pro file or its folder; default = project of the open board.
        """
        pro = _project_file(project_path)
        ns = (_load(pro).get("net_settings") or {})
        return {
            "project_file": str(pro),
            "classes": [{k: c.get(k) for k in ("name", "track_width", "clearance",
                                               "via_diameter", "via_drill", "priority")}
                        for c in ns.get("classes") or []],
            "patterns": ns.get("netclass_patterns") or [],
        }

    @mcp.tool()
    def configure_netclasses(classes: list[dict[str, Any]], project_path: str = "",
                             temp_rise_c: float = 10.0, copper_oz: float = 1.0,
                             replace_patterns: bool = False) -> dict[str, Any]:
        """Create/update net classes and assign nets to them in the .kicad_pro file.
        Widths can be derived from current (IPC-2221) instead of fixed numbers.

        The project must be CLOSED in KiCad (both editors): KiCad keeps project settings
        in memory and would overwrite this change on its next save. Reopen it afterwards
        and the nets pick up their classes.

        Args:
            classes: One object per class, e.g.
                [{"name": "Power", "current_a": 1.5, "nets": ["+5V", "VMOT"]},
                 {"name": "Motor", "current_a": 2.5, "nets": ["AO1", "AO2"]},
                 {"name": "Signal", "track_width_mm": 0.25, "nets": ["/*"]}]
                Keys: name (required); nets (names or KiCad wildcard patterns - plain
                names also match their '/NAME' root-sheet form); current_a (sizes the
                track via IPC-2221) or track_width_mm; clearance_mm; via_diameter_mm;
                via_drill_mm; priority (lower wins when patterns overlap).
            project_path: .kicad_pro file or its folder; default = project of the open board.
            temp_rise_c: Temperature rise used for current-based widths.
            copper_oz: Copper weight used for current-based widths.
            replace_patterns: Drop all existing net-class patterns first.
        """
        pro = _project_file(project_path)
        editor = _open_in_kicad(pro)
        if editor:
            raise RuntimeError(
                f"This project is open in KiCad's {editor}. Close the project in KiCad "
                "(all editors) first - KiCad would overwrite the project file on its next "
                "save - then call configure_netclasses again and reopen the project.")

        cfg = _load(pro)
        ns = _net_settings(cfg)
        if replace_patterns:
            ns["netclass_patterns"] = []
        by_name = {c["name"]: c for c in ns["classes"]}
        report = []
        for spec in classes:
            name = spec.get("name")
            if not name:
                raise ValueError(f"Every class needs a 'name': {spec}")
            entry = by_name.get(name)
            if entry is None:
                entry = dict(_CLASS_TEMPLATE, name=name)
                ns["classes"].append(entry)
                by_name[name] = entry
            info: dict[str, Any] = {"name": name}
            if spec.get("current_a") is not None:
                w = ipc2221_width_mm(float(spec["current_a"]), temp_rise_c, copper_oz)
                entry["track_width"] = _round_up(max(w, 0.2))
                info["current_a"] = spec["current_a"]
                if w > 3.0:
                    info["note"] = "Over 3 mm wide: route this net as a zone, not a track."
            elif spec.get("track_width_mm") is not None:
                entry["track_width"] = float(spec["track_width_mm"])
            for key, field in (("clearance_mm", "clearance"), ("via_diameter_mm", "via_diameter"),
                               ("via_drill_mm", "via_drill")):
                if spec.get(key) is not None:
                    entry[field] = float(spec[key])
            if spec.get("priority") is not None:
                entry["priority"] = int(spec["priority"])
            if entry["via_drill"] >= entry["via_diameter"]:
                raise ValueError(f"{name}: via drill must be smaller than via diameter.")
            existing = {(p["netclass"], p["pattern"]) for p in ns["netclass_patterns"]}
            added = []
            for net in spec.get("nets") or []:
                for pat in _patterns_for(str(net)):
                    if (name, pat) not in existing:
                        ns["netclass_patterns"].append({"netclass": name, "pattern": pat})
                        existing.add((name, pat))
                        added.append(pat)
            info.update({k: entry[k] for k in ("track_width", "clearance", "via_diameter",
                                                "via_drill", "priority")})
            info["patterns_added"] = added
            report.append(info)

        backup = pro.with_suffix(".kicad_pro.bak")
        backup.write_text(pro.read_text(encoding="utf-8"), encoding="utf-8")
        pro.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        return {"project_file": str(pro), "backup": str(backup), "classes": report,
                "next_step": "Open the project in KiCad; in the PCB editor run "
                             "'Update PCB from Schematic' if nets were renamed, then "
                             "list_netclasses to confirm."}
