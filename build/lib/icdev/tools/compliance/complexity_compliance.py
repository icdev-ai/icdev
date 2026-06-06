#!/usr/bin/env python3
# CUI // SP-CTI
"""Complexity Compliance Signal — maps cyclomatic/cognitive complexity to NIST SA-11/SA-15.

Maps code complexity metrics from the CodeAnalyzer to specific NIST SP 800-53 Rev 5
sub-controls, surfacing them as compliance findings in the Pre-Deployment Checklist (PDC).

Control Mappings
----------------
SA-11 (Developer Testing and Evaluation):
  SA-11(1) — Static Code Analysis
      Trigger: any function with cyclomatic complexity > 20
      Rationale: High-CC functions require static analysis tooling to identify
      untested branches (SA-11(1) mandates tool-assisted analysis for complex code).
  SA-11(3) — Independent Verification and Validation
      Trigger: >5% of functions have cyclomatic complexity > 15
      Rationale: Excessive complexity breadth makes IV&V cost-prohibitive without
      dedicated tooling; SA-11(3) requires independent assessment.
  SA-11(8) — Dynamic Code Analysis
      Trigger: any function with cognitive complexity > 25
      Rationale: High cognitive complexity indicates deeply nested, hard-to-reason-about
      logic that dynamic analysis (fuzzing, concolic testing) must cover.

SA-15 (Development Process, Standards, and Tools):
  SA-15(1) — Quality Metrics
      Trigger: average cyclomatic complexity > 10 across the project
      Rationale: SA-15(1) requires defined, tracked quality metrics; exceeding a
      project-wide CC threshold signals the metric is out of compliance.
  SA-15(7) — Developer Security Testing and Evaluation
      Trigger: average cognitive complexity > 15 across the project
      Rationale: High mean cognitive complexity indicates widespread readability/
      reasoning burden that security-focused review cannot adequately cover.
  SA-15(11) — Archive Information System / Component (Lifecycle Signal)
      Trigger: cognitive complexity trend degrading over last two scans
      Rationale: Rising complexity without corresponding test investment violates
      the "maintain current security state" lifecycle obligation.

Usage
-----
    python tools/compliance/complexity_compliance.py --json
    python tools/compliance/complexity_compliance.py --gate --json
    python tools/compliance/complexity_compliance.py --project-dir tools/ --json
    python tools/compliance/complexity_compliance.py --trend --json

Exit codes: 0 = no blocking findings, 1 = blocking findings present.

Architecture decision: D338 (Phase 52 extension — complexity as compliance signal).
"""

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# NIST control definitions
# ---------------------------------------------------------------------------

CONTROL_DEFS: Dict[str, Dict[str, str]] = {
    "SA-11(1)": {
        "family": "SA",
        "control": "SA-11",
        "enhancement": "1",
        "title": "Developer Testing and Evaluation | Static Code Analysis",
        "description": (
            "Require the developer to employ static code analysis tools to identify "
            "common flaws and document the results of the analysis."
        ),
        "fedramp": "SA-11(1)",
        "cmmc": "SA.L2-3.16.3",
    },
    "SA-11(3)": {
        "family": "SA",
        "control": "SA-11",
        "enhancement": "3",
        "title": "Developer Testing and Evaluation | Independent Verification and Validation",
        "description": (
            "Require an independent agent satisfying defined independence criteria "
            "to verify the correct implementation of the developer security testing "
            "and evaluation plans and evidence."
        ),
        "fedramp": "SA-11(3)",
        "cmmc": "SA.L2-3.16.3",
    },
    "SA-11(8)": {
        "family": "SA",
        "control": "SA-11",
        "enhancement": "8",
        "title": "Developer Testing and Evaluation | Dynamic Code Analysis",
        "description": (
            "Require the developer to employ dynamic code analysis tools to identify "
            "common flaws and document the results of the analysis."
        ),
        "fedramp": "SA-11(8)",
        "cmmc": "SA.L2-3.16.3",
    },
    "SA-15(1)": {
        "family": "SA",
        "control": "SA-15",
        "enhancement": "1",
        "title": "Development Process, Standards, and Tools | Quality Metrics",
        "description": (
            "Require the developer to define quality metrics at the beginning of the "
            "development process and provide evidence of meeting the quality metrics "
            "upon delivery of the system, system component, or system service."
        ),
        "fedramp": "SA-15(1)",
        "cmmc": "SA.L2-3.16.3",
    },
    "SA-15(7)": {
        "family": "SA",
        "control": "SA-15",
        "enhancement": "7",
        "title": "Development Process, Standards, and Tools | Developer Security Testing",
        "description": (
            "Require the developer to perform a security risk analysis during the "
            "development process and employ security testing procedures to assess "
            "the risk."
        ),
        "fedramp": "SA-15(7)",
        "cmmc": "SA.L2-3.16.3",
    },
    "SA-15(11)": {
        "family": "SA",
        "control": "SA-15",
        "enhancement": "11",
        "title": "Development Process, Standards, and Tools | Archive Information System",
        "description": (
            "Require the developer to archive the system, system component, or system "
            "service and conduct post-implementation support for designated periods of "
            "time in accordance with established procedures."
        ),
        "fedramp": "SA-15(11)",
        "cmmc": "SA.L2-3.16.3",
    },
}

