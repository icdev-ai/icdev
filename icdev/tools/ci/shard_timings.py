#!/usr/bin/env python3
"""Fold CI JUnit artifacts into the per-file duration snapshot the shards pack by.

WHY THIS EXISTS (crx-test-07)
-----------------------------
crx-test-05 sharded the gated pytest run ROUND-ROBIN, which balances FILE COUNT
and says nothing about RUNTIME. Measured on the first merged sharded pipeline
(GitHub run 32352491214, 2026-08-20):

    Test Shard 1 of 4  17m01s   <- the whole `Test` check waits on this
    Test Shard 2 of 4   5m59s
    Test Shard 3 of 4   5m43s
    Test Shard 4 of 4   6m36s

`Test` cost 17 minutes to do ~7 minutes of work and three runners idled for ten
of them, because shard 1 drew the repo-wide scanners whose cost is superlinear
in tree size. Reproduced locally in the same direction, so it is the PARTITION
and not a runner.

The data to fix it did not exist when round-robin was written and does now: each
shard uploads `ci-junit-shard-<k>.xml`. This module turns those four artifacts
into `args/ci_test_timings/snapshot.json`, and
`gated_test_list.partition()` bin-packs against it.

WHY THE JUNIT XML AND NOT `--durations`
---------------------------------------
`--durations=25` prints the 25 slowest CALLS to the log. That is a debugging
aid, not a census: it is truncated by design, it is prose, and it splits one
test across setup/call/teardown lines. The JUnit XML carries EVERY testcase with
pytest's default `junit_duration_report=total`, so setup is included — which is
the whole point here, since the four worst offenders on shard 1 spent 82.6s,
33.3s, 32.5s and 26.8s in SETUP alone.

CLASSNAME -> FILE, RESOLVED AGAINST THE ALLOWLIST
-------------------------------------------------
pytest's JUnit XML has no `file` attribute — only a dotted `classname`
(`tests.cortex.test_x`, or `tests.cortex.test_x.TestSomething` for a class). The
dotted form is ambiguous on its own: `tests.a.b` could be `tests/a/b.py` or a
class `b` in `tests/a.py`. So it is resolved against the ALLOWLIST vocabulary —
the longest known target whose dotted form prefixes the classname wins. A
classname matching no gated target is counted in `unattributed` and dropped,
never guessed at, because a wrong path in the snapshot silently mis-weights a
real file AND leaves the real one imputed.

USAGE
-----
    python tools/ci/shard_timings.py --from-junit .tmp/junit/*.xml --json
    python tools/ci/shard_timings.py --from-junit .tmp/junit/*.xml --write
    python tools/ci/shard_timings.py --show
    python tools/ci/shard_timings.py --balance --shards 4
    python tools/ci/shard_timings.py --balance --shards 4 --no-timings
"""
from __future__ import annotations

import argparse
import glob as globlib
import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# Script mode (`python tools/ci/shard_timings.py`) puts tools/ci on sys.path[0],
# not the repo root, so the package import below needs the root added first.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.ci.gated_test_list import (  # noqa: E402
    TIMING_DIR,
    AllowlistError,
    load_shard_pins,
    load_timings,
    partition,
    repo_root,
    resolve,
)

#: The scheduled refresh owns this name. A task correcting one file's weight
#: writes `<task-id>.json` beside it instead, so the two never collide — the same
#: per-task fragment discipline `core.d/` gave `core.txt`.
SNAPSHOT_NAME = "snapshot.json"


def dotted(path: str) -> str:
    """`tests/cortex/test_x.py` -> `tests.cortex.test_x`."""
    stem = path[:-3] if path.endswith(".py") else path.rstrip("/")
    return stem.replace("\\", "/").replace("/", ".")


def parse_junit(text: str) -> Dict[str, float]:
    """`{classname: seconds}` summed over every testcase in one report.

    A `time` that is absent or unparseable contributes 0 rather than aborting the
    parse: one malformed testcase must not discard a whole shard's measurement.
    """
    out: Dict[str, float] = {}
    root = ET.fromstring(text)
    for case in root.iter("testcase"):
        classname = case.get("classname") or ""
        if not classname:
            continue
        try:
            seconds = float(case.get("time") or 0.0)
        except (TypeError, ValueError):
            seconds = 0.0
        out[classname] = out.get(classname, 0.0) + seconds
    return out


