#!/usr/bin/env python3
# CUI // SP-CTI
"""GEPA Skill Optimizer — closes the self-improvement flywheel.

Reads agent_improvement_artifacts that the reflexion_agent has already generated,
maps each artifact to its source skill file, uses LLM Router to generate a
targeted patch, and writes the improved skill file. Creates a low-priority kanban
review card for each update so a human can audit before the next cycle uses it.

Implements the GEPA (Genetic Evolution via Prompt Artifacts) optimization pattern:
  execution trace → reflexion artifact → skill patch → improved skill file

Usage:
    python tools/skills/gepa_optimizer.py [--dry-run] [--json]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger(__name__)

# Minimum score improvement over baseline to trigger a skill update
_MIN_SCORE_DELTA = 0.05
# Minimum composite_score to consider applying (avoids updating on poor evidence)
_MIN_COMPOSITE_SCORE = 0.60
# Minimum rubric check: updated skill must be at least this fraction of original length
_MIN_LENGTH_RATIO = 0.80
# Skills root directory
_SKILLS_ROOT = _BASE / ".agents" / "skills"


def _find_skill_file(skill_used: str) -> Path | None:
    """Map skill_used → .agents/skills/icdev-{skill_used}/SKILL.md (or variants)."""
    if not skill_used:
        return None
    candidates = [
        _SKILLS_ROOT / f"icdev-{skill_used}" / "SKILL.md",
        _SKILLS_ROOT / skill_used / "SKILL.md",
        _SKILLS_ROOT / f"icdev-{skill_used.replace('_', '-')}" / "SKILL.md",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


# exa-refine-02: this was an f-string literal inside _generate_patch. The text is
# unchanged; the interpolations became named placeholders so the template can be
# versioned in the prompt registry under PATCH_PROMPT_NAME. The two float format
# specs (:.2f) are applied by _build_patch_prompt before substitution, because a
# stored template cannot carry Python format specs.
PATCH_PROMPT_NAME = "call_site/gepa_skill_patch"
PATCH_PROMPT_TEMPLATE = (
    "You are a skill optimizer for the ICDEV AI platform.\n\n"
    "SKILL FILE: {skill_name}\n\n"
    "CURRENT SKILL CONTENT:\n{current_content}\n\n"
    "IMPROVEMENT SUGGESTION (from {n_traces} execution traces, "
    "composite_score={composite_score} vs baseline {baseline_score}):\n"
    "{improvement_text}\n\n"
    "Generate the updated skill file content. Rules:\n"
    "- Keep ALL YAML frontmatter unchanged (everything between --- markers)\n"
    "- Make targeted improvements to steps/instructions based on the suggestion\n"
    "- Do not remove existing steps unless they are clearly incorrect\n"
    "- Do not add padding or unnecessary content\n"
    "- Keep total length within 20% of the original\n\n"
    "Return ONLY the updated skill file content. No explanation, no markdown fences."
)


def _build_patch_prompt(current_content: str, improvement_text: str,
                        skill_name: str, composite_score: float,
                        baseline_score: float, n_traces: int) -> str:
    """Render the skill-patch prompt: active registry version, else the module default."""
    from tools.llm.prompt_registry import render_prompt

    return render_prompt(
        PATCH_PROMPT_NAME,
        PATCH_PROMPT_TEMPLATE,
        skill_name=skill_name,
        current_content=current_content,
        n_traces=n_traces,
        composite_score=f"{composite_score:.2f}",
        baseline_score=f"{baseline_score:.2f}",
        improvement_text=improvement_text,
    )


def _generate_patch(current_content: str, improvement_text: str,
                    skill_name: str, composite_score: float,
                    baseline_score: float, n_traces: int) -> str | None:
    """Call LLM Router to generate updated skill file content."""
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        prompt = _build_patch_prompt(
            current_content, improvement_text, skill_name,
            composite_score, baseline_score, n_traces,
        )

        req = LLMRequest(
            system_prompt=(
                "You are a precise skill file editor. Return only the updated file content."
            ),
            messages=[{"role": "user", "content": prompt}],
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            temperature=0.2,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = LLMRouter().invoke("gepa_skill_patch", req)
        if resp and resp.content and resp.content.strip():
            return resp.content.strip()
    except Exception as exc:
        logger.warning("gepa_optimizer: LLM patch generation failed: %s", exc)
    return None


def _rubric_check(original: str, updated: str) -> bool:
    """Basic quality gate: updated content must retain YAML frontmatter and minimum length."""
    if not updated:
        return False
    if len(updated) < len(original) * _MIN_LENGTH_RATIO:
        return False
    # Must retain YAML frontmatter
    if "---" not in updated:
        return False
    return True


def _get_pending_artifacts(conn) -> list[dict]:
    """Fetch pending improvement artifacts above the score thresholds."""
    try:
        rows = conn.execute(
            "SELECT artifact_id, task_type, skill_used, improvement_text, "
            "       composite_score, baseline_score, evidence_traces "
            "FROM agent_improvement_artifacts "
            "WHERE status = 'pending' "
            "  AND composite_score IS NOT NULL "
            "  AND baseline_score IS NOT NULL "
            "  AND composite_score >= %s "
            "ORDER BY (composite_score - baseline_score) DESC "
            "LIMIT 10",
            (_MIN_COMPOSITE_SCORE,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            delta = (d.get("composite_score") or 0) - (d.get("baseline_score") or 0)
            if delta >= _MIN_SCORE_DELTA:
                try:
                    traces = json.loads(d.get("evidence_traces") or "[]")
                    d["n_traces"] = len(traces)
                except Exception:
                    d["n_traces"] = 0
                result.append(d)
        return result
    except Exception as exc:
        logger.warning("gepa_optimizer: failed to fetch artifacts: %s", exc)
        return []


def _mark_applied(conn, artifact_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE agent_improvement_artifacts "
        "SET status='applied', applied_at=%s, applied_count=COALESCE(applied_count,0)+1 "
        "WHERE artifact_id=%s",
        (now, artifact_id),
    )
    conn.commit()


def _seed_review_card(skill_name: str, skill_file: str) -> None:
    """Create a low-priority kanban card for human review of the GEPA update."""
    try:
        from tools.kanban.task_factory import create_tasks
        task_id = f"gepa-review-{skill_name}-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
        create_tasks([{
            "id": task_id,
            "title": f"[GEPA] Review auto-updated skill: {skill_name}",
            "description": (
                f"The GEPA optimizer automatically updated the skill file at:\n"
                f"{skill_file}\n\n"
                f"Review the changes to confirm they improve task execution quality. "
                f"Accept by closing this card or revert with `git diff {skill_file}`."
            ),
            "task_type": "review",
            "priority": "low",
            "status": "backlog",
            "dispatch_source": "gepa_optimizer",
        }])
    except Exception as exc:
        logger.warning("gepa_optimizer: failed to seed review card for %s: %s", skill_name, exc)


def _gepa_frozen() -> bool:
    """True when GEPA promotion is frozen for a scoring baseline transition.

    Set ``ICDEV_GEPA_FROZEN=1`` during an agx-pick-02 fitness-vocabulary cutover
    so a shifted composite distribution cannot mass-accept or mass-reject skill
    candidates on the next reflex run — the SIPA re-signature failure mode
    (kanban-manual-gate-integrity). The freeze is an EXPLICIT gate a human flips,
    never a side effect. See docs/audits/agx-pick-02-baseline-transition.md.
    """
    import os
    return str(os.environ.get("ICDEV_GEPA_FROZEN", "")).strip().lower() in {"1", "true", "yes", "on"}


def run(dry_run: bool = False) -> dict:
    """Run one GEPA optimization cycle. Returns summary dict."""
    from tools.db.storage import get_connection

    summary = {"applied": [], "skipped": [], "errors": []}

    if _gepa_frozen() and not dry_run:
        logger.warning(
            "gepa_optimizer: promotion FROZEN (ICDEV_GEPA_FROZEN) — fitness "
            "baseline transition in progress; skipping this cycle"
        )
        summary["skipped"].append({"reason": "gepa_frozen_baseline_transition"})
        return summary

    try:
        conn = get_connection()
    except Exception as exc:
        logger.error("gepa_optimizer: DB connection failed: %s", exc)
        return summary

    try:
        artifacts = _get_pending_artifacts(conn)
        if not artifacts:
            logger.info("gepa_optimizer: no pending artifacts above threshold")
            return summary

        for artifact in artifacts:
            artifact_id = artifact["artifact_id"]
            skill_used = artifact.get("skill_used") or ""
            skill_file = _find_skill_file(skill_used)

            if not skill_file:
                logger.info(
                    "gepa_optimizer: no skill file found for '%s' (artifact %s) — skipping",
                    skill_used, artifact_id,
                )
                summary["skipped"].append({"artifact_id": artifact_id, "reason": "skill_file_not_found"})
                continue

            current_content = skill_file.read_text(encoding="utf-8")
            improvement_text = artifact.get("improvement_text") or ""
            composite_score = artifact.get("composite_score", 0)
            baseline_score = artifact.get("baseline_score", 0)
            n_traces = artifact.get("n_traces", 0)

            if dry_run:
                logger.info(
                    "gepa_optimizer [dry-run]: would patch %s (delta=%.2f, traces=%d)",
                    skill_file.name, composite_score - baseline_score, n_traces,
                )
                summary["applied"].append({
                    "artifact_id": artifact_id,
                    "skill_file": str(skill_file),
                    "dry_run": True,
                })
                continue

            updated_content = _generate_patch(
                current_content, improvement_text,
                skill_file.name, composite_score, baseline_score, n_traces,
            )

            if not updated_content:
                logger.warning("gepa_optimizer: LLM returned empty patch for %s", artifact_id)
                summary["errors"].append({"artifact_id": artifact_id, "reason": "empty_patch"})
                continue

            if not _rubric_check(current_content, updated_content):
                logger.warning(
                    "gepa_optimizer: rubric failed for %s (len %d→%d, has_frontmatter=%s)",
                    artifact_id, len(current_content), len(updated_content),
                    "---" in updated_content,
                )
                summary["skipped"].append({"artifact_id": artifact_id, "reason": "rubric_failed"})
                continue

            # Write updated skill file
            skill_file.write_text(updated_content, encoding="utf-8", newline="")
            logger.info(
                "gepa_optimizer: updated %s (artifact %s, delta=+%.2f, traces=%d)",
                skill_file, artifact_id, composite_score - baseline_score, n_traces,
            )

            _mark_applied(conn, artifact_id)
            _seed_review_card(skill_used, str(skill_file))

            summary["applied"].append({
                "artifact_id": artifact_id,
                "skill_file": str(skill_file),
                "score_delta": round(composite_score - baseline_score, 3),
                "n_traces": n_traces,
            })

    except Exception as exc:
        logger.error("gepa_optimizer: cycle error: %s", exc)
        summary["errors"].append({"reason": str(exc)})
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="GEPA Skill Optimizer")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--json", action="store_true", dest="json_out", help="JSON output")
    args = parser.parse_args()

    result = run(dry_run=args.dry_run)

    if args.json_out:
        print(json.dumps(result, indent=2))
    else:
        print(f"GEPA optimizer: applied={len(result['applied'])} "
              f"skipped={len(result['skipped'])} errors={len(result['errors'])}")
        for item in result["applied"]:
            print(f"  [applied] {item.get('skill_file')} "
                  f"(delta +{item.get('score_delta', 0):.3f}, "
                  f"{item.get('n_traces', 0)} traces)")
