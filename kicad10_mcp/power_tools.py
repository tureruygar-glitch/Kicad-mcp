"""Power budget: how much current each supply net carries, from the schematic.

The schematic is read through ``kicad-cli sch export netlist`` (no running KiCad
needed). Each part is classified by behaviour - consumer, regulator, driver,
series element, passive, or connector - using ``parts_db``. Currents are then
summed from the loads back to the sources: a regulator's input follows its
output load, a driver's supply follows its channel loads, a fuse/diode/switch
passes on whatever is downstream of it.

Nothing is guessed silently. Parts missing from the database and connectors
whose far-side load is unknown are returned as questions; the model should look
the parts up in their datasheets (then ``set_part_current``) and ask the user
about the connectors, then run the analysis again.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from kicad10_mcp import parts_db
from kicad10_mcp.netclass_tools import _round_up, ipc2221_width_mm

_GND_RE = re.compile(r"^/?(A|D|P|S)?GND\w*$|^/?VSS\w*$|^/?0V$|^/?EARTH$|^/?GND_", re.I)
_POWER_NAME_RE = re.compile(
    r"(^|/|_|\+|-)(V(BAT|BATT|MOT|M|CC|DD|IN|BUS|SYS|S|PWR|SUPPLY|LED|RAW)\w*|\+?\d+V\d*|\d+V\d*|PWR\w*)$",
    re.I)
_SOURCE_WORDS = re.compile(r"bat|batt|pwr|power|supply|vin|dc.?in|input|usb|psu|adapter|jack", re.I)


def net_voltage(name: str) -> Optional[float]:
    """'+5V' -> 5, '3V3' -> 3.3, '/+12V' -> 12, '+3.3V' -> 3.3, '1V8' -> 1.8; otherwise None."""
    base = name.split("/")[-1].lstrip("+")
    m = re.fullmatch(r"(\d+)V(\d*)|(\d+\.\d+)V", base, re.I)
    if not m:
        return None
    if m.group(3):
        return float(m.group(3))
    return float(f"{m.group(1)}.{m.group(2)}") if m.group(2) else float(m.group(1))


# ---------------------------------------------------------------------------
# Netlist model
# ---------------------------------------------------------------------------

@dataclass
class Part:
    ref: str
    lib_id: str
    part: str
    value: str
    datasheet: str
    description: str = ""
    pins: dict[str, str] = field(default_factory=dict)       # pin number -> net
    names: dict[str, str] = field(default_factory=dict)      # pin number -> pin name
    types: dict[str, str] = field(default_factory=dict)      # pin number -> pin type

    def nets_for(self, pin_names: list[str]) -> list[str]:
        wanted = {n.upper() for n in pin_names}
        return sorted({net for num, net in self.pins.items()
                       if self.names.get(num, "").upper() in wanted or num in pin_names})


def _read_netlist(path: Path) -> dict[str, Part]:
    from kicad10_mcp.sexpr import child, children, parse

    tree = parse(path.read_text(encoding="utf-8"))
    parts: dict[str, Part] = {}
    for comp in children(child(tree, "components"), "comp"):
        ref = str(child(comp, "ref")[1])
        lib = child(comp, "libsource")
        lib_name = str(child(lib, "lib")[1]) if lib is not None else ""
        part = str(child(lib, "part")[1]) if lib is not None else ""
        ds = child(comp, "datasheet")
        desc = child(comp, "description")
        parts[ref] = Part(ref, f"{lib_name}:{part}", part, str(child(comp, "value")[1]),
                          str(ds[1]) if ds is not None and len(ds) > 1 else "",
                          str(desc[1]) if desc is not None and len(desc) > 1 else "")
    for net in children(child(tree, "nets"), "net"):
        name = str(child(net, "name")[1])
        for node in children(net, "node"):
            ref = str(child(node, "ref")[1])
            num = str(child(node, "pin")[1])
            fn = child(node, "pinfunction")
            pin_name = str(fn[1]) if fn is not None else ""
            if pin_name.endswith(f"_{num}"):  # KiCad 10 appends the pin number
                pin_name = pin_name[: -len(num) - 1]
            ptype = child(node, "pintype")
            if ref in parts:
                parts[ref].pins[num] = name
                parts[ref].names[num] = pin_name
                parts[ref].types[num] = str(ptype[1]) if ptype is not None else ""
    return parts


def _export_netlist(schematic: Path) -> Path:
    from kicad10_mcp.export_tools import _kicad_cli

    out = Path(tempfile.gettempdir()) / f"kicad10_mcp_power_{os.getpid()}.net"
    proc = subprocess.run([_kicad_cli(), "sch", "export", "netlist", "--format", "kicadsexpr",
                           "-o", str(out), str(schematic)], capture_output=True, text=True,
                          timeout=300)
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"kicad-cli netlist export failed: {proc.stderr.strip() or proc.stdout}")
    return out


def _find_schematic(schematic_path: str) -> Path:
    if schematic_path:
        p = Path(schematic_path)
        if p.is_dir():
            pros = sorted(p.glob("*.kicad_pro"))
            p = pros[0].with_suffix(".kicad_sch") if pros else next(iter(sorted(p.glob("*.kicad_sch"))), p)
        elif p.suffix == ".kicad_pro":
            p = p.with_suffix(".kicad_sch")
        if not p.exists():
            raise FileNotFoundError(f"Schematic not found: {p}")
        return p
    try:
        from kicad10_mcp.connection import require_board

        proj = require_board().get_project()
        p = Path(proj.path) / f"{proj.name}.kicad_sch"
        if p.exists():
            return p
    except Exception:  # noqa: BLE001
        pass
    raise ValueError("Pass schematic_path (the .kicad_sch, .kicad_pro, or project folder).")


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def _load_spec(spec: Any) -> dict[str, Any]:
    if isinstance(spec, (int, float)):
        return {"typ_a": float(spec), "max_a": float(spec)}
    s = dict(spec)
    if "current_a" in s:
        s.setdefault("max_a", float(s["current_a"]))
        s.setdefault("typ_a", float(s["current_a"]))
    s.setdefault("typ_a", s.get("max_a", 0.0))
    if "max_a" not in s:
        raise ValueError(f"External load needs current_a or max_a: {spec}")
    return s


class Budget:
    def __init__(self, parts: dict[str, Part], external_loads: dict[str, Any],
                 sources: list[str], net_voltages: dict[str, float]):
        self.parts = parts
        self.loads = {ref: _load_spec(v) for ref, v in (external_loads or {}).items()}
        self.volts = {k: float(v) for k, v in (net_voltages or {}).items()}
        self.nets: dict[str, list[tuple[str, str]]] = {}
        for p in parts.values():
            for num, net in p.pins.items():
                self.nets.setdefault(net, []).append((p.ref, num))
        self.kind: dict[str, str] = {}
        self.entry: dict[str, dict[str, Any]] = {}
        self.warnings: list[str] = []
        self.unknown: list[dict[str, Any]] = []
        self.unverified: set[str] = set()
        self.small: list[str] = []
        self._classify(sources)
        self.gnd = {n for n in self.nets if _GND_RE.search(n)}
        self.downstream = self._orient()

    # -- classification ----------------------------------------------------
    def _classify(self, sources: list[str]) -> None:
        explicit_sources = set(sources or [])
        for ref, p in self.parts.items():
            lid = p.lib_id
            if ref in self.loads:
                self.kind[ref] = "load"
            elif ref in explicit_sources:
                self.kind[ref] = "source"
            elif parts_db.matches(lid, parts_db.SOURCE_PATTERNS):
                self.kind[ref] = "source"
            elif re.search(r"zener|tvs|esd", lid, re.I):
                self.kind[ref] = "passive"
            elif parts_db.matches(lid, parts_db.SERIES_PATTERNS) or (
                    lid.startswith("Device:R") and re.fullmatch(r"0(\.0+)?\s*(R|Ω|ohm)?", p.value.strip(), re.I)):
                self.kind[ref] = "series"
            elif parts_db.matches(lid, parts_db.PASSIVE_PATTERNS):
                self.kind[ref] = "passive"
            elif parts_db.matches(lid, parts_db.SMALL_LOAD_PATTERNS):
                self.kind[ref] = "small"
                self.small.append(ref)
            elif parts_db.matches(lid, parts_db.CONNECTOR_PATTERNS) or ref.startswith(("J", "P", "BT", "CN")):
                self.kind[ref] = ("source" if _SOURCE_WORDS.search(f"{p.value} {lid}") and not explicit_sources
                                  else "connector")
            else:
                hit = parts_db.lookup(p.part, p.value)
                if hit:
                    key, entry = hit
                    self.kind[ref] = "known"
                    self.entry[ref] = entry
                    if not entry.get("verified"):
                        self.unverified.add(key)
                else:
                    self.kind[ref] = "unknown"

    def _is_power_pin(self, p: Part, num: str) -> bool:
        return p.types.get(num) in ("power_in", "power_out")

    # -- orientation through series elements -------------------------------
    def _orient(self) -> dict[str, list[tuple[str, str]]]:
        """For each net, (series part, downstream net) pairs, walking out from sources."""
        src_nets = {net for ref, k in self.kind.items() if k == "source"
                    for net in self.parts[ref].pins.values() if net not in self.gnd}
        self.source_nets = src_nets
        down: dict[str, list[tuple[str, str]]] = {}
        seen = set(src_nets)
        frontier = list(src_nets)
        series = [r for r, k in self.kind.items() if k == "series"]
        while frontier:
            net = frontier.pop()
            for ref in series:
                p = self.parts[ref]
                pass_pins = [n for n in p.pins if p.names.get(n, "").upper() not in ("G", "GATE")]
                nets = {p.pins[n] for n in pass_pins}
                if net not in nets:
                    continue
                for other in nets - {net}:
                    if other in self.gnd or other in seen:
                        continue
                    down.setdefault(net, []).append((ref, other))
                    seen.add(other)
                    frontier.append(other)
        return down

    # -- current -------------------------------------------------------------
    def net_current(self, net: str, mode: str, _stack: Optional[set] = None) -> tuple[float, list]:
        """Current flowing into the loads on ``net`` (mode 'typ' or 'max')."""
        stack = _stack or set()
        if net in stack or net in self.gnd:
            return 0.0, []
        stack = stack | {net}
        total, parts = 0.0, []

        def add(amount: float, why: str) -> None:
            nonlocal total
            if amount:
                total += amount
                parts.append({"from": why, "a": round(amount, 4)})

        charged: set[tuple[str, int]] = set()
        for ref, num in self.nets.get(net, []):
            p, kind = self.parts[ref], self.kind[ref]
            pin_name = p.names.get(num, "")
            if kind == "load" and net not in self.gnd:
                spec = self.loads[ref]
                pins = spec.get("pins")
                if pins is None or num in pins or pin_name in pins:
                    if (ref, -1) not in charged:
                        charged.add((ref, -1))
                        add(spec["max_a" if mode == "max" else "typ_a"], f"{ref} external load")
            elif kind == "known":
                entry = self.entry[ref]
                for gi, grp in enumerate(entry.get("supply") or []):
                    target = next((n for pn in grp["pins"] for n in p.nets_for([pn])), None)
                    if target == net and (ref, gi) not in charged:
                        charged.add((ref, gi))
                        add(grp["max_a" if mode == "max" else "typ_a"], f"{ref} {p.part} self")
                for di, drv in enumerate(entry.get("drivers") or []):
                    if net in p.nets_for(drv["supply_pins"]) and (ref, 100 + di) not in charged:
                        charged.add((ref, 100 + di))
                        add(self._driver_load(ref, drv, mode, stack), f"{ref} {p.part} outputs")
                for ri, reg in enumerate(entry.get("regulators") or []):
                    if net in p.nets_for(reg["in_pins"]) and (ref, 200 + ri) not in charged:
                        charged.add((ref, 200 + ri))
                        add(self._regulator_input(ref, reg, net, mode, stack),
                            f"{ref} {p.part} regulator input")
        for ref, other in self.downstream.get(net, []):
            amount, _ = self.net_current(other, mode, stack)
            add(amount, f"{ref} passes {other}")
        return total, parts

    def _driver_load(self, ref: str, drv: dict[str, Any], mode: str, stack: set) -> float:
        p = self.parts[ref]
        total = 0.0
        for ch in drv["channels"]:
            loads = [self.net_current(n, mode, stack)[0] for n in p.nets_for(ch)]
            ch_load = max(loads) if loads else 0.0  # current enters one output, leaves the other
            if mode == "max" and ch_load:
                cont, peak = drv.get("max_continuous_a"), drv.get("max_peak_a")
                label = f"{ref} {p.part} channel {'/'.join(ch)}: {ch_load:.2f} A"
                if peak and ch_load > peak:
                    self.warnings.append(f"{label} exceeds the {peak} A peak rating.")
                elif cont and ch_load > cont:
                    self.warnings.append(f"{label} exceeds the {cont} A continuous rating "
                                         f"(peak {peak} A): expect thermal shutdown under sustained load.")
            total += ch_load
        return total

    def _regulator_input(self, ref: str, reg: dict[str, Any], in_net: str, mode: str,
                         stack: set) -> float:
        p = self.parts[ref]
        out_nets = p.nets_for(reg["out_pins"])
        i_out = sum(self.net_current(n, mode, stack)[0] for n in out_nets)
        if mode == "max" and reg.get("max_out_a") and i_out > reg["max_out_a"]:
            self.warnings.append(f"{ref} {p.part}: output load {i_out:.3f} A exceeds its "
                                 f"{reg['max_out_a']} A rating{' (' + reg['note'] + ')' if reg.get('note') else ''}.")
        if reg["type"] == "linear":
            vin = self.volts.get(in_net, net_voltage(in_net))
            vout = reg.get("vout") or (self.volts.get(out_nets[0], net_voltage(out_nets[0])) if out_nets else None)
            if mode == "max" and vin and vout and i_out:
                loss = (vin - vout) * i_out
                if loss > 0.5:
                    self.warnings.append(f"{ref} {p.part}: linear drop dissipates {loss:.2f} W "
                                         f"({vin} V -> {vout} V at {i_out:.2f} A); check heatsinking.")
            return i_out
        eff = reg.get("efficiency", 0.85)
        vout = reg.get("vout") or (self.volts.get(out_nets[0], net_voltage(out_nets[0])) if out_nets else None)
        vin = self.volts.get(in_net, net_voltage(in_net))
        if vin and vout:
            return i_out * vout / (vin * eff)
        if mode == "max":
            self.warnings.append(f"{ref} {p.part}: input voltage of '{in_net}' unknown; assumed "
                                 f"I_in = I_out/eff. Pass net_voltages for an exact figure.")
        return i_out / eff

    # -- report ----------------------------------------------------------------
    def power_nets(self) -> list[str]:
        nets = set(self.source_nets)
        for net, members in self.nets.items():
            if net.startswith("unconnected-"):
                continue
            if any(self._is_power_pin(self.parts[r], n) for r, n in members) or _POWER_NAME_RE.search(net):
                nets.add(net)
            if any(self.kind[r] == "load" for r, _ in members):
                nets.add(net)
        for ref, kind in self.kind.items():
            if kind == "known":
                for drv in self.entry[ref].get("drivers") or []:
                    for ch in drv["channels"]:
                        nets.update(self.parts[ref].nets_for(ch))
        for pairs in self.downstream.values():
            nets.update(o for _, o in pairs)
        return sorted(nets - self.gnd)


def _rating_hint(description: str) -> Optional[str]:
    """Currents quoted in a KiCad symbol description ('1A Low Dropout regulator').

    Only a pointer for the datasheet lookup - the description rarely says whether a
    figure is input, output, peak, or per channel, so it is never used as data.
    """
    found = re.findall(r"(?<![\w.])±?\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?\s?m?A\b", description)
    return f"description mentions {', '.join(found)} (unverified)" if found else None


def _suggest_classes(rows: list[dict[str, Any]], temp_rise_c: float, copper_oz: float):
    buckets = {"HighCurrent": (3.0, None), "Power": (0.5, 3.0), "PowerLow": (0.05, 0.5)}
    out = []
    for name, (lo, hi) in buckets.items():
        members = [r for r in rows if r["max_a"] >= lo and (hi is None or r["max_a"] < hi)]
        if not members:
            continue
        worst = max(r["max_a"] for r in members)
        width = _round_up(max(ipc2221_width_mm(worst, temp_rise_c, copper_oz), 0.3))
        entry = {"name": name, "track_width_mm": width, "nets": [r["net"] for r in members],
                 "sized_for_a": round(worst, 3)}
        if width > 3.0:
            entry["note"] = "Wider than 3 mm: route these nets as copper zones."
        out.append(entry)
    return out


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def analyze_power_budget(schematic_path: str = "", external_loads: dict[str, Any] | None = None,
                             sources: list[str] | None = None,
                             net_voltages: dict[str, float] | None = None,
                             temp_rise_c: float = 10.0, copper_oz: float = 1.0) -> dict[str, Any]:
        """Estimate how much current every supply net carries, from the schematic, and
        suggest track widths / net classes. Works for any kind of board.

        Reads the saved schematic via kicad-cli (save it first). Parts are looked up in
        the current database; regulators, drivers, and series parts (fuse, diode,
        switch, inductor, 0-ohm) propagate current from loads back to the source.

        Returns 'questions' when information is missing - answer them and run again:
          - unknown_parts: open the part's 'datasheet' link (taken from the KiCad
            symbol; about half of vendor sites allow automated download - if it
            fails, search the web for "<part> datasheet"), read the supply/output
            current figures, then call set_part_current with the URL as source.
            'rating_hint' is only a pointer from the symbol description.
          - unknown_connectors: ask the user what is plugged in (and its worst-case
            current, e.g. motor stall), then pass it in external_loads.
        Values from the built-in database are flagged in 'unverified_parts' until
        checked against a datasheet.

        Args:
            schematic_path: .kicad_sch / .kicad_pro / project folder; default = project of
                the open board.
            external_loads: What hangs off connectors (or any part), by reference:
                {"J2": 2.5} or {"J2": {"typ_a": 0.4, "max_a": 2.5, "note": "motor stall"}}.
                Current is charged to every non-ground net the part touches; restrict
                with "pins": ["1"] (e.g. only the supply pin of a 3-pin servo header).
            sources: References of the supply inputs (battery/power connectors). Default:
                batteries, USB/barrel jacks, and connectors named like BAT/PWR/SUPPLY.
            net_voltages: Voltages for nets whose name doesn't say it, e.g. {"/VMOT": 7.4};
                used for switching regulators and linear-regulator dissipation.
            temp_rise_c: Temperature rise for the width suggestions.
            copper_oz: Copper weight for the width suggestions.
        """
        sch = _find_schematic(schematic_path)
        netlist = _export_netlist(sch)
        try:
            parts = _read_netlist(netlist)
        finally:
            netlist.unlink(missing_ok=True)
        b = Budget(parts, external_loads or {}, sources or [], net_voltages or {})

        rows = []
        for net in b.power_nets():
            typ, _ = b.net_current(net, "typ")
            worst, contrib = b.net_current(net, "max")
            if worst <= 0 and net not in b.source_nets:
                continue
            w = ipc2221_width_mm(worst, temp_rise_c, copper_oz) if worst > 0 else 0.0
            rows.append({"net": net, "typ_a": round(typ, 4), "max_a": round(worst, 4),
                         "min_width_mm": round(w, 3), "contributors": contrib})
        source_total = sum(r["max_a"] for r in rows if r["net"] in b.source_nets)
        for g in sorted(b.gnd):
            rows.append({"net": g, "typ_a": None, "max_a": round(source_total, 4),
                         "min_width_mm": round(ipc2221_width_mm(source_total, temp_rise_c, copper_oz), 3)
                         if source_total else 0.0,
                         "contributors": [{"from": "return current of all sources", "a": round(source_total, 4)}],
                         "note": "ground: prefer a solid pour/plane"})
        rows.sort(key=lambda r: -(r["max_a"] or 0))
        # Warnings collected during the 'max' passes may repeat; keep order, drop dupes.
        warnings = list(dict.fromkeys(b.warnings))

        unknown_parts = [{"ref": r, "part": parts[r].lib_id, "value": parts[r].value,
                          "datasheet": parts[r].datasheet if parts[r].datasheet not in ("", "~") else None,
                          "description": parts[r].description,
                          "rating_hint": _rating_hint(parts[r].description),
                          "power_pins": sorted({parts[r].names.get(n) or n for n in parts[r].pins
                                                if b._is_power_pin(parts[r], n)})}
                         for r, k in sorted(b.kind.items()) if k == "unknown"]
        unknown_connectors = [{"ref": r, "value": parts[r].value,
                               "nets": sorted(set(parts[r].pins.values()) - b.gnd)}
                              for r, k in sorted(b.kind.items())
                              if k == "connector" and set(parts[r].pins.values()) - b.gnd]
        complete = not unknown_parts and not unknown_connectors
        if not b.source_nets:
            warnings.append("No supply input found; pass sources=[...] with the battery/power "
                            "connector reference.")
        return {
            "schematic": str(sch),
            "complete": complete,
            "nets": rows,
            "sources": sorted(r for r, k in b.kind.items() if k == "source"),
            "warnings": warnings,
            "questions": {"unknown_parts": unknown_parts,
                          "unknown_connectors": unknown_connectors},
            "unverified_parts": sorted(b.unverified),
            "not_summed": [{"ref": r, "part": parts[r].lib_id,
                            "why": "current set by external resistor/driver; add via external_loads if significant"}
                           for r in b.small],
            "netclass_suggestion": _suggest_classes([r for r in rows if r["max_a"]], temp_rise_c, copper_oz),
            "next_step": ("Answer the questions and re-run." if not complete else
                          "Pass netclass_suggestion to configure_netclasses (project closed in KiCad)."),
        }

    @mcp.tool()
    def set_part_current(part: str, entry: dict[str, Any], source: str,
                         verified: bool = True, share: bool = False) -> dict[str, Any]:
        """Save a part's current data (from its datasheet) to the user part database so
        analyze_power_budget can use it. Always cite where the numbers came from.

        Args:
            part: Symbol name as in KiCad (e.g. 'TB6612FNG'); also used as the match
                pattern unless entry has "match": [...] (wildcards allowed).
            entry: Behaviour description; any of:
                "supply": [{"pins": ["VCC"], "typ_a": 0.0015, "max_a": 0.0022}]
                "regulators": [{"in_pins": ["VIN"], "out_pins": ["VOUT"],
                                "type": "linear"|"buck", "vout": 3.3,
                                "efficiency": 0.9, "max_out_a": 1.0}]
                "drivers": [{"supply_pins": ["VM"], "channels": [["OUT1", "OUT2"]],
                             "max_continuous_a": 1.2, "max_peak_a": 3.2}]
                Pin names must match the KiCad symbol's pin names.
            source: Datasheet URL (and page/table) the numbers came from.
            verified: False if the numbers are estimates rather than datasheet values.
            share: Also propose the entry to the shared Supabase database (reviewed by
                the maintainer before anyone else sees it). Only when the user agrees.
        """
        from kicad10_mcp import shared_db

        data = dict(entry)
        data.setdefault("match", [part])
        data["source"] = source
        data["verified"] = bool(verified)
        problems = parts_db.validate_entry(data)
        if problems:
            raise ValueError("Invalid entry: " + " ".join(problems))
        path = parts_db.save_user_entry(part, data)
        result: dict[str, Any] = {"saved": part, "database": str(path), "entry": data}
        if share:
            if not shared_db.configured():
                result["shared"] = "not submitted: shared database not configured"
            else:
                try:
                    shared_db.submit(part, data)
                    result["shared"] = "submitted for review"
                except (RuntimeError, OSError) as exc:
                    result["shared"] = f"not submitted: {exc}"
        return result

    @mcp.tool()
    def sync_part_database(force: bool = True) -> dict[str, Any]:
        """Download the reviewed shared part database (Supabase) into the local cache.
        The power budget also refreshes it automatically once a day and works offline
        from the cache.

        Args:
            force: Refresh even if the cache is less than a day old.
        """
        from kicad10_mcp import shared_db

        return shared_db.sync(force=force)

    @mcp.tool()
    def list_part_database(filter: str = "") -> dict[str, Any]:
        """List parts the power budget knows about (built-in and user-added).

        Args:
            filter: Optional case-insensitive substring to filter part names.
        """
        user = parts_db.load_user_db()
        rows = []
        for origin, db in (("user", user), ("shared", parts_db.load_shared_db()),
                           ("built-in", parts_db.BUILTIN)):
            for key, e in db.items():
                if filter and filter.lower() not in key.lower():
                    continue
                rows.append({"part": key, "origin": origin, "match": e.get("match"),
                             "verified": e.get("verified", False), "source": e.get("source"),
                             "kinds": [k for k in ("supply", "regulators", "drivers") if e.get(k)]})
        return {"user_database": str(parts_db.USER_DB), "parts": rows}
