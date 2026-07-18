#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV™ tools/ ↔ icdev/tools/ mirror-parity auditor.

The repository keeps a canonical/legacy split: ``tools/`` is the live
authority and ``icdev/tools/`` is the mirrored package copy. Drift between
the two (files present in one but not the other, or differing content)
causes import-time and coherence surprises. This tool reports, per
subtree, the byte-level parity between ``tools/<path>`` and
``icdev/tools/<path>`` using SHA256 comparison.

``tools/`` is treated as the authority: files that differ or are missing
from ``icdev/`` are reported as drift to be reconciled by copying
tools/→icdev/. Files present ONLY in ``icdev/`` are reported separately —
they may be legitimate mirror-only artifacts and require investigation
before deletion, so this tool never deletes.

Usage:
    python tools/dx/mirror_parity.py --paths security_canvas,security --json
    python tools/dx/mirror_parity.py --paths security --fix          # copy tools/→icdev/ drift
    python tools/dx/mirror_parity.py --paths security --gate         # exit 1 on any drift
"""

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _list_files(root: Path) -> set:
    if not root.is_dir():
        return set()
    return {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }


def audit_path(subpath: str, fix: bool = False) -> dict:
    """Audit parity for a single subtree under tools/ vs icdev/tools/.

    Returns a dict with drift classification. When ``fix`` is set, files
    missing from or differing in icdev/ are copied from tools/ (the
    authority). icdev-only files are never deleted.
    """
    live = BASE_DIR / "tools" / subpath
    mirror = BASE_DIR / "icdev" / "tools" / subpath

    result = {
        "path": subpath,
        "live_dir": str(live.relative_to(BASE_DIR)),
        "mirror_dir": str(mirror.relative_to(BASE_DIR)),
        "live_exists": live.is_dir(),
        "mirror_exists": mirror.is_dir(),
        "missing_from_mirror": [],   # in tools/ only — reconcile by copy
        "content_drift": [],         # in both, SHA differs — reconcile by copy
        "mirror_only": [],           # in icdev/ only — INVESTIGATE, do not delete
        "in_parity": 0,
        "copied": [],
    }

    live_files = _list_files(live)
    mirror_files = _list_files(mirror)

    for rel in sorted(live_files - mirror_files):
        result["missing_from_mirror"].append(rel)
    for rel in sorted(mirror_files - live_files):
        result["mirror_only"].append(rel)
    for rel in sorted(live_files & mirror_files):
        if _sha256(live / rel) != _sha256(mirror / rel):
            result["content_drift"].append(rel)
        else:
            result["in_parity"] += 1

    if fix:
        for rel in result["missing_from_mirror"] + result["content_drift"]:
            dst = mirror / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(live / rel, dst)
            result["copied"].append(rel)

    result["clean"] = not (
        result["missing_from_mirror"]
        or result["content_drift"]
        or result["mirror_only"]
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit tools/ ↔ icdev/tools/ mirror parity (SHA256)."
    )
    parser.add_argument(
        "--paths",
        required=True,
        help="Comma-separated subtrees under tools/ (e.g. security_canvas,security).",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON report.")
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Copy tools/→icdev/ for missing/drifted files (never deletes icdev-only).",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="Exit 1 if any drift remains after optional --fix.",
    )
    args = parser.parse_args()

    reports = [audit_path(p.strip(), fix=args.fix) for p in args.paths.split(",") if p.strip()]
    any_drift = not all(r["clean"] for r in reports)

    if args.json:
        print(json.dumps({"reports": reports, "clean": not any_drift}, indent=2))
    else:
        for r in reports:
            status = "CLEAN" if r["clean"] else "DRIFT"
            print(f"[{status}] {r['path']}  ({r['in_parity']} in parity)")
            for rel in r["missing_from_mirror"]:
                print(f"    missing-from-mirror: {rel}" + ("  -> copied" if rel in r["copied"] else ""))
            for rel in r["content_drift"]:
                print(f"    content-drift:       {rel}" + ("  -> copied" if rel in r["copied"] else ""))
            for rel in r["mirror_only"]:
                print(f"    mirror-only (INVESTIGATE, not deleted): {rel}")

    if args.gate:
        # Re-evaluate after fix: copied items no longer count as drift.
        remaining = any(
            (set(r["missing_from_mirror"]) | set(r["content_drift"])) - set(r["copied"])
            or r["mirror_only"]
            for r in reports
        )
        return 1 if remaining else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
