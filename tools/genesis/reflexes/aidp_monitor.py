#!/usr/bin/env python3
# CUI // SP-CTI
"""AIDP Monitor Reflex — daily provenance integrity scanner.

GREEN tier, 24 h cadence.  Zero LLM in hot path — all checks are
deterministic hash comparisons against rag_provenance_ledger.

Checks:
  1. SHA-256 integrity  — re-computes hash of chunk content and compares
                          with sha256_hash stored in the ledger.
  2. Chain-of-custody   — flags ingest records whose event_type =
                          'chain_of_custody' is missing prompt_sha256
                          or signature.
  3. Classification drift — detects parent_doc_uuid records that have
                            more than one distinct classification_label
                            across ingest events.

Critical findings (any violation) are emitted to oracle_predictions
(confidence >= 0.9, status = 'suggested') and to kanban_tasks
(status = 'suggested').
"""
IMPLEMENTATION_STATUS = "full"

import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

_LENS_ID = "aidp_monitor"
_LENS_NAME = "AIDP Provenance Monitor"
_CLASSIFICATION = "CUI // SP-CTI"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return str(uuid.uuid4())


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _get_conn():
    from tools.db.storage import get_connection  # noqa: PLC0415
    return get_connection()


def _table_exists(conn, table_name: str) -> bool:
    """Check existence in both SQLite and PostgreSQL."""
    try:
        conn.execute(f"SELECT 1 FROM {table_name} LIMIT 1")
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Check 1 — SHA-256 integrity
# ---------------------------------------------------------------------------

def _check_sha256_integrity(conn) -> List[Dict[str, Any]]:
    """Re-compute SHA-256 of chunk content vs stored sha256_hash."""
    violations: List[Dict[str, Any]] = []

    if not _table_exists(conn, "rag_provenance_ledger"):
        return violations
    if not _table_exists(conn, "rag_chunks"):
        return violations

    rows = conn.execute(
        """
        SELECT rpl.id, rpl.chunk_uuid, rpl.sha256_hash, rc.content
        FROM   rag_provenance_ledger rpl
        JOIN   rag_chunks rc ON rc.id = rpl.chunk_uuid
        WHERE  rpl.event_type = 'ingest'
          AND  rpl.sha256_hash IS NOT NULL
          AND  rc.content IS NOT NULL
        """
    ).fetchall()

    for row in rows:
        ledger_id, chunk_uuid, stored_hash, content = row
        computed = _sha256(content)
        if computed != stored_hash:
            violations.append(
                {
                    "type": "sha256_mismatch",
                    "ledger_id": ledger_id,
                    "chunk_uuid": chunk_uuid,
                    "stored_hash": stored_hash[:16] + "…",
                    "computed_hash": computed[:16] + "…",
                }
            )

    return violations


# ---------------------------------------------------------------------------
# Check 2 — Chain-of-custody completeness
# ---------------------------------------------------------------------------

def _check_chain_of_custody(conn) -> List[Dict[str, Any]]:
    """Flag chain_of_custody records missing prompt_sha256 or signature."""
    violations: List[Dict[str, Any]] = []

    if not _table_exists(conn, "rag_provenance_ledger"):
        return violations

    rows = conn.execute(
        """
        SELECT id, chunk_uuid, prompt_sha256, signature
        FROM   rag_provenance_ledger
        WHERE  event_type = 'chain_of_custody'
          AND  (prompt_sha256 IS NULL OR prompt_sha256 = ''
             OR signature     IS NULL OR signature     = '')
        """
    ).fetchall()

    for row in rows:
        ledger_id, chunk_uuid, prompt_sha256, signature = row
        missing = []
        if not prompt_sha256:
            missing.append("prompt_sha256")
        if not signature:
            missing.append("signature")
        violations.append(
            {
                "type": "custody_incomplete",
                "ledger_id": ledger_id,
                "chunk_uuid": chunk_uuid,
                "missing_fields": missing,
            }
        )

    return violations


