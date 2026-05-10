# Auto-grader for T3 M7 Step 1: Tier 3 Capstone

# ── Setup: perfect capstone app ───────────────────────────────────────────────

PERFECT_MANIFEST = AppManifest(
    app_name="Mission Tracker",
    app_slug="missiontracker",
    canvas="ODC",
    description="Real-time mission status tracking for operational teams",
    routes=["/", "/api/missions", "/api/status"],
    db_tables=["missions", "mission_events"],
    author="forge_academy",
)

PERFECT_GOAL = """
# Mission Tracker Goal
# Tools: tools/ops/mission_tracker.py, tools/db/storage.py
# Args: args/mission_config.yaml
# Output: DB table missions

## Steps
1. Load active missions — mission_tracker.py (system_id)
2. Update status — mission_tracker.py (mission_id, status)
3. Persist updates — storage.py (missions_table)
"""

PERFECT_FILES = {
    "tools/dashboard/templates/missiontracker/page.html",
    "apps/missiontracker/blueprint.py",
    "apps/missiontracker/missiontracker.py",
    "apps/missiontracker/constants.py",
    "apps/missiontracker/migrations",
    "icdev/tools/dashboard/templates/missiontracker/page.html",
}

app_perfect = CapstoneApp(
    manifest=PERFECT_MANIFEST,
    goal_content=PERFECT_GOAL,
    blueprint_files=PERFECT_FILES,
    system_name="ICDEV-Prod",
    control_id="IA-2",
)

# ── Test: ship() basic shape ──────────────────────────────────────────────────

report = app_perfect.ship()
assert report is not None, "ship() returned None"
assert isinstance(report, dict), f"ship() must return dict, got {type(report)}"
assert "ready" in report, "Result must have 'ready'"
assert "score" in report, "Result must have 'score'"
assert "max_score" in report, "Result must have 'max_score'"
assert "blockers" in report, "Result must have 'blockers'"
assert "components" in report, "Result must have 'components'"
assert report["max_score"] == 10, f"max_score must be 10, got {report['max_score']}"
assert isinstance(report["blockers"], list), "blockers must be list"
assert isinstance(report["components"], dict), "components must be dict"

# ── Test: ship() — perfect app scores 10/10 ──────────────────────────────────

assert report["score"] == 10, \
    f"Perfect capstone should score 10/10, got {report['score']} (components={report['components']})"
assert report["ready"] is True, \
    f"Perfect capstone should be ready, got ready=False (blockers={report['blockers']})"
assert len(report["blockers"]) == 0, \
    f"Perfect capstone should have no blockers, got: {report['blockers']}"

# ── Test: components dict has 5 entries ───────────────────────────────────────

for comp_name in ["manifest", "goal", "blueprint", "tool", "completeness"]:
    assert comp_name in report["components"], \
        f"components must have '{comp_name}' key"
    comp = report["components"][comp_name]
    assert "score" in comp, f"components['{comp_name}'] must have 'score'"
    assert isinstance(comp["score"], (int, float)), f"score must be numeric"
    assert comp["score"] >= 0, f"score must be ≥ 0"
    assert comp["score"] == 2, \
        f"Perfect app: components['{comp_name}']['score'] should be 2, got {comp['score']}"

# ── Test: ship() — invalid manifest → score deducted ─────────────────────────

bad_manifest = AppManifest("", "INVALID SLUG", "XYZ", "", [], [])
app_bad = CapstoneApp(
    manifest=bad_manifest,
    goal_content=PERFECT_GOAL,
    blueprint_files=PERFECT_FILES,
    system_name="ICDEV-Prod",
    control_id="IA-2",
)
report_bad = app_bad.ship()
assert report_bad["score"] < 10, \
    f"Bad manifest should reduce score, got {report_bad['score']}"
assert report_bad["components"]["manifest"]["score"] == 0, \
    f"Invalid manifest → manifest score=0, got {report_bad['components']['manifest']['score']}"
assert "manifest" in report_bad["blockers"], \
    f"Invalid manifest → 'manifest' in blockers, got {report_bad['blockers']}"
assert report_bad["ready"] is False, "Bad manifest → ready=False"

# ── Test: ship() — invalid goal → score deducted ─────────────────────────────

bad_goal = "# No tools, no steps, no output"
app_bad_goal = CapstoneApp(
    manifest=PERFECT_MANIFEST,
    goal_content=bad_goal,
    blueprint_files=PERFECT_FILES,
    system_name="ICDEV-Prod",
    control_id="IA-2",
)
report_bad_goal = app_bad_goal.ship()
assert report_bad_goal["components"]["goal"]["score"] == 0, \
    f"Invalid goal → goal score=0, got {report_bad_goal['components']['goal']['score']}"
assert "goal" in report_bad_goal["blockers"], \
    f"Invalid goal → 'goal' in blockers"

# ── Test: ship() — unknown system → tool score deducted ──────────────────────

app_unknown_sys = CapstoneApp(
    manifest=PERFECT_MANIFEST,
    goal_content=PERFECT_GOAL,
    blueprint_files=PERFECT_FILES,
    system_name="ICDEV-Unknown",
    control_id="ZZ-99",
)
report_unknown = app_unknown_sys.ship()
assert report_unknown["components"]["tool"]["score"] == 0, \
    f"Unknown system → tool score=0, got {report_unknown['components']['tool']['score']}"
assert "tool" in report_unknown["blockers"], "Unknown system → 'tool' in blockers"

# ── Test: ship() — partial blueprint ─────────────────────────────────────────

partial_files = {
    "tools/dashboard/templates/missiontracker/page.html",
    "apps/missiontracker/blueprint.py",
    "apps/missiontracker/missiontracker.py",
    # missing: constants, migrations, icdev_mirror (score=4/7, nav_link always True → actually 4 files + nav=5)
}
app_partial = CapstoneApp(
    manifest=PERFECT_MANIFEST,
    goal_content=PERFECT_GOAL,
    blueprint_files=partial_files,
    system_name="ICDEV-Prod",
    control_id="IA-2",
)
report_partial = app_partial.ship()
# partial blueprint: score≥3 but <5 → partial blueprint gets 1 point (or 0 if <3)
# With nav_link always True: 3 files + nav = 4 present → score=4, still ≥3 → gets 1 point
assert report_partial["components"]["blueprint"]["score"] <= 1, \
    f"Partial blueprint (3-4/7) should score ≤1, got {report_partial['components']['blueprint']['score']}"
assert report_partial["score"] < 10, "Partial blueprint should reduce total score"

print("PASS: CapstoneApp complete. ship() verified for perfect, bad-manifest, bad-goal, unknown-system, and partial-blueprint scenarios.")
