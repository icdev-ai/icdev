<!-- CUI // SP-CTI -->

# CLI Harmonization Gaps — Migration Canvas

**Classification:** CUI // SP-CTI
**Pipeline Reference:** pipe-d90684be
**Source:** Innovation Engine Introspective Scan (`source='introspective'`, `check IN ('cli_json_flag','cli_project_naming','db_path_centralization')`)
**Total Gaps Found:** 26
**Date:** 2026-05-08

---

## Summary

The introspective scan identified three categories of CLI drift across 26 tools:

| Check | Count | Severity | Impact |
|-------|-------|----------|--------|
| `cli_json_flag` | 19 | WARN | Missing `--json` flag breaks CI/automation pipelines and MCP tool chaining |
| `cli_project_naming` | 4 | WARN | `--project` instead of `--project-id` breaks scripted multi-project workflows |
| `db_path_centralization` | 3 | WARN | Hardcoded DB paths bypass `get_connection()`, break PostgreSQL and cross-env portability |

---

## Top 5 Priority Gaps (Concrete Harmonization Steps)

### P1 — `db_path_centralization`: `tools/mcp/unified_server.py`

**Current:** Hardcodes DB path (e.g., `sqlite3.connect('data/icdev.db')` or equivalent literal string).
**Problem:** `data/icdev.db` is the SQLite fallback only. Production uses PostgreSQL via `get_connection()`. Hardcoding bypasses the `ICDEV_DB_URL` env-var routing and will silently write to the wrong backend.
**Recommended Pattern:**
```python
# BEFORE (gap)
import sqlite3
conn = sqlite3.connect("data/icdev.db")

# AFTER (harmonized)
from tools.db.storage import get_connection
conn = get_connection()
```
**Steps:**
1. Replace all `sqlite3.connect(...)` calls with `from tools.db.storage import get_connection; conn = get_connection()`.
2. Remove any bare `import sqlite3` that served only as a DB connector (keep if used for type-checking only).
3. Run `python tools/testing/health_check.py --json` to confirm DB routing.
4. Run `python tools/workflow/coherence_checker.py --all --fix --gate`.

---

### P2 — `db_path_centralization`: `tools/govcon/capture_ai_blueprint.py`

**Current:** 3 tools hardcode DB paths.
**Problem:** Same portability issue as P1 — PostgreSQL production backend is bypassed.
**Recommended Pattern:** Identical to P1 (replace `sqlite3.connect(...)` with `get_connection()`).
**Steps:** Same as P1. Additionally verify that the `capture_ai_blueprint.py` CLI's `--json` output still serialises correctly after the DB swap (run `python tools/govcon/capture_ai_blueprint.py --generate --json | python -m json.tool`).

---

### P3 — `cli_project_naming`: `tools/infra_canvas/emit.py`

**Current:** Uses `--project` as the flag name (4 tools in this category total).
**Problem:** The rest of the ICDEV toolchain uses `--project-id`. Scripts that pass `--project-id` to a mixed invocation list fail silently when `emit.py` receives an unknown flag.
**Recommended Pattern:**
```python
# BEFORE (gap)
parser.add_argument("--project", required=True, help="Project ID")

# AFTER (harmonized)
parser.add_argument("--project-id", required=True, dest="project_id", help="Project ID")
```
**Steps:**
1. Rename `--project` → `--project-id` (with `dest="project_id"`) in the argparse definition.
2. Add a deprecated alias for backwards compat during a 30-day grace window (optional):
   ```python
   parser.add_argument("--project", dest="project_id", help=argparse.SUPPRESS)
   ```
3. Update any callers in `goals/`, `tools/`, and CI scripts.
4. Bump the tool's version comment (`# v1.x → v1.x+1 — harmonized --project-id`).

---

### P4 — `cli_project_naming`: `tools/simulation/cascade_bridge.py` and `tools/simulation/risk_monitor.py`

