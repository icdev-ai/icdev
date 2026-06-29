# CUI // SP-CTI
"""Weekly retrospective generator — what I shipped this week."""
from __future__ import annotations
import subprocess
from datetime import datetime, timezone, timedelta
from typing import Any
from tools.logging.icdev_logger import get_logger
logger = get_logger(__name__)


def get_all_users() -> list[tuple[str, str]]:
    """Return (user_id, tenant_id) pairs with context_complete=1."""
    try:
        from tools.second_brain.constants import BRIEFING_ENV_FLAG
        from tools.db.storage import get_canvas_connection, sql_placeholder
        with get_canvas_connection(BRIEFING_ENV_FLAG) as conn:
            rows = conn.execute(
                "SELECT user_id, tenant_id FROM user_identity_profiles WHERE context_complete=1"
            ).fetchall()
        return [(r[0], r[1]) for r in rows]
    except Exception:
        return [("default", "default")]


def generate_weekly_retro(user_id: str, tenant_id: str = "default") -> dict[str, Any] | None:
    """Assemble and store a weekly retrospective for the user."""
    since = datetime.now(timezone.utc) - timedelta(days=7)
    since_str = since.strftime("%Y-%m-%d")

    retro: dict[str, Any] = {
        "week_ending": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "done_tasks": _get_done_tasks(since_str),
        "commits": _get_git_commits(since),
        "interactions": _get_interactions_this_week(user_id, tenant_id, since_str),
        "objective_progress": _get_objective_progress(user_id, tenant_id),
    }
    retro["summary"] = _build_summary(retro)
    _store_retro(user_id, tenant_id, retro)
    return retro


def _get_done_tasks(since: str) -> list[dict]:
    try:
        from tools.db.storage import get_connection, sql_placeholder
        with get_connection() as conn:
            ph = sql_placeholder(conn)
            rows = conn.execute(
                f"SELECT title, updated_at FROM kanban_tasks "
                f"WHERE status='done' AND updated_at >= {ph} "
                f"ORDER BY updated_at DESC LIMIT 20",
                (since,),
            ).fetchall()
        return [{"title": r[0], "date": str(r[1])[:10]} for r in rows]
    except Exception:
        return []


def _get_git_commits(since: datetime) -> list[str]:
    try:
        since_str = since.strftime("%Y-%m-%d")
        result = subprocess.run(
            ["git", "log", f"--since={since_str}", "--no-merges", "--format=%s"],
            capture_output=True, text=True, timeout=10,
            cwd="C:/AI/ICDev",
        )
        lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
        lines = [l for l in lines if not l.startswith("chore: auto-commit")]
        return lines[:15]
    except Exception:
        return []


def _get_interactions_this_week(user_id: str, tenant_id: str, since: str) -> list[dict]:
    try:
        from tools.second_brain.constants import BRIEFING_ENV_FLAG
        from tools.db.storage import get_canvas_connection, sql_placeholder
        with get_canvas_connection(BRIEFING_ENV_FLAG) as conn:
            ph = sql_placeholder(conn)
            rows = conn.execute(
                f"SELECT i.title, i.interaction_date, r.name "
                f"FROM user_relationship_interactions i "
                f"JOIN user_relationships r ON r.id = i.relationship_id "
                f"WHERE i.user_id={ph} AND i.tenant_id={ph} AND i.interaction_date >= {ph} "
                f"ORDER BY i.interaction_date DESC LIMIT 10",
                (user_id, tenant_id, since),
            ).fetchall()
        return [{"title": r[0], "date": r[1], "contact": r[2]} for r in rows]
    except Exception:
        return []


def _get_objective_progress(user_id: str, tenant_id: str) -> list[dict]:
    try:
        from tools.second_brain.constants import BRIEFING_ENV_FLAG
        from tools.db.storage import get_canvas_connection, sql_placeholder
        with get_canvas_connection(BRIEFING_ENV_FLAG) as conn:
            ph = sql_placeholder(conn)
            rows = conn.execute(
                f"SELECT title, auto_progress_pct, horizon FROM user_objectives "
                f"WHERE user_id={ph} AND tenant_id={ph} AND status='active' AND auto_progress_pct > 0 "
                f"ORDER BY auto_progress_pct DESC LIMIT 5",
                (user_id, tenant_id),
            ).fetchall()
        return [{"title": r[0], "pct": r[1], "horizon": r[2]} for r in rows]
    except Exception:
        return []


