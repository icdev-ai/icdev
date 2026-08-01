# TSR DOC — document-intelligence test-file inventory (tsr-doc-01-d1)

Diagnostic only. Produced 2026-08-01 on branch `kanban/tsr-doc-01-d1`, a worktree off `origin/main`
at `60ad8b4c3`. No source file was modified.

Answers: which `tests/` files exercise the DOC epic — the document-intelligence, document-modernization,
writing/WriteGuard and content-quality `tools.*` packages — selected by what each file **imports**,
not by filename.

## Database seeding (acceptance criterion 2)

Run from the worktree root with the SQLite backend pinned. Several checkouts in this tree carry an
ambient `ICDEV_STORAGE_BACKEND=postgresql` with `ICDEV_PG_NO_FALLBACK=true`; an explicit env var beats
`load_dotenv`, so without the pin the seed targets Postgres and never lands in `data/icdev.db`, which
is the file `tests/conftest.py` forces every test onto.

```bash
export ICDEV_STORAGE_BACKEND=sqlite
unset ICDEV_PG_NO_FALLBACK
export PYTHONPATH=<worktree root>

python tools/db/init_icdev_db.py                                      # exit 0
python tools/studio/init_db.py                                        # exit 0
python tools/db/migrations/311_studio_event_tables_rls_columns/up.py  # exit 0
```

| command | exit | output on a fresh worktree |
|---------|------|----------------------------|
| `tools/db/init_icdev_db.py` | 0 | `Tables created (525)`; 8 wf_templates, 3 wf_document_templates seeded |
| `tools/studio/init_db.py` | 0 | 16 created (`studio_workflows`, `studio_trigger_events`, …) |
| `migrations/311_studio_event_tables_rls_columns/up.py` | 0 | `Migration 311 applied.` |

All three exit 0, leaving 541 tables in `data/icdev.db`. Ordering matters, and `migrate.py --up` cannot
substitute for step 3 — roughly 25 migrations are PG-only and fail on SQLite.

## Scope

The task card names six packages. Five exist and are in scope; the sixth does not exist under that
name:

| # | package | files matching | of which a real `import` |
|---|---------|----------------|--------------------------|
| 1 | `tools/document_intelligence` | 125 | 69 |
| 2 | `tools/quality` | 30 | 20 |
| 3 | `tools/doc_modernization` | 20 | 19 |
| 4 | `tools/pulse/writeguard` | 3 | 1 |
| 5 | `tools/writing` | 2 | 1 |
| 6 | `tools/dic` | 0 | 0 |

Two corrections to the card's package list, both verified against this checkout:

- **`tools/wg` does not exist.** "WG" is WriteGuard, and its code is split across `tools/writing/*`
  (already in scope — the analysis engines) and `tools/pulse/writeguard.py` (the
  `run_full_quality_check` entry point and the `writeguard_analyze` MCP adapter). The `wg_*` DB
  tables (`wg_analysis_results`, `wg_analysis_findings`, `wg_glossary`, `wg_style_guides`, …) are
  real, but no `tools.wg` Python package backs them. `tools.pulse.writeguard` is substituted for it
  here rather than all of `tools.pulse`, which is a separate subsystem.
- **`tools/dic` exists but no test imports it.** The package is a single module,
  `tools/dic/provenance_adapter.py`; the only `tools.dic` reference anywhere in the repo is a code
  sample in `tools/manifest/document-intelligence-canvas.md`. The Document Intelligence Canvas tests
  reach the canvas through `tools.document_intelligence.*` instead. `tools.dic` is kept in the regex
  so the zero is a measured result, not an omission.

## Exact patterns used

Discovery, run recursively from the repo root. The card's literal `tests\*.py` reaches only the 1,487
top-level files and silently misses the 526 under `tests/docmod`, `tests/document_intelligence`,
`tests/cortex`, `tests/govcon` and the other subdirectories. That omission is not cosmetic for this
epic: 34 of the 166 matching files live in those subdirectories, including 10 of the 54 tier-A
files and the whole of `tests/docmod` and `tests/document_intelligence`. The recursive form is what
was run:

```regex
(?<![\w.])(?:icdev\.)?tools\.(?:document_intelligence|doc_modernization|pulse\.writeguard|writing|quality|dic)(?![\w])
```

