#!/usr/bin/env python3
# CUI // SP-CTI
"""Oracle Triage Reflex — auto-triage Oracle-suggested kanban tasks.

For each ``status='suggested'`` task with an ``oracle_lens``, applies a
deterministic verifier to decide:

  promote → move to 'backlog'  (real gap confirmed by file check / grep)
  dismiss → move to 'done'     (false positive, bypasses guard-22 with audit reason)
  skip    → leave as suggested (ambiguous; requires human judgment)

Lens-specific verifiers
-----------------------
tool_not_in_manifest
    Path.exists() on the subject file path.  File exists → promote (real but
    unregistered tool).  File DNE → dismiss (Oracle found a reference to a
    planned/deleted file that was never created).

route_not_listed
    Grep app.py and blueprint files for ``@*route(*subject*)`` decorator.
    Route found in Flask code → promote (real route missing from start.md).
    Route absent → dismiss (in seed plans / manifest docs only, not built).

orphan_db_table
    Grep ``migrations/`` for ``CREATE TABLE … <table>``.  If found → dismiss
    (migration exists, Oracle is wrong).  If absent, count code refs in
    ``tools/``.  refs ≥ 1 → promote (active code, missing migration).
    refs = 0 → dismiss (dead reference, no real gap).

No-lens heuristics (tasks created by self_debug / V&V scripts)
    ``Oracle RCA:`` prefix or ``diag-`` id prefix → promote.
    ``V&V`` anywhere in title → promote.
    ``[FR]`` prefix → backlog (feature request needs human scoping).
    Everything else → skip.

Batch cards (``[Batch] <lens>:`` prefix)
    Parse subjects from description, apply per-subject verifier, take the
    majority vote.  All dismiss → dismiss.  Any promote → promote.

CLI
---
    python tools/genesis/reflexes/oracle_triage.py --run --json
    python tools/genesis/reflexes/oracle_triage.py --run --dry-run --json

Genesis daemon
--------------
    Called as ``module.run(config, trust)`` returning ``{"success": bool, ...}``.
    Registered under the ``awareness`` cycle (runs every 3 h alongside
    gap detection so fresh Oracle cards are triaged in the same cycle).
"""
IMPLEMENTATION_STATUS = "full"
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import argparse
import json
import logging
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

LOG = get_logger("oracle_triage")

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from tools.db.storage import get_connection
except ImportError:
    get_connection = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MIGRATIONS_DIR = BASE_DIR / "migrations"
_TOOLS_DIR = BASE_DIR / "tools"
_DASHBOARD_DIR = _TOOLS_DIR / "dashboard"
_DEFAULT_API_BASE = "http://localhost:5050"

# Minimum code-reference count to treat an orphan table as a real gap.
# Tables with zero refs are dead references → dismiss.
_ORPHAN_MIN_REFS = 1

# oracle_lens values this reflex handles.
_HANDLED_LENSES = {"tool_not_in_manifest", "route_not_listed", "orphan_db_table"}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _fetch_suggested_tasks() -> List[Dict[str, Any]]:
    """Return all kanban_tasks with status='suggested', joined to oracle_predictions."""
    if get_connection is None:
        return []
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT kt.id, kt.title, kt.description, kt.priority, kt.task_type,
                   op.lens_name      AS oracle_lens,
                   op.confidence     AS oracle_confidence,
                   op.prediction_type AS oracle_prediction_type
            FROM kanban_tasks kt
            LEFT JOIN oracle_predictions op ON kt.source_prediction_id = op.id
            WHERE kt.status = 'suggested'
            ORDER BY op.confidence DESC NULLS LAST, kt.created_at ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Kanban move via local REST API
# ---------------------------------------------------------------------------


