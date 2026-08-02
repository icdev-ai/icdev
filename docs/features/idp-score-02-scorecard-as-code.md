# CUI // SP-CTI

# idp-score-02 — Scorecard-as-Code: ladder + IQE rule expressions

**Epic:** IDP / SCORE — Readiness scoring over ICDEV's own component registry
**Status:** shipped

## Problem

cortex.io's scorecard model is the right shape for grading an estate of
components: a `ladder` of ranked `levels`, plus `rules` — each a boolean
expression carrying a `weight`, an optional `level`, an `identifier`, a
`failureMessage`, and an optional entity `filter` — plus `exemptions` and an
`evaluation.window`.

The obvious way to adapt it is also the wrong way: write a rule DSL. That would
mean a second expression language to parse, sandbox, document, and give a query
surface to — next to the one ICDEV already has.

## The adaptation: don't invent a DSL

**A rule is an IQE query.** `tools/iqe` already parses `foreach … where … select
…` against registered collections, is already used by the dashboard query
widget, `/ask-icdev`, and `python -m tools.iqe.run`, and already has per-canvas
adapters. So a rule reads:

```yaml
- identifier: e2e-spec
  level: Gold
  weight: 20
  expression: foreach c in idp.components where c.has_e2e_spec == true select c.key
```

and that exact text runs by hand:

```bash
python -m tools.iqe.run --query-string \
  'foreach c in idp.components where c.has_e2e_spec == true select c.key'
```

**The entities a rule's query returns are the entities that pass it.** The
evaluator rewrites each rule's SELECT to the scorecard's `entity_key`, so an
author may project whatever reads best by hand without changing what is scored.

Consequences that fall out for free: the rule language is already implemented,
already sandboxed (IQE evaluates a typed AST against adapter rows — no `eval`,
no SQL interpolation of user text), and already inspectable in the UI. A rule
that references a collection the scorecard does not declare is rejected at load
time rather than silently querying something else.

## Scoring vs. the ladder

This is the distinction that makes a ladder usable rather than one big
pass/fail, and it is preserved exactly:

- Every **applicable** rule contributes its `weight` to the entity's score.
- Only rules that declare a `level` **gate** ladder progression. An entity
  attains a level when it passes every applicable leveled rule at that rank
  *and* every rank below it — the walk stops at the first rung not fully met.
- A rule with **no `level`** still scores but can never hold an entity back.

That last point is what lets an aspirational check ship the day it is written.
`on-call-named` currently passes for 0 of 66 components; because it carries no
level, it moves the score without stalling all 66 behind it.

`filter` (also an IQE query) names the entities a rule applies to at all.
Outside the filter an entity is `not_applicable` and drops out of **both** the
score denominator and the ladder — a child app is not graded on the 8-point
canvas gate and is not held back by it.

`exemptions` waive one rule for one entity and credit their weight like a pass.
An `expires` date is optional; once past, the exemption stops applying on its
own rather than needing a cleanup pass.

## Configuration, not code

A scorecard is a YAML file in `args/scorecards/`. Adding a rule, a level, or a
whole new scorecard file requires **no Python change** — the same precedent as
adding a mirrored root to `args/mirror_parity.yaml`. Even the adapter import is
derived: collection `idp.components` → module `tools.iqe.adapters.idp`, override
with `adapter_module:` when a scorecard points somewhere else.

`tests/test_idp_scorecard.py::test_adding_a_rule_requires_no_python_change`
pins the contract by appending a rule to a YAML string and asserting the
outcome moves.

## The shipped scorecard

`args/scorecards/component-readiness.yaml`, over all 66 registry entries:

| Level | Rank | Gated on |
|-------|------|----------|
| Bronze | 1 | `blueprint-present` (filtered to components declaring a module), `rls-clean` |
| Silver | 2 | `iqe-adapter`, `iqe-seed-queries` |
| Gold | 3 | `e2e-spec`, `completeness-gate` (filtered to canvases) |
| Platinum | 4 | `has-owner`, `probes-healthy` (filtered to components actually probed) |

Scored but not gating: `nav-reachable`, `on-call-named`, `owner-reachable`.

Measured 2026-08-02 against the live DB:

