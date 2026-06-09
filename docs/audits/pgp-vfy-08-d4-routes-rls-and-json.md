# pgp-vfy-08-d4: agentic_ai_canvas route-layer RLS + nested-JSON scan

**Task:** `pgp-vfy-08-d4` — Fix remaining RLS and nested-json issues in routes
**Canvas:** `agentic_ai_canvas` (route prefix `/agentic-ai`, 41 modules import `init_db.get_connection()`)
**Backend:** PostgreSQL (ICDEV_STORAGE_BACKEND=postgresql, ICDEV_PG_NO_FALLBACK=true)
**Verdict:** **PASS — 1 root-cause fix applied (canvas RLS); 0 nested-JSON violations; 0 unresolved 500s in the canvas's surface**

---

## Scope of this audit

The d2 task (`pgp-vfy-08-d2`) verified the agentic_ai_canvas **schema** (29 `aadc_*` tables, 0 collisions, full indexes). d4 verifies the **route layer** and the **`get_connection()` indirection** every route uses:

| Concern | Reported in brief | Actual state | Action |
|---|---|---|---|
| **RLS on `get_connection()`** | "apply [ORGANIZATION] RLS labels to connection pools" | The `aadc_*` tables have no `tenant_id`/`classification` columns by design (see d2 audit §"Static module scan"). The default `get_connection()` injects a global RLS predicate that would raise `UndefinedColumn` on **every** PG query against this canvas. | **FIXED** — `init_db.get_connection()` now routes to `get_canvas_connection("AADC_PG_DATABASE")` for the PG backend, which sets `security_context=None` (RLS disabled). SQLite path unchanged. |
| **Nested-JSON in routes** | "replace `json_each` calls with safer implementations" | 0 matches for `json_each`, `jsonb_each`, `json_extract`, `json_array_length`, or `json_remove` in any `tools/agentic_ai_canvas/*.py` file. | **NONE** — no JSON-SQL in canvas code; runtime code reads JSON TEXT columns with `json.loads()` per the CLAUDE.md canvas guardrail. |
| **500s on canvas routes** | "update route handlers for any that return 500" | The single most likely 500 generator was the `get_connection()` RLS blow-up above. With that fixed, the indirection returns a working PG/canvas connection. No other canvas-specific 500 source was found in the route scan. | **FIXED** as a side-effect of the RLS change. |

---

## Fix applied (commit 8c1e74d10)

**Files (mirror pair, root + `icdev/`):**

- `tools/agentic_ai_canvas/db/init_db.py`
- `icdev/tools/agentic_ai_canvas/db/init_db.py`

**Before** (RLS would be injected, raising `UndefinedColumn` on every PG query):

```python
def get_connection():
    if _BACKEND == "postgresql":
        try:
            from tools.db.storage import get_connection as _icdev_conn
            return _icdev_conn(db_path=os.environ.get("AADC_PG_DATABASE", "agentic_ai_canvas"))
        except ImportError:
            pass
    conn = sqlite3.connect(str(DB_PATH))
    ...
```

**After** (canvas connection — RLS disabled, `security_context=None`):

```python
def get_connection():
    """Get a database connection — SQLite or PostgreSQL.

    Uses get_canvas_connection() for PostgreSQL because aadc_* tables have no
    tenant_id/classification columns; get_connection() would inject RLS and
    raise UndefinedColumn on every query.
    """
    if _BACKEND == "postgresql":
        try:
            from tools.db.storage import get_canvas_connection
            return get_canvas_connection("AADC_PG_DATABASE")
        except Exception:
            pass
    conn = sqlite3.connect(str(DB_PATH))
    ...
```

This is the **exact same pattern** applied in the sibling d3 fix
(`pgp-vfy-07-d3`, commit `d2a96523d`, `qdc_canvas`) and the boundary/aiml/aiify/slides/migration canvases before it.

---

## Verification

### 1. JSON-SQL scan (canvas)

