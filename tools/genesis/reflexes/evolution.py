# CUI // SP-CTI
"""NOVA SELA — Skill Evolution via Lamarckian Acquisition.

Weekly reflex that:
  1. Queries agent_execution_traces for the last 7 days grouped by skill_used.
  2. For each skill with success_rate < 0.75, generates 4 candidate mutations
     via LLMRouter.invoke('skill_mutation', ...).
  3. Scores each mutation using the fitness function from nova_sela_config.yaml
     (0.5 * correctness + 0.3 * procedure + 0.2 * conciseness).
  4. Inserts the top-scoring mutation into agent_improvement_artifacts
     (status='pending') if composite_score >= 0.75.

Config: args/nova_sela_config.yaml
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
from tools.logging.icdev_logger import get_logger
log = get_logger("icdev.nova.sela")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_sela_config() -> Dict[str, Any]:
    config_path = BASE_DIR / "args" / "nova_sela_config.yaml"
    try:
        import yaml  # type: ignore
        with open(config_path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return raw.get("sela", {})
    except Exception as exc:
        log.warning("[sela] could not load config: %s — using defaults", exc)
        return {}


def _query_low_performing_skills(
    conn,
    window_days: int,
    success_rate_threshold: float,
    min_trace_count: int,
    skill_limit: int,
) -> List[Dict[str, Any]]:
    """Return skills with success_rate < threshold over the last window_days.

    Returns a list of dicts: {skill_used, total, successes, success_rate}.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat(timespec="seconds")
    try:
        rows = conn.execute(
            """
            SELECT
                skill_used,
                COUNT(*) AS total,
                SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) AS successes
            FROM agent_execution_traces
            WHERE created_at >= %s
              AND skill_used != ''
            GROUP BY skill_used
            HAVING COUNT(*) >= %s
            ORDER BY (SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) * 1.0 / COUNT(*)) ASC
            LIMIT %s
            """,
            (cutoff, min_trace_count, skill_limit),
        ).fetchall()
    except Exception as exc:
        log.warning("[sela] trace query failed: %s", exc)
        return []

    skills = []
    for row in rows:
        if hasattr(row, "keys"):
            skill, total, successes = row["skill_used"], row["total"], row["successes"]
        else:
            skill, total, successes = row[0], row[1], row[2]
        total = int(total or 0)
        successes = int(successes or 0)
        rate = successes / total if total > 0 else 0.0
        if rate < success_rate_threshold:
            skills.append(
                {
                    "skill_used": skill,
                    "total": total,
                    "successes": successes,
                    "success_rate": round(rate, 4),
                }
            )
    return skills


def _get_failure_examples(conn, skill: str, window_days: int, limit: int = 5) -> List[str]:
    """Fetch lesson_pattern + improvement_notes from recent failed traces."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat(timespec="seconds")
    try:
        rows = conn.execute(
            """
            SELECT lesson_pattern, improvement_notes
            FROM agent_execution_traces
            WHERE skill_used = %s
              AND outcome != 'success'
              AND created_at >= %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (skill, cutoff, limit),
        ).fetchall()
    except Exception:
        return []

    examples = []
    for row in rows:
        if hasattr(row, "keys"):
            pattern, notes = row["lesson_pattern"], row["improvement_notes"]
        else:
            pattern, notes = row[0], row[1]
        parts = []
        if pattern:
            parts.append(f"pattern: {pattern}")
        if notes:
            parts.append(f"notes: {notes}")
        if parts:
            examples.append("; ".join(parts))
    return examples


def _generate_mutations(
    skill: str,
    skill_info: Dict[str, Any],
    failure_examples: List[str],
    mutation_count: int,
    mutation_function: str,
) -> List[str]:
    """Call LLMRouter to generate `mutation_count` candidate skill mutations.

    Returns a list of mutation strings (may be shorter than mutation_count on
    LLM errors; never raises).
    """
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
    except ImportError as exc:
        log.warning("[sela] LLM imports unavailable: %s", exc)
        return []

    examples_block = ""
    if failure_examples:
        examples_block = "\nRecent failure patterns:\n" + "\n".join(
            f"  - {ex}" for ex in failure_examples[:5]
        )

    prompt = (
        f"You are a skill improvement specialist for an AI agent system.\n\n"
        f"Skill under review: '{skill}'\n"
        f"Current success rate: {skill_info['success_rate']:.1%} "
        f"({skill_info['successes']}/{skill_info['total']} traces)\n"
        f"{examples_block}\n\n"
        f"Generate exactly {mutation_count} distinct candidate improvements to this skill.\n"
        f"Each mutation should address a likely root cause of failure.\n\n"
        f"Respond with a JSON array of {mutation_count} strings, each describing one mutation:\n"
        f'["mutation 1 description", "mutation 2 description", ...]'
    )

    try:
        router = LLMRouter()
        response = router.invoke(
            mutation_function,
            LLMRequest(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1024,
                project_id="nova_sela",
                skip_injection_scan=True,
            ),
        )
        content = (response.content or "").strip()
        start = content.find("[")
        end = content.rfind("]") + 1
        if start >= 0 and end > start:
            candidates = json.loads(content[start:end])
            if isinstance(candidates, list):
                return [str(c) for c in candidates[:mutation_count]]
    except Exception as exc:
        log.warning("[sela] LLM mutation generation failed for skill '%s': %s", skill, exc)

    return []


