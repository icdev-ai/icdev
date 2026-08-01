# CUI // SP-CTI
"""LLM relationship extraction for the knowledge graph.

The heuristic `_extract_relationships` in text_network only fires when two
entities sit in the SAME sentence with a hardcoded verb phrase literally between
them. Prose — and especially the conference-transcript text that dominates the
DIC corpus — almost never satisfies that, so the KG comes out node-rich and
edge-poor: 2,749 nodes against ~140 edges live, i.e. a bag of disconnected nodes
rather than a graph. The "+KG" half of DIC's RAG+KG therefore contributes almost
nothing to retrieval.

This module infers relationships SEMANTICALLY among the entities already
extracted for a chunk, using the LLM the router is configured for (Kimi in this
deployment). Two hard constraints keep it honest and keep the graph clean:

  * Endpoints must both be in the supplied entity set — the model may not invent
    nodes, only connect ones the deterministic extractor already found.
  * Relationship labels must pass `is_valid_relationship` (letters + underscore,
    <=40 chars). This is also the guard that stops the pollution seen live
    ("KG context", "batch 15min", "train/infer", "clean records") — every one of
    those garbage values contains a space, digit or slash and is rejected.

Each relationship carries a short evidence snippet, so an LLM-derived edge is
provenanced rather than asserted (TRUST).

Graceful by contract: any failure (no LLM, air-gap, unparseable output) returns
`[]` so the caller falls back to the deterministic `_extract_relationships`.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# Controlled vocabulary the LLM is asked to choose from. Snake_case, generic
# enough to span the DIC corpus (compliance, network, program docs) without
# inviting free-text. The deterministic VERB_MAP types (UPPERCASE) also pass the
# guard below, so the heuristic fallback stays valid.
RELATIONSHIP_VOCAB: tuple[str, ...] = (
    "relates_to",
    "depends_on",
    "part_of",
    "implements",
    "references",
    "supersedes",
    "governs",
    "mitigates",
    "conflicts_with",
    "derived_from",
    "produces",
    "owns",
    "located_in",
    "co_occurs_with",
)

_VOCAB_SET = frozenset(RELATIONSHIP_VOCAB)

# A valid relationship label is letters and underscores only, <=40 chars. This
# accepts the LLM vocab AND the deterministic UPPERCASE types, and rejects every
# polluted value observed in kg_edges (all contain a space, digit or slash).
_VALID_REL = re.compile(r"[A-Za-z][A-Za-z_]{0,39}$")

# Bound the prompt so a huge chunk / entity list can't blow the context.
_MAX_ENTITIES = 40
_MAX_TEXT_CHARS = 4000
_MAX_EVIDENCE_CHARS = 200

_SYSTEM_PROMPT = (
    "You are a knowledge-graph relationship extractor. You are given a text "
    "passage and a numbered list of entities already found in it. Identify "
    "directed relationships that the passage actually supports, ONLY between "
    "entities in the list, using ONLY these relationship labels:\n"
    + ", ".join(RELATIONSHIP_VOCAB)
    + ".\nReturn a strict JSON array; each item is "
    '{"source": <entity number>, "target": <entity number>, '
    '"relationship": <label>, "evidence": <short quote from the passage>}. '
    "Do not invent entities or labels. If the passage supports no relationship, "
    "return []."
)

# Entity types the combined extractor may assign. Snake_case, generic; anything
# off-list is normalised to "concept" rather than dropped, so a good entity is
# never lost to a fussy type.
ENTITY_TYPE_VOCAB: tuple[str, ...] = (
    "person", "organization", "system", "component", "technology", "standard",
    "control", "requirement", "document", "location", "event", "concept",
)
_ETYPE_SET = frozenset(ENTITY_TYPE_VOCAB)

_GRAPH_SYSTEM_PROMPT = (
    "You are a knowledge-graph extractor. From the passage, extract the salient "
    "entities and the directed relationships between them.\n"
    "Entity types must be one of: " + ", ".join(ENTITY_TYPE_VOCAB) + ".\n"
    "Relationship labels must be one of: " + ", ".join(RELATIONSHIP_VOCAB) + ".\n"
    "Return strict JSON: "
    '{"entities": [{"name": <str>, "type": <entity type>}], '
    '"relationships": [{"source": <str name>, "target": <str name>, '
    '"relationship": <label>, "evidence": <short quote>}]}. '
    "Relationship source/target must be names from your entities list. Extract "
    "only what the passage actually supports; return empty lists if none."
)

_MAX_LLM_ENTITIES = 60


def is_valid_relationship(rel: Any) -> bool:
    """True when *rel* is a clean relationship label (letters/underscore, <=40).

    The single guard both the LLM path and the edge writer use to keep garbage
    out of kg_edges.relationship.
    """
    return isinstance(rel, str) and bool(_VALID_REL.fullmatch(rel.strip()))


def _strip_fence(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1] if raw.count("```") >= 2 else raw.strip("`")
        if raw.lstrip().lower().startswith("json"):
            raw = raw.lstrip()[4:]
    return raw.strip()


def _parse_json_object(raw: str) -> dict:
    """Best-effort parse of a model reply that should be a JSON object."""
    raw = _strip_fence(raw)
    if not raw.startswith("{"):
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {}
        raw = raw[start : end + 1]
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def extract_graph_llm(
    text: str,
    *,
    router: Optional[Any] = None,
) -> tuple[list[tuple[str, str]], list[tuple[str, str, str, str]]]:
    """Extract entities AND relationships from *text* in one LLM call.

    Used when the deterministic regex extractor finds too few entities — which
    is 87% of the live DIC corpus (488/559 chunks yield zero entities), because
    the regex only matches compliance IDs, standards, and the like, not the
    prose/transcript text that dominates. Without entities there is nothing to
    connect, so relationship extraction alone leaves the graph empty.

    Returns (entities, relationships) where entities is [(label, type)] and
    relationships is [(source_label, target_label, relationship, evidence)].
    Relationship endpoints are guaranteed to be in the returned entity set.
    Empty on any failure, so the caller falls back to the regex path.
    """
    if not text:
        return [], []
    try:
        if router is None:
            from tools.llm.router import LLMRouter

            router = LLMRouter()
        from tools.llm.provider import LLMRequest

        req = LLMRequest(
            messages=[{"role": "user", "content": f"Passage:\n{text[:_MAX_TEXT_CHARS]}"}],
            system_prompt=_GRAPH_SYSTEM_PROMPT,
            max_tokens=1200,
            temperature=0.0,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = router.invoke("kg_relationship_extraction", req)
    except Exception as exc:  # noqa: BLE001 — graceful: any failure -> regex fallback
        logger.debug("LLM graph extraction unavailable: %s", exc)
        return [], []

    if not resp or not getattr(resp, "content", None):
        return [], []

    obj = _parse_json_object(resp.content)
    raw_entities = obj.get("entities") or []
    raw_rels = obj.get("relationships") or []

    # Entities: dedup by label, normalise type, cap.
    entities: list[tuple[str, str]] = []
    label_index: dict[str, int] = {}
    for e in raw_entities:
        if not isinstance(e, dict):
            continue
        name = str(e.get("name") or "").strip()
        if len(name) < 2 or name.lower() in label_index:
            continue
        etype = str(e.get("type") or "concept").strip().lower()
        if etype not in _ETYPE_SET:
            etype = "concept"
        label_index[name.lower()] = len(entities)
        entities.append((name, etype))
        if len(entities) >= _MAX_LLM_ENTITIES:
            break
    if len(entities) < 1:
        return [], []

    # Relationships: endpoints must resolve to extracted entities.
    relationships: list[tuple[str, str, str, str]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for r in raw_rels:
        if not isinstance(r, dict):
            continue
        src = str(r.get("source") or "").strip()
        tgt = str(r.get("target") or "").strip()
        rel = r.get("relationship")
        si = label_index.get(src.lower())
        ti = label_index.get(tgt.lower())
        if si is None or ti is None or si == ti:
            continue
        if not is_valid_relationship(rel) or rel not in _VOCAB_SET:
            continue
        s_label = entities[si][0]
        t_label = entities[ti][0]
        key = (s_label, t_label, rel)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        evidence = str(r.get("evidence") or "")[:_MAX_EVIDENCE_CHARS]
        relationships.append((s_label, t_label, rel, evidence))

    return entities, relationships


def _parse_json_array(raw: str) -> list:
    """Best-effort parse of a model reply that should be a JSON array."""
    raw = raw.strip()
    if raw.startswith("```"):
        # strip a ```json ... ``` fence
        raw = raw.split("```", 2)[1] if raw.count("```") >= 2 else raw.strip("`")
        if raw.lstrip().lower().startswith("json"):
            raw = raw.lstrip()[4:]
    raw = raw.strip()
    if not raw.startswith("["):
        # find the first array in the text
        start = raw.find("[")
        end = raw.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return []
        raw = raw[start : end + 1]
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def extract_relationships_llm(
    text: str,
    entities: list[tuple[str, str]],
    *,
    router: Optional[Any] = None,
) -> list[tuple[str, str, str, str]]:
    """Infer relationships among *entities* supported by *text*, via the LLM.

    Args:
        text: the chunk content.
        entities: list of (label, entity_type) as produced by _extract_entities.
        router: optional LLMRouter (injectable for tests).

    Returns:
        list of (source_label, target_label, relationship, evidence). Empty on
        any failure, so callers fall back to the deterministic extractor.
    """
    if not text or len(entities) < 2:
        return []

    # De-dup by label, preserving input order so the numbering the model sees is
    # stable and predictable. When over the cap, keep the longest (most specific)
    # entities but restore their original order.
    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for label, etype in entities:
        low = label.lower()
        if low in seen:
            continue
        seen.add(low)
        deduped.append((label, etype))
    if len(deduped) > _MAX_ENTITIES:
        keep = set(sorted(range(len(deduped)), key=lambda i: -len(deduped[i][0]))[:_MAX_ENTITIES])
        capped = [e for i, e in enumerate(deduped) if i in keep]
    else:
        capped = deduped
    if len(capped) < 2:
        return []

    numbered = "\n".join(f"{i}. {lbl} ({etype})" for i, (lbl, etype) in enumerate(capped))
    passage = text[:_MAX_TEXT_CHARS]

    try:
        if router is None:
            from tools.llm.router import LLMRouter

            router = LLMRouter()
        from tools.llm.provider import LLMRequest

        req = LLMRequest(
            messages=[{
                "role": "user",
                "content": f"Entities:\n{numbered}\n\nPassage:\n{passage}",
            }],
            system_prompt=_SYSTEM_PROMPT,
            max_tokens=800,
            temperature=0.0,
            skip_injection_scan=True,
            classification="CUI",
        )
        resp = router.invoke("kg_relationship_extraction", req)
    except Exception as exc:  # noqa: BLE001 — graceful: any failure -> heuristic fallback
        logger.debug("LLM relationship extraction unavailable: %s", exc)
        return []

    if not resp or not getattr(resp, "content", None):
        return []

    items = _parse_json_array(resp.content)
    out: list[tuple[str, str, str, str]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    n = len(capped)
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            s_idx = int(it.get("source"))
            t_idx = int(it.get("target"))
        except (TypeError, ValueError):
            continue
        rel = it.get("relationship")
        # Endpoints must be in-range and distinct; the model may not invent nodes.
        if not (0 <= s_idx < n and 0 <= t_idx < n) or s_idx == t_idx:
            continue
        # Label must be clean AND from the offered vocabulary.
        if not is_valid_relationship(rel) or rel not in _VOCAB_SET:
            continue
        src = capped[s_idx][0]
        tgt = capped[t_idx][0]
        key = (src, tgt, rel)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        evidence = str(it.get("evidence") or "")[:_MAX_EVIDENCE_CHARS]
        out.append((src, tgt, rel, evidence))
    return out
