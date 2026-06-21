"""Connection management for the KiCad IPC API.

A single :class:`kipy.KiCad` client is cached for the lifetime of the server
process.  Helpers raise ``RuntimeError`` with actionable messages when KiCad is
not reachable or the requested document is not open, so the model receives a
clear next step instead of a raw stack trace.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Optional

from kipy import KiCad
from kipy.proto.common.types import DocumentType

_kicad: Optional[KiCad] = None


def _timeout_ms() -> int:
    try:
        return int(os.environ.get("KICAD_API_TIMEOUT_MS", "10000"))
    except ValueError:
        return 10000


def get_kicad(force_reconnect: bool = False) -> KiCad:
    """Return a connected :class:`kipy.KiCad`, (re)connecting if needed."""
    global _kicad
    if _kicad is not None and not force_reconnect:
        return _kicad
    try:
        client = KiCad(timeout_ms=_timeout_ms())
        client.get_version()  # touch the socket so failures surface here
    except Exception as exc:  # noqa: BLE001 - surfaced as actionable message
        _kicad = None
        raise RuntimeError(
            "Could not connect to KiCad's IPC API. Make sure KiCad 10 is running "
            "and the API server is enabled (Preferences > Plugins > 'Enable the "
            "KiCad API server'), then retry. "
            f"Underlying error: {type(exc).__name__}: {exc}"
        ) from exc
    _kicad = client
    return _kicad


def require_board():
    """Return the open PCB, or raise an actionable error."""
    kicad = get_kicad()
    try:
        docs = kicad.get_open_documents(DocumentType.DOCTYPE_PCB)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Failed to query open documents from KiCad: {type(exc).__name__}: {exc}"
        ) from exc
    if not docs:
        raise RuntimeError(
            "No PCB is open in KiCad. Open a .kicad_pcb in the PCB Editor and retry."
        )
    from kipy.board import Board

    return Board(kicad._client, docs[0])


def require_schematic():
    """Return the open schematic, or raise an actionable error."""
    kicad = get_kicad()
    docs = kicad.get_open_documents(DocumentType.DOCTYPE_SCHEMATIC)
    if not docs:
        raise RuntimeError(
            "No schematic is open in KiCad. Open a .kicad_sch in the Schematic "
            "Editor and retry. Note: schematic API coverage is limited in KiCad 10."
        )
    from kipy.schematic import Schematic

    return Schematic(kicad._client, docs[0])


@contextmanager
def commit(board, message: str = ""):
    """Group board mutations into a single undo step.

    On success the commit is pushed; on any exception it is dropped so the
    board is left untouched.
    """
    handle = board.begin_commit()
    try:
        yield handle
    except Exception:
        try:
            board.drop_commit(handle)
        except Exception:  # noqa: BLE001
            pass
        raise
    else:
        board.push_commit(handle, message)
