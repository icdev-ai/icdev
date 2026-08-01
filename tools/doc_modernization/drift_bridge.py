# CUI // SP-CTI
"""DocDrift bridge — docmod findings become compliance drift.

docmod answers "what in this document is no longer true?" per domain pack.
ACOIC/DocDrift answers "so what?" — impact scoring, a HITL regeneration queue,
NIST 800-53 re-mapping and cited SSP fragments. Until now nothing connected
them: ACOIC's only producer was network topology drift, which guesses the
affected document from a *collection tag*. A docmod finding already knows the
real ``doc_id``, the section and the chunk, so this bridge is strictly better
evidence than the tag-based path.

This is what makes DocDrift domain-agnostic: every pack (crypto, software,
policy, network, and any pack a domain owner scaffolds later) reaches the same
compliance response through one contract, keyed by document.

Distinct from :mod:`tools.doc_modernization.card_bridge`, which emits
per-document rollups to oracle_predictions -> kanban suggested cards. That is a
*work-tracking* sink; this is the *compliance* sink. Both consume the same
findings; neither replaces the other.

Idempotency: the drift dedup key is the finding_id, which is stable for the life
of a finding. docmod_findings is append-only — a superseded finding gets a NEW
finding_id — so a genuinely new finding produces a new drift event, while
re-running this bridge over unchanged findings collapses onto the same rows
instead of appending a duplicate every sweep.
"""
from __future__ import annotations

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# Mirrors card_bridge: a finding with a pending redline still needs a human, so
# it still represents live drift. Resolved/superseded findings must not re-emit.
_AWAITING_STATES = ("open", "redline_drafted")

# docmod severity vocabulary already matches ACOIC's (_score_impact), so no
# translation table is needed — a mapping here would be a place for the two to
# silently drift apart.
_SOURCE_PREFIX = "docmod"


def _connect():
    from tools.db.storage import get_connection
    return get_connection()


def _claim_for_finding(conn, finding: dict) -> dict | None:
    """The invalidated semantic claim this finding flagged, or None (Phase D).

    Thin, defensive wrapper over ``claim_lifecycle.claim_for_finding`` — a corpus
    with claims disabled (no ``dic_claims`` rows) or an older schema returns None
    and the drift payload is unchanged. Never fatal: a claim-lookup error must not
    stop a finding from becoming compliance drift.
    """
    try:
        from tools.doc_modernization.claim_lifecycle import claim_for_finding
        return claim_for_finding(conn, finding)
    except Exception as exc:  # noqa: BLE001 — additive precision, never load-bearing
        logger.debug("drift_bridge: claim lookup skipped for %s: %s",
                     finding.get("finding_id"), exc)
        return None


def _pack_controls(pack_id: str) -> list[str]:
    """NIST controls a pack's findings implicate, declared in its YAML.

    Optional and declarative: `nist_controls: [SC-8, SC-13]` in
    args/docmod/packs/<pack>.yaml. Absent => no control re-map for that pack.
    Controls are NEVER inferred from the finding text — an invented control
    mapping in an SSP is worse than none (TRUST rule 1).
    """
    try:
        from tools.doc_modernization.pack_loader import load_packs

        pack = load_packs().get(pack_id)
        if pack is None:
            return []
        raw = (pack.config or {}).get("nist_controls") or []
        return [str(c).strip() for c in raw if str(c).strip()]
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("drift_bridge: pack controls unavailable for %s: %s", pack_id, exc)
        return []


def emit_drift(conn=None) -> dict:
    """Feed awaiting, above-threshold docmod findings into DocDrift.

    Returns {emitted, skipped_subthreshold, skipped_state, errors, events}.
    """
    from tools.doc_modernization import get_findings
    from tools.doc_modernization.pack_loader import load_config
    from tools.document_intelligence import acoic

    own = conn is None
    if own:
        conn = _connect()
    result: dict = {
        "emitted": 0,
        "enqueued": 0,
        "skipped_subthreshold": 0,
        "skipped_state": 0,
        "errors": [],
        "events": [],
    }
    try:
        threshold = float(load_config().get("confidence_threshold", 0.7) or 0.7)
        findings = get_findings(conn=conn)
    except Exception as exc:
        result["errors"].append(f"load: {exc}")
        if own:
            conn.close()
        return result

    try:
        for f in findings:
            if f.get("state") not in _AWAITING_STATES:
                result["skipped_state"] += 1
                continue
            # Sub-threshold findings never reach a human surface (same rule as
            # card_bridge) — an SSP must not be driven by low-confidence guesses.
            try:
                confidence = float(f.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            if confidence < threshold:
                result["skipped_subthreshold"] += 1
                continue

            finding_id = f.get("finding_id")
            doc_id = f.get("doc_id")
            pack_id = f.get("pack_id") or "unknown"
            # dmx-claims-02 Phase D: if this finding invalidated a semantic claim,
            # carry the claim_id + verbatim anchor span so ACOIC/redline surfaces
            # the exact SENTENCE, not just the section. Best-effort and additive:
            # a doc with no claims (toggle off) contributes nothing here.
            claim = _claim_for_finding(conn, f)
            try:
                out = acoic.handle_drift({
                    "source": f"{_SOURCE_PREFIX}.{pack_id}",
                    "entity": f.get("entity_label"),
                    "severity": f.get("severity") or "medium",
                    # The real document — not a tag guess. This is the whole point.
                    "document_id": doc_id,
                    "control_ids": _pack_controls(pack_id),
                    "dedup_key": f"{_SOURCE_PREFIX}:{finding_id}",
                    "classification": f.get("classification"),
                    "tenant_id": f.get("tenant_id"),
                    # Carried into the drift payload so a reviewer can see WHICH
                    # paragraph is wrong without re-running the scan.
                    "finding_id": finding_id,
                    "finding_type": f.get("finding_type"),
                    "currency_verdict": f.get("currency_verdict"),
                    "section_heading": f.get("section_heading"),
                    "page": f.get("page"),
                    "chunk_link_id": f.get("chunk_link_id"),
                    "rationale": f.get("rationale"),
                    # Sentence-level precision (dmx-claims-02): None when no claim.
                    "claim_id": claim.get("claim_id") if claim else None,
                    "anchor_start": claim.get("anchor_start") if claim else None,
                    "anchor_end": claim.get("anchor_end") if claim else None,
                    "claim_text": claim.get("claim_text") if claim else None,
                })
            except Exception as exc:
                result["errors"].append(f"{finding_id}: {exc}")
                continue

            result["emitted"] += 1
            result["enqueued"] += len(out.get("enqueued") or [])
            result["events"].append(out.get("event_id"))
    finally:
        if own:
            conn.close()

    logger.info(
        "drift_bridge: emitted=%s enqueued=%s sub_threshold=%s errors=%s",
        result["emitted"], result["enqueued"], result["skipped_subthreshold"],
        len(result["errors"]),
    )
    return result


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json as _json

    ap = argparse.ArgumentParser(
        description="DocDrift bridge — docmod findings -> drift/regen/NIST"
    )
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args(argv)
    out = emit_drift()
    if args.json:
        print(_json.dumps(out, indent=2, default=str))
    else:
        print(f"emitted={out['emitted']} enqueued={out['enqueued']} errors={len(out['errors'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
