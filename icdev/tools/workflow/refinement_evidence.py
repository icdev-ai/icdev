#!/usr/bin/env python3
# CUI // SP-CTI
"""Lesson-backed evidence for proposed refinements (exa-refine-04).

`agent_improvement_artifacts.evidence_traces` used to hold a bare list of trace
ids — opaque strings a human reviewer cannot act on, and nothing an automated
gate can weigh. Meanwhile `tools/workflow/lesson_learned.py` has been writing a
deterministic, 24-pattern classification of *why* every task actually ended the
way it did, with a 7-day recurrence score, into `memory_entries`. The two were
never connected.

This module is the join. For a proposed refinement it collects:

  * the **lesson rows** for the very tasks in the proposal's trajectory
    (trace.task_id → the `lesson_learned` memory entry for that task), and
  * the **recurrence score** for each distinct pattern those lessons carry,
    computed by `lesson_learned.get_recurrence` — the existing scorer, reused
    rather than reimplemented.

It produces a versioned bundle (`refinement_evidence/v1`) that is written whole
into `evidence_traces`, so a reviewer sees WHY a refinement was proposed and a
proposal motivated by nothing can be rejected mechanically rather than by taste.

Deliberately fail-open on *collection* and fail-closed on *the gate*: a DB
hiccup yields an empty bundle, and an empty bundle is unsupported. The one thing
this must never do is let a proposal through by pretending it had evidence.

Usage:
    python tools/workflow/refinement_evidence.py --task-type build --json
    python tools/workflow/refinement_evidence.py --artifact-id impr-build-7e7dfb16
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

_BASE = Path(__file__).resolve().parent.parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger(__name__)

EVIDENCE_SCHEMA = "refinement_evidence/v1"

_CONFIG_PATH = _BASE / "args" / "refinement_evidence.yaml"
_DEFAULT_CONFIG: dict[str, Any] = {
    # Attach the bundle to every proposal (always on — the bundle is the record).
    "window_days": 7,
    # The gate. `require_evidence: false` degrades to attach-only, which is what
    # a fresh install with no lesson history wants before it has any history.
    "require_evidence": True,
    "min_lessons": 1,
    "min_recurrence_score": 0.0,
    # Status written for a proposal the gate rejects. Deliberately NOT 'pending',
    # so it can never be picked up by GEPA or shown in a pending review queue.
    "rejected_status": "rejected_no_evidence",
    # Cap on lesson rows embedded in the bundle (the column is TEXT).
    "max_lessons": 25,
}


def load_config() -> dict[str, Any]:
    """Merge ``args/refinement_evidence.yaml`` over the defaults."""
    cfg = dict(_DEFAULT_CONFIG)
    try:
        import yaml  # noqa: PLC0415

        with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        if isinstance(raw, dict):
            for key in _DEFAULT_CONFIG:
                if key in raw:
                    cfg[key] = raw[key]
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001 — config is a layer, not a dependency
        logger.debug("refinement_evidence: config load failed: %s", exc)
    return cfg


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Lesson lookup
# ---------------------------------------------------------------------------
_LESSON_FIELDS = (
    "task_id",
    "task_title",
    "outcome",
    "pattern",
    "category",
    "failure_count",
    "last_failure_reason",
    "recurrence_score",
    "is_systemic",
    "recommendation",
    "timestamp",
)


_LESSON_QUERY_WINDOWED = (
    "SELECT id, content, created_at FROM memory_entries "
    "WHERE type = %s AND created_at >= %s AND content LIKE %s "
    "ORDER BY created_at DESC"
)
_LESSON_QUERY_ALL = (
    "SELECT id, content, created_at FROM memory_entries "
    "WHERE type = %s AND content LIKE %s "
    "ORDER BY created_at DESC"
)


def _connect(conn=None):
    if conn is not None:
        return conn
    from tools.db.storage import get_connection  # noqa: PLC0415

    return get_connection()


def lessons_for_task_ids(
    task_ids: Iterable[str],
    *,
    days: int = 7,
    conn=None,
    max_task_ids: int = 50,
) -> list[dict[str, Any]]:
    """Return the ``lesson_learned`` rows written for these kanban task ids.

    Matching is a loose ``LIKE`` on the JSON body narrowed to an exact
    ``task_id`` comparison in Python: the payload is a JSON blob, and per the
    repo's PG-portability rule the structured filtering belongs in Python, not
    in dialect-specific JSON SQL. The narrowing is load-bearing — without it a
    lesson for ``exa-refine-041`` would count as evidence for ``exa-refine-04``.
    """
    ids = list(dict.fromkeys(str(t).strip() for t in task_ids if str(t or "").strip()))
    if not ids:
        return []
    ids = ids[: max(1, int(max_task_ids))]
    wanted = set(ids)

    own_conn = conn is None
    try:
        c = _connect(conn)
    except Exception as exc:  # noqa: BLE001
        logger.debug("refinement_evidence: no DB connection: %s", exc)
        return []

    # One query per task id, against two constant SQL strings. An OR-chain built
    # with " OR ".join() would be one round trip, but it assembles the statement
    # by concatenation, and a lesson lookup is not worth teaching this codebase
    # that SELECTs get built that way. The id list is capped at `max_lessons`.
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))).isoformat()
    rows: list[Any] = []
    try:
        for tid in ids:
            try:
                rows.extend(
                    c.execute(_LESSON_QUERY_WINDOWED, ("lesson_learned", since, f"%{tid}%")).fetchall()
                )
            except Exception:  # noqa: BLE001
                # Older/foreign schemas may reject the ISO string comparison on
                # created_at — retry unwindowed rather than losing the evidence.
                rows.extend(
                    c.execute(_LESSON_QUERY_ALL, ("lesson_learned", f"%{tid}%")).fetchall()
                )
    except Exception as exc:  # noqa: BLE001
        logger.debug("refinement_evidence: lesson query failed: %s", exc)
    finally:
        if own_conn:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass

    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        try:
            d = dict(row) if hasattr(row, "keys") else {
                "id": row[0], "content": row[1], "created_at": row[2]
            }
            payload = json.loads(d.get("content") or "{}")
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(payload, dict):
            continue
        task_id = str(payload.get("task_id") or "")
        if task_id not in wanted:
            continue  # substring collision — the LIKE is loose on purpose
        lesson = {k: payload.get(k) for k in _LESSON_FIELDS}
        lesson["task_id"] = task_id
        lesson["memory_entry_id"] = str(d.get("id") or "")
        lesson["lesson_written_at"] = str(d.get("created_at") or "")
        key = (task_id, str(lesson.get("pattern") or ""))
        if key in seen:
            continue  # the same task re-classified identically on a later pass
        seen.add(key)
        out.append(lesson)
    return out


def _recurrence_for(pattern: str, task_ids: list[str], task_type: str, days: int) -> dict[str, Any]:
    """Recurrence of one pattern, via the existing lesson_learned scorer."""
    from tools.workflow.lesson_learned import _task_prefix, get_recurrence  # noqa: PLC0415

    prefix = ""
    for tid in task_ids:
        prefix = _task_prefix(tid)
        if prefix:
            break
    try:
        report = get_recurrence(pattern, prefix, task_type, days=days)
        return {
            "pattern": pattern,
            "prefix": prefix,
            "total_similar": report.total_similar,
            "total_in_window": report.total_in_window,
            "recurrence_score": report.recurrence_score,
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("refinement_evidence: recurrence failed for %s: %s", pattern, exc)
        return {
            "pattern": pattern,
            "prefix": prefix,
            "total_similar": 0,
            "total_in_window": 0,
            "recurrence_score": 0.0,
        }


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------
def collect_evidence(
    *,
    task_type: str,
    skill_used: str = "",
    traces: Optional[list[dict]] = None,
    task_ids: Optional[Iterable[str]] = None,
    days: Optional[int] = None,
    conn=None,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build the evidence bundle that justifies a proposed refinement.

    Pass the execution ``traces`` the proposal was derived from (their
    ``task_id``/``trace_id`` are the trajectory) and/or explicit ``task_ids``.
    Never raises: a failure yields a bundle with zero lessons, which the gate
    then treats as unsupported.
    """
    cfg = load_config()
    window = int(days if days is not None else cfg.get("window_days", 7))
    traces = traces or []

    trace_ids = [str(t.get("trace_id")) for t in traces if t.get("trace_id")]
    ids: list[str] = [str(t.get("task_id")) for t in traces if t.get("task_id")]
    for tid in (task_ids or []):
        if str(tid).strip():
            ids.append(str(tid).strip())
    ids = list(dict.fromkeys(ids))

    lessons = lessons_for_task_ids(ids, days=window, conn=conn)
    lessons = lessons[: int(cfg.get("max_lessons", 25))]

    # Group by pattern, then score each pattern's recurrence once.
    by_pattern: dict[str, list[str]] = {}
    for lesson in lessons:
        pattern = str(lesson.get("pattern") or "unknown")
        by_pattern.setdefault(pattern, []).append(str(lesson.get("task_id") or ""))

    patterns: list[dict[str, Any]] = []
    for pattern, pattern_task_ids in sorted(by_pattern.items()):
        entry = _recurrence_for(pattern, pattern_task_ids, task_type, window)
        entry["lesson_count"] = len(pattern_task_ids)
        entry["is_systemic"] = _pattern_is_systemic(pattern)
        patterns.append(entry)

    # The proposal's headline recurrence: the strongest pattern behind it.
    recurrence_score = max([p["recurrence_score"] for p in patterns], default=0.0)
    dominant = ""
    if patterns:
        dominant = max(
            patterns, key=lambda p: (p["lesson_count"], p["recurrence_score"])
        )["pattern"]

    bundle: dict[str, Any] = {
        "schema": EVIDENCE_SCHEMA,
        "collected_at": _utcnow(),
        "task_type": task_type,
        "skill_used": skill_used,
        "window_days": window,
        "trace_ids": trace_ids,
        "task_ids": ids,
        "lessons": lessons,
        "lesson_count": len(lessons),
        "patterns": patterns,
        "recurrence_score": round(float(recurrence_score), 3),
        "dominant_pattern": dominant,
        "systemic_count": sum(1 for lesson in lessons if lesson.get("is_systemic")),
    }
    if extra:
        bundle.update({k: v for k, v in extra.items() if k not in bundle})
    return bundle


