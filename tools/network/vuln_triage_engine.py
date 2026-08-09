# CUI // SP-CTI
"""PVM — Vulnerability Triage Engine (pvm-tri-01).

Scores CVE advisories using a 4-factor priority formula, applies Bayesian
reranking via optimal_compliance_order(), writes results to nc_triage_queue,
and appends to nc_nqe_audit_log.

4-Factor Formula
----------------
    kev_exploited         = 1.0 if exploited_in_wild else 0.0
    asset_criticality_norm = avg(nc_attack_surface.criticality) / 5.0
    network_exposure_norm  = count(reachable=1) / max(total_devices, 1)
    temporal_urgency       = min(days_since_published / 180.0, 1.0) * cvss_score / 10.0

    priority_score = 0.40*kev + 0.25*criticality + 0.20*exposure + 0.15*urgency

HITL Gates (configurable in args/network_canvas_config.yaml under pvm:)
---------
    >= hitl_threshold (default 0.75)  → status=pending, requires human approval
    <  auto_approve_threshold (0.40)  → status=approved, auto_approved=1
    else                               → status=pending, auto_approved=0

Public API
----------
    score_advisories(advisory_ids=None) -> dict
    get_triage_queue(status=None, limit=100) -> list[dict]
    approve_advisory(advisory_id, approved_by) -> dict
    defer_advisory(advisory_id, approved_by) -> dict

CLI
---
    python tools/network/vuln_triage_engine.py --score --json
    python tools/network/vuln_triage_engine.py --score --advisory-ids 1,2,3 --json
    python tools/network/vuln_triage_engine.py --queue --status pending --json
    python tools/network/vuln_triage_engine.py --approve 42 --by analyst@example.com --json
    python tools/network/vuln_triage_engine.py --defer 42 --by analyst@example.com --json
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sys

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger
from tools.network.db.init_db import get_connection

logger = get_logger(__name__)

_BASE_DIR = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _BASE_DIR / "args" / "network_canvas_config.yaml"

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    try:
        import yaml  # type: ignore
        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:
        return {}


def _pvm_config() -> dict:
    cfg = _load_config()
    return cfg.get("pvm", {}) if isinstance(cfg, dict) else {}


def _hitl_threshold() -> float:
    return float(_pvm_config().get("triage_hitl_threshold", 0.75))


def _auto_approve_threshold() -> float:
    return float(_pvm_config().get("triage_auto_approve_threshold", 0.40))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _days_since(date_str: str | None) -> float:
    if not date_str:
        return 0.0
    try:
        pub = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        pub = pub.replace(tzinfo=timezone.utc) if pub.tzinfo is None else pub
        delta = datetime.now(timezone.utc) - pub
        return max(0.0, delta.days)
    except Exception:
        return 0.0


def _attack_surface_stats(conn, advisory_id: int) -> tuple[float, float]:
    """Return (asset_criticality_norm, network_exposure_norm) from nc_attack_surface."""
    try:
        row = conn.execute(
            "SELECT AVG(criticality) AS avg_crit, "
            "SUM(CASE WHEN reachable=1 THEN 1 ELSE 0 END) AS reachable_count, "
            "COUNT(*) AS total "
            "FROM nc_attack_surface WHERE advisory_id=%s",
            (advisory_id,),
        ).fetchone()
        if row and row[2] and row[2] > 0:
            avg_crit = row[0] if row[0] is not None else 3.0
            reachable_count = row[1] or 0
            total = row[2]
            return avg_crit / 5.0, reachable_count / max(total, 1)
    except Exception as exc:
        logger.debug("attack_surface_stats error for advisory %s: %s", advisory_id, exc)
    # Default when no surface data available
    return 0.5, 0.0


def _compute_priority(adv: dict, asset_crit_norm: float, net_exp_norm: float) -> tuple[float, dict]:
    """Compute priority_score and factor breakdown dict."""
    exploited_raw = str(adv.get("exploited_in_wild", "0") or "0")
    kev = 1.0 if exploited_raw in ("1", "true", "True", "yes") else 0.0

    cvss_raw = adv.get("cvss_score") or adv.get("cvss_base_score") or 0.0
    try:
        cvss = float(cvss_raw)
    except (TypeError, ValueError):
        cvss = 0.0

    published_date = adv.get("published_date") or adv.get("created_at") or ""
    days = _days_since(published_date)
    urgency = min(days / 180.0, 1.0) * (cvss / 10.0)

    score = (
        kev * 0.40
        + asset_crit_norm * 0.25
        + net_exp_norm * 0.20
        + urgency * 0.15
    )
    score = round(max(0.0, min(1.0, score)), 4)

    rationale = {
        "kev": round(kev, 4),
        "criticality": round(asset_crit_norm, 4),
        "exposure": round(net_exp_norm, 4),
        "urgency": round(urgency, 4),
        "formula": "0.40*kev + 0.25*crit + 0.20*exp + 0.15*urg",
    }
    return score, rationale


def _determine_status(score: float) -> tuple[str, int]:
    """Return (status, auto_approved) based on HITL thresholds."""
    if score < _auto_approve_threshold():
        return "approved", 1
    return "pending", 0


def _upsert_triage_row(conn, row: dict) -> None:
    now = _now()
    conn.execute(
        """INSERT OR REPLACE INTO nc_triage_queue
           (advisory_id, priority_score, kev_exploited, asset_criticality_norm,
            network_exposure_norm, temporal_urgency, rank, rationale_json,
            status, auto_approved, approved_by, approved_at, created_at, updated_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
               COALESCE((SELECT created_at FROM nc_triage_queue WHERE advisory_id=%s), %s),
               %s)""",
        (
            row["advisory_id"],
            row["priority_score"],
            row["kev_exploited"],
            row["asset_criticality_norm"],
            row["network_exposure_norm"],
            row["temporal_urgency"],
            row.get("rank"),
            row["rationale_json"],
            row["status"],
            row["auto_approved"],
            row.get("approved_by"),
            row.get("approved_at"),
            row["advisory_id"],
            now,
            now,
        ),
    )


def _append_audit_log(conn, action: str, advisory_id: int, confidence: float = 1.0) -> None:
    try:
        conn.execute(
            """INSERT INTO nc_nqe_audit_log
               (action, advisory_id, input_text, nql_generated, data_source, confidence, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (action, advisory_id, f"triage advisory {advisory_id}", "", "pvm_triage", confidence, _now()),
        )
    except Exception as exc:
        logger.debug("audit_log insert failed: %s", exc)


