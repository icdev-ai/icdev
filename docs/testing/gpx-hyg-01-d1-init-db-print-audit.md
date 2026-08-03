# gpx-hyg-01-d1 — `init_db.py` print-statement audit

**Status:** complete (read-only research). **Branch:** `kanban/gpx-hyg-01-d1-audit`. **Base:** `origin/main` @ 91b55eb64.

Machine-verified with `ast` (not grep alone), so multi-line `print(...)` calls are counted once 
and `print` inside comments/strings is excluded.


## Headline numbers

| Metric | Count |
|---|---:|
| `tools/*/db/init_db.py` files on disk | 34 |
| …of which contain **any** `print()` | 28 |
| …of which contain a **`[init_db]`-tagged** `print()` | **11** |
| Tagged `print()` calls in `tools/` | **61** |
| Tagged `print()` calls in the `icdev/` mirror | **61** |
| **Tagged prints to remediate, both namespaces** | **122** |
| Untagged `print()` calls in `tools/` (out of scope, listed below) | 41 |

### Reconciling the task premise

The card predicted *"11 files and ~120 print statements"*. Both hold, with one clarification that 
materially changes the remediation scope:

- **11 files** is exact — but only if you scope to prints carrying the `[init_db]` tag. 34 files match 
`tools/*/db/init_db.py`, and 28 of them print something.
- **~120** is exact (122) **only when you count both namespaces**. `tools/` alone holds 61. Every one of 
these files except `tools/bom/db/init_db.py` has a byte-identical twin under `icdev/tools/`, so any 
remediation must edit both copies or the mirror-parity gate will block the branch.

## Prior art — the target pattern already exists

`tools/data_canvas/db/init_db.py` already has three converted call sites. They are the reference shape 
for the follow-on fix (lazy `%s` interpolation, severity-appropriate level):

```python
logger.debug("[init_db] Skipping SQLite trigger DDL on PG: %s", _head)      # line 2721
logger.warning("[init_db] DDL statement failed on PG: %s | %s", _head, _e)  # line 2723
logger.warning("[init_db] Failed to build PG immutability triggers: %s", _e)  # line 2795
```
Note these are *not* in the 61 — they are already migrated. They are also the reason a naive 
`rg '\[init_db\]'` returns 64 rather than 61.


## The checklist — 61 tagged prints in 11 files

Each row is `(file_path, line_number, current_print)`. Line numbers are against `origin/main` @ 91b55eb64. 
Apply the identical edit at the same line in `icdev/<file_path>` (files are byte-identical).


### `tools/aiify/db/init_db.py` — 1 print(s)

- Prep: add `import logging`
- Mirror: `icdev/tools/aiify/db/init_db.py` exists — must be edited too

| Line | Current print |
|---:|---|
| 365 | `print(f"[init_db] AI-ify schema ready ({backend})", file=sys.stderr)` |

### `tools/aisg/db/init_db.py` — 1 print(s)

- Prep: add `import logging`; add module-level `logger = logging.getLogger(__name__)`
- Mirror: `icdev/tools/aisg/db/init_db.py` exists — must be edited too

| Line | Current print |
|---:|---|
| 190 | `print(f"[init_db] AISG schema ready ({_AISG_BACKEND})", file=sys.stderr)` |

### `tools/boundary_canvas/db/init_db.py` — 10 print(s)

- Prep: add `import logging`; add module-level `logger = logging.getLogger(__name__)`
- Mirror: `icdev/tools/boundary_canvas/db/init_db.py` exists — must be edited too

