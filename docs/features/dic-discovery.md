<!-- CUI // SP-CTI -->
# Document Intelligence Canvas (DIC) — Discovery & Feature Catalog

> Phase 0 output for the `dic-disco-01` kanban task. Curated enterprise document/
> knowledge-management feature set (ECM/Enterprise Content Services domain) + ICDEV™
> wow-factors, mapped to DIC epics. The `dic-disco-01` task augments this with live
> Innovation/Creative/Research engine output when run headlessly:
>
> ```
> python tools/creative/creative_engine.py --run --domain "enterprise document management" --json
> python tools/research/research_engine.py --run --vertical <slug> --focus-areas "document lifecycle,knowledge retention,compliance docs" --json
> python tools/innovation/innovation_manager.py --run --json
> ```
> (These perform web scanning; in air-gap they degrade gracefully. Run when the
> kanban_tasks DB write path is not under lock contention.)

## The problem (from the IRAD + user brief)
Enterprises drown in SOPs, contracts, runbooks, policies, procedures, and guidelines
that go stale the instant infrastructure or org reality changes. People retire and
institutional knowledge leaves with them. New staff inherit outdated docs and "don't
know where the bodies are buried." No platform keeps the paperwork alive.

## Baseline enterprise-DMS / ECM feature coverage (must-haves → DIC epic)

| # | Capability (industry-standard ECM) | DIC epic |
|---|------------------------------------|----------|
| 1 | Multimodal ingestion (PDF/DOCX/XLSX/PPTX/scanned-image+OCR/HTML/SharePoint/Confluence) | ingest |
| 2 | Full-text + semantic indexing over the corpus (RAG chunks + KG) | ingest, search |
| 3 | Search that returns **actual source content with citations** (not a generated summary) | search |
| 4 | Metadata & taxonomy (doc_type, owner, collection, classification, tags) | foundation |
| 5 | Version control with immutable history + diff | collab |
| 6 | Check-out / check-in locking (optimistic) to prevent clobbering | collab |
| 7 | Review & approval workflow (draft → pending → approved/rejected) | collab |
| 8 | Records management: retention tiers, audit trail (append-only) | foundation, collab |
| 9 | Access control: RBAC + ABAC + row-level security; classification-aware | authz |
| 10 | Freshness / staleness monitoring + alerts | freshness |
| 11 | Document generation/assembly from approved sources | generate |
| 12 | Templates & reusable snippets (use-case starters) | templates |
| 13 | Collaboration: multi-user, shared collections, need-to-know sharing | collab |
| 14 | Compliance mapping (NIST 800-53 / SSP fragments) | acoic |
| 15 | Bulk import + connectors (SharePoint, future CMDB/Confluence) | ingest |
| 16 | Natural-language query over the canvas data (IQE) | iqe |
| 17 | Headless CLI / API for automation | blueprint |
| 18 | On-prem / air-gap operation (local models only) | search, finetune |

## Wow-factors (differentiators beyond commodity ECM)

1. **ACOIC drift-driven auto-regeneration** — the canvas *watches live infrastructure*
   (NDC/IDC drift events), scores which documents are impacted, and regenerates the
   affected SOPs/runbooks/SSP fragments — the IRAD's patentable "drift-to-doc" idea.
   *(epic: acoic)*
2. **NO-LLM grounded search with mandatory citations** — answers come from real stored
   data via BM25 + KG traversal; every hit is cited (doc/version/page/chunk); works with
   zero models in air-gap. No fabrication, ever. *(epic: search)*
3. **"Why this result?" provenance** — shows the matched terms + KG path that produced
   a hit (explainability, not LLM rationalization). *(epic: search)*
4. **CoT/CoD-verified generation with abstention** — generated/regenerated text is
   replayed claim-by-claim against its cited evidence in a Chain-of-Debate; unsupported
   claims are stripped and the system abstains when evidence is insufficient. *(epic: verify, generate)*
5. **Explicit AI-content labeling + HITL promotion** — AI-generated drafts are visibly
   badged ("AI-generated — pending/approved by <user>") and never auto-published; only a
   human approver promotes them to the current version. *(epic: collab, generate)*
6. **Knowledge-handoff / retirement capture** — an *active workflow* (not just a template)
   that interviews a departing SME — agenda auto-built from the explorer's single-owner
   findings — and structures their tacit knowledge into a maintained collection via
   CoD-verified generation, directly attacking institutional-knowledge loss.
   *(epic: **handoff** (new, `dic-handoff-01`); template seed in templates)*
7. **KG "buried bodies" explorer** — graph view + ranked findings surfacing orphaned docs,
   single-owner/tribal knowledge, undocumented dependencies, and contradictory/superseded
   documents people forgot about. *(epic: **explore** (new, `dic-explore-01`); reuses search KG, surfaced via synergy)*
