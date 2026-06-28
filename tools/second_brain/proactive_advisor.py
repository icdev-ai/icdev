# CUI // SP-CTI
"""Proactive AI advisor for the Second Brain canvas.

Three autonomous capabilities:
  1. Stalled-work scanner — detects objectives with no forward motion in N days
  2. Tomorrow-prep generator — evening briefing of what needs to happen next day
  3. Weekly architecture digest — domain intelligence tailored to the user's customer types
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_DEFAULT_STALL_DAYS = 5       # flag objective if no kanban activity in this many days
_DEFAULT_DIGEST_TOPICS = [    # fallback topics when no customer types are known
    "network architecture", "BGP", "SD-WAN", "network redundancy",
    "carrier infrastructure", "peering", "resiliency"
]

# Customer-type → domain topic mapping for the architecture digest
_CUSTOMER_DOMAIN_TOPICS: dict[str, list[str]] = {
    "customer": [           # External customers (CSPs, ISPs)
        "carrier-grade network architecture", "BGP peering", "MPLS/SR-MPLS",
        "peering exchange", "transit provider redundancy", "ISP infrastructure",
        "network scalability", "optical transport",
    ],
    "stakeholder": [        # Internal customers (Network Ops, App Teams)
        "network operations automation", "NOC tooling", "observability for networks",
        "application delivery", "SD-WAN for app teams", "traffic engineering",
        "zero-downtime migration", "capacity planning",
    ],
    "boss": [               # Leadership
        "network strategy", "technology roadmap", "vendor evaluation",
        "TCO optimization", "executive network briefing", "digital transformation",
    ],
    "direct": [             # Team / direct reports
        "network engineering skills", "team automation tooling",
        "documentation standards", "runbook quality", "training resources",
    ],
    "peer": [               # Peers
        "cross-domain architecture", "API-driven networking", "DevNetOps",
        "infrastructure as code", "cloud-native networking",
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# 1. Stalled-work scanner
# ─────────────────────────────────────────────────────────────────────────────

def scan_stalled_objectives(
    user_id: str,
    tenant_id: str = "default",
    stall_days: int = _DEFAULT_STALL_DAYS,
) -> list[dict[str, Any]]:
    """Return objectives that have had no kanban task activity in *stall_days* days."""
    try:
        from tools.second_brain.profile import get_objectives
        objectives = get_objectives(user_id, tenant_id)
    except Exception as exc:
        logger.warning("[proactive_advisor] could not load objectives: %s", exc)
        return []

    active = [o for o in objectives if o.get("status") == "active"]
    if not active:
        return []

    stalled: list[dict[str, Any]] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=stall_days)

    for obj in active:
        obj_id = obj.get("id", "")
        title = obj.get("title", "")
        created_raw = obj.get("created_at", "")

        # Check if there is a linked kanban task updated recently
        last_activity = _find_last_kanban_activity(title)

        if last_activity is None:
            # Never linked to kanban — stalled from creation
            try:
                created_dt = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
                if created_dt < cutoff:
                    stalled.append({
                        "objective_id": obj_id,
                        "title": title,
                        "horizon": obj.get("horizon", ""),
                        "reason": f"No kanban task linked. Objective is {(datetime.now(timezone.utc) - created_dt).days} days old.",
                        "severity": "high" if obj.get("horizon") == "week" else "medium",
                    })
            except (ValueError, TypeError):
                pass
        elif last_activity < cutoff:
            days_idle = (datetime.now(timezone.utc) - last_activity).days
            stalled.append({
                "objective_id": obj_id,
                "title": title,
                "horizon": obj.get("horizon", ""),
                "reason": f"Last kanban task update was {days_idle} days ago.",
                "severity": "high" if days_idle > stall_days * 2 else "medium",
            })

    return stalled


def _find_last_kanban_activity(objective_title: str) -> datetime | None:
    """Return the most recent update timestamp of any kanban task whose title
    fuzzy-matches *objective_title*. Returns None if no match found."""
    try:
        from tools.db.storage import get_connection
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT updated_at FROM kanban_tasks
                WHERE status NOT IN ('done', 'cancelled')
                  AND (title ILIKE %s OR description ILIKE %s)
                ORDER BY updated_at DESC LIMIT 1
                """,
                (f"%{objective_title[:30]}%", f"%{objective_title[:30]}%"),
            ).fetchall()
        if rows:
            raw = rows[0][0]
            if isinstance(raw, str):
                return datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if isinstance(raw, datetime):
                return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    except Exception as exc:
        logger.debug("[proactive_advisor] kanban lookup failed: %s", exc)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 2. Tomorrow-prep generator
