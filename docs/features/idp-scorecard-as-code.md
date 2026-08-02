# CUI // SP-CTI

# IDP — Scorecard-as-code: ladder + IQE rule expressions

**Task:** `idp-score-02` · **Project:** IDP — Internal Developer Portal

## What shipped

A scorecard is now a YAML file. `args/scorecards/component_readiness.yaml`
declares a three-rung ladder and eight rules over ICDEV's own 66 registered
components, and `python -m tools.idp.scorecard` grades every one of them.

```
Component Readiness (component-readiness) — 66 entities, 8 rules (6 gating)

Level distribution:
  Unrated      66
  Bronze        0
  Silver        0
  Gold          0

Average score: 46.2
```

That distribution is the finding, not a bug. Every component fails the Bronze
`has-owner` rule because **0 of 66 components declare an owner** — the measured
baseline `idp-cat-01` recorded when it added the ownership fields. The
scorecard's first job was to make that visible on a ladder instead of in a
paragraph, and it does.

## The adaptation: no new DSL

cortex.io's scorecard-as-code pairs a `ladder` of ranked `levels` with `rules`
written in a bespoke boolean expression language. Reimplementing that language
would have meant a new parser, a new sandbox and a new thing to learn.

ICDEV already has an expression language: **IQE**, with per-canvas adapters and
collections registered in the component registry's `iqe:` blocks. So a rule's
`expression` is an ordinary IQE query that returns the components which PASS:

```yaml
- identifier: has-owner
  title: Declares an accountable owner
  level: Bronze
  weight: 3
  expression: foreach c in idp.components where c.owned == true select c.key
  failure_message: >-
    No accountable owner. Add owner / owner_contact / on_call to this
    component's entry in args/component_registry.yaml.
```

The engine runs the query, reads the entity key out of each returned row, and
everything in that set passed. Everything in scope but absent failed. That is
the whole evaluation model — and the rule language is already implemented,
already sandboxed, and already has a query surface in the UI.

## Configuration, not code

Adding a rule is a YAML edit. There is no dispatch table, no base class and no
registration step, following the precedent of `args/mirror_parity.yaml`, where
adding a mirrored root needs no Python change.

`tests/test_idp_scorecard.py::test_a_rule_added_only_in_yaml_is_evaluated`
holds that line: it writes a scorecard file containing a rule that exists
nowhere in the source tree, evaluates it, and asserts the new rule's weight
moved the score.

## The ladder, and why some rules do not gate it

A component attains the highest level *L* such that every applicable,
non-exempt gating rule at every rank up to and including *L* passes.

| Level | Rank | Gating rules |
|-------|------|--------------|
| Bronze | 1 | `has-owner`, `coherence-clean` |
| Silver | 2 | `has-iqe-adapter`, `has-e2e-spec` |
| Gold | 3 | `completeness-gate`, `no-failing-health-probes` |

Two rules — `owner-is-reachable` and `has-on-call` — carry **no** `level`. They
contribute weight to the score and never block the ladder.

Preserving that distinction is what makes a ladder usable rather than a single
pass/fail. With one bar, every weak signal has to become a blocker or be
deleted. "Does the owner have a contact handle?" is worth points; it is not
worth holding a component off Bronze.

Ranks with no applicable rules are vacuously satisfied — a component is never
blocked by a level it cannot be measured on.

## Applicability is not failure

Each rule may carry a `filter`, a second IQE query naming the components it
applies to. A component outside it is reported `not_applicable`: it neither
passes nor fails, and the rule's weight is excluded from its score.

This is load-bearing rather than decorative. The 8-point completeness gate is
written for dashboard canvases. Without the filter, 30 features and core
extensions would fail a gate that was never about them, and the board would go
red in a way that carries no information. In the shipped run:

| Rule | pass | fail | not_applicable |
|------|-----:|-----:|---------------:|
| `has-owner` | 0 | 66 | 0 |
| `coherence-clean` | 66 | 0 | 0 |
| `has-iqe-adapter` | 33 | 3 | 30 |
| `has-e2e-spec` | 9 | 27 | 30 |
| `completeness-gate` | 35 | 1 | 30 |
| `no-failing-health-probes` | 1 | 10 | 55 |

## What the rules measure

Every fact comes from something the platform already produces. Nothing here is
aspirational:

| Fact | Source |
|------|--------|
| ownership | `args/component_registry.yaml` owner / owner_contact / on_call (idp-cat-01) |
| IQE adapter | the registry's own `iqe:` block |
| E2E spec | `tests/e2e/*.spec.ts`, matched on the component key or its package |
| completeness | `component_registry.validate_canvas_completeness` — the 8-point gate |
| coherence | `coherence_checker.check_canvas_rls_bypass` |
| health probes | `awareness_component_health`, joined by longest-matching `url_prefix` |

Each source degrades independently: a missing table or an un-migrated database
makes that one fact absent (`health_probed = false`), never an exception. A
scorecard that refuses to compute because one signal is unavailable is worse
than one that reports the signal as missing.

## The evaluation window

`evaluation.window` (`90d`, `12w`, `48h`, or a bare day count) bounds
time-series evidence. It is applied **once**, when the catalog snapshot is
fetched, as an IQE parameterised collection call (`idp.components(90)`).

That is why no rule mentions the window, and it also fixes a real hazard: the
adapter behind `idp.components` runs a coherence check and a probe query, so
grading each rule against its own fetch would mean eight rebuilds per run and
eight chances for two rules to disagree because the catalog moved between them.

Rules are evaluated on a private `Executor` bound to that snapshot, so a
scorecard run never mutates or races the global IQE collection registry.

## Exemptions

An exemption removes one (rule, component) pair from scoring **and** from
ladder gating. An `expires` date in the past is ignored, so an exemption nobody
renews lapses on its own rather than silently persisting.

Approval workflow and audit logging for exemptions are `idp-score-04`; the
schema and the arithmetic are here so exemptions written now keep working when
that lands.

## Failing loudly

A malformed scorecard raises `ScorecardError` when it is loaded, not when it
grades: unknown ladder level, duplicate rank or level name, negative weight,
duplicate rule identifier, an exemption for a rule that does not exist, or a
rule whose query does not project the entity key. A scorecard that grades
wrongly is worse than one that refuses to load.

## Files

| File | Role |
|------|------|
| `args/scorecards/component_readiness.yaml` | The shipped scorecard — ladder, 8 rules, filters, exemptions |
| `tools/idp/scorecard.py` | Spec loader, validator, evaluator, CLI, MCP entrypoint |
| `tools/idp/component_facts.py` | One fact row per component — the `idp.components` collection |
| `tools/iqe/adapters/idp.py` | Registers `idp.components` (mirrored to `icdev/`) |
| `tests/test_idp_scorecard.py` | 34 tests — ladder, filters, exemptions, validation, live registry |
| `tools/manifest/idp-scorecard.md` | Manifest shard |

## Out of scope

Persisting to `developer_scorecards` is `idp-score-01`/`-03`; score history is
`idp-score-03`; turning a failing rule into a kanban task is `idp-gap-01`; the
portal surface is `idp-ui-01`. This task computes the grade and returns it.
