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

# ── Publish gates ─────────────────────────────────────────────────────────────

#: The publish/export gates that can block a promotion, and whose HITL
#: force-overrides are recorded in the append-only ``idr_publish_audit`` table.
#:
#: This is the SOURCE OF TRUTH for that table's ``gate`` CHECK constraint —
#: CLAUDE.md requires SQL CHECK constraints to derive from a Python constant
#: rather than hardcode their values, and until now this one did not have one.
#: ``tests/test_publish_gates.py`` asserts the constant and the SQL agree, so
#: adding a gate here without a migration fails loudly instead of surfacing as
#: a constraint violation the first time someone overrides that gate.
PUBLISH_GATES: tuple[str, ...] = (
    "citation_guard",       # inline citations missing, or citing unretrieved evidence
    "placeholder_guard",    # unresolved [PLACEHOLDER] tokens
    "cove_guard",           # Chain-of-Verification found a claim needing revision
    "claim_guard",          # a claim's own cited span does not contain what it asserts
    "constitution_guard",   # a mandatory constitutional rule failed and stayed failed
    "kg_guard",             # a claim asserts a relation the knowledge graph forbids
)
# Guards are added here by the phase that WIRES them, together with the
# migration that widens the CHECK — never in advance. A gate value nothing can
# emit is the declared-but-unconsumed defect this module exists to catch, and it
# would also make `idr_publish_audit.gate` accept a value no reviewer can
# produce. structure_guard is deliberately absent until its guard ships.


def publish_gate_check_sql(column: str = "gate") -> str:
    """Render the CHECK body for ``PUBLISH_GATES``.

    Kept next to the constant so a migration can be written against it and the
    test can compare rendered-vs-stored rather than eyeballing two lists.
    """
    values = ",".join(f"'{g}'" for g in sorted(PUBLISH_GATES))
    return f"{column} IN ({values})"


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
    #: How the artifact relates to this source: "verbatim", "derived-text" or
    #: "derived-numeric" (see ``tools.quality.derivation``). Empty means not
    #: classified — deliberately NOT defaulted to "verbatim", which would assert
    #: a quotation nobody checked.
    derivation: str = ""

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "sha256": self.sha256,
            "classification": self.classification,
            "version_ref": self.version_ref,
            "ingest_timestamp": self.ingest_timestamp,
            "attribution_score": self.attribution_score,
            "derivation": self.derivation,
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


# ── Claim-level grounding ─────────────────────────────────────────────────────
#
# Everything above answers "does this citation point at a source that exists?".
# That is a structural check: `validate_citations` compares cited ids against the
# ids that were offered, and `compute_attribution_score` measures how much of a
# chunk resurfaced in the output. Neither compares a CLAIM against the TEXT of
# the source it cites, so a well-formed `[source: chunk 3]` attached to a wholly
# invented sentence passes every gate above.
#
# This section closes that. Two deterministic signals, both required, no LLM:
#
#   1. Span binding — find the window of the cited source that best matches the
#      claim, scored by token *F1*. F1 rather than recall: recall alone rewards
#      a claim that merely reuses the source's vocabulary while asserting more
#      than the source says. The winning window is also what a UI can show as
#      the supporting quote.
#   2. Anchor guard — every number, date, currency amount, percentage, acronym
#      and capitalised proper noun in the claim must appear in that bound span.
#      A fabricated claim carrying a real citation nearly always carries a
#      fabricated anchor, so this is the highest-signal check here and it costs
#      nothing.
#
# An optional `judge` callable adds entailment on top. It is INJECTED, never
# imported, so this module stays pure (no LLM, no DB, no Flask) and the
# deterministic tier remains the air-gap floor — per D310 the blocking path must
# not require a model.

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")
_WORD_SPAN_RE = re.compile(r"\b\w+\b")

