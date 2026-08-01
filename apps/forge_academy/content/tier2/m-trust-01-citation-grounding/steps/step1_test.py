
# Auto-grader — TRUST: attribution, confidence, provenance, fail-closed egress

# ── attribution_score (evidence recall) ───────────────────────────────────────
assert attribution_score("alpha beta gamma", "alpha beta gamma delta") == 1.0
assert attribution_score("alpha beta gamma", "alpha beta") == 0.6667
assert attribution_score("alpha beta", "gamma delta") == 0.0
assert attribution_score("", "anything here") == 0.0, "empty chunk cannot be attributed"
# case-insensitive
assert attribution_score("Alpha BETA", "alpha beta gamma") == 1.0

# ── classify_confidence (include / flag / abstain) ────────────────────────────
assert classify_confidence(0.9) == "include"
assert classify_confidence(0.7) == "include", "0.7 is the include floor"
assert classify_confidence(0.69) == "flag"
assert classify_confidence(0.4) == "flag", "0.4 is the abstain ceiling (flag)"
assert classify_confidence(0.39) == "abstain"
assert classify_confidence(0.0) == "abstain"

# ── build_provenance ──────────────────────────────────────────────────────────
p = build_provenance("kb-1", "abc123", 0.75)
assert p == {"source_id": "kb-1", "sha256": "abc123",
             "classification": "CUI", "attribution_score": 0.75}, f"unexpected: {p}"
assert build_provenance("x", "y", 0.1, classification="SECRET")["classification"] == "SECRET"
# default attribution is 0.0
assert build_provenance("x", "y")["attribution_score"] == 0.0

# ── egress_gate (fail-closed) ─────────────────────────────────────────────────
ok = egress_gate(True)
assert ok == {"allowed": True, "reason": "sanitized", "audited": False}, f"unexpected: {ok}"

# sanitizer down + fail-closed default -> BLOCK
blocked = egress_gate(False)
assert blocked["allowed"] is False
assert blocked["reason"] == "redaction_unavailable"

# forced override is allowed but audited
forced = egress_gate(False, force=True)
assert forced == {"allowed": True, "reason": "override_audited", "audited": True}, f"unexpected: {forced}"

# explicit fail-open lets it through unaudited
open_ = egress_gate(False, fail_closed=False)
assert open_["allowed"] is True and open_["reason"] == "fail_open" and open_["audited"] is False

print("PASS: TRUST attribution, confidence banding, provenance record, and fail-closed egress verified.")