# ---------------------------------------------------------------------------
# Check 3 — Classification drift
# ---------------------------------------------------------------------------

def _check_classification_drift(conn) -> List[Dict[str, Any]]:
    """Detect parent_doc_uuid with multiple distinct classification_labels."""
    violations: List[Dict[str, Any]] = []

    if not _table_exists(conn, "rag_provenance_ledger"):
        return violations

    rows = conn.execute(
        """
        SELECT   parent_doc_uuid,
                 COUNT(DISTINCT classification_label) AS label_count,
                 MIN(classification_label)            AS label_min,
                 MAX(classification_label)            AS label_max
        FROM     rag_provenance_ledger
        WHERE    event_type = 'ingest'
          AND    parent_doc_uuid IS NOT NULL
          AND    classification_label IS NOT NULL
        GROUP BY parent_doc_uuid
        HAVING   COUNT(DISTINCT classification_label) > 1
        """
    ).fetchall()

    for row in rows:
        parent_doc_uuid, label_count, label_min, label_max = row
        violations.append(
            {
                "type": "classification_drift",
                "parent_doc_uuid": parent_doc_uuid,
                "distinct_labels": label_count,
                "label_min": label_min,
                "label_max": label_max,
            }
        )

    return violations


# ---------------------------------------------------------------------------
# Emit findings
# ---------------------------------------------------------------------------

def _emit_oracle_prediction(conn, violation_type: str, subject_id: str,
                            violation: Dict[str, Any], now: str) -> str:
    pred_id = _new_id()
    text_map = {
        "sha256_mismatch": (
            f"SHA-256 mismatch on chunk {violation.get('chunk_uuid', '?')}: "
            f"stored={violation.get('stored_hash', '?')} computed={violation.get('computed_hash', '?')}. "
            "Possible data tampering or silent corruption."
        ),
        "custody_incomplete": (
            f"Chain-of-custody record {violation.get('ledger_id', '?')} missing: "
            f"{', '.join(violation.get('missing_fields', []))}. "
            "AIA-002 compliance requirement not satisfied."
        ),
        "classification_drift": (
            f"Document {subject_id} has {violation.get('distinct_labels', '?')} distinct "
            f"classification labels ({violation.get('label_min', '?')} … "
            f"{violation.get('label_max', '?')}). "
            "Potential classification spillage risk."
        ),
    }
    prediction_text = text_map.get(
        violation_type,
        f"Provenance violation detected: {violation_type}",
    )

    try:
        conn.execute(
            """
            INSERT INTO oracle_predictions
                (id, lens_id, lens_name, subject_type, subject_id,
                 prediction_type, prediction_text, confidence, severity,
                 horizon_days, evidence_json, classification, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pred_id,
                _LENS_ID,
                _LENS_NAME,
                "pipeline",
                subject_id,
                f"provenance_violation::{violation_type}",
                prediction_text,
                0.95,
                "critical",
                0,
                json.dumps(violation, ensure_ascii=False),
                _CLASSIFICATION,
                now,
            ),
        )
    except Exception as exc:
        print(f"  [aidp_monitor] oracle_predictions insert failed: {exc}")
        pred_id = ""

    return pred_id


def _emit_kanban_task(conn, violation_type: str, subject_id: str,
                      pred_id: str, violation: Dict[str, Any], now: str) -> None:
    task_id = f"aidp-viol-{uuid.uuid4().hex[:8]}"
    title_map = {
        "sha256_mismatch": f"[AIDP] SHA-256 mismatch — chunk {subject_id[:12]}",
        "custody_incomplete": f"[AIDP] Incomplete chain-of-custody — record {subject_id[:12]}",
        "classification_drift": f"[AIDP] Classification drift — doc {subject_id[:12]}",
    }
    title = title_map.get(violation_type, f"[AIDP] Provenance violation: {violation_type}")
    desc = (
        f"Provenance integrity violation detected by aidp_monitor reflex.\n\n"
        f"Type: {violation_type}\nSubject: {subject_id}\n\n"
        f"Evidence:\n{json.dumps(violation, indent=2, ensure_ascii=False)}\n\n"
        f"Source prediction: {pred_id}"
    )
    try:
        conn.execute(
            """
            INSERT INTO kanban_tasks
                (id, title, description, task_type, priority, status,
                 executor_type, source_prediction_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                title,
                desc,
                "chore",
                "high",
                "suggested",
                "claude_cli",
                pred_id or None,
                now,
                now,
            ),
        )
    except Exception as exc:
        print(f"  [aidp_monitor] kanban_tasks insert failed: {exc}")


