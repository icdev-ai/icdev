# CUI // SP-CTI
"""Calibration report — does confidence predict correctness?

TRUST gates on confidence. Nothing had ever checked whether confidence predicts
anything, and the machinery built to check it (compute_metrics, check_gates, the
ece_max gate) reads ``harness_eval``, which holds ONE row. The gate has been
green because it has been empty.

So this report goes and reads the evidence where it actually is, and is explicit
about which kind of evidence each number came from:

**Organic (BIASED).** ``oracle_predictions.outcome`` carries ``promoted:<task>``,
which joins to ``kanban_verifications.result``. That join is the only real
ground truth in the system, and nobody had run it. It is biased by construction:
a prediction only appears here if the system *chose* to promote it, so it can
tell you the precision of what got through and nothing about what did not.

**Sampled (UNBIASED).** ``harness_eval`` rows written by confidence_sampler under
``sampled:<surface>``. Drawn at random, so they carry no such bias.

These are never pooled. Averaging an unbiased sample into a biased one inherits
the bias while looking like more evidence.

**Report per RULE, not per band.** Confidence here is not computed per
prediction — it is a per-rule constant copied out of awareness_config.yaml, so a
band is mostly one rule wearing a number. On the live corpus the 0.9 band reads
"20 labels, observed 0.90" and is 19 rows of tool_not_in_manifest plus 1 row of
route_not_listed. The band's number is one rule's number with a rounding error
attached. A rule that is wrong is fixable; a band is an arbitrary set union.

Reporting only. It never moves a threshold: a gate that retunes itself from a
small, biased sample can silently widen, and every band here is small.

    python -m tools.genesis.harness.calibration_report --json
"""
from __future__ import annotations

import json
from typing import Any

from tools.genesis.harness.eval_harness import (
    MIN_CALIBRATION_SAMPLES,
    NON_EVIDENCE_OUTCOMES,
    calibration_by_rule,
)
from tools.genesis.harness.eval_harness import SUCCESS_OUTCOMES as HARNESS_SUCCESS
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# kanban_verifications speaks passed/failed/bypassed/phantom, not the harness's
# own "resolved". Hardcoding one vocabulary scored every other surface 0%
# accurate — not wrong, just spelled differently. This is why the engine takes
# the vocabulary as a parameter.
VERIFICATION_SUCCESS = frozenset({"passed"})

# ...and the sampled rows are read straight out of harness_eval, which speaks the
# harness vocabulary (resolved/false_positive/...), not kanban_verifications'
# passed/failed. Scoring them against VERIFICATION_SUCCESS meant a human who
# correctly labelled a drawn sample produced a row counted as a miscalibration —
# the same "spelled differently" bug the comment above warns about, one call site
# further down. HARNESS_SUCCESS (imported above) is the vocabulary for that path.

# 'bypassed' means verification was skipped. It is NOT a failure, and counting it
# as one manufactures miscalibration out of missing data -- the exact mistake made
# while first probing this, which reported every band as 0.00 accurate.
# NON_EVIDENCE_OUTCOMES already carries it; named here so the intent is greppable.
_ = NON_EVIDENCE_OUTCOMES

SAMPLE_REFLEX_PREFIX = "sampled:"


def _rows_organic(conn) -> list[dict]:
    """Promoted predictions joined to their verification verdict.

    substring(...) extracts the task id the promotion recorded. Rows never
    promoted have no task and simply do not join -- which is the bias, stated
    plainly rather than hidden.
    """
    cur = conn.execute(
        """
        SELECT p.prediction_type AS decision,
               p.confidence      AS confidence,
               v.result          AS actual_outcome
          FROM oracle_predictions p
          JOIN kanban_verifications v
            ON v.task_id = substring(p.outcome from 'promoted:(task-.*)$')
         WHERE p.outcome LIKE 'promoted:task-%%'
        """
    )
    return [dict(r) for r in cur.fetchall()]


