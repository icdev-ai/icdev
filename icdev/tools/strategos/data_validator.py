# CUI // SP-CTI
"""Data validator — routes adversarial signals to quarantine or clean DB tables.

Wraps AdversarialValidator and persists results:
  - high_risk signals (blocked/flagged) → sg_quarantine table
  - clean signals                        → sg_clean_signals table

NIST 800-53: SI-10, AU-9
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent.parent
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.db.storage import get_connection  # noqa: E402
from icdev.tools.strategos.adversarial_validator import AdversarialValidator  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.strategos.data_validator")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sg_quarantine (
    id           TEXT PRIMARY KEY,
    signal_id    TEXT NOT NULL,
    source_type  TEXT,
    risk_reason  TEXT,
    bias_score   REAL,
    deepfake_score REAL,
    manipulation_score REAL,
    details_json TEXT,
    quarantined_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sg_clean_signals (
    id           TEXT PRIMARY KEY,
    signal_id    TEXT NOT NULL,
    source_type  TEXT,
    validated_at TEXT NOT NULL
);
"""

_RISK_THRESHOLD = 0.5  # flagged/blocked → quarantine


def _ensure_tables(conn) -> None:
    for stmt in _SCHEMA.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()


def route_signal(signal: Dict[str, Any], validator: AdversarialValidator | None = None) -> Dict[str, Any]:
    """Validate one signal and route to quarantine or clean table. Returns routing decision."""
    if validator is None:
        validator = AdversarialValidator()

    result = validator.validate(signal)
    now = datetime.now(timezone.utc).isoformat()

    with get_connection() as conn:
        _ensure_tables(conn)

        max_score = max(result.bias_score, result.deepfake_score, result.manipulation_score)
        is_high_risk = result.flagged or max_score >= _RISK_THRESHOLD

        if is_high_risk:
            row_id = str(uuid.uuid4())
            conn.execute(
                "INSERT OR IGNORE INTO sg_quarantine "
                "(id, signal_id, source_type, risk_reason, bias_score, deepfake_score, "
                "manipulation_score, details_json, quarantined_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row_id,
                    result.signal_id,
                    result.source_type,
                    ",".join(result.issues) if result.issues else result.status,
                    result.bias_score,
                    result.deepfake_score,
                    result.manipulation_score,
                    json.dumps({}),
                    now,
                ),
            )
            conn.commit()
            destination = "quarantine"
            logger.warning(
                "Signal %s routed to quarantine (issues=%s score=%.2f)",
                result.signal_id,
                result.issues,
                max_score,
            )
        else:
            row_id = str(uuid.uuid4())
            conn.execute(
                "INSERT OR IGNORE INTO sg_clean_signals (id, signal_id, source_type, validated_at) "
                "VALUES (?, ?, ?, ?)",
                (row_id, result.signal_id, result.source_type, now),
            )
            conn.commit()
            destination = "clean"
            logger.info("Signal %s routed to clean table", result.signal_id)

    return {
        "signal_id": result.signal_id,
        "destination": destination,
        "status": result.status,
        "issues": result.issues,
        "bias_score": result.bias_score,
        "deepfake_score": result.deepfake_score,
        "manipulation_score": result.manipulation_score,
    }


def route_batch(signals: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Route a batch of signals. Returns summary counts."""
    validator = AdversarialValidator()
    quarantine_count = 0
    clean_count = 0
    results = []

    for sig in signals:
        decision = route_signal(sig, validator)
        results.append(decision)
        if decision["destination"] == "quarantine":
            quarantine_count += 1
        else:
            clean_count += 1

    return {
        "total": len(signals),
        "quarantine": quarantine_count,
        "clean": clean_count,
        "results": results,
    }