The lookbehind `(?<![\w.])` stops `icdev.tools.quality` counting twice and rejects
`foo_tools.writing`; the lookahead `(?![\w])` stops `tools.quality` matching a longer name. The
alternation is ordered longest-first so `document_intelligence` is tried before `dic`.

Second pass, used to tell a real import from a `mock.patch` target string or a docstring mention:

```regex
^\s*(?:from|import)\s+(?:icdev\.)?tools\.(document_intelligence|doc_modernization|pulse\.writeguard|writing|quality|dic)(?![\w])
```

## Result

`166` of the `2013` files under `tests/` match the discovery regex, carrying
`729` individual matches.

| tier | definition | files | run for DOC? |
|------|------------|-------|--------------|
| A | imports a DOC package and **no other** `tools.*` subsystem | 54 | **yes — this is the slice** |
| B | imports a DOC package **and** another `tools.*` subsystem | 45 | only when the failure is in DOC code |
| C | never imports one; matches only via `mock.patch` / string refs | 67 | no |

`tools/document_intelligence` dominates because the Document Intelligence Canvas, its chunking /
extraction / provenance stack and the DIC Tech Writer workspace all sit under it.

The tier-A slice collects cleanly against the seeded DB — **828 tests, zero collection errors, ~14s**:

```bash
python -m pytest $(cat docs/testing/tsr-doc-01-slice.txt) --collect-only -q --timeout=60
```

That is a collection check, not a run: it proves every file in the slice imports without an
ImportError and without the cold-DB hang the project brief warns about. Whether the 828 tests pass is
d2's question, not d1's.

### On the card's "5-15 test file paths"

The acceptance criterion estimated 5–15 files. The measured slice is 54 tier-A files
(166 matching in total) — low by roughly an order of magnitude, because DOC is one of the larger
epics. The tier split is what keeps it workable: tier A is the 54-file unit of work, and per the
project brief a slice that size runs in minutes rather than the >75 of a full sweep.

## Full listing

Tier A first, then B, then C. `packages` is every in-scope package the file references; `first match`
is the first matching line verbatim.

### Tier A — DOC-only imports (54)

