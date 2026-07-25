# SPIKE crx-db-01 — PostgreSQL Native RLS Policies (Defense-in-Depth Feasibility)

Feasibility write-up for enforcing row-level security at the PostgreSQL layer as
defense-in-depth behind the existing application-level RLS predicate. See
[CLAUDE.md](../../CLAUDE.md) for behavioral instructions and
[compliance-security.md](compliance-security.md) for the security-gate context.

- **Card:** `crx-db-01` (CRX — Component Review Remediation)
- **Source gap:** Database & Storage component review, Gap #8 — *"RLS predicate can
  be bypassed. The RLS predicate relies on `get_connection()` being called through
  the ICDEV path. Direct SQL connections bypass RLS entirely."*
- **Type:** SPIKE — write-up only. No production code, no migrations, no schema.
- **Recommendation:** **GO (phased, non-load-bearing first)** — the hard plumbing
  already exists; the work is packaging, a least-privilege runtime role, one
  correctness fix (transaction-scoped GUCs), and a canvas-table exemption allowlist.

---

## 1. Problem statement

Today, tenant/classification isolation is enforced **only in application code**: the
`StorageCursor` rewrites each query's `WHERE` clause to append a tenant +
classification predicate. Any connection that does **not** flow through that wrapper
— a `psql` shell, a BI tool, a raw `psycopg2` connect, or a bug that forgets to
attach a security context — sees **all tenants' rows**. The database itself imposes
no isolation. Native PostgreSQL RLS (`ENABLE ROW LEVEL SECURITY` + `CREATE POLICY`)
would move the boundary into the engine, so isolation holds regardless of how the
connection was opened.

This is a high-blast-radius change (a wrong policy can hide legitimate rows or
break writes across 300+ tables), which is why it is scoped as a spike.

---

## 2. Current app-level RLS mechanism (what actually runs today)

Traced through the live code, the app-level control has four moving parts:

| Concern | File · function | Notes |
|---|---|---|
| **Predicate injection point** | `tools/db/storage.py` · `StorageCursor._inject_rls()` (~L1010) | Reads the attached `SecurityContext`, computes the classification read-down set, calls `inject_row_predicate()`, and splices the extra params into the correct positions. **Wrapped in `try/except` that returns the SQL unmodified on any error (fail-open).** |
| **Predicate builder** | `tools/security/row_security.py` · `inject_row_predicate()` (L184) | Appends `tenant_id = ? AND (classification IS NULL OR … IN (…))` (+ optional LAC/COI). **Not column-aware** — it emits `tenant_id = ?` whenever a tenant is present, so it is only safe on tables that actually have the column. |
| **Read-down set** | `tools/security/security_context.py` · `classifications_dominated_by()` (L53), `_CLASSIFICATION_LABELS` (L50) | Bell-LaPadula read-down: `classification IN (<all labels the caller's clearance dominates>)`. Order: `PUBLIC < UNCLASSIFIED < CUI < ECI < SECRET < TOP SECRET < TOP SECRET//SCI`. |
| **Context attach** | `tools/db/storage.py` · `get_connection()` (L1448) → `_attach_flask_security_context()` | Inside a Flask request, derives tenant/classification from the authenticated user and calls `set_security_context()`. Outside a request context, **no predicate is attached at all.** |

### The GUC plumbing already exists (key finding)

`StorageConnection.set_security_context()` (`tools/db/storage.py` ~L1182) already does
this on a PostgreSQL backend:

```python
if self._backend == "postgresql" and ctx:
    from tools.security.row_security import set_pg_session_vars
    set_pg_session_vars(self._conn, ctx.tenant_id, ctx.classification)
```

and `set_pg_session_vars()` (`tools/security/row_security.py` L390) issues:

```python
conn.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
conn.execute("SELECT set_config('app.classification', %s, false)", (classification,))
```

So `current_setting('app.tenant_id')` and `current_setting('app.classification')`
are **already populated per checkout on every PG connection that carries a context.**
Native policies can consume these GUCs today with no new propagation code.

