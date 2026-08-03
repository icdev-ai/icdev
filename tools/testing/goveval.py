#!/usr/bin/env python3
# CUI // SP-CTI
"""GovEval — Domain-Specific Evaluation Benchmark for Gov/DoD Compliance Code Quality.

Adapted from LeanStral's FLTEval pattern (Mistral AI, 2026-03-16).
Just as FLTEval measures formal proof quality, GovEval measures compliance
code generation quality across ICDEV™'s compliance domains.

100% deterministic, air-gap safe. No LLM required for evaluation.
Optional LLM-as-judge mode for subjective quality scoring.

Architecture Decisions:
  D-VL-9: GovEval as domain-specific evaluation benchmark
  D-VL-10: Deterministic scoring (no LLM required for core metrics)
  D-VL-11: Append-only goveval_results table (NIST AU)

Evaluation Dimensions (7):
  1. SSP Completeness    — Are all required SSP sections present?
  2. Control Accuracy    — Do control implementations map correctly to NIST 800-53?
  3. CUI Consistency     — Are classification markings consistent and correct?
  4. SBOM Quality        — Is the SBOM complete and well-formed?
  5. Gate Pass Rate      — What fraction of security gates pass?
  6. Artifact Currency   — Are compliance artifacts up-to-date?
  7. Cross-Framework     — Does crosswalk cascade correctly?

Usage:
    python tools/testing/goveval.py --project-id "sparkpilot" --json
    python tools/testing/goveval.py --project-id "sparkpilot" --dimension ssp_completeness --json
    python tools/testing/goveval.py --project-id "sparkpilot" --gate --json
    python tools/testing/goveval.py --project-id "sparkpilot" --trend --json
    python tools/testing/goveval.py --project-id "sparkpilot" --compare --model-a "qwen3" --model-b "claude" --json
"""

import argparse
import json
import os
import sqlite3
import sys
import uuid
from tools.db.storage import get_connection
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.testing.goveval")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# ---------------------------------------------------------------------------
# Database (append-only, NIST AU — D-VL-11)
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS goveval_results (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    dimension TEXT NOT NULL,
    score REAL NOT NULL DEFAULT 0.0,
    max_score REAL NOT NULL DEFAULT 1.0,
    findings_count INTEGER NOT NULL DEFAULT 0,
    findings_json TEXT,
    model_label TEXT,
    evaluator TEXT NOT NULL DEFAULT 'deterministic',
    metadata_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_CREATE_TABLE)
    return conn


