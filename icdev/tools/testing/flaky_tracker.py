# CUI // SP-CTI
"""Flaky test tracker — detects intermittently-failing tests from JUnit XML history.

Reads pytest --junitxml output from .tmp/test_results/ and data/qa_runs/,
persists pass/fail history in data/flaky_history.db, and files kanban bug
tasks for tests whose fail rate exceeds the configured threshold.

Usage:
    python tools/testing/flaky_tracker.py [--threshold 0.15] [--min-runs 5] [--json] [--dry-run]

Genesis reflex integration: call run(config, state) directly.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.testing.flaky_tracker")

HISTORY_DB = BASE_DIR / "data" / "flaky_history.db"

# XML result directories to scan
_RESULT_DIRS = [
    BASE_DIR / ".tmp" / "test_results",
    BASE_DIR / "data" / "qa_runs",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS test_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    test_key    TEXT NOT NULL,
    file        TEXT,
    outcome     TEXT NOT NULL,
    run_file    TEXT,
    recorded_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS flaky_tasks_filed (
    test_key TEXT PRIMARY KEY,
    task_id  TEXT,
    filed_at TEXT
);
"""


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _open_history_db() -> sqlite3.Connection:
    HISTORY_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(HISTORY_DB))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _already_filed(hconn: sqlite3.Connection, test_key: str) -> bool:
    row = hconn.execute(
        "SELECT task_id FROM flaky_tasks_filed WHERE test_key = ?", (test_key,)
    ).fetchone()
    return row is not None


