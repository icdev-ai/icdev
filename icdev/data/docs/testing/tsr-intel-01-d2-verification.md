# CUI // SP-CTI

# tsr-intel-01-d2 — `test_research_engine.py` verification (no fix required)

**Date:** 2026-08-01
**Task:** Fix `test_research_engine` stale failures using `_sql_compat`
**Outcome:** No code change. The task premise is false — the file has no failures
and none of the `_sql_compat` anti-pattern.

## Premise vs. reality

The card asserted "3 stale failures previously fixed in #1034 but may have
regressed," to be repaired by replacing bare `sqlite3.connect` patches with
`tests/_sql_compat.py`'s `connect()` / `translating()`.

`tests/test_research_engine.py` currently passes **68/68**, confirmed in three
configurations plus the sibling task's own baseline:

| Run | Result |
|-----|--------|
| `pytest tests/test_research_engine.py -rfE --timeout=300 -q` | 68 passed |
| Baseline args (`-q -rfE --timeout=120 -p no:cacheprovider`) | 68 passed |
| `-p no:randomly` (fixed order) | 68 passed |
| `docs/testing/tsr-intel-01-baseline.json` (task d1, same date) | `"failed": 0, "passed": 68` |
| `ruff check tests/test_research_engine.py` | All checks passed |

## Why `_sql_compat` does not apply here

The anti-pattern `_sql_compat` exists to fix is: a test patches `get_connection`
with a bare `sqlite3.connect`, stripping the `%s` → `?` rewrite that
`StorageConnection` performs, so production SQL raises
`sqlite3.OperationalError: near "%": syntax error`. This file does none of that:

1. **It never patches the connection layer.** There is no `monkeypatch`, no
   `mock.patch`, no `MagicMock` anywhere in the file.
2. **Production opens its own translating connection.** Every `tools/research/*`
   module resolves the DB through `get_connection(db_path=str(path))` — see
   `tools/research/research_engine.py:71`, `session_manager.py:123`,
   `trend_detector.py:254`, and eight sibling modules. The fixtures hand the
   code under test a **path**, not a connection, so the translating layer stays
   in place.
3. **The fixtures' own SQL is already `?`-parameterised.** The four
   `sqlite3.connect` calls (`tests/test_research_engine.py:414, 422, 457, 477`)
   are local seed/setup writes using literal `?` placeholders. The file contains
   **zero** `%s` literals, so there is nothing to translate.

Wrapping those four fixture connections in `translating()` would be a no-op that
adds indirection without changing behaviour, so they were left alone.

## Where the defect actually is

The `near "%": syntax error` failures in the INTEL slice are in files owned by
sibling tasks, not this one:

| File | Failures | Owning task |
|------|----------|-------------|
| `tests/test_portfolio_greeks.py` | 4 | `tsr-intel-01-d4` (trading) |
| `tests/test_signal_decay.py` | 1 | `tsr-intel-01-d4` |

The remaining INTEL baseline failures are unrelated to SQL translation and are
recorded in `docs/testing/tsr-intel-01-baseline.json`: missing tables
(`test_simulation_engine.py`, `test_threat_level_thresholds.py`), a backend guard
(`test_nav_plat_01_simulated_data.py`), a missing module
(`e2e_killswitch_widget.py`), and assertion drift
(`test_register_external_patterns.py`, `e2e_fathomdesk_trap.py`).

**Note for d3/d4/d5:** do not re-investigate `test_research_engine.py`. It is
clean and was verified on 2026-08-01.
