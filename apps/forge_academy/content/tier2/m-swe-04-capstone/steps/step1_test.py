# Auto-grader for SWE/Arch Capstone M4: Multi-Agent System Designer

# ── Test: render_template_str ─────────────────────────────────────────────────

rendered = render_template_str("App: {{ app_name }}, Slug: {{ app_slug }}", {"app_name": "Test App", "app_slug": "testapp"})
assert rendered is not None, "render_template_str() returned None"
assert "Test App" in rendered, "app_name should be replaced"
assert "testapp" in rendered, "app_slug should be replaced"
assert "{{ app_name }}" not in rendered, "placeholder should be replaced"

# Multiple occurrences
rendered2 = render_template_str("{{ x }} and {{ x }}", {"x": "hello"})
assert rendered2.count("hello") == 2, "All occurrences of {{ x }} should be replaced"

# ── Test: validate_spec — valid ───────────────────────────────────────────────

spec_valid = ScaffoldSpec("Threat Monitor", "threatmon", "SDC")
valid, issues = validate_spec(spec_valid)
assert valid is True, f"Valid spec should pass, got issues: {issues}"
assert issues == [], f"Valid spec -> no issues, got: {issues}"

# ── Test: validate_spec — invalid cases ──────────────────────────────────────

spec_bad_slug = ScaffoldSpec("App", "My App", "SDC")
v2, iss2 = validate_spec(spec_bad_slug)
assert v2 is False, "Invalid slug should fail"
assert any("app_slug" in i or "My App" in i for i in iss2), f"Issues should mention slug: {iss2}"

spec_bad_canvas = ScaffoldSpec("App", "myapp", "XYZ")
v3, iss3 = validate_spec(spec_bad_canvas)
assert v3 is False, "Invalid canvas should fail"
assert any("canvas" in i for i in iss3), f"Issues should mention canvas: {iss3}"

spec_empty_name = ScaffoldSpec("", "myapp", "ODC")
v4, iss4 = validate_spec(spec_empty_name)
assert v4 is False, "Empty app_name should fail"
assert any("app_name" in i for i in iss4), f"Issues should mention app_name: {iss4}"

# ── Test: generate_scaffold_files ────────────────────────────────────────────

spec = ScaffoldSpec("Threat Monitor", "threatmon", "SDC")
files = generate_scaffold_files(spec)
assert files is not None, "generate_scaffold_files() returned None"
assert isinstance(files, dict), "generate_scaffold_files() must return dict"
assert len(files) == 3, f"Should generate 3 files, got {len(files)}"

expected_paths = ["apps/threatmon/__init__.py", "apps/threatmon/blueprint.py", "apps/threatmon/constants.py"]
for p in expected_paths:
    assert p in files, f"Expected file '{p}' not in generated files: {list(files.keys())}"
    assert isinstance(files[p], str), f"File content must be str: {p}"
    assert len(files[p]) > 5, f"File content too short: {p}"

assert "Threat Monitor" in files["apps/threatmon/__init__.py"], "__init__.py should have app_name"
assert "threatmon" in files["apps/threatmon/blueprint.py"], "blueprint.py should have app_slug"
assert "SDC" in files["apps/threatmon/constants.py"], "constants.py should have canvas"

# ── Test: SystemDesigner.register_app_tools ───────────────────────────────────

designer = SystemDesigner()
tools = designer.register_app_tools(spec)
assert tools is not None, "register_app_tools() returned None"
assert isinstance(tools, list), "register_app_tools() must return list"
assert len(tools) == 2, f"Should register 2 tools, got {len(tools)}"

assert any("get_records" in t for t in tools), "Should register a get_records tool"
assert any("get_status" in t for t in tools), "Should register a get_status tool"

# Tools should be callable via the registry
records_tool = next(t for t in tools if "get_records" in t)
call_result = designer.registry.call(records_tool, system_id="ICDEV-Prod")
assert "error" not in call_result, f"Tool call returned error: {call_result}"
assert "result" in call_result, "Tool call must return result"
assert "records" in call_result["result"], "get_records tool must return records key"

status_tool = next(t for t in tools if "get_status" in t)
call_status = designer.registry.call(status_tool, component="api-gateway")
assert "result" in call_status, "get_status tool must return result"
assert call_status["result"]["status"] == "ok", "get_status should return status=ok"

# ── Test: SystemDesigner.design — valid spec ──────────────────────────────────

designer2 = SystemDesigner()
result = designer2.design(spec)
assert result is not None, "design() returned None"
assert isinstance(result, dict), "design() must return dict"

for key in ["app_name", "app_slug", "canvas", "valid", "issues", "files_generated",
            "file_paths", "tools_registered", "ready"]:
    assert key in result, f"Result missing '{key}'"

assert result["app_name"] == "Threat Monitor", f"app_name mismatch: {result['app_name']}"
assert result["app_slug"] == "threatmon", f"app_slug mismatch: {result['app_slug']}"
assert result["valid"] is True, f"Valid spec -> valid=True, got issues: {result['issues']}"
assert result["ready"] is True, "Valid spec + files + tools -> ready=True"
assert result["files_generated"] == 3, f"Should generate 3 files, got {result['files_generated']}"
assert result["file_paths"] == sorted(result["file_paths"]), "file_paths must be sorted"
assert len(result["tools_registered"]) == 2, f"Should register 2 tools"

# ── Test: SystemDesigner.design — invalid spec ────────────────────────────────

designer3 = SystemDesigner()
bad_spec = ScaffoldSpec("", "bad slug!", "INVALID")
result_bad = designer3.design(bad_spec)
assert result_bad["valid"] is False, "Invalid spec -> valid=False"
assert result_bad["ready"] is False, "Invalid spec -> ready=False"
assert result_bad["files_generated"] == 0, "Invalid spec -> no files generated"
assert result_bad["tools_registered"] == [], "Invalid spec -> no tools registered"
assert len(result_bad["issues"]) >= 3, f"3 issues expected, got: {result_bad['issues']}"

print("PASS: SystemDesigner complete. render_template_str + validate_spec + generate_scaffold_files + design() all verified.")