# ─────────────────────────────────────────────────────────────────────────────

def generate_tomorrow_prep(
    user_id: str,
    tenant_id: str = "default",
) -> dict[str, Any]:
    """Generate a structured "prep for tomorrow" object and store it in the
    next day's briefing record."""
    from datetime import date
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    try:
        ctx = _load_world_model(user_id, tenant_id)
        stalled = scan_stalled_objectives(user_id, tenant_id)
        calendar_items = _fetch_calendar_items(user_id, for_date=tomorrow)
        open_tasks = _fetch_open_tasks(user_id)
        customers = _load_customers(user_id, tenant_id)
    except Exception as exc:
        logger.warning("[proactive_advisor] tomorrow-prep data load failed: %s", exc)
        return {}

    meeting_preps = []
    for evt in calendar_items[:5]:
        attendee_names = evt.get("attendees", [])
        # Match attendees to known customers
        matched = [
            c for c in customers
            if any(
                (c.get("name", "") or "").lower() in (a or "").lower()
                or (c.get("email", "") or "").lower() in (a or "").lower()
                for a in attendee_names
            )
        ]
        prep_note = ""
        if matched:
            roles = ", ".join(
                f"{m['name']} ({m.get('relationship_type','?')})" for m in matched[:2]
            )
            prep_note = f"Known customer(s) attending: {roles}. Review their current needs before this meeting."
        elif ctx:
            prep_note = f"Prepare a brief status update aligned with your objective: '{(ctx.get('objectives') or [''])[0]}'."

        meeting_preps.append({
            "time": evt.get("start", ""),
            "title": evt.get("title", evt.get("summary", "")),
            "attendees": attendee_names,
            "prep_note": prep_note,
        })

    # High-priority open tasks due tomorrow or overdue
    priority_tasks = [
        t for t in open_tasks
        if t.get("priority") in ("high", "critical")
        or (t.get("due_date") or "") <= tomorrow
    ][:5]

    # LLM-generated focus recommendation
    focus_text = _llm_focus_recommendation(ctx, meeting_preps, priority_tasks, stalled)

    prep = {
        "date": tomorrow,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "focus": focus_text,
        "meetings": meeting_preps,
        "priority_tasks": priority_tasks,
        "stalled_objectives": stalled[:3],
        "customer_nudges": _customer_nudges(customers),
    }

    # Persist to tomorrow's briefing record so morning briefing finds it
    _upsert_prep_section(user_id, tenant_id, tomorrow, prep)
    return prep


def _llm_focus_recommendation(ctx, meetings, tasks, stalled) -> str:
    """Ask LLM for a one-paragraph focus recommendation for tomorrow."""
    if not ctx:
        return "Set up your profile at /me/profile to get personalised focus recommendations."

    meeting_titles = ", ".join(m["title"] for m in meetings[:3]) or "none"
    task_titles = ", ".join(t.get("title", "") for t in tasks[:3]) or "none"
    stall_titles = ", ".join(s["title"] for s in stalled[:2]) or "none"

    prompt = (
        f"You are the AI assistant for {ctx.get('name','the user')}, "
        f"a {ctx.get('title','')} at {ctx.get('org_name','')}.\n"
        f"Tomorrow's meetings: {meeting_titles}.\n"
        f"High-priority open tasks: {task_titles}.\n"
        f"Stalled objectives: {stall_titles}.\n"
        f"Write a single focused paragraph (3-4 sentences) recommending "
        f"what to prioritise tomorrow and why. Be direct and specific."
    )
    try:
        from tools.llm.router import LLMRouter, LLMRequest
        router = LLMRouter()
        resp = router.invoke(
            "summarization",
            LLMRequest(prompt=prompt, max_tokens=200, temperature=0.3),
        )
        return (resp.content or "").strip()
    except Exception as exc:
        logger.debug("[proactive_advisor] LLM focus failed: %s", exc)
        return (
            f"Tomorrow you have {len(meetings)} meeting(s) and {len(tasks)} high-priority task(s). "
            + (f"Watch out for {len(stalled)} stalled objective(s)." if stalled else "")
        ).strip()


