"""RFI Style Engine — style guide merge, compliance checking, page-count estimation.

Phase A: deterministic checks (forbidden phrases, required headings, classification markings).
Phase B+: tone LLM check and engine-weighted context assembly added when engine runner ships.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.govcon.rfi_style_engine")

_STYLE_YAML = _ROOT / "args" / "govcon" / "company_style_guide.yaml"


# ── Company style guide ───────────────────────────────────────────────────────

def _load_yaml_guide() -> dict:
    try:
        import yaml
        with open(_STYLE_YAML, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("Could not load company_style_guide.yaml: %s", exc)
        return {}


def get_company_style_guide() -> dict:
    """Return the company-wide style guide. YAML wins over DB if both exist."""
    guide = _load_yaml_guide()
    if guide:
        return guide
    # Fallback: read from DB (populated via icdev profile / admin UI)
    try:
        from tools.db.storage import get_canvas_connection
        db = get_canvas_connection("ICDEV_DB_URL")
        row = db.execute(
            "SELECT * FROM rfi_company_style_guide WHERE is_active=TRUE ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row:
            guide = dict(row)
            for field in ("forbidden_phrases", "required_headings"):
                if isinstance(guide.get(field), str):
                    try:
                        guide[field] = json.loads(guide[field])
                    except Exception:
                        guide[field] = []
    except Exception as exc:
        logger.warning("Could not load company style guide from DB: %s", exc)
    return guide or {}


# ── Session style guide ───────────────────────────────────────────────────────

def get_session_style_guide(session_id: str) -> dict:
    """Return the effective style guide for a session.

    Merges company base + session-specific overrides.
    Session overrides win on conflict; page_limit and words_per_page
    are separate DB columns folded into the result dict.
    """
    base = get_company_style_guide()
    try:
        from tools.db.storage import get_canvas_connection
        db = get_canvas_connection("ICDEV_DB_URL")
        row = db.execute(
            "SELECT style_guide_overrides, page_limit, words_per_page "
            "FROM rfi_workbench_sessions WHERE id=%s",
            (session_id,),
        ).fetchone()
        if not row:
            return base
        d = dict(row)
        overrides: dict = {}
        raw = d.get("style_guide_overrides")
        if raw:
            try:
                overrides = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except Exception:
                pass
        merged = {**base, **overrides}
        if d.get("page_limit") is not None:
            merged["page_limit"] = d["page_limit"]
        if d.get("words_per_page") is not None:
            merged["words_per_page"] = d["words_per_page"]
        return merged
    except Exception as exc:
        logger.warning("Could not load session style guide for %s: %s", session_id, exc)
        return base


def set_session_style_overrides(
    session_id: str,
    overrides: dict,
    page_limit: int | None = None,
    words_per_page: int | None = None,
):
    """Persist session-level style overrides. page_limit / words_per_page go to dedicated columns."""
    from tools.db.storage import get_canvas_connection
    db = get_canvas_connection("ICDEV_DB_URL")
    db.execute(
        "UPDATE rfi_workbench_sessions "
        "SET style_guide_overrides=%s, page_limit=%s, words_per_page=%s, updated_at=%s "
        "WHERE id=%s",
        (json.dumps(overrides), page_limit, words_per_page, datetime.now(timezone.utc).isoformat(), session_id),
    )
    db.commit()


# ── Section limits ────────────────────────────────────────────────────────────

def get_section_limits(section_id: str) -> dict:
    """Return per-section word_limit and page_limit (both may be None = unlimited)."""
    try:
        from tools.db.storage import get_canvas_connection
        db = get_canvas_connection("ICDEV_DB_URL")
        row = db.execute(
            "SELECT word_limit, page_limit FROM rfi_workbench_sections WHERE id=%s",
            (section_id,),
        ).fetchone()
        if not row:
            return {"word_limit": None, "page_limit": None}
        d = dict(row)
        return {"word_limit": d.get("word_limit"), "page_limit": d.get("page_limit")}
    except Exception as exc:
        logger.warning("Could not get section limits for %s: %s", section_id, exc)
        return {"word_limit": None, "page_limit": None}


def set_section_limits(
    section_id: str,
    word_limit: int | None,
    page_limit: float | None,
):
    """Persist per-section word and page limits."""
    from tools.db.storage import get_canvas_connection
    db = get_canvas_connection("ICDEV_DB_URL")
    db.execute(
        "UPDATE rfi_workbench_sections SET word_limit=%s, page_limit=%s, updated_at=%s WHERE id=%s",
        (word_limit, page_limit, datetime.now(timezone.utc).isoformat(), section_id),
    )
    db.commit()


# ── Page count estimation ─────────────────────────────────────────────────────

def estimate_page_count(content: str, words_per_page: int = 250) -> float:
    """Return estimated page count (float) from a word-count formula."""
    if not content or not content.strip():
        return 0.0
    words = len(content.strip().split())
    return round(words / max(words_per_page, 1), 2)


# ── Style compliance check ────────────────────────────────────────────────────

def check_style_compliance(content: str, style_guide: dict) -> dict:
    """Run deterministic style compliance checks against the effective style guide.

    Returns:
        {
            "score": int (0-100),
            "findings": [{"type", "severity", "message", "line"}]
        }

    Checks (Phase A — no LLM):
    1. Forbidden phrase scan (regex, case-insensitive)
    2. Required heading presence check
    3. Classification marking presence (UNCLASSIFIED / CUI)
    """
    findings: list[dict] = []
    lines = content.split("\n") if content else []

    # 1. Forbidden phrase scan
    forbidden = style_guide.get("forbidden_phrases") or []
    if isinstance(forbidden, str):
        try:
            forbidden = json.loads(forbidden)
        except Exception:
            forbidden = []

    seen_phrases: set[str] = set()
    for i, line in enumerate(lines, 1):
        for phrase in forbidden:
            if phrase.lower() in seen_phrases:
                continue
            if re.search(re.escape(phrase), line, re.IGNORECASE):
                findings.append({
                    "type": "forbidden_phrase",
                    "severity": "warning",
                    "message": f"Avoid '{phrase}' (line {i})",
                    "line": i,
                })
                seen_phrases.add(phrase.lower())

    # 2. Required heading check
    required_headings = style_guide.get("required_headings") or []
    if isinstance(required_headings, str):
        try:
            required_headings = json.loads(required_headings)
        except Exception:
            required_headings = []

    content_lower = content.lower() if content else ""
    for heading in required_headings:
        if heading.lower() not in content_lower:
            findings.append({
                "type": "missing_heading",
                "severity": "error",
                "message": f"Required heading not found: '{heading}'",
                "line": 0,
            })

    # 3. Classification marking check
    if content and not re.search(r"\bUNCLASSIFIED\b|\bCUI\b", content, re.IGNORECASE):
        findings.append({
            "type": "missing_classification",
            "severity": "warning",
            "message": "No classification marking found (UNCLASSIFIED or CUI required)",
            "line": 0,
        })

    # Score: start at 100, deduct per finding
    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = sum(1 for f in findings if f["severity"] == "warning")
    score = max(0, 100 - errors * 20 - warnings * 5)

    return {"score": score, "findings": findings}
