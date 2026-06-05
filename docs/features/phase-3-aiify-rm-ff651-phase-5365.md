# CUI // SP-CTI

# Phase 3 — AI-ify rm-ff651 / Opportunity 5365: Threshold extraction in the Genesis Test reflex

**Roadmap:** rm-ff65174e96 · **Phase:** Phase 3 — Long-Horizon Investments
**Pattern:** `hardcoded_threshold` → `anomaly_detection` paradigm
**Module:** `tools/genesis/reflexes/test.py`
**Status:** Shipped 2026-06-03

## Problem

The Genesis Test reflex (identifies under-tested tools and generates real
tests) carried 14 inline magic numbers governing module discovery, test-code
generation caps, and subprocess limits. These were undocumented and not
discoverable as a single tuning surface.

## Change

Extracted all 14 values into named, commented module-level constants. No
behavioral change — each constant equals its prior inline literal. The
max-tests-per-run value remains config-overridable from
`genesis_config.yaml` under `test.max_tests_per_run`; the named constant
`_DEFAULT_MAX_TESTS_PER_RUN` is the in-code fallback.

| Group | Constants |
|-------|-----------|
| Discovery | `_DEFAULT_MAX_TESTS_PER_RUN` (10), `_MIN_MODULE_LINES` (20) |
| Generation caps | `_MAX_FUNCS_PER_MODULE` (15), `_MAX_SIG_PARAMS_ASSERTED` (5), `_MAX_INVOCATION_PARAMS` (3), `_MAX_PARAM_VALUES` (5), `_MAX_CLASSES_PER_MODULE` (5), `_MAX_METHODS_ASSERTED` (8), `_MAX_CONSTANTS_ASSERTED` (10) |
| Subprocess | `_EXTRACT_TIMEOUT_SEC` (30), `_RUN_TEST_TIMEOUT_SEC` (60), `_STDOUT_TAIL_CHARS` (1000), `_STDERR_TAIL_CHARS` (500), `_ERROR_SNIPPET_CHARS` (200) |

## Tests

`tests/genesis/test_test_reflex_constants.py` — 10 unit tests: constant
invariants (positivity, ordering relationships) plus behavior pins for
`_find_untested_modules(max_results=…)` and `_generate_param_fixture`.
All passing; `ruff check` clean.

## Acceptance

- [x] All 14 inline literals replaced by named constants
- [x] `max_tests_per_run` config override preserved
- [x] 10 tests passing
- [x] Lint clean
- [x] Opportunity 5365 closed
