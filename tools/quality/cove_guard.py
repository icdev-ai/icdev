# CUI // SP-CTI
"""CoVe promote/export guard (agx-verify-01).

An optional pre-promote / pre-export verification pass for LLM-drafted
artifacts (proposals, RFI, DIC, Tech Writer), mirroring how
``citation_grounding.citation_gate`` / ``content_grounding.placeholder`` already
gate promotion with a HITL ``force_*`` override + audit.

Division of labor (do NOT duplicate citation parsing — the TRUST invariant
forbids it):
  * ``tools/quality/citation_grounding.py`` decides whether citations PARSE and
    RESOLVE. This guard reuses ``parse_citations`` to discover which sources the
    draft cites and hands those resolved source texts to CoVe as the evidence.
  * The CoVe architecture (``tools/llm/architectures/cove.py``) decides whether
    each CLAIM survives INDEPENDENT interrogation and composes the verdict from
    per-question enums.

This module owns neither inference nor citation parsing; it is the thin gate
that turns a CoVe run into a blocking/overridable finding. Opt-in and
budget-capped — CoVe multiplies calls per artifact, so callers enable it
deliberately.
"""
from __future__ import annotations

from typing import Any, Dict, List

from tools.quality.citation_grounding import parse_citations


def _resolve_cited_sources(text: str, available_sources: Any) -> List[str]:
    """Return the texts of the sources the draft actually cites.

    ``available_sources`` may be a mapping ``{source_id: text}`` or an iterable
    of dicts carrying ``id``/``source_id`` + ``content``/``text``. Only sources
    the draft cites are handed to the verifier, so verification is grounded in
    the evidence the author claimed to use.
    """
    cited = parse_citations(text)
    if not cited:
        return []
    lookup: Dict[str, str] = {}
    if isinstance(available_sources, dict):
        lookup = {str(k): str(v) for k, v in available_sources.items()}
    elif available_sources:
        for s in available_sources:
            if isinstance(s, dict):
                sid = str(s.get("id") or s.get("source_id") or "")
                body = s.get("content") or s.get("text") or ""
                if sid:
                    lookup[sid] = str(body)
    return [lookup[c] for c in cited if c in lookup and lookup[c].strip()]


def cove_guard(
    text: str,
    *,
    available_sources: Any = None,
    router=None,
    force_override: bool = False,
    max_questions: int = 5,
    verify_function: str = "cove_verify",
    function: str = "architecture_run",
) -> dict:
    """Run CoVe over ``text`` and return a promote/export gate finding.

    Returns a dict::

        {passed, blocked, needs_revision, revised_text, contradicted_claims,
         decision, forced, method}

    ``blocked`` is True when CoVe found a claim needing revision AND the caller
    did not pass ``force_override``. When ``force_override`` is set, ``blocked``
    is False but ``forced`` is True — the caller MUST write an audit record for
    the override, exactly as the citation/placeholder guards require.
    """
    from tools.llm.architectures.registry import run as run_architecture

    if not (text or "").strip():
        return {
            "passed": True, "blocked": False, "needs_revision": False,
            "revised_text": text, "contradicted_claims": [], "decision": None,
            "forced": False, "method": "empty",
        }

    evidence = _resolve_cited_sources(text, available_sources)
    try:
        result = run_architecture(
            "chain_of_verification",
            text,
            router=router,
            baseline=text,
            citation_sources=evidence,
            max_questions=max_questions,
            verify_function=verify_function,
            function=function,
        )
    except Exception as exc:  # noqa: BLE001 — a guard must fail closed, never crash promote
        return {
            "passed": False, "blocked": not force_override, "needs_revision": True,
            "revised_text": text, "contradicted_claims": [],
            "decision": {"error": f"{type(exc).__name__}: {exc}"},
            "forced": bool(force_override), "method": "error",
        }

    decision = (result.metadata or {}).get("decision") or {}
    qa_trace = (result.metadata or {}).get("qa_trace") or []
    needs_revision = bool(decision.get("needs_revision")) or result.degraded
    contradicted = [
        t for t in qa_trace if t.get("verdict") in {"contradicted", "unsupported"}
    ]
    blocked = needs_revision and not force_override
    return {
        "passed": not needs_revision,
        "blocked": blocked,
        "needs_revision": needs_revision,
        "revised_text": result.output or text,
        "contradicted_claims": contradicted,
        "decision": decision,
        "forced": bool(force_override) and needs_revision,
        "method": "cove",
    }
