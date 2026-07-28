# CUI // SP-CTI
"""Deterministic sensor over the existing review loop (clx-sense-01).

A control loop needs a *sensor*: something that measures deviation from a
desired state and returns it as structured data a controller can rank, rather
than prose a model has to interpret. The source analysis asked for a new
``code_sensor`` tool built on ast-grep.

It is not needed. ``tools/quality/review_loop.py`` already **is** the sensor —
it runs ruff, the coherence checker and SIPA over a diff, applies deterministic
autofixes, and emits the remainder as ``Finding`` records. This module is a thin
adapter that exposes that existing machinery through a sensor-shaped interface,
so nothing acquires a second scanner or a new parsing dependency.

What this adds over calling ``ReviewLoop`` directly:

* a stable :class:`Violation` shape (file, line, rule, severity, fixable) that a
  controller can sort on, independent of the gate that produced it;
* ``severity``, which ``Finding`` does not carry — a blocking gate's findings
  outrank an advisory gate's;
* a ``measure()`` entry point that reports rather than repairs. The sensor
  never autofixes: a sensor that changes the system it is measuring cannot be
  used to decide what to change.

Usage::

    from tools.quality.sensor import ReviewLoopSensor

    violations = ReviewLoopSensor().measure(base="origin/main")
    worst = sorted(violations, key=lambda v: v.rank)[0]

CLI::

    python tools/quality/sensor.py --json
    python tools/quality/sensor.py --base origin/main --json
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import sys
from typing import Any, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.quality.sensor")

#: Gates whose findings block a merge. Everything else is advisory.
#: Mirrors review_loop's own blocking/non-blocking split rather than inventing
#: a second opinion about which gate matters.
_SEVERITY_BY_BLOCKING = {True: "blocking", False: "advisory"}

_SEVERITY_RANK = {"blocking": 0, "advisory": 1}

#: Suffixes that mark a Finding.file as an actual source location.
_PATH_SUFFIXES = (".py", ".md", ".yaml", ".yml", ".json", ".sql", ".html", ".js", ".ts")


def _looks_like_path(value: str) -> bool:
    """True when ``value`` is a source location rather than a check's name."""
    if not value:
        return False
    return value.endswith(_PATH_SUFFIXES) or "/" in value or "\\" in value


@dataclasses.dataclass(frozen=True)
class Violation:
    """One measured deviation from the desired state."""

    file: str
    line: int
    rule: str
    message: str
    gate: str
    severity: str = "advisory"
    fixable: bool = False
    #: False when ``file`` names a repo-wide check rather than a path. The
    #: coherence gate reports findings that belong to the tree, not to one file
    #: (``Finding.file`` carries the check's display name there), and a
    #: controller that assumed every violation had a location would mis-rank
    #: them.
    file_scoped: bool = True

    @property
    def rank(self) -> tuple:
        """Sort key: blocking first, then located findings, then file/line."""
        return (
            _SEVERITY_RANK.get(self.severity, 9),
            0 if self.file_scoped else 1,
            self.file,
            self.line,
            self.rule,
        )

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


class ReviewLoopSensor:
    """Measure outstanding violations on the working tree, without changing it."""

    def __init__(
        self,
        *,
        repo_root: Optional[pathlib.Path] = None,
        config: Optional[dict] = None,
        staged: bool = False,
    ):
        self.repo_root = repo_root
        self.config = config
        self.staged = staged

    def measure(self, base: Optional[str] = None) -> list[Violation]:
        """Return the violations currently present, most severe first.

        Runs the review loop with autofix DISABLED and a single iteration: a
        sensor reports the state of the system, it does not repair it. Letting
        it autofix would mean the controller could never see what the loop had
        silently changed underneath it.
        """
        from tools.quality.review_loop import ReviewLoop

        cfg = dict(self.config or {})
        cfg["max_iterations"] = 1

        loop = ReviewLoop(
            config=cfg or None,
            repo_root=self.repo_root,
            autofix=False,
            staged=self.staged,
        )
        report = loop.run(base=base)

        violations: list[Violation] = []
        for iteration in report.iterations:
            for gate in iteration.gates:
                if gate.skipped:
                    continue
                severity = _SEVERITY_BY_BLOCKING.get(bool(gate.blocking), "advisory")
                for f in gate.findings:
                    violations.append(
                        Violation(
                            file=f.file,
                            line=int(f.line or 0),
                            rule=f.code or f.gate or gate.name,
                            message=f.message,
                            # Prefer the finding's own label: GateResult.name is
                            # supplied by the caller and is not always the gate's
                            # canonical id.
                            gate=f.gate or gate.name,
                            severity=severity,
                            fixable=bool(f.fixable),
                            file_scoped=_looks_like_path(f.file),
                        )
                    )

        violations.sort(key=lambda v: v.rank)
        logger.info(
            "sensor: measured %d violation(s) (%d blocking)",
            len(violations),
            sum(1 for v in violations if v.severity == "blocking"),
        )
        return violations


def measure(base: Optional[str] = None, **kwargs: Any) -> list[Violation]:
    """Module-level convenience wrapper around :class:`ReviewLoopSensor`."""
    return ReviewLoopSensor(**kwargs).measure(base=base)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Deterministic violation sensor")
    ap.add_argument("--base", help="Git ref to diff against (default: review_loop's own resolution)")
    ap.add_argument("--staged", action="store_true", help="Measure staged changes only")
    ap.add_argument("--json", dest="as_json", action="store_true")
    args = ap.parse_args(argv)

    violations = ReviewLoopSensor(staged=args.staged).measure(base=args.base)

    if args.as_json:
        print(json.dumps(
            {
                "count": len(violations),
                "blocking": sum(1 for v in violations if v.severity == "blocking"),
                "violations": [v.to_dict() for v in violations],
            },
            indent=2,
        ))
    else:
        if not violations:
            print("no violations measured")
        for v in violations:
            print(f"  [{v.severity}] {v.file}:{v.line} {v.rule} ({v.gate}) — {v.message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