```
Component Readiness (component-readiness) — 66 entities over idp.components

Ladder: unranked=0, Bronze=30, Silver=29, Gold=7, Platinum=0

  blueprint-present             42/42  w=15  (gates Bronze)
  rls-clean                     66/66  w=15  (gates Bronze)
  iqe-adapter                   53/66  w=10  (gates Silver)
  iqe-seed-queries              38/66  w=5   (gates Silver)
  e2e-spec                      16/66  w=20  (gates Gold)
  completeness-gate             35/36  w=20  (gates Gold)
  has-owner                      0/66  w=25  (gates Platinum)
  probes-healthy                63/66  w=15  (gates Platinum)
  nav-reachable                 49/66  w=5   (scores only)
  on-call-named                  0/66  w=5   (scores only)
  owner-reachable                0/66  w=5   (scores only)
```

Every one of the 66 is ranked; none is `unranked`. **E2E coverage at 16/66 is
the dominant driver** — it is the single reason 30 components sit at Bronze and
29 at Silver rather than Gold. `has-owner` at 0/66 is not a defect: idp-cat-01
established that baseline deliberately, since the repo has no CODEOWNERS or team
roster to backfill from and a wrong owner routes an incident to nobody while
reading as answered.

## Facts

`tools/iqe/adapters/idp.py` registers `idp.components` — one row per registry
entry, all kinds. The Python here only *produces facts*; it knows nothing about
ladders, weights, or levels.

Three fact-layer decisions are load-bearing:

**Ownership reads the scrubbed dataclass field, never raw YAML.** `Component`
normalizes `UNOWNED_SENTINELS` (`tbd`, `todo`, `unassigned`, …) to `None`, so
`owner: TBD` scores as unowned. Reading `raw` would grade a placeholder as a
real owner — the precise failure idp-cat-01 set out to prevent, and it would
have shown up as a component climbing to Platinum on a stub.

**"Never probed" is not "healthy."** `health_probed` is false when the probe
table is empty or unreachable, and the `probes-healthy` rule filters on it. A
component with no probe data is `not_applicable`, not passing. Without that
split, an unmonitored component would outrank a monitored one.

**The probe read is bounded.** `awareness_component_health` is append-only and
held 465k rows on 2026-08-02; only the newest cycle matters. The adapter reads a
newest-first window (`probe_type = 'http_head'`, `LIMIT 20000`) and keeps the
latest snapshot per node, so a route that has since recovered stops counting
against its component. Measured 0.3s, against a full-table read that would grow
with probe history forever. A failed read rolls the connection back so an
aborted PostgreSQL transaction does not make every later fact query report
"relation does not exist."

Fact rows are memoized per process — a scorecard runs one IQE query per rule
(plus one per filter), which would otherwise re-walk the tree 11 times for the
shipped card. `reset_cache()` drops it.

## CLI

```bash
python tools/idp/scorecard.py --list
python tools/idp/scorecard.py --scorecard component-readiness --json
python tools/idp/scorecard.py --component ndc
```

## Files

- `args/scorecards/component-readiness.yaml` — the shipped scorecard
- `tools/idp/scorecard.py` — schema, loader, IQE-backed evaluator, CLI
- `tools/iqe/adapters/idp.py` — the `idp.components` fact collection
- `icdev/tools/iqe/adapters/idp.py` — mirror (`tools/iqe/adapters` is a
  mirror-parity root; an unmirrored change there blocks every branch)
- `tools/canvas_health/health_data.py` — `_rls_violations` promoted to
  `rls_violation_keys()` so the scorecard grades the same keys the Canvas Health
  dashboard shows instead of re-deriving the mapping; the private name stays as
  an alias
- `tools/manifest/idp-scorecards.md`, `tools/manifest.md` — manifest shard + index
- `docs/reference/commands.md` — CLI reference
- `tests/test_idp_scorecard.py` — 19 tests

## Verification

```bash
pytest tests/test_idp_scorecard.py -q          # 19 passed
python tools/idp/scorecard.py --list
python tools/idp/scorecard.py                  # 66 entities, all ranked
```

Coverage beyond the headline contract: ladder contiguity (a component failing
Bronze while passing Platinum stays unranked); un-levelled rules scoring without
gating; `filter` removing an entity from both denominator and ladder; active vs.
expired exemptions; rejection of a duplicate identifier, an off-ladder level, a
cross-collection query, and an unparseable expression; that every fact a shipped
rule references is actually emitted by the adapter (a typo'd field is not a
parse error — it resolves to `None`, fails for all 66, and reads like a real
finding); and the three fact-layer decisions above.
