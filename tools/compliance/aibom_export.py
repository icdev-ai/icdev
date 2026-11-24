# CUI // SP-CTI
"""Export ICDEV[IT]'s ``icos-aibom/v1`` AI-BOM for ICDEV[OS] (osx-cop-02).

One bundle: the AI components declared in ``args/aibom_export.yaml`` (router configs, the models they route to and the
registered ones, datasets, gold sets, prompts by digest) as an SPDX 3.0.1 AI document and a CycloneDX 1.7 ML-BOM, each
validated against its official schema (``tools/compliance/aibom_bundle.py`` builds it).

Deterministic given its inputs: the same files, registry rows and ``--as-of`` give the same ``content_digest``;
``--at`` pins ``produced_at`` too, so the file is byte for byte the same.

The bundle written is a DRAFT (``publication: null``) unless a person published that exact content: when the newest
published AI-BOM in the ledger has the same ``content_digest``, it is written with its ``publication``.
``--published`` writes the newest published AI-BOM as it was published. Publishing itself is a person's declared step
on /ai-bom/; this tool never publishes.

    python tools/compliance/aibom_export.py --out <dir> [--as-of 2026-11-18] [--at 2026-11-18T00:00:00Z]
    python tools/compliance/aibom_export.py --published --out <dir>
    python tools/compliance/aibom_export.py --list
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

from tools.compliance import aibom_bundle as ab  # noqa: E402


def export(*, as_of: str | None = None, at: str | None = None) -> dict:
    """The bundle: a draft, or carrying its publication when a person published this exact content."""
    b = ab.build(as_of=as_of, at=at)
    pub = ab.published_bundle()
    if pub is not None and pub.get("content_digest") == b["content_digest"]:
        b["publication"] = pub["publication"]
    return b


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Export ICDEV[IT]'s icos-aibom/v1 AI-BOM for ICDEV[OS] (never publishes).")
    ap.add_argument("--out", help="the directory to write icdev-it.json to")
    ap.add_argument("--as-of", dest="as_of", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--at", help="pin produced_at (ISO 8601)")
    ap.add_argument("--published", action="store_true", help="write the newest PUBLISHED AI-BOM")
    ap.add_argument("--list", action="store_true", help="the declared components and the ledger state")
    ap.add_argument("--json", action="store_true", help="JSON summary on stdout (always JSON)")
    args = ap.parse_args(argv)
    try:
        if args.list:
            v = ab.view()
            print(json.dumps({k: v[k] for k in ("config", "components", "omitted", "ledger", "contract", "standards")},
                             indent=2, default=str))
            return 0
        if not args.out:
            ap.error("--out is required (or --list)")
        if args.published:
            b = ab.published_bundle()
            if b is None:
                print(json.dumps({"error": "no AI-BOM has been published", "code": "unpublished"}))
                return 1
        else:
            b = export(as_of=args.as_of, at=args.at)
        errs = ab.validate(b)
        if errs:
            print(json.dumps({"error": "the AI-BOM does not meet its schemas", "errors": errs[:10]}))
            return 1
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{ab.SYSTEM}.json"
        path.write_text(json.dumps(b, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    except ab.AIBOMRefused as exc:
        print(json.dumps(exc.to_dict()))
        return 1
    print(json.dumps({"path": str(path), "content_digest": b["content_digest"],
                      "published": b["publication"] is not None, "summary": b["summary"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
