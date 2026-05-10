# Auto-grader for DataOps M3: Corrective RAG (CRAG)

# ── Test: score_relevance ─────────────────────────────────────────────────────

# Perfect overlap
doc_match = {"id":"x","text":"mfa authentication privileged accounts required","source":"test"}
score_exact = score_relevance(doc_match, "mfa authentication privileged")
assert score_exact is not None, "score_relevance() returned None"
assert isinstance(score_exact, float), "score_relevance() must return float"
assert 0.0 <= score_exact <= 1.0, f"score must be in [0,1], got {score_exact}"
assert score_exact > 0.5, f"High overlap should score >0.5, got {score_exact}"

# No overlap
doc_none = {"id":"y","text":"the capital of France is Paris Europe","source":"geo"}
score_none = score_relevance(doc_none, "mfa authentication privileged")
assert score_none < 0.1, f"No overlap should score <0.1, got {score_none}"

# Partial overlap
doc_partial = {"id":"z","text":"authentication is required for all users in the system","source":"test"}
score_partial = score_relevance(doc_partial, "authentication privileged mfa requirements")
assert 0.0 < score_partial < score_exact, "Partial overlap should be between 0 and perfect match"

# ── Test: validate_documents ──────────────────────────────────────────────────

docs = [
    {"id":"d1","text":"mfa multi-factor authentication for privileged accounts","source":"NIST"},
    {"id":"d2","text":"the capital of France is Paris","source":"geo"},
    {"id":"d3","text":"account management requires formal approval processes","source":"NIST"},
]
valid, invalid = validate_documents(docs, "mfa authentication privileged account", threshold=0.3)

assert valid is not None, "validate_documents() returned None valid"
assert invalid is not None, "validate_documents() returned None invalid"
assert isinstance(valid, list), "valid must be list"
assert isinstance(invalid, list), "invalid must be list"
assert len(valid) + len(invalid) == len(docs), "all docs must be categorized"

# relevance_score must be added to each doc
for doc in docs:
    assert "relevance_score" in doc, f"validate_documents must add relevance_score to doc {doc['id']}"
    assert isinstance(doc["relevance_score"], float), "relevance_score must be float"

# d1 (MFA/auth) and d3 (account management) should be valid for this query
valid_ids = [d["id"] for d in valid]
assert "d1" in valid_ids, "d1 (MFA text) should be valid for auth query"
assert "d2" not in valid_ids, "d2 (France/Paris) should not be valid for auth query"

# ── Test: CorrectiveRAG.retrieve ──────────────────────────────────────────────

crag = CorrectiveRAG()

retrieved = crag.retrieve("IA-2 authentication privileged", PRIMARY_CORPUS)
assert retrieved is not None, "retrieve() returned None"
assert isinstance(retrieved, list), "retrieve() must return list"
assert len(retrieved) <= 3, f"retrieve returns top-3, got {len(retrieved)}"
assert len(retrieved) >= 1, "retrieve should return at least 1 result from non-empty corpus"

# Each result should have relevance_score
for doc in retrieved:
    assert "relevance_score" in doc, "Retrieved doc must have relevance_score"
    assert "id" in doc, "Retrieved doc must have id"

# Results should be sorted by relevance descending
for i in range(len(retrieved)-1):
    assert retrieved[i]["relevance_score"] >= retrieved[i+1]["relevance_score"], \
        f"Results must be sorted descending: [{i}]={retrieved[i]['relevance_score']} < [{i+1}]={retrieved[i+1]['relevance_score']}"

# ── Test: CorrectiveRAG.query — high confidence query ────────────────────────

# This query matches well with PRIMARY_CORPUS
r_high = crag.query("IA-2 multi-factor authentication required privileged accounts")
assert r_high is not None, "query() returned None"
assert isinstance(r_high, dict), "query() must return dict"
assert "confidence" in r_high, "Result must have 'confidence'"
assert "retrieved_count" in r_high, "Result must have 'retrieved_count'"
assert "valid_count" in r_high, "Result must have 'valid_count'"
assert "final_docs" in r_high, "Result must have 'final_docs'"
assert "used_fallback" in r_high, "Result must have 'used_fallback'"
assert r_high["confidence"] in ("high", "low", "none"), \
    f"confidence must be 'high'/'low'/'none', got '{r_high['confidence']}'"

if r_high["confidence"] == "high":
    assert r_high["used_fallback"] is False, "High confidence -> used_fallback=False"

# ── Test: CorrectiveRAG.query — irrelevant query triggers fallback ────────────

r_fallback = crag.query("best pizza restaurant in Rome Italy food tourism")
assert r_fallback["used_fallback"] is True, \
    f"Irrelevant query should trigger fallback, got confidence='{r_fallback['confidence']}'"
assert r_fallback["confidence"] in ("low", "none"), \
    f"Irrelevant query -> confidence='low' or 'none', got '{r_fallback['confidence']}'"

# Fallback corpus docs should appear in final_docs
final_ids = [d["id"] for d in r_fallback["final_docs"]]
fallback_ids = [d["id"] for d in FALLBACK_CORPUS]
has_fallback = any(fid in final_ids for fid in fallback_ids)
assert has_fallback, \
    f"Fallback query should include fallback corpus docs, final_ids={final_ids}, fallback_ids={fallback_ids}"

print("PASS: CorrectiveRAG complete. score_relevance + validate_documents + retrieve + query all verified.")
