# Coherence-Gate Debt Triage — 2026-07-25

Categorized disposition of every finding from `python tools/workflow/coherence_checker.py --all`
as of `origin/main` @ `51d53a8b4`. Snapshot: **42 checks — 28 pass, 6 FAIL, 8 WARN**
(`overall_pass: false`). The gate fails on long-standing, repo-wide structural debt, not on
any single regression.

This document is the primary deliverable of the `chore/coherence-debt-triage` PR. Only a small,
mechanically-safe subset was fixed in the same PR (see **FIXED-here**); everything else is
dispositioned below with a reason.

> **Scope note (public repo).** This is a public repository. Security-relevant findings here are
> summarized by **count and category only**. Exact call-site locations for RLS-bypass /
> `security_context` items are tracked internally and are deliberately **not** enumerated in this
> doc, per the standing no-unpatched-vuln-detail-in-public policy.

Legend:
- **FIXED-here** — corrected in this PR (safe, mechanical, in-scope).
- **SAFE-TO-FIX-later** — mechanical and low-risk, but out of scope for a small PR (batch churn).
- **STRUCTURAL-defer** — needs design/human judgment; not an auto-fix.
- **INTENTIONAL** — working as designed; not a bug (e.g. stale-by-design mirrors, grandfathered debt).

---

## FIXED-here

### icdev_mirror_parity (was WARN, 1 finding) — FIXED
`tools/iqe/adapters/proposals.py` had no `icdev/tools/iqe/adapters/` twin. `tools/iqe/adapters`
is a tracked mirror-parity root (`args/mirror_parity.yaml`), and 60 of its 62 adapters are
byte-identical copies across the two trees, so the fix is an exact copy of the canonical
`tools/` file into `icdev/`. Generated child apps inherit IQE adapters from the `icdev/` package,
so the missing twin meant the `proposals.*` collections would be absent there.

Also brought **`govcon.py`** into parity in the same tracked root: the `icdev/` copy was stale
(missing `requirements_adapter` / the `govcon.requirements` registration added by prop-iqe-01 in
`tools/`). This drift is invisible to the current gate (the parity check only tests existence, and
`mirror_drift` does not cover `iqe/adapters`), but it is genuine drift in a tracked root where the
`tools/` side is unambiguously canonical and newer. Both are exact `tools/ → icdev/` copies; ruff
clean.

Files changed:
- `icdev/tools/iqe/adapters/proposals.py` (new — copy of `tools/` twin)
- `icdev/tools/iqe/adapters/govcon.py` (updated to match canonical `tools/` twin)

---

## Migration duplicate-number assessment — DOCUMENTED (not renumbered)

**Decision: do NOT renumber. Document as grandfathered debt.**

Findings and rationale:

1. **Scale.** `tools/db/migrations/` currently has **53 duplicate 3-digit prefixes** (not ~13),
   e.g. two `289_*` (`289_agent_cron_jobs.sql`, `289_twin_compat_reports.sql`). The entire tree
   is also mirrored under `icdev/tools/db/migrations/`, roughly doubling the rename surface.

2. **Runner semantics (corrects the "keys by filename" premise).** `MigrationRunner`
   (`tools/db/migration_runner.py`) keys by the **3-digit version number**, not the filename:
   `discover_migrations` extracts `version = re.match(r"^(\d{3})_", name).group(1)`, and
   `get_pending_migrations` dedupes by version — *keeping only the first by sort order*
   (see the in-code comment at ~L246–262, added after the CI E2E PG job hit a `schema_migrations.version`
   UNIQUE violation on a fresh DB). So for each dup pair the **second file by sort order is silently
   skipped** on a fresh database — it is not applied twice, and its DDL never runs via the runner.

3. **Why it is tolerated in practice.** The skipped-second tables are self-created by their runtime
   modules under the established "table existence handled gracefully" pattern, so the skip is masked:
   - `289_agent_cron_jobs` (first, wins the slot) — also self-created in `tools/agent_runtime/cron.py::_ensure_schema`.
   - `289_twin_compat_reports` (second, skipped) — self-created in `tools/twin_core/compat_report.py`.

4. **The gate agrees.** `check_migration_numbering` explicitly **grandfathers existing duplicates as
   WARN** and only FAILs on a *new* changed migration that reuses a number. Its intent is "prevent new
   collisions," not "renumber history."

