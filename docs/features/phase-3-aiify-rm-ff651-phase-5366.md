# AI-ify Phase 3 Assessment — hardcoded_threshold in genesis/reflexes/test.py

**Task ID:** aiify-rm-ff651-phase-5366
**Roadmap:** rm-ff65174e96 (AI-ify Roadmap — Scan 26)
**Opportunity:** 5366
**Phase:** Phase 3 — Long-Horizon Investments
**Pattern:** hardcoded_threshold
**File:** `tools/genesis/reflexes/test.py`
**Recommended paradigm:** `anomaly_detection` (claude-haiku-4-5-20251001)
**Scores:** composite=0.4916, value=0.431, feasibility=0.7075, risk=0.75

## Finding

The AI-ify scanner (Semgrep) flagged four numeric thresholds in the Genesis Test
Reflex:

| Line | Code | Nature |
|------|------|--------|
| 44 | `if line_count < 20` | file-size skip heuristic (tunable knob) |
| 94 | `if json_start >= 0` | `str.find()` sentinel — "found / not found" |
| 254 | `len(non_default_params) <= 3` | arity cap for DB-mock invocation tests (knob) |
| 263 | `if len(param_values) <= 5` | arity cap for synthesised args (knob) |

## Assessment

**ML anomaly detection is not applicable.** The Genesis Test Reflex is a
scanner-tier, deterministic code-generation tool (zero Claude tokens by design).
The flagged constants are static generation heuristics and a sentinel — there is
no runtime data stream over which a statistical anomaly detector could learn a
"normal range." Bolting an ML model onto these would violate the reflex's
zero-token contract and the Karpathy simplicity/YAGNI gate.

Line 94 (`json_start >= 0`) is a **false positive**: `str.find()` returns `-1`
when not found, so `>= 0` is a structural presence check, not a tunable threshold.

The remaining three constants (20, 3, 5) are legitimately tunable knobs. The
codebase-canonical remediation for `hardcoded_threshold` opportunities on this
roadmap is **magic-number extraction into named constants** (see commits
`2b8bbde66`, `a64c26a2b`, `ff8ce134b`, `356d72e70`), not ML replacement.

## Resolution

Already satisfied in the working tree. `tools/genesis/reflexes/test.py` extracts
every flagged tunable threshold into documented module-level constants
(behavior-preserving — original values 20/3/5/10 retained):

- line 44  → `_MIN_MODULE_LINES = 20`
- line 254 → `_MAX_INVOCATION_PARAMS = 3`
- line 263 → `_MAX_PARAM_VALUES = 5`
- (plus `_DEFAULT_MAX_TESTS_PER_RUN`, `_MAX_FUNCS_PER_MODULE`, timeout/tail caps)

The line-94 sentinel was correctly left unchanged. `max_tests_per_run` remains
config-overridable via `genesis_config.test.max_tests_per_run`; the named
constant is the in-code fallback. Syntax + import verified; constants preserve
original values.

This opportunity overlapped a sibling `aiify-rm-ff651` task editing the same
file. Per the "duplicate opportunities collide" rule, this task was verified and
closed against the existing remediation rather than authoring a competing edit.

## Status

Closed — remediation already present (magic-number extraction). ML anomaly
detection assessed as inapplicable; line 94 documented as a scanner false
positive for future calibration.
