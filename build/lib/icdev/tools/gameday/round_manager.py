# CUI // SP-CTI
"""AI GameDay League — round manager.

Runs all 4 teams in parallel threads with a 60-minute wall-clock timeout,
then hands results to the judge for scoring.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from datetime import datetime, timezone
from typing import Any

from .constants import ROUND_DURATION_MINUTES, TEAM_KEYS
from .db import (
    create_round,
    get_team,
    update_round,
)
from .team_runner import TeamRunner
from .judge_agent import JudgeAgent

log = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class RoundManager:
    """Coordinates one round across all 4 teams + judge scoring."""

    def __init__(self, tournament_id: int, ollama_url: str | None = None):
        self.tournament_id = tournament_id
        self.ollama_url    = ollama_url or os.environ.get(
            "OLLAMA_BASE_URL", "http://localhost:11434"
        )

    def run_round(self, round_num: int, scenario: dict) -> dict[str, Any]:
        """Execute one complete round: all teams + judge. Returns summary."""
        # Create round record
        round_row = create_round(self.tournament_id, round_num, scenario)
        round_id  = round_row["id"]
        update_round(round_id, status="active", started_at=_now())

        log.info(
            "[Round %d] Starting — scenario: %s — tournament %d",
            round_num, scenario.get("name", "?"), self.tournament_id
        )

        timeout_seconds = ROUND_DURATION_MINUTES * 60
        team_results: dict[str, dict] = {}

        # ── Run all 4 teams in parallel threads ───────────────────────────────
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="gd-team") as pool:
            futures = {
                pool.submit(
                    self._run_team, team_key, scenario, round_id
                ): team_key
                for team_key in TEAM_KEYS
            }

            for future in as_completed(futures, timeout=timeout_seconds):
                team_key = futures[future]
                try:
                    result = future.result(timeout=10)
                    team_results[team_key] = result
                    log.info("[Round %d] Team %s completed", round_num, team_key)
                except TimeoutError:
                    log.warning("[Round %d] Team %s timed out", round_num, team_key)
                    team_results[team_key] = {
                        "team_key": team_key,
                        "team_id":  self._get_team_id(team_key),
                        "error": "timed_out",
                        "orchestrator_output": {},
                        "artifacts": [],
                        "training_pairs": [],
                    }
                except Exception as exc:
                    log.error("[Round %d] Team %s error: %s", round_num, team_key, exc)
                    team_results[team_key] = {
                        "team_key": team_key,
                        "team_id":  self._get_team_id(team_key),
                        "error": str(exc),
                        "orchestrator_output": {},
                        "artifacts": [],
                        "training_pairs": [],
                    }

        # ── Save training pairs from team runners ─────────────────────────────
        all_training_pairs: list[dict] = []
        for tr in team_results.values():
            all_training_pairs.extend(tr.get("training_pairs", []))

        if all_training_pairs:
            self._save_training_pairs(all_training_pairs)

        # ── Judge scoring ─────────────────────────────────────────────────────
        judge = JudgeAgent(ollama_url=self.ollama_url)
        team_scores_map = {
            tk: {
                "team_id":            tr.get("team_id", 0),
                "orchestrator_output": tr.get("orchestrator_output", {}),
            }
            for tk, tr in team_results.items()
        }
        judge_result = judge.evaluate_round(
            round_id=round_id,
            tournament_id=self.tournament_id,
            team_scores_map=team_scores_map,
        )

        # ── Close round ───────────────────────────────────────────────────────
        update_round(round_id, status="completed", completed_at=_now())

        # ── Blockchain provenance ─────────────────────────────────────────────
        self._register_round_provenance(round_id, round_num, scenario, judge_result)

        log.info(
            "[Round %d] Completed — winner: %s",
            round_num, judge_result.get("round_winner", "?")
        )

        return {
            "round_id":       round_id,
            "round_num":      round_num,
            "scenario":       scenario,
            "team_results":   team_results,
            "judge_result":   judge_result,
            "training_pairs": len(all_training_pairs),
        }

    def _run_team(self, team_key: str, scenario: dict, round_id: int) -> dict:
        runner = TeamRunner(team_key)
        runner.ollama_url = self.ollama_url
        return runner.run_round(
            scenario=scenario,
            round_id=round_id,
            tournament_id=self.tournament_id,
        )

    def _get_team_id(self, team_key: str) -> int:
        team = get_team(self.tournament_id, team_key)
        return team["id"] if team else 0

    def _register_round_provenance(self, round_id: int, round_num: int, scenario: dict, judge_result: dict) -> None:
        """Register round results in source_citation_registry and anchor via ChainAnchor."""
        try:
            import hashlib
            import json
            from tools.provenance.registry import register_citation
            from tools.blockchain.chain_anchor import ChainAnchor

            payload = {
                "round_id": round_id,
                "round_num": round_num,
                "scenario": scenario.get("name", ""),
                "winner": judge_result.get("round_winner", ""),
                "tournament_id": self.tournament_id,
            }
            payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            source_hash = hashlib.sha256(payload_json.encode()).hexdigest()

            reg_id = register_citation(
                citation_type="canvas_ai",
                source_table="gd_ai_rounds",
                source_record_id=str(round_id),
                source_hash=source_hash,
                source_doc=f"gameday-round-{round_num}",
                project_id=f"tournament-{self.tournament_id}",
            )
            if reg_id:
                ChainAnchor().anchor_provenance([reg_id])
                log.info("[Round %d] Provenance registered: %s", round_num, reg_id)
        except Exception as exc:
            log.debug("[Round %d] Provenance registration skipped: %s", round_num, exc)

    def _save_training_pairs(self, pairs: list[dict]) -> None:
        from .db import save_training_pair
        for pair in pairs:
            try:
                save_training_pair(
                    round_id=pair["round_id"],
                    team_key=pair["team_key"],
                    member_role=pair["member_role"],
                    prompt=pair["prompt"],
                    completion=pair["completion"],
                    quality_score=0.7,
                )
            except Exception as exc:
                log.debug("Training pair save error: %s", exc)
