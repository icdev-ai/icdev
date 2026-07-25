# CUI // SP-CTI
"""Genesis Reflex R-GD1 — AI GameDay League autonomous orchestrator.

Fires on a configurable cadence (default: daily at 08:00).
- If no active tournament → starts a new one via GameMaster
- If tournament is active → refreshes the leaderboard + publishes OHC metrics
- If tournament completed → queues a SUGGESTED kanban card with results summary
"""
from __future__ import annotations
IMPLEMENTATION_STATUS = "full"

from tools.logging.icdev_logger import get_logger

from datetime import datetime, timezone

log = get_logger(__name__)

REFLEX_ID   = "R-GD1"
REFLEX_NAME = "gameday_orchestrator"


def run(config: dict, context: dict | None) -> dict:
    """Entry point called by Genesis daemon."""
    result = {
        "reflex":    REFLEX_NAME,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "actions":   [],
        "errors":    [],
    }

    try:
        from tools.gameday.db import get_latest_tournament
        from tools.gameday.leaderboard_engine import refresh_leaderboard
        from tools.gameday.ops_bridge import publish_llmops_summary, publish_aiops_health

        tournament = get_latest_tournament()

        if tournament is None or tournament["status"] == "completed":
            # Start a new tournament
            round_count = config.get("round_count", 5)
            _start_tournament(round_count, result)

        elif tournament["status"] == "active":
            # Refresh leaderboard and push ops metrics
            t_id = tournament["id"]
            refresh_leaderboard(t_id)
            publish_llmops_summary(t_id)
            publish_aiops_health(t_id)
            result["actions"].append({
                "action":      "leaderboard_refresh",
                "tournament":  tournament["name"],
                "round":       tournament["current_round"],
            })
            log.info("[%s] Refreshed leaderboard for tournament %d", REFLEX_NAME, t_id)

        elif tournament["status"] == "pending":
            # Tournament created but not yet started — kick it off
            _start_tournament(
                config.get("round_count", 5), result,
                existing_tournament=tournament
            )

    except Exception as exc:
        log.error("[%s] error: %s", REFLEX_NAME, exc)
        result["errors"].append(str(exc))

    return result


def _start_tournament(round_count: int, result: dict, existing_tournament: dict | None = None) -> None:
    import threading
    from tools.gameday.game_master import GameMaster

    gm = GameMaster(round_count=round_count)

    def _run():
        try:
            gm.run_tournament()
        except Exception as exc:
            log.error("[%s] background tournament failed: %s", REFLEX_NAME, exc)

    t = threading.Thread(target=_run, daemon=True, name="genesis-gd-tournament")
    t.start()

    result["actions"].append({
        "action":      "tournament_started",
        "name":        gm.tournament_name,
        "round_count": round_count,
        "background":  True,
    })
    log.info("[%s] Tournament %s started in background (%d rounds)", REFLEX_NAME, gm.tournament_name, round_count)
