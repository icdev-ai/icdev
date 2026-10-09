# CUI // SP-CTI

# flx-ci-02 — two pre-apply gates, reconciled to one

**Date measured:** 2026-09-05
**Tree measured:** `81ef92024153b43bbcfb633e3db0573566a01b6e` (the merge base of
`kanban/flx-ci-02`), the last commit at which BOTH gates existed.
**Outcome:** `tools/infra_canvas/pre_apply_gate.py` deleted;
`tools/infra_canvas/preapply_gate.py` survives.

## Why this document exists

The disagreement below **cannot be re-derived on the current tree**, because one
of the two modules it compares no longer exists. To reproduce it, restore the
loser against the cited ref:

```bash
git show 81ef92024:tools/infra_canvas/pre_apply_gate.py
```

That is the whole reason the measurement is written down rather than left as a
command to re-run. A number nobody can reproduce is an assertion, so the ref is
part of the finding.

## The pair

|                   | `preapply_gate.py`                      | `pre_apply_gate.py`                    |
|-------------------|-----------------------------------------|----------------------------------------|
| lines             | 243, has a CLI                          | 74, no CLI                             |
| verdict key       | `"gate": "pass"` / `"fail"`             | `"passed": bool`                       |
| rule source       | `context/iqe/queries/infra/*.iqe`       | `infra_engine.assess_infra_design` (13 rules) |
| severity policy   | any violation fails                     | CAT1 only                              |
| entry point       | `run_gate(plan_json)`                   | `check_plan(plan_data)`                |

They took the same input and answered the same question, and agreed on nothing.

## Measurement 1 — who calls `check_plan`?

**Nobody.** Searched `tools/`, `icdev/tools/`, the dashboard blueprints, and
every dynamic-import spelling (`from ... import`, module-object import, dotted
string):

| Reference kind | Count | Where |
|----------------|-------|-------|
| runtime caller | **0**  | — |
| test caller    | 18    | `tests/test_idc_twin_phase1.py` (UNGATED — line 983 of `args/ci_test_backlog.txt`) |
| own docstring  | 2     | the module itself |
| manifest row   | 2     | `tools/manifest/design-canvases.md` + `icdev/` mirror |

For contrast, `preapply_gate.run_gate` has **two live runtime consumers**:
`tools/twin_core/adapters/idc.py::simulate_delta` and
`tools/ci/floci_iac_gate.py` (plus `.github/workflows/floci-iac-gate.yml`).

This alone is the declared-but-unconsumed defect, and the card's stated
disposition for it is deletion rather than a merge. Measurement 2 is what makes
that disposition safe rather than merely permitted.

## Measurement 2 — do they disagree on a real plan?

Both gates, run over the two flx-ci-01 fixtures
(`tests/fixtures/floci_iac/*/recorded_plan.json`):

| fixture | `run_gate` | `check_plan` | agree? |
|---------|-----------|--------------|--------|
| `flocigate_ok` (compliant) | **`pass`**, 0 violations, 3 skipped | **`passed=False`**, 6 violations (4 CAT1), score 53.8 | **NO** |
| `flocigate_violating` | **`fail`**, 3 CAT2 (`cross_region_data_paths`, `fips_compliance_check`, `untagged_resources`) | **`passed=False`**, 6 violations (4 CAT1), score 53.8 | yes |

The important cell is not the disagreement — it is that **`check_plan` returned
the identical verdict for both fixtures.** Same `passed`, same violation count,
same score, for a plan built to be compliant and a plan built to violate. It has
**no discriminating power over its own stated input**.

## Measurement 3 — why. The rules are estate-scoped; the input is a delta.

Run against the three plans in `tests/test_idc_twin_phase1.py`:

| plan | nodes | `passed` | score | CAT1 |
|------|-------|----------|-------|------|
| `MINIMAL_TF_PLAN` (one bucket) | 2 | `False` | 38.5 | 4 |
| `TF_PLAN_NO_KMS` | 2 | `False` | 38.5 | 5 |
| `TF_PLAN_MINIMAL_PASSING` (KMS + IAM + Secrets Manager) | 3 | **`True`** | 84.6 | 0 |

`check_plan` passes **only when the plan itself contains KMS, IAM and Secrets
Manager** — i.e. only when the plan *is* the entire estate. Its CAT1 rules are
estate-completeness questions:

- `IDC-ENC-003` — "No KMS/Key Vault service found in the design."
- `IDC-IAM-001` — "No IAM or identity provider service in the design."
- `IDC-IAM-002` — "No secrets manager found."

