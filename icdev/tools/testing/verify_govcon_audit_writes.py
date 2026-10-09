#!/usr/bin/env python3
# CUI // SP-CTI
"""Probe every tools/govcon ``_audit()`` against the ambient backend.

The runtime complement to ``tests/test_audit_event_type_parity.py``. That test
is static: it proves each module's INSERT names real columns, balances its
placeholders, and uses an event type ``VALID_EVENT_TYPES`` admits. It cannot
prove the write actually lands, because every ``_audit`` swallows its own
exception — which is precisely how 45 govcon modules recorded nothing while
their unit tests stayed green.

So this calls each ``_audit`` for real and then looks for the row. A module that
raises, or that returns cleanly without inserting, is reported as a failure.

Two ``_audit`` shapes exist and both are handled by inspecting the signature:

* ``_audit(conn, action, details, ...)`` — the event type is a literal inside
  the function.
* ``_audit(conn, event_type, action, details, ...)`` — the event type is at the
  call site, so one is lifted from the module's own calls rather than invented
  (an invented one would test the constraint, not the module).

``tools/govcon/procurement_vehicles.py`` takes no ``conn``: it opens and commits
its own. It is probed on its own connection and is the one module ``--commit``
cannot help or hurt.

Usage::

    python tools/testing/verify_govcon_audit_writes.py            # rollback (default)
    python tools/testing/verify_govcon_audit_writes.py --commit   # persist the rows
    python tools/testing/verify_govcon_audit_writes.py --json

Default is rollback: the probe row is read back inside the transaction, which
proves the INSERT was accepted by ``audit_trail_event_type_check``, then undone.
audit_trail is append-only (NIST AU) and immutable, so a committed probe row can
never be removed — pass ``--commit`` only when persisted evidence is wanted.

Exit 0 iff every module inserted its row.
"""
from __future__ import annotations

import argparse
import ast
import importlib
import inspect
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
GOVCON_DIR = BASE_DIR / "tools" / "govcon"

# Modules that open their own connection and commit inside _audit.
_SELF_CONNECTING = {"procurement_vehicles"}


def _audit_modules() -> List[str]:
    """govcon module names that write audit_trail and expose an _audit()."""
    names = []
    for path in sorted(GOVCON_DIR.glob("*.py")):
        src = path.read_text(encoding="utf-8", errors="replace")
        if "INSERT INTO audit_trail" in src and "def _audit(" in src:
            names.append(path.stem)
    return names


def _first_call_site_event_type(module_name: str) -> Optional[str]:
    """An event type this module actually passes to its own _audit().

    Only used for the parameterised shape. Returns None when the module has no
    literal call site, which is itself worth reporting: nothing pins the type.
    """
    path = GOVCON_DIR / f"{module_name}.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return None
    idx = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_audit":
            names = [a.arg for a in node.args.args]
            if "event_type" in names:
                idx = names.index("event_type")
            break
    if idx is None:
        return None
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_audit"):
            continue
        if len(node.args) > idx and isinstance(node.args[idx], ast.Constant):
            value = node.args[idx].value
            if isinstance(value, str):
                return value
        for kw in node.keywords:
            if kw.arg == "event_type" and isinstance(kw.value.value if isinstance(kw.value, ast.Constant) else None, str):
                return kw.value.value
    return None


def _row_count(conn, marker: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM audit_trail WHERE details LIKE %s",
        (f"%{marker}%",),
    ).fetchone()
    if hasattr(row, "keys"):
        return int(list(dict(row).values())[0])
    return int(row[0])


def _probe_one(module_name: str, marker: str, commit: bool) -> Dict[str, Any]:
    """Call one module's _audit() and report whether a row appeared."""
    from tools.db.storage import get_connection

    result: Dict[str, Any] = {"module": module_name, "ok": False}
    try:
        module = importlib.import_module(f"tools.govcon.{module_name}")
    except Exception as exc:  # noqa: BLE001 - an unimportable module is a real finding
        result["error"] = f"import failed: {exc}"
        return result

    audit = getattr(module, "_audit", None)
    if audit is None:
        result["error"] = "no _audit attribute after import"
        return result

    params = list(inspect.signature(audit).parameters)
    details = json.dumps({"probe": marker, "module": module_name})
    action = f"swp-audit-01 write probe ({module_name})"

    conn = get_connection()
    try:
        args: List[Any] = []
        if params and params[0] == "conn":
            args.append(conn)
        if "event_type" in params:
            event_type = _first_call_site_event_type(module_name)
            if event_type is None:
                result["error"] = "parameterised _audit with no literal call site"
                return result
            result["event_type"] = event_type
            args.append(event_type)
        args.extend([action, details])

        before = _row_count(conn, marker)
        audit(*args)
        if module_name in _SELF_CONNECTING:
            # It committed on its own connection; re-read on a fresh one.
            conn.close()
            conn = get_connection()
        elif commit:
            conn.commit()
        after = _row_count(conn, marker)
        result["rows_written"] = after - before
        result["ok"] = after > before
        if not result["ok"]:
            result["error"] = "_audit returned cleanly but inserted no row"
        if not commit and module_name not in _SELF_CONNECTING:
            conn.rollback()
    except Exception as exc:  # noqa: BLE001 - surface, do not swallow (that is the bug)
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 - close failures are not the finding
            pass
    return result


def run(commit: bool = False) -> Dict[str, Any]:
    from tools.db.storage import get_connection

    conn = get_connection()
    backend = getattr(conn, "_backend", "unknown")
    conn.close()

    marker = f"swp-audit-01-{uuid.uuid4().hex[:12]}"
    results = [_probe_one(name, marker, commit) for name in _audit_modules()]
    failed = [r for r in results if not r["ok"]]
    return {
        "backend": backend,
        "committed": commit,
        "marker": marker,
        "modules": len(results),
        "passed": len(results) - len(failed),
        "failed": [r["module"] for r in failed],
        "results": results,
        "ok": not failed and bool(results),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--commit",
        action="store_true",
        help="persist the probe rows (audit_trail is append-only — they cannot be removed)",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = run(commit=args.commit)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        mode = "committed" if report["committed"] else "rolled back"
        print(f"backend={report['backend']}  mode={mode}  marker={report['marker']}")
        print(f"{report['passed']}/{report['modules']} govcon modules wrote an audit row")
        for r in report["results"]:
            if not r["ok"]:
                print(f"  FAIL {r['module']}: {r.get('error')}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
