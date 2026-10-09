# CUI // SP-CTI

# swp-swallow-01 — Log instead of swallowing at persistence sites

## The defect

An AST sweep of `tools/` (migrations excluded) found **227 sites** where a
`try` block containing an `INSERT` was guarded by a handler whose body was
exactly `pass`:

```python
try:
    conn.execute("INSERT INTO audit_trail (...) VALUES (...)", params)
    conn.commit()
except Exception:
    pass  # Never let audit failure crash the bus
```

The *behaviour* is almost always correct — the write really is best-effort and
must not break the caller. The *silence* is not. A write guarded this way can
fail on every single call, forever, and no operator, log line, or reflex ever
learns about it. That is why a batch of persistence defects survived long
enough to be worth a card: the system had no way to say it was failing.

The prescribed fix was established in `tools/llm/router.py` and
`tools/llm/chain_orchestrator.py` (PR #1071) — keep the best-effort semantics,
add the report.

## What shipped

### 1. The rewrite

```python
except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
    # Never let audit failure crash the bus
    logger.warning("_audit_event: best-effort INSERT into audit_trail failed (non-blocking): %s", exc)
```

Applied to **255 sites across 195 files** in `tools/` and **171 sites across
131 files** in the `icdev/` mirror (the mirror is a partial copy, so its count
is lower). No control flow changed anywhere: every handler still swallows, and
every caller still proceeds.

The message names the enclosing function and the target table, so a log line is
actionable without opening the file.

### 2. `tools/refactor/swallowed_persistence.py` — the shared detector

`find_sites(paths, project_root)` returns one `SwallowSite` per broad handler
(`except Exception`, `except BaseException`, bare `except:`) whose body is
exactly `pass`, over a `try` whose body contains an `INSERT` in a string
literal.

Not flagged, deliberately:

- narrow handlers (`except sqlite3.IntegrityError: pass`) — the author named
  the failure they expected;
- handlers that re-raise, return, or already log;
- swallowed blocks with no write in them — silence only matters when something
  was supposed to persist;
- `migrations/` (a failed migration is caught by the migration runner and the
  card scoped them out) and `tests/`.

It reads through a UTF-8 BOM. A BOM makes `ast.parse` raise, and files carrying
one are exactly how sites dodged earlier AST-based sweeps.

Both the fixer and the coherence gate import this one module, so they cannot
drift apart on what counts as a violation.

### 3. `tools/refactor/fix_swallowed_persistence.py` — the codemod

```bash
python tools/refactor/fix_swallowed_persistence.py --dry-run --json
python tools/refactor/fix_swallowed_persistence.py --write --json
python tools/refactor/fix_swallowed_persistence.py --write --path tools/govcon
```

The parts that are easy to get wrong, and how each is handled:

| Hazard | Handling |
|--------|----------|
| `except ... as exc` **unbinds** `exc` when the handler exits | Scans the enclosing function for names in use and picks `exc` → `_exc` → `_persist_exc` |
| A function-local `logger` would shadow a new module-level one, making the call raise `UnboundLocalError` | Detects the local binding and names the module logger `_persist_logger` |
| A module that already has a logger (`log`, `LOGGER`, or one imported from a package `_common`) | Reuses the existing name rather than defining a second one (which would trip F811) |
| The logger definition landing *below* a rewritten site, or inside a function | Tracks the anchor shift, then re-parses its own output and **refuses to write** a file whose module logger is not bound at module level |
| CRLF checkouts and long lines | Original line endings preserved; messages wrap across adjacent string literals to stay ≤120 columns |
| The author's `pass  # already exists — idempotent` note | Kept as a comment above the new warning — it explains *why* the write is best-effort |

One shape is detected but **not** rewritten: a bare `except:`. Naming its
exception means choosing between `Exception` (narrows the catch) and
`BaseException` (keeps `KeyboardInterrupt` swallowed), and the tree contains
none. The fixer logs the skip; the gate still fails on it.

### 4. The gate — `coherence_checker.check_swallowed_persistence`

Registered as `swallowed_persistence` in `CHECK_REGISTRY`. It is not in
`HEAVY_CHECKS`, so it runs in **both** the fast (per-task) and full (nightly
sweep) tiers — a full-tier-only check would not block the build that
reintroduces the pattern.

```bash
python tools/workflow/coherence_checker.py --check swallowed_persistence --json
```

With `--changed-files` it scans only the diff. Without, it scans 7,239 files in
~27s.

If the detector module cannot be imported, the check **fails** rather than
passing. A gate that goes quiet when its detector disappears is the exact
failure mode this card exists to eliminate.

### 5. Wiring it to the build

The coherence checker is not invoked by `.github/workflows/icdev-ci.yml` — it
runs as the per-task gate and the nightly `coherence_sweep` reflex. A check that
only ever runs there does not satisfy "fails the build". So
`tests/test_coherence_swallowed_persistence.py` is added to the **Test** job's
allowlist, and its `test_real_tree_is_clean` case calls the gate against the
whole tree. `Test` is a required check, so a PR that reintroduces the pattern
now goes red before it can merge.

`args/security_gates.yaml` carries the matching declaration —
`swallowed_persistence`, blocking, `max_swallowed_insert_sites: 0` — placed
beside `canvas_placeholder_style`, because that gate catches the SQL that
raises and this one catches the handler that eats it.

## Verification

| Check | Result |
|-------|--------|
| `tests/test_coherence_swallowed_persistence.py` | 13 passed — flags/ignores in both directions, fast-tier membership, missing-detector-fails, and `test_real_tree_is_clean` |
| `ruff check` over all 331 changed files | clean |
| Import smoke over all 197 changed `tools/` modules | 197 imported; the one failure is the pre-existing `SqliteServerRefused` backend guard in `tools/dashboard/app.py` |
| Logger-insertion audit (import present, ordered before use, no duplicate or shadowed binding) | 0 findings |
| BOM / line-ending audit vs `HEAD` | no BOM dropped, no CRLF↔LF flip |
| `tools/` ↔ `icdev/` byte parity for every changed file that has a mirror | in sync (the one pre-existing drift in `app.py` is the `e2p-back-04` backend guard, untouched here) |

## Known consequence

A handful of rewritten sites swallow an *expected* duplicate-key error on an
idempotent insert (`safety_layer.py`, `mcp_sync.py`). Those now emit a
`logger.warning` on the happy path. That is the trade the card asked for —
audible over silent — and the right follow-up is to narrow those specific
handlers to `IntegrityError`, not to re-mute them.
