# CUI // SP-CTI

# Decision Record — Canvas placeholder portability (`aca-hyg-07`)

**Status:** DECISION REQUIRED — recommendation below, no canvas code changed.
**Date:** 2026-08-01
**Task:** `aca-hyg-07` — "make data_canvas SQLite branch translate, then go PG-native"
**Scope of this document:** verify the card's premises, re-measure the stated risk,
and recommend a sequencing. Implementation is deliberately *not* included: the card
states a human call is required before code moves.

---

## 1. TL;DR

The card's diagnosis of `data_canvas` is **correct**. Its risk assessment is
**overstated**, and its scope is **too narrow**.

Three things changed after measurement:

1. **The stated blocker is not real.** The card holds back on step 1 because
   `StorageConnection.__exit__` commits *and closes* while raw `sqlite3.__exit__`
   commits and leaves the connection open. Measured across all 41 `with
   get_connection() as conn:` sites in `data_canvas`: **0 reuse the connection
   after the block, 0 close it explicitly inside the block, 0 leak a cursor past
   it.** The semantic change has no reachable call site.

2. **Step 1 is backward-compatible and independently shippable.**
   `StorageConnection` on the `sqlite` backend accepts **both** `?` and `%s`
   (verified live, §4). Wrapping the SQLite branch therefore requires **zero**
   changes to the 700 existing `?` statements. Step 1 does not depend on step 2;
   step 2 depends on step 1.

3. **`data_canvas` is not the problem canvas — it is the only *compliant* one.**
   Eight canvases share the identical hybrid `get_connection`. Seven of them
   already emit `%s` exclusively, which raw `sqlite3` **cannot execute**. Their
   SQLite fallback is dead code today. `data_canvas` is the sole hybrid canvas
   that actually honors the `?`-only constraint the hybrid imposes.

**Recommendation:** approve the fix, but **invert the priority** — ship the
one-function wrapper fix to the 7 broken canvases first (cheap, pure bug fix, no
SQL sweep), then do `data_canvas` steps 1→2→3.

---

## 2. Premises checked

