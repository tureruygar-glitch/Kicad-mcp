"""Autorouting with Freerouting (https://github.com/freerouting/freerouting).

Freerouting is GPL-3.0, so it is only ever run as a separate program; none of
its code is part of this package. The pipeline:

    board --(KiCad's Python: ExportSpecctraDSN)--> .dsn
          --(java -jar freerouting.jar, headless)--> .ses
          --(KiCad's Python: ImportSpecctraSES)--> board file
          --> KiCad reloads the file; zones are refilled and DRC is run.

Existing tracks are locked in the exported design, so copper routed by hand
(or by route_pads) stays put and only the missing connections are added. Net
classes can be excluded entirely, e.g. high-current nets that should be poured
or routed by hand.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

TOOLS_DIR = Path.home() / ".kicad10_mcp" / "freerouting"
_SPECCTRA = Path(__file__).with_name("kicad_py") / "specctra.py"
# Newest release that runs on each Java feature version (2.2.0+ needs Java 25).
_RELEASES = [(25, "2.4.1"), (21, "2.1.0")]
_RELEASE_URL = "https://github.com/freerouting/freerouting/releases/download/v{v}/freerouting-{v}.jar"


# ---------------------------------------------------------------------------
# Environment discovery
# ---------------------------------------------------------------------------

def _java_candidates() -> list[str]:
    """Every Java we can find: explicit setting, JAVA_HOME, PATH, the Windows
    registry, and the usual install folders (JAVA_HOME often points at an older one)."""
    exe = "java.exe" if os.name == "nt" else "java"
    out: list[str] = []
    if os.environ.get("KICAD10_MCP_JAVA"):
        out.append(os.environ["KICAD10_MCP_JAVA"])
    if os.environ.get("JAVA_HOME"):
        out.append(str(Path(os.environ["JAVA_HOME"]) / "bin" / exe))
    if shutil.which("java"):
        out.append(shutil.which("java"))
    if os.name == "nt":
        try:
            import winreg

            for key in (r"SOFTWARE\JavaSoft\JDK", r"SOFTWARE\JavaSoft\JRE"):
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as root:
                        for i in range(winreg.QueryInfoKey(root)[0]):
                            with winreg.OpenKey(root, winreg.EnumKey(root, i)) as ver:
                                home = winreg.QueryValueEx(ver, "JavaHome")[0]
                                out.append(str(Path(home) / "bin" / exe))
                except OSError:
                    continue
        except ImportError:
            pass
        for base in (Path(os.environ.get("ProgramFiles", r"C:\Program Files")),):
            for vendor in ("Java", "Eclipse Adoptium", "Microsoft", "Zulu", "Amazon Corretto",
                           "BellSoft", "Semeru"):
                out += [str(p) for p in (base / vendor).glob(f"*/bin/{exe}")]
    else:
        out += [str(p) for p in Path("/usr/lib/jvm").glob(f"*/bin/{exe}")]
        out += [str(p) for p in Path("/Library/Java/JavaVirtualMachines").glob(f"*/Contents/Home/bin/{exe}")]
    seen, unique = set(), []
    for c in out:
        key = os.path.normcase(os.path.abspath(c))
        if key not in seen and Path(c).exists():
            seen.add(key)
            unique.append(c)
    return unique


def _java_version(java: str) -> Optional[int]:
    try:
        res = subprocess.run([java, "-version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r'version "(\d+)(?:\.(\d+))?', res.stderr + res.stdout)
    if not m:
        return None
    major = int(m.group(1))
    return int(m.group(2)) if major == 1 and m.group(2) else major  # "1.8" style


def java_info() -> Optional[dict[str, Any]]:
    """The newest Java found (KICAD10_MCP_JAVA wins if set)."""
    if os.environ.get("KICAD10_MCP_JAVA"):
        v = _java_version(os.environ["KICAD10_MCP_JAVA"])
        if v:
            return {"java": os.environ["KICAD10_MCP_JAVA"], "version": v}
    best = None
    for java in _java_candidates():
        v = _java_version(java)
        if v and (best is None or v > best["version"]):
            best = {"java": java, "version": v}
    return best


def kicad_python() -> Path:
    """KiCad's bundled interpreter (it ships the pcbnew module)."""
    env = os.environ.get("KICAD10_MCP_KICAD_PYTHON")
    candidates = [Path(env)] if env else []
    try:
        from kicad10_mcp.export_tools import _kicad_cli

        candidates.append(Path(_kicad_cli()).with_name("python.exe"))
        candidates.append(Path(_kicad_cli()).with_name("python3"))
    except Exception:  # noqa: BLE001
        pass
    candidates += [Path(r"C:\Program Files\KiCad\10.0\bin\python.exe"),
                   Path("/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3"),
                   Path("/usr/bin/python3")]
    for c in candidates:
        if c.exists():
            return c
    raise RuntimeError("KiCad's Python (with pcbnew) was not found; set KICAD10_MCP_KICAD_PYTHON.")


