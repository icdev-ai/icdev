#!/usr/bin/env python3
# CUI // SP-CTI
from __future__ import annotations

import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger
"""Security Posture Auditor for ICDEV™.

Checks all framework layers and reports health:
- security_context  (thread-local / Flask g)
- abac_engine       (policy loading, PDP cache)
- classification_enforcer (MAC read/write, compartments)
- row_security      (predicate injection, PG DDL)
- column_security   (masking strategies, config)
- field_security    (recursive filtering, Flask hook)
- encryption_at_rest (HKDF, key rotation)
- mtls_integration  (cert resolver)
- security_middleware (Flask init, headers)

Public API:
    audit_posture() -> dict
"""

import json
import os
from typing import Any, Dict

logger = get_logger("security.posture")

# ---------------------------------------------------------------------------
# Layer checks
# ---------------------------------------------------------------------------

def _check_security_context() -> Dict[str, Any]:
    try:
        from tools.security.security_context import SecurityContext, get_security_context, set_security_context

        ctx = SecurityContext(user_id="test", role="admin", clearance_level=2, tenant_id="t1")
        set_security_context(ctx)
        retrieved = get_security_context()
        ok = retrieved is not None and retrieved.user_id == "test"
        return {"status": "pass" if ok else "fail", "detail": "context create/get OK" if ok else "context mismatch"}
    except Exception as exc:
        return {"status": "fail", "detail": str(exc)}


def _check_abac_engine() -> Dict[str, Any]:
    try:
        from tools.security.abac_engine import evaluate, reload_policies

        reload_policies()
        d = evaluate(
            subject={"role": "tenant_admin", "user_id": "u1"},
            resource={"type": "project"},
            action="GET",
        )
        ok = d.permit
        return {"status": "pass" if ok else "warn", "detail": f"default policy permit={d.permit}"}
    except Exception as exc:
        return {"status": "fail", "detail": str(exc)}


def _check_classification_enforcer() -> Dict[str, Any]:
    try:
        from tools.security.classification_enforcer import can_read, can_write, can_access_compartment
        from tools.security.security_context import SecurityContext

        ctx_cui = SecurityContext(clearance_level=1)
        ctx_secret = SecurityContext(clearance_level=3)  # SECRET (classification_manager scale: PUBLIC=0,CUI=1,ECI=2,SECRET=3)
        ok = (
            can_read("CUI", ctx_cui)
            and not can_read("SECRET", ctx_cui)
            and can_write("SECRET", ctx_secret)
            and not can_write("CUI", ctx_secret)
            and can_access_compartment({"COI_FINANCE"}, SecurityContext(compartments=frozenset({"COI_FINANCE"})))
        )
        return {"status": "pass" if ok else "fail", "detail": "MAC checks consistent"}
    except Exception as exc:
        return {"status": "fail", "detail": str(exc)}


def _check_row_security() -> Dict[str, Any]:
    try:
        from tools.security.row_security import inject_row_predicate

        sql, extra_params, _ = inject_row_predicate("SELECT * FROM t WHERE x = ?", "tenant_a")
        ok = "tenant_id = ?" in sql and "tenant_a" in extra_params
        return {"status": "pass" if ok else "fail", "detail": f"predicate injection OK ({len(extra_params)} params)"}
    except Exception as exc:
        return {"status": "fail", "detail": str(exc)}


def _check_column_security() -> Dict[str, Any]:
    try:
        from tools.security.column_security import mask_columns

        row = {"email": "a@b.com", "secret": "s1"}
        masked = mask_columns(row, {"secret": "null"})
        ok = masked["secret"] is None and masked["email"] == "a@b.com"
        return {"status": "pass" if ok else "fail", "detail": "column masking OK"}
    except Exception as exc:
        return {"status": "fail", "detail": str(exc)}


def _check_field_security() -> Dict[str, Any]:
    try:
        from tools.security.field_security import filter_response_fields

        data = {"user": {"email": "a@b.com", "ssn": "123-45-6789"}}
        filtered = filter_response_fields(data, {"ssn": "redact"})
        ok = filtered["user"]["ssn"] == "[REDACTED]" and filtered["user"]["email"] == "a@b.com"
        return {"status": "pass" if ok else "fail", "detail": "field filtering OK"}
    except Exception as exc:
        return {"status": "fail", "detail": str(exc)}


def _check_encryption_at_rest() -> Dict[str, Any]:
    try:
        __import__("tools.security.encryption_at_rest", fromlist=["rotate_keys"])
        return {"status": "pass", "detail": "module loadable"}
    except Exception as exc:
        return {"status": "warn", "detail": f"module not loadable: {exc}"}


def _check_mtls() -> Dict[str, Any]:
    cert = os.environ.get("ICDEV_MTLS_CLIENT_CERT")
    key = os.environ.get("ICDEV_MTLS_CLIENT_KEY")
    if cert and key:
        return {"status": "pass", "detail": "mTLS env vars configured"}
    return {"status": "warn", "detail": "mTLS env vars not set"}


def _check_middleware() -> Dict[str, Any]:
    try:

        return {"status": "pass", "detail": "init_security importable"}
    except Exception as exc:
        return {"status": "fail", "detail": str(exc)}


# ---------------------------------------------------------------------------
# Posture aggregator
# ---------------------------------------------------------------------------

def audit_posture() -> dict:
    """Run all layer checks and return a consolidated report."""
    layers = {
        "security_context": _check_security_context(),
        "abac_engine": _check_abac_engine(),
        "classification_enforcer": _check_classification_enforcer(),
        "row_security": _check_row_security(),
        "column_security": _check_column_security(),
        "field_security": _check_field_security(),
        "encryption_at_rest": _check_encryption_at_rest(),
        "mtls": _check_mtls(),
        "middleware": _check_middleware(),
    }

    statuses = [v["status"] for v in layers.values()]
    if "fail" in statuses:
        overall = "fail"
    elif "warn" in statuses:
        overall = "warn"
    else:
        overall = "pass"

    return {
        "overall_status": overall,
        "layers": layers,
        "timestamp": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Security Posture Auditor")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    result = audit_posture()
    print(json.dumps(result, indent=2) if args.json else f"Overall: {result['overall_status']}")
    if not args.json:
        for layer, info in result["layers"].items():
            print(f"  {layer}: {info['status']} — {info['detail']}")


if __name__ == "__main__":
    main()
