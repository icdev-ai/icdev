<!-- CUI // SP-CTI -->

# DMX Living-Document Mode — Design Spike (dmx-live-01)

**Status:** Design spike (no code). **Gap:** DMX source analysis Gap #8 — "living-document mode" (high effort, UX-heavy).
**Question:** Is living-document mode (a) a new data model, (b) a thin UX layer over existing redline + version tables, or (c) already achievable via the existing DIC Tech Writer workspace plus a batch-approve action?
**Recommendation:** **Option (c)** — reuse the Tech Writer workspace, the existing suggestion queue, and the existing approve gate; add one thin *batch-approve* action. No new data model. **Go decision: adopt-later.**

---

## 1. Current-state finding (with citations)

### 1.1 What the redline path actually does

The DMX source analysis frames the pain as *"full document regeneration per drift sweep"* producing version churn. **The code does not regenerate documents and does not create versions per sweep.** A drift sweep drafts *redlines*, and each redline lands in two places:

- A **`dic_suggestions`** row via the shared `create_suggestion(...)` helper — `tools/doc_modernization/redline_drafter.py:229-243`.
- An append-only **`docmod_findings`** state row transitioning `open → redline_drafted` — `redline_drafter.py:246-267`.

The sweep is capped by `max_redlines_per_sweep` (`redline_drafter.py:284`; default `10` in `args/docmod/docmod_config.yaml:7`). No `dic_versions` row is written anywhere in the redline path.

`dic_suggestions` is the accumulation surface. Its lifecycle store (`tools/document_intelligence/suggestion_store.py`) defines two tables: mutable `dic_suggestions` (`suggestion_store.py:30-46`) and append-only NIST-AU `dic_suggestion_decisions` (`suggestion_store.py:49-60`). Crucially, `decide_suggestion(...)` (`suggestion_store.py:182-234`) only flips `status` to `accepted`/`rejected` and writes one decision row — **it does not apply the suggested content to a section and does not create a new version.** Accepting a redline today is an audit event, not a document mutation.

### 1.2 What the version + history model is

- **`dic_versions`** (`tools/db/schema/pg_consolidated.sql:11943-11956`): `version_id, doc_id, version_no, origin, status` (default `approved`), `content_sha256, created_by, tenant_id, classification`. AI generation writes `origin='ai_generated', status='pending_review'` and is *never auto-published* (`tools/document_intelligence/doc_generator.py:11-13, 704-743`).
- **Approve gate** (`tools/document_intelligence/blueprint.py:2419-2448`): a reviewer moves a version `pending_review → approved`; the publish gate (`ground-dic-05`) blocks on unresolved `[PLACEHOLDER]` tokens and surfaces numeric conflicts unless `force=true`.
- **`dic_edit_history`** (`tools/document_intelligence/history_recorder.py:28-43`): append-only NIST-AU; `record_edit(...)` (`history_recorder.py:64-116`) only ever `INSERT`s a before/after + unified diff.

### 1.3 What the Tech Writer workspace already is

Migration 230 (`tools/db/migrations/230_dic_techwriter_columns.sql`) adds `template_type` (6 kinds: `STANDARD_GUIDE, SOP, RUNBOOK, ARCH_NETWORK, ARCH_APPLICATION, ARCH_SYSTEM`) and `writeguard_mode` to `dic_documents`. The workspace ships RAG+KG-backed drafting with the full TRUST citation chain (`tools/document_intelligence/tech_writing_assist.py:330-505`) and instantiates docs at `status='approved'` in the `techwriter` category (`blueprint.py:102`). It already owns a review surface and the approve gate.

### 1.4 Is version proliferation real? — **No (measured).**

Queried the live dev DB (`ICDEV_STORAGE_BACKEND=postgresql`, `data`) on 2026-07-24:

| Metric | Value |
|---|---|
| `dic_versions` total rows | **30** |
| Max versions on any single `doc_id` | **3** (one doc: 1 approved + 2 pending_review) |
| `dic_versions` status split | 17 approved / 13 pending_review |
| `dic_versions` origin split | 14 ai_generated / 13 human_authored / 2 ai_regenerated / 1 template |
| `docmod_findings` state split | **40 open / 33 redline_drafted** |
| `dic_suggestions` | **33 pending, 100% `canvas_source='doc_modernization'`** |
| `dic_edit_history` rows | 2 |

