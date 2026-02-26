#!/usr/bin/env python3
# CUI // SP-CTI
"""Runtime Feedback Collector — test-to-source correlation for ICDEV.

Phase 52 (D332, D334). Parses pytest JUnit XML output and correlates test
results back to source functions via naming convention. Stores append-only
feedback in runtime_feedback table. Computes per-function health scores
by joining code quality metrics with test results.

Usage:
    python tools/analysis/runtime_feedback.py --xml .tmp/results.xml --project-id proj-123 --json
    python tools/analysis/runtime_feedback.py --health --function analyze_code --json
"""

import argparse
import json
import re
import sqlite3
import sys
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    p = db_path or DB_PATH
    if not p.exists():
        raise FileNotFoundError(f"Database not found: {p}")
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _uid() -> str:
    return f"rf-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# RuntimeFeedbackCollector
# ---------------------------------------------------------------------------

class RuntimeFeedbackCollector:
    """Parse test results and correlate with source functions (D334)."""

    def __init__(
        self,
        project_id: Optional[str] = None,
        db_path: Optional[Path] = None,
    ):
        self.project_id = project_id
        self.db_path = db_path or DB_PATH

    # ---- JUnit XML parsing (D7 — xml.etree.ElementTree, air-gap safe) ----

    def parse_pytest_xml(self, xml_path: Path) -> List[Dict[str, Any]]:
        """Parse JUnit XML produced by pytest --junitxml=path."""
        results: List[Dict[str, Any]] = []
        try:
            tree = ET.parse(str(xml_path))
        except (ET.ParseError, FileNotFoundError):
            return results

        root = tree.getroot()
        # Handle both <testsuites><testsuite>... and <testsuite>... formats
        testsuites = root.findall(".//testsuite")
        if root.tag == "testsuite":
            testsuites = [root]

        for suite in testsuites:
            for tc in suite.findall("testcase"):
                classname = tc.get("classname", "")
                name = tc.get("name", "")
                time_s = float(tc.get("time", "0"))

                # Determine test file from classname
                test_file = classname.replace(".", "/") + ".py" if classname else ""
                # Take the last dotted segment as the test module
                parts = classname.split(".")
                if parts:
                    test_file = parts[0] + ".py"
                    if len(parts) > 1:
                        test_file = "/".join(parts[:-1]).replace(".", "/")
                        if not test_file.endswith(".py"):
                            test_file += ".py"

                # Check for failure/error
                failure = tc.find("failure")
                error = tc.find("error")
                passed = failure is None and error is None

                error_type = None
                error_message = None
                if failure is not None:
                    error_type = failure.get("type", "AssertionError")
                    error_message = (failure.get("message", "") or "")[:500]
                elif error is not None:
                    error_type = error.get("type", "Error")
                    error_message = (error.get("message", "") or "")[:500]

                # Map test to source
                source_file, source_function = self._map_test_to_source(
                    name, test_file
                )

                results.append({
                    "test_file": test_file,
                    "test_function": name,
                    "test_passed": passed,
                    "test_duration_ms": round(time_s * 1000, 2),
                    "error_type": error_type,
                    "error_message": error_message,
                    "source_file": source_file or "",
                    "source_function": source_function,
                })
        return results

    # ---- Stdout fallback parser ----

    _RESULT_RE = re.compile(
        r"(PASSED|FAILED|ERROR)\s+([\w/\\.-]+)::(\w+)(?:::(\w+))?"
    )

    def parse_pytest_stdout(self, stdout_text: str) -> List[Dict[str, Any]]:
        """Parse pytest -v output as fallback when no JUnit XML available."""
        results: List[Dict[str, Any]] = []
        for m in self._RESULT_RE.finditer(stdout_text):
            status = m.group(1)
            test_file = m.group(2)
            # group 3 may be class, group 4 may be function, or group 3 is function
            test_fn = m.group(4) or m.group(3)

            source_file, source_fn = self._map_test_to_source(test_fn, test_file)
            results.append({
                "test_file": test_file,
                "test_function": test_fn,
                "test_passed": status == "PASSED",
                "test_duration_ms": 0.0,
                "error_type": None if status == "PASSED" else status,
                "error_message": None,
                "source_file": source_file or "",
                "source_function": source_fn,
            })
        return results

    # ---- Test-to-source mapping (D334 — naming convention, advisory) ----

    @staticmethod
    def _map_test_to_source(
        test_function: str, test_file: str,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Map test function/file to source function/file via convention."""
        source_fn = None
        source_file = None

        # Strip test_ prefix from function name
        if test_function and test_function.startswith("test_"):
            source_fn = test_function[5:]

        # Map test file to source file
        if test_file:
            # tests/test_foo.py -> tools/.../foo.py (approximate)
            basename = Path(test_file).stem
            if basename.startswith("test_"):
                source_basename = basename[5:]
                source_file = source_basename + ".py"

        return source_file, source_fn

    # ---- DB storage (append-only, D332) ----

    def store_feedback(
        self,
        feedback_list: List[Dict[str, Any]],
        run_id: Optional[str] = None,
        db_path: Optional[Path] = None,
    ) -> int:
        """Bulk INSERT into runtime_feedback. Returns row count."""
        conn = _get_db(db_path or self.db_path)
        rid = run_id or f"run-{uuid.uuid4().hex[:12]}"
        count = 0
        try:
            for fb in feedback_list:
                conn.execute(
                    """INSERT INTO runtime_feedback
                    (id, project_id, source_file, source_function,
                     test_file, test_function, test_passed,
                     test_duration_ms, error_type, error_message,
                     coverage_pct, run_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        _uid(), self.project_id,
                        fb.get("source_file", ""),
                        fb.get("source_function"),
                        fb.get("test_file", ""),
                        fb.get("test_function", ""),
                        1 if fb.get("test_passed") else 0,
                        fb.get("test_duration_ms", 0.0),
                        fb.get("error_type"),
                        fb.get("error_message"),
                        fb.get("coverage_pct"),
                        rid,
                    ),
                )
                count += 1
            conn.commit()
        finally:
            conn.close()
        return count

    # ---- Collect pipeline ----

    def collect_from_xml(
        self,
        xml_path: Path,
        run_id: Optional[str] = None,
        db_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Parse XML + store feedback. Returns summary."""
        feedback = self.parse_pytest_xml(xml_path)
        stored = 0
        if feedback:
            try:
                stored = self.store_feedback(feedback, run_id, db_path)
            except FileNotFoundError:
                pass  # DB not available — return parsed data without storing
        total = len(feedback)
        passed = sum(1 for f in feedback if f.get("test_passed"))
        return {
            "xml_path": str(xml_path),
            "total_tests": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": round(passed / max(total, 1), 4),
            "stored_rows": stored,
            "run_id": run_id,
        }

    # ---- Per-function health score ----

    def compute_function_health(
        self,
        source_function: str,
        project_id: Optional[str] = None,
        db_path: Optional[Path] = None,
    ) -> Optional[Dict[str, Any]]:
        """Join code_quality_metrics + runtime_feedback for a function."""
        try:
            conn = _get_db(db_path or self.db_path)
        except FileNotFoundError:
            return None

        pid = project_id or self.project_id
        try:
            # Latest code quality for this function
            cq = conn.execute(
                """SELECT cyclomatic_complexity, cognitive_complexity,
                          nesting_depth, smell_count, maintainability_score
                   FROM code_quality_metrics
                   WHERE function_name = ?1
                     AND (?2 IS NULL OR project_id = ?2)
                   ORDER BY created_at DESC LIMIT 1""",
                (source_function, pid),
            ).fetchone()

            # Runtime feedback stats (last 30 days)
            rf = conn.execute(
                """SELECT COUNT(*) as total,
                          SUM(CASE WHEN test_passed = 1 THEN 1 ELSE 0 END) as passed,
                          AVG(test_duration_ms) as avg_duration
                   FROM runtime_feedback
                   WHERE source_function = ?1
                     AND (?2 IS NULL OR project_id = ?2)
                     AND created_at > datetime('now', '-30 days')""",
                (source_function, pid),
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        finally:
            conn.close()

        if not cq and (not rf or rf["total"] == 0):
            return None

        cc = cq["cyclomatic_complexity"] if cq else 0
        maint = cq["maintainability_score"] if cq else 1.0
        total_tests = rf["total"] if rf else 0
        passed_tests = rf["passed"] if rf else 0
        pass_rate = passed_tests / max(total_tests, 1)

        # Health = weighted combination of code quality + test reliability
        complexity_factor = max(0.0, 1.0 - cc / 25.0)
        health = round(
            0.40 * complexity_factor + 0.35 * pass_rate + 0.25 * maint, 4
        )

        return {
            "function_name": source_function,
            "cyclomatic_complexity": cc,
            "maintainability_score": maint,
            "test_total": total_tests,
            "test_passed": passed_tests,
            "test_pass_rate": round(pass_rate, 4),
            "avg_test_duration_ms": round(rf["avg_duration"] or 0, 2) if rf else 0,
            "health_score": health,
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Runtime Feedback Collector — test-to-source correlation (Phase 52)"
    )
    parser.add_argument("--xml", help="Path to JUnit XML file")
    parser.add_argument("--stdout", help="Raw pytest -v output text")
    parser.add_argument("--project-id", help="ICDEV project ID")
    parser.add_argument("--run-id", help="Test run identifier")
    parser.add_argument("--db-path", help="Override DB path")
    parser.add_argument("--health", action="store_true",
                        help="Compute health for a function")
    parser.add_argument("--function", help="Function name (for --health)")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="JSON output")
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else None
    collector = RuntimeFeedbackCollector(
        project_id=args.project_id,
        db_path=db_path,
    )

    if args.health and args.function:
        result = collector.compute_function_health(
            args.function, args.project_id, db_path
        )
        if result:
            print(json.dumps(result, indent=2))
        else:
            print(json.dumps({"error": "No data found", "function": args.function}))
        return

    if args.xml:
        result = collector.collect_from_xml(Path(args.xml), args.run_id, db_path)
    elif args.stdout:
        feedback = collector.parse_pytest_stdout(args.stdout)
        stored = 0
        try:
            stored = collector.store_feedback(feedback, args.run_id, db_path)
        except FileNotFoundError:
            pass
        total = len(feedback)
        passed = sum(1 for f in feedback if f.get("test_passed"))
        result = {
            "source": "stdout",
            "total_tests": total,
            "passed": passed,
            "failed": total - passed,
            "stored_rows": stored,
        }
    else:
        parser.print_help()
        return

    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