| # | Card claim | Verdict | Evidence |
|---|---|---|---|
| 1 | `data_canvas` `get_connection` is hybrid: PG→`get_canvas_connection`, SQLite→raw `sqlite3` | **CONFIRMED** | `tools/data_canvas/db/init_db.py:173-186` |
| 2 | Raw `sqlite3` accepts `?` but not `%s` | **CONFIRMED** | live probe: `%s` → `OperationalError: near "%": syntax error` |
| 3 | `translate_sql` is load-bearing for this canvas at runtime | **CONFIRMED** | `_DDC_BACKEND` defaults to `postgresql`; `tools/db/storage.py:353-361` rewrites `?`→`%s` and warns on every such call |
| 4 | The `?` placeholders are deliberate, not sloppiness | **CONFIRMED** | pinned by `tests/test_cvx_sql06_datacanvas_placeholders.py` |
| 5 | `agentic_ai_canvas` is the target shape | **CONFIRMED** | `tools/agentic_ai_canvas/db/init_db.py:34-64` wraps both branches, `%s` throughout |
| 6 | `pg_portability_linter` has no placeholder rule | **CONFIRMED** | no placeholder pattern in `tools/lint/pg_portability_linter.py` rule tables |
| 7 | "662 bare `?` in data_canvas" | **REVISED → 700** | AST census of `execute`/`executemany` string literals (card's figure was likely regex-based) |
| 8 | Changing `__exit__` semantics is a canvas-wide risk | **NOT SUPPORTED** | 0 post-`with` reuse / 0 double-close / 0 cursor escape across 41 sites |
| 9 | "the hybrid trap may repeat" in other modules | **CONFIRMED AND WORSE** | 8 canvases affected; 7 are already broken, see §3 |

One correction worth recording: `data_canvas` does contain 12 `%s` in
`execute()` calls, which initially looked like the hybrid invariant already
leaking. It is not. All 12 are legitimate:

- `data_profiler.py` (3) and `quality_engine.py` (1) — explicitly `db_kind`-branched
  against **external, user-supplied** databases via `_open_connection()`, not the
  canvas DB.
- `twin.py` (7+) — imports `get_canvas_connection` from `tools.db.storage`
  **directly**, bypassing the hybrid. It is already at the target shape.

`twin.py` is a useful precedent: part of this canvas has already migrated.

---

## 3. The finding that changes the priority

All eight canvases below return an **unwrapped raw `sqlite3` connection** from
`get_connection`'s fallback branch. The placeholder census shows what each one
actually emits:

| Canvas | `?` | `%s` | `with` sites | SQLite branch status |
|---|---:|---:|---:|---|
| `slides` | 0 | 68 | 0 | **DEAD** — cannot execute its own SQL |
| `ops_hub` | 0 | 64 | 0 | **DEAD** |
| `aisg` | 0 | 59 | 0 | **DEAD** |
| `pmc_canvas` | 0 | 52 | 0 | **DEAD** |
| `ccc_canvas` | 0 | 34 | 0 | **DEAD** |
| `aiml_canvas` | 0 | 30 | 0 | **DEAD** |
| `noc_canvas` | 0 | 2 | 0 | **DEAD** |
| `data_canvas` | **700** | 12 | 41 | works — `?`-only discipline held |

Seven canvases advertise a SQLite fallback that raises `OperationalError` on the
first parameterised query. `slides` compounds it — its fallback also feeds a
`_SCHEMA_PG` containing `SERIAL`/`JSONB`.

This reframes the task. The hybrid `get_connection` is not a deliberate
portability design that `data_canvas` must be carefully extracted from; it is a
copy-paste pattern that only `data_canvas` ever actually satisfied. Everywhere
else it silently disabled the fallback.

For those 7, the fix is **step 1 only** — wrap the branch, no SQL sweep, because
they are already `%s`-native. That is one function per canvas.

---

## 4. Risk, re-measured

The card's blocker:

> `StorageConnection.__exit__` commits AND CLOSES, while raw sqlite3's `__exit__`
> commits and keeps the connection open.

That difference is real (`tools/db/storage.py:1308-1314`). Whether it *matters*
depends on whether anything uses the connection after the block. Measured by AST
across `tools/data_canvas/**`:

| Hazard | Sites |
|---|---:|
| `with get_connection() as conn:` blocks | 41 |
| …where `conn` is referenced after the block | **0** |
| …with an explicit `conn.close()` inside (double-close) | **0** |
| …leaking a cursor/lazy result past the block | **0** |
| `conn = get_connection()` (non-`with`, unaffected by `__exit__`) | 93 |

And the compatibility property that makes step 1 free — verified live against
`StorageConnection(raw_sqlite3, "sqlite")`:

```
wrapped sqlite, '?' form  -> OK   <-- 700 existing stmts keep working
wrapped sqlite, '%s' form -> OK   <-- target shape works too
```

Both forms execute; `row_factory` dict access survives the wrapper. So step 1
changes **no SQL** and breaks **no statement**. It is a strictly-widening change.

Residual risk after step 1 is therefore confined to: connections closing earlier
than before at 41 sites that provably do not use them afterwards. That warrants a
regression pass, not a decision gate.

The genuinely risky step is **step 2** (700 placeholders), and it is risky for the
ordinary reason — sweep size — not for a semantic reason.

---

## 5. Options

**Option A — Wrapper-first, sweep second (RECOMMENDED).**
Ship step 1 to all 8 canvases. Then sweep `data_canvas` to `%s` and update the
tests. Fixes 7 dead fallbacks immediately; de-risks the sweep by making both
branches accept both forms *before* any SQL is touched. At every commit the tree
is green.

**Option B — Card's original order, `data_canvas` only.**
Correct but leaves the 7 dead fallbacks in place, and they are the cheaper, more
severe defect. Not recommended as-is; it is Option A restricted to one canvas.

**Option C — Delete the SQLite branch entirely.**
PostgreSQL is the primary backend, so arguably the fallback should not exist.
Rejected: air-gap/dev/single-user deployments rely on canvas SQLite files, and
`data_canvas` uses its branch today. This would be a product decision, not a
hygiene fix.

**Option D — Do nothing.**
Leaves `translate_sql` load-bearing for 700 runtime statements — the exact
condition CLAUDE.md prohibits — and leaves 7 fallbacks dead. Not recommended.

---

## 6. Recommended sequencing

Each step is independently shippable and independently green.

1. **`*-wrap-01`** — wrap the SQLite branch in `StorageConnection(conn, "sqlite")`
   for the 7 `%s`-native canvases (`slides`, `ops_hub`, `aisg`, `pmc_canvas`,
   `ccc_canvas`, `aiml_canvas`, `noc_canvas`). Copy
   `tools/agentic_ai_canvas/db/init_db.py:34-64`, including
   `set_security_context(None)` with the `rls-bypass:` annotation. Add a
   regression test per canvas that executes one real parameterised query on the
   SQLite branch — these are currently unprotected.
2. **`*-wrap-02`** — same wrapper for `data_canvas`. No SQL changes. Full
   `data_canvas` regression pass, focused on the 41 `with` sites and connection
   lifetime.
3. **`*-pgn-03`** — convert `data_canvas` runtime SQL `?` → `%s` (700
   placeholders). Mechanical but large; split per module
   (`blueprint.py` alone is 359).
4. **`*-tst-04`** — update `test_cvx_sql06`/`07`, whose premise ("only `?` works
   on both branches") is retired by step 2. Do not delete them — re-point them at
   the new invariant so the regression stays pinned.
5. **`*-lnt-05`** — add the placeholder rule to `pg_portability_linter`, with a
   per-module allowlist. Only meaningful *after* steps 1-4, otherwise it lands
   700+ findings on day one.

Steps 3-5 must not start before step 2 merges.

**Open question for the human call:** step 5's allowlist implies some hybrid
connection may be kept deliberately. After this work, none should remain — if
that holds, the rule can be allowlist-free and strictly enforced. Confirm before
building the allowlist mechanism.

---

## 7. Method

All figures are AST-derived, not regex-derived: `execute`/`executemany` first
arguments reconstructed from string constants, f-strings, and `+` concatenation,
with PG `::type` casts stripped before counting `:name` binds. Dynamic SQL built
by helper functions is not counted, so the 700 is a **lower bound**. Live
behavioural probes were run against in-memory / temp SQLite, never against the
live board.