def _emit_findings(violations: List[Tuple[str, str, Dict[str, Any]]], now: str) -> int:
    """Emit oracle_predictions + kanban_tasks for each violation.

    violations: list of (violation_type, subject_id, violation_dict)
    Returns count of successfully emitted predictions.
    """
    if not violations:
        return 0

    emitted = 0
    try:
        conn = _get_conn()
        for violation_type, subject_id, violation in violations:
            pred_id = _emit_oracle_prediction(conn, violation_type, subject_id, violation, now)
            if pred_id:
                _emit_kanban_task(conn, violation_type, subject_id, pred_id, violation, now)
                emitted += 1
        # Commit if using a connection that requires it
        try:
            conn.commit()
        except Exception:
            pass
    except Exception as exc:
        print(f"  [aidp_monitor] DB connection failed for emit: {exc}")

    return emitted


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the AIDP Monitor Reflex."""
    now = _utcnow_iso()
    print("  [aidp_monitor] starting provenance integrity scan…")

    sha256_viols: List[Dict[str, Any]] = []
    custody_viols: List[Dict[str, Any]] = []
    drift_viols: List[Dict[str, Any]] = []
    errors: List[str] = []

    try:
        conn = _get_conn()

        print("  [aidp_monitor] check 1: SHA-256 integrity…")
        sha256_viols = _check_sha256_integrity(conn)
        print(f"    → {len(sha256_viols)} mismatch(es)")

        print("  [aidp_monitor] check 2: chain-of-custody completeness…")
        custody_viols = _check_chain_of_custody(conn)
        print(f"    → {len(custody_viols)} incomplete record(s)")

        print("  [aidp_monitor] check 3: classification drift…")
        drift_viols = _check_classification_drift(conn)
        print(f"    → {len(drift_viols)} drift event(s)")

    except Exception as exc:
        errors.append(str(exc))
        print(f"  [aidp_monitor] scan error: {exc}")

    # Build unified violation list for emission
    violations: List[Tuple[str, str, Dict[str, Any]]] = []
    for v in sha256_viols:
        violations.append(("sha256_mismatch", v.get("chunk_uuid", "unknown"), v))
    for v in custody_viols:
        violations.append(("custody_incomplete", v.get("ledger_id", "unknown"), v))
    for v in drift_viols:
        violations.append(("classification_drift", v.get("parent_doc_uuid", "unknown"), v))

    total_violations = len(violations)

    emitted = 0
    if total_violations > 0:
        print(f"  [aidp_monitor] emitting {total_violations} finding(s) to oracle+kanban…")
        emitted = _emit_findings(violations, now)

    print(
        f"  [aidp_monitor] complete — {total_violations} violation(s), "
        f"{emitted} prediction(s) emitted"
    )

    return {
        "success": True,
        "metric_value": float(total_violations),
        "details": {
            "sha256_mismatches": len(sha256_viols),
            "custody_incomplete": len(custody_viols),
            "classification_drift": len(drift_viols),
            "total_violations": total_violations,
            "predictions_emitted": emitted,
            "errors": errors,
            "scanned_at": now,
        },
    }