| file | packages | matches | first match |
|------|----------|---------|-------------|
| `tests/docmod/test_architecture_patterns_pack.py` | `doc_modernization` | 6 | `from tools.doc_modernization.base_pack import ChunkRef  # noqa: E402` |
| `tests/docmod/test_change_control_pack.py` | `doc_modernization` | 3 | `from tools.doc_modernization.base_pack import CandidateEntity, ChunkRef  # noqa: E402` |
| `tests/docmod/test_evidence_currency_pack.py` | `doc_modernization` | 3 | `from tools.doc_modernization.base_pack import ChunkRef  # noqa: E402` |
| `tests/docmod/test_sop_workflows_pack.py` | `doc_modernization` | 6 | `from tools.doc_modernization.base_pack import ChunkRef  # noqa: E402` |
| `tests/docmod/test_temporal_validity.py` | `doc_modernization` | 7 | `from tools.doc_modernization import temporal` |
| `tests/document_intelligence/test_chunk_repair.py` | `document_intelligence` | 1 | `from tools.document_intelligence.chunk_repair import (` |
| `tests/document_intelligence/test_table_extract.py` | `document_intelligence` | 7 | `from tools.document_intelligence import table_extract as te` |
| `tests/genesis_auto/test_extractors.py` | `document_intelligence` | 18 | `"""Auto-generated tests for tools.document_intelligence.extractors.` |
| `tests/genesis_auto/test_ingest_orchestrator.py` | `document_intelligence` | 8 | `"""Auto-generated tests for tools.document_intelligence.ingest_orchestrator.` |
| `tests/quality/test_rigor_gates.py` | `quality` | 1 | `from tools.quality.rigor_gates import EvaluationResult, ILTierProfile, evaluate, load_p...` |
| `tests/test_categorical_scoring.py` | `quality` | 2 | `from tools.quality.categorical_scoring import (` |
| `tests/test_citation_grounding.py` | `quality` | 1 | `from tools.quality.citation_grounding import (` |
| `tests/test_claim_grounding.py` | `quality` | 2 | `from tools.quality.citation_grounding import (` |
| `tests/test_completion_auditor.py` | `quality` | 1 | `from tools.quality import completion_auditor as ca` |
| `tests/test_constitutional_ai.py` | `quality` | 5 | `from tools.quality.constitutional_ai import (` |
| `tests/test_crx_kg_blast_radius.py` | `document_intelligence` | 13 | `with _patch_attr("tools.document_intelligence.consistency_checker",` |
| `tests/test_derivation_disclosure.py` | `quality` | 1 | `from tools.quality.derivation import (` |
| `tests/test_dic_citation_publish_gate.py` | `document_intelligence`, `quality` | 2 | `from tools.document_intelligence.consistency_checker import (` |
| `tests/test_dic_claim_grounding_wiring.py` | `document_intelligence` | 2 | `from tools.document_intelligence.blueprint import _claim_grounding` |
| `tests/test_dic_collection_scoping.py` | `document_intelligence` | 1 | `from tools.document_intelligence import search_engine as se` |
| `tests/test_dic_cove_gate.py` | `document_intelligence`, `quality` | 10 | `from tools.document_intelligence import consistency_checker as cc` |
| `tests/test_dic_dead_surfaces_removed.py` | `document_intelligence` | 6 | `from tools.document_intelligence.blueprint import _PAGES` |
| `tests/test_dic_derivation_all_paths.py` | `document_intelligence` | 1 | `from tools.document_intelligence import blueprint as bp` |
| `tests/test_dic_derivation_wiring.py` | `document_intelligence`, `quality` | 5 | `from tools.document_intelligence import blueprint as bp` |
| `tests/test_dic_filters.py` | `document_intelligence` | 1 | `from tools.document_intelligence.filters import (` |
| `tests/test_dic_graphrag_chat.py` | `document_intelligence` | 1 | `from tools.document_intelligence import blueprint as bp` |
| `tests/test_dic_inbox.py` | `document_intelligence` | 24 | `from tools.document_intelligence.inbox import discover` |
| `tests/test_dic_layout_probe.py` | `document_intelligence` | 1 | `from tools.document_intelligence import extractors` |
| `tests/test_dic_nav_consistency.py` | `document_intelligence` | 1 | `from tools.document_intelligence.blueprint import _PAGE_GROUPS, _PAGES` |
| `tests/test_dic_publish_gate.py` | `document_intelligence` | 12 | `patch("tools.document_intelligence.blueprint._conn", _make_shim),` |
| `tests/test_dic_route_dedup.py` | `document_intelligence` | 2 | `from tools.document_intelligence import acoic` |
| `tests/test_dic_style_engine.py` | `document_intelligence` | 1 | `from tools.document_intelligence.style_engine import check_style, check_sections` |
| `tests/test_dic_techwriter.py` | `document_intelligence`, `writing` | 30 | `from tools.document_intelligence.constants import TEMPLATE_TYPES` |
| `tests/test_dic_techwriter_citations.py` | `document_intelligence`, `quality` | 3 | `from tools.document_intelligence import tech_writing_assist as twa` |
| `tests/test_divergence_critic.py` | `quality` | 7 | `from tools.quality.categorical_scoring import compose_divergence` |
| `tests/test_docdrift_rename.py` | `document_intelligence` | 4 | `from tools.document_intelligence import blueprint as bp` |
| `tests/test_docgen_bridge_scrub.py` | `document_intelligence` | 2 | `from tools.document_intelligence.doc_generator import (` |
| `tests/test_dsyn_canvas_adapter.py` | `document_intelligence` | 21 | `import tools.document_intelligence.canvas_adapter as mod` |
| `tests/test_dsyn_consistency.py` | `document_intelligence` | 27 | `from tools.document_intelligence.consistency_checker import extract_changed_concepts` |
| `tests/test_dsyn_patch_mode.py` | `document_intelligence` | 33 | `from tools.document_intelligence import doc_generator` |
| `tests/test_dsyn_suggest.py` | `document_intelligence` | 28 | `from tools.document_intelligence import blueprint` |
| `tests/test_dsyn_suggestion_api.py` | `document_intelligence` | 7 | `patch("tools.document_intelligence.blueprint._conn", _fake_conn),` |
| `tests/test_dsyn_suggestion_store.py` | `document_intelligence` | 20 | `import tools.document_intelligence.suggestion_store as mod` |
| `tests/test_markitdown_adapter.py` | `document_intelligence` | 11 | `ADAPTER_MODULE = "tools.document_intelligence.converters.markitdown_adapter"` |
| `tests/test_mcp_writeguard_tool.py` | `pulse.writeguard` | 4 | `(tools.pulse.writeguard.handle_writeguard_analyze)."""` |
| `tests/test_rted_conflict_detector.py` | `document_intelligence` | 1 | `from tools.document_intelligence.conflict_detector import (  # noqa: E402` |
| `tests/test_rted_history_api.py` | `document_intelligence` | 3 | `patch("tools.document_intelligence.blueprint._conn", _make_shim),` |
| `tests/test_rted_history_recorder.py` | `document_intelligence` | 6 | `with patch("tools.document_intelligence.history_recorder.get_connection", _fake_get_con...` |
| `tests/test_rted_history_ui.py` | `document_intelligence` | 3 | `patch("tools.document_intelligence.blueprint._conn", _make_shim),` |
| `tests/test_rted_lock_manager.py` | `document_intelligence` | 5 | `with patch("tools.document_intelligence.lock_manager.get_connection", _fake_get_connect...` |
| `tests/test_rted_presence_api.py` | `document_intelligence` | 6 | `import tools.document_intelligence.blueprint as _bp_mod` |
| `tests/test_rted_presence_registry.py` | `document_intelligence` | 2 | `with patch("tools.document_intelligence.presence_registry.get_connection", _fake):` |
| `tests/test_rted_vv_smoke.py` | `document_intelligence` | 9 | `patch("tools.document_intelligence.blueprint._conn", _fake_conn_fn),` |
| `tests/test_verifier_contract.py` | `document_intelligence` | 2 | `from tools.document_intelligence.verifier import (` |

