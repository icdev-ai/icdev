# CUI // SP-CTI
"""Phase 2 — Suggested Kanban Card Writer.

Reads ``oracle_predictions`` rows produced by ``drift_detector``
(lens_name='internal_awareness') and materializes them as
``kanban_tasks`` rows with ``status='suggested'``. The writer:

  * Filters by minimum confidence (default 0.7 per user sign-off)
  * Maps severity → kanban priority
  * Maps prediction_type → kanban task_type
  * **Subject-level dedup** — if a suggested card already exists for
    the same (rule, subject) pair, it's skipped regardless of
    prediction_id.
  * **Consolidation** — when a rule produces > N findings (YAML-tunable),
    creates one batch card instead of N individual cards.
  * **Auto-dismiss** — stale cards sitting in 'suggested' for > N days
    are auto-dismissed on each run.
  * Marks processed predictions with outcome='promoted' so the
    next run doesn't re-process them.

Config: ``args/awareness_config.yaml`` → ``promotion_filters`` section.

CLI:
    python tools/awareness/suggested_card_writer.py --write --json
    python tools/awareness/suggested_card_writer.py --dry-run --json
    python tools/awareness/suggested_card_writer.py --stats --json
    python tools/awareness/suggested_card_writer.py --promote-all --json
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import argparse
import json
import logging
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

LOG = get_logger("suggested_card_writer")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from tools.db.storage import get_connection  # noqa: E402
except ImportError:
    get_connection = None  # type: ignore[assignment]

LENS_NAME = "internal_awareness"
DEFAULT_CONFIDENCE_THRESHOLD = 0.7

# kanban_tasks constraints (per earlier migrations):
# task_type  ∈ ('build','run','fix','research','deploy','test','chore')
# priority   ∈ ('low','medium','high','critical')
# status     ∈ ('backlog','scheduled','in_progress','done','token_exhausted','suggested')

_SEVERITY_TO_PRIORITY = {
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
}

_PREDICTION_TYPE_TO_TASK_TYPE = {
    # Regressions are fixes
    "regression::http_head": "fix",
    "regression::module_import": "fix",
    "regression::coherence_status": "fix",
    # Lessons-learned remediation
    "lesson_learned": "fix",
}

# Statuses considered "open" — a card in any of these blocks new creation
OPEN_STATUSES = ("backlog", "scheduled", "in_progress", "suggested")

_CONFIG_PATH = BASE_DIR / "args" / "awareness_config.yaml"

# Fallback defaults if YAML is missing
_DEFAULT_FILTERS = {
    "subject_dedup": True,
    "consolidation": {"enabled": True, "threshold": 5},
    "auto_dismiss": {"enabled": True, "stale_days": 30},
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _card_id() -> str:
    return f"task-{uuid.uuid4().hex[:10]}"


def _load_filter_config() -> Dict[str, Any]:
    """Load promotion_filters from awareness_config.yaml with safe fallbacks."""
    try:
        import yaml
    except ImportError:
        return _DEFAULT_FILTERS

    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except (OSError, Exception):
        return _DEFAULT_FILTERS

    filters = raw.get("promotion_filters")
    if not isinstance(filters, dict):
        return _DEFAULT_FILTERS

    # Merge with defaults for any missing keys
    result = dict(_DEFAULT_FILTERS)
    for k in ("subject_dedup", "consolidation", "auto_dismiss"):
        if k in filters:
            result[k] = filters[k]
    return result


# ---------------------------------------------------------------------------
# Title / description generators
# ---------------------------------------------------------------------------


def _generate_title(pred: Dict[str, Any]) -> str:
    """Build a compact, actionable kanban card title.

    prediction_type is of the form ``<kind>::<rule>`` where kind is
    either ``regression`` (Phase 2 drift detector) or ``gap``
    (Phase 3 gap detector). Strip the prefix for display and use
    the kind word as the action verb.
    """
    prediction_type = pred.get("prediction_type") or ""
    # Parse kind + rule_id from the prediction_type
    if "::" in prediction_type:
        kind, rule_id = prediction_type.split("::", 1)
    else:
        kind, rule_id = "issue", prediction_type

    # Special-case lesson_learned for a clearer title
    if prediction_type == "lesson_learned":
        subject_id = pred.get("subject_id", "")
        parts = subject_id.split("::", 1)
        prefix = parts[0] if len(parts) > 1 else ""
        pattern = parts[1] if len(parts) > 1 else subject_id
        label = f"{pattern} ({prefix})" if prefix else pattern
        if len(label) > 50:
            label = label[:47] + "..."
        return f"Fix recurrence: {label}"

    subject_id = pred.get("subject_id", "")
    label = subject_id.split("::", 1)[-1] if "::" in subject_id else subject_id
    if len(label) > 40:
        label = label[:37] + "..."
    return f"{rule_id} {kind}: {label}"


def _generate_description(pred: Dict[str, Any]) -> str:
    """Build a detailed description with evidence for the executor."""
    try:
        evidence = json.loads(pred.get("evidence_json") or "{}")
    except Exception:
        evidence = {}

    parts = [
        f"AUTO-GENERATED from oracle_predictions ({LENS_NAME} lens)",
        f"Source prediction ID: {pred.get('id', '?')}",
        f"Confidence: {pred.get('confidence', 0):.2f}",
        f"Severity: {pred.get('severity', '?')}",
        f"Subject: {pred.get('subject_id', '?')}",
        "",
        pred.get("prediction_text", ""),
        "",
        "Evidence:",
    ]
    for k, v in evidence.items():
        if isinstance(v, dict):
            parts.append(f"  {k}: {json.dumps(v, ensure_ascii=False)[:200]}")
        else:
            parts.append(f"  {k}: {v}")
    parts.extend(
        [
            "",
            "Investigation:",
            "  1. Check recent commits to the affected component",
            f"  2. Run health_prober --probe {(pred.get('prediction_type') or '').replace('regression::','')} to verify current state",
            "  3. Fix the regression and re-run validation",
            "  4. POST to /api/kanban/tasks/<task_id>/move {\"status\":\"done\"}",
        ]
    )
    return "\n".join(parts)


def _generate_batch_title(rule_id: str, kind: str, count: int) -> str:
    """Generate a title for a consolidated batch card."""
    return f"[Batch] {rule_id}: {count} {kind} findings to address"


def _generate_batch_description(
    rule_id: str,
    predictions: List[Dict[str, Any]],
) -> str:
    """Generate a description listing all subjects in a batch card."""
    parts = [
        f"AUTO-GENERATED batch card from oracle_predictions ({LENS_NAME} lens)",
        f"Rule: {rule_id}",
        f"Total findings: {len(predictions)}",
        f"Confidence range: {min(p.get('confidence', 0) for p in predictions):.2f}"
        f" - {max(p.get('confidence', 0) for p in predictions):.2f}",
        "",
        "Subjects:",
    ]
    for pred in predictions:
        subj = pred.get("subject_id", "?")
        parts.append(f"  - {subj}")
    parts.extend([
        "",
        "All subjects share the same rule. A single fix may resolve all.",
        f"Source prediction IDs: {', '.join(p.get('id', '?') for p in predictions[:20])}",
    ])
    if len(predictions) > 20:
        parts.append(f"  ... and {len(predictions) - 20} more")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Subject-level dedup
# ---------------------------------------------------------------------------


def _extract_rule_subject(pred: Dict[str, Any]) -> str:
    """Extract a stable (rule, subject) key for dedup purposes."""
    ptype = pred.get("prediction_type") or ""
    subject = pred.get("subject_id") or ""
    return f"{ptype}::{subject}"


def _load_existing_subjects(conn: Any) -> set:
    """Load all (rule, subject) pairs that already have open (non-done) cards."""
    try:
        placeholders = ",".join(["%s"] * len(OPEN_STATUSES))
        rows = conn.execute(
            f"SELECT kt.title, op.prediction_type, op.subject_id "
            f"FROM kanban_tasks kt "
            f"LEFT JOIN oracle_predictions op ON kt.source_prediction_id = op.id "
            f"WHERE kt.status IN ({placeholders})",
            OPEN_STATUSES,
        ).fetchall()
        subjects = set()
        for r in rows:
            row = dict(r)
            ptype = row.get("prediction_type") or ""
            subject = row.get("subject_id") or ""
            if ptype and subject:
                subjects.add(f"{ptype}::{subject}")
        return subjects
    except Exception:
        return set()


# ---------------------------------------------------------------------------
# Auto-dismiss stale cards
# ---------------------------------------------------------------------------


def _auto_dismiss_stale(conn: Any, stale_days: int) -> int:
    """Dismiss suggested cards older than stale_days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=stale_days)).isoformat()
    now = _now_iso()
    try:
        # Get IDs to dismiss + their prediction IDs
        rows = conn.execute(
            "SELECT id, source_prediction_id FROM kanban_tasks "
            "WHERE status = 'suggested' AND created_at < %s",
            (cutoff,),
        ).fetchall()
        if not rows:
            return 0

        count = 0
        for r in rows:
            row = dict(r)
            conn.execute(
                "UPDATE kanban_tasks SET status = 'done', completed_at = %s, updated_at = %s "
                "WHERE id = %s",
                (now, now, row["id"]),
            )
            if row.get("source_prediction_id"):
                try:
                    conn.execute(
                        "UPDATE oracle_predictions SET outcome = 'dismissed' "
                        "WHERE id = %s AND (outcome IS NULL OR outcome = '' OR outcome = 'pending')",
                        (row["source_prediction_id"],),
                    )
                except Exception:
                    pass
            count += 1
        conn.commit()
        return count
    except Exception as exc:
        LOG.warning("auto_dismiss_stale failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return 0


# ---------------------------------------------------------------------------
# Promote all suggested → backlog
# ---------------------------------------------------------------------------


def promote_all_suggested() -> Dict[str, Any]:
    """Move every 'suggested' card to 'backlog' in one shot."""
    if get_connection is None:
        return {"error": "get_connection unavailable"}

    conn = get_connection()
    now = _now_iso()
    try:
        # Exclude self_debug-quarantined rows: they live in 'suggested' as a
        # durable park, not as awaiting-approval suggestions. Bulk-promoting
        # them re-loops the scheduler on a known-broken task.
        rows = conn.execute(
            "SELECT id FROM kanban_tasks WHERE status = 'suggested' "
            "AND (last_failure_reason IS NULL OR last_failure_reason NOT LIKE %s)",
            ("QUARANTINED by self_debug%",),
        ).fetchall()
        count = len(rows)
        if count == 0:
            return {"promoted": 0, "message": "No suggested cards to promote"}

        conn.execute(
            "UPDATE kanban_tasks SET status = 'backlog', updated_at = %s "
            "WHERE status = 'suggested' "
            "AND (last_failure_reason IS NULL OR last_failure_reason NOT LIKE %s)",
            (now, "QUARANTINED by self_debug%"),
        )
        conn.commit()
        return {"promoted": count, "new_status": "backlog"}
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return {"error": str(exc)[:200]}
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Core write logic
# ---------------------------------------------------------------------------


def _load_pending_predictions(
    conn: Any,
    min_confidence: float,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """Fetch predictions that are eligible for promotion:
      - lens_name = internal_awareness
      - confidence >= threshold
      - outcome is 'pending' or null/empty (not yet processed)

    Note: oracle_predictions.outcome has a DB default of 'pending',
    so new rows do NOT land with NULL — they land with 'pending'.

    Outcomes that EXCLUDE a prediction from re-promotion:
      * ``'promoted:<task_id>'``  — already turned into a kanban card
      * ``'dismissed'``           — operator bulk-dismissed via the
        kanban board (see tools/dashboard/api/kanban.py::bulk_move_tasks).
        Dismissed rows stay dismissed across awareness cycles, so a
        false-positive rule floods the board exactly once instead of
        every 3 hours until the rule is refined.
    """
    # Subtract a small epsilon from the threshold to work around
    # Postgres REAL (32-bit float) precision: 0.7 stored as REAL does
    # not satisfy ``>= 0.7`` when the literal is a 64-bit double.
    eps_confidence = float(min_confidence) - 1e-6
    rows = conn.execute(
        "SELECT id, lens_name, prediction_text, confidence, subject_id, "
        "       prediction_type, severity, evidence_json, created_at "
        "FROM oracle_predictions "
        "WHERE lens_name = %s "
        "  AND confidence >= %s "
        "  AND (outcome IS NULL OR outcome = '' OR outcome = 'pending') "
        "ORDER BY confidence DESC, created_at ASC "
        "LIMIT %s",
        (LENS_NAME, eps_confidence, int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def _find_open_card(
    conn: Any, prediction_id: str, title: str
) -> Optional[Dict[str, Any]]:
    """Return an existing open card matching prediction_id or title, or None.

    "Open" means status IN OPEN_STATUSES (backlog/scheduled/in_progress/suggested).
    Checks prediction_id first, then falls back to exact title match.
    """
    placeholders = ",".join(["%s"] * len(OPEN_STATUSES))
    try:
        row = conn.execute(
            f"SELECT id, title, status FROM kanban_tasks "
            f"WHERE source_prediction_id = %s AND status IN ({placeholders}) LIMIT 1",
            (prediction_id, *OPEN_STATUSES),
        ).fetchone()
        if row:
            return dict(row)
        row = conn.execute(
            f"SELECT id, title, status FROM kanban_tasks "
            f"WHERE title = %s AND status IN ({placeholders}) LIMIT 1",
            (title, *OPEN_STATUSES),
        ).fetchone()
        if row:
            return dict(row)
    except Exception:
        pass
    return None


def _find_done_card_for_subject(
    conn: Any, rule_subject_key: str, title: str
) -> Optional[Dict[str, Any]]:
    """Return a done card matching the (rule, subject) key or title, or None.

    Used to detect cases where a gap was previously closed but has re-opened.
    """
    try:
        if "::" in rule_subject_key:
            ptype, subject = rule_subject_key.split("::", 1)
            row = conn.execute(
                "SELECT kt.id, kt.title, kt.status FROM kanban_tasks kt "
                "JOIN oracle_predictions op ON kt.source_prediction_id = op.id "
                "WHERE kt.status = 'done' "
                "  AND op.prediction_type = %s AND op.subject_id = %s LIMIT 1",
                (ptype, subject),
            ).fetchone()
            if row:
                return dict(row)
        # Fallback: match by title
        row = conn.execute(
            "SELECT id, title, status FROM kanban_tasks "
            "WHERE status = 'done' AND title = %s LIMIT 1",
            (title,),
        ).fetchone()
        if row:
            return dict(row)
    except Exception:
        pass
    return None


def _reset_card_to_backlog(conn: Any, task_id: str) -> bool:
    """Reset a done card to backlog because the underlying gap is still open."""
    now = _now_iso()
    try:
        conn.execute(
            "UPDATE kanban_tasks "
            "SET status = 'backlog', completed_at = NULL, updated_at = %s "
            "WHERE id = %s AND status = 'done'",
            (now, task_id),
        )
        conn.commit()
        LOG.info("dedup: reset done card %s back to backlog (gap still open)", task_id)
        return True
    except Exception as exc:
        LOG.warning("reset_card_to_backlog failed for %s: %s", task_id, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def _insert_card(
    conn: Any,
    title: str,
    description: str,
    priority: str,
    task_type: str,
    prediction_id: str,
) -> Optional[str]:
    """Insert one kanban_tasks row with status='suggested'."""
    task_id = _card_id()
    now = _now_iso()

    try:
        conn.execute(
            "INSERT INTO kanban_tasks "
            "(id, title, description, task_type, priority, status, "
            " executor_type, source_prediction_id, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                task_id,
                title,
                description,
                task_type,
                priority,
                "suggested",
                "claude_cli",
                prediction_id,
                now,
                now,
            ),
        )
        conn.commit()
        return task_id
    except Exception as exc:
        LOG.warning("kanban insert failed for pred %s: %s", prediction_id, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return None


def _mark_prediction_promoted(conn: Any, prediction_id: str, task_id: str) -> None:
    """Set outcome='promoted' + outcome_at so the next run skips this."""
    try:
        conn.execute(
            "UPDATE oracle_predictions "
            "SET outcome = %s, outcome_at = %s "
            "WHERE id = %s",
            (f"promoted:{task_id}", _now_iso(), prediction_id),
        )
        conn.commit()
    except Exception as exc:
        LOG.warning("mark_promoted failed for %s: %s", prediction_id, exc)
        try:
            conn.rollback()
        except Exception:
            pass


def _mark_predictions_promoted_batch(
    conn: Any,
    prediction_ids: List[str],
    task_id: str,
) -> None:
    """Mark multiple predictions as promoted by a single batch card."""
    now = _now_iso()
    outcome = f"promoted:{task_id}"
    for pid in prediction_ids:
        try:
            conn.execute(
                "UPDATE oracle_predictions SET outcome = %s, outcome_at = %s WHERE id = %s",
                (outcome, now, pid),
            )
        except Exception as exc:
            LOG.warning("batch mark_promoted failed for %s: %s", pid, exc)
    try:
        conn.commit()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _current_gap_subjects_by_rule(rule_ids: List[str]) -> Dict[str, set]:
    """Re-run gap rules right now and return {rule_id: set(subjects)}.

    Used to drop stale predictions whose underlying gap has already been
    fixed (2026-04-18: /digital-twin was flagged at 0.90 confidence after
    it had been added to start.md). Re-verifying here instead of trusting
    the cached prediction avoids spawning work for already-resolved gaps.
    """
    current: Dict[str, set] = {}
    try:
        from tools.awareness import gap_detector as _gd
        rule_funcs = getattr(_gd, "_RULE_FUNCS", {})
        for rid in rule_ids:
            fn = rule_funcs.get(rid)
            if not fn:
                continue
            try:
                findings = fn() or []
            except Exception:
                continue
            subjects = set()
            for f in findings:
                subj = f.get("subject") if isinstance(f, dict) else None
                if subj:
                    subjects.add(subj)
            current[rid] = subjects
    except Exception:
        pass
    return current


def write_suggested_cards(
    min_confidence: float = DEFAULT_CONFIDENCE_THRESHOLD,
    dry_run: bool = False,
    limit: int = 500,
) -> Dict[str, Any]:
    """Main entry — promote eligible predictions to kanban_tasks.

    Pipeline:
      1. Load pending predictions
      2. Re-verify each gap still exists (drop already-fixed gaps)
      3. Subject-level dedup (skip if same rule+subject already on board)
      4. Group by rule for consolidation check
      5. Consolidation: rules with > threshold findings → one batch card
      6. Individual cards for rules below threshold
      7. Auto-dismiss stale cards
    """
    if get_connection is None:
        return {"error": "get_connection unavailable"}

    filters = _load_filter_config()
    conn = get_connection()

    # Phase 0: Auto-dismiss stale cards
    auto_dismissed = 0
    auto_dismiss_cfg = filters.get("auto_dismiss", {})
    if (
        not dry_run
        and isinstance(auto_dismiss_cfg, dict)
        and auto_dismiss_cfg.get("enabled")
    ):
        stale_days = int(auto_dismiss_cfg.get("stale_days", 30))
        auto_dismissed = _auto_dismiss_stale(conn, stale_days)

    # Phase 1: Load pending predictions
    try:
        pending = _load_pending_predictions(conn, min_confidence=min_confidence, limit=limit)
    except Exception as exc:
        conn.close()
        return {"error": f"load predictions failed: {exc}"}

    # Phase 1.5: Re-verify gap-style predictions against a fresh rule run.
    # Drops predictions whose gap no longer exists (e.g., the missing route
    # has since been listed, the missing tool has since been added to the
    # manifest). Only applies to ``<kind>::<rule_id>`` predictions where
    # rule_id is a known gap rule.
    gap_rule_ids: set = set()
    for p in pending:
        ptype = (p.get("prediction_type") or "")
        if "::" in ptype:
            gap_rule_ids.add(ptype.split("::", 1)[1])
    current_subjects = _current_gap_subjects_by_rule(list(gap_rule_ids)) if gap_rule_ids else {}
    skipped_gap_fixed = 0
    still_valid: List[Dict[str, Any]] = []
    for p in pending:
        ptype = p.get("prediction_type") or ""
        rule_id = ptype.split("::", 1)[1] if "::" in ptype else None
        subj = p.get("subject_id")
        if rule_id and rule_id in current_subjects and subj and subj not in current_subjects[rule_id]:
            # Gap is no longer present — prediction is stale.
            skipped_gap_fixed += 1
            if not dry_run:
                try:
                    conn.execute(
                        "UPDATE oracle_predictions SET outcome = %s, outcome_at = %s WHERE id = %s",
                        ("dismissed:gap_fixed", _now_iso(), p["id"]),
                    )
                    conn.commit()
                except Exception as exc:
                    LOG.warning("mark gap_fixed failed for %s: %s", p["id"], exc)
            continue
        still_valid.append(p)
    pending = still_valid

    # Phase 2: Subject-level dedup
    existing_subjects = set()
    subject_dedup_enabled = filters.get("subject_dedup", True)
    if subject_dedup_enabled:
        existing_subjects = _load_existing_subjects(conn)

    skipped_existing = 0
    skipped_subject_dedup = 0
    reset_to_backlog = 0
    eligible: List[Dict[str, Any]] = []

    for pred in pending:
        pred_id = pred["id"]
        pred_title = _generate_title(pred)
        rule_subject_key = _extract_rule_subject(pred)

        # Open-card dedup: skip if open card exists with same prediction_id or title
        open_card = _find_open_card(conn, pred_id, pred_title)
        if open_card:
            LOG.info(
                "dedup: skipping pred %s — open card %s (%s) already exists",
                pred_id, open_card["id"], open_card["status"],
            )
            skipped_existing += 1
            if not dry_run:
                _mark_prediction_promoted(
                    conn, pred_id, f"dedup-open:{open_card['id']}"
                )
            continue

        # Subject-level dedup against all open-status cards
        if subject_dedup_enabled and rule_subject_key in existing_subjects:
            LOG.info(
                "dedup: skipping pred %s — subject %r already covered by an open card",
                pred_id, rule_subject_key,
            )
            skipped_subject_dedup += 1
            if not dry_run:
                _mark_prediction_promoted(conn, pred_id, "dedup-skipped")
            continue

        eligible.append(pred)

    # Phase 3: Group by rule for consolidation
    consolidation_cfg = filters.get("consolidation", {})
    consolidation_enabled = (
        isinstance(consolidation_cfg, dict) and consolidation_cfg.get("enabled", True)
    )
    consolidation_threshold = int(
        consolidation_cfg.get("threshold", 5) if isinstance(consolidation_cfg, dict) else 5
    )

    by_rule: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for pred in eligible:
        ptype = pred.get("prediction_type") or ""
        rule = ptype.split("::", 1)[1] if "::" in ptype else ptype
        by_rule[rule].append(pred)

    created: List[Dict[str, Any]] = []
    errors = 0
    consolidated_rules: List[str] = []

    for rule, preds in by_rule.items():
        # Determine kind from first prediction
        first_ptype = preds[0].get("prediction_type") or ""
        kind = first_ptype.split("::", 1)[0] if "::" in first_ptype else "issue"

        # Phase 4: Consolidation check
        if consolidation_enabled and len(preds) > consolidation_threshold:
            consolidated_rules.append(rule)

            if dry_run:
                created.append({
                    "title": _generate_batch_title(rule, kind, len(preds)),
                    "prediction_count": len(preds),
                    "consolidated": True,
                    "dry_run": True,
                })
                continue

            title = _generate_batch_title(rule, kind, len(preds))
            description = _generate_batch_description(rule, preds)

            # Use highest severity from the group
            severities = [p.get("severity", "medium") for p in preds]
            sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
            worst_sev = min(severities, key=lambda s: sev_order.get(s, 99))
            priority = _SEVERITY_TO_PRIORITY.get(worst_sev, "medium")

            task_type = _PREDICTION_TYPE_TO_TASK_TYPE.get(first_ptype, "chore")

            # Dedup: open card by batch title or first prediction_id?
            open_card = _find_open_card(conn, preds[0]["id"], title)
            if open_card:
                LOG.info(
                    "dedup: skipping batch for rule %r — open card %s (%s) exists",
                    rule, open_card["id"], open_card["status"],
                )
                _mark_predictions_promoted_batch(
                    conn,
                    [p["id"] for p in preds],
                    f"dedup-open:{open_card['id']}",
                )
                skipped_existing += len(preds)
                continue

            # Done-card reset: if batch title matches a done card, resurface it
            done_card = _find_done_card_for_subject(conn, f"{first_ptype}::", title)
            if done_card:
                LOG.info(
                    "dedup: resetting done batch card %s to backlog (gap still open)",
                    done_card["id"],
                )
                if _reset_card_to_backlog(conn, done_card["id"]):
                    _mark_predictions_promoted_batch(
                        conn,
                        [p["id"] for p in preds],
                        f"dedup-reset:{done_card['id']}",
                    )
                    for p in preds:
                        existing_subjects.add(_extract_rule_subject(p))
                    reset_to_backlog += 1
                    created.append({
                        "task_id": done_card["id"],
                        "title": title,
                        "prediction_count": len(preds),
                        "consolidated": True,
                        "severity": worst_sev,
                        "reset_from_done": True,
                    })
                else:
                    errors += 1
                continue

            # Use the first prediction's ID as the source link
            task_id = _insert_card(
                conn, title, description, priority, task_type, preds[0]["id"],
            )
            if task_id is None:
                errors += 1
                continue

            # Mark ALL predictions in the batch as promoted
            _mark_predictions_promoted_batch(
                conn, [p["id"] for p in preds], task_id,
            )

            # Track the subjects in existing_subjects so further
            # batches in the same run don't recreate them
            for p in preds:
                existing_subjects.add(_extract_rule_subject(p))

            created.append({
                "task_id": task_id,
                "title": title,
                "prediction_count": len(preds),
                "consolidated": True,
                "severity": worst_sev,
            })
            continue

        # Phase 5: Individual cards (below consolidation threshold)
        for pred in preds:
            if dry_run:
                created.append({
                    "prediction_id": pred["id"],
                    "title": _generate_title(pred),
                    "severity": pred.get("severity"),
                    "confidence": pred.get("confidence"),
                    "dry_run": True,
                })
                continue

            title = _generate_title(pred)
            description = _generate_description(pred)
            severity = (pred.get("severity") or "medium").lower()
            priority = _SEVERITY_TO_PRIORITY.get(severity, "medium")
            task_type = _PREDICTION_TYPE_TO_TASK_TYPE.get(
                pred.get("prediction_type", ""), "fix",
            )
            rule_subject_key = _extract_rule_subject(pred)

            # Done-card reset: if gap is still open but prior card is done, resurface it
            done_card = _find_done_card_for_subject(conn, rule_subject_key, title)
            if done_card:
                LOG.info(
                    "dedup: gap still open for pred %s — resetting done card %s to backlog",
                    pred["id"], done_card["id"],
                )
                if _reset_card_to_backlog(conn, done_card["id"]):
                    _mark_prediction_promoted(
                        conn, pred["id"], f"dedup-reset:{done_card['id']}"
                    )
                    existing_subjects.add(rule_subject_key)
                    reset_to_backlog += 1
                    created.append({
                        "task_id": done_card["id"],
                        "prediction_id": pred["id"],
                        "title": title,
                        "severity": pred.get("severity"),
                        "confidence": pred.get("confidence"),
                        "reset_from_done": True,
                    })
                else:
                    errors += 1
                continue

            task_id = _insert_card(
                conn, title, description, priority, task_type, pred["id"],
            )
            if task_id is None:
                errors += 1
                continue

            _mark_prediction_promoted(conn, pred["id"], task_id)
            existing_subjects.add(rule_subject_key)

            created.append({
                "task_id": task_id,
                "prediction_id": pred["id"],
                "title": title,
                "severity": pred.get("severity"),
                "confidence": pred.get("confidence"),
            })

    try:
        conn.close()
    except Exception:
        pass

    return {
        "created": len(created),
        "skipped_prediction_dedup": skipped_existing,
        "skipped_subject_dedup": skipped_subject_dedup,
        "skipped_gap_fixed": skipped_gap_fixed,
        "reset_to_backlog": reset_to_backlog,
        "auto_dismissed": auto_dismissed,
        "consolidated_rules": consolidated_rules,
        "errors": errors,
        "min_confidence": min_confidence,
        "cards": created,
        "dry_run": dry_run,
    }


def get_stats() -> Dict[str, Any]:
    """Counts of suggested cards sourced from this lens."""
    if get_connection is None:
        return {"error": "get_connection unavailable"}
    conn = get_connection()
    try:
        # kanban_tasks with matching source_prediction_id from our lens
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM kanban_tasks kt "
            "JOIN oracle_predictions op ON op.id = kt.source_prediction_id "
            "WHERE op.lens_name = %s",
            (LENS_NAME,),
        ).fetchone()
        suggested = dict(row).get("cnt", 0) if row else 0

        # Pending (not yet promoted) predictions — 'pending' is the
        # DB default outcome for fresh rows.
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM oracle_predictions "
            "WHERE lens_name = %s AND (outcome IS NULL OR outcome = '' OR outcome = 'pending')",
            (LENS_NAME,),
        ).fetchone()
        pending = dict(row).get("cnt", 0) if row else 0

        # Already promoted — use a parameter for the LIKE pattern
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM oracle_predictions "
            "WHERE lens_name = %s AND outcome LIKE %s",
            (LENS_NAME, "promoted:%"),
        ).fetchone()
        promoted = dict(row).get("cnt", 0) if row else 0

        # Suggested cards by rule
        rows = conn.execute(
            "SELECT op.prediction_type, COUNT(*) AS cnt "
            "FROM kanban_tasks kt "
            "JOIN oracle_predictions op ON op.id = kt.source_prediction_id "
            "WHERE kt.status = 'suggested' AND op.lens_name = %s "
            "GROUP BY op.prediction_type ORDER BY cnt DESC",
            (LENS_NAME,),
        ).fetchall()
        by_rule = {dict(r)["prediction_type"]: dict(r)["cnt"] for r in rows}

        return {
            "lens": LENS_NAME,
            "cards_in_kanban": suggested,
            "suggested_by_rule": by_rule,
            "predictions_pending": pending,
            "predictions_promoted": promoted,
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="suggested_card_writer",
        description="Phase 2 — promote internal_awareness oracle predictions to kanban suggested cards",
    )
    parser.add_argument("--write", action="store_true", help="Write suggested cards")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--stats", action="store_true", help="Show stats")
    parser.add_argument(
        "--promote-all",
        action="store_true",
        help="Move all suggested cards to backlog",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=DEFAULT_CONFIDENCE_THRESHOLD,
        help=f"Minimum confidence to promote (default {DEFAULT_CONFIDENCE_THRESHOLD})",
    )
    parser.add_argument("--limit", type=int, default=500, help="Max predictions per run")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if args.stats:
        result: Dict[str, Any] = get_stats()
    elif args.promote_all:
        result = promote_all_suggested()
    elif args.write or args.dry_run:
        result = write_suggested_cards(
            min_confidence=args.min_confidence,
            dry_run=args.dry_run,
            limit=args.limit,
        )
    else:
        parser.print_help()
        return 1

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for k, v in result.items():
            if k == "cards":
                print(f"  cards: {len(v)} items")
            else:
                print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
