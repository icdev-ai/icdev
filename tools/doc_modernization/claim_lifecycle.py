# CUI // SP-CTI
"""Document Modernization Engine — post-extraction claim lifecycle (dmx-claims-02, Phase C+D).

Phase B (``claim_extractor``) turns prose into ``pending_review`` claims anchored
to a VERBATIM char span. This module carries a claim through the rest of its life:

  * **Phase C — HITL promotion** (``promote_claim`` / ``reject_claim`` /
    ``list_claims``): a human promotes ``pending_review → active`` (or rejects to
    ``superseded``). Every transition is an APPEND-ONLY new row via the
    ``supersede_claim`` seam — the prior row is never mutated. This mirrors the
    DIC review/HITL pattern (``modernization_routes.api_modernization_resolve``
    appends a disposition row; ``blueprint.api_review_approve`` gates promote on a
    human).
  * **Phase D — finding→claim linkage** (``link_findings_to_claims``): the
    DETERMINISTIC core. After the scanner writes ``docmod_findings``, join each
    finding to any ``active`` claim in the same ``doc_id``/``version_id`` whose
    ``subject_label`` (or ``object_label``) equals the finding's ``entity_label``,
    and append an ``invalidated`` claim row carrying the finding_id(s) in
    ``linked_evidence_ids``. **The ONLY thing that flips a claim to
    ``invalidated`` is the existence of a deterministic finding row — no LLM sits
    on that edge (TRUST rule 1).**
  * **Phase D — anchor-drift auto-supersede** (``verify_claim_anchors``): a claim
    whose anchor can no longer be located verbatim in the current approved
    version is auto-transitioned to ``superseded`` (the sentence was edited out).

Deviation from spike §2.2/§3 (inherited from PR1, #651): anchor offsets are
CHUNK-LOCAL (indexed into the chunk identified by ``chunk_link_id``), NOT
version-global. So ``verify_claim_anchors`` takes a ``chunk_link_id -> text`` map
and checks ``chunk_text[start:end] == claim_text`` on the CORRECT chunk — the
offset-exact check the spike intends, applied to the chunk the anchor indexes.

All operations here are pure DB / deterministic — no LLM, air-gap safe.
"""
from __future__ import annotations

from typing import Optional

from tools.logging.icdev_logger import get_logger

from .claim_extractor import _latest_claims, supersede_claim

logger = get_logger(__name__)

ACTIVE = "active"
PENDING = "pending_review"
INVALIDATED = "invalidated"
SUPERSEDED = "superseded"


# ── Phase C — HITL promotion surface ────────────────────────────────────────────


def list_claims(conn, doc_id: str, status: Optional[str] = None) -> list[dict]:
    """Latest-state claims for a document (supersede chains resolved), newest first.

    Returns one dict per ``dedupe_key`` — the current state of each claim — with
    ``linked_evidence_ids`` parsed to a list. Optionally filter to a single
    ``status``. This is the read model behind the Phase-E claims panel.
    """
    import json as _json

    latest = _latest_claims(conn, doc_id)
    out: list[dict] = []
    for row in latest.values():
        if status and row.get("status") != status:
            continue
        r = dict(row)
        ev = r.get("linked_evidence_ids")
        if ev:
            try:
                r["linked_evidence_ids"] = _json.loads(ev)
            except (ValueError, TypeError):
                r["linked_evidence_ids"] = []
        else:
            r["linked_evidence_ids"] = []
        out.append(r)
    # Newest first by extraction time (stable secondary on claim_id).
    out.sort(key=lambda r: (r.get("extracted_at") or "", r.get("claim_id") or ""), reverse=True)
    return out


def _latest_for_key(conn, doc_id: str, dedupe_key: str) -> Optional[dict]:
    latest = _latest_claims(conn, doc_id)
    return latest.get(dedupe_key)


