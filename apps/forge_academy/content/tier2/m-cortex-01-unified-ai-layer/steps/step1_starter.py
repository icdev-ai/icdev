
"""
Tier 2 — ICDEV Cortex: The Unified AI Layer
Goal: Build a miniature CortexFacade that routes a request to the right capability
      and wraps every response in a governance envelope.

Real Cortex (tools/cortex/, MCP tools cortex_complete / cortex_classify /
cortex_extract / cortex_search / cortex_ask / cortex_govern / cortex_agent_launch)
is ONE governed facade over LLMRouter, RAG, the Knowledge Graph, DIC documents, and
keyword search. Callers ask Cortex instead of wiring five backends by hand, and every
answer passes through governance. This exercise models that dispatch + governance
wrap with plain Python (no real LLM calls).
"""

# The Cortex capability surface (mirrors the real cortex_* MCP tools).
CAPABILITIES = ("complete", "classify", "extract", "search", "ask", "govern")

# Keyword signals that route a natural-language request to a capability.
INTENT_SIGNALS = {
    "search":   ("search", "find", "retrieve", "look up", "lookup"),
    "extract":  ("extract", "pull out", "parse", "fields from"),
    "classify": ("classify", "categorize", "categorise", "label", "what type"),
    "complete": ("complete", "draft", "write", "generate", "continue"),
    "govern":   ("redact", "govern", "check policy", "compliance"),
}

# Stub capability handlers. In real Cortex these call RAG / KG / LLMRouter; here they
# return canned payloads so the exercise stays offline and deterministic.
HANDLERS = {
    "complete": lambda req: {"text": f"draft for: {req}"},
    "classify": lambda req: {"label": "unknown", "confidence": 0.5},
    "extract":  lambda req: {"fields": {}},
    "search":   lambda req: {"hits": [], "backend": "rag"},
    "ask":      lambda req: {"answer": f"answer to: {req}", "sources": []},
    "govern":   lambda req: {"policy": "cui", "allowed": True},
}


# ── Step 1: Intent classifier ─────────────────────────────────────────────────

def classify_intent(request: str) -> str:
    """TODO: Map a natural-language request to a Cortex capability.

    1. Lowercase the request.
    2. For each capability in INTENT_SIGNALS, if ANY of its signal phrases appears
       in the request, return that capability. Check in INTENT_SIGNALS order.
    3. If nothing matches, return "ask" (the general question-answering default).
    """
    # YOUR CODE HERE
    pass


# ── Step 2: Governance envelope ───────────────────────────────────────────────

def govern(response: dict, classification: str = "CUI") -> dict:
    """TODO: Wrap a capability response in a Cortex governance envelope.

    Return a NEW dict:
        {
            "classification": classification,   # e.g. "CUI"
            "governed": True,
            "result": response,                 # the original response dict
        }
    Do not mutate the input dict.
    """
    # YOUR CODE HERE
    pass


# ── Step 3: The facade ────────────────────────────────────────────────────────

class CortexFacade:
    """One entry point: classify the request, dispatch to a handler, then govern."""

    def invoke(self, request: str, classification: str = "CUI") -> dict:
        """TODO: Route a request end-to-end.

        1. capability = classify_intent(request)
        2. handler = HANDLERS[capability]   (all capabilities have a handler)
        3. raw = handler(request)
        4. enveloped = govern(raw, classification)
        5. Add "capability": capability to the enveloped dict and return it.

        Return shape:
            {"capability": <str>, "classification": <str>, "governed": True,
             "result": <handler output dict>}
        """
        # YOUR CODE HERE
        pass


# Demo
if __name__ == "__main__":
    cortex = CortexFacade()
    for req in [
        "search the knowledge base for STIG findings",
        "classify this document",
        "extract the vendor fields from this invoice",
        "draft a summary of the incident",
        "why did the deploy fail?",
    ]:
        out = cortex.invoke(req)
        print(f"{out['capability']:9s} -> {out['result']}")
