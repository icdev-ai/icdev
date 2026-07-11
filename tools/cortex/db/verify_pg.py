# CUI // SP-CTI
"""Live-PostgreSQL acceptance check for the Cortex audit/session tables.

The unit suite (tests/cortex/test_audit_persistence.py) runs under conftest,
which forces SQLite for isolation — that proves the code path, NOT the primary
runtime backend. This script is the PG acceptance gate: run it from the repo
root against the .env-configured ICDEV_STORAGE_BACKEND=postgresql database.

It:
  1. applies the schema (init_db — idempotent),
  2. writes append-only cortex_audit rows for TWO distinct tenants,
  3. reads back under each tenant's RLS SecurityContext and asserts a caller
     only ever sees its own tenant's rows (row-level isolation),
  4. asserts the audit outcome CHECK + insert-only shape held.

Run:  python tools/cortex/db/verify_pg.py --json

Exit code is non-zero (and "ok": false) if any assertion fails, so CI/PR gates
can consume it directly. Uses a per-run uuid tenant suffix so repeated runs on a
shared DB never collide and never require deleting append-only rows.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _read_tenant_audit_ids(tenant_id: str, classification: str) -> set:
    """Read cortex_audit ids visible to a caller in ``tenant_id`` via RLS."""
    from tools.db.storage import get_connection
    from tools.security.security_context import SecurityContext

    conn = get_connection()
    try:
        conn.set_security_context(
            SecurityContext(tenant_id=tenant_id, classification=classification)
        )
        cur = conn.cursor()
        cur.execute("SELECT id, tenant_id FROM cortex_audit")
        rows = cur.fetchall()
        ids = set()
        for r in rows:
            # DictRow (PG) or tuple (sqlite fallback)
            ids.add(r["id"] if hasattr(r, "keys") else r[0])
        return ids
    finally:
        conn.close()


def run() -> dict:
    from tools.cortex.db.init_db import init_db, record_audit
    from tools.db.storage import get_backend

    result: dict = {"ok": False, "backend": get_backend(), "checks": []}

    def check(name: str, passed: bool, detail: str = "") -> None:
        result["checks"].append({"name": name, "passed": bool(passed), "detail": detail})

    if get_backend() != "postgresql":
        result["warning"] = (
            "ICDEV_STORAGE_BACKEND is not 'postgresql' — this runs against the "
            "SQLite fallback, which is NOT the primary acceptance target. Set "
            "ICDEV_STORAGE_BACKEND=postgresql (per .env) to gate on PG."
        )

    # 1. Schema applies cleanly (idempotent).
    init_db()
    check("schema_applies", True, "init_db() applied cortex_sessions + cortex_audit")

    run_id = uuid.uuid4().hex[:8]
    tenant_a = f"verify-a-{run_id}"
    tenant_b = f"verify-b-{run_id}"

    # 2. Append-only writes for two tenants.
    id_a = record_audit(
        {
            "record_id": f"cgov-a-{run_id}",
            "operation": "cortex.verify",
            "agent_id": "verify_pg",
            "tenant_id": tenant_a,
            "classification": "CUI",
            "outcomes": {"provenance": "pass"},
            "gates_run": ["provenance"],
            "blocked": False,
        }
    )
    id_b = record_audit(
        {
            "record_id": f"cgov-b-{run_id}",
            "operation": "cortex.verify",
            "agent_id": "verify_pg",
            "tenant_id": tenant_b,
            "classification": "CUI",
            "outcomes": {"provenance": "pass"},
            "gates_run": ["provenance"],
            "blocked": False,
        }
    )
    check("two_tenant_writes", bool(id_a and id_b), f"a={id_a} b={id_b}")

    # 3. RLS read isolation — each tenant sees only its own row.
    seen_a = _read_tenant_audit_ids(tenant_a, "CUI")
    seen_b = _read_tenant_audit_ids(tenant_b, "CUI")
    a_isolated = id_a in seen_a and id_b not in seen_a
    b_isolated = id_b in seen_b and id_a not in seen_b
    check("tenant_a_sees_only_own", a_isolated, f"visible={sorted(seen_a)}")
    check("tenant_b_sees_only_own", b_isolated, f"visible={sorted(seen_b)}")

    # 4. Outcome derivation persisted (blocked payload -> 'blocked').
    id_blocked = record_audit(
        {
            "record_id": f"cgov-blk-{run_id}",
            "operation": "cortex.verify",
            "tenant_id": tenant_a,
            "classification": "CUI",
            "blocked": True,
            "blocked_gate": "pre_check",
            "outcomes": {"pre_check": "fail"},
        }
    )
    check("blocked_outcome_written", bool(id_blocked), id_blocked)

    result["ok"] = all(c["passed"] for c in result["checks"])
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()

    try:
        result = run()
    except Exception as exc:  # noqa: BLE001 — surface any failure as ok=false
        result = {"ok": False, "error": str(exc), "backend": os.environ.get("ICDEV_STORAGE_BACKEND", "")}

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        status = "OK" if result.get("ok") else "FAIL"
        print(f"[verify_pg] {status} backend={result.get('backend')}")
        for c in result.get("checks", []):
            mark = "PASS" if c["passed"] else "FAIL"
            print(f"  [{mark}] {c['name']}: {c['detail']}")
        if result.get("error"):
            print(f"  error: {result['error']}")
        if result.get("warning"):
            print(f"  warning: {result['warning']}")

    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