| Line | Current print |
|---:|---|
| 1664 | `print("[init_db] BDC schema created (PostgreSQL)", file=sys.stderr)` |
| 1685 | `print(f"[init_db] BDC schema created at {DB_PATH}", file=sys.stderr)` |
| 1702 | `print(f"[init_db] BDC seeded {added} new templates (total: {count + added}).", file=sys.stderr)` |
| 1704 | `print(f"[init_db] BDC all {count} templates up to date.", file=sys.stderr)` |
| 1720 | `print(f"[init_db] BDC seeded {snp_added} new snippets (total: {snp_count + snp_added}).", file=sys.stderr)` |
| 1722 | `print(f"[init_db] BDC all {snp_count} snippets up to date.", file=sys.stderr)` |
| 1749 | `print(f"[init_db] BDC seeded {rb_added} new runbooks (total: {rb_count + rb_added}).", file=sys.stderr)` |
| 1751 | `print(f"[init_db] BDC all {rb_count} runbooks up to date.", file=sys.stderr)` |
| 1757 | `print("[init_db] BDC SOPs seeded.", file=sys.stderr)` |
| 1759 | `print(f"[init_db] BDC SOP seed skipped: {_e}", file=sys.stderr)` |

### `tools/ccc_canvas/db/init_db.py` — 1 print(s)

- Prep: add `import logging`; add module-level `logger = logging.getLogger(__name__)`
- Mirror: `icdev/tools/ccc_canvas/db/init_db.py` exists — must be edited too

| Line | Current print |
|---:|---|
| 210 | `print(f"[init_db] CCC schema ready ({_CCC_BACKEND})", file=sys.stderr)` |

### `tools/cortex/db/init_db.py` — 1 print(s)

- Prep: add `import logging`
- Mirror: `icdev/tools/cortex/db/init_db.py` exists — must be edited too

| Line | Current print |
|---:|---|
| 342 | `print("[init_db] Cortex session + audit schema ready", file=sys.stderr)` |

### `tools/data_canvas/db/init_db.py` — 15 print(s)

- Prep: add `import logging`
- Mirror: `icdev/tools/data_canvas/db/init_db.py` exists — must be edited too

| Line | Current print |
|---:|---|
| 2800 | `print(f"[init_db] Data Canvas schema created at {DB_PATH}", file=sys.stderr)` |
| 2837 | `print(f"[init_db] Migration applied: dm_domains.{_col} added.", file=sys.stderr)` |
| 2852 | `print(f"[init_db] Migration applied: dm_data_products.{_col} added.", file=sys.stderr)` |
| 2863 | `print("[init_db] Migration applied: dd_explore_profiles.anomaly_json added.", file=sys.stderr)` |
| 2874 | `print("[init_db] Migration applied: dd_quality_runs.reflex_run added.", file=sys.stderr)` |
| 2900 | `print(f"[init_db] Migration applied: {_table}.{_col} added.", file=sys.stderr)` |
| 2922 | `print(f"[init_db] Seeded {added} new DDC templates (total: {count + added}).", file=sys.stderr)` |
| 2924 | `print(f"[init_db] All {count} DDC templates up to date.", file=sys.stderr)` |
| 2940 | `print(f"[init_db] Seeded {snp_added} new DDC snippets (total: {snp_count + snp_added}).", file=sys.stderr)` |
| 2942 | `print(f"[init_db] All {snp_count} DDC snippets up to date.", file=sys.stderr)` |
| 2973 | `print(f"[init_db] Seeded {rb_added} new DDC runbooks (total: {rb_count + rb_added}).", file=sys.stderr)` |
| 2975 | `print(f"[init_db] All {rb_count} DDC runbooks up to date.", file=sys.stderr)` |
| 3011 | `print(f"[init_db] Seeded {sop_added} new DDC SOPs (total: {sop_count + sop_added}).", file=sys.stderr)` |
| 3013 | `print(f"[init_db] All {sop_count} DDC SOPs up to date.", file=sys.stderr)` |
| 3022 | `print("[init_db] --reinit: applying schema migrations...", file=sys.stderr)` |

### `tools/dsoc_canvas/db/init_db.py` — 1 print(s)

