# PGP-CA-04 — Child-App Generator & Shipped-App SQLite-ism Audit

CUI // SP-CTI

**Task:** pgp-ca-04 — Audit existing generated child apps + child-app templates for SQLite-isms
**Date:** 2026-06-07
**Scope:** `child_app_registry` (shipped apps) + child-app code-generation templates
**Gate:** `pg_portability_linter` (pgp-tx-03) over `tools/builder` — zero HIGH findings

---

## 1. What "child-app templates" actually are

There is **no** `tools/builder/templates/` directory. The child-app generator uses a
**copy-and-adapt** pattern (Decision D21): it copies the parent `tools/` tree into the
child and *generates* the child's database init script from in-code table-DDL templates.
The "templates" the linter must be clean against are therefore the **two code
generators** that emit the child's DB layer:

| # | Template (emitter) | Emits |
|---|--------------------|-------|
| 1 | `tools/builder/db_init_generator.py` → `generate_init_script()` | Primary child `init_<name>_db.py` (core + per-capability table DDL, `init_db`, `migrate_add_capability`, CLI) |
| 2 | `tools/builder/child_app_generator.py` → `step_05_db_init_script()` (inline fallback) | Minimal child `init_<name>_db.py` used only when the sister module can't be imported |

Both are mirrored to `icdev/tools/builder/`.

The copied DB runtime (`tools/db/storage.py`, with `get_connection`/`translate_sql`) is
vendored into each child, so the child's generated init can route through the same
PG-primary + SQLite-init-fallback storage layer the parent uses.

---

## 2. Findings

### F1 — HIGH (correctness) — Generated init script called an undefined `get_connection()`

The mass-migration **commit `4b529c79`** ("Mass-migrate 460 files from
`sqlite3.connect()` to `get_connection()`") did a blind textual swap *inside the
generator's template strings*:

```diff
-    parts.append("    conn = sqlite3.connect(str(path))")
+    parts.append("    conn = get_connection()")
```

…in all four emit sites, and added `from tools.db.storage import get_connection`
**to the generator's own imports — not to the emitted child script.** Result: every
**regenerated** child `init_*_db.py` would:

- import `sqlite3` (now unused) but call **`get_connection()` — an undefined name → `NameError` at runtime**, and
- drop the `path` argument, so even if defined it would have written to the parent's
  configured DB instead of the child's own `data/<name>.db` (and, with no `.db` path,
  triggered RLS injection against child tables that have no `tenant_id`/`classification`).

The identical break exists in the inline fallback (F1b): `child_app_generator.py`
`step_05_db_init_script()` emitted `import sqlite3` + `conn = get_connection()`.

**Severity rationale:** any post-`4b529c79` regeneration produces a child that cannot
initialize its database. Latent because the 7 registered children were all generated
*before* the migration (or are ephemeral test stubs — see §3).

### F2 — MEDIUM (portability) — `executescript` / `sqlite_master` / hardcoded `.db`

The generated init uses `conn.executescript(...)`, `SELECT name FROM sqlite_master
WHERE type='table'`, and a hardcoded `DB_PATH = .../data/<name>.db`. These are **not**
defects once the connection routes through the `StorageConnection` wrapper:

- `StorageConnection.executescript()` splits + runs per-statement on PG (SAVEPOINT-isolated),
- `translate_sql` rewrites `SELECT name FROM sqlite_master WHERE type='table'` →
  `information_schema.tables`, and the table DDL's `AUTOINCREMENT`/`datetime('now')`/`BLOB`
  are translated for PG, and
- passing the child's own `.db` path keeps `get_connection()` on SQLite for that file
  with RLS skipped — the correct standalone-child behavior.

So F2 is resolved **by fixing F1 correctly** (route through the wrapper, pass the path).
No JSON-dialect SQL (`json_extract` / `json_array_length` / `json_each`) appears in either
template.

### F3 — HIGH (correctness, pre-existing) — Inline fallback emitted unparseable Python

