# CUI // SP-CTI

# mvs-audit-03-d5 — consolidated findings and the action-required list

The final report for mvs-audit-03. It merges
[d3](mvs-audit-03-d3-first-20-replay.md) (rows 1–20) and
[d4](mvs-audit-03-d4-remaining-40-replay.md) (rows 21–60) over the 60 shadowed
migrations [d1](mvs-audit-03-d1-shadowed-migrations.md) enumerated, cross-checks
both against the two prior passes at this problem (**PR #1199**, merged; **PR
#1296**, still open), and reduces the result to a prioritized remediation list.

| File | Contents |
|------|----------|
| [mvs-audit-03-d5-consolidated.json](mvs-audit-03-d5-consolidated.json) | the action-required list, machine-readable |

Every verdict quoted from d3/d4 below was re-checked directly against
`tools/db/schema/pg_consolidated.sql` and the consuming source for this report;
where that re-check changed a verdict it is called out under
[Two corrections to d4](#two-corrections-to-d4).

## Answer

Of the 60 grandfathered shadowed migrations, **49 are safe to remain
grandfathered** and **11 are gaps on PostgreSQL**. Of those eleven, **six break
live code**, and **two of the six are covered by no remediation anywhere — not
on `main`, not on an open branch.**

| Classification | Count | Action |
|----------------|-------|--------|
| `benign` — the chain already declares it, or it declares nothing | 10 | none |
| `gap_sqlite_chain_only` — absent from the SQLite chain, present in both PostgreSQL oracles | 39 | none for PostgreSQL; see [the SQLite residue](#the-sqlite-residue-ruled-on) |
| **`gap_on_postgresql`** | **11** | see below |

and the eleven, by cost:

| PostgreSQL impact | Count | Remediation status |
|-------------------|-------|--------------------|
| **live consumers, nothing fixes them** | **2** | **NONE — new work** |
| live consumers, fix authored but unmerged | 4 | merge PR #1296 |
| index only | 3 | optional |
| inert — schema absent, nothing reads it | 2 | record, do not fix |

The headline is the first row. The two prior passes at this problem between them
closed sixteen shadowed migrations; `207_tenant_component_overrides` and
`257_idr_dic_doc_link.sql` were in neither.

---

## Action required — prioritized

Severity is by what a **fresh PostgreSQL deployment** does, because
`bootstrap_pg.py` loads `pg_consolidated.sql` and marks every migration ≤
`through_version: 301` applied without running it (verified:
`tools/db/schema/pg_consolidated.meta.json:2`). Every entry below is ≤ 282, so
absence from the snapshot with no runtime creator is permanent — and
`init_icdev_db.py` cannot supply the difference, because it refuses to run on
PostgreSQL (`tools/db/init_icdev_db.py:11383`).

### P1 — live consumers, no remediation exists

#### A1. `207_tenant_component_overrides` — per-tenant component overrides fail **open**

Worst of the two, because the failure is silent and it fails in the permissive
direction.

*Gap evidence.* `grep -c tenant_component_overrides pg_consolidated.sql` → **0**.
Its only other declaration is `tools/db/init_icdev_db.py:11077`, the SQLite-only
path. No canvas or reflex creates it.

*Consumers* — four modules, wider than d4 reported:

| File | Use |
|------|-----|
| `tools/config/component_registry.py:534, 575, 622, 653` | read / upsert / delete / list |
| `tools/idp/tenancy.py` | tenant provisioning |
| `tools/iqe/adapters/admin_console.py` | IQE collection |
| `tools/iqe/adapters/idp.py` | IQE collection |

*Failure mode.* The read path swallows and falls back to the global default:

```python
# tools/config/component_registry.py:531-547
try:
    ... "SELECT enabled FROM tenant_component_overrides "
        "WHERE tenant_id = %s AND component_key = %s" ...
except Exception as exc:
    logger.debug("Tenant override lookup failed for %s/%s: %s", ...)
return env_enabled
```

`UndefinedTable` → `logger.debug` → **`return env_enabled`**. A component
disabled for one tenant reads as enabled whenever the global env default is on,
at DEBUG level, with no error surfaced anywhere. The write path
(`set_tenant_component_override`, line 564) returns `False` and logs at WARNING,
so an operator setting an override sees a failure — but an override *believed*
already set is honoured nowhere and reported nowhere. CLAUDE.md names this table
as the tenant-level enablement override for the enterprise-configurable
platform.

*Remediation.* New timestamped migration creating `tenant_component_overrides`
+ `idx_tco_tenant`, `idx_tco_component`. Scaffold it — never hand-number:
`python tools/db/migrate.py --create "tenant_component_overrides"`.

#### A2. `257_idr_dic_doc_link.sql` — the docgen → Tech Writer bridge returns HTTP 500

*Gap evidence.* `dic_documents` in the snapshot carries `source_id` and no other
`source_*` column:

```
$ awk '/CREATE TABLE public.dic_documents \(/,/^\);/' pg_consolidated.sql | grep source_
    source_id text,
$ grep -c 'source_wg_result_id\|source_idr_session_id' pg_consolidated.sql
0
```

The three `idr_sessions` columns the same migration adds *are* present
(`suggested_classification` × 2), so the loss is partial — the session half
lands, the `dic_documents` half does not.

*Consumers.* `tools/document_intelligence/blueprint.py` writes both columns
twice — `UPDATE` at line 921 (path A, reuse an already-generated document) and
`INSERT` at line 964 (paths B / fallback). Both are inside one `try`; the
handler is:

```python
# tools/document_intelligence/blueprint.py (import-from-docgen)
except Exception as exc:
    logger.warning("dic: import-from-docgen error: %s", exc)
    return jsonify({"error": str(exc)}), 500
```

*Failure mode.* Every path through the route touches a missing column, so the
docgen → Tech Writer import is a hard **500** on a fresh PostgreSQL deployment —
loud, not silent, and unconditional.

*Remediation.* New timestamped migration adding
`dic_documents.source_wg_result_id` and `.source_idr_session_id`. The
`idr_sessions` half of 257 must **not** be re-applied.

### P2 — live consumers, fix authored on PR #1296 but unmerged

The action for all four is **merge PR #1296**, not new code. Its migration
`20260803204235_mvs_audit_03_shadowed_gaps` already carries the DDL, and its
timestamp id sorts after the migrations that ALTER these tables — the reason it
was folded rather than renumbered.

| # | Entry | Lost | Consumers |
|---|-------|------|-----------|
| B1 | `139_govlift_rbac_roles` **+** `247_dashboard_users_role_check` | 4 CHECK values on `dashboard_users.role` | `create_user()` (`tools/dashboard/auth.py:119`) raises a CHECK violation for `migration_engineer`, `component_admin`, `auditor`, `ciso` |
| B2 | `210_sso` | `sso_providers`, `sso_sessions`, 3 indexes | `tools/admin/blueprint.py`, `tools/auth/oidc.py`, `saml.py`, `blueprint.py`, `session.py`, `tools/compliance/gdpr_eraser.py` |
| B3 | `236_rfi_workbench.sql` | `rfi_workbench_sessions/sections/exports`, 2 indexes | 9 modules across `tools/govcon/`, `tools/iqe/adapters/rfi_canvas.py`, `tools/rag/source_registry.py` |

*Gap evidence, re-verified for this report:*

```
$ grep -c sso_providers pg_consolidated.sql              → 0
$ grep -c sso_sessions pg_consolidated.sql               → 0
$ grep -c rfi_workbench_sessions pg_consolidated.sql     → 0   (…sections, …exports likewise 0)
$ awk '/CREATE TABLE .*dashboard_users \(/,/^\);/' pg_consolidated.sql | grep role_check
  CONSTRAINT dashboard_users_role_check CHECK ((role = ANY (ARRAY[
    'admin','pm','developer','isso','co','cor','bd','capture_mgr',
    'contract_mgr','reviewer'])))
```

Ten values in the snapshot against fourteen in
`tools/dashboard/auth.py:357 VALID_DASHBOARD_ROLES`, whose own comment names the
four missing ones and points at `tools/govlift/rbac.py::GOVLIFT_ROLES`. B1 is
the case a table/column diff cannot see: no table changes, no column changes,
only an enum narrows.

*If #1296 will not land soon,* B1–B3 are P1 by consequence — enterprise SSO and
the whole RFI Workbench canvas are absent from every new PostgreSQL deployment —
and the DDL should be lifted out of that branch into its own migration.

### P3 — index only

Table and column present; the index is not. No functional break, no data loss.

| Entry | Missing index | Absent from |
|-------|---------------|-------------|
| `057_sg_raw_signals_processed` | `idx_sg_raw_signals_processed` | snapshot + live PG |
| `136_qdc_metrics` | `idx_qdc_metrics_key` | snapshot + live PG |
| `189_genesis_phase_log` | `idx_gpl_started_at` | snapshot (present live) |

All three re-verified: `grep -c` → 0 against the snapshot for each. Fold into
whichever migration closes A1/A2 if convenient; not worth one of its own.

`188_genesis_phase_log.sql` is the mirror case and is **not** actionable: its
`idx_gpl_tenant` **is** in the snapshot (`pg_consolidated.sql:15526`) and is
missing only from the local database. That is drift in one deployment, not a gap
in the product.

### P4 — inert, record but do not fix

| Entry | Missing | Why not to fix |
|-------|---------|----------------|
| `139_mfa_enforcement` | `dashboard_users.mfa_enabled`, `.totp_secret`, `.mfa_backup_codes`, `.mfa_verified_at` | MFA was reimplemented on a separate table; nothing reads these. Verified: `dashboard_users` in the snapshot has no `mfa`/`totp` column, and the only `totp_secret*` in the file is `ad_user_mfa.totp_secret_encrypted` (line 3811). #1296 already records it *delete-don't-run*. |
| `184_creative_gap_innovation_signal.sql` | `creative_gap`, `innovation_signal`, 8 indexes | **Downgraded by this audit** — see below. Zero consumers. |

---

## Two corrections to d4

**1. `184_creative_gap_innovation_signal.sql` has no consumers.** d4 lists it
among the seven gaps with live consumers. It is not one; it is inert, and the
live-consumer count is **six**, not seven.

d4 warned about exactly this trap on the snapshot side — "`innovation_signal**s**`
(plural) is a *different* table" — and then did not apply the same discipline to
the consumer scan. A word-bounded search finds no SQL against either table
anywhere:

```
$ grep -rniE "(FROM|INTO|UPDATE|JOIN)[[:space:]]+(public\.)?(creative_gap|innovation_signal)\b" \
    tools/ apps/ icdev/ | grep -v db/migrations
(no output)
```

Every apparent hit in the tree is either the plural `innovation_signals` (the
real, present table — `tools/tech_radar/radar_engine.py:214` writes it), a
foreign-key *column* named `innovation_signal_id` / `creative_gap_id` on some
other table, a config key `innovation_signal_min_score`, or prose in a kanban
seed file. The engine d4's own note names, `tools/creative/gap_scorer.py`, writes
`creative_feature_gaps` (lines 710, 791, 815), not `creative_gap`.

The gap is real — neither table is in the snapshot — but it costs nothing.

**2. The consumer surface for three of the P2 entries is wider than d4 reported.**
d4 lists 3 consumers for `sso_*`, 5 for `rfi_workbench_*`, and 1 module for
`tenant_component_overrides`. The full lists are above: 6, 9, and 4. This does
not change any verdict; it raises the blast radius. #1296's own migration
comments already carry the wider lists, which is corroboration, not coincidence.

Neither correction moves the totals in the summary table: 10 / 39 / 11 stands,
and the split inside the eleven becomes 6 live-consumer / 3 index / 2 inert.

---

## Cross-reference: PR #1199 (merged 2026-08-02)

#1199 triaged 71 shadowed migrations, found 10 that "left a real gap", and
renumbered them to 331–340. Those ten are not in d1's 60 — renumbering made them
pending, so they left the shadowed set. **There is no overlap of entries, and a
complete overlap of subject matter.**

The useful finding is *why #1199 called the remaining entries benign when six of
them break live code on PostgreSQL*. Its two dismissal buckets are exactly where
the six live:

| #1199 bucket | Count | Which of the eleven PostgreSQL gaps it swallowed |
|--------------|-------|--------------------------------------------------|
| "Benign — tables exist anyway" | 38 | `184`, `207`, `210`, `236` — all four **are** in the live PostgreSQL database and **none** is in `pg_consolidated.sql` |
| "Create no tables (ALTER/data only)" | 9 | `057`, `139_govlift_rbac_roles`, `139_mfa_enforcement`, `247`, `257` |

Two distinct methodological misses, one per bucket:

1. **The wrong PostgreSQL oracle.** "Tables exist anyway" was measured against
   the live database, which has had migrations hand-run against it for months.
   Its having a table proves that *that* database has it, not that a new
   deployment would. `sso_providers` is the clean demonstration: present live,
   `grep -c` → 0 in the snapshot. d3 and d4 both name this as the reason oracle 3
   decides and oracle 2 alone is weak.
2. **"Creates no tables" is not "changes nothing".** An enum-widening migration
   adds no table and no column and is invisible to a table diff — and `247` is a
   migration written *because* `139` was PG-incompatible, then lost the same way.
   Both were filed under "ALTER/data only" and dropped.

#1199 also found, and fixed, `211_soc2_evidence` declaring `def up() -> None`
while `MigrationRunner` calls `mod.up(conn)`. **Two more instances survive**, and
d4 caught them: `210_sso` (`up.py:6 def up():`) and `215_user_preferences`
(`up.py:30 def up() -> None:`), against
`tools/db/migration_runner.py:452 mod.up(conn)`. Both raise `TypeError` on first
contact with the runner. Neither is reached today because both are shadowed —
but A2's remediation must not simply renumber `210_sso`, and any future
un-shadowing of `215` needs the signature fixed first. #1296's fold-don't-renumber
approach sidesteps this for `210`; nothing covers `215`.

Finally, #1199's own stated lesson holds here and was independently re-derived by
d4: *exercise a migration through the runner's own path, never by hand-parsing
the file.* d4's `057_sg_raw_signals_processed` verdict is the same shape — the
replay's empty delta was an artefact of a swallowed `except Exception: pass`, not
evidence the column existed.

## Cross-reference: PR #1296 (`feat/mvs-audit-03-shadowed-audit`, **still open**)

#1296 audited the same 60 entries by rebuilding both backends from empty and
found **6 real gaps**. d3+d4 replayed them and found **11 PostgreSQL gaps** plus
the SQLite residue. Verified open and unmerged as of 2026-08-07
(`gh pr view 1296 --json state` → `OPEN`).

**They agree on every entry #1296 flagged.** All of #1296's six, plus its two
appendix entries, appear in d3/d4 with the same substance:

| #1296 finding | d3/d4 verdict | Agree |
|---------------|---------------|-------|
| `247` + `139_govlift_rbac_roles` — role CHECK | PG gap, consumers | ✓ |
| `236_rfi_workbench.sql` | PG gap, consumers | ✓ |
| `210_sso` | PG gap, consumers | ✓ |
| `113_kanban_vibe_tier1` (SQLite) | `gap_sqlite_chain_only` (d4 #33) | ✓ |
| `055_sg_conflict_events_cyber_op` (SQLite) | `gap_sqlite_chain_only` (d3 #17) | ✓ |
| `184_memory_fts5` (SQLite FTS) | `gap_sqlite_chain_only` (d4 #47) | ✓ |
| `050_theater_supply_chain` (SQLite) | `gap_sqlite_chain_only` (d3 #15) | ✓ |
| `139_mfa_enforcement` — gap left deliberately unfixed | PG gap, inert | ✓ |

Two independent methods, no contradictions. That is the strongest evidence in
this report that the P2 list is right.

**The delta is what #1296 does not fix.** Reading its remediation migration
directly off the branch:

```
$ git show FETCH_HEAD:tools/db/migrations/20260803204235_mvs_audit_03_shadowed_gaps/up.py \
    | grep -c tenant_component_overrides   → 0
    …source_wg_result_id / source_idr_session_id → 0
    …creative_gap / innovation_signal            → 0
    …idx_qdc_metrics_key / idx_sg_raw_signals_processed / idx_gpl_started_at → 0
    …mfa_enabled                                 → 0
```

So **merging #1296 closes B1–B3 and nothing else.** A1 and A2 — both with live
consumers, one failing open — remain, and so do P3 and P4.

The plausible reason #1296 cleared `207` is its "declaring source in the tree"
oracle, which subtracts only files that *never run*. `init_icdev_db.py` does run,
on SQLite, and it declares `tenant_component_overrides` at line 11077 — so the
entry clears, even though that file refuses PostgreSQL outright
(`init_icdev_db.py:11383`) and can therefore never supply the table on the
primary backend. d4's fourth oracle avoids this by scanning for *runtime*
creators only. The lesson is narrow and worth carrying: **a declaration in
`init_icdev_db.py` clears a SQLite gap and clears nothing on PostgreSQL.**

`args/migration_duplicate_versions.yaml` on `main` still has no inline reasons
and its header still misreports `53 duplicated versions; 70 migrations shadowed`
against a live count of 48/60. Do not tell anyone to read the reason in the YAML
until #1296 lands.

## The SQLite residue, ruled on

d3 and d4 each closed by handing d5 a residue: **47 tables and 86 indexes**
declared only by a shadowed entry, with no creator anywhere else — 8 tables / 37
indexes from d3, 39 tables / 49 indexes from d4. Both said the cost depends on
whether any supported SQLite deployment is built by replaying the chain.

**It is bounded to a hand-built local SQLite install, and it does not reach the
test suite.** Three checks:

1. `init_db()` (`tools/db/init_icdev_db.py:11402`) runs `SCHEMA_SQL` and returns;
   it delegates to the runner only if `schema_migrations` already exists, and
   `SCHEMA_SQL` does not create that table (`grep -c "CREATE TABLE IF NOT EXISTS
   schema_migrations"` → 0). So init alone never replays the chain.
2. A subsequent `python tools/db/migrate.py --up` does replay it — and the
   shadowed entries still do not run, by definition. init's 527 tables are a
   strict subset of the chain's 872, so the union is the chain and the residue is
   missing either way. Build order does not matter.
3. `tests/conftest.py` builds from `MINIMAL_ICDEV_SCHEMA` (line 78, applied at
   line 3937), not from init and not from the chain. The residue is invisible to
   pytest and cannot explain a test failure.

Consequently: **no P1–P3 action on the SQLite residue.** PostgreSQL is the
primary backend per CLAUDE.md; SQLite is the local/dev default. The one item
worth carrying forward is d3's note that
`idx_ad_coach_position_trap_dedup` is a dedup index rather than a performance
index, so on a chain-built SQLite database `ad_options_coach_events` is missing
entirely rather than merely unindexed — and `184_memory_fts5` (FTS silently
degraded to `ILIKE`), which #1296 already fixes.

## Everything not requiring action

**49 of 60.** 10 benign and 39 SQLite-chain-only. Per-entry evidence is in the
d3 and d4 tables; the classification is unchanged by this consolidation. Three
entries in that set are worth a line because their *reason* is not "the chain
declares it":

- `269_kg_empty_graph_counts.sql` declares no DDL at all. What was lost is a data
  correction (39 `kg_graphs` rows claiming 59 phantom entities, which the
  dashboard sums directly). **That is a data question, still open, and out of
  scope for a schema audit** — flagged here rather than closed.
- `173_white_team_review_type.py` and `179_kanban_task_revivals.py` are not
  shadowed at all — they are bare `NNN_name.py` files and
  `MigrationRunner.discover_migrations` skips non-directories
  (`migration_runner.py:271`). `migration_versions.py::shadowed_migrations()`
  scans the filesystem and so misreports them. Their schema outcome is benign
  either way (179's table is created at runtime; 173's value was superseded by a
  post-`through_version` migration that does run), so this is a **gate accuracy**
  defect, not a schema gap. It belongs with the 17 other
  `up.sql`/`up.py`-less migrations CLAUDE.md already names.

## Prioritized summary

| P | Entry | Action | Owner artefact |
|---|-------|--------|----------------|
| **1** | `207_tenant_component_overrides` | new migration — table + 2 indexes | none exists |
| **1** | `257_idr_dic_doc_link.sql` | new migration — 2 columns on `dic_documents` only | none exists |
| 2 | `139_govlift_rbac_roles` + `247_dashboard_users_role_check` | merge PR #1296 | `20260803204235_mvs_audit_03_shadowed_gaps` |
| 2 | `210_sso` | merge PR #1296 (do **not** renumber — `def up()` signature) | same |
| 2 | `236_rfi_workbench.sql` | merge PR #1296 | same |
| 3 | `057`, `136`, `189` | 3 indexes, fold into the P1 migration | none exists |
| 4 | `139_mfa_enforcement` | record delete-don't-run | #1296 allowlist |
| 4 | `184_creative_gap_innovation_signal.sql` | record delete-don't-run (**new**) | none exists |
| — | `215_user_preferences` | fix `def up()` → `def up(conn=None)` before any future un-shadowing | none exists |
| — | 49 remaining entries | leave grandfathered | — |

Both P1 migrations must be scaffolded, never hand-numbered:

```bash
python tools/db/migrate.py --create "tenant_component_overrides"
python tools/db/migrate.py --create "dic_documents_source_links"
```

and mirrored to `icdev/tools/db/migrations/` — the runner reads its own mirror,
and all 60 audited entries exist in both trees.

## Limits

- The claim "a fresh PostgreSQL database lacks this" is deductive, not measured,
  and inherits d4's chain of reasoning: absent from `pg_consolidated.sql` +
  version ≤ `through_version` (so `bootstrap_pg.py` marks it applied without
  running) + no runtime creator + `init_icdev_db.py` refuses PostgreSQL. Each
  link is verified in this report against the file or the source line cited.
  Building a fresh database with `bootstrap_pg.py` and diffing it would measure
  it directly, and is the obvious next task.
- Snapshot verdicts here use plain `grep -c` on object names plus, where the name
  is a substring of a live object, a `CREATE TABLE` / column-block check. The
  three cases where that mattered are recorded inline: `creative_gap` (matches
  the column `creative_gap_id`), `innovation_signal` (matches
  `innovation_signals`), `totp_secret` (matches `ad_user_mfa.totp_secret_encrypted`).
- Consumer counts are `grep` over `tools/` and `apps/` for the object name.
  A consumer reaching a table through a dynamically built name would be missed.
- #1296's contents are read from `origin/feat/mvs-audit-03-shadowed-audit` at the
  time of writing. If that branch moves, re-check the delta before acting on P2.
- Audited `tools/db/migrations`. `icdev/tools/db/migrations` is a separate tree;
  all 60 entries exist in both, but the trees are known to diverge elsewhere
  (#1296 reports 27 missing from the mirror, 20 stale, 17 colliding versions that
  differ). Reconciling them is separate work.
