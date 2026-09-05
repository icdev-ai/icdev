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
    python tools/dx/mirror_parity.py --files tools/db/storage.py,icdev/tools/db/x.py --json
    python tools/dx/mirror_parity.py --files tools/db/storage.py --fix --gate

``--files`` (mfx-ci-01) audits ONLY the named files against their twins — the
shape a pre-commit hook needs. ``--paths db`` hashes every pair in the package
(864 pairs, 512ms median measured 2026-09-04) to answer a question about the
three files a commit staged, and it reports that package's PRE-EXISTING backlog,
which the committing author neither caused nor can fix without stepping on the
PR that owns it -- correctness of scope, not just the 6.4x. ``--files`` answers
the question in 79ms; it never
calls ``git ls-files --ignored`` because a staged file is by definition tracked.
Either spelling of a path is accepted (``tools/…`` or ``icdev/tools/…``) and is
resolved to the SAME pair, so a change to the mirror alone is drift too.
"""

import argparse
import functools
import hashlib
import json
import re
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


@functools.lru_cache(maxsize=None)
def _git_ignored(root: Path = BASE_DIR) -> frozenset:
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
            cwd=root, capture_output=True, text=True, timeout=60,
        ).stdout
        return frozenset(p for p in out.split("\0") if p)
    except Exception:  # noqa: BLE001 - git absent: fall back to comparing everything
        return frozenset()


def _list_files(root: Path, base: Path = BASE_DIR) -> set:
    if not root.is_dir():
        return set()
    ignored = _git_ignored(base)
    out = set()
    for p in root.rglob("*"):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        try:
            if p.relative_to(base).as_posix() in ignored:
                continue
        except ValueError:
            pass
        out.add(p.relative_to(root).as_posix())
    return out


def discover_mirrored_paths(root: Path = BASE_DIR) -> list:
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
    mirror_root = root / "icdev" / "tools"
    live_root = root / "tools"
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


def audit_all(fix: bool = False, root: Path = BASE_DIR) -> dict:
    """Audit every mirrored package. Summary plus only the drifting reports.

    Clean subtrees are counted but not listed — a report nobody can read is a
    report nobody reads.
    """
    reports = [audit_path(p, fix=fix, root=root) for p in discover_mirrored_paths(root)]
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


def audit_path(subpath: str, fix: bool = False, root: Path = BASE_DIR) -> dict:
    """Audit parity for a single subtree under tools/ vs icdev/tools/.

    Returns a dict with drift classification. When ``fix`` is set, files
    missing from or differing in icdev/ are copied from tools/ (the
    authority). icdev-only files are never deleted.
    """
    live = root / "tools" / subpath
    mirror = root / "icdev" / "tools" / subpath

    result = {
        "path": subpath,
        "live_dir": str(live.relative_to(root)),
        "mirror_dir": str(mirror.relative_to(root)),
        "live_exists": live.is_dir(),
        "mirror_exists": mirror.is_dir(),
        "missing_from_mirror": [],   # in tools/ only — reconcile by copy
        "content_drift": [],         # in both, SHA differs — reconcile by copy
        "mirror_only": [],           # in icdev/ only — INVESTIGATE, do not delete
        "in_parity": 0,
        "copied": [],
    }

    live_files = _list_files(live, root)
    mirror_files = _list_files(mirror, root)

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


def is_mirror_shim(path: Path) -> bool:
    """True if a file is an INTENTIONAL re-export shim of its icdev twin.

    Detected by content marker: the file re-exports from its ``icdev.tools.*``
    twin and says so ("re-export" or "shim") and is short (<120 lines). The
    canonical example is ``tools/llm/agent_loop.py`` -- it must never be flagged
    as drift. Physically-separate full copies are NOT shims and are compared.

    THE ONE COPY OF THE RULE. ``coherence_checker._is_mirror_shim`` delegates
    here (mfx-ci-01), and ``audit_files`` applies it, so the pre-commit hook and
    the coherence gate cannot disagree about what a shim is. Measured on the
    200-commit survey: the five shims (``llm/agent_loop``,
    ``showcase/synthetic_data_engine``, ``testing/qa_agent_runner``,
    ``testing/selector_healer``, ``billing/tier``) were three of the seven
    post-exclusion "drifts" -- every one a false refusal without this.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return False
    if text.count("\n") >= 120:
        return False
    imports_twin = re.search(r"from\s+icdev\.tools[\w.]*\s+import\b", text) is not None
    # "re-export" OR "shim": three of the five (testing/qa_agent_runner,
    # testing/selector_healer, billing/tier) say "Backward-compat shim" and were
    # counted as drift by the narrower marker -- measured 2026-09-04, they were
    # two of the survey's seven post-exclusion fires and would have refused a
    # commit that merely touched the shim.
    lowered = text.lower()
    marks_reexport = "re-export" in lowered or "shim" in lowered
    return imports_twin and marks_reexport


