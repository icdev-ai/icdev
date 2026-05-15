
# Auto-grader for SWE/Arch M3: System Scaffold Generator

# ── Test: render_template_str ─────────────────────────────────────────────────

rendered = render_template_str("Hello {{ name }}, you are {{ age }} years old.", {"name": "Alice", "age": 30})
assert rendered is not None, "render_template_str() returned None"
assert isinstance(rendered, str), "render_template_str() must return str"
assert "Alice" in rendered, "name placeholder should be replaced with 'Alice'"
assert "30" in rendered, "age placeholder should be replaced with '30'"
assert "{{ name }}" not in rendered, "{{ name }} should be replaced"
assert "{{ age }}" not in rendered, "{{ age }} should be replaced"

# Multiple occurrences of same placeholder
rendered2 = render_template_str("{{ x }} + {{ x }} = double {{ x }}", {"x": "7"})
assert rendered2.count("7") == 3, f"All 3 occurrences of {{{{ x }}}} should be replaced, got: {rendered2}"

# Unused context keys are fine
rendered3 = render_template_str("Hello {{ name }}", {"name": "Bob", "unused": "key"})
assert "Bob" in rendered3, "Used key should be replaced"

# ── Setup generator ───────────────────────────────────────────────────────────

gen = ScaffoldGenerator()
spec = ScaffoldSpec(
    app_name="Mission Dashboard",
    app_slug="missiondash",
    canvas="ODC",
    description="Real-time mission tracking",
)

# ── Test: validate_spec — valid ───────────────────────────────────────────────

valid, issues = gen.validate_spec(spec)
assert valid is True, f"Valid spec should pass, got valid=False, issues={issues}"
assert isinstance(issues, list), "issues must be list"
assert len(issues) == 0, f"Valid spec should have no issues, got: {issues}"

# ── Test: validate_spec — invalid slug ───────────────────────────────────────

bad_spec = ScaffoldSpec("App", "My App", "ODC")
v, iss = gen.validate_spec(bad_spec)
assert v is False, "Invalid slug should fail"
assert any("app_slug" in i or "My App" in i for i in iss), \
    f"Issues should mention slug, got: {iss}"

# ── Test: validate_spec — invalid canvas ─────────────────────────────────────

bad_canvas = ScaffoldSpec("App", "myapp", "XYZ")
v2, iss2 = gen.validate_spec(bad_canvas)
assert v2 is False, "Invalid canvas should fail"
assert any("canvas" in i for i in iss2), f"Issues should mention canvas, got: {iss2}"

# ── Test: validate_spec — empty app_name ─────────────────────────────────────

empty_name = ScaffoldSpec("", "myapp", "ODC")
v3, iss3 = gen.validate_spec(empty_name)
assert v3 is False, "Empty name should fail"
assert any("app_name" in i for i in iss3), f"Issues should mention app_name, got: {iss3}"

# ── Test: generate_files ──────────────────────────────────────────────────────

files = gen.generate_files(spec)
assert files is not None, "generate_files() returned None"
assert isinstance(files, dict), "generate_files() must return dict"
assert len(files) == 5, f"Should generate 5 files, got {len(files)}"

expected_files = [
    "apps/missiondash/__init__.py",
    "apps/missiondash/blueprint.py",
    "apps/missiondash/db.py",
    "apps/missiondash/constants.py",
    "apps/missiondash/migrations/001_initial.sql",
]
for f in expected_files:
    assert f in files, f"Expected file '{f}' not in generated files: {list(files.keys())}"
    assert isinstance(files[f], str), f"File content for '{f}' must be str"
    assert len(files[f]) > 10, f"File '{f}' content is too short"

# __init__.py should mention app name
init_content = files["apps/missiondash/__init__.py"]
assert "Mission Dashboard" in init_content, \
    f"__init__.py should contain app_name, got: {init_content}"

# blueprint.py should have route decorator and correct prefix
bp_content = files["apps/missiondash/blueprint.py"]
assert "@bp.route" in bp_content, "blueprint.py should have route decorators"
assert "missiondash" in bp_content, "blueprint.py should contain app_slug"
assert "Blueprint" in bp_content, "blueprint.py should import Blueprint"

# constants.py should have correct values
const_content = files["apps/missiondash/constants.py"]
assert "Mission Dashboard" in const_content, "constants.py should have app_name"
assert "missiondash" in const_content, "constants.py should have app_slug"
assert "ODC" in const_content, "constants.py should have canvas"

# migration SQL should have IF NOT EXISTS and created_at
sql_content = files["apps/missiondash/migrations/001_initial.sql"]
assert "IF NOT EXISTS" in sql_content, "Migration must use IF NOT EXISTS"
assert "created_at" in sql_content, "Migration must have created_at"
assert "missiondash" in sql_content, "Migration table should include app_slug"

# ── Test: get_file_list ───────────────────────────────────────────────────────

file_list = gen.get_file_list(spec)
assert file_list is not None, "get_file_list() returned None"
assert isinstance(file_list, list), "get_file_list() must return list"
assert len(file_list) == 5, f"get_file_list should return 5 files, got {len(file_list)}"
assert file_list == sorted(file_list), "File list should be sorted"

print("PASS: ScaffoldGenerator complete. render_template_str + validate_spec + generate_files + get_file_list all verified.")
