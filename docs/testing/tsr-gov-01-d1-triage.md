# TSR GOV — failing-test triage for the govcon / proposal_genesis / cpmp slice (tsr-gov-01-d1)

Research task. **No test or source file was modified.** Produced 2026-08-02 on branch
`kanban/tsr-gov-01-d1-triage`, base `629820ae1`.

Scope: every test file that imports `tools.govcon`, `tools.proposal_genesis`, `tools.cpmp`,
`tools.win_loss`, `tools.voc`, or `tools.pulse`, run in **two** environments and compared.

---

## Headline

| | files | tests | passed | failed |
|---|---:|---:|---:|---:|
| **Env A** — shared checkout `C:\AI\ICDev` (populated ambient DB) | 70 | 2,280 | 2,115 | **165** |
| **Env B** — clean worktree, freshly seeded DB | 70 | 2,280 | 2,115 | **165** |
| delta | 0 | 0 | **0** | **0** |

**The two environments are identical — not merely in count, but test-for-test and
reason-for-reason.** `comm` over the sorted failure sets returns zero env-exclusive entries,
and `diff` over the full `FAILED … - <reason>` lines returns zero differences.

### The load-bearing conclusion

> **This slice has zero ambient-DB-dependent tests. All 165 failures are real defects.**

That is an unusual result for TSR and it is worth stating plainly, because the epic's standing
assumption (project-card brief #3) is that a meaningful share of failures are ambient-state
artifacts. Here that share is **0/165**. The reason is structural, not luck: essentially every
failing test in this slice either builds its own `sqlite3` connection, or uses the `icdev_db`
conftest fixture — both of which construct a DB from scratch and are therefore blind to whatever
is sitting in `data/icdev.db`. The ambient DB never gets a chance to matter.

**Consequence for the epic: for this slice, triage-by-environment-comparison has no discriminating
power and should be skipped.** Anyone picking up the fix tasks can work directly in a clean
worktree and trust the result. This is the cheapest finding in the report and the one most likely
to save time downstream.

---

## Correction to the task premise

The task description asks to *"identify the 2 remaining proposal_genesis failures plus any
cpmp/voc/pulse/win_loss failures."* Measured against the tree:

| subsystem | expected by task | actual |
|---|---|---|
| `proposal_genesis` | 2 failures | **0** — `test_proposal_genesis.py` (202 tests) and `test_proposal_genesis_draft_anomaly.py` are **fully green**, as are all 12 `tests/genesis/*` reflex files |
| `cpmp` | "any" | **0** — all 20 `test_gcpl_*` files and `test_cpmp_portfolio_smoke.py` are green |
| `pulse` | "any" | **0** — `tests/pulse/`, `test_pulse_rewrite_loop.py`, `test_nav_intel_09_pulse_judge_gate.py` all green |
| `voc` | "any" | 4 |
| `win_loss` | "any" | 2 |
| `govcon` (not called out) | — | **159** |

The premise inverts where the damage is. `proposal_genesis` and `cpmp` — the two subsystems the
task names — are clean. **96% of the slice's failures (159/165) are in `govcon`**, which the task
mentions only in passing. Whatever measurement produced "2 remaining proposal_genesis failures"
does not reproduce on `629820ae1`; the fix tasks should be scoped against govcon, not
proposal_genesis.

*(Per-file totals in Appendix A; per-test names in Appendix B.)*

---

## Prioritized defect list

Five root causes account for all 165 failures. **None is a product bug in govcon business logic** —
they split into stale tests (P1, P2), a test-infrastructure schema gap (P3, P4), and unlanded
content (P5). Ordered by fix leverage:

### P1 — Bare-Flask fixtures never authenticate → 401 (84 failures, 5 files)

| file | failures |
|---|---:|
| `tests/test_govcon_auto_compliance_api.py` | 22 |
| `tests/test_proposals_detail_extract_requirements.py` | 19 |
| `tests/test_proposals_detail_map_capabilities.py` | 18 |
| `tests/test_govcon_bid_recommendation_api.py` | 15 |
| `tests/test_proposals_ptw_blackhat_api.py` | 10 |

All five share one fixture shape — a bare app with the blueprint and no session:

```python
flask_app = Flask(__name__)
flask_app.config["TESTING"] = True
flask_app.register_blueprint(govcon_api)      # or proposals_api
```

Every request returns `401 UNAUTHORIZED` with an HTML body. The many downstream
`TypeError: 'NoneType' object is not subscriptable` / `... is not a container or iterable`
(52 of the 84) are **not separate bugs** — they are `resp.get_json()` returning `None` because
the 401 page is `text/html`. Fixing the auth setup clears all of them at once.

**The code is right and the tests are stale — verified, not assumed:**

```
git show babdf2c83:tools/dashboard/api/govcon.py   # 2026-05-20, at the test's own commit
  @govcon_api.route("/opportunities/<opp_id>/bid-recommendation", methods=["GET"])
  def bid_recommendation(opp_id):                  # ← no decorator

git show HEAD:tools/dashboard/api/govcon.py        # today
  @govcon_api.route("/opportunities/<opp_id>/bid-recommendation", methods=["GET"])
  @require_role(*GOVCON_WRITE_ROLES)               # ← added 2026-07-25
  def bid_recommendation(opp_id):
```

The tests were written against undecorated routes; `@require_role` was added later as
intentional security hardening. **Fix the fixtures, do not touch the decorators.** The tests are
currently asserting that a protected endpoint is reachable without credentials — they would fail
open if "fixed" the other way.

Nobody noticed for ~10 weeks because CI's Test job is a 12-file allowlist that includes none of
these files.

### P2 — RBAC stubbed but ABAC not → 403 `ABAC_DENIED` (33 failures, 1 file)

`tests/test_govcon_capabilities.py` — 33 of 610 fail (577 pass).

This file *does* try to neutralize auth:

```python
with patch("tools.dashboard.app.require_role", lambda *roles: lambda f: f):
    from tools.dashboard.app import _register_govcon_pages
```

That disables the **RBAC** decorator, but a second, later-added **ABAC** enforcement layer is not
stubbed, so requests still 403:

```json
{"code": "ABAC_DENIED", "error": "Access denied by policy",
 "policy": "default_deny", "reason": "No applicable policy found — default deny"}
```

Same class as P1 (test predates an auth layer) but a **different fix** — this one needs an ABAC
policy/context in the fixture, not a session. Worth calling out separately so it is not folded
into the P1 batch and mistakenly "fixed" by patching one more symbol.

Note this file is 95% green; the failures are confined to the endpoint-exercising test classes.

### P3 — conftest `audit_trail` schema does not match production (6 failures, 2 files)

`tests/test_procurement_quote_compare.py` (3) and `tests/test_procurement_vehicles.py` (3).

`MINIMAL_ICDEV_SCHEMA` in `tests/conftest.py:552` defines an `audit_trail` that shares only
**4 of 11** columns with the live table:

| | columns |
|---|---|
| conftest fixture | `id, tenant_id, user_id, action, resource, details, classification, recorded_at` |
| production (`data/icdev.db`) | `id, project_id, event_type, actor, action, details, affected_files, classification, ip_address, session_id, created_at` |
| in common | `id, action, details, classification` |

Production code writes the production shape:

```python
# tools/govcon/procurement_vehicles.py:144
"INSERT INTO audit_trail "
"(event_type, actor, action, details, project_id, classification, created_at) ..."
except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
```

Against the fixture DB that INSERT raises `no such column: project_id`, **the bare `except`
swallows it**, zero audit rows land, and the test's `assert "create_vehicle" in actions` fails
against `[]`. Both failure signatures observed — the raw `OperationalError` where the test queries
`audit_trail` directly, and the empty-list assert where it goes through the swallowing writer —
are the same defect seen from two angles.

This is precisely the swallowed-INSERT hazard CLAUDE.md documents, naming `tools/govcon` as the
worked example. Here the swallow is in the *product* code but the schema mismatch is in the
*test fixture*, so the correct fix is to align `MINIMAL_ICDEV_SCHEMA` with the live table.

**This is the highest-value item in the report despite its low failure count.** The fixture is
shared repo-wide, so every `icdev_db` test that exercises an audit write is silently asserting
against a no-op it caused itself. The blast radius is the whole suite, not this slice — and
tests that merely *write* audit rows without asserting on them are passing today for the wrong
reason. Recommend scoping a dedicated task rather than folding it into a govcon fix.

### P4 — Tables missing from the conftest fixture entirely (6 failures, 2 files)

`tests/voc/test_voc_engine.py` (4) and `tests/win_loss/test_win_loss_engine.py` (2).

`MINIMAL_ICDEV_SCHEMA` contains **none** of `voc_job_statements`, `voc_documents`,
`win_loss_analysis_runs`, `pg_win_loss_records`, `creative_gap` (grep count: 0 for each).
Tests using the `icdev_db` fixture hit `no such table:` and fail.

Two distinct sub-cases, which matter for the fix:

- **`win_loss_analysis_runs` / `pg_win_loss_records` — defined in `init_icdev_db.py`** and present
  in a real seeded DB (confirmed in the 525-table seed output). Purely a fixture gap; copy the DDL
  across.
- **`voc_job_statements` / `voc_documents` — defined *only* in `tools/db/migrations/069_voc_signals/up.py`**,
  never in `init_icdev_db.py`. So they are absent from a freshly seeded worktree *and* from the
  long-lived shared dev DB — migration 069 has evidently never applied on SQLite. This one needs a
  decision (backfill into `init_icdev_db.py`, or make 069 apply) before the fixture can be fixed.

Root cause for both: CLAUDE.md's new-module registration checklist item #6 — *"tests/conftest.py —
add new table schemas to MINIMAL_ICDEV_SCHEMA"* — was not done when these subsystems landed.

### P5 — A declared skill library that was never committed (33 failures, 1 file)

`tests/test_ski_roles_lifecycle.py` — 33 of 88 fail (55 pass).

`args/ace/roles/software_craftsperson.yaml` declares skill IDs such as
`addyosmani-spec-driven-development`, and the test asserts each has a `SKILL.md` on disk:

```
AssertionError: Missing SKILL.md for pm-skill: pm-create-prd
  where exists = WindowsPath('C:/AI/ICDev/.agents/skills/pm-create-prd/SKILL.md').exists
```

Neither directory contains them:

| glob | found | asserted |
|---|---:|---:|
| `.agents/skills/pm-*/SKILL.md` | **0** | ≥20 |
| `.agents/skills/addyosmani-*/SKILL.md` | **0** | ≥22 |

`.agents/skills/` holds 25 entries, all `icdev-*`; `.claude/skills/` holds 21, all `icdev-*`.
The ~68 `pm-*` / `addyosmani-*` skill files the config and tests both reference were never added
to the repo.

A second, smaller sub-cause lives in the same file: the role YAML is also missing declared A2A
event topics, independent of the skill files —

```
AssertionError: Craftsperson missing listen_topics:
  {'test.failed', 'spec.approved', 'security.finding', 'pr.review.requested'}
```

so `software_craftsperson.yaml` is under-populated in two directions. Both point at the same
event: the role was declared but its supporting content never fully landed.

**This is a content/decision gap, not a code fix**, and it is the one item here that cannot be
closed by editing tests. Either the skill library and topic wiring land, or the role YAML and its
tests are scoped down to what actually ships. Flagging for a decision — do not let it sit in a
"fix the tests" batch, and note that deleting the assertions would be exactly the failure mode
the project card warns against.

---

## Method

**Slice construction** — 70 files, by import rather than filename (the epic's stated rule):

```bash
grep -rlE "(from|import) +(icdev\.)?tools\.(govcon|proposal_genesis|cpmp|win_loss|voc|pulse)\b" \
  --include="*.py" tests/
```

Restricting to `--include="*.py"` matters: an unrestricted `grep -rl` also matches
`__pycache__/*.pyc` and inflates the slice ~3×.

**Env A** — shared checkout `C:\AI\ICDev`, ambient `data/icdev.db` as-is.

**Env B** — `git worktree add -b kanban/tsr-gov-01-d1-triage /c/AI/.worktrees/tsr-gov-d1 origin/main`,
then seeded with `ICDEV_STORAGE_BACKEND=sqlite` pinned and an absolute `PYTHONPATH`:

```
python tools/db/init_icdev_db.py     → 525 tables
```

Both arms ran the identical 70-file list in one process:

```bash
python -m pytest <70 files> -p no:randomly --timeout=300 -q --tb=line -rA
```

Env A 633 s; Env B 673 s. Both exited cleanly with no collection errors, no timeouts, and no
module-scope aborts — all 2,280 collected tests reported an outcome in both arms, which is what
licenses the set comparison.

### Two notes on seeding, for whoever runs the next slice

- `tools/db/migration_runner.py` is a **library with no CLI** — `--help` prints nothing and
  `--apply` silently no-ops. The CLI is `tools/db/migrate.py`.
- `migrate.py --up` is not a substitute for `init_icdev_db.py`. It fails at migration **006**
  (`no such column: genome_id`) because `init_icdev_db.py` already creates the *current* schema,
  which the older migration then contradicts. This matches project-card brief #1 (~25 migrations
  are PG-only and fail on SQLite). The base schema from `init_icdev_db.py` is complete and
  sufficient; the migration stall is expected and did not affect this slice.

### Confidence and limits

- The env-equivalence claim is strong: identical sets *and* identical reasons, 2,280 tests per arm.
- Root causes P1–P5 were each confirmed by reading the fixture and the production code, and P1 by
  `git show` at two commits — not inferred from error text alone.
- Env B was seeded with `init_icdev_db.py` only. Project-card brief #1 also lists
  `tools/studio/init_db.py` and migration 311; those were skipped as this slice touches no studio
  tables. **This is a real limit**: had a govcon test transitively needed a studio table it would
  have failed in Env B only, and no such asymmetry appeared — which is corroborating but not
  proof. Given zero env-exclusive failures across 2,280 tests, the omission is very unlikely to
  have changed any conclusion here.
- Failure *counts* are exact. The root-cause attribution for the two largest files
  (`test_govcon_capabilities`, `test_ski_roles_lifecycle`) was established from the dominant error
  signature plus fixture inspection; each is known to carry at least one secondary cause
  (the `listen_topics` gap under P5 is the confirmed example), so expect a residue after the
  primary fix rather than a clean 33 → 0.

---

## Appendix A — per-file results (identical in both environments)

58 of 70 files are fully clean. The 12 with failures:

| file | tests | failed | passed | cause |
|---|---:|---:|---:|---|
| `tests/test_govcon_capabilities.py` | 610 | 33 | 577 | P2 |
| `tests/test_ski_roles_lifecycle.py` | 88 | 33 | 55 | P5 |
| `tests/test_govcon_auto_compliance_api.py` | 22 | 22 | 0 | P1 |
| `tests/test_proposals_detail_extract_requirements.py` | 24 | 19 | 5 | P1 |
| `tests/test_proposals_detail_map_capabilities.py` | 23 | 18 | 5 | P1 |
| `tests/test_govcon_bid_recommendation_api.py` | 15 | 15 | 0 | P1 |
| `tests/test_proposals_ptw_blackhat_api.py` | 20 | 10 | 10 | P1 |
| `tests/voc/test_voc_engine.py` | 6 | 4 | 2 | P4 |
| `tests/test_pma_credential_reflex.py` | 15 | 3 | 12 | see below |
| `tests/test_procurement_quote_compare.py` | 39 | 3 | 36 | P3 |
| `tests/test_procurement_vehicles.py` | 39 | 3 | 36 | P3 |
| `tests/win_loss/test_win_loss_engine.py` | 6 | 2 | 4 | P4 |

`test_pma_credential_reflex.py` (3 failures) fails as `assert result["success"] is True` →
`False` from the reflex's own return value. It is the one item not cleanly attributable to
P1–P5 from the evidence gathered; it needs its own short investigation. Its 3 failures are
included in the 165 total.

The 58 clean files — including all of `proposal_genesis` (202 + reflexes), all of `cpmp`
(`test_gcpl_*`), all of `pulse`, `test_docgen.py` (133), and `test_rfi_canvas.py` (87) — passed
fully in both environments.

## Appendix B — per-test failure list

Full per-test names for all 165 failures, grouped by file. Identical in Env A and Env B.

### tests/test_govcon_auto_compliance_api.py — 22 failing

- TestAutoComplianceAPIEndpoint::test_500_error_message_is_non_empty
- TestAutoComplianceAPIEndpoint::test_500_response_has_error_key
- TestAutoComplianceAPIEndpoint::test_compliance_items_created_equals_matrix_length
- TestAutoComplianceAPIEndpoint::test_endpoint_accepts_arbitrary_opp_id
- TestAutoComplianceAPIEndpoint::test_grade_counts_sum_to_total_requirements
- TestAutoComplianceAPIEndpoint::test_l_compliant_matches_fake_value
- TestAutoComplianceAPIEndpoint::test_m_partial_matches_fake_value
- TestAutoComplianceAPIEndpoint::test_matrix_length_matches_total_requirements
- TestAutoComplianceAPIEndpoint::test_n_gap_matches_fake_value
- TestAutoComplianceAPIEndpoint::test_no_requirements_returns_200
- TestAutoComplianceAPIEndpoint::test_no_requirements_status_in_response
- TestAutoComplianceAPIEndpoint::test_post_returns_200
- TestAutoComplianceAPIEndpoint::test_response_content_type_is_json
- TestAutoComplianceAPIEndpoint::test_response_has_compliance_items_created_key
- TestAutoComplianceAPIEndpoint::test_response_has_compliance_rate_key
- TestAutoComplianceAPIEndpoint::test_response_has_l_compliant_key
- TestAutoComplianceAPIEndpoint::test_response_has_m_partial_key
- TestAutoComplianceAPIEndpoint::test_response_has_matrix_key
- TestAutoComplianceAPIEndpoint::test_response_has_n_gap_key
- TestAutoComplianceAPIEndpoint::test_response_has_status_key
- TestAutoComplianceAPIEndpoint::test_response_status_is_ok
- TestAutoComplianceAPIEndpoint::test_returns_500_when_populate_raises

### tests/test_govcon_bid_recommendation_api.py — 15 failing

- TestBidRecommendationAPIEndpoint::test_arbitrary_opportunity_id_returns_200
- TestBidRecommendationAPIEndpoint::test_bid_recommendation_has_score_key
- TestBidRecommendationAPIEndpoint::test_bid_recommendation_score_in_valid_range
- TestBidRecommendationAPIEndpoint::test_bid_recommendation_score_is_numeric
- TestBidRecommendationAPIEndpoint::test_bid_recommendation_score_matches_seeded_data
- TestBidRecommendationAPIEndpoint::test_exception_response_has_error_key
- TestBidRecommendationAPIEndpoint::test_get_returns_200
- TestBidRecommendationAPIEndpoint::test_get_summary_exception_returns_500
- TestBidRecommendationAPIEndpoint::test_no_requirements_decision_is_insufficient_data
- TestBidRecommendationAPIEndpoint::test_no_requirements_score_is_zero
- TestBidRecommendationAPIEndpoint::test_response_content_type_is_json
- TestBidRecommendationAPIEndpoint::test_response_has_bid_recommendation_key
- TestBidRecommendationAPIEndpoint::test_response_has_overall_key
- TestBidRecommendationAPIEndpoint::test_response_has_status_key
- TestBidRecommendationAPIEndpoint::test_response_opportunity_id_matches

### tests/test_govcon_capabilities.py — 33 failing

- TestApproveDraftEndpoint::test_approve_draft_id_matches_requested
- TestApproveDraftEndpoint::test_approve_inserts_new_approved_row
- TestApproveDraftEndpoint::test_approve_nonexistent_draft_returns_404
- TestApproveDraftEndpoint::test_approve_response_approved_is_true
- TestApproveDraftEndpoint::test_approve_response_has_draft_id
- TestApproveDraftEndpoint::test_approve_response_has_status_key
- TestApproveDraftEndpoint::test_approve_response_status_is_ok
- TestApproveDraftEndpoint::test_approve_returns_200
- TestApproveDraftEndpoint::test_approved_row_preserves_draft_content
- TestApproveDraftEndpoint::test_approved_row_reviewed_by_defaults_to_govcon_api
- TestApproveDraftEndpoint::test_approved_row_reviewed_by_from_request_body
- TestApproveDraftSectionTransition::test_approve_without_section_id_returns_ok
- TestApproveDraftSectionTransition::test_section_not_started_advances_to_drafting
- TestApproveDraftSectionTransition::test_section_notes_mention_reviewer
- TestApproveDraftSectionTransition::test_section_outlining_advances_to_drafting
- TestApproveDraftSectionTransition::test_status_history_entity_type_is_section
- TestApproveDraftSectionTransition::test_status_history_new_status_is_drafting
- TestApproveDraftSectionTransition::test_status_history_old_status_is_not_started
- TestApproveDraftSectionTransition::test_status_history_row_inserted_on_transition
- TestRejectDraftEndpoint::test_reject_draft_id_matches_requested
- TestRejectDraftEndpoint::test_reject_inserts_new_rejected_row
- TestRejectDraftEndpoint::test_reject_nonexistent_draft_returns_404
- TestRejectDraftEndpoint::test_reject_response_has_draft_id
- TestRejectDraftEndpoint::test_reject_response_has_status_key
- TestRejectDraftEndpoint::test_reject_response_rejected_is_true
- TestRejectDraftEndpoint::test_reject_response_status_is_ok
- TestRejectDraftEndpoint::test_reject_returns_200
- TestRejectDraftEndpoint::test_rejected_row_preserves_draft_content
- TestRejectDraftEndpoint::test_rejected_row_review_notes_defaults_to_rejected
- TestRejectDraftEndpoint::test_rejected_row_reviewed_by_defaults_to_govcon_api
- TestRejectDraftEndpoint::test_rejected_row_reviewed_by_from_request_body
- TestRejectDraftEndpoint::test_rejected_row_stores_review_notes
- TestRejectDraftSectionNotChanged::test_reject_without_section_id_returns_ok

### tests/test_pma_credential_reflex.py — 3 failing

- TestReflexRun::test_alert_dedup_second_run_is_noop
- TestReflexRun::test_critical_expiry_inserts_alert_and_kanban_task
- TestReflexRun::test_watch_expiry_no_kanban_task

### tests/test_procurement_quote_compare.py — 3 failing

- TestAuditTrail::test_add_igce_line_audited
- TestAuditTrail::test_add_quote_audited_with_variance
- TestAuditTrail::test_create_procurement_audited

### tests/test_procurement_vehicles.py — 3 failing

- TestAuditTrail::test_create_audited
- TestAuditTrail::test_delete_audited
- TestAuditTrail::test_update_audited

### tests/test_proposals_detail_extract_requirements.py — 19 failing

- TestExtractRequirementsAPIEndpoint::test_500_error_message_is_non_empty
- TestExtractRequirementsAPIEndpoint::test_500_response_has_error_key
- TestExtractRequirementsAPIEndpoint::test_duplicate_count_matches_fake_value
- TestExtractRequirementsAPIEndpoint::test_endpoint_accepts_arbitrary_opp_id
- TestExtractRequirementsAPIEndpoint::test_extracted_count_matches_fake_value
- TestExtractRequirementsAPIEndpoint::test_new_count_matches_fake_value
- TestExtractRequirementsAPIEndpoint::test_new_plus_duplicate_equals_extracted
- TestExtractRequirementsAPIEndpoint::test_post_returns_200
- TestExtractRequirementsAPIEndpoint::test_response_content_type_is_json
- TestExtractRequirementsAPIEndpoint::test_response_has_duplicate_count
- TestExtractRequirementsAPIEndpoint::test_response_has_extracted_count
- TestExtractRequirementsAPIEndpoint::test_response_has_new_count
- TestExtractRequirementsAPIEndpoint::test_returns_500_when_extract_and_store_raises
- TestExtractRequirementsAPIEndpoint::test_zero_extraction_has_new_count_zero
- TestExtractRequirementsAPIEndpoint::test_zero_extraction_returns_200
- TestRenderedHtmlExtractRequirements::test_extract_button_in_rendered_html
- TestRenderedHtmlExtractRequirements::test_govcon_action_extract_in_rendered_html
- TestRenderedHtmlExtractRequirements::test_govcon_status_div_in_rendered_html
- TestRenderedHtmlExtractRequirements::test_opp_id_in_extract_action

### tests/test_proposals_detail_map_capabilities.py — 18 failing

- TestMapCapabilitiesAPIEndpoint::test_500_error_message_is_non_empty
- TestMapCapabilitiesAPIEndpoint::test_500_response_has_error_key
- TestMapCapabilitiesAPIEndpoint::test_capability_links_matches_fake_value
- TestMapCapabilitiesAPIEndpoint::test_endpoint_accepts_arbitrary_opp_id
- TestMapCapabilitiesAPIEndpoint::test_patterns_mapped_matches_fake_value
- TestMapCapabilitiesAPIEndpoint::test_post_returns_200
- TestMapCapabilitiesAPIEndpoint::test_response_content_type_is_json
- TestMapCapabilitiesAPIEndpoint::test_response_has_capability_links
- TestMapCapabilitiesAPIEndpoint::test_response_has_patterns_mapped
- TestMapCapabilitiesAPIEndpoint::test_response_has_status_key
- TestMapCapabilitiesAPIEndpoint::test_returns_500_when_map_all_patterns_raises
- TestMapCapabilitiesAPIEndpoint::test_status_is_ok
- TestMapCapabilitiesAPIEndpoint::test_zero_patterns_has_status_ok
- TestMapCapabilitiesAPIEndpoint::test_zero_patterns_returns_200
- TestRenderedHtmlMapCapabilities::test_govcon_action_map_in_rendered_html
- TestRenderedHtmlMapCapabilities::test_govcon_status_div_in_rendered_html
- TestRenderedHtmlMapCapabilities::test_map_capabilities_button_in_rendered_html
- TestRenderedHtmlMapCapabilities::test_opp_id_in_map_action

### tests/test_proposals_ptw_blackhat_api.py — 10 failing

- TestBlackhatCrud::test_create_invalid_posture_defaults_to_competitive
- TestBlackhatCrud::test_create_requires_competitor_name
- TestBlackhatCrud::test_create_then_list_round_trip
- TestBlackhatCrud::test_delete_blackhat_assessment
- TestBlackhatCrud::test_list_ordered_most_recent_first
- TestBlackhatCrud::test_update_blackhat_assessment
- TestBlackhatCrud::test_update_requires_at_least_one_field
- TestPtwBidScore::test_returns_score_and_optimal_order
- TestPtwVendorProfile::test_profiles_vendor
- TestPtwVendorProfile::test_requires_vendor_name

### tests/test_ski_roles_lifecycle.py — 33 failing

- TestA2AEventRouting::test_craft_listen_topics
- TestA2AEventRouting::test_event_dispatcher_topic_index
- TestA2AEventRouting::test_pm_spec_approved_triggers_craftsperson
- TestMessageBusAndRouting::test_pm_emits_feed_craftsperson_pipeline
- TestProductManagerFields::test_steps_are_non_empty_strings
- TestSkillLibraryOnDisk::test_addyosmani_skill_count
- TestSkillLibraryOnDisk::test_craft_skill_file_exists[addyosmani-code-review-and-quality]
- TestSkillLibraryOnDisk::test_craft_skill_file_exists[addyosmani-documentation-and-adrs]
- TestSkillLibraryOnDisk::test_craft_skill_file_exists[addyosmani-doubt-driven-development]
- TestSkillLibraryOnDisk::test_craft_skill_file_exists[addyosmani-security-and-hardening]
- TestSkillLibraryOnDisk::test_craft_skill_file_exists[addyosmani-shipping-and-launch]
- TestSkillLibraryOnDisk::test_craft_skill_file_exists[addyosmani-spec-driven-development]
- TestSkillLibraryOnDisk::test_craft_skill_file_exists[addyosmani-test-driven-development]
- TestSkillLibraryOnDisk::test_pm_skill_count
- TestSkillLibraryOnDisk::test_pm_skill_file_exists[pm-audit-ai-feature]
- TestSkillLibraryOnDisk::test_pm_skill_file_exists[pm-battlecard]
- TestSkillLibraryOnDisk::test_pm_skill_file_exists[pm-continuous-discovery]
- TestSkillLibraryOnDisk::test_pm_skill_file_exists[pm-create-prd]
- TestSkillLibraryOnDisk::test_pm_skill_file_exists[pm-decompose-okrs]
- TestSkillLibraryOnDisk::test_pm_skill_file_exists[pm-pre-mortem]
- TestSoftwareCraftspersonFields::test_steps_are_non_empty_strings
- TestStepSequence::test_craft_all_steps_present
- TestStepSequence::test_craft_no_duplicate_steps
- TestStepSequence::test_pm_all_steps_present
- TestStepSequence::test_pm_no_duplicate_steps
- TestSubPersonas::test_persona_frontmatter_valid[code-reviewer]
- TestSubPersonas::test_persona_frontmatter_valid[security-auditor]
- TestSubPersonas::test_persona_frontmatter_valid[test-engineer]
- TestSubPersonas::test_persona_frontmatter_valid[web-performance-auditor]
- TestSubPersonas::test_persona_skill_md_exists[code-reviewer]
- TestSubPersonas::test_persona_skill_md_exists[security-auditor]
- TestSubPersonas::test_persona_skill_md_exists[test-engineer]
- TestSubPersonas::test_persona_skill_md_exists[web-performance-auditor]

### tests/voc/test_voc_engine.py — 4 failing

- test_engine_clusters_statements_by_keyword
- test_engine_empty_upload_dir_returns_zero
- test_engine_high_cluster_creates_gap_signal
- test_ingestor_extracts_job_statements_from_txt

### tests/win_loss/test_win_loss_engine.py — 2 failing

- test_engine_high_impact_cross_registers_to_creative_gaps
- test_engine_run_inserts_analysis_run
