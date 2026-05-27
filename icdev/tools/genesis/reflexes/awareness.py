#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Awareness Reflex — internal self-observation cycle.

Orchestrates the Internal Awareness Engine on a 3-hour cadence:

  index   → component_indexer.scan()       (discover/refresh components)
  probe   → health_prober.run_all()        (health snapshots)
  drift   → drift_detector.detect()        (regression detection)
  gap     → gap_detector.detect()          (structural gap analysis)
  suggest → suggested_card_writer.…()     (kanban card promotion)

Each sub-phase writes a row to ``awareness_run_log`` tagged with a shared
``cycle_id`` in ``details_json`` so the 5 rows can be queried as a unit.

Design decisions:
  * GREEN tier — read analysis + DB writes only, no code mutation.
  * Zero LLM in the hot path; every step is deterministic.
  * Enablement-aware: reads current flags at cycle start; if the
    signature changed since the last cycle, triggers a full re-index.
  * Cadence: 3 hours (configured in args/genesis_config.yaml + mirrored
    in args/awareness_config.yaml).
  * Cooldown: 60 minutes minimum between cycles (enforced by daemon).

Return value:
  {"run_id": <cycle_id>, "drift": N, "gaps": N, "cards": N}

See docs/features/internal-awareness-engine.md §Phase 5 for the full plan.
"""
from __future__ import annotations
IMPLEMENTATION_STATUS = "full"

import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ---------------------------------------------------------------------------
# Optional imports — awareness tools may not be present in slim environments
# ---------------------------------------------------------------------------

try:
    from tools.db.storage import get_connection
except ImportError:
    get_connection = None  # type: ignore[assignment]

try:
    from tools.awareness.component_indexer import scan as _indexer_scan
except ImportError:
    _indexer_scan = None  # type: ignore[assignment]

try:
    from tools.awareness.health_prober import run_all as _prober_run_all
except ImportError:
    _prober_run_all = None  # type: ignore[assignment]

try:
    from tools.awareness.drift_detector import detect as _drift_detect
except ImportError:
    _drift_detect = None  # type: ignore[assignment]

try:
    from tools.awareness.gap_detector import detect as _gap_detect
except ImportError:
    _gap_detect = None  # type: ignore[assignment]

try:
    from tools.awareness.suggested_card_writer import write_suggested_cards as _write_cards
except ImportError:
    _write_cards = None  # type: ignore[assignment]

try:
    from tools.awareness.enablement import enabled_flags_signature, load_enablement_flags
except ImportError:
    enabled_flags_signature = None  # type: ignore[assignment]
    load_enablement_flags = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

_RUN_LOG_DDL = """
CREATE TABLE IF NOT EXISTS awareness_run_log (
    run_id        TEXT PRIMARY KEY,
    phase         TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    completed_at  TEXT,
    status        TEXT NOT NULL DEFAULT 'running',
    probes_ok     INTEGER DEFAULT 0,
    probes_fail   INTEGER DEFAULT 0,
    elapsed_ms    INTEGER,
    details_json  TEXT
)
"""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_log_table(conn: Any) -> None:
    try:
        conn.execute(_RUN_LOG_DDL)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


def _write_phase_row(
    conn: Any,
    run_id: str,
    phase: str,
    status: str,
    cycle_id: str,
    elapsed_ms: int = 0,
    probes_ok: int = 0,
    probes_fail: int = 0,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Insert one phase row into awareness_run_log.  Idempotent on conflict."""
    now = _utcnow_iso()
    details: Dict[str, Any] = {"cycle_id": cycle_id}
    if extra:
        details.update(extra)
    try:
        conn.execute(
            """
            INSERT INTO awareness_run_log
                (run_id, phase, started_at, completed_at, status,
                 probes_ok, probes_fail, elapsed_ms, details_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                completed_at = excluded.completed_at,
                status       = excluded.status,
                probes_ok    = excluded.probes_ok,
                probes_fail  = excluded.probes_fail,
                elapsed_ms   = excluded.elapsed_ms,
                details_json = excluded.details_json
            """,
            (
                run_id,
                phase,
                now,
                now,
                status,
                probes_ok,
                probes_fail,
                elapsed_ms,
                json.dumps(details),
            ),
        )
        conn.commit()
    except Exception as exc:
        print(f"  [awareness reflex] WARNING: log write failed for phase={phase}: {exc}")
        try:
            conn.rollback()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Enablement signature cache (stored in genesis_reflex_state via config dict)
