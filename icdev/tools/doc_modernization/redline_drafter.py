# CUI // SP-CTI
"""TRUST-gated redline drafting — cited suggested edits for docmod findings.

The LLM's ONLY job is wording prose around facts it is handed:
- the finding's deterministic evidence (rule ids, catalog entries, EOL rows)
- the deterministic recommended replacement (candidates passed IN; any output
  naming a replacement outside the candidate list is REJECTED)

Gating chain (approved plan, TRUST section — every step mandatory):
1. inline [source: <id>] citations resolving to the finding's evidence ids,
   validated via tools/quality/citation_grounding (never re-implemented);
   hallucinated citation => hard block
2. out-of-candidate replacement => hard block
3. reasoning-residue scrub (same helper as the TRUST-compliant DIC path);
   residue that survives => confidence forced to the HITL-flag band
4. confidence bands via classify_confidence: >=0.7 include, 0.4-0.69
   include+flag, <0.4 abstain => draft discarded, finding stays open
5. provenance persisted via build_artifact_provenance
6. accepted drafts land as dic_suggestions rows (existing accept/edit/reject
   UI) + an append-only docmod_findings state row 'redline_drafted'

dwr-anchor-04 — the drafter sees the SENTENCE, and refuses to write a proposal
that could never be applied. The finding carries a chunk-local span
(dwr-anchor-02); ``resolve_passage`` widens it to the sentence around it and
locates that passage in the section's content of record. What the model is
shown, what ``current_content`` records, and what ``anchor_text`` pins are then
one and the same string. An entity that resolves to no single section, or that
occurs twice in one, stays ``unanchored`` and is REPORTED on the finding rather
than persisted: a pending suggestion whose ``section_id`` is empty updates zero
rows on accept and reports success, which is the defect dwr-anchor-03 measured
on 58 of 58 live rows.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger
from tools.quality.citation_grounding import (
    build_artifact_provenance,
    classify_confidence,
    compute_attribution_score,
    parse_citations,
    validate_citations,
)
from tools.document_intelligence.suggestion_store import resolve_anchor

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect():
    from tools.db.storage import get_connection
    return get_connection()


@dataclass
class RedlineResult:
    finding_id: str
    status: str   # 'drafted' | 'unanchored' | 'abstained' | 'blocked' | 'error'
    suggestion_id: str | None = None
    confidence: float = 0.0
    band: str = ""                   # include | flag | abstain
    reason: str = ""
    draft: str = ""
    citations: list[str] = field(default_factory=list)
    provenance_id: str = ""
    # dwr-anchor-04 — reported whether or not anything was written.
    anchor_basis: str = "unanchored"
    section_id: str | None = None
    passage: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _strip_reasoning(text: str) -> str:
    try:
        from tools.document_intelligence.doc_generator import _strip_reasoning_artifacts
        return _strip_reasoning_artifacts(text)
    except Exception:
        # Minimal fallback: drop <think>/<reasoning> blocks
        return re.sub(r"(?is)<(think|reasoning)>.*?</\1>", "", text).strip()


# ── dwr-anchor-04: the passage, and where it goes ─────────────────────────────

_SENTENCE_END = re.compile(r"""[.!?]["'\u2019\u201d)\]]*(?=\s|$)""")


@dataclass
class PassageAnchor:
    """The real text a redline replaces, and the basis it may honestly claim.

    ``passage`` is a slice of the document, never the entity label. The offsets
    index into the SECTION's content (``section_content[anchor_start:anchor_end]
    == passage``), which is the convention ``dic_suggestions.anchor_start``
    records and the accept path re-derives.
    """

    passage: str = ""
    section_id: str | None = None
    section_content: str | None = None
    anchor_basis: str = "unanchored"
    anchor_start: int | None = None
    anchor_end: int | None = None
    reason: str = ""

    @property
    def anchored(self) -> bool:
        return self.anchor_basis in ("exact", "relocated")


