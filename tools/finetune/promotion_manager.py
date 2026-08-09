#!/usr/bin/env python3
# CUI // SP-CTI
"""Promotion manager — threshold-based auto-promote + manual override (D-FT-16).

Auto-promotes models meeting BLEU >= 0.30 AND ROUGE-L >= 0.40 AND
perplexity improvement >= 10%. Manual override via --force-promote.
All transitions logged in ft_promotion_log (append-only).

Usage:
    python tools/finetune/promotion_manager.py --check --model-version-id "mv-xxx" --function "code_generation" --json
    python tools/finetune/promotion_manager.py --auto-promote --model-version-id "mv-xxx" --function "code_generation" --json
    python tools/finetune/promotion_manager.py --force-promote --model-version-id "mv-xxx" --function "code_generation" --json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection
from tools.common.helpers import now_iso
from pathlib import Path
from typing import Any, Dict, Optional
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.finetune.promotion_manager")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


def _get_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    conn = get_connection(db_path=str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _load_thresholds() -> Dict[str, float]:
    """Load promotion thresholds from config."""
    config_path = BASE_DIR / "args" / "finetune_config.yaml"
    if config_path.exists():
        try:
            import yaml

            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
            promo = cfg.get("promotion", {})
            return {
                "min_bleu": promo.get("min_bleu", 0.30),
                "min_rouge_l": promo.get("min_rouge_l", 0.40),
                "min_perplexity_improvement_pct": promo.get("min_perplexity_improvement_pct", 10),
            }
        except (ImportError, Exception):
            pass
    return {"min_bleu": 0.30, "min_rouge_l": 0.40, "min_perplexity_improvement_pct": 10}


def check_promotion_eligibility(
    model_version_id: str,
    function_name: str = "",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Check if a model version meets auto-promotion thresholds (D-FT-16).

    Returns eligibility status and threshold details.
    """
    conn = _get_db(db_path)
    try:
        mv = conn.execute(
            "SELECT * FROM ft_model_versions WHERE id = %s",
            (model_version_id,),
        ).fetchone()
        if not mv:
            return {"success": False, "error": f"Model version not found: {model_version_id}"}

        thresholds = _load_thresholds()

        bleu_pass = mv["eval_bleu"] >= thresholds["min_bleu"]
        rouge_pass = mv["eval_rouge_l"] >= thresholds["min_rouge_l"]

        # Perplexity improvement check (compare vs current active model)
        perplexity_pass = True  # Default pass if no comparison available
        perplexity_improvement = 0.0
        current_perplexity = 0.0

        if function_name:
            current = conn.execute(
                """SELECT mv.eval_perplexity FROM ft_active_models am
                   JOIN ft_model_versions mv ON am.model_version_id = mv.id
                   WHERE am.function_name = %s AND am.deactivated_at IS NULL""",
                (function_name,),
            ).fetchone()
            if current and current["eval_perplexity"] > 0 and mv["eval_perplexity"] > 0:
                current_perplexity = current["eval_perplexity"]
                improvement = (current_perplexity - mv["eval_perplexity"]) / current_perplexity * 100
                perplexity_improvement = round(improvement, 2)
                perplexity_pass = improvement >= thresholds["min_perplexity_improvement_pct"]

        eligible = bleu_pass and rouge_pass and perplexity_pass
        has_ollama = bool(mv["ollama_model_name"])

        return {
            "success": True,
            "model_version_id": model_version_id,
            "eligible": eligible and has_ollama,
            "has_ollama_model": has_ollama,
            "checks": {
                "bleu": {"score": mv["eval_bleu"], "threshold": thresholds["min_bleu"], "pass": bleu_pass},
                "rouge_l": {"score": mv["eval_rouge_l"], "threshold": thresholds["min_rouge_l"], "pass": rouge_pass},
                "perplexity": {
                    "score": mv["eval_perplexity"],
                    "current_active": current_perplexity,
                    "improvement_pct": perplexity_improvement,
                    "threshold_pct": thresholds["min_perplexity_improvement_pct"],
                    "pass": perplexity_pass,
                },
            },
            "thresholds": thresholds,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def auto_promote(
    model_version_id: str,
    function_name: str,
    routing_tier: str = "worker",
    activated_by: str = "auto_promote",
    tenant_id: str = "",
    project_id: str = "",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Auto-promote a model if it meets all thresholds (D-FT-16).

    Checks eligibility first, then promotes via model_registry.
    """
    eligibility = check_promotion_eligibility(
        model_version_id,
        function_name,
        db_path,
    )
    if not eligibility.get("success"):
        return eligibility

    if not eligibility["eligible"]:
        failed = [k for k, v in eligibility["checks"].items() if not v["pass"]]
        return {
            "success": False,
            "error": f"Model does not meet auto-promotion thresholds. Failed: {', '.join(failed)}",
            "checks": eligibility["checks"],
        }

    from tools.finetune.model_registry import promote_model

    result = promote_model(
        model_version_id=model_version_id,
        function_name=function_name,
        routing_tier=routing_tier,
        activated_by=activated_by,
        activation_reason="Auto-promoted: met all thresholds",
        tenant_id=tenant_id,
        project_id=project_id,
        db_path=db_path,
    )

    if result.get("success"):
        # Log auto-promotion event
        conn = _get_db(db_path)
        try:
            conn.execute(
                """INSERT INTO ft_promotion_log
                   (model_version_id, action, function_name, eval_score_summary,
                    reason, actor, tenant_id, project_id, created_at)
                   VALUES (%s, 'auto_promoted', %s, %s, %s, %s, %s, %s, %s)""",
                (
                    model_version_id,
                    function_name,
                    json.dumps(eligibility["checks"]),
                    "Auto-promoted: met all thresholds",
                    activated_by,
                    tenant_id,
                    project_id,
                    now_iso(),
                ),
            )
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning("auto_promote: best-effort INSERT into ft_promotion_log failed (non-blocking): %s", exc)
        finally:
            conn.close()

    return result


def force_promote(
    model_version_id: str,
    function_name: str,
    routing_tier: str = "worker",
    activated_by: str = "",
    reason: str = "Manual override",
    tenant_id: str = "",
    project_id: str = "",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Force-promote a model regardless of thresholds (D-FT-16).

    Bypasses auto-promotion checks. Logs as 'override_promoted'.
    """
    from tools.finetune.model_registry import promote_model

    result = promote_model(
        model_version_id=model_version_id,
        function_name=function_name,
        routing_tier=routing_tier,
        activated_by=activated_by,
        activation_reason=f"Force-promoted: {reason}",
        tenant_id=tenant_id,
        project_id=project_id,
        db_path=db_path,
    )

    if result.get("success"):
        # Log override promotion
        conn = _get_db(db_path)
        try:
            conn.execute(
                """INSERT INTO ft_promotion_log
                   (model_version_id, action, function_name,
                    reason, actor, tenant_id, project_id, created_at)
                   VALUES (%s, 'override_promoted', %s, %s, %s, %s, %s, %s)""",
                (
                    model_version_id,
                    function_name,
                    f"Force-promoted: {reason}",
                    activated_by,
                    tenant_id,
                    project_id,
                    now_iso(),
                ),
            )
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning("force_promote: best-effort INSERT into ft_promotion_log failed (non-blocking): %s", exc)
        finally:
            conn.close()

    return result


# -- CLI -----------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Fine-tuning promotion manager")
    parser.add_argument("--check", action="store_true", help="Check promotion eligibility")
    parser.add_argument("--auto-promote", action="store_true", help="Auto-promote if eligible")
    parser.add_argument("--force-promote", action="store_true", help="Force-promote (bypass thresholds)")

    parser.add_argument("--model-version-id", type=str, default="")
    parser.add_argument("--function", type=str, default="")
    parser.add_argument("--routing-tier", type=str, default="worker")
    parser.add_argument("--activated-by", type=str, default="")
    parser.add_argument("--reason", type=str, default="Manual override")
    parser.add_argument("--tenant-id", type=str, default="")
    parser.add_argument("--project-id", type=str, default="")
    parser.add_argument("--json", action="store_true")

    args = parser.parse_args()

    if args.check:
        result = check_promotion_eligibility(
            args.model_version_id,
            args.function,
        )
    elif args.auto_promote:
        result = auto_promote(
            args.model_version_id,
            args.function,
            routing_tier=args.routing_tier,
            activated_by=args.activated_by or "auto",
            tenant_id=args.tenant_id,
            project_id=args.project_id,
        )
    elif args.force_promote:
        result = force_promote(
            args.model_version_id,
            args.function,
            routing_tier=args.routing_tier,
            activated_by=args.activated_by,
            reason=args.reason,
            tenant_id=args.tenant_id,
            project_id=args.project_id,
        )
    else:
        result = {"success": False, "error": "Specify --check, --auto-promote, or --force-promote"}

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
