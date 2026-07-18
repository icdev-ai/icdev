
# Auto-grader — Cortex: route + govern

# ── classify_intent ───────────────────────────────────────────────────────────
assert classify_intent("search the KB for STIG findings") == "search"
assert classify_intent("find the latest CVE report") == "search"
assert classify_intent("classify this document") == "classify"
assert classify_intent("extract the vendor fields from this invoice") == "extract"
assert classify_intent("draft a summary of the incident") == "complete"
assert classify_intent("redact the PII before egress") == "govern"
# No signal → general ask default
assert classify_intent("why did the deployment fail last night?") == "ask"
assert classify_intent("") == "ask"

# ── govern ────────────────────────────────────────────────────────────────────
src = {"text": "hello"}
env = govern(src)
assert env["classification"] == "CUI", f"default classification should be CUI, got {env['classification']}"
assert env["governed"] is True
assert env["result"] == {"text": "hello"}
assert src == {"text": "hello"}, "govern() must not mutate its input"
assert govern({"x": 1}, "SECRET")["classification"] == "SECRET"

# ── CortexFacade.invoke ───────────────────────────────────────────────────────
cortex = CortexFacade()

r = cortex.invoke("search the corpus for hardening guidance")
assert r["capability"] == "search", f"expected search, got {r['capability']}"
assert r["governed"] is True
assert r["classification"] == "CUI"
assert isinstance(r["result"], dict)
assert r["result"].get("backend") == "rag"

r2 = cortex.invoke("what is the ATO boundary?")
assert r2["capability"] == "ask", f"unmatched request should route to ask, got {r2['capability']}"
assert "answer" in r2["result"]

r3 = cortex.invoke("classify this file", classification="SECRET")
assert r3["capability"] == "classify"
assert r3["classification"] == "SECRET"

print("PASS: Cortex facade routes intents and governs every response.")
