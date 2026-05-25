# CUI // SP-CTI
"""GameDay League — game master (thin adapter over AI Game Engine).

Retained for backward compatibility and the Genesis reflex.
All orchestration is now delegated to tools.ai_game_engine.GameSession.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


log = get_logger(__name__)

_TEAMS_YAML = Path(__file__).parent.parent.parent / "args" / "gameday_teams.yaml"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class GameMaster:
    """Thin adapter — delegates to ai_game_engine.GameSession."""

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
        from tools.ai_game_engine.game_session import GameSession
        session = GameSession(
            game_key="gameday",
            round_count=self.round_count,
            timing=self.timing,
            tournament_name=self.tournament_name,
            ollama_url=self.ollama_url,
        )
        return session.run()


def get_or_create_active_tournament() -> dict:
    """Return the latest active tournament, or create a new one (seeded but not started)."""
    from .db import get_latest_tournament, create_tournament, seed_teams
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
