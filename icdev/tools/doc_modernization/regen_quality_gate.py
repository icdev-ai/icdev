# CUI // SP-CTI
"""Regeneration quality gate (dmx-qa-01).

A full-document regeneration (``regen_orchestrator.regenerate_document``) mints a
NEW ``pending_review`` version of an existing document from current evidence. The
DIC generator already runs per-section confidence verification, placeholder
detection, reasoning-artifact scrubbing and confabulation assessment before it
persists. What it did NOT do for the regeneration path — and what this module
adds — is a single deterministic gate, evaluated at the moment the version would
enter the review queue, that BLOCKS a defective regeneration from reaching
``pending_review`` unless a human forces the override:

  1. Citation re-validation — every ``[source: ...]`` tag in the new text is
     checked against the CURRENT evidence source ids via the shared
     ``tools.quality.citation_grounding`` (never re-implemented here). A citation
     to a source that is not in the current evidence (a hallucinated citation),
     or a non-abstained section with no citation at all, is a blocking defect.
  2. Internal-consistency check — unresolved ``[PLACEHOLDER]`` tokens and
     cross-section numeric conflicts, reusing the same
     ``content_grounding`` / ``consistency_checker`` primitives the approve-time
     publish gate uses (``check_version_consistency``). Placeholders block;
     numeric conflicts are surfaced (mirrors the approve gate's semantics).
  3. Claim-preservation diff summary — a text-level diff of what the old APPROVED
     version asserted vs the new draft, so a reviewer can see at a glance what
     changed. Informational only (never blocks).

Everything here is deterministic (pure regex/difflib/dict) — no LLM verdict
gates promotion. The gate READS the generated sections; it never mutates
``dic_versions`` / ``dic_edit_history``. Persistence of the gate-decided status
and the force-override audit note live in ``regen_orchestrator``.

Air-gap: all checks run locally with no external resource. When no LLM provider
is available the generator synthesizes uncited prose from source text; that draft
has no ``[source:]`` tags, so it fails citation re-validation and BLOCKS (a
documented, non-bypassing default) rather than silently passing — an authorized
human can still force past it with an audited override.

Upgrade path (dmx-claims): the claim-preservation diff is currently a text-level
difflib summary. Semantic claim tracking (assertion-level add/drop/contradiction
against ``dic_claims``) lands in the dmx-claims task; swap
``_claim_preservation_summary`` for the dic_claims comparator when it exists.
"""
from __future__ import annotations

import difflib
from typing import Any


# Blocking issue codes surfaced to the reviewer / audit note.
BLOCK_MISSING_CITATIONS = "missing_citations"
BLOCK_HALLUCINATED_CITATION = "hallucinated_citation"
#: Every section abstained, so NOTHING reached the citation check. The
#: gate must not report that as clean — see the note in
#: `evaluate_regeneration_quality`.
BLOCK_NOTHING_VERIFIABLE = "nothing_verifiable"
BLOCK_UNRESOLVED_PLACEHOLDERS = "unresolved_placeholders"


def _section_dicts(sections: list[Any]) -> list[dict]:
    """Normalize GeneratedSection objects OR dicts to the shape the shared
    grounding/citation primitives expect. Abstained sections make no claims and
    are dropped (they carry the "(Abstained — ...)" sentinel, not real prose)."""
    out: list[dict] = []
    for s in sections or []:
        if isinstance(s, dict):
            abstained = bool(s.get("abstained"))
            heading = s.get("heading") or s.get("item_number") or s.get("title") or "?"
            content = s.get("content") or s.get("ai_draft") or ""
        else:
            abstained = bool(getattr(s, "abstained", False))
            heading = getattr(s, "heading", "") or "?"
            content = getattr(s, "content", "") or ""
        if abstained:
            continue
        out.append({"item_number": heading, "heading": heading, "content": content})
    return out


def _citation_findings(section_dicts: list[dict], allowed_sources) -> list[dict]:
    """Re-validate citations against CURRENT evidence via the shared module.

    Reuses ``tools.quality.citation_grounding`` — citation parsing/validation is
    NEVER re-implemented here (TRUST invariant). ``allowed_sources`` is the set of
    evidence source ids that actually backed this regeneration; a citation to any
    id outside it is hallucinated, and a section with no citation is uncited.
    """
    from tools.quality.citation_grounding import citation_gate

    allowed = {str(s) for s in (allowed_sources or [])}
    # Attach allowed_sources so citation_gate flags hallucinated citations too.
    scoped = [{**sec, "allowed_sources": allowed} for sec in section_dicts]
    return citation_gate(scoped, require_citations=True)


def _consistency_findings(section_dicts: list[dict]) -> dict:
    """Placeholder + numeric-conflict check, reusing the same primitives as the
    approve-time publish gate (``consistency_checker.check_version_consistency``)."""
    from tools.document_intelligence.consistency_checker import check_numeric_claims
    from tools.quality.content_grounding import placeholder_findings

    return {
        "placeholders": placeholder_findings(section_dicts),
        "numeric_conflicts": check_numeric_claims(section_dicts),
    }


