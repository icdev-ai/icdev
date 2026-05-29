# CUI // SP-CTI
"""Data Mesh — Contract Engine (ODCS v1.1+ compatible).

Pure-function CRUD + YAML lint + contract testing.
No Flask. Uses get_connection() from tools/data_canvas/db/init_db.py.
"""
from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from tools.data_canvas.db.init_db import get_connection

_ODCS_REQUIRED_FIELDS = ("dataContractSpecification", "id", "info", "models")
_ODCS_INFO_REQUIRED = ("title", "owner")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def list_contracts(domain_id: str | None = None, product_id: str | None = None) -> list[dict]:
    try:
        with get_connection() as conn:
            clauses, params = [], []
            if domain_id:
                clauses.append("domain_id=?")
                params.append(domain_id)
            if product_id:
                clauses.append("product_id=?")
                params.append(product_id)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            rows = conn.execute(
                f"SELECT * FROM dm_data_contracts {where} ORDER BY name", params
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        return [{"error": str(exc)}]


def get_contract(contract_id: str) -> dict | None:
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM dm_data_contracts WHERE id=?", (contract_id,)
            ).fetchone()
        return dict(row) if row else None
    except Exception as exc:
        return {"error": str(exc)}


def create_contract(data: dict) -> dict:
    try:
        contract_id = data.get("id") or str(uuid.uuid4())
        now = _now()
        with get_connection() as conn:
            conn.execute(
                """INSERT INTO dm_data_contracts
                   (id, domain_id, product_id, name, contract_yaml,
                    version, status, classification, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    contract_id,
                    data.get("domain_id", ""),
                    data.get("product_id", ""),
                    data.get("name", ""),
                    data.get("contract_yaml", ""),
                    data.get("version", "1.0.0"),
                    data.get("status", "draft"),
                    data.get("classification", "CUI // SP-CTI"),
                    now,
                    now,
                ),
            )
            conn.commit()
        return get_contract(contract_id) or {"id": contract_id}
    except Exception as exc:
        return {"error": str(exc)}


def update_contract(contract_id: str, data: dict) -> dict | None:
    try:
        now = _now()
        fields = {k: v for k, v in data.items()
                  if k in ("name", "contract_yaml", "version", "status",
                           "classification", "domain_id", "product_id")}
        if not fields:
            return get_contract(contract_id)
        set_clause = ", ".join(f"{k}=?" for k in fields)
        values = list(fields.values()) + [now, contract_id]
        with get_connection() as conn:
            conn.execute(
                f"UPDATE dm_data_contracts SET {set_clause}, updated_at=? WHERE id=?",
                values,
            )
            conn.commit()
        return get_contract(contract_id)
    except Exception as exc:
        return {"error": str(exc)}


def delete_contract(contract_id: str) -> bool:
    try:
        with get_connection() as conn:
            conn.execute("DELETE FROM dm_data_contracts WHERE id=?", (contract_id,))
            conn.commit()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Validation / Lint
# ---------------------------------------------------------------------------

def validate_yaml_structure(yaml_text: str) -> dict:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return {"valid": False, "fields_present": [], "missing_required": list(_ODCS_REQUIRED_FIELDS),
                "warnings": ["PyYAML not installed"]}
    try:
        doc = yaml.safe_load(yaml_text or "") or {}
    except Exception as exc:
        return {"valid": False, "fields_present": [], "missing_required": list(_ODCS_REQUIRED_FIELDS),
                "warnings": [f"YAML parse error: {exc}"]}

    fields_present = [f for f in _ODCS_REQUIRED_FIELDS if f in doc]
    missing = [f for f in _ODCS_REQUIRED_FIELDS if f not in doc]
    warnings = []
    if "info" in doc and isinstance(doc["info"], dict):
        for inf in _ODCS_INFO_REQUIRED:
            if not doc["info"].get(inf):
                warnings.append(f"info.{inf} is missing or empty")
    return {"valid": not missing, "fields_present": fields_present,
            "missing_required": missing, "warnings": warnings}


def lint_contract(yaml_text: str) -> dict:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return {"passed": False, "errors": ["PyYAML not installed"], "warnings": [], "score": 0.0}

    errors, warnings = [], []
    try:
        doc = yaml.safe_load(yaml_text or "") or {}
    except Exception as exc:
        return {"passed": False, "errors": [f"YAML parse error: {exc}"], "warnings": [], "score": 0.0}

    if not doc.get("dataContractSpecification"):
        errors.append("dataContractSpecification version is missing")
    if not doc.get("id"):
        errors.append("id field is missing")
    elif not str(doc["id"]).startswith(("urn:", "http")):
        warnings.append("id should be a URN (e.g. urn:datacontract:...)")
    info = doc.get("info") or {}
    if not info.get("owner"):
        errors.append("info.owner is not set")
    models = doc.get("models")
    if not models:
        errors.append("models section is empty or missing")
    if not doc.get("quality"):
        warnings.append("quality section absent — data quality checks not defined")
    if not doc.get("servers"):
        warnings.append("servers section absent — connection info not specified")

    score = max(0.0, 100.0 - len(errors) * 20 - len(warnings) * 5)
    return {"passed": not errors, "errors": errors, "warnings": warnings, "score": round(score, 1)}


# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------

def test_contract(contract_id: str, conn_params: dict | None = None) -> dict:
    contract = get_contract(contract_id)
    if not contract or "error" in contract:
        return {"passed": False, "error_count": 1, "warnings": 0,
                "result_json": "{}", "method": "internal",
                "error": "contract not found"}

    result: dict

    # Try datacontract-cli if installed
    if importlib.util.find_spec("datacontract"):
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml",
                                            delete=False, encoding="utf-8") as tmp:
                tmp.write(contract.get("contract_yaml", ""))
                tmp_path = tmp.name
            cmd = ["datacontract", "test", "--file", tmp_path]
            if conn_params:
                for k, v in conn_params.items():
                    cmd += [f"--{k}", str(v)]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            passed = proc.returncode == 0
            result = {"passed": passed, "error_count": 0 if passed else 1,
                      "warnings": 0, "result_json": proc.stdout[:2000],
                      "method": "cli"}
            Path(tmp_path).unlink(missing_ok=True)
        except Exception as exc:
            result = {"passed": False, "error_count": 1, "warnings": 0,
                      "result_json": str(exc), "method": "internal"}
    else:
        lint = lint_contract(contract.get("contract_yaml", ""))
        passed = lint["passed"]
        result = {
            "passed": passed,
            "error_count": len(lint["errors"]),
            "warnings": len(lint["warnings"]),
            "result_json": str(lint),
            "method": "internal",
        }

    run_id = str(uuid.uuid4())
    try:
        with get_connection() as conn:
            conn.execute(
                """INSERT INTO dm_contract_test_runs
                   (id, contract_id, passed, error_count, warnings, result_json, method, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (run_id, contract_id,
                 1 if result["passed"] else 0,
                 result["error_count"], result["warnings"],
                 result["result_json"][:4000], result["method"], _now()),
            )
            conn.commit()
    except Exception:
        pass

    result["run_id"] = run_id
    return result
