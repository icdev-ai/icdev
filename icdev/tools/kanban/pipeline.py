# CUI // SP-CTI
"""Per-task delivery-pipeline assembler (Phase 1 — visualization only).

Surfaces the gates that ALREADY run in validate_working_tree / kanban_verifications
plus the kanban_status_transitions timeline, so the dashboard can show where a
task really is (Implement -> Code Quality -> Coherence -> Conformance -> Unit
Tests -> E2E -> PR -> CI -> Merged). Read-only; no behavior change to the runner.

The canonical stage list lives in args/pipeline.yaml (single source of truth).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)


def _enforce_mode() -> str:
    """Whether the delivery-pipeline gates currently BLOCK done or only record.

    Reads ``KANBAN_PIPELINE_ENFORCE`` — the same switch
    ``validated_commit._pipeline_enforce()`` gates on — so the pipeline view can
    tell the user at a glance whether the gates are ``enforced`` (block done) or
    ``record_only`` (recorded but non-blocking). A light env read so this module
    stays dependency-free for the dashboard/CLI hot paths.
    """
    return "enforced" if os.environ.get("KANBAN_PIPELINE_ENFORCE", "0").lower() in ("1", "true", "yes") else "record_only"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PIPELINE_YAML = _REPO_ROOT / "args" / "pipeline.yaml"

# Which kanban_verifications column backs each verification-sourced stage.
# review_passed does not exist until Phase 2 -> conformance renders "not run".
_STAGE_GATE_COL = {
    "code_quality": "codelens_passed",
    "coherence": "coherence_passed",
    "conformance": "review_passed",
    "unit_tests": "pytest_passed",
    "e2e": "e2e_passed",
}

# kanban_tasks.status -> the pipeline stage the task is currently AT.
_STATUS_TO_STAGE = {
    "suggested": "implement",
    "backlog": "implement",
    "scheduled": "implement",
    "in_progress": "implement",
    "decomposed": "implement",
    "needs_decomposition": "implement",
    "validating": "code_quality",
    "pr_opened": "pr",
    "changes_requested": "pr",
    "merge_conflict": "pr",
    "ci_failed": "ci",
    "done": "merged",
    "failed": "implement",
    "token_exhausted": "implement",
}

# Statuses that represent an off-happy-path "branch" (needs attention), surfaced
# separately so the stepper can flag them without faking forward progress.
_BRANCH_STATES = {
    "ci_failed", "merge_conflict", "changes_requested",
    "failed", "token_exhausted", "validating",
}

_cached_stages: Optional[List[Dict[str, Any]]] = None


def load_stages() -> List[Dict[str, Any]]:
    """Load the ordered canonical stage list from args/pipeline.yaml (cached)."""
    global _cached_stages
    if _cached_stages is None:
        with open(_PIPELINE_YAML, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        _cached_stages = list(data.get("stages", []))
    return _cached_stages


def status_to_stage(status: Optional[str]) -> str:
    """Map a kanban_tasks.status to its current pipeline stage key."""
    return _STATUS_TO_STAGE.get((status or "").strip(), "implement")


def _norm_gate(v: Any) -> str:
    """Normalize a kanban_verifications boolean-ish column to pass/fail/not_run."""
    if v is None or v == "":
        return "not_run"
    if v in (1, True, "1", "true", "True"):
        return "pass"
    if v in (0, False, "0", "false", "False"):
        return "fail"
    return "not_run"


_MOJIBAKE_MARKERS = ("â€", "Ãƒ", "â")


def _fix_mojibake(s: Optional[str]) -> Optional[str]:
    """Best-effort repair of UTF-8-decoded-as-cp1252 text (e.g. em-dash '—'
    stored by the runner's git capture as 'a-euro-...'). Only touches strings
    that carry the tell-tale markers, and reverts if the round-trip fails."""
    if not s or not any(m in s for m in _MOJIBAKE_MARKERS):
        return s
    # cp1252 (not latin-1) — it's the codec the mojibake came from, so it round-
    # trips €, em-dash and smart quotes that latin-1 can't encode.
    for enc in ("cp1252", "latin-1"):
        try:
            repaired = s.encode(enc).decode("utf-8")
            if "�" not in repaired:  # reject if it introduced replacement chars
                return repaired
        except (UnicodeError, ValueError):
            continue
    return s


def _commit_subject(summary: Optional[str]) -> Optional[str]:
    """First line of a (possibly multi-commit) commit_summary, mojibake-repaired
    and truncated — for a clean one-line display instead of the raw blob."""
    if not summary:
        return None
    lines = [ln for ln in str(summary).strip().splitlines() if ln.strip()]
    if not lines:
        return None
    subj = _fix_mojibake(lines[0].strip())
    extra = len(lines) - 1
    if subj and len(subj) > 100:
        subj = subj[:99] + "…"
    if extra > 0:
        subj = f"{subj}  (+{extra} more)"
    return subj


def _fetch_row(conn, sql: str, params: tuple) -> Optional[dict]:
    # Best-effort ENRICHMENT reads only (verification row, meta). Failures here
    # degrade gracefully to None. NEVER use this for the task-existence check —
    # a swallowed DB error there would masquerade as "task_not_found".
    try:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None
    except Exception as exc:
        logger.debug("pipeline._fetch_row failed (%s): %s", sql[:40], exc)
        return None


def _extract_gates(vrow: Optional[dict]) -> Dict[str, Dict[str, Any]]:
    """Per-gate {status, detail} from the latest kanban_verifications row.

    Uses dict.get() so it tolerates schema drift (root vs dev-SQLite: pytest_passed
    may be present or absent; review_passed absent until Phase 2)."""
    vrow = vrow or {}
    gates: Dict[str, Dict[str, Any]] = {}
    for stage_key, col in _STAGE_GATE_COL.items():
        status = _norm_gate(vrow.get(col))
        detail = ""
        if stage_key == "code_quality":
            ru, ba = vrow.get("ruff_issues"), vrow.get("bandit_issues")
            bits = []
            if ru not in (None, ""):
                bits.append(f"ruff:{ru}")
            if ba not in (None, ""):
                bits.append(f"bandit:{ba}")
            detail = " ".join(bits)
        elif stage_key == "coherence" and vrow.get("coherence_violations") not in (None, ""):
            detail = f"violations:{vrow.get('coherence_violations')}"
        elif stage_key == "unit_tests" and vrow.get("failed_tests") not in (None, ""):
            detail = f"failed:{vrow.get('failed_tests')}"
        elif stage_key == "conformance" and vrow.get("review_findings") not in (None, ""):
            try:
                import json as _json
                _f = _json.loads(vrow.get("review_findings") or "[]")
                _unmet = [x for x in _f if isinstance(x, dict) and not x.get("met", True)]
                if _unmet:
                    detail = f"{len(_unmet)} unmet: " + "; ".join(
                        (x.get("criterion") or "")[:40] for x in _unmet[:2]
                    )
            except Exception:
                pass
        elif stage_key == "e2e":
            # e2e only meaningful when it ran; backend-only tasks skip it.
            if _norm_gate(vrow.get("e2e_ran")) != "pass":
                status = "not_run"
                detail = "skipped (no UI change)"
        gates[stage_key] = {"status": status, "detail": detail}
    return gates


def _stage_state(stage_key: str, idx: int, current_idx: int,
                 gates: Dict[str, Dict[str, Any]], is_done: bool) -> str:
    """Per-stage display state: completed | current | failed | pending | not_run."""
    if is_done:
        return "completed"
    col = _STAGE_GATE_COL.get(stage_key)
    if col:
        g = gates.get(stage_key, {}).get("status", "not_run")
        if g == "fail":
            return "failed"
        if g == "pass":
            return "completed"
        # not_run gate
        if idx < current_idx:
            return "not_run"
        return "current" if idx == current_idx else "pending"
    # position-based (implement / pr / ci / merged)
    if idx < current_idx:
        return "completed"
    return "current" if idx == current_idx else "pending"


def assemble(task_id: str) -> Dict[str, Any]:
    """Assemble the full per-task pipeline view (no live gh — see resolve_pr_live)."""
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        # Task-existence check goes direct (NOT via _fetch_row) so a DB error
        # propagates to the handler below as "pipeline_error" instead of being
        # silently reported as "task_not_found".
        trow = conn.execute(
            "SELECT * FROM kanban_tasks WHERE id = %s", (task_id,)
        ).fetchone()
        if not trow:
            return {"error": "task_not_found", "task_id": task_id}
        task = dict(trow)
        status = (task.get("status") or "").strip()
        current_stage = status_to_stage(status)
        is_done = status == "done"

        vrow = _fetch_row(
            conn,
            "SELECT * FROM kanban_verifications WHERE task_id = %s "
            "ORDER BY verified_at DESC LIMIT 1",
            (task_id,),
        )
        gates = _extract_gates(vrow)

        trows = []
        try:
            rows = conn.execute(
                "SELECT from_status, to_status, actor, reason, recorded_at "
                "FROM kanban_status_transitions WHERE task_id = %s "
                "ORDER BY recorded_at",
                (task_id,),
            ).fetchall()
            trows = [dict(r) for r in rows]
        except Exception:
            trows = []
        transitions = [
            {
                "from": t.get("from_status"),
                "to": t.get("to_status"),
                "actor": t.get("actor"),
                "reason": t.get("reason"),
                "at": t.get("recorded_at"),
            }
            for t in trows
        ]

        stage_defs = load_stages()
        keys = [s["key"] for s in stage_defs]
        current_idx = keys.index(current_stage) if current_stage in keys else 0
        stages = []
        for idx, s in enumerate(stage_defs):
            stages.append({
                "key": s["key"],
                "label": s.get("label", s["key"]),
                "tooltip": s.get("tooltip", ""),
                "blocking": bool(s.get("blocking", False)),
                "state": _stage_state(s["key"], idx, current_idx, gates, is_done),
                "detail": gates.get(s["key"], {}).get("detail", ""),
            })

        meta = {
            "status": status,
            "branch_name": task.get("branch_name"),
            "commit_summary": _fix_mojibake(task.get("commit_summary")),
            "commit_subject": _commit_subject(task.get("commit_summary")),
            "files_changed": task.get("files_changed"),
            "lines_added": task.get("lines_added"),
            "lines_removed": task.get("lines_removed"),
            "completed_at": task.get("completed_at"),
            "failure_reason": task.get("last_failure_reason"),
        }

        return {
            "task_id": task_id,
            "current_stage": current_stage,
            "branch_state": status if status in _BRANCH_STATES else None,
            "stages": stages,
            "gates": gates,
            "transitions": transitions,
            "meta": meta,
            "enforce_mode": _enforce_mode(),
        }
    except Exception as exc:
        logger.warning("pipeline.assemble(%s) failed: %s", task_id, exc)
        return {"error": "pipeline_error", "task_id": task_id, "detail": str(exc)[:200]}
    finally:
        conn.close()


def _summarize_ci(rollup: Any) -> str:
    """Reduce gh statusCheckRollup to passing | failing | pending | unknown."""
    if not rollup or not isinstance(rollup, list):
        return "unknown"
    any_pending = False
    for c in rollup:
        if not isinstance(c, dict):
            continue
        concl = (c.get("conclusion") or "").upper()
        st = (c.get("status") or c.get("state") or "").upper()
        if concl in ("FAILURE", "TIMED_OUT", "CANCELLED", "ERROR") or st == "FAILURE":
            return "failing"
        if st in ("PENDING", "IN_PROGRESS", "QUEUED", "WAITING") or (not concl and not st):
            any_pending = True
    return "pending" if any_pending else "passing"


def resolve_pr_live(task_id: str) -> Optional[Dict[str, Any]]:
    """On-demand live PR/CI state via gh (best-effort; None on any failure)."""
    try:
        from tools.db.storage import get_connection
        from tools.ci import pr_watcher

        tasks = pr_watcher.list_pr_tasks(get_connection, task_id=task_id)
        if not tasks:
            return None
        pr_url = tasks[0].get("pr_url")
        if not pr_url:
            return None
        state = pr_watcher.fetch_pr_state(pr_url)
        return {
            "number": state.get("number"),
            "url": state.get("url") or pr_url,
            "state": state.get("state"),
            "ci_conclusion": _summarize_ci(state.get("statusCheckRollup")),
            "mergeable": state.get("mergeable"),
        }
    except Exception:
        return None