def promote_claim(conn, claim_id: str) -> dict:
    """HITL: promote a ``pending_review`` claim to ``active`` (append-only).

    Only the CURRENT (latest) pending_review row for a dedupe chain may be
    promoted — this is the human gate (spike invariant 3). Returns
    ``{claim_id, status, superseded}`` on success. Idempotent-safe: promoting a
    claim that is already active is a no-op that reports the current state.
    """
    return _transition(conn, claim_id, from_status=PENDING, to_status=ACTIVE)


def reject_claim(conn, claim_id: str) -> dict:
    """HITL: reject a ``pending_review`` claim (append-only ``superseded`` row).

    The status vocabulary has no dedicated 'rejected' state (CHECK constraint);
    a human dismissal is recorded as a ``superseded`` transition — the claim
    leaves the review queue and can never be ``invalidated`` (only ``active``
    claims can be). Append-only: a new row, prior untouched.
    """
    return _transition(conn, claim_id, from_status=PENDING, to_status=SUPERSEDED)


def _transition(conn, claim_id: str, *, from_status: str, to_status: str) -> dict:
    """Shared append-only HITL transition guarded on the current chain state."""
    row = conn.execute(
        "SELECT * FROM dic_claims WHERE claim_id = %s", (claim_id,)
    ).fetchone()
    if not row:
        return {"error": "claim not found", "claim_id": claim_id}
    r = dict(row)
    key = r.get("dedupe_key")
    current = _latest_for_key(conn, r["doc_id"], key) if key else r
    if not current or current.get("claim_id") != claim_id:
        # The referenced row is not the head of its chain — refuse to fork history.
        return {"error": "claim is not the current state", "claim_id": claim_id,
                "current_status": (current or {}).get("status")}
    if current.get("status") == to_status:
        return {"claim_id": claim_id, "status": to_status, "superseded": None,
                "reason": "already_in_state"}
    if current.get("status") != from_status:
        return {"error": f"claim status is {current.get('status')!r}, expected {from_status!r}",
                "claim_id": claim_id, "current_status": current.get("status")}
    new_id = supersede_claim(conn, current, to_status)
    return {"claim_id": new_id, "status": to_status, "superseded": claim_id}


# ── Phase D — finding→claim linkage (deterministic core) ─────────────────────────


def _open_findings(conn, doc_id: str, version_id: Optional[str] = None) -> list[dict]:
    """Latest-state 'open' docmod findings for a doc (mirrors scanner._open_findings).

    A finding is 'live' when its latest chain row is in an awaiting state. Only
    live findings drive claim invalidation — a resolved/rejected finding must not
    flip a claim.
    """
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM docmod_findings WHERE doc_id = %s ORDER BY created_at, finding_id",
        (doc_id,),
    ).fetchall()]
    latest: dict[str, dict] = {}
    for r in rows:  # ascending — later rows supersede earlier per dedupe_key
        key = r.get("dedupe_key") or r.get("finding_id")
        latest[key] = r
    live_states = ("open", "redline_drafted")
    out = [r for r in latest.values() if r.get("state") in live_states]
    if version_id:
        out = [r for r in out if r.get("version_id") == version_id]
    return out


