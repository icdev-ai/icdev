# Phase crx-db-01 — PostgreSQL Native RLS Spike

**Type:** SPIKE (write-up only — no production code, no migrations, no schema)
**Card:** `crx-db-01` · CRX — Component Review Remediation
**Source gap:** Database & Storage review, Gap #8 (RLS predicate bypassable via direct DB connection)
**Full write-up:** [spike-crx-db-01-pg-native-rls.md](../reference/spike-crx-db-01-pg-native-rls.md)

## Summary

Feasibility study for enforcing tenant/classification row-level security at the
PostgreSQL layer (`ENABLE ROW LEVEL SECURITY` + `CREATE POLICY`) as defense-in-depth
behind the existing application-level predicate injection. The app-level control
(`StorageCursor._inject_rls` in `tools/db/storage.py`) is the authoritative isolation
boundary today and is tied to access flowing through the ICDEV connection wrapper; the
database engine does not itself impose per-row isolation. Adding a native engine-level
boundary would make isolation hold independently of the connection path.

> **Scope note (public repo):** this is a forward-looking hardening plan; detailed
> threat analysis of the present state is tracked internally, not in this public doc.

## Outcome: GO (phased, flag-gated, non-load-bearing first)

The expensive plumbing already exists in the tree:

- **GUC propagation** through the pooled connection layer is wired:
  `StorageConnection.set_security_context()` already calls `set_pg_session_vars()`,
  which sets `current_setting('app.tenant_id')` / `app.classification` per checkout.
- **Policy DDL generation** exists: `generate_rls_policy()` / `apply_tenant_rls()` in
  `tools/security/row_security.py`.
- **A working native-RLS proof of concept** exists and passes:
  `tools/cortex/db/verify_tenant_isolation.py` (ENABLE + FORCE + CREATE POLICY over
  `current_setting`).
- **Read-down semantics** exist: `classifications_dominated_by()` in
  `tools/security/security_context.py`.

## Key findings

- **Canvas-table exemption is mandatory.** ~117 of ~1334 tables have neither
  `tenant_id` nor `classification` (canvas/aux families: `aac_*`, `ace_*`, `dic_*`,
  `mission_*`, `aiify_*`, `zig_*`, `zta_*`, `slides_*`, …). Native RLS must be
  column-driven and allowlist-guarded; `get_canvas_connection()` remains the app-side
  hatch. Coverage: ~1200 tables have `classification`, ~174 have `tenant_id`, ~157
  have both.
- **One correctness fix required before load-bearing:** `set_pg_session_vars()` uses
  session-scoped GUCs (`set_config(..., false)`), which persist when a pooled
  connection returns to the pool. Must switch to transaction scope (`SET LOCAL`) or
  reset-on-return so native policies never read a stale tenant.
- **Least-privilege runtime role is the biggest new work:** native RLS only takes
  effect when the connecting role is not superuser/`BYPASSRLS`. Provisioning a
  dedicated `NOBYPASSRLS` runtime role is the prerequisite for the engine-level layer
  to be effective.
- **Migrate at scale via one idempotent, information_schema-driven migration** that
  loops qualifying tables and skips the exempt allowlist — never 391 hand-written
  policies.
- **Perf:** policy `USING` clauses are ordinary appended predicates; overhead is low
  single-digit percent **when `tenant_id`/`classification` are indexed** (some indexes
  like `idx_cf_app_tenant` already exist). Index audit per family is required.
- **SQLite caveat:** no native RLS — the application-level predicate stays the sole
  authority in air-gap mode and must never be removed.

## Phased plan

0. Harden plumbing (SET LOCAL GUCs, `app.clearance` GUC, `icdev_app` NOBYPASSRLS role, extend the isolation probe).
1. Crown jewels — enable on ~12 highest-sensitivity both-column tables; app predicate stays on.
2. All tenant tables via column-driven migration, flag-gated (`ICDEV_PG_NATIVE_RLS`).
3. Classification-only tables get read-down policy; canvas/aux ~117 stay exempt.
4. Flip prod to the NOBYPASSRLS role; native RLS becomes load-bearing, app predicate retained as defense-in-depth.

## Deliverables

- `docs/reference/spike-crx-db-01-pg-native-rls.md` — full spike (mechanism, exemption, GUC design, migration-at-scale, perf, SQLite caveat, GO/NO-GO + phases).
- This feature entry.

No production code, schema, or migrations were introduced — this is a decision + plan artifact only.
