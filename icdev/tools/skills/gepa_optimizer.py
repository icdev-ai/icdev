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

# --------------------------------------------------------------------------
# Decision vocabulary (rem-cap-01)
# --------------------------------------------------------------------------
# GEPA used to write exactly one outcome — status='applied' — so an artifact it
# looked at and declined stayed 'pending' forever. Two consequences, both
# measured on the live board:
#
#   1. The queue only ever grew. 132 of 162 rows were pending with an IMMUTABLE
#      delta of 0.0 (composite == baseline == 1.0, written before the
#      exa-refine writer fixes) and skill_used = '', so they could never be
#      selected and could never leave. capability_consumption's own alarm
#      condition — queue full, zero rows satisfying the selection predicate,
#      i.e. "structurally cannot ever act" — was therefore stuck on forever
#      regardless of whether the flywheel worked.
#   2. "GEPA ran and correctly declined everything" was indistinguishable from
#      "GEPA never ran", which is exactly the declared-but-unconsumed shape the
#      awareness engine exists to catch. The reflex had 7 successful runs and
#      recorded nothing.
#
# So GEPA now stamps a decision on every artifact it evaluates. A decision whose
# inputs can never change is TERMINAL and the artifact is not re-examined; one
# that could come out differently on a later cycle is not.
DECISION_APPLIED = "applied"
DECISION_NO_DELTA = "declined_no_delta"
DECISION_LOW_SCORE = "declined_low_score"
DECISION_UNMAPPABLE_SKILL = "declined_unmappable_skill"
DECISION_SKILL_FILE_MISSING = "declined_skill_file_missing"
DECISION_EMPTY_PATCH = "declined_empty_patch"
DECISION_RUBRIC = "declined_rubric"

# Terminal decisions. Nothing rescores an artifact after insert — the only
# UPDATEs against this table touch status/applied_at/applied_count/gepa_* — so a
# score-based decline can never come out differently, and neither can a blank
# skill_used. DECISION_SKILL_FILE_MISSING is deliberately NOT terminal: the
# artifact names a skill whose SKILL.md simply is not there yet, and someone may
# add it. Nor are the rubric/empty-patch declines, which depend on a fresh LLM
# call.
TERMINAL_DECISIONS = frozenset({
    DECISION_APPLIED,
    DECISION_NO_DELTA,
    DECISION_LOW_SCORE,
    DECISION_UNMAPPABLE_SKILL,
})


def _skill_dir_candidates(skill_used: str) -> list[str]:
    """Directory names to try under _SKILLS_ROOT for a given ``skill_used``.

    Writers are inconsistent about the prefix: NOVA's skill_generator stores
    ``icdev-<slug>`` while the Reflexion agent may store a bare ``<slug>``.
    Prefixing unconditionally (as this used to) turns the former into
    ``icdev-icdev-<slug>``, so only prefix a name that is not already prefixed.
    Underscores are normalised because skill directories use hyphens.
    """
    raw = (skill_used or "").strip().strip("/\\")
    if not raw:
        return []
    names: list[str] = []
    for base in (raw, raw.replace("_", "-")):
        names.append(base)
        if not base.startswith("icdev-"):
            names.append(f"icdev-{base}")
        else:
            # Tolerate an already-double-prefixed value written by an older run.
            names.append(base[len("icdev-"):])
    # Dedupe, preserving order.
    return list(dict.fromkeys(names))


def _find_skill_file(skill_used: str) -> Path | None:
    """Map skill_used → .agents/skills/<dir>/SKILL.md, or None if no dir matches."""
    for name in _skill_dir_candidates(skill_used):
        candidate = _SKILLS_ROOT / name / "SKILL.md"
        if candidate.exists():
            return candidate
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
            # No `model=` pin: the chain for `gepa_skill_patch` is declared in
            # args/llm_config.yaml so an air-gapped or non-Anthropic deployment
            # routes this itself. LLMRouter resolves the model from that entry.
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


