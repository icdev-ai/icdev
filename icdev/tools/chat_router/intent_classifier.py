# CUI // SP-CTI
"""Intent classifier — maps a user message to a chat canvas mode.

Fast path: keyword rules.
Fallback: the governed ``tools.cortex.api.classify`` facade when confidence is
low. The message is USER-AUTHORED chat text, so it must not reach a provider
without the gateway pre-check, redaction, budget/rate gate, provider fallback
chain and append-only audit row that ``LLMRouter.invoke`` applies.

Returns:
    {
        "mode": "intake" | "cam" | "ndc" | "sdc" | "eda" | "ddc" | "pdc" | "bdc" | "odc" | "idc",
        "canvas_type": str | None,   # None when mode == "intake"
        "confidence": float,         # 0.0–1.0
        "reason": str
    }

Boundary — there are three distinct "classify" surfaces; do not merge them
(cxo-adopt-07):

  1. THIS module — *which canvas* a chat message belongs to (intake or one of
     the nine design canvases). It is an LLM call path on the low-confidence
     fallback, so it goes through ``cortex_api.classify`` and inherits the
     gateway pre-check, redaction, budget gate and audit row.
  2. ``tools.cortex.intent_router`` — *which Cortex facade* serves a message
     (search / ask / complete / agent). Deterministic keyword rules that consume
     THIS module's output as the agent-intent signal. No provider call of its
     own; see that module's docstring.
  3. ``tools.rag.query_classifier`` (MCP ``query_classify``) — *what shape of
     answer* a RAG query needs (fact_single / summary / reasoning /
     unanswerable). Fully deterministic, no provider; ``cortex_api.classify``
     degrades to this same classifier when no provider is reachable.

Only (1) reaches a provider, so only (1) is TRUST-chain relevant. (2) and (3)
are rule engines and must NOT be retargeted onto a governed LLM facade —
doing so buys no governance and costs a provider round-trip per turn.

The related retrieval boundary: ``search_knowledge`` / ``rag_search`` /
``kg_search`` / ``dic_search`` are single-backend MCP retrieval tools whose
schemas expose backend-specific knobs (``pattern_type``, ``source_type``,
``profile``, ``collection_id``) that ``cortex_search`` has no equivalent for.
They are not LLM call paths either. Prefer ``cortex_search`` for cross-backend
retrieval with citations; retargeting the four onto it would be a lossy
contract change, not a consolidation. CUI egress from any of them to an
external MCP client is a gateway concern — it belongs in
``tools/gateway/security_chain.py``, which already wraps all gateway traffic.
"""
from __future__ import annotations

import re
from typing import Any

CANVAS_MODES = {"cam", "ndc", "sdc", "eda", "ddc", "pdc", "bdc", "odc", "idc"}
INTAKE_MODE = "intake"

# cortex_api.classify's documented marker for its deterministic degradation path
# (see tools/cortex/api.py::classify — provider="deterministic").
_CORTEX_FALLBACK_PROVIDER = "deterministic"

# Labels handed to cortex_api.classify, and the mode each maps back to.
#
# classify() puts the label list verbatim in the prompt, so the bare mode codes
# carry no meaning to a model and the answer is close to noise — measured against
# the configured provider, "translate this paragraph into Spanish" came back as
# "idc" and "write a summary of the Q3 report" as "eda". These glosses restore
# the semantics the old hand-rolled prompt spelled out.
#
# _NO_CANVAS_LABEL is the none-of-the-above escape hatch. classify() forces a
# choice among the labels, so without it every off-topic message is assigned some
# canvas; with it, the same messages come back as no-canvas and stay in intake.
_NO_CANVAS_LABEL = "none-of-the-above"
_LABEL_TO_MODE: dict[str, str] = {
    _NO_CANVAS_LABEL: INTAKE_MODE,
    "requirements-intake": INTAKE_MODE,
    "cloud-migration": "cam",
    "network-design": "ndc",
    "security-design": "sdc",
    "data-architecture": "eda",
    "database-design": "ddc",
    "process-design": "pdc",
    "business-design": "bdc",
    "observability-design": "odc",
    "infrastructure-design": "idc",
}

