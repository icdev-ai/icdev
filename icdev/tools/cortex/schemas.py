# CUI // SP-CTI
"""ICDEV Cortex unified schemas.

The ICDEV Cortex facade is the platform's Snowflake-Cortex-style unified AI
layer. The four existing search backends (rag, knowledge_graph,
document_intelligence, keyword KB) return four incompatible result shapes;
the dataclasses in this module are the single normalization contract every
backend adapter maps into and every Cortex consumer reads from.

All dataclasses round-trip through ``to_dict()`` / ``from_dict()`` so they
can cross process/JSON boundaries (MCP, dashboard APIs, audit rows)
losslessly. ``from_dict()`` ignores unknown keys, so older callers keep
working when new fields are added.

Patterns follow ``tools/llm/provider.py`` (LLMRequest/LLMResponse) and
``tools/document_intelligence/search_engine.py`` (Citation).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Optional

# The four normalized retrieval backends behind the Cortex facade.
CORTEX_BACKENDS = ("rag", "graph", "dic", "kb")


def _known_fields(cls: type, data: Optional[dict]) -> dict:
    """Filter a dict down to keys that are actual dataclass fields."""
    allowed = {f.name for f in fields(cls)}
    return {k: v for k, v in (data or {}).items() if k in allowed}


@dataclass
class Citation:
    """Provenance pointer for one piece of retrieved evidence.

    Every Cortex answer fragment must be traceable to a source row; uncited
    content is suppressed by the facade, never returned.
    """

    source_id: str = ""
    source_type: str = ""  # e.g. "rag_chunk", "kg_node", "dic_document", "kb_entry"
    source_table: str = ""  # DB table the source row lives in
    title: str = ""
    snippet: str = ""
    url: str = ""
    classification: str = "CUI"
    clearance_required: str = ""
    provenance_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "Citation":
        return cls(**_known_fields(cls, data))


@dataclass
class CortexSearchResult:
    """One backend hit normalized into the unified Cortex shape.

    ``score`` is normalized to [0, 1] (clamped on construction) so results
    from different backends are directly comparable; each backend's native
    scores are preserved verbatim in ``raw_scores``.
    """

    content: str = ""
    score: float = 0.0
    backend: str = ""  # one of CORTEX_BACKENDS
    strategy: str = ""  # backend-specific retrieval strategy (e.g. "bm25", "hybrid")
    citation: Citation = field(default_factory=Citation)
    raw_scores: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.score = min(1.0, max(0.0, float(self.score)))

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "score": round(self.score, 6),
            "backend": self.backend,
            "strategy": self.strategy,
            "citation": self.citation.to_dict(),
            "raw_scores": self.raw_scores,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "CortexSearchResult":
        kwargs = _known_fields(cls, data)
        if isinstance(kwargs.get("citation"), dict):
            kwargs["citation"] = Citation.from_dict(kwargs["citation"])
        return cls(**kwargs)


@dataclass
class GovernanceReport:
    """Record of the governance gates applied to one Cortex invocation."""

    gates_run: list = field(default_factory=list)  # gate names, in execution order
    outcomes: dict = field(default_factory=dict)  # gate name -> "pass" | "warn" | "fail" | "skip"
    redactions_applied: int = 0
    blocked: bool = False
    blocked_reason: str = ""  # populated when blocked is True
    # Semantic content-grounding detail for the content_grounding gate (ctx-01):
    # {"score": float, "method": "heuristic"|"llm"|"no_context"|"placeholder",
    #  "ungrounded_claims": [...], "floor": float}. Empty until that gate runs.
    content_grounding: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "GovernanceReport":
        return cls(**_known_fields(cls, data))


@dataclass
class CortexResult:
    """Final synthesized answer returned by the Cortex facade.

    ``grounded`` is False whenever the text is not fully supported by
    ``citations`` — callers must surface that rather than presenting the
    text as evidence-backed.

    ``data`` carries the structured payload behind the text, when one exists
    (e.g. the Cortex Analyst puts its result rows here: ``{"rows": [...],
    "row_count": N, "iqe": "..."}``). Search-style answers leave it empty.

    ``metadata`` carries TRUST labels about the answer itself (not the
    payload): the analyst records ``confidence`` ("include"/"flag"/"abstain"),
    ``confidence_score``, ``grounding`` mode, and the citation-validation
    report here so governance layers can gate without re-deriving them.
    """

    text: str = ""
    citations: list = field(default_factory=list)  # list[Citation]
    governance: GovernanceReport = field(default_factory=GovernanceReport)
    provider: str = ""
    model: str = ""
    cost: float = 0.0  # USD
    latency_ms: int = 0
    input_tokens: int = 0  # prompt tokens (from LLMResponse accounting)
    output_tokens: int = 0  # completion tokens
    grounded: bool = False
    data: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "citations": [c.to_dict() for c in self.citations],
            "governance": self.governance.to_dict(),
            "provider": self.provider,
            "model": self.model,
            "cost": self.cost,
            "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "grounded": self.grounded,
            "data": self.data,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "CortexResult":
        kwargs = _known_fields(cls, data)
        kwargs["citations"] = [
            Citation.from_dict(c) if isinstance(c, dict) else c
            for c in kwargs.get("citations") or []
        ]
        if isinstance(kwargs.get("governance"), dict):
            kwargs["governance"] = GovernanceReport.from_dict(kwargs["governance"])
        return cls(**kwargs)


@dataclass
class CortexContext:
    """Caller identity and policy context threaded through every Cortex call.

    ``tenant_id`` and ``classification`` are mandatory members from day one
    so RLS predicates and read-down filtering can key off the context without
    a retrofit. ``fail_closed`` mirrors the platform redaction toggle: when
    True, any governance failure blocks the response instead of degrading.
    """

    tenant_id: str = ""
    user_id: str = ""
    classification: str = "CUI"
    domain: str = ""
    session_id: str = ""  # links audit rows to a persisted cortex_sessions row
    # Budget/rate-limit attribution key for the LLM router (check_budget /
    # rate_gate key off LLMRequest.agent_id). Empty -> _build_request derives a
    # per-tenant key so Cortex calls are never billed to an empty/unkeyed bucket.
    agent_id: str = ""
    air_gap: bool = False
    # Tri-state fail-closed posture. None (the default) means "use the platform
    # policy" — governance.fail_closed in args/cortex_config.yaml — resolved via
    # config.resolve_fail_closed(). An explicit True/False from the caller always
    # wins. When effective-True, a gate ERROR or a grounding "fail" blocks the
    # response instead of degrading; injection blocks always, regardless. Note
    # this never blocks a *generative* (retrieval=False) call for merely being
    # ungrounded — those calls skip the grounding gates entirely.
    fail_closed: Optional[bool] = None
    # Trusted first-party content (e.g. a document already inside the tenant
    # boundary that docgen ingests). When True, the input injection screen and
    # input redaction gates are SKIPPED — mirroring the router's long-standing
    # ``LLMRequest.skip_injection_scan=True`` contract for trusted pipeline
    # calls. Output redaction, provenance, and the append-only audit row are
    # STILL applied: trust affects the *input* screen only, never egress or the
    # NIST-AU record. Default False — callers must opt in explicitly.
    trusted_content: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "CortexContext":
        return cls(**_known_fields(cls, data))