5. **Renumbering is risky.** (a) Code references specific migration numbers in docstrings
   (`tools/agent_runtime/cron.py:10` and its `icdev/` mirror both say "migration 289"), so a rename
   drifts documentation. (b) On databases where a version was already applied, moving a dup to the next
   free number makes it **pending again** — re-running DDL that is only safe if the file is fully
   `IF NOT EXISTS`. Verifying that across 53 pairs × 2 trees is not a small-PR change.

**Recommendation (future, if ever pursued):** do not mass-renumber. Instead keep the WARN as tracked
debt; ensure every "second" dup file is idempotent (`IF NOT EXISTS`) and/or has a runtime self-create
fallback (most already do). Renumber only opportunistically, one pair at a time, when a file is being
edited anyway and is confirmed idempotent.

---

## STRUCTURAL-defer (do not auto-fix)

### canvas_placeholder_style (FAIL, 47) — STRUCTURAL
Despite the check name, these are **not** the Jinja2 `'%%.0f'|format(value)` template rule, and they
are **not injectable** — every finding is a parameterized Python `execute()` call using a bare `?`
placeholder where psycopg2 wants `%s` (`? raises ProgrammingError on PostgreSQL`; this is a
SQLite-vs-psycopg2 dialect bug, not a SQL-injection risk). They cluster across several canvas
`db/init_db.py` and engine paths.

Deferred because: (a) many sites are **init/seed paths** where `translate_sql` is the SQLite
init-fallback and a blanket `?`→`%s` rewrite can break the SQLite path; each site needs individual
judgment (parameterized runtime query vs. init DDL vs. literal). (b) Several files sit under
`data_canvas` / `observability_canvas` that other sessions may be editing — out of bounds for this PR.
Not a mechanical global replace.

### schema_code (FAIL, 2) — STRUCTURAL
- `tools/agent/token_tracker.py`: INSERT into `agent_token_usage` names unknown columns
  `['api_key_source', 'user_id']`.
- `tools/rag/sqlite_vector_store.py`: INSERT into `rag_chunks` names unknown column `['sign_bits']`.

Each is either a missing migration/column or a stale INSERT — needs the owning subsystem to decide
whether to add the column or drop it from the INSERT. Not a safe blind edit.

### fixture_schema (FAIL, 1) — STRUCTURAL
`tests/test_bdr_vv_suite.py`: the local `projects` fixture omits columns the test uses
(`description`, `impact_level`, `status`). Fix belongs to the test/fixture owner (align the fixture
to the shared conftest schema — cf. MEMORY "standardize tests on shared conftest schema").

### security_context (FAIL, 10) — STRUCTURAL
10 `set_security_context(None)` call sites lack the required `# rls-bypass: <reason>` annotation.
Each needs a human to confirm the bypass is legitimate and write an accurate justification (and a
task ID) per CLAUDE.md. Annotating without verifying the intent would defeat the check. Specific
locations are tracked internally, not enumerated here (public-repo policy). Deferred.

### new_page_completeness (FAIL, 2) — STRUCTURAL
- `tools/dashboard/templates/strategos/page.html`: no `strategos/blueprint.py`.
- `tch-completeness-sdc-iqe_widget`: `security_canvas/index.html` missing the `iqe_query_widget` include.

Both require building/wiring real components (blueprint module; IQE widget include) — the 8-component
page-completeness contract, not a triage fix.

---

## SAFE-TO-FIX-later (mechanical, but out of scope for a small PR)

### log_standard (FAIL, 16) — SAFE-TO-FIX-later
16 tools use raw `logging.getLogger()` instead of `tools.logging.icdev_logger.get_logger()`
(e.g. `tools/aisg/*` ×5, `tools/govcon/*` ×2, `tools/builder/child_app_generator.py`,
`tools/data_canvas/db/init_db.py`, `tools/fathomdesk/blueprint.py`, `boundary_canvas/cato_twin/...`).
The swap is mechanical but spans 16 unrelated modules across several subsystems (churn + broad blast
radius, some in canvases other sessions may touch). Belongs in a focused logging-migration PR, not
this triage.

### manifest (WARN, 5) — SAFE-TO-FIX-later
5 tools absent from the manifest shards: `tools/genesis/reflexes/{dic_inbox_sweep,observability_retention,odc_coverage_refresh}.py`
and `tools/doc_modernization/packs/{change_control,evidence_currency}.py`. Purely additive doc entries
in `tools/manifest/*.md`; low risk, but manifest shards are a known merge-conflict hotspot, so best
landed by the owning feature PRs rather than a cross-cutting triage.