def _api_move(
    task_id: str,
    new_status: str,
    api_base: str,
    dry_run: bool,
    bypass_reason: Optional[str] = None,
) -> Tuple[bool, str]:
    """POST /api/kanban/tasks/<id>/move.

    Returns (success, message).
    If bypass_reason is set, adds bypass_verification=true to the payload
    (required by guard-22 for moves to 'done' without a verification row).
    """
    if dry_run:
        return True, f"[dry-run] would move {task_id} → {new_status}"

    payload: Dict[str, Any] = {"status": new_status}
    if bypass_reason:
        payload["bypass_verification"] = True
        payload["bypass_reason"] = bypass_reason

    body = json.dumps(payload).encode()
    url = f"{api_base}/api/kanban/tasks/{task_id}/move"
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            return True, result.get("status", new_status)
    except urllib.error.HTTPError as exc:
        body_bytes = exc.read()
        try:
            err = json.loads(body_bytes).get("error", str(body_bytes[:120]))
        except Exception:
            err = str(body_bytes[:120])
        return False, f"HTTP {exc.code}: {err}"
    except Exception as exc:
        return False, str(exc)


# ---------------------------------------------------------------------------
# Verifier: tool_not_in_manifest
# ---------------------------------------------------------------------------


def _verify_tool_not_in_manifest(subject: str) -> Tuple[str, str]:
    """Check whether the subject file path exists on disk.

    subject  — e.g. "tools/ttx/engine.py"

    Returns (action, reason):
      "promote", "File exists — real tool missing manifest entry"
      "dismiss", "File does not exist — Oracle false positive (planned/deleted file)"
    """
    path = BASE_DIR / subject.replace("\\", "/")
    if path.exists():
        return "promote", f"File exists ({path.stat().st_size} bytes) — needs manifest entry"
    return "dismiss", f"File does not exist at {subject} — Oracle false positive (planned/deleted)"


# ---------------------------------------------------------------------------
# Verifier: route_not_listed
# ---------------------------------------------------------------------------

_ROUTE_DECORATOR_RE = re.compile(
    r"""@\w+\.route\(\s*["']([^"']+)["']""",
    re.MULTILINE,
)


def _collect_flask_routes() -> set:
    """Return set of all route paths defined in app.py and blueprint files."""
    routes: set = set()
    for py_file in [_DASHBOARD_DIR / "app.py"] + list(_DASHBOARD_DIR.glob("*.py")):
        if not py_file.exists():
            continue
        try:
            src = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _ROUTE_DECORATOR_RE.finditer(src):
            routes.add(m.group(1).split("<")[0].rstrip("/") or "/")
    return routes


_flask_routes_cache: Optional[set] = None


def _verify_route_not_listed(subject: str) -> Tuple[str, str]:
    """Verify whether the route actually exists as a Flask decorator.

    subject — e.g. "/studio/narrate"

    The gap_detector fires when a Flask route is missing from start.md.
    If the route ISN'T in Flask code at all, the Oracle found a planning
    reference (seed script, manifest doc) — dismiss as a false positive.
    If the route IS in Flask code, the gap is real — promote.
    """
    global _flask_routes_cache
    if _flask_routes_cache is None:
        _flask_routes_cache = _collect_flask_routes()

    # Normalise the subject: strip trailing slash, collapse <param> segments
    needle = re.sub(r"<[^>]+>", "<param>", subject.rstrip("/")) or "/"

    # Exact match
    if needle in _flask_routes_cache:
        return "promote", f"Route {subject} exists in Flask code — add to start.md"

    # Prefix match (route may have <param> suffix in app.py)
    base = needle.split("<")[0].rstrip("/")
    for r in _flask_routes_cache:
        if r == base or r.startswith(base + "/") or r.startswith(base + "<"):
            return "promote", f"Route {subject} matched by Flask route {r!r} — add to start.md"

    return "dismiss", (
        f"Route {subject} has no Flask @route decorator in dashboard — "
        "only referenced in seed plans/docs (not yet built)"
    )


# ---------------------------------------------------------------------------
# Verifier: orphan_db_table
# ---------------------------------------------------------------------------

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+(\w+)",
    re.IGNORECASE,
)


