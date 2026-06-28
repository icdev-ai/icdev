# CUI // SP-CTI
"""Second Brain — daily briefing generator and multi-channel delivery engine."""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from typing import Any

from tools.db.storage import get_canvas_connection, sql_placeholder
from tools.logging.icdev_logger import get_logger
from tools.second_brain.constants import BRIEFING_ENV_FLAG
from tools.second_brain.profile import build_world_model_context, get_challenges, get_objectives

logger = get_logger(__name__)
_ENV_FLAG = BRIEFING_ENV_FLAG


def _conn():
    return get_canvas_connection(_ENV_FLAG)


# ─────────────────────────────────────────────────────────────────────────────
# Briefing generation
# ─────────────────────────────────────────────────────────────────────────────


def generate_briefing(
    user_id: str,
    briefing_date: str | None = None,
    tenant_id: str = "default",
) -> dict[str, Any]:
    """Assemble context from all sources, call LLM, store and return structured briefing.

    *briefing_date* is YYYY-MM-DD (defaults to today).
    """
    if briefing_date is None:
        briefing_date = date.today().isoformat()

    ctx = build_world_model_context(user_id, tenant_id)
    if not ctx:
        logger.info("[briefing] user %s has no context — skipping", user_id)
        return {}

    # Gather items from all connected integrations
    calendar_items: list[dict] = []
    task_items: list[dict] = []
    mention_items: list[dict] = []

    try:
        from tools.second_brain.connectors.google import GoogleConnector
        calendar_items = GoogleConnector().get_todays_items(user_id)
    except Exception as exc:
        logger.debug("[briefing] google calendar unavailable: %s", exc)

    for svc in ("github", "jira"):
        try:
            if svc == "github":
                from tools.second_brain.connectors.github import GitHubConnector
                task_items.extend(GitHubConnector().get_todays_items(user_id))
            else:
                from tools.second_brain.connectors.jira import JiraConnector
                task_items.extend(JiraConnector().get_todays_items(user_id))
        except Exception as exc:
            logger.debug("[briefing] %s unavailable: %s", svc, exc)

    try:
        from tools.second_brain.connectors.slack import SlackConnector
        mention_items = SlackConnector().get_todays_items(user_id)
    except Exception as exc:
        logger.debug("[briefing] slack unavailable: %s", exc)

    objectives = get_objectives(user_id, tenant_id)
    challenges = get_challenges(user_id, tenant_id)

    # Build the briefing content via LLM (with template fallback)
    content = _build_content(
        ctx=ctx,
        briefing_date=briefing_date,
        calendar_items=calendar_items,
        task_items=task_items,
        mention_items=mention_items,
        objectives=objectives,
        challenges=challenges,
        user_id=user_id,
        tenant_id=tenant_id,
    )

    # Store
    brid = f"brf-{uuid.uuid4().hex[:12]}"
    with _conn() as conn:
        ph = sql_placeholder(conn)
        conn.execute(
            f"""
            INSERT INTO user_daily_briefings (id, user_id, tenant_id, briefing_date, content_json, delivery_channels)
            VALUES ({ph},{ph},{ph},{ph},{ph},{ph})
            ON CONFLICT(user_id, tenant_id, briefing_date) DO UPDATE SET
                content_json = excluded.content_json
            """,
            (brid, user_id, tenant_id, briefing_date,
             json.dumps(content, default=str),
             json.dumps(ctx.get("delivery_channels", ["dashboard"]))),
        )
        conn.commit()

    logger.info("[briefing] generated %s for %s on %s", brid, user_id, briefing_date)
    return content


