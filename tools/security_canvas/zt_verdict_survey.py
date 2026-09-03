#!/usr/bin/env python3
# CUI // SP-CTI
"""rmf-zt-01 — how many recorded ZT device checks were a PASS nothing measured?

``device_compliance_scanner.scan_device()`` evaluated every CIS/STIG check as
``bool(ctx.get(check_id, True))``: an absent probe reads as a PASS. Before that
default is replaced with a three-state ``pass | fail | unknown`` verdict, this
survey measures how much of the recorded corpus the change moves — the FLIP
COUNT — so "the numbers dropped" is a prediction made in advance rather than a
surprise discovered on a demo.

TWO INDEPENDENT FACTS, joined here and nowhere else:

  1. THE RECORDED CORPUS. Every row in ``zig_device_compliance_scans``, split by
     the verdict it recorded. Read from the canvas database, never re-derived by
     calling the scanner — a survey that asked the code under survey what it
     would say would prove only that the function is deterministic.

  2. THE CALL-SITE CENSUS. Whether any caller of ``scan_device`` anywhere in the
     tree supplies a ``context`` at all, found with ``ast``. This is what turns
     "the default fired" from an assumption into a measurement: if NO call site
     passes probe data, then every ctx-driven row in the corpus was the
     optimistic default and nothing else.

THREE BUCKETS, and the middle one is the honest one:

  flips_to_unknown        a ctx-driven check recorded PASS while no caller
                          supplied probe data. THE FLIP COUNT.
  undetermined_derived    a DERIVED check (``cc-07-continuous-mon`` reads
                          ``trust.last_seen_seconds_ago``; ``stig-antivirus``
                          reads ``trust.trusted``). Whether the posture behind
                          it was measured is NOT recorded on the row, so this
                          survey cannot say — and says so, rather than guessing.
                          The live posture is re-derived beside it under
                          ``live_posture`` so a reader can see what the same
                          check reports on this deployment today.
  unchanged               a recorded FAIL, or a PASS a caller actually probed.
                          These do not move.

UNMEASURABLE IS NEVER A CLEAN ZERO. The scan tables are created lazily by the
scanner itself, so on a database the scanner has never run against they are
ABSENT — a different fact from "present and empty" (the writer ran and recorded
nothing), and both are different from "zero flips".

THE CORPUS IS REACHED THROUGH THE CANVAS SEAM ONLY
(``tools.security_canvas.db.init_db.get_connection``), never a direct
``sqlite3.connect``. Which database that is comes from
``SC_STORAGE_BACKEND``, read by that module AT IMPORT TIME — so surveying the
SQLite canvas corpus means setting it in the environment before the process
starts (``SC_STORAGE_BACKEND=sqlite python -m
tools.security_canvas.zt_verdict_survey``), never a flag this module flips
after the import has already happened. The survey and the scanner therefore
cannot read two different databases by two different rules.

Report only, deliberately no ``--gate`` (kpr-fix-03): it measures a recorded
CORPUS, not a diff, so a gate would fail commits for a condition the committer
did not cause. Exit 2 = the survey could not be produced, which is never the
same as a clean survey.

NIST 800-53: CA-7, CM-6, SI-4
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from pathlib import Path
from typing import Any

from icdev.core.paths import repo_root

BASE_DIR = repo_root(__file__)

try:
    from tools.security_canvas.device_compliance_scanner import (
        CIS_CONTROL_CHECKS,
        DERIVED_CHECKS,
        STIG_CHECKS,
    )
except ImportError:  # pragma: no cover - the scanner is a hard dependency
    CIS_CONTROL_CHECKS, STIG_CHECKS, DERIVED_CHECKS = {}, {}, frozenset()


# ---------------------------------------------------------------------------
# 2. Call-site census — does ANY caller supply probe data?
# ---------------------------------------------------------------------------

#: Directories scanned for callers. The icdev/ mirror is deliberately included:
#: a probe supplied only there would still be a probe supplied.
_CALLER_ROOTS = ("tools", "icdev/tools")


def _context_kind(value) -> str:
    """``supplies`` | ``conditional`` | ``no_probe`` for a ``context`` argument."""
    if value is None:
        return "no_probe"
    if isinstance(value, ast.Constant) and value.value is None:
        return "no_probe"
    # `probes.get(h)` / `probes.get(h, None)` — forwarded, and None when the
    # caller has nothing. A parameter passed is not probe data supplied.
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute):
        if value.func.attr == "get":
            return "conditional"
    if isinstance(value, ast.BoolOp):
        return "conditional"
    return "supplies"


def _scan_device_calls(root: Path) -> list[dict[str, Any]]:
    """Every literal call to ``scan_device`` under *root*, with whether it probes.

    THREE KINDS, because "passes a context parameter" and "supplies probe data"
    are not the same claim:

      supplies      an UNCONDITIONAL context — a literal, a name, a call that
                    is not an optional lookup. This caller has probe data.
      conditional   ``context=probes.get(h)`` and friends: the parameter is
                    forwarded, and it is ``None`` whenever the caller has
                    nothing. ``run_fleet_scan`` is exactly this. Counting it as
                    a probe would report an uninstrumented fleet as an
                    instrumented one — the defect this survey exists to measure.
      no_probe      no context argument at all, or an explicit ``None``.

    ``**kwargs`` counts as UNDETERMINABLE rather than as a probe: a survey that
    assumed the permissive reading of its own evidence would be committing the
    defect it was written to measure.
    """
    calls: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.py")):
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "scan_device" not in src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name != "scan_device":
                continue
            value = node.args[2] if len(node.args) >= 3 else next(
                (kw.value for kw in node.keywords if kw.arg == "context"), None
            )
            kind = _context_kind(value)
            undeterminable = any(kw.arg is None for kw in node.keywords)
            calls.append(
                {
                    "file": str(path.relative_to(BASE_DIR)).replace("\\", "/"),
                    "line": node.lineno,
                    "context": kind,
                    "supplies_context": kind == "supplies",
                    "context_undeterminable": bool(undeterminable and kind == "no_probe"),
                }
            )
    return calls


def call_site_census(base_dir: Path | None = None) -> dict[str, Any]:
    """``{callers, supplying_context, undeterminable, sites}`` for ``scan_device``."""
    base = base_dir or BASE_DIR
    sites: list[dict[str, Any]] = []
    for rel in _CALLER_ROOTS:
        root = base / rel
        if root.is_dir():
            sites.extend(_scan_device_calls(root))
    return {
        "callers": len(sites),
        "supplying_context": sum(1 for s in sites if s["context"] == "supplies"),
        "conditional_context": sum(1 for s in sites if s["context"] == "conditional"),
        "no_probe": sum(1 for s in sites if s["context"] == "no_probe"),
        "undeterminable": sum(1 for s in sites if s["context_undeterminable"]),
        "sites": sites,
    }


# ---------------------------------------------------------------------------
# 1. The recorded corpus
# ---------------------------------------------------------------------------

_SCAN_TABLE = "zig_device_compliance_scans"


def read_corpus(conn=None) -> dict[str, Any]:
    """Recorded check rows, or WHY there are none.

    ``state``: ``absent`` (no such table — the scanner has never run here) |
    ``empty`` (the table exists and holds nothing) | ``rows`` | ``unreadable``.
    Merging the first two would report a scanner that has never run and a
    scanner that recorded nothing as the same fact; they send you to different
    fixes.

    A caller-supplied ``conn`` is NOT closed here — the caller owns it.
    """
    backend = os.environ.get(
        "SC_STORAGE_BACKEND",
        os.environ.get(
            "ICDEV_CANVAS_STORAGE_BACKEND",
            os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql"),
        ),
    ).lower()
    owned = conn is None
    if owned:
        try:
            from tools.security_canvas.db.init_db import get_connection

            conn = get_connection()
        except Exception as exc:  # noqa: BLE001 - an unreachable DB is a result
            return {
                "state": "unreadable",
                "backend": backend,
                "error": str(exc),
                "rows": [],
            }
    try:
        rows = conn.execute(
            "SELECT check_id, scan_type, passed, device_id FROM " + _SCAN_TABLE  # nosec B608 - fixed literal
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 - an absent table is a survey result
        return {
            "state": "absent",
            "backend": backend,
            "error": str(exc).split("\n")[0],
            "rows": [],
        }
    finally:
        if owned:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    parsed = [dict(r) for r in rows]
    return {
        "state": "rows" if parsed else "empty",
        "backend": backend,
        "error": None,
        "rows": parsed,
    }


# ---------------------------------------------------------------------------
# Live posture — what the derived checks read on THIS deployment right now
# ---------------------------------------------------------------------------


def live_posture() -> dict[str, Any]:
    """The device-trust status this deployment returns, and the stub-gate state.

    Reported beside ``undetermined_derived`` because a recorded row cannot say
    whether the posture behind it was measured, and the current answer is the
    closest honest thing available.
    """
    out: dict[str, Any] = {"status": None, "measured": None, "stub_allowed": None}
    try:
        from tools.security.stub_gate import stub_allowed

        out["stub_allowed"] = stub_allowed()
    except Exception as exc:  # noqa: BLE001
        out["stub_error"] = str(exc)
    try:
        from tools.security.device_trust import verify_device_posture

        trust = verify_device_posture("rmf-zt-01-survey-probe")
        status = getattr(trust, "status", "") or ""
        out["status"] = status or "not_evaluated"
        out["measured"] = status in ("healthy", "unhealthy")
        out["reason"] = getattr(trust, "reason", "")
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    return out


# ---------------------------------------------------------------------------
# The survey
# ---------------------------------------------------------------------------


def survey(conn=None, base_dir: Path | None = None) -> dict[str, Any]:
    """The flip count, its denominator, and what could not be attributed."""
    census = call_site_census(base_dir)
    corpus = read_corpus(conn)

    result: dict[str, Any] = {
        "task": "rmf-zt-01",
        "corpus_state": corpus["state"],
        "backend": corpus["backend"],
        "call_sites": census,
        "live_posture": live_posture(),
    }

    if corpus["state"] != "rows":
        result.update(
            {
                "measurable": False,
                "reason": {
                    "absent": "the scan table does not exist here — the scanner has "
                    "never run against this database",
                    "empty": "the scan table exists and holds no rows",
                    "unreadable": "the canvas database could not be opened",
                }.get(corpus["state"], corpus["state"]),
                "error": corpus["error"],
                "recorded_checks": None,
                "flips_to_unknown": None,
                "flip_rate_pct": None,
            }
        )
        return result

    rows = corpus["rows"]
    # No caller supplies probe data => every ctx-driven row WAS the default. If
    # some caller does, individual rows cannot be attributed and the survey says
    # so rather than assuming the flattering reading.
    probes_supplied = census["supplying_context"] > 0 or census["undeterminable"] > 0

    flips = 0
    derived = 0
    unchanged = 0
    unattributable = 0
    recorded_pass = 0
    recorded_fail = 0
    per_check: dict[str, dict[str, int]] = {}

    for row in rows:
        check_id = row.get("check_id") or ""
        passed = bool(row.get("passed"))
        bucket = per_check.setdefault(
            check_id, {"rows": 0, "passed": 0, "flips": 0, "derived": 0}
        )
        bucket["rows"] += 1
        if passed:
            recorded_pass += 1
            bucket["passed"] += 1
        else:
            recorded_fail += 1

        if check_id in DERIVED_CHECKS:
            bucket["derived"] += 1
            derived += 1
            continue
        if not passed:
            unchanged += 1
            continue
        if probes_supplied:
            unattributable += 1
            continue
        bucket["flips"] += 1
        flips += 1

    devices = {r.get("device_id") for r in rows}
    # None, never 0.0, over an empty denominator (args/perfect_score_gate.yaml).
    flip_rate = round(100.0 * flips / recorded_pass, 2) if recorded_pass else None

    result.update(
        {
            "measurable": True,
            "reason": None,
            "recorded_checks": len(rows),
            "recorded_devices": len(devices),
            "recorded_pass": recorded_pass,
            "recorded_fail": recorded_fail,
            "flips_to_unknown": flips,
            "flip_rate_pct": flip_rate,
            "undetermined_derived": derived,
            "unattributable_pass": unattributable,
            "unchanged": unchanged,
            "per_check": per_check,
            "derived_checks": sorted(DERIVED_CHECKS),
            "catalog_size": len(CIS_CONTROL_CHECKS) + len(STIG_CHECKS),
            # The census describes the CURRENT tree; the corpus was written by
            # whatever tree was live at the time. Stated rather than assumed —
            # a caller that once supplied probes and no longer does would make
            # this an over-count, and nobody could tell from the numbers alone.
            "attribution_basis": (
                "no caller supplies unconditional probe data in the current tree"
                if not probes_supplied
                else "at least one caller supplies probe data — individual rows "
                "cannot be attributed and are counted as unattributable_pass"
            ),
            "notes": [
                "flip_rate_pct is the share of recorded PASSES with no probe "
                "behind them; it is None, never 0.0, over an empty denominator.",
                "undetermined_derived is NEITHER a flip nor a non-flip — the row "
                "does not record whether the posture behind it was measured.",
            ],
        }
    )
    return result


def _render(report: dict[str, Any]) -> str:
    lines = ["ZT check verdict flip survey (rmf-zt-01)", "=" * 44]
    lines.append("corpus             : %s (%s)" % (report["corpus_state"], report["backend"]))
    cs = report["call_sites"]
    lines.append(
        "scan_device callers: %d (%d supply probe data, %d forward optionally, "
        "%d none, %d undeterminable)"
        % (
            cs["callers"],
            cs["supplying_context"],
            cs["conditional_context"],
            cs["no_probe"],
            cs["undeterminable"],
        )
    )
    lp = report["live_posture"]
    lines.append(
        "live posture       : %s (measured=%s, stub_allowed=%s)"
        % (lp.get("status"), lp.get("measured"), lp.get("stub_allowed"))
    )
    if not report.get("measurable"):
        lines += [
            "",
            "UNMEASURABLE: %s" % report.get("reason"),
            "This is NOT a clean bill of health — nothing was measured.",
        ]
        return "\n".join(lines)
    lines += [
        "",
        "recorded checks    : %d over %d device(s)"
        % (report["recorded_checks"], report["recorded_devices"]),
        "  recorded pass    : %d" % report["recorded_pass"],
        "  recorded fail    : %d" % report["recorded_fail"],
        "",
        "FLIPS TO UNKNOWN   : %d (%s%% of recorded passes)"
        % (report["flips_to_unknown"], report["flip_rate_pct"]),
        "undetermined       : %d (derived checks)" % report["undetermined_derived"],
        "unattributable     : %d" % report["unattributable_pass"],
        "unchanged          : %d" % report["unchanged"],
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)
    try:
        report = survey()
    except Exception as exc:  # noqa: BLE001 - exit 2 means "could not survey"
        print("survey could not be produced: %s" % exc, file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, default=str) if args.json else _render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
