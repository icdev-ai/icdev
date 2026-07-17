# PG-Primary Runtime Migration — Audit & Plan

**Goal (user directive):** everything on PostgreSQL; SQLite only as backup/fallback.
**Method:** `pg_portability_linter` repo-wide scan (533 findings) → filtered to runtime
(excl. migrations/seeds/init/tests) → 424 findings / ~90 files → classified.

## Classification

| Class | Meaning | Count | Action |
|-------|---------|------:|--------|
| **JSON** | `json_extract()` in runtime (pgp-tx-03 high) | 4 sites / 2 files | Compute-in-Python or PG `jsonb` |
| **A** | raw `sqlite3.connect`/`sqlite_master`, **no backend abstraction** | 20 files | Migrate to `get_connection()` / PG-primary |
| **B** | already branch on backend (`get_connection`/`is_pg`/`STORAGE_BACKEND`) | 59 files | Verify SQLite is fallback-only; fix PRAGMA/`sqlite_master` |
| **False-positive** | connects to *external/user* SQLite, or SQLite-specific infra | subset of A | Keep; annotate `# pg-ok` |

**Confirmed false-positives (keep, do not migrate):**
- `tools/data_canvas/data_profiler.py` — profiles *user-provided* SQLite data sources.
- `tools/security/db_encryption.py` — SQLCipher-style encrypted **SQLite** wrapper (backup path).
- Any `*/demo_runner.py`, `testing/route_smoke.py` — dev/demo harnesses.

---

## Epics (prioritized)

### Epic PG1 — JSON runtime violations (pgp-tx-03 gate) — HIGHEST, smallest
The load-bearing JSON anti-pattern the gate explicitly blocks.
- `tools/dashboard/app.py:1677, 6301, 6302`
- `tools/network/network_ingester.py:415`
- Fix: read raw JSON column + `json.loads()` in Python, or PG-native `jsonb` behind `is_pg` branch (pattern already in `csp_monitor.py`, `network_ingester` node-id lookup).
- ~1 PR. Unblocks the strictest gate.

### Epic PG2 — Class A, unconditional-SQLite ICDEV storage (highest data risk)
Raw SQLite with no PG path → invisible to dashboard, breaks on PG. Convert to `get_connection()` (or `get_canvas_connection()` for canvas tables), `?`→`%s`, `sqlite_master`→`information_schema`, guard PRAGMA.
- **PG2a — NDC cluster (8):** `ndc/cloud_topology_overlay, config_alignment_analyzer, config_translator, eol_scanner, executive_summary_generator, migration_document_generator, port_mapping_generator, replacement_recommender` (exclude `demo_runner`).
- **PG2b — monitor/dashboard/iqe (4):** `monitor/async_alert_writer` (confirm: local WAL buffer vs storage), `dashboard/findings_aggregator`, `iqe/run`, `strategos/war_kg`.
- **PG2c — sdc/network (4):** `sdc/isso_gate`, `sdc/roi_calculator`, `network/connectivity_ref`, `network/sops`.
- Each file = one task (investigate → migrate → verify).

### Epic PG3 — Class B verification / fallback-only (bulk, lower risk)
Already backend-aware; confirm SQLite path is *only* a guarded fallback, fix stray `sqlite_master`/PRAGMA (`# pg-ok`). Cluster-sized tasks:
- **PG3a — govlift/* (11):** whole subsystem — one review task, likely batch fix.
- **PG3b — memory/* (5):** `memory_db, hybrid_search, memory_read, memory_write, auto_capture`.
- **PG3c — strategos importers (6):** `acled/economic/frontline/oryx_importer, historical_baselines, iw_bayesian/iw_scorers`.
- **PG3d — trading/news/* (3), data_canvas/* (3), conflict_mesh/* (2), testing/* (4), remainder.**

### Epic PG4 — PRAGMA / sqlite_master cleanup sweep (mechanical, medium)
236 PRAGMA + 90 `sqlite_master` medium findings across guarded runtime. After PG2/PG3, sweep the rest: guard PRAGMA behind `is_pg` (`# pg-ok`), replace runtime `sqlite_master` existence checks with `information_schema`/`to_regclass`. Update the linter baseline.

---

## Sequencing & guardrails
1. **PG1** first (unblocks pgp-tx-03 gate; tiny).
2. **PG2** next (real data-visibility bugs).
3. **PG3** in cluster batches (mostly verification).
4. **PG4** cleanup + baseline refresh.
- Every module: keep the SQLite branch as **guarded fallback** (user: "SQLite for backup"), never delete it — tests + init-fallback depend on it.
- Per-module: worktree → convert → `pg_portability_linter --file` PASS → tests → PR. No big-bang.
- Verify each finding isn't an **external-SQLite** or **SQLite-infra** false-positive before converting.

## Rough sizing
- PG1: 1 PR (~S). PG2: ~16 tasks / 3–4 PRs (M). PG3: ~5 cluster tasks / 3 PRs (M–L). PG4: 1–2 sweep PRs (S–M).
- Total ≈ 22–25 kanban tasks under a `pgrt-` prefix (PG Runtime).
