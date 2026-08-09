# CUI // SP-CTI
"""GameDay VIZ Reporter — export tournament results as PPTX/HTML via tools/viz/ kernel."""
from __future__ import annotations

import json
from pathlib import Path

from tools.logging.icdev_logger import get_logger

_log = get_logger(__name__)


def export_tournament_report(
    tournament_data: dict,
    output_format: str = "html",
    output_path: str | None = None,
) -> dict:
    """Export a GameDay tournament report using the VIZ visualization kernel.

    Args:
        tournament_data: tournament record including teams, scores, rounds, leaderboard
        output_format: "html", "pptx", or "json"
        output_path: where to write the output (auto-generated if None)

    Returns:
        {"success": bool, "path": str | None, "format": str, "error": str | None}
    """
    try:
        spec = _build_viz_spec(tournament_data)

        if output_path is None:
            t_id = tournament_data.get("tournament_id", "unknown")
            output_path = str(Path("data") / "gameday" / f"tournament_{t_id}_report.{output_format}")

        try:
            from tools.viz.kernel import build_presentation
            build_presentation(spec, output_format=output_format, output_path=output_path)
            return {"success": True, "path": output_path, "format": output_format, "error": None}
        except ImportError:
            _log.info("VIZ kernel not available; falling back to JSON export")
            json_path = output_path.replace(f".{output_format}", ".json")
            Path(json_path).parent.mkdir(parents=True, exist_ok=True)
            Path(json_path).write_text(json.dumps(spec, indent=2), encoding="utf-8", newline="")
            return {
                "success": True,
                "path": json_path,
                "format": "json",
                "error": "VIZ kernel unavailable — JSON fallback used",
            }

    except Exception as exc:
        _log.error("Tournament export failed: %s", exc)
        return {"success": False, "path": None, "format": output_format, "error": str(exc)}


def export_leaderboard_slide(leaderboard: list[dict], output_path: str | None = None) -> dict:
    """Export a leaderboard as a single slide (HTML or PPTX)."""
    spec = {
        "title": "AI GameDay Leaderboard",
        "slides": [
            {
                "type": "leaderboard",
                "title": "Final Standings",
                "data": leaderboard[:10],
                "columns": ["rank", "team_name", "total_pts", "rounds_won", "ai_tool_receipts"],
            }
        ],
        "theme": "dark_ops",
    }
    try:
        from tools.viz.kernel import build_presentation
        path = output_path or "data/gameday/leaderboard_slide.html"
        build_presentation(spec, output_format="html", output_path=path)
        return {"success": True, "path": path}
    except ImportError:
        return {"success": False, "path": None, "error": "VIZ kernel not available"}


def _build_viz_spec(tournament_data: dict) -> dict:
    """Convert tournament data to a VIZ kernel spec."""
    teams = tournament_data.get("teams", [])
    leaderboard = tournament_data.get("leaderboard", [])
    rounds = tournament_data.get("rounds", [])

    slides = []

    slides.append({
        "type": "title",
        "title": "AI GameDay Tournament Report",
        "subtitle": tournament_data.get("scenario_name", "Unknown Scenario"),
        "metadata": {
            "tournament_id": tournament_data.get("tournament_id"),
            "date": tournament_data.get("completed_at", ""),
            "teams": len(teams),
            "rounds": len(rounds),
        },
    })

    if leaderboard:
        slides.append({
            "type": "leaderboard",
            "title": "Final Leaderboard",
            "data": leaderboard[:10],
            "columns": ["rank", "team_name", "total_pts", "ai_tools_used"],
        })

    for team in teams:
        slides.append({
            "type": "team_summary",
            "title": f"Team {team.get('team_key', '').upper()} — {team.get('role', '')}",
            "data": {
                "score": team.get("total_score", 0),
                "artifacts": team.get("artifact_count", 0),
                "ai_receipts": team.get("ai_tool_receipts", 0),
                "ribbons": team.get("ribbons", []),
                "top_artifact": team.get("top_artifact_type"),
            },
        })

    if rounds:
        slides.append({
            "type": "timeline",
            "title": "Tournament Timeline",
            "events": [
                {
                    "time": r.get("started_at", ""),
                    "label": f"Round {r.get('round_num', '?')}",
                    "status": r.get("state"),
                }
                for r in rounds
            ],
        })

    if tournament_data.get("aar_content"):
        slides.append({
            "type": "text",
            "title": "After-Action: Key Findings",
            "content": tournament_data["aar_content"][:500],
        })

    return {
        "title": f"GameDay Tournament Report — {tournament_data.get('scenario_name', 'Unknown')}",
        "slides": slides,
        "theme": "dark_ops",
        "classification": "CUI // SP-CTI",
    }
