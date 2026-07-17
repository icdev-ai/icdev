# CUI // SP-CTI
"""DIC Inbox Sweep — ingest whatever landed in the drop folder.

``tools/document_intelligence/inbox.py`` was built for exactly one problem: teams
pull documents out of SharePoint with a browser session (Playwright, export, any
acquisition path) and then have nowhere to put them — "the acquisition was
automated and the ingestion was not". It works, it is tested, and until now
**nothing ever ran it**. It had no caller anywhere in the repo: no reflex, no
daemon, no route, no MCP tool. The landing zone existed with no one watching it.

This reflex is the missing launcher. It does not scrape, authenticate, or know
what SharePoint is — a browser session already solves auth across on-prem and
M365 better than a REST client can, and the repo has no Playwright of its own.
Files arrive by whatever means the operator already uses; this notices them.

Why not the SharePoint connector: ``tools/sharepoint/`` looks like the answer and
is not. Its ``client.get_file()`` — the one method that would fetch bytes — is
dead code called by nothing, ``sharepoint_documents.content_hash`` hashes the
file's *path* rather than its content, it has zero tests, and it needs the very
login the operator does not have from ICDEV. Ingesting SharePoint metadata into
``sharepoint_*`` tables that no DIC/RAG/KG code reads gets you zero documents.

Cadence is minutes, not hours: this is a drop folder, not a nightly sweep. A CR
approved this morning should not wait until 03:30 to become evidence.

Registered in tools/genesis/daemon.py REFLEX_NAMES and args/genesis_config.yaml
(all three points — known gotcha; miss one and it silently never runs).
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

import uuid
from typing import Any, Dict

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# Informational only — args/genesis_config.yaml interval_seconds is what the
# daemon actually schedules on (it does not read this module's constants).
CADENCE_MINUTES = 5

_DEFAULT_COLLECTION = "change-records"


class DicInboxSweepReflex:
    """Scan the DIC drop folder; ingest anything new into its collection."""

    def run(self, context: Dict[str, Any], db_conn=None) -> Dict[str, Any]:
        # `context` is this reflex's entry from args/genesis_config.yaml — the
        # daemon passes the config dict straight through (daemon.py: module.run(
        # config, trust)), which is how doc_modernization_sweep reads dry_run.
        reflex_id = context.get("reflex_id", f"dis-{uuid.uuid4().hex[:10]}")
        watch_dir = context.get("watch_dir") or None  # None -> inbox's default
        collection_id = context.get("collection_id") or _DEFAULT_COLLECTION
        dry_run = bool(context.get("dry_run", False))
        recursive = bool(context.get("recursive", False))
        move_processed = bool(context.get("move_processed", False))

        try:
            from tools.document_intelligence.inbox import ingest_directory

            out = ingest_directory(
                watch_dir=watch_dir,
                collection_id=collection_id,
                recursive=recursive,
                dry_run=dry_run,
                move_processed=move_processed,
                created_by="dic_inbox_sweep",
            )
        except Exception as exc:
            # A sweep that could not run is a failure, not a quiet zero. The
            # daemon reads success=False and surfaces it.
            logger.warning("dic inbox sweep: ingest_directory failed: %s", exc)
            return {
                "success": False,
                "metric_value": 0.0,
                "details": {"reflex_id": reflex_id, "error": str(exc)},
            }

        ingested = int(out.get("ingested", 0) or 0)
        failed = int(out.get("failed", 0) or 0)

        # NOT `success: True` unconditionally. The sibling sweep
        # (doc_modernization_sweep) collects per-step errors into its result and
        # then returns success=True regardless, so a persistently broken step
        # degrades to a logger.warning nobody reads. A drop folder where every
        # file fails to ingest must not report success — that is precisely the
        # "silence looks like health" failure this canvas already suffered from.
        #
        # Note per-file failures are non-fatal inside ingest_directory by design
        # (one unreadable PDF must not strand the rest of a drop), and a failed
        # file is deliberately left out of the state file so the next sweep
        # retries it. So `failed > 0` here means "retry pending", and it is
        # honest to report it rather than swallow it.
        success = failed == 0

        if ingested or failed:
            logger.info(
                "dic inbox sweep: ingested=%s skipped=%s failed=%s -> %s",
                ingested, out.get("skipped_duplicate", 0), failed, collection_id,
            )

        return {
            "success": success,
            "metric_value": float(ingested),
            "details": {"reflex_id": reflex_id, **out},
        }


def run(context: Dict[str, Any], db_conn=None) -> Dict[str, Any]:
    """Module-level entry point (Genesis daemon dispatch contract)."""
    return DicInboxSweepReflex().run(context, db_conn)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="DIC Inbox Sweep Reflex")
    parser.add_argument("--dir", help="Watch directory (default: data/dic_inbox)")
    parser.add_argument("--collection", default=_DEFAULT_COLLECTION,
                        help=f"Target collection (default: {_DEFAULT_COLLECTION})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would ingest; write nothing")
    parser.add_argument("--json", dest="json_out", action="store_true", help="JSON output")
    args = parser.parse_args()

    result = run({
        "watch_dir": args.dir,
        "collection_id": args.collection,
        "dry_run": args.dry_run,
    })
    if args.json_out:
        print(json.dumps(result, indent=2, default=str))
    else:
        d = result["details"]
        print(f"ingested={d.get('ingested')} skipped={d.get('skipped_duplicate')} "
              f"failed={d.get('failed')} success={result['success']}")
        for err in d.get("errors", []):
            print(f"  ! {err}")
    raise SystemExit(0 if result["success"] else 1)
