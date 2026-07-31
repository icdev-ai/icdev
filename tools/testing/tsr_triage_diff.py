#!/usr/bin/env python3
"""Triage a pytest slice run across two checkouts (tsr-core-01-d3).

Reads two `pytest --tb=no -q -rf` transcripts -- one produced in the shared
checkout (C:\\AI\\ICDev, carrying an ambient multi-GB data/icdev.db accumulated
by months of dashboard and runner traffic) and one produced in a clean worktree
off origin/main (schema-seeded DB only) -- and emits the comparison table.

Classification, per test file:

    ambient  failed clean-only  -- the file needs ambient DB rows or other state
                                  that only the shared checkout has accumulated.
    real     failed in BOTH     -- a genuine defect; reproduces from a cold start.
    shared   failed shared-only -- shared-checkout contamination (stale rows,
                                  leftover fixtures) rather than a code defect.
    unknown  a confounder applies (source file differs between the two checkouts)

Usage:
    python tools/testing/tsr_triage_diff.py --shared <txt> --clean <txt> \
        --slice docs/testing/tsr-core-01-slice.txt --out <md>
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from pathlib import Path

# A pytest short-summary line: "FAILED tests/x.py::test_y - AssertionError: ..."
_OUTCOME_RE = re.compile(r"^(FAILED|ERROR)\s+(\S+?)(?:::\S+)?\s*(?:-\s*(.*))?$")
# The trailing count line: "12 failed, 340 passed, 3 errors in 88.12s"
_TOTALS_RE = re.compile(r"(\d+)\s+(failed|passed|errors?|skipped|xfailed|xpassed)")


def parse(path: Path) -> tuple[dict[str, dict[str, int]], dict[str, int], dict[str, str]]:
    """Return (per-file outcome counts, slice-wide totals, per-file first reason)."""
    per_file: dict[str, dict[str, int]] = defaultdict(lambda: {"FAILED": 0, "ERROR": 0})
    totals: dict[str, int] = {}
    reasons: dict[str, str] = {}

    if not path.exists():
        return {}, {}, {}

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        m = _OUTCOME_RE.match(line)
        if m:
            kind, test_path, reason = m.group(1), m.group(2), m.group(3)
            test_path = test_path.replace("\\", "/")
            per_file[test_path][kind] += 1
            if reason and test_path not in reasons:
                reasons[test_path] = reason.strip()[:110]
            continue
        # Totals live on the final "=== N failed, M passed in ..." banner.
        if (" in " in line) and ("passed" in line or "failed" in line or "error" in line):
            found = _TOTALS_RE.findall(line)
            if found:
                # Transcripts are concatenated shard outputs, so accumulate rather
                # than overwrite -- one summary banner per shard.
                for count, label in found:
                    key = label.rstrip("s")
                    totals[key] = totals.get(key, 0) + int(count)

    return dict(per_file), totals, reasons


def classify(shared_bad: int, clean_bad: int, confounded: bool) -> str:
    if confounded:
        return "unknown"
    if shared_bad and clean_bad:
        return "real"
    if clean_bad and not shared_bad:
        return "ambient"
    if shared_bad and not clean_bad:
        return "shared"
    return "clean"


def fmt(counts: dict[str, int] | None) -> str:
    if not counts:
        return "pass"
    bits = []
    if counts.get("FAILED"):
        bits.append(f"{counts['FAILED']}F")
    if counts.get("ERROR"):
        bits.append(f"{counts['ERROR']}E")
    return " ".join(bits) or "pass"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shared", required=True, type=Path)
    ap.add_argument("--clean", required=True, type=Path)
    ap.add_argument("--slice", required=True, type=Path)
    ap.add_argument("--confound", type=Path, help="file list that differs between checkouts")
    ap.add_argument(
        "--hung",
        type=Path,
        help="files whose shard was killed by the timeout guard -- the file that hung",
    )
    ap.add_argument(
        "--unreached",
        type=Path,
        help="files never executed because an earlier file in their shard hung",
    )
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    shared, shared_totals, shared_why = parse(args.shared)
    clean, clean_totals, clean_why = parse(args.clean)

    slice_files = [
        ln.strip().replace("\\", "/")
        for ln in args.slice.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    confounded = set()
    if args.confound and args.confound.exists():
        confounded = {
            ln.strip().replace("\\", "/")
            for ln in args.confound.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        }

    def _load(p: Path | None) -> set[str]:
        if not p or not p.exists():
            return set()
        return {
            ln.strip().replace("\\", "/")
            for ln in p.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        }

    hung, unreached = _load(args.hung), _load(args.unreached)

    rows = []
    tally: dict[str, int] = defaultdict(int)
    for f in slice_files:
        s, c = shared.get(f), clean.get(f)
        s_bad = sum(s.values()) if s else 0
        c_bad = sum(c.values()) if c else 0

        if f in hung:
            # Hung in both checkouts: the timeout guard killed the shard, so pytest
            # never emitted a FAILED line. A cold-start hang is a real defect.
            tally["real"] += 1
            rows.append((f, "HANG", "HANG", "real", "wedged in pathlib.open; killed by --timeout=45"))
            continue
        if f in unreached:
            tally["unknown"] += 1
            rows.append((f, "not run", "not run", "unknown", "shard aborted by the hang above"))
            continue

        cls = classify(s_bad, c_bad, f in confounded)
        tally[cls] += 1
        if cls == "clean":
            continue  # green in both -- excluded from the table, counted in the summary
        rows.append((f, fmt(s), fmt(c), cls, clean_why.get(f) or shared_why.get(f) or ""))

    order = {"real": 0, "ambient": 1, "shared": 2, "unknown": 3}
    rows.sort(key=lambda r: (order.get(r[3], 9), -_bad(r[1]), r[0]))

    out = []
    out.append("| test_file | shared_checkout_result | clean_worktree_result | classification | first failure reason |")
    out.append("|---|---|---|---|---|")
    for f, s, c, cls, why in rows:
        why = why.replace("|", "\\|")
        out.append(f"| `{f}` | {s} | {c} | **{cls}** | {why} |")

    out.append("")
    out.append("### Baseline counts (before)")
    out.append("")
    out.append("| metric | shared checkout | clean worktree |")
    out.append("|---|---|---|")
    for label in ("passed", "failed", "error", "skipped"):
        out.append(
            f"| {label} | {shared_totals.get(label, 0)} | {clean_totals.get(label, 0)} |"
        )
    out.append(f"| files in slice | {len(slice_files)} | {len(slice_files)} |")
    out.append("")
    out.append("| classification | files |")
    out.append("|---|---|")
    for k in ("real", "ambient", "shared", "unknown", "clean"):
        out.append(f"| {k} | {tally.get(k, 0)} |")

    args.out.write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))
    return 0


def _bad(cell: str) -> int:
    return sum(int(n) for n in re.findall(r"(\d+)[FE]", cell))


if __name__ == "__main__":
    raise SystemExit(main())