def _build_content(
    ctx: dict,
    briefing_date: str,
    calendar_items: list[dict],
    task_items: list[dict],
    mention_items: list[dict],
    objectives: list[dict],
    challenges: list[dict],
    user_id: str = "default",
    tenant_id: str = "default",
) -> dict[str, Any]:
    """Build structured briefing dict — LLM-enhanced, template fallback."""
    name = ctx.get("name", "")
    top_objs = [o["title"] for o in objectives[:3]]
    top_chals = [c.get("challenge_key") or c.get("custom_description", "") for c in challenges[:3]]

    # LLM greeting + focus paragraph
    greeting = f"Good morning, {name}. Here's what matters on {briefing_date}."
    focus = f"Your top priorities: {'; '.join(top_objs) or 'check your task list'}."

    try:
        prompt = (
            f"Write a 1-sentence morning greeting and a 1-sentence focus statement for {name}, "
            f"a {ctx.get('title')} at {ctx.get('org_name')}. "
            f"Their top goals: {'; '.join(top_objs) or 'not yet defined'}. "
            f"Key challenges: {'; '.join(top_chals) or 'not yet defined'}. "
            f"Date: {briefing_date}. Be warm, professional, specific."
        )
        from tools.llm.router import LLMRouter
        result = LLMRouter().invoke("summarization", {"prompt": prompt, "max_tokens": 120})
        llm_text = (result or {}).get("content") or (result or {}).get("text") or ""
        if llm_text:
            lines = [l.strip() for l in llm_text.strip().splitlines() if l.strip()]
            if lines:
                greeting = lines[0]
            if len(lines) > 1:
                focus = " ".join(lines[1:])
    except Exception:
        pass

    # Meeting prep notes (lightweight LLM per meeting with fallback)
    meetings = []
    for ev in calendar_items[:8]:
        attendee_names = ", ".join(ev.get("attendees", [])[:4])
        prep = ""
        try:
            prep_prompt = (
                f"Write a 1-sentence prep note for {name} attending '{ev.get('title')}' "
                f"with {attendee_names or 'no external attendees'}. Be concise."
            )
            from tools.llm.router import LLMRouter
            r = LLMRouter().invoke("summarization", {"prompt": prep_prompt, "max_tokens": 60})
            prep = (r or {}).get("content") or (r or {}).get("text") or ""
        except Exception:
            pass
        meetings.append({**ev, "prep_notes": prep.strip()})

    # Relationship nudges — scored by days-since-last-interaction
    relationships = []
    relationship_nudges: list[dict] = []
    try:
        from tools.second_brain.relationship_health import (
            generate_relationship_nudges,
            get_relationship_health_map,
        )
        health_map = get_relationship_health_map(user_id, tenant_id)
        for rel in health_map[:3]:
            relationships.append({
                "name": rel.get("name", ""),
                "relationship_type": rel.get("relationship_type", "peer"),
                "nudge": rel.get("health", {}).get("nudge") or f"Check in with {rel.get('name','')} today.",
            })
        relationship_nudges = generate_relationship_nudges(user_id, tenant_id)
    except Exception as exc:
        logger.debug("[briefing] relationship health failed: %s", exc)

    # Challenge spotlight
    challenges_spotlight = None
    if challenges:
        top = challenges[0]
        key = top.get("challenge_key") or top.get("custom_description", "")
        challenges_spotlight = {
            "key": key,
            "suggestion": f"Spotlight: {key.replace('_',' ').title()}. Look for one small action today to reduce this friction.",
        }

    return {
        "date": briefing_date,
        "greeting": greeting,
        "focus": focus,
        "meetings": meetings,
        "tasks": task_items[:10],
        "mentions": mention_items[:5],
        "relationships": relationships,
        "relationship_nudges": relationship_nudges,
        "challenges_spotlight": challenges_spotlight,
        "objectives_reminder": top_objs,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Retrieval
# ─────────────────────────────────────────────────────────────────────────────


def get_todays_briefing(user_id: str, tenant_id: str = "default") -> dict | None:
    """Return today's stored briefing, or None if not yet generated."""
    today = date.today().isoformat()
    with _conn() as conn:
        ph = sql_placeholder(conn)
        row = conn.execute(
            f"SELECT content_json, created_at FROM user_daily_briefings WHERE user_id={ph} AND tenant_id={ph} AND briefing_date={ph}",
            (user_id, tenant_id, today),
        ).fetchone()
    if row is None:
        return None
    if isinstance(row, dict):
        return json.loads(row.get("content_json") or "{}")
    return json.loads(row[0] or "{}")


def mark_briefing_opened(user_id: str, tenant_id: str = "default") -> None:
    today = date.today().isoformat()
    with _conn() as conn:
        ph = sql_placeholder(conn)
        conn.execute(
            f"UPDATE user_daily_briefings SET opened_at={ph} WHERE user_id={ph} AND tenant_id={ph} AND briefing_date={ph} AND opened_at IS NULL",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), user_id, tenant_id, today),
        )
        conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Delivery