- Prep: add `import logging`; add module-level `logger = logging.getLogger(__name__)`
- Mirror: `icdev/tools/dsoc_canvas/db/init_db.py` exists — must be edited too

| Line | Current print |
|---:|---|
| 195 | `print(f"[init_db] DSOC schema ready ({'postgresql' if pg else 'sqlite'})", file=sys.stderr)` |

### `tools/integrity/db/init_db.py` — 1 print(s)

- Prep: add `import logging`; add module-level `logger = logging.getLogger(__name__)`
- Mirror: `icdev/tools/integrity/db/init_db.py` exists — must be edited too

| Line | Current print |
|---:|---|
| 201 | `print("[init_db] SIPA integrity schema ready", file=sys.stderr)` |

### `tools/mission_canvas/db/init_db.py` — 1 print(s)

- Prep: add `import logging`; add module-level `logger = logging.getLogger(__name__)`
- Mirror: `icdev/tools/mission_canvas/db/init_db.py` exists — must be edited too

| Line | Current print |
|---:|---|
| 183 | `print("[init_db] Mission Canvas schema ready", file=sys.stderr)` |

### `tools/network/db/init_db.py` — 21 print(s)

- Prep: add `import logging`; add module-level `logger = logging.getLogger(__name__)`
- Mirror: `icdev/tools/network/db/init_db.py` exists — must be edited too

| Line | Current print |
|---:|---|
| 14654 | `print("[init_db] Schema created (PostgreSQL)", file=sys.stderr)` |
| 14675 | `print(f"[init_db] Schema created at {DB_PATH}", file=sys.stderr)` |
| 14727 | `print(f"[init_db] Migrated: added {col} to {table}", file=sys.stderr)` |
| 14765 | `print(f"[init_db] Seeded {added} new templates (total: {count + added}).", file=sys.stderr)` |
| 14767 | `print(f"[init_db] All {count} templates up to date.", file=sys.stderr)` |
| 14795 | `print(f"[init_db] Seeded {snip_added} new enclave snippets (total: {snip_count + snip_added}).", file=sys.stderr)` |
| 14797 | `print(f"[init_db] All {snip_count} enclave snippets up to date.", file=sys.stderr)` |
| 14840 | `print(f"[init_db] Seeded {doc_added} template docs.", file=sys.stderr)` |
| 14843 | `print(f"[init_db] All {existing} template docs up to date.", file=sys.stderr)` |
| 14856 | `print("[init_db] Default admin user created (username: admin, password: admin).", file=sys.stderr)` |
| 14916 | `print("[init_db] Seeded 3 review boards (ARB, ERB, CCB).", file=sys.stderr)` |
| 15112 | `print(f"[init_db] Seeded {len(_patterns)} design patterns.", file=sys.stderr)` |
| 15411 | `print(f"[init_db] Seeded {len(_profiles)} device profiles.", file=sys.stderr)` |
| 15517 | `print(f"[init_db] Seeded {len(_hw_profiles)} hardware profiles.", file=sys.stderr)` |
| 15564 | `print(f"[init_db] Seeded {len(_conventions)} naming conventions.", file=sys.stderr)` |
| 15736 | `print(f"[init_db] Seeded {len(_sops)} NDC SOPs.", file=sys.stderr)` |
| 15747 | `print(f"[init_db] Auto-seeded {result['seeded']} approved SOPs via seed_sops.", file=sys.stderr)` |
| 15749 | `print(f"[init_db] seed_sops auto-seed skipped: {_e}", file=sys.stderr)` |
| 15759 | `print("[init_db] Auto-seeded demo migration projects.", file=sys.stderr)` |
| 15761 | `print(f"[init_db] demo migration seed skipped: {_e}", file=sys.stderr)` |
| 15768 | `print("[init_db] Done.", file=sys.stderr)` |

### `tools/security_canvas/db/init_db.py` — 8 print(s)

