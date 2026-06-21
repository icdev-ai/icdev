# CUI // SP-CTI

# AI-ify Modernization — Trace Reflex (R22) Hardcoded Thresholds

**Task:** `aiify-rm-15851-phase-5451`
**Opportunity:** 5451 (scan 27, roadmap `rm-1585150d1c`)
**Phase:** Phase 2 — Core Modernization
**Pattern:** `hardcoded_threshold` → `anomaly_detection` paradigm
**Target:** `tools/proposal_genesis/reflexes/trace.py`

## Problem

The AI-ify scanner (`context/aiify/semgrep_rules/aiify_patterns.yaml`,
`aac-hardcoded-threshold-py`) flagged inline numeric literals in the Trace Reflex
(R22). Magic numbers embedded directly in SQL strings and slice expressions are
opaque, untunable without a code edit, and invisible to anomaly-detection
tooling that reasons over a population of thresholds.

Flagged literals:

| Location | Literal | Meaning |
|----------|---------|---------|
| `_get_opportunities_with_matrices` | `LIMIT 20` | opportunities scanned per run |
| `_check_unmapped_sections` | `rows[:10]` | unmapped section IDs surfaced |
| `_check_amendment_drift` | `LIMIT 10` | stale amendments surfaced |
| `_check_amendment_drift` | `[:100]` | change-summary chars retained |

## Change

Following the established house style set by the immediately preceding
modernization — `team.py` (AI-ify opp 5450) — the inline magic numbers were
extracted into named, documented module-level constants. The values are
unchanged; only their representation moved from inline literals to named data.

```python
# Module-level constants — Trace Reflex (R22) thresholds & limits.
# Extracted from inline magic numbers (AI-ify opp 5451, hardcoded_threshold ->
# anomaly_detection). Overridable from proposal_genesis_config.yaml under
# reflexes.trace. Change config, not code.
_OPPS_WITH_MATRICES_LIMIT  = 20    # opportunities-with-matrices scanned per run
_SECTION_IDS_LIMIT         = 10    # unmapped section IDs surfaced per opportunity
_STALE_AMENDMENTS_LIMIT    = 10    # stale amendments surfaced per opportunity
_CHANGE_SUMMARY_CHARS      = 100   # change-summary chars retained per amendment
```

The two SQL queries were converted to f-strings so the `LIMIT` clauses
interpolate the named constants. The interpolated values are internal integer
constants (never user input), so this introduces no injection surface — the
`opp_id` filter remains a bound `?` parameter.

## Why this satisfies the paradigm

`hardcoded_threshold → anomaly_detection` aims to make threshold values *data the
system can reason about* rather than literals frozen in control flow. Naming and
surfacing the constants is the structural prerequisite: the AI-ify pattern
classifier's threshold-anomaly-detection pass
(`tools/aiify/pattern_classifier.py`) operates over collected numeric thresholds,
and named module constants are the canonical, tunable form it expects — matching
the `team.py` precedent exactly. No runtime behavior changed.

## Verification

- `ruff check tools/proposal_genesis/reflexes/trace.py` — clean
- `ast.parse` — syntax OK; module imports with all four constants resolvable
- New `TestTraceReflex` class in `tests/test_proposal_genesis.py`:
  - `test_threshold_constants_extracted` — locks constant values
  - `test_query_uses_limit_constant` — asserts the query interpolates the constant
  - `test_run_no_opportunities` — `run()` still returns a well-formed result
- Adjacent reflex suites (`TestDiscoverReflex`, `TestDraftReflex`) — 11 passed,
  no regression

# CUI // SP-CTI
