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

A message that describes a GRAPH — ordered steps with conditions, branches that
run in parallel, a named approval gate, a named workflow template — additionally
carries ``agent_mode="graph"`` and a ``graph_signal`` block naming which of those
four families fired (hgx-cx-02). ``cortex.agent``'s ``"auto"`` mode can never
select graph on its own (a graph run names a workflow, and that cannot be
inferred), so without this hint a user who described a DAG was silently handed a
single agent loop. ``requires_confirm`` stays True regardless — a graph launch
starts a durable run holding per-node tool authorizations, which is the last
thing that should begin from an unconfirmed chat message.

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


# ---------------------------------------------------------------------------
# Graph-shaped signal (hgx-cx-02)
# ---------------------------------------------------------------------------
# _AGENT_RULES above detect "this is more than one step". These detect
# something narrower and more useful: the user has described a SHAPE — a DAG.
# Four families, because a graph is exactly these four things and nothing the
# _AGENT_RULES catch implies any of them:
#
#   sequence   ordered steps, and steps CONDITIONAL on an earlier outcome
#   parallel   branches that run at the same time (a DAG, not a list)
#   gate       a named point where a human approves before it continues
#   template   a workflow that already exists and is being named
#
# Why this matters rather than being a nicety: cortex.agent's "auto" mode will
# NEVER select graph — a graph run names a workflow and that cannot be inferred,
# so auto falls through to team or single. A user who described a DAG and got a
# single agent loop got the wrong runtime silently. The decision therefore
# carries agent_mode="graph" as a HINT, and the confirm affordance is what turns
# a hint into a launch. requires_confirm stays True either way (see _decision):
# a graph launch starts a durable, resumable run holding per-node tool
# authorizations, which is the LAST thing that should start from a chat message
# nobody confirmed.
_GRAPH_SEQUENCE_RULES = _c([
    r"\bif\b.{0,80}\bthen\b",
    r"\b(only\s+)?(if|when|once|after)\s+(it|that|they|the\s+\w+)\s+"
    r"(pass(es)?|fail(s)?|succeed(s)?|complet(es?|ed)|finish(es|ed)?)\b",
    r"\bon\s+(success|failure|error|approval|completion)\b",
    r"\b(otherwise|else)\b.{0,60}\b(stop|halt|skip|retry|rollback|notify|fail)\b",
    r"\bstep\s*\d+\b",
    r"\bstage\s+(one|two|three|\d+)\b",
    r"\b(first|firstly)\b.{0,120}\b(then|next|finally|lastly)\b",
    r"\bdepends?\s+on\s+(the\s+)?(previous|prior|first|earlier)\b",
    r"\bretry\b.{0,40}\b(if|on|when)\b",
])

_GRAPH_PARALLEL_RULES = _c([
    r"\bin\s+parallel\b",
    r"\bparallel\s+(branch|step|node|path|track|run)",
    r"\bconcurrent(ly)?\b",
    r"\bsimultaneous(ly)?\b",
    r"\bat\s+the\s+same\s+time\b",
    r"\bfan[- ]?(out|in)\b",
    r"\bside[- ]by[- ]side\b",
])

_GRAPH_GATE_RULES = _c([
    r"\b(approval|sign[- ]?off|review)\s+gate\b",
    r"\bgate\s+(called|named|for)\b",
    r"\b(human|manual|human[- ]in[- ]the[- ]loop|hitl)\s+"
    r"(approval|review|gate|check|sign[- ]?off)\b",
    r"\b(wait|pause|hold|block)\s+for\s+(\w+\s+){0,3}"
    r"(approval|sign[- ]?off|review|confirmation)\b",
    r"\b(require|need)s?\s+(\w+\s+){0,3}(approval|sign[- ]?off)\b",
    r"\bapprov(al|ed|es)\s+(by|from)\s+\w+",
])

