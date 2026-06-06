# CUI // SP-CTI
"""Seed Kanban tasks for the Document Intelligence Canvas (DIC).

Project: dic  (task_prefix 'dic-')  — Enterprise RAG+KG Document Management.
Plan: C:/Users/schuo/.claude/plans/icdev-has-rag-graphrag-deep-mccarthy.md

Task IDs follow the <prefix><epic>-<N> convention (dic-<epic>-NN) so
tools/project/kanban_project_sync.py auto-populates args/projects.yaml.

depends_on_task_id is single-parent (engine limitation). Where a task has
secondary cross-epic prerequisites, they are named in the description text.

Run:
    python tools/kanban/seed_dic_kanban.py
    python tools/kanban/seed_dic_kanban.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.kanban.task_factory import create_tasks  # noqa: E402

PROJECT_ID = "dic"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


TASKS = [
    # =========================================================================
    # EPIC disco — Discovery (run engines → curated feature catalog)
    # =========================================================================
    {
        "id": "dic-disco-01",
        "title": "Curate DMS feature catalog from Innovation/Creative/Research engine outputs",
        "description": (
            "Phase 0 discovery. Run (or read the captured output of) the three engines scoped to "
            "enterprise document/knowledge management and distill a curated feature list into "
            "docs/features/dic-discovery.md: "
            "(1) Creative Engine: `python tools/creative/creative_engine.py --run --domain \"enterprise "
            "document management\" --json`; "
            "(2) Research Engine: `python tools/research/research_engine.py --run --vertical <slug> "
            "--name \"Enterprise Content Services\" --focus-areas \"document lifecycle,knowledge retention,"
            "compliance docs\" --json`; "
            "(3) Innovation Engine: `python tools/innovation/innovation_manager.py --run --json`. "
            "Map each surfaced pain-point/feature to either an existing dic-* epic or a NEW dic-* task "
            "(append via this seeder). Confirm the baseline wow-factors are covered: drift-driven "
            "auto-regeneration (ACOIC), 'Why this result?' provenance, knowledge-handoff/retirement "
            "capture, CoD-verified generation with abstention, freshness heatmap, KG 'buried bodies' "
            "explorer. Output is a checklist the rest of the epics implement against."
        ),
        "task_type": "research",
        "priority": "high",
        "depends_on_task_id": None,
    },

    # =========================================================================
    # EPIC foundation — DB (PG-first) + constants + config + wiring toggles
    # =========================================================================
    {
        "id": "dic-foundation-01",
        "title": "DIC DB schema (PG-first dual schema, RLS columns) + constants.py",
        "description": (
            "Create tools/document_intelligence/ package with db/init_db.py modeled EXACTLY on "
            "tools/ai_augmentation/db/init_db.py: _DIC_BACKEND defaults to 'postgresql'; "
            "get_connection() for DOCUMENT-BEARING tables returns the RLS-aware "
            "tools.db.storage.get_connection() (NOT get_canvas_connection) because DIC docs carry "
            "tenant_id/classification; provide a separate get_catalog_connection() -> "
            "get_canvas_connection('DIC_PG_DATABASE') for config-only tables. Dual SCHEMA_PG/SCHEMA_SQLITE "
            "(SQLite via .replace() transforms). Tables (all dic_*): dic_collections(owner_id, share_list, "
            "tenant_id, classification), dic_documents(doc_id, collection_id, title, doc_type CHECK, "
            "source_uri, owner_id, tenant_id, classification, current_version, freshness_state CHECK, "
            "created_at), dic_versions(APPEND-ONLY: version_num, content_hash, author, origin CHECK "
            "(human_authored|ai_generated|ai_assisted), status CHECK (draft|pending|approved|rejected), "
            "approved_by, approved_at, changes_summary, created_at), dic_chunk_links(chunk_id ref to "
            "rag_chunks + page/section for citations), dic_reviews, dic_locks, dic_templates, dic_snippets, "
            "dic_drift_events, dic_doc_impacts, dic_regen_queue, dic_ssp_fragments, dic_ft_datasets, "
            "dic_ft_jobs, dic_ft_models. ALL CHECK constraints derived from constants.py enums (never "
            "hardcoded). constants.py: DIC_FEATURE_FLAG='ICDEV_DIC_ENABLED', DOC_TYPES, INGEST_FORMATS, "
            "SEARCH_MODES, FRESHNESS_STATES, REVIEW_STATES, VERSION_ORIGINS, VERSION_STATES, TEMPLATE_KINDS, "
            "DIC_ROLES, and CHECK_* derived strings. init_db() idempotent, graceful if tables exist."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "dic-disco-01",
    },
    {
        "id": "dic-foundation-02",
        "title": "DIC config + .env toggle + canvas registry + app.py/CLI registration",
        "description": (
            "Wire the canvas on by default. (1) args/document_intelligence_config.yaml: search weights "
            "(bm25_weight, kg_weight, rrf_k, vector_enabled:false), freshness thresholds (stale_days, "
            "critical_days), ingest formats, finetune.airgap_only:true. (2) .env: add ICDEV_DIC_ENABLED=true "
            "and DIC_PG_DATABASE. (3) tools/dashboard/app.py: add to _CANVAS_DEFS (line ~141) "
            "('dic','ICDEV_DIC_ENABLED','tools.document_intelligence.blueprint','create_dic_blueprint') and "
            "add 'dic' to _CANVAS_DEFAULTS_TRUE (line ~164). (4) tools/canvas/canvas_registry.py: add 'dic' "
            "to CANVAS_FLOW_NOUNS ('document') and CANVAS_DISPLAY_NAMES ('Document Intelligence Canvas'). "
            "(5) tools/cli/enable.py: add 'document-intelligence':['ICDEV_DIC_ENABLED'] to TOGGLES + a "
            "DESCRIPTIONS entry. Requires dic-foundation-01."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "dic-foundation-01",
    },

    # =========================================================================
    # EPIC authz — RBAC + ABAC + RLS
    # =========================================================================
    {
        "id": "dic-authz-01",
        "title": "access_control.py — RBAC + ABAC + RLS composition + roles + decorators",
        "description": (
            "Create tools/document_intelligence/access_control.py composing the EXISTING stack (no new "
            "auth engine). authorize(subject_ctx, resource_attrs, action) -> {allow:bool, reason:str}. "
            "Order: RLS (rows already scoped by get_connection + inject_row_predicate on tenant_id/"
            "classification) -> ABAC (tools/security/classification_enforcer.can_read no-read-up, "
            "can_write no-write-down, can_access_compartment; need-to-know via collection owner_id/"
            "share_list) -> RBAC (tools/saas/auth/rbac.check_permission with a new 'documents' category "
            "mapping DIC_ROLES {dic_admin,dic_author,dic_reviewer,dic_approver,dic_reader} x actions "
            "{ingest,search,view,edit,regenerate,approve,finetune,share,delete} -> R/W/OWN/-). Provide a "
            "@require_dic(action) Flask decorator that reads g.current_user/g.security_context (populated by "
            "tools/dashboard/auth.register_dashboard_auth). Log every deny via tools/dashboard/auth."
            "log_auth_event. Add DIC page entries to RBAC_MATRIX (tools/dashboard/auth.py ~324). "
            "Requires dic-foundation-02."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "dic-foundation-02",
    },

    # =========================================================================
    # EPIC ingest — multimodal providers + orchestrator (reuse RAG)
    # =========================================================================
    {
        "id": "dic-ingest-01",
        "title": "Multimodal providers — DOCX/XLSX/PPTX (provider ABC like pdf_provider)",
        "description": (
            "Create tools/document_intelligence/providers/ with a DocProvider ABC mirroring "
            "icdev/tools/rag/pdf_provider.py (check_availability() + extract(path) -> structured "
            "{pages:[{text, page, section}], metadata}). Implement docx_provider.py (python-docx), "
            "xlsx_provider.py (openpyxl), pptx_provider.py (python-pptx). Guard imports so a missing lib "
            "degrades gracefully (mark format unavailable, like pdf_provider's backend chain). Preserve "
            "page/sheet/slide + section anchors so citations can reference them. Add deps to requirements.txt "
            "in the wiring epic. Requires dic-foundation-02."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "dic-foundation-02",
    },
    {
        "id": "dic-ingest-02",
        "title": "Multimodal providers — Images+OCR and HTML/Confluence/SharePoint",
        "description": (
            "Add ocr_provider.py (pytesseract for scanned images/diagrams, with the existing vision LLM "
            "path — LLaVA/Gemma3 via tools/llm/router — as fallback) and html_provider.py (html2text for "
            "HTML/Confluence exports; reuse tools/sharepoint/ for live SharePoint pulls). Same DocProvider "
            "ABC contract + graceful degradation. OCR carries page/region anchors for citations. "
            "Requires dic-ingest-01."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "dic-ingest-01",
    },
    {
        "id": "dic-ingest-03",
        "title": "ingest_orchestrator.py — route file → provider → RAG ingest + KG bridge + dic rows",
        "description": (
            "Create tools/document_intelligence/ingest_orchestrator.py: given a file + collection, pick the "
            "right provider by extension, extract, then REUSE icdev/tools/rag/ingestion_manager.ingest_source "
            "+ chunker.chunk_content to chunk/embed/upsert into the vector store, and "
            "icdev/tools/rag/rag_to_kg_ingester to populate the KG. Write dic_documents + an initial "
            "dic_versions(origin='human_authored', status='approved') row, and dic_chunk_links mapping "
            "rag_chunks back to the document + page/section. Stamp tenant_id/classification from the caller's "
            "security context on every row. Headless CLI in __main__.py (--ingest <path> --collection <id> "
            "--json). Requires dic-ingest-02; secondary: dic-authz-01 (writes under access control)."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "dic-ingest-02",
    },

    # =========================================================================
    # EPIC search — grounded NO-LLM retrieval + mandatory citations
    # =========================================================================
    {
        "id": "dic-search-01",
        "title": "search_engine.py — NO-LLM grounded retrieval (BM25 + KG) + citation.py + access filter",
        "description": (
            "Create search_engine.py with default mode='grounded': fuse (a) BM25 keyword scoring over "
            "dic_documents/rag_chunks (reuse the BM25 scorer inside icdev/tools/rag/retriever.py) and "
            "(b) KG traversal via icdev/tools/knowledge_graph/graph_rag.retrieve_graph_context(profile="
            "'provenance'). NO embedding call, NO generative LLM. Deterministic RRF fusion. Run under the "
            "caller's g.security_context so RLS scopes rows; pass every hit through "
            "access_control.authorize (ABAC) — a doc the caller can't read is never returned or cited. "
            "Create citation.py (Citation dataclass + build()): every hit MUST carry "
            "{document_id, version, page/section, chunk_id, source_uri, classification}; drop hits that "
            "cannot be cited. Response: {results:[{text,score,citation}], mode, channels_used}. A query with "
            "no corpus match returns empty (never invents). Requires dic-ingest-03; secondary dic-authz-01."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "dic-ingest-03",
    },
    {
        "id": "dic-search-02",
        "title": "Optional hybrid vector channel + 'Why this result?' provenance",
        "description": (
            "Add mode='hybrid': only when args/document_intelligence_config.yaml search.vector_enabled=true "
            "AND a LOCAL embedding provider is available (e.g. air-gap nomic-embed-text via tools/llm/router "
            "get_embedding_provider), add the vector channel to the RRF fusion. Stays opt-in so the canvas "
            "works with ZERO models. Add a 'Why this result?' payload exposing matched BM25 terms + the KG "
            "path that produced the hit (provenance, not LLM rationalization). Requires dic-search-01."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "dic-search-01",
    },

    # =========================================================================
    # EPIC freshness — staleness engine + genesis reflex
    # =========================================================================
    {
        "id": "dic-freshness-01",
        "title": "freshness_engine.py + dic_freshness genesis reflex (autonomous staleness sweep)",
        "description": (
            "Create freshness_engine.py adapting icdev/tools/data_canvas/freshness_guardian.py to score "
            "dic_documents by age vs config thresholds (stale_days, critical_days) → freshness_state "
            "(fresh|stale|critical). Create tools/genesis/reflexes/dic_freshness.py following "
            "tools/genesis/reflexes/ndc_topology_drift.py (CADENCE_HOURS, run(ctx, conn) -> "
            "{stale, events_published, status}); flip states and publish 'dic.document.stale' via "
            "tools/canvas/event_bus.publish. Expose a freshness heatmap data endpoint for the UI. "
            "Requires dic-ingest-03."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "dic-ingest-03",
    },

    # =========================================================================
    # EPIC collab — versioning + locks + HITL approval + AI-content labeling
    # =========================================================================
    {
        "id": "dic-collab-01",
        "title": "collaboration.py — versioning, locks, HITL review/approval (promotes AI drafts), AI labeling",
        "description": (
            "Create collaboration.py: append-only versioning on dic_versions; optimistic lock via dic_locks "
            "(version_num check) with conflict detection; review/approval workflow over dic_reviews using "
            "REVIEW_STATES, gated by access_control (@require_dic('approve')). A version is the document's "
            "current_version ONLY when status='approved'. AI-content labeling: every version carries "
            "origin (human_authored|ai_generated|ai_assisted); approvals record approved_by+approved_at "
            "append-only. Provide helpers the UI/exporter use to render the 'AI-generated — pending/approved "
            "by <user>' badge. Requires dic-authz-01."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "dic-authz-01",
    },

    # =========================================================================
    # EPIC verify — CoT/CoD verifier + citation gate + abstention
    # =========================================================================
    {
        "id": "dic-verify-01",
        "title": "verifier.py — CoT/CoD verification + citation validation + abstention",
        "description": (
            "Create verifier.py wrapping the existing Chain-of-Thought/Chain-of-Debate orchestration "
            "(tools/manifest/llm-chain-orchestration.md; .claude/commands/cod.md) and "
            "icdev/tools/rag/retriever.validate_citations + icdev/tools/rag/corrective_rag. "
            "verify(draft_text, evidence_chunks) -> {verified_text, claims:[{claim, supported, source_n}], "
            "abstained:bool}. Each [SOURCE-N] claim is replayed against its cited chunk in a debate; "
            "unsupported claims are stripped or the draft is rejected; insufficient evidence -> abstain. "
            "This is the ONLY anti-hallucination gate for generated content. Requires dic-search-01."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "dic-search-01",
    },

    # =========================================================================
    # EPIC generate — doc generation → verify → AI-labeled pending (HITL)
    # =========================================================================
    {
        "id": "dic-generate-01",
        "title": "doc_generator.py — generate from sources → CoD verify → AI-labeled PENDING version",
        "description": (
            "Create doc_generator.py: retrieve grounded evidence (search_engine) → draft via "
            "tools/llm/router.LLMRouter with [SOURCE-N] citation instructions → run verifier.verify → "
            "write the result to dic_versions as origin='ai_generated', status='pending' (NEVER auto-publish; "
            "never becomes current_version until an approver promotes it via collaboration.py). Stamp the "
            "visible 'AI-generated — pending approval' marker into the stored content + export metadata "
            "(PDF/DOCX). Requires dic-verify-01; secondary: dic-collab-01 (promotion path)."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "dic-verify-01",
    },

    # =========================================================================
    # EPIC finetune — air-gap local fine-tuning / training (option)
    # =========================================================================
    {
        "id": "dic-finetune-01",
        "title": "finetune_bridge.py — air-gap local fine-tuning/training over a collection",
        "description": (
            "Create finetune_bridge.py: an OPTION (canvas works without it). Air-gap by construction — "
            "tools/finetune/provider_factory.get_provider constrained to Unsloth (local GPU QLoRA) or "
            "CPU-only Ollama; OpenAI/Bedrock/Azure providers excluded when tools/airgap.is_airgap() or "
            "config finetune.airgap_only=true (default). Build pairs from the collection's own chunks/KG via "
            "tools/finetune/pair_generator + kg_pair_generator + rag_ft_pipeline; version with dataset_manager; "
            "carry CUI/classification on every example; pairs never leave the enclave. Train -> evaluate "
            "(tools/finetune/evaluator.py pure-Python BLEU/ROUGE/perplexity) -> gguf_exporter (Q4_K_M) -> "
            "register in model_registry -> serve via local Ollama. Write dic_ft_datasets/dic_ft_jobs/"
            "dic_ft_models (airgap=true). The fine-tuned model may back optional hybrid generation paths — "
            "NEVER the no-LLM grounded search path. GPU detect via tools/finetune/gpu_detector; degrade to "
            "CPU/Ollama or 'training unavailable' gracefully. Requires dic-ingest-03; secondary dic-authz-01."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "dic-ingest-03",
    },

    # =========================================================================
    # EPIC templates — use-case templates + snippets galleries
    # =========================================================================
    {
        "id": "dic-templates-01",
        "title": "templates_catalog.py + snippets + seed JSON (ACOIC, SOP, contract, runbook, policy, onboarding)",
        "description": (
            "Create templates_catalog.py (data-driven from context/document_intelligence/templates/*.json) "
            "exposing built-in use-case templates and reusable snippets, mirroring the gallery pattern in "
            "tools/dashboard/templates/data_canvas/templates.html and agentic_ai_canvas/snippets.html. Seed "
            "templates: ACOIC (flagship — wires drift→regen), SOP Refresh, Contract Lifecycle, Runbook "
            "Validation, Policy Library, Onboarding/Knowledge-Handoff (retirement capture). Each template "
            "preconfigures a collection + ingestion sources + freshness thresholds + generation prompts. "
            "Catalog/snippet tables use get_catalog_connection (canvas conn). Requires dic-generate-01."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "dic-generate-01",
    },

    # =========================================================================
    # EPIC acoic — flagship drift→doc compliance bridge
    # =========================================================================
    {
        "id": "dic-acoic-01",
        "title": "acoic.py — drift event subscription + impact scoring + regen queue",
        "description": (
            "Create acoic.py: subscribe('dic','ndc.topology.drift_detected', handler) + IDC drift via "
            "tools/canvas/event_bus; persist to dic_drift_events. Impact scoring: join drifted entities "
            "(KG) to documents referencing them -> dic_doc_impacts ranked. For high-impact docs, enqueue "
            "regeneration through doc_generator (which CoD-verifies + labels AI) into dic_regen_queue with "
            "REVIEW_STATES for HITL. Requires dic-generate-01."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "dic-generate-01",
    },
    {
        "id": "dic-acoic-02",
        "title": "ACOIC RICOAS NIST bridge + SSP fragment generation (CoD-verified, HITL)",
        "description": (
            "Extend acoic.py: map changed controls via icdev/tools/knowledge_graph/compliance_graph + the "
            "RICOAS/NIST 800-53 crosswalk engine; draft SSP fragments (dic_ssp_fragments) through "
            "doc_generator+verifier (cited, CoD-verified, AI-labeled, HITL-gated). Surface the regen queue + "
            "compliance re-map on the /document-intelligence/acoic page. Requires dic-acoic-01."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "dic-acoic-01",
    },

    # =========================================================================
    # EPIC blueprint — Flask blueprint + routes + API
    # =========================================================================
    {
        "id": "dic-blueprint-01",
        "title": "blueprint.py — create_dic_blueprint() factory: all page routes + JSON API + IQE route",
        "description": (
            "Create tools/document_intelligence/blueprint.py with create_dic_blueprint() factory (returns "
            "None if ICDEV_DIC_ENABLED false), url_prefix='/document-intelligence', before_request init_db. "
            "Page routes: / (index), /collections/<id>, /search, /freshness, /collaboration, /generate, "
            "/templates, /snippets, /acoic, /finetune. JSON API for ingest, search (grounded), generate, "
            "review/approve, freshness, acoic queue, finetune jobs — each guarded by @require_dic(action). "
            "POST /document-intelligence/api/iqe-query mirroring the ai_augmentation blueprint pattern. "
            "Requires dic-foundation-02; uses modules from authz/search/generate/etc. (import-safe even if "
            "those land later — guard with try/except like other canvases)."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "dic-foundation-02",
    },

    # =========================================================================
    # EPIC ui — 10 dashboard pages (8-component gate each) + icdev/ mirror + nav
    # =========================================================================
    {
        "id": "dic-ui-01",
        "title": "UI — index + collections + search pages (dark mode, CUI banner, IQE widget)",
        "description": (
            "Create tools/dashboard/templates/document_intelligence/{page,collection,search}.html extending "
            "base.html, CUI//SP-CTI banner, dark-mode styling consistent with AIMC/AADC. Search page shows "
            "results with mandatory citation chips (doc/version/page) + 'Why this result?' expander; never "
            "renders an uncited answer. Include {% include 'includes/iqe_query_widget.html' %}. Wire route "
            "render context (iqe_canvas='dic', iqe_api_route). Requires dic-blueprint-01; secondary "
            "dic-search-01."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "dic-blueprint-01",
    },
    {
        "id": "dic-ui-02",
        "title": "UI — freshness + collaboration + generate pages (AI-generated badges)",
        "description": (
            "Create freshness.html (staleness heatmap), collaboration.html (version history, review/approval "
            "queue, lock state), generate.html (source picker → generated draft preview). Generated/AI "
            "content MUST render a visible 'AI-generated — pending/approved by <user>' badge/watermark; "
            "pending drafts are clearly not the current approved version. Requires dic-ui-01."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "dic-ui-01",
    },
    {
        "id": "dic-ui-03",
        "title": "UI — templates + snippets + acoic + finetune pages + nav + icdev/ mirror",
        "description": (
            "Create templates.html, snippets.html (galleries), acoic.html (drift→impact→regen queue + NIST "
            "re-map), finetune.html (air-gap training jobs/models; cloud providers not selectable). Add nav "
            "links in base.html and the index page so all 10 pages are reachable. Mirror every template to "
            "icdev/tools/dashboard/templates/document_intelligence/ (or rely on companion sync). Requires "
            "dic-ui-02; secondary dic-templates-01, dic-acoic-02, dic-finetune-01."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "dic-ui-02",
    },

    # =========================================================================
    # EPIC iqe — IQE adapter + dispatch + path + seed queries
    # =========================================================================
    {
        "id": "dic-iqe-01",
        "title": "IQE — adapter dic.py + _CANVAS_MAP + PATH_CANVAS + seed queries",
        "description": (
            "Create tools/iqe/adapters/dic.py registering read-only collections returning REAL rows: "
            "dic.documents, dic.collections, dic.impacts, dic.regen_queue, dic.freshness, dic.ft_models "
            "(mirror tools/iqe/adapters/ai_augmentation.py). Add the 'dic' entry to _CANVAS_MAP in "
            "tools/dashboard/app.py (~line 2714). Add [/^\\/document-intelligence/, 'dic'] to PATH_CANVAS in "
            "base.html (~line 857). Add >=3 seed queries in context/iqe/queries/dic/*.iqe (e.g. stale docs, "
            "high-impact drifted docs, pending AI drafts). Requires dic-blueprint-01."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "dic-blueprint-01",
    },

    # =========================================================================
    # EPIC synergy — ecosystem integration
    # =========================================================================
    {
        "id": "dic-synergy-01",
        "title": "Ecosystem synergy — event bus pub/sub + awareness indexing + MCP tools + marketplace",
        "description": (
            "Cross-canvas integration: (1) publish dic.* events (document.stale, document.regenerated, "
            "ssp.fragment.drafted) and subscribe to NDC/IDC drift via tools/canvas/event_bus; (2) register "
            "DIC components with the Internal Awareness Engine (tools/awareness/component_indexer) so docs/"
            "collections appear in /components-map and /ask-icdev; (3) expose DIC tools via "
            "tools/mcp/tool_registry (e.g. dic_search grounded, dic_ingest) reusing the rag/knowledge MCP "
            "server pattern; (4) marketplace: allow publishing DIC templates as FORGE assets. Requires "
            "dic-ui-03; secondary dic-acoic-02, dic-iqe-01."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "dic-ui-03",
    },

    # =========================================================================
    # EPIC wiring — registration checklist + sync + coherence
    # =========================================================================
    {
        "id": "dic-wiring-01",
        "title": "Wiring — manifest shard + requirements + conftest schema + APPEND_ONLY + sync + coherence",
        "description": (
            "Complete the 8-point registration checklist: (1) tools/manifest/document-intelligence-canvas.md "
            "shard + index line in tools/manifest.md; (2) requirements.txt += python-docx, openpyxl, "
            "python-pptx, pytesseract, html2text; (3) tests/conftest.py MINIMAL_ICDEV_SCHEMA += all dic_* "
            "tables; (4) .claude/hooks/pre_tool_use.py APPEND_ONLY_TABLES += dic_versions, dic_reviews, "
            "dic_drift_events; (5) docs/security/sandbox-coverage.md decision for any user-content ingester "
            "(OPT-58); (6) docs/reference/commands.md += DIC CLI; (7) python tools/dx/companion.py --sync "
            "--write --json; (8) python tools/workflow/coherence_checker.py --all --fix --gate. "
            "Requires dic-ui-03; secondary dic-iqe-01."
        ),
        "task_type": "chore",
        "priority": "medium",
        "depends_on_task_id": "dic-ui-03",
    },

    # =========================================================================
    # EPIC vv — verification gate
    # =========================================================================
    {
        "id": "dic-vv-01",
        "title": "V&V — pytest + behave + Playwright E2E (10 pages) + health + feature docs",
        "description": (
            "Final validation gate. (1) tests: test_dic_search.py (no-LLM grounded path returns real data "
            "with mandatory citations; uncited hits suppressed; empty on no-match), test_dic_access_control.py "
            "(RBAC 403s, ABAC no-read-up + compartment, RLS tenant isolation, denies audited), "
            "test_dic_generation.py + test_dic_collab.py (CoD verify/abstain; AI draft is pending+labeled, "
            "promoted only by approver, append-only audit), test_dic_finetune.py (air-gap: no cloud provider "
            "selectable, no egress). (2) behave BDD. (3) Playwright E2E for all 10 pages (screenshots to "
            "playwright/screenshots/). (4) python tools/testing/health_check.py --json. (5) feature docs "
            "docs/features/phase-dic-*.md. All gates green. Requires dic-wiring-01; secondary dic-synergy-01."
        ),
        "task_type": "test",
        "priority": "critical",
        "depends_on_task_id": "dic-wiring-01",
    },

    # =========================================================================
    # EPIC explore — KG "buried bodies" explorer (NET-NEW from dic-disco-01)
    #   Phase-0 discovery (dic-disco-01) found wow-factor #7 (KG buried-bodies
    #   explorer) had no dedicated epic — only passing mentions in search/synergy.
    #   Market evidence: microsoft/graphrag, HKUDS/LightRAG, GitNexus (client-side
    #   code/knowledge graphs) surfaced by the Research Engine scan.
    # =========================================================================
    {
        "id": "dic-explore-01",
        "title": "explorer.py — KG 'buried bodies' explorer (orphans, single-owner, contradictions) + /explorer page",
        "description": (
            "NET-NEW from dic-disco-01. Build the flagship wow-factor #7: a knowledge-graph explorer that "
            "surfaces the institutional knowledge people forgot about. Create "
            "tools/document_intelligence/explorer.py reusing icdev/tools/knowledge_graph/graph_rag + the "
            "rag->kg graph already populated by dic-ingest-03; NO generative LLM (graph analytics only). "
            "Detectors over the DIC corpus + KG: (a) ORPHANS — documents/entities with no inbound references "
            "or no owner; (b) SINGLE-OWNER / tribal-knowledge — entities whose only documenting source has one "
            "owner_id (single point of knowledge failure; feeds dic-handoff-01); (c) UNDOCUMENTED DEPENDENCIES "
            "— KG edges between systems/controls with no covering document; (d) CONTRADICTIONS / STALE-LINKS — "
            "documents that supersede/contradict each other or cite retired entities (reuse the "
            "supports/contradicts/supersedes edge types). Persist findings to dic_kg_findings (finding_type "
            "CHECK from constants, severity, entity_ref, doc_ids, owner_ids, classification). Run under "
            "g.security_context (RLS) and pass every referenced doc through access_control.authorize so a user "
            "only sees buried knowledge they may read. Add an 11th page route /document-intelligence/explorer "
            "(graph view + ranked findings list) — coordinate the route into dic-blueprint-01, nav into "
            "dic-ui-03, and the E2E page count in dic-vv-01 (10 -> 11 pages). Primary parent dic-search-01 "
            "(KG traversal); secondary dic-ingest-03 (KG data), dic-blueprint-01 (route), dic-ui-03 (page/nav)."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "dic-search-01",
    },

    # =========================================================================
    # EPIC handoff — Knowledge-handoff / retirement capture (NET-NEW from dic-disco-01)
    #   Phase-0 discovery found wow-factor #6 existed only as a *template preset*
    #   (dic-templates-01), not an active capture workflow. Market evidence:
    #   thedotmack/claude-mem, mem0ai/mem0 ("universal memory layer") surfaced by
    #   the Research Engine scan — persistent institutional memory is a live need.
    # =========================================================================
    {
        "id": "dic-handoff-01",
        "title": "handoff.py — knowledge-handoff/retirement capture workflow (SME interview -> structured collection)",
        "description": (
            "NET-NEW from dic-disco-01. Promote wow-factor #6 from a static template to an active workflow that "
            "attacks institutional-knowledge loss when an SME departs/retires. Create "
            "tools/document_intelligence/handoff.py: (1) seed a handoff session from a departing owner_id — pull "
            "the single-owner/tribal-knowledge findings from dic-explore-01 (dic_kg_findings) to auto-build the "
            "interview agenda (what only this person documents); (2) capture answers as source material and run "
            "them through doc_generator (CoD-verified, cited, AI-labeled PENDING versions) into a destination "
            "collection; (3) reassign owner_id on the departing SME's documents and flag any that become orphaned. "
            "Persist dic_handoff_sessions + dic_handoff_items (status CHECK from constants, classification). "
            "Gated by access_control (@require_dic('edit')); every generated artifact is HITL-promoted via "
            "collaboration.py (never auto-approved). Surface on the collaboration page (or a /handoff tab). "
            "Primary parent dic-generate-01 (doc generation); secondary dic-explore-01 (single-owner findings), "
            "dic-templates-01 (Onboarding/Knowledge-Handoff template), dic-collab-01 (promotion)."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "dic-generate-01",
    },
]


def seed(dry_run: bool = False) -> int:
    now = _now()
    # These tasks are genuinely time-deferred: status 'scheduled' WITH a real
    # scheduled_at (now) — schedule immediately. Stamp status/scheduled_at +
    # the project's "CUI" classification on every task.
    for t in TASKS:
        t["status"] = "scheduled"
        t["scheduled_at"] = now
        t["project_id"] = PROJECT_ID
        t["classification"] = "CUI"

    report = create_tasks(
        PROJECT_ID, TASKS, dry_run=dry_run, strict=False, register_project=False
    )
    print(report.summary())
    return len(report.seeded)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Seed DIC kanban tasks")
    ap.add_argument("--dry-run", action="store_true", help="Print only, no DB write")
    args = ap.parse_args()
    seed(dry_run=args.dry_run)