# ─────────────────────────────────────────────────────────────────────────────


def deliver_briefing(user_id: str, briefing_date: str | None = None, tenant_id: str = "default") -> dict:
    """Fan-out briefing delivery to all configured channels. Returns delivery status dict."""
    if briefing_date is None:
        briefing_date = date.today().isoformat()

    ctx = build_world_model_context(user_id, tenant_id)
    if not ctx:
        return {"error": "no_profile"}

    channels = ctx.get("delivery_channels", ["dashboard"])
    status: dict[str, Any] = {}

    # Dashboard: always stored — mark as delivered
    status["dashboard"] = "stored"

    if "email" in channels:
        status["email"] = _deliver_email(user_id, briefing_date, ctx, tenant_id)

    if "slack" in channels:
        status["slack"] = _deliver_slack(user_id, briefing_date, ctx, tenant_id)

    if "calendar" in channels:
        status["calendar"] = _deliver_calendar(user_id, briefing_date, ctx, tenant_id)

    # M365 email (Outlook) — attempt first; SMTP is fallback
    if "outlook" in channels or "email" in channels:
        m365_ok = _deliver_m365_email(user_id, briefing_date, ctx, tenant_id)
        status["outlook"] = m365_ok
        # If M365 not configured but SMTP is, fall back
        if not m365_ok and "email" in channels and status.get("email") is None:
            status["email"] = _deliver_email(user_id, briefing_date, ctx, tenant_id)

    if "teams" in channels:
        status["teams"] = _deliver_teams(user_id, briefing_date, ctx, tenant_id)

    # Persist delivery status
    with _conn() as conn:
        ph = sql_placeholder(conn)
        conn.execute(
            f"UPDATE user_daily_briefings SET delivery_status_json={ph} WHERE user_id={ph} AND tenant_id={ph} AND briefing_date={ph}",
            (json.dumps(status), user_id, tenant_id, briefing_date),
        )
        conn.commit()

    return status


def _deliver_email(user_id: str, briefing_date: str, ctx: dict, tenant_id: str) -> str:
    try:
        import smtplib
        from email.mime.text import MIMEText
        smtp_host = os.environ.get("SMTP_HOST", "")
        smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        smtp_user = os.environ.get("SMTP_USER", "")
        smtp_pass = os.environ.get("SMTP_PASS", "")
        from_addr = os.environ.get("SMTP_FROM", smtp_user)
        to_addr = ctx.get("work_email") or ""
        if not all([smtp_host, smtp_user, to_addr]):
            return "skipped_no_config"
        briefing = get_todays_briefing(user_id, tenant_id) or {}
        body = _format_email_html(briefing, ctx)
        msg = MIMEText(body, "html", "utf-8")
        msg["Subject"] = f"Your AI Briefing — {briefing_date}"
        msg["From"] = from_addr
        msg["To"] = to_addr
        with smtplib.SMTP(smtp_host, smtp_port) as s:
            s.starttls()
            if smtp_user:
                s.login(smtp_user, smtp_pass)
            s.send_message(msg)
        return "sent"
    except Exception as exc:
        logger.warning("[briefing] email delivery failed: %s", exc)
        return f"error: {exc}"


def _deliver_slack(user_id: str, briefing_date: str, ctx: dict, tenant_id: str) -> str:
    try:
        from tools.second_brain.integrations import get_integration_metadata
        meta = get_integration_metadata(user_id, "slack", tenant_id)
        slack_user_id = meta.get("slack_user_id", "")
        if not slack_user_id:
            return "skipped_no_slack_user_id"
        briefing = get_todays_briefing(user_id, tenant_id) or {}
        text = f"*{briefing.get('greeting','')}*\n{briefing.get('focus','')}\n📅 {len(briefing.get('meetings',[]))} meetings | ✅ {len(briefing.get('tasks',[]))} tasks"
        from tools.second_brain.connectors.slack import SlackConnector
        ok = SlackConnector().post_dm(user_id, slack_user_id, text)
        return "sent" if ok else "failed"
    except Exception as exc:
        logger.warning("[briefing] slack delivery failed: %s", exc)
        return f"error: {exc}"


