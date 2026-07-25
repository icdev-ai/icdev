# CUI // SP-CTI
"""Freshness Guardian Genesis Reflex — hourly freshness checks.

Runs as a Genesis reflex every 1 hour. Checks all enabled freshness
quality rules across all data designs, writes results to dd_freshness_alerts
and dd_quality_runs, and logs breaches to genesis_audit.
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"
from tools.logging.icdev_logger import get_logger

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

logger = get_logger(__name__)

CADENCE_HOURS = 1


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class FreshnessGuardianReflex:
    """Hourly reflex that checks freshness quality rules across all data designs."""

    def run(self, context: Dict[str, Any], db_conn=None) -> Dict[str, Any]:
        """Execute one freshness guardian cycle.

        Args:
            context: Genesis context dict (may contain 'reflex_id', 'dry_run').
            db_conn: Unused — uses own DDC connection via get_connection().

        Returns:
            {checked, passed, failed, breaches}
        """
        reflex_id = context.get("reflex_id", f"fg-{uuid.uuid4().hex[:10]}")
        dry_run = context.get("dry_run", False)

        checked = 0
        passed_count = 0
        failed_count = 0
        breaches: List[Dict] = []

        try:
            from tools.data_canvas.db.init_db import get_connection as get_ddc_conn
            from tools.data_canvas.quality_engine import _run_freshness
            from tools.data_canvas.data_profiler import _open_connection
        except ImportError as exc:
            logger.error("freshness_guardian: import error: %s", exc)
            return {"checked": 0, "passed": 0, "failed": 0, "breaches": [], "error": str(exc)}

        ddc_conn = get_ddc_conn()
        try:
            rules = ddc_conn.execute(
                "SELECT * FROM dd_quality_rules WHERE check_type='freshness' AND enabled=1"
            ).fetchall()
        except Exception as exc:
            logger.error("freshness_guardian: failed to query rules: %s", exc)
            ddc_conn.close()
            return {"checked": 0, "passed": 0, "failed": 0, "breaches": [], "error": str(exc)}

        for row in rules:
            rule = dict(row)
            rule_id = rule["id"]
            checked += 1

            # ── Resolve db_conn_json from most recent run for this rule ──────────
            db_conn_json_str = None
            try:
                run_row = ddc_conn.execute(
                    "SELECT db_conn_json FROM dd_quality_runs WHERE rule_id=%s ORDER BY created_at DESC LIMIT 1",
                    (rule_id,),
                ).fetchone()
                if run_row:
                    db_conn_json_str = run_row[0] if isinstance(run_row, (list, tuple)) else run_row["db_conn_json"]
            except Exception as exc:
                logger.warning("freshness_guardian: could not fetch db_conn_json for rule %s: %s", rule_id, exc)

            if not db_conn_json_str or db_conn_json_str in ("{}", "null", ""):
                logger.warning("freshness_guardian: no db_conn_json for rule %s — skipping", rule_id)
                failed_count += 1
                continue

            try:
                conn_params = json.loads(db_conn_json_str)
            except (json.JSONDecodeError, TypeError) as exc:
                logger.warning("freshness_guardian: invalid db_conn_json for rule %s: %s", rule_id, exc)
                failed_count += 1
                continue

            # ── Open target connection ────────────────────────────────────────────
            try:
                target_conn, db_kind = _open_connection(conn_params)
            except Exception as exc:
                logger.warning("freshness_guardian: connection failed for rule %s: %s", rule_id, exc)
                failed_count += 1
                continue

            # ── Run freshness check ──────────────────────────────────────────────
            try:
                threshold = float(rule.get("threshold", 1.0))
                params = rule.get("params_json") or {}
                if isinstance(params, str):
                    try:
                        params = json.loads(params)
                    except Exception:
                        params = {}

                result = _run_freshness(
                    target_conn,
                    db_kind,
                    rule["table_name"],
                    rule.get("column_name", ""),
                    threshold,
                    params,
                )
            except Exception as exc:
                logger.warning("freshness_guardian: _run_freshness error for rule %s: %s", rule_id, exc)
                failed_count += 1
                continue
            finally:
                try:
                    target_conn.close()
                except Exception:
                    pass

            rule_passed = bool(result.get("passed"))
            if rule_passed:
                passed_count += 1
            else:
                failed_count += 1
                breaches.append({
                    "rule_id": rule_id,
                    "design_id": rule.get("design_id", ""),
                    "detail": result.get("detail", ""),
                })

            if dry_run:
                continue

            now_ts = _utcnow()
            # Stable alert ID: one row per rule (INSERT OR REPLACE overwrites prior result)
            alert_id = "fa-" + rule_id
            safe_conn_json = json.dumps({k: v for k, v in conn_params.items() if k != "password"})
            classification = rule.get("classification", "CUI // SP-CTI")

            # ── Write dd_freshness_alerts ────────────────────────────────────────
            try:
                ddc_conn.execute(
                    """INSERT OR REPLACE INTO dd_freshness_alerts
                       (id, rule_id, design_id, db_conn_json, last_checked,
                        passed, actual_max_value, cutoff_value, detail,
                        classification, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        alert_id,
                        rule_id,
                        rule.get("design_id", ""),
                        safe_conn_json,
                        now_ts,
                        1 if rule_passed else 0,
                        "",
                        "",
                        result.get("detail", "")[:500],
                        classification,
                        now_ts,
                    ),
                )
            except Exception as exc:
                logger.warning("freshness_guardian: dd_freshness_alerts write failed for %s: %s", rule_id, exc)

            # ── Write dd_quality_runs (with reflex_run if column exists) ─────────
            run_id = f"qr-{uuid.uuid4().hex[:10]}"
            try:
                ddc_conn.execute(
                    """INSERT INTO dd_quality_runs
                       (id, rule_id, db_conn_json, passed, actual_value,
                        threshold, detail, classification, reflex_run, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        run_id,
                        rule_id,
                        safe_conn_json,
                        1 if rule_passed else 0,
                        result.get("actual_value", 0.0),
                        threshold,
                        result.get("detail", "")[:500],
                        classification,
                        reflex_id,
                        now_ts,
                    ),
                )
            except Exception:
                # reflex_run column absent (migration not yet applied) — fall back
                try:
                    ddc_conn.execute(
                        """INSERT INTO dd_quality_runs
                           (id, rule_id, db_conn_json, passed, actual_value,
                            threshold, detail, classification, created_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (
                            run_id,
                            rule_id,
                            safe_conn_json,
                            1 if rule_passed else 0,
                            result.get("actual_value", 0.0),
                            threshold,
                            result.get("detail", "")[:500],
                            classification,
                            now_ts,
                        ),
                    )
                except Exception as exc2:
                    logger.warning("freshness_guardian: dd_quality_runs write failed for %s: %s", rule_id, exc2)

        ddc_conn.commit()
        ddc_conn.close()

        # ── Write genesis_audit entry for any breaches ───────────────────────────
        if breaches and not dry_run:
            try:
                from tools.db.storage import get_connection as get_main_conn
                main_conn = get_main_conn()
                try:
                    main_conn.execute(
                        """INSERT INTO genesis_audit
                           (id, event_type, reflex_name, details, created_at)
                           VALUES (%s, %s, %s, %s, %s)""",
                        (
                            f"aud-{uuid.uuid4().hex[:10]}",
                            "freshness.breach",
                            "freshness_guardian",
                            json.dumps({
                                "severity": "warning",
                                "breaches": breaches,
                                "reflex_id": reflex_id,
                            }),
                            _utcnow(),
                        ),
                    )
                    main_conn.commit()
                finally:
                    main_conn.close()
            except Exception as exc:
                logger.warning("freshness_guardian: genesis_audit write failed: %s", exc)

        return {
            "checked": checked,
            "passed": passed_count,
            "failed": failed_count,
            "breaches": breaches,
        }


def run(context: Dict[str, Any], db_conn=None) -> Dict[str, Any]:
    """Module-level entry point for Genesis daemon compatibility."""
    return FreshnessGuardianReflex().run(context, db_conn)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Freshness Guardian Genesis Reflex")
    parser.add_argument("--dry-run", action="store_true", help="Check rules without writing results")
    parser.add_argument("--json", dest="json_out", action="store_true", help="JSON output")
    args = parser.parse_args()

    out = run({"dry_run": args.dry_run})
    print(json.dumps(out, indent=2, default=str))