def _apply_bayesian_ranks(conn, scored_ids: list[int]) -> None:
    """Call Bayesian teacher for optimal ordering; update nc_triage_queue.rank."""
    if not scored_ids:
        return
    control_ids = [str(i) for i in scored_ids]
    try:
        from tools.intelligence.bayesian_teacher import optimal_compliance_order
        result = optimal_compliance_order(control_ids, project_id="ndc-pvm")
        ordered = result.get("ordered_controls", control_ids)
    except Exception as exc:
        logger.warning("Bayesian reranking failed, using priority_score fallback: %s", exc)
        # Fallback: order by priority_score DESC
        rows = conn.execute(
            "SELECT advisory_id FROM nc_triage_queue "
            "WHERE advisory_id IN ({}) ORDER BY priority_score DESC".format(
                ",".join("?" * len(scored_ids))
            ),
            scored_ids,
        ).fetchall()
        ordered = [str(r[0]) for r in rows]

    for rank, cid in enumerate(ordered, start=1):
        try:
            adv_id = int(cid.replace("ADV-", ""))
        except ValueError:
            adv_id = int(cid) if cid.isdigit() else None
        if adv_id is not None:
            conn.execute(
                "UPDATE nc_triage_queue SET rank=%s, updated_at=%s WHERE advisory_id=%s",
                (rank, _now(), adv_id),
            )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_advisories(advisory_ids: list[int] | None = None) -> dict:
    """Score CVE advisories and write priority scores to nc_triage_queue.

    Args:
        advisory_ids: Specific advisory IDs to score. If None, scores all
                      open/in_progress advisories.

    Returns:
        {"scored": N, "auto_approved": M, "pending_hitl": K, "queue": [rows]}
    """
    conn = get_connection()
    try:
        if advisory_ids:
            placeholders = ",".join("?" * len(advisory_ids))
            advisories = conn.execute(
                f"SELECT * FROM nc_advisories WHERE id IN ({placeholders})",
                advisory_ids,
            ).fetchall()
        else:
            advisories = conn.execute(
                "SELECT * FROM nc_advisories WHERE status IN ('open','in_progress')"
            ).fetchall()

        scored = 0
        auto_approved = 0
        pending_hitl = 0
        scored_ids = []

        for adv_row in advisories:
            adv = dict(adv_row)
            adv_id = adv["id"]

            asset_crit_norm, net_exp_norm = _attack_surface_stats(conn, adv_id)
            score, rationale = _compute_priority(adv, asset_crit_norm, net_exp_norm)
            status, auto_appr = _determine_status(score)

            kev_exploited = 1 if rationale["kev"] > 0 else 0
            row = {
                "advisory_id": adv_id,
                "priority_score": score,
                "kev_exploited": kev_exploited,
                "asset_criticality_norm": asset_crit_norm,
                "network_exposure_norm": net_exp_norm,
                "temporal_urgency": rationale["urgency"],
                "rank": None,
                "rationale_json": json.dumps(rationale),
                "status": status,
                "auto_approved": auto_appr,
                "approved_by": None,
                "approved_at": None,
            }
            _upsert_triage_row(conn, row)
            _append_audit_log(conn, "triage_score", adv_id, confidence=score)

            scored += 1
            scored_ids.append(adv_id)
            if auto_appr:
                auto_approved += 1
            else:
                pending_hitl += 1

        # Apply Bayesian ranking across all scored advisories
        _apply_bayesian_ranks(conn, scored_ids)
        conn.commit()

        queue = get_triage_queue(conn=conn)
        return {
            "scored": scored,
            "auto_approved": auto_approved,
            "pending_hitl": pending_hitl,
            "queue": queue,
        }
    finally:
        conn.close()