def _migration_has_create(table: str) -> bool:
    """Return True if any migration file defines CREATE TABLE <table>."""
    if not _MIGRATIONS_DIR.exists():
        return False
    for f in _MIGRATIONS_DIR.rglob("*"):
        if f.suffix not in {".sql", ".py"}:
            continue
        try:
            src = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _CREATE_TABLE_RE.finditer(src):
            if m.group(1).lower() == table.lower():
                return True
    return False


def _count_code_refs(table: str) -> int:
    """Count how many lines in tools/ Python files reference <table>."""
    pattern = re.compile(r"\b" + re.escape(table) + r"\b", re.IGNORECASE)
    count = 0
    for py_file in _TOOLS_DIR.rglob("*.py"):
        if any(p in py_file.parts for p in {".git", ".tmp", "__pycache__"}):
            continue
        try:
            src = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        count += len(pattern.findall(src))
    return count


def _verify_orphan_db_table(table: str) -> Tuple[str, str]:
    """Verify an orphan table gap.

    If a CREATE TABLE migration already exists → dismiss (gap_detector missed it).
    If no migration and code refs >= _ORPHAN_MIN_REFS → promote (real gap).
    If no migration and 0 refs → dismiss (dead reference, no active code path).
    """
    if _migration_has_create(table):
        return "dismiss", f"CREATE TABLE {table} found in migrations — gap_detector false positive"

    refs = _count_code_refs(table)
    if refs >= _ORPHAN_MIN_REFS:
        return "promote", f"No CREATE TABLE migration; {refs} code reference(s) — write migration"

    return "dismiss", "No CREATE TABLE migration and 0 code references — dead reference, dismiss"


# ---------------------------------------------------------------------------
# No-lens heuristics
# ---------------------------------------------------------------------------


def _verify_no_lens(task: Dict[str, Any]) -> Tuple[str, str]:
    """Heuristic triage for tasks that the Oracle created without a gap lens.

    These come from the self_debug reflex (Oracle RCA), V&V scripts, or
    feature-request sessions.
    """
    task_id: str = task.get("id", "")
    title: str = task.get("title", "")
    title_lower = title.lower()

    # Oracle RCA cards: self_debug reflex creates these for recurring failures
    if title.startswith("Oracle RCA:") or task_id.startswith("diag-"):
        return "promote", "Self-debug reflex RCA card — recurring scheduler failure needs fix"

    # V&V cards: mandatory post-build verification tasks
    if "v&v" in title_lower or "playwright v&v" in title_lower:
        return "promote", "V&V card — mandatory verification gate, promote to run"

    # Feature requests: real value but need human scoping before queuing
    if title.startswith("[FR]"):
        return "backlog", "Feature request — real value, promote to backlog for scoping"

    return "skip", "No heuristic matched — leaving for human judgment"


# ---------------------------------------------------------------------------
# Batch card helper
# ---------------------------------------------------------------------------

_BATCH_SUBJECT_RE = re.compile(r"^\s+-\s+(\S+)", re.MULTILINE)


def _parse_batch_subjects(description: str) -> List[str]:
    """Extract the subjects list from a [Batch] card description."""
    subjects: List[str] = []
    in_subjects = False
    for line in description.splitlines():
        stripped = line.strip()
        if stripped == "Subjects:":
            in_subjects = True
            continue
        if in_subjects:
            if stripped.startswith("- "):
                subjects.append(stripped[2:].strip())
            elif stripped and not stripped.startswith("-"):
                break  # end of subjects block
    return subjects