- Prep: add `import logging`; add module-level `logger = logging.getLogger(__name__)`
- Mirror: `icdev/tools/security_canvas/db/init_db.py` exists — must be edited too

| Line | Current print |
|---:|---|
| 1577 | `print("[init_db] WARNING: Could not import ZIG constants — skipping ZIG seed.", file=sys.stderr)` |
| 1630 | `print(f"[init_db] ZIG seed: {p_added} pillars, {c_added} capabilities, {a_added} activities added.", file=sys.stderr)` |
| 1679 | `print("[init_db] Schema created (PostgreSQL)", file=sys.stderr)` |
| 1700 | `print(f"[init_db] Schema created at {DB_PATH}", file=sys.stderr)` |
| 1733 | `print(f"[init_db] Seeded {added} new templates (total: {count + added}).", file=sys.stderr)` |
| 1735 | `print(f"[init_db] All {count} templates up to date.", file=sys.stderr)` |
| 1750 | `print(f"[init_db] Seeded {snip_added} new snippets (total: {snip_count + snip_added}).", file=sys.stderr)` |
| 1752 | `print(f"[init_db] All {snip_count} snippets up to date.", file=sys.stderr)` |

## Out of scope — 41 untagged `print()` calls

These live in the same 34-file set but carry no `[init_db]` tag. Recorded so the follow-on task can decide 
explicitly whether to convert them; they are **not** part of the 122.

