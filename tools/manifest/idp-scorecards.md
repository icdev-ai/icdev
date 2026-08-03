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
| Gap seeder | tools/idp/gap_seeder.py | Turns every `status == "fail"` outcome into one gated kanban task — one per (scorecard, component, rule). Description is the rule's `failureMessage`; acceptance criteria is the IQE query that measured the failure plus an instruction not to satisfy it by editing the rule. Public API: `load_config(path)`, `collect_gaps(conn, directory=, scorecard_key=) -> (gaps, keys)`, `gaps_from_report(scorecard, report)`, `filter_gaps(gaps, config)`, `prioritize(gaps)`, `apply_caps(gaps, per_component, per_run) -> (kept, truncation)`, `build_task_spec(gap, config)`, `gate_spec(id)`, `existing_idempotency_keys(conn, keys)`, `gate_state(conn, id)`, `seed(conn, dry_run=True, ...)`. Seeds through `task_factory.create_tasks`, never a raw INSERT. Raises `GapSeederError` on an unknown scorecard key or a `gate_task_id` that does not end in `-gate-00`. | `[--seed \| --dry-run] \| --scorecard <key> \| --dir <path> \| --config <path> \| --max-per-run N \| --max-per-component N \| --force \| --json` | JSON report or human table |
| IDP component facts | tools/iqe/adapters/idp.py | Registers the IQE collection `idp.components` — one row per entry in `args/component_registry.yaml` (all kinds), carrying the facts a rule can assert on: ownership (`has_owner`, `owner`, `owner_contact`, `on_call`, `has_owner_contact`), wiring (`has_blueprint`, `has_e2e_spec`, `has_iqe_adapter`, `has_seed_queries`, `has_nav`, `iqe_collections`), the 8-point gate (`completeness_declared`, `completeness_passed`, `completeness_points`), and live signals (`rls_clean`, `failing_probes`, `health_probed`). Memoized per process; call `reset_cache()` after changing registry or tree state. Mirrored to `icdev/tools/iqe/adapters/idp.py` (mirror-parity root). | (auto-registered) | list[dict] |

## Portal surface (Phase: idp-ui)

The dashboard page that renders the catalog and the scorecards, at `/idp`.
Registered entirely from `args/component_registry.yaml` — blueprint mount, nav
entry, CLI toggle, `/api/iqe/dispatch` mapping and the client-side
`PATH_CANVAS` regex are all derived, with no Python list edited in `app.py`,
`cli/enable.py` or `base.html`.

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Portal view models | tools/idp/portal.py | Joins `idp.components` facts with a scorecard evaluation into template-ready shapes. Public API: `component_facts(conn, refresh)`, `scorecard_report(key, conn)`, `build_catalog(facts, report)`, `group_by_kind(rows)`, `portal_overview(scorecard_key, conn, refresh)`, `component_detail(key)`, `completeness_points(key)`, `self_check()`, `schema_status()`. Every entry point degrades instead of raising — a malformed scorecard or an unreachable DB costs the page its grades, not its catalog. An ungraded component keeps `score=None`, never `0`. | (library) | dict / list[dict] |
| Portal blueprint | tools/idp/blueprint.py | `bp` — `/idp/`, `/idp/catalog`, `/idp/scorecards`, `/idp/component/<key>`, plus JSON `/idp/api/catalog`, `/idp/api/scorecard`, `/idp/api/component/<key>` and `POST /idp/api/iqe-query`. Declares no `url_prefix` of its own; the registry supplies it. | HTTP | HTML / JSON |
| Portal constants | tools/idp/constants.py | Default scorecard key, IQE wiring for the query widget, catalog columns, status→badge map. Deliberately holds no ladder or rule data — that lives in YAML. | (library) | constants |
| Schema dependency probe | tools/idp/db/init_db.py | The portal creates **no** tables. Reports which optional backing tables exist (`developer_scorecards`, `awareness_component_health`, `kg_edges`) in one catalog query, so an absent signal renders as "not measured" rather than as a passing zero. Uses `get_canvas_connection()` per the CLAUDE.md canvas-RLS rule. Public API: `schema_status(conn)`, `init_db()`, `OPTIONAL_TABLES`. | (library) | list[dict] |

Seed queries: `context/iqe/queries/idp/` (catalog overview, canvases failing the
8-point gate, unowned components, components with no E2E spec).
E2E: `tests/e2e/idp_portal.spec.ts`. Unit: `tests/test_idp_portal.py`.

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

## Closing the loop (Phase: idp-gap)

`gap_seeder.py` is the half a catalog product cannot ship: a failing rule
becomes a kanban task instead of a red cell someone has to notice. Config is
`args/idp_gap_seeder.yaml`.

| Knob | Effect |
|------|--------|
| `enabled` | Ships **false**. `--seed` is refused until it is flipped (or `--force` is passed), so the caps get proven by a dry run first. |
| `max_tasks_per_component` | Applied **first**, so one badly scoring component cannot consume the run budget and starve the estate. |
| `max_tasks_per_run` | Hard ceiling per run. Measured on the live board: 311 failing rules → 10 tasks. |
| `only_gating_rules`, `include_rules`, `exclude_rules` | Rule selection. Prefer a scorecard `exemption` when the intent is "this is accepted" — that credits the weight too. |
| `gate_task_id` | Must end in `-gate-00` or `gate_state()` raises; otherwise the kanban sweeps treat the sentinel as work and complete it. |
| `status`, `priority_by_level`, `default_priority` | `critical` is clamped to `high` in code — a critical card is auto-promoted out of `suggested` by the deadlock-breaker. |

Three invariants, each pinned by `tests/test_idp_gap_seeder.py`:

* **One task per failing rule per component.** `pass`, `exempt` and
  `not_applicable` never seed — an exemption is a decision someone already made.
* **Re-running seeds nothing.** Idempotency key
  `idp-gap:<scorecard>:<component>:<rule>`, stable across runs. Already-seeded
  gaps are filtered out *before* the cap so the first N are not re-offered
  forever while N+1 never lands. The trade: a closed gap that regresses does not
  reseed under its old key — `idp_scorecard_history` is where that shows up.
* **Nothing dispatches without confirmation.** `suggested` **and**
  `depends_on_task_id` → a held `*-gate-00`. Only the dependency edge is enforced
  in code (`_deps_satisfied`); `suggested` alone can be promoted out by the
  deadlock-breaker. Seeding is refused if the gate has already been released.

There is deliberately **no scheduled reflex**. The seeder is CLI-only until an
operator has run a dry run and flipped `enabled` — an autonomous writer behind a
disabled config would be dead weight, and one behind an enabled config is a
decision to make on purpose.
