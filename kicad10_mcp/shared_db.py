"""Shared, reviewed part database hosted on Supabase (optional).

Reads the ``parts`` table through the Supabase Data API with a *publishable*
key and caches it in ``~/.kicad10_mcp/shared_parts.json`` so the power budget
keeps working offline. New entries are never written to ``parts`` directly:
``submit`` files a proposal in ``part_submissions`` that the project owner
reviews (see supabase/parts_schema.sql).

By default the project's public database is used. The publishable key below is
meant to be public: it maps to the ``anon`` role, which can only read approved
parts and file proposals (enforced by grants + RLS in supabase/parts_schema.sql).

Configuration (environment variables, optional):
  KICAD10_MCP_SUPABASE_URL   https://<project-ref>.supabase.co   (own database)
  KICAD10_MCP_SUPABASE_KEY   its publishable key (never the secret/service key)
  KICAD10_MCP_SHARED_DB=0    turn the shared database off entirely
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

CACHE = Path.home() / ".kicad10_mcp" / "shared_parts.json"
MAX_AGE_S = 24 * 3600
_TIMEOUT_S = 8

DEFAULT_URL = "https://sltliqcuuczmphthlibh.supabase.co"
DEFAULT_KEY = "sb_publishable_7fn-oLZhrqmA2GX5ekuo8w_ZmvscJpT"


def _config() -> Optional[tuple[str, str]]:
    if os.environ.get("KICAD10_MCP_SHARED_DB", "1").strip().lower() in ("0", "false", "off", "no"):
        return None
    url = (os.environ.get("KICAD10_MCP_SUPABASE_URL") or DEFAULT_URL).rstrip("/")
    key = os.environ.get("KICAD10_MCP_SUPABASE_KEY") or DEFAULT_KEY
    if not url or not key:
        return None
    if key.startswith("sb_secret_"):
        raise RuntimeError("KICAD10_MCP_SUPABASE_KEY is a secret key; use the publishable key.")
    return url, key


def configured() -> bool:
    return _config() is not None


def _request(method: str, path: str, body: Any = None, headers: Optional[dict] = None):
    cfg = _config()
    if cfg is None:
        raise RuntimeError("Shared database not configured (set KICAD10_MCP_SUPABASE_URL "
                           "and KICAD10_MCP_SUPABASE_KEY).")
    url, key = cfg
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"{url}/rest/v1/{path}", data=data, method=method)
    req.add_header("apikey", key)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"Supabase {method} {path} failed ({exc.code}): {detail}") from exc


def _read_cache() -> Optional[dict[str, Any]]:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def sync(force: bool = False) -> dict[str, Any]:
    """Refresh the local cache from Supabase (if stale or forced). Never raises for
    network problems - the previous cache stays in use and the error is reported."""
    cache = _read_cache()
    if not configured():
        return {"configured": False, "parts": len((cache or {}).get("parts", {}))}
    fresh = cache and time.time() - cache.get("fetched_at", 0) < MAX_AGE_S
    if fresh and not force:
        return {"configured": True, "refreshed": False, "parts": len(cache["parts"]),
                "fetched_at": cache["fetched_at"]}
    try:
        rows = _request("GET", "parts?select=key,match,entry,source,verified&order=key") or []
    except (RuntimeError, OSError) as exc:
        return {"configured": True, "refreshed": False, "error": str(exc),
                "parts": len((cache or {}).get("parts", {}))}
    parts = {}
    for r in rows:
        entry = dict(r.get("entry") or {})
        entry.update({"match": r["match"], "source": r["source"],
                      "verified": bool(r.get("verified", True))})
        parts[r["key"]] = entry
    data = {"fetched_at": time.time(), "parts": parts}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"configured": True, "refreshed": True, "parts": len(parts)}


def load() -> dict[str, dict[str, Any]]:
    """Cached shared parts (refreshing quietly when stale)."""
    if configured():
        sync()
    return (_read_cache() or {}).get("parts", {})


def submit(key: str, entry: dict[str, Any], client: str = "kicad10-mcp") -> None:
    body = {
        "key": key,
        "match": entry.get("match") or [key],
        "entry": {k: v for k, v in entry.items() if k not in ("match", "source", "verified")},
        "source": entry["source"],
        "verified": bool(entry.get("verified", False)),
        "client": client,
    }
    # return=minimal: submissions are write-only, there is no SELECT policy to read back.
    _request("POST", "part_submissions", body, {"Prefer": "return=minimal"})
