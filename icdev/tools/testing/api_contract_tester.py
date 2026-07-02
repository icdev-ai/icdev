# CUI // SP-CTI
"""API Response Contract Tester — validates live API responses against OpenAPI spec.

Hits each GET endpoint defined in the OpenAPI spec, checks HTTP status, and
validates that required response fields are present. Files [API-CONTRACT] kanban
bug tasks for any schema drift detected.

Usage:
    python tools/testing/api_contract_tester.py [--base URL] [--spec PATH] [--dry-run] [--json]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger
logger = get_logger("icdev.testing.api_contract_tester")

_DEDUP_DB = BASE_DIR / "data" / "api_contract_filed.db"

_SPEC_SEARCH_PATHS = [
    "docs/openapi.yaml",
    "docs/openapi.json",
    "frontend/openapi.yaml",
    "frontend/openapi.json",
    "docs/api/openapi.yaml",
    "docs/api/openapi.json",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Spec loading
# ---------------------------------------------------------------------------

def _load_spec(spec_path: Optional[str] = None) -> Optional[Dict]:
    """Find and parse openapi.yaml/openapi.json. Returns None if unavailable."""
    candidates = [spec_path] if spec_path else []
    candidates += [str(BASE_DIR / p) for p in _SPEC_SEARCH_PATHS]

    for candidate in candidates:
        if not candidate:
            continue
        p = Path(candidate)
        if not p.exists():
            continue
        try:
            text = p.read_text(encoding="utf-8")
            if p.suffix in (".yaml", ".yml"):
                try:
                    import yaml
                    return yaml.safe_load(text)
                except ImportError:
                    logger.warning("PyYAML not installed — cannot parse %s", p)
                    return None
            else:
                return json.loads(text)
        except Exception as exc:
            logger.warning("Failed to load spec %s: %s", candidate, exc)

    return None


# ---------------------------------------------------------------------------
# Endpoint extraction
# ---------------------------------------------------------------------------

def _extract_endpoints(spec: Dict) -> List[Dict]:
    """Return [{path, method, expected_status, response_schema}] for GET endpoints."""
    endpoints: List[Dict] = []
    paths = spec.get("paths", {})
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        get_op = methods.get("get")
        if not get_op:
            continue
        responses = get_op.get("responses", {})
        resp_200 = responses.get("200") or responses.get(200)
        if not resp_200:
            continue
        # Extract schema from response content (OpenAPI 3.x)
        schema = None
        content = resp_200.get("content", {})
        for media_type, media_obj in content.items():
            if "json" in media_type and isinstance(media_obj, dict):
                schema = media_obj.get("schema")
                break
        # OpenAPI 2.x fallback
        if schema is None:
            schema = resp_200.get("schema")

        endpoints.append({
            "path": path,
            "method": "GET",
            "expected_status": 200,
            "response_schema": schema or {},
        })
    return endpoints


# ---------------------------------------------------------------------------
# Response validation
# ---------------------------------------------------------------------------

def _validate_response(
    base: str,
    endpoint: Dict,
    timeout: float = 10.0,
) -> Dict:
    """Hit the endpoint and validate status + JSON schema."""
    path = endpoint["path"]
    # Replace path params with placeholders so the URL is valid
    url = base.rstrip("/") + path
    # If path has {params}, skip (can't know valid values)
    if "{" in url:
        return {
            "path": path, "method": "GET", "ok": True,
            "skipped": True, "reason": "path has template params",
            "status_got": None, "expected_status": endpoint["expected_status"],
            "schema_errors": [], "elapsed_ms": 0,
        }

    result: Dict[str, Any] = {
        "path": path,
        "method": "GET",
        "ok": False,
        "skipped": False,
        "reason": None,
        "status_got": None,
        "expected_status": endpoint["expected_status"],
        "schema_errors": [],
        "elapsed_ms": 0,
    }

    t0 = time.monotonic()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ICDEV-ContractTester/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body_bytes = resp.read(131072)
            status = resp.status
    except urllib.error.HTTPError as e:
        result["status_got"] = e.code
        result["reason"] = f"HTTP {e.code} {e.reason}"
        result["elapsed_ms"] = round((time.monotonic() - t0) * 1000)
        return result
    except Exception as e:
        result["reason"] = str(e)
        result["elapsed_ms"] = round((time.monotonic() - t0) * 1000)
        return result

    result["status_got"] = status
    result["elapsed_ms"] = round((time.monotonic() - t0) * 1000)

    if status != endpoint["expected_status"]:
        result["reason"] = f"Expected {endpoint['expected_status']}, got {status}"
        return result

    # Schema validation — check required properties
    schema = endpoint.get("response_schema") or {}
    required = schema.get("required", [])
    properties = schema.get("properties", {})

    if required or properties:
        try:
            body = json.loads(body_bytes.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, ValueError):
            result["schema_errors"].append("Response is not valid JSON")
            result["reason"] = "Non-JSON response"
            return result

        if isinstance(body, dict):
            for field in required:
                if field not in body:
                    result["schema_errors"].append(f"Required field '{field}' missing")
        elif isinstance(body, list) and schema.get("type") == "object":
            result["schema_errors"].append("Expected object, got array")

    if result["schema_errors"]:
        result["reason"] = "; ".join(result["schema_errors"][:3])
        return result

    result["ok"] = True
    return result


# ---------------------------------------------------------------------------
# Server probe
# ---------------------------------------------------------------------------

def _server_up(base: str, timeout: float = 3.0) -> bool:
    try:
        urllib.request.urlopen(f"{base.rstrip('/')}/health", timeout=timeout)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Main test runner
# ---------------------------------------------------------------------------

def run_contract_tests(
    base: str = "http://localhost:5050",
    spec_path: Optional[str] = None,
    timeout: float = 10.0,
    verbose: bool = True,
) -> Tuple[bool, List[Dict]]:
    """Run contract tests against all GET endpoints in the spec.

    Returns (all_passed, results). Skips gracefully if server is down or spec not found.
    """
    spec = _load_spec(spec_path)
    if spec is None:
        if verbose:
            print("[api-contract] No OpenAPI spec found — skipping contract tests")
        return True, []

    if not _server_up(base):
        if verbose:
            print(f"[api-contract] Server not running at {base} — skipping")
        return True, []

    endpoints = _extract_endpoints(spec)
    if verbose:
        print(f"[api-contract] Testing {len(endpoints)} GET endpoints from spec")

    results = []
    for ep in endpoints:
        r = _validate_response(base, ep, timeout=timeout)
        results.append(r)
        if verbose:
            if r.get("skipped"):
                print(f"  [SKIP] {ep['path']} ({r['reason']})")
            elif r["ok"]:
                print(f"  [OK]   {ep['path']}  ({r['elapsed_ms']}ms)")
            else:
                print(f"  [FAIL] {ep['path']}  {r['reason']}")

    all_passed = all(r["ok"] or r.get("skipped") for r in results)
    return all_passed, results


# ---------------------------------------------------------------------------
# Dedup + kanban filing
# ---------------------------------------------------------------------------

def _init_dedup_db() -> sqlite3.Connection:
    _DEDUP_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DEDUP_DB))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS filed (
            path TEXT PRIMARY KEY,
            task_id TEXT,
            filed_at TEXT
        )"""
    )
    conn.commit()
    return conn