def _score_mutation(
    mutation: str,
    skill: str,
    fitness_weights: Dict[str, float],
) -> Dict[str, Any]:
    """Score a mutation text deterministically.

    Delegates to the shared rubric in tools/workflow/improvement_fitness.py so
    SELA and the Reflexion agent produce comparable composite_score values —
    GEPA compares them against the same _MIN_COMPOSITE_SCORE floor.

    Returns {"mutation": str, "correctness": float, "procedure": float,
             "conciseness": float, "composite_score": float}.
    """
    from tools.workflow.improvement_fitness import score_improvement

    return score_improvement(mutation, skill, fitness_weights)


def _insert_artifact(conn, skill: str, best: Dict[str, Any], baseline_score: float) -> str:
    """Insert the top mutation into agent_improvement_artifacts.

    Returns the new artifact_id.
    """
    artifact_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO agent_improvement_artifacts
            (artifact_id, task_type, skill_used, generation_n,
             improvement_text, composite_score, baseline_score,
             evidence_traces, applied_count, status, created_at)
        VALUES (%s, %s, %s, 1, %s, %s, %s, '[]', 0, 'pending', %s)
        """,
        (
            artifact_id,
            "sela_mutation",
            skill,
            best["mutation"],
            best["composite_score"],
            round(baseline_score, 4),
            _utcnow(),
        ),
    )
    conn.commit()
    return artifact_id


def run_evolution(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Execute one SELA evolution pass.

    Args:
        config: Optional override dict (merged on top of nova_sela_config.yaml).

    Returns:
        {
          "skills_evaluated": int,
          "artifacts_created": int,
          "artifacts": [{"skill": str, "artifact_id": str, "score": float}],
          "skipped_skills": [str],
        }
    """
    cfg = _load_sela_config()
    if config:
        cfg.update(config)

    window_days = int(cfg.get("trace_window_days", 7))
    success_rate_threshold = float(cfg.get("success_rate_threshold", 0.75))
    min_trace_count = int(cfg.get("min_trace_count", 3))
    skill_limit = int(cfg.get("skill_limit", 10))
    mutation_count = int(cfg.get("mutation_count", 4))
    artifact_min_score = float(cfg.get("artifact_min_score", 0.75))
    mutation_function = cfg.get("mutation_function", "skill_mutation")
    fitness_weights = cfg.get("fitness", {"correctness": 0.5, "procedure": 0.3, "conciseness": 0.2})

    from tools.db.storage import get_connection
    conn = get_connection()

    try:
        skills = _query_low_performing_skills(
            conn, window_days, success_rate_threshold, min_trace_count, skill_limit
        )
        log.info("[sela] %d under-performing skill(s) found", len(skills))

        artifacts_created: List[Dict[str, Any]] = []
        skipped_skills: List[str] = []

        for skill_info in skills:
            skill = skill_info["skill_used"]
            baseline_score = skill_info["success_rate"]

            failure_examples = _get_failure_examples(conn, skill, window_days)

            candidates = _generate_mutations(
                skill, skill_info, failure_examples, mutation_count, mutation_function
            )
            if not candidates:
                log.info("[sela] no mutations generated for skill '%s' — skipping", skill)
                skipped_skills.append(skill)
                continue

            scored = [_score_mutation(m, skill, fitness_weights) for m in candidates]
            scored.sort(key=lambda s: s["composite_score"], reverse=True)
            best = scored[0]

            log.info(
                "[sela] skill='%s' best_score=%.3f (threshold=%.2f)",
                skill,
                best["composite_score"],
                artifact_min_score,
            )

            if best["composite_score"] >= artifact_min_score:
                artifact_id = _insert_artifact(conn, skill, best, baseline_score)
                artifacts_created.append(
                    {"skill": skill, "artifact_id": artifact_id, "score": best["composite_score"]}
                )
                log.info("[sela] artifact %s created for skill '%s'", artifact_id, skill)
            else:
                skipped_skills.append(skill)

        return {
            "skills_evaluated": len(skills),
            "artifacts_created": len(artifacts_created),
            "artifacts": artifacts_created,
            "skipped_skills": skipped_skills,
        }
    finally:
        conn.close()


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Genesis reflex entry point.

    Delegates to run_evolution() and wraps result in the standard
    {success, metric_value, details} envelope.
    """
    try:
        result = run_evolution(config)
        return {
            "success": True,
            "metric_value": float(result["artifacts_created"]),
            "details": result,
        }
    except Exception as exc:
        log.error("[sela] evolution run failed: %s", exc)
        return {
            "success": False,
            "metric_value": 0.0,
            "details": {"error": str(exc)},
        }
