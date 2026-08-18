# CUI // SP-CTI
"""ICDEV Cortex unified schemas.

The ICDEV Cortex facade is the platform's Snowflake-Cortex-style unified AI
layer. The four retrieval backends (rag, knowledge_graph,
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

# Every normalized backend behind the Cortex facade.
# ``currency`` (cef-bck-01) answers "is this entity still current?" over the
# entity-currency store and the learned de-facto standards; it is a retrieval
# backend like the first four. ``external`` (cef-bck-02) retrieves too — a feed
# item existed before the query and can be re-read — but it is the only backend
# whose evidence comes from OUTSIDE the boundary: it reads authorized external
# sources through the DataBridge agent broker, inheriting that broker's
# authorization model whole rather than restating any part of it. That makes it
# EVIDENTIARY and separately governed, which are different axes; the second one
# lives in the rung, not in this tuple. ``sme`` (cef-bck-03) is not a retrieval
# backend at all — see below.
CORTEX_BACKENDS = ("rag", "graph", "dic", "kb", "currency", "external", "sme")

# The split inside CORTEX_BACKENDS, and the reason it exists (cef-bck-03).
#
# Every backend but ``sme`` RETRIEVES: every hit is a row that existed before the
# query and can be re-read. ``sme`` does not — it asks an ACE domain-expert
# persona for an OPINION, which the model authors at query time. Both shapes
# normalize into CortexSearchResult, so nothing downstream can tell them apart
# from the dataclass alone; this tuple pair is what tells them apart.
#
# base_pack TRUST rule 1 requires a verdict to derive from deterministic
# evidence and never from an LLM. An advisory backend therefore:
#   * is NEVER selected automatically — not by ``strategy="all"``, not by
#     ``search_all()``'s default, not by a ``ROUTE_LABEL_BACKENDS`` label, and
#     not by ``search.fan_out.backends``. A caller reaches it only by naming it.
#   * carries ``metadata["advisory"] = True`` on every result, which
#     ``is_advisory()`` reads (tools/cortex/search_service.py).
#   * weighs 0.0 in RRF (``search.strategy_weights.sme``), so it can never
#     outrank an evidentiary hit in a fused list.
#
# Adding a backend to ADVISORY_BACKENDS is the whole opt-out: EVIDENTIARY_BACKENDS
# is derived, so it cannot drift from it.
ADVISORY_BACKENDS = ("sme",)
EVIDENTIARY_BACKENDS = tuple(b for b in CORTEX_BACKENDS if b not in ADVISORY_BACKENDS)


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
    # KG-grounding detail for the kg_grounding gate (trust-kg-03):
    # {"status": "ok"|"kg_unmeasurable", "schema_source": "declared"|"observed"|
    #  "unavailable", "counts": {...}, "unknown_entities": [...],
    #  "findings": [...]}. Empty until that gate runs — and it is OPT-IN, so it
    # stays empty for every profile that does not declare it.
    #
    # `schema_source` is the field that decides what the verdict is worth:
    # kg_ontology ships empty, so in practice the schema is OBSERVED, under which
    # an unrecognised triple is UNATTESTED (warns) and never CONTRADICTED
    # (fails). A consumer reading a clean kg verdict without reading this key is
    # reading more assurance than the gate offered.
    kg_grounding: dict = field(default_factory=dict)
    # Governance profile this call ran under (hgx-gov-01). "default" is the full
    # gate chain minus the opt-in gates — what every caller that names no profile
    # gets. A narrower profile shows up as `skip` outcomes whose detail names it.
    profile: str = "default"
    # Wall-clock cost of the governed call, in milliseconds (ctx-obs-02).
    # ``CortexResult.latency_ms`` times the LLM call ONLY, so until these fields
    # existed the seven-gate chain's own cost was unmeasured: "how much of a
    # Cortex call is governance?" — the question that decides whether the TRUST
    # chain is worth its cost, and whether perf work should target the gates or
    # the model call — could not be answered at all.
    #
    # total_ms      the whole governed call as measured by GovernancePipeline.
    #               Measured up to (not including) the audit write that records
    #               it — a write cannot be inside its own measurement.
    # operation_ms  the wrapped operation alone (the LLM / retrieval call).
    # gate_ms       per-gate wall time, keyed like ``outcomes``. Segment
    #               boundaries are the gates, so the glue between two gates is
    #               charged to the one that follows and the values sum to
    #               ``total_ms`` (a call blocked mid-gate sums to less: the
    #               interrupted segment is never closed).
    # All three are 0.0 on a report that was never run through ``wrap`` and on
    # rows written before ctx-obs-02 — consumers must treat 0 as "not measured"
    # rather than "instant".
    total_ms: float = 0.0
    operation_ms: float = 0.0
    gate_ms: dict = field(default_factory=dict)
    # Provider spend the RESULT does not carry (ctx-obs-01). `search` returns a
    # list and `govern` returns a str, so keying accounting on
    # isinstance(result, CortexResult) left the most expensive facade there is
    # contributing nothing to cost_usd or by_model - the spend panel was missing
    # its biggest consumer. GovernancePipeline populates this from every router
    # call made inside the governed operation; {} when nothing was attributed.
    llm_tally: dict = field(default_factory=dict)

    @property
    def governance_ms(self) -> float:
        """Wall time spent in the gates rather than in the wrapped operation.

        This is the number the chain is judged on. Derived rather than stored so
        it cannot drift from its two operands; ``0.0`` when the call was not
        timed (both operands 0) and clamped at 0 so clock noise on a sub-
        millisecond call never reports negative overhead.
        """
        return round(max(0.0, self.total_ms - self.operation_ms), 3)

    def to_dict(self) -> dict:
        data = asdict(self)
        # asdict() sees fields only; the derived overhead is what most consumers
        # actually read, so surface it explicitly. from_dict() drops unknown
        # keys, so the round-trip stays lossless.
        data["governance_ms"] = self.governance_ms
        return data

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
    # Service-key scopes when the caller presented one (rest_v1 copies them off
    # ``g.cortex_binding``); None when no key was presented at all — a
    # session-authenticated dashboard user, an in-process caller, a reflex.
    #
    # Tri-state on purpose, and the third state is the point: None means "no key
    # was presented, so this context carries no scope claim" and defers to
    # whatever the downstream rung's own authorization is, while an EMPTY LIST
    # means "a key WAS presented and it carries no scopes" — a denial, not an
    # absence. Collapsing the two would make an unscoped key indistinguishable
    # from a trusted internal caller, which is the wrong direction to guess in.
    #
    # Read by the ``external`` search backend (cef-bck-02), which requires
    # ``databridge:<connector>:read``; that scope is never in DEFAULT_SCOPES, so
    # reaching an external source over REST is an explicit grant.
    scopes: Optional[list] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "CortexContext":
        return cls(**_known_fields(cls, data))
