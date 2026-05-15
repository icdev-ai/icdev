
"""
Tier 2 DataOps Mission 2: ChromaDB-Style Vector Store
Goal: Implement a VectorCollection with cosine similarity search.
"""

import math
from typing import Optional


# ── Step 1: Cosine Similarity ─────────────────────────────────────────────────

def cosine_similarity(a: list[float], b: list[float]) -> float:
    """TODO: Compute cosine similarity between two vectors.

    Formula: dot(a, b) / (norm(a) * norm(b))
    Return 0.0 if either vector has zero magnitude (no raise).
    Both vectors will always have the same length.
    """
    # YOUR CODE HERE
    pass


# ── Step 2: VectorCollection ──────────────────────────────────────────────────

class VectorCollection:
    """In-memory vector store mirroring ChromaDB's collection API."""

    def __init__(self, name: str):
        self.name = name
        self._store: dict = {}  # id → {"document": str, "embedding": list, "metadata": dict}

    def add(self, ids: list[str], documents: list[str],
            embeddings: list[list[float]],
            metadatas: Optional[list[dict]] = None) -> None:
        """TODO: Store documents with embeddings and optional metadata.

        For each (id, document, embedding, metadata) tuple:
        - If id already exists, overwrite it (upsert behavior)
        - If metadatas is None, use empty dict for each document
        - Store as: self._store[id] = {"document": doc, "embedding": emb, "metadata": meta}

        Raise ValueError if len(ids) != len(documents) or len(ids) != len(embeddings).
        """
        # YOUR CODE HERE
        pass

    def query(self, query_embeddings: list[list[float]], n_results: int = 3) -> dict:
        """TODO: Find top-N most similar documents for each query embedding.

        For each query_embedding in query_embeddings:
        1. Compute cosine_similarity(query_embedding, stored_embedding) for all stored docs
        2. Sort by similarity descending
        3. Return top n_results

        Return structure (matching ChromaDB API):
        {
            "ids": [[id1, id2, ...] for each query],
            "documents": [[doc1, doc2, ...] for each query],
            "metadatas": [[meta1, meta2, ...] for each query],
            "distances": [[1-sim1, 1-sim2, ...] for each query],  ← distance = 1 - similarity
        }

        If store is empty, return empty lists for each query.
        """
        # YOUR CODE HERE
        pass

    def get(self, ids: list[str]) -> dict:
        """TODO: Retrieve documents by ID.

        Return only the IDs that exist in the store:
        {
            "ids": [found_ids],
            "documents": [found_documents],
            "metadatas": [found_metadatas],
        }
        Missing IDs are silently skipped.
        """
        # YOUR CODE HERE
        pass

    def delete(self, ids: list[str]) -> int:
        """TODO: Delete documents by ID.

        Remove each id from self._store if it exists.
        Return count of documents actually deleted (skip missing ids silently).
        """
        # YOUR CODE HERE
        pass

    def count(self) -> int:
        """TODO: Return the number of documents in the collection."""
        # YOUR CODE HERE
        pass


# Test
if __name__ == "__main__":
    col = VectorCollection("compliance_docs")

    # Add documents
    col.add(
        ids=["doc1", "doc2", "doc3"],
        documents=["STIG IA-2: MFA required for privileged access",
                   "NIST AC-2: Account management procedures",
                   "FedRAMP SI-4: System monitoring requirements"],
        embeddings=[[0.9, 0.1, 0.2], [0.2, 0.8, 0.3], [0.1, 0.3, 0.9]],
        metadatas=[{"source": "stig"}, {"source": "nist"}, {"source": "fedramp"}],
    )

    print(f"Documents in collection: {col.count()}")

    results = col.query([[0.85, 0.15, 0.25]], n_results=2)
    print(f"Top 2 results for MFA query: {results['ids'][0]}")
    print(f"Documents: {results['documents'][0]}")

    col.delete(["doc2"])
    print(f"After deletion: {col.count()} documents")