8. **Freshness heatmap** — at-a-glance staleness across the whole corpus for CISOs/PMs. *(epic: freshness)*
9. **Air-gap local fine-tuning** — optionally fine-tune a local model on the org's own
   corpus (Unsloth/Ollama, no cloud, no egress) to sharpen generation — never used in the
   grounded search path. *(epic: finetune)*
10. **Ecosystem synergy** — cross-canvas event bus, Awareness Engine indexing, MCP tools,
    and marketplace publishing of DIC templates as FORGE assets. *(epic: synergy)*

## Built-in use-case templates (epic: templates)
- **ACOIC** (flagship): infra-drift → impacted-doc regeneration → RICOAS NIST bridge.
- **SOP Refresh**: keep standard operating procedures current against process changes.
- **Contract Lifecycle**: ingest contracts, track obligations/renewals, flag stale clauses.
- **Runbook Validation**: validate runbooks against actual system behavior (AI GameDay tie-in).
- **Policy Library**: governed policy/procedure/guideline repository with approval gates.
- **Onboarding / Knowledge-Handoff**: capture retiring-SME knowledge into a living collection.

## Engine run evidence (Phase-0 `dic-disco-01`, 2026-05-30)

The three engines were run headlessly, scoped to enterprise document/knowledge
management. All three perform **live web scanning**; the Creative and Research full
pipelines exceeded the 280 s scan budget and were read from their **incrementally
persisted DB output** (the task explicitly allows "run *or read the captured output*").

**Research Engine** — `--vertical enterprise_content_services` (new vertical seeded at
`context/research/verticals/enterprise_content_services.json`; loaded via
`vertical_loader`). Session `rsess-afa8c52df237` captured **440 signals** before the
scan window closed (status `scanning`; SYNTHESIZE not reached).
- **Market categories confirmed** (G2/Capterra): Document Management, Enterprise Content
  Management (ECM), Knowledge Management, Contract Management, **Records Management**,
  Enterprise Search. → validates the baseline catalog scope; **Records Management** is a
  first-class ECM category (see gap analysis).
- **Leading OSS surfaced (by stars), mapped to DIC design:**
  - RAG engines — `infiniflow/ragflow`, `HKUDS/LightRAG`, `QuivrHQ/quivr`,
    `run-llama/llama_index`, **`VectifyAI/PageIndex` ("Vectorless, reasoning-based RAG")**
    → validates the DIC **no-LLM/no-vector grounded default** (`dic-search-01`).
  - GraphRAG — `microsoft/graphrag`, `HKUDS/LightRAG`, `abhigyanpatwari/GitNexus`
    (client-side knowledge graph) → validates the **KG bridge** + the new **"buried bodies"
    explorer** (`dic-explore-01`).
  - OCR / IDP — `PaddlePaddle/PaddleOCR`, `llama_index` OCR → validates `dic-ingest-02`.
  - DMS / KM proper — `paperless-ngx` ("scan, index, archive"), `siyuan`, `logseq`,
    `TriliumNext/Trilium`, `khoj-ai/khoj` ("AI second brain over your docs"),
    `Mintplex-Labs/anything-llm` (privacy-first, on-device) → validates corpus Q&A +
    the **air-gap** posture (`dic-finetune-01`, `dic-search`).
  - Persistent memory — `thedotmack/claude-mem`, `mem0ai/mem0` ("universal memory layer")
    → validates **knowledge-handoff / retention capture** (`dic-handoff-01`).

**Creative Engine** — `--domain "enterprise document management"`. Full pipeline timed out
on live competitor discovery (G2/Capterra/Reddit). Persisted output corroborates the
domain: surfaced **ownCloud** (file/document collaboration) as a competitor and
TrustRadius/VOC ("frustrated_by", "pain_point", "when_i…so_that") signals — thin but
on-domain. No *net-new* feature beyond the baseline catalog.

**Innovation Engine** — `--run` (internal self-improvement focus, not external market).
Confirmed platform readiness for DIC and is the source of this task's **seeder**
(`tools/kanban/seed_dic_kanban.py`). Introspection surfaced ICDEV-side items (unused
compliance tools, 36/1048 CLI tools missing `--json`, high-complexity blueprints) — tracked
elsewhere, not DIC features.

## Gap analysis — wow-factors vs. existing epics

| Wow-factor | Covered by | Verdict |
|------------|-----------|---------|
| ACOIC drift→regen | `dic-acoic-01/02` | ✅ covered |
| NO-LLM grounded search + mandatory citations | `dic-search-01` | ✅ covered |
| "Why this result?" provenance | `dic-search-02` | ✅ covered |
| CoD-verified generation + abstention | `dic-verify-01`, `dic-generate-01` | ✅ covered |
| AI-content labeling + HITL promotion | `dic-collab-01`, `dic-generate-01` | ✅ covered |
| Freshness heatmap | `dic-freshness-01`, `dic-ui-02` | ✅ covered |
| Air-gap local fine-tuning | `dic-finetune-01` | ✅ covered |
| Ecosystem synergy | `dic-synergy-01` | ✅ covered |
| **Knowledge-handoff / retirement capture** | only a *template preset* in `dic-templates-01` | ⚠️ **partial → new task** |
| **KG "buried bodies" explorer** | only passing mention in search/synergy; no builder | ❌ **uncovered → new task** |