def _score_verdict(artifact: dict) -> str | None:
    """The score-based decline reason for an artifact, or None if it is selectable.

    Mirrors the thresholds this module has always applied; it just names the
    reason instead of dropping the row on the floor.
    """
    composite = artifact.get("composite_score")
    baseline = artifact.get("baseline_score")
    if composite is None or baseline is None:
        return DECISION_LOW_SCORE
    if float(composite) < _MIN_COMPOSITE_SCORE:
        return DECISION_LOW_SCORE
    if float(composite) - float(baseline) < _MIN_SCORE_DELTA:
        return DECISION_NO_DELTA
    return None


def _attach_evidence(d: dict) -> dict:
    """Parse evidence_traces onto an artifact dict.

    exa-refine-04: evidence_traces is a lesson-backed bundle now. parse_evidence
    also reads the legacy bare trace-id list and NOVA's provenance dict, so
    n_traces stays meaningful for the artifacts written before the bundle
    existed.
    """
    from tools.workflow.refinement_evidence import parse_evidence

    evidence = parse_evidence(d.get("evidence_traces"))
    d["evidence"] = evidence
    d["n_traces"] = len(evidence.get("trace_ids") or [])
    d["n_lessons"] = int(evidence.get("lesson_count") or 0)
    return d


def _decisions_recordable(conn) -> bool:
    """True when this database has GEPA's decision columns.

    False means the gepa_decision_columns migration has not run here. GEPA still
    selects and patches; it simply cannot record its verdicts, and
    capability_consumption reports the class UNMEASURABLE rather than zero.
    """
    try:
        from tools.db.storage import column_exists

        return bool(column_exists(conn, "agent_improvement_artifacts", "gepa_decision"))
    except Exception:  # noqa: BLE001
        return False


def _fetch_undecided_pending(conn, limit: int = 200) -> list[dict]:
    """Every pending artifact GEPA has not already TERMINALLY decided.

    This is the set GEPA owes a decision to. It is deliberately not filtered by
    the selection predicate: the rows that fail that predicate are the ones
    whose fate was previously invisible, and they are the whole reason the
    pending queue could only ever grow.
    """
    # An install that has not run 20260816125047_gepa_decision_columns still has
    # a working optimizer — it just cannot record what it decided. Selecting on
    # a column that is not there would turn a missing migration into "GEPA finds
    # nothing", which is the silent no-op this whole task was about.
    decisions_recordable = _decisions_recordable(conn)
    if decisions_recordable:
        placeholders = ", ".join(["%s"] * len(TERMINAL_DECISIONS))
        sql = (
            "SELECT artifact_id, task_type, skill_used, improvement_text, "
            "       composite_score, baseline_score, evidence_traces, gepa_decision "
            "FROM agent_improvement_artifacts "
            "WHERE status = 'pending' "
            f"  AND (gepa_decision IS NULL OR gepa_decision NOT IN ({placeholders})) "
            "ORDER BY (COALESCE(composite_score, 0) - COALESCE(baseline_score, 0)) DESC "
            f"LIMIT {int(limit)}"
        )
        params: tuple = tuple(sorted(TERMINAL_DECISIONS))
    else:
        sql = (
            "SELECT artifact_id, task_type, skill_used, improvement_text, "
            "       composite_score, baseline_score, evidence_traces "
            "FROM agent_improvement_artifacts "
            "WHERE status = 'pending' "
            "ORDER BY (COALESCE(composite_score, 0) - COALESCE(baseline_score, 0)) DESC "
            f"LIMIT {int(limit)}"
        )
        params = ()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [_attach_evidence(dict(r)) for r in rows]
    except Exception as exc:
        logger.warning("gepa_optimizer: failed to fetch artifacts: %s", exc)
        return []


def _get_pending_artifacts(conn) -> list[dict]:
    """Fetch pending improvement artifacts above the score thresholds.

    The selection predicate, unchanged: composite >= _MIN_COMPOSITE_SCORE and a
    delta of at least _MIN_SCORE_DELTA over baseline, best delta first.
    """
    selectable = [
        a for a in _fetch_undecided_pending(conn) if _score_verdict(a) is None
    ]
    return selectable[:10]


