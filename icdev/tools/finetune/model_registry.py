#!/usr/bin/env python3
# CUI // SP-CTI
"""Model version registry — track trained adapters, Ollama names, promotions (D-FT-7).

Manages the lifecycle of fine-tuned model versions from creation through
evaluation, promotion, and eventual archival.

Usage:
    python tools/finetune/model_registry.py --list --json
    python tools/finetune/model_registry.py --get --model-version-id "mv-xxx" --json
    python tools/finetune/model_registry.py --promote --model-version-id "mv-xxx" --function "code_generation" --json
    python tools/finetune/model_registry.py --demote --function "code_generation" --json
    python tools/finetune/model_registry.py --active --json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from tools.db.storage import get_connection
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


def _get_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    conn = get_connection(db_path=str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- Model Version CRUD --------------------------------------------------------


def get_model_version(
    model_version_id: str,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Get a single model version by ID."""
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM ft_model_versions WHERE id = %s",
            (model_version_id,),
        ).fetchone()
        if not row:
            return {"success": False, "error": f"Model version not found: {model_version_id}"}
        return {"success": True, "model_version": dict(row)}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def list_model_versions(
    model_name: str = "",
    status: str = "",
    tenant_id: str = "",
    project_id: str = "",
    limit: int = 50,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """List model versions with optional filters."""
    conn = _get_db(db_path)
    try:
        query = "SELECT * FROM ft_model_versions WHERE 1=1"
        params: List[Any] = []
        if model_name:
            query += " AND model_name = ?"
            params.append(model_name)
        if status:
            query += " AND status = ?"
            params.append(status)
        if tenant_id:
            query += " AND tenant_id = ?"
            params.append(tenant_id)
        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        return {"success": True, "versions": [dict(r) for r in rows], "count": len(rows)}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def update_eval_scores(
    model_version_id: str,
    eval_bleu: float = 0.0,
    eval_rouge_l: float = 0.0,
    eval_perplexity: float = 0.0,
    eval_custom: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Update evaluation scores on a model version."""
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT id FROM ft_model_versions WHERE id = %s",
            (model_version_id,),
        ).fetchone()
        if not row:
            return {"success": False, "error": f"Model version not found: {model_version_id}"}

        conn.execute(
            """UPDATE ft_model_versions
               SET eval_bleu = %s, eval_rouge_l = %s, eval_perplexity = %s,
                   eval_custom = %s, status = 'evaluated'
               WHERE id = %s""",
            (eval_bleu, eval_rouge_l, eval_perplexity, json.dumps(eval_custom or {}), model_version_id),
        )
        conn.commit()
        return {
            "success": True,
            "model_version_id": model_version_id,
            "status": "evaluated",
            "eval_bleu": eval_bleu,
            "eval_rouge_l": eval_rouge_l,
            "eval_perplexity": eval_perplexity,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


# -- Active Model Management (D-FT-6) -----------------------------------------


def promote_model(
    model_version_id: str,
    function_name: str,
    routing_tier: str = "worker",
    activated_by: str = "",
    activation_reason: str = "",
    tenant_id: str = "",
    project_id: str = "",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Promote a model version to active for a specific function (D-FT-6).

    Only one model can be active per (function_name, tenant_id, project_id).
    If another model is already active, it gets deactivated first.
    """
    conn = _get_db(db_path)
    try:
        # Validate model version exists
        mv = conn.execute(
            "SELECT * FROM ft_model_versions WHERE id = %s",
            (model_version_id,),
        ).fetchone()
        if not mv:
            return {"success": False, "error": f"Model version not found: {model_version_id}"}

        if not mv["ollama_model_name"]:
            return {"success": False, "error": "Model has no Ollama model name — register with Ollama first"}

        now = _now()
        previous_model = ""

        # Deactivate current active model for this function
        current = conn.execute(
            """SELECT * FROM ft_active_models
               WHERE function_name = %s AND tenant_id = %s AND project_id = %s
                 AND deactivated_at IS NULL""",
            (function_name, tenant_id, project_id),
        ).fetchone()
        if current:
            previous_model = current["ollama_model_name"]
            conn.execute(
                "UPDATE ft_active_models SET deactivated_at = %s WHERE id = %s",
                (now, current["id"]),
            )
            # Log demotion of previous model
            conn.execute(
                """INSERT INTO ft_promotion_log
                   (model_version_id, action, function_name, previous_model,
                    reason, actor, tenant_id, project_id, created_at)
                   VALUES (%s, 'demoted', %s, %s, %s, %s, %s, %s, %s)""",
                (
                    current["model_version_id"],
                    function_name,
                    "",
                    f"Replaced by {model_version_id}",
                    activated_by,
                    tenant_id,
                    project_id,
                    now,
                ),
            )

        # Insert new active model
        conn.execute(
            """INSERT OR REPLACE INTO ft_active_models
               (function_name, model_version_id, ollama_model_name, routing_tier,
                activated_by, activation_reason, tenant_id, project_id, activated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                function_name,
                model_version_id,
                mv["ollama_model_name"],
                routing_tier,
                activated_by,
                activation_reason,
                tenant_id,
                project_id,
                now,
            ),
        )

        # Update model version status
        conn.execute(
            "UPDATE ft_model_versions SET status = 'promoted' WHERE id = %s",
            (model_version_id,),
        )

        # Log promotion (append-only)
        conn.execute(
            """INSERT INTO ft_promotion_log
               (model_version_id, action, function_name, previous_model,
                eval_score_summary, reason, actor, tenant_id, project_id, created_at)
               VALUES (%s, 'promoted', %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                model_version_id,
                function_name,
                previous_model,
                json.dumps(
                    {
                        "bleu": mv["eval_bleu"],
                        "rouge_l": mv["eval_rouge_l"],
                        "perplexity": mv["eval_perplexity"],
                    }
                ),
                activation_reason,
                activated_by,
                tenant_id,
                project_id,
                now,
            ),
        )
        conn.commit()

        return {
            "success": True,
            "model_version_id": model_version_id,
            "function_name": function_name,
            "ollama_model_name": mv["ollama_model_name"],
            "previous_model": previous_model,
            "routing_tier": routing_tier,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def demote_model(
    function_name: str,
    tenant_id: str = "",
    project_id: str = "",
    demoted_by: str = "",
    reason: str = "",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Remove active model for a function, reverting to default qwen3."""
    conn = _get_db(db_path)
    try:
        current = conn.execute(
            """SELECT * FROM ft_active_models
               WHERE function_name = %s AND tenant_id = %s AND project_id = %s
                 AND deactivated_at IS NULL""",
            (function_name, tenant_id, project_id),
        ).fetchone()
        if not current:
            return {"success": False, "error": f"No active model for function: {function_name}"}

        now = _now()
        conn.execute(
            "UPDATE ft_active_models SET deactivated_at = %s WHERE id = %s",
            (now, current["id"]),
        )

        # Update model version status
        conn.execute(
            "UPDATE ft_model_versions SET status = 'demoted' WHERE id = %s",
            (current["model_version_id"],),
        )

        # Log demotion (append-only)
        conn.execute(
            """INSERT INTO ft_promotion_log
               (model_version_id, action, function_name, previous_model,
                reason, actor, tenant_id, project_id, created_at)
               VALUES (%s, 'demoted', %s, %s, %s, %s, %s, %s, %s)""",
            (
                current["model_version_id"],
                function_name,
                current["ollama_model_name"],
                reason,
                demoted_by,
                tenant_id,
                project_id,
                now,
            ),
        )
        conn.commit()

        return {
            "success": True,
            "function_name": function_name,
            "demoted_model": current["ollama_model_name"],
            "model_version_id": current["model_version_id"],
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def get_active_model(
    function_name: str,
    tenant_id: str = "",
    project_id: str = "",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Get the currently active fine-tuned model for a function (D-FT-6).

    Returns None if no fine-tuned model is active (default routing applies).
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            """SELECT am.*, mv.base_model, mv.model_name, mv.version, mv.eval_bleu,
                      mv.eval_rouge_l, mv.eval_perplexity
               FROM ft_active_models am
               JOIN ft_model_versions mv ON am.model_version_id = mv.id
               WHERE am.function_name = %s AND am.tenant_id = %s AND am.project_id = %s
                 AND am.deactivated_at IS NULL""",
            (function_name, tenant_id, project_id),
        ).fetchone()
        if not row:
            return {"success": True, "active_model": None}
        return {"success": True, "active_model": dict(row)}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def list_active_models(
    tenant_id: str = "",
    project_id: str = "",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """List all currently active fine-tuned model overrides."""
    conn = _get_db(db_path)
    try:
        query = """SELECT am.*, mv.model_name, mv.version, mv.base_model
                   FROM ft_active_models am
                   JOIN ft_model_versions mv ON am.model_version_id = mv.id
                   WHERE am.deactivated_at IS NULL"""
        params: List[Any] = []
        if tenant_id:
            query += " AND am.tenant_id = ?"
            params.append(tenant_id)
        if project_id:
            query += " AND am.project_id = ?"
            params.append(project_id)
        query += " ORDER BY am.activated_at DESC"

        rows = conn.execute(query, params).fetchall()
        return {"success": True, "active_models": [dict(r) for r in rows], "count": len(rows)}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


def get_promotion_history(
    model_version_id: str = "",
    function_name: str = "",
    limit: int = 50,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Get promotion/demotion history (append-only log)."""
    conn = _get_db(db_path)
    try:
        query = "SELECT * FROM ft_promotion_log WHERE 1=1"
        params: List[Any] = []
        if model_version_id:
            query += " AND model_version_id = ?"
            params.append(model_version_id)
        if function_name:
            query += " AND function_name = ?"
            params.append(function_name)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        return {"success": True, "history": [dict(r) for r in rows], "count": len(rows)}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        conn.close()


# -- CLI -----------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Fine-tuning model registry")
    parser.add_argument("--list", action="store_true", help="List model versions")
    parser.add_argument("--get", action="store_true", help="Get model version")
    parser.add_argument("--promote", action="store_true", help="Promote model to active")
    parser.add_argument("--demote", action="store_true", help="Demote active model")
    parser.add_argument("--active", action="store_true", help="List active models")
    parser.add_argument("--history", action="store_true", help="Promotion history")

    parser.add_argument("--model-version-id", type=str, default="")
    parser.add_argument("--model-name", type=str, default="")
    parser.add_argument("--function", type=str, default="")
    parser.add_argument("--routing-tier", type=str, default="worker")
    parser.add_argument("--status", type=str, default="")
    parser.add_argument("--tenant-id", type=str, default="")
    parser.add_argument("--project-id", type=str, default="")
    parser.add_argument("--activated-by", type=str, default="")
    parser.add_argument("--reason", type=str, default="")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--json", action="store_true")

    args = parser.parse_args()

    if args.get and args.model_version_id:
        result = get_model_version(args.model_version_id)
    elif args.list:
        result = list_model_versions(
            model_name=args.model_name,
            status=args.status,
            tenant_id=args.tenant_id,
            project_id=args.project_id,
            limit=args.limit,
        )
    elif args.promote:
        result = promote_model(
            model_version_id=args.model_version_id,
            function_name=args.function,
            routing_tier=args.routing_tier,
            activated_by=args.activated_by,
            activation_reason=args.reason,
            tenant_id=args.tenant_id,
            project_id=args.project_id,
        )
    elif args.demote:
        result = demote_model(
            function_name=args.function,
            tenant_id=args.tenant_id,
            project_id=args.project_id,
            demoted_by=args.activated_by,
            reason=args.reason,
        )
    elif args.active:
        result = list_active_models(tenant_id=args.tenant_id, project_id=args.project_id)
    elif args.history:
        result = get_promotion_history(
            model_version_id=args.model_version_id,
            function_name=args.function,
            limit=args.limit,
        )
    else:
        result = {"success": False, "error": "Specify --list, --get, --promote, --demote, --active, or --history"}

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