def normalise_live_rel(path, root: Path = BASE_DIR):
    """``tools/x/y`` | ``icdev/tools/x/y`` | absolute -> ``x/y``, or None if outside.

    Both spellings name ONE pair, so a change staged on the mirror side alone is
    audited against the same twin as a change on the live side.
    """
    p = Path(str(path).replace("\\", "/"))
    if p.is_absolute():
        try:
            p = p.resolve().relative_to(root.resolve())
        except ValueError:
            return None
    parts = p.as_posix().split("/")
    if parts[:2] == ["icdev", "tools"]:
        parts = parts[2:]
    elif parts[:1] == ["tools"]:
        parts = parts[1:]
    else:
        return None
    if len(parts) < 2 or "__pycache__" in parts:
        return None  # a top-level tools/*.py module has no package twin
    return "/".join(parts)


def audit_files(files, fix: bool = False, root: Path = BASE_DIR) -> dict:
    """Audit ONLY the named files against their twins. No package walk, no git.

    Classification per file, and the buckets are never merged:
      content_drift        both sides exist and the bytes differ  <- the finding
      missing_from_mirror  live exists, twin absent (reconcile by copy)
      mirror_only          twin exists, live absent (INVESTIGATE, never deleted)
      in_parity            identical bytes
      not_mirrored         the PACKAGE has no icdev/tools/<pkg> twin at all -- a
                           file there cannot drift, and is out of scope
      shim                 an intentional re-export shim (is_mirror_shim); the two
                           names resolve to ONE module object, so there is no
                           stale half and comparing bytes is meaningless
      outside              not under tools/ or icdev/tools/ (ignored, listed)

    ``audit_path`` / ``audit_all`` deliberately do NOT apply the shim rule: the
    recorded baseline (args/mirror_drift_baseline.yaml) was measured without it
    and its test refuses an empty file, so the package-level report keeps its
    historical meaning and the shim exclusion is applied by the consumers that
    gate (coherence_checker, and this per-file audit).
    """
    result = {
        "scope": "files",
        "files": [],
        "content_drift": [],
        "missing_from_mirror": [],
        "mirror_only": [],
        "in_parity": [],
        "not_mirrored": [],
        "shim": [],
        "outside": [],
        "copied": [],
    }
    seen = set()
    for raw in files:
        rel = normalise_live_rel(raw, root)
        if rel is None:
            result["outside"].append(str(raw))
            continue
        if rel in seen:
            continue
        seen.add(rel)
        result["files"].append(rel)
        pkg = rel.split("/", 1)[0]
        live = root / "tools" / rel
        mirror = root / "icdev" / "tools" / rel
        if not (root / "icdev" / "tools" / pkg).is_dir():
            result["not_mirrored"].append(rel)
            continue
        if (live.is_file() and is_mirror_shim(live)) or (mirror.is_file() and is_mirror_shim(mirror)):
            result["shim"].append(rel)
        elif live.is_file() and not mirror.is_file():
            result["missing_from_mirror"].append(rel)
        elif mirror.is_file() and not live.is_file():
            result["mirror_only"].append(rel)
        elif live.is_file() and mirror.is_file():
            if _sha256(live) != _sha256(mirror):
                result["content_drift"].append(rel)
            else:
                result["in_parity"].append(rel)
        # neither side exists (a staged deletion): nothing to compare
    if fix:
        for rel in result["missing_from_mirror"] + result["content_drift"]:
            dst = root / "icdev" / "tools" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / "tools" / rel, dst)
            result["copied"].append(rel)
    result["clean"] = not (result["content_drift"] or result["missing_from_mirror"] or result["mirror_only"])
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
    parser.add_argument(
        "--files",
        help="Comma-separated files (tools/... or icdev/tools/...) audited against their twins only.",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="Checkout to audit (default: the checkout this tool lives in).",
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
    root = Path(args.root).resolve() if args.root else BASE_DIR

    if not args.paths and not args.all and not args.files:
        parser.error("one of --paths, --files or --all is required")

    if args.files:
        report = audit_files(
            [f.strip() for f in args.files.split(",") if f.strip()], fix=args.fix, root=root
        )
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            status = "CLEAN" if report["clean"] else "DRIFT"
            print(f"[{status}] {len(report['files'])} file(s)  ({len(report['in_parity'])} in parity)")
            for rel in report["content_drift"]:
                print(f"    content-drift:       {rel}" + ("  -> copied" if rel in report["copied"] else ""))
            for rel in report["missing_from_mirror"]:
                print(f"    missing-from-mirror: {rel}" + ("  -> copied" if rel in report["copied"] else ""))
            for rel in report["mirror_only"]:
                print(f"    mirror-only (INVESTIGATE, not deleted): {rel}")
            for rel in report["not_mirrored"]:
                print(f"    not-mirrored (package has no twin): {rel}")
            for rel in report["shim"]:
                print(f"    shim (one module object, never drift): {rel}")
        if args.gate:
            remaining = (
                (set(report["content_drift"]) | set(report["missing_from_mirror"])) - set(report["copied"])
                or report["mirror_only"]
            )
            return 1 if remaining else 0
        return 0

    if args.all:
        summary = audit_all(fix=args.fix, root=root)
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

    reports = [audit_path(p.strip(), fix=args.fix, root=root) for p in args.paths.split(",") if p.strip()]
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
