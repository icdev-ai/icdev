# CUI // SP-CTI

# IDP — The portal surface: catalog and scorecards (idp-ui-01)

The user-facing half of the Internal Developer Portal. Lists every registered
ICDEV component with its owner, ladder level and letter grade, breaks each
score into per-dimension scores, and links every one of those to the evidence
that produced it.

Builds directly on what was already live rather than rebuilding it:
`idp.components` (the IQE collection, idp-cat-01), `tools/idp/scorecard.py`
(scorecard-as-code, idp-score-02) and the `/idp` page (idp-ui-02).

## The one rule this feature exists to enforce

> Show the evidence, not just the grade.

A score with no traceable source is an assertion. The platform's own TRUST
posture says an untraceable claim is not a claim, and a portal that grades 67
components on numbers nobody can re-derive would be exactly the thing it is
supposed to catch. So:

* every rule outcome carries the query, the fact fields, and the observed value
* every dimension links to the outcomes behind its score
* `/idp/evidence` **re-runs the rule live** instead of replaying a verdict
* an absent signal is reported absent, never summarised as a pass

## What shipped

### 1. Dimensions

Rules declare a `dimension`. Each dimension scores like the overall score does
— earned weight over applicable weight — so a component's grade decomposes
instead of arriving as one opaque number.

The default dimension keys are the five score columns `developer_scorecards`
**already has**, so a persisted history (idp-score-03) has somewhere to write
each one without a schema change:

| Dimension | `developer_scorecards` column | Rules in the shipped card |
|-----------|-------------------------------|---------------------------|
| `code_quality` | `code_quality_score` | blueprint-present, iqe-adapter, iqe-seed-queries |
| `security` | `security_score` | rls-clean |
| `compliance` | `compliance_score` | completeness-gate, probes-healthy |
| `test_coverage` | `test_coverage_score` | e2e-spec |
| `velocity` | `velocity_score` | has-owner, nav-reachable, on-call-named, owner-reachable |

A rule that declares no dimension lands in a synthetic `unassigned` bucket
rather than being dropped — an unclassified rule should be visible, not
silently uncounted. The bucket only appears when something is in it.

Adding or re-banding a dimension is a YAML edit. No Python changes.

### 2. Letter grades

`grading.bands` in the scorecard YAML bands a score to a letter (default
A≥90, B≥80, C≥70, D≥60, F≥0). Applied to the overall score and to each
dimension independently.

### 3. Unassessed ≠ failing

The load-bearing distinction. Previously `score = earned / total` fell back to
`0.0` when `total == 0`, which on a page is indistinguishable from a component
that was measured and failed everything.

Now an entity or dimension with no applicable rule has:

```
score        = None
letter_grade = None
assessed     = False
```

and renders as **"Not assessed"** in a neutral badge — never `0%`, never `F`,
never a red cell. `_score_of()` is the single place that decision is made.

The mirror image is pinned too: `test_a_measured_zero_stays_a_zero` asserts
that failing everything still reads as `0%` and `F`. "Never score zero" would
otherwise be satisfiable by never scoring zero.

### 4. Evidence

Every `RuleOutcome` carries an `Evidence` block:

| Field | Meaning |
|-------|---------|
| `expression` | The IQE query the evaluator ran, paste-able into the query widget |
| `filter_expression` | The query naming which entities the rule applies to |
| `collection`, `adapter_module` | Where the facts came from |
| `fields` | Fact fields the predicate reads, extracted from the parsed AST |
| `observed` | This entity's value for each of those fields |
| `note` | Prose from the rule's `evidence:` key naming the underlying source |

`fields` is derived by walking the WHERE clause for `AttrRef` nodes, so it
tracks the query rather than a hand-maintained list. A field the collection
does not expose is **absent** from `observed` rather than rendered as null —
null reads as "the value is empty", absent reads as "this was not read".

`GET /idp/evidence?component=<key>&rule=<id>` resolves one verdict end to end
and additionally attaches:

* the raw `awareness_component_health` rows for probe-derived facts
* the per-point 8-point-gate breakdown for completeness facts

Each source carries `measured`, which tracks whether any rows came back. An
empty probe set reports **"not measured"**, not "0 failures".

### 5. The catalog

`/idp` lists all 67 registered components, grouped by kind, with columns:

```
Component | Owner | Level | Grade | Score | Code Quality | Security |
Compliance | Test Coverage | Velocity | Kind | Route | Blueprint |
IQE | E2E | RLS | Failing rules
```

