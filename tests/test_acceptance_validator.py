# CUI // SP-CTI
"""Tests for the ICDEV Acceptance Criteria Validator (V&V gate)."""

import json
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread

import pytest

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.testing.acceptance_validator import (
    parse_acceptance_criteria,
    map_criteria_to_evidence,
    check_page,
    validate_acceptance,
    DOM_ERROR_PATTERNS,
)


# --- Fixtures ---


@pytest.fixture
def plan_with_criteria(tmp_path):
    """Create a plan file with acceptance criteria."""
    plan = tmp_path / "plan.md"
    plan.write_text(
        "# Feature: Dashboard Kanban\n\n"
        "## Solution Statement\nReplace stat cards with kanban board.\n\n"
        "## Acceptance Criteria\n"
        "- Dashboard displays a kanban board with 4 columns\n"
        "- All unit tests pass with zero failures\n"
        "- E2E browser tests verify the dashboard renders correctly\n"
        "- No security vulnerabilities detected by SAST scan\n\n"
        "## Notes\nSome additional notes.\n",
        encoding="utf-8",
    )
    return str(plan)


@pytest.fixture
def plan_without_criteria(tmp_path):
    """Create a plan file missing ## Acceptance Criteria section."""
    plan = tmp_path / "plan_no_ac.md"
    plan.write_text(
        "# Feature: Something\n\n"
        "## Solution Statement\nDo something.\n\n"
        "## Notes\nNo acceptance criteria here.\n",
        encoding="utf-8",
    )
    return str(plan)


@pytest.fixture
def plan_with_numbered_criteria(tmp_path):
    """Create a plan file with numbered acceptance criteria."""
    plan = tmp_path / "plan_numbered.md"
    plan.write_text(
        "# Feature: API\n\n"
        "## Acceptance Criteria\n"
        "1. API returns 200 for valid requests\n"
        "2. API returns 400 for invalid input\n"
        "3. All BDD scenarios pass\n\n"
        "## Testing Strategy\n",
        encoding="utf-8",
    )
    return str(plan)


@pytest.fixture
def plan_with_checkboxes(tmp_path):
    """Create a plan file with checkbox acceptance criteria."""
    plan = tmp_path / "plan_checkbox.md"
    plan.write_text(
        "# Feature: Auth\n\n"
        "## Acceptance Criteria\n"
        "- [x] Login page renders without errors\n"
        "- [ ] Session management works correctly\n"
        "- [X] Compliance gate passes\n\n",
        encoding="utf-8",
    )
    return str(plan)


@pytest.fixture
def test_state_passing(tmp_path):
    """Create a test state file with passing results."""
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps({
            "run_id": "abc12345",
            "unit_passed": 14,
            "unit_failed": 0,
            "bdd_passed": 3,
            "bdd_failed": 0,
            "e2e_passed": 2,
            "e2e_failed": 0,
            "security_gate_passed": True,
            "compliance_gate_passed": True,
        }),
        encoding="utf-8",
    )
    return str(state)


@pytest.fixture
def test_state_failing(tmp_path):
    """Create a test state file with some failures."""
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps({
            "run_id": "def67890",
            "unit_passed": 10,
            "unit_failed": 4,
            "bdd_passed": 0,
            "bdd_failed": 0,
            "e2e_passed": 0,
            "e2e_failed": 0,
            "security_gate_passed": False,
            "compliance_gate_passed": None,
        }),
        encoding="utf-8",
    )
    return str(state)


class _OkHandler(BaseHTTPRequestHandler):
    """Test HTTP handler returning clean HTML."""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body><h1>Dashboard</h1><p>All good</p></body></html>")

    def log_message(self, *args):
        pass  # Suppress logging


class _ErrorHandler(BaseHTTPRequestHandler):
    """Test HTTP handler returning error content."""

    def do_GET(self):
        if self.path == "/500":
            self.send_response(500)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>Internal Server Error</body></html>")
        elif self.path == "/traceback":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body>Traceback (most recent call last):\n"
                b"  File 'app.py', line 42\n"
                b"jinja2.exceptions.TemplateNotFound: missing.html</body></html>"
            )
        elif self.path == "/js-error":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><script>ReferenceError: ICDEV is not defined</script></body></html>"
            )
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>OK</body></html>")

    def log_message(self, *args):
        pass


@pytest.fixture
def ok_server():
    """Start a test HTTP server returning clean pages."""
    server = HTTPServer(("127.0.0.1", 0), _OkHandler)
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture
def error_server():
    """Start a test HTTP server returning error pages."""
    server = HTTPServer(("127.0.0.1", 0), _ErrorHandler)
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


# --- Tests: parse_acceptance_criteria ---


class TestParseAcceptanceCriteria:
    def test_parse_bullet_points(self, plan_with_criteria):
        criteria = parse_acceptance_criteria(plan_with_criteria)
        assert len(criteria) == 4
        assert "Dashboard displays a kanban board with 4 columns" in criteria[0]
        assert "unit tests pass" in criteria[1]

    def test_parse_numbered_list(self, plan_with_numbered_criteria):
        criteria = parse_acceptance_criteria(plan_with_numbered_criteria)
        assert len(criteria) == 3
        assert "API returns 200" in criteria[0]
        assert "BDD scenarios pass" in criteria[2]

    def test_parse_checkboxes(self, plan_with_checkboxes):
        criteria = parse_acceptance_criteria(plan_with_checkboxes)
        assert len(criteria) == 3
        assert "Login page renders without errors" in criteria[0]

    def test_no_criteria_section(self, plan_without_criteria):
        criteria = parse_acceptance_criteria(plan_without_criteria)
        assert criteria == []

    def test_nonexistent_file(self):
        criteria = parse_acceptance_criteria("/nonexistent/path/plan.md")
        assert criteria == []


