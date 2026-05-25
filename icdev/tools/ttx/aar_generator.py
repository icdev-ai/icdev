# CUI // SP-CTI
"""TTX Engine — After-Action Report (AAR) generator."""

from __future__ import annotations

import logging
from pathlib import Path

from tools.db.storage import get_connection
from .leaderboard import get_leaderboard, award_ribbons
from .constants import RIBBON_DEFS

log = logging.getLogger(__name__)


def generate_aar(session_id: int) -> str:
    """Generate a Markdown AAR for a completed session."""
    conn = get_connection()
    session = conn.execute(
        "SELECT * FROM ttx_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if not session:
        return f"# AAR Error\nSession {session_id} not found."

    session = dict(session)
    teams = {
        r["team_id"]: dict(r)
        for r in conn.execute(
            "SELECT * FROM ttx_teams WHERE session_id = ?", (session_id,)
        ).fetchall()
    }
    injects = conn.execute(
        "SELECT * FROM ttx_injects WHERE session_id = ? ORDER BY sequence_num, at_minute",
        (session_id,),
    ).fetchall()
    lb = get_leaderboard(session_id)
    ribbons = award_ribbons(session_id)

    lines: list[str] = []
    _h = lines.append

    _h("# CUI // SP-CTI")
    _h(f"# After-Action Report — {session['scenario_slug'].replace('_', ' ').title()}")
    _h(f"**Session ID:** {session_id}  ")
    _h(f"**Facilitator:** {session.get('facilitator_name', 'N/A')}  ")
    _h(f"**Mode:** {session.get('session_mode', 'N/A')}  ")
    _h(f"**Started:** {session.get('started_at', 'N/A')}  ")
    _h(f"**Ended:** {session.get('ended_at', 'N/A')}  ")
    _h("")

    # Leaderboard
    _h("## Final Leaderboard")
    _h("")
    _h("| Rank | Team | Total Pts | AI Receipts | Judge Score | Time Bonus |")
    _h("|------|------|-----------|-------------|-------------|------------|")
    for entry in lb:
        _h(f"| {entry['rank']} | {entry['team_name']} | **{entry['total_score']}** | {entry['receipt_pts']} | {entry['judge_pts']} | {entry['time_bonus_pts']} |")
    _h("")

    # Ribbons
    _h("## Category Ribbons")
    _h("")
    for ribbon_id, winner in ribbons.items():
        defn = RIBBON_DEFS[ribbon_id]
        if winner:
            _h(f"- {defn['icon']} **{defn['label']}** → {winner['team_name']} (score: {winner['value']})")
        else:
            _h(f"- {defn['icon']} **{defn['label']}** → *No winner*")
    _h("")

    # Inject-by-inject replay
    _h("## Inject Replay")
    _h("")
    for inj in injects:
        inj = dict(inj)
        _h(f"### Inject: {inj['title']}")
        _h(f"*Dispatched: {inj.get('dispatched_at', 'N/A')} | Closed: {inj.get('closed_at', 'N/A')}*")
        _h("")

        responses = conn.execute(
            """SELECT r.*, s.receipt_pts, s.judge_pts, s.time_bonus_pts, s.total_pts, s.judge_rationale_json
               FROM ttx_responses r
               LEFT JOIN ttx_scores s ON s.response_id = r.response_id
               WHERE r.inject_id = ?
               ORDER BY r.submitted_at""",
            (inj["inject_id"],),
        ).fetchall()

        if responses:
            _h("| Team | Response (truncated) | Receipt Pts | Judge Pts | Time Bonus | Total |")
            _h("|------|----------------------|-------------|-----------|------------|-------|")
            for resp in responses:
                resp = dict(resp)
                team_name = teams.get(resp["team_id"], {}).get("team_name", f"Team {resp['team_id']}")
                preview = (resp.get("response_text") or "")[:80].replace("|", "\\|").replace("\n", " ")
                _h(
                    f"| {team_name} | {preview}… | "
                    f"{resp.get('receipt_pts', 0)} | {resp.get('judge_pts', 0)} | "
                    f"{resp.get('time_bonus_pts', 0)} | **{resp.get('total_pts', 0)}** |"
                )
        else:
            _h("*No responses submitted for this inject.*")
        _h("")

    # AI Usage Summary
    _h("## AI Usage Summary")
    _h("")
    _h("| Team | Total Receipts | AI Tools Used |")
    _h("|------|----------------|---------------|")
    for team_id, team in teams.items():
        receipt_count = conn.execute(
            "SELECT COALESCE(SUM(receipt_count), 0) FROM ttx_scores WHERE team_id = ?",
            (team_id,),
        ).fetchone()[0] or 0
        tools_used = conn.execute(
            "SELECT DISTINCT tool_slug FROM ttx_api_log WHERE team_id = ?",
            (team_id,),
        ).fetchall()
        tool_list = ", ".join(r["tool_slug"] for r in tools_used) or "None"
        _h(f"| {team['team_name']} | {receipt_count} | {tool_list} |")
    _h("")

    # AADC Design Compliance Summary (only if any AADC design challenge injects ran)
    aadc_scores = _collect_aadc_scores(conn, session_id, injects, teams)
    if aadc_scores:
        _h("## AADC Design Compliance Scores")
        _h("")
        _h("*Automated scores from AADC assess_design() — objective, no judge required.*")
        _h("")
        _h("| Team | Inject | AADC Score | Checks Passed | Checks Failed |")
        _h("|------|--------|------------|---------------|---------------|")
        for entry in aadc_scores:
            _h(
                f"| {entry['team_name']} | {entry['inject_title']} | "
                f"**{entry['score']}/100** | {entry['passed']} | {entry['failed']} |"
            )
        _h("")

    _h("---")
    _h("*Generated by ICDEV™ TTX Engine — CUI // SP-CTI*")

    return "\n".join(lines)


def _collect_aadc_scores(conn, session_id: int, injects, teams: dict) -> list[dict]:
    """Extract AADC compliance scores from judge_rationale_json for design challenge injects."""
    import json as _json
    results: list[dict] = []
    for inj in injects:
        inj = dict(inj)
        cfg = _json.loads(inj.get("config_json") or "{}")
        if cfg.get("inject_type") != "aadc_design_challenge":
            continue
        responses = conn.execute(
            """SELECT r.team_id, s.judge_pts, s.judge_rationale_json
               FROM ttx_responses r
               LEFT JOIN ttx_scores s ON s.response_id = r.response_id
               WHERE r.inject_id = ?""",
            (inj["inject_id"],),
        ).fetchall()
        for resp in responses:
            resp = dict(resp)
            rationale_raw = resp.get("judge_rationale_json") or "{}"
            try:
                rationale = _json.loads(rationale_raw)
            except Exception:
                rationale = {}
            check_results = rationale.get("check_results", [])
            passed = sum(1 for c in check_results if c.get("passed"))
            failed_ids = [c["id"] for c in check_results if not c.get("passed")]
            team_name = teams.get(resp["team_id"], {}).get("team_name", f"Team {resp['team_id']}")
            results.append({
                "team_name": team_name,
                "inject_title": inj["title"],
                "score": resp.get("judge_pts", 0),
                "passed": passed,
                "failed": ", ".join(failed_ids) if failed_ids else "none",
            })
    return results


def export_aar_pdf(session_id: int, output_path: Path | None = None) -> Path | None:
    """Export AAR as PDF using weasyprint if available."""
    try:
        import markdown
        from weasyprint import HTML
    except ImportError:
        log.warning("weasyprint/markdown not available — PDF export skipped")
        return None

    md_content = generate_aar(session_id)
    html_body = markdown.markdown(md_content, extensions=["tables"])
    html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<style>
  body {{font-family: Arial, sans-serif; font-size: 10pt; color: #222;}}
  table {{border-collapse: collapse; width: 100%;}}
  th, td {{border: 1px solid #ccc; padding: 4px 8px;}}
  th {{background: #f0f0f0;}}
  h1 {{color: #c00;}} h2 {{color: #333;}} h3 {{color: #555;}}
</style>
</head><body>{html_body}</body></html>"""

    if output_path is None:
        import tempfile
        tmp = Path(tempfile.gettempdir()) / f"aar_session_{session_id}.pdf"
        output_path = tmp

    HTML(string=html).write_pdf(str(output_path))
    return output_path
