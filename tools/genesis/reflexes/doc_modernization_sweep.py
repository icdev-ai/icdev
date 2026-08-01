# CUI // SP-CTI
"""Document Modernization Sweep — nightly Genesis reflex.

Keeps enterprise documents honest as evidence changes:
  1. refresh EOL caches (endoflife.date + migration-canvas Cisco EoX;
     network-permitting, no-ops offline)
  2. recompute de facto deployment standards from ni_devices
  3. incremental document scan (skips docs whose approved version AND combined
     pack evidence hash are unchanged — docmod_doc_scan_state)
  4. draft TRUST-gated redlines for new findings (capped by
     args/docmod/docmod_config.yaml max_redlines_per_sweep)
  5. emit per-document kanban rollups + reconcile resolved ones

Registered in tools/genesis/daemon.py REFLEX_NAMES and
args/genesis_config.yaml (all three points — known gotcha).
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

import json
import uuid
from typing import Any, Dict

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

CADENCE_HOURS = 24


class DocModernizationSweepReflex:
    """Nightly sweep: evidence refresh → learner → scan → redlines → cards."""

    def run(self, context: Dict[str, Any], db_conn=None) -> Dict[str, Any]:
        reflex_id = context.get("reflex_id", f"dms-{uuid.uuid4().hex[:10]}")
        dry_run = bool(context.get("dry_run", False))
        result: Dict[str, Any] = {"reflex_id": reflex_id, "dry_run": dry_run}

        # 1. Evidence refresh — every step best-effort; offline is normal.
        try:
            from tools.doc_modernization.eol_products_sync import sync as eol_sync
            result["eol_products_sync"] = eol_sync()
        except Exception as exc:
            logger.warning("docmod sweep: eol_products_sync failed: %s", exc)
            result["eol_products_sync"] = {"error": str(exc)}
        try:
            from tools.migration_canvas.eol_sync import sync_eol_database
            result["mc_eol_sync"] = sync_eol_database()
        except Exception as exc:
            logger.debug("docmod sweep: migration-canvas eol sync unavailable: %s", exc)
            result["mc_eol_sync"] = {"skipped": str(exc)}
        # NIST publication revision feed -> docmod_nist_pubs (policy_refs evidence).
        # Scheduled pull only; cadence-gated and https-only, self-skips offline.
        try:
            from tools.doc_modernization.nist_pubs_sync import sync as nist_sync
            result["nist_pubs_sync"] = nist_sync()
        except Exception as exc:
            logger.warning("docmod sweep: nist_pubs_sync failed: %s", exc)
            result["nist_pubs_sync"] = {"error": str(exc)}

        # 2. De facto learner
        try:
            from tools.doc_modernization.defacto_learner import recompute
            result["defacto"] = recompute()
        except Exception as exc:
            logger.warning("docmod sweep: defacto recompute failed: %s", exc)
            result["defacto"] = {"error": str(exc)}

        if dry_run:
            result["skipped"] = "dry_run — no scan/redlines/cards"
            return {"success": True, "metric_value": 0, "details": result}

        # 3. Incremental corpus scan
        try:
            from tools.doc_modernization.scanner import scan_collection
            scan = scan_collection(collection_id=None, trigger="reflex")
            result["scan"] = scan
        except Exception as exc:
            logger.error("docmod sweep: scan failed: %s", exc)
            result["scan"] = {"error": str(exc)}
            scan = {}

        # 3b. Egress-safe URL link-rot check. NETWORK feature, default OFF; the
        # checker self-skips when disabled or when there is no egress (air-gap),
        # and its outbound calls pass an SSRF egress guard (https-only, every
        # resolved IP checked against internal ranges before connecting).
        try:
            from tools.doc_modernization.link_check import check_corpus_links
            result["link_rot"] = check_corpus_links(trigger="reflex")
        except Exception as exc:
            logger.warning("docmod sweep: link-rot check failed: %s", exc)
            result["link_rot"] = {"error": str(exc)}

        # 3c. CVE → docmod bridge. Products CITED in documents that have a known
        # CVE in the supply-chain cve_triage store raise HITL compliance drift
        # (via acoic, the same sink as step 6). Local read only — no egress; a
        # missing/empty cve_triage degrades to zero emissions and never raises.
        try:
            from tools.doc_modernization.pack_loader import load_config
            if load_config().get("cve_bridge_enabled", True):
                from tools.doc_modernization.cve_bridge import bridge_cves
                result["cve_bridge"] = bridge_cves(
                    project_id=load_config().get("cve_bridge_project_id")
                )
            else:
                result["cve_bridge"] = {"skipped": "cve_bridge_enabled=false"}
        except Exception as exc:
            logger.warning("docmod sweep: cve bridge failed: %s", exc)
            result["cve_bridge"] = {"error": str(exc)}

        # 4. Capped TRUST-gated redline drafting
        try:
            from tools.doc_modernization.redline_drafter import draft_open_redlines
            result["redlines"] = draft_open_redlines()
        except Exception as exc:
            logger.warning("docmod sweep: redline drafting failed: %s", exc)
            result["redlines"] = {"error": str(exc)}

        # 5. Kanban rollups + reconciliation
        try:
            from tools.doc_modernization.card_bridge import emit_rollups, reconcile
            result["cards"] = emit_rollups()
            result["reconciled"] = reconcile()
        except Exception as exc:
            logger.warning("docmod sweep: card bridge failed: %s", exc)
            result["cards"] = {"error": str(exc)}

        # 6. DocDrift — findings become compliance drift: impact scoring, HITL
        # regen queue, NIST re-map, SSP fragments. Separate sink from the card
        # bridge above (work tracking vs compliance); both read the same findings.
        # Isolated so a DocDrift failure never costs us the scan or the cards.
        try:
            from tools.doc_modernization.drift_bridge import emit_drift
            result["drift"] = emit_drift()
        except Exception as exc:
            logger.warning("docmod sweep: drift bridge failed: %s", exc)
            result["drift"] = {"error": str(exc)}

        findings_new = int((scan or {}).get("findings_new", 0) or 0)
        return {"success": True, "metric_value": findings_new, "details": result}


def run(context: Dict[str, Any], db_conn=None) -> Dict[str, Any]:
    """Module-level entry point (Genesis daemon dispatch contract)."""
    return DocModernizationSweepReflex().run(context, db_conn)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Document Modernization Sweep Reflex")
    parser.add_argument("--dry-run", action="store_true", help="Refresh evidence only")
    parser.add_argument("--json", dest="json_out", action="store_true", help="JSON output")
    args = parser.parse_args()

    out = run({"dry_run": args.dry_run})
    print(json.dumps(out, indent=2, default=str))