**Current:** Both use `--project` (confirmed in source: `parser.add_argument("--project", required=True, ...)`).
**Problem:** These are called by the Kanban scheduler and Genesis reflexes — any `--project-id` invocation from a reflex breaks the run silently.
**Recommended Pattern:** Same as P3.
**Steps:**
1. Apply rename in both files simultaneously (single commit, same PR).
2. Check Genesis reflex invocations in `tools/genesis/reflexes/` and `tools/fathomdesk/reflexes/` for `--project` references.
3. Update `docs/reference/commands.md` entries for both tools.
4. Verify with `python tools/simulation/cascade_bridge.py --help | grep project` showing `--project-id`.

---

### P5 — `cli_json_flag`: Core scheduler / proposal daemon (`tools/genesis/kanban_scheduler.py`, `tools/proposal_genesis/daemon.py`)

**Current:** Missing `--json` flag. The scheduler and daemon are invoked headlessly by `tools/anvil/` wrappers and Kanban task runners that parse JSON output.
**Problem:** Without `--json`, callers must parse unstructured stdout. Any log-format change silently breaks downstream parsers.
**Recommended Pattern:**
```python
# Add to argparse block
parser.add_argument("--json", action="store_true", help="Emit JSON output")

# Wrap final output
if args.json:
    import json, sys
    print(json.dumps(result))
else:
    print(f"[OK] {result}")
```
**Steps:**
1. Add `parser.add_argument("--json", action="store_true", help="Emit JSON output")` to each file's `argparse` block.
2. Wrap all terminal `print()` calls with the json/human branch.
3. For daemon processes that loop indefinitely, emit a JSON status line on each tick when `--json` is set (useful for `tools/anvil/` polling).
4. Add an entry to `tools/manifest/memory-system.md` or the appropriate topic shard noting the flag was added.
5. Run `python tools/genesis/kanban_scheduler.py --json` and confirm valid JSON is emitted.

---

## Full Gap Table (All 26)

| # | Check | Tool Path | Current CLI Flags | Recommended Harmonized Pattern | Priority |
|---|-------|-----------|-------------------|-------------------------------|----------|
| 1 | `db_path_centralization` | `tools/mcp/unified_server.py` | `sqlite3.connect(...)` direct | `get_connection()` from `tools.db.storage` | **HIGH** |
| 2 | `db_path_centralization` | `tools/govcon/capture_ai_blueprint.py` | `sqlite3.connect(...)` direct (3 sites) | `get_connection()` from `tools.db.storage` | **HIGH** |
| 3 | `db_path_centralization` | `tools/sre/seed_runbooks.py` | `sqlite3.connect(...)` direct (3 sites) | `get_connection()` from `tools.db.storage` | **HIGH** |
| 4 | `cli_project_naming` | `tools/infra_canvas/emit.py` | `--project` | `--project-id` (dest=`project_id`) | **HIGH** |
| 5 | `cli_project_naming` | `tools/simulation/cascade_bridge.py` | `--project` | `--project-id` (dest=`project_id`) | **HIGH** |
| 6 | `cli_project_naming` | `tools/simulation/risk_monitor.py` | `--project` | `--project-id` (dest=`project_id`) | **HIGH** |
| 7 | `cli_project_naming` | `tools/llm/cost_intelligence.py` | `--project` | `--project-id` (dest=`project_id`) | **HIGH** |
| 8 | `cli_json_flag` | `tools/genesis/kanban_scheduler.py` | Missing `--json` | Add `--json` flag with JSON/human output branch | **MEDIUM** |
| 9 | `cli_json_flag` | `tools/proposal_genesis/daemon.py` | Missing `--json` | Add `--json` flag with JSON/human output branch | **MEDIUM** |
| 10 | `cli_json_flag` | `tools/builder/code_generator.py` | Missing `--json` | Add `--json` flag with JSON/human output branch | **MEDIUM** |
| 11 | `cli_json_flag` | `tools/appforge/reflexes/build.py` | Missing `--json` | Add `--json` flag with JSON/human output branch | **MEDIUM** |
| 12 | `cli_json_flag` | `tools/planning/design_twice.py` | Missing `--json` | Add `--json` flag with JSON/human output branch | **MEDIUM** |
| 13 | `cli_json_flag` | `tools/fathomdesk/openbb_gateway.py` | Missing `--json` | Add `--json` flag with JSON/human output branch | **MEDIUM** |
| 14 | `cli_json_flag` | `tools/trading/options/genesis_daemon.py` | Missing `--json` | Add `--json` flag with JSON/human output branch | **MEDIUM** |
| 15 | `cli_json_flag` | `tools/verify_manifest.py` | Missing `--json` | Add `--json` flag with JSON/human output branch | **MEDIUM** |
| 16 | `cli_json_flag` | `tools/strategos/adsb_importer.py` | Missing `--json` | Add `--json` flag with JSON/human output branch | **MEDIUM** |
| 17 | `cli_json_flag` | `tools/strategos/ground_vehicle_importer.py` | Missing `--json` | Add `--json` flag with JSON/human output branch | **MEDIUM** |
| 18 | `cli_json_flag` | `tools/strategos/tle_importer.py` | Missing `--json` | Add `--json` flag with JSON/human output branch | **MEDIUM** |
| 19 | `cli_json_flag` | `tools/strategos/uas_importer.py` | Missing `--json` | Add `--json` flag with JSON/human output branch | **MEDIUM** |
| 20 | `cli_json_flag` | `tools/studio/wne/export_pack_generator.py` | Missing `--json` | Add `--json` flag with JSON/human output branch | **MEDIUM** |
| 21 | `cli_json_flag` | `tools/infra_canvas/preapply_gate.py` | Missing `--json` | Add `--json` flag with JSON/human output branch | **MEDIUM** |
| 22 | `cli_json_flag` | `tools/network/backup.py` | Missing `--json` | Add `--json` flag with JSON/human output branch | **LOW** |
| 23 | `cli_json_flag` | `tools/trading/dashboard/app.py` | Missing `--json` | Add `--json` flag with JSON/human output branch | **LOW** |
| 24 | `cli_json_flag` | `tools/trading/auth/admin_cli.py` | Missing `--json` | Add `--json` flag with JSON/human output branch | **LOW** |
| 25 | `cli_json_flag` | `tools/trading/billing/admin_cli.py` | Missing `--json` | Add `--json` flag with JSON/human output branch | **LOW** |
| 26 | `cli_json_flag` | `tools/trading/migrations/add_user_id_to_legacy_tables.py` | Missing `--json` | Add `--json` flag with JSON/human output branch | **LOW** |

