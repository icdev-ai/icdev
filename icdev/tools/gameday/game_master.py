# CUI // SP-CTI
"""GameDay League — game master.

Drives an autonomous tournament by delegating each round to the working
RoundManager orchestrator (tools/gameday/round_manager.py), which runs the four
teams (Red/Blue/Gold/Green) and the judge. The previous adapter delegated to a
never-built ``tools.ai_game_engine.GameSession``; that generic engine is
intentionally not resurrected here (YAGNI).
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


log = get_logger(__name__)

_TEAMS_YAML = Path(__file__).parent.parent.parent / "args" / "gameday_teams.yaml"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class GameMaster:
    """Orchestrates a full tournament via RoundManager rounds."""

    def __init__(
        self,
        tournament_name: str | None = None,
        round_count: int = 5,
        ollama_url: str | None = None,
        timing: str = "standard",
    ):
        self.tournament_name = tournament_name or f"GameDay-{datetime.now().strftime('%Y%m%d-%H%M')}"
        self.round_count = round_count
        self.ollama_url  = ollama_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        self.timing      = timing

    def run_tournament(self) -> dict[str, Any]:
        """Run all rounds end-to-end. Persists failure state on the tournament row.

        Raises the original exception after recording ``status='aborted'`` so
        callers (the /api/gameday/ai-league/start thread, the Genesis reflex)
        can log it rather than silently swallowing an ImportError.
        """
        from . import db as _db
        from .constants import CYBER_SCENARIOS
        from .round_manager import RoundManager

        tournament = get_or_create_active_tournament()
        tournament_id = tournament["id"]
        _db.update_tournament(tournament_id, status="active", started_at=_now())

        try:
            manager = RoundManager(tournament_id, ollama_url=self.ollama_url)
            scenarios = CYBER_SCENARIOS or [{}]
            round_summaries: list[dict] = []
            for i in range(self.round_count):
                scenario = scenarios[i % len(scenarios)]
                _db.update_tournament(tournament_id, current_round=i + 1)
                round_summaries.append(manager.run_round(i + 1, scenario))

            _db.update_tournament(tournament_id, status="completed", completed_at=_now())

            # Refresh the persisted leaderboard (best-effort).
            try:
                from .leaderboard_engine import refresh_leaderboard
                refresh_leaderboard(tournament_id)
            except Exception as exc:  # noqa: BLE001
                log.debug("Leaderboard refresh skipped: %s", exc)

            return {
                "tournament_id":   tournament_id,
                "tournament_name": self.tournament_name,
                "rounds":          len(round_summaries),
                "status":          "completed",
            }

        except Exception as exc:
            # Persist failure state on the tournament row (never except:pass).
            log.error("Tournament %s failed: %s", tournament_id, exc)
            try:
                cfg = json.loads(tournament.get("config_json") or "{}")
            except Exception:
                cfg = {}
            cfg["error"] = str(exc)
            cfg["failed_at"] = _now()
            try:
                _db.update_tournament(
                    tournament_id, status="aborted", config_json=json.dumps(cfg)
                )
            except Exception as persist_exc:  # noqa: BLE001
                log.error("Failed to persist tournament failure state: %s", persist_exc)
            raise


def get_or_create_active_tournament() -> dict:
    """Return the latest active tournament, or create a new one (seeded but not started)."""
    from .db import create_tournament, get_latest_tournament, seed_teams
    latest = get_latest_tournament()
    if latest and latest["status"] in ("pending", "active"):
        return latest
    gm = GameMaster()
    tournament = create_tournament(name=gm.tournament_name)
    with open(_TEAMS_YAML, encoding="utf-8") as fh:
        import yaml as _yaml
        cfg = _yaml.safe_load(fh)
    team_defs = [
        {"key": k, "name": v["name"], "domain": v["domain"], "color": v.get("color", "#6c6c80")}
        for k, v in cfg.get("teams", {}).items()
    ]
    seed_teams(tournament["id"], team_defs)
    return tournament
