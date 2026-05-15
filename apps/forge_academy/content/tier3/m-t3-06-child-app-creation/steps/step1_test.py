
# Auto-grader for T3 M6 Step 1: Child App Creation

# ── Test: AppManifest.validate() — valid manifest ────────────────────────────

m_valid = AppManifest(
    app_name="Status Board",
    app_slug="statusboard",
    canvas="ODC",
    description="Real-time operational status display for mission tracking",
    routes=["/", "/api/status"],
    db_tables=["statusboard_items"],
    author="forge_academy",
)

result = m_valid.validate()
assert result is not None, "validate() returned None"
assert isinstance(result, tuple), f"validate() must return tuple, got {type(result)}"
assert len(result) == 2, "validate() must return (bool, list)"
valid, issues = result
assert valid is True, f"Valid manifest should pass, got valid=False, issues={issues}"
assert isinstance(issues, list), f"issues must be list, got {type(issues)}"
assert len(issues) == 0, f"Valid manifest should have no issues, got: {issues}"

# ── Test: AppManifest.validate() — empty app_name ────────────────────────────

m_no_name = AppManifest("", "myapp", "NDC", "desc", ["/"], ["t"])
valid_nn, issues_nn = m_no_name.validate()
assert valid_nn is False, "Empty app_name should be invalid"
assert any("app_name" in i for i in issues_nn), \
    f"Issues should mention 'app_name', got: {issues_nn}"

# ── Test: AppManifest.validate() — bad slug ───────────────────────────────────

bad_slugs = ["My App", "MyApp", "my-app", "123app", "app name", "APP"]
for slug in bad_slugs:
    m_bad = AppManifest("App", slug, "NDC", "d", ["/"], ["t"])
    v, iss = m_bad.validate()
    assert v is False, f"Slug '{slug}' should be invalid"
    assert any("app_slug" in i or slug in i for i in iss), \
        f"Issue should mention slug '{slug}', got: {iss}"

# Good slugs should pass slug check
good_slugs = ["myapp", "my_app", "app123", "statusboard"]
for slug in good_slugs:
    m_good = AppManifest("App", slug, "NDC", "d", ["/"], ["t"])
    v, iss = m_good.validate()
    slug_issues = [i for i in iss if "slug" in i.lower()]
    assert len(slug_issues) == 0, f"Good slug '{slug}' should not have slug issues, got: {slug_issues}"

# ── Test: AppManifest.validate() — invalid canvas ────────────────────────────

m_bad_canvas = AppManifest("App", "myapp", "XYZ", "d", ["/"], ["t"])
v_bc, iss_bc = m_bad_canvas.validate()
assert v_bc is False, "Invalid canvas should fail"
assert any("canvas" in i for i in iss_bc), \
    f"Issues should mention 'canvas', got: {iss_bc}"

# All 7 valid canvases should pass canvas check
for code in ["NDC", "SDC", "PDC", "BDC", "DDC", "ODC", "IDC"]:
    m_c = AppManifest("App", "myapp", code, "d", ["/"], ["t"])
    _, iss_c = m_c.validate()
    canvas_issues = [i for i in iss_c if "canvas" in i.lower()]
    assert len(canvas_issues) == 0, f"Valid canvas '{code}' should not have issues, got: {canvas_issues}"

# ── Test: AppManifest.validate() — no routes ─────────────────────────────────

m_no_routes = AppManifest("App", "myapp", "NDC", "d", [], ["t"])
v_nr, iss_nr = m_no_routes.validate()
assert v_nr is False, "No routes should be invalid"
assert any("route" in i.lower() for i in iss_nr), \
    f"Issues should mention 'route', got: {iss_nr}"

# ── Test: AppManifest.validate() — no tables ─────────────────────────────────

m_no_tables = AppManifest("App", "myapp", "NDC", "d", ["/"], [])
v_nt, iss_nt = m_no_tables.validate()
assert v_nt is False, "No DB tables should be invalid"
assert any("table" in i.lower() for i in iss_nt), \
    f"Issues should mention 'table', got: {iss_nt}"

# ── Test: AppManifest.to_dict ─────────────────────────────────────────────────

d = m_valid.to_dict()
assert d is not None, "to_dict() returned None"
assert isinstance(d, dict), "to_dict() must return dict"
for key in ["app_name", "app_slug", "canvas", "description", "routes", "db_tables", "author"]:
    assert key in d, f"to_dict() must have '{key}' key"
assert d["app_slug"] == "statusboard", f"app_slug mismatch: {d['app_slug']}"
assert d["canvas"] == "ODC", f"canvas mismatch: {d['canvas']}"
assert isinstance(d["routes"], list), "routes must be list"
assert isinstance(d["db_tables"], list), "db_tables must be list"

# ── Test: generate_file_tree ──────────────────────────────────────────────────

tree = generate_file_tree("statusboard")
assert tree is not None, "generate_file_tree() returned None"
assert isinstance(tree, list), "generate_file_tree() must return list"
assert len(tree) == 7, f"File tree must have 7 entries, got {len(tree)}: {tree}"

expected_paths = [
    "apps/statusboard/__init__.py",
    "apps/statusboard/blueprint.py",
    "apps/statusboard/statusboard.py",
    "apps/statusboard/constants.py",
    "apps/statusboard/migrations",
    "tools/dashboard/templates/statusboard/page.html",
    "icdev/tools/dashboard/templates/statusboard/page.html",
]
for path in expected_paths:
    assert path in tree, f"Expected '{path}' in file tree, got: {tree}"

# ── Test: check_completeness ──────────────────────────────────────────────────

c_full = check_completeness(m_valid)
assert c_full is not None, "check_completeness() returned None"
assert isinstance(c_full, dict), "check_completeness() must return dict"
assert "score" in c_full, "Result must have 'score'"
assert "max_score" in c_full, "Result must have 'max_score'"
assert "complete" in c_full, "Result must have 'complete'"
assert "signals" in c_full, "Result must have 'signals'"
assert c_full["max_score"] == 5, f"max_score must be 5, got {c_full['max_score']}"
assert c_full["score"] == 5, \
    f"Full manifest should score 5/5, got {c_full['score']} (signals={c_full['signals']})"
assert c_full["complete"] is True, "Full manifest should be complete"

# Sparse manifest → lower score
m_sparse = AppManifest("A", "myapp", "NDC", "", ["/"], [])
c_sparse = check_completeness(m_sparse)
assert c_sparse["score"] < 5, f"Sparse manifest should score <5, got {c_sparse['score']}"
assert c_sparse["complete"] is False, "Sparse manifest should not be complete"

print("PASS: AppManifest complete. validate + to_dict + generate_file_tree + check_completeness all verified.")
