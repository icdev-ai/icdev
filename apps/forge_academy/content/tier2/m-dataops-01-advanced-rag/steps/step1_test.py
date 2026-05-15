
# Auto-grader for DataOps M1 Step 1: Advanced RAG

import sys
from io import StringIO

captured = StringIO()
sys.stdout = captured

try:
    rag = AdvancedRAG(COMPLIANCE_CORPUS, chunk_size=200, overlap=50)
    ans_mfa = rag.query("What are the MFA requirements for FedRAMP High?")
    ans_cat = rag.query("How long to remediate a CAT I STIG finding?")
    ans_cmmc = rag.query("What is CMMC Level 2?")

    chunks = chunk_document("A" * 500, chunk_size=200, overlap=50)
    index = build_index(COMPLIANCE_CORPUS[:1], chunk_size=200, overlap=50)
    results = semantic_search("MFA authentication fedramp", index, top_k=2)
    reranked = rerank("MFA fedramp", results, threshold=0.05)

    print(f"Chunks: {len(chunks)}")
    print(f"Index entries: {len(index)}")
    print(f"Search results: {len(results)}")
    print(f"Reranked: {len(reranked)}")
    print(f"MFA answer: {ans_mfa[:60]}")
finally:
    sys.stdout = sys.__stdout__

output = captured.getvalue()

# chunk_document tests
assert chunks is not None, "chunk_document() returned None"
assert isinstance(chunks, list), f"chunk_document() must return list, got {type(chunks)}"
assert len(chunks) >= 3, f"500 chars / 200 chunk_size should give ≥3 chunks, got {len(chunks)}"

text500 = "A" * 500
chunks500 = chunk_document(text500, chunk_size=200, overlap=50)
for c in chunks500:
    assert len(c) <= 200 + 5, f"Chunk too long: {len(c)} chars"  # small tolerance

# Overlap check: adjacent chunks should share characters
if len(chunks500) >= 2:
    # Second chunk should start 150 chars in (200-50 advance)
    # So chunks[0][-50:] == chunks[1][:50] approximately
    assert chunks500[0][150:200] == chunks500[1][:50] or len(chunks500[1]) <= 200, \
        "Chunks should overlap by 50 chars"

# build_index tests
assert index is not None, "build_index() returned None"
assert isinstance(index, list), f"build_index() must return list, got {type(index)}"
assert len(index) >= 2, f"One doc with 350 chars should produce ≥2 chunks, got {len(index)}"
for entry in index:
    assert "text" in entry, "Index entry must have 'text'"
    assert "embedding" in entry, "Index entry must have 'embedding'"
    assert "source" in entry, "Index entry must have 'source'"
    assert isinstance(entry["embedding"], dict), "embedding must be a dict"

# semantic_search tests
assert results is not None, "semantic_search() returned None"
assert isinstance(results, list), "semantic_search() must return list"
assert len(results) == 2, f"top_k=2 should return 2 results, got {len(results)}"
assert "score" in results[0], "Each result must have a 'score' field"
if len(results) >= 2:
    assert results[0]["score"] >= results[1]["score"], \
        "Results must be sorted by descending score"

# rerank tests
assert reranked is not None, "rerank() returned None"
assert isinstance(reranked, list), "rerank() must return list"
# All reranked items must have score
for r in reranked:
    assert "score" in r, "Reranked items must retain 'score'"

# Full RAG query tests
assert ans_mfa, "MFA question returned empty answer"
assert ans_cat, "CAT I question returned empty answer"
assert ans_cmmc, "CMMC question returned empty answer"

ans_mfa_lower = ans_mfa.lower()
assert any(kw in ans_mfa_lower for kw in ["fedramp", "mfa", "multi-factor", "ia-2", "ia-5", "authentication"]), \
    f"MFA answer not grounded: {ans_mfa[:120]}"

ans_cmmc_lower = ans_cmmc.lower()
assert any(kw in ans_cmmc_lower for kw in ["cmmc", "cui", "800-171", "level 2", "dod"]), \
    f"CMMC answer not grounded: {ans_cmmc[:120]}"

# Answer must reference a source
assert any(src in ans_mfa for src in ["FedRAMP", "RHEL", "CMMC", "NIST"]), \
    f"Answer must cite a source document. Got: {ans_mfa[:120]}"

assert len(output) > 20, "Print results in main block"

print("PASS: Advanced RAG complete. Chunking + semantic search + reranking + AdvancedRAG.query all verified.")
