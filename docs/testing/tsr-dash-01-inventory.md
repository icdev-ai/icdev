# TSR DASH — DB seed + test-file inventory (tsr-dash-01-d1)

Diagnostic only. Produced 2026-07-31 on branch `kanban/tsr-dash-01-d1`, a worktree off `origin/main`
at `3dd996c57`. No source file was modified.

Two deliverables: (1) the three seed commands run clean in a fresh worktree, so `storage.py` does not
hang on a cold DB; (2) the list of `tests/` files that exercise the DASH epic —
`tools/{dashboard, browser, iqe, viz, slides}` plus the NAV project — selected by what each file
**imports**, not by filename.

## 1. Seed — all three commands green

Run from the worktree root with the SQLite pin. **The pin is required**: without
`ICDEV_STORAGE_BACKEND=sqlite` the seed reaches for PostgreSQL and half-lands, leaving a DB that
looks present but is missing tables.

```bash
export ICDEV_STORAGE_BACKEND=sqlite
export PYTHONPATH=<worktree root>
python tools/db/init_icdev_db.py
python tools/studio/init_db.py
python tools/db/migrations/311_studio_event_tables_rls_columns/up.py
```

| step | result |
|------|--------|
| `tools/db/init_icdev_db.py` | 524 tables created at `data/icdev.db`; 8 `wf_templates` + 3 `wf_document_templates` seeded |
| `tools/studio/init_db.py` | 16 `studio_*` tables created, 0 pre-existing |
| migration `311_studio_event_tables_rls_columns` | applied |

Artifact on disk: `data/icdev.db`, 8,925,184 bytes. `data/*.db` did not exist before this run — a
fresh worktree ships no database, which is exactly the cold-DB condition that hangs `storage.py`.

## 2. `tools/nav` is not a package

There is **no `tools/nav` directory** and **zero `tools.nav` references** anywhere in the repo
(`grep -rn 'tools\.nav\b' --include=*.py .` → 0 hits). The task named it alongside five real
packages, but NAV is a *project* (the nav-menu-readiness sweep), not an importable subsystem. Its
tests are therefore keyed by filename, not by import:

```regex
(?:^|[_/])nav(?:[_.]|$)
```

24 files match on filename while importing none of the five DASH packages. They are carried in the
slice as tier `NAV`. Anyone re-running this scan should not expect an import-based query to find them.

## 3. Exact patterns used

Discovery pass, run from the repo root:

```powershell
Get-ChildItem -Path tests -Recurse -Filter *.py -File | Select-String -Pattern '(?<![\w.])(?:icdev\.)?tools\.(?:dashboard|browser|iqe|viz|slides)(?![\w])' -List
```

Regex, verbatim. Matches the canonical `icdev.tools.*` namespace and the legacy `tools.*` shim, and
matches inside `mock.patch("...")` target strings as well as in import statements:

```regex
(?<![\w.])(?:icdev\.)?tools\.(?:dashboard|browser|iqe|viz|slides)(?![\w])
```

The lookbehind `(?<![\w.])` keeps `icdev.tools.iqe` from counting twice and rejects `foo_tools.viz`;
the lookahead `(?![\w])` keeps `tools.slides` from matching `tools.slides_export`.

Second pass, used to tell a real import from a patch-target string:

```regex
^\s*(?:from|import)\s+(?:icdev\.)?tools\.(dashboard|browser|iqe|viz|slides)(?![\w])
```

Select-String reports **267** matching files. The tiered scan reports A+B+C = 165+76+26 = **267**.
The two passes agree exactly, which is the check that the Python classifier did not drop files.

## 4. Result — and why the raw match count is not the slice

`267` of the `1988` files under `tests/` match the discovery regex. That number is **not** the DASH
slice. 26 of them only ever name a DASH package inside a `mock.patch` string — a `cortex` or
`databridge` test that patches `tools.dashboard.app.get_connection` is a CORTEX or DATABRIDGE test,
not a DASH one. The tiers separate them.

| tier | definition | files | run for DASH? |
|------|------------|-------|---------------|
| A | imports a DASH package and **no other** `tools.*` subsystem | 165 | **yes — this is the slice** |
| B | imports a DASH package **and** another `tools.*` subsystem | 76 | only when the failure is in DASH code |
| C | never imports one; only names it in a patch-target string | 26 | no — owned by another epic |
| NAV | filename-keyed NAV project, imports no DASH package | 24 | **yes — this is the slice** |

**The DASH slice is tier A + tier NAV = 189 files**, written to
[`tsr-dash-01-slice.txt`](tsr-dash-01-slice.txt), one path per line, repo-relative POSIX.

The file is `text` in `.gitattributes`, so the blob is LF but a Windows checkout renders it CRLF.
Strip the `\r` before consuming it — `while read -r f; do f="${f%$'\r'}"; ...` or
`tr -d '\r' < slice.txt | xargs pytest`. Without that, `[ -f "$f" ]` reports all 189 paths missing
and neither the read nor the test errors, so it reads as "the inventory is wrong" when the inventory
is fine.

Full per-file record — tier, which DASH packages it imports, which it only references, and which
non-DASH subsystems it pulls in — is in [`tsr-dash-01-inventory.json`](tsr-dash-01-inventory.json).

### Per-package counts

| package | any reference | real import | tier-A files |
|---------|---------------|-------------|--------------|
| `tools/dashboard` | 161 | 147 | 92 |
| `tools/browser` | 49 | 46 | 40 |
| `tools/iqe` | 47 | 41 | 24 |
| `tools/viz` | 11 | 9 | 7 |
| `tools/slides` | 10 | 8 | 7 |
| NAV (filename) | — | — | 24 |

Columns do not sum to the tier-A total: a file importing both `tools.dashboard` and `tools.iqe` and
nothing else is one tier-A file counted in two rows.

### What tier B is entangled with

Top non-DASH subsystems co-imported by the 76 tier-B files:

| subsystem | files |
|-----------|-------|
| `tools/db` | 32 |
| `tools/cortex` | 8 |
| `tools/llm` | 8 |
| `tools/genesis` | 7 |
| `tools/govcon` | 6 |
| `tools/security` | 5 |

`tools/db` dominates for the same reason it dominated the CORE inventory: patching
`tools.db.storage.get_connection` is the house style for any test that touches a database. A tier-B
file whose only non-DASH import is `tools.db` is closer to tier A than the tier label suggests.

## 5. Acceptance — sample imports do not hang

With the seeded DB in place, collection across all five packages:

```bash
python -m pytest --collect-only -q \
  tests/dashboard/test_api_blueprint_registration.py \
  tests/browser/test_backend.py \
  tests/test_iqe_executor.py \
  tests/slides/test_slides_engine.py \
  tests/viz/test_asset_generator.py
```

**91 tests collected in 0.62s**, wall clock 2.99s, exit 0. No hang, no collection error. This is
collection only — it proves the import path is clean, it does **not** claim the tests pass. Running
them is the next task's job.

## Reproducing

The scan script is not committed — it is a throwaway. To regenerate, the classifier is: for each
`tests/**/*.py`, apply the two regexes above; tier A if it has a DASH import and no other `tools.*`
import, B if it has both, C if it has a reference but no DASH import, NAV if the filename matches
the NAV pattern and no DASH package is imported.
