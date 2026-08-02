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
| Scorecard evaluator | tools/idp/scorecard.py | Loads `args/scorecards/*.yaml`, runs one IQE query per rule (plus one per `filter`) over the declared collection, and assigns every entity a weighted score, a per-dimension breakdown, an A–F letter grade and a ladder level. An entity with no applicable rule scores `None` (unassessed), never `0`. Public API: `load_scorecards(dir)`, `load_scorecard(key, dir)`, `parse_scorecard(mapping)`, `evaluate(scorecard, conn) -> report dict`, `evaluate_all(dir, conn)`, `Scorecard.letter_grade(score)`, `Scorecard.dimension_order()`. Raises `ScorecardError` on a malformed file, an unknown level, a duplicate rule identifier, an unparseable expression, or a rule that reads a collection the scorecard does not declare. | `--list \| --scorecard <key> \| --component <key> \| --dir <path> \| --json` | JSON report or human table |
| IDP component facts | tools/iqe/adapters/idp.py | Registers the IQE collection `idp.components` — one row per entry in `args/component_registry.yaml` (all kinds), carrying the facts a rule can assert on: ownership (`has_owner`, `owner`, `owner_contact`, `on_call`, `has_owner_contact`), wiring (`has_blueprint`, `has_e2e_spec`, `has_iqe_adapter`, `has_seed_queries`, `has_nav`, `iqe_collections`), the 8-point gate (`completeness_declared`, `completeness_passed`, `completeness_points`), and live signals (`rls_clean`, `failing_probes`, `probed_routes`, `health_probed` — the last two are **per component**, so a component whose own routes have no probe row reads as unmeasured rather than healthy). Also exposes `probe_evidence(route, conn)`, the raw probe rows behind `failing_probes`. Memoized per process; call `reset_cache()` after changing registry or tree state. Mirrored to `icdev/tools/iqe/adapters/idp.py` (mirror-parity root). | (auto-registered) | list[dict] |

## Portal surface (Phase: idp-ui)

The dashboard page that renders the catalog and the scorecards, at `/idp`.
Registered entirely from `args/component_registry.yaml` — blueprint mount, nav
entry, CLI toggle, `/api/iqe/dispatch` mapping and the client-side
`PATH_CANVAS` regex are all derived, with no Python list edited in `app.py`,
`cli/enable.py` or `base.html`.

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Portal view models | tools/idp/portal.py | Joins `idp.components` facts with a scorecard evaluation into template-ready shapes. Public API: `component_facts(conn, refresh)`, `scorecard_report(key, conn)`, `build_catalog(facts, report)`, `group_by_kind(rows)`, `portal_overview(scorecard_key, conn, refresh)`, `component_detail(key)`, `completeness_points(key)`, `rule_evidence(component, identifier, scorecard_key, conn)`, `self_check()`, `schema_status()`. Every entry point degrades instead of raising — a malformed scorecard or an unreachable DB costs the page its grades, not its catalog. An ungraded component keeps `score=None` and `letter_grade=None`, never `0`/`F`; `assessed` is the flag templates branch on. | (library) | dict / list[dict] |
| Portal blueprint | tools/idp/blueprint.py | `bp` — `/idp/`, `/idp/catalog`, `/idp/scorecards`, `/idp/component/<key>`, `/idp/evidence?component=<key>&rule=<id>`, plus JSON `/idp/api/catalog`, `/idp/api/scorecard`, `/idp/api/component/<key>`, `/idp/api/evidence` and `POST /idp/api/iqe-query`. Declares no `url_prefix` of its own; the registry supplies it. | HTTP | HTML / JSON |
| Portal constants | tools/idp/constants.py | Default scorecard key, IQE wiring for the query widget, catalog columns, status→badge map, letter-grade→badge map, the `Not assessed` label. Deliberately holds no ladder or rule data — that lives in YAML. | (library) | constants |
| Schema dependency probe | tools/idp/db/init_db.py | The portal creates **no** tables. Reports which optional backing tables exist (`developer_scorecards`, `awareness_component_health`, `kg_edges`) in one catalog query, so an absent signal renders as "not measured" rather than as a passing zero. Uses `get_canvas_connection()` per the CLAUDE.md canvas-RLS rule. Public API: `schema_status(conn)`, `init_db()`, `OPTIONAL_TABLES`. | (library) | list[dict] |

Seed queries: `context/iqe/queries/idp/` (catalog overview, canvases failing the
8-point gate, unowned components, components with no E2E spec).
E2E: `tests/e2e/idp_portal.spec.ts`. Unit: `tests/test_idp_portal.py`,
`tests/test_idp_catalog_scorecards.py` (catalog/grade/evidence contract).

### Scorecard schema (`args/scorecards/<key>.yaml`)

| Key | Meaning |
|-----|---------|
| `key`, `name`, `description` | Identity. `key` is required and is what `--scorecard` matches. |
| `collection` | The IQE collection every rule must read. A rule naming a different one is rejected. |
| `entity_key` | Field identifying an entity (default `key`). The evaluator rewrites each rule's SELECT to this, so a rule may project whatever reads best by hand. |
| `evaluation.window` | How long an evaluation stays fresh. Recorded on the report; score history consumes it. |
| `ladder.levels[]` | `name`, `rank` (higher is better, must be distinct), `description`, `color`. |
| `dimensions[]` | `key`, `label`, `description`, `column` (the `developer_scorecards` column a persisted score lands in). Defaults to the five columns that table already has. `unassigned` is reserved. |
| `grading.bands[]` | `letter` + `min`, highest first. Bands the overall and per-dimension scores into an A–F grade. An unassessed score gets **no** letter. |
| `rules[]` | `identifier` (unique), `expression` (an IQE query — the entities it returns are the entities that pass), `weight`, optional `level`, `dimension`, `title`, `failureMessage`, `evidence` (prose naming the source), and optional `filter` (also an IQE query) naming the entities the rule applies to at all. |
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

### Dimensions, grades and evidence (idp-ui-01)

Each rule declares a `dimension`; each dimension is scored the same way the
overall score is (earned weight over applicable weight) and banded to its own
letter. This is what lets the catalog show *why* a component scores what it
does instead of one opaque number. A rule that declares no dimension lands in a
synthetic `unassigned` bucket rather than being dropped, so an unclassified
rule is visible instead of silently uncounted.

**Unassessed is not zero.** An entity — or a dimension — with no applicable
rule has `score=None`, `letter_grade=None` and `assessed=False`. It renders as
"Not assessed" in a neutral badge, never as `0%` or `F`. `0%` means measured
and failing; `None` means nothing measured it, and collapsing the two asserts a
finding the platform does not have.

**Every verdict is traceable.** Each `RuleOutcome` carries an `Evidence` block:
the IQE expression, its filter, the collection and adapter module, the fact
fields the predicate reads, and this entity's observed value for each. The
`/idp/evidence` page resolves that per rule and **re-runs the query live**
rather than replaying a stored verdict, attaching the raw
`awareness_component_health` rows for probe-derived facts and the per-point
breakdown for the 8-point gate. An empty source set reports `measured: false` —
absent evidence is reported absent, not summarised as a pass.
