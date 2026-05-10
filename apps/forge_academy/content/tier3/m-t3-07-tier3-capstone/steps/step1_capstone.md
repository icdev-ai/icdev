# Tier 3 Capstone — Ship a Real Child App

You've traced goal executions, written tools, authored goals, built blueprints, selected canvases, and scaffolded manifests. Now you ship something real. In this capstone you'll integrate all 7 Tier 3 skills into a complete `CapstoneApp` — a mini ICDEV extension that wires the whole stack together.

## The Integration Challenge

Real ICDEV apps connect these layers:

```
AppManifest (canvas + slug + routes + tables)
    ↓
GoalValidator (goal file validates before execution)
    ↓
BlueprintSpec (7-component gate before shipping)
    ↓
evidence collector tool (ICDEV tool contract)
    ↓
CapstoneApp.ship() → comprehensive readiness report
```

## What You'll Build

A `CapstoneApp` that integrates manifest, goal, blueprint, and tool layers:

```python
app = CapstoneApp(
    manifest=AppManifest(...),
    goal_content="# Goal\n# Tools: ...",
    blueprint_mock_files={"files": [...]},
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