def _build_summary(retro: dict) -> str:
    tasks = retro.get("done_tasks", [])
    commits = retro.get("commits", [])
    interactions = retro.get("interactions", [])
    objs = retro.get("objective_progress", [])

    lines = [f"Week ending {retro['week_ending']}:"]
    if tasks:
        titles = ", ".join(t["title"] for t in tasks[:3])
        lines.append(f"Completed {len(tasks)} task(s): {titles}{'...' if len(tasks) > 3 else ''}.")
    if commits:
        lines.append(f"{len(commits)} commit(s) shipped.")
    if interactions:
        contacts = list({i["contact"] for i in interactions})
        lines.append(f"Engaged with {len(contacts)} contact(s): {', '.join(contacts[:3])}.")
    if objs:
        top = objs[0]
        lines.append(f"Top objective '{top['title'][:40]}' at {top['pct']}%.")
    if len(lines) == 1:
        lines.append("A quiet week — no tasks, commits, or interactions logged.")
    return " ".join(lines)


def _store_retro(user_id: str, tenant_id: str, retro: dict) -> None:
    import json
    import uuid
    from tools.second_brain.constants import BRIEFING_ENV_FLAG
    from tools.db.storage import get_canvas_connection, sql_placeholder
    try:
        with get_canvas_connection(BRIEFING_ENV_FLAG) as conn:
            ph = sql_placeholder(conn)
            bdate = f"{retro['week_ending']}-retro"
            bid = str(uuid.uuid4())
            content = json.dumps({"type": "weekly_retro", **retro})
            try:
                conn.execute(
                    f"INSERT INTO user_daily_briefings (id,user_id,tenant_id,briefing_date,content_json) "
                    f"VALUES ({ph},{ph},{ph},{ph},{ph}) "
                    f"ON CONFLICT(user_id,tenant_id,briefing_date) DO UPDATE SET content_json=excluded.content_json",
                    (bid, user_id, tenant_id, bdate, content),
                )
            except Exception:
                conn.execute(
                    f"INSERT OR REPLACE INTO user_daily_briefings (id,user_id,tenant_id,briefing_date,content_json) "
                    f"VALUES ({ph},{ph},{ph},{ph},{ph})",
                    (bid, user_id, tenant_id, bdate, content),
                )
            conn.commit()
    except Exception as exc:
        logger.debug("[retro] store failed: %s", exc)


def export_retro_as_slides(retro: dict, user_id: str, tenant_id: str = "default") -> bytes | None:
    """Convert a weekly retro dict to a PPTX file and return bytes."""
    try:
        from tools.slides.pptx_builder import build as build_pptx
    except ImportError:
        logger.warning("[retro] pptx_builder not available")
        return None

    slides = []

    slides.append({
        "title": "Weekly Retrospective",
        "subtitle": f"Week ending {retro.get('week_ending', '')}",
        "slide_type": "title",
    })

    summary = retro.get("summary", "")
    if summary:
        slides.append({
            "title": "Week in Summary",
            "body": summary,
            "slide_type": "content",
        })

    done = retro.get("done_tasks", [])
    if done:
        bullets = "\n".join(f"• {t['title']}" for t in done[:8])
        slides.append({"title": f"✅ Completed Tasks ({len(done)})", "body": bullets, "slide_type": "content"})

    commits = retro.get("commits", [])
    if commits:
        bullets = "\n".join(f"• {c}" for c in commits[:8])
        slides.append({"title": f"💻 Code Shipped ({len(commits)} commits)", "body": bullets, "slide_type": "content"})

    interactions = retro.get("interactions", [])
    if interactions:
        bullets = "\n".join(f"• {i['title']} — {i['contact']} ({i['date']})" for i in interactions[:6])
        slides.append({"title": f"🤝 Stakeholder Touchpoints ({len(interactions)})", "body": bullets, "slide_type": "content"})

    objs = retro.get("objective_progress", [])
    if objs:
        bullets = "\n".join(f"• {o['title'][:60]} — {o['pct']}%" for o in objs)
        slides.append({"title": "🎯 Objective Progress", "body": bullets, "slide_type": "content"})

    slides.append({"title": "Next Week", "subtitle": "Generated by ICDEV Second Brain", "slide_type": "outro"})

    try:
        result = build_pptx(slides, title="Weekly Retrospective")
        import pathlib
        p = pathlib.Path(result)
        if p.exists():
            return p.read_bytes()
        return None
    except Exception as exc:
        logger.warning("[retro] pptx build failed: %s", exc)
        return None


def get_latest_retro(user_id: str, tenant_id: str = "default") -> dict | None:
    """Retrieve the most recent weekly retro for display."""
    import json
    from tools.second_brain.constants import BRIEFING_ENV_FLAG
    from tools.db.storage import get_canvas_connection, sql_placeholder
    try:
        with get_canvas_connection(BRIEFING_ENV_FLAG) as conn:
            ph = sql_placeholder(conn)
            row = conn.execute(
                f"SELECT content_json FROM user_daily_briefings "
                f"WHERE user_id={ph} AND tenant_id={ph} AND briefing_date LIKE '%-retro' "
                f"ORDER BY briefing_date DESC LIMIT 1",
                (user_id, tenant_id),
            ).fetchone()
        if not row:
            return None
        return json.loads(row[0] or "{}")
    except Exception:
        return None