def widen_to_passage(text: str, start: int, end: int) -> tuple[int, int]:
    """Widen a match span to the sentence around it, or the paragraph when the
    paragraph carries no sentence boundary (a heading, a table cell, a bullet).

    The result ALWAYS contains ``[start, end)`` — the boundaries are taken from
    before ``start`` and after ``end``, so the entity the finding is about can
    never be sliced in half. A sentence terminator must be followed by
    whitespace or the end of the text, so ``TLS 1.1`` and ``800-53 Rev 4`` are
    not boundaries; ``Rev 4. The`` is, and correctly.

    Pure: reads nothing, and returns offsets into the SAME string it was given.
    """
    if not text:
        return 0, 0
    start = max(0, min(int(start), len(text)))
    end = max(start, min(int(end), len(text)))

    para_start = text.rfind("\n\n", 0, start)
    para_start = 0 if para_start < 0 else para_start + 2
    para_end = text.find("\n\n", end)
    para_end = len(text) if para_end < 0 else para_end

    para = text[para_start:para_end]
    local_start, local_end = start - para_start, end - para_start

    lo, hi = 0, len(para)
    for m in _SENTENCE_END.finditer(para):
        boundary = m.end()
        if boundary <= local_start:
            lo = boundary            # the last sentence to END before the match
        elif boundary >= local_end:
            hi = boundary            # the first to end AT or after it
            break

    a_start, a_end = para_start + lo, para_start + hi
    # Trim surrounding whitespace WITHOUT ever crossing the match itself.
    while a_start < start and text[a_start].isspace():
        a_start += 1
    while a_end > end and text[a_end - 1].isspace():
        a_end -= 1
    return a_start, a_end


def _chunk_text(conn, finding: dict) -> str | None:
    """The text the finding's chunk-local offsets index into.

    Mirrors ``scanner._doc_chunks``: the rag chunk behind ``chunk_link_id``.
    A finding with NO chunk link came from the section-fallback scan, so its
    offsets index into the section content and ``resolve_passage`` supplies
    that; this reads only the chunk-link half. None is UNREADABLE, never empty.
    """
    link_id = finding.get("chunk_link_id")
    if link_id:
        try:
            row = conn.execute(
                "SELECT rc.content FROM dic_chunk_links dcl "
                "JOIN rag_chunks rc ON dcl.rag_chunk_id = rc.id "
                "WHERE dcl.link_id = %s",
                (link_id,),
            ).fetchone()
        except Exception as exc:
            logger.warning("docmod redline: chunk-link read failed for %s: %s", link_id, exc)
            try:
                conn.rollback()   # PG: a failed statement poisons the transaction
            except Exception:
                pass
            return None
        return (dict(row).get("content") or None) if row else None
    return None


