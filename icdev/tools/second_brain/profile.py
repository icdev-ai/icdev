# CUI // SP-CTI
"""Second Brain — user identity profile CRUD and world model builder.

All DB access uses get_canvas_connection() — these tables have no
classification/tenant_id RLS columns and must bypass the global RLS predicate.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.db.storage import get_canvas_connection, sql_placeholder
from tools.logging.icdev_logger import get_logger
from tools.second_brain.constants import BRIEFING_ENV_FLAG, COMM_STYLE_LABELS

logger = get_logger(__name__)

_ENV_FLAG = BRIEFING_ENV_FLAG


def _conn():
    return get_canvas_connection(_ENV_FLAG)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ─────────────────────────────────────────────────────────────────────────────
# Profile (user_identity_profiles)
# ─────────────────────────────────────────────────────────────────────────────


def get_profile(user_id: str, tenant_id: str = "default") -> dict | None:
    """Return the identity profile for *user_id*, or None if not found."""
    with _conn() as conn:
        ph = sql_placeholder(conn)
        row = conn.execute(
            f"SELECT * FROM user_identity_profiles WHERE user_id={ph} AND tenant_id={ph}",
            (user_id, tenant_id),
        ).fetchone()
    if row is None:
        return None
    return dict(row) if hasattr(row, "keys") else dict(zip(
        ["user_id","tenant_id","full_name","work_email","title","seniority_tier",
         "department","timezone","work_start","work_end","focus_block",
         "meeting_heavy_days","briefing_time","delivery_channels","comm_style",
         "org_name","org_industry","org_size","team_mission","profile_summary",
         "context_complete","created_at","updated_at"],
        row,
    ))


def upsert_profile(user_id: str, tenant_id: str = "default", **fields) -> dict:
    """Insert or update profile fields. Returns the full profile after save."""
    fields.pop("user_id", None)
    fields.pop("tenant_id", None)
    fields["updated_at"] = _utcnow()

    with _conn() as conn:
        ph = sql_placeholder(conn)
        existing = conn.execute(
            f"SELECT user_id FROM user_identity_profiles WHERE user_id={ph} AND tenant_id={ph}",
            (user_id, tenant_id),
        ).fetchone()

        if existing is None:
            fields.setdefault("created_at", _utcnow())
            cols = ["user_id", "tenant_id"] + list(fields.keys())
            vals = [user_id, tenant_id] + list(fields.values())
            placeholders = ", ".join(ph for _ in vals)
            col_str = ", ".join(cols)
            conn.execute(
                f"INSERT INTO user_identity_profiles ({col_str}) VALUES ({placeholders})",
                vals,
            )
        else:
            set_clause = ", ".join(f"{k}={ph}" for k in fields)
            vals = list(fields.values()) + [user_id, tenant_id]
            conn.execute(
                f"UPDATE user_identity_profiles SET {set_clause} WHERE user_id={ph} AND tenant_id={ph}",
                vals,
            )
        conn.commit()

    return get_profile(user_id, tenant_id) or {}


# ─────────────────────────────────────────────────────────────────────────────
# Objectives (user_objectives)
# ─────────────────────────────────────────────────────────────────────────────


def get_objectives(user_id: str, tenant_id: str = "default", status: str = "active") -> list[dict]:
    with _conn() as conn:
        ph = sql_placeholder(conn)
        rows = conn.execute(
            f"SELECT * FROM user_objectives WHERE user_id={ph} AND tenant_id={ph} AND status={ph} ORDER BY sort_order, created_at",
            (user_id, tenant_id, status),
        ).fetchall()
    cols = ["id","user_id","tenant_id","title","description","horizon","metric","status","sort_order","created_at"]
    return [dict(zip(cols, r)) if not isinstance(r, dict) else dict(r) for r in rows]


def save_objectives(user_id: str, objectives: list[dict], tenant_id: str = "default") -> list[str]:
    """Replace all active objectives for *user_id* with *objectives*. Returns list of IDs."""
    ids: list[str] = []
    with _conn() as conn:
        ph = sql_placeholder(conn)
        conn.execute(
            f"DELETE FROM user_objectives WHERE user_id={ph} AND tenant_id={ph} AND status='active'",
            (user_id, tenant_id),
        )
        for i, obj in enumerate(objectives):
            oid = obj.get("id") or f"obj-{uuid.uuid4().hex[:12]}"
            conn.execute(
                f"INSERT INTO user_objectives (id,user_id,tenant_id,title,description,horizon,metric,sort_order) VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
                (oid, user_id, tenant_id, obj.get("title",""), obj.get("description",""),
                 obj.get("horizon","quarter"), obj.get("metric",""), i),
            )
            ids.append(oid)
        conn.commit()
    return ids


# ─────────────────────────────────────────────────────────────────────────────
# Relationships (user_relationships)
# ─────────────────────────────────────────────────────────────────────────────


def get_relationships(user_id: str, tenant_id: str = "default") -> list[dict]:
    with _conn() as conn:
        ph = sql_placeholder(conn)
        rows = conn.execute(
            f"SELECT * FROM user_relationships WHERE user_id={ph} AND tenant_id={ph} ORDER BY relationship_type, name",
            (user_id, tenant_id),
        ).fetchall()
    cols = ["id","user_id","tenant_id","name","title","email","org","relationship_type","notes","last_contact_at","created_at"]
    return [dict(zip(cols, r)) if not isinstance(r, dict) else dict(r) for r in rows]


def save_relationships(user_id: str, relationships: list[dict], tenant_id: str = "default") -> list[str]:
    ids: list[str] = []
    with _conn() as conn:
        ph = sql_placeholder(conn)
        conn.execute(f"DELETE FROM user_relationships WHERE user_id={ph} AND tenant_id={ph}", (user_id, tenant_id))
        for rel in relationships:
            rid = rel.get("id") or f"rel-{uuid.uuid4().hex[:12]}"
            conn.execute(
                f"INSERT INTO user_relationships (id,user_id,tenant_id,name,title,email,org,relationship_type,notes) VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
                (rid, user_id, tenant_id, rel.get("name",""), rel.get("title",""),
                 rel.get("email",""), rel.get("org",""), rel.get("relationship_type","other"), rel.get("notes","")),
            )
            ids.append(rid)
        conn.commit()
    return ids


# ─────────────────────────────────────────────────────────────────────────────
# Challenges (user_challenges)
# ─────────────────────────────────────────────────────────────────────────────


def get_challenges(user_id: str, tenant_id: str = "default", status: str = "active") -> list[dict]:
    with _conn() as conn:
        ph = sql_placeholder(conn)
        rows = conn.execute(
            f"SELECT * FROM user_challenges WHERE user_id={ph} AND tenant_id={ph} AND status={ph} ORDER BY severity DESC, created_at",
            (user_id, tenant_id, status),
        ).fetchall()
    cols = ["id","user_id","tenant_id","challenge_key","custom_description","severity","status","created_at"]
    return [dict(zip(cols, r)) if not isinstance(r, dict) else dict(r) for r in rows]


def save_challenges(user_id: str, challenges: list[dict], tenant_id: str = "default") -> list[str]:
    ids: list[str] = []
    with _conn() as conn:
        ph = sql_placeholder(conn)
        conn.execute(f"DELETE FROM user_challenges WHERE user_id={ph} AND tenant_id={ph} AND status='active'", (user_id, tenant_id))
        for chal in challenges:
            cid = chal.get("id") or f"chal-{uuid.uuid4().hex[:12]}"
            conn.execute(
                f"INSERT INTO user_challenges (id,user_id,tenant_id,challenge_key,custom_description,severity) VALUES ({ph},{ph},{ph},{ph},{ph},{ph})",
                (cid, user_id, tenant_id, chal.get("challenge_key",""), chal.get("custom_description",""), chal.get("severity","medium")),
            )
            ids.append(cid)
        conn.commit()
    return ids


# ─────────────────────────────────────────────────────────────────────────────
# Full profile save (wizard Phase 6 submission)
# ─────────────────────────────────────────────────────────────────────────────


def save_full_profile(user_id: str, data: dict, tenant_id: str = "default") -> dict:
    """Save profile + objectives + relationships + challenges from wizard submission.

    *data* shape:
        {profile: {...}, objectives: [...], relationships: [...], challenges: [...]}
    Returns the assembled full profile.
    """
    profile_fields = data.get("profile", {})
    objectives = data.get("objectives", [])
    relationships = data.get("relationships", [])
    challenges = data.get("challenges", [])

    if "delivery_channels" in profile_fields and isinstance(profile_fields["delivery_channels"], list):
        profile_fields["delivery_channels"] = json.dumps(profile_fields["delivery_channels"])
    if "meeting_heavy_days" in profile_fields and isinstance(profile_fields["meeting_heavy_days"], list):
        profile_fields["meeting_heavy_days"] = json.dumps(profile_fields["meeting_heavy_days"])

    upsert_profile(user_id, tenant_id, **profile_fields)
    if objectives:
        save_objectives(user_id, objectives, tenant_id)
    if relationships:
        save_relationships(user_id, relationships, tenant_id)
    if challenges:
        save_challenges(user_id, challenges, tenant_id)

    return get_full_profile(user_id, tenant_id)


def get_full_profile(user_id: str, tenant_id: str = "default") -> dict:
    """Return profile + objectives + relationships + challenges in one dict."""
    return {
        "profile": get_profile(user_id, tenant_id) or {},
        "objectives": get_objectives(user_id, tenant_id),
        "relationships": get_relationships(user_id, tenant_id),
        "challenges": get_challenges(user_id, tenant_id),
    }


# ─────────────────────────────────────────────────────────────────────────────
# World model context (for ACE SOUL injection)
# ─────────────────────────────────────────────────────────────────────────────


def build_world_model_context(user_id: str, tenant_id: str = "default") -> dict[str, Any] | None:
    """Return a compact dict for ACE SOUL injection. None if profile is incomplete."""
    profile = get_profile(user_id, tenant_id)
    if not profile or not profile.get("context_complete"):
        return None

    objectives = get_objectives(user_id, tenant_id)
    relationships = get_relationships(user_id, tenant_id)
    challenges = get_challenges(user_id, tenant_id)

    comm_style_val = profile.get("comm_style", 3)
    try:
        comm_style_val = int(comm_style_val)
    except (TypeError, ValueError):
        comm_style_val = 3

    return {
        "name": profile.get("full_name") or user_id,
        "title": profile.get("title") or "Team Member",
        "org_name": profile.get("org_name") or "",
        "seniority_tier": profile.get("seniority_tier") or "ic",
        "timezone": profile.get("timezone") or "UTC",
        "comm_style_label": COMM_STYLE_LABELS.get(comm_style_val, "Balanced"),
        "objectives": [o["title"] for o in objectives[:5]],
        "challenge_keys": [
            c.get("challenge_key") or c.get("custom_description", "")[:60]
            for c in challenges[:5]
        ],
        "relationship_names": [
            f"{r['name']} ({r.get('relationship_type','peer')})"
            for r in relationships[:8]
        ],
        "briefing_time": profile.get("briefing_time") or "08:00",
        "delivery_channels": json.loads(profile.get("delivery_channels") or '["dashboard"]'),
    }


# ─────────────────────────────────────────────────────────────────────────────
# LLM profile summary generator
# ─────────────────────────────────────────────────────────────────────────────


def generate_profile_summary(user_id: str, tenant_id: str = "default") -> str:
    """Generate a 2-paragraph LLM narrative of the user's world model.

    Stores the result in user_identity_profiles.profile_summary and returns it.
    Falls back to a template if the LLM is unavailable.
    """
    ctx = build_world_model_context(user_id, tenant_id)
    if not ctx:
        return ""

    prompt = f"""You are an AI executive assistant onboarding assistant.
