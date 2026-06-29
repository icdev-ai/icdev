# CUI // SP-CTI
"""Re-rank DIC search results by Second Brain user context."""
from __future__ import annotations

import re
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)


def personalise_dic_results(
    results: list[dict[str, Any]],
    user_id: str,
    tenant_id: str = "default",
) -> list[dict[str, Any]]:
    """Re-rank DIC search results by user objectives + customer types.

    Each result dict is expected to have: title, content/snippet, score (float).
    Returns the same list with an added `personal_score` field, sorted desc.
    """
    try:
        ctx = _build_personal_context(user_id, tenant_id)
    except Exception as exc:
        logger.debug("[dic_personaliser] context load failed: %s", exc)
        return results

    if not ctx["keywords"]:
        return results

    reranked = []
    for r in results:
        text = f"{r.get('title', '')} {r.get('content', r.get('snippet', ''))}".lower()
        boost = sum(1.0 for kw in ctx["keywords"] if re.search(rf"\b{re.escape(kw)}\b", text))
        personal_score = min(1.0, boost * 0.15)
        base_score = float(r.get("score", r.get("similarity", 0.5)))
        r["personal_score"] = round(personal_score, 3)
        r["combined_score"] = round(base_score * 0.7 + personal_score * 0.3, 4)
        reranked.append(r)

    reranked.sort(key=lambda x: x["combined_score"], reverse=True)
    return reranked


def _build_personal_context(user_id: str, tenant_id: str) -> dict:
    """Extract keyword signals from the user's objectives + customer types."""
    keywords: list[str] = []

    try:
        from tools.second_brain.profile import get_objectives, get_relationships
        objs = get_objectives(user_id, tenant_id) or []
        for o in objs[:5]:
            title = o.get("title", "")
            words = [w for w in re.split(r"[^a-z]+", title.lower()) if len(w) > 3]
            keywords.extend(words)

        rels = get_relationships(user_id, tenant_id) or []
        for rel in rels:
            rtype = rel.get("relationship_type", "")
            org = rel.get("org", "")
            if org:
                keywords.extend([w for w in re.split(r"[^a-z]+", org.lower()) if len(w) > 3])
            if rtype == "customer":
                keywords += ["client", "customer", "sla", "support"]
            elif rtype == "stakeholder":
                keywords += ["stakeholder", "executive", "leadership"]
    except Exception as exc:
        logger.debug("[dic_personaliser] profile load error: %s", exc)

    _STOP = {"that", "this", "with", "from", "have", "will", "your", "their", "been", "they"}
    seen: set[str] = set()
    unique: list[str] = []
    for kw in keywords:
        if kw not in _STOP and kw not in seen:
            seen.add(kw)
            unique.append(kw)

    return {"keywords": unique[:30]}
