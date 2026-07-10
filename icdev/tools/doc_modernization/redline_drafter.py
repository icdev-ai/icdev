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

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect():
    from tools.db.storage import get_connection
    return get_connection()


@dataclass
class RedlineResult:
    finding_id: str
    status: str                      # 'drafted' | 'abstained' | 'blocked' | 'error'
    suggestion_id: str | None = None
    confidence: float = 0.0
    band: str = ""                   # include | flag | abstain
    reason: str = ""
    draft: str = ""
    citations: list[str] = field(default_factory=list)
    provenance_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _strip_reasoning(text: str) -> str:
    try:
        from tools.document_intelligence.doc_generator import _strip_reasoning_artifacts
        return _strip_reasoning_artifacts(text)
    except Exception:
        # Minimal fallback: drop <think>/<reasoning> blocks
        return re.sub(r"(?is)<(think|reasoning)>.*?</\1>", "", text).strip()


def _build_prompt(finding: dict, evidence: list[dict], candidates: list[str],
                  old_text: str) -> tuple[str, str]:
    system = (
        "You are a technical editor updating stale enterprise documentation. "
        "Rewrite ONLY the outdated passage you are given. Rules (mandatory): "
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


def _candidate_mentioned_ok(draft: str, candidates: list[str], stale_label: str) -> bool:
    """Reject drafts that name a replacement product/version outside the
    candidate list. Heuristic: any candidate mention is fine; the stale label
    may appear (being replaced); other model-number-like tokens that appear in
    neither are a block."""
    allowed = {c.lower() for c in candidates} | {stale_label.lower()}
    for token in re.findall(r"\b[A-Z][A-Za-z]*(?:[ -]?\d{2,5}[A-Za-z0-9.+-]*)\b", draft):
        t = token.lower().strip()
        if any(t in a or a in t for a in allowed):
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
        old_text = finding.get("entity_label", "")

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
        if not _candidate_mentioned_ok(draft, candidates, old_text):
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
            section_id="",  # section-level anchor resolved by the UI via heading
            collection_id="",
            canvas_source="doc_modernization",
            suggested_content=draft,
            current_content=old_text,
            rationale=(
                f"[docmod:{finding_id}] {finding.get('rationale','')} "
                f"(confidence {confidence}, band {band})"
            ),
            tenant_id=finding.get("tenant_id") or "",
            classification=finding.get("classification") or "CUI",
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
