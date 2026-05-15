
# Auto-grader for DataOps Capstone M4: RAG Evaluation Pipeline
from datetime import date

# ── Test: cosine_similarity ───────────────────────────────────────────────────

sim_identical = cosine_similarity([1.0, 0.0, 0.0], [1.0, 0.0, 0.0])
assert sim_identical is not None, "cosine_similarity() returned None"
assert isinstance(sim_identical, float), "cosine_similarity() must return float"
assert abs(sim_identical - 1.0) < 0.01, f"Identical vectors -> 1.0, got {sim_identical}"

sim_ortho = cosine_similarity([1.0, 0.0], [0.0, 1.0])
assert abs(sim_ortho - 0.0) < 0.01, f"Orthogonal vectors -> 0.0, got {sim_ortho}"

sim_partial = cosine_similarity([1.0, 1.0], [1.0, 0.0])
assert 0.5 < sim_partial < 1.0, f"Partial overlap -> (0.5, 1.0), got {sim_partial}"

# Zero vector
sim_zero = cosine_similarity([0.0, 0.0], [1.0, 0.0])
assert sim_zero == 0.0, f"Zero vector -> 0.0, got {sim_zero}"

# ── Test: score_keyword_relevance ─────────────────────────────────────────────

doc_high = {"id": "x", "text": "multi-factor authentication required privileged accounts"}
score_high = score_keyword_relevance(doc_high, "authentication privileged accounts")
assert score_high is not None, "score_keyword_relevance() returned None"
assert isinstance(score_high, float), "score_keyword_relevance() must return float"
assert score_high > 0.5, f"High overlap -> >0.5, got {score_high}"

doc_none = {"id": "y", "text": "Paris is the capital of France in Western Europe"}
score_none = score_keyword_relevance(doc_none, "authentication privileged accounts")
assert score_none == 0.0, f"No overlap -> 0.0, got {score_none}"

# ── Test: retrieve_top_k ──────────────────────────────────────────────────────

retrieved = retrieve_top_k("authentication privileged", SAMPLE_DOCS, k=3)
assert retrieved is not None, "retrieve_top_k() returned None"
assert isinstance(retrieved, list), "retrieve_top_k() must return list"
assert len(retrieved) <= 3, f"Should return at most 3, got {len(retrieved)}"
assert len(retrieved) >= 1, "Should return at least 1 result"

for doc in retrieved:
    assert "relevance_score" in doc, "Retrieved doc must have relevance_score"
    assert "id" in doc, "Retrieved doc must have id"

# Should be sorted descending
for i in range(len(retrieved) - 1):
    assert retrieved[i]["relevance_score"] >= retrieved[i+1]["relevance_score"], \
        "Retrieved docs must be sorted by relevance descending"

# d1 should rank highly for authentication query
top_ids = [d["id"] for d in retrieved]
assert "d1" in top_ids, "d1 (MFA text) should appear in top-3 for authentication query"

# retrieve_top_k should not mutate original docs
original_keys = set(SAMPLE_DOCS[0].keys())
assert "relevance_score" not in original_keys, \
    "retrieve_top_k must not mutate original SAMPLE_DOCS"

# ── Test: RAGEvaluator.evaluate_query ─────────────────────────────────────────

evaluator = RAGEvaluator()

eq = evaluator.evaluate_query("mfa authentication privileged", ["d1", "d4"], SAMPLE_DOCS)
assert eq is not None, "evaluate_query() returned None"
assert isinstance(eq, dict), "evaluate_query() must return dict"

for key in ["query", "retrieved_ids", "expected_ids", "hits", "precision", "recall", "f1"]:
    assert key in eq, f"evaluate_query result missing '{key}'"

assert eq["query"] == "mfa authentication privileged", "query field mismatch"
assert isinstance(eq["retrieved_ids"], list), "retrieved_ids must be list"
assert isinstance(eq["hits"], list), "hits must be list"
assert isinstance(eq["precision"], float), "precision must be float"
assert isinstance(eq["recall"], float), "recall must be float"
assert isinstance(eq["f1"], float), "f1 must be float"
assert 0.0 <= eq["precision"] <= 1.0, f"precision out of range: {eq['precision']}"
assert 0.0 <= eq["recall"] <= 1.0, f"recall out of range: {eq['recall']}"
assert 0.0 <= eq["f1"] <= 1.0, f"f1 out of range: {eq['f1']}"

# All hits must be in both retrieved and expected
for h in eq["hits"]:
    assert h in eq["retrieved_ids"], f"hit '{h}' not in retrieved_ids"
    assert h in eq["expected_ids"], f"hit '{h}' not in expected_ids"

# Perfect recall test: single expected doc retrieved
eq_perfect = evaluator.evaluate_query("encryption fips modules", ["d3"], SAMPLE_DOCS)
if "d3" in eq_perfect["retrieved_ids"]:
    assert eq_perfect["recall"] == 1.0, "d3 retrieved -> recall should be 1.0"

# ── Test: RAGEvaluator.run ────────────────────────────────────────────────────

report = evaluator.run(TEST_QUERIES, SAMPLE_DOCS)
assert report is not None, "run() returned None"
assert isinstance(report, dict), "run() must return dict"

for key in ["run_date", "total_queries", "results", "summary"]:
    assert key in report, f"Report missing '{key}'"

assert report["run_date"] == str(date.today()), "run_date must be today"
assert report["total_queries"] == len(TEST_QUERIES), \
    f"total_queries should be {len(TEST_QUERIES)}, got {report['total_queries']}"
assert len(report["results"]) == len(TEST_QUERIES), \
    f"results should have {len(TEST_QUERIES)} items"

summary = report["summary"]
for key in ["avg_precision", "avg_recall", "avg_f1", "perfect_recall"]:
    assert key in summary, f"summary missing '{key}'"

assert isinstance(summary["avg_precision"], float), "avg_precision must be float"
assert isinstance(summary["avg_recall"], float), "avg_recall must be float"
assert isinstance(summary["avg_f1"], float), "avg_f1 must be float"
assert isinstance(summary["perfect_recall"], int), "perfect_recall must be int"
assert 0.0 <= summary["avg_precision"] <= 1.0, "avg_precision out of range"
assert 0.0 <= summary["avg_recall"] <= 1.0, "avg_recall out of range"
assert 0 <= summary["perfect_recall"] <= len(TEST_QUERIES), "perfect_recall out of range"

# Each result in report is a valid evaluate_query dict
for r in report["results"]:
    assert "query" in r and "f1" in r, f"Result entry missing fields: {r.keys()}"

print("PASS: RAGEvaluator complete. cosine_similarity + retrieve_top_k + evaluate_query + run() all verified.")