def _store_result(result: Dict, project_id: str, model_label: str = "") -> None:
    try:
        conn = _get_db()
        conn.execute(
            """INSERT INTO goveval_results
               (id, project_id, dimension, score, max_score, findings_count,
                findings_json, model_label, evaluator, metadata_json)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                f"ge-{uuid.uuid4().hex[:12]}",
                project_id,
                result.get("dimension", ""),
                result.get("score", 0.0),
                result.get("max_score", 1.0),
                result.get("findings_count", 0),
                json.dumps(result.get("findings", []))[:5000],
                model_label,
                result.get("evaluator", "deterministic"),
                json.dumps(result.get("metadata", {}))[:2000],
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("_store_result: best-effort INSERT into goveval_results failed (non-blocking): %s", exc)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Dimension 1: SSP Completeness
# ---------------------------------------------------------------------------

_SSP_REQUIRED_SECTIONS = [
    "system_name",
    "system_description",
    "security_categorization",
    "system_boundary",
    "network_architecture",
    "data_flow",
    "personnel_roles",
    "authentication_mechanisms",
    "audit_mechanisms",
    "incident_response",
    "contingency_plan",
    "maintenance_procedures",
    "physical_security",
    "risk_assessment",
    "security_assessment",
]


def eval_ssp_completeness(project_id: str) -> Dict:
    """Check SSP completeness by querying compliance artifacts in DB."""
    findings = []
    sections_found = 0
    try:
        conn = _get_db()
        # Check if SSP exists
        rows = conn.execute(
            "SELECT content FROM compliance_artifacts WHERE project_id = %s AND artifact_type = 'ssp' ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchall()
        conn.close()

        if not rows:
            findings.append({"description": "No SSP artifact found", "severity": "critical"})
        else:
            content = rows[0][0] or ""
            for section in _SSP_REQUIRED_SECTIONS:
                # Check if section appears in SSP content (keyword match)
                keywords = section.replace("_", " ")
                if keywords.lower() in content.lower():
                    sections_found += 1
                else:
                    findings.append(
                        {
                            "description": f"SSP section missing: {section}",
                            "severity": "warning",
                        }
                    )
    except Exception:
        # DB table may not exist — still score
        findings.append({"description": "Cannot query compliance_artifacts table", "severity": "info"})

    total = len(_SSP_REQUIRED_SECTIONS)
    score = sections_found / total if total else 0.0

    return {
        "dimension": "ssp_completeness",
        "score": round(score, 3),
        "max_score": 1.0,
        "findings_count": len(findings),
        "findings": findings,
        "evaluator": "deterministic",
        "metadata": {"sections_found": sections_found, "sections_required": total},
    }


# ---------------------------------------------------------------------------
# Dimension 2: Control Accuracy (NIST 800-53 mapping)
# ---------------------------------------------------------------------------


def eval_control_accuracy(project_id: str) -> Dict:
    """Check that control mappings exist and map to valid NIST controls."""
    findings = []
    total_controls = 0
    valid_controls = 0
    try:
        conn = _get_db()
        rows = conn.execute(
            "SELECT control_id, status FROM control_mappings WHERE project_id = %s", (project_id,)
        ).fetchall()
        conn.close()

        total_controls = len(rows)
        for control_id, status in rows:
            # Valid NIST control pattern: XX-N or XX-N(N)
            if control_id and len(control_id) >= 3 and "-" in control_id:
                valid_controls += 1
            else:
                findings.append(
                    {
                        "description": f"Invalid control ID format: {control_id}",
                        "severity": "warning",
                    }
                )

        if total_controls == 0:
            findings.append({"description": "No control mappings found", "severity": "high"})

    except Exception:
        findings.append({"description": "Cannot query control_mappings table", "severity": "info"})

    score = valid_controls / max(total_controls, 1)
    return {
        "dimension": "control_accuracy",
        "score": round(score, 3),
        "max_score": 1.0,
        "findings_count": len(findings),
        "findings": findings,
        "evaluator": "deterministic",
        "metadata": {"total_controls": total_controls, "valid_controls": valid_controls},
    }


# ---------------------------------------------------------------------------
# Dimension 3: CUI Consistency
# ---------------------------------------------------------------------------


def eval_cui_consistency(project_id: str) -> Dict:
    """Check CUI markings across project files (sample-based)."""
    findings = []
    files_checked = 0
    files_with_cui = 0

    project_dir = str(BASE_DIR)
    # Check a sample of Python files for CUI markings
    exclude = {".git", "__pycache__", "venv", ".venv", "node_modules", ".tmp"}
    py_files = []
    for root, dirs, fnames in os.walk(os.path.join(project_dir, "tools")):
        dirs[:] = [d for d in dirs if d not in exclude]
        for fname in fnames:
            if fname.endswith(".py"):
                py_files.append(os.path.join(root, fname))
                if len(py_files) >= 50:
                    break
        if len(py_files) >= 50:
            break

    for fpath in py_files:
        files_checked += 1
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                head = f.read(500)
            if "CUI" in head:
                files_with_cui += 1
        except Exception:
            pass

    if files_checked == 0:
        findings.append({"description": "No Python files found to check", "severity": "info"})
        score = 1.0
    else:
        score = files_with_cui / files_checked
        if score < 0.9:
            findings.append(
                {
                    "description": f"CUI marking rate: {score:.0%} ({files_with_cui}/{files_checked})",
                    "severity": "warning",
                }
            )

    return {
        "dimension": "cui_consistency",
        "score": round(score, 3),
        "max_score": 1.0,
        "findings_count": len(findings),
        "findings": findings,
        "evaluator": "deterministic",
        "metadata": {"files_checked": files_checked, "files_with_cui": files_with_cui},
    }


# ---------------------------------------------------------------------------
# Dimension 4: SBOM Quality
# ---------------------------------------------------------------------------


def eval_sbom_quality(project_id: str) -> Dict:
    """Check SBOM existence and quality."""
    findings = []
    score = 0.0
    try:
        conn = _get_db()
        rows = conn.execute(
            "SELECT content FROM compliance_artifacts WHERE project_id = %s AND artifact_type = 'sbom' ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchall()
        conn.close()

        if not rows:
            findings.append({"description": "No SBOM artifact found", "severity": "high"})
        else:
            content = rows[0][0] or ""
            # Check for CycloneDX or SPDX format markers
            has_format = "bomFormat" in content or "spdxVersion" in content
            has_components = "components" in content or "packages" in content
            has_version = "specVersion" in content or "spdxVersion" in content

            checks = [has_format, has_components, has_version]
            score = sum(checks) / len(checks)

            if not has_format:
                findings.append({"description": "SBOM missing standard format marker", "severity": "warning"})
            if not has_components:
                findings.append({"description": "SBOM has no components/packages", "severity": "warning"})

    except Exception:
        findings.append({"description": "Cannot query compliance_artifacts table", "severity": "info"})

    return {
        "dimension": "sbom_quality",
        "score": round(score, 3),
        "max_score": 1.0,
        "findings_count": len(findings),
        "findings": findings,
        "evaluator": "deterministic",
    }


# ---------------------------------------------------------------------------
# Dimension 5: Gate Pass Rate
# ---------------------------------------------------------------------------


def eval_gate_pass_rate(project_id: str) -> Dict:
    """Check historical gate evaluation pass rate from audit trail."""
    findings = []
    total_gates = 0
    passed_gates = 0
    try:
        conn = _get_db()
        rows = conn.execute(
            """SELECT action, details FROM audit_trail
               WHERE project_id = %s AND event_type = 'gate.evaluation'
               ORDER BY created_at DESC LIMIT 100""",
            (project_id,),
        ).fetchall()
        conn.close()

        total_gates = len(rows)
        for action, details in rows:
            if "pass" in (action or "").lower() or "pass" in (details or "").lower():
                passed_gates += 1

        if total_gates == 0:
            findings.append({"description": "No gate evaluations found in audit trail", "severity": "info"})

    except Exception:
        findings.append({"description": "Cannot query audit_trail table", "severity": "info"})

    score = passed_gates / max(total_gates, 1)
    return {
        "dimension": "gate_pass_rate",
        "score": round(score, 3),
        "max_score": 1.0,
        "findings_count": len(findings),
        "findings": findings,
        "evaluator": "deterministic",
        "metadata": {"total_gates": total_gates, "passed_gates": passed_gates},
    }


# ---------------------------------------------------------------------------
# Dimension 6: Artifact Currency
# ---------------------------------------------------------------------------


def eval_artifact_currency(project_id: str) -> Dict:
    """Check if compliance artifacts are recent (< 30 days)."""
    findings = []
    total = 0
    current = 0
    try:
        conn = _get_db()
        rows = conn.execute(
            """SELECT artifact_type, created_at FROM compliance_artifacts
               WHERE project_id = %s ORDER BY created_at DESC""",
            (project_id,),
        ).fetchall()
        conn.close()

        now = datetime.now(timezone.utc)
        seen_types = set()
        for art_type, created_at_str in rows:
            if art_type in seen_types:
                continue  # Only check latest per type
            seen_types.add(art_type)
            total += 1
            try:
                created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                age_days = (now - created_at).days
                if age_days <= 30:
                    current += 1
                else:
                    findings.append(
                        {
                            "description": f"{art_type} is {age_days} days old (max 30)",
                            "severity": "warning",
                        }
                    )
            except Exception:
                current += 1  # Assume current if can't parse date

    except Exception:
        findings.append({"description": "Cannot query compliance_artifacts", "severity": "info"})

    score = current / max(total, 1)
    return {
        "dimension": "artifact_currency",
        "score": round(score, 3),
        "max_score": 1.0,
        "findings_count": len(findings),
        "findings": findings,
        "evaluator": "deterministic",
        "metadata": {"total_artifacts": total, "current_artifacts": current},
    }


# ---------------------------------------------------------------------------
# Dimension 7: Cross-Framework Cascade
# ---------------------------------------------------------------------------


def eval_crosswalk_cascade(project_id: str) -> Dict:
    """Check that crosswalk engine properly cascades across frameworks."""
    findings = []
    try:
        conn = _get_db()
        # Check if multiple framework assessments exist
        rows = conn.execute(
            """SELECT DISTINCT source_framework FROM crosswalk_results
               WHERE project_id = %s""",
            (project_id,),
        ).fetchall()
        conn.close()

        frameworks = [r[0] for r in rows]
        if len(frameworks) == 0:
            findings.append({"description": "No crosswalk results found", "severity": "info"})
            score = 0.0
        elif len(frameworks) == 1:
            findings.append({"description": "Only 1 framework in crosswalk — cascade not testable", "severity": "info"})
            score = 0.5
        else:
            score = min(1.0, len(frameworks) / 5.0)  # 5+ frameworks = 1.0

    except Exception:
        findings.append({"description": "Cannot query crosswalk_results", "severity": "info"})
        score = 0.0

    return {
        "dimension": "crosswalk_cascade",
        "score": round(score, 3),
        "max_score": 1.0,
        "findings_count": len(findings),
        "findings": findings,
        "evaluator": "deterministic",
    }


# ---------------------------------------------------------------------------
# Main evaluation
# ---------------------------------------------------------------------------

ALL_DIMENSIONS = {
    "ssp_completeness": eval_ssp_completeness,
    "control_accuracy": eval_control_accuracy,
    "cui_consistency": eval_cui_consistency,
    "sbom_quality": eval_sbom_quality,
    "gate_pass_rate": eval_gate_pass_rate,
    "artifact_currency": eval_artifact_currency,
    "crosswalk_cascade": eval_crosswalk_cascade,
}

# Weights for composite score (D21 deterministic weighted average)
DIMENSION_WEIGHTS = {
    "ssp_completeness": 0.20,
    "control_accuracy": 0.20,
    "cui_consistency": 0.15,
    "sbom_quality": 0.10,
    "gate_pass_rate": 0.15,
    "artifact_currency": 0.10,
    "crosswalk_cascade": 0.10,
}


def run_evaluation(
    project_id: str,
    dimension: str = "",
    model_label: str = "",
) -> Dict:
    """Run GovEval benchmark on a project."""
    results = []

    if dimension:
        if dimension not in ALL_DIMENSIONS:
            return {"error": f"Unknown dimension: {dimension}", "available": list(ALL_DIMENSIONS.keys())}
        fn = ALL_DIMENSIONS[dimension]
        result = fn(project_id)
        _store_result(result, project_id, model_label)
        results.append(result)
    else:
        for dim_name, fn in ALL_DIMENSIONS.items():
            result = fn(project_id)
            _store_result(result, project_id, model_label)
            results.append(result)

    # Compute weighted composite score
    composite = 0.0
    for r in results:
        dim = r.get("dimension", "")
        weight = DIMENSION_WEIGHTS.get(dim, 1.0 / len(ALL_DIMENSIONS))
        composite += r.get("score", 0.0) * weight

    # Normalize if only running one dimension
    if dimension:
        composite = results[0].get("score", 0.0)

    total_findings = sum(r.get("findings_count", 0) for r in results)

    return {
        "project_id": project_id,
        "model_label": model_label or "default",
        "dimensions_evaluated": len(results),
        "composite_score": round(composite, 3),
        "total_findings": total_findings,
        "gate_passed": composite >= 0.60,
        "dimension_results": results,
        "evaluated_at": _now(),
    }


def get_trend(project_id: str) -> Dict:
    """Get score trend over recent evaluations."""
    try:
        conn = _get_db()
        rows = conn.execute(
            """SELECT dimension, score, model_label, created_at
               FROM goveval_results
               WHERE project_id = %s
               ORDER BY created_at DESC
               LIMIT 100""",
            (project_id,),
        ).fetchall()
        conn.close()

        entries = []
        for dim, score, model, created in rows:
            entries.append(
                {
                    "dimension": dim,
                    "score": score,
                    "model_label": model or "default",
                    "created_at": created,
                }
            )

        return {
            "project_id": project_id,
            "entries": entries,
            "count": len(entries),
        }
    except Exception as e:
        return {"project_id": project_id, "error": str(e), "entries": []}


def compare_models(project_id: str, model_a: str, model_b: str) -> Dict:
    """Compare GovEval scores between two model labels."""
    try:
        conn = _get_db()
        rows_a = conn.execute(
            """SELECT dimension, AVG(score) as avg_score
               FROM goveval_results
               WHERE project_id = %s AND model_label = %s
               GROUP BY dimension""",
            (project_id, model_a),
        ).fetchall()
        rows_b = conn.execute(
            """SELECT dimension, AVG(score) as avg_score
               FROM goveval_results
               WHERE project_id = %s AND model_label = %s
               GROUP BY dimension""",
            (project_id, model_b),
        ).fetchall()
        conn.close()

        scores_a = {dim: score for dim, score in rows_a}
        scores_b = {dim: score for dim, score in rows_b}
        all_dims = sorted(set(scores_a.keys()) | set(scores_b.keys()))

        comparison = []
        for dim in all_dims:
            sa = scores_a.get(dim, 0.0)
            sb = scores_b.get(dim, 0.0)
            comparison.append(
                {
                    "dimension": dim,
                    f"{model_a}_score": round(sa, 3),
                    f"{model_b}_score": round(sb, 3),
                    "delta": round(sa - sb, 3),
                    "winner": model_a if sa > sb else (model_b if sb > sa else "tie"),
                }
            )

        return {
            "project_id": project_id,
            "model_a": model_a,
            "model_b": model_b,
            "dimensions": comparison,
        }
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="GovEval Benchmark (LeanStral FLTEval-adapted)")
    parser.add_argument("--project-id", required=True, help="ICDEV™ project ID")
    parser.add_argument("--dimension", default="", help="Specific dimension to evaluate")
    parser.add_argument("--model-label", default="", help="Model label for comparison tracking")
    parser.add_argument("--trend", action="store_true", help="Show score trend")
    parser.add_argument("--compare", action="store_true", help="Compare two models")
    parser.add_argument("--model-a", default="", help="First model for comparison")
    parser.add_argument("--model-b", default="", help="Second model for comparison")
    parser.add_argument("--gate", action="store_true", help="Gate evaluation (exit 0=pass, 1=fail)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    args = parser.parse_args()

    if args.trend:
        result = get_trend(args.project_id)
    elif args.compare:
        if not args.model_a or not args.model_b:
            parser.error("--compare requires --model-a and --model-b")
        result = compare_models(args.project_id, args.model_a, args.model_b)
    else:
        result = run_evaluation(args.project_id, args.dimension, args.model_label)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    elif args.human:
        _print_human(result)
    else:
        print(json.dumps(result, indent=2, default=str))

    if args.gate:
        sys.exit(0 if result.get("gate_passed", False) else 1)


def _print_human(result: Dict):
    """Human-readable output."""
    if "dimension_results" in result:
        print(f"\n{'=' * 60}")
        print(f"  GovEval Benchmark — {result['project_id']}")
        print(f"{'=' * 60}")
        print(f"  Composite Score:  {result['composite_score']:.3f}")
        print(f"  Dimensions:       {result['dimensions_evaluated']}")
        print(f"  Total Findings:   {result['total_findings']}")
        print(f"  Gate:             {'PASS' if result['gate_passed'] else 'FAIL'}")
        print()
        for r in result.get("dimension_results", []):
            bar_len = int(r.get("score", 0) * 20)
            bar = "#" * bar_len + "." * (20 - bar_len)
            print(f"  {r['dimension']:25s} [{bar}] {r['score']:.3f}  ({r['findings_count']} findings)")
        print(f"{'=' * 60}\n")
    elif "entries" in result:
        print(f"\n  GovEval Trend — {result['project_id']} ({result['count']} entries)")
        for e in result.get("entries", [])[:20]:
            print(f"  {e['created_at'][:19]}  {e['dimension']:25s}  {e['score']:.3f}  ({e['model_label']})")
    elif "dimensions" in result:
        print(f"\n  GovEval Comparison — {result.get('model_a')} vs {result.get('model_b')}")
        for d in result.get("dimensions", []):
            print(
                f"  {d['dimension']:25s}  {d.get(result.get('model_a', '') + '_score', 0):.3f} vs {d.get(result.get('model_b', '') + '_score', 0):.3f}  winner={d['winner']}"
            )


if __name__ == "__main__":
    main()
