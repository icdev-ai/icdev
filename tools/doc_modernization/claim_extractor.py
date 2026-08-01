# CUI // SP-CTI
"""Document Modernization Engine — semantic claim extraction (dmx-claims-02, Phase B).

A *claim* is a typed ``(subject, predicate, object)`` proposition anchored to a
VERBATIM char span in a specific approved ``dic_versions`` row. Extraction turns
document prose into claims that a human reviews (HITL) and that a later,
deterministic finding can flag at sentence granularity.

Design authority: ``docs/design/dmx-claims-tracking-spike.md`` (human-approved).

Deterministic-first (TRUST rule 1, non-negotiable): the LLM only *proposes* claim
structure. It NEVER evaluates whether a claim is valid — there is no
``evaluate()`` / verdict logic in this module. Validity linkage is a later phase.

Two candidate sources, one anchoring gate:

  * **LLM path** — reuses the PR-#318 engine
    ``tools.knowledge_graph.llm_relationship_extractor.extract_graph_llm``: its
    typed ``(subject, relation, object, evidence)`` triples are the claim
    candidates. We do NOT extend the KG (``kg_nodes``/``kg_edges``) schema
    (spike §5) — we reuse the extraction *code path* and persist to ``dic_claims``.
  * **No-LLM / air-gap path** — reuses the crypto rulebook regexes
    (``packs.crypto_protocols``): a rulebook hit (``TLS 1.1``, ``MD5``) yields a
    claim whose subject is the matched token and whose span is the match.
    Deterministic, lower recall, $0 LLM (spike §2.3).

**The load-bearing gate** (spike §2.2): every candidate's ``claim_text`` MUST be
located verbatim via ``text.find(claim_text)`` in the chunk's source text. A
candidate that cannot be found verbatim is REJECTED — an LLM that paraphrased or
invented a sentence cannot produce a claim. The span, not the model output, is
the claim's identity.

**Gated by ``claims.enabled`` (default OFF)**: with the toggle off,
``extract_and_persist_claims`` is a complete no-op — zero LLM calls, zero rows.

Append-only: ``dic_claims`` state changes are NEW rows whose ``supersedes_id``
points at the prior row for the same ``dedupe_key`` (latest-wins resolution
mirrors ``scanner._open_findings``). Claims land ``status='pending_review'``.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from tools.knowledge_graph.llm_relationship_extractor import extract_graph_llm
from tools.logging.icdev_logger import get_logger
from tools.quality.citation_grounding import classify_confidence

from .base_pack import ChunkRef

logger = get_logger(__name__)

# Provenance constant — bump when the claim-extraction contract changes.
CLAIM_PROMPT_VERSION = "claim_extract_v1"

# Base confidence assigned to a candidate by source. The verbatim-anchor gate is
# the hard proof of grounding; confidence is a soft, per-source prior. LLM
# proposals are grounded-but-fallible; rulebook hits are byte-exact regex matches.
LLM_CANDIDATE_CONFIDENCE = 0.9
NO_LLM_CANDIDATE_CONFIDENCE = 1.0

PENDING_STATUS = "pending_review"
NO_LLM_MODEL = "no_llm_rulebook"

# Default pack domain this Phase-B build is scoped to (spike §8: crypto only).
DEFAULT_PACK_DOMAIN = "crypto_protocols"

# Predicate used for a bare no-LLM rulebook hit (subject token, no object): the
# document *references* the deprecated token. Controlled verb, kept generic.
NO_LLM_PREDICATE = "references"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ClaimCandidate:
    """A proposed claim before anchoring/persistence.

    ``claim_text`` is the verbatim span the persistence step will locate via
    ``str.find``; a candidate whose ``claim_text`` is not found verbatim is
    rejected. ``confidence`` is gated at ``CONF_INCLUDE`` (0.7).
    """

    subject_label: str
    predicate: str
    claim_text: str
    subject_type: Optional[str] = None
    object_label: Optional[str] = None
    object_type: Optional[str] = None
    confidence: float = LLM_CANDIDATE_CONFIDENCE
    prov_model: str = "llm"
    prov_prompt_version: str = CLAIM_PROMPT_VERSION
    attributes: dict = field(default_factory=dict)


def dedupe_key(doc_id: str, subject_label: str, predicate: str,
               object_label: str | None) -> str:
    """sha256(doc_id|subject|predicate|object) — stable across versions.

    Mirrors ``scanner.dedupe_key`` shape so a claim's identity is version-
    independent: the same proposition in v1 and v2 hashes identically, which is
    what lets the append-only supersede chain track it across revisions.
    """
    raw = (
        f"{doc_id}"
        f"|{(subject_label or '').lower().strip()}"
        f"|{(predicate or '').lower().strip()}"
        f"|{(object_label or '').lower().strip()}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def anchor(claim_text: str, source_text: str, base_offset: int = 0) -> Optional[tuple[int, int]]:
    """The load-bearing verbatim gate (spike §2.2).

    Return ``(anchor_start, anchor_end)`` = ``base_offset`` + local match offsets
    when ``claim_text`` appears verbatim in ``source_text``; ``None`` otherwise.
    A ``None`` return means REJECT the candidate — the anti-hallucination guard.
    """
    if not claim_text or not source_text:
        return None
    idx = source_text.find(claim_text)
    if idx == -1:
        return None
    return (base_offset + idx, base_offset + idx + len(claim_text))


def _include(confidence: float) -> bool:
    """Confidence gate — mirrors ``citation_grounding.classify_confidence``.

    Candidates below ``CONF_INCLUDE`` (0.7) never persist / never reach a human.
    """
    try:
        return classify_confidence(float(confidence)) == "include"
    except (TypeError, ValueError):
        return False


def _llm_model_label(router: Any) -> str:
    """Best-effort LLM model id for provenance; falls back to a generic label."""
    try:
        if router is None:
            from tools.llm.router import LLMRouter

            router = LLMRouter()
        getter = getattr(router, "get_provider_for_function", None)
        if callable(getter):
            val = getter("kg_relationship_extraction")
            if val:
                return str(val)
    except Exception:  # noqa: BLE001 — provenance label is best-effort, never fatal
        pass
    return "llm"


def extract_claim_candidates(text: str, *, router: Optional[Any] = None) -> list[ClaimCandidate]:
    """LLM path: reuse ``extract_graph_llm`` typed triples as claim candidates.

    Each ``(subject, relation, object, evidence)`` triple becomes a candidate
    whose ``claim_text`` is the model's short verbatim ``evidence`` quote — which
    the persistence step then anchors with ``str.find`` (candidates whose quote
    is not verbatim are dropped there). Returns ``[]`` on any extractor failure
    (air-gap / no LLM) so the caller can fall back to the no-LLM path.
    """
    if not text:
        return []
    try:
        entities, relationships = extract_graph_llm(text, router=router)
    except Exception as exc:  # noqa: BLE001 — graceful: fall back to no-LLM path
        logger.debug("claim extraction: LLM extractor unavailable: %s", exc)
        return []
    if not relationships:
        return []
    type_map = {str(lbl).lower(): etype for lbl, etype in (entities or [])}
    model = _llm_model_label(router)
    out: list[ClaimCandidate] = []
    for rel in relationships:
        try:
            subject, obj, predicate, evidence = rel
        except (ValueError, TypeError):
            continue
        evidence = (evidence or "").strip()
        if not subject or not predicate or not evidence:
            continue
        out.append(ClaimCandidate(
            subject_label=str(subject),
            predicate=str(predicate),
            claim_text=evidence,
            subject_type=type_map.get(str(subject).lower()),
            object_label=str(obj) if obj else None,
            object_type=type_map.get(str(obj).lower()) if obj else None,
            confidence=LLM_CANDIDATE_CONFIDENCE,
            prov_model=model,
        ))
    return out


def extract_claim_candidates_no_llm(text: str) -> list[ClaimCandidate]:
    """Air-gap / no-LLM path (spike §2.3): rulebook-anchored spans only.

    Reuses the crypto pack's deterministic regex extractor. Each rulebook hit
    yields a claim whose subject is the matched token, ``claim_text`` is that
    verbatim token, and predicate is a generic ``references``. $0 LLM, bounded
    regex cost, lower recall.
    """
    if not text:
        return []
    from .packs.crypto_protocols import CryptoProtocolsPack

    pack = CryptoProtocolsPack(config={"pack_id": DEFAULT_PACK_DOMAIN})
    ref = ChunkRef(doc_id="", version_id="")
    out: list[ClaimCandidate] = []
    for entity in pack.extract(text, ref):
        token = (entity.raw_match or entity.label or "").strip()
        if not token:
            continue
        out.append(ClaimCandidate(
            subject_label=entity.label,
            predicate=NO_LLM_PREDICATE,
            claim_text=token,
            subject_type=entity.entity_type,
            object_label=None,
            object_type=None,
            confidence=NO_LLM_CANDIDATE_CONFIDENCE,
            prov_model=NO_LLM_MODEL,
            attributes={"rule_id": entity.attributes.get("rule_id")},
        ))
    return out


def _latest_claims(conn, doc_id: str) -> dict[str, dict]:
    """Latest-state claims for a doc keyed by dedupe_key (mirrors _open_findings)."""
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM dic_claims WHERE doc_id = %s ORDER BY extracted_at, claim_id",
        (doc_id,),
    ).fetchall()]
    latest: dict[str, dict] = {}
    for r in rows:  # ascending — later rows supersede earlier ones for the same key
        if r.get("dedupe_key"):
            latest[r["dedupe_key"]] = r
    return latest


def _insert_claim(conn, *, claim_id: str, doc_id: str, version_id: str, candidate: ClaimCandidate,
                  anchor_start: int, anchor_end: int, key: str, status: str,
                  supersedes_id: str | None, section: str | None, chunk_link_id: str | None,
                  page: int | None, pack_domain: str, linked_evidence_ids: list | None,
                  tenant_id: str | None, classification: str | None) -> str:
    conn.execute(
        """INSERT INTO dic_claims
           (claim_id, doc_id, version_id, section, chunk_link_id, page,
            claim_text, anchor_start, anchor_end, subject_label, subject_type,
            predicate, object_label, object_type, pack_domain, linked_evidence_ids,
            status, supersedes_id, dedupe_key, prov_model, prov_prompt_version,
            extracted_at, confidence, tenant_id, classification)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            claim_id, doc_id, version_id, section, chunk_link_id, page,
            candidate.claim_text, anchor_start, anchor_end,
            candidate.subject_label, candidate.subject_type,
            candidate.predicate, candidate.object_label, candidate.object_type,
            pack_domain,
            json.dumps(linked_evidence_ids) if linked_evidence_ids is not None else None,
            status, supersedes_id, key, candidate.prov_model,
            candidate.prov_prompt_version, _now(), float(candidate.confidence),
            tenant_id or "default", classification or "CUI",
        ),
    )
    return claim_id


