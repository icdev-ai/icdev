# Logging System

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

ICDEV™ structured-logging stack: a single NDJSON logging standard
(`get_logger`), build-result capture, an append-only `centralized_logs`
DB sink (RLS-aware), a shared read/query surface, and the Genesis
`log_triage` autofix bridge that turns ERROR/CRITICAL signatures into
remediation cards.

## Data flow

```
component code ──get_logger()──▶ .logs/<component>.ndjson  ┐
pytest / Playwright ──build_logger──▶ .logs/build.ndjson   │
                                                           ├─▶ centralized_logs  (migration 181, append-only, RLS)
                                                           │        │
                                                           │        ├─ log_query.query_logs()  (CLI + read path)
                                                           │        └─ log_triage.run_centralized()  (autofix bridge)
```

`centralized_logs` is a **global** table (`get_connection`, not
`get_canvas_connection`) carrying `tenant_id` + `classification` for RLS,
and is **append-only** — registered in `APPEND_ONLY_TABLES` in
`.claude/hooks/pre_tool_use.py`. Never UPDATE/DELETE its rows.

## Tools

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| ICDEV Logger | tools/logging/icdev_logger.py | Central NDJSON logger (LOG-01). `get_logger(component)` is the only sanctioned way to obtain a logger — never `logging.getLogger()` directly. Reads config (level, log_dir, rotation, per-component overrides), builds rotating NDJSON file handlers. `invalidate_cache()` resets the config cache for tests. | get_logger(component) / invalidate_cache() | `logging.Logger` writing NDJSON |
| Build Logger | tools/logging/build_logger.py | Captures pytest + Playwright results as NDJSON build events (LOG-04). `capture_pytest()` / `capture_playwright()` write structured events to `.logs/build.ndjson` so the `log_triage` reflex can detect failures and open remediation cards. Parses failure lines; explicit counts override parsing. | capture_pytest(rc, output, ...) / capture_playwright(rc, output) | `.logs/build.ndjson` events |
| Log Query | tools/logging/log_query.py | Single read path over the append-only `centralized_logs` sink (RLS via `get_connection`) across ALL components. Backs the `/logs` dashboard page, the `GET /api/logs` route, the IQE `logs.entries` adapter, and a `__main__` CLI. (eqo-log-04) | --component, --level, --since, --contains, --limit, --json | Log rows (newest first) |
| Logging Constants | tools/logging/constants.py | Single source of truth for the query surface: `LOG_LEVELS`, `LEVEL_RANK`, `DEFAULT_LIMIT`/`MAX_LIMIT`, `LOGS_TABLE`, `FEATURE_FLAG`. Shared by `log_query`, the `/logs` blueprint, and the IQE adapter so the level vocabulary and query bounds are defined once. (eqo-log-04) | (import only) | Module-level constants |
| Swallowed-Persistence Detector | tools/refactor/swallowed_persistence.py | Library (no CLI). Shared AST detector for `except Exception: pass` guarding an `INSERT` (swp-swallow-01). `find_sites(paths, root)` returns one `SwallowSite` per broad handler whose body is exactly `pass` over a try block that writes. Narrow handlers, handlers that re-raise/return, and handlers that already log are not flagged. Skips `migrations/`, `tests/`, and its own three self-referential modules; reads through a UTF-8 BOM (a BOM makes `ast.parse` raise, which is how sites dodged earlier gates). Imported by BOTH the fixer and `coherence_checker.check_swallowed_persistence` so gate and tool can never disagree. | find_sites(paths, project_root) | `List[SwallowSite]` |
| Swallowed-Persistence Fixer | tools/refactor/fix_swallowed_persistence.py | Codemod that keeps best-effort persistence and makes it audible: `except Exception:` / `pass` → `except Exception as exc:` / `logger.warning("<fn>: best-effort INSERT into <table> failed (non-blocking): %s", exc)`. Adds `get_logger` + a module logger only when the module has none; reuses an existing `logger`/`log`/`LOGGER`; picks `_exc`/`_persist_exc` when `exc` is already bound in scope (an `as` binding is deleted on handler exit); falls back to `_persist_logger` when a function binds `logger` locally. Re-parses its own output and refuses to write a file whose module logger did not land at module level. Preserves the author's trailing comment, the file's line endings, and line width ≤120. A bare `except:` is detected but not rewritten — it is logged and skipped. | --path (repeatable), --write, --dry-run, --json | `{files_changed, sites_fixed, files, sites, errors}` |
| Swallowed-INSERT CLI Gate | tools/dev/check_swallowed_inserts.py | Standalone CLI over `swallowed_persistence.find_sites` (swp-swallow-01-d1) — prints one `file:line: ...` per violation and exits **0 clean / 1 violations / 2 bad path**. Deliberately a wrapper, not a second scanner: the detection rules stay in the one module the codemod and the coherence gate both import, so a third definition can never drift. Use when a shell, pre-commit hook, or air-gapped CI stage needs the verdict without loading the whole coherence harness; `coherence_checker --check swallowed_persistence` remains the in-pipeline gate. | --path (repeatable), --root, --json | Text `file:line` lines, or `{clean, count, scanned, violations[]}` |
| Swallowed-Insert Check (CLI) | tools/dev/check_swallowed_inserts.py | Developer-facing CLI over the shared detector — owns no detection logic of its own, so it cannot drift from the gate (swp-swallow-01-d1). Prints one `file:line` per violation plus the enclosing function and target table. Exit `0` clean, `1` violations, `2` the detector could not be imported (distinct on purpose: a check that reports clean because its detector vanished is the failure mode the card exists to close). Defaults to `tools/` (~71s); `--path` scopes a subtree (`tools/govcon` ≈1s) for inner-loop use. Build-blocking in CI since swp-swallow-01-d2: the `Swallowed-INSERT gate` step in the `test` job of `.github/workflows/icdev-ci.yml`, after `lint` and before pytest. It sits in `test` rather than `lint` because the detector is reached through `tools/__init__.py` (imports LLMRouter at import time) and `lint` installs only ruff. `coherence_checker --check swallowed_persistence` remains the repo-wide gate. | --path (repeatable), --json | `{status, count, scanned, violations[]}` |
| Log-Triage Reflex (autofix bridge) | tools/genesis/reflexes/log_triage.py | Genesis reflex (every 30m). **Path 1:** build log `.logs/build.ndjson` → `task_type='bug'` cards. **Path 2 (eqo-log-05):** reads ERROR/CRITICAL from `centralized_logs` across ALL components via `query_logs`, scores + dedups by `(component, message_hash[:8])`, opens one `task_type='fix'` card per NEW signature with component, log excerpt, and a reproduce query. `run_centralized()` is independently callable; each path keeps a separate seen-store. | run(config, trust) / run_centralized(config, ...) | {tasks_created, fix_cards_created, centralized:{...}} |

## Migration

| Migration | Path | Notes |
|-----------|------|-------|
| 181 — centralized_logs | tools/db/migrations/181_centralized_logs/ | Append-only RLS-aware global log sink (`get_connection`); `tenant_id` + `classification` columns. Reversible. Existing DBs: `python tools/db/migrate.py --mark-applied 181`. Registered in `APPEND_ONLY_TABLES`. |

## EQO logging epic — remaining surfaces (not yet on this branch)

The query/sink/triage layer above is live. The following consuming
surfaces are part of the EQO logging epic and are tracked separately;
they are **not present on this branch** (lost to concurrent worktree
churn and owned by their original cards):

- **`log_ingest` reflex (eqo-log-02)** — tails `.logs/*.ndjson` into
  `centralized_logs`. Until it lands, the sink is populated only by
  direct writers; `log_query`/`log_triage` read whatever rows exist.
- **`/logs` dashboard page + `GET /api/logs` route (eqo-log-03)** — the
  human-facing surface that `log_query` and `constants` already back.
- **IQE `logs.entries` adapter (eqo-log-04)** — registers the logs
  collection for `POST /api/iqe-query`.

When those cards merge, fold their rows into the **Tools** table above
and drop this section.
