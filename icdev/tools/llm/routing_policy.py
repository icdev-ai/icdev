#!/usr/bin/env python3
# CUI // SP-CTI
"""Routing policy resolver — the CONTENT decides whether a call may leave the host.

prem-p0-01. Before this, CUI protection was a hand-maintained list: certain
functions were *pinned* in ``args/llm_config.yaml`` to ``chain: [qwen3-local,
llama-local]`` under a comment saying "never send raw proposal content to cloud
LLMs". A pin is weak in three ways, and all three were live:

  1. **It is not enforced.** Nothing checked that a pinned chain was actually
     local. Add a cloud model to the list, or rename a local one, and CUI
     egresses silently.
  2. **It only protects what someone remembered to pin.** ``requirement_extraction``
     sits inside that same "never send proposal content to cloud" block and routes
     to ``kimi-cloud`` FIRST.
  3. **The pin was not even the last word.** ``two_tier`` routing is applied in
     ``LLMRouter.invoke`` BEFORE the chain is resolved, and the CLI-bridge
     invoke-time override PREPENDS ``claude-cli`` — a cloud egress path — to
     whatever chain it is handed. A chain that looks local-only in YAML could
     still be fronted by a cloud model at request time.

So the guarantee cannot live in the chain. It lives at the egress point: the
router asks this module "may this content leave the machine?" and refuses the
provider call if the answer is no. Chain filtering still happens (so we *prefer*
a local model rather than merely failing), but it is defence in depth, not the
guarantee.

## The rungs

Strict precedence — each rung overrides every rung below it:

  1. ``airgap``                 — an air-gapped install routes local, always.
  2. ``force_local``            — the function is declared ``force_local: true``.
                                  Belt-and-braces for the highest-risk functions
                                  (raw proposal prose, pricing, past performance).
  3. ``declared_classification``— ``LLMRequest.classification`` at/above the
                                  threshold.
  4. ``derived_classification`` — THE SUBTLE ONE. Individually-unclassified fields
                                  can COMPILE to a higher classification (the
                                  mosaic effect). ``aggregation_guard`` evaluates
                                  the SCG co-occurrence rules over the actual
                                  request content; a derived classification forces
                                  local exactly like a declared one. Without this,
                                  the mosaic leaks through the very door we think
                                  we closed.
  5. ``default``                — cloud permitted. The content is still sanitized
                                  at egress (``_pre_invoke_redaction``), and with
                                  ``redaction.fail_closed`` the call is REFUSED if
                                  the sanitizer cannot run.

## Why the threshold is not CUI

``LLMRequest.classification`` defaults to ``"CUI"`` (tools/llm/provider.py). That
default is a *marking convention*, not an assertion that the content is CUI. A
threshold of CUI would therefore force EVERY call in the platform to local and
take the product down.

CUI is protected by the other two mechanisms instead, which is the composition the
plan asks for — "verbatim CUI never leaves; masked/sanitized content may go cloud":

  * Functions that handle **raw, unmaskable** CUI (proposal prose, bid pricing,
    colour review, the ``rfi_*`` roles) are declared ``force_local`` — rung 2.
    They never attempt masking; they simply never leave.
  * Everything else is **masked before egress** by the redaction sanitizer, and
    with ``redaction.fail_closed: true`` a sanitizer that cannot run REFUSES the
    cloud call rather than sending raw text.

Above the threshold (``SECRET`` by default), masking is *not* considered
sufficient and the content stays local regardless.

Configure in ``args/llm_config.yaml``::

    routing_policy:
      enabled: true
      local_threshold: SECRET      # declared/derived at-or-above this => local
      aggregation_guard: true      # rung 4 (mosaic effect)

## What this does NOT cover — read this before trusting it

**The guarantee is scoped to the router.** Every call that goes through
``LLMRouter`` is covered: the chain walk, streaming, two_tier,
``_invoke_model_direct`` (ChainOrchestrator's CoT/CoD path), and ``invoke_for_role``.

There are ~15 call sites in ``tools/`` that construct a provider SDK client and send
a completion **without touching the router at all**, and this module cannot see them.
The ones that reach a CLOUD provider are the ones that matter:

  * ``tools/rag/pdf_provider.py``            — ``client.messages.create`` / ``genai``.
    The sharpest edge: PDFs are exactly where CUI solicitations and proposals live.
  * ``tools/genesis/reflexes/inspect_adapt.py``, ``sdc_control_expiry.py``,
    ``fathomdesk_openbb_refresh.py``, ``strategos/osint_harvester.py``
                                             — ``client.messages.create`` (Anthropic).
  * ``tools/memory/{semantic_search,hybrid_search,embed_memory,maintenance_cron}.py``
                                             — ``openai.OpenAI`` embeddings.
  * ``tools/agent/bedrock_client.py``, ``tools/saas/bedrock/bedrock_proxy.py``,
    ``tools/viz/asset_generator.py``, the ``tools/finetune/`` providers.

(Direct calls to ``http://localhost:11434`` — llm_judge, ner_recognizer,
security_canvas — are local and therefore not an egress concern.)

Closing those means routing them through the router, or hoisting this check into a
shared egress gate. That is a separate piece of work, deliberately not smuggled into
this one. **Do not describe P0 as "no CUI can reach a cloud LLM" — it is "no CUI can
reach a cloud LLM *via the router*", and the router is not yet the only door.**

## Known limitation — read this before trusting it

Locality is decided by ``cli_bridge.activate.is_local_only_model``: a provider of
``type: ollama`` carrying no ``api_key_env``. That correctly separates ``ollama``
from ``ollama_cloud`` (which share a type and differ only by key). But it infers
locality from provider *type*, not from where bytes actually go: ``ollama``'s
base_url is ``${OLLAMA_BASE_URL:-http://localhost:11434}``, so pointing that env
var at a remote host makes CUI egress while still being classified "local". It
also means a localhost vLLM (``type: openai_compatible``) is NOT counted as local,
which is conservative — it fails closed — but would refuse a legitimately
air-gapped vLLM deployment. Tightening this is deliberately out of scope here;
it belongs with the provider config, not the routing policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from tools.logging.icdev_logger import get_logger
logger = get_logger("icdev.llm.routing_policy")

# Rung identifiers. Tests assert on these, and every refusal names the rung that
# fired — "it routed local" is not a useful thing to debug; "airgap fired" is.
RULE_AIRGAP = "airgap"
RULE_FORCE_LOCAL = "force_local"
RULE_DECLARED = "declared_classification"
RULE_DERIVED = "derived_classification"
RULE_DEFAULT = "default"
RULE_DISABLED = "policy_disabled"

DEFAULT_LOCAL_THRESHOLD = "SECRET"


@dataclass(frozen=True)
class RoutingDecision:
    """The answer to: may this content leave the machine?"""

    local_only: bool
    rule: str
    reason: str
    threshold: str = DEFAULT_LOCAL_THRESHOLD
    classification: str = ""
    derived: str = ""
    fired_rules: List[str] = field(default_factory=list)

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        where = "LOCAL-ONLY" if self.local_only else "cloud-permitted"
        return f"{where} [{self.rule}] {self.reason}"


# ---------------------------------------------------------------------------
# Primitives (each wrapped so a missing/broken dependency FAILS CLOSED)
# ---------------------------------------------------------------------------


# The classification lattice, as understood by classification_manager. Anything
# NOT in here is unrecognised — see _clearance_order.
_KNOWN_CLASSIFICATIONS = {
    "PUBLIC",
    "UNCLASSIFIED",
    "U",
    "CUI",
    "ECI",
    "CONFIDENTIAL",
    "SECRET",
    "TS",
    "TOP SECRET",
    "TOP SECRET//SCI",
}

_FAIL_CLOSED_ORDER = 99


def _clearance_order(classification: str) -> int:
    """Position of *classification* in the lattice. UNRECOGNISED => highest.

    Fail closed, and note this deliberately does NOT match
    ``classification_manager.get_clearance_order``, which maps an unknown string
    to ``1`` (i.e. CUI) rather than to 0. That default is fine for display, but as
    an *egress* decision it is fail-OPEN: a typo — ``"SECRETT"`` — would score 1,
    land below a SECRET threshold, and be waved through to a cloud provider.

    A classification we cannot place is not evidence that the content is harmless.
    """
    value = (classification or "").strip().upper()
    if not value:
        return 0
    if value not in _KNOWN_CLASSIFICATIONS:
        logger.warning(
            "unrecognised classification %r — treating as maximum (fail-closed). "
            "A typo must not become a cloud egress.",
            classification,
        )
        return _FAIL_CLOSED_ORDER
    try:
        from tools.compliance.classification_manager import get_clearance_order

        return int(get_clearance_order(value))
    except Exception:
        return _FAIL_CLOSED_ORDER


def _is_airgap() -> bool:
    """Air-gapped install? Cached by the detector, so this is cheap per-invoke.

    If the detector cannot run we return False rather than forcing every call
    local — an unavailable *air-gap probe* is not evidence of classified content,
    and the classification rungs below still apply. This is the one rung that
    fails OPEN, deliberately: it answers "where am I installed", not "is this
    content sensitive".
    """
    try:
        from tools.airgap.detector import is_airgap

        return bool(is_airgap(use_cache=True))
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("air-gap detection unavailable: %s", exc)
        return False


def _request_text(request: Any) -> str:
    """Flatten an LLMRequest's user-supplied text. Never raises."""
    parts: List[str] = []
    try:
        for msg in getattr(request, "messages", None) or []:
            if isinstance(msg, dict):
                c = msg.get("content", "")
                if isinstance(c, str):
                    parts.append(c)
                elif isinstance(c, list):
                    # Vision/multimodal: keep the text blocks, ignore image bytes.
                    for block in c:
                        if isinstance(block, dict) and isinstance(block.get("text"), str):
                            parts.append(block["text"])
        sp = getattr(request, "system_prompt", "") or ""
        if isinstance(sp, str):
            parts.append(sp)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("could not flatten request text: %s", exc)
    return "\n".join(p for p in parts if p)


