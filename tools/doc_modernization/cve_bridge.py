# CUI // SP-CTI
"""CVE → docmod bridge — cited products with known CVEs become compliance drift.

This bridge adds NO poller. It REUSES the existing supply-chain CVE store
(``cve_triage``, populated by the NVD/CISA/OSV ingestion pipeline documented in
goals/threat_triage.md). For each DIC document it re-runs the network_hardware +
software pack extractors to learn which products the document CITES, matches
those product strings against ``cve_triage.package_name``, and routes each hit
through the SAME compliance sink the DocDrift bridge uses
(:func:`tools.document_intelligence.acoic.handle_drift`) — which records a drift
event, enqueues a HITL regen/triage item, and re-maps the affected NIST controls
(RA-5 Vulnerability Monitoring, SI-2 Flaw Remediation).

Why emit straight to acoic instead of inserting a docmod_findings row: the
scanner OWNS docmod_findings for a document. On its next incremental pass it
supersedes any open finding whose dedupe_key its packs did not re-emit — a CVE
finding (a finding_type the packs never produce) would be silently auto-resolved.
Emitting to the drift sink keeps CVE evidence on the compliance track without
colliding with the scanner's lifecycle, and reuses the exact payload contract of
tools/doc_modernization/drift_bridge.py.

Invariants: deterministic (a CVE↔product string match — TRUST rule 1, no LLM
verdict); idempotent (a stable ``dedup_key`` per doc/product/CVE); HITL (acoic's
regen queue, never an auto-edit); air-gap safe (a missing/empty cve_triage table
degrades to zero emissions and never raises).
"""
from __future__ import annotations

import re

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# NIST controls a "cited product has a CVE" finding implicates — declared here,
# never inferred from finding text (TRUST rule 1).
_CVE_CONTROL_IDS = ["RA-5", "SI-2"]
_PRODUCT_PACKS = ("network_hardware", "software")
_VALID_SEVERITIES = {"critical", "high", "medium", "low", "info"}


def _evidence_connect():
    """RLS-free backend connection for evidence reads (docs, chunks, cve_triage).

    Same rationale as scanner._evidence_connect: the DIC tables carry
    tenant_id/classification and cve_triage does not, so the canvas
    (security_context=None) connection is the correct isolation for read-only
    evidence and never trips the RLS predicate."""
    from tools.db.storage import get_canvas_connection
    return get_canvas_connection()


def _product_packs(packs=None) -> list:
    """The extractor packs that name products (network_hardware, software)."""
    if packs is not None:
        return list(packs)
    from tools.doc_modernization.pack_loader import load_packs

    loaded = load_packs()
    return [loaded[k] for k in _PRODUCT_PACKS if k in loaded]


def _product_matches(label: str, package_name: str) -> bool:
    """Deterministic product-string match between a cited label and a CVE package.

    Case- and whitespace-insensitive; matches on equality or whole-string
    containment either direction, guarded by a minimum package length so a tiny
    package token never matches every document."""
    a = re.sub(r"\s+", " ", (label or "").strip().lower())
    b = re.sub(r"\s+", " ", (package_name or "").strip().lower())
    if not a or not b or len(b) < 3:
        return False
    return a == b or b in a or a in b


