# IDP — Scorecard-as-code (ladder + IQE rule expressions)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

Grades every component in `args/component_registry.yaml` against a ladder of
ranked levels. Adapted from cortex.io's scorecard-as-code model, with one
deliberate substitution: **ICDEV does not get a scorecard DSL.** cortex.io's
rules are written in a bespoke expression language; ICDEV already has one —
IQE — so a rule's `expression` is an ordinary IQE query returning the entities
that PASS it. The rule language is therefore already implemented, already
sandboxed, and already has a query surface in the UI.

A scorecard lives entirely in `args/scorecards/<key>.yaml`. **Adding a rule is
a YAML edit; there is no Python change and no dispatch table.** The precedent
is `args/mirror_parity.yaml`, where adding a mirrored root needs no code.

## Tools
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Scorecard Engine | tools/idp/scorecard.py | Loads and validates `args/scorecards/*.yaml`, executes each rule's IQE expression against a single catalog snapshot, and assigns every entity a ladder level plus a weighted score. Malformed scorecards raise `ScorecardError` at load (unknown level, duplicate rank, negative weight, exemption for a nonexistent rule) rather than grading wrong. Public API: `load_scorecard`, `find_scorecard`, `load_all_scorecards`, `evaluate_scorecard(card, catalog=...)`, `evaluate_named`. | `--list`, `--scorecard KEY`, `--failures`, `--json`, `--dir DIR` | Ladder distribution + per-component rule outcomes |
| Component Facts | tools/idp/component_facts.py | Builds one flat fact row per registered component — the `idp.components` collection. Sources: registry ownership (idp-cat-01), the registry `iqe:` block, `tests/e2e/*.spec.ts`, the 8-point completeness gate, the canvas RLS coherence check, and `awareness_component_health` probes joined by longest-matching `url_prefix`. Every source degrades to "absent" rather than raising. Library: `build_component_facts(window_days, conn, registry, repo_root)`, `parse_window`. | library | `list[dict]` |
| IQE Adapter | tools/iqe/adapters/idp.py | Registers the `idp.components` collection. Mirrored to `icdev/tools/iqe/adapters/idp.py` (`args/mirror_parity.yaml`). Accepts an optional window argument: `idp.components(90)`. | library (import to register) | collection rows |

MCP tool: `idp_scorecard` — params `scorecard` (string), `failures_only` (bool).

## Adding a rule
1. append an entry under `rules:` in `args/scorecards/component_readiness.yaml`
2. write `expression` as an IQE query returning the components that PASS, and
   project the entity key: `select c.key`
3. `python -m tools.idp.scorecard --scorecard component-readiness --failures`

There is no step 4.

## level vs no level — why the distinction matters
A rule **with** a `level` gates ladder progression: fail it and the component
cannot reach that level or any level above. A rule **without** a `level` still
contributes its weight to the score but never blocks the ladder.

Dropping that distinction collapses a scorecard back into a single pass/fail,
which is the failure mode a ladder exists to avoid: with one bar, every weak
signal either becomes a blocker or gets deleted. "Does the owner have a contact
handle?" is worth points and is not worth holding a component off Bronze.

## filter — applicability is not failure
`filter` is a second IQE query returning the components a rule APPLIES to. A
component outside it is `not_applicable`: it neither passes nor fails, and the
rule's weight is left out of its score.

This is load-bearing. The 8-point completeness gate is written for dashboard
canvases; without a filter, 30 features and core extensions would fail a gate
that was never about them, and the resulting red board would say nothing.

## Fields available to rules
Produced by `build_component_facts`, one row per component:

`key`, `kind`, `display_name`, `enabled`, `url_prefix`, `module`,
`owned`, `owner`, `owner_contact`, `on_call`, `has_owner_contact`, `has_on_call`,
`has_iqe_adapter`, `iqe_collections`,
`has_e2e_spec`, `e2e_specs`,
`completeness_checked`, `completeness_passed`, `completeness_present`,
`completeness_required`,
`coherence_clean`,
`health_probed`, `health_probes`, `health_probe_failures`.

## The evaluation window
`evaluation.window` (`90d`, `12w`, `48h`, or a bare day count) bounds the
time-series evidence — health probes older than the window are not counted. It
is applied once, when the catalog snapshot is fetched, as an IQE parameterised
collection call. That is why no rule mentions it: every rule grades the same
windowed snapshot, so two rules can never disagree because the catalog moved
between them.

## Exemptions
An exemption drops one (rule, component) pair from scoring **and** from ladder
gating. An `expires` date in the past is ignored, so an exemption nobody renews
lapses on its own. Approval workflow and audit logging are `idp-score-04`; the
schema is here so exemptions written now keep working when that lands.

## What this does not do
It does not write `developer_scorecards` — persistence is `idp-score-01`/`-03`,
and score history is `idp-score-03`. Turning a failing rule into a kanban task
is `idp-gap-01`. This module computes the grade and returns it.
