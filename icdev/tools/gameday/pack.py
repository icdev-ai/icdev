# CUI // SP-CTI
"""GameDay League — CyberAdversarialPack.

Plugs the AI GameDay League (Red/Blue/Gold/Green teams, cyber scenarios)
into the generic AI Game Engine.  This is the ONLY game-specific file that
needs to be written for each new game pack.

To build a new game:
  1. Create tools/<your_game>/pack.py
  2. Subclass GamePack, implement the 4 abstract methods
  3. Decorate with @register_pack("your_game_key")
  4. Done — GameSession("your_game_key") runs it
"""

from __future__ import annotations

from pathlib import Path
import yaml

from tools.ai_game_engine.game_pack import GamePack, TeamConfig, TeamMemberConfig, JudgeConfig
from tools.ai_game_engine.registry import register_pack
from tools.ai_game_engine.timing import TimingConfig
from .constants import (
    CYBER_SCENARIOS,
    DEFAULT_AGENT_MODEL,
    JUDGE_MODEL,
    ETHICS_BLOCK_THRESHOLD,
    SUGGESTED_THRESHOLD,
    TRAINING_PAIR_THRESHOLD,
    SCORING_WEIGHTS,
)

_TEAMS_YAML = Path(__file__).parent.parent.parent / "args" / "gameday_teams.yaml"

_JUDGE_SYSTEM = """\
You are an independent AI judge for a cyber security GameDay competition.
Evaluate each team's artifact honestly and impartially.
You use a DIFFERENT model from the competing teams — your role is to provide independent perspective.

Scoring dimensions (each 0.0–1.0):
- quality_score: correctness, completeness, clarity
- innovation_score: novelty, creativity, reusable techniques
- ethics_score: ethical AI compliance, no real destructive content (1.0 = fully compliant)
- adversarial_score: attack/defense effectiveness or capability enhancement
- compliance_score: NIST/DoD policy alignment, classification markings

Output ONLY valid JSON:
{
  "quality_score": 0.0-1.0,
  "innovation_score": 0.0-1.0,
  "ethics_score": 0.0-1.0,
  "adversarial_score": 0.0-1.0,
  "compliance_score": 0.0-1.0,
  "judge_notes": "brief explanation",
  "training_pairs": [{"prompt": "...", "completion": "..."}]
}
"""

_ARTIFACT_MAP = {
    ("red",   "scout"):              "recon_findings",
    ("red",   "threat_analyst"):     "ttp_analysis",
    ("red",   "exploit_engineer"):   "exploit_chain",
    ("red",   "orchestrator"):       "attack_plan",
    ("blue",  "soc_analyst"):        "threat_detection",
    ("blue",  "security_architect"): "countermeasures",
    ("blue",  "ir_responder"):       "ir_playbook",
    ("blue",  "orchestrator"):       "defense_posture",
    ("gold",  "researcher"):         "research_gaps",
    ("gold",  "builder"):            "module_code",
    ("gold",  "evaluator"):          "module_evaluation",
    ("gold",  "orchestrator"):       "innovation_package",
    ("green", "auditor"):            "nist_audit",
    ("green", "risk_assessor"):      "risk_assessment",
    ("green", "policy_advisor"):     "policy_review",
    ("green", "orchestrator"):       "compliance_verdict",
}


@register_pack("gameday")
class CyberAdversarialPack(GamePack):
    """AI GameDay League — 4-team cyber adversarial competition."""

    @property
    def game_key(self) -> str:
        return "gameday"

    @property
    def display_name(self) -> str:
        return "AI GameDay League"

    @property
    def description(self) -> str:
        return "Autonomous Red/Blue/Gold/Green teams compete in cyber adversarial scenarios."

    @property
    def timing(self) -> TimingConfig:
        return TimingConfig(
            round_duration_minutes=60,
            member_time_budget_minutes=8,
            orchestrator_time_budget_minutes=6,
            model_train_trigger_pairs=20,
            genesis_cooldown_seconds=86400,
        )

    def get_teams(self) -> list[TeamConfig]:
        with open(_TEAMS_YAML, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
        teams = []
        for key, td in cfg.get("teams", {}).items():
            members = []
            for md in td.get("members", []):
                members.append(TeamMemberConfig(
                    role=md["role"],
                    name=md.get("name", md["role"]),
                    specialty=md.get("specialty", ""),
                    system_prompt=md.get("system_prompt", ""),
                    model=md.get("model", DEFAULT_AGENT_MODEL),
                    is_orchestrator=(md["role"] == "orchestrator"),
                    time_budget_seconds=(
                        cfg.get("round", {}).get("orchestrator_time_budget_minutes", 6) * 60
                        if md["role"] == "orchestrator"
                        else cfg.get("round", {}).get("member_time_budget_minutes", 8) * 60
                    ),
                ))
            teams.append(TeamConfig(
                key=key,
                name=td["name"],
                domain=td["domain"],
                color=td.get("color", "#6c6c80"),
                role=td.get("role", key),
                members=members,
                scenario_brief_key={
                    "red": "attack_brief", "blue": "defense_brief",
                    "gold": "innovation_brief", "green": "compliance_brief",
                }.get(key, "description"),
            ))
        return teams

    def get_scenarios(self) -> list[dict]:
        return CYBER_SCENARIOS

    def get_judge_config(self) -> JudgeConfig:
        return JudgeConfig(
            model=JUDGE_MODEL,
            system_prompt=_JUDGE_SYSTEM,
            scoring_dimensions=[
                "quality_score", "innovation_score", "ethics_score",
                "adversarial_score", "compliance_score",
            ],
            scoring_weights={
                "quality_score":     SCORING_WEIGHTS["adversarial_effectiveness"] / 2,
                "adversarial_score": SCORING_WEIGHTS["adversarial_effectiveness"] / 2,
                "innovation_score":  SCORING_WEIGHTS["innovation_score"],
                "compliance_score":  SCORING_WEIGHTS["compliance_score"],
                "ethics_score":      SCORING_WEIGHTS["training_quality"],
            },
            ethics_block_threshold=ETHICS_BLOCK_THRESHOLD,
            suggested_threshold=SUGGESTED_THRESHOLD,
            training_pair_threshold=TRAINING_PAIR_THRESHOLD,
        )

    def get_artifact_type(self, team_key: str, member_role: str) -> str:
        return _ARTIFACT_MAP.get((team_key, member_role), f"{team_key}_{member_role}")

    def compute_total_score(self, dimension_scores: dict) -> int:
        weights = {
            "adversarial_score": SCORING_WEIGHTS["adversarial_effectiveness"] / 2,
            "quality_score":     SCORING_WEIGHTS["adversarial_effectiveness"] / 2,
            "innovation_score":  SCORING_WEIGHTS["innovation_score"],
            "compliance_score":  SCORING_WEIGHTS["compliance_score"],
            "ethics_score":      SCORING_WEIGHTS["training_quality"],
        }
        total = sum(
            dimension_scores.get(dim, 0.0) * weight
            for dim, weight in weights.items()
        )
        return max(0, int(round(total)))