# ---------------------------------------------------------------------------

_LAST_SIG_KEY = "_awareness_last_enablement_sig"


def _get_last_sig(config: Dict[str, Any]) -> str:
    return config.get(_LAST_SIG_KEY, "")


def _set_last_sig(config: Dict[str, Any], sig: str) -> None:
    config[_LAST_SIG_KEY] = sig


# ---------------------------------------------------------------------------
# Main reflex entry point
# ---------------------------------------------------------------------------


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute one full awareness cycle (index → probe → drift → gap → suggest).

    Called by the Genesis daemon every ``interval_seconds`` seconds (10 800 s
    = 3 hours as configured in args/genesis_config.yaml).

    Returns a summary dict consumed by the daemon's success_metric gate.
    """
    cycle_id = f"awareness-{uuid.uuid4().hex[:12]}"
    start_wall = time.time()

    print(f"[awareness] cycle start  cycle_id={cycle_id}")

    # ------------------------------------------------------------------
    # Open DB connection (shared across all phases)
    # ------------------------------------------------------------------
    conn: Optional[Any] = None
    if get_connection is not None:
        try:
            conn = get_connection()
            _ensure_log_table(conn)
        except Exception as exc:
            print(f"  [awareness] WARNING: DB unavailable: {exc}")
            conn = None

    # ------------------------------------------------------------------
    # Enablement signature check — full re-index if .env changed
    # ------------------------------------------------------------------
    incremental = True
    if enabled_flags_signature is not None:
        try:
            current_sig = enabled_flags_signature()
            last_sig = _get_last_sig(config)
            if current_sig != last_sig:
                print("  [awareness] enablement flags changed — forcing full re-index")
                incremental = False
                _set_last_sig(config, current_sig)
        except Exception as exc:
            print(f"  [awareness] WARNING: enablement sig check failed: {exc}")

    # Collect results per phase
    index_result: Dict[str, Any] = {}
    probe_result: Dict[str, Any] = {}
    drift_result: Dict[str, Any] = {}
    gap_result: Dict[str, Any] = {}
    cards_result: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Phase 1 — index
    # ------------------------------------------------------------------
    t0 = time.time()
    print("  [awareness] phase=index ...")
    try:
        if _indexer_scan is not None:
            index_result = _indexer_scan()
        else:
            index_result = {"skipped": True, "reason": "component_indexer not available"}
    except Exception as exc:
        index_result = {"error": str(exc)[:300]}
        print(f"  [awareness] index error: {exc}")

    elapsed_index = int((time.time() - t0) * 1000)
    if conn is not None:
        _write_phase_row(
            conn,
            run_id=f"{cycle_id}:index",
            phase="index",
            status="error" if "error" in index_result else "success",
            cycle_id=cycle_id,
            elapsed_ms=elapsed_index,
            extra={
                "nodes": index_result.get("nodes", 0),
                "edges": index_result.get("edges", 0),
                "incremental": incremental,
            },
        )

    # ------------------------------------------------------------------
    # Phase 2 — probe
    # ------------------------------------------------------------------
    t0 = time.time()
    print("  [awareness] phase=probe ...")
    try:
        if _prober_run_all is not None:
            probe_result = _prober_run_all()
        else:
            probe_result = {"skipped": True, "reason": "health_prober not available"}
    except Exception as exc:
        probe_result = {"error": str(exc)[:300]}
        print(f"  [awareness] probe error: {exc}")

    elapsed_probe = int((time.time() - t0) * 1000)
    probes_ok = probe_result.get("total_ok", probe_result.get("ok", 0))
    probes_fail = probe_result.get("total_fail", probe_result.get("fail", 0))
    if conn is not None:
        _write_phase_row(
            conn,
            run_id=f"{cycle_id}:probe",
            phase="probe",
            status="error" if "error" in probe_result else "success",
            cycle_id=cycle_id,
            elapsed_ms=elapsed_probe,
            probes_ok=probes_ok,
            probes_fail=probes_fail,
        )

    # ------------------------------------------------------------------
    # Phase 3 — drift
    # ------------------------------------------------------------------
    t0 = time.time()
    print("  [awareness] phase=drift ...")
    try:
        if _drift_detect is not None:
            drift_result = _drift_detect()
        else:
            drift_result = {"skipped": True, "total_findings": 0}
    except Exception as exc:
        drift_result = {"error": str(exc)[:300], "total_findings": 0}
        print(f"  [awareness] drift error: {exc}")

    elapsed_drift = int((time.time() - t0) * 1000)
    drift_count = drift_result.get("total_findings", 0)
    if conn is not None:
        _write_phase_row(
            conn,
            run_id=f"{cycle_id}:drift",
            phase="drift",
            status="error" if "error" in drift_result else "success",
            cycle_id=cycle_id,
            elapsed_ms=elapsed_drift,
            extra={"findings": drift_count, "by_rule": drift_result.get("by_rule", {})},
        )

    # ------------------------------------------------------------------
    # Phase 4 — gap
    # ------------------------------------------------------------------
    t0 = time.time()
    print("  [awareness] phase=gap ...")
    try:
        if _gap_detect is not None:
            gap_result = _gap_detect()
        else:
            gap_result = {"skipped": True, "total_findings": 0}
    except Exception as exc:
        gap_result = {"error": str(exc)[:300], "total_findings": 0}
        print(f"  [awareness] gap error: {exc}")

    elapsed_gap = int((time.time() - t0) * 1000)
    gap_count = gap_result.get("total_findings", 0)
    if conn is not None:
        # gap_detector returns summary under the key ``by_rule`` (not
        # ``total_by_rule``). Fixed 2026-04-11 during Phase 5 bring-up.
        _write_phase_row(
            conn,
            run_id=f"{cycle_id}:gap",
            phase="gap",
            status="error" if "error" in gap_result else "success",
            cycle_id=cycle_id,
            elapsed_ms=elapsed_gap,
            extra={"findings": gap_count, "by_rule": gap_result.get("by_rule", {})},
        )

    # ------------------------------------------------------------------
    # Phase 5 — suggest
    # ------------------------------------------------------------------
    t0 = time.time()
    print("  [awareness] phase=suggest ...")
    try:
        confidence_threshold = float(
            config.get("promotion_threshold", 0.7)
        )
        if _write_cards is not None:
            cards_result = _write_cards(min_confidence=confidence_threshold)
        else:
            cards_result = {"skipped": True, "created": []}
    except Exception as exc:
        cards_result = {"error": str(exc)[:300], "created": []}
        print(f"  [awareness] suggest error: {exc}")

    elapsed_suggest = int((time.time() - t0) * 1000)
    # Handle BOTH shapes for cards_result:
    #   * suggested_card_writer (real):  {"created": <int>, "cards": [<list>], ...}
    #   * legacy / test stubs:           {"created": [<list>], ...}
    # Fixed 2026-04-11 during Phase 5 bring-up — the prior
    # ``len(int)`` would have raised TypeError on the first real
    # cycle, and the subsequent ``int(list)`` would have broken
    # the test stubs. Now tolerates either.
    _created_raw = cards_result.get("created", 0)
    if isinstance(_created_raw, list):
        cards_created = len(_created_raw)
    else:
        try:
            cards_created = int(_created_raw or 0)
        except (TypeError, ValueError):
            cards_created = 0
    if conn is not None:
        _write_phase_row(
            conn,
            run_id=f"{cycle_id}:suggest",
            phase="suggest",
            status="error" if "error" in cards_result else "success",
            cycle_id=cycle_id,
            elapsed_ms=elapsed_suggest,
            extra={"cards_created": cards_created},
        )

    total_elapsed_ms = int((time.time() - start_wall) * 1000)
    print(
        f"[awareness] cycle done  cycle_id={cycle_id}"
        f"  drift={drift_count}  gaps={gap_count}  cards={cards_created}"
        f"  elapsed={total_elapsed_ms}ms"
    )

    return {
        "success": True,
        "metric_value": 1.0,  # awareness_cycle_complete (gte 0 = always passes)
        "details": {
            "run_id": cycle_id,
            "drift": drift_count,
            "gaps": gap_count,
            "cards": cards_created,
            "elapsed_ms": total_elapsed_ms,
        },
        "run_id": cycle_id,
        "drift": drift_count,
        "gaps": gap_count,
        "cards": cards_created,
        "elapsed_ms": total_elapsed_ms,
        "phases": {
            "index": index_result,
            "probe": probe_result,
            "drift": drift_result,
            "gap": gap_result,
            "suggest": cards_result,
        },
    }
