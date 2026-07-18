#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration Intelligence — Enterprise Goal Manager.

CRUD + NL chat parsing for enterprise technical goals (5-10 year horizon).
Chat input is air-gap conditional: when ICDEV_AIRGAP=true, NL parse is
skipped and goals must be entered via structured form.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
_DB_PATH = BASE_DIR / "data" / "migration_intel.db"
_AIRGAP = os.environ.get("ICDEV_AIRGAP", "").lower() in ("true", "1", "yes")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_conn(db_path: str | None = None):
    from tools.migration_intelligence.db.init_db import get_connection
    return get_connection(db_path)


# =========================================================================
# GOAL CRUD
# =========================================================================

def create_goal(
    title: str,
    description: str = "",
    category: str = "infrastructure",
    horizon_years: int = 5,
    target_year: int | None = None,
    priority: str = "medium",
    source: str = "manual",
    owner: str = "",
    success_criteria: str = "",
    kpis: list[str] | None = None,
    tags: list[str] | None = None,
    created_by: str = "system",
    db_path: str | None = None,
) -> dict[str, Any]:
    """Create a new enterprise technical goal."""
    goal_id = f"goal-{uuid.uuid4().hex[:10]}"
    now = _now()
    conn = _get_conn(db_path)
    try:
        conn.execute(
            """INSERT INTO mi_goals
               (id, title, description, category, horizon_years, target_year,
                priority, source, owner, status, success_criteria, kpis, tags,
                created_by, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',%s,%s,%s,%s,%s,%s)""",
            (
                goal_id, title, description, category, horizon_years, target_year,
                priority, source, owner, success_criteria,
                json.dumps(kpis or []), json.dumps(tags or []),
                created_by, now, now,
            ),
        )
        conn.commit()
        return {"id": goal_id, "title": title, "status": "active", "created_at": now}
    finally:
        conn.close()


