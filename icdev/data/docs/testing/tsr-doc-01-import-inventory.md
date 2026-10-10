# TSR DOC — document-intelligence test-file inventory (tsr-doc-01-d1)

Diagnostic only. Produced 2026-08-01 on branch `kanban/tsr-doc-01-d1-r3`, a worktree off
`origin/main` at `3616a861b`. No source or test file was modified.

Answers: which `tests/` files exercise the DOC epic — the document-intelligence,
document-modernization, writing/WriteGuard and content-quality `tools.*` packages — selected by what
each file **loads**, not by filename.

> **This supersedes the first tsr-doc-01-d1 inventory.** That run classified imports with the
> line-anchored regex `^\s*(?:from|import)\s+tools\.<pkg>`, which by construction cannot match
> `importlib.import_module("tools.<pkg>")`. It therefore filed 67 files as tier C "mention only"
> when **63 of them do load DOC code** — 60 through `importlib`, 3 through a `mock.patch` string
> target. The old `slice.txt` held 54 paths; the corrected slice holds 162. See
> [Correction](#correction-to-the-superseded-inventory).

## Databases seeded (acceptance criterion 2)

A fresh worktree starts with no `data/*.db`, so this is a prerequisite, not a formality. Run from the
worktree root:

```bash
export PYTHONPATH=<worktree root>        # else: ModuleNotFoundError: No module named 'tools'
export ICDEV_STORAGE_BACKEND=sqlite      # else the seed half-lands against Postgres
unset ICDEV_PG_NO_FALLBACK

python tools/db/init_icdev_db.py
python tools/studio/init_db.py
python tools/db/migrations/311_studio_event_tables_rls_columns/up.py
```

| # | command | exit | result |
|---|---------|------|--------|
| 1 | `tools/db/init_icdev_db.py` | **0** | `Tables created (525)`; 8 `wf_templates` + 3 `wf_document_templates` seeded |
| 2 | `tools/studio/init_db.py` | **0** | 16 studio tables (`studio_workflow_runs`, `studio_trigger_events`, …) |
| 3 | `migrations/311_studio_event_tables_rls_columns/up.py` | **0** | `Migration 311 applied.` |

All three exit 0, leaving **541 tables** in `data/icdev.db`.

Both env pins are load-bearing and must be repeated by any follow-on task in this worktree. The
ambient environment carries `ICDEV_STORAGE_BACKEND=postgresql`; an explicit env var beats
`load_dotenv`, so without the sqlite pin step 1 never populates the local SQLite file that
`tests/conftest.py` forces every test onto. Ordering matters, and `migrate.py --up` cannot substitute
for step 3 — roughly 25 migrations are PG-only and fail on SQLite.

## Scope

The card names six packages. **Five exist as named; one does not.**

| named by card | resolution |
|---------------|------------|
| `tools/document_intelligence` | exists — 38 top-level modules |
| `tools/doc_modernization` | exists — 20 top-level modules |
| `tools/writing` | exists — 33 top-level modules |
| `tools/quality` | exists — 13 top-level modules |
| `tools/dic` | exists, but **no test references it** — see below |
| `tools/wg` | **does not exist** → `tools.pulse.writeguard` substituted |

- **`tools/wg` does not exist.** "WG" is WriteGuard, whose code is split across `tools/writing/*`
  (already in scope — the analysis engines) and `tools/pulse/writeguard.py` (the
  `run_full_quality_check` entry point and the `writeguard_analyze` MCP adapter). The `wg_*` tables
  are real — `wg_analysis_results`, `wg_analysis_findings`, `wg_glossary`, `wg_style_guides`,
  `wg_style_profiles`, `wg_snippets`, `wg_batch_runs`, `wg_style_guide_locks` — but no `tools.wg`
  Python package backs them. Only `tools.pulse.writeguard` was substituted, not all of `tools.pulse`,
  which is a separate subsystem.
- **`tools/dic` exists but no test imports it.** The package is two files, `__init__.py` and
  `provenance_adapter.py`. The only `tools.dic` reference anywhere in the repo is a code sample in
  `tools/manifest/document-intelligence-canvas.md` (and its `icdev/` mirror). The Document
  Intelligence Canvas tests reach the canvas through `tools.document_intelligence.*` instead.
  `tools.dic` is kept in the regex so the zero is a measured result, not an omission.

## Method

Discovery runs recursively over every `*.py` under `tests/` (2,036 files) — not just `test_*.py`, so
`conftest.py` and `e2e_*.py` helpers are caught too.

```
(?<![\w.])(?:icdev\.)?tools\.(?:document_intelligence|doc_modernization|writing|dic|quality|wg|pulse\.writeguard)(?![\w])
```

The leading `(?<![\w.])` stops `tools.canvas_quality`-style false positives; the trailing `(?![\w])`
stops `tools.quality_extra`. Both the canonical `icdev.tools.*` and the legacy `tools.*` shim
namespaces are matched.

Each matching file is then **parsed with `ast`** and classified by how it actually reaches DOC code:

| signal | detection | why it counts |
|--------|-----------|---------------|
| static import | `ast.Import` / `ast.ImportFrom` | `from tools.quality import citation_grounding` |
| dynamic import | `import_module` / `__import__` / `reload` with a string literal | CLAUDE.md *mandates* `importlib.import_module("tools.x")` + `setattr` for shim-aware patching, so this is the recommended form here, not an oddity |
| patch target | `patch("tools.x.y.attr")` | `mock.patch` imports `tools.x.y` when the patch is entered |
| reference only | none of the above | the name appears in a docstring, a registry data tuple, or a `patch.dict(sys.modules, {...: None})` that *blocks* the import |

Counting only `ast.Import` is what produced the superseded inventory's undercount.

### The card's literal `tests\*.py` is too narrow

`Select-String` over `tests\*.py` reaches only the top-level files and misses everything under
`tests/docmod`, `tests/document_intelligence`, `tests/cortex`, `tests/govcon` and friends. That is
not cosmetic for this epic: **34 of the 166 matching files live in subdirectories**, including 10
tier-A files and the whole of `tests/docmod` (21 files) and `tests/document_intelligence` (3). The
recursive form is what was used.

## Results — 166 files match, 162 load DOC code

| # | package | files matching | load it | static | dynamic |
|---|---------|----------------|---------|--------|---------|
| 1 | `tools/document_intelligence` | 125 | 122 | 69 | 57 |
| 2 | `tools/quality` | 30 | 26 | 20 | 6 |
| 3 | `tools/doc_modernization` | 20 | 20 | 19 | 1 |
| 4 | `tools/pulse/writeguard` | 3 | 1 | 1 | 0 |
| 5 | `tools/writing` | 2 | 2 | 1 | 1 |
| 6 | `tools/dic` | 0 | 0 | 0 | 0 |
| — | `tools/wg` *(does not exist)* | 0 | 0 | 0 | 0 |

Per-file tiers (a file may hit several packages):

| tier | meaning | count | artifact |
|------|---------|-------|----------|
| **A** | loads DOC code and imports **no other** `tools.*` package | **68** | `tsr-doc-01-exclusive.txt` |
| **B** | loads DOC code, but also imports other `tools.*` packages | **94** | in `tsr-doc-01-slice.txt` |
| **C** | names a DOC package but never loads it | **4** | `tsr-doc-01-all-matches.txt` only |

99 files use a static import; 60 more reach DOC code *only* through `importlib`; 3 more only through
a `patch()` target.

The four tier-C files, all verified by hand:

| file | why it does not load DOC code |
|------|-------------------------------|
| `tests/test_cnr_docgen.py` | `patch.dict(sys.modules, {"tools.pulse.writeguard": None})` — deliberately blocks the import to exercise the fallback |
| `tests/test_cnr_mission_canvas.py` | prose in a docstring |
| `tests/test_component_registry.py` | a registry data tuple naming the blueprint path |
| `tests/test_release_orchestrator.py` | a `bytes` literal of source text being scanned |

## Artifacts

| file | lines | contents |
|------|-------|----------|
| `tsr-doc-01-slice.txt` | 162 | the run list — every file that loads DOC code (tiers A+B) |
| `tsr-doc-01-exclusive.txt` | 68 | tier A only — safe to run as an isolated DOC slice |
| `tsr-doc-01-all-matches.txt` | 166 | every file matching the discovery regex, tier C included |
| `tsr-doc-01-inventory.json` | — | per-file record: packages, static/dynamic/patch signals, co-imported non-DOC packages |

All four are written with **LF endings**. A CRLF path list makes `pytest $(cat slice.txt)` report
every entry as MISSING.

Verified usable: `pytest $(cat docs/testing/tsr-doc-01-exclusive.txt) --collect-only` →
**1,003 tests collected, 0 collection errors**, against the seeded `data/icdev.db`. Every one of the
162 slice paths exists on disk.

### What tier B drags in

The 94 tier-B files also import these non-DOC packages, so a tier-B run is not a clean DOC-only
signal — `tools.db` (48 files) and `tools.llm` (28) dominate, then `tools.cortex` (6),
`tools.docgen` (5), `tools.rag` / `tools.govcon` / `tools.genesis` (4 each).

## Deviation from the stated acceptance criterion

The card asks for "a list of 5–15 test file paths". The real DOC surface is **162 files (1,003+
tests)**; even the strictest defensible cut, tier A, is 68. The 5–15 estimate was written before the
surface was measured and is off by an order of magnitude. The list was **not** truncated to fit it —
an arbitrary 15-file cut would silently drop ~90% of the epic and hand the follow-on task a false
baseline. Both acceptance criteria are otherwise met: the DB init commands exit 0, and the file list
is written to a known location.

Sizing note for whoever schedules the run: at 1,003 tests for tier A alone, the DOC slice needs
sharding like the DASH slice did (`tsr-dash-01-d2-shard[1-5].txt`), not a single run.

## Correction to the superseded inventory

| | superseded | corrected |
|---|---|---|
| tier A | 54 | **68** |
| tier B | 45 | **94** |
| tier C ("mention only") | 67 | **4** |
| `slice.txt` | 54 paths | **162 paths** |

The 166-file match set and the per-package *matching* counts were right; only the import
classification was wrong. `slice.txt` also changed meaning — it was tier A alone, it is now tiers
A+B, which matches the sibling NET slice's convention (`tsr-net-01-slice.txt` = the epic's files,
`tsr-net-01-exclusive.txt` = the isolated subset). Anything already built on the 54-path list should
re-read it.