---

## Harmonized Pattern Reference

### `--json` Flag (ICDEV Standard)

All `tools/` scripts that produce terminal output MUST support `--json` for machine-readable output:

```python
import argparse, json, sys

def main():
    parser = argparse.ArgumentParser(description="Tool description")
    # ... other args ...
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    args = parser.parse_args()

    result = do_work()

    if args.json:
        print(json.dumps(result))
    else:
        print(f"[OK] {result['summary']}")
```

### `--project-id` Naming (ICDEV Standard)

Use `--project-id` (not `--project`) as the canonical flag for project identifiers:

```python
parser.add_argument("--project-id", required=True, dest="project_id",
                    help="ICDEV project ID")
```

### DB Connection (ICDEV Standard)

Always use `get_connection()` — never `sqlite3.connect()` for `icdev.db`:

```python
from tools.db.storage import get_connection

conn = get_connection()   # routes to SQLite or PostgreSQL per ICDEV_DB_URL
```

---

## Remediation Backlog

Estimated effort to close all 26 gaps:

| Category | Gaps | Est. Files Changed | Est. Hours |
|----------|------|--------------------|------------|
| `db_path_centralization` | 3 | 3 | 1–2 h |
| `cli_project_naming` | 4 | 4–8 (callers) | 2–3 h |
| `cli_json_flag` (MEDIUM) | 13 | 13 | 3–4 h |
| `cli_json_flag` (LOW) | 6 | 6 | 1–2 h |
| **Total** | **26** | **~26–31** | **7–11 h** |

Suggested Kanban decomposition: one task per check category per subsystem (3–4 atomic tasks total), each ≤300 words, with a companion-sync chore at epic close.

---

*Generated by ICDEV™ Innovation Engine — introspective scan pipeline pipe-d90684be.*
*Classification: CUI // SP-CTI*