| File | Line | Current print |
|---|---:|---|
| `tools/ace/db/init_db.py` | 392 | `print(f"ACE canvas DB initialized: {len(rows)} tables")` |
| `tools/ace/db/init_db.py` | 394 | `print(f"  {r[col]}")` |
| `tools/agentic_ai_canvas/db/init_db.py` | 1049 | `print(f"AADC Canvas DB initialized: {len(tables)} tables")` |
| `tools/agentic_ai_canvas/db/init_db.py` | 1052 | `print(f"  {t}: {count} rows")` |
| `tools/aimc/db/init_db.py` | 99 | `print("AIMC DB initialised.")` |
| `tools/aiml_canvas/db/init_db.py` | 679 | `print( f"[AIMC init_db] Schema ready. {tpl_count} templates, " f"{snp_count} snippets.", file=sys.stderr, )` |
| `tools/bom/db/init_db.py` | 673 | `print(json.dumps(init_db(), indent=2))` |
| `tools/foundry/db/init_db.py` | 229 | `print("foundry schema:", "ok" if ok else "FAILED")` |
| `tools/govlift/db/init_db.py` | 344 | `print("GovLift DB: schema initialized OK", file=sys.stderr)` |
| `tools/govlift/db/init_db.py` | 346 | `print(f"GovLift DB init error: {exc}", file=sys.stderr)` |
| `tools/govlift/db/init_db.py` | 354 | `print("DB OK")` |
| `tools/infra_canvas/db/init_db.py` | 1462 | `print(f"IDC: seeded {len(_build_templates())} templates", file=sys.stderr)` |
| `tools/infra_canvas/db/init_db.py` | 1476 | `print(f"IDC: seeded {added} snippets", file=sys.stderr)` |
| `tools/infra_canvas/db/init_db.py` | 1482 | `print(f"IDC: seeded {rb_added} runbooks", file=sys.stderr)` |
| `tools/infra_canvas/db/init_db.py` | 1509 | `print("IDC database initialized.")` |
| `tools/migration_canvas/db/init_db.py` | 1969 | `print("Migration Canvas DB initialized at", DB_PATH)` |
| `tools/migration_intelligence/db/init_db.py` | 298 | `print(f"[mi_init_db] Migration Intelligence DB initialized ({_backend()})", file=sys.stderr)` |
| `tools/network/db/init_db.py` | 15778 | `print(_json.dumps({"status": "ok", "db": str(DB_PATH), "templates": len(TEMPLATES)}))` |
| `tools/noc_canvas/db/init_db.py` | 36 | `print( f"[nocc-db] PostgreSQL unavailable ({exc}), falling back to SQLite", file=sys.stderr, )` |
| `tools/noc_canvas/db/init_db.py` | 384 | `print("[nocc-db] Schema initialized OK", file=sys.stderr)` |
| `tools/noc_canvas/db/init_db.py` | 386 | `print(f"[nocc-db] Schema init error: {exc}", file=sys.stderr)` |
| `tools/noc_canvas/db/init_db.py` | 395 | `print(f"[nocc-db] Backend: {_NOCC_BACKEND}")` |
| `tools/nova/db/init_db.py` | 156 | `print(json.dumps(init_nova_tables(), indent=2))` |
| `tools/observability_canvas/db/init_db.py` | 922 | `print(json.dumps(result, indent=2))` |
| `tools/observability_canvas/db/init_db.py` | 924 | `print(f"Observability Canvas DB initialized: {DB_PATH}")` |
| `tools/observability_canvas/db/init_db.py` | 925 | `print(f"  Templates: {tpl_count}")` |
| `tools/observability_canvas/db/init_db.py` | 926 | `print(f"  Designs:   {design_count}")` |
| `tools/observability_canvas/db/init_db.py` | 927 | `print(f"  Assessments: {assessment_count}")` |
| `tools/ops_hub/db/init_db.py` | 181 | `print("[ohc-db] Schema initialized OK", file=sys.stderr)` |
| `tools/ops_hub/db/init_db.py` | 183 | `print(f"[ohc-db] Schema init error: {exc}", file=sys.stderr)` |
| `tools/ops_hub/db/init_db.py` | 192 | `print(f"[ohc-db] Database ready at {DB_PATH}", file=sys.stderr)` |
| `tools/pipeline/db/init_db.py` | 1719 | `print(json.dumps(result, indent=2))` |
| `tools/pipeline/db/init_db.py` | 1721 | `print(f"Pipeline Canvas DB initialized: {DB_PATH}")` |
| `tools/pipeline/db/init_db.py` | 1722 | `print(f"  Templates: {tpl_count}")` |
| `tools/pipeline/db/init_db.py` | 1723 | `print(f"  Snippets:  {snip_count}")` |
| `tools/pmc_canvas/db/init_db.py` | 36 | `print( f"[pmc-db] PostgreSQL unavailable ({exc}), falling back to SQLite", file=sys.stderr, )` |
| `tools/pmc_canvas/db/init_db.py` | 325 | `print("[pmc-db] Schema initialized OK", file=sys.stderr)` |
| `tools/pmc_canvas/db/init_db.py` | 327 | `print(f"[pmc-db] Schema init error: {exc}", file=sys.stderr)` |
| `tools/pmc_canvas/db/init_db.py` | 336 | `print(f"[pmc-db] Backend: {_PMC_BACKEND}")` |
| `tools/qdc_canvas/db/init_db.py` | 856 | `print(f"QDC Canvas DB initialized: {len(tables)} tables")` |
| `tools/qdc_canvas/db/init_db.py` | 859 | `print(f"  {t[0]}: {count} rows")` |

## Gotchas for the remediation task

1. **Mirror parity blocks every branch.** 33 of 34 files have a byte-identical `icdev/tools/` twin. Edit both 
or the parity gate fails. `tools/bom/db/init_db.py` is the sole exception — it has no mirror (and no tagged print).
2. **`tools/network/db/init_db.py` is the largest single job** — 21 tagged prints, a third of the work.
3. **Three files already define `logger`** (`aiify`, `cortex`, `data_canvas`) and need no prep; the other eight need 
both the import and the module-level logger.
4. **No file currently does `import logging` except `migration_canvas`**, which has a logging import but zero tagged prints.
5. **Use lazy `%s` interpolation**, not f-strings, to match the `data_canvas` prior art and avoid formatting cost on suppressed levels.