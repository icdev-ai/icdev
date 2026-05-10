# ChromaDB-Style Vector Store — Document Embedding and Retrieval

Production RAG systems need persistent vector storage. In this mission you'll implement a lightweight in-memory vector store that mirrors ChromaDB's collection API — `add()`, `query()`, `get()`, `delete()` — backed by cosine similarity search.

## What You'll Build

A `VectorCollection` that stores documents with embeddings and supports similarity search:

```python
col = VectorCollection("compliance_docs")
col.add(
    ids=["doc1", "doc2"],
    documents=["STIG IA-2: MFA required", "AC-2: Account management"],
    embeddings=[[0.1, 0.9, 0.3], [0.8, 0.2, 0.5]],
    metadatas=[{"source": "stig"}, {"source": "nist"}],
)
results = col.query(query_embeddings=[[0.1, 0.85, 0.35]], n_results=1)
```

## Core Operations

- **add()**: Store documents with their embeddings and optional metadata
- **query()**: Find top-N most similar documents using cosine similarity
- **get()**: Retrieve documents by ID
- **delete()**: Remove documents by ID
- **count()**: Return number of stored documents

## Cosine Similarity Formula

```
similarity(A, B) = (A · B) / (|A| × |B|)
```

Where `A · B` is the dot product and `|A|` is the L2 norm.

## Success Criteria

- `cosine_similarity(a, b)` returns 1.0 for identical vectors, 0 for orthogonal
- `VectorCollection.add()` stores documents and validates id uniqueness
- `VectorCollection.query()` returns top-N results sorted by similarity (descending)
- `VectorCollection.get()` retrieves by exact ID match
- `VectorCollection.delete()` removes documents and returns count deleted
- `VectorCollection.count()` returns current document count
