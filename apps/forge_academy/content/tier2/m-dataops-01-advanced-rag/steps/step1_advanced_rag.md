---
ontology_id: icdev:mission:m-dataops-01-advanced-rag:step:1
step_class: icdev:Lesson
---

# Advanced RAG — Chunking, Embedding, and Reranking

The basic RAG pipeline from M03 works — but it fails on long documents, technical jargon, and multi-hop questions. Production RAG requires three upgrades: better chunking, semantic similarity scoring, and a reranker that filters noise before the LLM sees anything.

## What you'll build

```
Document corpus
        │
        ▼
chunk_document(text, chunk_size, overlap) → list of chunks
        │
        ▼
build_index(chunks) → list of {text, embedding, source}
        │
        ▼
semantic_search(query, index, top_k) → top chunks by similarity
        │
        ▼
rerank(query, candidates) → filtered, reordered chunks
        │
        ▼
AdvancedRAG.query(question) → grounded answer
```

## The three upgrades

### 1. Chunking with overlap

Basic RAG splits on newlines. Production RAG uses **sliding window chunking** with overlap to avoid cutting sentences mid-thought:

```python
chunk_size = 200  # characters
overlap = 50      # characters shared between adjacent chunks
```

### 2. Semantic similarity (cosine)

Instead of keyword counting, score chunks by cosine similarity against the query embedding. We simulate embeddings using term-frequency vectors for this mission.

**Cosine similarity:** `dot(a, b) / (|a| * |b|)`

### 3. Reranking

After retrieving `top_k` candidates, a reranker scores each by keyword overlap with the query and drops any chunk scoring below a threshold. This removes chunks that match semantically but lack the actual answer.

## Success criteria

- `chunk_document()` produces at least 3 chunks from the test corpus with correct overlap
- `semantic_search()` returns the most relevant chunk first for a FedRAMP query
- `rerank()` correctly drops a low-relevance chunk from the candidates
- `AdvancedRAG.query()` returns a grounded answer citing at least one source
