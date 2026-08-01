#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration Chat Advisor — intelligence layer for /simulate/chat (cam canvas) and /chat.

Handles:
  - Slash command processing for 'cam' canvas type
  - Deprecated code / EOL detection from message content
  - COA generation and formatting
  - Active project status injection
  - Cross-canvas deep-link generation
  - LLM-enhanced migration guidance when available

Slash commands:
  /coa <tech>             — COAs for a technology
  /deprecated <tech>      — EOL/deprecation status check
  /refactor <component>   — dispatch refactor job for a component
  /status                 — current migration project status
  /analyze <code>         — scan code/config for deprecated patterns
  /components             — list app components and migration status

Called from:
  tools/simulation/tfw_chat_agent.py  (cam canvas slash commands)
  tools/extensions/builtins/090_migration_advisory_chat.py  (chat_message_after hook)
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import re
from pathlib import Path

logger = get_logger("icdev.migration_chat_advisor")

_ICDEV_ROOT = Path(__file__).resolve().parent.parent.parent

# Patterns that suggest the user is mentioning a technology needing migration analysis
_TECH_MENTION_PATTERNS = [
    r"\b(oracle|postgres|postgresql|mysql|mongodb|redis|elasticsearch|opensearch|kafka|nifi|react|angular|vue|spring|struts|python2|java8|java11|log4j|kubernetes|k8s)\b",
]
_COMPILED_TECH_RE = re.compile("|".join(_TECH_MENTION_PATTERNS), re.I)

# Patterns suggesting a deprecation/EOL question
_EOL_QUESTION_RE = re.compile(
    r"\b(deprecated|end.of.life|eol|outdated|obsolete|legacy|old version|unmaintained|sunsetted|sunset|no longer supported)\b",
    re.I,
)

# Patterns suggesting a migration question
_MIGRATION_QUESTION_RE = re.compile(
    r"\b(migrate|migration|modernize|modernization|upgrade|refactor|cloud.native|lift.and.shift|coa|course of action|7r|rehost|replatform|rearchitect)\b",
    re.I,
)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _get_cam_conn():
    from tools.db.storage import get_connection
    from tools.dashboard.config import DB_PATH
    return get_connection(db_path=str(DB_PATH))


def _table_exists(conn, name: str) -> bool:
    try:
        r = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=%s", (name,)).fetchone()
        return (r[0] if isinstance(r, (tuple, list)) else r["cnt"]) > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Slash command handlers
# ---------------------------------------------------------------------------


