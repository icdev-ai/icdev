# CUI // SP-CTI
"""Shared, deterministic triage synthesis for domain lenses.

``grounded_triage`` turns a set of scored Cortex search hits into a structured,
grounded-by-construction brief (evidence / coverage / recommended actions + a
rendered text) with domain-specific section labels. Every field derives from a
real retrieved row, so it carries no hallucination risk — the same guarantee as
``domains/security.py::triage_summary``, generalized so the proposal/document
lenses can reuse it instead of each re-implementing ranking + rendering.
"""
from __future__ import annotations

from typing import Callable, List


def confidence_tier(score: float) -> str:
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "low"
    if s >= 0.75:
        return "high"
    if s >= 0.4:
        return "medium"
    return "low"


def _source_of(result) -> str:
    citation = getattr(result, "citation", None)
    if citation is not None:
        return (getattr(citation, "source_table", "")
                or getattr(citation, "source_id", "")
                or getattr(citation, "doc_title", "") or "")
    return ""


def _coverage(ranked: list) -> dict:
    sources = []
    for r in ranked:
        s = _source_of(r)
        if s and s not in sources:
            sources.append(s)
    return {
        "result_count": len(ranked),
        "distinct_sources": len(sources),
        "sources": sources[:8],
    }


def _format(query: str, headers: dict, evidence: List[dict],
            coverage: dict, actions: List[str]) -> str:
    title = headers.get("title", "Triage")
    lines = [f"{title} for: {query!r}" if query else title]
    if not evidence:
        lines.append(headers.get("empty", "No scoped findings."))
        return "\n".join(lines)
    lines.append("")
    lines.append(headers.get("evidence", "Top evidence:"))
    for e in evidence:
        lines.append(
            f"  {e['rank']}. [{e['tier'].upper()}] {e['title']} "
            f"(score {e['score']}, source {e['source']})"
        )
    lines.append("")
    lines.append(
        f"{headers.get('coverage', 'Coverage')}: "
        f"{coverage['distinct_sources']} distinct source(s) across "
        f"{coverage['result_count']} hit(s)"
        + (f" — {', '.join(coverage['sources'][:5])}" if coverage['sources'] else "")
    )
    lines.append("")
    lines.append(headers.get("actions", "Recommended actions:"))
    for i, action in enumerate(actions, 1):
        lines.append(f"  {i}. {action}")
    return "\n".join(lines)


def grounded_triage(
    results,
    domain: str,
    headers: dict,
    actions_fn: Callable[[list], List[str]],
    query: str = "",
    top_n: int = 5,
) -> dict:
    """Deterministic grounded triage over ranked hits for ``domain``.

    ``headers`` supplies the section labels (title/evidence/coverage/actions/
    empty). ``actions_fn(ranked)`` returns the domain's recommended actions
    (derived from the retrieved rows, never invented).
    """
    ranked = sorted(results or [], key=lambda r: getattr(r, "score", 0.0) or 0.0, reverse=True)
    top = ranked[: max(0, int(top_n))]
    evidence: List[dict] = []
    for i, r in enumerate(top, 1):
        citation = getattr(r, "citation", None)
        title = (
            (getattr(citation, "title", "") if citation else "")
            or (getattr(citation, "doc_title", "") if citation else "")
            or ((r.content[:80]) if getattr(r, "content", "") else "")
            or "(untitled)"
        )
        evidence.append({
            "rank": i,
            "title": title,
            "tier": confidence_tier(getattr(r, "score", 0.0)),
            "score": round(float(getattr(r, "score", 0.0) or 0.0), 3),
            "backend": getattr(r, "backend", ""),
            "source": _source_of(r) or getattr(r, "backend", ""),
            "source_id": getattr(citation, "source_id", "") if citation else "",
            "snippet": (getattr(r, "content", "") or "")[:200],
        })
    coverage = _coverage(ranked)
    actions = actions_fn(ranked)
    return {
        "domain": domain,
        "query": query,
        "result_count": len(ranked),
        "top_evidence": evidence,
        "coverage": coverage,
        "recommended_actions": actions,
        "text": _format(query, headers, evidence, coverage, actions),
    }
