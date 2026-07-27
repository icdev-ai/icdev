# Spike: pytest Parallelization + Coverage Trending (crx-test-03)

Investigation write-up for CRX review findings **testing_quality.md #7 (No Test Parallelization)**
and **#8 (No Coverage Trending)**. This is a SPIKE — evaluation and plan only. No production
code or config changed. See [CLAUDE.md](../../CLAUDE.md) for behavioral instructions and
[testing.md](testing.md) for the current test-harness reference.

**Verdict (one line): CONDITIONAL GO — adopt `pytest-xdist` with a strict serial-quarantine
group and a per-module coverage-snapshot JSON; do NOT flip `-n auto` on the whole suite blind.**

---

## 1. Why this is a spike, not a build

The suite has documented, load-bearing constraints that make naive `pytest -n auto` unsafe:

| Constraint | Evidence (file:ref) |
|---|---|
| conftest **forces SQLite** for the whole suite | `tests/conftest.py:30-35` (`ICDEV_STORAGE_BACKEND=sqlite`, plus NOCC/PMC/CCC/DSOC) |
| Default SQLite DB is an **ambient shared file** at `cwd/data/icdev.db` | `tools/db/storage.py:110` (`DB_PATH`), `:1506` (`Path.cwd()/"data"/"icdev.db"`), `:1369`, `:773`, `:791` |
| Connections are **process-cached** | `[[compass-get-connection-is-cached-never-close]]`; `tools/db/storage.py:1272` (`global _pg_pool`) |
| Full conftest can **hang pytest for 5+ min in a worktree** | `[[feedback_kanban_sandbox_and_pytest]]` gotcha #2 |
| Some tests **pass only by accident** of the `%s`→`?` translator | `[[tests-raw-sqlite-bypasses-translate]]` |
| CI `test` job is a **12-file allowlist**, not the full suite | `.github/workflows/icdev-ci.yml:64-89` |
| Suite size | **1,280 test files, ~25,644 `def test_` functions** (measured) |

Green CI therefore already does **not** mean the full ~27k-test suite passes
(`[[ci-test-job-is-a-12-file-allowlist]]`). Any parallelization work must not deepen that
illusion — see §6.

---

## 2. Current test-harness model (what xdist workers would/would not share)

`pytest-xdist` workers are **separate OS processes** (`execnet` gateways). Each worker
re-imports `conftest.py`, re-runs session setup, and gets its **own** module-level globals
(the `get_connection` cache, `_pg_pool`, any lru_cache). Process-local state is therefore
**not** a cross-worker hazard. The hazards are anything the workers share on disk or by env.

### 2.1 conftest fixtures — classified

`tests/conftest.py` (3,158 lines) exposes two shapes of DB fixture:

- **`icdev_db(tmp_path)` — `:3096`.** Creates a fresh `MINIMAL_ICDEV_SCHEMA` DB under the
  test's **`tmp_path`** (xdist gives every worker a distinct `tmp_path` basetemp).
  **Parallel-safe** — no cross-worker sharing.
- **Canvas fixtures `nocc_db` / `ccc_db` / `dsoc_db` / `pmc_db` — `:3108-3157`.** Each does
  `monkeypatch.setenv("<X>_DB_PATH", tmp_path/...)` then `init_db()`.
  **Parallel-safe** — DB path is per-test `tmp_path`, and `monkeypatch` env is per-test.

The safe pattern is already established: **tests that receive a `tmp_path`-scoped DB fixture
are inherently xdist-safe.** The unsafe tests are the ones that bypass these fixtures.

### 2.2 The central hazard — the ambient `cwd/data/icdev.db`

`get_connection()` with no explicit path resolves to `ICDEV_DB_PATH` or, absent that,
`Path.cwd()/"data"/"icdev.db"` (`storage.py:1506`, `:110`). Because **all xdist workers share
one working directory**, every worker that calls the bare `get_connection()` writes to the
**same physical SQLite file**. Measured: **138 test files** import/use the ambient
`get_connection` / `data/icdev.db` path. Under `-n auto` these would:

- interleave writes/reads on one file → non-deterministic row counts, `UNIQUE`/PK violations,
  and "no such table" mid-run if another worker is mid-`init`;
- hit SQLite writer-lock contention (`sqlite3.connect(..., timeout=5)`, `storage.py:773/791`)
  → `database is locked` flakes, worse on Windows (§5).

This is the file-sharing collision the spec calls out. It is the reason the answer is
*conditional* GO, not unconditional.

---

## 3. xdist safety classification

