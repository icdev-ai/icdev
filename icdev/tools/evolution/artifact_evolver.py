# CUI // SP-CTI
"""NOVA SELA — Artifact Evolver (GEPA-style text mutation orchestrator).

Evolves a skill file or hardprompt template using reflective text mutation:
  1. Load artifact (skill text)
  2. Build eval dataset from kanban history / golden / synthetic
  3. Generate N candidate mutations via LLM (GEPA-inspired: read failure traces
     to guide mutation direction rather than blind random search)
  4. Score each candidate on train+val split (fast heuristic)
  5. Constraint gates: coherence, SIPA size limits (≤15KB, growth ≤+20%)
  6. Score winner on holdout (expensive LLM judge)
  7. If winner beats baseline by MIN_IMPROVEMENT_DELTA → create oracle_prediction
     row for kanban suggested card (NEVER auto-merge skill files)

Inspired by NousResearch/hermes-agent-self-evolution evolve_skill.py.

Config: args/evolution_config.yaml
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

import yaml

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = BASE_DIR / ".agents" / "skills"
HARDPROMPTS_DIR = BASE_DIR / "hardprompts"
CONFIG_PATH = BASE_DIR / "args" / "nova_sela_config.yaml"

# Constraint limits (also defined in evolution_config.yaml; these are code defaults)
MAX_SKILL_BYTES = 15 * 1024          # 15 KB
MAX_GROWTH_RATIO = 0.20              # +20% max size increase
MIN_IMPROVEMENT_DELTA = 0.05         # composite score must improve by ≥5%
DEFAULT_MUTATION_COUNT = 4


def _load_config() -> dict:
    try:
        return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _conn():
    from tools.db.storage import get_connection
    return get_connection()


# ────────────────────────────────────────────────────────────────────────────
# Skill / hardprompt loading
# ────────────────────────────────────────────────────────────────────────────


def _find_skill_path(skill_name: str) -> Path | None:
    for ext in (".md", ".txt", ""):
        p = SKILLS_DIR / f"{skill_name}{ext}"
        if p.exists():
            return p
    for p in SKILLS_DIR.glob(f"*{skill_name}*"):
        if p.is_file():
            return p
    return None


def _find_hardprompt_path(prompt_name: str) -> Path | None:
    for ext in (".md", ".txt", ""):
        p = HARDPROMPTS_DIR / f"{prompt_name}{ext}"
        if p.exists():
            return p
    return None


def _load_artifact(artifact_name: str, artifact_type: str = "skill") -> tuple[str, Path | None]:
    """Load artifact text. Returns (text, path)."""
    if artifact_type == "skill":
        path = _find_skill_path(artifact_name)
    else:
        path = _find_hardprompt_path(artifact_name)

    if path and path.exists():
        return path.read_text(encoding="utf-8"), path
    return "", None


# ────────────────────────────────────────────────────────────────────────────
# Constraint gates (all-or-nothing — any failure rejects the candidate)
# ────────────────────────────────────────────────────────────────────────────


def _validate_candidate(
    candidate: str,
    baseline: str,
) -> tuple[bool, str]:
    """Return (passed, reason). All gates must pass."""
    # Gate 1: Not empty
    if not candidate or len(candidate.strip()) < 50:
        return False, "candidate is empty or too short"

    # Gate 2: Size limit
    candidate_bytes = len(candidate.encode("utf-8"))
    if candidate_bytes > MAX_SKILL_BYTES:
        return False, f"size {candidate_bytes} exceeds limit {MAX_SKILL_BYTES}"

    # Gate 3: Growth limit
    baseline_bytes = len(baseline.encode("utf-8"))
    if baseline_bytes > 0:
        growth = (candidate_bytes - baseline_bytes) / baseline_bytes
        if growth > MAX_GROWTH_RATIO:
            return False, f"growth {growth:.1%} exceeds limit {MAX_GROWTH_RATIO:.1%}"

    # Gate 4: Structural integrity — must still have a title/heading
    if not re.search(r"^#\s", candidate, re.MULTILINE):
        return False, "missing top-level markdown heading"

    return True, "ok"


# ────────────────────────────────────────────────────────────────────────────
# Mutation generation
# ────────────────────────────────────────────────────────────────────────────


def _generate_mutations(
    skill_name: str,
    baseline_text: str,
    failure_summary: str,
    n: int = DEFAULT_MUTATION_COUNT,
) -> list[str]:
    """Generate N candidate mutations of the skill text using LLM guidance."""
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        prompt = (
            f"You are a skill evolution agent for the ICDEV™ platform.\n\n"
            f"Skill name: {skill_name!r}\n\n"
            f"CURRENT SKILL TEXT:\n{baseline_text[:1500]}\n\n"
            f"RECENT FAILURE PATTERNS:\n{failure_summary[:800]}\n\n"
            f"Generate {n} improved versions of this skill. Each version should:\n"
            f"  1. Fix the failure patterns described above\n"
            f"  2. Keep the same overall structure and purpose\n"
            f"  3. Be clearer and more actionable\n"
            f"  4. NOT grow more than 20% in length\n"
            f"  5. Preserve the top-level # heading\n\n"
            f"Output as JSON array of {n} strings, each being a complete revised skill. "
            f"No explanations — just the JSON array."
        )
        router = LLMRouter()
        req = LLMRequest(messages=[{"role": "user", "content": prompt}], max_tokens=3000, temperature=0.6)
        resp = router.invoke("code_generation", req)
        if not resp or not resp.content:
            return []
        match = re.search(r"\[.*\]", resp.content, re.DOTALL)
        if not match:
            return []
        candidates = json.loads(match.group())
        return [c for c in candidates if isinstance(c, str)][:n]
    except Exception as exc:
        logger.warning("[evolver] mutation generation failed: %s", exc)
        return []


# ────────────────────────────────────────────────────────────────────────────
# Oracle prediction (kanban suggested card)
# ────────────────────────────────────────────────────────────────────────────


def _create_evolution_suggestion(
    skill_name: str,
    artifact_type: str,
    winner_text: str,
    baseline_score: float,
    winner_score: float,
) -> str | None:
    """Create an oracle_predictions row for the evolved artifact."""
    try:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        pred_id = f"nova-sela-{uuid.uuid4().hex[:10]}"
        conn = _conn()
        conn.execute(
            """
            INSERT INTO oracle_predictions
                (id, prediction_type, target, confidence, rationale,
                 suggested_action, status, created_at, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s, %s)
            """,
            (
                pred_id,
                "gap::skill_evolution",
                f"{artifact_type}:{skill_name}",
                round(winner_score, 3),
                (
                    f"SELA evolved {artifact_type} {skill_name!r}. "
                    f"Baseline score: {baseline_score:.3f} → Winner score: {winner_score:.3f} "
                    f"(+{winner_score - baseline_score:.3f}). "
                    "Candidate has passed all constraint gates (size, growth, structure)."
                ),
                (
                    f"Review evolved {artifact_type} in oracle_predictions.id={pred_id} and, "
                    f"if approved, replace .agents/skills/{skill_name} with the candidate text. "
                    "NEVER auto-apply — human review required."
                ),
                now,
                now,
            ),
        )
        conn.commit()
        logger.info("[evolver] created suggestion %s for %s", pred_id, skill_name)
        return pred_id
    except Exception as exc:
        logger.warning("[evolver] failed to create suggestion: %s", exc)
        return None


# ────────────────────────────────────────────────────────────────────────────
# Main evolution loop
# ────────────────────────────────────────────────────────────────────────────


def evolve_artifact(
    artifact_name: str,
    artifact_type: str = "skill",
    dry_run: bool = False,
    mutation_count: int | None = None,
) -> dict[str, Any]:
    """
    Run the SELA evolution loop for a single skill or hardprompt.

    Returns a result dict with baseline_score, winner_score, improvement, etc.
    """
    cfg = _load_config()
    n_mutations = mutation_count or cfg.get("mutation_count", DEFAULT_MUTATION_COUNT)

    # 1. Load artifact
    baseline_text, artifact_path = _load_artifact(artifact_name, artifact_type)
    if not baseline_text:
        return {"error": f"{artifact_type} {artifact_name!r} not found"}

    # 2. Build eval dataset
    from tools.evolution.eval_builder import build_dataset
    dataset = build_dataset(artifact_name, skill_text=baseline_text)
    if not dataset.is_sufficient():
        return {
            "skipped": True,
            "reason": f"insufficient eval examples ({len(dataset.all_examples)})",
        }

    # 3. Score baseline
    from tools.evolution.fitness import score_examples
    baseline_score, _ = score_examples(dataset.train + dataset.val, baseline_text, mode="fast")

    # 4. Build failure summary for GEPA-style reflective mutation
    from tools.workflow.trace_logger import get_traces_for_task_type
    traces = get_traces_for_task_type(artifact_name, limit=20)
    failures = [t for t in traces if t.get("outcome") not in ("success",)]
    failure_summary = "\n".join(
        f"- {t.get('lesson_pattern','unknown')}: {t.get('improvement_notes','')[:100]}"
        for t in failures[:8]
    ) or "No failure traces available."

    # 5. Generate mutation candidates
    candidates = _generate_mutations(artifact_name, baseline_text, failure_summary, n=n_mutations)
    if not candidates:
        return {"skipped": True, "reason": "LLM mutation generation returned no candidates"}

    # 6. Validate + score each candidate on train+val (fast heuristic)
    valid_candidates: list[tuple[float, str]] = []
    for i, candidate in enumerate(candidates):
        passed, reason = _validate_candidate(candidate, baseline_text)
        if not passed:
            logger.debug("[evolver] candidate %d rejected: %s", i, reason)
            continue
        score, _ = score_examples(dataset.train + dataset.val, candidate, mode="fast")
        valid_candidates.append((score, candidate))

    if not valid_candidates:
        return {"skipped": True, "reason": "all candidates failed constraint gates"}

    # 7. Pick best candidate by train+val score
    valid_candidates.sort(key=lambda x: x[0], reverse=True)
    winner_trainval_score, winner_text = valid_candidates[0]

    # 8. Score winner on holdout (expensive LLM judge)
    winner_holdout_score, _ = score_examples(dataset.holdout, winner_text, mode="full")

    improvement = winner_holdout_score - baseline_score

    # 9. Promote if improvement exceeds threshold
    suggestion_id = None
    if improvement >= MIN_IMPROVEMENT_DELTA and not dry_run:
        suggestion_id = _create_evolution_suggestion(
            artifact_name, artifact_type, winner_text, baseline_score, winner_holdout_score
        )

    logger.info(
        "[evolver] %s %s: baseline=%.3f winner=%.3f improvement=%+.3f %s",
        artifact_type, artifact_name, baseline_score, winner_holdout_score, improvement,
        "(promoted)" if suggestion_id else "(not promoted)",
    )

    return {
        "artifact_name": artifact_name,
        "artifact_type": artifact_type,
        "baseline_score": baseline_score,
        "winner_score": winner_holdout_score,
        "improvement": round(improvement, 4),
        "candidates_generated": len(candidates),
        "candidates_valid": len(valid_candidates),
        "promoted": suggestion_id is not None,
        "suggestion_id": suggestion_id,
        "dry_run": dry_run,
    }


def evolve_all_skills(dry_run: bool = False, limit: int = 10) -> dict[str, Any]:
    """Evolve all icdev-* skills up to limit."""
    results = {}
    for path in sorted(SKILLS_DIR.glob("icdev-*.md"))[:limit]:
        name = path.stem
        results[name] = evolve_artifact(name, artifact_type="skill", dry_run=dry_run)
    return {"skills_processed": len(results), "results": results}
