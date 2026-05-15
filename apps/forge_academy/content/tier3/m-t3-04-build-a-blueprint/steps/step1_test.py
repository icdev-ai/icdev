
# Auto-grader for T3 M4 Step 1: Build a Blueprint

# ── Setup mock filesystem ─────────────────────────────────────────────────────

FULL_APP = "statusboard"
PARTIAL_APP = "partialapp"

# Full app — all 6 file-checkable components present (nav_link always True)
MOCK_FS.update([
    f"tools/dashboard/templates/{FULL_APP}/page.html",
    f"apps/{FULL_APP}/blueprint.py",
    f"apps/{FULL_APP}/{FULL_APP}.py",
    f"apps/{FULL_APP}/constants.py",
    f"apps/{FULL_APP}/migrations",
    f"icdev/tools/dashboard/templates/{FULL_APP}/page.html",
])

# Partial app — only template + blueprint, missing backing_module/constants/migrations/icdev_mirror
MOCK_FS.update([
    f"tools/dashboard/templates/{PARTIAL_APP}/page.html",
    f"apps/{PARTIAL_APP}/blueprint.py",
])

# ── Test: BlueprintSpec.__init__ ──────────────────────────────────────────────

spec_full = BlueprintSpec(FULL_APP, use_mock=True)
assert hasattr(spec_full, "app_name"), "BlueprintSpec must store app_name"
assert spec_full.app_name == FULL_APP, f"app_name should be '{FULL_APP}'"
assert hasattr(spec_full, "use_mock"), "BlueprintSpec must store use_mock"
assert hasattr(spec_full, "template"), "BlueprintSpec must compute template path"
assert FULL_APP in spec_full.template, "template path must include app_name"
assert "page.html" in spec_full.template, "template path must end with page.html"
assert hasattr(spec_full, "blueprint_file"), "BlueprintSpec must compute blueprint_file"
assert hasattr(spec_full, "backing_module"), "BlueprintSpec must compute backing_module"
assert hasattr(spec_full, "constants_file"), "BlueprintSpec must compute constants_file"
assert hasattr(spec_full, "migrations_dir"), "BlueprintSpec must compute migrations_dir"
assert hasattr(spec_full, "icdev_mirror"), "BlueprintSpec must compute icdev_mirror"
assert "icdev" in spec_full.icdev_mirror, "icdev_mirror path must contain 'icdev'"

# ── Test: check_components() — full app ──────────────────────────────────────

components = spec_full.check_components()
assert components is not None, "check_components() returned None"
assert isinstance(components, dict), f"check_components() must return dict"
for name in COMPONENT_NAMES:
    assert name in components, f"components must have '{name}' key"
    assert isinstance(components[name], bool), f"components['{name}'] must be bool"

assert components["template"] is True, "template should exist in full app"
assert components["route"] is True, "route (blueprint.py) should exist in full app"
assert components["backing_module"] is True, "backing_module should exist in full app"
assert components["constants"] is True, "constants should exist in full app"
assert components["migration"] is True, "migration should exist in full app"
assert components["nav_link"] is True, "nav_link is always True"
assert components["icdev_mirror"] is True, "icdev_mirror should exist in full app"

# ── Test: validate() — full app (score=7) ────────────────────────────────────

result_full = spec_full.validate()
assert result_full is not None, "validate() returned None"
assert isinstance(result_full, dict), f"validate() must return dict"
assert "score" in result_full, "Result must have 'score'"
assert "valid" in result_full, "Result must have 'valid'"
assert "components" in result_full, "Result must have 'components'"
assert "missing" in result_full, "Result must have 'missing'"
assert "max_score" in result_full, "Result must have 'max_score'"
assert result_full["max_score"] == 7, f"max_score must be 7, got {result_full['max_score']}"
assert result_full["score"] == 7, f"Full app should score 7/7, got {result_full['score']}"
assert result_full["valid"] is True, "Full app should be valid"
assert len(result_full["missing"]) == 0, f"Full app should have no missing components: {result_full['missing']}"

# ── Test: validate() — partial app ────────────────────────────────────────────

spec_partial = BlueprintSpec(PARTIAL_APP, use_mock=True)
result_partial = spec_partial.validate()
assert result_partial["valid"] is False, "Partial app should not be valid"
assert result_partial["score"] < 7, f"Partial app score should be <7, got {result_partial['score']}"
assert result_partial["score"] >= 2, f"Partial app has 2 files → score≥2 (nav_link=True adds 1 more), got {result_partial['score']}"
assert len(result_partial["missing"]) >= 1, "Partial app should have missing components"
assert "backing_module" in result_partial["missing"], "backing_module should be missing"
assert "icdev_mirror" in result_partial["missing"], "icdev_mirror should be missing"

# ── Test: extract_routes ──────────────────────────────────────────────────────

bp_code = '''
@bp.route("/")
def index(): pass

@bp.route("/api/records")
def api_records(): pass

@bp.route("/detail/<int:id>")
def detail(id): pass
'''

routes = extract_routes(bp_code)
assert routes is not None, "extract_routes() returned None"
assert isinstance(routes, list), f"extract_routes() must return list"
assert len(routes) == 3, f"Should find 3 routes, got {len(routes)}: {routes}"
assert "/" in routes, "'/' should be in routes"
assert "/api/records" in routes, "'/api/records' should be in routes"

# No routes → empty list
no_routes = extract_routes("# blueprint with no routes\ndef helper(): pass\n")
assert isinstance(no_routes, list), "extract_routes() must return list for empty blueprint"
assert len(no_routes) == 0, f"No @bp.route lines → [], got {no_routes}"

# ── Test: check_migration_sql ─────────────────────────────────────────────────

good_sql = "CREATE TABLE IF NOT EXISTS statusboard_items (id INTEGER PRIMARY KEY, name TEXT, created_at TEXT);"
check_good = check_migration_sql(good_sql)
assert check_good is not None, "check_migration_sql() returned None"
assert isinstance(check_good, dict), f"check_migration_sql() must return dict"
assert check_good["if_not_exists"] is True, f"Good SQL has IF NOT EXISTS"
assert check_good["has_id"] is True, f"Good SQL has 'id' column"
assert check_good["has_created_at"] is True, f"Good SQL has created_at"
assert check_good["valid"] is True, f"Good SQL should be valid"

# Missing IF NOT EXISTS
bad_sql1 = "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, created_at TEXT);"
check_bad1 = check_migration_sql(bad_sql1)
assert check_bad1["if_not_exists"] is False, "Missing IF NOT EXISTS"
assert check_bad1["valid"] is False, "Incomplete SQL is not valid"

# Missing created_at
bad_sql2 = "CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT);"
check_bad2 = check_migration_sql(bad_sql2)
assert check_bad2["has_created_at"] is False, "Missing created_at"
assert check_bad2["valid"] is False, "Missing created_at → not valid"

print("PASS: BlueprintSpec complete. __init__ + check_components + validate + extract_routes + check_migration_sql all verified.")
