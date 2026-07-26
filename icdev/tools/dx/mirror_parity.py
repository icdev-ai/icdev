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
import functools
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


@functools.lru_cache(maxsize=1)
def _git_ignored() -> frozenset:
    """Repo-relative posix paths git ignores, so they are never called drift.

    Without this the audit compares files that are not in the repository at
    all. `tools/trading/` is gitignored, so a developer with local files there
    sees three phantom "content drifts" against their tracked `icdev/` twins —
    invisible in CI (clean checkout) and unreproducible for anyone else. A
    parity report that differs per working tree is worse than none.
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "--others", "--ignored", "--exclude-standard", "-z"],
            cwd=BASE_DIR, capture_output=True, text=True, timeout=60,
        ).stdout
        return frozenset(p for p in out.split("\0") if p)
    except Exception:  # noqa: BLE001 - git absent: fall back to comparing everything
        return frozenset()


def _list_files(root: Path) -> set:
    if not root.is_dir():
        return set()
    ignored = _git_ignored()
    out = set()
    for p in root.rglob("*"):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        try:
            if p.relative_to(BASE_DIR).as_posix() in ignored:
                continue
        except ValueError:
            pass
        out.add(p.relative_to(root).as_posix())
    return out


def discover_mirrored_paths() -> list:
    """Every ``tools/<pkg>`` that already has an ``icdev/tools/<pkg>`` twin.

    Derived from the tree rather than a hand-maintained list. A curated list is
    how this class of bug survives: ``coherence_checker.check_mirror_drift``
    audits 8 named packages out of 197 that have twins, and compares only
    ``*.py``, so a drifted ``pg_consolidated.sql`` under ``tools/db/schema``
    was invisible to it — the twin existed, the contents differed, and nothing
    looked.

    Presence of a twin IS the signal that a package is meant to be mirrored, so
    a newly mirrored package is covered the moment it exists, with no list to
    remember to update.
    """
    mirror_root = BASE_DIR / "icdev" / "tools"
    live_root = BASE_DIR / "tools"
    if not mirror_root.is_dir():
        return []
    out = set()
    for p in mirror_root.rglob("*"):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        rel = p.relative_to(mirror_root)
        if len(rel.parts) < 2:
            continue  # top-level module, audited as part of the root sweep
        top = rel.parts[0]
        if (live_root / top).is_dir():
            out.add(top)
    return sorted(out)


def audit_all(fix: bool = False) -> dict:
    """Audit every mirrored package. Summary plus only the drifting reports.

    Clean subtrees are counted but not listed — a report nobody can read is a
    report nobody reads.
    """
    reports = [audit_path(p, fix=fix) for p in discover_mirrored_paths()]
    drifting = [r for r in reports if not r["clean"]]
    return {
        "packages_audited": len(reports),
        "packages_with_drift": len(drifting),
        "content_drift": sum(len(r["content_drift"]) for r in reports),
        "missing_from_mirror": sum(len(r["missing_from_mirror"]) for r in reports),
        "mirror_only": sum(len(r["mirror_only"]) for r in reports),
        "clean": not drifting,
        "reports": drifting,
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
        a, b = live / rel, mirror / rel
        # Size first: differing sizes cannot be identical bytes, and identical
        # sizes are rare enough that the hash is only paid when it matters.
        # `--all` compares ~20k file pairs across 200 packages; hashing every
        # one made the sweep too slow to sit in CI, which always runs on a cold
        # checkout. stat() is roughly two orders of magnitude cheaper than a
        # full read, and the result is identical.
        try:
            if a.stat().st_size != b.stat().st_size:
                result["content_drift"].append(rel)
                continue
        except OSError:
            pass  # fall through to the authoritative hash comparison
        if _sha256(a) != _sha256(b):
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
        help="Comma-separated subtrees under tools/ (e.g. security_canvas,security).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Audit every tools/<pkg> that has an icdev/tools/<pkg> twin (auto-discovered).",
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

    if not args.paths and not args.all:
        parser.error("one of --paths or --all is required")

    if args.all:
        summary = audit_all(fix=args.fix)
        reports = summary["reports"]
        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            print(
                f"audited {summary['packages_audited']} mirrored package(s): "
                f"{summary['packages_with_drift']} with drift — "
                f"{summary['content_drift']} content-drift, "
                f"{summary['missing_from_mirror']} missing-from-mirror, "
                f"{summary['mirror_only']} mirror-only"
            )
            for r in reports:
                print(f"  [DRIFT] {r['path']}  ({r['in_parity']} in parity)")
                for rel in r["content_drift"]:
                    print(f"      content-drift:       {rel}")
                for rel in r["missing_from_mirror"]:
                    print(f"      missing-from-mirror: {rel}")
        if args.gate and not summary["clean"]:
            return 1
        return 0

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
