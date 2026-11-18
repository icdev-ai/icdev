# CUI // SP-CTI
"""Export the FORGE Academy's xAPI batch for ICDEV[OS], and verify an export (icx-cop-01).

The batch (``icos-cop-academy/v1``) and how it is built: ``tools/idp/cop_tracks.py``. Only a PUBLISHED batch is
exported for ICDEV[OS]: a person who did not prepare it took the declared step decl-wft-cop-academy-xapi-publish on
/idp/cop-tracks. The export is two files, because the batch schema admits no extra field:

    icos-cop-academy.json               the batch, exactly as prepared (what ICDEV[OS] imports)
    icos-cop-academy.publication.json   its publication (icos-cop-academy-publication/v1, ICDEV[OS]'s contract)

Usage:
    python tools/idp/cop_xapi_export.py --published --out <dir>      # the newest published batch, both files
    python tools/idp/cop_xapi_export.py --verify <dir> [--ledger]    # both files against the pinned schemas,
                                                                     # the digest, the separation (and the ledger)
    python tools/idp/cop_xapi_export.py --preview --at <ISO time>    # the batch the ledger supports now (unpublished;
                                                                     # deterministic given --at)

Exit codes: 0 done / verifies, 1 refused / does not verify, 2 could not run.
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

from tools.idp import cop_tracks  # noqa: E402

BATCH_FILE = "icos-cop-academy.json"
PUBLICATION_FILE = "icos-cop-academy.publication.json"


def _write(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def export_published(out: Path) -> dict:
    """Write the newest published batch and its publication to ``out``. Refused when none is published."""
    pub = cop_tracks.published()
    if pub is None:
        raise cop_tracks.CopRefused(409, "unpublished", "no xAPI batch has been published: nothing leaves")
    out.mkdir(parents=True, exist_ok=True)
    _write(out / BATCH_FILE, pub["batch"])
    _write(out / PUBLICATION_FILE, pub["publication"])
    return {"written": [str(out / BATCH_FILE), str(out / PUBLICATION_FILE)],
            "content_digest": pub["batch"]["content_digest"], "signed_by": pub["publication"]["signed_by"],
            "statements": len(pub["batch"]["statements"])}


def verify_dir(src: Path, *, ledger: bool = False) -> dict:
    """Verify an export directory; with ``ledger``, also against this platform's ledger."""
    def load(name: str):
        try:
            return json.loads((src / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    rows = cop_tracks.read_events() if ledger else None
    return cop_tracks.verify(load(BATCH_FILE), load(PUBLICATION_FILE), rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Export or verify the FORGE Academy xAPI batch for ICDEV[OS].")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--published", action="store_true", help="write the newest published batch and publication")
    mode.add_argument("--verify", type=Path, metavar="DIR", help="verify an export directory")
    mode.add_argument("--preview", action="store_true", help="the batch the ledger supports now (unpublished)")
    ap.add_argument("--out", type=Path, help="output directory (--published, --preview)")
    ap.add_argument("--at", help="produced_at for --preview (ISO 8601); deterministic given it")
    ap.add_argument("--ledger", action="store_true", help="--verify: also check against this platform's ledger")
    ap.add_argument("--json", action="store_true", help="accepted for CLI consistency (output is always JSON)")
    args = ap.parse_args(argv)
    try:
        if args.published:
            if not args.out:
                ap.error("--published needs --out")
            out = export_published(args.out)
        elif args.verify:
            out = verify_dir(args.verify, ledger=args.ledger)
            print(json.dumps(out, indent=2, sort_keys=True))
            return 0 if out["ok"] else 1
        else:
            batch = cop_tracks.build(at=args.at)
            if args.out:
                args.out.mkdir(parents=True, exist_ok=True)
                _write(args.out / "icos-cop-academy.preview.json", batch)
            out = {"content_digest": batch["content_digest"], "statements": len(batch["statements"]),
                   "paths": batch["paths"], "published": False}
    except cop_tracks.CopRefused as exc:
        print(json.dumps(exc.to_dict()), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — could not run is not "nothing found"
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