def _record_decision(conn, artifact_id: str, decision: str) -> None:
    """Stamp what GEPA decided about this artifact, and when.

    Written for EVERY evaluated artifact, applied or declined. `status` is left
    alone — tools/agent_runtime/skills_lifecycle.py and tools/ace/blueprint.py
    read status='pending' as NOVA's proposal queue, and this is GEPA's verdict,
    not NOVA's.
    """
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute(
            "UPDATE agent_improvement_artifacts "
            "SET gepa_decision = %s, gepa_decided_at = %s "
            "WHERE artifact_id = %s",
            (decision, now, artifact_id),
        )
        conn.commit()
    except Exception as exc:
        # An install that has not run the gepa_decision_columns migration yet
        # must not lose the cycle — but it must not report the decision as
        # recorded either, so this is a warning and not a swallow.
        logger.warning(
            "gepa_optimizer: could not record decision %s for %s: %s",
            decision, artifact_id, exc,
        )


def _evidence_summary(evidence) -> str:
    """One-line lesson-evidence summary; never raises inside the report path."""
    try:
        from tools.workflow.refinement_evidence import evidence_summary
        return evidence_summary(evidence)
    except Exception:  # noqa: BLE001
        return ""


def _mark_applied(conn, artifact_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE agent_improvement_artifacts "
        "SET status='applied', applied_at=%s, applied_count=COALESCE(applied_count,0)+1 "
        "WHERE artifact_id=%s",
        (now, artifact_id),
    )
    conn.commit()
    # An apply is a GEPA decision like any other, so it lands in the same
    # column the declines do. Recorded separately from the status write above so
    # an install still missing the gepa_decision_columns migration keeps the
    # apply rather than losing the whole cycle to an UndefinedColumn.
    _record_decision(conn, artifact_id, DECISION_APPLIED)