| Family | Signal | Verdict | How to mark |
|---|---|---|---|
| `tmp_path`-fixture DB tests (`icdev_db`, `*_db` canvas fixtures) | per-worker basetemp | **PARALLEL-SAFE** | default (no marker) |
| Pure-logic / parsing / no I/O (e.g. `test_circuit_breaker`, `test_retry`, `test_correlation`, `test_errors`, `test_schemas`) | no DB, no socket | **PARALLEL-SAFE** | default |
| Ambient `data/icdev.db` writers (138 files via bare `get_connection`) | shared file | **SERIAL / isolate** | `@pytest.mark.xdist_group("ambient_db")` or `-p no:randomly` per-file; long-term migrate to `tmp_path` |
| Dashboard / Flask `test_client`, `localhost:5050`, `socket`/`bind` (390 files) | shared port / app singleton | **SERIAL for port-binders**; `test_client` in-process is usually safe but app-level module state can leak | `xdist_group("dashboard")`; keep any real port-binding test serial |
| `%s`→`?` translator-dependent tests | `[[tests-raw-sqlite-bypasses-translate]]` | **SAFE under xdist** (SQLite still forced) but **fragile** — do not "fix" opportunistically | default; leave alone |
| cwd-sensitive | measured **0** `os.chdir`/`monkeypatch.chdir` in `tests/` | n/a | — |

**Marking mechanics.** xdist assigns tests to workers by test-id hash under the default
`--dist load`. To force same-worker execution for a colliding family use **`--dist loadgroup`**
plus `@pytest.mark.xdist_group("<name>")` — every test tagged with the same group name runs on
one worker, in order, eliminating cross-worker file contention while other groups still run in
parallel. Truly non-isolable tests get their own group (effectively serial). The
`-p no:cacheprovider` flag is orthogonal (it only disables `.pytest_cache`); it is worth adding
in CI to avoid workers racing on the cache dir but it is **not** an isolation mechanism.

Recommended default invocation once markers exist:

```
pytest --dist loadgroup -n auto -p no:cacheprovider
```

---

## 4. Realistic wall-clock estimate + caveats

**Model.** Speedup is bounded by (a) Amdahl's serial fraction — the `xdist_group("ambient_db")`
+ dashboard groups collapse to ~2 workers' worth of serial time — and (b) fixed per-worker
startup: each worker re-imports the **3,158-line conftest** and its session setup, which is the
same setup that has hung for 5+ min in worktrees (`[[feedback_kanban_sandbox_and_pytest]]`).

- Import/collection is a **fixed tax paid N times** (once per worker). At `-n 8` that is 8×
  conftest import before the first assertion runs.
- The ~138 ambient-DB + real-port tests are pinned serial.

**Estimate (order-of-magnitude, full ~25.6k-test suite on an 8-core runner):**

| Scenario | Expected wall-clock vs serial |
|---|---|
| Naive `-n auto`, no grouping | **unreliable** — flakes/locks dominate; net often *slower* after retries |
| `-n 8 --dist loadgroup` with ambient+dashboard quarantined | **~2.5–4× faster** on the parallel-safe majority; serial tail sets the floor |
| Same, after migrating ambient-DB tests to `tmp_path` (Phase 3) | **~4–6×**; serial tail shrinks to real port-binders only |

Caveats: these are *estimates*, not measured — the spike deliberately did **not** run the full
suite (worktree conftest-hang risk + hours of wall-clock). The first real build task should
measure baseline serial time on the CI runner and a `-n 4/-n 8` sweep before committing a
worker count. Memory is also a ceiling: N workers hold N copies of the imported app; on a
2-core / low-RAM runner `-n auto` can thrash — pin an explicit `-n` in CI.

---

## 5. Windows caveats

- **Process-based workers only.** xdist on Windows uses spawned processes (no `fork`); every
  worker pays full interpreter + conftest import startup. The fixed tax in §4 is larger on
  Windows than on Linux CI.
- **SQLite file locks are stricter.** Windows holds mandatory locks on the ambient
  `data/icdev.db`; concurrent workers on the shared file will raise `database is locked` far
  more readily than on Linux. This makes the §3 quarantine **mandatory**, not optional, for any
  local Windows `-n auto` run.
- **`tmp_path` basetemp is per-worker** on Windows too, so the safe fixtures stay safe.
- Local dev guidance: on Windows use a conservative `-n 4` with `--dist loadgroup`; reserve
  `-n auto` for the Linux CI runner.

---

## 6. Coverage trending design (finding #8)

### 6.1 Does EQO already cover this? **No.**

The **EQO** project (`[[project-eqo-quality-observability]]`, prefix `eqo-`) is *Ecosystem
Quality & Observability*: its three epics are the **SIPA blocking code-quality gate**,
**centralized logging** (`centralized_logs` table + `/logs`), and **AI-ify PRD readiness**.
None of the 17 EQO tasks produce a per-module coverage snapshot or drop-alert. `pytest-cov>=5.0`
is a declared dependency (`requirements.txt:11`, `pyproject.toml:106`) but there is **no**
`[tool.coverage]` config, no `--cov` in CI, and **no** coverage-snapshot tooling under
`tools/testing/` (verified empty). **Coverage trending is greenfield — no overlap, no
duplication risk.**

