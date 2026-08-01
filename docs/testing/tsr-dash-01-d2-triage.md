# TSR DASH — failing-test triage: clean worktree vs populated checkout (tsr-dash-01-d2)

Diagnostic only. No source file was modified. Produced 2026-08-01 on branch
`kanban/tsr-dash-01-d2`, off `origin/main` at `3c2650eb7`.

The question this answers, per test file in the DASH slice:

| category | meaning | tool label |
|---|---|---|
| **(a)** | passes on the populated checkout, fails clean → the fixture depends on ambient DB state | `ambient` |
| **(b)** | fails in **both** places → a real defect that reproduces from a cold start | `real` |
| **(c)** | anything else — fails only on the populated checkout (stale-row contamination), not runnable headless, or not executed | `shared`, `unknown`, `not-run` |

## 1. The two arms

Both arms are the **same commit** (`3c2650eb7`). The only difference is `data/`.

| arm | path | `data/icdev.db` |
|---|---|---|
| clean worktree | `C:\AI\.worktrees\tsr-dash-d2` | seeded schema only — `tools/db/init_icdev_db.py`, `tools/studio/init_db.py`, migration 311, per the tsr-dash-01-d1 recipe |
| populated | `C:\AI\ICDev\.tmp\worktrees\tsr-dash-01-d2-pop` | full copy of the shared checkout's `data/` — months of accumulated dashboard and runner traffic |

**The populated arm is a worktree carrying a copy of the shared checkout's `data/`, not
`C:\AI\ICDev` itself.** Running a 158-file pytest slice directly in the shared checkout would
write to the live `data/icdev.db` that other concurrent sessions and the kanban scheduler are
using. The copy gives the same ambient-state condition without that blast radius.

Command, identical in both arms:

```bash
export ICDEV_STORAGE_BACKEND=sqlite          # required; without the pin the seed half-lands
export PYTHONPATH=<arm root>
python -m pytest $(cat runlist.txt) -q -rfE --timeout=60 \
    --continue-on-collection-errors -p no:cacheprovider
```

`-rfE` (not `-rf`) is deliberate: `-rf` hides collection ERRORs, and in this slice collection
errors are a large share of the failures.

## 2. Scope: 189 slice files → 158 executed, 31 excluded

The DASH slice from tsr-dash-01-d1 is 189 files. **31 were excluded from execution** and are
categorised **(c) not runnable headless** without being run:

- 26 under `tests/e2e_selenium/`, plus `tests/e2e_fathomdesk_modal.py`,
  `tests/e2e_infra_emit.py`, `tests/e2e_network_canvas.py`,
  `tests/e2e_zta_lac_deny_assertions.py` — all drive a real browser through Selenium/WebDriver
  against a dashboard on `localhost:5050`. With no server and no browser they fail identically in
  both arms for a reason that has nothing to do with DB state, so running them would add 31
  false `real` verdicts.
- `tests/e2e/e2e_govlift_lifecycle.py` — not a browser test, but its filename matches neither
  `test_*.py` nor `*_test.py`, so **pytest never collects it**. It is dead weight in the slice.

Excluded files are listed verbatim in `tsr-dash-01-d2-excluded.txt`. The remaining **158**
executed files are in `tsr-dash-01-d2-runlist.txt`.

## 3. Results

<!-- RESULTS -->

## 4. How to re-run

```bash
python tools/testing/tsr_triage_diff.py \
    --shared docs/testing/tsr-dash-01-d2-populated.txt \
    --clean  docs/testing/tsr-dash-01-d2-clean.txt \
    --slice  docs/testing/tsr-dash-01-d2-runlist.txt \
    --out    docs/testing/tsr-dash-01-d2-table.md
```