def list_goals(status: str | None = None, category: str | None = None, db_path: str | None = None) -> list[dict]:
    conn = _get_conn(db_path)
    try:
        where, params = [], []
        if status:
            where.append("status = %s"); params.append(status)
        if category:
            where.append("category = %s"); params.append(category)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        rows = conn.execute(
            f"SELECT * FROM mi_goals {clause} ORDER BY priority DESC, created_at DESC", params
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_goal(goal_id: str, db_path: str | None = None) -> dict | None:
    conn = _get_conn(db_path)
    try:
        row = conn.execute("SELECT * FROM mi_goals WHERE id = %s", (goal_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_goal(goal_id: str, updates: dict[str, Any], db_path: str | None = None) -> bool:
    allowed = {"title", "description", "category", "horizon_years", "target_year",
               "priority", "owner", "status", "success_criteria", "kpis", "tags"}
    cols = {k: v for k, v in updates.items() if k in allowed}
    if not cols:
        return False
    # JSON-encode list fields
    for f in ("kpis", "tags"):
        if f in cols and isinstance(cols[f], list):
            cols[f] = json.dumps(cols[f])
    cols["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = %s" for k in cols)
    conn = _get_conn(db_path)
    try:
        conn.execute(f"UPDATE mi_goals SET {set_clause} WHERE id = %s", [*cols.values(), goal_id])
        conn.commit()
        return True
    finally:
        conn.close()


# =========================================================================
# NL CHAT GOAL PARSING (air-gap conditional)
# =========================================================================

_GOAL_PARSE_PROMPT = """You are an enterprise IT strategic planner. The user has described their 5-10 year
technical goals in natural language. Extract structured goals from their input.

For each goal, output a JSON object with:
- title: concise goal title (≤80 chars)
- description: 1-2 sentence summary
- category: one of [infrastructure, platform, security, compliance, cost, cloud, network, application, data, workforce]
- horizon_years: integer 1-10
- priority: one of [critical, high, medium, low]
- success_criteria: how success is measured (string)
- kpis: list of 2-4 KPI strings
- tags: list of 2-5 keyword tags

Output a JSON array of goal objects. Output ONLY valid JSON, no prose.

User input: {user_input}"""


def parse_goals_from_chat(
    user_input: str,
    created_by: str = "chat",
    db_path: str | None = None,
) -> dict[str, Any]:
    """Parse natural language goal input into structured goals.

    Air-gap: when ICDEV_AIRGAP=true, returns an error — structured input required.
    """
    log_id = f"chat-{uuid.uuid4().hex[:10]}"

    if _AIRGAP:
        _log_chat(log_id, user_input, [], [], model="none", confidence=0.0, db_path=db_path)
        return {
            "ok": False,
            "error": "Air-gap mode: NL goal parsing unavailable. Use structured goal form.",
            "airgap": True,
            "log_id": log_id,
        }

    try:
        from tools.llm.router import LLMRouter
        router = LLMRouter()
    except Exception as exc:
        return {"ok": False, "error": f"LLM router unavailable: {exc}", "log_id": log_id}

    prompt = _GOAL_PARSE_PROMPT.format(user_input=user_input)
    try:
        from tools.llm.provider import LLMRequest
        response = router.invoke(
            "text_analysis",
            LLMRequest(messages=[{"role": "user", "content": prompt}], max_tokens=2048),
        )
        raw_text = (response.content or "").strip()
    except Exception as exc:
        return {"ok": False, "error": f"LLM call failed: {exc}", "log_id": log_id}

    # Parse JSON from LLM response
    parsed_goals = []
    try:
        # Strip code fences if present
        text = raw_text.strip("`").strip()
        if text.startswith("json"):
            text = text[4:].strip()
        parsed_goals = json.loads(text)
        if not isinstance(parsed_goals, list):
            parsed_goals = [parsed_goals]
    except (json.JSONDecodeError, ValueError):
        _log_chat(log_id, user_input, [], [], model="llm", confidence=0.0, db_path=db_path)
        return {"ok": False, "error": "LLM returned invalid JSON", "raw": raw_text[:500], "log_id": log_id}

    # Create goals from parsed output
    created_ids = []
    for g in parsed_goals:
        if not isinstance(g, dict) or not g.get("title"):
            continue
        result = create_goal(
            title=g.get("title", "Untitled Goal"),
            description=g.get("description", ""),
            category=g.get("category", "infrastructure"),
            horizon_years=int(g.get("horizon_years", 5)),
            priority=g.get("priority", "medium"),
            source="chat",
            success_criteria=g.get("success_criteria", ""),
            kpis=g.get("kpis", []),
            tags=g.get("tags", []),
            created_by=created_by,
            db_path=db_path,
        )
        created_ids.append(result["id"])

    confidence = min(1.0, len(created_ids) / max(1, len(parsed_goals)))
    _log_chat(log_id, user_input, parsed_goals, created_ids, model="llm", confidence=confidence, db_path=db_path)

    return {
        "ok": True,
        "log_id": log_id,
        "goals_parsed": len(parsed_goals),
        "goals_created": len(created_ids),
        "goal_ids": created_ids,
        "confidence": confidence,
    }


def _log_chat(log_id, user_input, parsed_goals, goal_ids, model, confidence, db_path=None):
    conn = _get_conn(db_path)
    try:
        conn.execute(
            """INSERT OR IGNORE INTO mi_goal_chat_log
               (id, user_input, parsed_goals, goals_created, llm_model, confidence, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (log_id, user_input[:2000], json.dumps(parsed_goals), json.dumps(goal_ids),
             model, confidence, _now()),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


# =========================================================================
# GOAL ALIGNMENT
# =========================================================================

def compute_goal_alignment(opportunity_id: str, db_path: str | None = None) -> list[dict]:
    """Compute alignment scores between an opportunity and all active goals.

    Returns sorted list of {goal_id, score, reason} dicts.
    """
    conn = _get_conn(db_path)
    try:
        opp = conn.execute("SELECT * FROM mi_opportunities WHERE id = %s", (opportunity_id,)).fetchone()
        if not opp:
            return []
        goals = conn.execute("SELECT * FROM mi_goals WHERE status = 'active'").fetchall()
        alignments = []
        for g in goals:
            score, reason = _score_alignment(dict(opp), dict(g))
            if score > 0.0:
                alignments.append({"goal_id": g["id"], "score": score, "reason": reason})

        # Upsert alignment rows
        for a in alignments:
            aln_id = f"aln-{uuid.uuid4().hex[:8]}"
            conn.execute(
                """INSERT OR REPLACE INTO mi_goal_alignments
                   (id, goal_id, opportunity_id, alignment_score, alignment_reason, computed_at)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (aln_id, a["goal_id"], opportunity_id, a["score"], a["reason"], _now()),
            )
        conn.commit()
        return sorted(alignments, key=lambda x: x["score"], reverse=True)
    finally:
        conn.close()


def _score_alignment(opp: dict, goal: dict) -> tuple[float, str]:
    """Heuristic alignment scoring — keyword + category matching."""
    score = 0.0
    reasons = []

    opp_text = f"{opp.get('title','')} {opp.get('description','')} {opp.get('opportunity_type','')}".lower()
    goal_text = f"{goal.get('title','')} {goal.get('description','')} {goal.get('category','')}".lower()

    # Category alignment
    _CATEGORY_MAP = {
        "infrastructure": ["hardware", "server", "storage", "datacenter", "rack", "eol", "refresh"],
        "network": ["network", "router", "switch", "firewall", "wan", "sd-wan", "sdwan", "mpls"],
        "cloud": ["cloud", "aws", "azure", "gcp", "migration", "lift"],
        "security": ["security", "vulnerability", "stig", "compliance", "patch", "cve"],
        "platform": ["platform", "kubernetes", "container", "modernization", "refactor"],
        "cost": ["cost", "consolidat", "optimize", "reduce", "saving"],
        "application": ["application", "app", "software", "microservice"],
        "data": ["data", "database", "storage", "backup", "archive"],
    }
    cat = goal.get("category", "")
    keywords = _CATEGORY_MAP.get(cat, [])
    kw_hits = sum(1 for kw in keywords if kw in opp_text)
    if kw_hits > 0:
        score += min(0.5, kw_hits * 0.15)
        reasons.append(f"category match ({cat})")

    # Goal keyword presence in opportunity
    goal_words = [w for w in goal_text.split() if len(w) > 4]
    opp_hits = sum(1 for w in goal_words if w in opp_text)
    if goal_words:
        ratio = opp_hits / len(goal_words)
        score += ratio * 0.3
        if ratio > 0.1:
            reasons.append(f"{opp_hits}/{len(goal_words)} goal keywords matched")

    # Priority boost
    if goal.get("priority") in ("critical", "high"):
        score = min(1.0, score * 1.1)

    return round(min(1.0, score), 3), "; ".join(reasons) or "no alignment"
