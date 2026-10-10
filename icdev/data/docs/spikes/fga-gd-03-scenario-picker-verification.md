# CUI // SP-CTI

# FGA-GD-03 — Scenario picker: claim does not reproduce

**Date:** 2026-07-28
**Task:** `fga-gd-03` — VERIFY-BEFORE-FIX, explicitly an investigation: *"Do not
change behaviour unless it reproduces."*

## The claim

The GameDay audit reported that the scenario picker is cosmetic — that all 9
scenarios serve the same 6 `ai_gameday` injects, and that 9 sessions created from
different dropdown selections all showed identical injects.

## Verdict: **REFUTED. No code was changed.**

Every pack resolves to a distinct inject set. Nine packs, nine distinct sets:

| pack | injects |
|---|---|
| `ai_gameday` | 6 |
| `document-integrity` | 4 |
| `forge_ascent` | 5 |
| `grounding-red-team` | 4 |
| `hunt_the_fleet` | 5 |
| `interagency` | 5 |
| `meridian` | 5 |
| `red_team_the_ai` | 4 |
| `slo-meltdown` | 4 |

**Distinct inject sets: 9 across 9 packs.** No two packs share a set.

## How it was checked

Against `load_scenario()` — the same resolver the session seeder calls
(`tools/ttx/engine.py:42` `load_scenario(slug)` → `:56` `seed_injects(session_id,
scenario)`), so this exercises the real path rather than a re-implementation:

```python
from tools.ttx.scenario_loader import _SCENARIOS_DIR, load_scenario
slugs = sorted(p.name for p in _SCENARIOS_DIR.iterdir() if (p / "scenario.yaml").exists())
for s in slugs:
    print(s, len(load_scenario(s).get("injects") or []))
```

## Why the original claim was plausible

The task's own description already recorded that the tree contradicts it, and
that holds up:

- the picker is wired end to end — `hub.html` select → `{scenario_slug}` POST →
  `blueprint.py:318` `data.get("scenario_slug", SCENARIO_SLUG)` →
  `load_scenario(slug)` → `seed_injects(...)`;
- there is no fallback-to-default path — an unknown slug raises
  `FileNotFoundError` (`scenario_loader.py:31`) and surfaces as a 400.

The most likely explanation for the original observation is that the 9 sessions
were created without the slug reaching the POST body — leaving
`data.get("scenario_slug", SCENARIO_SLUG)` to fall back to the default for every
one of them. That is a session-creation path question, not a picker defect, and
it is not reproducible from the scenario data.

## Note on counts

The task predicted `ai_gameday 7, forge_ascent 6, grounding-red-team 6,
hunt_the_fleet 6, meridian 6, document-integrity 5, red_team_the_ai 5,
slo-meltdown 5`. Measured counts are uniformly one lower. The discrepancy does
not affect the verdict — the sets are distinct either way — but it means the
audit's per-pack numbers were counted by some other method and should not be
treated as authoritative.

## Recommendation

Close `fga-gd-03` as *not reproduced*. If the symptom is ever seen again, capture
the POST body of the session-create request first: the question is whether
`scenario_slug` arrives, not whether the packs differ.