def _resolve_section(conn, finding: dict) -> tuple[str | None, str | None]:
    """``(section_id, content)`` for the finding's section, or ``(None, None)``.

    Resolved by HEADING within the finding's version, and only when the heading
    names exactly ONE section: two sections sharing a heading is the same
    ambiguity ``resolve_anchor`` refuses to guess at, one level up. A heading
    the scan carried from a rag chunk (``dic_chunk_links.section``) need not be
    a ``dic_sections`` heading at all, and then nothing resolves.
    """
    heading = finding.get("section_heading")
    version_id = finding.get("version_id")
    if not heading or not version_id:
        return None, None
    try:
        rows = conn.execute(
            "SELECT section_id, content FROM dic_sections "
            "WHERE version_id = %s AND heading = %s",
            (version_id, heading),
        ).fetchall()
    except Exception as exc:
        logger.warning("docmod redline: section read failed for %s/%s: %s",
                       version_id, heading, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return None, None
    if len(rows) != 1:
        return None, None
    d = dict(rows[0])
    return d.get("section_id") or None, d.get("content")


def resolve_passage(conn, finding: dict) -> PassageAnchor:
    """The sentence a redline rewrites, and where it sits in its section.

    dwr-anchor-02 persists a CHUNK-LOCAL span on the finding; a suggestion is
    applied against a SECTION. So the span is widened to the sentence around it
    inside the chunk, and that passage is then located in the section content:
    when the chunk IS the section the offsets verify and the basis is ``exact``;
    otherwise the passage found ONCE is ``relocated``. Found nowhere, or found
    twice, is ``unanchored`` — never a guess between two places.
    """
    start, end = finding.get("anchor_start"), finding.get("anchor_end")
    if start is None or end is None:
        return PassageAnchor(reason="finding carries no span (pre-anchor scan, "
                                    "or a pack that anchors nothing)")

    section_id, section_content = _resolve_section(conn, finding)
    # A finding with no chunk link came from the section-fallback scan: its
    # offsets index into the section content itself.
    text = _chunk_text(conn, finding) if finding.get("chunk_link_id") else section_content
    if not text:
        return PassageAnchor(
            section_id=section_id, section_content=section_content,
            reason=("the chunk the span indexes into could not be read"
                    if finding.get("chunk_link_id")
                    else "no single dic_sections row carries this heading"))
    try:
        start, end = int(start), int(end)
    except (TypeError, ValueError):
        return PassageAnchor(reason="finding span is not a pair of integers")
    if start < 0 or end <= start or end > len(text):
        return PassageAnchor(reason=f"span {start}:{end} is outside a chunk of {len(text)} chars")

    recorded = finding.get("anchor_text")
    if recorded is not None and text[start:end] != recorded:
        return PassageAnchor(
            reason="the chunk moved under the span: "
                   f"{text[start:end]!r} is no longer {recorded!r}")

    p_start, p_end = widen_to_passage(text, start, end)
    passage = text[p_start:p_end]

    if not section_id or section_content is None:
        return PassageAnchor(passage=passage,
                             reason="no single dic_sections row carries this heading")

    resolved = resolve_anchor(section_content, passage,
                              anchor_start=p_start, anchor_end=p_end)
    return PassageAnchor(
        passage=passage, section_id=section_id, section_content=section_content,
        anchor_basis=resolved["anchor_basis"], anchor_start=resolved["anchor_start"],
        anchor_end=resolved["anchor_end"], reason=resolved["reason"],
    )


def _build_prompt(finding: dict, evidence: list[dict], candidates: list[str],
                  old_text: str) -> tuple[str, str]:
    """``old_text`` is the PASSAGE being rewritten (dwr-anchor-04). The stale
    entity is still named separately as the item to replace — the model has to
    rewrite the sentence it is shown, not echo a token back at us."""
    system = (
        "You are a technical editor updating stale enterprise documentation. "
        "Rewrite ONLY the outdated passage you are given, preserving every "
        "statement in it that is still correct. Rules (mandatory): "
        "(1) State facts ONLY from the EVIDENCE list; cite each fact inline as "
        "[source: <id>] using the exact ids given. "
        "(2) If a replacement technology is needed, use ONLY an item from "
        "CANDIDATE REPLACEMENTS verbatim — never invent products, models, or versions. "
        "(3) Output the replacement passage only — no preamble, no explanation, "
        "no reasoning."
    )
    ev_lines = "\n".join(
        f"- id: {e.get('source')} — {e.get('detail', '')} {e.get('date', '')}".strip()
        for e in evidence
    )
    user = (
        f"OUTDATED PASSAGE (from section '{finding.get('section_heading') or ''}'):\n"
        f"{old_text or finding.get('entity_label', '')}\n\n"
        f"OUT-OF-DATE ITEM IN THAT PASSAGE:\n{finding.get('entity_label', '')}\n\n"
        f"WHY IT IS OUTDATED:\n{finding.get('rationale', '')}\n\n"
        f"EVIDENCE:\n{ev_lines}\n\n"
        f"CANDIDATE REPLACEMENTS:\n"
        + ("\n".join(f"- {c}" for c in candidates) if candidates else "- (none — flag for removal)")
        + "\n\nWrite the corrected passage."
    )
    return system, user


def _invoke_llm(system: str, user: str) -> str | None:
    """Central router only (config-driven; redaction egress toggles apply)."""
    try:
        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter
    except ImportError:
        return None
    try:
        req = LLMRequest(
            messages=[{"role": "user", "content": user}],
            system_prompt=system,
            max_tokens=1024,
            temperature=0.2,
        )
        response = LLMRouter().invoke("docmod_redline", req)
        return (response.content or "").strip() or None
    except Exception as exc:
        logger.warning("docmod redline: LLM invoke failed: %s", exc)
        return None


def _candidate_mentioned_ok(draft: str, candidates: list[str], stale_label: str,
                            source_text: str = "") -> bool:
    """Reject drafts that INVENT a replacement product/version.

    The rule has always been "nothing in the output that was not in the input
    or the candidate list": any candidate mention is fine, the stale label may
    appear (it is the thing being replaced), and other model-number-like tokens
    are a block.

    dwr-anchor-04 widened the INPUT from a bare entity label to the passage the
    model is actually shown, so ``source_text`` widens the allowed set by
    exactly the same step and the rule itself does not move. A token verbatim
    in the passage was handed to the model BY US: a faithful rewrite of
    "Catalyst 6500 switches terminate TLS 1.1 tunnels" has to keep the switch.
    Nothing the model could invent is admitted — a product absent from the
    passage, the candidates and the label is still a hard block, and swapping a
    product that WAS in the passage for a different one is still a hard block,
    because the substitute is in none of the three.
    """
    allowed = {c.lower() for c in candidates} | {stale_label.lower()}
    source_lower = (source_text or "").lower()
    for token in re.findall(r"\b[A-Z][A-Za-z]*(?:[ -]?\d{2,5}[A-Za-z0-9.+-]*)\b", draft):
        t = token.lower().strip()
        if any(t in a or a in t for a in allowed):
            continue
        if t and t in source_lower:      # verbatim in the passage we handed it
            continue
        # citations / dates / rule ids are not product tokens
        if re.fullmatch(r"(19|20)\d{2}", token.split()[-1] if " " in token else token):
            continue
        if f"[source: {t}" in draft.lower():
            continue
        return False
    return True


def draft_redline(finding_id: str, conn=None) -> RedlineResult:
    """Draft one TRUST-gated redline for an open finding."""
    own = conn is None
    if own:
        conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM docmod_findings WHERE finding_id = %s", (finding_id,)
        ).fetchone()
        if not row:
            return RedlineResult(finding_id, "error", reason="finding not found")
        finding = dict(row)
        if finding.get("state") != "open":
            return RedlineResult(finding_id, "error",
                                 reason=f"finding state is '{finding.get('state')}', not open")

        try:
            evidence = json.loads(finding.get("evidence_json") or "[]")
        except Exception:
            evidence = []
        try:
            rep_evidence = json.loads(finding.get("replacement_evidence_json") or "[]")
        except Exception:
            rep_evidence = []
        evidence = evidence + [e for e in rep_evidence if e not in evidence]
        if not evidence:
            return RedlineResult(finding_id, "abstained",
                                 reason="no deterministic evidence — flag-only finding")

        candidates = [finding["recommended_replacement"]] if finding.get("recommended_replacement") else []
        entity_label = finding.get("entity_label", "")

        # ── dwr-anchor-04: WHERE does this change go? ───────────────────────
        # Asked BEFORE the LLM call, on purpose. A draft that cannot be
        # anchored is refused below, so drafting one first would spend a token
        # per sweep, forever, on prose no surface stores or renders. The
        # finding stays `open` and nothing here has to be undone: re-ingesting
        # the document with chunk links, or a heading that resolves to one
        # section, makes the same finding anchorable on the next sweep.
        anchor = resolve_passage(conn, finding)
        if not anchor.anchored:
            return RedlineResult(
                finding_id, "unanchored", passage=anchor.passage,
                anchor_basis=anchor.anchor_basis, section_id=anchor.section_id,
                reason=f"cannot anchor the redline: {anchor.reason}",
            )
        old_text = anchor.passage

        system, user = _build_prompt(finding, evidence, candidates, old_text)
        raw = _invoke_llm(system, user)
        if raw is None:
            return RedlineResult(finding_id, "abstained", reason="LLM unavailable")

        draft, residue_flag = _strip_reasoning(raw), False
        if draft != raw.strip():
            residue_flag = True  # reasoning residue found — force HITL flag band

        # ── TRUST gate 1: citations resolve to evidence ids ────────────────
        allowed_ids = [e.get("source") for e in evidence if e.get("source")]
        cited = parse_citations(draft)
        validation = validate_citations(draft, allowed_ids)
        if validation.get("hallucinated_citations"):
            return RedlineResult(
                finding_id, "blocked", draft=draft, citations=cited,
                reason=f"hallucinated citations: {validation['hallucinated_citations']}",
            )
        if not cited:
            return RedlineResult(finding_id, "blocked", draft=draft,
                                 reason="draft carries no [source: ...] citations")

        # ── TRUST gate 2: replacements only from the candidate list ────────
        # The stale label and the passage go in SEPARATELY, never merged:
        # `stale_label` is matched with a substring rule (`a in t`), so folding
        # the passage into it would admit near-misses of every product the
        # passage mentions. `source_text` is a verbatim membership test, which
        # admits only what is actually in the document.
        if not _candidate_mentioned_ok(draft, candidates, entity_label,
                                       source_text=old_text):
            return RedlineResult(
                finding_id, "blocked", draft=draft, citations=cited,
                reason="draft names a replacement outside the candidate list",
            )

        # ── TRUST gate 3/4: confidence band (deterministic proxy, no LLM self-
        # grading): evidence-coverage rate and token-attribution recall, floored
        # at the flag band; surviving reasoning residue forces the flag band.
        evidence_text = " ".join(str(e.get("detail", "")) for e in evidence)
        attribution = compute_attribution_score(evidence_text, draft) if evidence_text else 0.0
        citation_rate = float(validation.get("citation_rate") or 0.0)
        confidence = round(0.4 + 0.6 * max(citation_rate, attribution), 3)
        if residue_flag:
            confidence = min(confidence, 0.69)
        band = classify_confidence(confidence)
        if band == "abstain":
            return RedlineResult(finding_id, "abstained", draft=draft, citations=cited,
                                 confidence=confidence, band=band,
                                 reason="confidence below abstain threshold")

        # ── TRUST gate 5: provenance ────────────────────────────────────────
        provenance = build_artifact_provenance(
            artifact_id=f"redline-{finding_id}",
            sources=allowed_ids,
            generation_model="llm_router:docmod_redline",
            method="docmod_redline_v1",
        )

        # ── store as dic_suggestion (existing HITL accept/edit/reject UI) ───
        from tools.document_intelligence.suggestion_store import create_suggestion
        suggestion_id = create_suggestion(
            doc_id=finding["doc_id"],
            section_id=anchor.section_id,
            collection_id="",
            canvas_source="doc_modernization",
            suggested_content=draft,
            # dwr-anchor-04: the PASSAGE, not the entity label. This is the
            # before-text of THIS change, so a reviewer finally has an honest
            # before/after; the anchor below says where in the section it sits.
            current_content=old_text,
            rationale=(
                f"[docmod:{finding_id}] {finding.get('rationale','')} "
                f"(confidence {confidence}, band {band})"
            ),
            tenant_id=finding.get("tenant_id") or "",
            classification=finding.get("classification") or "CUI",
            # dwr-anchor-03/04: the basis is RECORDED, never inferred — it is
            # whatever `resolve_anchor` could prove against the live section,
            # and this call is only ever reached when that is `exact` or
            # `relocated`. `anchor_content` is the SECTION, the string the
            # offsets index into; `current_content` above is the passage, and
            # verifying the span against that would prove only that a string
            # contains itself.
            origin_kind="docmod_redline",
            anchor_section_id=anchor.section_id,
            anchor_start=anchor.anchor_start,
            anchor_end=anchor.anchor_end,
            anchor_text=anchor.passage,
            anchor_basis=anchor.anchor_basis,
            anchor_content=anchor.section_content,
        )

        # ── append-only state row: open -> redline_drafted ──────────────────
        conn.execute(
            """INSERT INTO docmod_findings
               (finding_id, run_id, doc_id, version_id, chunk_link_id, section_heading,
                page, pack_id, entity_label, entity_type, finding_type, currency_verdict,
                severity, rationale, evidence_json, recommended_replacement,
                replacement_evidence_json, confidence, state, supersedes_id,
                redline_suggestion_id, dedupe_key, created_at, tenant_id, classification)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                       'redline_drafted',%s,%s,%s,%s,%s,%s)""",
            (
                f"fnd-{uuid.uuid4().hex[:12]}", finding["run_id"], finding["doc_id"],
                finding["version_id"], finding.get("chunk_link_id"),
                finding.get("section_heading"), finding.get("page"), finding["pack_id"],
                finding["entity_label"], finding.get("entity_type"),
                finding["finding_type"], finding["currency_verdict"],
                finding.get("severity") or "medium", finding.get("rationale"),
                finding.get("evidence_json"), finding.get("recommended_replacement"),
                finding.get("replacement_evidence_json"), confidence,
                finding["finding_id"], suggestion_id, finding.get("dedupe_key"),
                _now(), finding.get("tenant_id"), finding.get("classification"),
            ),
        )
        conn.commit()
        return RedlineResult(
            finding_id, "drafted", suggestion_id=suggestion_id, confidence=confidence,
            band=band, draft=draft, citations=cited,
            provenance_id=getattr(provenance, "artifact_id", f"redline-{finding_id}"),
            anchor_basis=anchor.anchor_basis, section_id=anchor.section_id,
            passage=anchor.passage,
        )
    finally:
        if own:
            conn.close()


def draft_open_redlines(doc_id: str | None = None, limit: int | None = None) -> dict:
    """Draft redlines for open findings, capped by max_redlines_per_sweep."""
    from tools.doc_modernization import get_findings
    from tools.doc_modernization.pack_loader import load_config

    cap = limit if limit is not None else int(load_config().get("max_redlines_per_sweep", 10) or 10)
    open_findings = [f for f in get_findings(doc_id=doc_id, state="open")
                     if not f.get("redline_suggestion_id")]
    results = []
    for f in open_findings[:cap]:
        results.append(draft_redline(f["finding_id"]).to_dict())
    by_status: dict[str, int] = {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    return {"attempted": len(results), "by_status": by_status, "results": results}