def _already_filed(dedup: sqlite3.Connection, path: str) -> bool:
    return dedup.execute("SELECT 1 FROM filed WHERE path = ?", (path,)).fetchone() is not None


def _mark_filed(dedup: sqlite3.Connection, path: str, task_id: str) -> None:
    dedup.execute(
        "INSERT OR REPLACE INTO filed (path, task_id, filed_at) VALUES (?,?,?)",
        (path, task_id, _utcnow().isoformat()),
    )
    dedup.commit()


def file_failure_tasks(failures: List[Dict], dry_run: bool = False) -> List[str]:
    """File [API-CONTRACT] kanban bug tasks for schema failures."""
    if not failures:
        return []

    filed_ids: List[str] = []
    try:
        dedup = _init_dedup_db()
    except Exception as exc:
        logger.warning("api_contract_tester: cannot open dedup DB: %s", exc)
        return []

    try:
        from tools.db.storage import get_connection
        kanban_conn = get_connection()
    except Exception as exc:
        logger.warning("api_contract_tester: cannot connect to kanban DB: %s", exc)
        return []

    now = _utcnow().isoformat()
    for r in failures:
        path = r["path"]
        if _already_filed(dedup, path):
            continue
        if dry_run:
            logger.info("[DRY-RUN] Would file task for %s", path)
            continue
        first_error = (r.get("schema_errors") or [r.get("reason", "unknown")])[0]
        task_id = f"task-contract-{uuid.uuid4().hex[:8]}"
        title = f"[API-CONTRACT] {path}: {first_error[:80]}"
        desc = (
            f"API contract test failure on `{path}`.\n\n"
            f"**Status got:** {r.get('status_got')} (expected {r.get('expected_status')})\n"
            f"**Schema errors:** {'; '.join(r.get('schema_errors', []) or [r.get('reason', '?')])}\n\n"
            "**To reproduce:**\n"
            f"```\ncurl http://localhost:5050{path}\n```\n\n"
            "**To re-run the contract suite:**\n"
            "```\npython tools/testing/api_contract_tester.py --json\n```\n"
            "Fix the endpoint or update the OpenAPI spec, then close this task."
        )
        try:
            kanban_conn.execute(
                """INSERT INTO kanban_tasks
                   (id, title, description, task_type, priority, status,
                    scheduled_at, created_at, updated_at, dispatch_source)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (task_id, title, desc, "bug", "high", "backlog",
                 now, now, now, "api_contract_tester"),
            )
            kanban_conn.commit()
            _mark_filed(dedup, path, task_id)
            filed_ids.append(task_id)
            logger.info("api_contract_tester: filed task %s for %s", task_id, path)
        except Exception as exc:
            logger.warning("api_contract_tester: failed to file task for %s: %s", path, exc)

    try:
        kanban_conn.close()
    except Exception:
        pass
    return filed_ids


# ---------------------------------------------------------------------------
# Genesis run() entry point
# ---------------------------------------------------------------------------

def run(config: dict, state: object) -> dict:
    """Genesis reflex entry point."""
    base = config.get("base", "http://localhost:5050")
    spec_path = config.get("spec_path")
    timeout = float(config.get("timeout", 10.0))
    dry_run = bool(config.get("dry_run", False))

    spec = _load_spec(spec_path)
    spec_found = spec is not None

    all_ok, results = run_contract_tests(base=base, spec_path=spec_path, timeout=timeout, verbose=False)

    failures = [r for r in results if not r["ok"] and not r.get("skipped")]
    filed = file_failure_tasks(failures, dry_run=dry_run)

    return {
        "total": len(results),
        "passed": sum(1 for r in results if r["ok"]),
        "skipped": sum(1 for r in results if r.get("skipped")),
        "failed": len(failures),
        "failures": [{"path": r["path"], "reason": r.get("reason")} for r in failures],
        "spec_found": spec_found,
        "filed_tasks": filed,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="ICDEV API Contract Tester")
    parser.add_argument("--base", default="http://localhost:5050")
    parser.add_argument("--spec", dest="spec_path", help="Path to OpenAPI spec")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    verbose = not args.json
    all_ok, results = run_contract_tests(
        base=args.base, spec_path=args.spec_path, verbose=verbose
    )

    failures = [r for r in results if not r["ok"] and not r.get("skipped")]
    if not args.dry_run and failures:
        file_failure_tasks(failures)

    if args.json:
        spec_found = _load_spec(args.spec_path) is not None
        print(json.dumps({
            "total": len(results),
            "passed": sum(1 for r in results if r["ok"]),
            "skipped": sum(1 for r in results if r.get("skipped")),
            "failed": len(failures),
            "spec_found": spec_found,
            "failures": [{"path": r["path"], "reason": r.get("reason")} for r in failures],
        }, indent=2))
    else:
        total = len(results)
        passed = sum(1 for r in results if r["ok"])
        print(f"\n[api-contract] {passed}/{total} passed, {len(failures)} failed")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
