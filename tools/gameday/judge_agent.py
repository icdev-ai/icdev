# CUI // SP-CTI
"""AI GameDay League — Judge Agent.

Uses gemma4:e4b (independent model, different from team agents) to:
1. Score each team's orchestrator artifact on 5 dimensions
2. Apply ethics gate (block below threshold, flag)
3. Route high-quality artifacts to SUGGESTED kanban
4. Extract training pairs above quality threshold
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import time
from typing import Any

from .constants import (
    ETHICS_BLOCK_THRESHOLD,
    GAMEDAY_JUDGE_LLM_FUNCTION,
    JUDGE_MODEL,
    OLLAMA_BASE_URL,
    SCORING_WEIGHTS,
    SUGGESTED_THRESHOLD,
    TRAINING_PAIR_THRESHOLD,
)
from .db import (
    log_llmops_event,
    save_judge_eval,
    save_training_pair,
    update_team_scores,
)

log = get_logger(__name__)

_JUDGE_SYSTEM = """\
You are an independent AI judge for a cyber security GameDay competition.
Evaluate each team's artifact honestly and impartially.
You use a DIFFERENT model from the competing teams — your role is to provide independent perspective.

Scoring dimensions (each 0.0–1.0):
- quality_score: correctness, completeness, clarity of the artifact
- innovation_score: novelty, creativity, reusable techniques
- ethics_score: adherence to ethical AI, no real destructive content (1.0 = fully compliant)
- adversarial_score: for Red/Blue: effectiveness of attack or defense; for Gold: capability enhancement; for Green: rigor of compliance
- compliance_score: NIST/DoD policy alignment, classification markings, responsible AI

