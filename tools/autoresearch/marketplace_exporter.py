#!/usr/bin/env python3
# CUI // SP-CTI
"""Marketplace Exporter — package experiment results as marketplace assets (D-AR-7).

Converts successful autoresearch experiment results into publishable
marketplace assets (experiment_program type). Enables cross-tenant
sharing of improved domain configurations.

Usage:
    python tools/autoresearch/marketplace_exporter.py --export --experiment-id "exp-xxx" --json
    python tools/autoresearch/marketplace_exporter.py --list-exportable --json
    python tools/autoresearch/marketplace_exporter.py --health --json
"""

import argparse
import hashlib
import json
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent

from tools.common.helpers import now_iso
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.autoresearch.marketplace_exporter")


def _gen_id(prefix="mke"):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _get_db():
    """Get database connection via storage abstraction."""
    from tools.db.storage import get_connection

    return get_connection()


def list_exportable(min_improvement: float = 0.005) -> dict:
    """List experiment results eligible for marketplace export.

    Criteria: decision='keep' AND improvement_pct >= min_improvement
    """
    try:
        with _get_db() as conn:
            rows = conn.execute(
                "SELECT er.id, er.experiment_id, er.domain, er.hypothesis, "
                "er.metric_delta, er.improvement_pct, er.decision, er.created_at "
                "FROM experiment_results er "
                "WHERE er.decision = 'keep' AND er.improvement_pct >= %s "
                "ORDER BY er.improvement_pct DESC LIMIT 50",
                (min_improvement,),
            ).fetchall()
            return {
                "exportable": [dict(r) for r in rows],
                "count": len(rows),
                "min_improvement": min_improvement,
                "timestamp": now_iso(),
            }
    except Exception as exc:
        return {"exportable": [], "count": 0, "error": str(exc)[:200]}


def export_experiment_as_asset(
    experiment_id: str,
    tenant_id: str = "default",
    publisher_user: str = "autoresearch-engine",
) -> dict:
    """Package a successful experiment result into a marketplace asset.

    Creates an experiment_program asset from the experiment's domain config
    with embedded results metadata for reproducibility.
    """
    try:
        with _get_db() as conn:
            # Get experiment result
            result_row = conn.execute(
                "SELECT * FROM experiment_results WHERE experiment_id = %s "
                "AND decision = 'keep' ORDER BY created_at DESC LIMIT 1",
                (experiment_id,),
            ).fetchone()
            if not result_row:
                return {
                    "success": False,
                    "error": f"No kept result found for experiment {experiment_id}",
                }

            result = dict(result_row)
            domain = result["domain"]

            # Get experiment candidate
            candidate_row = conn.execute(
                "SELECT * FROM experiment_candidates WHERE id = %s",
                (experiment_id,),
            ).fetchone()
            candidate_data = dict(candidate_row) if candidate_row else {}

            # Load domain program config
            program_path = _ROOT / "args" / "experiment_programs" / f"{domain}.yaml"
            program_content = ""
            if program_path.exists():
                with open(program_path, encoding="utf-8") as f:
                    program_content = f.read()

            # Build asset metadata
            asset_name = f"autoresearch-{domain}-{now_iso()[:10]}"
            content_hash = hashlib.sha256(
                f"{domain}:{result['hypothesis']}:{result['metric_delta']}".encode()
            ).hexdigest()[:16]

            category = candidate_data.get("category", "general")
            description = (
                f"Autoresearch experiment program for {domain}/{category} domain. "
                f"Hypothesis: {result['hypothesis'][:200]}. "
                f"Improvement: {result.get('improvement_pct', 0):.2f}%."
            )

            # Build asset package (JSON metadata + YAML config)
            asset_package = {
                "name": asset_name,
                "asset_type": "experiment_program",
                "description": description,
                "domain": domain,
                "version": "1.0.0",
                "experiment_metadata": {
                    "experiment_id": experiment_id,
                    "hypothesis": result["hypothesis"],
                    "pre_metric": result.get("pre_metric"),
                    "post_metric": result.get("post_metric"),
                    "metric_delta": result.get("metric_delta"),
                    "improvement_pct": result.get("improvement_pct"),
                    "decision": result["decision"],
                    "created_at": result.get("created_at"),
                },
                "program_config": program_content,
                "content_hash": content_hash,
                "publisher": publisher_user,
                "classification": "CUI",
            }

            # Store as marketplace asset (if publish pipeline available)
            asset_id = _gen_id("mka")
            try:
                conn.execute(
                    "INSERT INTO marketplace_assets "
                    "(id, tenant_id, name, asset_type, description, slug, "
                    "publisher_user, impact_level, status, classification, "
                    "created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        asset_id,
                        tenant_id,
                        asset_name,
                        "experiment_program",
                        description,
                        f"{tenant_id}/{asset_name}",
                        publisher_user,
                        "IL4",
                        "draft",
                        "CUI",
                        now_iso(),
                        now_iso(),
                    ),
                )
            except Exception as _exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
                # Table may not exist in all environments
                logger.warning(
                    "export_experiment_as_asset: best-effort INSERT into marketplace_assets failed (non-blocking): %s",
                    _exc,
                )

            # Audit trail
            try:
                conn.execute(
                    "INSERT INTO audit_trail (id, event_type, actor, action, "
                    "details, project_id, session_id, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        f"at-{uuid.uuid4().hex[:12]}",
                        "autoresearch.marketplace_export",
                        "autoresearch-engine",
                        f"Exported experiment {experiment_id} as marketplace asset {asset_id}",
                        json.dumps(
                            {"experiment_id": experiment_id, "asset_id": asset_id, "domain": domain}, default=str
                        ),
                        "autoresearch",
                        "autoresearch",
                        now_iso(),
                    ),
                )
            except Exception as _exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
                logger.warning(
                    "export_experiment_as_asset: best-effort INSERT into audit_trail failed (non-blocking): %s",
                    _exc,
                )

        return {
            "success": True,
            "asset_id": asset_id,
            "asset_name": asset_name,
            "domain": domain,
            "experiment_id": experiment_id,
            "improvement_pct": result.get("improvement_pct", 0),
            "status": "draft",
            "asset_package": asset_package,
            "timestamp": now_iso(),
        }

    except Exception as exc:
        return {"success": False, "error": str(exc)[:300]}


def health_check() -> dict:
    """Health check for marketplace exporter."""
    db_ok = False
    exportable_count = 0
    try:
        result = list_exportable()
        exportable_count = result.get("count", 0)
        db_ok = "error" not in result
    except Exception:
        pass

    return {
        "status": "healthy" if db_ok else "degraded",
        "db_available": db_ok,
        "exportable_experiments": exportable_count,
        "timestamp": now_iso(),
    }


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Marketplace Exporter")
    parser.add_argument("--export", action="store_true", help="Export experiment as marketplace asset")
    parser.add_argument("--list-exportable", action="store_true", help="List exportable experiments")
    parser.add_argument("--health", action="store_true", help="Health check")
    parser.add_argument("--experiment-id", type=str, default="")
    parser.add_argument("--min-improvement", type=float, default=0.005)
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.health:
        result = health_check()
    elif args.list_exportable:
        result = list_exportable(args.min_improvement)
    elif args.export:
        if not args.experiment_id:
            result = {"error": "--experiment-id required for export"}
        else:
            result = export_experiment_as_asset(args.experiment_id)
    else:
        parser.print_help()
        return

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