# Spelled-out numbers. Without these the guard misses the single most likely
# fabrication in prose: "seven years" quietly becoming "forty-seven years".
# Digits-only anchors do not fire, and the surrounding sentence is otherwise
# near-identical, so lexical overlap alone scores it as well supported.
_NUM_ONES = ("zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
             "thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen")
_NUM_TENS = "twenty|thirty|forty|fourty|fifty|sixty|seventy|eighty|ninety"
_NUM_SCALE = "hundred|thousand|million|billion|trillion"
_NUMBER_WORD_RE = re.compile(
    rf"\b(?:(?:{_NUM_TENS})(?:[-\s](?:{_NUM_ONES}))?|(?:{_NUM_ONES}))"
    rf"(?:[-\s](?:{_NUM_SCALE}))?\b",
    re.IGNORECASE,
)

# Anchors: the concrete, checkable particles of a claim.
_ANCHOR_PATTERNS = (
    re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?"),                 # currency
    re.compile(r"\b\d+(?:\.\d+)?\s?%"),                     # percentage
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),                   # ISO date
    re.compile(r"\b\d[\d,]*(?:\.\d+)?\b"),                  # bare number
    _NUMBER_WORD_RE,                                        # spelled-out number
    re.compile(r"\b[A-Z]{2,}(?:-\d+)?\b"),                  # acronym / AC-3
    re.compile(r"\b(?:[A-Z][a-z]+\s){1,3}[A-Z][a-z]+\b"),   # proper-noun phrase
)

# Tokens that look like anchors but carry no factual weight on their own.
_ANCHOR_STOP = frozenset({"The", "This", "That", "These", "Those", "It", "A", "An", "I"})


def decompose_claims(text: str) -> list[tuple[str, int, int]]:
    """Split ``text`` into atomic claims as ``(claim, start, end)`` offsets.

    Offsets index the ORIGINAL text, so a caller can highlight, strip or
    annotate the exact span. That is what makes per-claim provenance
    renderable rather than merely computable.
    """
    if not text or not text.strip():
        return []
    out: list[tuple[str, int, int]] = []
    pos = 0
    for part in _SENTENCE_SPLIT_RE.split(text):
        if not part:
            continue
        start = text.find(part, pos)
        if start < 0:
            start = pos
        end = start + len(part)
        pos = end
        if part.strip():
            out.append((part.strip(), start, end))
    return out


def strip_citations(text: str) -> str:
    """Remove citation tags so they are not mistaken for claim content.

    Without this the tag itself becomes an anchor — ``[SOURCE-1]`` matches the
    acronym pattern, is obviously absent from the source prose, and marks every
    cited claim unsupported. It also pollutes the span-binding token set, since
    the source text never contains the marker the model wrote.

    A citation is a pointer, not an assertion; nothing inside one is a claim.
    """
    if not text:
        return ""
    out = _SOURCE_RE.sub(" ", text)
    out = _SOURCE_N_RE.sub(" ", out)
    out = re.sub(r"\s{2,}", " ", out)
    # Removing a trailing tag leaves "years ." — close the gap so the stripped
    # claim reads as prose if a caller ever shows it to a user.
    out = re.sub(r"\s+([.,;:!?])", r"\1", out)
    return out.strip()


def extract_anchors(claim: str) -> list[str]:
    """Concrete particles that must appear in the source: numbers, dates, names.

    Order-preserving and de-duplicated. Sub-matches are dropped, so the ``7``
    inside ``$7,500`` is not counted separately. Citation tags are stripped
    first — a marker is a pointer, not a factual particle.
    """
    claim = strip_citations(claim)
    if not claim:
        return []
    found: list[tuple[int, int, str]] = []
    for pat in _ANCHOR_PATTERNS:
        for m in pat.finditer(claim):
            tok = m.group(0).strip()
            if tok and tok not in _ANCHOR_STOP:
                found.append((m.start(), m.end(), tok))
    found.sort(key=lambda t: (t[0], -(t[1] - t[0])))
    out: list[str] = []
    covered: list[tuple[int, int]] = []
    for s, e, tok in found:
        if any(s >= cs and e <= ce for cs, ce in covered):
            continue  # contained within a longer anchor already taken
        covered.append((s, e))
        if tok not in out:
            out.append(tok)
    return out


