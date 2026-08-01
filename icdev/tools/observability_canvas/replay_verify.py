# CUI // SP-CTI — ICDEV Observability Design Canvas: SDC Replay Verifier
"""
ODC closed-loop hook: replay an SDC attack path and verify TTP detection coverage.

Deterministic — no LLM.  Coverage is assessed against two signals:
  1. Sigma rule template index  (_TECHNIQUE_DETECTIONS in sigma_generator)
  2. cmp-baseline nodes in observability_designs (covered field in config_json)

States written to od_ttp_coverage:
  full    — Sigma snippet exists AND a design baseline marks the TTP as covered
  partial — only one signal present, or baseline exists but covered=false
  none    — absent from both signals

One od_audit row is written per TTP for the NIST AU append-only trail.

CLI:
  python tools/observability_canvas/replay_verify.py T1059 T1078 --json
  python tools/observability_canvas/replay_verify.py T1059 --design-id <id> --json
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

_ICDEV_ROOT = Path(__file__).resolve().parents[2]
if str(_ICDEV_ROOT) not in sys.path:
    sys.path.insert(0, str(_ICDEV_ROOT))


# ── DB helpers ────────────────────────────────────────────────────────────────


def _get_conn():
    from tools.observability_canvas.db.init_db import get_connection, init_db

    init_db()
    return get_connection()


# od_ttp_coverage is owned by init_db.SCHEMA and (re)created on every
# _get_conn() → init_db() call. The DDL below is a defensive fallback for DBs
# that predate that SCHEMA entry; it is materialized at most once per process
# (this flag) instead of on every verify_path() call.
_schema_ensured = False


def _ensure_coverage_schema(conn) -> None:
    """Materialize od_ttp_coverage once per process as a defensive fallback.

    Schema creation is owned by ``init_db`` (od_ttp_coverage is in its SCHEMA).
    This runs its DDL only the first time it is called per process — gated by the
    module-level ``_schema_ensured`` flag — so verify_path() no longer re-issues
    CREATE TABLE / CREATE INDEX on every invocation.
    """
    global _schema_ensured
    if _schema_ensured:
        return
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS od_ttp_coverage (
            id              TEXT PRIMARY KEY,
            ttp_id          TEXT NOT NULL,
            design_id       TEXT DEFAULT '',
            state           TEXT NOT NULL
                                CHECK (state IN ('full', 'partial', 'none')),
            sigma_match     INTEGER NOT NULL DEFAULT 0,
            baseline_match  INTEGER NOT NULL DEFAULT 0,
            detail          TEXT DEFAULT '{}',
            verified_at     TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_od_ttp_cov_ttp ON od_ttp_coverage(ttp_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_od_ttp_cov_state ON od_ttp_coverage(state)"
    )
    conn.commit()
    _schema_ensured = True


# ── Coverage signals ──────────────────────────────────────────────────────────


def _sigma_known_ttps() -> set[str]:
    """Return the set of TTP IDs that have Sigma detection snippets."""
    try:
        from tools.observability_canvas.sigma_generator import _TECHNIQUE_DETECTIONS  # type: ignore[attr-defined]

        return set(_TECHNIQUE_DETECTIONS.keys())
    except Exception:
        return set()


def _design_baseline_states(conn) -> dict[str, bool]:
    """
    Scan all observability_designs for cmp-baseline nodes.

    Returns {ttp_id: covered_bool}.  A TTP is True if *any* design baseline
    marks it covered=true.  Silences only JSON decode errors on malformed rows.
    """
    states: dict[str, bool] = {}
    rows = conn.execute(
        "SELECT graph_json FROM observability_designs"
    ).fetchall()
    for row in rows:
        try:
            graph = json.loads(row["graph_json"] or "{}")
        except json.JSONDecodeError:
            continue
        for node in graph.get("nodes", []):
            if node.get("type") != "cmp-baseline":
                continue
            try:
                cfg = json.loads(node.get("config_json", "{}") or "{}")
            except json.JSONDecodeError:
                continue
            for tech in cfg.get("techniques", []):
                tid = tech.get("id", "")
                if not tid:
                    continue
                covered = bool(tech.get("covered", False))
                states[tid] = states.get(tid, False) or covered
    return states


# ── State computation ─────────────────────────────────────────────────────────


def _compute_state(
    ttp_id: str,
    sigma_ttps: set[str],
    baseline: dict[str, bool],
) -> tuple[str, bool, bool]:
    """Return (state, sigma_match, baseline_match)."""
    sigma_match = ttp_id in sigma_ttps
    baseline_match = ttp_id in baseline
    baseline_covered = baseline.get(ttp_id, False)

    if sigma_match and baseline_covered:
        state = "full"
    elif sigma_match or baseline_match:
        state = "partial"
    else:
        state = "none"

    return state, sigma_match, baseline_match


# ── Public API ────────────────────────────────────────────────────────────────


def verify_path(ttp_ids: Sequence[str], design_id: str = "") -> dict:
    """Verify detection coverage for a list of MITRE ATT&CK technique IDs.

    Writes one ``od_ttp_coverage`` row and one ``od_audit`` row per TTP.

    Args:
        ttp_ids:   Ordered list of technique IDs (e.g. ["T1059", "T1078"]).
        design_id: Optional design context stored in the audit row.

    Returns::

        {
            "path":    ["T1059", ...],
            "results": [{"ttp_id": str, "state": str, "coverage_row_id": str}, ...],
            "summary": {"full": N, "partial": N, "none": N, "total": N},
        }
    """
    conn = _get_conn()
    try:
        _ensure_coverage_schema(conn)
        sigma_ttps = _sigma_known_ttps()
        baseline = _design_baseline_states(conn)

        now = datetime.now(timezone.utc).isoformat()
        results: list[dict] = []

        for ttp_id in ttp_ids:
            state, sigma_match, baseline_match = _compute_state(
                ttp_id, sigma_ttps, baseline
            )
            row_id = str(uuid.uuid4())
            detail = json.dumps(
                {
                    "sigma_match": sigma_match,
                    "baseline_match": baseline_match,
                    "baseline_covered": baseline.get(ttp_id),
                }
            )

            conn.execute(
                "INSERT INTO od_ttp_coverage"
                " (id, ttp_id, design_id, state, sigma_match, baseline_match, detail, verified_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    row_id,
                    ttp_id,
                    design_id or "",
                    state,
                    1 if sigma_match else 0,
                    1 if baseline_match else 0,
                    detail,
                    now,
                ),
            )

            conn.execute(
                "INSERT INTO od_audit"
                " (design_id, actor, action, detail, classification, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    design_id or "",
                    "replay_verify",
                    "ttp_coverage_check",
                    json.dumps(
                        {"ttp_id": ttp_id, "state": state, "coverage_row_id": row_id}
                    ),
                    "CUI // SP-CTI",
                    now,
                ),
            )

            results.append(
                {"ttp_id": ttp_id, "state": state, "coverage_row_id": row_id}
            )

        conn.commit()

        summary: dict[str, int] = {"full": 0, "partial": 0, "none": 0, "total": len(results)}
        for r in results:
            summary[r["state"]] += 1

        return {"path": list(ttp_ids), "results": results, "summary": summary}

    finally:
        conn.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Verify TTP detection coverage for an SDC attack path"
    )
    parser.add_argument("ttp_ids", nargs="+", metavar="TTP_ID", help="MITRE ATT&CK technique IDs")
    parser.add_argument("--design-id", default="", help="Optional design context ID")
    parser.add_argument("--json", dest="as_json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    result = verify_path(args.ttp_ids, design_id=args.design_id)

    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Path:    {result['path']}")
        print(f"Summary: {result['summary']}")
        for r in result["results"]:
            print(f"  {r['ttp_id']:12s}  {r['state']}")