---

## INTENTIONAL (working as designed — not a bug)

### mirror_drift (WARN, 17) — INTENTIONAL / stale-by-design
All 17 findings are in `llm`, `ace`, and `genesis/reflexes` — the packages MEMORY and CLAUDE.md flag
as **intentionally stale mirrors** ("Kanban reflex icdev/ mirror is STALE; tools/ copy is live",
genesis-reflex mirrors, gateway is tools/-only). None fall in a tracked mirror-parity root
(`cortex`, `quality`, `iqe/adapters`, `mcp/cortex_server.py`). The check is WARN-only by design and
explicitly not gate-blocking. **No action** — syncing these would fight the intended split. (The one
real, tracked-root drift, `iqe/adapters`, is handled under FIXED-here.)

### migration_numbering (WARN, 53) — INTENTIONAL / grandfathered
See the migration assessment above. WARN-only by design; the gate grandfathers existing dups and only
FAILs new collisions. **No action.**

### template_variable_parity (WARN, 514) & runtime_placeholder_style (WARN, 269) — INTENTIONAL / known-noise
High-volume WARN-only heuristics that surface long-standing template/placeholder debt across the whole
repo. Not gate-blocking; not a regression from any recent change. Left as tracked background debt.

### test_db_isolation (WARN, 194) — INTENTIONAL / known pattern
194 tests hand runtime code a raw `sqlite3` connection, bypassing `translate_sql` so `%s` SQL could
raise. This is a broad, pre-existing test-harness pattern (cf. MEMORY "tests w/ raw sqlite3 bypass
%s→? translator"). WARN-only; fixing belongs with each test's owner, not a triage PR.

### canvas_completeness (WARN, 1) — INTENTIONAL / legacy
`aiify_compat` missing a `nav_link` — a legacy compat canvas; WARN notes "legacy canvases may need
registry updates." Registry decision for the aiify owner, not triage.

> **RESOLVED 2026-08-02 (idp-score-05).** The disposition above is superseded. `aiify_compat` already
> declared `completeness.nav_link: false` — it is a 301-redirect alias with no sidebar entry by
> design — but the validator ignored the declaration and required the point anyway. Point 7 now
> honours an explicit `false` the way points 6 and 8 already did, so the canvas passes on its own
> terms. With the last finding cleared, `canvas_completeness` was flipped WARN → FAIL and declared
> blocking in `args/security_gates.yaml`, matching its filesystem-driven twin `new_page_completeness`.

---

## Summary — one line per non-passing category

| Check | Status | Disposition |
|-------|--------|-------------|
| icdev_mirror_parity | WARN(1) | **FIXED-here** — added `proposals.py` twin (+ `govcon.py` parity) in tracked root |
| migration_numbering | WARN(53) | **DOCUMENTED** — grandfathered; renumber unsafe (runner dedupes by number, self-create fallbacks mask skip) |
| canvas_placeholder_style | FAIL(47) | STRUCTURAL — psycopg2 `?`→`%s` in init/engine paths; per-site judgment |
| schema_code | FAIL(2) | STRUCTURAL — INSERT vs schema column mismatch; owner decision |
| fixture_schema | FAIL(1) | STRUCTURAL — test fixture missing `projects` columns |
| security_context | FAIL(10) | STRUCTURAL — undocumented RLS bypasses need human justification |
| new_page_completeness | FAIL(2) | STRUCTURAL — missing blueprint / IQE widget |
| log_standard | FAIL(16) | SAFE-TO-FIX-later — mechanical logger swap, broad blast radius |
| manifest | WARN(5) | SAFE-TO-FIX-later — additive manifest entries (conflict hotspot) |
| mirror_drift | WARN(17) | INTENTIONAL — stale-by-design llm/ace/genesis mirrors |
| template_variable_parity | WARN(514) | INTENTIONAL — known WARN-only noise |
| runtime_placeholder_style | WARN(269) | INTENTIONAL — known WARN-only noise |
| test_db_isolation | WARN(194) | INTENTIONAL — pre-existing raw-sqlite test pattern |
| canvas_completeness | WARN(1) | **FIXED-later (idp-score-05)** — `nav_link: false` waiver honoured; check flipped WARN → FAIL and declared blocking |
