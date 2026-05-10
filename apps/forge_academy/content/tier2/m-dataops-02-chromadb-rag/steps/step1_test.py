# Auto-grader for DataOps M2: ChromaDB-Style Vector Store

import math

# ── Test: cosine_similarity ───────────────────────────────────────────────────

# Identical vectors -> 1.0
s_identical = cosine_similarity([1.0, 0.0, 0.0], [1.0, 0.0, 0.0])
assert s_identical is not None, "cosine_similarity() returned None"
assert abs(s_identical - 1.0) < 1e-6, f"Identical vectors -> 1.0, got {s_identical}"

# Orthogonal vectors -> 0.0
s_ortho = cosine_similarity([1.0, 0.0], [0.0, 1.0])
assert abs(s_ortho - 0.0) < 1e-6, f"Orthogonal vectors -> 0.0, got {s_ortho}"

# Opposite vectors -> -1.0
s_opp = cosine_similarity([1.0, 0.0], [-1.0, 0.0])
assert abs(s_opp - (-1.0)) < 1e-6, f"Opposite vectors -> -1.0, got {s_opp}"

# Zero vector -> 0.0 (no crash)
s_zero = cosine_similarity([0.0, 0.0], [1.0, 0.0])
assert s_zero == 0.0, f"Zero vector -> 0.0, got {s_zero}"

# Similar vectors -> high score
s_sim = cosine_similarity([0.9, 0.1, 0.2], [0.85, 0.15, 0.25])
assert s_sim > 0.95, f"Similar vectors should score >0.95, got {s_sim}"

# ── Setup collection ──────────────────────────────────────────────────────────

col = VectorCollection("test_collection")
assert col.name == "test_collection", "Collection name should be stored"
assert col.count() == 0, f"Empty collection should have count=0, got {col.count()}"

col.add(
    ids=["doc1", "doc2", "doc3"],
    documents=["MFA required for privileged access",
               "Account management procedures",
               "System monitoring requirements"],
    embeddings=[[0.9, 0.1, 0.2], [0.2, 0.8, 0.3], [0.1, 0.3, 0.9]],
    metadatas=[{"source": "stig"}, {"source": "nist"}, {"source": "fedramp"}],
)

# ── Test: count ───────────────────────────────────────────────────────────────

assert col.count() == 3, f"After adding 3 docs -> count=3, got {col.count()}"

# ── Test: upsert (overwrite on duplicate id) ──────────────────────────────────

col.add(ids=["doc1"], documents=["Updated MFA doc"], embeddings=[[0.9, 0.1, 0.2]])
assert col.count() == 3, f"Upsert should not increase count, still 3, got {col.count()}"

col.add(ids=["doc1"], documents=["MFA required for privileged access"], embeddings=[[0.9, 0.1, 0.2]],
        metadatas=[{"source": "stig"}])  # restore

# ── Test: query ───────────────────────────────────────────────────────────────

results = col.query([[0.9, 0.1, 0.2]], n_results=2)
assert results is not None, "query() returned None"
assert isinstance(results, dict), f"query() must return dict"
assert "ids" in results, "Result must have 'ids'"
assert "documents" in results, "Result must have 'documents'"
assert "distances" in results, "Result must have 'distances'"
assert "metadatas" in results, "Result must have 'metadatas'"

# One query -> one result set
assert len(results["ids"]) == 1, f"1 query -> 1 result set, got {len(results['ids'])}"
top_ids = results["ids"][0]
assert len(top_ids) == 2, f"n_results=2 -> 2 ids, got {len(top_ids)}"
assert top_ids[0] == "doc1", f"Most similar to [0.9,0.1,0.2] should be doc1, got {top_ids[0]}"

# Distances should be sorted ascending (most similar = smallest distance)
distances = results["distances"][0]
assert distances[0] <= distances[1], \
    f"Distances should be ascending (best first): {distances}"

# ── Test: get ─────────────────────────────────────────────────────────────────

got = col.get(["doc1", "doc3", "missing_id"])
assert got is not None, "get() returned None"
assert isinstance(got, dict), "get() must return dict"
assert len(got["ids"]) == 2, \
    f"get(['doc1','doc3','missing_id']) should return 2 found docs, got {len(got['ids'])}"
assert "doc1" in got["ids"], "doc1 should be in results"
assert "doc3" in got["ids"], "doc3 should be in results"
assert "missing_id" not in got["ids"], "missing_id should be silently skipped"

# ── Test: delete ──────────────────────────────────────────────────────────────

deleted = col.delete(["doc2", "missing_id"])
assert isinstance(deleted, int), "delete() must return int"
assert deleted == 1, f"Deleted 1 existing doc -> 1, got {deleted}"
assert col.count() == 2, f"After deleting doc2 -> count=2, got {col.count()}"

# Verify doc2 is gone
got_after = col.get(["doc2"])
assert len(got_after["ids"]) == 0, "doc2 should be gone after deletion"

# ── Test: ValueError on mismatched lengths ────────────────────────────────────

try:
    col.add(ids=["x", "y"], documents=["one"], embeddings=[[0.1, 0.2]])
    assert False, "Should raise ValueError for mismatched lengths"
except ValueError:
    pass  # expected

print("PASS: VectorCollection complete. cosine_similarity + add + query + get + delete + count all verified.")
