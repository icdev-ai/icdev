# CUI // SP-CTI
"""Cross-tenant leakage verifier for the ICDEV Cortex analyst data path (ctx-expose-03).

PostgreSQL is THE backend for tenant isolation: RLS predicates,
``classifications_dominated_by()`` read-down, and native ``FORCE ROW LEVEL
SECURITY`` policies only exist on PG. SQLite proves nothing here, so this
verifier hard-requires a reachable PostgreSQL backend and **skips cleanly**
(exit 0, ``status="skipped"``) when PG is unreachable or the active backend is
SQLite — never a silent pass.

It seeds two tenants (and mixed classifications) into an RLS-protected probe
table, then proves isolation through every LIVE Cortex entry-point path that
actually reads tenant-scoped rows:

1. ``connection``  — the security-context primitive both analyst paths funnel
   through (``StorageConnection.set_security_context`` → PG session vars +
   app-level predicate injection). Tenant A's connection cannot see tenant B's
   rows; a CUI caller cannot read SECRET rows (read-up blocked) while a SECRET
   caller can (read-down, dominated-set ``IN`` semantics).
2. ``analyst_iqe`` — ``cortex.analyst.ask`` IQE path: the registered collection
   reads the probe table through the connection the analyst threaded the
   CortexContext into.
3. ``analyst_nlq`` — ``cortex.analyst.ask`` NL→SQL fallback (mode="nlq") with a
   stubbed SQL generator: proves the ctx-expose-03 fix (NLQ execution now
   threads the RLS context instead of the dashboard's context-free
   ``execute_safely``).

The four *nominal* entry points named in the task (Python API, chat, REST, MCP)
collapse to the Python API today — chat/REST/MCP Cortex surfaces are not yet
built (see docs/features/cortex-rls-audit.md). Every path that leaves Cortex to
read tenant rows goes through the analyst, so proving the analyst paths proves
the isolation contract for the exposed surface.

Run from the repo root::

    ICDEV_STORAGE_BACKEND=postgresql python tools/cortex/db/verify_tenant_isolation.py --json

Exit code: 0 when isolation holds or PG is unavailable (skipped); 1 on any
leak or unexpected error.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import Any, Optional

# Running this file directly puts its own dir (tools/cortex/db) on sys.path[0],
# which shadows the repo-root ``tools`` package. Resolve the repo root from
# __file__ (never cwd — worktree-safe) so ``tools.cortex.*`` imports resolve.
_REPO_ROOT = str(Path(__file__).resolve().parents[3])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_PROBE_TABLE = "cortex_rls_probe"
_TENANT_A = "ctx-expose-03-tenant-a"
_TENANT_B = "ctx-expose-03-tenant-b"


# ---------------------------------------------------------------------------
# PG probe fixture
# ---------------------------------------------------------------------------
def _pg_connection() -> Optional[Any]:
    """Return a live PostgreSQL StorageConnection, or None when unavailable."""
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
    except Exception:
        return None
    if getattr(conn, "_backend", "sqlite") != "postgresql":
        try:
            conn.close()
        except Exception:
            pass
        return None
    return conn


def _seed_probe(conn: Any) -> None:
    """Create + seed the RLS-protected probe table with two tenants.

    tenant-a carries both a CUI and a SECRET row so classification read-down
    can be exercised; tenant-b carries a single CUI row.

    A native PG RLS policy (FORCE) is created as defense-in-depth, but note:
    when the app DB role holds BYPASSRLS/superuser (the case in many ICDEV
    dev/CI databases), native policies are bypassed entirely. The load-bearing
    isolation control this verifier exercises is therefore the *application-
    level predicate injection* in ``StorageCursor._inject_rls`` — driven by the
    threaded security context, it rewrites reads with ``tenant_id = ? AND
    classification IN (<dominated set>)``. Seeding sets the session tenant per
    insert so the WITH-CHECK side of the policy passes under non-bypass roles.
    """
    conn.execute(f"DROP TABLE IF EXISTS {_PROBE_TABLE}")
    conn.execute(
        f"""CREATE TABLE {_PROBE_TABLE} (
            id SERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            classification TEXT NOT NULL DEFAULT 'CUI',
            content TEXT NOT NULL
        )"""
    )
    conn.execute(f"ALTER TABLE {_PROBE_TABLE} ENABLE ROW LEVEL SECURITY")
    conn.execute(f"ALTER TABLE {_PROBE_TABLE} FORCE ROW LEVEL SECURITY")
    conn.execute(f"DROP POLICY IF EXISTS rls_probe_tenant ON {_PROBE_TABLE}")
    conn.execute(
        f"""CREATE POLICY rls_probe_tenant ON {_PROBE_TABLE}
            USING (tenant_id = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"""
    )
    conn.commit()

    seed = [
        (_TENANT_A, "CUI", "tenant-a public row"),
        (_TENANT_A, "SECRET", "tenant-a secret row"),
        (_TENANT_B, "CUI", "tenant-b public row"),
    ]
    for tenant_id, classification, content in seed:
        conn.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
        conn.execute(
            f"INSERT INTO {_PROBE_TABLE} (tenant_id, classification, content) "
            "VALUES (%s, %s, %s)",
            (tenant_id, classification, content),
        )
    conn.commit()
    # Reset the raw session var so nothing downstream inherits a seed tenant.
    conn.execute("SELECT set_config('app.tenant_id', '', false)")
    conn.commit()


def _drop_probe(conn: Any) -> None:
    try:
        conn.execute("SELECT set_config('app.tenant_id', '', false)")
        conn.execute(f"DROP TABLE IF EXISTS {_PROBE_TABLE}")
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def _tenants_seen(conn: Any, ctx) -> set:
    """Tenants visible when reading the probe under a CortexContext."""
    from tools.cortex.analyst import _apply_security_context

    _apply_security_context(conn, ctx)
    rows = conn.execute(f"SELECT tenant_id FROM {_PROBE_TABLE}").fetchall()
    conn.set_security_context(None)  # rls-bypass: reset probe conn to no-tenant baseline between isolation checks (task ctx-expose-03 leakage verifier, not runtime request code)
    return {(dict(r) if isinstance(r, dict) else {"tenant_id": r[0]})["tenant_id"] for r in rows}


def _classifications_seen(conn: Any, ctx) -> set:
    from tools.cortex.analyst import _apply_security_context

    _apply_security_context(conn, ctx)
    rows = conn.execute(
        f"SELECT classification FROM {_PROBE_TABLE} WHERE tenant_id = %s",
        (ctx.tenant_id,),
    ).fetchall()
    conn.set_security_context(None)  # rls-bypass: reset probe conn to no-tenant baseline between isolation checks (task ctx-expose-03 leakage verifier, not runtime request code)
    return {
        (dict(r) if isinstance(r, dict) else {"classification": r[0]})["classification"]
        for r in rows
    }


def _check_connection(conn: Any) -> dict:
    """Primitive: security-context threading isolates tenants + honors read-down."""
    from tools.cortex.schemas import CortexContext

    a_seen = _tenants_seen(conn, CortexContext(tenant_id=_TENANT_A, classification="CUI"))
    b_seen = _tenants_seen(conn, CortexContext(tenant_id=_TENANT_B, classification="CUI"))

    cui_cls = _classifications_seen(
        conn, CortexContext(tenant_id=_TENANT_A, classification="CUI")
    )
    secret_cls = _classifications_seen(
        conn, CortexContext(tenant_id=_TENANT_A, classification="SECRET")
    )

    failures = []
    if a_seen != {_TENANT_A}:
        failures.append(f"tenant A connection saw {sorted(a_seen)}, expected only {_TENANT_A!r}")
    if b_seen != {_TENANT_B}:
        failures.append(f"tenant B connection saw {sorted(b_seen)}, expected only {_TENANT_B!r}")
    if "SECRET" in cui_cls:
        failures.append(f"CUI caller read SECRET row (read-up leak): saw {sorted(cui_cls)}")
    if "SECRET" not in secret_cls or "CUI" not in secret_cls:
        failures.append(
            f"SECRET caller missing read-down rows: saw {sorted(secret_cls)}, "
            "expected both CUI and SECRET"
        )
    return {
        "name": "connection",
        "passed": not failures,
        "tenant_a_saw": sorted(a_seen),
        "tenant_b_saw": sorted(b_seen),
        "cui_caller_classifications": sorted(cui_cls),
        "secret_caller_classifications": sorted(secret_cls),
        "failures": failures,
    }


def _check_analyst_iqe() -> dict:
    """IQE entry point: analyst threads the CortexContext into the probe read."""
    from tools.cortex.analyst import ask
    from tools.cortex.schemas import CortexContext
    from tools.iqe import executor as _executor
    from tools.iqe.executor import register_collection

    def _probe_adapter(conn):
        # Read through the security-context-bearing connection the analyst
        # opened and threaded ctx into — the row set is RLS-filtered here.
        rows = conn.execute(f"SELECT tenant_id FROM {_PROBE_TABLE}").fetchall()
        return [dict(r) if isinstance(r, dict) else {"tenant_id": r[0]} for r in rows]

    saved = dict(_executor._default._registry)
    register_collection(_PROBE_TABLE, _probe_adapter)
    try:
        res_a = ask(
            f"show all {_PROBE_TABLE}",
            collections=[_PROBE_TABLE],
            ctx=CortexContext(tenant_id=_TENANT_A, classification="CUI"),
        )
        res_b = ask(
            f"show all {_PROBE_TABLE}",
            collections=[_PROBE_TABLE],
            ctx=CortexContext(tenant_id=_TENANT_B, classification="CUI"),
        )
    finally:
        _executor._default._registry.clear()
        _executor._default._registry.update(saved)

    a_seen = sorted({r["tenant_id"] for r in res_a.data.get("rows", [])})
    b_seen = sorted({r["tenant_id"] for r in res_b.data.get("rows", [])})
    failures = []
    if a_seen != [_TENANT_A]:
        failures.append(f"IQE tenant A answer saw {a_seen}, expected only [{_TENANT_A!r}]")
    if b_seen != [_TENANT_B]:
        failures.append(f"IQE tenant B answer saw {b_seen}, expected only [{_TENANT_B!r}]")
    return {"name": "analyst_iqe", "passed": not failures, "tenant_a_saw": a_seen,
            "tenant_b_saw": b_seen, "failures": failures}


def _check_analyst_nlq() -> dict:
    """NLQ entry point: the ctx-expose-03 fix threads RLS context into execution."""
    from tools.cortex.analyst import ask
    from tools.cortex.schemas import CortexContext
    from tools.dashboard import nlq_processor as _nlq
    from tools.iqe import executor as _executor
    from tools.iqe.executor import register_collection

    probe_sql = f"SELECT id, tenant_id, content FROM {_PROBE_TABLE}"

    # Register the probe so it lands on the analyst's table allowlist, and stub
    # the LLM SQL generator so no provider call is made — the test controls SQL.
    saved_registry = dict(_executor._default._registry)
    register_collection(_PROBE_TABLE, lambda conn: [])
    saved_gen = _nlq.generate_sql_via_bedrock
    saved_schema = _nlq.extract_schema
    _nlq.generate_sql_via_bedrock = lambda question, schema=None, exclude_model_ids=None: probe_sql
    _nlq.extract_schema = lambda: {}
    try:
        res_a = ask(
            "list probe rows for isolation check",
            mode="nlq",
            ctx=CortexContext(tenant_id=_TENANT_A, classification="CUI"),
        )
        res_b = ask(
            "list probe rows for isolation check",
            mode="nlq",
            ctx=CortexContext(tenant_id=_TENANT_B, classification="CUI"),
        )
    finally:
        _nlq.generate_sql_via_bedrock = saved_gen
        _nlq.extract_schema = saved_schema
        _executor._default._registry.clear()
        _executor._default._registry.update(saved_registry)

    a_seen = sorted({r["tenant_id"] for r in res_a.data.get("rows", [])})
    b_seen = sorted({r["tenant_id"] for r in res_b.data.get("rows", [])})
    failures = []
    if a_seen != [_TENANT_A]:
        failures.append(f"NLQ tenant A answer saw {a_seen}, expected only [{_TENANT_A!r}]")
    if b_seen != [_TENANT_B]:
        failures.append(f"NLQ tenant B answer saw {b_seen}, expected only [{_TENANT_B!r}]")
    return {"name": "analyst_nlq", "passed": not failures, "tenant_a_saw": a_seen,
            "tenant_b_saw": b_seen, "failures": failures}


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def run_verification() -> dict:
    """Run all isolation checks against live PG. Returns a structured report."""
    conn = _pg_connection()
    if conn is None:
        return {
            "status": "skipped",
            "reason": "PostgreSQL backend unreachable or inactive "
            "(set ICDEV_STORAGE_BACKEND=postgresql against a live PG instance)",
            "checks": [],
        }
    try:
        _seed_probe(conn)
        checks = [_check_connection(conn), _check_analyst_iqe(), _check_analyst_nlq()]
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "reason": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
            "checks": [],
        }
    finally:
        _drop_probe(conn)
        try:
            conn.close()
        except Exception:
            pass

    passed = all(c["passed"] for c in checks)
    return {"status": "passed" if passed else "failed", "checks": checks}


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON report")
    args = parser.parse_args(argv)

    report = run_verification()
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(f"Cortex tenant-isolation verification: {report['status'].upper()}")
        if report.get("reason"):
            print(f"  reason: {report['reason']}")
        for check in report.get("checks", []):
            mark = "PASS" if check["passed"] else "FAIL"
            print(f"  [{mark}] {check['name']}")
            for failure in check.get("failures", []):
                print(f"         - {failure}")

    # skipped/passed -> 0; failed/error -> 1
    return 0 if report["status"] in ("passed", "skipped") else 1


if __name__ == "__main__":
    sys.exit(main())