def _derived_classification(text: str) -> tuple[str, List[str]]:
    """Highest classification the SCG aggregation rules DERIVE from *text*.

    The mosaic effect: no single field here is classified, but their
    co-occurrence compiles to one that is. Returns ("", []) when nothing fires.

    Fails closed on error: if the guard cannot be evaluated we return the
    configured threshold, so the caller routes local rather than guessing.
    """
    if not (text or "").strip():
        return "", []
    try:
        from tools.security.aggregation_guard import evaluate_rules

        fired = evaluate_rules([{"element_id": "llm_request", "text": text}]) or []
    except Exception as exc:
        logger.warning(
            "aggregation_guard unavailable (%s) — cannot rule out a DERIVED "
            "classification, routing local (fail-closed).",
            exc,
        )
        return DEFAULT_LOCAL_THRESHOLD, ["aggregation_guard_unavailable"]

    best, best_order, names = "", -1, []
    for rule in fired:
        derive = (rule.get("derive") or "").strip().upper()
        if not derive:
            continue
        names.append(str(rule.get("rule_id") or rule.get("description") or "?"))
        order = _clearance_order(derive)
        if order > best_order:
            best, best_order = derive, order
    return best, names


# ---------------------------------------------------------------------------
# The resolver
# ---------------------------------------------------------------------------


