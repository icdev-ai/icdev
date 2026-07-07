# CUI // SP-CTI
"""Read-only HTTP client for a running Compass backend.

Compass (`C:\\AI\\standalone\\compass`) is a separate standalone app --
LCAT/staffing, document analysis, rate-card automation, and AI-assisted
writing for GovCon teams -- that itself bridges back into ICDEV via
`tools/mcp`'s `dic_search`/`dic_ingest`/`ace_persona_query`/`council_query`
(see Compass's own `tools/integrations/icdev_client.py`, cloned from
idea_lab's identical pattern). This module is the reverse direction: it
lets ICDEV's own CPMP/GovCon modules query Compass's LCAT taxonomy and
staffing data instead of duplicating it (Compass's `tools/govcon/
lcat_mapper.py` and `personnel_manager.py` were themselves the source
Compass vendored its own equivalents from -- this bridge is for querying
Compass's live, evolving copy, not re-deriving it here).

Compass is a plain FastAPI web app, not an MCP server, so this is a simple
HTTP client (via `tools.http.client.request`, ICDEV's standard outbound-HTTP
helper) rather than a stdio/JSON-RPC subprocess bridge. Every public
function degrades to `None` on ANY failure (integration disabled, Compass
unreachable, timeout, non-2xx response, malformed JSON) and never raises --
this integration is entirely optional; no CPMP/GovCon workflow depends on
Compass being present, running, or even installed.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import yaml  # noqa: E402

from tools.http.client import request as http_request  # noqa: E402


def _load_config() -> dict[str, Any]:
    path = _ROOT / "args" / "compass_integration.yaml"
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:
        return {}


def _compass_url(cfg: dict[str, Any]) -> str | None:
    if not cfg.get("enabled", False):
        return None
    url = (cfg.get("compass_url") or "").strip()
    return url.rstrip("/") or None


def lcat_lookup(text: str) -> dict | None:
    """Look up the best-matching BLS SOC labor category for `text` (a task
    description, resume, etc.) via Compass's live LCAT taxonomy. Returns
    {soc_code, title, confidence, match_score}, or None on any failure."""
    if not (text or "").strip():
        return None
    cfg = _load_config()
    base_url = _compass_url(cfg)
    if base_url is None:
        return None

    try:
        resp = http_request(
            "POST", f"{base_url}/api/staffing/lcat-lookup",
            json={"text": text}, timeout=cfg.get("timeout_seconds", 15.0),
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def staffing_summary() -> dict | None:
    """Fetch Compass's current staffing matrix (personnel vs. resume-
    matched LCAT compliance). Returns {rows, person_count, mismatch_count,
    unresolved_count}, or None on any failure."""
    cfg = _load_config()
    base_url = _compass_url(cfg)
    if base_url is None:
        return None

    try:
        resp = http_request(
            "GET", f"{base_url}/api/staffing/matrix",
            timeout=cfg.get("timeout_seconds", 15.0),
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data if isinstance(data, dict) else None
    except Exception:
        return None

# CUI // SP-CTI