def _claim_preservation_summary(old_text: str, new_text: str, *, max_lines: int = 40) -> dict:
    """Text-level diff of the old APPROVED assertions vs the new draft.

    Informational only — never blocks. Upgrade path (dmx-claims): replace with a
    semantic assertion-level comparator against ``dic_claims``.
    """
    old = (old_text or "").splitlines(keepends=True)
    new = (new_text or "").splitlines(keepends=True)
    diff_lines = list(difflib.unified_diff(old, new, fromfile="approved", tofile="regenerated", n=1))
    removed = sum(1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---"))
    added = sum(1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++"))
    summary = "".join(diff_lines[:max_lines])
    if len(diff_lines) > max_lines:
        summary += f"\n... ({len(diff_lines) - max_lines} more diff lines)"
    return {
        "removed_lines": removed,
        "added_lines": added,
        "unchanged": removed == 0 and added == 0,
        "diff_summary": summary,
    }


def evaluate_regeneration_quality(
    new_sections: list[Any],
    old_text: str,
    allowed_sources,
    *,
    new_text: str = "",
) -> dict:
    """Deterministic quality gate for a regenerated version.

    Args:
        new_sections: GeneratedSection objects (or dicts) of the new draft.
        old_text: reassembled markdown of the current APPROVED version.
        allowed_sources: iterable of evidence source ids backing the regeneration
            (the chunk ids the draft is allowed to cite).
        new_text: reassembled markdown of the new draft (for the claim diff). If
            omitted it is rebuilt from ``new_sections``.

    Returns a report dict::

        {blocked: bool, reasons: [str],
         citation: {findings: [...]},
         consistency: {placeholders: [...], numeric_conflicts: [...]},
         claim_preservation: {removed_lines, added_lines, unchanged, diff_summary}}

    ``blocked`` is True when any citation defect (missing/hallucinated) or
    unresolved placeholder is present. Numeric conflicts are surfaced but do not
    block (mirrors the approve-time publish gate). A blocked report is what the
    orchestrator uses to withhold ``pending_review`` (or, on force, to audit).
    """
    section_dicts = _section_dicts(new_sections)
    #: NOTHING REACHED THE CHECKS, which is not the same as nothing being wrong.
    #: `_section_dicts` drops abstained sections, and an UNCITED section always
    #: abstains before it gets here: `_compute_section_confidence` returns 0.0
    #: when no claim is cited, and the confidence band abstains at 0.0. So
    #: `missing_citations` was structurally UNREACHABLE through
    #: `regenerate_document` — the one defect this gate names first in its own
    #: docstring could never fire — and a draft whose every section abstained
    #: came back `blocked: False, reasons: []`, i.e. a clean bill of health for
    #: a document nobody had examined.
    #:
    #: The abstention itself is correct and is left alone: the prose is replaced
    #: with the "(Abstained — ...)" sentinel, so unsupported text never reaches
    #: the document. What was wrong is REPORTING it as clean. A draft with
    #: sections but none verifiable is withheld, with a reason that says which
    #: of the two zeroes this is.
    nothing_verifiable = bool(new_sections) and not section_dicts
    if not new_text:
        new_text = "\n\n".join(f"## {s['heading']}\n\n{s['content']}" for s in section_dicts)

    citation_findings = _citation_findings(section_dicts, allowed_sources)
    consistency = _consistency_findings(section_dicts)
    claim_preservation = _claim_preservation_summary(old_text, new_text)

    reasons: list[str] = []
    issues = {f.get("issue") for f in citation_findings}
    if BLOCK_HALLUCINATED_CITATION in issues:
        reasons.append(BLOCK_HALLUCINATED_CITATION)
    if BLOCK_MISSING_CITATIONS in issues:
        reasons.append(BLOCK_MISSING_CITATIONS)
    if consistency["placeholders"]:
        reasons.append(BLOCK_UNRESOLVED_PLACEHOLDERS)

    return {
        "blocked": bool(reasons),
        "reasons": reasons,
        "citation": {"findings": citation_findings,
                     #: Explicit, so a reader can tell "checked, found nothing"
                     #: from "there was nothing to check".
                     "sections_examined": len(section_dicts),
                     "sections_submitted": len(new_sections or []),
                     #: The named case: sections WERE drafted and NONE reached
                     #: the checks. Carried as its own boolean rather than left
                     #: for a caller to infer from two counts, because the
                     #: inference is exactly what nobody was doing when a draft
                     #: whose every section abstained came back `reasons: []`.
                     "nothing_verifiable": nothing_verifiable},
        "consistency": consistency,
        "claim_preservation": claim_preservation,
    }


def format_gate_reason(report: dict) -> str:
    """Human-readable one-line summary of why the gate blocked, for the HITL note."""
    reasons = report.get("reasons") or []
    parts: list[str] = []
    cf = (report.get("citation") or {}).get("findings") or []
    halluc = [f["item_number"] for f in cf if f.get("issue") == BLOCK_HALLUCINATED_CITATION]
    uncited = [f["item_number"] for f in cf if f.get("issue") == BLOCK_MISSING_CITATIONS]
    placeholders = (report.get("consistency") or {}).get("placeholders") or []
    if halluc:
        parts.append(f"hallucinated citations in section(s) {', '.join(map(str, halluc[:5]))}")
    if uncited:
        parts.append(f"uncited section(s) {', '.join(map(str, uncited[:5]))}")
    if placeholders:
        labels = ", ".join(str(p.get("item_number")) for p in placeholders[:5])
        parts.append(f"unresolved placeholders in section(s) {labels}")
    if not parts:
        return "; ".join(reasons) or "quality gate passed"
    return "; ".join(parts)