### A working native-RLS proof of concept is already in the tree

`tools/cortex/db/verify_tenant_isolation.py` (L82–131) already stands up a probe
table with the full native pattern and exercises it:

```sql
ALTER TABLE <probe> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <probe> FORCE  ROW LEVEL SECURITY;
CREATE POLICY rls_probe_tenant ON <probe>
    USING      (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
```

Its own docstring flags the load-bearing caveat: **when the app DB role holds
`BYPASSRLS`/superuser (common in ICDEV dev/CI databases), native policies are
bypassed entirely**, so today the *application* predicate is the real control. That
caveat is the crux of turning native RLS into a genuine second layer.

There is also a DDL generator ready to reuse: `generate_rls_policy()`
(`row_security.py` L341) emits `CREATE POLICY … USING (<expr>)`, and
`apply_tenant_rls()` (L368) executes a tenant policy for a table (though it does not
yet `ENABLE`/`FORCE` RLS or emit a classification policy).

---

## 3. The canvas-table exemption problem

Native RLS cannot be turned on blindly across the schema, because a large set of
tables **intentionally have neither `tenant_id` nor `classification`**. A policy that
references `current_setting('app.tenant_id')` against a table with no `tenant_id`
column raises `UndefinedColumn` — exactly the failure `get_canvas_connection()`
exists to avoid at the app layer.

Measured against `tools/db/schema/pg_consolidated.sql` (the consolidated PG schema):

| Bucket | Count (approx) | RLS treatment |
|---|---|---|
| Total `CREATE TABLE` | ~1334 | — |
| Have `classification` | ~1200 | classification read-down policy candidate |
| Have `tenant_id` | ~174 | tenant policy candidate |
| Have **both** | ~157 | tenant + classification policy (crown jewels) |
| Have **neither** | ~117 | **EXEMPT — never `ENABLE ROW LEVEL SECURITY`** |

The ~117 "neither" tables cluster by canvas/subsystem prefix — e.g. `aac_*`, `ace_*`,
`dic_*`, `mission_*`, `aiify_*`, `govlift_*`, `slides_*`, `zig_*`, `zta_*`, `sdc_*`,
`sc_*`, `pc_*`, `dm_*`, `dd_*`, `th_*`, `sg_*`, plus scattered ops tables. These are
reached through `get_canvas_connection()` (`tools/db/storage.py` L1576), which
deliberately calls `conn.set_security_context(None)` so **no predicate and no GUC**
are applied. The canonical canvas init pattern (`tools/ai_augmentation/db/init_db.py`)
uses this helper for precisely this reason.

**Design consequence:** a native-RLS rollout must be **column-driven and
allowlist-guarded**. Only tables that physically have `tenant_id` and/or
`classification` get `ENABLE ROW LEVEL SECURITY`; canvas/aux prefixes are an explicit
exempt set that the migration skips and a coherence check asserts is never enabled.

---

## 4. Session-context propagation through the pool (GUC design)

### What works today
`set_pg_session_vars()` is called from `set_security_context()`, which the connection
factory invokes on checkout. So the GUCs track the caller for the life of the
attached context.

### The correctness bug to fix before this is load-bearing
`set_config(..., false)` sets the GUC at **session** scope (`is_local = false`), **not**
transaction scope. ICDEV pools/reuses PG connections within a request context
(and the review notes "reuses connections within request context"). A session-scoped
GUC **persists when the connection returns to the pool** — the next borrower inherits
the previous caller's `app.tenant_id` until it is overwritten. With app-level
predicates that is masked (the predicate is recomputed per query); with **native
policies it becomes the enforcement input**, so a stale GUC = cross-tenant read.

Two acceptable fixes (choose per rollout phase):

1. **Transaction-scoped GUC** — call `set_config('app.tenant_id', %s, true)`
   (`is_local = true`, i.e. `SET LOCAL`) inside the request's transaction. Postgres
   resets it automatically at `COMMIT`/`ROLLBACK`, so nothing leaks to the pool.
   Requires that each logical unit of work runs in one transaction (already the case
   for the `StorageConnection` context-manager path).
