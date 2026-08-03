# CUI // SP-CTI
"""Cortex intent router — map a chat message to the right Cortex facade.

The /cortex chat surface is a single front door over the eight Cortex facades.
Free-form chat only needs four of them; this module classifies a user message
into one of those four *intents* and returns which ``cortex.*`` call should
serve it:

    retrieval-ish    → cortex.search    ("find docs about…", "search the KB")
    data-question    → cortex.ask       ("how many…", "list all…", analyst)
    generative       → cortex.complete  ("write…", "draft…", "summarize…")
    multi-step goal  → cortex.agent     ("build X then deploy Y", orchestrate)

It is a THIN wrapper over ``tools.chat_router.intent_classifier.classify`` —
that classifier's mature keyword taxonomy detects requirements-intake and the
nine design-canvas modes (cam/ndc/sdc/…), all of which describe *building or
designing something*, i.e. a multi-step goal. We reuse it as the agent-intent
signal and layer four lightweight Cortex-intent rule sets on top for the
search / ask / complete distinction, rather than duplicating a second LLM
classifier here.

The ``agent`` intent is returned with ``requires_confirm=True``: the chat
surface must NOT auto-launch an agent loop / ACE team — it surfaces a confirm
affordance first (see blueprint ``api_chat``).

Boundary — three "classify" surfaces, deliberately kept separate
(cxo-adopt-07):

  1. ``tools.chat_router.intent_classifier`` — *which canvas* (intake or one of
     the nine design canvases). The only one of the three that reaches a
     provider: its low-confidence fallback goes through ``cortex_api.classify``
     and inherits the gateway pre-check, redaction, budget gate and audit row.
  2. THIS module — *which Cortex facade* (search / ask / complete / agent).
     Pure keyword rules layered on (1); it makes no provider call of its own and
     must not grow one. Routing a facade choice through an LLM would add a
     round-trip per turn and buy no governance the callee does not already
     apply.
  3. ``tools.rag.query_classifier`` (MCP ``query_classify``) — *what shape of
     answer* a RAG query needs. Fully deterministic; ``cortex_api.classify``
     degrades to exactly this classifier when no provider is reachable, which is
     why ``query_classify`` is not a TRUST-chain bypass and is not adopted.

Retrieval boundary: ``search_knowledge`` / ``rag_search`` / ``kg_search`` /
``dic_search`` overlap ``cortex_search``'s surface but are single-backend tools
carrying backend-specific knobs (``pattern_type``, ``source_type``, ``profile``,
``collection_id``) that ``cortex_search`` cannot express, and none of them sends
a prompt to a provider. Their registry descriptions point callers at
``cortex_search`` for cross-backend retrieval with citations; their handlers are
intentionally NOT retargeted. Output-side CUI egress to an external MCP client
is a gateway-wide concern owned by ``tools/gateway/security_chain.py``, not a
per-tool one. ``dic_chat`` is the one genuine LLM path in that cluster and is
already Cortex-adopted.
"""
from __future__ import annotations

import re
from typing import Any

# Cortex facade an intent maps onto (cortex.<facade>). Intent name == facade
# name for all four — kept as an explicit mapping so the two can diverge later.
INTENT_FACADES = {
    "search": "search",
    "ask": "ask",
    "complete": "complete",
    "agent": "agent",
}

DEFAULT_INTENT = "ask"  # grounded analyst path — safest default for a bare question