Write a concise 2-paragraph professional profile summary for:
Name: {ctx['name']}
Role: {ctx['title']} at {ctx['org_name']}
Seniority: {ctx['seniority_tier']}
Top objectives: {'; '.join(ctx['objectives'][:3]) or 'Not specified'}
Key challenges: {'; '.join(ctx['challenge_keys'][:3]) or 'Not specified'}
Key relationships: {', '.join(ctx['relationship_names'][:5]) or 'Not specified'}
Communication style: {ctx['comm_style_label']}

Paragraph 1: describe who this person is and what they're working toward.
Paragraph 2: describe what their AI coworker should focus on to help them succeed.
Be specific, warm, and professional. Do not use bullet points."""

    summary = ""
    try:
        from tools.second_brain.redaction_util import redact_for_llm
        prompt = redact_for_llm(prompt)  # cnr-me-02: mask PII before egress
        from tools.llm.router import LLMRouter
        router = LLMRouter()
        result = router.invoke("summarization", {"prompt": prompt, "max_tokens": 300})
        summary = (result or {}).get("content") or (result or {}).get("text") or str(result or "")
    except Exception as exc:
        logger.warning("[second_brain] LLM summary generation failed: %s", exc)
        # Template fallback
        summary = (
            f"{ctx['name']} is a {ctx['title']} at {ctx['org_name']}. "
            f"Their top priorities are: {'; '.join(ctx['objectives'][:3]) or 'not yet defined'}. "
            f"\n\nTheir AI coworker should help them tackle: {'; '.join(ctx['challenge_keys'][:3]) or 'not yet defined'}, "
            f"working in a {ctx['comm_style_label'].lower()} communication style "
            f"across relationships with {', '.join(ctx['relationship_names'][:3]) or 'their key stakeholders'}."
        )

    if summary:
        upsert_profile(user_id, tenant_id, profile_summary=summary)

    return summary


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Second Brain profile utility")
    parser.add_argument("--user-id", default="default")
    parser.add_argument("--tenant-id", default="default")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--summary", action="store_true", help="Generate LLM profile summary")
    args = parser.parse_args()

    if args.summary:
        result = generate_profile_summary(args.user_id, args.tenant_id)
        print(result)
    else:
        data = get_full_profile(args.user_id, args.tenant_id)
        if args.json:
            print(json.dumps(data, indent=2, default=str))
        else:
            profile = data["profile"]
            print(f"Profile: {profile.get('full_name','?')} | {profile.get('title','?')} | {profile.get('org_name','?')}")
            print(f"Objectives: {len(data['objectives'])} | Relationships: {len(data['relationships'])} | Challenges: {len(data['challenges'])}")
            print(f"Context complete: {bool(profile.get('context_complete'))}")