def persist_claims(conn, doc_id: str, version_id: str, source_text: str,
                   candidates: list[ClaimCandidate], *, base_offset: int = 0,
                   section: str | None = None, chunk_link_id: str | None = None,
                   page: int | None = None, pack_domain: str = DEFAULT_PACK_DOMAIN,
                   tenant_id: str | None = None, classification: str | None = None,
                   existing: dict[str, dict] | None = None) -> list[str]:
    """Gate, anchor, dedupe and persist candidates as ``pending_review`` claims.

    Gates applied in order:
      1. Confidence — ``< CONF_INCLUDE`` (0.7) dropped.
      2. Verbatim anchor — ``claim_text`` not found verbatim in ``source_text``
         dropped (the load-bearing anti-hallucination gate).
      3. Dedupe — a claim already present for the same ``dedupe_key`` is skipped
         (idempotent re-extraction of the same version).

    Returns the list of newly inserted ``claim_id``s.
    """
    if existing is None:
        existing = _latest_claims(conn, doc_id)
    seen: set[str] = set()
    inserted: list[str] = []
    for cand in candidates:
        if not _include(cand.confidence):
            continue
        span = anchor(cand.claim_text, source_text, base_offset)
        if span is None:
            continue  # non-verbatim → reject (anti-hallucination)
        key = dedupe_key(doc_id, cand.subject_label, cand.predicate, cand.object_label)
        if key in seen or key in existing:
            seen.add(key)
            continue
        seen.add(key)
        claim_id = f"clm-{uuid.uuid4().hex[:12]}"
        _insert_claim(
            conn, claim_id=claim_id, doc_id=doc_id, version_id=version_id,
            candidate=cand, anchor_start=span[0], anchor_end=span[1], key=key,
            status=PENDING_STATUS, supersedes_id=None, section=section,
            chunk_link_id=chunk_link_id, page=page, pack_domain=pack_domain,
            linked_evidence_ids=None, tenant_id=tenant_id, classification=classification,
        )
        inserted.append(claim_id)
    return inserted


