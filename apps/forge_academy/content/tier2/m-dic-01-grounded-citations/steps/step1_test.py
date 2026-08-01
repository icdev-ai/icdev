
# Auto-grader — DIC grounded citations

# ── extract_citations ─────────────────────────────────────────────────────────
t = "Sky is blue [source: doc1]. Grass is green [source:doc2]. See [source:  Doc 3 ]."
cites = extract_citations(t)
assert cites == ["doc1", "doc2", "Doc 3"], f"unexpected: {cites}"
assert extract_citations("no markers here") == []
assert extract_citations("") == []
# duplicates preserved
assert extract_citations("[source: a] then [source: a]") == ["a", "a"]

# ── validate_citations ────────────────────────────────────────────────────────
evidence = {"doc1", "doc2"}
v = validate_citations("A [source: doc1] and B [source: doc9]", evidence)
assert v["cited"] == ["doc1", "doc9"]
assert v["grounded"] == ["doc1"]
assert v["ungrounded"] == ["doc9"]
assert v["ok"] is False, "an ungrounded citation must make ok False"

v2 = validate_citations("All good [source: doc1][source: doc2]", evidence)
assert v2["ungrounded"] == []
assert v2["ok"] is True

# text with zero citations is NOT ok (nothing is grounded)
v3 = validate_citations("bare assertion, no source", evidence)
assert v3["cited"] == []
assert v3["ok"] is False

# ── citation_guard (fail-closed gate) ─────────────────────────────────────────
g_ok = citation_guard("Enable MFA [source: doc1].", evidence)
assert g_ok["passed"] is True, f"grounded text should pass: {g_ok}"
assert g_ok["defects"] == []

g_none = citation_guard("Fully compliant. No findings.", evidence)
assert g_none["passed"] is False
assert any("no citations" in d for d in g_none["defects"])

g_bad = citation_guard("Compliant [source: fake-9].", evidence)
assert g_bad["passed"] is False
assert any("ungrounded citation" in d and "fake-9" in d for d in g_bad["defects"])

print("PASS: DIC citation guard parses [source:] markers and fails closed on ungrounded claims.")