## Net-new tasks seeded from this discovery (`tools/kanban/seed_dic_kanban.py`)

- **`dic-explore-01`** (build, high) — `explorer.py`: KG "buried bodies" explorer
  (orphans, single-owner/tribal knowledge, undocumented dependencies, contradictions/
  superseded docs) + new `/document-intelligence/explorer` page. Graph analytics only,
  RLS + access-control filtered. Parent `dic-search-01`. **Ripple:** adds an 11th page —
  `dic-blueprint-01` (route), `dic-ui-03` (nav), `dic-vv-01` (E2E count 10→11) must account.
- **`dic-handoff-01`** (build, high) — `handoff.py`: knowledge-handoff / retirement capture
  workflow (SME interview agenda auto-built from `dic-explore-01` single-owner findings →
  CoD-verified, AI-labeled PENDING collection via `doc_generator` → owner reassignment +
  orphan flagging). Parent `dic-generate-01`; secondary `dic-explore-01`, `dic-templates-01`,
  `dic-collab-01`.

## Decisions deferred to existing epics (mapped, no new task)

- **Records Management / retention & disposition** (surfaced by Research: Capterra "Records
  Management Software" + NARA regulatory body). The current schema only carries
  `freshness_state`; formal retention tiers, legal hold, and defensible disposition are
  **not yet built**. Recommendation: implement inside `dic-foundation-01` (add
  `dic_retention_schedules` / `dic_legal_holds` tables + constants) and surface via
  `dic-freshness-01` / `dic-acoic-02`, rather than a new epic. Tracked as a coherence
  caveat below.

## Phase-0 acceptance checklist (the rest of the epics implement against this)

Baseline ECM must-haves (items 1–18 above) + the 10 wow-factors:

- [ ] **Ingestion** multimodal (PDF/DOCX/XLSX/PPTX/OCR/HTML/SharePoint) → `dic-ingest-01/02/03`
- [ ] **Grounded search** NO-LLM BM25+KG, mandatory citations, empty-on-no-match → `dic-search-01`
- [ ] **"Why this result?"** provenance (matched terms + KG path) → `dic-search-02`
- [ ] **Access control** RBAC+ABAC+RLS, classification-aware, denies audited → `dic-authz-01`
- [ ] **Versioning + locks + review/approval**, AI-content labeling, append-only → `dic-collab-01`
- [ ] **CoD-verified generation** with abstention, PENDING+labeled, HITL promote → `dic-verify-01`, `dic-generate-01`
- [ ] **Freshness** scoring + heatmap + autonomous staleness reflex → `dic-freshness-01`, `dic-ui-02`
- [ ] **ACOIC** drift→impact→regen queue + RICOAS/NIST SSP fragments → `dic-acoic-01/02`
- [ ] **Templates & snippets** (ACOIC, SOP, Contract, Runbook, Policy, Onboarding) → `dic-templates-01`
- [ ] **Air-gap fine-tuning** (Unsloth/Ollama, no egress, never in grounded path) → `dic-finetune-01`
- [ ] **KG "buried bodies" explorer** (orphans/tribal/contradictions) → **`dic-explore-01` (new)**
- [ ] **Knowledge-handoff / retirement capture** workflow → **`dic-handoff-01` (new)**
- [ ] **IQE** NLQ over DIC collections → `dic-iqe-01`
- [ ] **UI** all pages (now 11 incl. /explorer), CUI banner, dark mode, IQE widget → `dic-ui-01/02/03`
- [ ] **Wiring** manifest/requirements/conftest/APPEND_ONLY/sandbox/sync/coherence → `dic-wiring-01`
- [ ] **Synergy** event bus + awareness + MCP + marketplace → `dic-synergy-01`
- [ ] **V&V** pytest+behave+Playwright (11 pages)+health → `dic-vv-01`
- [ ] *(caveat)* **Records retention/disposition + legal hold** — fold into `dic-foundation-01` + `dic-freshness-01`

## Notes
- Coverage is enforced by the dic-* epics and the V&V gate (`dic-vv-01`).
- This file is the Phase-0 contract: 27 `dic-*` tasks now defined (25 original + 2 net-new).
- New vertical `enterprise_content_services` is registered for future Research re-runs;
  re-running the engines off-peak (no `kanban_tasks` lock contention, longer scan budget)
  will reach the SYNTHESIZE/DOSSIER stages and may surface further refinements to append here.