def link_findings_to_claims(conn, doc_id: str, version_id: Optional[str] = None) -> dict:
    """Flag active claims invalidated by a deterministic finding (spike §4).

    For each live ``docmod_findings`` row, find every ``active`` claim in the same
    ``doc_id`` (and ``version_id`` when given) whose ``subject_label`` OR
    ``object_label`` equals the finding's ``entity_label`` (case-insensitive), and
    append an ``invalidated`` claim row carrying the finding_id(s) in
    ``linked_evidence_ids``. The append preserves the verbatim anchor / span so
    the redline surface can flag the exact SENTENCE.

    DETERMINISTIC-FIRST: the sole trigger is the existence of a finding row. No
    LLM. Returns ``{invalidated: [claim_id...], links: [{claim_id, finding_ids}]}``.
    """
    findings = _open_findings(conn, doc_id, version_id)
    if not findings:
        return {"invalidated": [], "links": []}

    # entity_label (lower) -> [finding rows]. A subject may be hit by >1 finding.
    by_entity: dict[str, list[dict]] = {}
    for f in findings:
        label = (f.get("entity_label") or "").strip().lower()
        if label:
            by_entity.setdefault(label, []).append(f)

    invalidated: list[str] = []
    links: list[dict] = []
    for claim in _latest_claims(conn, doc_id).values():
        if claim.get("status") != ACTIVE:
            continue
        if version_id and claim.get("version_id") != version_id:
            continue
        subj = (claim.get("subject_label") or "").strip().lower()
        obj = (claim.get("object_label") or "").strip().lower()
        matched: list[dict] = []
        for token in (subj, obj):
            if token and token in by_entity:
                matched.extend(by_entity[token])
        if not matched:
            continue
        finding_ids = sorted({f["finding_id"] for f in matched})
        new_id = supersede_claim(conn, claim, INVALIDATED, linked_evidence_ids=finding_ids)
        invalidated.append(new_id)
        links.append({"claim_id": new_id, "supersedes": claim["claim_id"],
                      "finding_ids": finding_ids})
    if invalidated:
        logger.info("claim linkage: doc=%s invalidated=%d", doc_id, len(invalidated))
    return {"invalidated": invalidated, "links": links}


def verify_claim_anchors(conn, doc_id: str, chunk_texts: dict[str, str]) -> dict:
    """Auto-``superseded`` claims whose anchor no longer resolves verbatim (spike §2.2).

    ``chunk_texts`` maps ``chunk_link_id -> current chunk source text``. For each
    latest-state claim that is ``active`` or ``pending_review``, the anchor is
    valid iff ``chunk_texts[claim.chunk_link_id][start:end] == claim_text``. A
    claim whose chunk is gone, or whose slice no longer equals ``claim_text`` (the
    prose was edited out), is transitioned append-only to ``superseded``.

    Deviation note: offsets are chunk-local (PR1 #651), so the check is applied to
    the specific chunk the anchor indexes, not a version-global string.
    """
    superseded: list[str] = []
    for claim in _latest_claims(conn, doc_id).values():
        if claim.get("status") not in (ACTIVE, PENDING):
            continue
        link_id = claim.get("chunk_link_id")
        text = chunk_texts.get(link_id) if link_id else None
        start = claim.get("anchor_start")
        end = claim.get("anchor_end")
        ok = (
            text is not None
            and isinstance(start, int) and isinstance(end, int)
            and 0 <= start <= end <= len(text)
            and text[start:end] == claim.get("claim_text")
        )
        if ok:
            continue
        new_id = supersede_claim(conn, claim, SUPERSEDED)
        superseded.append(new_id)
    if superseded:
        logger.info("claim anchor-drift: doc=%s superseded=%d", doc_id, len(superseded))
    return {"superseded": superseded}


def claim_for_finding(conn, finding: dict) -> Optional[dict]:
    """The invalidated claim (if any) a finding flagged — for the drift payload.

    Returns the latest ``invalidated`` claim row whose ``linked_evidence_ids``
    contains ``finding['finding_id']`` (Python-side JSON membership per the repo's
    'compute JSON filters in Python, not SQL' rule). ``None`` when the finding
    touched no claim. Used by ``drift_bridge`` to add ``claim_id`` +
    ``anchor_start``/``anchor_end`` so ACOIC/redline surfaces the sentence.
    """
    import json as _json

    finding_id = finding.get("finding_id")
    doc_id = finding.get("doc_id")
    if not finding_id or not doc_id:
        return None
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM dic_claims WHERE doc_id = %s AND status = %s "
        "ORDER BY extracted_at DESC, claim_id DESC",
        (doc_id, INVALIDATED),
    ).fetchall()]
    for r in rows:
        raw = r.get("linked_evidence_ids")
        if not raw:
            continue
        try:
            ids = _json.loads(raw)
        except (ValueError, TypeError):
            continue
        if finding_id in (ids or []):
            return r
    return None
