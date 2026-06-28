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
    _upsert_prep_section(user_id, tenant_id, tomorrow, {"tomorrow_prep": prep})
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
    """Merge *prep* keys into the briefing record for *date_str*.

    Each caller wraps its section under a named key, e.g.
    ``{"tomorrow_prep": {...}}`` or ``{"weekly_digest": {...}}``.
    This function does a shallow ``content.update(prep)`` so multiple callers
    each add their own top-level key without overwriting each other.
    """
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
                content.update(prep)
                conn.execute(
                    f"UPDATE user_daily_briefings SET content_json={ph} WHERE id={ph}",
                    (json.dumps(content), record_id),
                )
            else:
                record_id = str(uuid.uuid4())
                conn.execute(
                    f"INSERT INTO user_daily_briefings "
                    f"(id, user_id, tenant_id, briefing_date, content_json) "
                    f"VALUES ({ph},{ph},{ph},{ph},{ph})",
                    (record_id, user_id, tenant_id, date_str, json.dumps(prep)),
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
    """Pull intelligence relevant to the user's role + customer types and
    generate a weekly digest stored in today's briefing."""
    from tools.second_brain.role_advisor import get_digest_topics

    ctx = _load_world_model(user_id, tenant_id)

    # Role-aware topics (combines role persona + customer type boost)
    topics: list[str] = get_digest_topics(user_id, tenant_id)
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
        "standards_watch": generate_standards_watch(user_id, tenant_id),
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
    """Cross-reference a design against the user's role + customers.

    Returns findings combining:
      1. Role-aware dimension checks (from args/role_canvas_affinity.yaml)
      2. Customer-specific expectation checks (derived from relationship types)

    *design_context* is a free-form dict — the advisor checks which expected
    keys are present vs absent; it does not require a fixed schema. Canvases
    pass whatever fields they have; unfilled checks become medium/high findings.
    """
    from tools.second_brain.role_advisor import build_role_aware_review

    # 1. Role-aware review (dimension checks from YAML config)
    findings: list[dict[str, Any]] = build_role_aware_review(design_context, user_id, tenant_id)

    # 2. Customer expectation checks (relationship-type heuristics, role-agnostic)
    customers = _load_customers(user_id, tenant_id)
    for customer in customers:
        ctype = customer.get("relationship_type", "")
        cname = customer.get("name", "")
        customer_findings = _check_customer_expectations(design_context, cname, ctype)
        findings.extend(customer_findings)

    return findings


def _check_customer_expectations(
    design_context: dict[str, Any],
    cname: str,
    ctype: str,
) -> list[dict[str, Any]]:
    """Generic customer-expectation checks that apply regardless of user role.

    These are derived from the relationship_type, not the user's job title:
      - customer (external): availability, no SPOFs, explicit SLA reference
      - stakeholder (internal): runbook, monitoring, escalation path
      - direct (team member): documentation, reproducible process
      - boss (manager): executive summary, risk register
    """
    findings: list[dict[str, Any]] = []

    if ctype == "customer":
        # External customers expect high availability and explicit SLA commitment
        spofs = design_context.get("single_points_of_failure", [])
        if spofs:
            findings.append({
                "customer": cname,
                "customer_type": "External Customer",
                "finding": f"SPOFs detected: {', '.join(str(s) for s in spofs[:3])}.",
                "severity": "high",
                "recommendation": (
                    f"{cname} is an external customer — eliminate SPOFs or "
                    f"document failover procedures before delivery."
                ),
                "dimension": "reliability",
            })
        avail = str(design_context.get("availability_target", ""))
        if avail and "99.99" not in avail and "99.999" not in avail:
            findings.append({
                "customer": cname,
                "customer_type": "External Customer",
                "finding": f"Availability target {avail}% may fall short of external customer expectations.",
                "severity": "medium",
                "recommendation": (
                    f"Confirm SLA requirements with {cname}. External customers typically "
                    f"expect 99.99% or better."
                ),
                "dimension": "reliability",
            })

    elif ctype == "stakeholder":
        # Internal stakeholders need operational handoff clarity
        if not design_context.get("runbook_referenced"):
            findings.append({
                "customer": cname,
                "customer_type": "Internal Stakeholder",
                "finding": "No runbook or operational doc referenced.",
                "severity": "medium",
                "recommendation": (
                    f"{cname} will operate or rely on this work day-to-day. "
                    f"Add runbooks, monitoring thresholds, and escalation paths."
                ),
                "dimension": "operational_handoff",
            })

    elif ctype == "boss":
        # Managers expect risk visibility
        if not design_context.get("risks_documented"):
            findings.append({
                "customer": cname,
                "customer_type": "Manager",
                "finding": "No risk register or risk summary included.",
                "severity": "low",
                "recommendation": (
                    f"Add a brief risk section for {cname}. Managers need to "
                    f"understand top risks before approving."
                ),
                "dimension": "risk_register",
            })

    elif ctype == "direct":
        # Team members need reproducible, documented work
        if not design_context.get("methodology_documented"):
            findings.append({
                "customer": cname,
                "customer_type": "Direct Report / Team",
                "finding": "Methodology or process not documented.",
                "severity": "low",
                "recommendation": (
                    f"{cname} may need to reproduce or extend this work. "
                    f"Document the approach so the process can be handed off."
                ),
                "dimension": "documentation",
            })

    return findings


# ─────────────────────────────────────────────────────────────────────────────
# 8. Domain standards watch (used by weekly digest)
# ─────────────────────────────────────────────────────────────────────────────

def generate_standards_watch(user_id: str, tenant_id: str = "default") -> list[dict[str, Any]]:
    """Pull standards/regulatory updates from existing DB sources filtered by role topics.

    Draws from research_findings, cve_entries, and memory_entries already in the
    platform — no external HTTP calls.
    """
    from tools.second_brain.role_advisor import get_digest_topics
    topics = get_digest_topics(user_id, tenant_id)[:6]
    if not topics:
        return []

    items: list[dict] = []

    # 1. Research canvas findings matching role topics
    for r in _fetch_research_items(topics[:5]):
        items.append({
            "title": (r.get("title") or "")[:120],
            "source": "Research",
            "severity": "",
            "date": str(r.get("date", ""))[:10],
            "url": r.get("url", ""),
        })

    # 2. CVEs mentioning role topics
    for c in _fetch_relevant_cves(topics[:3]):
        items.append({
            "title": f"{c.get('id', 'CVE')}: {(c.get('description') or '')[:80]}",
            "source": "CVE",
            "severity": c.get("severity", ""),
            "date": str(c.get("published_at", ""))[:10],
            "url": "",
        })

    # 3. Semantic memory entries tagged with role topics (last 7 days)
    try:
        from tools.db.storage import get_connection
        from datetime import timedelta
        since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        topic_filter = " OR ".join([f"content ILIKE %s"] * len(topics))
        params = [f"%{t}%" for t in topics] + [since]
        with get_connection() as conn:
            rows = conn.execute(
                f"SELECT content, created_at FROM memory_entries "
                f"WHERE memory_type='semantic' AND ({topic_filter}) AND created_at > %s "
                f"ORDER BY created_at DESC LIMIT 5",
                params,
            ).fetchall()
        for r in rows:
            items.append({
                "title": (r[0] or "")[:120],
                "source": "Knowledge",
                "severity": "",
                "date": str(r[1] or "")[:10],
                "url": "",
            })
    except Exception:
        pass

    # Deduplicate by title prefix and sort newest-first
    seen: set[str] = set()
    unique: list[dict] = []
    for item in items:
        key = (item.get("title") or "")[:50].lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(item)

    unique.sort(key=lambda x: x.get("date", ""), reverse=True)
    return unique[:8]


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


# ─────────────────────────────────────────────────────────────────────────────
# 5. Context-aware challenge mitigations
# ─────────────────────────────────────────────────────────────────────────────

def generate_challenge_mitigations(
    user_id: str,
    tenant_id: str = "default",
) -> list[dict[str, Any]]:
    """Generate specific, context-grounded mitigations for each active challenge.

    Uses the user's actual objectives, open tasks, and customers — not generic advice.
    Returns list of {challenge_key, description, mitigations (bullet string)}.
    """
    try:
        from tools.second_brain.profile import get_challenges, get_objectives
        from tools.second_brain.role_advisor import infer_persona, _get_user_title
    except ImportError as exc:
        logger.warning("[proactive_advisor] generate_challenge_mitigations import failed: %s", exc)
        return []

    challenges = [
        c for c in (get_challenges(user_id, tenant_id) or [])
        if c.get("status") == "active"
    ]
    if not challenges:
        return []

    objectives = get_objectives(user_id, tenant_id) or []
    open_tasks = _fetch_open_tasks(user_id)
    customers = _load_customers(user_id, tenant_id)
    title = _get_user_title(user_id, tenant_id)
    persona = infer_persona(title)
    persona_label = persona.get("display_name", "professional")

    mitigations: list[dict[str, Any]] = []
    for ch in challenges[:5]:
        key = ch.get("challenge_key", "")
        desc = ch.get("custom_description") or key.replace("_", " ").title()

        obj_titles = [o.get("title", "") for o in objectives[:3]]
        priority_tasks = [
            t.get("title", "") for t in open_tasks
            if t.get("priority") in ("high", "critical")
        ][:3]
        customer_names = [c.get("name", "") for c in customers[:3]]

        prompt = (
            f"You are an AI assistant for a {persona_label}.\n"
            f"Their active challenge: '{desc}'.\n"
            f"Current objectives: {obj_titles}.\n"
            f"High-priority open tasks: {priority_tasks}.\n"
            f"Key customers/stakeholders: {customer_names}.\n"
            f"Generate exactly 3 specific, actionable mitigations for this challenge. "
            f"Each must reference the actual situation above — no generic advice. "
            f"Format: one bullet per line starting with '•'. No intro or closing text."
        )
        try:
            from tools.llm.router import LLMRouter, LLMRequest
            resp = LLMRouter().invoke(
                "summarization",
                LLMRequest(prompt=prompt, max_tokens=220, temperature=0.4),
            )
            bullets = (resp.content or "").strip()
        except Exception as exc:
            logger.debug("[proactive_advisor] LLM mitigate failed for %s: %s", key, exc)
            bullets = (
                "• Block 30 minutes this week to identify the root cause.\n"
                "• Discuss with a trusted peer or stakeholder.\n"
                "• Review this challenge in your next weekly reflection."
            )

        mitigations.append({
            "challenge_key": key,
            "description": desc,
            "mitigations": bullets,
        })

    return mitigations


# ─────────────────────────────────────────────────────────────────────────────
# 6. Meeting prep cards (callable by meeting_prep_reflex + manual endpoint)
# ─────────────────────────────────────────────────────────────────────────────

def generate_meeting_preps(
    user_id: str,
    tenant_id: str = "default",
) -> list[dict[str, Any]]:
    """For each calendar event in the next 90 minutes with a known customer attendee,
    generate a 3-bullet prep card and store it in today's briefing."""
    from datetime import date, timedelta as td

    customers = _load_customers(user_id, tenant_id)
    customer_names = {c.get("name", "").lower(): c for c in customers}
    if not customer_names:
        return []

    today = date.today().isoformat()
    calendar_items = _fetch_calendar_items(user_id, today)

    now_utc = datetime.now(timezone.utc)
    upcoming = []
    for item in calendar_items:
        start_raw = item.get("start") or item.get("date") or ""
        try:
            start_dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        delta = (start_dt - now_utc).total_seconds() / 60
        if 0 <= delta <= 90:
            upcoming.append(item)

    cards = []
    for event in upcoming[:5]:
        title = event.get("title", event.get("summary", "Meeting"))
        attendees = event.get("attendees", [])
        matched = next(
            (customer_names[a.lower()] for a in attendees if a.lower() in customer_names),
            None,
        )
        if not matched:
            # Try partial name match
            matched = next(
                (c for name, c in customer_names.items()
                 if name in title.lower() or title.lower() in name),
                None,
            )
        if not matched:
            continue

        cname = matched.get("name", "")
        ctype = matched.get("relationship_type", "")
        notes = matched.get("notes", "")
        expectations = matched.get("expectations_json") or "{}"
        if isinstance(expectations, str):
            try:
                expectations = json.loads(expectations)
            except Exception:
                expectations = {}

        prompt = (
            f"You are preparing someone for a meeting with {cname} ({ctype}) titled '{title}'.\n"
            f"Customer notes: {notes[:200] if notes else 'None recorded'}.\n"
            f"Key expectations: {json.dumps(expectations)[:200]}.\n"
            f"Write exactly 3 bullet points (starting with •) to help the person walk in prepared:\n"
            f"1. What to lead with (context or update {cname} needs)\n"
            f"2. A question to ask to uncover concerns or needs\n"
            f"3. One commitment or action to offer that would add value.\n"
            f"Be specific to this customer. No intro or closing."
        )
        try:
            from tools.llm.router import LLMRouter, LLMRequest
            resp = LLMRouter().invoke(
                "summarization",
                LLMRequest(prompt=prompt, max_tokens=180, temperature=0.35),
            )
            bullets = (resp.content or "").strip()
        except Exception as exc:
            logger.debug("[proactive_advisor] meeting prep LLM failed: %s", exc)
            bullets = (
                f"• Confirm {cname}'s current priorities before diving in.\n"
                f"• Ask what would make this meeting most valuable for them.\n"
                f"• Offer one clear next step you'll own before the next sync."
            )

        card = {
            "event_title": title,
            "customer": cname,
            "customer_type": ctype,
            "start": event.get("start", ""),
            "bullets": bullets,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        cards.append(card)

    if cards:
        _upsert_prep_section(user_id, tenant_id, today, {"meeting_preps": cards})
        logger.info("[proactive_advisor] %d meeting prep cards generated for %s", len(cards), user_id)

    return cards


# ─────────────────────────────────────────────────────────────────────────────
# 7. Commitment date watch (callable by commitment_watch_reflex + manual endpoint)
# ─────────────────────────────────────────────────────────────────────────────

def generate_commitment_alerts(
    user_id: str,
    tenant_id: str = "default",
) -> list[dict[str, Any]]:
    """Scan all customer relationships for upcoming or overdue commitment dates.

    Returns list of alert dicts bucketed by urgency:
      overdue / critical (≤7d) / high (≤30d) / medium (≤90d)
    Also upserts into today's briefing under 'commitment_alerts'.
    """
    import json as _json
    from datetime import date

    customers = _load_customers(user_id, tenant_id)
    today = date.today()
    alerts: list[dict[str, Any]] = []

    for c in customers:
        cname = c.get("name", "")
        ctype = c.get("relationship_type", "")

        # Commitment date can live in the top-level column or inside expectations_json
        cdate_raw = c.get("commitment_date")
        if not cdate_raw:
            exp = c.get("expectations_json") or "{}"
            if isinstance(exp, str):
                try:
                    exp = _json.loads(exp)
                except Exception:
                    exp = {}
            cdate_raw = exp.get("commitment_date")

        if not cdate_raw:
            continue

        try:
            cdate = date.fromisoformat(str(cdate_raw)[:10])
        except (ValueError, TypeError):
            continue

        delta = (cdate - today).days

        if delta < 0:
            urgency = "overdue"
            label = f"Overdue by {-delta} day(s)"
        elif delta <= 7:
            urgency = "critical"
            label = f"Due in {delta} day(s)"
        elif delta <= 30:
            urgency = "high"
            label = f"Due in {delta} day(s)"
        elif delta <= 90:
            urgency = "medium"
            label = f"Due in {delta} day(s)"
        else:
            continue

        alerts.append({
            "customer": cname,
            "customer_type": ctype,
            "commitment_date": str(cdate),
            "urgency": urgency,
            "label": label,
            "delta_days": delta,
        })

    alerts.sort(key=lambda a: a["delta_days"])

    if alerts:
        from datetime import date as _date
        today_str = _date.today().isoformat()
        _upsert_prep_section(user_id, tenant_id, today_str, {"commitment_alerts": alerts})
        logger.info("[proactive_advisor] %d commitment alerts generated for %s", len(alerts), user_id)

    return alerts
