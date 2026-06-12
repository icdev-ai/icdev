#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis cATO Monitor Reflex — 6-hour continuous compliance monitoring.

Discovers all compliance/* IQE seed queries, runs them via importlib-loaded
parser/executor, and calls poam_generator for any new violations.

Scanner-tier only (zero Claude tokens).  Air-gap safe.
"""
IMPLEMENTATION_STATUS = "full"

import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

_IQE_DIR = BASE_DIR / "context" / "iqe" / "queries" / "compliance"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _collect_iqe_files() -> List[Path]:
    """Return all .iqe files under context/iqe/queries/compliance/."""
    if not _IQE_DIR.exists():
        return []
    return sorted(_IQE_DIR.rglob("*.iqe"))


def _run_iqe_query(query_file: Path, conn: Any) -> Dict[str, Any]:
    """Parse and execute one IQE file. Returns {name, rows, error}."""
    name = f"{query_file.parent.name}/{query_file.stem}"
    try:
        parser = importlib.import_module("tools.iqe.parser")
        executor_mod = importlib.import_module("tools.iqe.executor")
        text = query_file.read_text(encoding="utf-8")
        ast = parser.parse(text)
        ex = executor_mod.Executor()
        rows = ex.run(ast, conn)
        return {"name": name, "rows": rows, "error": None}
    except Exception as exc:
        return {"name": name, "rows": [], "error": str(exc)}


def _call_poam_generator(project_id: str) -> Dict[str, Any]:
    """Invoke poam_generator.generate_poam(). Returns {success, path, error}."""
    try:
        gen = importlib.import_module("tools.compliance.poam_generator")
        path = gen.generate_poam(project_id)
        return {"success": True, "path": str(path)}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the cATO Monitor Reflex.

    Returns {success, scanned, violations, poam_created, metric_value, details}.
    """
    project_id = config.get("project_id", "sparkpilot")
    iqe_files = _collect_iqe_files()

    try:
        from tools.db.storage import get_connection
        conn = get_connection()
    except Exception as exc:
        return {
            "success": False,
            "scanned": 0,
            "violations": 0,
            "poam_created": 0,
            "metric_value": 0.0,
            "error": f"DB connection failed: {exc}",
        }

    query_results: List[Dict[str, Any]] = []
    total_violations = 0
    try:
        for qf in iqe_files:
            result = _run_iqe_query(qf, conn)
            query_results.append(result)
            total_violations += len(result["rows"])
    finally:
        try:
            conn.close()
        except Exception:
            pass

    poam_created = 0
    poam_detail: Dict[str, Any] = {}
    if total_violations > 0:
        poam_detail = _call_poam_generator(project_id)
        if poam_detail.get("success"):
            poam_created = 1

    return {
        "success": True,
        "scanned": len(iqe_files),
        "violations": total_violations,
        "poam_created": poam_created,
        "metric_value": float(total_violations),
        "details": {
            "project_id": project_id,
            "queries_run": len(iqe_files),
            "queries_failed": sum(1 for r in query_results if r["error"]),
            "query_results": [
                {
                    "name": r["name"],
                    "violations": len(r["rows"]),
                    "error": r["error"],
                }
                for r in query_results
            ],
            "poam": poam_detail,
            "timestamp": _utcnow_iso(),
        },
    }
