"""Specctra DSN export / SES import, run with KiCad's own Python (pcbnew).

KiCad 10's kicad-cli cannot export DSN, but the bundled pcbnew module can. This
script is executed as a separate process with KiCad's interpreter so the MCP
server itself never imports pcbnew.

usage:
  python specctra.py export <board.kicad_pcb> <out.dsn> [--lock-existing]
  python specctra.py import <board.kicad_pcb> <in.ses> <out.kicad_pcb>
  python specctra.py strip  <board.kicad_pcb> <out.kicad_pcb> [keep,nets]   (tests only)
  python specctra.py fingerprint <board.kicad_pcb> <net,net>                (tests only)

Prints one JSON object between @@KICAD10_MCP_RESULT@@ and @@END@@ on stdout.
"""

import json
import sys

import pcbnew


def _tracks(board):
    return list(board.GetTracks())


def export(board_path, dsn_path, lock_existing):
    board = pcbnew.LoadBoard(board_path)
    locked = 0
    if lock_existing:
        # Locked tracks are written as fixed ("protect") wires, so Freerouting keeps
        # hand-routed copper exactly where it is and only routes what is missing.
        for t in _tracks(board):
            if not t.IsLocked():
                t.SetLocked(True)
                locked += 1
    ok = pcbnew.ExportSpecctraDSN(board, dsn_path)
    return {"ok": bool(ok), "dsn": dsn_path, "existing_tracks": len(_tracks(board)),
            "locked_for_export": locked}


def _key(item):
    """Geometry identity of a track/arc/via, rounded to 1 um."""
    def r(v):
        return round(v / 1000)
    net = item.GetNetname()
    if item.Type() == pcbnew.PCB_VIA_T:
        p = item.GetPosition()
        return ("via", r(p.x), r(p.y), net)
    a, b = item.GetStart(), item.GetEnd()
    ends = tuple(sorted([(r(a.x), r(a.y)), (r(b.x), r(b.y))]))
    return ("seg", ends, item.GetLayer(), r(item.GetWidth()), net)


def import_ses(board_path, ses_path, out_path):
    """Import a session, keeping every track that existed before.

    ImportSpecctraSES replaces all tracks with the session's wires, but Freerouting
    leaves fixed (locked) wires out of the session - so hand-routed copper would be
    lost. Existing items are cloned first and put back unless the session already
    contains the same geometry.
    """
    board = pcbnew.LoadBoard(board_path)
    originals = [(t.Duplicate(), _key(t)) for t in _tracks(board)]
    ok = pcbnew.ImportSpecctraSES(board, ses_path)
    restored = 0
    if ok:
        present = {_key(t) for t in _tracks(board)}
        for clone, key in originals:
            if key not in present:
                board.Add(clone)
                present.add(key)
                restored += 1
        pcbnew.SaveBoard(out_path, board)
    vias = sum(1 for t in _tracks(board) if t.Type() == pcbnew.PCB_VIA_T)
    return {"ok": bool(ok), "board": out_path, "tracks": len(_tracks(board)) - vias, "vias": vias,
            "kept_existing": len(originals), "restored_existing": restored}


def strip(board_path, out_path, keep_nets=()):
    board = pcbnew.LoadBoard(board_path)
    n = 0
    for t in _tracks(board):
        if t.GetNetname() in keep_nets:
            continue
        board.Remove(t)
        n += 1
    pcbnew.SaveBoard(out_path, board)
    return {"ok": True, "removed": n, "board": out_path}


def fingerprint(board_path, nets):
    """Sorted geometry keys of all tracks/vias on the given nets (tests only)."""
    board = pcbnew.LoadBoard(board_path)
    keys = sorted(repr(_key(t)) for t in _tracks(board) if t.GetNetname() in nets)
    return {"ok": True, "count": len(keys), "keys": keys}


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    try:
        if cmd == "export":
            res = export(args[0], args[1], "--lock-existing" in args)
        elif cmd == "import":
            res = import_ses(args[0], args[1], args[2])
        elif cmd == "strip":
            res = strip(args[0], args[1], set(args[2].split(",")) if len(args) > 2 else ())
        elif cmd == "fingerprint":
            res = fingerprint(args[0], set(args[1].split(",")))
        else:
            res = {"ok": False, "error": f"unknown command {cmd}"}
    except Exception as exc:  # noqa: BLE001 - reported to the caller as JSON
        res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    # SWIG writes leak warnings to the same stdout without newlines, so wrap the
    # result in markers the caller can find anywhere in the stream.
    sys.stdout.write("\n@@KICAD10_MCP_RESULT@@" + json.dumps(res) + "@@END@@\n")
    sys.stdout.flush()
