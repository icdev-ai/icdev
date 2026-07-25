# Testing (Additional)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Testing (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Claude Dir Validator | tools/testing/claude_dir_validator.py | .claude directory governance validator (9 checks) | --json, --human, --check, --all | Alignment report |


## Testing (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| GovEval Benchmark | tools/testing/goveval.py | 7-dimension Gov/DoD compliance quality benchmark (LeanStral FLTEval-adapted, D-VL-9) | --project-id, --dimension, --gate, --trend, --compare, --json | Dimension scores + gate |
| Platform Check | tools/testing/platform_check.py | OS environment compatibility validation (D145) | --json | Compatibility report |
| Claude Dir Validator | tools/testing/claude_dir_validator.py | .claude directory governance validator (9 checks) | --json, --human, --check, --all | Alignment report |
| Theater Detector | tools/testing/theater_detector.py | Detects test theater anti-patterns (8 types: tautological_assertion, mock_dominated, fixture_theater, assertion_free, hardcoded_oracle, smoke_masquerade, always_green, spec_drift) using AST + regex; no LLM required, air-gap safe | --scan &lt;dir&gt; [--json] | Anti-pattern report with severity (block/warn/none) |
| QA Agent Runner | tools/testing/qa_agent_runner.py | ACE qa_agent execution primitive — runs Playwright E2E suite, detects canvas coverage gaps, files kanban bug tasks for failures, persists results to ace_qa_runs/ace_qa_failures | --run [--canvas KEY] --json, --discover-gaps --json, --status RUN_ID --json | QARunResult JSON / gap list / run status |
| Selector Healer | tools/testing/selector_healer.py | AI-assisted Playwright locator repair — parses error output for broken selectors, proposes accessibility-tree replacements via vision LLM (confidence ≥ 0.7), applies via HITL-gated patch_file | --stderr-file FILE --json, --selector SEL --spec-file SPEC --screenshot PNG [--apply] --json | RepairProposal JSON |
| A11y Sweep (Section 508) | tools/testing/a11y_sweep.py | Section 508 / WCAG 2.1 accessibility sweep — injects vendored axe-core (tools/testing/vendor/axe/axe.min.js, MPL-2.0) into ~15 dashboard pages via headless Selenium, reports violations by impact, diffs vs committed baseline (tests/e2e/a11y_baseline.json). Warn-only by default; fails on NEW critical only under ICDEV_A11Y_ENFORCE=1. Self-skips with no live dashboard. Pytest wrapper: tests/e2e/test_a11y_section508.py | --json, --base-url URL, --update-baseline, --enforce, --no-screenshot | Sweep report JSON (violations by impact + NEW-vs-baseline diff) |