# Each entry: (canvas_type, score_weight, [keyword_patterns])
# Patterns are matched case-insensitively against the full message.
_RULES: list[tuple[str, float, list[str]]] = [
    # --- Migration / CAM ---
    ("cam", 0.95, [
        r"\bmigrat",
        r"\brefactor\b",
        r"\borganic\s+to\s+cloud\b",
        r"\bmove\s+to\s+(aws|azure|gcp|cloud)",
        r"\bcloud\s+migration\b",
        r"\beol\b",
        r"\bend.of.life\b",
        r"\bdeprecated?\b",
        r"\bsunsett?",
        r"\blegacy\s+(app|system|code|database|db)\b",
        r"\bstrangler\s+fig\b",
        r"\b7rs?\b",
        r"\blift.and.shift\b",
        r"\boracle\b",
        r"\boracle\s+to\b",
        r"\bmigrate.*\b(oracle|mysql|postgres|mongodb|sql.server|elasticsearch)\b",
        r"\b(oracle|mysql|elasticsearch|ruby|java\s*[678])\b.*\b(migrat|upgrad|deprecat|eol)\b",
        r"\bpostgres\s+migration\b",
        r"\bmysql\s+to\b",
        r"\bjava\s*8\b.*migrat",
        r"\bspring\s+boot\s+\d",
        r"\bmoderniz",
        r"\bupgrad.*database\b",
        r"\bcoa\b",
        r"\bcourse.*of.*action\b",
        r"\brefactor.*job\b",
    ]),

    # --- Network Design / NDC ---
    ("ndc", 0.90, [
        r"\bnetwork\s+(topology|design|diagram|segment)\b",
        r"\bnetwork\s+architecture\b.*\b(firewall|vlan|router|subnet|dmz|acl)\b",
        r"\bfirewall\s+(design|rule|policy|architecture)\b",
        r"\bvlan\s+(design|segmentation|trunking|tagging)\b",
        r"\bsubnet\s+(design|mask|cidr|allocation)\b",
        r"\brouter\s+(config|design|protocol|bgp|ospf)\b",
        r"\bload\s+balanc.*\b(design|architect|config)\b",
        r"\bdns\s+(config|design|setup)\b",
        r"\bvpn\b.*design",
        r"\bppsm\b",
        r"\bnetwork\s+access\s+control\b",
        r"\bwireless\s+(design|architecture)\b",
        r"\blan\b.*design",
        r"\bwan\b.*design",
        r"\bsdwan\b",
        r"\bzero.trust.network\b",
        r"\bnetwork\s+segmentation\b",
        r"\bblast\s+radius\b",
        r"\bredundancy\b.*\b(network|wan|link)\b",
        r"\bnetwork\s+(topology|map|diagram)\b",
        r"\b(bgp|ospf|eigrp|isis)\s+(routing|design|peering)\b",
        r"\bdmz\s+(design|architecture|zone)\b",
    ]),

    # --- Security Design / SDC ---
    ("sdc", 0.90, [
        r"\bsecurity\s+(architecture|design|model)\b",
        r"\bthreat\s+model",
        r"\bapi\s+security\b",
        r"\bauthentication\s+design\b",
        r"\bauthorization\s+(model|design|flow)\b",
        r"\biam\s+(design|architecture)\b",
        r"\boauth\b.*design",
        r"\boidc\b.*design",
        r"\bsecurity\s+control",
        r"\bnist\s+800",
        r"\bstig\s+design\b",
        r"\bfips\b.*design",
        r"\bzero\s+trust\s+(architecture|design)\b",
        r"\bpentest\s+architecture\b",
        r"\bsecurity\s+hardening\b",
        r"\bencryption\s+design\b",
        r"\bpki\s+(design|architecture)\b",
    ]),

    # --- Enterprise Data Architecture / EDA ---
    ("eda", 0.88, [
        r"\bdata\s+pipeline\b",
        r"\bevent\s+(streaming|driven|catalog)\b",
        r"\bkafka\b",
        r"\bdata\s+flow\b",
        r"\betl\b",
        r"\belt\b",
        r"\bdata\s+ingestion\b",
        r"\bdata\s+lake\b",
        r"\bdata\s+warehouse\s+design\b",
        r"\bstream\s+processing\b",
        r"\bmessage\s+bus\b",
        r"\bpub.sub\b",
        r"\bapache\s+(spark|flink|beam)\b",
        r"\bdata\s+architecture\b",
        r"\bevent\s+sourcing\b",
        r"\bcqrs\b",
    ]),

    # --- Database Design / DDC ---
    ("ddc", 0.88, [
        r"\bdatabase\s+(schema|design|model)\b",
        r"\ber\s+diagram\b",
        r"\bentity.relationship\b",
        r"\bdata\s+model\b",
        r"\bnormali[sz]ation\b",
        r"\bindex\s+(strategy|design)\b",
        r"\bsharding\b",
        r"\bpartition\s+(strategy|design)\b",
        r"\bsql\s+schema\b",
        r"\btable\s+design\b",
        r"\bnosql\s+design\b",
        r"\bdocument\s+(model|store)\b",
        r"\bmongodb\s+schema\b",
        r"\bpostgres\s+(schema|design)\b",
        r"\bmaster\s+data\b",
        r"\bdata\s+governance\b",
    ]),

    # --- Process Design / PDC ---
    ("pdc", 0.85, [
        r"\bprocess\s+(design|flow|model)\b",
        r"\bworkflow\s+(design|orchestration)\b",
        r"\bbpmn\b",
        r"\bstate\s+machine\b",
        r"\bbusiness\s+process\b",
        r"\bprocess\s+automation\b",
        r"\bbpm\b",
        r"\borchestration\s+(design|flow)\b",
        r"\bsaga\s+pattern\b",
    ]),

    # --- Business Design / BDC ---
    ("bdc", 0.85, [
        r"\bbusiness\s+(model|design|architecture)\b",
        r"\bbusiness\s+process\s+diagram\b",
        r"\bvalue\s+stream\b",
        r"\bcapability\s+map\b",
        r"\bstakeholder\s+(map|analysis)\b",
        r"\bbusiness\s+case\b.*design",
        r"\borganizational\s+design\b",
    ]),

    # --- Observability Design / ODC ---
    ("odc", 0.87, [
        r"\bobservabilit",
        r"\bmonitoring\s+design\b",
        r"\blogging\s+architecture\b",
        r"\btracing\s+(design|architecture)\b",
        r"\btelemetry\s+design\b",
        r"\bsiem\s+design\b",
        r"\balerting\s+(design|strategy)\b",
        r"\bdashboard\s+design\b",
        r"\bslo\b.*design",
        r"\bsre\s+(design|architecture)\b",
        r"\bopentelemetry\b",
    ]),

    # --- Infrastructure Design / IDC ---
    ("idc", 0.88, [
        r"\binfrastructure\s+(design|architecture|as\s+code)\b",
        r"\bterraform\s+design\b",
        r"\bk8s\s+design\b",
        r"\bkubernetes\s+(design|architecture)\b",
        r"\bcloud\s+(infrastructure|architecture)\b",
        r"\biac\b.*design",
        r"\bansible\b.*design",
        r"\bcontainer\s+(design|architecture)\b",
        r"\bmicroservices\s+architecture\b",
        r"\bservice\s+mesh\b",
        r"\bhelm\s+(chart|design)\b",
        r"\bcluster\s+design\b",
        r"\bgitops\b",
        r"\bdeployment\s+architecture\b",
    ]),
]