def _jar_version(path: Path) -> tuple[int, ...]:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", path.name)
    return tuple(int(x) for x in m.groups()) if m else (0,)


def find_jar(java_major: Optional[int]) -> Optional[Path]:
    """Configured jar, else the newest installed one this Java can run."""
    env = os.environ.get("KICAD10_MCP_FREEROUTING_JAR")
    if env and Path(env).exists():
        return Path(env)
    jars = sorted(TOOLS_DIR.glob("freerouting-*.jar"), key=_jar_version, reverse=True)
    for jar in jars:
        needed = 25 if _jar_version(jar) >= (2, 2, 0) else 21
        if java_major is None or java_major >= needed:
            return jar
    return None


def _run_kicad_py(*args: str, timeout: int = 300) -> dict[str, Any]:
    proc = subprocess.run([str(kicad_python()), str(_SPECCTRA), *args],
                          capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")
    m = re.search(r"@@KICAD10_MCP_RESULT@@(.*?)@@END@@", proc.stdout, re.S)
    if m is None:
        noise = "swig/python detected a memory leak"
        detail = "\n".join(l for l in (proc.stderr + proc.stdout).splitlines() if noise not in l)
        raise RuntimeError(f"KiCad Python step failed (exit {proc.returncode}): {detail.strip()[-800:]}")
    res = json.loads(m.group(1))
    if not res.get("ok"):
        raise RuntimeError(f"KiCad Python step failed: {res.get('error') or res}")
    return res


def _write_settings(user_dir: Path, max_passes: int, timeout_s: int,
                    version: str = "") -> None:
    """Headless, no telemetry. (Freerouting 2.1.0 ignores the pass/time limits in
    CLI mode; 2.2+ honours them.)"""
    user_dir.mkdir(parents=True, exist_ok=True)
    h, rem = divmod(max(timeout_s, 30), 3600)
    m, s = divmod(rem, 60)
    import uuid

    settings = {
        # Without it 2.4 treats the file as a "very old config" and warns.
        **({"version": version} if version else {}),
        # 2.4+ requires a profile id; a fresh random one per run is not trackable.
        "profile": {"id": str(uuid.uuid4()), "email": "", "allow_telemetry": False,
                    "allow_contact": False},
        "gui": {"enabled": False},
        "router": {
            "max_passes": int(max_passes),                      # 2.1 location
            "autorouter": {"max_passes": int(max_passes)},       # 2.4+ location
            "job_timeout": f"{h:02d}:{m:02d}:{s:02d}",
        },
        "usage_and_diagnostic_data": {"disable_analytics": True},
    }
    (user_dir / "freerouting.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")


def run_freerouting(dsn: Path, ses: Path, *, max_passes: int = 20, timeout_s: int = 600,
                    skip_netclasses: Optional[list[str]] = None) -> dict[str, Any]:
    info = java_info()
    if info is None or info["version"] < 21:
        raise RuntimeError("Freerouting needs Java 21 or newer "
                           f"(found: {info['version'] if info else 'none'}).")
    jar = find_jar(info["version"])
    if jar is None:
        raise RuntimeError("Freerouting is not installed; call install_freerouting first.")
    work = dsn.parent
    _write_settings(work / "freerouting_user", max_passes, timeout_s,
                    ".".join(map(str, _jar_version(jar))))
    cmd = [info["java"], "-jar", str(jar), "-de", str(dsn), "-do", str(ses),
           "-mp", str(max_passes), "--gui.enabled=false", "-da",
           f"--user_data_path={work / 'freerouting_user'}"]
    if skip_netclasses:
        cmd += ["-inc", ",".join(skip_netclasses)]
    started = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s,
                              encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Freerouting did not finish within {timeout_s} s (Freerouting "
                           f"2.1.0 ignores pass limits; Java 25 + Freerouting 2.4 fixes that).") from exc
    log = proc.stdout + proc.stderr
    # 2.1: "pass #12 ... (2 unrouted)"   2.4: "pass #2 ... (0 unrouted and 1 violation)"
    passes = re.findall(r"[Aa]uto-rout\w* pass #(\d+).*?\((\d+) unrouted(?: and (\d+) violations?)?", log)
    if not ses.exists():
        raise RuntimeError(f"Freerouting produced no session file. Log tail: {log[-800:]}")
    last = passes[-1] if passes else None
    return {"jar": jar.name, "java": info["version"], "seconds": round(time.time() - started, 1),
            "passes": int(last[0]) if last else None,
            "unrouted_reported": int(last[1]) if last else None,
            "violations_reported": int(last[2]) if last and last[2] else None}


def _drc_summary(board_file: Path) -> dict[str, Any]:
    from kicad10_mcp.export_tools import _kicad_cli

    report = board_file.with_suffix(".drc.json")
    subprocess.run([_kicad_cli(), "pcb", "drc", "--refill-zones", "--format", "json",
                    "--severity-all", "-o", str(report), str(board_file)],
                   capture_output=True, text=True, timeout=600)
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"error": "DRC report missing"}
    finally:
        report.unlink(missing_ok=True)
    by_type: dict[str, int] = {}
    for v in data.get("violations", []):
        by_type[v["type"]] = by_type.get(v["type"], 0) + 1
    examples = [f"{v['type']}: " + " / ".join(i["description"] for i in v.get("items", []))
                for v in data.get("violations", [])[:5]]
    return {"unconnected": len(data.get("unconnected_items", [])), "violations": by_type,
            "examples": examples}


