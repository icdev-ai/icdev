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
| Rulebook Pack (shared) | tools/doc_modernization/packs/rulebook_pack.py | Generic rules-driven pack parametrized by `rulebook_path` in its own YAML — a new rules-driven domain needs NO Python (crypto_protocols/policy_refs each hardcode their own path). Verdict/replacement come from the rule and cite `rule:<id>`; evidence_snapshot hashes the rules so an edit re-scans. Cache keyed by path (shared across domains). | `RulebookPack(config={"rulebook_path": ...})` | DomainPack |
| Pack Generator | tools/cli/scaffold.py (`docmod-pack` target) | `icdev scaffold docmod-pack <key>` — generates a currency pack for a new domain. `--flavor rulebook` (default) emits YAML only (pack + starter rulebook, evaluator = shared RulebookPack); `--flavor catalog --evidence-table <t>` emits a Python stub for table-driven domains. Writes in place, `skip_existing` so a re-run never clobbers authored rules; generated packs ship `enabled: false`. Templates: data/templates/docmod_packs/<flavor>/ | `docmod-pack <key> --display-name "N" [--flavor] [--dry-run] [--json]` | files + JSON |
| Scanner | tools/doc_modernization/scanner.py | Scan pipeline: latest approved dic_version → chunks (dic_chunk_links⋈rag_chunks, dic_sections fallback) → packs → docmod_findings w/ dedupe + supersede rows + incremental evidence-hash skip (docmod_doc_scan_state) | `scan_document(doc_id)` / `scan_collection(cid)` | dict summary |
| Catalog | tools/doc_modernization/catalog.py | CatalogProvider interface; GenericStoreProvider (docmod_catalog_entries), NetworkCatalogAdapter (READ-ONLY over nc_hardware_profiles/nc_device_profiles, graceful when absent), MergedCatalog (source-labelled), `propose_from_defacto()` (draft entry + audit row) | Python API | CatalogEntry list |

| Crypto Pack | tools/doc_modernization/packs/crypto_protocols.py | Rulebook pack (args/docmod/rulebook_crypto.yaml): TLS 1.0/1.1, SSLv2/3, SSHv1, telnet, SNMPv1/2c, MD5/SHA-1/DES/RC4 → deprecated_tech findings citing rule ids | pack API | Verdict/Replacement |
| Software Pack | tools/doc_modernization/packs/software.py | Product+version extraction; verdicts from docmod_eol_products; unmapped → `unknown` never `eol`; replacement = newest supported cycle | pack API | Verdict/Replacement |
| Network HW Pack | tools/doc_modernization/packs/network_hardware.py | Curated catalog PRIMARY (NetworkCatalogAdapter + generic store) → mc_net_eol_data fallback → de facto stats tie-breaker; inventory-vocabulary extraction | pack API | Verdict/Replacement |
| Policy Pack | tools/doc_modernization/packs/policy_refs.py | Supersession map (args/docmod/rulebook_policy.yaml): NIST rev withdrawals, RFC obsoletions, FIPS 140-2→3; KG standard-node corroboration | pack API | Verdict/Replacement |
| SOP Workflows Pack | args/docmod/packs/sop_workflows.yaml (no Python — shared RulebookPack) | TOOL/COMMAND/PLATFORM drift in procedures (args/docmod/rulebook_sop_workflows.yaml, 18 rules): retired CI (Travis), container/k8s tooling (docker-compose v1, docker-machine, `helm init`, `--generator`, `--export`, PodSecurityPolicy, gcr.io), build commands (python2, easy_install, nosetests, apt-key), host commands (ifconfig, netstat, wmic), shut-down platforms (HipChat, Skype for Business, Bitbucket Server). entity_type `tool_reference`; platform shutdowns → `stale_reference`, commands → `deprecated_tech`. Ships `enabled: false` pending validation against a real corpus; org-specific rules (Jenkins deploy.sh, svn) ship commented. Org/role drift is OUT OF SCOPE — needs an org catalog this repo lacks. | pack API | Verdict/Replacement |
| Architecture Patterns Pack | args/docmod/packs/architecture_patterns.yaml (no Python — shared RulebookPack) | Obsolete→modern ARCHITECTURE-PATTERN drift in design docs (args/docmod/rulebook_architecture_patterns.yaml, 7 rules): Hystrix→Resilience4j/mesh, CORBA/DCOM→gRPC/REST, SOAP/WSDL→REST/gRPC, ESB→API-gateway+event-streaming, hand-rolled crypto→vetted library, in-process session state→stateless/externalized, monolithic three-tier→microservices. entity_type `architecture_pattern` (new; KG_ENTITY_TYPES both namespaces); retired stacks (CORBA/DCOM) → `stale_reference`, rest → `deprecated_tech`; NIST SA-8/PL-8. Each rule NARROWED with a false-positive guard (plain "three-tier"/"session affinity"/non-crypto "custom" stay clean) + `citation` URL + `confidence`. Ships `enabled: false` pending corpus validation; org-specific rules ship commented. Procedure/tool drift is OUT OF SCOPE (that is SOP Workflows); on-prem/air-gap is a supported posture, deliberately NOT flagged. | pack API | Verdict/Replacement |
| EOL Products Sync | tools/doc_modernization/eol_products_sync.py | endoflife.date cache (docmod_eol_products): live sync + `args/docmod/eol_products.yaml` seed + `import_dataset()` air-gap bundle; `get_product_eol()` alias-aware lookup; `newest_supported_cycle()` | `--seed/--sync/--import <path> --json` | JSON |
| De Facto Learner | tools/doc_modernization/defacto_learner.py | Recency-weighted (half-life 180d) deployment stats over ni_devices → docmod_defacto_standards; `get_recommended(category)`; `cross_check(catalog)` → divergence/gap records | `recompute()` | dict |

KG hook: `tools/knowledge_graph/text_network.py::EXTRA_ENTITY_PATTERNS` — pack
regexes are published at load time so ingested docs grow hardware_model /
software_product / protocol / crypto_algorithm KG nodes. MCP repair:
`handle_kg_stale_entities` in tools/mcp/gap_handlers.py now backs the
previously-dangling `kg_stale_entities` tool via knowledge_graph/temporal.py.

Config: `args/docmod/docmod_config.yaml` (thresholds, cadence, offline flag) +
`args/docmod/packs/*.yaml` (the four launch packs enabled; `sop_workflows` and
`architecture_patterns` ship disabled) +
`args/docmod/{rulebook_crypto,rulebook_policy,rulebook_sop_workflows,rulebook_architecture_patterns,eol_products}.yaml`.
Tests: `tests/docmod/test_core_engine.py`, `tests/docmod/test_domain_packs.py`,
`tests/docmod/test_sop_workflows_pack.py`,
`tests/docmod/test_architecture_patterns_pack.py`.
