# Knowledge Graph & GraphRAG

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Knowledge Graph & GraphRAG
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Graph RAG | tools/knowledge_graph/graph_rag.py | GraphRAG retrieval with scoring profiles (D-KARL-1) | --query, --profile, --json | Retrieval context |
| Graph-Hop Expander | tools/knowledge_graph/graph_rag.py | Expand top-K BM25 hit node IDs to 1-hop neighbors; results persisted to temp file for restart recovery | --expand-hits ID [ID ...], --project-id, --json | {status, hit_ids, expansions, all_neighbors, cache_file, elapsed_ms} |
| KG Ingester | tools/knowledge_graph/ingester.py | Knowledge graph document ingestion | --file, --project-id, --json | Ingestion result |
| Insight Generator | tools/knowledge_graph/insight_generator.py | AI insight generation from graph (scanner-tier) | --graph-id, --questions, --bridge-gaps, --json | Insights |
| Text Network | tools/knowledge_graph/text_network.py | Text-to-knowledge-graph conversion | --text, --project-id, --json | Graph data |
| KG Enricher | tools/knowledge_graph/enricher.py | Centrality + embedding computation (D-KARL-7) | --graph-id, --centrality, --embeddings, --json | Enrichment results |
| Compliance Graph | tools/knowledge_graph/compliance_graph.py | Compliance crosswalk as knowledge graph — NIST/FedRAMP/CMMC controls as nodes with crosswalk edges | --build, --crosswalk, --coverage, --target, --json | Graph/crosswalk/coverage results |
| Disambiguator | tools/knowledge_graph/disambiguator.py | Entity disambiguation — find duplicates, merge entities, add aliases, resolve ambiguous labels | --find-duplicates, --merge, --add-alias, --resolve, --json | Disambiguation results |
| Federation | tools/knowledge_graph/federation.py | Cross-project graph federation — federated search, shared entities, federated views, cross-project coverage | --search, --shared, --create-view, --coverage, --json | Federation results |
| Temporal | tools/knowledge_graph/temporal.py | Temporal reasoning — time range queries, graph evolution, recent changes, stale entities, temporal diffs | --range, --evolution, --recent, --stale, --diff, --json | Temporal results |
| RAG-KG Search API | tools/knowledge_graph/blueprint.py | Flask blueprint — hybrid RAG + Knowledge Graph search endpoint with classification filtering (CUI // SP-CTI); combines RAGRetriever + GraphRAGRetriever results | GET /api/rag-kg/search?q=&tenant_id=&classification=il2&top_k=10&include_kg=true&min_tier=warm | JSON {rag_results, kg_nodes, total, classification} |
| Canvas Indexer | tools/knowledge_graph/canvas_indexer.py | Indexes canvas design topologies from sidecar databases into main KG (kg_nodes, kg_edges, kg_graphs); supports all 5 canvases (pdc/bdc/ddc/odc/idc) and both SQLite/PostgreSQL backends | --canvas [pdc|bdc|ddc|odc|idc|all] --json | JSON {indexed, errors, duration_ms} |
| Ontology Federation | tools/ontology/federation.py | Cross-canvas ontology federation — merges domain ontologies into ICDEV Core graph, resolves equivalent classes, adds cross-domain properties, SPARQL-like queries, pre-computes rdfs:subClassOf transitive closure. Integrates with tools/knowledge_graph/federation.py | --build-federation, --query, --list-domains, --list-classes, --integrate-kg, --resolve, --json | JSON federation/query results |