def _verify_batch(task: Dict[str, Any]) -> Tuple[str, str]:
    """For [Batch] cards, derive the lens from the title and apply
    per-subject verification.  Majority rules: any promote → promote.
    """
    title: str = task.get("title", "")
    desc: str = task.get("description", "") or ""

    # Detect lens from title: "[Batch] <lens>: N gap findings"
    lens_match = re.match(r"\[Batch\]\s+(\w+):", title)
    if not lens_match:
        return "skip", "Batch card with unrecognised lens format"
    lens = lens_match.group(1)

    subjects = _parse_batch_subjects(desc)
    if not subjects:
        return "skip", "Batch card — could not parse subjects from description"

    promotions = 0
    dismissals = 0
    reasons: List[str] = []
    for subj in subjects:
        if lens == "tool_not_in_manifest":
            action, reason = _verify_tool_not_in_manifest(subj)
        elif lens == "route_not_listed":
            action, reason = _verify_route_not_listed(subj)
        elif lens == "orphan_db_table":
            action, reason = _verify_orphan_db_table(subj)
        else:
            action, reason = "skip", f"Unknown lens {lens!r}"

        reasons.append(f"{subj}: {action} — {reason}")
        if action == "promote":
            promotions += 1
        elif action == "dismiss":
            dismissals += 1

    summary = f"{promotions} promote / {dismissals} dismiss / {len(subjects)-promotions-dismissals} skip"
    detail = "; ".join(reasons[:3]) + (f" ... (+{len(reasons)-3} more)" if len(reasons) > 3 else "")

    if promotions > 0:
        return "promote", f"Batch: {summary}. {detail}"
    if dismissals == len(subjects):
        return "dismiss", f"Batch: all {dismissals} subjects are false positives. {detail}"
    return "skip", f"Batch: {summary}. {detail}"


# ---------------------------------------------------------------------------
# Core triage engine
# ---------------------------------------------------------------------------


def _triage_one(task: Dict[str, Any]) -> Tuple[str, str]:
    """Return (action, reason) for a single suggested task.

    action ∈ {'promote', 'dismiss', 'backlog', 'skip'}
    """
    title: str = task.get("title", "")
    lens: Optional[str] = task.get("oracle_lens")

    # [Batch] cards have a compound title; treat them specially
    if title.startswith("[Batch]"):
        return _verify_batch(task)

    if lens == "tool_not_in_manifest":
        subject = re.sub(r"^tool_not_in_manifest gap:\s*", "", title).strip()
        return _verify_tool_not_in_manifest(subject)

    if lens == "route_not_listed":
        subject = re.sub(r"^route_not_listed gap:\s*", "", title).strip()
        return _verify_route_not_listed(subject)

    if lens == "orphan_db_table":
        subject = re.sub(r"^orphan_db_table gap:\s*", "", title).strip()
        return _verify_orphan_db_table(subject)

    if lens is None:
        return _verify_no_lens(task)

    # Unknown lens — skip and let human decide
    return "skip", f"Unrecognised oracle_lens={lens!r} — no verifier registered"


# ---------------------------------------------------------------------------
# Notification helper
# ---------------------------------------------------------------------------