Every row has one cell per declared dimension, in declared order, whether or
not that component was assessed on it — a row that omitted an unassessed
dimension would shift the remaining cells and misalign the headers. Each
assessed cell links to that dimension's section on the component page.

Sort order is level, then score, then key, with unassessed **below** a measured
`0%`: a real zero is actionable, a `None` is not.

## Bug found and fixed: never-probed components read as healthy

`probes-healthy` gates Platinum on `failing_probes == 0`, filtered to
`health_probed == true`. `health_probed` was a **single platform-wide boolean**
— true the moment any one route anywhere had a probe row. An unprobed route
also contributes no failures, so `failing_probes` was 0 as well.

Result: every component whose routes had never been probed **passed** a health
check nothing had ever run against it.

Measured on the live PostgreSQL board, 2026-08-02: `awareness_component_health`
holds 49,901 `http_head` rows across 188 distinct routes, but only **7 of 67**
components have any probe row under their own `url_prefix`.
`/document-intelligence` had zero and still scored a pass.

`_latest_failing_routes()` now returns `(failing_routes, probed_routes)` as two
route sets instead of `(failing_routes, bool)`, and `health_probed` /
`probed_routes` are computed per component from the second. The 60 unprobed
components now read `not_applicable` on that rule and contribute nothing to
their compliance dimension — which is the honest answer.

Grade distribution before and after, same data:

| | A | B | C | D | F |
|---|---|---|---|---|---|
| Before (unprobed credited) | 0 | 0 | 7 | 23 | 37 |
| After (unprobed excluded) | 0 | 0 | 7 | 5 | 55 |

Pinned by `test_health_probed_is_per_component_not_platform_wide`.

## Routes

| Route | Purpose |
|-------|---------|
| `GET /idp/evidence?component=&rule=` | Why one rule landed as it did, re-derived live |
| `GET /idp/api/evidence?component=&rule=` | Same, JSON |

Existing `/idp/`, `/idp/catalog`, `/idp/scorecards`, `/idp/component/<key>` and
the four JSON endpoints are unchanged in shape; their payloads gained
`letter_grade`, `assessed`, `dimensions`, `grade_bands` and
`grade_distribution`.

## Files

| File | Change |
|------|--------|
| `tools/idp/scorecard.py` | `Dimension`, `Evidence`, `DimensionResult`; `_score_of`, `_fact_rows`, `_predicate_fields`, `_relative_source`; letter grades; `score: float \| None` |
| `tools/idp/portal.py` | `rule_evidence()`, `_dimension_cell()`; grade/dimension/evidence on catalog rows and detail |
| `tools/idp/blueprint.py` | `/idp/evidence`, `/idp/api/evidence` |
| `tools/idp/constants.py` | `GRADE_BADGE`, `UNASSESSED_BADGE`, `UNASSESSED_LABEL` |
| `tools/iqe/adapters/idp.py` | `probe_evidence()`, `_routes_under()`; per-component `health_probed` |
| `tools/dashboard/templates/idp/page.html` | Owner/Grade/dimension columns, grade strip, per-rule "how it is measured" |
| `tools/dashboard/templates/idp/component.html` | Dimension cards with inline evidence |
| `tools/dashboard/templates/idp/evidence.html` | New |
| `args/scorecards/component-readiness.yaml` | `dimensions`, `grading.bands`, per-rule `dimension` + `evidence` |
| `tests/test_idp_catalog_scorecards.py` | New — 29 tests, one section per acceptance clause |
| `tests/e2e/idp_portal.spec.ts` | 7 new scenarios |

All mirrored roots (`tools/iqe/adapters/idp.py`, the `icdev/tools/idp/` package
and the templates) are mirrored; mirror parity is pinned by
`test_template_and_icdev_mirror_are_identical`.

## Verification

```bash
pytest tests/test_idp_portal.py tests/test_idp_scorecard.py \
       tests/test_idp_catalog_scorecards.py -q      # 76 passed
python tools/idp/scorecard.py                        # per-dimension CLI table
ruff check tools/idp/ tools/iqe/adapters/idp.py      # clean
```

Rendered and screenshotted against a live dashboard on PostgreSQL via the
repo's own CDP driver (`tools/browser/cdp/`) — `/idp/`, `/idp/component/dic`
and `/idp/evidence` all render, and the evidence page shows
`awareness_component_health` as **not measured** for a component with no probe
rows.

The 8-point completeness gate still passes for `idp` with all eight points
present.

# CUI // SP-CTI