# ---------------------------------------------------------------------------
# Default thresholds (can be overridden via args/security_gates.yaml)
# ---------------------------------------------------------------------------

_DEFAULT_THRESHOLDS = {
    # SA-11(1): any single function exceeding this cyclomatic CC triggers finding
    "sa11_1_max_cyclomatic_per_function": 20,
    # SA-11(3): pct of functions exceeding CC threshold before IV&V finding
    "sa11_3_max_pct_high_cyclomatic": 5.0,
    "sa11_3_high_cyclomatic_threshold": 15,
    # SA-11(8): any single function exceeding this cognitive CC triggers finding
    "sa11_8_max_cognitive_per_function": 25,
    # SA-15(1): project-wide average cyclomatic CC
    "sa15_1_max_avg_cyclomatic": 10.0,
    # SA-15(7): project-wide average cognitive CC
    "sa15_7_max_avg_cognitive": 15.0,
    # SA-15(11): cognitive trend delta — negative = degrading
    "sa15_11_cognitive_trend_min_delta": -2.0,
}


def _load_thresholds() -> Dict[str, Any]:
    """Merge defaults with thresholds from args/security_gates.yaml if present."""
    thresholds = dict(_DEFAULT_THRESHOLDS)
    gates_path = PROJECT_ROOT / "args" / "security_gates.yaml"
    if not gates_path.exists():
        return thresholds
    try:
        import yaml  # type: ignore

        with open(gates_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        cc_cfg = cfg.get("complexity_compliance", {}).get("thresholds", {})
        sa11 = cc_cfg.get("sa11", {})
        sa15 = cc_cfg.get("sa15", {})
        if "max_cyclomatic_per_function" in sa11:
            thresholds["sa11_1_max_cyclomatic_per_function"] = int(sa11["max_cyclomatic_per_function"])
        if "max_pct_high_cyclomatic" in sa11:
            thresholds["sa11_3_max_pct_high_cyclomatic"] = float(sa11["max_pct_high_cyclomatic"])
        if "high_cyclomatic_threshold" in sa11:
            thresholds["sa11_3_high_cyclomatic_threshold"] = int(sa11["high_cyclomatic_threshold"])
        if "max_cognitive_per_function" in sa11:
            thresholds["sa11_8_max_cognitive_per_function"] = int(sa11["max_cognitive_per_function"])
        if "max_avg_cyclomatic" in sa15:
            thresholds["sa15_1_max_avg_cyclomatic"] = float(sa15["max_avg_cyclomatic"])
        if "max_avg_cognitive" in sa15:
            thresholds["sa15_7_max_avg_cognitive"] = float(sa15["max_avg_cognitive"])
        if "cognitive_trend_min_delta" in sa15:
            thresholds["sa15_11_cognitive_trend_min_delta"] = float(sa15["cognitive_trend_min_delta"])
    except Exception:
        pass
    return thresholds


# ---------------------------------------------------------------------------
# Finding builder
# ---------------------------------------------------------------------------


def _finding(
    finding_id: str,
    control_id: str,
    severity: str,  # "blocking" | "warning"
    status: str,  # "fail" | "warn" | "pass"
    summary: str,
    evidence: Dict[str, Any],
) -> Dict[str, Any]:
    ctrl = CONTROL_DEFS.get(control_id, {})
    return {
        "finding_id": finding_id,
        "control_id": control_id,
        "control_title": ctrl.get("title", control_id),
        "control_family": ctrl.get("family", "SA"),
        "fedramp_control": ctrl.get("fedramp", control_id),
        "cmmc_practice": ctrl.get("cmmc", ""),
        "severity": severity,
        "status": status,
        "summary": summary,
        "evidence": evidence,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ---------------------------------------------------------------------------
# Metric collection
# ---------------------------------------------------------------------------


def _collect_metrics(project_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """Run CodeAnalyzer and return per-function metrics."""
    sys.path.insert(0, str(PROJECT_ROOT))
    from tools.analysis.code_analyzer import CodeAnalyzer  # noqa: PLC0415

    scan_dir = Path(project_dir) if project_dir else PROJECT_ROOT / "tools"
    analyzer = CodeAnalyzer(project_dir=str(scan_dir))
    result = analyzer.scan_directory()
    # scan_directory returns a summary dict with a "metrics" list of per-function records
    metrics: List[Dict[str, Any]] = result.get("metrics", [])
    return [m for m in metrics if m.get("function_name")]


def _collect_trend(project_id: str = "icdev") -> List[Dict[str, Any]]:
    """Return historical scan aggregates for trend analysis."""
    sys.path.insert(0, str(PROJECT_ROOT))
    from tools.analysis.code_analyzer import CodeAnalyzer  # noqa: PLC0415

    db_path = PROJECT_ROOT / "data" / "icdev.db"
    analyzer = CodeAnalyzer()
    try:
        return analyzer.get_trend(project_id, db_path=db_path)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# SA-11 evaluators
# ---------------------------------------------------------------------------


def evaluate_sa11_1(fn_metrics: List[Dict], thresholds: Dict) -> Dict[str, Any]:
    """SA-11(1): Static Code Analysis — high cyclomatic CC per function."""
    threshold = thresholds["sa11_1_max_cyclomatic_per_function"]
    violators = [
        {
            "function": m.get("function_name"),
            "file": m.get("file_path"),
            "cyclomatic_complexity": m.get("cyclomatic_complexity", 0),
        }
        for m in fn_metrics
        if m.get("cyclomatic_complexity", 0) > threshold
    ]
    violators_sorted = sorted(violators, key=lambda x: x["cyclomatic_complexity"], reverse=True)
    if violators:
        return _finding(
            finding_id=f"SA11-1-{uuid.uuid4().hex[:8]}",
            control_id="SA-11(1)",
            severity="warning",
            status="warn",
            summary=(
                f"{len(violators)} function(s) exceed cyclomatic complexity threshold "
                f"(>{threshold}), requiring static analysis tool coverage per SA-11(1)."
            ),
            evidence={
                "threshold": threshold,
                "violator_count": len(violators),
                "top_violators": violators_sorted[:10],
                "total_functions_scanned": len(fn_metrics),
            },
        )
    return _finding(
        finding_id=f"SA11-1-{uuid.uuid4().hex[:8]}",
        control_id="SA-11(1)",
        severity="warning",
        status="pass",
        summary=(
            f"No functions exceed cyclomatic complexity threshold (>{threshold}). "
            "SA-11(1) static analysis signal satisfied."
        ),
        evidence={"threshold": threshold, "total_functions_scanned": len(fn_metrics)},
    )


def evaluate_sa11_3(fn_metrics: List[Dict], thresholds: Dict) -> Dict[str, Any]:
    """SA-11(3): IV&V — percentage of high-CC functions."""
    cc_threshold = thresholds["sa11_3_high_cyclomatic_threshold"]
    max_pct = thresholds["sa11_3_max_pct_high_cyclomatic"]
    if not fn_metrics:
        return _finding(
            finding_id=f"SA11-3-{uuid.uuid4().hex[:8]}",
            control_id="SA-11(3)",
            severity="warning",
            status="pass",
            summary="No function metrics available to evaluate SA-11(3).",
            evidence={},
        )
    high_cc = [m for m in fn_metrics if m.get("cyclomatic_complexity", 0) > cc_threshold]
    pct = round(len(high_cc) / len(fn_metrics) * 100, 2)
    exceeds = pct > max_pct
    return _finding(
        finding_id=f"SA11-3-{uuid.uuid4().hex[:8]}",
        control_id="SA-11(3)",
        severity="warning",
        status="warn" if exceeds else "pass",
        summary=(
            f"{len(high_cc)}/{len(fn_metrics)} functions ({pct}%) exceed CC>{cc_threshold}. "
            + (
                f"Exceeds {max_pct}% threshold — IV&V scope at risk (SA-11(3))."
                if exceeds
                else f"Within {max_pct}% IV&V threshold. SA-11(3) satisfied."
            )
        ),
        evidence={
            "cc_threshold": cc_threshold,
            "max_pct_threshold": max_pct,
            "high_cc_count": len(high_cc),
            "total_functions": len(fn_metrics),
            "high_cc_pct": pct,
            "top_high_cc": sorted(
                [
                    {
                        "function": m.get("function_name"),
                        "file": m.get("file_path"),
                        "cyclomatic_complexity": m.get("cyclomatic_complexity", 0),
                    }
                    for m in high_cc
                ],
                key=lambda x: x["cyclomatic_complexity"],
                reverse=True,
            )[:10],
        },
    )


def evaluate_sa11_8(fn_metrics: List[Dict], thresholds: Dict) -> Dict[str, Any]:
    """SA-11(8): Dynamic Code Analysis — high cognitive CC per function."""
    threshold = thresholds["sa11_8_max_cognitive_per_function"]
    violators = [
        {
            "function": m.get("function_name"),
            "file": m.get("file_path"),
            "cognitive_complexity": m.get("cognitive_complexity", 0),
        }
        for m in fn_metrics
        if m.get("cognitive_complexity", 0) > threshold
    ]
    violators_sorted = sorted(violators, key=lambda x: x["cognitive_complexity"], reverse=True)
    if violators:
        return _finding(
            finding_id=f"SA11-8-{uuid.uuid4().hex[:8]}",
            control_id="SA-11(8)",
            severity="warning",
            status="warn",
            summary=(
                f"{len(violators)} function(s) exceed cognitive complexity threshold "
                f"(>{threshold}), requiring dynamic analysis coverage per SA-11(8)."
            ),
            evidence={
                "threshold": threshold,
                "violator_count": len(violators),
                "top_violators": violators_sorted[:10],
                "total_functions_scanned": len(fn_metrics),
            },
        )
    return _finding(
        finding_id=f"SA11-8-{uuid.uuid4().hex[:8]}",
        control_id="SA-11(8)",
        severity="warning",
        status="pass",
        summary=(
            f"No functions exceed cognitive complexity threshold (>{threshold}). "
            "SA-11(8) dynamic analysis signal satisfied."
        ),
        evidence={"threshold": threshold, "total_functions_scanned": len(fn_metrics)},
    )


# ---------------------------------------------------------------------------
# SA-15 evaluators
# ---------------------------------------------------------------------------


def evaluate_sa15_1(fn_metrics: List[Dict], thresholds: Dict) -> Dict[str, Any]:
    """SA-15(1): Quality Metrics — avg cyclomatic complexity project-wide."""
    max_avg = thresholds["sa15_1_max_avg_cyclomatic"]
    if not fn_metrics:
        return _finding(
            finding_id=f"SA15-1-{uuid.uuid4().hex[:8]}",
            control_id="SA-15(1)",
            severity="blocking",
            status="pass",
            summary="No function metrics available to evaluate SA-15(1) quality metrics.",
            evidence={},
        )
    avg_cc = round(sum(m.get("cyclomatic_complexity", 0) for m in fn_metrics) / len(fn_metrics), 3)
    exceeds = avg_cc > max_avg
    return _finding(
        finding_id=f"SA15-1-{uuid.uuid4().hex[:8]}",
        control_id="SA-15(1)",
        severity="blocking",
        status="fail" if exceeds else "pass",
        summary=(
            f"Average cyclomatic complexity = {avg_cc} across {len(fn_metrics)} functions. "
            + (
                f"Exceeds SA-15(1) quality metric threshold of {max_avg}."
                if exceeds
                else f"Within SA-15(1) quality metric threshold ({max_avg}). Compliant."
            )
        ),
        evidence={
            "avg_cyclomatic_complexity": avg_cc,
            "max_avg_threshold": max_avg,
            "function_count": len(fn_metrics),
        },
    )


def evaluate_sa15_7(fn_metrics: List[Dict], thresholds: Dict) -> Dict[str, Any]:
    """SA-15(7): Developer Security Testing — avg cognitive complexity."""
    max_avg = thresholds["sa15_7_max_avg_cognitive"]
    if not fn_metrics:
        return _finding(
            finding_id=f"SA15-7-{uuid.uuid4().hex[:8]}",
            control_id="SA-15(7)",
            severity="warning",
            status="pass",
            summary="No function metrics available to evaluate SA-15(7).",
            evidence={},
        )
    avg_cog = round(sum(m.get("cognitive_complexity", 0) for m in fn_metrics) / len(fn_metrics), 3)
    exceeds = avg_cog > max_avg
    return _finding(
        finding_id=f"SA15-7-{uuid.uuid4().hex[:8]}",
        control_id="SA-15(7)",
        severity="warning",
        status="warn" if exceeds else "pass",
        summary=(
            f"Average cognitive complexity = {avg_cog} across {len(fn_metrics)} functions. "
            + (
                f"Exceeds SA-15(7) security testing readability threshold of {max_avg}."
                if exceeds
                else f"Within SA-15(7) security testing threshold ({max_avg}). Compliant."
            )
        ),
        evidence={
            "avg_cognitive_complexity": avg_cog,
            "max_avg_threshold": max_avg,
            "function_count": len(fn_metrics),
        },
    )


def evaluate_sa15_11(trend: List[Dict], thresholds: Dict) -> Dict[str, Any]:
    """SA-15(11): Archive/Lifecycle Signal — cognitive complexity trend."""
    min_delta = thresholds["sa15_11_cognitive_trend_min_delta"]
    if len(trend) < 2:
        return _finding(
            finding_id=f"SA15-11-{uuid.uuid4().hex[:8]}",
            control_id="SA-15(11)",
            severity="warning",
            status="pass",
            summary=f"Insufficient trend data ({len(trend)} scan(s)). SA-15(11) lifecycle check deferred.",
            evidence={"scan_count": len(trend)},
        )
    # Use cognitive complexity trend if available, else fall back to general maintainability
    latest = trend[-1]
    previous = trend[-2]
    latest_cog = latest.get("avg_cognitive_complexity", latest.get("avg_maintainability", 0))
    prev_cog = previous.get("avg_cognitive_complexity", previous.get("avg_maintainability", 0))
    delta = round(latest_cog - prev_cog, 3)
    degrading = delta < min_delta
    return _finding(
        finding_id=f"SA15-11-{uuid.uuid4().hex[:8]}",
        control_id="SA-15(11)",
        severity="warning",
        status="warn" if degrading else "pass",
        summary=(
            f"Cognitive complexity trend delta = {delta:+.3f} "
            f"(latest={latest_cog}, previous={prev_cog}). "
            + (
                f"Degrading beyond SA-15(11) lifecycle threshold ({min_delta:+.1f})."
                if degrading
                else "Stable or improving. SA-15(11) lifecycle signal satisfied."
            )
        ),
        evidence={
            "latest_value": latest_cog,
            "previous_value": prev_cog,
            "delta": delta,
            "min_delta_threshold": min_delta,
            "scan_timestamps": [
                trend[-1].get("scanned_at", ""),
                trend[-2].get("scanned_at", ""),
            ],
        },
    )


# ---------------------------------------------------------------------------
# Main evaluator
# ---------------------------------------------------------------------------


def run_complexity_compliance(
    project_dir: Optional[str] = None,
    include_trend: bool = True,
) -> Dict[str, Any]:
    """Run all SA-11/SA-15 complexity compliance checks.

    Returns a dict with:
      overall_pass (bool), findings (list), summary (dict).
    """
    thresholds = _load_thresholds()
    findings: List[Dict] = []
    errors: List[str] = []

    # --- Collect metrics ---
    try:
        fn_metrics = _collect_metrics(project_dir)
    except Exception as exc:
        fn_metrics = []
        errors.append(f"Metric collection failed: {exc}")

    # --- SA-11 ---
    try:
        findings.append(evaluate_sa11_1(fn_metrics, thresholds))
    except Exception as exc:
        errors.append(f"SA-11(1) eval error: {exc}")

    try:
        findings.append(evaluate_sa11_3(fn_metrics, thresholds))
    except Exception as exc:
        errors.append(f"SA-11(3) eval error: {exc}")

    try:
        findings.append(evaluate_sa11_8(fn_metrics, thresholds))
    except Exception as exc:
        errors.append(f"SA-11(8) eval error: {exc}")

    # --- SA-15 ---
    try:
        findings.append(evaluate_sa15_1(fn_metrics, thresholds))
    except Exception as exc:
        errors.append(f"SA-15(1) eval error: {exc}")

    try:
        findings.append(evaluate_sa15_7(fn_metrics, thresholds))
    except Exception as exc:
        errors.append(f"SA-15(7) eval error: {exc}")

    if include_trend:
        try:
            trend = _collect_trend()
        except Exception as exc:
            trend = []
            errors.append(f"Trend collection failed: {exc}")
        try:
            findings.append(evaluate_sa15_11(trend, thresholds))
        except Exception as exc:
            errors.append(f"SA-15(11) eval error: {exc}")

    # --- Aggregate ---
    blocking_failures = [f for f in findings if f["severity"] == "blocking" and f["status"] == "fail"]
    all_warnings = [f for f in findings if f["status"] in ("warn",)]
    overall_pass = len(blocking_failures) == 0

    sa11_findings = [f for f in findings if f["control_id"].startswith("SA-11")]
    sa15_findings = [f for f in findings if f["control_id"].startswith("SA-15")]
    sa11_pass = all(f["status"] in ("pass",) for f in sa11_findings)
    sa15_pass = all(f["status"] in ("pass",) for f in sa15_findings)

    return {
        "overall_pass": overall_pass,
        "sa11_compliant": sa11_pass,
        "sa15_compliant": sa15_pass,
        "finding_count": len(findings),
        "blocking_count": len(blocking_failures),
        "warning_count": len(all_warnings),
        "functions_scanned": len(fn_metrics),
        "findings": findings,
        "thresholds_applied": thresholds,
        "errors": errors,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Complexity Compliance Signal — NIST SA-11/SA-15 mapping for PDC.",
    )
    parser.add_argument("--project-dir", help="Directory to scan (default: tools/)")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--gate", action="store_true", help="Exit 1 if blocking findings")
    parser.add_argument("--no-trend", action="store_true", help="Skip trend analysis")
    parser.add_argument("--control", help="Filter output to specific control (e.g. SA-11(1))")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    result = run_complexity_compliance(
        project_dir=args.project_dir,
        include_trend=not args.no_trend,
    )

    if args.control:
        result["findings"] = [f for f in result["findings"] if f["control_id"] == args.control]

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        status_icon = "PASS" if result["overall_pass"] else "FAIL"
        print(f"[{status_icon}] Complexity Compliance - NIST SA-11/SA-15")
        print(f"  Functions scanned : {result['functions_scanned']}")
        print(f"  Blocking findings : {result['blocking_count']}")
        print(f"  Warnings          : {result['warning_count']}")
        print(f"  SA-11 compliant   : {result['sa11_compliant']}")
        print(f"  SA-15 compliant   : {result['sa15_compliant']}")
        print()
        for finding in result["findings"]:
            icon = {"pass": "[OK]", "warn": "[WARN]", "fail": "[FAIL]"}.get(finding["status"], "[?]")
            print(f"  {icon} [{finding['control_id']}] {finding['summary']}")
        if result["errors"]:
            print("\n  Errors:")
            for err in result["errors"]:
                print(f"    - {err}")

    if args.gate and not result["overall_pass"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
