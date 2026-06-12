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

