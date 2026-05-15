#!/usr/bin/env python3
# CUI // SP-CTI
"""Backfill source_citation_registry from existing provenance sources.

One-time migration script that indexes:
  - prov_entities          → citation_type="prov_entity"
  - canvas_ai_decisions    → citation_type="canvas_ai"
  - compliance_evidence_chain → citation_type="compliance_evidence"
  - sbom_records           → citation_type="sbom"
  - wf_citations           → citation_type="hitl"

Usage:
    python tools/provenance/backfill_registry.py --dry-run --json
    python tools/provenance/backfill_registry.py --execute --json
    python tools/provenance/backfill_registry.py --execute --project <project_id>
"""

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.provenance.registry import index_existing_citations
from tools.provenance.trust_scorer import update_all_scores


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill source_citation_registry")
    parser.add_argument("--execute", action="store_true", help="Actually write to DB")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument("--update-scores", action="store_true", help="Recompute trust scores after backfill")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if not args.execute and not args.dry_run:
        parser.print_help()
        return 1

    if args.dry_run:
        # Dry-run: just show what would happen without writing
        stats = {"prov_entities": "pending", "canvas_ai": "pending", "evidence": "pending", "sbom": "pending", "hitl": "pending", "errors": 0}
        if args.json:
            print(json.dumps({"status": "dry_run", "would_backfill": stats}, indent=2))
        else:
            print("DRY RUN: would backfill from:")
            print("  - prov_entities")
            print("  - canvas_ai_decisions")
            print("  - compliance_evidence_chain")
            print("  - sbom_records")
            print("  - wf_citations")
        return 0

    # Execute backfill
    stats = index_existing_citations()
    result = {"status": "completed", "backfilled": stats}

    if args.update_scores:
        score_result = update_all_scores()
        result["trust_scores_updated"] = score_result

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("Backfill complete:")
        for k, v in stats.items():
            print(f"  {k}: {v}")
        if args.update_scores:
            print(f"Trust scores updated: {result['trust_scores_updated']['updated']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