Output ONLY valid JSON:
{
  "quality_score": 0.0-1.0,
  "innovation_score": 0.0-1.0,
  "ethics_score": 0.0-1.0,
  "adversarial_score": 0.0-1.0,
  "compliance_score": 0.0-1.0,
  "judge_notes": "brief explanation",
  "training_pairs": [
    {"prompt": "...", "completion": "..."}
  ]
}
Training pairs should capture generalizable knowledge from this artifact (aim for 2-5 pairs).
"""


class JudgeAgent:
    """Independent judge using gemma4:e4b."""

    def __init__(self, ollama_url: str = OLLAMA_BASE_URL):
        self.model = JUDGE_MODEL
        self.ollama_url = ollama_url.rstrip("/")

    def evaluate_round(
        self,
        round_id: int,
        tournament_id: int,
        team_scores_map: dict[str, dict],  # team_key → {team_id, orchestrator_output}
    ) -> dict[str, Any]:
        """Score all teams for one round. Returns per-team evaluations."""
        results = {}

        for team_key, team_data in team_scores_map.items():
            team_id = team_data["team_id"]
            artifact_content = team_data.get("orchestrator_output", {})

            eval_result = self._score_artifact(
                team_key=team_key,
                artifact=artifact_content,
                tournament_id=tournament_id,
                round_id=round_id,
            )

            # Compute total score using scoring weights
            total_score = _compute_total_score(eval_result)

            # Ethics gate
            ethics_blocked = 0
            if eval_result["ethics_score"] < ETHICS_BLOCK_THRESHOLD:
                ethics_blocked = 1
                log.warning(
                    "[Judge] Team %s BLOCKED — ethics_score=%.2f < %.2f",
                    team_key, eval_result["ethics_score"], ETHICS_BLOCK_THRESHOLD
                )
                total_score = 0  # Blocked teams score zero

            # Determine SUGGESTED routing
            overall_quality = (
                eval_result["quality_score"] + eval_result["innovation_score"]
            ) / 2.0
            routed_to_suggested = 1 if (
                not ethics_blocked and overall_quality >= SUGGESTED_THRESHOLD
            ) else 0

            if routed_to_suggested:
                _route_to_suggested_kanban(team_key, round_id, artifact_content, eval_result)

            # Save training pairs that meet quality threshold
            pairs = eval_result.get("training_pairs", [])
            pairs_saved = 0
            for pair in pairs:
                if not isinstance(pair, dict):
                    continue
                if pair.get("prompt") and pair.get("completion"):
                    quality = eval_result["quality_score"]
                    if quality >= TRAINING_PAIR_THRESHOLD:
                        save_training_pair(
                            round_id=round_id,
                            team_key=team_key,
                            member_role="judge_extracted",
                            prompt=pair["prompt"],
                            completion=pair["completion"],
                            quality_score=quality,
                        )
                        pairs_saved += 1

            # Persist eval
            eval_row = save_judge_eval(
                round_id=round_id,
                team_id=team_id,
                team_key=team_key,
                quality_score=eval_result["quality_score"],
                innovation_score=eval_result["innovation_score"],
                ethics_score=eval_result["ethics_score"],
                adversarial_score=eval_result["adversarial_score"],
                compliance_score=eval_result["compliance_score"],
                total_score=total_score,
                routed_to_suggested=routed_to_suggested,
                training_pairs_extracted=pairs_saved,
                ethics_blocked=ethics_blocked,
                judge_notes=eval_result.get("judge_notes", ""),
            )

            # Update team aggregate scores
            update_team_scores(
                tournament_id=tournament_id,
                team_key=team_key,
                score_delta=total_score,
                artifacts_suggested_delta=routed_to_suggested,
                training_pairs_delta=pairs_saved,
            )

            results[team_key] = {
                "eval":              eval_row,
                "total_score":       total_score,
                "ethics_blocked":    bool(ethics_blocked),
                "routed_suggested":  bool(routed_to_suggested),
                "training_pairs":    pairs_saved,
            }

        # Determine round winner (highest score, not blocked)
        winner = max(
            (k for k, v in results.items() if not v["ethics_blocked"]),
            key=lambda k: results[k]["total_score"],
            default=None,
        )
        if winner:
            update_team_scores(tournament_id=tournament_id, team_key=winner, rounds_won_delta=1)

        return {"results": results, "round_winner": winner}

    def _score_artifact(
        self,
        team_key: str,
        artifact: dict,
        tournament_id: int,
        round_id: int | None,
        scenario: dict | None = None,
    ) -> dict:
        # Prefer adversarial 3-lens panel when scenario context is available
        if scenario:
            try:
                panel = judge_with_adversarial_panel(
                    artifact={"artifact_type": _role_to_artifact_type_hint(team_key), "content": artifact},
                    scenario=scenario,
                )
                normalized = panel["final_score"] / 100.0
                return {
                    "quality_score":     min(1.0, normalized * 1.1),
                    "innovation_score":  normalized,
                    "ethics_score":      1.0,
                    "adversarial_score": normalized,
                    "compliance_score":  min(1.0, normalized * 1.05),
                    "judge_notes": panel["reasoning"],
                    "training_pairs": [],
                    "_panel": panel,
                }
            except Exception as exc:
                log.debug("Adversarial panel failed, falling back to single-lens: %s", exc)

        prompt = (
            f"Team: {team_key.upper()}\n\n"
            f"Artifact to evaluate:\n{json.dumps(artifact, indent=2)}\n\n"
            f"Score this artifact and extract training pairs."
        )

        t0 = time.time()
        try:
            from tools.llm.router import LLMRouter
            from tools.llm.provider import LLMRequest

            router = LLMRouter()
            provider, model_id, cfg = router.get_provider_for_function(GAMEDAY_JUDGE_LLM_FUNCTION)
            if provider is None:
                return _default_scores()

            req = LLMRequest(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=_JUDGE_SYSTEM,
                max_tokens=1024,
                temperature=0.3,
                skip_injection_scan=True,
            )
            resp = provider.invoke(req, model_id, cfg)
            latency_ms = int((time.time() - t0) * 1000)
            content = getattr(resp, "content", "") or ""

            log_llmops_event(
                tournament_id=tournament_id,
                round_id=round_id,
                team_key="judge",
                member_role="judge",
                model=(self.model or getattr(resp, "model_id", None) or model_id or "router"),
                prompt_tokens=int(getattr(resp, "input_tokens", 0) or 0),
                completion_tokens=int(getattr(resp, "output_tokens", 0) or 0),
                latency_ms=latency_ms,
            )

            return _parse_judge_response(content)

        except Exception as exc:
            log.warning("[Judge] scoring failed for %s: %s", team_key, exc)
            return _default_scores()


# ── Adversarial 3-Lens Panel ──────────────────────────────────────────────────

def judge_with_adversarial_panel(artifact: dict, scenario: dict) -> dict:
    """Run 3 independent adversarial lenses on a team artifact.

    Returns dict with: final_score, lens_scores, consensus, reasoning.
    At least 2/3 lenses must agree on a score within ±10 points for consensus.
    If no consensus: average the 3 scores with a -5 uncertainty penalty.
    """
    lenses = [
        {
            "name": "correctness",
            "prompt": (
                f"You are an adversarial judge. Your job is to FIND FLAWS in this artifact.\n"
                f"Scenario: {scenario.get('name')}\n"
                f"Artifact type: {artifact.get('artifact_type')}\n"
                f"Artifact content: {str(artifact.get('content', ''))[:800]}\n\n"
                "Score 0-100 for CORRECTNESS only. Be harsh. Find every factual error, "
                "logical gap, and missing element. Return JSON: {\"score\": N, \"issues\": [...]}"
            ),
        },
        {
            "name": "completeness",
            "prompt": (
                f"You are an adversarial judge. Your job is to FIND GAPS in this artifact.\n"
                f"Scenario: {scenario.get('name')}\n"
                f"Artifact type: {artifact.get('artifact_type')}\n"
                f"Artifact content: {str(artifact.get('content', ''))[:800]}\n\n"
                "Score 0-100 for COMPLETENESS only. What required elements are missing? "
                "What questions does this raise but not answer? "
                "Return JSON: {\"score\": N, \"missing\": [...]}"
            ),
        },
        {
            "name": "compliance",
            "prompt": (
                f"You are an adversarial judge. Your job is to FIND COMPLIANCE GAPS.\n"
                f"Scenario: {scenario.get('name')}\n"
                f"Artifact type: {artifact.get('artifact_type')}\n"
                f"Artifact content: {str(artifact.get('content', ''))[:800]}\n\n"
                "Score 0-100 for REGULATORY/DOCTRINE COMPLIANCE only. Does this follow "
                "NIST 800-53, DoD doctrine, AADC requirements? What violations exist? "
                "Return JSON: {\"score\": N, \"violations\": [...]}"
            ),
        },
    ]

    lens_results = []
    for lens in lenses:
        try:
            score, details = _run_lens(lens["prompt"])
            lens_results.append({"lens": lens["name"], "score": score, "details": details})
        except Exception as exc:
            log.warning("Lens %s failed: %s", lens["name"], exc)
            lens_results.append({"lens": lens["name"], "score": 50, "details": {}})

    scores = [r["score"] for r in lens_results]
    avg_score = sum(scores) / len(scores)

    # Check consensus: at least 2 scores within ±10 of each other
    consensus = False
    for i in range(len(scores)):
        for j in range(i + 1, len(scores)):
            if abs(scores[i] - scores[j]) <= 10:
                consensus = True
                break

    final_score = avg_score if consensus else max(0, avg_score - 5)

    return {
        "final_score": round(final_score, 1),
        "lens_scores": lens_results,
        "consensus": consensus,
        "reasoning": (
            f"3-lens adversarial panel: "
            f"correctness={scores[0]}, completeness={scores[1]}, compliance={scores[2]}"
        ),
    }


def _run_lens(prompt: str) -> tuple[float, dict]:
    """Run a single adversarial lens via LLM router. Returns (score, details)."""
    import json as _json
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
        router = LLMRouter()
        provider, model_id, cfg = router.get_provider_for_function("chat")
        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
            temperature=0.3,
            skip_injection_scan=True,
        )
        resp = provider.invoke(req, model_id, cfg)
        content = (resp.content or "").strip()
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            data = _json.loads(content[start:end])
            score = float(data.get("score", 50))
            score = max(0.0, min(100.0, score))
            return score, {k: v for k, v in data.items() if k != "score"}
    except Exception:
        pass
    return 50.0, {}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_judge_response(content: str) -> dict:
    content = content.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        inner, in_fence = [], False
        for line in lines:
            if line.startswith("```") and not in_fence:
                in_fence = True
                continue
            if line.startswith("```") and in_fence:
                break
            if in_fence:
                inner.append(line)
        content = "\n".join(inner)

    start, end = content.find("{"), content.rfind("}")
    if start != -1 and end != -1:
        try:
            data = json.loads(content[start:end + 1])
            return {
                "quality_score":     float(data.get("quality_score",     0.5)),
                "innovation_score":  float(data.get("innovation_score",  0.5)),
                "ethics_score":      float(data.get("ethics_score",      1.0)),
                "adversarial_score": float(data.get("adversarial_score", 0.5)),
                "compliance_score":  float(data.get("compliance_score",  0.5)),
                "judge_notes":       str(data.get("judge_notes", "")),
                "training_pairs":    data.get("training_pairs", []),
            }
        except (json.JSONDecodeError, ValueError):
            pass
    return _default_scores()


def _default_scores() -> dict:
    return {
        "quality_score":     0.5,
        "innovation_score":  0.5,
        "ethics_score":      1.0,
        "adversarial_score": 0.5,
        "compliance_score":  0.5,
        "judge_notes":       "Default scores — judge unavailable",
        "training_pairs":    [],
    }


def _compute_total_score(scores: dict) -> int:
    """Convert 0-1 dimension scores to integer total using SCORING_WEIGHTS."""
    adversarial_eff = (scores["adversarial_score"] + scores["quality_score"]) / 2.0
    raw = (
        adversarial_eff * SCORING_WEIGHTS["adversarial_effectiveness"] +
        scores["innovation_score"] * SCORING_WEIGHTS["innovation_score"] +
        scores["compliance_score"] * SCORING_WEIGHTS["compliance_score"] +
        scores["ethics_score"]     * SCORING_WEIGHTS["training_quality"]
    )
    return max(0, int(round(raw)))


def _role_to_artifact_type_hint(team_key: str) -> str:
    return {
        "red": "attack_plan", "blue": "defense_posture",
        "gold": "innovation_package", "green": "compliance_verdict",
    }.get(team_key, "orchestrator_brief")


def _route_to_suggested_kanban(
    team_key: str,
    round_id: int,
    artifact: dict,
    scores: dict,
) -> None:
    """Push a high-quality artifact to the SUGGESTED kanban column."""
    try:
        from tools.db.storage import get_connection
        from datetime import datetime, timezone

        title = f"GameDay {team_key.upper()} — high-quality artifact (round {round_id})"
        description = (
            f"Auto-routed from AI GameDay Judge. "
            f"Quality={scores['quality_score']:.2f} "
            f"Innovation={scores['innovation_score']:.2f} "
            f"Ethics={scores['ethics_score']:.2f}\n\n"
            f"Artifact excerpt:\n{json.dumps(artifact, indent=2)[:1000]}"
        )
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn = get_connection()
        conn.execute(
            """INSERT INTO kanban_tasks
               (title, description, status, priority, task_type, created_at, updated_at)
               VALUES (%s, %s, 'suggested', 'medium', 'gameday_artifact', %s, %s)""",
            (title, description, now, now),
        )
        conn.commit()
        log.info("[Judge] Routed %s artifact to SUGGESTED kanban", team_key)
    except Exception as exc:
        log.warning("[Judge] SUGGESTED routing failed: %s", exc)