_GRAPH_TEMPLATE_RULES = _c([
    r"\b(run|start|launch|kick\s?off|execute|trigger)\s+(the\s+)?"
    r"[\w.-]+\s+(workflow|pipeline|template|graph|dag)\b",
    r"\b(workflow|pipeline)\s+template\b",
    r"\b(using|with|from)\s+the\s+[\w.-]+\s+(workflow|template)\b",
    r"\bworkflow[_ ]id\b",
    r"\b[\w-]+\.(ya?ml)\b.{0,20}\b(workflow|pipeline|template)\b",
    r"\b(dag|directed\s+acyclic\s+graph)\b",
    r"\bstudio\s+(workflow|run|graph)\b",
])

_GRAPH_FAMILIES = (
    ("sequence", _GRAPH_SEQUENCE_RULES),
    ("parallel", _GRAPH_PARALLEL_RULES),
    ("gate", _GRAPH_GATE_RULES),
    ("template", _GRAPH_TEMPLATE_RULES),
)

# How many distinct families must fire before the message is called graph-shaped.
# TWO, not one — deliberately. "explain the approval gate for FedRAMP" hits the
# gate family and is a question about a gate, not a request to build one; "run
# the report pipeline" hits template and is one step. Requiring two independent
# families is what separates a description of a DAG from a passing mention of a
# word that appears in one.
_GRAPH_MIN_FAMILIES = 2


def _score(message: str, rules: list[re.Pattern]) -> int:
    return sum(1 for pat in rules if pat.search(message))


def graph_signal(message: str) -> dict[str, Any]:
    """Which graph families *message* exhibits, and whether that is a DAG.

    Returns ``{"families": [...], "hits": {family: n}, "is_graph": bool}``.
    Exposed (not private) because the confirm affordance shows the user WHY a
    graph run is being proposed — "you described parallel branches and an
    approval gate" is an explanation; "confidence 0.8" is not.
    """
    text = message or ""
    hits = {name: _score(text, rules) for name, rules in _GRAPH_FAMILIES}
    families = sorted(name for name, n in hits.items() if n)
    return {
        "families": families,
        "hits": {name: n for name, n in hits.items() if n},
        "is_graph": len(families) >= _GRAPH_MIN_FAMILIES,
    }


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
    graph = graph_signal(text)
    if not text:
        return _decision(DEFAULT_INTENT, 1.0, "empty message", base, graph)

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

    # A described DAG is a multi-step goal by construction. +2 — enough to win
    # against a single incidental keyword from another family (a graph
    # description says "generate the report", which is a _COMPLETE_RULES hit),
    # not enough to override a message that is emphatically something else.
    if graph["is_graph"]:
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
                graph,
            )
        return _decision(DEFAULT_INTENT, 0.4, "no intent signal; default to ask",
                         base, graph)

    # Confidence scales with hit count (diminishing), capped at 0.95.
    confidence = min(0.5 + 0.15 * best_score, 0.95)
    reason = f"keyword match: {best_score} rule(s) for {best_intent}"
    if best_intent == "agent" and graph["is_graph"]:
        reason += f"; graph-shaped ({', '.join(graph['families'])})"
    return _decision(best_intent, round(confidence, 3), reason, base, graph)


def _decision(intent: str, confidence: float, reason: str, base: dict,
              graph: dict | None = None) -> dict[str, Any]:
    graph = graph or {"families": [], "hits": {}, "is_graph": False}
    return {
        "intent": intent,
        "facade": INTENT_FACADES[intent],
        "confidence": confidence,
        "reason": reason,
        # ALWAYS True for the agent intent, graph-shaped or not. A graph launch
        # starts a durable, resumable run whose nodes hold their own tool
        # authorizations — the strongest reason to confirm, never a reason to skip.
        "requires_confirm": intent == "agent",
        # The mode to PROPOSE on the confirm card. cortex.agent's "auto" never
        # picks graph (a graph run names a workflow; that cannot be inferred), so
        # without this hint a user who described a DAG would be handed a single
        # agent loop and told nothing about it.
        "agent_mode": "graph" if (intent == "agent" and graph["is_graph"]) else "auto",
        "graph_signal": graph,
        "base_classifier": base,
    }