def _seed_review_card(skill_name: str, skill_file: str, evidence: dict | None = None) -> None:
    """Create a low-priority kanban card for human review of the GEPA update.

    This card is the human review surface for an applied refinement, so it
    carries the lesson_learned rows and recurrence score that motivated it
    (exa-refine-04) — a reviewer should not have to go query the DB to find out
    why a skill file changed under them.
    """
    try:
        from tools.kanban.task_factory import create_tasks
        from tools.workflow.refinement_evidence import (
            evidence_summary,
            render_evidence_markdown,
        )
        task_id = f"gepa-review-{skill_name}-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
        create_tasks([{
            "id": task_id,
            "title": f"[GEPA] Review auto-updated skill: {skill_name}",
            "description": (
                f"The GEPA optimizer automatically updated the skill file at:\n"
                f"{skill_file}\n\n"
                f"Motivating evidence: {evidence_summary(evidence)}\n\n"
                f"{render_evidence_markdown(evidence)}\n\n"
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

    summary = {"applied": [], "skipped": [], "errors": [], "declined": []}

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

    def _decline(artifact_id: str, decision: str) -> None:
        """Record a decline and note it on the summary."""
        if not dry_run:
            _record_decision(conn, artifact_id, decision)
        summary["declined"].append({"artifact_id": artifact_id, "decision": decision})
        # Preserved for callers that already read `skipped`; `declined` is the
        # one that carries the reason vocabulary.
        summary["skipped"].append({"artifact_id": artifact_id, "reason": decision})

    try:
        # Every pending artifact GEPA has not already terminally decided — not
        # just the selectable ones. GEPA owes each of them a recorded verdict;
        # writing only the 'applied' ones is what left 132 permanently
        # unselectable rows queued forever and GEPA's own work unmeasurable.
        candidates = _fetch_undecided_pending(conn)
        if not candidates:
            logger.info("gepa_optimizer: no undecided pending artifacts")
            return summary

        artifacts = []
        for artifact in candidates:
            verdict = _score_verdict(artifact)
            if verdict is None:
                artifacts.append(artifact)
            else:
                _decline(artifact["artifact_id"], verdict)
        artifacts = artifacts[:10]

        if not artifacts:
            logger.info(
                "gepa_optimizer: no pending artifacts above threshold "
                "(%d declined on score this cycle)", len(summary["declined"]),
            )
            return summary

        for artifact in artifacts:
            artifact_id = artifact["artifact_id"]
            skill_used = artifact.get("skill_used") or ""
            skill_file = _find_skill_file(skill_used)

            if not skill_file:
                # A blank skill_used can never resolve, so that decline is
                # terminal. A named skill whose SKILL.md is simply absent might
                # resolve once someone adds the file, so it is not.
                decision = (
                    DECISION_UNMAPPABLE_SKILL if not skill_used.strip()
                    else DECISION_SKILL_FILE_MISSING
                )
                logger.info(
                    "gepa_optimizer: no skill file found for '%s' (artifact %s) — %s",
                    skill_used, artifact_id, decision,
                )
                _decline(artifact_id, decision)
                continue

            current_content = skill_file.read_text(encoding="utf-8")
            improvement_text = artifact.get("improvement_text") or ""
            composite_score = artifact.get("composite_score", 0)
            baseline_score = artifact.get("baseline_score", 0)
            n_traces = artifact.get("n_traces", 0)

            if dry_run:
                logger.info(
                    "gepa_optimizer [dry-run]: would patch %s (delta=%.2f, traces=%d, %s)",
                    skill_file.name, composite_score - baseline_score, n_traces,
                    _evidence_summary(artifact.get("evidence")),
                )
                summary["applied"].append({
                    "artifact_id": artifact_id,
                    "skill_file": str(skill_file),
                    "n_lessons": artifact.get("n_lessons", 0),
                    "evidence_summary": _evidence_summary(artifact.get("evidence")),
                    "dry_run": True,
                })
                continue

            updated_content = _generate_patch(
                current_content, improvement_text,
                skill_file.name, composite_score, baseline_score, n_traces,
            )

            if not updated_content:
                logger.warning("gepa_optimizer: LLM returned empty patch for %s", artifact_id)
                # Not terminal: a later cycle's LLM call may well return one.
                _record_decision(conn, artifact_id, DECISION_EMPTY_PATCH)
                summary["declined"].append(
                    {"artifact_id": artifact_id, "decision": DECISION_EMPTY_PATCH}
                )
                summary["errors"].append({"artifact_id": artifact_id, "reason": "empty_patch"})
                continue

            if not _rubric_check(current_content, updated_content):
                logger.warning(
                    "gepa_optimizer: rubric failed for %s (len %d→%d, has_frontmatter=%s)",
                    artifact_id, len(current_content), len(updated_content),
                    "---" in updated_content,
                )
                # Not terminal, for the same reason as the empty patch above.
                _decline(artifact_id, DECISION_RUBRIC)
                continue

            # Write updated skill file
            skill_file.write_text(updated_content, encoding="utf-8", newline="")
            logger.info(
                "gepa_optimizer: updated %s (artifact %s, delta=+%.2f, traces=%d)",
                skill_file, artifact_id, composite_score - baseline_score, n_traces,
            )

            _mark_applied(conn, artifact_id)
            _seed_review_card(skill_used, str(skill_file), artifact.get("evidence"))

            summary["applied"].append({
                "artifact_id": artifact_id,
                "skill_file": str(skill_file),
                "score_delta": round(composite_score - baseline_score, 3),
                "n_traces": n_traces,
                "n_lessons": artifact.get("n_lessons", 0),
                "evidence_summary": _evidence_summary(artifact.get("evidence")),
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
              f"declined={len(result.get('declined', []))} "
              f"skipped={len(result['skipped'])} errors={len(result['errors'])}")
        for item in result["applied"]:
            print(f"  [applied] {item.get('skill_file')} "
                  f"(delta +{item.get('score_delta', 0):.3f}, "
                  f"{item.get('n_traces', 0)} traces)")
        by_reason: dict[str, int] = {}
        for item in result.get("declined", []):
            by_reason[item["decision"]] = by_reason.get(item["decision"], 0) + 1
        for reason, count in sorted(by_reason.items()):
            print(f"  [declined] {reason}: {count}")
