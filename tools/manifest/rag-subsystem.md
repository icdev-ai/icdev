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
| RAG Benchmark | tools/rag/rag_benchmark.py | Retrieval-quality baseline harness — golden query set (compliance/NIST) scored for recall@k, MRR, citation-hit-rate; reuses evaluator.mrr/ndcg (rce-eval-01) | --golden-set, --top-k, --baseline-out, --compare, --json | Aggregate + per-query metrics |