def is_force_local(function: str, config: Dict[str, Any]) -> bool:
    """True when *function* is declared ``force_local: true`` in llm_config.yaml."""
    routing = (config or {}).get("routing", {}) or {}
    route = routing.get(function) or routing.get("default") or {}
    return bool(route.get("force_local", False))


def resolve(
    function: Optional[str],
    request: Any,
    config: Optional[Dict[str, Any]] = None,
) -> RoutingDecision:
    """Decide whether this call may reach a cloud provider.

    *function* may be None: ChainOrchestrator's ``_invoke_model_direct`` path
    carries real content with no function name. The content-based rungs still
    apply — which is the entire point of moving the decision off the function.
    """
    config = config or {}
    pol = config.get("routing_policy", {}) or {}

    if not pol.get("enabled", True):
        return RoutingDecision(
            local_only=False,
            rule=RULE_DISABLED,
            reason="routing_policy.enabled is false — no content policy applied.",
        )

    threshold = str(pol.get("local_threshold", DEFAULT_LOCAL_THRESHOLD)).strip().upper()
    t_order = _clearance_order(threshold)

    # Rung 1 — air-gapped install. Nothing can leave; nothing else matters.
    if _is_airgap():
        return RoutingDecision(
            local_only=True,
            rule=RULE_AIRGAP,
            reason="air-gapped install — no cloud provider is reachable or permitted.",
            threshold=threshold,
        )

    # Rung 2 — the function is declared force_local.
    if function and is_force_local(function, config):
        return RoutingDecision(
            local_only=True,
            rule=RULE_FORCE_LOCAL,
            reason=(
                f"function '{function}' is declared force_local: it handles raw CUI "
                f"(proposal prose / pricing / past performance) that cannot be masked."
            ),
            threshold=threshold,
        )

    # Rung 3 — declared classification at/above the threshold.
    declared = (getattr(request, "classification", "") or "").strip().upper()
    if declared and _clearance_order(declared) >= t_order:
        return RoutingDecision(
            local_only=True,
            rule=RULE_DECLARED,
            reason=f"declared classification {declared} is at/above threshold {threshold}.",
            threshold=threshold,
            classification=declared,
        )

    # Rung 4 — DERIVED (mosaic) classification. The subtle one.
    if pol.get("aggregation_guard", True):
        derived, fired = _derived_classification(_request_text(request))
        if derived and _clearance_order(derived) >= t_order:
            return RoutingDecision(
                local_only=True,
                rule=RULE_DERIVED,
                reason=(
                    f"content COMPILES to {derived} (>= {threshold}) via SCG aggregation "
                    f"rules {fired} — no single field is classified, their co-occurrence is."
                ),
                threshold=threshold,
                classification=declared,
                derived=derived,
                fired_rules=fired,
            )

    # Rung 5 — cloud permitted. Egress is still sanitized (and fail-closed if the
    # sanitizer cannot run — see redaction.fail_closed).
    return RoutingDecision(
        local_only=False,
        rule=RULE_DEFAULT,
        reason="no rung fired; cloud permitted, content will be sanitized at egress.",
        threshold=threshold,
        classification=declared,
    )