def get_triage_queue(
    status: str | None = None,
    limit: int = 100,
    conn=None,
) -> list[dict]:
    """Return nc_triage_queue rows ordered by rank ASC, priority_score DESC."""
    _close = conn is None
    if conn is None:
        conn = get_connection()
    try:
        sql = "SELECT * FROM nc_triage_queue WHERE 1=1"
        params: list[Any] = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY COALESCE(rank, 999999) ASC, priority_score DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        if _close:
            conn.close()


def approve_advisory(advisory_id: int, approved_by: str) -> dict:
    """Approve a triage queue entry for patch scheduling."""
    conn = get_connection()
    try:
        now = _now()
        conn.execute(
            """UPDATE nc_triage_queue
               SET status='approved', approved_by=%s, approved_at=%s, updated_at=%s
               WHERE advisory_id=%s""",
            (approved_by, now, now, advisory_id),
        )
        _append_audit_log(conn, "triage_approve", advisory_id)
        conn.commit()
        row = conn.execute(
            "SELECT * FROM nc_triage_queue WHERE advisory_id=%s", (advisory_id,)
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def defer_advisory(advisory_id: int, approved_by: str) -> dict:
    """Defer a triage queue entry (will not be scheduled)."""
    conn = get_connection()
    try:
        now = _now()
        conn.execute(
            """UPDATE nc_triage_queue
               SET status='deferred', approved_by=%s, approved_at=%s, updated_at=%s
               WHERE advisory_id=%s""",
            (approved_by, now, now, advisory_id),
        )
        _append_audit_log(conn, "triage_approve", advisory_id, confidence=0.0)
        conn.commit()
        row = conn.execute(
            "SELECT * FROM nc_triage_queue WHERE advisory_id=%s", (advisory_id,)
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main() -> None:
    parser = argparse.ArgumentParser(description="PVM Vulnerability Triage Engine")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--score", action="store_true", help="Score advisories and populate triage queue")
    group.add_argument("--queue", action="store_true", help="Fetch triage queue")
    group.add_argument("--approve", type=int, metavar="ADVISORY_ID", help="Approve advisory for patch scheduling")
    group.add_argument("--defer", type=int, metavar="ADVISORY_ID", help="Defer advisory")

    parser.add_argument("--advisory-ids", type=str, help="Comma-separated advisory IDs (for --score)")
    parser.add_argument("--status", type=str, help="Filter queue by status (for --queue)")
    parser.add_argument("--by", type=str, default="cli", help="Approver identity (for --approve/--defer)")
    parser.add_argument("--json", action="store_true", dest="json_out", help="JSON output")

    args = parser.parse_args()

    result: Any = None

    if args.score:
        ids = None
        if args.advisory_ids:
            ids = [int(x.strip()) for x in args.advisory_ids.split(",") if x.strip()]
        result = score_advisories(advisory_ids=ids)

    elif args.queue:
        result = get_triage_queue(status=args.status)

    elif args.approve is not None:
        result = approve_advisory(args.approve, args.by)

    elif args.defer is not None:
        result = defer_advisory(args.defer, args.by)

    if args.json_out:
        print(json.dumps(result, default=str, indent=2))
    else:
        if isinstance(result, dict):
            for k, v in result.items():
                if k != "queue":
                    print(f"{k}: {v}")
        elif isinstance(result, list):
            for row in result:
                print(f"  adv={row.get('advisory_id')} score={row.get('priority_score')} "
                      f"rank={row.get('rank')} status={row.get('status')}")


if __name__ == "__main__":
    _main()