### Tier B — DOC plus other subsystems (45)

| file | packages | matches | first match |
|------|----------|---------|-------------|
| `tests/cortex/test_analyst_citations.py` | `quality` | 2 | ```tools.quality.citation_grounding`` module. Fixture answers cover valid,` |
| `tests/cortex/test_governance_pipeline.py` | `quality` | 1 | `from tools.quality import citation_grounding` |
| `tests/cortex/test_search_adapters.py` | `document_intelligence` | 2 | `from tools.document_intelligence.search_engine import Citation, DICSearchResult` |
| `tests/docmod/test_claim_extractor.py` | `doc_modernization` | 2 | `from tools.doc_modernization import claim_extractor as ce` |
| `tests/docmod/test_claim_lifecycle.py` | `doc_modernization`, `document_intelligence` | 11 | `from tools.doc_modernization import claim_lifecycle as cl` |
| `tests/docmod/test_core_engine.py` | `doc_modernization` | 17 | `from tools.doc_modernization.base_pack import DomainPack  # noqa: F401` |
| `tests/docmod/test_cross_reference_tracker.py` | `document_intelligence` | 1 | `from tools.document_intelligence import cross_reference_tracker as xrt` |
| `tests/docmod/test_domain_packs.py` | `doc_modernization` | 14 | `from tools.doc_modernization.base_pack import ChunkRef` |
| `tests/docmod/test_feed_wiring.py` | `doc_modernization`, `document_intelligence` | 17 | `from tools.doc_modernization.base_pack import CandidateEntity, ChunkRef` |
| `tests/docmod/test_hitl_surfaces.py` | `doc_modernization`, `document_intelligence` | 13 | `from tools.doc_modernization import redline_drafter as rd` |
| `tests/docmod/test_import_from_docgen.py` | `document_intelligence` | 4 | `from tools.document_intelligence.blueprint import dic_bp` |
| `tests/docmod/test_link_check.py` | `doc_modernization` | 2 | `from tools.doc_modernization import link_check as lc` |
| `tests/docmod/test_modernization_ui.py` | `doc_modernization`, `document_intelligence` | 8 | `import tools.document_intelligence.blueprint as bp_mod  # registers all routes` |
| `tests/docmod/test_pack_generator.py` | `doc_modernization` | 2 | `from tools.doc_modernization.base_pack import ChunkRef  # noqa: E402` |
| `tests/docmod/test_pg_fixes_and_flow.py` | `doc_modernization`, `document_intelligence` | 16 | `import tools.document_intelligence.blueprint as bp_mod` |
| `tests/docmod/test_regen_quality_gate.py` | `doc_modernization`, `document_intelligence` | 9 | `from tools.doc_modernization.regen_quality_gate import (` |
| `tests/docmod/test_standards_catalog.py` | `doc_modernization` | 1 | `from tools.doc_modernization.blueprint import docmod_bp` |
| `tests/document_intelligence/test_freshness_notifier.py` | `document_intelligence` | 2 | `from tools.document_intelligence.freshness_engine import FreshnessResult` |
| `tests/http/test_egress_guard.py` | `doc_modernization` | 2 | `from tools.doc_modernization.link_check import egress_guard as legacy` |
| `tests/http/test_fetch_extract_retrofit.py` | `document_intelligence` | 4 | `from tools.document_intelligence import extractors` |
| `tests/test_acoic_hitl_audit.py` | `document_intelligence` | 9 | `import tools.document_intelligence.acoic as acoic_mod` |
| `tests/test_capture_strategy.py` | `quality` | 1 | `from tools.quality.content_grounding import find_placeholders` |
| `tests/test_clx_control_loop.py` | `quality` | 3 | `from tools.quality.sensor import ReviewLoopSensor, Violation, _looks_like_path` |
| `tests/test_content_grounding.py` | `quality` | 2 | `from tools.quality.citation_grounding import CONF_ABSTAIN` |
| `tests/test_context_budget.py` | `document_intelligence` | 1 | `from tools.document_intelligence.blueprint import (` |
| `tests/test_cove_architecture.py` | `quality` | 2 | `from tools.quality.cove_guard import cove_guard` |
| `tests/test_dic_acoic_ux.py` | `document_intelligence` | 9 | `import tools.document_intelligence.acoic as acoic_mod` |
| `tests/test_dic_analytics_kg_linkage.py` | `document_intelligence` | 1 | `from tools.document_intelligence import analytics_engine` |
| `tests/test_dic_analytics_provenance.py` | `document_intelligence` | 1 | `from tools.document_intelligence import analytics_engine` |
| `tests/test_dic_chat_cortex_adoption.py` | `document_intelligence` | 1 | `import tools.document_intelligence.blueprint as bp` |
| `tests/test_dic_chat_memory.py` | `document_intelligence` | 5 | ```tools.document_intelligence.chat_memory`` adds grounded, citable session` |
| `tests/test_dic_chat_memory_schema.py` | `document_intelligence` | 1 | `from tools.document_intelligence import chat_memory as cm` |
| `tests/test_dic_collection_listing_anomaly.py` | `document_intelligence` | 2 | `analytics = importlib.import_module("tools.document_intelligence.analytics_engine")` |
| `tests/test_dic_collection_registry.py` | `document_intelligence` | 1 | `from tools.document_intelligence.collection_registry import (` |
| `tests/test_dic_cross_canvas_context.py` | `document_intelligence` | 1 | `from tools.document_intelligence.cross_canvas_context import (` |
| `tests/test_dic_freshness_engine.py` | `document_intelligence` | 1 | `from tools.document_intelligence.freshness_engine import (` |
| `tests/test_dic_ingest_orchestrator.py` | `document_intelligence` | 12 | `from tools.document_intelligence import ingest_orchestrator as orch` |
| `tests/test_docmod_drift_bridge.py` | `doc_modernization`, `document_intelligence` | 14 | `import tools.doc_modernization.drift_bridge as db` |
| `tests/test_dsyn_vv_smoke.py` | `document_intelligence` | 8 | `import tools.document_intelligence.suggestion_store as ss` |
| `tests/test_idr_multi_source.py` | `document_intelligence` | 15 | `from tools.document_intelligence.doc_generator import generate_document` |
| `tests/test_network_doc_lifecycle.py` | `document_intelligence` | 10 | `from tools.document_intelligence import acoic` |
| `tests/test_penta_aiify_trust.py` | `quality` | 3 | `from tools.quality.html_sanitizer import sanitize_html` |
| `tests/test_placeholder_promote_gate.py` | `quality` | 1 | `import tools.quality.content_grounding as cg` |
| `tests/test_publish_gates.py` | `quality` | 1 | `from tools.quality.citation_grounding import PUBLISH_GATES, publish_gate_check_sql` |
| `tests/test_wiki_integrations.py` | `document_intelligence` | 8 | `from tools.document_intelligence.search_engine import _file_qa_to_wiki, _qa_slug` |