2. **Reset-on-return** — explicitly `RESET app.tenant_id; RESET app.classification`
   (or `set_config(..., '', false)`) when a pooled connection is released, mirroring
   what `verify_tenant_isolation.py` already does after seeding (it resets
   `app.tenant_id` to `''` so nothing downstream inherits it).

Add a third GUC, `app.clearance` (an integer order from
`_get_clearance_order()`), so the classification policy can express read-down in SQL
without enumerating a set on the client:

```sql
CREATE POLICY rls_<t>_class ON <t>
    USING (classification IS NULL OR classification = ''
           OR icdev_class_order(classification) <= current_setting('app.clearance', true)::int);
```

where `icdev_class_order()` is a small `IMMUTABLE` SQL function mirroring
`_CLASSIFICATION_LABELS`. (Alternatively pass the dominated set as a text array GUC
and use `= ANY`.) Either way the source of truth stays
`tools/security/security_context.py`.

---

## 5. Migration at scale (391+ tables)

**Do not hand-author per-table policies.** Author **one idempotent migration** that
generates policies dynamically:

1. Read `information_schema.columns` to discover, per table, whether `tenant_id`
   and/or `classification` exist.
2. Skip any table whose name matches the **exempt allowlist** (canvas/aux prefixes
   from §3) or that has neither column.
3. For each qualifying table, emit (reusing `generate_rls_policy()`):
   - `ALTER TABLE <t> ENABLE ROW LEVEL SECURITY;`
   - a tenant `USING`/`WITH CHECK` policy when `tenant_id` exists;
   - a classification read-down `USING` policy when `classification` exists.
4. Guard the whole thing behind a flag (e.g. `ICDEV_PG_NATIVE_RLS=true`) so it is a
   no-op until explicitly enabled, and make every statement `IF EXISTS`/`DROP POLICY
   IF EXISTS` idempotent so re-runs are safe.

`FORCE ROW LEVEL SECURITY` should be added **table-owner-aware**: policies do not
apply to the table owner unless `FORCE`d. Decide per phase (owner tables in migrations
vs. runtime role at query time).

**Least-privilege runtime role (the load-bearing prerequisite).** Native RLS does
nothing while the app connects as a superuser or `BYPASSRLS` role. The rollout must
introduce a dedicated `icdev_app` role that is `NOSUPERUSER NOBYPASSRLS`, owns no RLS
tables (or is `FORCE`d), and holds only DML grants. This is the single biggest piece
of *new* work and is orthogonal to policy authoring.

---

## 6. Performance

- A policy `USING (...)` clause is appended to each query as an additional `AND`
  predicate and evaluated by the planner like any other filter — there is no separate
  "RLS engine" pass. Cost ≈ cost of the extra predicate.
- `current_setting('app.tenant_id', true)` is a cheap stable function call resolved
  once per query, not per row.
- **Indexing is the real lever.** With a `tenant_id` (and ideally
  `(tenant_id, classification)`) index, the policy predicate is index-eligible and
  overhead is typically low single-digit percent. Without it, RLS can turn scans into
  seq scans. The schema already carries some (e.g. `idx_cf_app_tenant` on
  `cf_applications`); a rollout must audit index coverage for every table it protects.
- `WITH CHECK` adds a per-row validation on `INSERT`/`UPDATE`; negligible for OLTP
  write volumes.
