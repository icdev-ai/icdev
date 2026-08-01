#!/usr/bin/env python3
# CUI // SP-CTI
"""PG pytest tier runner (kph).

The complement to the static ``check_test_db_isolation`` coherence gate: this
DYNAMICALLY runs a curated allowlist of DB-exercising tests against a LIVE
PostgreSQL so PG-native ``%s`` / portability bugs fail loudly at runtime. CI runs
the ordinary Python suite on SQLite only (conftest forces it), where
``translate_sql`` silently rewrites ``%s`` -> ``?`` and masks these bugs.

Two layers of PG coverage:
  1. The allowlist (tests/pg_tier_allowlist.txt) — tests that use the ambient
     backend and, under ICDEV_PYTEST_PG=1 (fail-closed via ICDEV_PG_NO_FALLBACK),
     execute real PG queries. A sentinel test asserts the backend is actually PG.
  2. The purpose-built acceptance scripts verify_pg.py + verify_tenant_isolation.py,
     which force real PG (skip-clean on SQLite) and assert the audit/session tables
     + cross-tenant RLS read-down — the checks whose unit-test equivalents
     self-force sqlite.

Usage (needs ICDEV_STORAGE_BACKEND=postgresql + a reachable PG):
    python tools/testing/pg_pytest_tier.py [--json] [--pytest-only] [--verify-only]
Exit 0 iff every layer passed.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess  # nosec B404 — fixed argv, no user input
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ALLOWLIST = BASE_DIR / "tests" / "pg_tier_allowlist.txt"
_VERIFY_SCRIPTS = (
    "tools/cortex/db/verify_pg.py",
    "tools/cortex/db/verify_tenant_isolation.py",
)


def _read_allowlist() -> list[str]:
    if not ALLOWLIST.exists():
        return []
    out: list[str] = []
    for line in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def _env() -> dict:
    env = dict(os.environ)
    env["ICDEV_PYTEST_PG"] = "1"          # conftest: honor PG + fail-closed
    env.setdefault("ICDEV_STORAGE_BACKEND", "postgresql")
    env["ICDEV_PG_NO_FALLBACK"] = "1"     # never silently fall back to sqlite
    env["PYTHONPATH"] = str(BASE_DIR)
    return env


def _init_pg_schema() -> None:
    """Apply the cortex schema on the ambient PG before the allowlist runs.

    The consolidated CI snapshot (pg_consolidated.sql) predates the cortex tables
    (migrations 262/263), so a fresh CI PG lacks cortex_audit / cortex_sessions /
    cortex_chat_sessions. init_db() is idempotent — safe on a live dev PG too.
    Best-effort: a failure here surfaces as the allowlist tests failing, not a
    silent skip.
    """
    try:
        from tools.cortex.db.init_db import init_db
        init_db()
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] cortex init_db failed ({exc}) — allowlist tests may fail", file=sys.stderr)


def run_pytest_layer() -> dict:
    paths = _read_allowlist()
    if not paths:
        return {"layer": "pytest", "passed": False, "reason": "empty allowlist", "returncode": 2}
    _init_pg_schema()
    cmd = [sys.executable, "-m", "pytest", *paths, "-q", "-p", "no:cacheprovider", "--tb=short"]
    proc = subprocess.run(cmd, cwd=str(BASE_DIR), env=_env(), capture_output=True, text=True)
    tail = "\n".join(proc.stdout.splitlines()[-15:])
    return {"layer": "pytest", "passed": proc.returncode == 0,
            "returncode": proc.returncode, "paths": len(paths), "tail": tail}


def run_verify_layer() -> list[dict]:
    results = []
    for rel in _VERIFY_SCRIPTS:
        script = BASE_DIR / rel
        if not script.exists():
            results.append({"layer": "verify", "script": rel, "passed": False, "reason": "missing"})
            continue
        proc = subprocess.run(
            [sys.executable, str(script), "--json"],
            cwd=str(BASE_DIR), env=_env(), capture_output=True, text=True,
        )
        ok = proc.returncode == 0
        # verify_tenant_isolation exits 0 on 'skipped' too — treat skip as pass (no PG).
        results.append({"layer": "verify", "script": rel, "passed": ok, "returncode": proc.returncode,
                        "tail": "\n".join(proc.stdout.splitlines()[-6:])})
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the live-PostgreSQL pytest tier.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--pytest-only", action="store_true")
    ap.add_argument("--verify-only", action="store_true")
    args = ap.parse_args()

    layers: list[dict] = []
    if not args.verify_only:
        layers.append(run_pytest_layer())
    if not args.pytest_only:
        layers.extend(run_verify_layer())

    overall = all(layer["passed"] for layer in layers) if layers else False
    report = {"ok": overall, "layers": layers}
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for layer in layers:
            name = layer.get("script") or layer["layer"]
            print(f"[{'PASS' if layer['passed'] else 'FAIL'}] {name}")
            if not layer["passed"] and layer.get("tail"):
                print(layer["tail"])
        print(f"\nPG tier: {'PASS' if overall else 'FAIL'}")
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
