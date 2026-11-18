# CUI // SP-CTI
"""Export a source's ``icos-discovery/v1`` bundle for ICDEV[OS] (osx-cap-02).

One bundle per declared source (``--source <key>``, ``args/data_discovery_export.yaml``): what the Data Design Canvas
found in the source's stored Explore profile -- tables and columns with types and counts, PII flags (pattern names,
never values), rule-based anomalies, the design's quality rules and their latest results, and the catalogue entries
that cover it. Metadata only, never a datum (``tools/data_canvas/discovery_bundle.py`` builds it).

Deterministic given its inputs: the same profile, rules, runs, products and ``--as-of`` give the same
``content_digest``; ``--at`` pins ``produced_at`` too, so the file is byte for byte the same.

The bundle written is a DRAFT (``publication: null``) unless a person published that exact content: when the source's
newest published bundle in the ledger has the same ``content_digest``, it is written with its ``publication``.
``--published`` writes the newest published bundle as it was published. Publishing itself is a person's declared step
on /data-discovery/; this tool never publishes.

    python tools/data_canvas/discovery_export.py --source <key> --out <dir> [--profile <id>] [--as-of 2026-11-18]
        [--at 2026-11-18T00:00:00Z]
    python tools/data_canvas/discovery_export.py --source <key> --published --out <dir>
    python tools/data_canvas/discovery_export.py --list
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# sys.path BOOTSTRAP (the import root, not a data path): run by path, this
# file's own directory is sys.path[0] and the repo root is not.
_BOOTSTRAP_ROOT = Path(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_ROOT))

from tools.data_canvas import discovery_bundle as db  # noqa: E402


def export(source: str, *, profile_id: str | None = None, as_of: str | None = None, at: str | None = None) -> dict:
    """The bundle: a draft, or carrying its publication when a person published this exact content."""
    b = db.build(source, profile_id=profile_id, as_of=as_of, at=at)
    pub = db.published_bundle(source)
    if pub is not None and pub.get("content_digest") == b["content_digest"]:
        b["publication"] = pub["publication"]
    return b


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Export a source's icos-discovery/v1 bundle for ICDEV[OS] (never publishes).")
    ap.add_argument("--source", help="a source key declared in args/data_discovery_export.yaml")
    ap.add_argument("--profile", help="a stored Explore profile id (default: the source's newest)")
    ap.add_argument("--out", help="the directory to write <source>.json to")
    ap.add_argument("--as-of", dest="as_of", help="YYYY-MM-DD (default: the profile's date)")
    ap.add_argument("--at", help="pin produced_at (ISO 8601)")
    ap.add_argument("--published", action="store_true", help="write the newest PUBLISHED bundle")
    ap.add_argument("--list", action="store_true", help="list the declared sources and their ledger state")
    ap.add_argument("--json", action="store_true", help="JSON summary on stdout (always JSON)")
    args = ap.parse_args(argv)
    try:
        if args.list:
            print(json.dumps(db.sources_view(), indent=2, default=str))
            return 0
        if not args.source or not args.out:
            ap.error("--source and --out are required (or --list)")
        if args.published:
            b = db.published_bundle(args.source)
            if b is None:
                print(json.dumps({"error": f"no bundle of {args.source} has been published", "code": "unpublished"}))
                return 1
        else:
            b = export(args.source, profile_id=args.profile, as_of=args.as_of, at=args.at)
        errs = db.validate(b)
        if errs:
            print(json.dumps({"error": "the bundle does not meet the contract", "errors": errs[:10]}))
            return 1
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{b['source']['key']}.json"
        path.write_text(json.dumps(b, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    except db.DiscoveryRefused as exc:
        print(json.dumps(exc.to_dict()))
        return 1
    print(json.dumps({"path": str(path), "source": b["source"]["key"], "content_digest": b["content_digest"],
                      "published": b["publication"] is not None, "summary": b["summary"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