# Intake signals — if these match strongly, stay in intake regardless
_INTAKE_STRONG: list[str] = [
    r"\bi\s+(need|want|would\s+like)\s+(to\s+build|to\s+create|an?\s+app)",
    r"\bbuild\s+(me\s+)?a\b",
    r"\bcreate\s+(a|an)\s+\w+\s+(app|application|system|tool)\b",
    r"\bnew\s+(project|app|application|system)\b",
    r"\brequirement",
    r"\bspe[ck]\b.*\bfor\b",
    r"\buser\s+stor",
    r"\bacceptance\s+criteria\b",
    r"\bricoas\b",
    r"\bproject\s+brief\b",
    r"\bfeasibilit",
    # Intelligence / OSINT / ISR — always requirements intake, never NDC
    r"\bosint\b",
    r"\bopen.source\s+intelligence\b",
    r"\bintelligence\s+(analysis|gather|collect|monitor|fusion|report)\b",
    r"\bisr\b",  # Intelligence, Surveillance, Reconnaissance
    r"\bsigint\b",
    r"\bgeoint\b",
    r"\bhumint\b",
    r"\bmasint\b",
    r"\bfinint\b",
    r"\bthreat\s+(intelligence|actor|hunt)\b",
    r"\bindicator\s+of\s+(compromise|attack)\b",
    r"\bioc\b",
    r"\bconflict\s+(monitor|track|analys)",
    r"\battribution\b",
    r"\badversary\s+(track|profile|analys)\b",
    r"\bsituation\s+(aware|report|monitor)\b",
    r"\btargeting\s+(analys|system)\b",
]