def _notify(summary: Dict[str, Any], api_base: str, dry_run: bool) -> None:
    """Write a notification row to the notifications table via the dashboard API."""
    if dry_run:
        return
    counts = summary.get("counts", {})
    msg = (
        f"Oracle Triage: {counts.get('promoted', 0)} promoted, "
        f"{counts.get('dismissed', 0)} dismissed, "
        f"{counts.get('skipped', 0)} skipped "
        f"(dry_run={dry_run})"
    )
    try:
        payload = json.dumps({
            "source": "genesis.oracle_triage",
            "title": "Oracle Triage complete",
            "message": msg,
            "severity": "info",
        }).encode()
        req = urllib.request.Request(
            f"{api_base}/api/notifications",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass  # notification failure is non-fatal


# ---------------------------------------------------------------------------
# Main triage loop
# ---------------------------------------------------------------------------


def triage(
    api_base: str = _DEFAULT_API_BASE,
    dry_run: bool = False,
    verbose: bool = False,
) -> Dict[str, Any]:
    """Run the triage loop over all suggested tasks.

    Returns a summary dict with counts and per-task decisions.
    """
    global _flask_routes_cache
    _flask_routes_cache = None  # reset cache each run

    tasks = _fetch_suggested_tasks()
    if not tasks:
        return {"success": True, "counts": {"total": 0}, "decisions": []}

    decisions: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {"total": len(tasks), "promoted": 0, "dismissed": 0, "skipped": 0, "errors": 0}

    for task in tasks:
        task_id = task["id"]
        title = task["title"]
        action, reason = _triage_one(task)

        if verbose:
            LOG.info("[%s] %s → %s (%s)", task_id, title[:60], action, reason[:80])

        entry: Dict[str, Any] = {
            "id": task_id,
            "title": title,
            "lens": task.get("oracle_lens"),
            "action": action,
            "reason": reason,
            "api_success": None,
            "api_message": None,
        }

        if action == "promote":
            ok, msg = _api_move(task_id, "backlog", api_base, dry_run)
            entry["api_success"] = ok
            entry["api_message"] = msg
            if ok:
                counts["promoted"] += 1
            else:
                counts["errors"] += 1

        elif action == "dismiss":
            bypass_reason = f"oracle_triage_reflex: {reason[:200]}"
            ok, msg = _api_move(task_id, "done", api_base, dry_run, bypass_reason=bypass_reason)
            entry["api_success"] = ok
            entry["api_message"] = msg
            if ok:
                counts["dismissed"] += 1
            else:
                counts["errors"] += 1

        elif action == "backlog":
            # FR tasks: promote to backlog (human scoping needed)
            ok, msg = _api_move(task_id, "backlog", api_base, dry_run)
            entry["api_success"] = ok
            entry["api_message"] = msg
            if ok:
                counts["promoted"] += 1  # count as promoted for summary
            else:
                counts["errors"] += 1

        else:  # skip
            counts["skipped"] += 1
            entry["api_success"] = True
            entry["api_message"] = "no action taken"

        decisions.append(entry)

    summary: Dict[str, Any] = {
        "success": counts["errors"] == 0,
        "dry_run": dry_run,
        "counts": counts,
        "decisions": decisions,
    }
    return summary


# ---------------------------------------------------------------------------
# Genesis daemon entry point
# ---------------------------------------------------------------------------


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:  # noqa: ARG001
    """Genesis reflex entry point — called by daemon every 3 h."""
    api_base = config.get("api_base", _DEFAULT_API_BASE)
    dry_run = bool(config.get("dry_run", False))
    verbose = bool(config.get("verbose", False))

    LOG.info("[oracle_triage] starting triage cycle")
    result = triage(api_base=api_base, dry_run=dry_run, verbose=verbose)
    counts = result.get("counts", {})
    LOG.info(
        "[oracle_triage] done — promoted=%d dismissed=%d skipped=%d errors=%d",
        counts.get("promoted", 0),
        counts.get("dismissed", 0),
        counts.get("skipped", 0),
        counts.get("errors", 0),
    )
    _notify(result, api_base, dry_run)
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Oracle Triage Reflex — auto-triage suggested kanban tasks")
    p.add_argument("--run", action="store_true", help="Execute the triage loop")
    p.add_argument("--dry-run", action="store_true", help="Analyse but do not move any tasks")
    p.add_argument("--json", action="store_true", help="Output result as JSON")
    p.add_argument("--verbose", action="store_true", help="Log each decision")
    p.add_argument("--api-base", default=_DEFAULT_API_BASE, help=f"Dashboard API base URL (default: {_DEFAULT_API_BASE})")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _build_parser().parse_args(argv)

    if not args.run:
        print("Use --run to execute the triage loop.  Add --dry-run to preview.")
        return 0

    result = triage(
        api_base=args.api_base,
        dry_run=args.dry_run,
        verbose=args.verbose or not args.json,
    )

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        counts = result.get("counts", {})
        print(f"\nOracle Triage {'[DRY RUN] ' if args.dry_run else ''}complete")
        print(f"  Promoted:  {counts.get('promoted', 0)}")
        print(f"  Dismissed: {counts.get('dismissed', 0)}")
        print(f"  Skipped:   {counts.get('skipped', 0)}")
        print(f"  Errors:    {counts.get('errors', 0)}")
        if not args.json:
            for d in result.get("decisions", []):
                if d["action"] != "skip":
                    status = "OK" if d.get("api_success") else "FAIL"
                    print(f"  [{status}] {d['action']:8} {d['id']:30} {d['reason'][:70]}")

    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