def _mark_filed(hconn: sqlite3.Connection, test_key: str, task_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    hconn.execute(
        "INSERT OR REPLACE INTO flaky_tasks_filed (test_key, task_id, filed_at) VALUES (?, ?, ?)",
        (test_key, task_id, now),
    )
    hconn.commit()


# ---------------------------------------------------------------------------
# XML ingestion
# ---------------------------------------------------------------------------

def _parse_xml(xml_path: Path) -> List[Dict[str, str]]:
    """Parse a JUnit XML file; return list of {test_key, file, outcome} dicts."""
    records = []
    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()
        # Handle both <testsuite> root and <testsuites> wrapping
        suites = root.findall("testsuite") if root.tag == "testsuites" else [root]
        for suite in suites:
            for tc in suite.findall("testcase"):
                classname = tc.get("classname", "")
                name = tc.get("name", "")
                file_ = tc.get("file", "")
                test_key = f"{classname}::{name}" if classname else name
                if not test_key:
                    continue
                failed = tc.find("failure") is not None or tc.find("error") is not None
                skipped = tc.find("skipped") is not None
                if skipped:
                    continue
                records.append({
                    "test_key": test_key,
                    "file": file_,
                    "outcome": "fail" if failed else "pass",
                    "run_file": str(xml_path),
                })
    except Exception as exc:
        logger.warning("flaky_tracker: failed to parse %s: %s", xml_path, exc)
    return records


def _collect_xml_files() -> List[Path]:
    files: List[Path] = []
    for d in _RESULT_DIRS:
        if d.exists():
            files.extend(sorted(d.glob("*.xml")))
    return files


def _ingest(hconn: sqlite3.Connection) -> int:
    """Ingest all XML result files not yet recorded. Returns count of new rows."""
    already_ingested: set = {
        row[0]
        for row in hconn.execute("SELECT DISTINCT run_file FROM test_runs").fetchall()
    }
    total = 0
    for xml_path in _collect_xml_files():
        if str(xml_path) in already_ingested:
            continue
        records = _parse_xml(xml_path)
        for r in records:
            hconn.execute(
                "INSERT INTO test_runs (test_key, file, outcome, run_file) VALUES (?, ?, ?, ?)",
                (r["test_key"], r["file"], r["outcome"], r["run_file"]),
            )
        total += len(records)
    hconn.commit()
    return total


# ---------------------------------------------------------------------------
# Flaky detection
# ---------------------------------------------------------------------------

def _detect_flaky(
    hconn: sqlite3.Connection,
    threshold: float = 0.15,
    min_runs: int = 5,
    window: int = 30,
) -> List[Dict[str, Any]]:
    """Return tests with fail_rate >= threshold across their last `window` runs."""
    rows = hconn.execute(
        """
        SELECT test_key, file,
               SUM(CASE WHEN outcome='fail' THEN 1 ELSE 0 END) AS fails,
               SUM(CASE WHEN outcome='pass' THEN 1 ELSE 0 END) AS passes,
               COUNT(*) AS total
        FROM (
            SELECT test_key, file, outcome
            FROM test_runs
            ORDER BY id DESC
            LIMIT -1 OFFSET 0
        )
        GROUP BY test_key
        HAVING fails >= 1 AND passes >= 1 AND total >= ?
        """,
        (min_runs,),
    ).fetchall()

    flaky = []
    for row in rows:
        d = dict(row)
        fail_rate = d["fails"] / d["total"]
        if fail_rate >= threshold:
            d["fail_rate"] = round(fail_rate, 4)
            flaky.append(d)

    flaky.sort(key=lambda x: x["fail_rate"], reverse=True)
    return flaky


# ---------------------------------------------------------------------------
# Kanban task filing
# ---------------------------------------------------------------------------

def _file_kanban_task(test_key: str, file_: str, fail_rate: float, total: int) -> Optional[str]:
    """Insert a kanban bug task for a flaky test. Returns task_id or None."""
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
    except Exception as exc:
        logger.warning("flaky_tracker: kanban DB unavailable, skipping task filing: %s", exc)
        return None

    task_id = f"task-flaky-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()
    title = f"[FLAKY] {test_key} (fail_rate={fail_rate:.0%})"
    desc = (
        f"Flaky test detected by `flaky_tracker.py`.\n\n"
        f"**Test:** `{test_key}`\n"
        f"**File:** `{file_ or 'unknown'}`\n"
        f"**Fail rate:** {fail_rate:.1%} over last {total} runs\n\n"
        "**Remediation options:**\n"
        "1. Add `@pytest.mark.flaky(reruns=3)` (requires `pytest-rerunfailures`) as a short-term quarantine.\n"
        "2. Investigate test teardown — shared state or timing issues are the most common cause.\n"
        "3. If the test relies on external I/O (DB, HTTP), add proper mocking or a retry assertion.\n"
        "4. Close this task once the test passes 10+ consecutive runs without failure.\n"
    )
    try:
        conn.execute(
            """
            INSERT INTO kanban_tasks
                (id, title, description, task_type, priority, status,
                 scheduled_at, created_at, updated_at, dispatch_source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (task_id, title, desc, "bug", "high", "backlog", now, now, now, "flaky_tracker"),
        )
        conn.commit()
        logger.info("flaky_tracker: filed kanban task %s for %s", task_id, test_key)
        return task_id
    except Exception as exc:
        logger.warning("flaky_tracker: failed to insert kanban task: %s", exc)
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def detect_and_file(
    threshold: float = 0.15,
    min_runs: int = 5,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Ingest XML results, detect flaky tests, file kanban tasks for new ones."""
    result: Dict[str, Any] = {
        "total_ingested": 0,
        "flaky": [],
        "filed_tasks": [],
    }

    try:
        hconn = _open_history_db()
    except Exception as exc:
        logger.error("flaky_tracker: cannot open history DB: %s", exc)
        result["error"] = str(exc)
        return result

    try:
        result["total_ingested"] = _ingest(hconn)
        flaky_tests = _detect_flaky(hconn, threshold=threshold, min_runs=min_runs)

        for ft in flaky_tests:
            entry = {
                "test_key": ft["test_key"],
                "file": ft.get("file", ""),
                "fail_rate": ft["fail_rate"],
                "total_runs": ft["total"],
                "fails": ft["fails"],
                "passes": ft["passes"],
            }
            result["flaky"].append(entry)

            if dry_run:
                continue
            if _already_filed(hconn, ft["test_key"]):
                continue
            task_id = _file_kanban_task(
                test_key=ft["test_key"],
                file_=ft.get("file", ""),
                fail_rate=ft["fail_rate"],
                total=ft["total"],
            )
            if task_id:
                _mark_filed(hconn, ft["test_key"], task_id)
                result["filed_tasks"].append({"test_key": ft["test_key"], "task_id": task_id})

    except Exception as exc:
        logger.exception("flaky_tracker: unexpected error: %s", exc)
        result["error"] = str(exc)
    finally:
        try:
            hconn.close()
        except Exception:
            pass

    return result


def run(config: Dict[str, Any], state: Any) -> Dict[str, Any]:
    """Genesis reflex entry point."""
    threshold = float(config.get("threshold", 0.15))
    min_runs = int(config.get("min_runs", 5))
    dry_run = bool(config.get("dry_run", False))
    return detect_and_file(threshold=threshold, min_runs=min_runs, dry_run=dry_run)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="ICDEV Flaky Test Tracker")
    parser.add_argument("--threshold", type=float, default=0.15,
                        help="Fail-rate threshold to flag a test as flaky (default: 0.15)")
    parser.add_argument("--min-runs", type=int, default=5,
                        help="Minimum recorded runs before a test is evaluated (default: 5)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Detect flaky tests without filing kanban tasks")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Output results as JSON")
    args = parser.parse_args()

    result = detect_and_file(
        threshold=args.threshold,
        min_runs=args.min_runs,
        dry_run=args.dry_run,
    )

    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Ingested {result['total_ingested']} new test results")
        if result["flaky"]:
            print(f"\nFlaky tests ({len(result['flaky'])}):")
            for ft in result["flaky"]:
                print(f"  {ft['fail_rate']:.0%} fail rate | {ft['test_key']} ({ft['file'] or 'no file'})")
        else:
            print("No flaky tests detected.")
        if result["filed_tasks"]:
            print(f"\nKanban tasks filed: {len(result['filed_tasks'])}")
            for t in result["filed_tasks"]:
                print(f"  {t['task_id']} — {t['test_key']}")
        if result.get("error"):
            print(f"\nError: {result['error']}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
