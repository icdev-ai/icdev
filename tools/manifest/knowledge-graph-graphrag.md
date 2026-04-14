# Knowledge Graph & GraphRAG

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Knowledge Graph & GraphRAG
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Graph RAG | tools/knowledge_graph/graph_rag.py | GraphRAG retrieval with scoring profiles (D-KARL-1) | --query, --profile, --json | Retrieval context |
| KG Ingester | tools/knowledge_graph/ingester.py | Knowledge graph document ingestion | --file, --project-id, --json | Ingestion result |
| Insight Generator | tools/knowledge_graph/insight_generator.py | AI insight generation from graph (scanner-tier) | --graph-id, --questions, --bridge-gaps, --json | Insights |
| Text Network | tools/knowledge_graph/text_network.py | Text-to-knowledge-graph conversion | --text, --project-id, --json | Graph data |
| KG Enricher | tools/knowledge_graph/enricher.py | Centrality + embedding computation (D-KARL-7) | --graph-id, --centrality, --embeddings, --json | Enrichment results |
| Compliance Graph | tools/knowledge_graph/compliance_graph.py | Compliance crosswalk as knowledge graph — NIST/FedRAMP/CMMC controls as nodes with crosswalk edges | --build, --crosswalk, --coverage, --target, --json | Graph/crosswalk/coverage results |
| Disambiguator | tools/knowledge_graph/disambiguator.py | Entity disambiguation — find duplicates, merge entities, add aliases, resolve ambiguous labels | --find-duplicates, --merge, --add-alias, --resolve, --json | Disambiguation results |
| Federation | tools/knowledge_graph/federation.py | Cross-project graph federation — federated search, shared entities, federated views, cross-project coverage | --search, --shared, --create-view, --coverage, --json | Federation results |
| Temporal | tools/knowledge_graph/temporal.py | Temporal reasoning — time range queries, graph evolution, recent changes, stale entities, temporal diffs | --range, --evolution, --recent, --stale, --diff, --json | Temporal results |