### Tier C — patch-target / string reference only (67)

| file | packages | matches | first match |
|------|----------|---------|-------------|
| `tests/browser/test_scope.py` | `doc_modernization` | 1 | `link_check = importlib.import_module("tools.doc_modernization.link_check")` |
| `tests/docmod/test_bridge_persist.py` | `document_intelligence` | 1 | `doc_gen = importlib.import_module("tools.document_intelligence.doc_generator")` |
| `tests/docmod/test_reverse_bridge.py` | `document_intelligence` | 2 | `doc_gen = importlib.import_module("tools.document_intelligence.doc_generator")` |
| `tests/govcon/test_past_performance_suggester.py` | `quality` | 1 | `cg = importlib.import_module("tools.quality.citation_grounding")` |
| `tests/test_cnr_docgen.py` | `pulse.writeguard` | 2 | `with patch.dict(sys.modules, {"tools.pulse.writeguard": None}):` |
| `tests/test_cnr_mission_canvas.py` | `quality` | 1 | `is grounded via tools.quality.citation_grounding.` |
| `tests/test_component_registry.py` | `document_intelligence` | 1 | `("dic", "ICDEV_DIC_ENABLED", "tools.document_intelligence.blueprint", "dic_bp"),` |
| `tests/test_dic_aiassist_retry.py` | `document_intelligence` | 1 | `dg = importlib.import_module("tools.document_intelligence.doc_generator")` |
| `tests/test_dic_anomaly_severity.py` | `document_intelligence` | 1 | `analytics = importlib.import_module("tools.document_intelligence.analytics_engine")` |
| `tests/test_dic_batch_ingest_anomaly.py` | `document_intelligence` | 1 | `orch = importlib.import_module("tools.document_intelligence.ingest_orchestrator")` |
| `tests/test_dic_bulk_edit_anomaly_detection.py` | `document_intelligence` | 1 | `analytics = importlib.import_module("tools.document_intelligence.analytics_engine")` |
| `tests/test_dic_confabulation.py` | `document_intelligence` | 1 | `dg = importlib.import_module("tools.document_intelligence.doc_generator")` |
| `tests/test_dic_consumer_file_anomaly.py` | `document_intelligence` | 1 | `orch = importlib.import_module("tools.document_intelligence.ingest_orchestrator")` |
| `tests/test_dic_consumer_pipeline_health_anomaly.py` | `document_intelligence` | 1 | `orch = importlib.import_module("tools.document_intelligence.ingest_orchestrator")` |
| `tests/test_dic_document_model_anomaly_detection.py` | `document_intelligence` | 1 | `analytics = importlib.import_module("tools.document_intelligence.analytics_engine")` |
| `tests/test_dic_document_routing_anomaly_detection.py` | `document_intelligence` | 1 | `analytics = importlib.import_module("tools.document_intelligence.analytics_engine")` |
| `tests/test_dic_extraction_quality.py` | `document_intelligence` | 1 | `extractors = importlib.import_module("tools.document_intelligence.extractors")` |
| `tests/test_dic_field_validation_anomaly_detection.py` | `document_intelligence` | 1 | `analytics = importlib.import_module("tools.document_intelligence.analytics_engine")` |
| `tests/test_dic_inbox_sweep_reflex.py` | `document_intelligence` | 2 | `_inbox = importlib.import_module("tools.document_intelligence.inbox")` |
| `tests/test_dic_ingest_ai_summary.py` | `document_intelligence` | 1 | `ingest = importlib.import_module("tools.document_intelligence.ingest_orchestrator")` |
| `tests/test_dic_ingest_anomaly_detection.py` | `document_intelligence` | 1 | `analytics = importlib.import_module("tools.document_intelligence.analytics_engine")` |
| `tests/test_dic_ingest_classify.py` | `document_intelligence` | 1 | `ingest = importlib.import_module("tools.document_intelligence.ingest_orchestrator")` |
| `tests/test_dic_ingest_consumer_outcome_anomaly.py` | `document_intelligence` | 1 | `orch = importlib.import_module("tools.document_intelligence.ingest_orchestrator")` |
| `tests/test_dic_ingest_correspondence.py` | `document_intelligence` | 1 | `ingest = importlib.import_module("tools.document_intelligence.ingest_orchestrator")` |
| `tests/test_dic_ingest_duplex_anomaly.py` | `document_intelligence` | 1 | `ingest = importlib.import_module("tools.document_intelligence.ingest_orchestrator")` |
| `tests/test_dic_ingest_email_anomaly.py` | `document_intelligence` | 1 | `ingest = importlib.import_module("tools.document_intelligence.ingest_orchestrator")` |
| `tests/test_dic_ingest_identifiers.py` | `document_intelligence` | 1 | `ingest = importlib.import_module("tools.document_intelligence.ingest_orchestrator")` |
| `tests/test_dic_ingest_import_validation.py` | `document_intelligence` | 1 | `orch = importlib.import_module("tools.document_intelligence.ingest_orchestrator")` |
| `tests/test_dic_ingest_job_anomaly_detection.py` | `document_intelligence` | 1 | `analytics = importlib.import_module("tools.document_intelligence.analytics_engine")` |
| `tests/test_dic_ingest_label_match_criteria.py` | `document_intelligence` | 1 | `ingest = importlib.import_module("tools.document_intelligence.ingest_orchestrator")` |
| `tests/test_dic_ingest_metadata.py` | `document_intelligence` | 1 | `ingest = importlib.import_module("tools.document_intelligence.ingest_orchestrator")` |
| `tests/test_dic_ingest_near_duplicate.py` | `document_intelligence` | 1 | `ingest = importlib.import_module("tools.document_intelligence.ingest_orchestrator")` |
| `tests/test_dic_ingest_ocr_cleanup.py` | `document_intelligence` | 1 | `ingest = importlib.import_module("tools.document_intelligence.ingest_orchestrator")` |
| `tests/test_dic_ingest_routing_metadata.py` | `document_intelligence` | 1 | `ingest = importlib.import_module("tools.document_intelligence.ingest_orchestrator")` |
| `tests/test_dic_ingest_throughput_anomaly.py` | `document_intelligence` | 1 | `analytics = importlib.import_module("tools.document_intelligence.analytics_engine")` |
| `tests/test_dic_ingest_workflow_action_params.py` | `document_intelligence` | 1 | `ingest = importlib.import_module("tools.document_intelligence.ingest_orchestrator")` |
| `tests/test_dic_ingest_workflow_mutations.py` | `document_intelligence` | 1 | `ingest = importlib.import_module("tools.document_intelligence.ingest_orchestrator")` |
| `tests/test_dic_lifecycle_assignment_anomaly.py` | `document_intelligence` | 1 | `orch = importlib.import_module("tools.document_intelligence.ingest_orchestrator")` |
| `tests/test_dic_output_export_anomaly_detection.py` | `document_intelligence` | 1 | `analytics = importlib.import_module("tools.document_intelligence.analytics_engine")` |
| `tests/test_dic_re_enrich_metadata.py` | `document_intelligence` | 1 | `ingest = importlib.import_module("tools.document_intelligence.ingest_orchestrator")` |
| `tests/test_dic_reasoning_scrub.py` | `document_intelligence` | 1 | `dg = importlib.import_module("tools.document_intelligence.doc_generator")` |
| `tests/test_dic_search_access.py` | `document_intelligence` | 1 | `se = importlib.import_module("tools.document_intelligence.search_engine")` |
| `tests/test_dic_search_anomaly.py` | `document_intelligence` | 1 | `se = importlib.import_module("tools.document_intelligence.search_engine")` |
| `tests/test_dic_search_answer.py` | `document_intelligence` | 1 | `se = importlib.import_module("tools.document_intelligence.search_engine")` |
| `tests/test_dic_search_expand.py` | `document_intelligence` | 1 | `se = importlib.import_module("tools.document_intelligence.search_engine")` |
| `tests/test_dic_search_filter_coverage_anomaly.py` | `document_intelligence` | 1 | `se = importlib.import_module("tools.document_intelligence.search_engine")` |
| `tests/test_dic_search_filter_query.py` | `document_intelligence` | 1 | `se = importlib.import_module("tools.document_intelligence.search_engine")` |
| `tests/test_dic_search_intent.py` | `document_intelligence` | 1 | `se = importlib.import_module("tools.document_intelligence.search_engine")` |
| `tests/test_dic_search_keyword.py` | `document_intelligence` | 1 | `se = importlib.import_module("tools.document_intelligence.search_engine")` |
| `tests/test_dic_search_query_intent.py` | `document_intelligence` | 1 | `se = importlib.import_module("tools.document_intelligence.search_engine")` |
| `tests/test_dic_search_result_summary.py` | `document_intelligence` | 1 | `se = importlib.import_module("tools.document_intelligence.search_engine")` |
| `tests/test_dic_search_snippet.py` | `document_intelligence` | 1 | `se = importlib.import_module("tools.document_intelligence.search_engine")` |
| `tests/test_dic_template_chunking.py` | `document_intelligence` | 1 | `io = importlib.import_module("tools.document_intelligence.ingest_orchestrator")` |
| `tests/test_dic_text_parse_quality.py` | `document_intelligence` | 1 | `extractors = importlib.import_module("tools.document_intelligence.extractors")` |
| `tests/test_dic_view_anomaly_detection.py` | `document_intelligence` | 1 | `analytics = importlib.import_module("tools.document_intelligence.analytics_engine")` |
| `tests/test_doc_generator_confidence_bands.py` | `document_intelligence`, `quality` | 2 | `_doc_generator = importlib.import_module("tools.document_intelligence.doc_generator")` |
| `tests/test_docgen.py` | `document_intelligence`, `pulse.writeguard` | 22 | `# sys.modules[name]=None makes `from tools.pulse.writeguard import …` raise ImportError.` |
| `tests/test_dsyn_dic_integration_reflex.py` | `document_intelligence` | 2 | `patch("tools.document_intelligence.canvas_adapter.get_connection", _fake_gc),` |
| `tests/test_dvg_benchmark.py` | `quality` | 2 | `critic = importlib.import_module("tools.quality.divergence_critic")` |
| `tests/test_dvg_wire04_mcp.py` | `quality` | 1 | `patch("tools.quality.divergence_critic.score_idea_pool", return_value=fake_scored):` |
| `tests/test_dvg_wire_creative_branch.py` | `quality` | 1 | `critic_mod = importlib.import_module("tools.quality.divergence_critic")` |
| `tests/test_dvg_wire_solution_divergence.py` | `quality` | 1 | `critic_mod = importlib.import_module("tools.quality.divergence_critic")` |
| `tests/test_nav_intel_09_d5_judge_publish_integration.py` | `writing` | 4 | `The import is `from tools.writing.llm_judge import evaluate_and_store,` |
| `tests/test_release_orchestrator.py` | `quality` | 1 | `b"from icdev.tools.quality.citation_grounding import parse_citations\n",` |
| `tests/test_review_loop.py` | `quality` | 1 | `rl = importlib.import_module("tools.quality.review_loop")` |
| `tests/test_tech_writing_assist.py` | `document_intelligence` | 1 | `twa = importlib.import_module("tools.document_intelligence.tech_writing_assist")` |
| `tests/test_tw_standards_validation.py` | `document_intelligence` | 1 | `twa = importlib.import_module("tools.document_intelligence.tech_writing_assist")` |

## Machine-readable companions

- `docs/testing/tsr-doc-01-slice.txt` — the 54 tier-A paths, one per line, for `pytest @file`-style use.
- `docs/testing/tsr-doc-01-all-matches.txt` — all 166 matching paths, one per line.
- `docs/testing/tsr-doc-01-inventory.json` — per-file packages, tier, match counts and line numbers.

All 220 paths were verified to exist in this checkout.

Both `.txt` files are LF-terminated. The repo's root `.gitattributes` sets `* text=auto eol=lf` and
`*.txt text eol=lf`, so they are stored and checked out as LF on every platform — a bash
`while read -r f` over either file works directly. Beware only of reading them from a nested `.tmp/`
copy of the tree rather than from a real checkout: `.gitattributes` does not apply there, the files
can carry CRLF, and the trailing carriage return then makes every path look missing.