def _score_message(text: str) -> dict[str, Any]:
    """Score a message against keyword rules. Returns best match or intake."""
    lower = text.lower()

    # Check intake signals first
    for pat in _INTAKE_STRONG:
        if re.search(pat, lower):
            return {"mode": INTAKE_MODE, "canvas_type": None, "confidence": 0.90, "reason": "intake keyword matched"}

    best_canvas: str | None = None
    best_score: float = 0.0
    best_count = 0

    for canvas_type, weight, patterns in _RULES:
        hits = sum(1 for p in patterns if re.search(p, lower))
        if hits == 0:
            continue
        # Score: weight * sqrt(hits) — more hits = higher confidence, diminishing returns
        import math
        score = weight * math.sqrt(hits)
        if score > best_score:
            best_score = score
            best_canvas = canvas_type
            best_count = hits

    if best_canvas and best_score >= 0.70:
        # Normalise to 0–1: cap at 1.0
        confidence = min(best_score / 1.2, 1.0)
        return {
            "mode": best_canvas,
            "canvas_type": best_canvas,
            "confidence": round(confidence, 3),
            "reason": f"keyword match: {best_count} rule(s) hit for {best_canvas.upper()}",
        }

    return {"mode": INTAKE_MODE, "canvas_type": None, "confidence": 0.55, "reason": "no strong keyword signal"}


def _intake_default(reason: str) -> dict[str, Any]:
    return {"mode": INTAKE_MODE, "canvas_type": None, "confidence": 0.50, "reason": reason}


def _llm_classify(text: str) -> dict[str, Any]:
    """Governed LLM fallback for ambiguous messages.

    Returns the same shape as :func:`_score_message`. Routed through
    ``cortex_api.classify`` (and therefore ``LLMRouter.invoke``) rather than a
    provider handle, so the call carries the gateway pre-check, input/output
    redaction, budget and rate gates, the provider fallback chain and the
    append-only ``cortex_audit`` row. Any failure — governance block, exhausted
    chain, Cortex unavailable — degrades to intake.
    """
    try:
        from tools.cortex.api import classify as cortex_classify
        from tools.cortex.schemas import CortexContext

        result = cortex_classify(
            text,
            labels=sorted(_LABEL_TO_MODE),
            ctx=CortexContext(agent_id="chat-intent"),
        )

        if (result.provider or "") == _CORTEX_FALLBACK_PROVIDER:
            # No LLM was reachable (air-gap / exhausted chain), so classify()
            # answered from tools/rag/query_classifier.py, whose taxonomy
            # (factual / analytical / …) maps onto none of these labels — it then
            # falls through to labels[0]. Accepting that would route every
            # ambiguous air-gap message to one arbitrary canvas.
            return _intake_default("LLM unavailable, defaulting to intake")

        label = (result.text or "").strip().lower()
        mode = _LABEL_TO_MODE.get(label)
        if mode is None:
            return _intake_default("LLM returned no known label, defaulting to intake")

        return {
            "mode": mode,
            "canvas_type": mode if mode in CANVAS_MODES else None,
            "confidence": 0.78,
            "reason": f"LLM: cortex.classify chose {label} via {result.provider or 'cortex'}",
        }
    except Exception:
        return _intake_default("LLM unavailable, defaulting to intake")


def classify(message: str) -> dict[str, Any]:
    """Classify a user message. Returns mode, canvas_type, confidence, reason."""
    if not message or not message.strip():
        return {"mode": INTAKE_MODE, "canvas_type": None, "confidence": 1.0, "reason": "empty message"}

    result = _score_message(message)

    # Use LLM for low-confidence keyword results (not for clear intake signals)
    if result["confidence"] < 0.70 and result["mode"] == INTAKE_MODE:
        result = _llm_classify(message)

    return result
