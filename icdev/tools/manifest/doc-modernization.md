# Document Modernization Engine (docmod)

> Generic, domain-agnostic staleness intelligence for DIC documents. Pluggable
> domain packs (args/docmod/packs/*.yaml) detect EOL hardware/software,
> deprecated technology (TLS 1.1, telnet, MD5…), and superseded standards;
> verdicts are deterministic (TRUST — no LLM in evaluation); redlines/regen are
> HITL-gated. Schema: migration 257 — merged via PR #141 (docmod_findings and docmod_scan_runs and
> docmod_catalog_audit are APPEND-ONLY). Kanban project: docmod-.

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Docmod Public API | tools/doc_modernization/__init__.py | `scan_document(doc_id)`, `scan_collection(collection_id)`, `get_findings(doc_id, state, finding_type)` — latest-state reads over the append-only supersede chain | Python API | dict / list[dict] |
| Constants | tools/doc_modernization/constants.py | FINDING_TYPES, CURRENCY_VERDICTS, FINDING_STATES, CATALOG_STATUSES, KG_ENTITY_TYPES, APPEND_ONLY_TABLES, confidence bands | import | constants |
| Base Pack | tools/doc_modernization/base_pack.py | `DomainPack` ABC (extract/evaluate/recommend/evidence_snapshot) + CandidateEntity, Verdict (citation-shaped evidence), Replacement, ChunkRef dataclasses | subclass | dataclasses |
| Pack Loader | tools/doc_modernization/pack_loader.py | Loads args/docmod/packs/*.yaml (mtime hot-reload), resolves `evaluator:` dotted path, validates, publishes extraction regexes to text_network.EXTRA_ENTITY_PATTERNS | `load_packs()` | dict[str, DomainPack] |
| Scanner | tools/doc_modernization/scanner.py | Scan pipeline: latest approved dic_version → chunks (dic_chunk_links⋈rag_chunks, dic_sections fallback) → packs → docmod_findings w/ dedupe + supersede rows + incremental evidence-hash skip (docmod_doc_scan_state) | `scan_document(doc_id)` / `scan_collection(cid)` | dict summary |
| Catalog | tools/doc_modernization/catalog.py | CatalogProvider interface; GenericStoreProvider (docmod_catalog_entries), NetworkCatalogAdapter (READ-ONLY over nc_hardware_profiles/nc_device_profiles, graceful when absent), MergedCatalog (source-labelled), `propose_from_defacto()` (draft entry + audit row) | Python API | CatalogEntry list |

Config: `args/docmod/docmod_config.yaml` (thresholds, cadence, offline flag) +
`args/docmod/packs/{network_hardware,software,crypto_protocols,policy_refs}.yaml`
(launch packs ship `enabled: false` until their evaluators land — docmod-packs-01..05).
Tests: `tests/docmod/test_core_engine.py`.
