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

# The AI-ops packs key their briefs by team colour; TeamRunner reads the
# cyber-pack keys. Map once here so every pack reaches the teams with a brief.
_BRIEF_ALIASES = {
    "red_brief":   "attack_brief",
    "blue_brief":  "defense_brief",
    "gold_brief":  "innovation_brief",
    "green_brief": "compliance_brief",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_teams_config() -> dict:
    import yaml as _yaml
    with open(_TEAMS_YAML, encoding="utf-8") as fh:
        return _yaml.safe_load(fh) or {}


def selected_pack_names(config: dict | None = None) -> list[str]:
    """Pack names the League plays, from ``scenario_selection.packs``.

    ``all`` (the default) means every pack in ``ALL_SCENARIO_PACKS``. An unknown
    or empty selection raises ValueError rather than silently playing nothing.
    """
    from .scenarios import ALL_SCENARIO_PACKS

    if config is None:
        config = _load_teams_config()
    packs = (config.get("scenario_selection") or {}).get("packs", "all")
    if packs is None or packs == "all":
        return list(ALL_SCENARIO_PACKS)
    if isinstance(packs, str):
        packs = [packs]
    names = list(packs)
    unknown = [p for p in names if p not in ALL_SCENARIO_PACKS]
    if unknown:
        raise ValueError(
            f"scenario_selection.packs names unknown pack(s) {unknown}; "
            f"known: {sorted(ALL_SCENARIO_PACKS)}"
        )
    if not any(ALL_SCENARIO_PACKS[p] for p in names):
        raise ValueError("scenario_selection.packs selects no scenarios")
    return names


def select_scenarios(config: dict | None = None) -> list[dict]:
    """Scenarios for the League, interleaved round-robin across the selected packs.

    Round N plays ``result[(N-1) % len(result)]``, so a 5-round tournament with
    every pack selected plays five different packs — not five cyber scenarios.
    Each scenario is a copy tagged with its ``pack`` and carrying the cyber-pack
    brief keys TeamRunner reads.
    """
    from .scenarios import ALL_SCENARIO_PACKS

    queues = []
    for pack in selected_pack_names(config):
        queue = []
        for scenario in ALL_SCENARIO_PACKS[pack]:
            s = dict(scenario)
            s.setdefault("pack", pack)
            for src, dst in _BRIEF_ALIASES.items():
                if src in s and dst not in s:
                    s[dst] = s[src]
            queue.append(s)
        queues.append(queue)

    ordered: list[dict] = []
    for i in range(max(len(q) for q in queues)):
        ordered.extend(q[i] for q in queues if i < len(q))
    return ordered


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
        from .round_manager import RoundManager

        tournament = get_or_create_active_tournament()
        tournament_id = tournament["id"]
        _db.update_tournament(tournament_id, status="active", started_at=_now())

        try:
            manager = RoundManager(tournament_id, ollama_url=self.ollama_url)
            scenarios = select_scenarios()
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
    cfg = _load_teams_config()
    packs = selected_pack_names(cfg)
    tournament = create_tournament(
        name=gm.tournament_name,
        scenario_pack=packs[0] if len(packs) == 1 else "mixed",
    )
    team_defs = [
        {"key": k, "name": v["name"], "domain": v["domain"], "color": v.get("color", "#6c6c80")}
        for k, v in cfg.get("teams", {}).items()
    ]
    seed_teams(tournament["id"], team_defs)
    return tournament