def attribute(
    class_times: Dict[str, float],
    targets: Sequence[str],
) -> Tuple[Dict[str, float], Dict[str, float]]:
    """`(per-file seconds, unattributed classname seconds)`.

    LONGEST dotted target prefix wins, so `tests/a/b.py::TestC` is attributed to
    `tests/a/b.py` and never to a hypothetical `tests/a.py`. Only gated targets
    are candidates — see the module docstring on why an unmatched classname is
    counted rather than guessed.
    """
    by_dotted = {dotted(t): t for t in targets if t.endswith(".py")}
    files: Dict[str, float] = {}
    unattributed: Dict[str, float] = {}
    for classname, seconds in class_times.items():
        parts = classname.split(".")
        hit: Optional[str] = None
        for n in range(len(parts), 0, -1):
            candidate = ".".join(parts[:n])
            if candidate in by_dotted:
                hit = by_dotted[candidate]
                break
        if hit is None:
            unattributed[classname] = unattributed.get(classname, 0.0) + seconds
        else:
            files[hit] = files.get(hit, 0.0) + seconds
    return files, unattributed


def build_snapshot(
    junit_paths: Sequence[Path],
    targets: Sequence[str],
    generated_at: str,
    source: str,
) -> Dict[str, object]:
    """The snapshot document, plus the provenance needed to argue with it.

    Durations are keyed and SORTED by path so a refresh produces a minimal,
    readable diff instead of a whole-file rewrite every week.
    """
    class_times: Dict[str, float] = {}
    read: List[str] = []
    unreadable: List[str] = []
    for path in junit_paths:
        try:
            part = parse_junit(Path(path).read_text(encoding="utf-8"))
        except (OSError, ET.ParseError) as exc:
            unreadable.append(f"{Path(path).name}: {exc}")
            continue
        read.append(Path(path).name)
        for classname, seconds in part.items():
            class_times[classname] = class_times.get(classname, 0.0) + seconds

    files, unattributed = attribute(class_times, targets)
    gated = set(targets)
    return {
        "generated_at": generated_at,
        "source": source,
        "unit": "seconds",
        "note": (
            "Per-FILE pytest duration (setup+call+teardown), summed from the "
            "ci-junit-shard-*.xml artifacts. Consumed by "
            "tools/ci/gated_test_list.py::partition. Refreshed by "
            ".github/workflows/shard-timings.yml — do not hand-edit; add "
            "args/ci_test_timings/<task-id>.json instead."
        ),
        "reports_read": read,
        "reports_unreadable": unreadable,
        "measured_files": len(files),
        "gated_targets": len(gated),
        # The two numbers that say whether this snapshot is worth trusting: a
        # gated target with no measurement gets the median imputed, and a
        # classname attributed to nothing gated is time this snapshot cannot
        # explain. Both are recorded rather than rounded away.
        "gated_unmeasured": sorted(gated - set(files)),
        "unattributed_seconds": round(sum(unattributed.values()), 2),
        "unattributed_classnames": len(unattributed),
        "total_seconds": round(sum(files.values()), 2),
        "durations": {k: round(v, 3) for k, v in sorted(files.items())},
    }


def snapshot_path(root: Optional[Path] = None, name: str = SNAPSHOT_NAME) -> Path:
    return (root or repo_root()) / TIMING_DIR / name


def write_snapshot(doc: Dict[str, object], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(doc, indent=2, sort_keys=False) + "\n",
        encoding="utf-8", newline="\n")
    return path