A `terraform plan` is a **delta**: the resources this apply touches. Adding one
S3 bucket to an estate that already has KMS, IAM and Secrets Manager produces a
graph containing one bucket, and every one of those rules fires. So
`check_plan` was **structurally incapable of passing any real incremental
plan** — the compliant-fixture failure above is not a tuning problem, it is the
category error.

`run_gate` asks per-resource questions (is this resource tagged? is it in an
allowed region? is it FIPS-compliant?), which are answerable about the changed
resources alone. That is the correct shape for a plan gate.

## Why deletion, not a merge

Folding the CAT1 estate rules into `run_gate` would import exactly the defect
that made them useless here: a gate that fails every incremental plan. The two
modules are not two implementations of one question, they are one question asked
of the right input and a **different** question asked of the wrong one.

**Nothing was lost.** `infra_engine.assess_infra_design` — the whole 13-rule
rulebook — is consumed live by `tools/infra_canvas/blueprint.py` at three sites,
over the **full design graph**, which is the input those rules were written for.
Deleting `check_plan` removed a wrapper, not a rule. The IDC canvas still asks
"is this design complete?"; it just no longer asks it of a plan delta.

## The second defect, fixed by the deletion

`check_plan` returned `assessment.get("score", 100.0)` — **a perfect score over
an absent measurement**, the rem-hyg-13 shape. `tools/ci/perfect_score_census.py`
does not catch it because that census's finding is a conjunction (a `100.0`
fallback arm **and** a body computing a ratio) and this was a `.get` default.

The census predicate was **not** widened — `else 100.0` matches sites that are
not scores at all, which is precisely why the conjunction exists.

Note that `assess_infra_design` itself is already correct: it returns
`score = round(...) if total_rules else None`. The wrapper's `.get` default was
unreachable *today* only because the assessor always emits the key; it was one
substituted assessor away from firing. The surviving gate has no score at all —
it returns `gate` / `violations` / `delta` / `skipped` — so there is no
unmeasured score left to publish.

## What was migrated

| consumer | disposition |
|----------|-------------|
| `tests/test_idc_twin_phase1.py::TestPreApplyGate` (15 tests) | **deleted** — its subject was the wrapper's return shape, which is gone. Replaced by a tombstone comment recording why. |
| `tests/test_idc_twin_phase1.py::TestFullGateFlow` (3 tests) | **migrated** to call `import_terraform_plan` + `assess_infra_design` directly — the two lines the wrapper inlined. Coverage of the plan-to-graph-to-assessment-to-snapshot chain is preserved. |
| `tests/ci/test_floci_iac_gate.py` | assertion **inverted**: it required the workflow to name both gates; it now requires the deleted one to be absent. |
| `tools/manifest/design-canvases.md` (+ mirror) | row removed |
| `.github/workflows/floci-iac-gate.yml`, `tools/ci/floci_iac_gate.py` (+ mirror), `preapply_gate.py` (+ mirror) | "WHICH GATE" blocks rewritten — they named the loser as a live alternative |

## Standing guard

`tests/infra_canvas/test_one_preapply_gate.py` fails if a second
`*pre*apply*gate*.py` appears under `tools/infra_canvas/` in **either** tree, and
if any of the docs above names the deleted module as live. **A shim does not
satisfy it** — the check is on the FILE, because a shim over a gate is a second
gate with a redirect: two names to import, two things to grep, two places a
future edit can land.

## Named, not fixed here

- **`tools/infra_canvas/snapshot_writer.py` is broken on SQLite.**
  `write_snapshot` and `write_violations` send PostgreSQL `%s` placeholders to a
  bare `sqlite3.connect()` connection that never sees `translate_sql`, so both
  raise `sqlite3.OperationalError: near "%": syntax error` (lines 118 and 162).
  This is **pre-existing and unrelated** — 11 tests in
  `tests/test_idc_twin_phase1.py` failed on it before this card and fail on it
  after, unchanged. Out of scope: a different module, a different defect, with a
  PG-side blast radius that needs its own card and its own red-first proof.
- **`terraform_show_importer.import_terraform_plan` now has no runtime caller.**
  Deleting `check_plan` left it exercised by tests only. It is a legitimate
  importer and deleting it is not this card's business, but it is now a
  declared-but-unconsumed unit and should be either wired into the IDC blueprint
  or retired on its own card.
