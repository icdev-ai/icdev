# CUI // SP-CTI
"""AI GameDay League — single-team round runner.

Loads team members from args/gameday_teams.yaml, runs each member
sequentially (passing prior output as context), then the orchestrator
synthesises the final team artifact.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import logging
import os
from pathlib import Path
from typing import Any

import yaml

from .base_agent import GameDayAgent
from .constants import (
    DEFAULT_AGENT_MODEL,
    MEMBER_TIME_BUDGET_MINUTES,
    ORCHESTRATOR_TIME_BUDGET_MINUTES,
    OLLAMA_BASE_URL,
)
from .db import save_artifact, get_team

log = get_logger(__name__)

_TEAMS_YAML = Path(__file__).parent.parent.parent / "args" / "gameday_teams.yaml"


def _load_team_config(team_key: str) -> dict:
    with open(_TEAMS_YAML, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    teams = cfg.get("teams", {})
    if team_key not in teams:
        raise ValueError(f"Unknown team_key: {team_key}")
    return teams[team_key]


def _build_prompt(team_key: str, member_role: str, scenario: dict) -> str:
    brief_key = {
        "red":   "attack_brief",
        "blue":  "defense_brief",
        "gold":  "innovation_brief",
        "green": "compliance_brief",
    }.get(team_key, "description")

    brief = scenario.get(brief_key, scenario.get("description", "No scenario brief available."))
    return (
        f"Scenario: {scenario.get('name', 'Unknown')}\n\n"
        f"{brief}\n\n"
        f"Respond with structured JSON as specified in your instructions."
    )


def _build_orchestrator_prompt(team_key: str, scenario: dict, member_outputs: list[dict]) -> str:
    brief_key = {
        "red":   "attack_brief",
        "blue":  "defense_brief",
        "gold":  "innovation_brief",
        "green": "compliance_brief",
    }.get(team_key, "description")

    brief = scenario.get(brief_key, scenario.get("description", ""))
    summaries = []
    for out in member_outputs:
        summaries.append({
            "role":   out["member_role"],
            "output": out["parsed"],
        })

    return (
        f"Scenario: {scenario.get('name', 'Unknown')}\n\n"
        f"Mission: {brief}\n\n"
        f"Your team members have completed their analysis. Synthesize their outputs:\n"
        f"{json.dumps(summaries, indent=2)}\n\n"
        f"Produce the final team artifact as structured JSON."
    )


class TeamRunner:
    """Runs one team through one round and returns all artifacts."""

    def __init__(self, team_key: str):
        self.team_key = team_key
        self.team_cfg  = _load_team_config(team_key)
        self.ollama_url = os.environ.get("OLLAMA_BASE_URL", OLLAMA_BASE_URL)

    def run_round(
        self,
        scenario: dict,
        round_id: int,
        tournament_id: int,
    ) -> dict[str, Any]:
        """Execute full round for this team. Returns dict with artifacts + score metadata."""
        team_row = get_team(tournament_id, self.team_key)
        if not team_row:
            return {"error": f"Team {self.team_key} not seeded for tournament {tournament_id}"}

        team_id = team_row["id"]
        members = self.team_cfg.get("members", [])

        member_outputs: list[dict] = []
        artifacts: list[dict] = []
        training_pairs: list[dict] = []

        # ── Run each non-orchestrator member ──────────────────────────────────
        for member_cfg in members:
            if member_cfg.get("role") == "orchestrator":
                continue

            agent = GameDayAgent(
                name=member_cfg.get("name", member_cfg["role"]),
                role=member_cfg["role"],
                team_key=self.team_key,
                specialty=member_cfg.get("specialty", ""),
                system_prompt=member_cfg.get("system_prompt", ""),
                model=member_cfg.get("model", DEFAULT_AGENT_MODEL),
                ollama_url=self.ollama_url,
                time_budget_seconds=MEMBER_TIME_BUDGET_MINUTES * 60,
            )

            # Build context from prior members
            context = {
                out["member_role"]: out["parsed"]
                for out in member_outputs
                if not out.get("error")
            } if member_outputs else None

            prompt = _build_prompt(self.team_key, member_cfg["role"], scenario)
            result = agent.run(
                prompt,
                context=context,
                tournament_id=tournament_id,
                round_id=round_id,
            )
            member_outputs.append(result)

            # Determine artifact type from role
            artifact_type = _role_to_artifact_type(self.team_key, member_cfg["role"])
            artifact_content = json.dumps(result["parsed"], ensure_ascii=False)

            saved = save_artifact(
                round_id=round_id,
                team_id=team_id,
                team_key=self.team_key,
                member_role=member_cfg["role"],
                artifact_type=artifact_type,
                content=artifact_content,
                tokens_used=result["tokens_used"],
                model_used=result["model"],
                latency_ms=result["latency_ms"],
            )
            artifacts.append(saved)

            # Extract training pairs if present
            pairs = result["parsed"].get("training_pairs", [])
            if isinstance(pairs, list):
                for pair in pairs:
                    if isinstance(pair, dict) and "prompt" in pair and "completion" in pair:
                        training_pairs.append({
                            "round_id":    round_id,
                            "team_key":    self.team_key,
                            "member_role": member_cfg["role"],
                            "prompt":      pair["prompt"],
                            "completion":  pair["completion"],
                        })

        # ── Run orchestrator ──────────────────────────────────────────────────
        orch_cfg = next(
            (m for m in members if m.get("role") == "orchestrator"), None
        )
        orchestrator_output: dict = {}
        if orch_cfg:
            orch_agent = GameDayAgent(
                name=orch_cfg.get("name", "Orchestrator"),
                role="orchestrator",
                team_key=self.team_key,
                specialty=orch_cfg.get("specialty", ""),
                system_prompt=orch_cfg.get("system_prompt", ""),
                model=orch_cfg.get("model", DEFAULT_AGENT_MODEL),
                ollama_url=self.ollama_url,
                time_budget_seconds=ORCHESTRATOR_TIME_BUDGET_MINUTES * 60,
            )
            orch_prompt = _build_orchestrator_prompt(
                self.team_key, scenario, member_outputs
            )
            orch_result = orch_agent.run(
                orch_prompt,
                tournament_id=tournament_id,
                round_id=round_id,
            )
            orchestrator_output = orch_result["parsed"]

            saved = save_artifact(
                round_id=round_id,
                team_id=team_id,
                team_key=self.team_key,
                member_role="orchestrator",
                artifact_type=_role_to_artifact_type(self.team_key, "orchestrator"),
                content=json.dumps(orchestrator_output, ensure_ascii=False),
                tokens_used=orch_result["tokens_used"],
                model_used=orch_result["model"],
                latency_ms=orch_result["latency_ms"],
            )
            artifacts.append(saved)

            # Orchestrator-level training pairs
            pairs = orchestrator_output.get("training_pairs", [])
            if isinstance(pairs, list):
                for pair in pairs:
                    if isinstance(pair, dict) and "prompt" in pair and "completion" in pair:
                        training_pairs.append({
                            "round_id":    round_id,
                            "team_key":    self.team_key,
                            "member_role": "orchestrator",
                            "prompt":      pair["prompt"],
                            "completion":  pair["completion"],
                        })

        return {
            "team_key":           self.team_key,
            "team_id":            team_id,
            "artifacts":          artifacts,
            "orchestrator_output": orchestrator_output,
            "member_outputs":     member_outputs,
            "training_pairs":     training_pairs,
        }


def _role_to_artifact_type(team_key: str, role: str) -> str:
    mapping = {
        ("red",   "scout"):             "recon_findings",
        ("red",   "threat_analyst"):    "ttp_analysis",
        ("red",   "exploit_engineer"):  "exploit_chain",
        ("red",   "orchestrator"):      "attack_plan",
        ("blue",  "soc_analyst"):       "threat_detection",
        ("blue",  "security_architect"):"countermeasures",
        ("blue",  "ir_responder"):      "ir_playbook",
        ("blue",  "orchestrator"):      "defense_posture",
        ("gold",  "researcher"):        "research_gaps",
        ("gold",  "builder"):           "module_code",
        ("gold",  "evaluator"):         "module_evaluation",
        ("gold",  "orchestrator"):      "innovation_package",
        ("green", "auditor"):           "nist_audit",
        ("green", "risk_assessor"):     "risk_assessment",
        ("green", "policy_advisor"):    "policy_review",
        ("green", "orchestrator"):      "compliance_verdict",
    }
    return mapping.get((team_key, role), "orchestrator_brief")