def supersede_claim(conn, prior: dict, new_status: str, *,
                    linked_evidence_ids: list | None = None) -> str:
    """Append a NEW claim row that supersedes ``prior`` with ``new_status``.

    Append-only state transition (never mutate the prior row). Carries the same
    verbatim anchor / span / subject so the sentence identity is preserved. Used
    by the Phase-D finding→claim linkage (this Phase-B build ships the mechanism
    + latest-wins resolution so the append-only chain is testable now).
    """
    valid = {"pending_review", "active", "invalidated", "superseded"}
    if new_status not in valid:
        raise ValueError(f"invalid claim status: {new_status!r}")
    claim_id = f"clm-{uuid.uuid4().hex[:12]}"
    ev = linked_evidence_ids
    if ev is None and prior.get("linked_evidence_ids"):
        try:
            ev = json.loads(prior["linked_evidence_ids"])
        except (json.JSONDecodeError, TypeError):
            ev = None
    conn.execute(
        """INSERT INTO dic_claims
           (claim_id, doc_id, version_id, section, chunk_link_id, page,
            claim_text, anchor_start, anchor_end, subject_label, subject_type,
            predicate, object_label, object_type, pack_domain, linked_evidence_ids,
            status, supersedes_id, dedupe_key, prov_model, prov_prompt_version,
            extracted_at, confidence, tenant_id, classification)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            claim_id, prior["doc_id"], prior["version_id"], prior.get("section"),
            prior.get("chunk_link_id"), prior.get("page"), prior["claim_text"],
            prior["anchor_start"], prior["anchor_end"], prior["subject_label"],
            prior.get("subject_type"), prior["predicate"], prior.get("object_label"),
            prior.get("object_type"), prior.get("pack_domain"),
            json.dumps(ev) if ev is not None else None,
            new_status, prior["claim_id"], prior.get("dedupe_key"),
            prior.get("prov_model"), prior.get("prov_prompt_version"), _now(),
            prior.get("confidence") if prior.get("confidence") is not None else 1.0,
            prior.get("tenant_id") or "default", prior.get("classification") or "CUI",
        ),
    )
    return claim_id


def _claims_enabled(config: dict) -> bool:
    return bool((config.get("claims") or {}).get("enabled", False))


def _pack_domains(config: dict) -> list[str]:
    domains = (config.get("claims") or {}).get("pack_domains") or [DEFAULT_PACK_DOMAIN]
    return list(domains)


def extract_and_persist_claims(conn, doc_id: str, version_id: str,
                               chunks: list[tuple[str, ChunkRef]], *,
                               router: Optional[Any] = None, config: Optional[dict] = None,
                               tenant_id: str | None = None, classification: str | None = None,
                               use_llm: bool = True, force: bool = False) -> dict:
    """Toggle-gated entry point: extract + persist claims for one approved version.

    **No-op when disabled** (``claims.enabled`` false, the default): returns
    immediately WITHOUT calling the extractor — zero LLM calls, zero rows.

    ``crypto_protocols``-only in this Phase-B build. ``chunks`` is the
    ``(text, ChunkRef)`` list the scanner already iterated for this version, so no
    extra chunk fetch. Incremental by construction: the scanner reaches this call
    only when a NEW approved version (or changed evidence) is seen — the
    ``docmod_doc_scan_state`` skip fires first (spike §6). ``force`` bypasses the
    per-version idempotency guard.
    """
    if config is None:
        from .pack_loader import load_config

        config = load_config()
    if not _claims_enabled(config):
        return {"enabled": False, "claims_new": 0, "reason": "toggle_off"}

    domains = _pack_domains(config)
    if DEFAULT_PACK_DOMAIN not in domains:
        # Phase B is crypto-only; nothing to do if crypto is not a claim domain.
        return {"enabled": True, "claims_new": 0, "reason": "no_crypto_domain"}

    existing = _latest_claims(conn, doc_id)
    if existing and not force and any(
        r.get("version_id") == version_id for r in existing.values()
    ):
        # Claims already extracted for this approved version — idempotent skip.
        return {"enabled": True, "claims_new": 0, "reason": "version_unchanged"}

    total_new = 0
    for text, ref in chunks:
        if not text:
            continue
        candidates = extract_claim_candidates(text, router=router) if use_llm else []
        if not candidates:
            # Air-gap / LLM-empty → deterministic rulebook fallback.
            candidates = extract_claim_candidates_no_llm(text)
        if not candidates:
            continue
        inserted = persist_claims(
            conn, doc_id, version_id, text, candidates,
            section=getattr(ref, "section", None),
            chunk_link_id=getattr(ref, "chunk_link_id", None),
            page=getattr(ref, "page", None),
            pack_domain=DEFAULT_PACK_DOMAIN,
            tenant_id=tenant_id, classification=classification,
            existing=existing,
        )
        total_new += len(inserted)
    return {"enabled": True, "claims_new": total_new, "version_id": version_id}
