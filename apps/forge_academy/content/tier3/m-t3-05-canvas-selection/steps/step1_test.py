
# Auto-grader for T3 M5 Step 1: Canvas Selection
# Canvases are a subset of args/component_registry.yaml (Design Canvas family).

# ── Test: score_canvas ────────────────────────────────────────────────────────

# NDC description — high network overlap
ndc_desc = "Design redundant WAN routing, size link capacity, and lay out subnets"
ndc_score = score_canvas("NDC", ndc_desc)
assert ndc_score is not None, "score_canvas() returned None"
assert isinstance(ndc_score, float), f"score_canvas() must return float, got {type(ndc_score)}"
assert 0.0 <= ndc_score <= 1.0, f"score must be in [0.0, 1.0], got {ndc_score}"
assert ndc_score > 0.0, f"NDC description should score >0 against NDC canvas, got {ndc_score}"

# SDC description — high security overlap
sdc_desc = "Threat model the system, apply STIG hardening, and map attack-path exposure to CVEs"
sdc_score = score_canvas("SDC", sdc_desc)
assert sdc_score > 0.0, f"SDC description should score >0 against SDC, got {sdc_score}"

# NDC description should score LOWER against SDC than against NDC
ndc_vs_sdc = score_canvas("SDC", ndc_desc)
assert ndc_vs_sdc < ndc_score, \
    f"NDC description should score higher on NDC than SDC ({ndc_score:.3f} vs {ndc_vs_sdc:.3f})"

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
    assert isinstance(explanation, str), "explain_canvas() must return str"
    assert len(explanation) > 5, f"Explanation too short for {code}: '{explanation}'"

# Unknown canvas
unknown_exp = explain_canvas("XYZ")
assert "unknown" in unknown_exp.lower() or "xyz" in unknown_exp.lower(), \
    f"Unknown canvas should say 'unknown', got '{unknown_exp}'"

# ── Test: CanvasSelector.select — one description per Design Canvas ────────────

selector = CanvasSelector()

cases = {
    "NDC": "Design redundant WAN routing and size link capacity across subnets",
    "SDC": "Threat model the system, apply STIG hardening, and map attack-path exposure to CVEs",
    "PDC": "Build a GitLab CI/CD pipeline with worktree isolation and build and deploy stages, publishing an artifact on merge",
    "BDC": "Assess the ATO boundary impact and supply chain SCRM for a new vendor interconnection and ISA",
    "DDC": "Model the data schema, track data lineage, and generate synthetic datasets for quality checks",
    "ODC": "Add distributed tracing, structured logging, and SLO monitoring dashboards for SRE telemetry",
    "IDC": "Provision cloud infrastructure with Terraform and Kubernetes manifests targeting an AWS cluster",
}

expected_names = {
    "NDC": "Network Design Canvas", "SDC": "Security Design Canvas",
    "PDC": "Pipeline Design Canvas", "BDC": "Boundary & Supply Chain Canvas",
    "DDC": "Data Design Canvas", "ODC": "Observability Design Canvas",
    "IDC": "Infrastructure Design Canvas",
}

for expected, desc in cases.items():
    r = selector.select(desc)
    assert r is not None, "select() returned None"
    assert isinstance(r, dict), "select() must return dict"
    for key in ("canvas", "name", "confidence", "reasoning", "all_scores"):
        assert key in r, f"Result must have '{key}' (desc={desc!r})"
    assert isinstance(r["confidence"], float), "confidence must be float"
    assert 0.0 <= r["confidence"] <= 1.0, f"confidence out of range: {r['confidence']}"
    assert len(r["all_scores"]) == 7, "all_scores must have 7 entries (one per canvas)"
    assert r["canvas"] == expected, \
        f"'{desc}' → expected {expected}, got '{r['canvas']}' (all_scores={r['all_scores']})"
    assert r["name"] == expected_names[expected], \
        f"name mismatch for {expected}: got '{r['name']}'"

# No-match description → NONE sentinel (do not guess; consult the registry)
r_none = selector.select("the quick brown fox jumped over the lazy dog")
assert r_none["canvas"] == "NONE", \
    f"No-match description → canvas 'NONE' (consult registry), got '{r_none['canvas']}'"
assert r_none["confidence"] == 0.0, \
    f"Zero-match confidence should be 0.0, got {r_none['confidence']}"
assert "all_scores" in r_none, "NONE result must still expose all_scores"

# ── Test: CanvasSelector.rank_canvases ────────────────────────────────────────

ranked = selector.rank_canvases("Design redundant WAN routing and size link capacity across subnets")
assert ranked is not None, "rank_canvases() returned None"
assert isinstance(ranked, list), "rank_canvases() must return list"
assert len(ranked) == 7, f"rank_canvases must return all 7 canvases, got {len(ranked)}"
for item in ranked:
    assert "canvas" in item, f"Each ranked item must have 'canvas', got: {item}"
    assert "name" in item, "Each ranked item must have 'name'"
    assert "score" in item, "Each ranked item must have 'score'"
    assert isinstance(item["score"], float), "score must be float"

# Scores should be descending
for i in range(len(ranked) - 1):
    assert ranked[i]["score"] >= ranked[i+1]["score"], \
        f"rank_canvases must be sorted descending: [{i}]={ranked[i]['score']} < [{i+1}]={ranked[i+1]['score']}"

assert ranked[0]["canvas"] == "NDC", \
    f"Top canvas for network description should be NDC, got '{ranked[0]['canvas']}'"

print("PASS: CanvasSelector complete. score_canvas + explain_canvas + select + rank_canvases all verified.")
