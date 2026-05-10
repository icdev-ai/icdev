# What is RAG?

RAG — Retrieval-Augmented Generation — is how you make an LLM smarter without retraining it. Instead of baking knowledge into model weights (expensive, slow, stale), you retrieve relevant documents at query time and inject them into the prompt.

## The problem RAG solves

LLMs have two fatal flaws in enterprise use:

1. **Knowledge cutoff** — The model was trained on data up to a certain date. It doesn't know about your current policies, this year's compliance findings, or the RFP that dropped last week.

2. **Hallucination** — Without grounding, the model generates plausible-sounding but fabricated answers. In a DoD context, a hallucinated compliance control citation is not a minor error.

RAG solves both by giving the model real, current documents to reason over.

## How it works

```
Query → Embed → Vector Search → Top-K Docs → Prompt + Docs → LLM → Answer
```

1. **Index time**: Documents are chunked and embedded (converted to vector representations). Stored in a vector DB (ChromaDB, Weaviate, FAISS).
2. **Query time**: Your question is embedded. A similarity search finds the top-K most relevant chunks. Those chunks are injected into the LLM prompt as context.
3. **Generate**: The LLM reasons over the retrieved context to produce a grounded answer.

## In ICDEV

The full RAG stack lives in `tools/rag/`. It handles chunking, embedding via Ollama, ChromaDB storage, and retrieval. In this mission you'll work with a simplified in-memory version to understand the core mechanics — then the next step shows you the real ICDEV RAG pipeline.

## Your task

Implement `simple_rag()` that performs a basic keyword-based retrieval (no vectors yet) from a document corpus and uses the retrieved context to answer a question.