def _deliver_calendar(user_id: str, briefing_date: str, ctx: dict, tenant_id: str) -> str:
    # Calendar injection requires Google OAuth — best-effort
    try:
        from tools.second_brain.integrations import get_decrypted_token
        token = get_decrypted_token(user_id, "gcal", tenant_id)
        if not token:
            return "skipped_no_token"
        briefing = get_todays_briefing(user_id, tenant_id) or {}
        description = f"{briefing.get('greeting','')}\n\n{briefing.get('focus','')}"
        if briefing.get("tasks"):
            description += "\n\nTop tasks:\n" + "\n".join(f"• {t.get('title','')}" for t in briefing["tasks"][:5])
        import urllib.request
        body = json.dumps({
            "summary": f"🤖 AI Briefing — {briefing_date}",
            "start": {"date": briefing_date},
            "end": {"date": briefing_date},
            "description": description,
        }).encode()
        req = urllib.request.Request(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            data=body, method="POST",
        )
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return "inserted" if resp.status in (200, 201) else f"http_{resp.status}"
    except Exception as exc:
        logger.warning("[briefing] calendar delivery failed: %s", exc)
        return f"error: {exc}"


def _get_m365_token(user_id: str, tenant_id: str) -> str:
    """Load and auto-refresh the msgraph access token. Returns empty string if not configured."""
    try:
        from tools.db.storage import get_connection, sql_placeholder
        from datetime import datetime, timezone
        with get_connection() as conn:
            ph = sql_placeholder(conn)
            row = conn.execute(
                f"SELECT access_token_enc, refresh_token_enc, token_expiry FROM user_integrations "
                f"WHERE user_id={ph} AND tenant_id={ph} AND service='msgraph' AND status='active'",
                (user_id, tenant_id),
            ).fetchone()
        if not row:
            return ""
        if isinstance(row, dict):
            access_tok, refresh_tok, expiry = row.get("access_token_enc", ""), row.get("refresh_token_enc", ""), row.get("token_expiry", "")
        else:
            access_tok, refresh_tok, expiry = row[0], row[1], row[2]
        if expiry:
            try:
                exp_dt = datetime.fromisoformat(str(expiry).replace("Z", "+00:00"))
                if datetime.now(timezone.utc) >= exp_dt and refresh_tok:
                    from tools.second_brain.connectors.microsoft import refresh_token as ms_refresh
                    new_tokens = ms_refresh(refresh_tok)
                    if new_tokens.get("access_token"):
                        access_tok = new_tokens["access_token"]
                        _update_m365_token(user_id, tenant_id, new_tokens)
            except Exception:
                pass
        return access_tok or ""
    except Exception:
        return ""


def _update_m365_token(user_id: str, tenant_id: str, tokens: dict) -> None:
    try:
        from tools.db.storage import get_connection, sql_placeholder
        with get_connection() as conn:
            ph = sql_placeholder(conn)
            conn.execute(
                f"UPDATE user_integrations SET access_token_enc={ph}, token_expiry={ph} "
                f"WHERE user_id={ph} AND tenant_id={ph} AND service='msgraph'",
                (tokens.get("access_token", ""), tokens.get("expires_in", ""), user_id, tenant_id),
            )
            conn.commit()
    except Exception:
        pass


def _deliver_m365_email(user_id: str, briefing_date: str, ctx: dict, tenant_id: str) -> str:
    """Deliver briefing via Outlook using Microsoft Graph API."""
    token = _get_m365_token(user_id, tenant_id)
    if not token:
        return "skipped_no_m365_token"
    to_addr = ctx.get("work_email") or ""
    if not to_addr:
        return "skipped_no_email"
    try:
        briefing = get_todays_briefing(user_id, tenant_id) or {}
        html_body = _format_email_html(briefing, ctx)
        from tools.second_brain.connectors.microsoft import send_mail
        ok = send_mail(token, to_addr, f"🧠 AI Briefing — {briefing_date}", html_body)
        return "sent" if ok else "failed"
    except Exception as exc:
        logger.warning("[briefing] M365 email delivery failed: %s", exc)
        return f"error: {exc}"