def _upsert_prep_section(user_id: str, tenant_id: str, date_str: str, prep: dict) -> None:
    """Store tomorrow-prep in the briefing record for *date_str*."""
    import uuid
    try:
        from tools.second_brain.constants import BRIEFING_ENV_FLAG
        from tools.db.storage import get_canvas_connection, sql_placeholder
        with get_canvas_connection(BRIEFING_ENV_FLAG) as conn:
            ph = sql_placeholder(conn)
            row = conn.execute(
                f"SELECT id, content_json FROM user_daily_briefings "
                f"WHERE user_id={ph} AND tenant_id={ph} AND briefing_date={ph}",
                (user_id, tenant_id, date_str),
            ).fetchone()
            if row:
                record_id = row[0]
                content = json.loads(row[1] or "{}")
                content["tomorrow_prep"] = prep
                conn.execute(
                    f"UPDATE user_daily_briefings SET content_json={ph} WHERE id={ph}",
                    (json.dumps(content), record_id),
                )
            else:
                record_id = str(uuid.uuid4())
                content = {"tomorrow_prep": prep}
                conn.execute(
                    f"INSERT INTO user_daily_briefings "
                    f"(id, user_id, tenant_id, briefing_date, content_json) "
                    f"VALUES ({ph},{ph},{ph},{ph},{ph})",
                    (record_id, user_id, tenant_id, date_str, json.dumps(content)),
                )
            conn.commit()
    except Exception as exc:
        logger.warning("[proactive_advisor] upsert prep failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Weekly architecture digest
# ─────────────────────────────────────────────────────────────────────────────

def generate_weekly_architecture_digest(
    user_id: str,
    tenant_id: str = "default",
) -> dict[str, Any]:
    """Pull architecture intelligence relevant to the user's customer types
    and generate a weekly digest stored in today's briefing."""
    ctx = _load_world_model(user_id, tenant_id)
    customers = _load_customers(user_id, tenant_id)

    # Build topic list from customer types
    topics: list[str] = []
    seen_types: set[str] = set()
    for c in customers:
        ctype = c.get("relationship_type", "")
        if ctype not in seen_types:
            seen_types.add(ctype)
            topics.extend(_CUSTOMER_DOMAIN_TOPICS.get(ctype, [])[:3])

    if not topics:
        topics = _DEFAULT_DIGEST_TOPICS

    # Deduplicate while preserving order
    topics = list(dict.fromkeys(topics))[:10]

    # Pull CVE / threat intel from existing research infrastructure
    cve_items = _fetch_relevant_cves(topics[:3])
    # Pull from research canvas if available
    research_items = _fetch_research_items(topics[:3])

    # LLM-synthesise into a digest
    digest_text = _llm_weekly_digest(ctx, topics, cve_items, research_items)

    digest = {
        "week_of": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "topics": topics[:5],
        "cve_highlights": cve_items[:3],
        "research_highlights": research_items[:5],
        "synthesis": digest_text,
    }

    # Store in today's briefing under "weekly_digest" key
    from datetime import date
    today = date.today().isoformat()
    _upsert_prep_section(user_id, tenant_id, today, {"weekly_digest": digest})

    logger.info("[proactive_advisor] weekly digest generated for %s (%d topics)", user_id, len(topics))
    return digest


def _llm_weekly_digest(ctx, topics, cve_items, research_items) -> str:
    name = ctx.get("name", "the user") if ctx else "the user"
    title = ctx.get("title", "network professional") if ctx else "network professional"
    topic_str = ", ".join(topics[:5])

    cve_text = ""
    if cve_items:
        cve_text = "Relevant CVEs this week: " + "; ".join(
            f"{c.get('id','?')} ({c.get('severity','?')}) — {c.get('description','')[:80]}"
            for c in cve_items[:3]
        )

    research_text = ""
    if research_items:
        research_text = "Recent research: " + "; ".join(
            f"\"{r.get('title','')}\"" for r in research_items[:3]
        )

    prompt = (
        f"You are writing a weekly architecture intelligence digest for {name}, a {title}.\n"
        f"Their domain focus: {topic_str}.\n"
        f"{cve_text}\n{research_text}\n"
        f"Write 3-4 concise bullet points covering: (1) one important trend, "
        f"(2) one risk or CVE to watch, (3) one architectural pattern worth exploring, "
        f"(4) one thought-leadership angle relevant to their customers. "
        f"Be specific and actionable. Format as bullet points starting with •."
    )
    try:
        from tools.llm.router import LLMRouter, LLMRequest
        router = LLMRouter()
        resp = router.invoke(
            "summarization",
            LLMRequest(prompt=prompt, max_tokens=400, temperature=0.4),
        )
        return (resp.content or "").strip()
    except Exception as exc:
        logger.debug("[proactive_advisor] LLM digest failed: %s", exc)
        bullets = [f"• Focus area this week: {t}" for t in topics[:3]]
        if cve_items:
            bullets.append(f"• Security: Review {cve_items[0].get('id','CVE')} affecting your stack.")
        return "\n".join(bullets)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Customer-aware design check (callable by any canvas)
# ─────────────────────────────────────────────────────────────────────────────

def customer_aware_review(
    design_context: dict[str, Any],
    user_id: str,
    tenant_id: str = "default",
) -> list[dict[str, Any]]:
    """Cross-reference a design against the user's known customers and their
    implied SLA/resiliency requirements.

    *design_context* should contain at minimum:
        {"title": str, "description": str, "redundancy_level": "N+0"|"N+1"|"N+2",
         "single_points_of_failure": [...], "availability_target": "99.9" | "99.99" | ...}

    Returns a list of findings, each with keys: customer, finding, severity, recommendation.
    """
    customers = _load_customers(user_id, tenant_id)
    if not customers:
        return []

    findings: list[dict[str, Any]] = []
    redundancy = design_context.get("redundancy_level", "N+1")
    spofs = design_context.get("single_points_of_failure", [])
    avail_target = str(design_context.get("availability_target", "99.9"))

    for customer in customers:
        ctype = customer.get("relationship_type", "")
        cname = customer.get("name", "")

        # External customers (CSPs, ISPs) typically need 99.99%+ / N+2
        if ctype == "customer":
            if spofs:
                findings.append({
                    "customer": cname,
                    "customer_type": "External Customer",
                    "finding": f"Single points of failure detected: {', '.join(str(s) for s in spofs[:3])}.",
                    "severity": "high",
                    "recommendation": (
                        f"{cname} is an external customer expecting carrier-grade reliability. "
                        f"Eliminate all SPOFs or document explicit failover procedures before delivery."
                    ),
                })
            if redundancy == "N+0":
                findings.append({
                    "customer": cname,
                    "customer_type": "External Customer",
                    "finding": "No redundancy (N+0) detected in design.",
                    "severity": "critical",
                    "recommendation": (
                        f"External customers like {cname} (CSP/ISP) typically require N+1 minimum. "
                        f"N+0 creates unacceptable outage risk for their downstream services."
                    ),
                })
            if "99.999" not in avail_target and "99.99" not in avail_target:
                findings.append({
                    "customer": cname,
                    "customer_type": "External Customer",
                    "finding": f"Availability target {avail_target}% may not meet CSP/ISP SLA expectations.",
                    "severity": "medium",
                    "recommendation": (
                        "Verify the SLA with this customer. Most carrier-grade agreements require "
                        "99.99% (52 min downtime/year) or better."
                    ),
                })

        # Internal customers (Network Ops, App Teams) need operational clarity
        elif ctype == "stakeholder":
            if not design_context.get("runbook_referenced"):
                findings.append({
                    "customer": cname,
                    "customer_type": "Internal Customer",
                    "finding": "No runbook or operational documentation referenced in design.",
                    "severity": "medium",
                    "recommendation": (
                        f"{cname} (internal team) will operate this infrastructure daily. "
                        f"Include runbooks, escalation paths, and monitoring thresholds before handoff."
                    ),
                })

    return findings


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_world_model(user_id: str, tenant_id: str) -> dict | None:
    try:
        from tools.second_brain.profile import build_world_model_context
        return build_world_model_context(user_id, tenant_id)
    except Exception as exc:
        logger.debug("[proactive_advisor] world model load failed: %s", exc)
        return None


def _load_customers(user_id: str, tenant_id: str) -> list[dict]:
    try:
        from tools.second_brain.profile import get_relationships
        return get_relationships(user_id, tenant_id) or []
    except Exception:
        return []


def _fetch_calendar_items(user_id: str, for_date: str) -> list[dict]:
    try:
        from tools.second_brain.connectors.google import GoogleConnector
        items = GoogleConnector().get_todays_items(user_id)
        return [i for i in items if (i.get("date") or i.get("start", ""))[:10] == for_date]
    except Exception:
        return []


def _fetch_open_tasks(user_id: str) -> list[dict]:
    try:
        from tools.db.storage import get_connection
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, title, priority, due_date, status
                FROM kanban_tasks
                WHERE status NOT IN ('done','cancelled')
                  AND (assignee_id = %s OR created_by = %s)
                ORDER BY priority DESC, due_date ASC NULLS LAST
                LIMIT 20
                """,
                (user_id, user_id),
            ).fetchall()
        cols = ["id", "title", "priority", "due_date", "status"]
        return [dict(zip(cols, r)) for r in rows]
    except Exception as exc:
        logger.debug("[proactive_advisor] open tasks fetch failed: %s", exc)
        return []


def _fetch_relevant_cves(topics: list[str]) -> list[dict]:
    try:
        from tools.db.storage import get_connection
        topic_filter = " OR ".join(f"description ILIKE %s" for _ in topics)
        params = [f"%{t}%" for t in topics]
        with get_connection() as conn:
            rows = conn.execute(
                f"SELECT cve_id, severity, description, published_at "
                f"FROM cve_entries WHERE ({topic_filter}) "
                f"ORDER BY published_at DESC LIMIT 5",
                params,
            ).fetchall()
        return [
            {"id": r[0], "severity": r[1], "description": r[2], "published_at": str(r[3])}
            for r in rows
        ]
    except Exception:
        return []


def _fetch_research_items(topics: list[str]) -> list[dict]:
    try:
        from tools.db.storage import get_connection
        topic_filter = " OR ".join(f"title ILIKE %s OR content ILIKE %s" for _ in topics)
        params = [p for t in topics for p in (f"%{t}%", f"%{t}%")]
        with get_connection() as conn:
            rows = conn.execute(
                f"SELECT title, source_url, created_at FROM research_findings "
                f"WHERE ({topic_filter}) ORDER BY created_at DESC LIMIT 5",
                params,
            ).fetchall()
        return [{"title": r[0], "url": r[1], "date": str(r[2])} for r in rows]
    except Exception:
        return []


def _customer_nudges(customers: list[dict]) -> list[dict]:
    """Surface customers not contacted recently based on last_contact_at."""
    nudges = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    for c in customers:
        raw = c.get("last_contact_at")
        if not raw:
            nudges.append({
                "name": c.get("name", ""),
                "type": c.get("relationship_type", ""),
                "nudge": "No contact recorded. Consider scheduling a sync.",
            })
            continue
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt < cutoff:
                days = (datetime.now(timezone.utc) - dt).days
                nudges.append({
                    "name": c.get("name", ""),
                    "type": c.get("relationship_type", ""),
                    "nudge": f"Last contact {days} days ago. Due for a check-in.",
                })
        except (ValueError, TypeError):
            pass
    return nudges[:5]
