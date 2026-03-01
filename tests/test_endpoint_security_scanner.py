#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for endpoint security scanner — detection of missing auth and validation.

Covers: route detection, auth pattern recognition, validation pattern recognition,
exempt routes, test file exclusion, context window scanning, gate evaluation,
multi-language support, CLI flags, directory scanning.
"""

import json
import sys
import subprocess
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from tools.security.endpoint_security_scanner import EndpointSecurityScanner


@pytest.fixture
def scanner():
    return EndpointSecurityScanner()


# ---------------------------------------------------------------------------
# Python route detection
# ---------------------------------------------------------------------------

class TestPythonRouteDetection:
    def test_flask_route_detected(self, scanner):
        code = '''
@app.route("/users")
def list_users():
    return jsonify(users)
'''
        result = scanner.scan_content(code, "python")
        assert result["routes_found"] >= 1

    def test_blueprint_route_detected(self, scanner):
        code = '''
@bp.route("/items", methods=["GET"])
def get_items():
    return jsonify([])
'''
        result = scanner.scan_content(code, "python")
        assert result["routes_found"] >= 1

    def test_get_post_put_delete_detected(self, scanner):
        code = '''
@api.get("/a")
def a(): pass

@api.post("/b")
def b(): pass

@api.put("/c")
def c(): pass

@api.delete("/d")
def d(): pass
'''
        result = scanner.scan_content(code, "python")
        assert result["routes_found"] == 4


# ---------------------------------------------------------------------------
# Missing auth detection
# ---------------------------------------------------------------------------

class TestMissingAuth:
    def test_route_without_auth_is_critical(self, scanner):
        code = '''
@app.route("/users")
def list_users():
    return jsonify(users)
'''
        result = scanner.scan_content(code, "python")
        assert result["critical"] >= 1
        assert any(
            f["name"] == "api_route_without_auth_decorator"
            for f in result["findings"]
        )

    def test_route_with_require_role_is_clean(self, scanner):
        code = '''
@app.route("/users")
@require_role("admin")
def list_users():
    return jsonify(users)
'''
        result = scanner.scan_content(code, "python")
        auth_findings = [
            f for f in result["findings"]
            if f["name"] == "api_route_without_auth_decorator"
        ]
        assert len(auth_findings) == 0

    def test_route_with_login_required_is_clean(self, scanner):
        code = '''
@login_required
@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")
'''
        result = scanner.scan_content(code, "python")
        auth_findings = [
            f for f in result["findings"]
            if f["name"] == "api_route_without_auth_decorator"
        ]
        assert len(auth_findings) == 0

    def test_route_with_g_current_user_is_clean(self, scanner):
        code = '''
@app.route("/profile")
def profile():
    user = g.current_user
    return jsonify(user)
'''
        result = scanner.scan_content(code, "python")
        auth_findings = [
            f for f in result["findings"]
            if f["name"] == "api_route_without_auth_decorator"
        ]
        assert len(auth_findings) == 0

    def test_route_with_require_auth_is_clean(self, scanner):
        code = '''
@app.route("/data")
@require_auth
def get_data():
    return jsonify(data)
'''
        result = scanner.scan_content(code, "python")
        auth_findings = [
            f for f in result["findings"]
            if f["name"] == "api_route_without_auth_decorator"
        ]
        assert len(auth_findings) == 0


# ---------------------------------------------------------------------------
# Missing validation on write routes
# ---------------------------------------------------------------------------

class TestMissingValidation:
    def test_post_without_validation_is_high(self, scanner):
        code = '''
@app.route("/users", methods=["POST"])
@require_role("admin")
def create_user():
    data = request.get_json()
    return jsonify({"id": 1})
'''
        result = scanner.scan_content(code, "python")
        val_findings = [
            f for f in result["findings"]
            if f["name"] == "write_route_without_input_validation"
        ]
        assert len(val_findings) >= 1
        assert val_findings[0]["severity"] == "high"

    def test_post_with_validate_fields_is_clean(self, scanner):
        code = '''
@app.route("/users", methods=["POST"])
@require_role("admin")
def create_user():
    data = request.get_json()
    _validate_fields(data, ["name", "email"])
    return jsonify({"id": 1})
'''
        result = scanner.scan_content(code, "python")
        val_findings = [
            f for f in result["findings"]
            if f["name"] == "write_route_without_input_validation"
        ]
        assert len(val_findings) == 0

    def test_put_without_validation_flagged(self, scanner):
        code = '''
@app.put("/items/<id>")
@require_auth
def update_item(id):
    data = request.get_json()
    return jsonify(data)
'''
        result = scanner.scan_content(code, "python")
        val_findings = [
            f for f in result["findings"]
            if f["name"] == "write_route_without_input_validation"
        ]
        assert len(val_findings) >= 1

    def test_get_without_validation_not_flagged(self, scanner):
        code = '''
@app.route("/items", methods=["GET"])
@require_role("admin")
def list_items():
    return jsonify([])
'''
        result = scanner.scan_content(code, "python")
        val_findings = [
            f for f in result["findings"]
            if f["name"] == "write_route_without_input_validation"
        ]
        assert len(val_findings) == 0

    def test_post_with_isinstance_check_is_clean(self, scanner):
        code = '''
@app.route("/items", methods=["POST"])
@require_auth
def create_item():
    data = request.get_json()
    if not isinstance(data, dict):
        return jsonify({"error": "bad"}), 400
    return jsonify({"id": 1})
'''
        result = scanner.scan_content(code, "python")
        val_findings = [
            f for f in result["findings"]
            if f["name"] == "write_route_without_input_validation"
        ]
        assert len(val_findings) == 0


# ---------------------------------------------------------------------------
# Exempt routes
# ---------------------------------------------------------------------------

class TestExemptRoutes:
    def test_health_endpoint_exempt(self, scanner):
        code = '''
@app.route("/health")
def health():
    return jsonify({"status": "ok"})
'''
        result = scanner.scan_content(code, "python")
        assert result["routes_exempt"] >= 1
        auth_findings = [
            f for f in result["findings"]
            if f["name"] == "api_route_without_auth_decorator"
        ]
        assert len(auth_findings) == 0

    def test_metrics_endpoint_exempt(self, scanner):
        code = '''
@app.route("/metrics")
def metrics():
    return "metrics"
'''
        result = scanner.scan_content(code, "python")
        assert result["routes_exempt"] >= 1

    def test_login_endpoint_exempt(self, scanner):
        code = '''
@app.route("/login", methods=["POST"])
def login():
    return jsonify({"token": "abc"})
'''
        result = scanner.scan_content(code, "python")
        assert result["routes_exempt"] >= 1

    def test_static_endpoint_exempt(self, scanner):
        code = '''
@app.route("/static/style.css")
def static_file():
    return ""
'''
        result = scanner.scan_content(code, "python")
        assert result["routes_exempt"] >= 1


# ---------------------------------------------------------------------------
# Test file exclusion
# ---------------------------------------------------------------------------

class TestFileExclusion:
    def test_test_file_excluded(self, scanner):
        with tempfile.NamedTemporaryFile(
            suffix=".py", prefix="test_", mode="w", delete=False
        ) as f:
            f.write('@app.route("/users")\ndef x(): pass\n')
            f.flush()
            result = scanner.scan_file(f.name)
        assert result["status"] == "skipped"

    def test_conftest_excluded(self, scanner):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "conftest.py"
            p.write_text('@app.route("/x")\ndef x(): pass\n', encoding="utf-8")
            result = scanner.scan_file(str(p))
        assert result["status"] == "skipped"


# ---------------------------------------------------------------------------
# Multi-language support
# ---------------------------------------------------------------------------

class TestJavaRoutes:
    def test_spring_get_mapping(self, scanner):
        code = '''
@GetMapping("/users")
public List<User> listUsers() {
    return userRepo.findAll();
}
'''
        result = scanner.scan_content(code, "java")
        assert result["routes_found"] >= 1
        assert result["critical"] >= 1  # missing auth

    def test_spring_with_preauthorize(self, scanner):
        code = '''
@PreAuthorize("hasRole('ADMIN')")
@GetMapping("/users")
public List<User> listUsers() {
    return userRepo.findAll();
}
'''
        result = scanner.scan_content(code, "java")
        auth_findings = [
            f for f in result["findings"]
            if f["name"] == "api_route_without_auth_decorator"
        ]
        assert len(auth_findings) == 0


class TestGoRoutes:
    def test_go_handlefunc(self, scanner):
        code = '''
http.HandleFunc("/users", listUsers)
'''
        result = scanner.scan_content(code, "go")
        assert result["routes_found"] >= 1

    def test_go_with_auth_middleware(self, scanner):
        code = '''
http.HandleFunc("/users", authMiddleware(listUsers))
'''
        result = scanner.scan_content(code, "go")
        auth_findings = [
            f for f in result["findings"]
            if f["name"] == "api_route_without_auth_decorator"
        ]
        assert len(auth_findings) == 0


class TestTypescriptRoutes:
    def test_express_route(self, scanner):
        code = '''
router.get("/users", (req, res) => { res.json([]); });
'''
        result = scanner.scan_content(code, "typescript")
        assert result["routes_found"] >= 1

    def test_express_with_auth(self, scanner):
        code = '''
router.get("/users", authMiddleware, (req, res) => { res.json([]); });
'''
        result = scanner.scan_content(code, "typescript")
        auth_findings = [
            f for f in result["findings"]
            if f["name"] == "api_route_without_auth_decorator"
        ]
        assert len(auth_findings) == 0


class TestRustRoutes:
    def test_actix_route(self, scanner):
        code = '''
#[actix_web::get("/users")]
async fn list_users() -> impl Responder {
    HttpResponse::Ok().json(vec![])
}
'''
        result = scanner.scan_content(code, "rust")
        assert result["routes_found"] >= 1

    def test_actix_with_authorize(self, scanner):
        code = '''
#[authorize]
#[actix_web::get("/users")]
async fn list_users() -> impl Responder {
    HttpResponse::Ok().json(vec![])
}
'''
        result = scanner.scan_content(code, "rust")
        auth_findings = [
            f for f in result["findings"]
            if f["name"] == "api_route_without_auth_decorator"
        ]
        assert len(auth_findings) == 0


class TestCsharpRoutes:
    def test_aspnet_route(self, scanner):
        code = '''
[HttpGet]
public IActionResult ListUsers() {
    return Ok(new List<User>());
}
'''
        result = scanner.scan_content(code, "csharp")
        assert result["routes_found"] >= 1

    def test_aspnet_with_authorize(self, scanner):
        code = '''
[Authorize]
[HttpGet]
public IActionResult ListUsers() {
    return Ok(new List<User>());
}
'''
        result = scanner.scan_content(code, "csharp")
        auth_findings = [
            f for f in result["findings"]
            if f["name"] == "api_route_without_auth_decorator"
        ]
        assert len(auth_findings) == 0


# ---------------------------------------------------------------------------
# Gate evaluation
# ---------------------------------------------------------------------------

class TestGateEvaluation:
    def test_gate_passes_clean_scan(self, scanner):
        code = '''
@app.route("/health")
def health():
    return "ok"
'''
        result = scanner.scan_content(code, "python")
        gate = scanner.evaluate_gate(result)
        assert gate["passed"] is True

    def test_gate_fails_on_missing_auth(self, scanner):
        code = '''
@app.route("/users")
def list_users():
    return jsonify([])
'''
        result = scanner.scan_content(code, "python")
        gate = scanner.evaluate_gate(result)
        assert gate["passed"] is False
        assert len(gate["blocking_issues"]) >= 1

    def test_gate_fails_on_missing_validation(self, scanner):
        code = '''
@app.route("/users", methods=["POST"])
@require_role("admin")
def create_user():
    data = request.get_json()
    return jsonify(data)
'''
        result = scanner.scan_content(code, "python")
        gate = scanner.evaluate_gate(result)
        assert gate["passed"] is False

    def test_gate_structure(self, scanner):
        result = scanner.scan_content("# empty", "python")
        gate = scanner.evaluate_gate(result)
        assert "gate" in gate
        assert "passed" in gate
        assert "blocking_issues" in gate
        assert "config" in gate
        assert gate["gate"] == "endpoint_security"


# ---------------------------------------------------------------------------
# Directory scanning
# ---------------------------------------------------------------------------

class TestDirectoryScan:
    def test_scan_directory(self, scanner):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "routes.py"
            p.write_text(
                '@app.route("/x")\ndef x(): pass\n', encoding="utf-8"
            )
            result = scanner.scan_directory(td)
        assert result["status"] == "completed"
        assert result["scanned_files"] >= 1
        assert result["routes_found"] >= 1

    def test_scan_empty_directory(self, scanner):
        with tempfile.TemporaryDirectory() as td:
            result = scanner.scan_directory(td)
        assert result["status"] == "completed"
        assert result["scanned_files"] == 0

    def test_scan_nonexistent_directory(self, scanner):
        result = scanner.scan_directory("/nonexistent/path")
        assert "error" in result


# ---------------------------------------------------------------------------
# Scan result structure
# ---------------------------------------------------------------------------

class TestResultStructure:
    def test_scan_content_fields(self, scanner):
        result = scanner.scan_content("# nothing", "python")
        assert "scan_type" in result
        assert result["scan_type"] == "endpoint_security"
        assert "routes_found" in result
        assert "routes_exempt" in result
        assert "findings_count" in result
        assert "critical" in result
        assert "high" in result
        assert "findings" in result
        assert isinstance(result["findings"], list)

    def test_finding_fields(self, scanner):
        code = '''
@app.route("/users")
def list_users():
    return jsonify([])
'''
        result = scanner.scan_content(code, "python")
        assert len(result["findings"]) >= 1
        f = result["findings"][0]
        assert "name" in f
        assert "severity" in f
        assert "description" in f
        assert "line" in f
        assert "route_path" in f
        assert "language" in f


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

class TestCLI:
    def test_cli_help(self):
        result = subprocess.run(
            [sys.executable, "tools/security/endpoint_security_scanner.py", "--help"],
            capture_output=True, text=True, cwd=str(Path(__file__).parent.parent)
        )
        assert result.returncode == 0
        assert "endpoint" in result.stdout.lower()

    def test_cli_json_output(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "api.py"
            p.write_text(
                '@app.route("/test")\ndef t(): pass\n', encoding="utf-8"
            )
            result = subprocess.run(
                [sys.executable, "tools/security/endpoint_security_scanner.py",
                 "--dir", td, "--json"],
                capture_output=True, text=True,
                cwd=str(Path(__file__).parent.parent)
            )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["scan_type"] == "endpoint_security"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_content(self, scanner):
        result = scanner.scan_content("", "python")
        assert result["routes_found"] == 0
        assert result["findings_count"] == 0

    def test_comments_only(self, scanner):
        result = scanner.scan_content("# just a comment\n# nothing here\n", "python")
        assert result["routes_found"] == 0

    def test_unsupported_language(self, scanner):
        result = scanner.scan_content("code", "cobol")
        assert result["status"] == "skipped"
        assert result["findings_count"] == 0

    def test_multiple_routes_mixed_auth(self, scanner):
        # Routes must be >20 lines apart (context window) to avoid
        # the auth decorator from /secure leaking into /public's context.
        padding = "\n".join([f"    # line {i}" for i in range(25)])
        code = f'''
@app.route("/public")
def public():
    return "hello"

{padding}

@app.route("/secure")
@require_role("admin")
def secure():
    return "secret"
'''
        result = scanner.scan_content(code, "python")
        assert result["routes_found"] == 2
        # public should be flagged, secure should not
        auth_findings = [
            f for f in result["findings"]
            if f["name"] == "api_route_without_auth_decorator"
        ]
        assert len(auth_findings) == 1
        assert "/public" in auth_findings[0].get("route_path", "")