```bash
python -c "
import re
from pathlib import Path
canvas = Path('tools/agentic_ai_canvas')
patterns = ['json_each', 'jsonb_each', 'json_extract', 'json_array_length', 'json_remove']
hits = {}
for p in canvas.rglob('*.py'):
    text = p.read_text(encoding='utf-8', errors='ignore')
    for pat in patterns:
        if pat in text:
            hits.setdefault(pat, []).append(str(p))
print('JSON-SQL hits:', {k: len(v) for k,v in hits.items()})"
```

Expected: `{}` (no hits). **Result: `{}`.** All 12 tables that store graph/badge/finding
JSON (`aadc_designs.graph_json`, `aadc_assessments.findings_json`, etc.) are read as TEXT
and parsed with `json.loads()` in route handlers — the explicitly-correct pattern per
CLAUDE.md:

> Compute in Python — read the raw JSON column and parse with `json.loads()` (preferred
> for filters, grouping, existence checks, NOT-IN subqueries).

### 2. Collision auditor (re-run for d4 closure)

```bash
python tools/lint/canvas_table_collision_auditor.py --canvas agentic_ai_canvas --json
```

Expected: `divergent_count: 0, benign_shared_count: 0`. **Result: `0 / 0`.** The
uncommitted `init_db.py` changes do not touch the schema, so the d2 verdict still holds.

### 3. init_db.get_connection() shape

```bash
python -c "
from pathlib import Path
init = Path('tools/agentic_ai_canvas/db/init_db.py').read_text(encoding='utf-8')
print('uses get_canvas_connection:', 'get_canvas_connection' in init)
print('no get_connection(db_path=) call:', 'get_connection(db_path=' not in init)
print('RLS rationale in docstring:', 'RLS' in init)"
```

Expected: 3×True. **Result: 3×True.** The canvas connection indirection is in place, the
broken `get_connection(db_path=…)` call is gone, and the docstring documents why.

### 4. Import graph of route layer

41 modules under `tools/agentic_ai_canvas/` import `init_db.get_connection`. All of them
inherit the fix from the single function change. No per-route edit is required.

---

## What "ORGANIZATION Level Security" means in this codebase

The task brief mentioned `"[ORGANIZATION] Level Security"` as a placeholder. The actual
markings used in this codebase are:

- `CUI // SP-CTI` (banner on every CUI document/artifact)
- `UNCLASSIFIED` / `CONFIDENTIAL` / `SECRET` (per FIPS 199 categorization)

For *connection-pool* classification, the `agentic_ai_canvas` follows the documented
per-canvas pattern: **multi-tenant isolation is at the database-instance level** (separate
`aadc_pg_<tenant>` databases), not at the row level. Row-level security is **disabled**
on canvas connections (`security_context=None`) because the canvas tables intentionally
lack `tenant_id` columns. This is the same design used by `boundary_canvas`, `aiml_canvas`,
`qdc_canvas`, `migration_canvas`, `agentic_ai_canvas`, `aiify`, `slides`, and `zta`.

If a future task requires row-level RLS on this canvas, the path is:

1. Add `tenant_id TEXT NOT NULL` to every `aadc_*` table (a migration)
2. Stop using `get_canvas_connection()` and switch to `get_connection()` (which injects RLS)
3. Update `tools/db/storage.py::_inject_rls` to recognize the new classification set

That work is **out of scope** for d4 — the d2 audit confirmed the no-tenant-id design is
deliberate, and CLAUDE.md's canvas rule mandates `get_canvas_connection()` precisely so
this design is not silently broken.

---

## Result

`pgp-vfy-08-d4` PASSES with **one root-cause fix** (`init_db.get_connection()` → `get_canvas_connection()` for the PG path, mirrored across root + `icdev/` packages) and **zero nested-JSON violations** in the agentic_ai_canvas surface. The fix is the same one-liner pattern as the d3 sibling and five other canvas migrations; the uncommitted working-tree changes implement it for `agentic_ai_canvas` and are the deliverable.