**Finding:** version-table proliferation is *not* the pain — no document exceeds 3 versions, and the redline path never touches `dic_versions`. The real accumulation is **33 pending `dic_suggestions`, all from doc_modernization**, each demanding an individual accept/reject decision. The pain is **reviewer decision fatigue**, and a second gap: an accepted redline does not currently flow into any published version at all (§1.1). Living-document mode's genuine value is therefore *batching those per-item decisions into one approval that produces one new version* — not avoiding regeneration (there is none) and not de-duplicating versions (there is no churn).

---

## 2. The three options, evaluated against the code

### (a) A genuinely new data model — **REJECT**
Proposes a new "approved baseline" table + "accumulated redlines" table + "batch approval" table. Every piece already exists: the baseline is the latest `approved` `dic_versions` row; the accumulated redlines are `pending` `dic_suggestions`; the approval audit is `dic_suggestion_decisions` (append-only) plus the new-version insert. A parallel model would duplicate three tables, add fresh `tenant_id`/`classification` RLS surface and migrations, and fork the TRUST/placeholder gates. Violates YAGNI (Karpathy #3) and the "prefer simpler" guardrail.

### (b) A thin UX layer over existing tables — **PARTIAL / subsumed**
Directionally correct on substrate, but the analysis's term `docmod_redlines` is not a real table — redlines *are* `dic_suggestions`. The only thing genuinely missing under (b) is a single **apply-batch → one-version** action. That action is small enough that (b) collapses into (c); there is no separate "thin UX layer" worth its own model.

### (c) Existing Tech Writer workspace + a batch-approve action — **RECOMMEND**
Everything maps onto shipped primitives:

| Living-document concept | Existing primitive |
|---|---|
| Approved baseline | latest `dic_versions` row with `status='approved'` |
| Accumulated redlines | `dic_suggestions` rows, `status='pending'`, `canvas_source='doc_modernization'` |
| Periodic batch approval | **new action** → for the selected suggestions: `record_edit(...)` each into `dic_edit_history`, assemble ONE new `dic_versions` row (`version_no = max+1`, `origin='ai_regenerated'`, `status='pending_review'`), then run it through the existing approve gate (`blueprint.py:2419-2448`) |
| Approval audit | `dic_suggestion_decisions` (one `accepted` row per suggestion) + the new version row |
| Editing surface | the Tech Writer / DIC review pane that already renders pending suggestions and the approve button |

The single new capability is the batch **apply-and-version** service. No new tables. This is the smallest change that closes both real gaps (decision fatigue + the missing accepted-redline→version link).

---

## 3. Batch-approval flow mockup

```
DIC ▸ Tech Writer Workspace ▸ Document: "Cloud Ops Standard Guide"   [CUI]
─────────────────────────────────────────────────────────────────────────
Baseline: v3 (approved 2026-07-12, human_authored)      Drift: 8 open findings
Accumulated redlines from drift sweeps: 5 pending      [ Batch approve… ]
─────────────────────────────────────────────────────────────────────────
 sel  section                 change (cited)                    conf  band
 [x]  2.1 Compute             r5.large → r7g (Graviton) [src:3]  0.86  incl
 [x]  2.4 TLS                 TLS 1.2 → TLS 1.3        [src:1]    0.79  incl
 [x]  3.2 Backup cadence      6h → 4h RPO              [src:2]    0.74  incl
 [ ]  4.1 Vendor X EOL note   remove paragraph        [src:7]    0.58  flag ⚠
 [ ]  5.0 Pricing table       (no citation)                       —    blocked
─────────────────────────────────────────────────────────────────────────
 3 of 5 selected                          [ Preview merged v4 ]  [ Approve ]
═════════════════════════════════════════════════════════════════════════
Clicking [ Approve ]:
  1. decide_suggestion(accepted) × 3   → dic_suggestion_decisions (append-only)
  2. record_edit(before→after) × 3     → dic_edit_history        (append-only)
  3. INSERT dic_versions               → v4, origin='ai_regenerated',
                                          status='pending_review'  (NEW ROW)
  4. publish gate (ground-dic-05):     placeholders / citations / numeric
        pass → UPDATE v4 status='approved'
        fail → 409, v4 stays pending_review, reviewer resolves or force=true
─────────────────────────────────────────────────────────────────────────
 Result: baseline advances v3 → v4 in ONE reviewer action.
         Flagged/blocked redlines (2) remain pending for the next batch.
```

Hooks into existing screens: the selectable list *is* the current pending-suggestions pane (`get_pending_suggestions(canvas_source='doc_modernization')`); `[ Approve ]` reuses the version approve gate; per-row confidence/band/citation come straight off the redline result (`redline_drafter.py:211-214`).

---

## 4. Append-only invariant (non-negotiable)

The batch action is **insert-first** and touches no audit history:

- **`dic_versions`** — never `UPDATE`d in content; a batch always `INSERT`s one new row at `version_no = max+1`. The only mutation is the *new* row's `status` moving `pending_review → approved`, which is the existing, sanctioned approve semantics (`blueprint.py:2447`). Prior versions are immutable.
- **`dic_edit_history`** — `record_edit(...)` is `INSERT`-only (`history_recorder.py:88-102`); each applied redline adds one before/after/diff row. Never updated or deleted.
- **`dic_suggestion_decisions`** — append-only NIST-AU; one `accepted` row per suggestion in the batch.
- The one deliberately-mutable table, `dic_suggestions.status` (`pending → accepted`), is documented as mutable by design (`suggestion_store.py:4`) and is not an audit surface.

No `APPEND_ONLY_TABLES` entry changes because no new audit table is introduced under the recommended option.

---

## 5. DOCMOD invariants acknowledgment

1. **Deterministic-first verdicts** — untouched. Currency verdicts and finding generation stay deterministic; batch approve consumes already-drafted redlines and never re-runs LLM verdict logic.
2. **Append-only versions/history** — preserved exactly as in §4 (insert-new-version, insert-edit-history, insert-decision).
3. **HITL `pending_review` gating** — preserved and strengthened: the merged version is written `pending_review` and only a human `[ Approve ]` (through the placeholder/citation/numeric publish gate) can publish it. Nothing auto-publishes.
4. **`tenant_id` + `classification` RLS on new tables** — none required for option (c) (no new tables). Every insert carries the source suggestion's `tenant_id`/`classification` (already threaded through `create_suggestion`/`record_edit`/`dic_versions`). *If* a future optional batch-run audit table is added, it must carry `tenant_id`+`classification` and the standard RLS predicate.
5. **Air-gap fallbacks** — intact. Redline drafting already abstains cleanly when the LLM/RAG is unavailable (`redline_drafter.py:178-179`; `tech_writing_assist.py` never raises). Batch approve is *deterministic assembly* over already-approved redline text — it needs no LLM and works fully air-gapped.

---

## 6. Go / No-Go

**Decision: GO — adopt-later (sequence after in-flight DMX work; do not seed now).**

Rationale: the mechanism is sound and ~90% exists; the recommended change is one thin batch apply-and-version action, not a data model. It is *adopt-later* rather than *adopt-now* because (i) the measured accumulation (33 pending suggestions) is still manageable with the per-item accept/reject UI that already works, and (ii) the missing accepted-redline → published-version link (§1.1) is the real prerequisite and should be built and tested deliberately with the publish gate, not rushed.

**Follow-up work that WOULD be seeded (described only — not created here):** a small `docmod-batch` card, gate-held per DMX convention, roughly four tasks — (1) batch multi-select in the pending-suggestions / Tech Writer review pane; (2) an `apply_batch(...)` service that `record_edit`s each selected suggestion and assembles one `pending_review` `dic_versions` row (`origin='ai_regenerated'`); (3) route the merged version through the existing `ground-dic-05` publish gate (placeholders + citations + numeric conflicts) before approve, with the HITL `force` override + audit note preserved; (4) tests covering append-only invariants, air-gap determinism, and RLS on any new query paths. **No kanban tasks are created by this spike.**
