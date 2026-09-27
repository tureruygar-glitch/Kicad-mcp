"""Connection management for the KiCad IPC API.

A single :class:`kipy.KiCad` client is cached for the lifetime of the server
process.  Helpers raise ``RuntimeError`` with actionable messages when KiCad is
not reachable or the requested document is not open, so the model receives a
clear next step instead of a raw stack trace.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from typing import Optional

from kipy import KiCad
from kipy.errors import ApiError, ConnectionError as KiCadConnectionError
from kipy.proto.common import ApiStatusCode
from kipy.proto.common.types import DocumentType

_kicad: Optional[KiCad] = None

_BUSY_MESSAGE = (
    "KiCad is running but busy and not accepting API calls: a dialog is open (look "
    "for a modal window, e.g. a file-lock warning after a crash) or an interactive "
    "tool is running. Close it / press Esc in the editor, then retry."
)


def _is_busy(exc: Exception) -> bool:
    return isinstance(exc, ApiError) and exc.code in (ApiStatusCode.AS_NOT_READY,
                                                      ApiStatusCode.AS_BUSY)


def _timeout_ms() -> int:
    try:
        return int(os.environ.get("KICAD_API_TIMEOUT_MS", "10000"))
    except ValueError:
        return 10000


def _busy_wait_s() -> float:
    try:
        return float(os.environ.get("KICAD_API_BUSY_WAIT_S", "5"))
    except ValueError:
        return 5.0


def _retry_when_busy(client: KiCad) -> KiCad:
    """Make every request of ``client`` wait out short busy spells.

    KiCad rejects requests up front (without running them) while it is busy -
    e.g. during its periodic local-history autosave - so resending is safe. Only
    a lasting busy state (open dialog, active interactive tool) reaches the caller,
    as the actionable _BUSY_MESSAGE.
    """
    inner = client._client
    send = inner.send

    def send_with_retry(command, response_type):
        deadline = time.monotonic() + _busy_wait_s()
        while True:
            try:
                return send(command, response_type)
            except ApiError as exc:
                if not _is_busy(exc):
                    raise
                if time.monotonic() >= deadline:
                    raise RuntimeError(_BUSY_MESSAGE) from exc
                time.sleep(0.25)

    inner.send = send_with_retry
    return client


def get_kicad(force_reconnect: bool = False) -> KiCad:
    """Return a connected :class:`kipy.KiCad`, (re)connecting if needed."""
    global _kicad
    if _kicad is not None and not force_reconnect:
        # A restarted KiCad (e.g. after a crash) has a new session token and
        # rejects the cached client's; reconnect instead of failing every call.
        try:
            _kicad.ping()
            return _kicad
        except ApiError as exc:
            if _is_busy(exc):
                raise RuntimeError(_BUSY_MESSAGE) from exc
            if exc.code != ApiStatusCode.AS_TOKEN_MISMATCH:
                raise
        except KiCadConnectionError:
            pass
    try:
        client = _retry_when_busy(KiCad(timeout_ms=_timeout_ms()))
        client.get_version()  # touch the socket so failures surface here
    except Exception as exc:  # noqa: BLE001 - surfaced as actionable message
        _kicad = None
        if isinstance(exc, RuntimeError) and str(exc) == _BUSY_MESSAGE:
            raise
        raise RuntimeError(
            "Could not connect to KiCad's IPC API. Make sure KiCad 10 is running "
            "and the API server is enabled (Preferences > Plugins > 'Enable the "
            "KiCad API server'), then retry. "
            f"Underlying error: {type(exc).__name__}: {exc}"
        ) from exc
    _kicad = client
    return _kicad


def open_documents(kicad: KiCad, doc_type) -> list:
    """Documents of ``doc_type`` open in KiCad; [] when that editor isn't open.

    Only the editor frames answer GetOpenDocuments, so with just the project
    manager running KiCad replies AS_UNHANDLED ("no handler available").
    """
    try:
        return list(kicad.get_open_documents(doc_type))
    except ApiError as exc:
        if exc.code == ApiStatusCode.AS_UNHANDLED:
            return []
        if _is_busy(exc):
            raise RuntimeError(_BUSY_MESSAGE) from exc
        raise


def require_board():
    """Return the open PCB, or raise an actionable error."""
    kicad = get_kicad()
    try:
        docs = open_documents(kicad, DocumentType.DOCTYPE_PCB)
    except RuntimeError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Failed to query open documents from KiCad: {type(exc).__name__}: {exc}"
        ) from exc
    if not docs:
        raise RuntimeError(
            "No PCB is open in KiCad. Open the project and its board in the PCB "
            "Editor (a new board needs Tools > Update PCB from Schematic and a "
            "save first), then retry."
        )
    from kipy.board import Board

    return Board(kicad._client, docs[0])


def require_schematic():
    """Return the open schematic, or raise an actionable error."""
    kicad = get_kicad()
    docs = open_documents(kicad, DocumentType.DOCTYPE_SCHEMATIC)
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
