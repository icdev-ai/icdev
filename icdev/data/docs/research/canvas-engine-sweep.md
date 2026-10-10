# Canvas & Engine Sweep — Per-Component Liveness

CUI // SP-CTI · First pass 2026-08-02 · Companion to [external-benchmark-map.md](external-benchmark-map.md)

Measures every one of the **66 registered components** (36 canvases, 17 core extensions,
9 features, 4 child apps) for whether it actually *runs and holds data*, rather than whether
it exists. The benchmark map predicted this sweep would pay off most in data canvases and
evaluation surfaces; it did.

**Method.** Scripted against the live PostgreSQL instance and the registry: module import,
tables created by the component's own migrations, row counts, E2E spec presence, IQE adapter,
seed-query count, and the 8-point completeness verdict. Nothing was written.

**Known measurement limit, stated up front.** Table discovery by migration path missed
components whose migrations live in the shared `tools/db/migrations/` tree. A first pass
appeared to show 19 canvases with no tables; re-checking by table-name prefix showed most do
have them (`dic` 32 tables / 807 rows, `aadc` 33 / 122, `ohc` 9 / 1,472, `odc` 9 / 785,
`idc` 11 / 91). **Those are not findings and are excluded below.** Only components whose
tables were positively located *and* measured empty are reported as inert.

---

## Headline results

| Measure | Result |
|---|---|
| Components that fail to import | **0 of 66** |
| Canvases passing 8-point completeness | 35 of 36 |
| Canvases with an E2E spec | **10 of 36** |
| Canvases with an IQE adapter | 28 of 36 |
| Canvases with ≥3 IQE seed queries | **16 of 36** |

Two of those numbers are the story.

---

## 1. Nothing is broken at import — a genuinely good result

All 66 components import cleanly. Given CLAUDE.md's warning that a missing backing module
produces an `ImportError` at startup, and given how often this sweep found *unfed* systems,
zero import failures across 66 components is worth recording as a strength.

---

## 2. E2E coverage is 10 of 36 — this is the amber driver, now quantified

`tools/canvas_health` reports 32 of 36 canvases as **amber**, and the earlier inventory found
the dominant cause was "no E2E spec". This sweep puts a number on it: **26 canvases have no
E2E spec at all.**

That single gap is what keeps the canvas-health board yellow. It is also the cheapest
scorecard rule to write and the most mechanical to close, which makes it the natural first
rule for the `idp` scorecard ladder.

---

## 3. The 8-point completeness gate under-enforces, on two independent axes

CLAUDE.md specifies the gate requires "**≥3 seed queries** in `context/iqe/queries/<canvas>/`".

- `validate_canvas_completeness` checks that the seed-query **directory exists**
  (`component_registry.py:1090-1107`), not that it contains three queries — and if the IQE
  adapter is present it can mark seed queries present via a fallback candidate path.
- Result: **19 of 36 canvases pass completeness with fewer than 3 seed queries** — `idc`,
  `ndc`, `sdc`, `bdc`, `pdc`, `odc`, `ddc`, `mdc`, `nocc`, `pmc`, `ccc`, `dsoc`, `ace`,
  `rfi_canvas`, `docgen`, `wfc`, `canvas_health`, `cwk`, `second_brain`.

Compounding this, the registry-driven check returns `status="warn"`, never `"fail"`
(`coherence_checker.py:5446`), so it cannot block a merge even when it does detect a gap.
Its filesystem-driven sibling `check_new_page_completeness` *does* fail — so the two
implementations of the same documented rule disagree on severity.

**A gate that passes 19 of 36 components against its own written standard is measuring
something other than what it claims.** This is the same shape as the CXO finding: an
enforcement surface that reports success while not enforcing.

→ Related: `idp-score-05` (flip warn→fail). This sweep adds a second required fix: count the
seed queries rather than stat the directory.

---

## 4. Canvases that are built but unfed

Positively located tables, zero rows:

| Canvas | Tables | E2E | Seeds |
|---|---|---|---|
| `mission_canvas` | 10 | yes | 3 |
| `aisg` | 9 | no | 3 |
| `ccc` | 7 | no | 0 |
| `dsoc` | 7 | yes | 0 |
| `pmc` | 6 | no | 0 |
| `pdc` | 3 | no | 0 |
| `mdc` | 2 | no | 0 |
| `aimc` | 2 | no | 10 |

All eight are **enabled** and all eight **pass completeness**. They are shipped, reachable,
and empty.

This is not automatically a defect — a canvas can be legitimately awaiting its first real
engagement. But it is exactly the condition a scorecard should surface and currently nothing
does, and `dsoc` (DDoS & Security Ops, 7 tables) being empty while ~79 analyzer modules exist
elsewhere is the specific overlap the `anz` analyzer-contract card addresses.

---

## 5. Where the data actually is

| Component | Rows | Populated tables |
|---|---|---|
| `integrity` | 1,185,793 | 5 / 5 |
| `foundry` | 7,589 | 2 / 6 |
| `ace` | 1,916 | 11 / 13 |
| `ohc` | 1,472 | — |
| `dic` | 807 | — |
| `odc` | 785 | — |
| `sdc` | 321 | 10 / 22 |
| `qdc` | 137 | 12 / 15 |
| `cortex` | 129 | 1 / 5 |

`integrity` at 1.19M rows dwarfs everything else. `cortex` at 129 rows across 1 of 5 tables
is consistent with the CXO finding that its governed traffic is thin and stopped on
2026-07-17.

---

## Recommendations

1. **Make "has an E2E spec" the first scorecard rule.** It is the single largest coverage
   gap (26 of 36), it is mechanically checkable, and closing it moves the canvas-health board
   off amber. It is also a rule a component can genuinely fail today, which is what makes a
   ladder credible.
2. **Fix the completeness gate to count seed queries, not stat a directory** — add to
   `idp-score-05` alongside the warn→fail flip. Expect 19 canvases to fail on first run;
   whitelist deliberately rather than weakening the check.
3. **Surface "enabled, complete, and empty" as a distinct scorecard state.** Eight canvases
   are in it. Passing every structural check while holding no data is precisely the condition
   that structural gates cannot see.
4. **Do not treat the tableless list as findings.** Several components legitimately hold no
   tables of their own (`canvas_health` computes, `demo_runner`, `logs`), and others store in
   shared or differently-prefixed tables (`second_brain` in `user_*`, `cwk` in `ace_*`).

## Reproducing

The sweep is a script, not a manual audit, so it can be re-run as the scorecard's evidence
collector rather than repeated by hand. Folding it into `idp-score-01` as an evidence source
is preferable to keeping it as a one-off document.