The inline fallback's summary `print` used backslash-escaped quotes inside an f-string
expression:

```python
print(f"Tables created ({len(tables)}): {\", \".join(sorted(tables))}")
#                                         ^ SyntaxError: line continuation char
```

Every child produced via the fallback path would fail to `import`/run. Fixed by
precomputing the join into a local.

### F4 — MEDIUM (out of scope, noted) — `PRAGMA` in `dev_profile_manager.py`

`tools/builder/dev_profile_manager.py:113-114` issue connection-setup `PRAGMA`s. These
are MEDIUM (never gate; `translate_sql` no-ops PRAGMA on PG) and are **not** part of the
child-app code-generation path. Left as-is.

---

## 3. Shipped-app inventory (`child_app_registry`)

7 rows. Every "real" app was generated **before** the breaking commit (Mar 18); the rest
are ephemeral test stubs. **No shipped app on disk requires a patch:**

| Created | Name | Path | On disk? | Init script | Action |
|---------|------|------|----------|-------------|--------|
| 2026-03-15 | trading-engine | `.tmp\…\trading-engine` | No | — | none (gone) |
| 2026-03-15 | govchain | `…Temp\govchain\govchain` | No | — | none (gone) |
| 2026-03-15 | govchain | `…Temp\govchain2\govchain` | No | — | none (gone) |
| 2026-03-16 | tac | `D:\AI\TAC-ICDEV` | No | — | none (gone) |
| 2026-05-15 | test-child-sec | `…Temp\test-child-apps\…` | Yes | none found | none (test stub, no DB layer) |
| 2026-05-15 | test-sec-child | `.tmp\test-sec-child` | No | — | none (gone) |
| 2026-05-15 | test-sec-child | `.tmp\test-sec-child` | No | — | none (gone) |

(The two May-15 stubs were generated *after* `4b529c79` and would have carried the F1
break, but both were temp/test artifacts and are deleted/empty.)

---

## 4. Remediation applied

Both templates now emit a single backend-agnostic connection helper and use it,
passing the child's own DB path:

```python
def _get_db_connection(db_path):
    """Backend-agnostic connection (PG-primary, SQLite init-fallback)."""
    try:
        from tools.db.storage import get_connection
        return get_connection(str(db_path))
    except Exception:
        import sqlite3
        return sqlite3.connect(str(db_path))  # pg-ok: guarded standalone fallback
```

- **F1/F1b fixed:** `conn = get_connection()` → `conn = _get_db_connection(path)`; the
  `get_connection` import is now emitted *into the child*; unused top-level `import sqlite3`
  removed (imported lazily only in the guarded fallback, marked `# pg-ok`).
- **F2 resolved:** `executescript` + `sqlite_master` now run through the wrapper /
  native SQLite — portable either way.
- **F3 fixed:** fallback's f-string join precomputed into `_names`.
- Mirrored to `icdev/tools/builder/`.

Files changed:
- `tools/builder/db_init_generator.py` (+ `icdev/` mirror)
- `tools/builder/child_app_generator.py` (+ `icdev/` mirror)
- `tests/genesis_auto/test_db_init_generator.py` (regression tests)

---

## 5. Verification

- `pg_portability_linter` over `tools/builder`: **0 HIGH** (2 MEDIUM PRAGMA in
  `dev_profile_manager.py`, F4 — not gated, not a template).
- Regenerated child init scripts from **both** templates: `ast.parse` OK; `scan_file`
  reports **0 HIGH / 0 MEDIUM** (guarded fallback honored via `# pg-ok`).
- `tests/genesis_auto/test_db_init_generator.py` + `test_child_app_generator.py`:
  **47 passed** (37 existing + 3 new + 7 existing child-app), including new regression
  tests that assert the generated script parses, routes through the storage layer, and
  carries zero HIGH PG findings.

**Acceptance met:** report lists every affected template/app with severity; templates
remediated so a regeneration is PG-portable; linter reports zero HIGH over the child-app
code-generation path.