def _pattern_is_systemic(pattern: str) -> bool:
    try:
        from tools.workflow.lesson_learned import _SYSTEMIC_PATTERNS  # noqa: PLC0415

        return pattern in _SYSTEMIC_PATTERNS
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------
def evaluate_evidence(evidence: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Decide whether a bundle supports the refinement it is attached to.

    Returns ``{"supported": bool, "reason": str, "lesson_count": int,
    "recurrence_score": float, "enforced": bool}``. ``enforced`` is False when
    the deployment has turned the gate off, in which case ``supported`` is True
    but the reason still records what the evidence actually was.
    """
    cfg = load_config()
    enforced = bool(cfg.get("require_evidence", True))
    parsed = parse_evidence(evidence)

    lesson_count = int(parsed.get("lesson_count") or 0)
    recurrence = float(parsed.get("recurrence_score") or 0.0)
    min_lessons = int(cfg.get("min_lessons", 1))
    min_recurrence = float(cfg.get("min_recurrence_score", 0.0))

    if lesson_count < min_lessons:
        reason = (
            f"no_supporting_evidence: {lesson_count} lesson_learned row(s) "
            f"for this trajectory, {min_lessons} required"
        )
        supported = False
    elif recurrence < min_recurrence:
        reason = (
            f"recurrence_below_floor: {recurrence:.3f} < {min_recurrence:.3f}"
        )
        supported = False
    else:
        reason = (
            f"supported: {lesson_count} lesson row(s), "
            f"recurrence {recurrence:.3f}, pattern "
            f"{parsed.get('dominant_pattern') or 'unknown'}"
        )
        supported = True

    return {
        "supported": supported or not enforced,
        "gate_passed": supported,
        "enforced": enforced,
        "reason": reason,
        "lesson_count": lesson_count,
        "recurrence_score": recurrence,
        "rejected_status": str(cfg.get("rejected_status", "rejected_no_evidence")),
    }


# ---------------------------------------------------------------------------
# Reading / display
# ---------------------------------------------------------------------------
def parse_evidence(raw: Any) -> dict[str, Any]:
    """Normalise whatever is in ``evidence_traces`` into a v1-shaped dict.

    Three shapes exist in the wild and all must read without raising:
      * the v1 bundle this module writes,
      * a bare ``["trace-…", …]`` list (every artifact written before
        exa-refine-04), and
      * NOVA's provenance dict (``{"source_pattern": …, "session_id": …}``).
    """
    if raw is None or raw == "":
        return _empty_bundle()
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except Exception:  # noqa: BLE001
            return _empty_bundle(note="unparseable evidence_traces")
    if isinstance(raw, list):
        bundle = _empty_bundle(note="legacy trace-id list — no lesson evidence attached")
        bundle["trace_ids"] = [str(x) for x in raw]
        return bundle
    if not isinstance(raw, dict):
        return _empty_bundle(note="unrecognised evidence_traces shape")
    if raw.get("schema") == EVIDENCE_SCHEMA:
        bundle = _empty_bundle()
        bundle.update(raw)
        return bundle
    # A provenance-only dict: keep it visible, but it is not lesson evidence.
    bundle = _empty_bundle(note="provenance only — no lesson evidence attached")
    bundle["provenance"] = raw
    trace_ids = raw.get("trace_ids")
    if isinstance(trace_ids, list):
        bundle["trace_ids"] = [str(x) for x in trace_ids]
    return bundle


def _empty_bundle(note: str = "") -> dict[str, Any]:
    return {
        "schema": EVIDENCE_SCHEMA,
        "collected_at": "",
        "task_type": "",
        "skill_used": "",
        "window_days": 0,
        "trace_ids": [],
        "task_ids": [],
        "lessons": [],
        "lesson_count": 0,
        "patterns": [],
        "recurrence_score": 0.0,
        "dominant_pattern": "",
        "systemic_count": 0,
        "note": note,
    }


def evidence_summary(evidence: Any) -> str:
    """One-line human summary for a review surface."""
    b = parse_evidence(evidence)
    count = int(b.get("lesson_count") or 0)
    if not count:
        return b.get("note") or "No lesson_learned evidence attached."
    return (
        f"{count} lesson_learned row(s); dominant pattern "
        f"'{b.get('dominant_pattern') or 'unknown'}'; recurrence "
        f"{float(b.get('recurrence_score') or 0.0):.2f}; "
        f"{int(b.get('systemic_count') or 0)} systemic"
    )


def render_evidence_markdown(evidence: Any, *, max_rows: int = 8) -> str:
    """Markdown block for a human review surface (GEPA's kanban review card)."""
    b = parse_evidence(evidence)
    lines = ["## Evidence (lesson_learned)", "", evidence_summary(b), ""]
    patterns = b.get("patterns") or []
    if patterns:
        lines.append("| Pattern | Lessons | Recurrence | Systemic |")
        lines.append("|---------|---------|------------|----------|")
        for p in patterns[:max_rows]:
            lines.append(
                f"| {p.get('pattern', '')} | {p.get('lesson_count', 0)} | "
                f"{float(p.get('recurrence_score') or 0.0):.2f} | "
                f"{'yes' if p.get('is_systemic') else 'no'} |"
            )
        lines.append("")
    lessons = b.get("lessons") or []
    if lessons:
        lines.append("| Task | Pattern | Outcome | Last failure reason |")
        lines.append("|------|---------|---------|---------------------|")
        for lesson in lessons[:max_rows]:
            reason = str(lesson.get("last_failure_reason") or "").replace("|", "/")
            lines.append(
                f"| {lesson.get('task_id', '')} | {lesson.get('pattern', '')} | "
                f"{lesson.get('outcome', '')} | {reason[:120]} |"
            )
        lines.append("")
    if len(lessons) > max_rows:
        lines.append(f"…and {len(lessons) - max_rows} more lesson row(s).")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _evidence_for_artifact(artifact_id: str) -> dict[str, Any]:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT artifact_id, task_type, skill_used, status, evidence_traces "
            "FROM agent_improvement_artifacts WHERE artifact_id = %s",
            (artifact_id,),
        ).fetchone()
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    if not row:
        return {"error": "artifact not found", "artifact_id": artifact_id}
    d = dict(row) if hasattr(row, "keys") else {
        "artifact_id": row[0], "task_type": row[1], "skill_used": row[2],
        "status": row[3], "evidence_traces": row[4],
    }
    evidence = parse_evidence(d.get("evidence_traces"))
    return {
        "artifact_id": d.get("artifact_id"),
        "task_type": d.get("task_type"),
        "skill_used": d.get("skill_used"),
        "status": d.get("status"),
        "evidence": evidence,
        "verdict": evaluate_evidence(evidence),
        "summary": evidence_summary(evidence),
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Lesson-backed evidence for proposed refinements (exa-refine-04)"
    )
    parser.add_argument("--task-type", help="Collect evidence for a task_type's recent traces")
    parser.add_argument("--skill", default="", help="Skill the refinement targets")
    parser.add_argument("--window-days", type=int, default=None, help="Look-back window")
    parser.add_argument("--artifact-id", help="Show the evidence stored on an existing artifact")
    parser.add_argument("--json", action="store_true", dest="json_out", help="JSON output")
    args = parser.parse_args(argv)

    if args.artifact_id:
        result = _evidence_for_artifact(args.artifact_id)
    elif args.task_type:
        from tools.workflow.trace_logger import get_traces_for_task_type  # noqa: PLC0415

        traces = get_traces_for_task_type(args.task_type, limit=20)
        evidence = collect_evidence(
            task_type=args.task_type,
            skill_used=args.skill,
            traces=traces,
            days=args.window_days,
        )
        result = {
            "task_type": args.task_type,
            "traces_examined": len(traces),
            "evidence": evidence,
            "verdict": evaluate_evidence(evidence),
            "summary": evidence_summary(evidence),
        }
    else:
        parser.error("one of --task-type or --artifact-id is required")
        return 2

    if args.json_out:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(result.get("summary") or result.get("error", ""))
        verdict = result.get("verdict") or {}
        if verdict:
            print(f"  gate: {'PASS' if verdict.get('gate_passed') else 'REJECT'} — {verdict.get('reason')}")
        print(render_evidence_markdown(result.get("evidence")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