def _rows_sampled(conn) -> list[dict]:
    """Rows the confidence sampler drew at random. Unbiased, and today empty."""
    cur = conn.execute(
        """
        SELECT decision, confidence, actual_outcome
          FROM harness_eval
         WHERE reflex LIKE %s
        """,
        (f"{SAMPLE_REFLEX_PREFIX}%",),
    )
    return [dict(r) for r in cur.fetchall()]


def _harness_eval_size(conn) -> dict[str, int]:
    cur = conn.execute(
        "SELECT count(*) AS total, count(actual_outcome) AS labelled FROM harness_eval"
    )
    return dict(cur.fetchone())


def build_report(conn) -> dict[str, Any]:
    organic = _rows_organic(conn)
    sampled = _rows_sampled(conn)
    return {
        "harness_eval": _harness_eval_size(conn),
        "organic": {
            "provenance": "BIASED -- only predictions the system chose to promote",
            "joined": len(organic),
            "rules": calibration_by_rule(organic, success_outcomes=VERIFICATION_SUCCESS),
        },
        "sampled": {
            "provenance": "UNBIASED -- drawn at random by confidence_sampler",
            "joined": len(sampled),
            "rules": calibration_by_rule(sampled, success_outcomes=HARNESS_SUCCESS),
        },
        "min_samples": MIN_CALIBRATION_SAMPLES,
    }


def _render_rules(groups: list[dict]) -> list[str]:
    if not groups:
        return ["    (no evidence)"]
    lines = []
    for g in groups:
        rule = str(g.get("rule") or "?")
        claimed = g["claimed"]
        if not g["measured"]:
            # Never a point estimate below the floor. "0.33" off 3 samples is
            # noise wearing a decimal point, and it would be self-defeating to
            # report unearned certainty inside the report about unearned
            # certainty.
            why = f"{g['labelled']} label(s)" if g["labelled"] else "no labels"
            lines.append(
                f"    {rule:<38} claimed {claimed:<5} "
                f"UNMEASURED ({why}, {g['non_evidence']} unverified)"
            )
            continue
        ci = f"[{g['ci_low']:.2f}-{g['ci_high']:.2f}]"
        gap = g["gap"]
        verdict = "claims more than it delivers" if gap is not None and gap > 0.1 else "consistent"
        lines.append(
            f"    {rule:<38} claimed {claimed:<5} observed {g['observed_accuracy']:.2f} {ci:<14}"
            f" n={g['labelled']:<3} -- {verdict}"
        )
        if len(g["claimed_values"]) > 1:
            # The rule's constant was edited and older rows kept the old value,
            # so "claimed" here is an average of two different claims.
            lines.append(f"        ! this rule has claimed {g['claimed_values']} -- mixed constants")
    return lines


def render(report: dict[str, Any]) -> str:
    he = report["harness_eval"]
    out = ["", "CALIBRATION -- does confidence predict correctness?", ""]

    if he["total"] < MIN_CALIBRATION_SAMPLES:
        out += [
            f"  harness_eval holds {he['total']} row(s) ({he['labelled']} labelled).",
            "  compute_metrics/check_gates read this table, so the ece gate has been",
            "  green because it is empty, not because the system is calibrated.",
            "",
        ]

    out += ["  ORGANIC -- " + report["organic"]["provenance"],
            f"  {report['organic']['joined']} promoted prediction(s) reached a verification verdict.",
            ""]
    out += _render_rules(report["organic"]["rules"])
    out += ["", "  SAMPLED -- " + report["sampled"]["provenance"],
            f"  {report['sampled']['joined']} sampled row(s).", ""]
    out += _render_rules(report["sampled"]["rules"])
    out += [
        "",
        "  Read per rule, not per band: confidence is a per-rule constant, so a band",
        "  is a set union of unrelated rules. Organic and sampled are never pooled --",
        "  averaging an unbiased sample into a biased one inherits the bias while",
        "  looking like more evidence.",
        "",
    ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Calibration report -- per rule, with provenance")
    p.add_argument("--json", dest="as_json", action="store_true")
    args = p.parse_args(argv)

    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        report = build_report(conn)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    print(json.dumps(report, indent=2, default=str) if args.as_json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
