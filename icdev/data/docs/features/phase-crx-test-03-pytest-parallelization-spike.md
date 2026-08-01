# Phase crx-test-03 — pytest Parallelization + Coverage Trending (SPIKE)

**Status:** Spike complete (evaluation + plan only — no production code/config changed).
**Card:** `crx-test-03` (CRX Component Review Remediation).
**Source:** CRX review `testing_quality.md` findings **#7 (No Test Parallelization)** and
**#8 (No Coverage Trending)**.
**Full write-up:** [docs/reference/spike-crx-test-03-pytest-parallelization.md](../reference/spike-crx-test-03-pytest-parallelization.md)

## Verdict

**CONDITIONAL GO** — adopt `pytest-xdist` behind a serial-quarantine group, and add a
per-module coverage-snapshot JSON with drop alerts. Do **not** flip `-n auto` on the raw suite.

## What was investigated

- **Harness constraints:** conftest forces SQLite (`tests/conftest.py:30-35`); the default
  SQLite DB is an **ambient shared file** `cwd/data/icdev.db` (`tools/db/storage.py:110,1506`)
  — the core cross-worker collision risk. Safe fixtures `icdev_db` / `nocc_db` / `ccc_db` /
  `dsoc_db` / `pmc_db` (`conftest.py:3096-3157`) are per-`tmp_path` and xdist-safe.
- **Suite size:** 1,280 test files / ~25,644 test functions (~27k). **138** files touch the
  ambient DB; **390** use Flask `test_client`/port/socket; **0** use `chdir`.
- **CI reality:** the `test` job is a **12-file allowlist** (`.github/workflows/icdev-ci.yml:
  64-89`) — green CI already ≠ full suite passes; parallelization must not deepen that.
- **EQO overlap check:** the EQO project (logging / SIPA gate / PRD readiness) does **not** do
  coverage trending; `pytest-cov>=5.0` is present but unconfigured. Trending is greenfield.
- **Windows:** spawn-based workers pay full conftest-import tax N times; stricter SQLite file
  locks make the serial quarantine mandatory locally.

## Deliverables in the write-up

- xdist safety classification (parallel-safe vs serial families) + marking mechanics
  (`--dist loadgroup` + `@pytest.mark.xdist_group`, `-p no:cacheprovider`).
- Realistic wall-clock estimate (~2.5–4× with quarantine; ~4–6× after `tmp_path` migration) —
  explicitly estimates, not measured (spike avoided the worktree conftest-hang risk).
- Coverage-trending design: per-run per-module snapshot JSON, >1pp total / >5pp module drop
  alerts, append-only `coverage_snapshots` history, advisory CI job on the broad suite.
- 4-phase plan: isolation scaffolding → measured enablement → migrate ambient-DB tests →
  coverage trending.

## Scope guardrails honored

Docs only. No `requirements.txt` / `pyproject.toml` / CI / test changes. The
`%s`→`?`-translator-dependent tests were left untouched (out of scope).
