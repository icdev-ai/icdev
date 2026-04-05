#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for GSD-adapted features: stub detection, context pressure, deviation rules.

Tests cover 3 new modules:
  1. tools/testing/stub_detector.py — 4-level verification
  2. tools/agent/context_pressure.py — context pressure & stuck detection
  3. tools/knowledge/deviation_rules.py — category-based deviation rules
"""

import json
import sqlite3

import pytest


# ── Fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary database with required tables."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS stub_detection_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            project_dir TEXT,
            files_checked INTEGER DEFAULT 0,
            files_passed INTEGER DEFAULT 0,
            files_failed INTEGER DEFAULT 0,
            stub_total INTEGER DEFAULT 0,
            level_summary TEXT,
            failures TEXT,
            overall_passed INTEGER DEFAULT 1,
            classification TEXT DEFAULT 'CUI',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS context_pressure_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            pressure_level TEXT NOT NULL,
            estimated_tokens_used INTEGER DEFAULT 0,
            estimated_remaining_pct REAL DEFAULT 100.0,
            tool_call_count INTEGER DEFAULT 0,
            recommendation TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS hook_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            hook_type TEXT,
            tool_name TEXT,
            payload TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS deviation_rule_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            confidence REAL NOT NULL,
            original_decision TEXT NOT NULL,
            final_decision TEXT NOT NULL,
            category_overrode INTEGER DEFAULT 0,
            matched_keywords TEXT,
            reason TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.close()
    return db_path


@pytest.fixture
def stub_python_file(tmp_path):
    """Create a Python file with stub functions."""
    f = tmp_path / "stub_example.py"
    f.write_text(
        "# CUI // SP-CTI\n"
        "def implemented_function():\n"
        '    return {"status": "ok"}\n\n'
        "def stub_function():\n"
        "    pass\n\n"
        "def another_stub():\n"
        "    raise NotImplementedError\n\n"
        "def placeholder():\n"
        "    # TODO: implement this\n"
        "    return None\n",
        encoding="utf-8",
    )
    return f


@pytest.fixture
def real_python_file(tmp_path):
    """Create a real Python file with actual implementation."""
    f = tmp_path / "real_example.py"
    f.write_text(
        "# CUI // SP-CTI\n"
        "import json\n"
        "from pathlib import Path\n\n"
        "def process_data(data):\n"
        "    result = []\n"
        "    for item in data:\n"
        '        if item.get("active"):\n'
        '            result.append(item["name"])\n'
        "    return result\n\n"
        "def save_to_file(data, path):\n"
        "    path = Path(path)\n"
        '    path.write_text(json.dumps(data), encoding="utf-8")\n'
        "    return True\n",
        encoding="utf-8",
    )
    return f


@pytest.fixture
def empty_file(tmp_path):
    """Create an empty file."""
    f = tmp_path / "empty.py"
    f.write_text("", encoding="utf-8")
    return f


# ── Stub Detector Tests ────────────────────────────────────────────────────
class TestStubDetectorExists:
    """Level 1: EXISTS verification."""

    def test_existing_file_passes(self, real_python_file):
        from tools.testing.stub_detector import check_exists

        result = check_exists(real_python_file)
        assert result["passed"] is True
        assert result["level"] == "exists"

    def test_nonexistent_file_fails(self, tmp_path):
        from tools.testing.stub_detector import check_exists

        result = check_exists(tmp_path / "nonexistent.py")
        assert result["passed"] is False

    def test_empty_file_fails(self, tmp_path):
        from tools.testing.stub_detector import check_exists

        f = tmp_path / "empty.py"
        f.write_text("", encoding="utf-8")
        # Empty file is 0 bytes
        result = check_exists(f)
        assert result["passed"] is False


class TestStubDetectorSubstantive:
    """Level 2: SUBSTANTIVE verification."""

    def test_real_code_passes(self, real_python_file):
        from tools.testing.stub_detector import check_substantive

        result = check_substantive(real_python_file)
        assert result["passed"] is True
        assert result["stub_count"] == 0

    def test_stub_code_detects_stubs(self, stub_python_file):
        from tools.testing.stub_detector import check_substantive

        result = check_substantive(stub_python_file)
        assert result["stub_count"] > 0
        # Should detect pass-only, NotImplementedError, and TODO
        stub_patterns = [s["pattern"] for s in result["stubs_found"]]
        assert any("pass" in p.lower() for p in stub_patterns)

    def test_high_stub_ratio_fails(self, tmp_path):
        from tools.testing.stub_detector import check_substantive

        f = tmp_path / "all_stubs.py"
        f.write_text(
            "def a(): pass\ndef b(): pass\ndef c(): pass\n",
            encoding="utf-8",
        )
        result = check_substantive(f)
        assert result["passed"] is False
        assert result["stub_ratio"] > 0.5

    def test_unsupported_file_type(self, tmp_path):
        from tools.testing.stub_detector import check_substantive

        f = tmp_path / "data.csv"
        f.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
        result = check_substantive(f)
        assert result["passed"] is True  # Unsupported types pass by default


class TestStubDetectorWired:
    """Level 3: WIRED verification."""

    def test_file_with_imports_passes(self, real_python_file, tmp_path):
        from tools.testing.stub_detector import check_wired

        result = check_wired(real_python_file, tmp_path)
        assert result["passed"] is True
        assert result["connections"]["has_imports"] is True

    def test_file_without_imports_fails(self, tmp_path):
        from tools.testing.stub_detector import check_wired

        f = tmp_path / "no_imports.py"
        f.write_text(
            "def standalone():\n    return 42\n",
            encoding="utf-8",
        )
        result = check_wired(f, tmp_path)
        assert result["passed"] is False

    def test_test_file_passes_without_imports(self, tmp_path):
        from tools.testing.stub_detector import check_wired

        f = tmp_path / "test_something.py"
        f.write_text(
            "def test_basic():\n    assert True\n",
            encoding="utf-8",
        )
        result = check_wired(f, tmp_path)
        # Test files are exempt from import requirement
        assert result["passed"] is True


class TestStubDetectorFunctional:
    """Level 4: FUNCTIONAL verification."""

    def test_valid_python_passes(self, real_python_file):
        from tools.testing.stub_detector import check_functional

        result = check_functional(real_python_file)
        assert result["passed"] is True

    def test_syntax_error_fails(self, tmp_path):
        from tools.testing.stub_detector import check_functional

        f = tmp_path / "broken.py"
        f.write_text("def broken(\n    return\n", encoding="utf-8")
        result = check_functional(f)
        assert result["passed"] is False

    def test_non_python_deferred(self, tmp_path):
        from tools.testing.stub_detector import check_functional

        f = tmp_path / "code.js"
        f.write_text('console.log("hello");', encoding="utf-8")
        result = check_functional(f)
        assert result["passed"] is True  # Deferred for non-Python


class TestStubDetectorPipeline:
    """Full 4-level pipeline tests."""

    def test_full_pass(self, real_python_file):
        from tools.testing.stub_detector import verify_file

        result = verify_file(real_python_file, real_python_file.parent)
        assert result["overall_passed"] is True
        assert result["highest_level_passed"] == "functional"

    def test_cascade_failure(self, tmp_path):
        from tools.testing.stub_detector import verify_file

        # File doesn't exist — should fail at EXISTS level
        result = verify_file(tmp_path / "nope.py")
        assert result["overall_passed"] is False
        assert result["failed_at_level"] == "exists"
        # Should NOT have checked higher levels
        assert "substantive" not in result["levels"]

    def test_directory_scan(self, tmp_path, real_python_file, stub_python_file):
        from tools.testing.stub_detector import verify_directory

        results = verify_directory(tmp_path, max_level="substantive")
        assert results["files_checked"] >= 2

    def test_gate_evaluation_pass(self, real_python_file):
        from tools.testing.stub_detector import verify_directory, evaluate_gate

        results = verify_directory(real_python_file.parent, max_level="substantive")
        gate = evaluate_gate(results)
        assert gate["passed"] is True

    def test_store_results(self, tmp_path, tmp_db, real_python_file):
        from tools.testing.stub_detector import verify_directory, store_results

        results = verify_directory(tmp_path, max_level="exists")
        rid = store_results(results, "test-proj", tmp_db)
        assert rid > 0

        conn = sqlite3.connect(str(tmp_db))
        row = conn.execute("SELECT * FROM stub_detection_results WHERE id = ?", (rid,)).fetchone()
        conn.close()
        assert row is not None


# ── Context Pressure Tests ──────────────────────────────────────────────────
class TestContextPressure:
    """Context pressure monitoring tests."""

    def test_estimate_normal(self, tmp_db):
        from tools.agent.context_pressure import estimate_context_usage

        result = estimate_context_usage("test-session", tmp_db)
        assert result["pressure_level"] == "normal"
        assert result["estimated_remaining_pct"] > 0

    def test_pressure_levels(self):
        from tools.agent.context_pressure import DEFAULT_CONFIG

        assert DEFAULT_CONFIG["warning_threshold_pct"] == 35
        assert DEFAULT_CONFIG["critical_threshold_pct"] == 25

    def test_health_check(self, tmp_db):
        from tools.agent.context_pressure import health_check

        result = health_check("test-session", tmp_db)
        assert result["overall_healthy"] is True
        assert "context_pressure" in result
        assert "stuck_detection" in result


class TestStuckDetection:
    """Stuck detection guard tests."""

    def test_no_events_not_stuck(self, tmp_db):
        from tools.agent.context_pressure import detect_stuck

        result = detect_stuck("test-session", tmp_db)
        assert result["is_stuck"] is False

    def test_consecutive_reads_detected(self, tmp_db):
        from tools.agent.context_pressure import detect_stuck

        # Insert many read-only tool events
        conn = sqlite3.connect(str(tmp_db))
        for i in range(10):
            conn.execute(
                """INSERT INTO hook_events
                   (session_id, hook_type, tool_name, payload, created_at)
                   VALUES (?, 'pre_tool_use', 'Read', '{}', datetime('now'))""",
                ("stuck-session",),
            )
        conn.commit()
        conn.close()

        result = detect_stuck("stuck-session", tmp_db)
        assert result["is_stuck"] is True
        assert result["stuck_type"] == "analysis_paralysis"
        assert result["consecutive_reads"] >= 5

    def test_write_resets_reads(self, tmp_db):
        from tools.agent.context_pressure import detect_stuck

        conn = sqlite3.connect(str(tmp_db))
        # Insert: Read, Write, Read, Read — only 2 reads after write
        # Query is DESC order, so most recent first: Read, Read, Write, Read
        # Walking from newest: Read(1), Read(2), then hits Write → break
        # consecutive_reads = 2, below threshold of 5
        for i, tool in enumerate(["Read", "Write", "Read", "Read"]):
            conn.execute(
                """INSERT INTO hook_events
                   (id, session_id, hook_type, tool_name, payload, created_at)
                   VALUES (?, ?, 'pre_tool_use', ?, ?, datetime('now'))""",
                (1000 + i, "not-stuck-session", tool, json.dumps({"idx": i})),
            )
        conn.commit()
        conn.close()

        result = detect_stuck("not-stuck-session", tmp_db)
        assert result["is_stuck"] is False
        assert result["consecutive_reads"] <= 4

    def test_duplicate_loop_detected(self, tmp_db):
        from tools.agent.context_pressure import detect_stuck

        conn = sqlite3.connect(str(tmp_db))
        # Insert Write first (so analysis_paralysis doesn't fire),
        # then interleave Grep (same) and Edit to avoid consecutive reads
        conn.execute(
            """INSERT INTO hook_events
               (id, session_id, hook_type, tool_name, payload, created_at)
               VALUES (?, ?, 'pre_tool_use', 'Write', '{"f":"a.py"}', datetime('now'))""",
            (2000, "loop-session"),
        )
        # Insert 5 Grep calls with interleaved Writes (avoid analysis paralysis)
        for i in range(5):
            conn.execute(
                """INSERT INTO hook_events
                   (id, session_id, hook_type, tool_name, payload, created_at)
                   VALUES (?, ?, 'pre_tool_use', 'Grep', '{"pattern":"same"}', datetime('now'))""",
                (2001 + i * 2, "loop-session"),
            )
            if i < 4:
                conn.execute(
                    """INSERT INTO hook_events
                       (id, session_id, hook_type, tool_name, payload, created_at)
                       VALUES (?, ?, 'pre_tool_use', 'Edit', ?, datetime('now'))""",
                    (2002 + i * 2, "loop-session", json.dumps({"f": f"file{i}"})),
                )
        conn.commit()
        conn.close()

        result = detect_stuck("loop-session", tmp_db)
        # Should detect the repeating Grep pattern
        assert result["duplicate_calls"] >= 3


# ── Deviation Rules Tests ───────────────────────────────────────────────────
class TestDeviationRules:
    """Category-based deviation rules tests."""

    def test_classify_security_vulnerability(self):
        from tools.knowledge.deviation_rules import classify_failure

        result = classify_failure(
            {
                "error_type": "sast_finding",
                "error_message": "SQL injection detected in query builder",
            }
        )
        assert result["has_override"] is True
        assert result["category"] == "security_vulnerability"
        assert result["decision_override"] == "auto_heal"

    def test_classify_blocking_issue(self):
        from tools.knowledge.deviation_rules import classify_failure

        result = classify_failure(
            {
                "error_type": "import_error",
                "error_message": "Module not found: requests",
            }
        )
        assert result["has_override"] is True
        assert result["category"] == "blocking_issue"

    def test_classify_architectural_change(self):
        from tools.knowledge.deviation_rules import classify_failure

        result = classify_failure(
            {
                "error_type": "design_decision",
                "error_message": "Schema change required for new table",
            }
        )
        assert result["has_override"] is True
        assert result["category"] == "architectural_change"
        assert result["decision_override"] == "escalate"

    def test_classify_compliance(self):
        from tools.knowledge.deviation_rules import classify_failure

        result = classify_failure(
            {
                "error_type": "compliance_check",
                "error_message": "CUI marking missing from generated artifact",
            }
        )
        assert result["has_override"] is True
        assert result["category"] == "compliance_violation"
        assert result["decision_override"] == "escalate"

    def test_classify_no_match(self):
        from tools.knowledge.deviation_rules import classify_failure

        result = classify_failure(
            {
                "error_type": "unknown_thing",
                "error_message": "Something completely unrelated happened",
            }
        )
        assert result["has_override"] is False
        assert result["category"] is None

    def test_apply_security_auto_heal(self, tmp_db):
        from tools.knowledge.deviation_rules import apply_deviation_rules

        result = apply_deviation_rules(
            failure_data={
                "error_type": "sast_finding",
                "error_message": "SQL injection in query",
            },
            confidence=0.55,  # Below normal 0.7 threshold
            auto_healable=True,
            db_path=tmp_db,
        )
        # Category rule should override: security allows 0.5 threshold
        assert result["final_decision"] == "auto_heal"
        assert result["category_overrode"] is True

    def test_apply_architectural_always_escalates(self, tmp_db):
        from tools.knowledge.deviation_rules import apply_deviation_rules

        result = apply_deviation_rules(
            failure_data={
                "error_type": "design_decision",
                "error_message": "Breaking change to API contract",
            },
            confidence=0.95,  # Even high confidence
            auto_healable=True,
            db_path=tmp_db,
        )
        # Architectural changes ALWAYS escalate
        assert result["final_decision"] == "escalate"
        assert result["category_overrode"] is True

    def test_apply_no_match_uses_confidence(self, tmp_db):
        from tools.knowledge.deviation_rules import apply_deviation_rules

        result = apply_deviation_rules(
            failure_data={
                "error_type": "misc",
                "error_message": "Something random",
            },
            confidence=0.8,
            auto_healable=True,
            db_path=tmp_db,
        )
        # No category match — uses normal confidence
        assert result["final_decision"] == "auto_heal"
        assert result["category_overrode"] is False

    def test_apply_low_confidence_security(self, tmp_db):
        from tools.knowledge.deviation_rules import apply_deviation_rules

        result = apply_deviation_rules(
            failure_data={
                "error_type": "sast_finding",
                "error_message": "Possible XSS in template",
            },
            confidence=0.3,  # Below even the lowered 0.5 threshold
            auto_healable=True,
            db_path=tmp_db,
        )
        # Even security has limits
        assert result["final_decision"] == "suggest"

    def test_list_categories(self):
        from tools.knowledge.deviation_rules import load_config

        categories = load_config()
        assert "security_vulnerability" in categories
        assert "architectural_change" in categories
        assert "compliance_violation" in categories
        assert len(categories) >= 5

    def test_stats_empty(self, tmp_db):
        from tools.knowledge.deviation_rules import get_stats

        result = get_stats(tmp_db)
        assert result["total_events"] == 0


# ── CLI Integration Tests ───────────────────────────────────────────────────
class TestCLIIntegration:
    """Test that CLI entry points work."""

    def test_stub_detector_file(self, real_python_file):
        import subprocess

        result = subprocess.run(
            ["python", "tools/testing/stub_detector.py", "--file", str(real_python_file), "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["overall_passed"] is True

    def test_context_pressure_health(self):
        import subprocess

        result = subprocess.run(
            ["python", "tools/agent/context_pressure.py", "--check", "health", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "overall_healthy" in data

    def test_deviation_rules_classify(self):
        import subprocess

        failure = json.dumps({"error_message": "SQL injection found"})
        result = subprocess.run(
            ["python", "tools/knowledge/deviation_rules.py", "--classify", failure],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["category"] == "security_vulnerability"

    def test_deviation_rules_list(self):
        import subprocess

        result = subprocess.run(
            ["python", "tools/knowledge/deviation_rules.py", "--list-categories", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "security_vulnerability" in data
