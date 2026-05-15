
# Auto-grader for T3 M5 Step 1: Canvas Selection

# ── Test: score_canvas ────────────────────────────────────────────────────────

# NDC description — high network overlap
ndc_desc = "Monitor packet loss across network segments, track router interfaces and bandwidth"
ndc_score = score_canvas("NDC", ndc_desc)
assert ndc_score is not None, "score_canvas() returned None"
assert isinstance(ndc_score, float), f"score_canvas() must return float, got {type(ndc_score)}"
assert 0.0 <= ndc_score <= 1.0, f"score must be in [0.0, 1.0], got {ndc_score}"
assert ndc_score > 0.0, f"NDC description should score >0 against NDC canvas, got {ndc_score}"

# BDC description — high business overlap
bdc_desc = "Track contract awards, vendor invoices, and procurement spending on SAM.gov"
bdc_score = score_canvas("BDC", bdc_desc)
assert bdc_score > 0.0, f"BDC description should score >0 against BDC, got {bdc_score}"

# NDC description should score LOWER against BDC
ndc_vs_bdc = score_canvas("BDC", ndc_desc)
assert ndc_vs_bdc < ndc_score, \
    f"NDC description should score higher on NDC than BDC ({ndc_score:.3f} vs {ndc_vs_bdc:.3f})"

# Unknown canvas code → 0.0 (no raise)
unknown_score = score_canvas("XYZ", "any description")
assert unknown_score == 0.0, f"Unknown canvas code → 0.0, got {unknown_score}"

# Empty description → 0.0
empty_score = score_canvas("NDC", "")
assert empty_score == 0.0, f"Empty description → 0.0, got {empty_score}"

# ── Test: explain_canvas ──────────────────────────────────────────────────────

for code in ["NDC", "SDC", "PDC", "BDC", "DDC", "ODC", "IDC"]:
    explanation = explain_canvas(code)
    assert explanation is not None, f"explain_canvas('{code}') returned None"
    assert isinstance(explanation, str), f"explain_canvas() must return str"
    assert len(explanation) > 5, f"Explanation too short for {code}: '{explanation}'"

# Unknown canvas
unknown_exp = explain_canvas("XYZ")
assert "unknown" in unknown_exp.lower() or "xyz" in unknown_exp.lower(), \
    f"Unknown canvas should say 'unknown', got '{unknown_exp}'"

# ── Test: CanvasSelector.select ───────────────────────────────────────────────

selector = CanvasSelector()

# Network description → NDC
r_ndc = selector.select("Monitor packet loss across DoD network segments and routing tables")
assert r_ndc is not None, "select() returned None"
assert isinstance(r_ndc, dict), f"select() must return dict"
assert "canvas" in r_ndc, "Result must have 'canvas'"
assert "name" in r_ndc, "Result must have 'name'"
assert "confidence" in r_ndc, "Result must have 'confidence'"
assert "reasoning" in r_ndc, "Result must have 'reasoning'"
assert "all_scores" in r_ndc, "Result must have 'all_scores'"
assert r_ndc["canvas"] == "NDC", \
    f"Network description → NDC, got '{r_ndc['canvas']}' (all_scores={r_ndc['all_scores']})"
assert isinstance(r_ndc["confidence"], float), "confidence must be float"
assert 0.0 <= r_ndc["confidence"] <= 1.0, f"confidence out of range: {r_ndc['confidence']}"
assert "all_scores" in r_ndc and len(r_ndc["all_scores"]) == 7, \
    f"all_scores must have 7 entries (one per canvas)"

# Compliance/docs description → DDC
r_ddc = selector.select("Build a RAG system over STIG documents and NIST 800-53 compliance controls")
assert r_ddc["canvas"] == "DDC", \
    f"Compliance docs → DDC, got '{r_ddc['canvas']}' (scores={r_ddc['all_scores']})"

# Pipeline/automation description → ODC
r_odc = selector.select("Automate CI/CD pipeline workflow with build, test, deploy stages and job queue")
assert r_odc["canvas"] == "ODC", \
    f"Pipeline automation → ODC, got '{r_odc['canvas']}' (scores={r_odc['all_scores']})"

# No-match description → IDC (safe default)
r_default = selector.select("the quick brown fox jumped over the lazy dog")
assert r_default["canvas"] == "IDC", \
    f"No-match description → IDC (default), got '{r_default['canvas']}'"
assert r_default["confidence"] == 0.0, \
    f"Zero-match confidence should be 0.0, got {r_default['confidence']}"

# ── Test: CanvasSelector.rank_canvases ────────────────────────────────────────

ranked = selector.rank_canvases("Monitor packet loss across network segments and router interfaces")
assert ranked is not None, "rank_canvases() returned None"
assert isinstance(ranked, list), f"rank_canvases() must return list"
assert len(ranked) == 7, f"rank_canvases must return all 7 canvases, got {len(ranked)}"
for item in ranked:
    assert "canvas" in item, f"Each ranked item must have 'canvas', got: {item}"
    assert "name" in item, f"Each ranked item must have 'name'"
    assert "score" in item, f"Each ranked item must have 'score'"
    assert isinstance(item["score"], float), "score must be float"

# Scores should be descending
for i in range(len(ranked) - 1):
    assert ranked[i]["score"] >= ranked[i+1]["score"], \
        f"rank_canvases must be sorted descending: [{i}]={ranked[i]['score']} < [{i+1}]={ranked[i+1]['score']}"

assert ranked[0]["canvas"] == "NDC", \
    f"Top canvas for network description should be NDC, got '{ranked[0]['canvas']}'"

print("PASS: CanvasSelector complete. score_canvas + explain_canvas + select + rank_canvases all verified.")