def _norm_anchor(text: str) -> str:
    """Collapse separators so '$7,500' and '7500' compare equal."""
    return (text.lower().replace(",", "").replace("$", "")
            .replace(" ", "").replace("-", ""))


def _anchor_present(anchor: str, span_text: str) -> bool:
    """Is ``anchor`` genuinely present in ``span_text``?

    Word-boundary aware on purpose. A naive substring test reports "seven" as
    present in "seventeen", which would let a quantity swap through the guard —
    the exact defect the guard exists to catch. Numeric forms fall back to a
    separator-insensitive comparison so "$7,500" matches "7500", and hyphens
    are collapsed so "forty-seven" matches "forty seven".
    """
    a = (anchor or "").strip().strip(".,;:")
    if not a or not span_text:
        return False
    if re.fullmatch(r"[\w\-\s]+", a) and re.search(r"[A-Za-z]", a):
        # Word-ish anchor: require whole-word match, tolerating hyphen/space.
        pattern = r"[-\s]+".join(re.escape(p) for p in re.split(r"[-\s]+", a) if p)
        return bool(re.search(rf"\b{pattern}\b", span_text, re.IGNORECASE))
    na = _norm_anchor(a)
    return bool(na) and na in _norm_anchor(span_text)


def bind_claim_span(claim: str, source_text: str, source_id: str = "") -> dict | None:
    """Best-matching window of ``source_text`` for ``claim``, scored by token F1.

    Returns ``{source_id, start, end, quote, score}``, or ``None`` when either
    side is empty or nothing overlaps. The window is sized relative to the claim
    (0.75x-2.5x its token count) so a one-line claim cannot be "supported" by
    matching a whole page.
    """
    if not claim or not source_text:
        return None
    claim_tokens = _tokenize(claim)
    if not claim_tokens:
        return None

    spans = [(m.start(), m.end(), m.group(0).lower())
             for m in _WORD_SPAN_RE.finditer(source_text)]
    if not spans:
        return None

    n_claim = max(len(claim_tokens), 1)
    best_score = 0.0
    best_start = 0
    best_end = 0
    widths = {max(1, int(n_claim * 0.75)), n_claim,
              max(1, int(n_claim * 1.5)), max(1, int(n_claim * 2.5))}
    for width in widths:
        width = max(1, min(width, len(spans)))
        step = max(1, width // 4)
        for i in range(0, max(1, len(spans) - width + 1), step):
            window = spans[i:i + width]
            win_tokens = {w[2] for w in window}
            overlap = len(claim_tokens & win_tokens)
            if not overlap:
                continue
            precision = overlap / len(win_tokens)
            recall = overlap / len(claim_tokens)
            f1 = 2 * precision * recall / (precision + recall)
            if f1 > best_score:
                best_score = f1
                best_start = window[0][0]
                best_end = window[-1][1]

    if best_score <= 0.0:
        return None
    return {
        "source_id": source_id,
        "start": best_start,
        "end": best_end,
        "quote": source_text[best_start:best_end],
        "score": round(best_score, 4),
    }


def verify_claim(claim: str, sources: dict, *, cited_ids=None, judge=None) -> dict:
    """Verify one claim against the source(s) it cites.

    Args:
        claim: the claim text.
        sources: ``{source_id: source_text}``.
        cited_ids: ids this claim cites; parsed from the claim when omitted.
        judge: optional ``(claim, span_text) -> bool | None`` entailment check.
            Injected so this module stays LLM-free. ``None`` means "no opinion"
            and leaves the deterministic verdict standing.

    Returns ``{claim, verdict, score, method, cited_ids, bound_spans,
    missing_anchors}`` where ``verdict`` is ``supported`` | ``partial`` |
    ``unsupported`` | ``uncited``.
    """
    cited = list(cited_ids) if cited_ids is not None else parse_citations(claim)
    base = {
        "claim": claim,
        "cited_ids": cited,
        "bound_spans": [],
        "missing_anchors": [],
        "score": 0.0,
        "method": "span",
    }
    if not cited:
        # No attribution. Not a defect on its own — `citation_gate` decides
        # whether an uncited sentence is acceptable on a given surface.
        return {**base, "verdict": "uncited"}

    anchors = extract_anchors(claim)
    # Bind on the claim WITHOUT its citation tags: the source prose never
    # contains the marker, so leaving it in drags F1 down and can push a
    # correctly-cited claim below the support band.
    claim_text = strip_citations(claim) or claim
    best_span = None
    for sid in cited:
        text = sources.get(sid) or sources.get(str(sid)) or ""
        span = bind_claim_span(claim_text, text, str(sid))
        if span and (best_span is None or span["score"] > best_span["score"]):
            best_span = span

    if best_span is None:
        return {**base, "verdict": "unsupported", "missing_anchors": anchors}

    missing = [a for a in anchors if not _anchor_present(a, best_span["quote"])]
    out = {
        **base,
        "bound_spans": [best_span],
        "score": best_span["score"],
        "missing_anchors": missing,
    }

    # A missing anchor is decisive regardless of lexical overlap: the claim
    # asserts a specific that its own cited span does not contain.
    if missing:
        return {**out, "verdict": "unsupported", "method": "anchor"}

    mapped = {"include": "supported", "flag": "partial", "abstain": "unsupported"}[
        classify_confidence(best_span["score"])
    ]

    if judge is not None:
        try:
            opinion = judge(claim, best_span["quote"])
        except Exception:  # noqa: BLE001 - a judge failure must never block
            opinion = None
        if opinion is False:
            return {**out, "verdict": "unsupported", "method": "judge"}
        if opinion is True and mapped == "partial" and best_span["score"] > 0.0:
            # A judge may promote a borderline span, but never one with zero
            # lexical footprint: a model must not rescue a claim that is absent
            # from the text it cites. Ported from the DIC verifier cross-check.
            return {**out, "verdict": "supported", "method": "judge"}
    return {**out, "verdict": mapped}


def ground_claims(text: str, sources: dict, *, judge=None) -> dict:
    """Per-claim grounding report for a whole answer.

    Returns ``{claims, supported, partial, unsupported, uncited,
    supported_ratio}``. ``supported_ratio`` counts only CITED claims — an
    uncited sentence makes no attributed assertion, so scoring it either way
    would distort the result (the same reasoning as
    ``doc_generator._compute_section_confidence``).
    """
    verdicts = [verify_claim(c, sources, judge=judge)
                for c, _s, _e in decompose_claims(text)]
    counts = {"supported": 0, "partial": 0, "unsupported": 0, "uncited": 0}
    for v in verdicts:
        counts[v["verdict"]] = counts.get(v["verdict"], 0) + 1
    cited_total = len(verdicts) - counts["uncited"]
    ratio = (
        (counts["supported"] + 0.5 * counts["partial"]) / cited_total
    ) if cited_total else 1.0
    return {"claims": verdicts, **counts, "supported_ratio": round(ratio, 4)}


def claim_gate(report: dict, *, require_supported: bool = True) -> list[dict]:
    """Turn a :func:`ground_claims` report into blocking findings.

    Mirrors the shape of :func:`citation_gate` and
    ``content_grounding.placeholder_findings`` — ``{item_number, issue, detail}``
    — so a promote/export gate consumes all three symmetrically instead of
    special-casing each.
    """
    findings: list[dict] = []
    if not require_supported:
        return findings
    for i, v in enumerate(report.get("claims") or [], start=1):
        if v.get("verdict") == "unsupported":
            findings.append({
                "item_number": i,
                "issue": "unsupported_claim",
                "detail": v.get("missing_anchors") or [v.get("claim", "")[:120]],
            })
    return findings
