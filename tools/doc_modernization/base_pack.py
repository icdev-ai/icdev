# CUI // SP-CTI
"""Document Modernization Engine — domain pack interface.

A DomainPack turns document text into currency findings for one domain
(network hardware, software, crypto/protocols, policy references, ...).
Packs are declared in ``args/docmod/packs/*.yaml`` and loaded by
``pack_loader.load_packs()``.

TRUST rule 1 applies to every implementation: ``evaluate()`` must derive its
verdict from deterministic evidence (catalog rows, EOL dates, rulebook
matches, inventory counts) — never from an LLM. The LLM's only later role is
wording the redline prose around a verdict produced here.
"""
from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ChunkRef:
    """Location of a text chunk inside a DIC document version."""

    doc_id: str
    version_id: str
    chunk_link_id: str | None = None
    page: int | None = None
    section: str | None = None


@dataclass
class CandidateEntity:
    """An entity a pack's extractor matched in document text."""

    label: str                       # canonical matched text, e.g. "Catalyst 6500"
    entity_type: str                 # e.g. "hardware_model" (constants.KG_ENTITY_TYPES)
    pack_id: str
    chunk_ref: ChunkRef
    raw_match: str = ""              # the literal text matched
    context: str = ""                # surrounding text for reviewer display
    attributes: dict = field(default_factory=dict)  # e.g. {"vendor": "Cisco", "version": "2012"}


@dataclass
class Verdict:
    """Deterministic currency assessment for one CandidateEntity.

    ``evidence`` entries are citation-shaped dicts —
    ``{"source": "<id>", "detail": "...", "date": "..."}`` — so
    tools/quality/citation_grounding.validate_citations can gate any redline
    drafted from this finding.
    """

    currency_verdict: str            # constants.CURRENCY_VERDICTS
    finding_type: str | None = None  # constants.FINDING_TYPES; None => no finding
    severity: str = "medium"
    rationale: str = ""
    confidence: float = 1.0          # deterministic evidence defaults to certain
    evidence: list[dict] = field(default_factory=list)

    @property
    def is_finding(self) -> bool:
        return self.finding_type is not None and self.currency_verdict != "current"


@dataclass
class Replacement:
    """A recommended replacement — always an existing catalog/learner entry,
    never invented (TRUST rule 1)."""

    label: str                       # e.g. "Catalyst 9500"
    source: str                      # 'catalog' | 'defacto' | 'rulebook'
    source_ref: str = ""             # entry_id / rule id backing the recommendation
    detail: str = ""
    evidence: list[dict] = field(default_factory=list)


class DomainPack(ABC):
    """One domain's extract → evaluate → recommend pipeline."""

    pack_id: str = ""
    label: str = ""
    entity_types: list[str] = []
    config: dict = {}

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.pack_id = self.config.get("pack_id", self.pack_id or self.__class__.__name__)
        self.label = self.config.get("label", self.label or self.pack_id)
        self.entity_types = list(self.config.get("entity_types", self.entity_types))

    @abstractmethod
    def extract(self, text: str, chunk_ref: ChunkRef) -> list[CandidateEntity]:
        """Match domain entities in one chunk of document text."""

    @abstractmethod
    def evaluate(self, entity: CandidateEntity, conn) -> Verdict:
        """Deterministic currency verdict for an entity (no LLM — TRUST)."""

    def recommend(self, entity: CandidateEntity, verdict: Verdict, conn) -> Replacement | None:
        """Optional replacement recommendation from curated/learned evidence."""
        return None

    def evidence_snapshot(self, conn) -> str:
        """sha256 of this pack's evidence state — unchanged hash lets the
        scanner skip unchanged documents on incremental sweeps. Default:
        hash of the pack config (static packs re-scan only when config changes)."""
        payload = json.dumps(self.config, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
