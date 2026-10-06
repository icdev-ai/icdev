# aicur-fix-04 — League scenario selection and the hollow `interagency` pack

## 1. The League played cyber scenarios only

`GameMaster.run_tournament` cycled `CYBER_SCENARIOS` (5 scenarios). The 14 AI-ops
League scenarios in `tools/gameday/constants.py` (`AI_OPS_SCENARIOS`: ace, rg, gc,
dr, cx, di, nt, of, fz, gr) were registered and covered by tests, but no round ever
played one.

**Change.** `game_master.select_scenarios()` builds the round list from
`tools/gameday/scenarios.ALL_SCENARIO_PACKS`, filtered by `scenario_selection.packs`
in `args/gameday_teams.yaml` (`all` by default, or a list of pack names). Packs are
interleaved round-robin, so a 5-round tournament plays five different packs. An
unknown pack name or an empty selection raises `ValueError`, which `run_tournament`
records as `status='aborted'`. A new tournament row records `scenario_pack` as the
single selected pack, or `mixed`.

The AI-ops scenarios key their briefs by team colour (`red_brief` …), while
`team_runner._build_prompt` reads the cyber keys (`attack_brief` …) and fell back to
the generic `description`. Selection copies each scenario and adds the cyber keys,
so each team gets its own brief. The registered constants are left unchanged.

## 2. `scenarios/interagency` referenced files that did not exist

`scenario.yaml` referenced 5 inject bodies, 5 rubrics and 6 persona templates, and
none of them existed. `tools/ttx/scenario_loader.load_scenario` skips a missing file
without an error. So the pack showed up in discovery and loaded, but every inject
had no body and no rubric, and `ai_scorer` marked every response `unscored`.

**Decision: author the pack, do not remove it.** The scenario was already fully
designed: roles, timing, scoring, consequences and ribbons. The ontology bridge
(`tools/ai_game_engine/ontology.py`) also maps it. Removing it from discovery would
have thrown that work away to save five files. The pack now ships:

- `injects/inject-0{1..5}-*.yaml`: attribution crisis, data-fusion failure,
  cross-domain bridge build sprint, joint COA, interoperability governance. All
  content is fictional.
- `rubrics/*.yaml`: one per inject, in the canonical dimensions-list format, with
  weights summing to 1.0.
- `personas/*.yaml`: one per role.

The same check found 5 more missing persona templates in `forge_ascent`,
`hunt_the_fleet` and `meridian`. These are authored too.

**Guard.** `tests/test_gameday_scenario_selection.py::test_every_referenced_pack_file_exists`
fails if any pack under `scenarios/` references a file that does not exist. It
closes this whole class of defect, not just this one pack.

**Not changed:** the loader still skips a missing file without an error, because
runtime behaviour for operator-authored packs is outside this card's scope. Also,
`body_variants` and `consequence.affects_inject` are consumed only by the
per-scenario simulators (`tools/ttx/scenarios/sim_*.py`), not by the generic TTX
engine. `interagency`'s `fusion_achieved` consequence is therefore declarative,
just like every other pack's.
