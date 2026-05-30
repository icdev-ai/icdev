#!/usr/bin/env python3
"""Headless CLI for the Document Intelligence Canvas (DIC).

[TEMPLATE: CUI // SP-CTI]

Usage:
    python -m tools.document_intelligence --ingest <path> --collection <id> --json
    python -m tools.document_intelligence --ingest doc.md --collection contracts \
        --tenant acme --classification CUI --no-embed --no-kg
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure repo root on path when invoked as a module/script.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.document_intelligence.ingest_orchestrator import ingest_file


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m tools.document_intelligence",
        description="DIC ingest orchestrator — file -> provider -> RAG + KG + dic rows",
    )
    ap.add_argument("--ingest", metavar="PATH", help="file to ingest")
    ap.add_argument("--collection", metavar="ID", help="target collection id")
    ap.add_argument("--tenant", "--tenant-id", dest="tenant", help="tenant id stamp")
    ap.add_argument(
        "--classification", help="classification stamp (e.g. UNCLASSIFIED, CUI)"
    )
    ap.add_argument("--created-by", dest="created_by", help="author user id")
    ap.add_argument(
        "--no-embed", action="store_true", help="skip vector-store embedding"
    )
    ap.add_argument(
        "--no-kg", action="store_true", help="skip knowledge-graph bridging"
    )
    ap.add_argument("--json", action="store_true", help="emit JSON")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if not args.ingest or not args.collection:
        print(
            "error: --ingest <path> and --collection <id> are required",
            file=sys.stderr,
        )
        return 2

    try:
        outcome = ingest_file(
            args.ingest,
            args.collection,
            tenant_id=args.tenant,
            classification=args.classification,
            created_by=args.created_by,
            embed=not args.no_embed,
            bridge_kg=not args.no_kg,
        )
    except FileNotFoundError as e:
        if args.json:
            print(json.dumps({"ok": False, "error": str(e)}))
        else:
            print(f"error: {e}", file=sys.stderr)
        return 1

    data = outcome.to_dict()
    if args.json:
        print(json.dumps({"ok": True, **data}, indent=2))
    else:
        print(f"ingested {data['doc_id']} (provider={data['provider']})")
        print(f"  collection : {data['collection_id']}")
        print(f"  version    : {data['version_id']} (human_authored/approved)")
        print(f"  source_id  : {data['source_id']}")
        print(f"  chunks     : {data['chunks']} (embedded={data['chunks_embedded']})")
        print(
            f"  kg         : {data['kg_entities']} entities, "
            f"{data['kg_relationships']} relationships"
        )
        print(f"  stamp      : tenant={data['tenant_id']} class={data['classification']}")
        if data["errors"]:
            print(f"  warnings   : {len(data['errors'])}")
            for err in data["errors"]:
                print(f"    - {err}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