### 6.2 Proposed design (new build task, not this spike)

A small deterministic tool (`tools/testing/coverage_trend.py`), FORGE-style, run after a
`--cov` pytest pass:

1. **Snapshot per run.** Read `coverage.py`'s JSON (`coverage json -o -`) and emit
   `data/coverage/snapshots/coverage_<UTC-ISO>.json`:
   ```json
   {
     "run_id": "2026-07-25T04-00-00Z",
     "commit": "<sha>",
     "total_pct": 62.4,
     "modules": { "tools/db/storage.py": 71.2, "tools/llm/router.py": 44.0 }
   }
   ```
   Cross-platform: `pathlib.Path`, `encoding='utf-8'`, `datetime.now(timezone.utc)`.
2. **Drop alerts.** Compare the newest snapshot to the previous one (or a rolling baseline):
   - **total** drop > **1.0 pp** → WARN;
   - **any module** drop > **5.0 pp** (threshold from finding #8's "dropped 5%") → FAIL/alert,
     with the module list.
   Thresholds live in `args/` (e.g. `args/coverage_trend.yaml`), never hardcoded.
3. **History / leaderboard.** Persist snapshots to an **append-only** `coverage_snapshots`
   table (add to `APPEND_ONLY_TABLES` in `.claude/hooks/pre_tool_use.py` and to
   `MINIMAL_ICDEV_SCHEMA`); render newest-first + a per-module trend on an existing quality
   surface. Append-only preserves the audit trail (NIST AU) — never UPDATE/DELETE snapshots.
4. **CI wiring.** A dedicated non-blocking `coverage-trend` job (advisory first, gate later) so
   it does not stall merges while a baseline is established. **Critical:** run `--cov` over the
   **broad** suite (or an explicit large subset), *not* the 12-file allowlist — otherwise the
   trend measures 12 files and misreports the other ~1,268 as uncovered (§1, `[[ci-test-job-
   is-a-12-file-allowlist]]`). Coverage collection under xdist requires `pytest-cov`'s built-in
   xdist support (each worker writes a `.coverage.<worker>` then `coverage combine`) — this is
   supported but is another reason to land §7 Phase 1 before enabling `--cov -n`.

---

## 7. GO / NO-GO + phased plan

**CONDITIONAL GO.** Both findings are real and worth fixing; the effort is Low–Medium as the
review claims, *but only if sequenced behind the isolation work*. Flipping `-n auto` on the raw
suite is a NO-GO (it would trade slow-but-honest for fast-but-flaky, and the 138 ambient-DB
tests would flake immediately, worse on Windows).

Phased build plan (each phase a separate kanban task under a future card; **this spike ships
none of it**):

- **Phase 1 — Isolation scaffolding.** Add `pytest-xdist` to `requirements.txt`/`pyproject`.
  Tag the 138 ambient-`get_connection` files `@pytest.mark.xdist_group("ambient_db")` and real
  port-binders `xdist_group("dashboard")`. No `-n` in CI yet. Acceptance: `pytest --dist
  loadgroup -n 4` on the parallel-safe subset is green and repeatable 3×.
- **Phase 2 — Measured enablement.** Baseline serial vs `-n 4/-n 8` sweep on the CI runner;
  pin an explicit `-n` (not `auto`). Keep the serial groups. Acceptance: documented speedup,
  zero new flakes across 3 runs.
- **Phase 3 — Migrate ambient-DB tests to `tmp_path`.** Convert the `xdist_group("ambient_db")`
  tests to the `icdev_db`/canvas-fixture pattern, shrinking the serial tail. Incremental.
- **Phase 4 — Coverage trending.** Ship `coverage_trend.py` + `coverage_snapshots` table +
  advisory CI job (§6). Then, separately, consider promoting the full-suite run out of the
  12-file allowlist so both parallelization *and* coverage measure reality.

**Do not** enable `-n auto` in CI, and **do not** "fix" the `%s`→`?`-translator-dependent tests
as part of this work (`[[tests-raw-sqlite-bypasses-translate]]`) — that is unrelated scope.

---

## 8. Success criteria for the eventual build

- `pytest --dist loadgroup -n <N>` green and **repeatable** (3 consecutive clean runs) on the
  parallel-safe subset — no `database is locked`, no order-dependent failures.
- Serial-quarantine markers present on all 138 ambient-DB files + real port-binders.
- Measured wall-clock speedup documented against a serial baseline on the CI runner.
- `coverage_trend.py` emits a per-module snapshot JSON and FAILs on a synthetic >5pp module
  drop; `coverage_snapshots` is append-only and in `APPEND_ONLY_TABLES`.
- CI coverage run targets the broad suite, not the 12-file allowlist.
