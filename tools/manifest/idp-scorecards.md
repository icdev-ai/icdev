# IDP — Scorecard-as-Code

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Scorecard-as-code (Phase: idp-score)

Grades ICDEV's own component registry against a **ladder** of ranked levels
using rules written as **IQE queries**. The rule language is not new — it is
`tools/iqe`, the same DSL the dashboard query widget and `python -m tools.iqe.run`
speak, so every rule in a scorecard can be pasted into either one and run by hand.

Scorecards live in `args/scorecards/*.yaml`. **Adding a rule, a level, or a whole
new scorecard is a YAML edit — no Python change.** That is the contract, and
`tests/test_idp_scorecard.py::test_adding_a_rule_requires_no_python_change`
pins it.

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Scorecard evaluator | tools/idp/scorecard.py | Loads `args/scorecards/*.yaml`, runs one IQE query per rule (plus one per `filter`) over the declared collection, and assigns every entity a weighted score and a ladder level. Public API: `load_scorecards(dir)`, `load_scorecard(key, dir)`, `parse_scorecard(mapping)`, `evaluate(scorecard, conn) -> report dict`, `evaluate_all(dir, conn)`. Raises `ScorecardError` on a malformed file, an unknown level, a duplicate rule identifier, an unparseable expression, or a rule that reads a collection the scorecard does not declare. | `--list \| --scorecard <key> \| --component <key> \| --dir <path> \| --json` | JSON report or human table |
| Score history | tools/idp/score_history.py | Persists one `idp_scorecard_history` row per component per evaluation, carrying the attained ladder level so level CHANGES are detectable and not just score drift, and reads the series back. Public API: `persist_evaluation(report, conn)`, `record_scorecard(card, conn, if_due=)`, `record_all(conn, if_due=)`, `get_score_trend(component, scorecard_key, conn)`, `get_level_changes(scorecard_key, conn)`, `is_due(card, conn)`, `parse_window(str)`, `window_start(dt, seconds)`. `window_start` is `evaluated_at` floored to the scorecard's `evaluation.window`, anchored on the epoch, so two processes agree on the bucket without coordinating; `if_due=True` skips a bucket already recorded. Raises `ScoreHistoryError` on an unparseable window. | `--record [--if-due] \| --trend <component> \| --level-changes [--since] \| --scorecard <key> \| --dir <path> \| --limit N \| --json` | JSON or human table |
| Score recorder reflex | tools/genesis/reflexes/idp_score_recorder.py | GREEN-tier Genesis reflex on the awareness 3h cadence (`args/genesis_config.yaml: reflexes.idp_score_recorder`) that calls `record_all(if_due=True)`. Awareness refreshes `awareness_component_health`, the source of the `probes-healthy` facts, so a point lands against fresh probe data. `metric_value` is rows written; a cycle that correctly skips an already-recorded window reports 0 and still succeeds (`gte 0`). | reflex config (`if_due`, `scorecard_dir`) | `{success, metric_value, details}` |
| IDP component facts | tools/iqe/adapters/idp.py | Registers the IQE collection `idp.components` — one row per entry in `args/component_registry.yaml` (all kinds), carrying the facts a rule can assert on: ownership (`has_owner`, `owner`, `owner_contact`, `on_call`, `has_owner_contact`), wiring (`has_blueprint`, `has_e2e_spec`, `has_iqe_adapter`, `has_seed_queries`, `has_nav`, `iqe_collections`), the 8-point gate (`completeness_declared`, `completeness_passed`, `completeness_points`), and live signals (`rls_clean`, `failing_probes`, `health_probed`). Memoized per process; call `reset_cache()` after changing registry or tree state. Mirrored to `icdev/tools/iqe/adapters/idp.py` (mirror-parity root). | (auto-registered) | list[dict] |

### Scorecard schema (`args/scorecards/<key>.yaml`)

| Key | Meaning |
|-----|---------|
| `key`, `name`, `description` | Identity. `key` is required and is what `--scorecard` matches. |
| `collection` | The IQE collection every rule must read. A rule naming a different one is rejected. |
| `entity_key` | Field identifying an entity (default `key`). The evaluator rewrites each rule's SELECT to this, so a rule may project whatever reads best by hand. |
| `evaluation.window` | How long an evaluation stays fresh. Recorded on the report; score history consumes it. |
| `ladder.levels[]` | `name`, `rank` (higher is better, must be distinct), `description`, `color`. |
| `rules[]` | `identifier` (unique), `expression` (an IQE query — the entities it returns are the entities that pass), `weight`, optional `level`, `title`, `failureMessage`, and optional `filter` (also an IQE query) naming the entities the rule applies to at all. |
| `exemptions[]` | `identifier` + `entity` + `reason` + optional `expires` (an expired exemption stops applying on its own). An exemption credits its weight like a pass. |

### Scoring vs. the ladder

Every **applicable** rule contributes its `weight` to the entity's score. Only
rules that declare a `level` gate ladder progression: an entity attains a level
when it passes every applicable leveled rule at that rank *and* every rank
below. A rule with no `level` still scores but can never hold an entity back —
that distinction is what makes this a ladder rather than one big pass/fail, and
it is what lets an aspirational check ship immediately instead of stalling every
component behind it. Entities outside a rule's `filter` are `not_applicable` and
drop out of both the score denominator and the ladder.

### Shipped scorecard

`args/scorecards/component-readiness.yaml` — Bronze (registered, routable,
RLS-clean) < Silver (queryable: IQE adapter + seed queries) < Gold (proven: E2E
spec + 8-point gate) < Platinum (owned + probes healthy), plus three scored-only
rules. Every fact it asserts on is measurable today.