- `BYPASSRLS`/superuser roles skip policy evaluation entirely — zero cost, zero
  protection (hence §5's dedicated role).

Net: acceptable for OLTP paths **provided** tenant/classification columns are indexed;
the phased rollout should include an `EXPLAIN` spot-check gate per family.

---

## 7. SQLite caveat

SQLite has **no native RLS**. In air-gap/SQLite mode the application-level predicate
in `StorageCursor._inject_rls()` remains the **sole** authoritative isolation control.
Therefore:

- The app-level predicate is **permanent** — native RLS is additive defense-in-depth
  on PG only, never a replacement.
- The fail-open `try/except` in `_inject_rls()` is more concerning on SQLite (no DB
  backstop). A follow-on (outside this spike) should consider fail-closed behavior or
  at least an audited alarm when injection raises.

---

## 8. GO / NO-GO

**GO — phased, flag-gated, non-load-bearing first.** The feasibility risk is low
because the expensive plumbing is already built and demonstrated:

- GUC propagation through the pool: **exists** (`set_pg_session_vars` wired into
  `set_security_context`).
- Policy DDL generation: **exists** (`generate_rls_policy`, `apply_tenant_rls`).
- End-to-end native-RLS proof: **exists and passes** (`verify_tenant_isolation.py`).
- Read-down semantics: **exists** (`classifications_dominated_by`).

The remaining work is well-bounded engineering, not research: a least-privilege
runtime role, a transaction-scoped-GUC fix, a column-driven idempotent migration, and
a canvas exemption allowlist + coherence assertion. Blast radius is contained by
keeping the app-level predicate authoritative until each family is proven under native
policies.

### Phased rollout plan

| Phase | Scope | Exit criteria |
|---|---|---|
| **0 — Harden plumbing** | Fix GUCs to transaction scope (`SET LOCAL`) or reset-on-return; add `app.clearance` GUC + `icdev_class_order()` immutable fn; create `icdev_app` NOBYPASSRLS role in a non-prod DB. Extend the existing `verify_tenant_isolation.py` probe to cover classification read-down under the non-bypass role. | Probe passes for tenant **and** classification under `icdev_app`; no GUC leak across pooled checkouts. |
| **1 — Crown jewels** | Enable RLS + tenant + classification policies on the highest-sensitivity **both-column** tables (audit/provenance, proposals/`cpmp`-adjacent tenant tables, compliance evidence) — a hand-picked ~dozen. App predicate stays on (belt-and-suspenders). | Functional + `EXPLAIN` regression clean; zero cross-tenant leakage in tests. |
| **2 — All tenant tables** | Column-driven migration over the ~157 both-column + remaining ~174 tenant tables, allowlist-guarded. Flag-gated (`ICDEV_PG_NATIVE_RLS`). | Idempotent re-run safe; coherence check asserts canvas prefixes never enabled. |
| **3 — Classification-only** | Classification read-down policy on the ~1200 classification-only tables (no tenant clause). Canvas/aux ~117 remain **explicitly exempt**. | Read-down verified against `_CLASSIFICATION_LABELS`. |
| **4 — Make it load-bearing** | Flip prod app connection to the `NOBYPASSRLS` role. App-level predicate retained as defense-in-depth and as the SQLite authority. | Direct-connection bypass demonstrably blocked in prod-like env. |

### Non-goals / guardrails carried forward

- Never `ENABLE ROW LEVEL SECURITY` on canvas/aux tables (§3 exempt set);
  `get_canvas_connection()` stays the app-side hatch.
- Never remove the application-level predicate (SQLite has no DB backstop).
- Keep classification ordering single-sourced in `security_context.py`.

---

## 9. Files read for this spike

- `tools/db/storage.py` — `StorageCursor._inject_rls` (L1010), `StorageConnection.set_security_context` (L1182), `get_connection` (L1448), `get_canvas_connection` (L1576), `resolve_canvas_backend` (L1550)
- `tools/security/row_security.py` — `inject_row_predicate` (L184), `generate_rls_policy` (L341), `apply_tenant_rls` (L368), `set_pg_session_vars` (L390)
- `tools/security/security_context.py` — `classifications_dominated_by` (L53), `_CLASSIFICATION_LABELS` (L50), `_get_clearance_order` (L31)
- `tools/cortex/db/verify_tenant_isolation.py` — native-RLS probe (L82–131)
- `tools/db/schema/pg_consolidated.sql` — column-coverage measurement
- `tools/ai_augmentation/db/init_db.py` — canonical `get_canvas_connection()` usage
- Source gap: `C:\AI\searches\archive\icdev_component_reviews\database_storage.md` (Gap #8)