# Each rule set is (intent, [compiled patterns]). Patterns are matched
# case-insensitively against the whole message; a hit contributes one point.
def _c(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


# retrieval-ish — pull passages/documents/records from the corpora.
_SEARCH_RULES = _c([
    r"\b(search|find|look\s?up|lookup|retrieve|locate|surface)\b",
    r"\b(documents?|docs?|passages?|articles?|sources?|references?|papers?|"
    r"records?|files?)\s+(about|on|regarding|mentioning|related\s+to|for)\b",
    r"\bwhat\s+do(?:es)?\s+(?:our|the|my)\s+\w+\s+(?:say|mention|state)\b",
    r"\b(similar|related)\s+(to|documents?|content)\b",
    r"\bknowledge\s+base\b",
    r"\bcitations?\s+for\b",
    r"\bsemantic\s+search\b",
])

# data-question — a structured answer computed over rows in the platform DB.
_ASK_RULES = _c([
    r"\bhow\s+many\b",
    r"\b(count|number\s+of|total\s+number)\b",
    r"\blist\s+(all|the|every|our)\b",
    r"\bshow\s+(me\s+)?(all|the|every|our)\b",
    r"\b(which|what)\s+\w+\s+(have|has|are|is|were|had)\b",
    r"\b(average|avg|sum|max|maximum|min|minimum|median)\b",
    r"\btop\s+\d+\b",
    r"\b(rows?|tables?|records?|entries)\b",
    r"\bhow\s+much\b",
    r"\b(status|state)\s+of\b",
    r"\bgroup(ed)?\s+by\b",
    r"\bbreakdown\s+(of|by)\b",
])

# generative — produce new prose/content from the model.
_COMPLETE_RULES = _c([
    r"\b(write|draft|compose|author|rewrite|paraphrase|rephrase)\b",
    r"\b(summari[sz]e|summary\s+of|tl;?dr)\b",
    r"\b(generate|produce)\s+(a|an|some)?\s*\w*\s*"
    r"(paragraph|email|message|memo|letter|note|blurb|description|caption|"
    r"outline|essay|poem|story|tagline|copy|text|response|reply)\b",
    r"\b(translate|localize)\b",
    r"\bexplain\b",
    r"\b(brainstorm|come\s+up\s+with|suggest)\b",
    r"\bmake\s+(it|this)\s+(shorter|longer|clearer|formal|casual)\b",
])

# multi-step goal — chained actions / orchestration explicitly in the message.
# These are additive to the intent_classifier build/design signal below.
_AGENT_RULES = _c([
    r"\borchestrat",
    r"\bautomate\b",
    r"\bmulti[- ]?step\b",
    r"\bend[- ]to[- ]end\b",
    r"\bpipeline\s+that\b",
    r"\bworkflow\s+that\b",
    r"\bco-?worker\b",
    r"\b@team\b",
    r"\b(then|after\s+that|afterwards?|next)\b.{0,60}\b(then|deploy|generate|"
    r"build|create|run|send|publish|test)\b",
    r"\b(build|create|implement|develop|set\s?up|scaffold)\b.{0,60}\band\s+"
    r"(then\s+)?(deploy|test|publish|monitor|document|run)\b",
])

# The design-canvas modes the base classifier emits — every one is a
# "design/build something" request, i.e. a multi-step goal → agent.
_DESIGN_MODES = {"cam", "ndc", "sdc", "eda", "ddc", "pdc", "bdc", "odc", "idc"}


def _score(message: str, rules: list[re.Pattern]) -> int:
    return sum(1 for pat in rules if pat.search(message))


def _base_signal(message: str) -> dict[str, Any]:
    """Thin call into the shared chat intent classifier (best-effort)."""
    try:
        from tools.chat_router.intent_classifier import classify as _classify

        return _classify(message) or {}
    except Exception:  # noqa: BLE001 — classifier optional; router still works
        return {}


def route(message: str) -> dict[str, Any]:
    """Classify *message* into a Cortex intent and the facade that serves it.

    Returns::

        {
          "intent": "search" | "ask" | "complete" | "agent",
          "facade": "search" | "ask" | "complete" | "agent",
          "confidence": float,        # 0.0–1.0
          "reason": str,
          "requires_confirm": bool,   # True only for the agent intent
          "base_classifier": {...},   # raw intent_classifier result (provenance)
        }
    """
    text = (message or "").strip()
    base = _base_signal(text)
    if not text:
        return _decision(DEFAULT_INTENT, 1.0, "empty message", base)

    scores = {
        "search": _score(text, _SEARCH_RULES),
        "ask": _score(text, _ASK_RULES),
        "complete": _score(text, _COMPLETE_RULES),
        "agent": _score(text, _AGENT_RULES),
    }

    # The base classifier's build/design signal reinforces the agent intent:
    # a design-canvas mode, or intake with an explicit build phrasing, means
    # the user described something to build end-to-end.
    base_mode = base.get("mode")
    if base_mode in _DESIGN_MODES:
        scores["agent"] += 2

    best_intent = max(scores, key=lambda k: scores[k])
    best_score = scores[best_intent]

    if best_score == 0:
        # No Cortex-intent keyword fired. Fall back to the base classifier: a
        # design/build request routes to agent; anything else defaults to the
        # grounded analyst path.
        if base_mode in _DESIGN_MODES:
            return _decision(
                "agent",
                float(base.get("confidence", 0.6)),
                f"base classifier → {base_mode} (design/build goal)",
                base,
            )
        return _decision(DEFAULT_INTENT, 0.4, "no intent signal; default to ask", base)

    # Confidence scales with hit count (diminishing), capped at 0.95.
    confidence = min(0.5 + 0.15 * best_score, 0.95)
    reason = f"keyword match: {best_score} rule(s) for {best_intent}"
    return _decision(best_intent, round(confidence, 3), reason, base)


def _decision(intent: str, confidence: float, reason: str, base: dict) -> dict[str, Any]:
    return {
        "intent": intent,
        "facade": INTENT_FACADES[intent],
        "confidence": confidence,
        "reason": reason,
        "requires_confirm": intent == "agent",
        "base_classifier": base,
    }