# --- Tests: map_criteria_to_evidence ---


class TestMapCriteriaToEvidence:
    def test_maps_unit_test_criteria(self, test_state_passing):
        state = json.loads(Path(test_state_passing).read_text())
        criteria = ["All unit tests pass with zero failures"]
        results = map_criteria_to_evidence(criteria, state)
        assert len(results) == 1
        assert results[0].status == "verified"
        assert results[0].evidence_type == "unit_test"

    def test_maps_e2e_criteria(self, test_state_passing):
        state = json.loads(Path(test_state_passing).read_text())
        criteria = ["E2E browser tests verify the dashboard renders"]
        results = map_criteria_to_evidence(criteria, state)
        assert results[0].status == "verified"
        assert results[0].evidence_type == "e2e_test"

    def test_maps_security_criteria(self, test_state_passing):
        state = json.loads(Path(test_state_passing).read_text())
        criteria = ["No security vulnerabilities detected by SAST scan"]
        results = map_criteria_to_evidence(criteria, state)
        assert results[0].status == "verified"

    def test_unmatched_criteria_stays_unverified(self, test_state_passing):
        state = json.loads(Path(test_state_passing).read_text())
        criteria = ["Configuration file supports YAML format"]
        results = map_criteria_to_evidence(criteria, state)
        assert results[0].status == "unverified"

    def test_no_state_all_unverified(self):
        criteria = ["Some criterion", "Another criterion"]
        results = map_criteria_to_evidence(criteria, None)
        assert all(r.status == "unverified" for r in results)


# --- Tests: check_page ---


class TestCheckPage:
    def test_clean_page_passes(self, ok_server):
        result = check_page(ok_server, "/")
        assert result.status_code == 200
        assert not result.has_errors
        assert result.content_length > 0
        assert result.error_patterns_found == []

    def test_500_error_detected(self, error_server):
        result = check_page(error_server, "/500")
        assert result.has_errors
        assert result.status_code == 500

    def test_traceback_in_html_detected(self, error_server):
        result = check_page(error_server, "/traceback")
        assert result.has_errors
        assert any("Traceback" in p for p in result.error_patterns_found)

    def test_js_error_detected(self, error_server):
        result = check_page(error_server, "/js-error")
        assert result.has_errors
        assert any("ReferenceError" in p for p in result.error_patterns_found)

    def test_connection_error(self):
        result = check_page("http://127.0.0.1:1", "/nonexistent")
        assert result.has_errors
        assert any("Connection error" in p or "Unexpected error" in p for p in result.error_patterns_found)


# --- Tests: validate_acceptance (full integration) ---


class TestValidateAcceptance:
    def test_full_pass_with_evidence(self, plan_with_criteria, test_state_passing, ok_server):
        report = validate_acceptance(
            plan_path=plan_with_criteria,
            test_results_path=test_state_passing,
            base_url=ok_server,
            pages=["/", "/dashboard"],
        )
        assert report.criteria_count == 4
        assert report.criteria_failed == 0
        assert report.pages_with_errors == 0
        assert report.overall_pass is True

    def test_no_criteria_section_fails_gate(self, plan_without_criteria):
        report = validate_acceptance(plan_path=plan_without_criteria)
        assert report.criteria_count == 0
        assert report.overall_pass is False

    def test_error_page_fails_gate(self, plan_with_criteria, test_state_passing, error_server):
        report = validate_acceptance(
            plan_path=plan_with_criteria,
            test_results_path=test_state_passing,
            base_url=error_server,
            pages=["/ok", "/500"],
        )
        assert report.pages_with_errors >= 1
        assert report.overall_pass is False

    def test_traceback_page_fails_gate(self, plan_with_criteria, error_server):
        report = validate_acceptance(
            plan_path=plan_with_criteria,
            base_url=error_server,
            pages=["/traceback"],
        )
        assert report.pages_with_errors == 1
        assert report.overall_pass is False

    def test_no_plan_no_pages_passes(self):
        report = validate_acceptance()
        assert report.overall_pass is True
        assert report.criteria_count == 0
        assert report.pages_checked == 0

    def test_only_pages_no_plan(self, ok_server):
        report = validate_acceptance(base_url=ok_server, pages=["/"])
        assert report.overall_pass is True
        assert report.pages_checked == 1
        assert report.pages_with_errors == 0

    def test_report_has_timestamp(self, plan_with_criteria):
        report = validate_acceptance(plan_path=plan_with_criteria)
        assert report.timestamp != ""
        assert "T" in report.timestamp  # ISO format


# --- Tests: DOM_ERROR_PATTERNS ---


class TestDomErrorPatterns:
    def test_patterns_are_nonempty(self):
        assert len(DOM_ERROR_PATTERNS) >= 10

    def test_key_patterns_included(self):
        patterns_lower = [p.lower() for p in DOM_ERROR_PATTERNS]
        assert "internal server error" in patterns_lower
        assert "templatenotfound" in patterns_lower
        assert "referenceerror" in patterns_lower
