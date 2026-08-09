#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Outcome Verifier — tracks auto-resolution PR merge status and failure recurrence (D-EVO-6).

Dual-check verification:
    1. PR merge check: poll GitHub (gh) / GitLab (glab) CLI for PR state
    2. Recurrence check: after merge, watch for same pattern_signature within 7 days

Confidence update:
    - PR merged + no recurrence in 7 days → +0.1 to pattern confidence
    - PR closed without merge OR recurrence detected → -0.2 to pattern confidence

Architecture:
    - Append-only outcome_verification_log (NIST AU, D6)
    - Reads from auto_resolution_log (pr_url, resolution_status)
    - Calls pattern_detector.update_pattern_confidence() for feedback
    - Config from args/evolution_config.yaml outcome_verification section
    - Graceful degradation when gh/glab CLI unavailable

Usage:
    python tools/monitor/outcome_verifier.py --check-pending --json
    python tools/monitor/outcome_verifier.py --check-recurrence --json
    python tools/monitor/outcome_verifier.py --run-all --json
    python tools/monitor/outcome_verifier.py --status --json
"""

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# ---------------------------------------------------------------------------
# Config (from args/evolution_config.yaml)
# ---------------------------------------------------------------------------
_DEFAULT_PR_POLL_INTERVAL_MIN = 30
_DEFAULT_PR_MAX_POLL_DAYS = 7
_DEFAULT_RECURRENCE_WINDOW_DAYS = 7
_DEFAULT_CONFIDENCE_DELTA_SUCCESS = 0.1
_DEFAULT_CONFIDENCE_DELTA_FAILURE = -0.2


def _load_config() -> dict:
    """Load outcome_verification config from evolution_config.yaml."""
    config_path = BASE_DIR / "args" / "evolution_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("outcome_verification", {})
    except Exception:
        return {}


_cfg = _load_config()
PR_MAX_POLL_DAYS = _cfg.get("pr_max_poll_days", _DEFAULT_PR_MAX_POLL_DAYS)
RECURRENCE_WINDOW_DAYS = _cfg.get("recurrence_window_days", _DEFAULT_RECURRENCE_WINDOW_DAYS)
CONFIDENCE_DELTA_SUCCESS = _cfg.get("confidence_delta_success", _DEFAULT_CONFIDENCE_DELTA_SUCCESS)
CONFIDENCE_DELTA_FAILURE = _cfg.get("confidence_delta_failure", _DEFAULT_CONFIDENCE_DELTA_FAILURE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(prefix: str = "ov") -> str:
    import uuid

    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _get_conn(db_path: Optional[Path] = None) -> sqlite3.Connection:
    conn = get_connection(db_path=str(db_path))
    return conn


def _ensure_table(db_path: Optional[Path] = None) -> None:
    """Create outcome_verification_log table if missing."""
    conn = _get_conn(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS outcome_verification_log (
                id TEXT PRIMARY KEY,
                resolution_id TEXT NOT NULL,
                pr_url TEXT,
                pattern_signature TEXT,
                verification_type TEXT NOT NULL
                    CHECK(verification_type IN ('pr_merge_check', 'recurrence_check')),
                result TEXT NOT NULL DEFAULT 'pending'
                    CHECK(result IN ('pending', 'merged', 'closed', 'recurred',
                                     'resolved', 'timeout', 'cli_unavailable')),
                checked_at TEXT,
                confidence_delta REAL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ov_resolution
                ON outcome_verification_log(resolution_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ov_result
                ON outcome_verification_log(result)
        """)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# PR Merge Checking
