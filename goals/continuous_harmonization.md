# CUI // SP-CTI
# Goal: Continuous Harmonization — CLI Consistency & Code Quality

> Ensure all ICDEV tools follow consistent CLI patterns, naming conventions,
> and output formats so developers can trust every tool behaves predictably.

---

## Why

ICDEV has 200+ tools built across 45 phases. Without continuous enforcement,
conventions drift: some tools use `--project`, others `--project-id`; some
support `--json`, others don't; some hardcode DB paths, others use env vars.
Every inconsistency is a papercut that slows adoption and erodes trust.

Harmonization is not a one-time task. It is a **continuous quality signal**
that must be detected, scored, and resolved through the Innovation Engine.

---

## Standards (Enforced)

### 1. CLI Flag Naming
| Pattern | Standard | Example |
|---------|----------|---------|
| Project identifier | `--project-id` | `--project-id "proj-123"` |
| Project directory | `--project-dir` | `--project-dir /path/to/code` |
| Project file path | `--project-path` | `--project-path /path/file` |
| JSON output | `--json` | `--json` (always `action="store_true"`) |
| Human output | `--human` | `--human` (colored terminal) |
| Database path | `--db-path` | `--db-path /path/to/db` |

**Backward compat:** When renaming `--project` to `--project-id`, keep
`--project` as a deprecated alias via `dest="project_id"`.

### 2. DB Path Resolution
All tools MUST use the centralized utility:
```python
from tools.compat.db_utils import get_icdev_db_path
DB_PATH = get_icdev_db_path()  # env var > explicit > default
```

Never hardcode `Path(__file__).resolve().parent.parent.parent / "data" / "icdev.db"`.

### 3. JSON Output Contract
Every CLI tool with `argparse` MUST support `--json`:
```python
parser.add_argument("--json", action="store_true", help="JSON output")
# ...
if args.json:
    print(json.dumps(result, indent=2, default=str))
```

JSON output MUST be a dict with at minimum: `{"status": "ok"|"error", ...}`.

### 4. CUI Headers
Every `.py` file MUST start with:
```python
#!/usr/bin/env python3
# CUI // SP-CTI
```

### 5. Error Handling
CLI tools MUST exit with code 0 on success, non-zero on failure.
Errors in JSON mode: `{"status": "error", "error": "message"}`.

---

## Detection (Automated)

### Governance Validator
`python tools/testing/claude_dir_validator.py --json` runs 6+ checks
including CLI harmonization. Blocked by `claude_config_alignment` gate.

### Introspective Analyzer
The Innovation Engine's introspective analyzer (`tools/innovation/introspective_analyzer.py`)
scans for CLI pattern drift every 12 hours and generates innovation signals
with category `cli_harmonization`.

### Signals Generated
| Signal | Category | Auto-Triage |
|--------|----------|-------------|
| Tool missing --json flag | cli_harmonization | yes |
| Tool using --project instead of --project-id | cli_harmonization | yes |
| Tool hardcoding DB path | cli_harmonization | yes |
| Tool missing CUI header | cli_harmonization | yes |

---

## Resolution Process

1. **Detect** — Governance validator or introspective analyzer flags drift
2. **Score** — Innovation Engine scores signal (typically 0.6-0.8 feasibility)
3. **Fix** — Developer or CI/CD auto-fixer applies the standard pattern
4. **Verify** — Governance validator confirms fix, signal marked resolved
5. **Prevent** — Pre-tool-use hook blocks new tools that violate standards

---

## Cadence

| Activity | Frequency | Owner |
|----------|-----------|-------|
| Governance validator run | Every CI/CD build | Automated |
| Introspective CLI scan | Every 12 hours | Innovation Engine daemon |
| Manual harmonization review | Per PI (every 2 weeks) | Tech lead |
| Standards update | Quarterly or on new phase | Architect agent |

---

## Tools

| Tool | Purpose |
|------|---------|
| `tools/testing/claude_dir_validator.py` | Governance enforcement (6+ checks) |
| `tools/compat/db_utils.py` | Centralized DB path resolution |
| `tools/innovation/introspective_analyzer.py` | CLI drift detection signals |
| `tools/cli/output_formatter.py` | `--human` colored output helper |

---

## Security Gate

The `claude_config_alignment` gate in `args/security_gates.yaml` blocks on:
- `append_only_table_unprotected` — new append-only table without hook protection
- `hook_syntax_error` — broken hook file
- `hook_reference_missing` — settings.json references nonexistent hook
- `cli_pattern_violation` — tool violates naming/output standards (NEW)

---

## Metrics

| Metric | Target | Current |
|--------|--------|---------|
| Tools with --json support | 100% | ~77% (237/306) |
| Tools using --project-id (not --project) | 100% | ~77% (110/142) |
| Tools using db_utils.py | 100% | ~66% (80/122 DB tools) |
| CUI headers present | 100% | ~98% |
| Governance validator pass rate | 100% | 100% (6/6) |
