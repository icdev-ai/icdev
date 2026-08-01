# CUI // SP-CTI
"""Phase 2 — Health Prober.

Runs deterministic health probes against every component node in the
kg-icdev-self-awareness graph and persists per-probe snapshots to
``awareness_component_health``. Each probe is a small, cheap check
that produces a ``status`` (pass|fail|warn|error) and a JSON detail
blob. Downstream consumers (``drift_detector``) diff snapshots over
time to surface regressions.

Probe types shipped in Phase 2:
  * http_head           — HTTP HEAD against a dashboard_route
  * module_import       — `python -c "import <module>"` for tool files
  * coherence_status    — parse coherence_checker --json per-check results

Deferred to later sub-phases (need more infrastructure):
  * test_green          — pytest pass-rate tracking via hook
  * api_surface_match   — diff api_surface_extractor output vs baseline
  * db_table_present    — verify DB tables still exist

Design:
  * Pure stdlib + optional requests (falls back to urllib). No LLM.
  * Respects enablement — disabled components are skipped entirely.
  * Every probe call writes ONE row to awareness_component_health.
  * Each --run-all invocation writes a run summary row to
    awareness_run_log so drift_detector can join snapshots by run.

CLI:
    python tools/awareness/health_prober.py --run-all --json
    python tools/awareness/health_prober.py --probe http_head --json
    python tools/awareness/health_prober.py --stats --json
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import argparse
import ast
import hashlib
import json
import logging
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

LOG = get_logger("health_prober")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.awareness.enablement import (  # noqa: E402
    is_component_enabled,
    load_enablement_flags,
    load_enablement_map,
)

try:
    from tools.db.storage import get_connection  # noqa: E402
except ImportError:
    get_connection = None  # type: ignore[assignment]

GRAPH_ID = "kg-icdev-self-awareness"
# Use the IPv4 loopback explicitly. On Windows, "localhost" resolves to ::1
# (IPv6) first, but the dashboard listens on IPv4 only — so every probe pays a
# ~2s failed-IPv6-connect penalty before falling back. Across dozens of
# http_head probes per cycle that added minutes, pushing the awareness cycle
# past its watchdog and stalling self_monitor's probe refresh. 127.0.0.1 is ~30x
# faster (0.07s vs 2.17s measured).
DASHBOARD_BASE = "http://127.0.0.1:5050"
HTTP_HEAD_TIMEOUT = 5.0
IMPORT_PROBE_TIMEOUT = 15.0
_AWARENESS_CONFIG = BASE_DIR / "args" / "awareness_config.yaml"


def _load_import_suppressions() -> Set[str]:
    """Load the module_import_suppressions list from awareness_config.yaml.

    Returns a set of normalised posix-style paths (e.g. 'tools/db/foo.py')
    so callers can do a simple ``file_path in suppressions`` check.
    """
    try:
        import yaml  # type: ignore[import]
        with _AWARENESS_CONFIG.open(encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        raw: List[str] = cfg.get("module_import_suppressions") or []
        return {str(Path(p).as_posix()) for p in raw}
    except Exception:
        return set()


# ---------------------------------------------------------------------------
# Schema management — create awareness_run_log + awareness_learned_edges
# on first use. awareness_component_health already exists in the live DB.
# ---------------------------------------------------------------------------


_RUN_LOG_DDL = """
CREATE TABLE IF NOT EXISTS awareness_run_log (
    run_id        TEXT PRIMARY KEY,
    phase         TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    completed_at  TEXT,
    status        TEXT NOT NULL DEFAULT 'running',
    probes_ok     INTEGER DEFAULT 0,
    probes_fail   INTEGER DEFAULT 0,
    elapsed_ms    INTEGER,
    details_json  TEXT
)
"""

_LEARNED_EDGES_DDL = """
CREATE TABLE IF NOT EXISTS awareness_learned_edges (
    id              TEXT PRIMARY KEY,
    source_node_id  TEXT NOT NULL,
    target_node_id  TEXT NOT NULL,
    relation_type   TEXT NOT NULL,
    evidence_count  INTEGER DEFAULT 1,
    confidence      REAL NOT NULL,
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL
)
"""


def _ensure_tables(conn: Any) -> None:
    """Create the Phase 2 tables if missing. Idempotent."""
    try:
        conn.execute(_RUN_LOG_DDL)
        conn.execute(_LEARNED_EDGES_DDL)
        conn.commit()
    except Exception as exc:
        LOG.warning("_ensure_tables failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Probe result dataclass
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _snapshot_id(node_id: str, probe_type: str, run_id: str) -> str:
    h = hashlib.sha256(f"{node_id}::{probe_type}::{run_id}".encode("utf-8")).hexdigest()
    return f"probe-{h[:20]}"


def _write_snapshot(
    conn: Any,
    node_id: str,
    probe_type: str,
    status: str,
    detail: Dict[str, Any],
    run_id: str,
) -> bool:
    """Persist one probe result to awareness_component_health.

    Returns True on success. detail is serialized to JSON with
    run_id inlined for drift_detector join-by-run-id queries.
    """
    detail_with_run = dict(detail)
    detail_with_run["run_id"] = run_id
    snap_id = _snapshot_id(node_id, probe_type, run_id)
    now = _now_iso()
    try:
        conn.execute(
            "INSERT INTO awareness_component_health "
            "(id, node_id, probe_type, status, detail, probed_at) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT(id) DO UPDATE SET "
            "status = excluded.status, detail = excluded.detail, "
            "probed_at = excluded.probed_at",
            (
                snap_id,
                node_id,
                probe_type,
                status,
                json.dumps(detail_with_run, ensure_ascii=False),
                now,
            ),
        )
        conn.commit()
        return True
    except Exception as exc:
        LOG.warning("snapshot write failed (%s %s): %s", node_id, probe_type, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return False


# ---------------------------------------------------------------------------
# Node loader — reads components from kg_nodes
# ---------------------------------------------------------------------------


def _load_component_nodes(
    conn: Any, entity_types: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """Fetch component nodes from kg_nodes, optionally filtered by
    entity_type. Returns dicts with id, label, entity_type, and a
    parsed properties dict.
    """
    if entity_types:
        placeholders = ",".join(["%s"] * len(entity_types))
        rows = conn.execute(
            f"SELECT id, label, entity_type, properties FROM kg_nodes "
            f"WHERE graph_id = %s AND entity_type IN ({placeholders})",  # nosec B608
            tuple([GRAPH_ID] + entity_types),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, label, entity_type, properties FROM kg_nodes "
            "WHERE graph_id = %s",
            (GRAPH_ID,),
        ).fetchall()
    result: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        try:
            d["properties"] = json.loads(d.get("properties") or "{}")
        except Exception:
            d["properties"] = {}
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------


_INTROSPECT_PATH = "/api/_introspect/routes"
_HTTP_HEAD_MAX_ROUTES = 80  # safety cap on routes probed per cycle


def _fetch_probeable_routes() -> Optional[List[str]]:
    """Ask the live dashboard for its real GET-able, parameter-free page routes.

    Returns the list, or None if the dashboard is unreachable / the endpoint is
    missing (older build). Sourcing from the running url_map means we probe the
    ACTUAL mounted paths (blueprint url_prefixes applied), not decorator-relative
    paths — which eliminated a wave of false 404s.
    """
    url = DASHBOARD_BASE + _INTROSPECT_PATH
    if not url.startswith(("http://", "https://")):
        return None
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=HTTP_HEAD_TIMEOUT) as resp:  # nosec B310 -- scheme validated above
            if resp.status >= 400:
                return None
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        routes = data.get("routes") or []
        return [r for r in routes if isinstance(r, str) and r.startswith("/")]
    except Exception as exc:
        LOG.warning("route introspection fetch failed: %s", exc)
        return None


def _probe_http_head(
    conn: Any,
    run_id: str,
    flags: Dict[str, bool],
    mapping: List[Dict[str, Any]],
) -> Dict[str, int]:
    """HEAD the dashboard's real page routes (from the live url_map).

    Replaces the old approach of walking canvas ``blueprint_routes`` (which stored
    decorator-relative paths without url_prefixes, and parameterized/POST routes —
    producing false 404s). We now fetch the actual GET-able, parameter-free routes
    from ``/api/_introspect/routes`` and HEAD each. If the dashboard is unreachable,
    that itself is the signal — one ``dashboard::unreachable`` failure.
    """
    ok = 0
    fail = 0
    skipped = 0

    routes = _fetch_probeable_routes()
    if routes is None:
        # Dashboard down or introspection endpoint missing → single real signal.
        _write_snapshot(
            conn, "dashboard::unreachable", "http_head", "fail",
            {"route": _INTROSPECT_PATH, "error": "dashboard unreachable or introspection endpoint missing"},
            run_id,
        )
        return {"ok": 0, "fail": 1, "skipped": 0}

    # Dashboard reachable — clear any prior 'unreachable' failure (recovery), so a
    # down→up transition supersedes the old fail instead of lingering forever.
    _write_snapshot(
        conn, "dashboard::unreachable", "http_head", "pass",
        {"route": _INTROSPECT_PATH, "recovered": True}, run_id,
    )

    for route in routes[:_HTTP_HEAD_MAX_ROUTES]:
        url = DASHBOARD_BASE + route
        if not url.startswith(("http://", "https://")):
            continue
        node_id = f"route::{route}"
        status = "fail"
        detail: Dict[str, Any] = {"route": route}
        start = time.time()
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=HTTP_HEAD_TIMEOUT) as resp:  # nosec B310 -- scheme validated above
                code = resp.status
                detail["status_code"] = code
                detail["response_time_ms"] = int((time.time() - start) * 1000)
                status = "pass" if code < 400 else "fail"
        except urllib.error.HTTPError as e:
            detail["status_code"] = e.code
            detail["response_time_ms"] = int((time.time() - start) * 1000)
            # 405 = route exists but HEAD not allowed — still healthy.
            status = "pass" if e.code == 405 else "fail"
        except Exception as exc:
            detail["error"] = str(exc)[:200]

        if _write_snapshot(conn, node_id, "http_head", status, detail, run_id):
            if status == "pass":
                ok += 1
            else:
                fail += 1

    if len(routes) > _HTTP_HEAD_MAX_ROUTES:
        skipped = len(routes) - _HTTP_HEAD_MAX_ROUTES
        LOG.info("http_head: probed %d of %d routes (cap %d)", _HTTP_HEAD_MAX_ROUTES, len(routes), _HTTP_HEAD_MAX_ROUTES)
    return {"ok": ok, "fail": fail, "skipped": skipped}


def _prev_module_import_status(conn: Any, node_id: str) -> str:
    """Return the status of the most recent module_import snapshot for this node
    from a PREVIOUS run (i.e. not the current run_id). Returns 'pass' if no
    prior snapshot exists (benefit of the doubt).
    """
    try:
        row = conn.execute(
            "SELECT status FROM awareness_component_health "
            "WHERE node_id = %s AND probe_type = 'module_import' "
            "ORDER BY probed_at DESC LIMIT 1",
            (node_id,),
        ).fetchone()
        return dict(row)["status"] if row else "pass"
    except Exception:
        return "pass"


def _probe_module_import(
    conn: Any,
    run_id: str,
    flags: Dict[str, bool],
    mapping: List[Dict[str, Any]],
) -> Dict[str, int]:
    """Verify every tool component can be imported.

    Cheap version: uses AST parse instead of subprocess `python -c`
    per file, which would take forever on 842 tools. AST parse
    catches syntax errors and missing structure; full import-time
    errors are caught by the coherence_status probe.

    2-probe confirmation: a single failing probe writes 'warn' rather
    than 'fail'. Only a second consecutive failure (prev snapshot also
    non-pass) promotes to 'fail'. This eliminates false-positive kanban
    tasks from transient file-system or encoding edge-cases.
    """
    ok = 0
    fail = 0
    skipped = 0

    suppressions = _load_import_suppressions()
    nodes = _load_component_nodes(conn, entity_types=["tool", "reflex", "mcp_server"])
    for node in nodes:
        if not is_component_enabled(
            node["id"],
            {"entity_type": node["entity_type"], **node["properties"]},
            flags=flags, mapping=mapping,
        ):
            skipped += 1
            continue
        file_path = node["properties"].get("file_path", "")
        if not file_path or not file_path.endswith(".py"):
            continue
        # Skip paths suppressed in awareness_config.yaml (child-app artifacts,
        # renamed tools still referenced in the knowledge graph).
        norm_path = str(Path(file_path).as_posix())
        if norm_path in suppressions:
            skipped += 1
            continue
        abs_path = BASE_DIR / file_path
        if not abs_path.exists():
            prev = _prev_module_import_status(conn, node["id"])
            # First-time failure: write 'warn' and wait for confirmation.
            # Consecutive failure: escalate to 'fail' to trigger a kanban card.
            status = "fail" if prev != "pass" else "warn"
            detail = {"file_path": file_path, "error": "file not found", "confirmation": prev != "pass"}
            _write_snapshot(conn, node["id"], "module_import", status, detail, run_id)
            if status == "fail":
                fail += 1
            continue
        detail = {"file_path": file_path}
        try:
            source = abs_path.read_text(encoding="utf-8", errors="replace")
            ast.parse(source, filename=str(abs_path))
            status = "pass"
            detail["ast_ok"] = True
        except SyntaxError as e:
            status = "fail"
            detail["error"] = f"SyntaxError: {e}"[:200]
        except Exception as e:
            status = "fail"
            detail["error"] = str(e)[:200]

        # Apply 2-probe confirmation for syntax failures too.
        if status == "fail":
            prev = _prev_module_import_status(conn, node["id"])
            if prev == "pass":
                status = "warn"
                detail["confirmation"] = False

        if _write_snapshot(conn, node["id"], "module_import", status, detail, run_id):
            if status == "pass":
                ok += 1
            elif status == "fail":
                fail += 1

    return {"ok": ok, "fail": fail, "skipped": skipped}


def _probe_coherence_status(
    conn: Any, run_id: str, **_kwargs: Any
) -> Dict[str, int]:
    """Run coherence_checker --all --json and record one snapshot
    per check_id. A failed check_id becomes a fail snapshot; warns
    become warn snapshots.
    """
    ok = 0
    fail = 0
    try:
        result = subprocess.run(
            [sys.executable, "tools/workflow/coherence_checker.py", "--all", "--json"],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=120,
        )
        stdout = result.stdout or ""
        # Coherence checker prints JSON as last lines
        try:
            report = json.loads(stdout)
        except json.JSONDecodeError:
            # Log noise precedes the JSON — find the first '{' at the
            # start of a line (not inside a string like '{%' in messages)
            idx = -1
            for i, ch in enumerate(stdout):
                if ch == "{" and (i == 0 or stdout[i - 1] == "\n"):
                    idx = i
                    break
            if idx >= 0:
                report = json.loads(stdout[idx:])
            else:
                return {"ok": 0, "fail": 1, "error": "could not parse coherence output"}
    except Exception as exc:
        return {"ok": 0, "fail": 1, "error": str(exc)[:200]}

    for check in report.get("checks", []):
        check_id = check.get("check_id", "unknown")
        status = check.get("status", "error")
        # Map warn → pass for health-tracking purposes (warn is not a regression)
        tracked_status = "pass" if status in ("pass", "warn") else "fail"
        detail = {
            "check_id": check_id,
            "original_status": status,
            "message": check.get("message", "")[:200],
        }
        # Use a synthetic node_id keyed by check_id so drift can
        # diff one check's health over time
        node_id = f"coherence::{check_id}"
        if _write_snapshot(conn, node_id, "coherence_status", tracked_status, detail, run_id):
            if tracked_status == "pass":
                ok += 1
            else:
                fail += 1

    return {"ok": ok, "fail": fail}


def _probe_gap_tool_not_in_manifest(
    conn: Any,
    run_id: str,
    **_kwargs: Any,
) -> Dict[str, int]:
    """Run the gap_detector tool_not_in_manifest rule and record one
    snapshot. Zero findings → pass (gap resolved); any findings → fail.
    """
    try:
        result = subprocess.run(
            [
                sys.executable,
                "tools/awareness/gap_detector.py",
                "--detect",
                "--rule", "tool_not_in_manifest",
                "--dry-run",
                "--json",
            ],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=60,
        )
        stdout = result.stdout or ""
        try:
            report = json.loads(stdout)
        except json.JSONDecodeError:
            idx = stdout.rfind("{")
            if idx >= 0:
                report = json.loads(stdout[idx:])
            else:
                return {"ok": 0, "fail": 1, "error": "could not parse gap_detector output"}
    except Exception as exc:
        return {"ok": 0, "fail": 1, "error": str(exc)[:200]}

    by_rule = report.get("by_rule", {})
    rule_data = by_rule.get("tool_not_in_manifest", {})
    findings_count = rule_data.get("findings", report.get("total_findings", 0))
    status = "pass" if findings_count == 0 else "fail"
    detail: Dict[str, Any] = {
        "rule": "tool_not_in_manifest",
        "findings_count": findings_count,
        "confidence": rule_data.get("confidence", 0.95),
        "severity": rule_data.get("severity", "medium"),
    }
    node_id = "gap::tool_not_in_manifest"
    _write_snapshot(conn, node_id, "gap::tool_not_in_manifest", status, detail, run_id)
    return {"ok": 1 if status == "pass" else 0, "fail": 0 if status == "pass" else 1}


# ---------------------------------------------------------------------------
# Probe registry + orchestrator
# ---------------------------------------------------------------------------


_PROBE_REGISTRY: Dict[str, Callable[..., Dict[str, int]]] = {
    "http_head": _probe_http_head,
    "module_import": _probe_module_import,
    "coherence_status": _probe_coherence_status,
    "gap::tool_not_in_manifest": _probe_gap_tool_not_in_manifest,
}


def run_all(probe_types: Optional[List[str]] = None) -> Dict[str, Any]:
    """Run every probe type in sequence, returning a run summary."""
    if get_connection is None:
        return {"error": "get_connection unavailable"}

    run_id = f"run-{uuid.uuid4().hex[:12]}"
    started_at = _now_iso()
    start_wall = time.time()

    conn = get_connection()
    _ensure_tables(conn)

    flags = load_enablement_flags()
    mapping = load_enablement_map()

    probe_keys = probe_types or list(_PROBE_REGISTRY.keys())
    results_per_probe: Dict[str, Dict[str, Any]] = {}
    total_ok = 0
    total_fail = 0

    for key in probe_keys:
        func = _PROBE_REGISTRY.get(key)
        if not func:
            results_per_probe[key] = {"error": "unknown probe"}
            continue
        try:
            r = func(conn, run_id, flags=flags, mapping=mapping)
            results_per_probe[key] = r
            total_ok += r.get("ok", 0)
            total_fail += r.get("fail", 0)
        except Exception as exc:
            LOG.error("probe %s raised: %s", key, exc)
            results_per_probe[key] = {"error": str(exc)[:200]}

    elapsed_ms = int((time.time() - start_wall) * 1000)
    completed_at = _now_iso()
    status = "success" if total_fail == 0 else "degraded"

    # Write a run_log row
    try:
        conn.execute(
            "INSERT INTO awareness_run_log "
            "(run_id, phase, started_at, completed_at, status, "
            " probes_ok, probes_fail, elapsed_ms, details_json) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT(run_id) DO UPDATE SET "
            "completed_at = excluded.completed_at, status = excluded.status, "
            "probes_ok = excluded.probes_ok, probes_fail = excluded.probes_fail, "
            "elapsed_ms = excluded.elapsed_ms, details_json = excluded.details_json",
            (
                run_id,
                "probe",
                started_at,
                completed_at,
                status,
                total_ok,
                total_fail,
                elapsed_ms,
                json.dumps(results_per_probe, ensure_ascii=False),
            ),
        )
        conn.commit()
    except Exception as exc:
        LOG.warning("run_log write failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass

    try:
        conn.close()
    except Exception:
        pass

    return {
        "run_id": run_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "elapsed_ms": elapsed_ms,
        "status": status,
        "probes": results_per_probe,
        "total_ok": total_ok,
        "total_fail": total_fail,
    }


def get_stats() -> Dict[str, Any]:
    """Return snapshot + run_log counts."""
    if get_connection is None:
        return {"error": "get_connection unavailable"}
    conn = get_connection()
    _ensure_tables(conn)
    try:
        r1 = conn.execute("SELECT COUNT(*) AS cnt FROM awareness_component_health").fetchone()
        r2 = conn.execute("SELECT COUNT(*) AS cnt FROM awareness_run_log").fetchone()
        rows = conn.execute(
            "SELECT probe_type, COUNT(*) AS cnt FROM awareness_component_health "
            "GROUP BY probe_type ORDER BY cnt DESC"
        ).fetchall()
        by_probe = {dict(r)["probe_type"]: dict(r)["cnt"] for r in rows}
        return {
            "snapshots": dict(r1).get("cnt", 0) if r1 else 0,
            "runs": dict(r2).get("cnt", 0) if r2 else 0,
            "by_probe": by_probe,
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="health_prober", description="Phase 2 — ICDEV component health prober"
    )
    parser.add_argument("--run-all", action="store_true", help="Run every probe")
    parser.add_argument("--probe", type=str, default=None, help="Run one probe only")
    parser.add_argument("--stats", action="store_true", help="Show snapshot stats")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if args.stats:
        result: Dict[str, Any] = get_stats()
    elif args.probe:
        result = run_all(probe_types=[args.probe])
    elif args.run_all:
        result = run_all()
    else:
        parser.print_help()
        return 1

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for k, v in result.items():
            print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