# ---------------------------------------------------------------------------
def _detect_vcs_cli() -> Optional[str]:
    """Detect whether gh (GitHub) or glab (GitLab) CLI is available."""
    for cli in ("gh", "glab"):
        try:
            subprocess.run([cli, "--version"], capture_output=True, timeout=10, stdin=subprocess.DEVNULL)
            return cli
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def _check_pr_status(pr_url: str, cli: str) -> Optional[str]:
    """Check PR/MR merge status via CLI.

    Returns: 'merged', 'closed', 'open', or None on error.
    """
    try:
        if cli == "gh":
            # gh pr view <url> --json state
            result = subprocess.run(
                [cli, "pr", "view", pr_url, "--json", "state"],
                capture_output=True,
                text=True,
                timeout=30,
                stdin=subprocess.DEVNULL,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                state = data.get("state", "").upper()
                if state == "MERGED":
                    return "merged"
                elif state == "CLOSED":
                    return "closed"
                elif state == "OPEN":
                    return "open"
        elif cli == "glab":
            # glab mr view <url> --json state (gitlab)
            result = subprocess.run(
                [cli, "mr", "view", pr_url, "--output", "json"],
                capture_output=True,
                text=True,
                timeout=30,
                stdin=subprocess.DEVNULL,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                state = data.get("state", "").lower()
                if state == "merged":
                    return "merged"
                elif state == "closed":
                    return "closed"
                elif state in ("opened", "open"):
                    return "open"
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        pass
    return None


def check_pending_prs(db_path: Optional[Path] = None) -> dict:
    """Check merge status of all auto-resolution PRs that are pending verification.

    Finds auto_resolution_log entries with pr_url and resolution_status='pr_created'
    that don't yet have a terminal outcome_verification_log entry.

    Returns:
        Dict with checked/merged/closed/open/error counts and details.
    """
    _ensure_table(db_path)
    cli = _detect_vcs_cli()

    conn = _get_conn(db_path)
    try:
        # Find PRs needing verification
        cutoff = (datetime.now(timezone.utc) - timedelta(days=PR_MAX_POLL_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")

        rows = conn.execute(
            """
            SELECT ar.id, ar.pr_url, ar.confidence, ar.created_at
            FROM auto_resolution_log ar
            WHERE ar.pr_url IS NOT NULL
              AND ar.resolution_status = 'pr_created'
              AND ar.created_at >= %s
              AND ar.id NOT IN (
                  SELECT resolution_id FROM outcome_verification_log
                  WHERE result IN ('merged', 'closed', 'timeout')
              )
            ORDER BY ar.created_at ASC
            LIMIT 20
        """,
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()

    results = {
        "checked": 0,
        "merged": 0,
        "closed": 0,
        "open": 0,
        "timeout": 0,
        "cli_unavailable": 0,
        "details": [],
    }

    if not cli:
        # No VCS CLI available — record all as cli_unavailable
        for row in rows:
            r = dict(row)
            ov_id = _generate_id("ov")
            conn = _get_conn(db_path)
            try:
                conn.execute(
                    """
                    INSERT INTO outcome_verification_log
                        (id, resolution_id, pr_url, verification_type, result,
                         checked_at, created_at)
                    VALUES (%s, %s, %s, 'pr_merge_check', 'cli_unavailable', %s, %s)
                """,
                    (ov_id, r["id"], r["pr_url"], _now(), _now()),
                )
                conn.commit()
            finally:
                conn.close()
            results["cli_unavailable"] += 1
            results["details"].append({"resolution_id": r["id"], "result": "cli_unavailable"})
        return results

    for row in rows:
        r = dict(row)
        pr_state = _check_pr_status(r["pr_url"], cli)
        results["checked"] += 1

        # Check if PR is past max poll window
        created = r.get("created_at", "")
        is_expired = False
        if created:
            try:
                created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                age_days = (datetime.now(timezone.utc) - created_dt).days
                is_expired = age_days > PR_MAX_POLL_DAYS
            except (ValueError, TypeError):
                pass

        if pr_state == "merged":
            ov_result = "merged"
            results["merged"] += 1
            # Schedule recurrence check
            _schedule_recurrence_check(r["id"], r["pr_url"], db_path)
        elif pr_state == "closed":
            ov_result = "closed"
            results["closed"] += 1
            # Closed without merge — apply negative confidence delta
            _apply_confidence_delta(r["id"], CONFIDENCE_DELTA_FAILURE, db_path)
        elif is_expired:
            ov_result = "timeout"
            results["timeout"] += 1
        else:
            ov_result = "pending"
            results["open"] += 1
            continue  # Don't record pending — will check again later

        # Record result
        ov_id = _generate_id("ov")
        conn = _get_conn(db_path)
        try:
            conn.execute(
                """
                INSERT INTO outcome_verification_log
                    (id, resolution_id, pr_url, verification_type, result,
                     checked_at, created_at)
                VALUES (%s, %s, %s, 'pr_merge_check', %s, %s, %s)
            """,
                (ov_id, r["id"], r["pr_url"], ov_result, _now(), _now()),
            )
            conn.commit()
        finally:
            conn.close()

        results["details"].append(
            {
                "resolution_id": r["id"],
                "pr_url": r["pr_url"],
                "result": ov_result,
            }
        )

    return results


def _schedule_recurrence_check(resolution_id: str, pr_url: str, db_path: Optional[Path] = None) -> None:
    """Record that a merged PR needs recurrence monitoring."""
    ov_id = _generate_id("ov")
    conn = _get_conn(db_path)
    try:
        conn.execute(
            """
            INSERT INTO outcome_verification_log
                (id, resolution_id, pr_url, verification_type, result,
                 checked_at, created_at)
            VALUES (%s, %s, %s, 'recurrence_check', 'pending', NULL, %s)
        """,
            (ov_id, resolution_id, pr_url, _now()),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Recurrence Checking
# ---------------------------------------------------------------------------
def check_recurrence(db_path: Optional[Path] = None) -> dict:
    """Check if auto-resolved failures have recurred after PR merge.

    Looks at outcome_verification_log entries with verification_type='recurrence_check'
    and result='pending'. For each, checks if the same error pattern has appeared
    in auto_resolution_log within RECURRENCE_WINDOW_DAYS.

    Returns:
        Dict with checked/recurred/resolved counts and details.
    """
    _ensure_table(db_path)
    conn = _get_conn(db_path)

    results = {
        "checked": 0,
        "recurred": 0,
        "resolved": 0,
        "pending": 0,
        "details": [],
    }

    try:
        # Find pending recurrence checks
        rows = conn.execute("""
            SELECT ov.id, ov.resolution_id, ov.pr_url, ov.created_at
            FROM outcome_verification_log ov
            WHERE ov.verification_type = 'recurrence_check'
              AND ov.result = 'pending'
            ORDER BY ov.created_at ASC
            LIMIT 20
        """).fetchall()
    finally:
        conn.close()

    for row in rows:
        r = dict(row)
        res_id = r["resolution_id"]

        # Get the original resolution's alert_type for pattern matching
        conn = _get_conn(db_path)
        try:
            orig = conn.execute(
                "SELECT alert_type, created_at FROM auto_resolution_log WHERE id = %s", (res_id,)
            ).fetchone()
        finally:
            conn.close()

        if not orig:
            continue

        orig_data = dict(orig)
        merge_created = r.get("created_at", "")

        # Check if enough time has passed for recurrence window
        elapsed_days = 0
        if merge_created:
            try:
                merge_dt = datetime.fromisoformat(merge_created.replace("Z", "+00:00"))
                elapsed_days = (datetime.now(timezone.utc) - merge_dt).days
            except (ValueError, TypeError):
                pass

        if elapsed_days < RECURRENCE_WINDOW_DAYS:
            results["pending"] += 1
            continue

        results["checked"] += 1

        # Check if same alert_type appeared in auto_resolution_log after merge
        conn = _get_conn(db_path)
        try:
            recurrence = conn.execute(
                """
                SELECT COUNT(*) as cnt
                FROM auto_resolution_log
                WHERE alert_type = %s
                  AND created_at > %s
                  AND id != %s
            """,
                (orig_data["alert_type"], merge_created, res_id),
            ).fetchone()
        finally:
            conn.close()

        recurrence_count = dict(recurrence)["cnt"] if recurrence else 0
        recurred = recurrence_count > 0

        if recurred:
            ov_result = "recurred"
            results["recurred"] += 1
            delta = CONFIDENCE_DELTA_FAILURE
        else:
            ov_result = "resolved"
            results["resolved"] += 1
            delta = CONFIDENCE_DELTA_SUCCESS

        # Update the verification record
        conn = _get_conn(db_path)
        try:
            conn.execute(
                """
                UPDATE outcome_verification_log
                SET result = %s, checked_at = %s, confidence_delta = %s
                WHERE id = %s
            """,
                (ov_result, _now(), delta, r["id"]),
            )
            conn.commit()
        finally:
            conn.close()

        # Apply confidence delta to the original pattern
        _apply_confidence_delta(res_id, delta, db_path)

        results["details"].append(
            {
                "resolution_id": res_id,
                "result": ov_result,
                "recurrence_count": recurrence_count,
                "confidence_delta": delta,
            }
        )

    return results


# ---------------------------------------------------------------------------
# Confidence Feedback
# ---------------------------------------------------------------------------
def _apply_confidence_delta(resolution_id: str, delta: float, db_path: Optional[Path] = None) -> None:
    """Apply confidence adjustment to the pattern that matched the original alert."""
    conn = _get_conn(db_path)
    try:
        # Get the matched pattern from the resolution details
        row = conn.execute("SELECT details FROM auto_resolution_log WHERE id = %s", (resolution_id,)).fetchone()
        if not row:
            return

        details_str = dict(row).get("details", "")
        if not details_str:
            return

        try:
            details = json.loads(details_str)
        except (json.JSONDecodeError, TypeError):
            return

        # Extract pattern_id from nested analysis
        pattern_id = None
        if isinstance(details, dict):
            analysis = details.get("analysis", details)
            matched = analysis.get("matched_pattern", {})
            pattern_id = matched.get("id") or matched.get("pattern_id")

        if not pattern_id:
            return

        # Update pattern confidence via existing mechanism
        try:
            from tools.knowledge.pattern_detector import update_pattern_confidence

            outcome = "success" if delta > 0 else "failure"
            update_pattern_confidence(pattern_id, outcome, db_path)
        except ImportError:
            pass

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
def get_status(db_path: Optional[Path] = None) -> dict:
    """Get outcome verification status summary."""
    _ensure_table(db_path)
    conn = _get_conn(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) as cnt FROM outcome_verification_log").fetchone()
        by_result = conn.execute("""
            SELECT result, COUNT(*) as cnt
            FROM outcome_verification_log
            GROUP BY result
        """).fetchall()
        by_type = conn.execute("""
            SELECT verification_type, COUNT(*) as cnt
            FROM outcome_verification_log
            GROUP BY verification_type
        """).fetchall()

        return {
            "total": dict(total)["cnt"] if total else 0,
            "by_result": {dict(r)["result"]: dict(r)["cnt"] for r in by_result},
            "by_type": {dict(r)["verification_type"]: dict(r)["cnt"] for r in by_type},
            "cli_available": _detect_vcs_cli(),
            "config": {
                "pr_max_poll_days": PR_MAX_POLL_DAYS,
                "recurrence_window_days": RECURRENCE_WINDOW_DAYS,
                "confidence_delta_success": CONFIDENCE_DELTA_SUCCESS,
                "confidence_delta_failure": CONFIDENCE_DELTA_FAILURE,
            },
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Outcome Verifier — track auto-resolution PR outcomes (D-EVO-6)")
    parser.add_argument("--check-pending", action="store_true", help="Check merge status of pending PRs")
    parser.add_argument(
        "--check-recurrence", action="store_true", help="Check for failure recurrence after merged fixes"
    )
    parser.add_argument("--run-all", action="store_true", help="Run both PR merge check and recurrence check")
    parser.add_argument("--status", action="store_true", help="Show verification status summary")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    parser.add_argument("--db-path", help="Database path override")
    args = parser.parse_args()

    db = Path(args.db_path) if args.db_path else None

    if args.status:
        result = get_status(db)
    elif args.check_pending:
        result = check_pending_prs(db)
    elif args.check_recurrence:
        result = check_recurrence(db)
    elif args.run_all:
        pr_result = check_pending_prs(db)
        recurrence_result = check_recurrence(db)
        result = {
            "pr_check": pr_result,
            "recurrence_check": recurrence_result,
        }
    else:
        parser.print_help()
        return

    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