def handle_coa(tech_arg: str, session_id: str = "") -> dict:
    """Handle /coa <tech> — generate COA options for a technology."""
    tech = tech_arg.strip()
    if not tech:
        return {
            "reply": "[COA] Usage: `/coa <technology>` — e.g. `/coa oracle` or `/coa elasticsearch`",
            "mode": "coa",
            "coa_cards": [],
        }

    # Detect cloud preference from session context (default aws)
    cloud = "aws"
    try:
        conn = _get_cam_conn()
        if _table_exists(conn, "mc_projects"):
            conn.execute(
                "SELECT name FROM mc_projects ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            conn.close()
    except Exception:
        pass

    from tools.migration_canvas.coa_engine import get_coas, format_coa_markdown
    coas = get_coas(tech, cloud=cloud)

    md = f"## Courses of Action: **{tech.title()}** → {cloud.upper()}\n\n"
    md += format_coa_markdown(coas)
    md += "\n\n---\n*Use `/refactor <component>` to dispatch code transformation jobs, or click action buttons above.*"

    # Check for deprecation
    from tools.migration_canvas.coa_engine import get_deprecation_status
    dep = get_deprecation_status(tech)
    if dep.get("status") in ("deprecated", "at_risk"):
        severity = dep.get("severity", "medium")
        icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(severity, "⚪")
        eol_date = dep.get("eol_date", "")
        eol_str = f" (EOL: {eol_date})" if eol_date else ""
        md = (
            f"> {icon} **DEPRECATION WARNING** — `{tech}`{eol_str}: {dep.get('reason', '')}\n"
            f"> **Recommended successor:** {dep.get('successor', 'see COAs below')}\n\n"
        ) + md

    return {
        "reply": md,
        "mode": "coa",
        "coa_cards": coas,
        "tech": tech,
        "cloud": cloud,
        "deprecation": dep,
    }


def handle_deprecated(tech_arg: str) -> dict:
    """Handle /deprecated <tech> — show EOL status and migration path."""
    tech = tech_arg.strip() or "?"
    from tools.migration_canvas.coa_engine import get_deprecation_status
    dep = get_deprecation_status(tech)

    status = dep.get("status", "unknown")
    severity = dep.get("severity", "info")
    icon_map = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢", "info": "ℹ️"}
    icon = icon_map.get(severity, "⚪")

    if status == "deprecated":
        eol = dep.get("eol_date", "unknown")
        lines = [
            f"{icon} **`{tech}` is DEPRECATED** (EOL: {eol})",
            f"\n**Why:** {dep.get('reason', 'No details available.')}",
            f"\n**Successor:** {dep.get('successor', 'Not specified')}",
            f"\n**Migration path:** {dep.get('migration_path', 'See COAs')}",
            f"\n\nRun `/coa {tech}` to see full Courses of Action with pros/cons and effort estimates.",
        ]
    elif status == "at_risk":
        lines = [
            f"{icon} **`{tech}` is AT RISK** — still maintained but approaching EOL or strategic shift recommended.",
            f"\n{dep.get('reason', '')}",
            f"\n**Recommended successor:** {dep.get('successor', '')}",
            f"\nRun `/coa {tech}` for migration options.",
        ]
    elif status == "active":
        lines = [
            f"✅ **`{tech}` is actively maintained.**",
            f"\n{dep.get('reason', '')}",
            f"\n**Cloud successor:** {dep.get('successor', '')}",
            f"\nRun `/coa {tech}` to explore migration options anyway.",
        ]
    else:
        lines = [
            f"❓ **Unknown status for `{tech}`.**",
            "No deprecation data found in catalog. Check vendor release notes.",
            f"\nRun `/coa {tech}` for general migration COAs.",
        ]

    return {
        "reply": "\n".join(lines),
        "mode": "deprecated",
        "tech": tech,
        "deprecation": dep,
    }


def handle_refactor(component_arg: str, session_id: str = "") -> dict:
    """Handle /refactor <component> — dispatch or show refactor jobs."""
    component = component_arg.strip()

    # Find the active CAM project
    project_id = _get_active_project_id(session_id)
    if not project_id:
        return {
            "reply": (
                "[Refactor] No active CAM project found. "
                "Navigate to [Migration Canvas](/migration-canvas/projects) to set up a project first, "
                "then return here to trigger refactor jobs."
            ),
            "mode": "refactor",
            "jobs": [],
        }

    try:
        from tools.migration_canvas.cam_refactor_engine import dispatch, get_jobs

        # Dispatch new jobs (dry_run=False triggers actual job creation)
        result = dispatch(project_id, component_name=component or None, dry_run=False)
        jobs = get_jobs(project_id)

        relevant = [j for j in jobs if not component or component.lower() in j.get("component_name", "").lower()]
        lines = [f"**[Refactor]** Dispatched {len(result)} job(s) for project `{project_id[:16]}...`\n"]
        if relevant:
            lines.append("| Component | Type | Status |")
            lines.append("| --- | --- | --- |")
            for j in relevant[:10]:
                lines.append(f"| {j['component_name']} | `{j['refactor_type']}` | {j['status']} |")
        lines.append("\nView full results on the [Migration Canvas project detail](/migration-canvas/projects).")

        return {
            "reply": "\n".join(lines),
            "mode": "refactor",
            "project_id": project_id,
            "jobs": relevant[:10],
            "dispatched": len(result),
        }
    except Exception as exc:
        logger.warning("Refactor dispatch error: %s", exc)
        return {
            "reply": f"[Refactor] Error dispatching jobs: {exc}",
            "mode": "refactor",
            "error": str(exc),
        }


def handle_status(session_id: str = "") -> dict:
    """Handle /status — show current migration project status."""
    try:
        conn = _get_cam_conn()
        if not _table_exists(conn, "mc_projects"):
            conn.close()
            return {"reply": "[Status] No migration projects found. Navigate to [Migration Canvas](/migration-canvas/projects) to create one.", "mode": "status"}

        projects = conn.execute(
            "SELECT id, name, customer, status, created_at FROM mc_projects ORDER BY created_at DESC LIMIT 5"
        ).fetchall()

        if not projects:
            conn.close()
            return {"reply": "[Status] No migration projects. [Create one](/migration-canvas/projects).", "mode": "status"}

        lines = ["## Migration Project Status\n"]
        for p in projects:
            proj_id = p["id"] if hasattr(p, "keys") else p[0]
            proj_name = p["name"] if hasattr(p, "keys") else p[1]
            customer = p["customer"] if hasattr(p, "keys") else p[2]
            status = p["status"] if hasattr(p, "keys") else p[3]
            status_icon = {"active": "🟢", "in_review": "🟡", "draft": "⚪", "complete": "✅", "archived": "📦"}.get(status, "⚪")

            # Get phase info
            if _table_exists(conn, "mc_project_phases"):
                phases = conn.execute(
                    "SELECT COUNT(*) as total FROM mc_project_phases WHERE project_id = %s", (proj_id,)
                ).fetchone()
                in_progress = conn.execute(
                    "SELECT COUNT(*) as cnt FROM mc_project_phases WHERE project_id = %s AND status = 'in_progress'", (proj_id,)
                ).fetchone()
                completed = conn.execute(
                    "SELECT COUNT(*) as cnt FROM mc_project_phases WHERE project_id = %s AND status = 'completed'", (proj_id,)
                ).fetchone()
                total_ph = phases[0] if phases else 0
                ip_ph = in_progress[0] if in_progress else 0
                done_ph = completed[0] if completed else 0
                phase_str = f"{done_ph}/{total_ph} phases complete, {ip_ph} in progress"
            else:
                phase_str = "phase data unavailable"

            lines.append(f"### {status_icon} [{proj_name}](/migration-canvas/projects/{proj_id})")
            lines.append(f"**Customer:** {customer}  |  **Status:** {status}  |  **Phases:** {phase_str}")

            # Refactor jobs summary
            if _table_exists(conn, "mc_refactor_jobs"):
                rj = conn.execute(
                    "SELECT status, COUNT(*) as cnt FROM mc_refactor_jobs WHERE project_id = %s GROUP BY status", (proj_id,)
                ).fetchall()
                if rj:
                    rj_summary = ", ".join(f"{r[1]} {r[0]}" for r in rj)
                    lines.append(f"**Refactor jobs:** {rj_summary}")

            lines.append("")

        conn.close()
        lines.append("---")
        lines.append("Use `/coa <tech>` for migration options or `/components` to see all app components.")

        return {"reply": "\n".join(lines), "mode": "status", "projects": [dict(p) for p in projects]}

    except Exception as exc:
        logger.warning("Status error: %s", exc)
        return {"reply": f"[Status] Error loading projects: {exc}", "mode": "status"}


def handle_components(session_id: str = "") -> dict:
    """Handle /components — list app components and migration status."""
    project_id = _get_active_project_id(session_id)

    try:
        conn = _get_cam_conn()
        if not _table_exists(conn, "mc_app_inventory"):
            conn.close()
            return {"reply": "[Components] No app inventory found.", "mode": "components", "components": []}

        if project_id:
            # Get session linked to project
            proj_row = conn.execute(
                "SELECT mc_session_id FROM mc_projects WHERE id = %s", (project_id,)
            ).fetchone()
            session_filter = (proj_row[0] or "") if proj_row else ""
            if session_filter:
                rows = conn.execute(
                    "SELECT * FROM mc_app_inventory WHERE session_id = %s ORDER BY name", (session_filter,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM mc_app_inventory ORDER BY name LIMIT 20").fetchall()
        else:
            rows = conn.execute("SELECT * FROM mc_app_inventory ORDER BY name LIMIT 20").fetchall()

        conn.close()
        if not rows:
            return {
                "reply": "[Components] No app components found. Seed a migration project first.",
                "mode": "components",
                "components": [],
            }

        lines = ["## App Components & Migration Status\n"]
        lines.append("| Component | Type | Version | Strategy | Migration Status |")
        lines.append("| --- | --- | --- | --- | --- |")
        components = []
        for r in rows:
            d = dict(r)
            name = d.get("name", "?")
            app_type = d.get("app_type", "?")
            version = d.get("version", "")
            strategy = d.get("migration_strategy", "?")
            status = d.get("migration_status", "?")
            status_icon = {"completed": "✅", "in_progress": "🔄", "not_started": "⚪", "blocked": "🔴"}.get(status, "⚪")
            lines.append(f"| **{name}** | {app_type} | {version} | `{strategy}` | {status_icon} {status} |")
            components.append(d)

        lines.append("\n*Run `/coa <component>` for migration options or `/refactor <component>` to dispatch code transformation.*")

        return {
            "reply": "\n".join(lines),
            "mode": "components",
            "components": components,
        }

    except Exception as exc:
        logger.warning("Components error: %s", exc)
        return {"reply": f"[Components] Error: {exc}", "mode": "components", "components": []}


_EOL_CHECKS = [
    (r"import\s+java\.util\.Date\b", "java_date", "java8", "Java Date API deprecated — use java.time.LocalDate"),
    (r"python_requires\s*=\s*['\"]<3\b", "python2_req", "python2", "Package targets Python 2"),
    (r"from\s+struts2\b|Struts2\b", "struts2_import", "struts2", "Apache Struts 2 import detected — EOL framework"),
    (r"log4j-1\.\d|log4j:log4j:", "log4j1_dep", "log4j1", "Log4j 1.x dependency — EOL with known RCE CVEs"),
    (r"spring-core.*[34]\.\d|spring-framework.*[34]\.\d", "spring_old", "spring4", "Spring Framework 3/4.x — upgrade to Spring Boot 3.x"),
    (r"angular.*['\"]1\.\d|AngularJS", "angularjs", "angular1", "AngularJS (1.x) detected — EOL Dec 2021"),
    (r"elasticsearch.*[67]\.\d", "es_old", "elasticsearch7", "Elasticsearch 6/7.x — SSPL license change, migrate to OpenSearch"),
    (r"oracle\.jdbc|jdbc:oracle", "oracle_jdbc", "oracle", "Oracle JDBC detected — consider Aurora PostgreSQL migration"),
    (r"jedis|lettuce.*redis", "redis_client", "redis", "Redis client — consider ElastiCache for Redis migration"),
    (r"@SuppressWarnings.*deprecated|@Deprecated", "java_deprecated", None, "Deprecated Java API usage detected"),
    (r"TODO.*migrate|FIXME.*deprecated|HACK.*legacy", "todo_migrate", None, "Code comments indicate known migration debt"),
]


def _scan_eol_patterns(text: str) -> list[dict]:
    findings = []
    for pattern, finding_id, tech_key, message in _EOL_CHECKS:
        if re.search(pattern, text, re.I):
            dep_info = None
            if tech_key:
                try:
                    from tools.migration_canvas.coa_engine import get_deprecation_status
                    dep_info = get_deprecation_status(tech_key)
                except Exception:
                    pass
            findings.append({
                "id": finding_id,
                "message": message,
                "tech": tech_key,
                "severity": dep_info.get("severity", "medium") if dep_info else "info",
                "suggestion": f"Run `/coa {tech_key}`" if tech_key else "Review and modernize",
            })
    return findings


def handle_analyze(code_or_text: str) -> dict:
    """Handle /analyze — accepts a URL (fetches + LLM analysis) or pasted code (EOL scan + LLM)."""
    arg = code_or_text.strip()
    if not arg:
        return {
            "reply": (
                "**Usage:** `/analyze <url | code>`\n\n"
                "- `/analyze https://github.com/org/repo` — fetch and analyze a repo\n"
                "- `/analyze <paste code or dependency list>` — scan for deprecated patterns"
            ),
            "mode": "analyze",
            "findings": [],
        }

    # URL path — delegate to url_analyzer for fetch + LLM analysis
    if arg.startswith("http://") or arg.startswith("https://"):
        try:
            from tools.chat_router.url_analyzer import analyze as _url_analyze
            result = _url_analyze(arg, canvas_type="cam")
            return {"reply": result["reply"], "mode": "analyze", "findings": []}
        except Exception as exc:
            return {"reply": f"[Analyze] URL fetch error: {exc}", "mode": "analyze", "findings": []}

    # Text path — EOL pattern scan + LLM enrichment
    findings = _scan_eol_patterns(arg)

    if not findings:
        if re.search(r"spring-boot.*3\.\d|springboot3", arg, re.I):
            findings.append({"id": "spring_boot3_ok", "message": "Spring Boot 3.x detected — current and supported.", "severity": "info", "tech": None})
        elif re.search(r"python_requires.*>=.*3\.", arg, re.I):
            findings.append({"id": "python3_ok", "message": "Python 3.x requirement — no action needed.", "severity": "info", "tech": None})

    if findings:
        lines = [f"## Code Analysis Findings ({len(findings)})\n"]
        for f in findings:
            icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢", "info": "ℹ️"}.get(f.get("severity", "info"), "⚪")
            lines.append(f"{icon} **{f['message']}**")
            if f.get("suggestion"):
                lines.append(f"   → {f['suggestion']}")
        reply = "\n".join(lines)
    else:
        reply = (
            "✅ **No deprecated patterns detected** in the provided snippet.\n\n"
            "For deeper analysis, share your `requirements.txt`, `pom.xml`, or `package.json`."
        )

    return {"reply": reply, "mode": "analyze", "findings": findings}


_URL_RE = re.compile(r"https?://\S+", re.I)
_ANALYZE_INTENT_RE = re.compile(
    r"\b(analyze|analyse|review|inspect|check out|look at|scan|what is|tell me about|assess)\b",
    re.I,
)
_DEPRECATED_INTENT_RE = re.compile(
    r"\b(deprecated|end.of.life|eol|outdated|obsolete|legacy|old version|unmaintained|sunset)\b",
    re.I,
)
_REFACTOR_INTENT_RE = re.compile(
    r"\b(refactor|convert|rewrite|port|translate|migrate.*code|code.*migration)\b",
    re.I,
)
_STATUS_INTENT_RE = re.compile(
    r"\b(status|readiness|how.*(ready|far|done)|progress|report|dryrun|dry.run)\b",
    re.I,
)
_COA_INTENT_RE = re.compile(
    r"\b(optimize|optimise|performance|cost|scaling|improve|speed.?up|slow|latency|expensive)\b",
    re.I,
)


def handle_free_text(content: str, session_id: str = "") -> dict:
    """Handle free-text messages in cam canvas.

    Auto-detects intent and routes to the appropriate handler without
    requiring the user to type explicit slash commands.
    """
    # --- Auto-route: URL in message → /analyze ---
    url_match = _URL_RE.search(content)
    if url_match:
        url = url_match.group(0).rstrip(".,;)")
        return handle_analyze(url)

    # --- Auto-route: code/config pasted (multi-line, no natural language) ---
    lines = content.strip().splitlines()
    if len(lines) >= 3 and sum(1 for ln in lines if re.search(r"[{};=<>]|import |require |def |class ", ln)) >= 2:
        return handle_analyze(content)

    # --- Intent detection ---
    is_eol_q = bool(_DEPRECATED_INTENT_RE.search(content))
    is_migration_q = bool(_MIGRATION_QUESTION_RE.search(content))
    is_refactor_q = bool(_REFACTOR_INTENT_RE.search(content))
    is_status_q = bool(_STATUS_INTENT_RE.search(content))
    is_coa_q = bool(_COA_INTENT_RE.search(content))
    mentioned_techs = list({m.lower() for m in _COMPILED_TECH_RE.findall(content)})

    # Deprecated / EOL question — run it for each mentioned tech, or ask LLM if none
    if is_eol_q:
        if mentioned_techs:
            return handle_deprecated(mentioned_techs[0])
        llm_response = _call_llm_with_migration_context(content, session_id)
        if llm_response:
            return _cam_llm_reply(llm_response)

    # Refactor / modernize intent — run for specific tech, or status+components for general
    if is_refactor_q:
        if mentioned_techs:
            return handle_refactor(mentioned_techs[0], session_id=session_id)
        # General modernization ask — surface inventory + status automatically
        return _auto_modernize(content, session_id)

    # Status / readiness check
    if is_status_q:
        return handle_status(session_id=session_id)

    # COA / optimization for a tech
    if is_coa_q:
        if mentioned_techs:
            return handle_coa(mentioned_techs[0], session_id=session_id)
        llm_response = _call_llm_with_migration_context(content, session_id)
        if llm_response:
            return _cam_llm_reply(llm_response)

    # General migration question with tech mention → COA
    if is_migration_q and mentioned_techs:
        return handle_coa(mentioned_techs[0], session_id=session_id)

    # General migration question with no tech → surface overview + LLM guidance
    if is_migration_q:
        return _auto_modernize(content, session_id)

    # Anything else → LLM with migration context
    llm_response = _call_llm_with_migration_context(content, session_id)
    if llm_response:
        return _cam_llm_reply(llm_response)

    # Final fallback
    if mentioned_techs:
        tech = mentioned_techs[0]
        return handle_coa(tech, session_id=session_id)
    return {"reply": "What would you like to migrate or modernize? Paste a GitHub URL, describe your stack, or name a technology.", "mode": "cam"}


# ---------------------------------------------------------------------------
# Advisory for chat_message_after hook
# ---------------------------------------------------------------------------


ADVISORY_COOLDOWN_TURNS = 10
_last_advisory_turn: dict = {}


def get_migration_advisory(context: dict) -> dict | None:
    """Check for migration-relevant advisory to inject after a chat message.

    Called from tools/extensions/builtins/090_migration_advisory_chat.py.
    Returns advisory dict or None.
    """
    context_id = context.get("context_id", "")
    turn_number = context.get("turn_number", 0)
    project_id = context.get("project_id", "")
    user_query = context.get("user_query", "")

    # Cooldown guard
    last = _last_advisory_turn.get(context_id, -(ADVISORY_COOLDOWN_TURNS + 1))
    if (turn_number - last) < ADVISORY_COOLDOWN_TURNS:
        return None

    # Only advise when migration keywords are present or project is set
    if not project_id and not (_MIGRATION_QUESTION_RE.search(user_query) or _EOL_QUESTION_RE.search(user_query)):
        return None

    try:
        conn = _get_cam_conn()
        if not _table_exists(conn, "mc_projects"):
            conn.close()
            return None

        if project_id:
            proj = conn.execute("SELECT * FROM mc_projects WHERE id = %s", (project_id,)).fetchone()
        else:
            proj = conn.execute("SELECT * FROM mc_projects ORDER BY created_at DESC LIMIT 1").fetchone()

        if not proj:
            conn.close()
            return None

        proj_dict = dict(proj)
        proj_name = proj_dict.get("name", "Migration Project")
        proj_id = proj_dict.get("id", "")

        # Check in-progress phases
        advisory_msg = None
        if _table_exists(conn, "mc_project_phases"):
            ip = conn.execute(
                "SELECT title, wave_num FROM mc_project_phases WHERE project_id = %s AND status = 'in_progress' LIMIT 1",
                (proj_id,),
            ).fetchone()
            if ip:
                advisory_msg = (
                    f"Active migration phase: **{ip[0]}** (Wave {ip[1]}) is in progress for project '{proj_name}'. "
                    f"[View project]({'/migration-canvas/projects/' + proj_id})"
                )

        # Check failed refactor jobs
        if not advisory_msg and _table_exists(conn, "mc_refactor_jobs"):
            failed = conn.execute(
                "SELECT component_name, refactor_type FROM mc_refactor_jobs WHERE project_id = %s AND status = 'failed' LIMIT 1",
                (proj_id,),
            ).fetchone()
            if failed:
                advisory_msg = (
                    f"⚠️ Refactor job failed: `{failed[1]}` for `{failed[0]}`. "
                    f"[Review jobs]({'/migration-canvas/projects/' + proj_id})"
                )

        conn.close()

        if not advisory_msg:
            return None

        _last_advisory_turn[context_id] = turn_number
        return {
            "message": advisory_msg,
            "action": f"python tools/migration_canvas/cam_refactor_engine.py --list-jobs --project {proj_id} --json",
            "project_id": proj_id,
            "project_name": proj_name,
        }

    except Exception as exc:
        logger.debug("Migration advisory error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _auto_modernize(original_content: str, session_id: str = "") -> dict:
    """Auto-run status + components + LLM guidance for a general modernization ask.

    Called when the user expresses intent to migrate/refactor but hasn't named a
    specific technology or component — avoids asking them to type more commands.
    """
    parts: list[str] = []

    # 1. Run /status to show current migration state
    try:
        status_result = handle_status(session_id=session_id)
        status_reply = status_result.get("reply", "")
        if status_reply and "No active" not in status_reply and "Error" not in status_reply:
            parts.append(status_reply)
    except Exception:
        pass

    # 2. Run /components to list what needs migrating
    try:
        comp_result = handle_components(session_id=session_id)
        comp_reply = comp_result.get("reply", "")
        if comp_reply and "No app components" not in comp_reply and "Error" not in comp_reply:
            parts.append(comp_reply)
    except Exception:
        pass

    # 3. LLM guidance on the user's specific question
    llm_reply = _call_llm_with_migration_context(original_content, session_id)
    if llm_reply:
        parts.append(llm_reply)

    if not parts:
        # No inventory yet — fall back to LLM or onboarding prompt
        if llm_reply:
            return _cam_llm_reply(llm_reply)
        return {
            "reply": (
                "No migration inventory found yet. To get started:\n\n"
                "- Paste a GitHub URL or architecture doc and I'll analyze it automatically\n"
                "- Describe your current stack (e.g. 'Java Spring Boot on-prem with Oracle DB') for a migration roadmap\n"
                "- Share a `pom.xml`, `requirements.txt`, or config file for dependency analysis"
            ),
            "mode": "cam",
        }

    return _cam_llm_reply("\n\n---\n\n".join(parts))


def _get_active_project_id(session_id: str = "") -> str | None:
    """Find the most recent active CAM project ID."""
    try:
        conn = _get_cam_conn()
        if not _table_exists(conn, "mc_projects"):
            conn.close()
            return None
        row = conn.execute(
            "SELECT id FROM mc_projects WHERE status IN ('active','in_review','approved') ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def _cam_llm_reply(text: str) -> dict:
    """Build a 'cam' chat reply dict with a grounding verdict attached.

    TRUST invariant: LLM-drafted migration guidance must carry validated
    citations or a grounding-warning flag (see tools.migration_canvas.grounding,
    built on tools.quality.citation_grounding).
    """
    reply: dict = {"reply": text, "mode": "cam"}
    try:
        from tools.migration_canvas.grounding import assess_response
        reply["grounding"] = assess_response(text, method="cam_chat")
    except Exception:  # pragma: no cover - grounding is best-effort
        reply["grounding"] = {}
    return reply


def _call_llm_with_migration_context(content: str, session_id: str = "", chain_mode: str = "") -> str | None:
    """Call LLM with migration system context injected. Returns None if LLM unavailable."""
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        system_prompt = (
            "You are a senior cloud migration architect. Answer the user's question directly "
            "with specific, actionable guidance — concrete steps, tradeoffs, AWS/Azure/GCP "
            "service recommendations, effort estimates, and risks. "
            "Do NOT ask the user to run commands or tell them what commands are available. "
            "Do NOT present 'Next Steps' that require the user to do something — execute the "
            "analysis yourself and present the findings. "
            "Use markdown formatting. Be direct and specific."
        )

        router = LLMRouter()
        req = LLMRequest(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            chain_mode=chain_mode,
        )
        resp = router.invoke("chat_response", req)
        return resp.content if resp and resp.content else None
    except (ImportError, Exception) as exc:
        logger.debug("LLM unavailable for migration chat: %s", exc)
        return None