def autoroute_file(board_file: Path, out_file: Path, *, max_passes: int = 20,
                   timeout_s: int = 600, lock_existing: bool = True,
                   skip_netclasses: Optional[list[str]] = None) -> dict[str, Any]:
    """Route ``board_file`` and write the result to ``out_file`` (may be the same file)."""
    with tempfile.TemporaryDirectory(prefix="kicad10_mcp_route_") as tmp:
        tmpd = Path(tmp)
        dsn, ses = tmpd / "board.dsn", tmpd / "board.ses"
        before = _drc_summary(board_file)
        exp = _run_kicad_py("export", str(board_file), str(dsn),
                            *(["--lock-existing"] if lock_existing else []))
        fr = run_freerouting(dsn, ses, max_passes=max_passes, timeout_s=timeout_s,
                             skip_netclasses=skip_netclasses)
        imp = _run_kicad_py("import", str(board_file), str(ses), str(out_file))
    after = _drc_summary(out_file)
    fr["note"] = ("Freerouting's own unrouted/violation counts include connections of "
                  "skipped net classes and pours; drc_after is authoritative.")
    return {"freerouting": fr, "existing_tracks_locked": exp.get("locked_for_export", 0),
            "existing_items_kept": imp.get("kept_existing", 0),
            "tracks": imp["tracks"], "vias": imp["vias"],
            "widened_to_min_width": imp.get("widened_to_min_width", 0),
            "unconnected_before": before.get("unconnected"), "drc_after": after}


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def freerouting_status() -> dict[str, Any]:
        """Report whether autorouting can run: Java version, installed Freerouting jars,
        and KiCad's Python (needed for DSN/SES conversion)."""
        info = java_info()
        try:
            kpy = str(kicad_python())
        except RuntimeError as exc:
            kpy = f"missing: {exc}"
        jar = find_jar(info["version"] if info else None)
        return {"java": info, "installed_jars": sorted(p.name for p in TOOLS_DIR.glob("*.jar")),
                "usable_jar": jar.name if jar else None, "kicad_python": kpy,
                "ready": bool(info and info["version"] >= 21 and jar and not kpy.startswith("missing")),
                "note": ("With Java 25+ Freerouting 2.4 is used (seconds per board, honours "
                         "limits); with Java 21-24 only 2.1.0 runs, which ignores pass limits "
                         "and takes minutes.")}

    @mcp.tool()
    def install_freerouting(version: str = "auto") -> dict[str, Any]:
        """Download the official Freerouting release jar from GitHub into
        ~/.kicad10_mcp/freerouting. 'auto' picks the newest release the installed
        Java can run (2.4.1 on Java 25+, 2.1.0 on Java 21-24). Freerouting is GPL-3.0
        and runs as a separate program.

        Args:
            version: 'auto' or an explicit release such as '2.1.0'.
        """
        info = java_info()
        if version == "auto":
            if info is None or info["version"] < 21:
                raise RuntimeError("Install Java 21+ first (Java 25 recommended).")
            version = next(v for need, v in _RELEASES if info["version"] >= need)
        target = TOOLS_DIR / f"freerouting-{version}.jar"
        if target.exists():
            return {"installed": str(target), "already_present": True}
        TOOLS_DIR.mkdir(parents=True, exist_ok=True)
        url = _RELEASE_URL.format(v=version)
        part = target.with_suffix(".part")
        with urllib.request.urlopen(url, timeout=120) as resp, open(part, "wb") as fh:
            shutil.copyfileobj(resp, fh)
        with open(part, "rb") as fh:
            if fh.read(2) != b"PK":
                part.unlink(missing_ok=True)
                raise RuntimeError(f"Download from {url} is not a jar file.")
        part.replace(target)
        return {"installed": str(target), "url": url, "size_mb": round(target.stat().st_size / 1e6, 1)}

    @mcp.tool()
    def autoroute(max_passes: int = 20, timeout_s: int = 600, lock_existing: bool = True,
                  skip_netclasses: list[str] | None = None,
                  board_file: str = "") -> dict[str, Any]:
        """Route the remaining connections with Freerouting.

        Typical flow: set net classes (configure_netclasses), route or pour the
        high-current nets yourself, then autoroute the rest with those classes in
        skip_netclasses. Existing tracks are locked so they are not moved.

        With the board open in KiCad it is saved, backed up
        (<board>.pre-autoroute.kicad_pcb), routed on disk, and reloaded into the
        editor - that replaces the editor's undo history, so the backup is the way
        back. Afterwards zones are refilled and DRC runs; always read 'drc_after'
        (copper text and some keepouts are invisible to Freerouting).

        Args:
            max_passes: Autorouter pass limit (honoured by Freerouting 2.2+).
            timeout_s: Hard limit for the Freerouting run, in seconds.
            lock_existing: Keep existing tracks exactly where they are.
            skip_netclasses: Net classes Freerouting must not route, e.g. ['HighCurrent'].
            board_file: Route this .kicad_pcb on disk instead of the open board.
        """
        if board_file:
            path = Path(board_file)
            backup = path.with_name(path.stem + ".pre-autoroute.kicad_pcb")
            shutil.copy2(path, backup)
            result = autoroute_file(path, path, max_passes=max_passes, timeout_s=timeout_s,
                                    lock_existing=lock_existing, skip_netclasses=skip_netclasses)
            result.update({"board": str(path), "backup": str(backup), "reloaded": False})
            return result

        from kicad10_mcp.connection import require_board
        from kicad10_mcp.export_tools import _pcb_path

        board = require_board()
        path = Path(_pcb_path(True))  # saves the open board first
        backup = path.with_name(path.stem + ".pre-autoroute.kicad_pcb")
        shutil.copy2(path, backup)
        result = autoroute_file(path, path, max_passes=max_passes, timeout_s=timeout_s,
                                lock_existing=lock_existing, skip_netclasses=skip_netclasses)
        try:
            board.revert()  # reload the routed file into the editor
            reloaded = True
        except Exception as exc:  # noqa: BLE001
            reloaded = False
            result["reload_error"] = (f"{type(exc).__name__}: {exc}. The routed board is on "
                                      "disk; reopen it in KiCad (File > Revert).")
        result.update({"board": str(path), "backup": str(backup), "reloaded": reloaded})
        return result
