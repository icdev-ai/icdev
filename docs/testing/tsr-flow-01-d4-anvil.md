# TSR FLOW — `tools/anvil` slice (`tsr-flow-01-d4`)

Verification run for the anvil third of the FLOW epic. Companion to
`tsr-flow-01-baseline.md` (`tsr-flow-01-d1`).

## Headline

**No anvil test failures exist. 88 passed / 0 failed, before and after. No code changed.**

The card's premise — "for each failing anvil test file … replace bare `sqlite3.connect`
patches with `tests/_sql_compat.py`" — does not hold. d1's baseline already recorded it:

> ### `tools.anvil` — 0 failing files
> Green across the slice.

This run re-measured rather than trusting that, and confirms it. It also checked the
second half of the card (deleted features) and **corrects one error in the d1 baseline**.

## Before / after counts

Each file in its own process (per the one-`sys.exit`-aborts-the-session rule), seeded
worktree, `ICDEV_STORAGE_BACKEND=sqlite` via `tests/conftest.py`:

| file | before | after |
|---|---|---|
| `tests/security/test_anvil_runner.py` | 12 passed | 12 passed |
| `tests/test_anvil_critique.py` | 36 passed | 36 passed |
| `tests/test_autogen_cards_reference_only.py` | 2 passed | 2 passed |
| `tests/test_wiki_integrations.py` | 19 passed | 19 passed |
| `tests/tools/anvil/agentic_runner_reasoned_test.py` | 6 passed | 6 passed |
| `tests/tools/llm/reasoned_codegen_test.py` | 13 passed | 13 passed |
| **total** | **88 passed, 0 failed** | **88 passed, 0 failed** |

Also run as a **single combined process** to rule out the cross-file pollution that
produced 11 of the epic's failures in the `workflow_hitl` third: **88 passed in 2.44s**.
No pollution in the anvil slice.

`tests/security/e2e_anvil.py` is `e2e_*`-named, driven by `e2e_runner.py`, not pytest —
out of scope, unchanged.

## Why no `_sql_compat` conversion was made

`tests/test_anvil_critique.py` has six bare `sqlite3.connect` sites (L188, 201, 218, 252,
272, 603) — the only ones in the slice. They were inspected individually rather than
waved through on green, because a passing test is not evidence here: the failure mode
`_sql_compat` exists for is a test that silently asserts its own no-op.

They are **all test-side**, and the conversion would be a no-op:

- Each opens a connection to a `tmp_path` file DB and runs *test-authored* SQL with `?`
  placeholders — already SQLite dialect, nothing for `translate_sql` to rewrite.
- None is patched over `get_connection` or otherwise handed to production code.
  `AtlasCritique` receives `db_path=<Path>`, not a connection, and opens its own via
  `_get_db()`. The layer `_sql_compat` restores was never removed.
- The one test that reads back a *production* write, `test_findings_persisted_in_db`
  (L574), asserts `len(findings) == 3` — a **positive** assertion. Had the write silently
  failed it would fail `0 == 3`, which is the opposite of the no-op pattern.

Per `tests/_sql_compat.py`'s own contract ("wherever the connection is handed to
production code"), converting these would be churn with zero behavioural effect.

## Deleted-feature check (`sync_package_tree`) — nothing lost

The card asked for a `git log -S` sweep for symbols dropped by a mirror sync, per
[[mirror-only-authoring-silently-deletes-features]]. Result: **clean.**

- `git diff origin/main -- tools/anvil/ icdev/tools/anvil/` is **empty** — the tree is
  identical to main.
- Mirror parity is **17/17 files, byte-identical** between `tools/anvil/` and
  `icdev/tools/anvil/`. No drift, no missing module, no hollow shim.
- One commit does delete anvil paths — `67d80f187` — but its deletions are all under
  `build/lib/icdev/…`, i.e. untracking build artifacts, not source. It is **not an
  ancestor of `origin/main`** (it lives only on the unmerged `kanban/dm-found-01`).

Nothing to restore.

## Correction to the d1 baseline

The d1 baseline lists `tests/tools/anvil/agentic_runner_reasoned_test.py` among "7 files
in scope that pytest does not collect", on the grounds that:

> `pyproject.toml` sets the default `python_files = test_*.py`

**`pyproject.toml` sets no `python_files` key at all** (only `testpaths = ["tests"]`), so
pytest's default applies — and that default is `test_*.py` ***and*** `*_test.py`. The
`*_test.py` suffix matches. Verified by collection under the real project config:

```
$ python -m pytest tests/tools/anvil/ --collect-only -q
6 tests collected
```

So those 6 tests **do** run in the suite; the file is not dead weight and needs no rename.
d1's call to flag rather than rename was the right one, for a different reason than stated.

The setting d1 cited is real, but lives elsewhere: the only `python_files = ["test_*.py"]`
in the tree is inside `tools/project/project_scaffold.py` (L426, L694) — the `pyproject.toml`
template written into **generated child projects**. It never applies to this repo. Worth
knowing generally: grepping for a config key in this codebase can hit a scaffold template
for downstream projects rather than the live config, so confirm with `--collect-only`.

The other six files in that list are genuinely uncollected — they are `e2e_*`-prefixed,
which matches neither default pattern. That part of the baseline stands.

## Consequence for the epic

The anvil third of FLOW is closed with no code change. The epic's remaining failures are
all in the `workflow_hitl` and `kanban` thirds (`d2`/`d3`), unaffected by this task.
`tsr-flow-01-d5` should treat anvil as a green baseline and not re-scope it.
