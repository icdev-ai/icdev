# TSR CORE - subsystem test-file inventory (tsr-core-01-d2)

Diagnostic only. Produced 2026-07-31 on branch `kanban/tsr-core-01-d2`, a worktree off `origin/main`
at `5766e4748`. No source file was modified.

Answers: which `tests/` files exercise the CORE epic - `tools/{db, saas, testing, installer, cli,
config, compat, project, dx, billing, auth}` - selected by what each file **imports**, not by filename.

## Package list

| # | package | tests importing it (Tier A+B) |
|---|---------|-------------------------------|
| 1 | `tools/db` | 302 |
| 2 | `tools/saas` | 20 |
| 3 | `tools/testing` | 19 |
| 4 | `tools/installer` | 7 |
| 5 | `tools/cli` | 20 |
| 6 | `tools/config` | 15 |
| 7 | `tools/compat` | 3 |
| 8 | `tools/project` | 5 |
| 9 | `tools/dx` | 8 |
| 10 | `tools/billing` | 1 |
| 11 | `tools/auth` | 5 |

## Exact patterns used

Discovery, run from the repo root:

```powershell
Get-ChildItem -Path tests -Recurse -Filter *.py -File | Select-String -Pattern '(?<![\w.])(?:icdev\.)?tools\.(?:db|saas|testing|installer|cli|config|compat|project|dx|billing|auth)(?![\w])' -List
```

Regex, verbatim. Matches the canonical `icdev.tools.*` namespace and the legacy `tools.*` shim, and
matches inside `mock.patch("...")` target strings as well as in import statements:

```regex
(?<![\w.])(?:icdev\.)?tools\.(?:db|saas|testing|installer|cli|config|compat|project|dx|billing|auth)(?![\w])
```

The lookbehind `(?<![\w.])` keeps `icdev.tools.db` from counting twice and rejects `foo_tools.db`;
the lookahead `(?![\w])` keeps `tools.config` from matching `tools.configuration` and `tools.project`
from matching `tools.project_manager`.

Second pass, used to tell a real import from a patch-target string:

```regex
^\s*(?:from|import)\s+(?:icdev\.)?tools\.(db|saas|testing|installer|cli|config|compat|project|dx|billing|auth)(?![\w])
```

## Result - and why the raw match count is not the slice

`476` of the `1988` files under `tests/` match the discovery regex. That number is **not** the
CORE slice. `tools.db` alone accounts for 388 of them, because patching `tools.db.storage.get_connection`
is the house style for any test that touches a database - a Genesis or DIC test that patches
`get_connection` is a GEN or DOC test, not a CORE one. The three tiers separate them.

| tier | definition | files | run for CORE? |
|------|------------|-------|---------------|
| A | imports a CORE package and **no other** `tools.*` subsystem | 138 | **yes - this is the slice** |
| B | imports a CORE package **and** another `tools.*` subsystem | 249 | only when the failure is in CORE code |
| C | no CORE import at all; matches only inside a patch-target string | 89 | no - belongs to its own epic |

Tier A is 134 runnable test modules plus 4 support files (below).

### Tier A composition

| package | files |
|---------|-------|
| `tools/db` | 69 |
| `tools/saas` | 20 |
| `tools/testing` | 15 |
| `tools/cli` | 14 |
| `tools/config` | 9 |
| `tools/dx` | 7 |
| `tools/installer` | 6 |
| `tools/project` | 5 |
| `tools/compat` | 3 |
| `tools/auth` | 1 |

### Tier B - which epic each file actually belongs to

Top co-imported subsystems across the 249 Tier B files. Route the file to that epic; reach for it
under CORE only when the traceback lands in CORE code.

| co-imported subsystem | files |
|-----------------------|-------|
| `tools/genesis` | 28 |
| `tools/dashboard` | 24 |
| `tools/document_intelligence` | 21 |
| `tools/studio` | 17 |
| `tools/ace` | 17 |
| `tools/govcon` | 15 |
| `tools/doc_modernization` | 13 |
| `tools/network` | 13 |
| `tools/security` | 10 |
| `tools/boundary_canvas` | 10 |
| `tools/integrity` | 10 |
| `tools/kanban` | 8 |
| `tools/pipeline` | 8 |
| `tools/iqe` | 7 |
| `tools/security_canvas` | 7 |
| `tools/workflow` | 7 |
| `tools/observability_canvas` | 6 |
| `tools/mcp` | 5 |
| `tools/llm` | 5 |
| `tools/workflow_hitl` | 5 |

Full per-file tier assignment, including every Tier B and Tier C path with its packages, is in
[`tsr-core-01-inventory.json`](tsr-core-01-inventory.json).

## Targeted execution

The 134 runnable Tier A paths are in [`tsr-core-01-slice.txt`](tsr-core-01-slice.txt), one
per line, POSIX separators, repo-root-relative.

Seed the database first - an unseeded worktree **hangs** in `storage.py::execute` rather than failing:

```powershell
$env:ICDEV_STORAGE_BACKEND='sqlite'   # without this the seed silently half-lands
python tools/db/init_icdev_db.py
python tools/studio/init_db.py
python tools/db/migrations/311_studio_event_tables_rls_columns/up.py
```

`migrate.py --up` is not a substitute - roughly 25 migrations are PostgreSQL-only and fail on SQLite.

Then run the slice:

```powershell
$env:PYTHONPATH = 'C:\AI\ICDev'
$files = Get-Content docs/testing/tsr-core-01-slice.txt
pytest $files -v --tb=short
```

```bash
pytest $(cat docs/testing/tsr-core-01-slice.txt) -v --tb=short
```

`tsr-core-01-slice.txt` is written with LF endings on purpose: with CRLF, `$(cat ...)` hands pytest paths
with a trailing `\r` and every one fails as `file or directory not found`.

### Verified

The slice was seeded and collected in this worktree:

```
pytest $(cat docs/testing/tsr-core-01-slice.txt) --collect-only -q
-> 2188 tests collected in 51.64s, 0 errors
```

Collection is clean - no import errors, no missing paths. Warnings only, all pre-existing: several
`TestResult` / `TestFailure` dataclasses that pytest declines to collect because they have `__init__`,
and an unregistered `pytest.mark.live` in `tests/test_ace_session_smoke.py`. Nothing was executed;
pass/fail counts are `tsr-core-01-d3`'s job.

## Caveats

- **4 Tier A files are support, not tests**, and are excluded from `tsr-core-01-slice.txt`:
  `tests/_academy_conn.py`, `tests/_sql_compat.py`, `tests/api/conftest.py`, `tests/docmod/conftest.py`.
  pytest collects nothing from a `conftest.py` or an underscore-prefixed helper. `tests/_sql_compat.py`
  is on the list because it imports `tools.db` - it is the wrapper `tsr-core-01-d4` edits.
- **6 entries are top-level `tests/e2e_*.py` scripts**, not `test_*.py` modules, so a bare `pytest tests/`
  skips them. Named explicitly, as above, pytest collects them. They hit a live DB.
- **Tier assignment is textual.** A CORE import inside a function body still lands in the right tier,
  but a subsystem reached only through a re-export - `from tools.foo import bar` where `bar` itself
  calls into `tools/db` - is invisible to this scan.
- Counts are the state of `origin/main` at `5766e4748`. Re-run the discovery command after any merge
  that adds test files.

## Next

`tsr-core-01-d3` triages this Tier A list against the populated shared checkout versus a clean worktree,
separating ambient-DB-state fixtures from real defects.