def balance(
    root: Optional[Path] = None,
    shards: int = 4,
    name: str = "core",
    use_timings: bool = True,
) -> Dict[str, object]:
    """What the partition WOULD do right now — the check that needs no CI run."""
    root = root or repo_root()
    entries = resolve(name, root)
    durations = load_timings(root)["durations"] if use_timings else {}
    parts, report = partition(
        entries, shards, load_shard_pins(root), durations)  # type: ignore[arg-type]
    report = dict(report)
    report["counts"] = [len(p) for p in parts]
    report["list"] = name
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", type=Path, help="repository root (default: from __file__)")
    parser.add_argument("--list", dest="name", default="core",
                        help="allowlist whose targets form the attribution vocabulary")
    parser.add_argument("--from-junit", nargs="+", metavar="PATH",
                        help="JUnit XML reports (globs allowed) to fold into a snapshot")
    parser.add_argument("--write", action="store_true",
                        help="write the snapshot to args/ci_test_timings/")
    parser.add_argument("--out", type=Path, help="write to this path instead")
    parser.add_argument("--name", dest="out_name", default=SNAPSHOT_NAME,
                        help=f"snapshot filename under {TIMING_DIR.as_posix()}/ "
                             f"(default: {SNAPSHOT_NAME})")
    parser.add_argument("--source", default="",
                        help="provenance label, e.g. github-run-32352491214")
    parser.add_argument("--generated-at",
                        help="ISO-8601 stamp (default: now, UTC). Set it explicitly "
                             "to make a rebuild byte-reproducible.")
    parser.add_argument("--show", action="store_true",
                        help="print what the loader currently merges, with provenance")
    parser.add_argument("--balance", action="store_true",
                        help="print the partition this snapshot would produce")
    parser.add_argument("--shards", type=int, default=4, help="with --balance")
    parser.add_argument("--no-timings", action="store_true",
                        help="with --balance, show the round-robin baseline instead")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    try:
        root = args.root.resolve() if args.root else repo_root()
    except AllowlistError as exc:
        print(f"::error::shard timings: {exc}", file=sys.stderr)
        return 1

    if args.show:
        loaded = load_timings(root)
        if args.json:
            print(json.dumps(loaded, indent=2))
        else:
            durations = loaded["durations"]
            print(f"{len(durations)} measured files from "  # type: ignore[arg-type]
                  f"{len(loaded['sources'])} snapshot(s): "  # type: ignore[arg-type]
                  f"{', '.join(loaded['sources']) or 'none'}")  # type: ignore[arg-type]
            for warning in loaded["warnings"]:  # type: ignore[union-attr]
                print(f"::warning::{warning}", file=sys.stderr)
        return 0

    if args.balance:
        try:
            report = balance(root, args.shards, args.name, not args.no_timings)
        except (AllowlistError, ValueError) as exc:
            print(f"::error::shard timings: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"partition of '{report['list']}' into {report['shards']} shards "
                  f"by {report['method']}: "
                  f"{report['measured_entries']}/{report['entries']} measured, "
                  f"{report['imputed_units']} units imputed")
            for i, count in enumerate(report["counts"], start=1):  # type: ignore[arg-type]
                seconds = report["estimated_seconds"]
                est = f"{seconds[i - 1]:8.1f}s" if isinstance(seconds, list) else "       ?"
                print(f"  shard {i}: {count:4d} files  {est}")
            if report.get("estimated_spread_pct") is not None:
                print(f"  spread: {report['estimated_spread_pct']}% "
                      "(max-min over max)")
            if report.get("lower_bound_seconds") is not None:
                verdict = ("AT THE FLOOR — the critical path is ONE unit, so "
                           "raising the shard count buys nothing"
                           if report.get("at_lower_bound")
                           else "below the busiest shard — packing can still improve")
                print(f"  floor:  {report['lower_bound_seconds']}s "
                      f"({report['heaviest_unit']}) — {verdict}")
        return 0

    if not args.from_junit:
        parser.error("nothing to do — pass --from-junit, --show or --balance")

    paths: List[Path] = []
    for pattern in args.from_junit:
        # Expanded here as well as by the shell: the scheduled workflow passes a
        # glob that has no matches until `gh run download` has run, and a shell
        # that leaves an unmatched glob verbatim would hand us a literal `*`.
        matched = sorted(globlib.glob(pattern))
        paths.extend(Path(m) for m in matched) if matched else paths.append(Path(pattern))

    targets = resolve(args.name, root)
    generated_at = args.generated_at or datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    doc = build_snapshot(paths, targets, generated_at, args.source or "local")

    if not doc["durations"]:
        # A snapshot with no durations would REPLACE a good one with a document
        # that silently reverts every shard to round-robin. Refuse; the caller's
        # committed snapshot stays in place and the failure is named.
        print("::error::shard timings: no testcase durations could be attributed "
              f"to gated targets from {len(paths)} report(s) — refusing to write "
              "an empty snapshot", file=sys.stderr)
        if args.json:
            print(json.dumps(doc, indent=2))
        return 1

    if args.write or args.out:
        out = args.out if args.out else snapshot_path(root, args.out_name)
        write_snapshot(doc, out)
        print(f"wrote {out} — {doc['measured_files']} files, "
              f"{doc['total_seconds']}s total, "
              f"{len(doc['gated_unmeasured'])} gated targets unmeasured")  # type: ignore[arg-type]

    if args.json:
        print(json.dumps(doc, indent=2))
    elif not (args.write or args.out):
        print(f"{doc['measured_files']} files, {doc['total_seconds']}s total, "
              f"{len(doc['gated_unmeasured'])} gated targets unmeasured, "  # type: ignore[arg-type]
              f"{doc['unattributed_classnames']} classnames unattributed "
              f"({doc['unattributed_seconds']}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
