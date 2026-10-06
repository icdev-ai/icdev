---
ontology_id: icdev:mission:m-t3-07-tier3-capstone:step:1
step_class: icdev:Lesson
---

# Tier 3 Capstone — Ship a Real Child App

You've traced goal executions, written tools, authored goals, built blueprints, selected canvases, and scaffolded manifests. Now you put them together. In this capstone you'll integrate the Tier 3 building blocks into a `CapstoneApp` — a ship-readiness model that wires the manifest, goal, blueprint and tool layers into one report.

> **Scope note:** `CapstoneApp` is a self-contained readiness model that runs in the exercise sandbox — the helper classes are the simplified versions you built in T3-02 to T3-06 and are provided in the starter. In the real repo, "ready to ship" is decided by `python tools/builder/forge_validator.py --project-dir <app> --gate` for a generated child app, and by the required CI checks (Lint, Test, Security Scan, Helm Lint) on its pull request.

## The Integration Challenge

Real ICDEV apps connect these layers:

```
AppManifest (canvas + slug + routes + tables)
    ↓
GoalValidator (goal file validates before execution)
    ↓
BlueprintSpec (7 file-checkable gate components)
    ↓
evidence collector tool (the T3-02 tool contract)
    ↓
CapstoneApp.ship() → comprehensive readiness report
```

## What You'll Build

A `CapstoneApp` that integrates manifest, goal, blueprint, and tool layers:

```python
app = CapstoneApp(
    manifest=AppManifest(...),
    goal_content="# Goal\n# Tools: ...",
    blueprint_files={"apps/<slug>/blueprint.py", "apps/<slug>/constants.py", ...},  # set of file paths present
)
report = app.ship()
# → {"ready": bool, "score": N/10, "components": {...}, "blockers": [...]}
```

## Scoring (10 points total)

| Component | Points | Check |
|-----------|--------|-------|
| Manifest valid | 2 | manifest.validate() passes |
| Goal valid | 2 | GoalValidator().validate() passes |
| Blueprint ≥5/7 | 2 | BlueprintSpec score ≥ 5 |
| Tool returns ok | 2 | collect_evidence result has status=="ok" |
| Completeness | 2 | manifest completeness score == 5 |

## Blockers

Any component scoring 0 (completely missing or invalid) adds itself to the `blockers` list. The app is `ready` only when `score >= 8` and `blockers == []`.

## Success Criteria

- `CapstoneApp.ship()` returns a dict with `ready`, `score`, `components`, `blockers`
- Score correctly totals across all 5 component checks
- `ready=True` requires score ≥ 8 and no blockers
- Partial scores (e.g., blueprint 3/7) award partial points
- Each component result is included in `components` for debugging