def _deliver_teams(user_id: str, briefing_date: str, ctx: dict, tenant_id: str) -> str:
    """Deliver briefing summary to Microsoft Teams via Graph API."""
    token = _get_m365_token(user_id, tenant_id)
    if not token:
        return "skipped_no_m365_token"
    try:
        from tools.db.storage import get_connection, sql_placeholder
        with get_connection() as conn:
            ph = sql_placeholder(conn)
            row = conn.execute(
                f"SELECT metadata_json FROM user_integrations "
                f"WHERE user_id={ph} AND tenant_id={ph} AND service='msgraph' AND status='active'",
                (user_id, tenant_id),
            ).fetchone()
        if not row:
            return "skipped_no_integration"
        meta = json.loads((row[0] if not isinstance(row, dict) else row.get("metadata_json", "")) or "{}")
        chat_id = meta.get("teams_chat_id", "")
        if not chat_id:
            return "skipped_no_teams_chat_id"
        briefing = get_todays_briefing(user_id, tenant_id) or {}
        lines = [
            f"🧠 *AI Briefing — {briefing_date}*",
            "",
            briefing.get("greeting", ""),
            briefing.get("focus", ""),
            "",
            f"📅 {len(briefing.get('meetings', []))} meetings  ✅ {len(briefing.get('tasks', []))} tasks",
            "View full briefing: http://localhost:5050/me/briefing/today",
        ]
        from tools.second_brain.connectors.microsoft import send_teams_message
        ok = send_teams_message(token, chat_id, "\n".join(lines))
        return "sent" if ok else "failed"
    except Exception as exc:
        logger.warning("[briefing] Teams delivery failed: %s", exc)
        return f"error: {exc}"


def _format_email_html(briefing: dict, ctx: dict) -> str:
    name = ctx.get("name", "")
    rows = [f"<h2>{briefing.get('greeting','Good morning!')}</h2>",
            f"<p>{briefing.get('focus','')}</p>"]
    if briefing.get("meetings"):
        rows.append("<h3>📅 Today's Meetings</h3><ul>")
        for m in briefing["meetings"][:5]:
            rows.append(f"<li><strong>{m.get('time','')}</strong> — {m.get('title','')} <em>{m.get('prep_notes','')}</em></li>")
        rows.append("</ul>")
    if briefing.get("tasks"):
        rows.append("<h3>✅ Top Tasks</h3><ul>")
        for t in briefing["tasks"][:5]:
            rows.append(f"<li>[{t.get('source','').upper()}] {t.get('title','')}</li>")
        rows.append("</ul>")
    rows.append("<hr><p style='color:#888;font-size:11px'>Sent by your ICDev AI Assistant</p>")
    return "\n".join(rows)


# ─────────────────────────────────────────────────────────────────────────────
# Users to brief (for the genesis reflex)
# ─────────────────────────────────────────────────────────────────────────────


def get_users_due_for_briefing(current_hour_utc: int) -> list[dict]:
    """Return profiles whose briefing_time (HH:MM) falls in *current_hour_utc*."""
    try:
        with _conn() as conn:
            rows = conn.execute(
                "SELECT user_id, tenant_id, briefing_time FROM user_identity_profiles WHERE context_complete=1"
            ).fetchall()
        due = []
        for row in rows:
            if isinstance(row, dict):
                uid, tid, bt = row["user_id"], row["tenant_id"], row.get("briefing_time", "08:00")
            else:
                uid, tid, bt = row[0], row[1], row[2] or "08:00"
            try:
                bh = int((bt or "08:00").split(":")[0])
            except Exception:
                bh = 8
            if bh == current_hour_utc:
                due.append({"user_id": uid, "tenant_id": tid, "briefing_time": bt})
        return due
    except Exception as exc:
        logger.warning("[briefing] get_users_due_for_briefing failed: %s", exc)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────

import os

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Second Brain daily briefing")
    parser.add_argument("--user-id", default="default")
    parser.add_argument("--tenant-id", default="default")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (default today)")
    parser.add_argument("--deliver", action="store_true", help="Deliver to configured channels")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    content = generate_briefing(args.user_id, args.date, args.tenant_id)
    if args.deliver:
        status = deliver_briefing(args.user_id, args.date, args.tenant_id)
        if args.json:
            print(json.dumps({"briefing": content, "delivery": status}, indent=2, default=str))
        else:
            print(f"Delivered: {status}")
    else:
        if args.json:
            print(json.dumps(content, indent=2, default=str))
        else:
            print(content.get("greeting", ""))
            print(content.get("focus", ""))
            print(f"\n{len(content.get('meetings',[]))} meetings | {len(content.get('tasks',[]))} tasks")
