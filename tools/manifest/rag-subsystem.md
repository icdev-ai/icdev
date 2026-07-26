# RAG Subsystem (Additional)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## RAG Subsystem (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Auto Indexer | tools/rag/auto_indexer.py | Automatic RAG index maintenance | --index, --json | Index status |
| Corrective RAG | tools/rag/corrective_rag.py | Parallel multi-strategy retrieval (D-KARL-3) | --parallel, --query, --profile, --json | Merged results |
| PDF Provider | tools/rag/pdf_provider.py | PDF text extraction for RAG ingestion (D-FT-11) | (library) | Extracted text |
| Reranker Provider | tools/rag/reranker_provider.py | Two-stage re-ranking provider (D-RAG-3) | (library) | Reranked results |
| Secret Ref | tools/rag/secret_ref.py | Secret reference resolver for RAG | (library) | Resolved refs |
| Codebase Indexer | tools/rag/codebase_indexer.py | AST-based Python + text codebase indexer for assistant widget (D-CA-1, D-CA-2) | --scan, --scope, --json | Index status |
| CRAG Evaluator | tools/rag/crag_evaluator.py | CRAG benchmark evaluation — 8 question types, hallucination-penalizing scoring (D-RAG-23) | --benchmark-crag, --classify-question, --score, --gate, --json | Campaign results |
| Query Classifier | tools/rag/query_classifier.py | 4-label taxonomy classifier for RAG queries (D-RAG-24) | --classify --query, --classify-batch --input, --json | Label + confidence |
| Quality Feedback Loop | tools/rag/quality_feedback_loop.py | Closed-loop RAG quality → retrain pipeline (D-KARL-9) | --run, --dry-run, --status, --json | Cycle results |
| Statement Extractor | tools/finetune/statement_extractor.py | Grounded Q&A pair generation via statement extraction (D-FT-23) | --extract, --extract-from-rag, --stats, --json | Pairs + taxonomy labels |
| Contextual Retrieval | tools/rag/contextual_retrieval.py | Anthropic contextual-retrieval prefixing at ingestion — embed contextualized text, cite original (rce-ctx-01); opt-in, air-gap safe | (library) generate_context_prefix, contextualize_chunk | Context prefix + provenance |
| Contextual Re-index | tools/rag/reindex_contextual.py | Re-index existing chunks with contextual prefixes + measure vs baseline (rce-ctx-02); resumable (windowed --limit/--offset, rce-eval-04), retention-aware, injectable | --reindex --source, --limit, --offset, --dry-run, --execute, --benchmark --baseline, --json | Re-index stats + window resume cursor + baseline deltas |
| RAPTOR Builder (RCE) | tools/rag/raptor.py | RAPTOR summary hierarchy above rag_chunks leaves; cheap-LLM summaries, graceful no-op, default-OFF (D-RAG-RCE-1) | --build, --source, --dry-run, --json | rag_chunk_summaries tree |
| RAG Benchmark | tools/rag/rag_benchmark.py | Retrieval-quality baseline harness — golden query set (compliance/NIST) scored for recall@k, MRR, citation-hit-rate; reuses evaluator.mrr/ndcg (rce-eval-01). `--toggle`/`--sweep` measure one retrieval toggle in isolation against a common control (oss-meas-01-d2) and **refuse** to emit a number for a toggle whose consumer is unreachable from the retriever | --golden-set, --top-k, --baseline-out, --compare, --toggle, --sweep, --only, --json | Aggregate + per-query metrics; sweep adds per-toggle deltas and NOT-WIRED verdicts |
| Toggle Harness | tools/rag/toggle_harness.py | oss-meas-01-d2. Single-toggle isolation for benchmarking. `probe_reachability()` walks the import closure from `tools.rag.retriever` (following deferred function-level imports) to answer "could flipping this change retrieval at all"; `isolated_config()` writes a temp rag_config.yaml with exactly one toggle changed and points `ICDEV_RAG_CONFIG` at it, never touching the committed file. Exists because an unwired toggle and a useless toggle both measure as zero — reporting a delta for dead code turns "never connected" into an evidence-backed "DROP" | --probe, --verify TOGGLE, --list, --json | WIRED / WIRED-INGEST-ONLY / NOT-WIRED per toggle; isolation proof |
| RAG Config Path | tools/rag/config_path.py | Single resolver for `args/rag_config.yaml`, honouring `ICDEV_RAG_CONFIG`. Adopted by retriever/auto_indexer/sqlite_vector_store so a measurement run can vary one toggle without rewriting a config file shared with other agent sessions and the kanban scheduler. A non-existent override is returned as-is rather than falling back, so a typo fails visibly instead of silently measuring the baseline twice | (import) | Path |
| Embedding Feasibility | tools/rag/embedding_feasibility.py | Domain-adapted embedding fine-tune feasibility probe — counts in-domain (compliance/NIST) vs research chunks in the vector store; re-runnable training-data-availability signal behind the rce-eval-02 go/no-go (dependency-free) | --db, --min-eligible, --baseline, --json | Corpus stats + TRAIN-DATA signal |