def _load_cves(conn, project_id=None) -> list[dict]:
    """Triaged CVE rows (air-gap safe — missing table => empty list).

    ``project_id`` optionally scopes to one supply-chain project; None (the
    default) reads CVEs across all projects."""
    try:
        if project_id:
            rows = conn.execute(
                "SELECT cve_id, package_name, severity, cvss_score, triage_rationale "
                "FROM cve_triage WHERE project_id = %s",
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT cve_id, package_name, severity, cvss_score, triage_rationale "
                "FROM cve_triage"
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:  # table absent / not initialized — normal offline
        logger.info("cve_bridge: cve_triage unavailable (%s) — no CVE evidence", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return []


def _cited_products(conn, doc_id: str, version_id: str, packs) -> dict:
    """{label_lower: CandidateEntity} for every product a document cites."""
    from tools.doc_modernization.scanner import _doc_chunks

    labels: dict = {}
    for text, chunk_ref in _doc_chunks(conn, doc_id, version_id):
        for pack in packs:
            try:
                for ent in pack.extract(text, chunk_ref):
                    labels.setdefault(ent.label.lower(), ent)
            except Exception as exc:
                logger.warning("cve_bridge: %s.extract failed: %s",
                               getattr(pack, "pack_id", "?"), exc)
    return labels


def _latest_approved_version(conn, doc_id: str) -> str | None:
    row = conn.execute(
        "SELECT version_id FROM dic_versions WHERE doc_id=%s AND status='approved' "
        "ORDER BY version_no DESC LIMIT 1",
        (doc_id,),
    ).fetchone()
    return dict(row)["version_id"] if row else None


def bridge_cves(conn=None, packs=None, project_id=None) -> dict:
    """Match products cited in DIC documents against cve_triage and emit HITL drift.

    Returns {docs_scanned, matched, emitted, enqueued, errors, events}.
    """
    from tools.document_intelligence import acoic

    own = conn is None
    if own:
        conn = _evidence_connect()
    result: dict = {
        "docs_scanned": 0, "matched": 0, "emitted": 0, "enqueued": 0,
        "errors": [], "events": [],
    }
    try:
        product_packs = _product_packs(packs)
        cves = _load_cves(conn, project_id=project_id)
        if not product_packs or not cves:
            result["skipped"] = "no product packs" if not product_packs else "no CVEs in store"
            return result

        try:
            doc_rows = conn.execute(
                "SELECT doc_id, tenant_id, classification FROM dic_documents"
            ).fetchall()
        except Exception as exc:
            result["errors"].append(f"docs: {exc}")
            return result

        for drow in (dict(r) for r in doc_rows):
            doc_id = drow["doc_id"]
            version_id = _latest_approved_version(conn, doc_id)
            if not version_id:
                continue
            result["docs_scanned"] += 1
            cited = _cited_products(conn, doc_id, version_id, product_packs)
            if not cited:
                continue
            for entity in cited.values():
                for cve in cves:
                    if not _product_matches(entity.label, cve.get("package_name", "")):
                        continue
                    result["matched"] += 1
                    cve_id = cve.get("cve_id") or "CVE-UNKNOWN"
                    severity = (cve.get("severity") or "medium").lower()
                    if severity not in _VALID_SEVERITIES:
                        severity = "medium"
                    dedup_key = f"docmod-cve:{doc_id}:{entity.label.lower()}:{cve_id}"
                    cvss = cve.get("cvss_score")
                    rationale = (
                        f"{entity.label} (cited in this document) is affected by "
                        f"{cve_id}"
                        + (f" (CVSS {cvss})" if cvss is not None else "")
                        + f" per package '{cve.get('package_name')}' in the CVE triage store."
                    )
                    try:
                        out = acoic.handle_drift({
                            "source": "docmod.cve",
                            "entity": entity.label,
                            "severity": severity,
                            "document_id": doc_id,
                            "control_ids": _CVE_CONTROL_IDS,
                            "dedup_key": dedup_key,
                            "classification": drow.get("classification"),
                            "tenant_id": drow.get("tenant_id"),
                            "finding_type": "vulnerable_component",
                            "cve_id": cve_id,
                            "package_name": cve.get("package_name"),
                            "cvss_score": cvss,
                            "section_heading": entity.chunk_ref.section,
                            "page": entity.chunk_ref.page,
                            "chunk_link_id": entity.chunk_ref.chunk_link_id,
                            "rationale": rationale,
                        })
                    except Exception as exc:
                        result["errors"].append(f"{doc_id}:{cve_id}: {exc}")
                        continue
                    result["emitted"] += 1
                    result["enqueued"] += len(out.get("enqueued") or [])
                    result["events"].append(out.get("event_id"))
    finally:
        if own:
            try:
                conn.close()
            except Exception:
                pass

    logger.info(
        "cve_bridge: docs=%s matched=%s emitted=%s errors=%s",
        result["docs_scanned"], result["matched"], result["emitted"],
        len(result["errors"]),
    )
    return result


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json as _json

    ap = argparse.ArgumentParser(
        description="CVE → docmod bridge — cited products with CVEs -> HITL drift"
    )
    ap.add_argument("--project-id", dest="project_id", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    out = bridge_cves(project_id=args.project_id)
    if args.json:
        print(_json.dumps(out, indent=2, default=str))
    else:
        print(f"docs={out['docs_scanned']} matched={out['matched']} "
              f"emitted={out['emitted']} errors={len(out['errors'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
