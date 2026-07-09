# CUI // SP-CTI
"""Surface-agnostic citation & provenance utilities for LLM-drafted content.

Companion to ``content_grounding.py``. Where that module removes the
*opportunity* to hallucinate (unresolved placeholders, numeric conflicts),
this module enforces that every factual claim is *attributed* to a real
source and carries data provenance.

The model is extracted from the DIC document generator
(``tools/document_intelligence/doc_generator.py``) so any drafting surface —
RFI workbench, proposals, DIC, Tech Writer — can reuse the same deterministic
pieces instead of each re-implementing citation parsing/validation:

  - parse_citations(text)                 -> source ids cited in the text
  - validate_citations(text, allowed)     -> cited/uncited/hallucinated report
  - citation_gate(sections)               -> per-section blocking findings,
                                             ready for a promote/export gate
  - classify_confidence(score)            -> "include" | "flag" | "abstain"
  - compute_attribution_score(chunk, out) -> token-overlap recall proxy
  - Provenance                            -> per-artifact lineage record

Everything here is pure regex/dict/dataclass — no LLM, no DB, no Flask.
Surface-specific logic (which sources are valid, how sections are stored)
stays in the caller. Citation-tag syntax mirrors DIC's ``[source: chunk <id>]``
and the RAG layer's ``[SOURCE-N]`` so a single parser covers both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── Citation parsing ────────────────────────────────────────────────────────

# DIC / drafting style: "[source: chunk 12ab]" or "[source: KB-42]" — the
# body is captured then split on comma/semicolon and any leading "chunk ".
_SOURCE_RE = re.compile(r"\[source:\s*([^\]]+?)\s*\]", re.IGNORECASE)
# RAG-injection style: "[SOURCE-3]".
_SOURCE_N_RE = re.compile(r"\[SOURCE-(\d+)\]")
_CHUNK_PREFIX_RE = re.compile(r"^chunk\s+", re.IGNORECASE)


def parse_citations(text: str) -> list[str]:
    """Return unique source ids cited in ``text`` (order preserved).

    Recognises both ``[source: chunk <id>]`` / ``[source: <id>]`` and the RAG
    ``[SOURCE-N]`` forms. A single tag may cite several ids separated by
    commas or semicolons: ``[source: chunk a, chunk b]`` -> ["a", "b"].
    """
    if not text:
        return []
    ids: list[str] = []
    for m in _SOURCE_RE.finditer(text):
        for part in re.split(r"[;,]", m.group(1)):
            token = _CHUNK_PREFIX_RE.sub("", part.strip()).strip()
            if token:
                ids.append(token)
    for m in _SOURCE_N_RE.finditer(text):
        ids.append(m.group(1))
    seen: set[str] = set()
    unique: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            unique.append(i)
    return unique


def has_citations(text: str) -> bool:
    """True if ``text`` contains at least one recognised citation tag."""
    return bool(parse_citations(text))


def validate_citations(text: str, allowed_sources) -> dict:
    """Validate citation tags in ``text`` against the sources actually available.

    Args:
        text: draft content containing ``[source: ...]`` / ``[SOURCE-N]`` tags.
        allowed_sources: either an ``int`` count (ids "1".."N", the RAG
            injected-source convention) or an iterable of valid source ids.

    Returns a report dict:
        {cited_count, available_count, citation_rate,
         uncited_sources[], hallucinated_citations[], valid}
    where ``valid`` is True when no citation references an unavailable source
    (a hallucinated citation). Presence/absence of citations is intentionally
    NOT part of ``valid`` — that policy belongs to ``citation_gate``.
    """
    cited = set(parse_citations(text))
    if isinstance(allowed_sources, bool):  # guard: bool is an int subclass
        available: set[str] = set()
    elif isinstance(allowed_sources, int):
        available = {str(i + 1) for i in range(max(allowed_sources, 0))}
    else:
        available = {str(s) for s in (allowed_sources or [])}

    matched = cited & available
    hallucinated = sorted(cited - available)
    return {
        "cited_count": len(matched),
        "available_count": len(available),
        "citation_rate": len(matched) / max(len(available), 1),
        "uncited_sources": sorted(available - cited),
        "hallucinated_citations": hallucinated,
        "valid": not hallucinated,
    }


def citation_gate(
    sections: list[dict],
    *,
    content_keys: tuple[str, ...] = ("content", "ai_draft"),
    require_citations: bool = True,
) -> list[dict]:
    """Scan sections for citation defects. Empty list == gate passes.

    Each section dict may carry:
      - content under one of ``content_keys``
      - an identifying field (item_number / title / id — first present is used)
      - ``abstained`` (bool): abstained sections make no claims, so are skipped
      - ``allowed_sources`` (iterable or int): when present, citations to any
        id outside this set are flagged as hallucinated

    Returns ``[{item_number, issue, detail}]`` where ``issue`` is one of
    ``"missing_citations"`` (require_citations and none present) or
    ``"hallucinated_citation"`` (cites an unavailable source). Mirrors the
    shape of ``content_grounding.placeholder_findings`` so a promote/export
    gate can treat both symmetrically.
    """
    findings: list[dict] = []
    for sec in sections:
        if sec.get("abstained"):
            continue
        content = ""
        for key in content_keys:
            if sec.get(key):
                content = sec[key]
                break
        if not content:
            continue
        label = sec.get("item_number") or sec.get("title") or sec.get("id") or "?"

        allowed = sec.get("allowed_sources")
        if allowed is not None:
            report = validate_citations(content, allowed)
            if report["hallucinated_citations"]:
                findings.append({
                    "item_number": label,
                    "issue": "hallucinated_citation",
                    "detail": report["hallucinated_citations"],
                })
        if require_citations and not parse_citations(content):
            findings.append({
                "item_number": label,
                "issue": "missing_citations",
                "detail": [],
            })
    return findings


# ── Confidence bands ──────────────────────────────────────────────────────────
# Mirrors the DIC doc_generator thresholds: >=0.7 include, 0.4-0.69 include +
# HITL flag, <0.4 abstain. Kept here so every surface uses the same bands.

CONF_INCLUDE = 0.7
CONF_ABSTAIN = 0.4


def classify_confidence(score: float) -> str:
    """Map a [0,1] confidence score to "include" | "flag" | "abstain"."""
    if score >= CONF_INCLUDE:
        return "include"
    if score >= CONF_ABSTAIN:
        return "flag"
    return "abstain"


# ── Attribution scoring ────────────────────────────────────────────────────────


def _tokenize(text: str) -> set[str]:
    """Lowercase word tokens (punctuation stripped)."""
    return set(re.findall(r"\b\w+\b", text.lower()))


def compute_attribution_score(chunk_text: str, output_text: str) -> float:
    """Token-overlap recall: |chunk ∩ output| / |chunk|, rounded to 4 dp.

    A deterministic proxy for how much of a source chunk actually appears in
    the generated output — no LLM call. Returns 0.0 when either input is empty
    or the chunk has no tokens. This is the same measure DIC's
    ``provenance_adapter`` uses; that adapter should delegate here.
    """
    if not chunk_text or not output_text:
        return 0.0
    chunk_tokens = _tokenize(chunk_text)
    if not chunk_tokens:
        return 0.0
    return round(len(chunk_tokens & _tokenize(output_text)) / len(chunk_tokens), 4)


# ── Provenance record ──────────────────────────────────────────────────────────


@dataclass
class Provenance:
    """Per-source lineage attached to a generated artifact.

    Field names match DIC's ``provenance_adapter.get_chunk_provenance`` output
    so records can be built from a ledger row without remapping.
    """

    source_id: str = ""
    sha256: str = ""
    classification: str = "CUI"
    version_ref: str = ""
    ingest_timestamp: str = ""
    attribution_score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "sha256": self.sha256,
            "classification": self.classification,
            "version_ref": self.version_ref,
            "ingest_timestamp": self.ingest_timestamp,
            "attribution_score": self.attribution_score,
        }

    @classmethod
    def from_ledger_row(cls, row: dict) -> "Provenance":
        """Build from a ``rag_provenance_ledger`` row (or the adapter's dict)."""
        return cls(
            source_id=row.get("source_id") or row.get("chunk_uuid", ""),
            sha256=row.get("sha256") or row.get("sha256_hash", ""),
            classification=row.get("classification")
            or row.get("classification_label", "CUI"),
            version_ref=row.get("version_ref") or row.get("version_tree_ref", ""),
            ingest_timestamp=row.get("ingest_timestamp", ""),
            attribution_score=float(row.get("attribution_score", 0.0) or 0.0),
        )


@dataclass
class ArtifactProvenance:
    """Provenance bundle for a whole generated artifact (section/draft/doc)."""

    artifact_id: str = ""
    generation_model: str = ""
    method: str = ""
    sources: list[Provenance] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "generation_model": self.generation_model,
            "method": self.method,
            "sources": [s.to_dict() for s in self.sources],
        }


def build_artifact_provenance(
    artifact_id: str,
    sources,
    *,
    generation_model: str = "",
    method: str = "",
) -> ArtifactProvenance:
    """Standardized factory for the per-artifact provenance object (trust-cite-04).

    One way for every drafting surface (RFI/proposals/DIC/Tech Writer) to build
    the provenance bundle attached to a generated artifact. ``sources`` may be
    Provenance instances, ledger-row dicts, or bare source-id strings — each is
    normalized to a Provenance. Pure: constructs the object; persistence (e.g.
    a prov_recorder lineage write) is the caller's concern.
    """
    norm: list[Provenance] = []
    for s in sources or []:
        if isinstance(s, Provenance):
            norm.append(s)
        elif isinstance(s, dict):
            norm.append(Provenance.from_ledger_row(s))
        elif s:
            norm.append(Provenance(source_id=str(s)))
    return ArtifactProvenance(
        artifact_id=artifact_id,
        generation_model=generation_model,
        method=method,
        sources=norm,
    )
