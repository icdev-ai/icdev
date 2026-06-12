# Phase 71: CRAG Evaluation, Know Your RAG Taxonomy, and LLM Sandbox

**Date:** 2026-03-21
**ADRs:** D-RAG-23, D-RAG-24, D-FT-23, D-SEC-10, D-KARL-9, D-SEC-11

## Summary

Integrates three external resources into ICDEV™'s RAG evaluation, fine-tuning, and security subsystems:

1. **CRAG Benchmark** (arxiv 2406.04744) — 8 question type taxonomy, hallucination-penalizing scoring (-1/0/0.5/1), entity popularity tiers
2. **Know Your RAG** (arxiv 2411.19710) — 4-label query taxonomy, statement extraction for grounded pair generation, per-label adaptive hybrid search weights
3. **LLM Sandbox** (vndee/llm-sandbox) — Docker-based containerized code execution with resource limits and audit logging

## New Tools

| Tool | File | ADR |
|------|------|-----|
| CRAG Evaluator | `tools/rag/crag_evaluator.py` | D-RAG-23 |
| Query Classifier | `tools/rag/query_classifier.py` | D-RAG-24 |
| Statement Extractor | `tools/finetune/statement_extractor.py` | D-FT-23 |
| Sandbox Executor | `tools/security/sandbox_executor.py` | D-SEC-10 |
| Quality Feedback Loop | `tools/rag/quality_feedback_loop.py` | D-KARL-9 |
| RAG Eval Dashboard API | `tools/dashboard/api/rag_eval.py` | D-KARL-9 |

## New DB Tables

- `rag_evaluation_campaigns` — Evaluation campaign tracking
- `rag_evaluation_results` — Per-case CRAG + RAGAS scores with question type and popularity tier
- `sandbox_execution_log` — Append-only audit trail for all sandboxed code executions

## Config Changes

- `args/rag_config.yaml` — Added `evaluation.crag` and `label_weights` sections
- `args/finetune_config.yaml` — Added `taxonomy` and extended `quality_feedback` sections
- `args/sandbox_config.yaml` — New config for sandbox runtime, resources, and images
- `args/security_gates.yaml` — Added `sandbox_execution` gate

## Modified Files

- `tools/rag/evaluator.py` — Added `scoring_mode` param (ragas/crag/both) to `evaluate_generation()`
- `tools/rag/retriever.py` — Added `query_label` param with per-label BM25/RRF weights
- `tools/registry/sandbox_scorer.py` — Enhanced `_check_runtime_isolation()` with Docker probe
- `tools/dashboard/app.py` — Registered `rag_eval_api` blueprint

## Key Design Decisions

- All LLM calls use scanner-tier (qwen3.5) — zero Claude token cost
- All tools have deterministic template fallbacks for air-gap operation
- Sandbox executor gracefully degrades when Docker/llm-sandbox unavailable
- Per-label adaptive weights are backward-compatible (without `query_label`, existing behavior unchanged)
- Statement extraction produces grounded pairs with near-zero hallucination risk
- Closed feedback loop: quality degradation → targeted pair generation → retrain trigger

## Test Coverage

172 new tests across 6 test files, all passing.
