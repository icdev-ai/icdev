#!/usr/bin/env python3
# CUI // SP-CTI
"""DoD SWFT (Software Factory Trust) Evidence Bundler.

Packages ICDEV compliance artifacts into a SWFT-compliant evidence bundle
for DoD software factory authorization. Bundles SLSA provenance, SBOM,
VEX, attestations, and compliance artifacts into a single package.

Architecture Decisions:
  D341: Extends existing attestation_manager.py patterns.
  D342: CycloneDX spec version parameterized for forward compatibility.

Usage:
  python tools/compliance/swft_evidence_bundler.py --project-id proj-test --bundle --json
  python tools/compliance/swft_evidence_bundler.py --project-id proj-test --validate --json
  python tools/compliance/swft_evidence_bundler.py --project-id proj-test --bundle --output-dir /tmp/swft --json
"""

import argparse
import hashlib
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# SWFT artifact categories per DoD Software Factory requirements
SWFT_ARTIFACT_CATEGORIES = {
    "provenance": {
        "description": "SLSA provenance attestation",
        "required": True,
        "sources": ["slsa_attestation_generator"],
    },
    "sbom": {
        "description": "Software Bill of Materials (CycloneDX)",
        "required": True,
        "sources": ["sbom_generator"],
    },
    "vex": {
        "description": "Vulnerability Exploitability eXchange",
        "required": True,
        "sources": ["slsa_attestation_generator"],
    },
    "sast_results": {
        "description": "Static Application Security Testing results",
        "required": True,
        "sources": ["sast_runner"],
    },
    "dependency_audit": {
        "description": "Dependency vulnerability audit",
        "required": True,
        "sources": ["dependency_auditor"],
    },
    "container_scan": {
        "description": "Container image security scan",
        "required": False,
        "sources": ["container_scanner"],
    },
    "secret_detection": {
        "description": "Secret detection scan results",
        "required": True,
        "sources": ["secret_detector"],
    },
    "image_attestation": {
        "description": "Signed container image attestation",
        "required": False,
        "sources": ["attestation_manager"],
    },
    "compliance_assessment": {
        "description": "Multi-framework compliance assessment",
        "required": True,
        "sources": ["multi_regime_assessor"],
    },
    "production_audit": {
        "description": "Production readiness audit results",
        "required": False,
        "sources": ["production_audit"],
    },
}


def _get_connection(db_path=None):
    """Get a database connection."""
    path = db_path or DB_PATH
    if path.exists():
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        return conn
    return None


def _collect_artifact_evidence(project_id: str, category: str, conn) -> dict:
    """Collect evidence for a specific SWFT artifact category."""
    evidence = {"available": False, "records": [], "latest_date": None}

    if not conn:
        return evidence

    # Table mapping for each category
    table_queries = {
        "provenance": ("devsecops_pipeline_audit", "project_id"),
        "sbom": ("sbom_records", "project_id"),
        "sast_results": ("audit_trail", "project_id"),
        "dependency_audit": ("audit_trail", "project_id"),
        "secret_detection": ("audit_trail", "project_id"),
        "compliance_assessment": ("production_audits", "project_id"),
        "production_audit": ("production_audits", "project_id"),
        "container_scan": ("devsecops_pipeline_audit", "project_id"),
        "image_attestation": ("devsecops_pipeline_audit", "project_id"),
    }

    query_info = table_queries.get(category)
    if not query_info:
        return evidence

    table, id_col = query_info
    try:
        row = conn.execute(
            f"SELECT COUNT(*) as cnt FROM {table} WHERE {id_col} = ?",
            (project_id,),
        ).fetchone()
        if row and row["cnt"] > 0:
            evidence["available"] = True
            evidence["record_count"] = row["cnt"]

        # Get latest date
        date_row = conn.execute(
            f"SELECT MAX(created_at) as latest FROM {table} WHERE {id_col} = ?",
            (project_id,),
        ).fetchone()
        if date_row and date_row["latest"]:
            evidence["latest_date"] = date_row["latest"]
    except sqlite3.OperationalError:
        pass

    return evidence


def bundle_swft_evidence(
    project_id: str,
    output_dir: str = None,
    db_path: Path = None,
) -> dict:
    """Bundle SWFT evidence package for DoD software factory authorization.

    Args:
        project_id: Project identifier.
        output_dir: Optional output directory for evidence files.
        db_path: Optional database path override.

    Returns:
        dict with bundle manifest, artifact status, and readiness assessment.
    """
    conn = _get_connection(db_path)
    now = datetime.now(timezone.utc)
    bundle_id = str(uuid.uuid4())[:12]

    try:
        artifacts = {}
        required_met = 0
        required_total = 0
        optional_met = 0
        optional_total = 0

        for category, config in SWFT_ARTIFACT_CATEGORIES.items():
            evidence = _collect_artifact_evidence(project_id, category, conn)
            artifact = {
                "category": category,
                "description": config["description"],
                "required": config["required"],
                "available": evidence["available"],
                "latest_date": evidence.get("latest_date"),
                "record_count": evidence.get("record_count", 0),
            }
            artifacts[category] = artifact

            if config["required"]:
                required_total += 1
                if evidence["available"]:
                    required_met += 1
            else:
                optional_total += 1
                if evidence["available"]:
                    optional_met += 1

        total_met = required_met + optional_met
        total_artifacts = required_total + optional_total
        readiness_pct = (total_met / total_artifacts * 100) if total_artifacts > 0 else 0

        bundle = {
            "bundle_id": bundle_id,
            "project_id": project_id,
            "bundle_type": "swft_evidence",
            "specification": "DoD Software Factory Trust Framework",
            "generated_at": now.isoformat(),
            "artifacts": artifacts,
            "summary": {
                "total_categories": total_artifacts,
                "available": total_met,
                "missing": total_artifacts - total_met,
                "required_met": required_met,
                "required_total": required_total,
                "optional_met": optional_met,
                "optional_total": optional_total,
                "readiness_pct": round(readiness_pct, 1),
                "all_required_met": required_met == required_total,
            },
            "integrity": {
                "digest_algorithm": "sha256",
                "bundle_hash": hashlib.sha256(
                    json.dumps(artifacts, sort_keys=True, default=str).encode()
                ).hexdigest(),
            },
        }

        # Write to output directory if specified
        if output_dir:
            out_path = Path(output_dir)
            out_path.mkdir(parents=True, exist_ok=True)
            manifest_file = out_path / f"swft-bundle-{bundle_id}.json"
            manifest_file.write_text(json.dumps(bundle, indent=2, default=str))
            bundle["output_file"] = str(manifest_file)

        return bundle

    finally:
        if conn:
            conn.close()


def validate_swft_bundle(
    project_id: str,
    db_path: Path = None,
) -> dict:
    """Validate SWFT evidence completeness and freshness.

    Args:
        project_id: Project identifier.
        db_path: Optional database path override.

    Returns:
        dict with validation results, gaps, and recommendations.
    """
    bundle = bundle_swft_evidence(project_id, db_path=db_path)
    gaps = []
    recommendations = []

    for category, artifact in bundle["artifacts"].items():
        if artifact["required"] and not artifact["available"]:
            gaps.append({
                "category": category,
                "description": artifact["description"],
                "severity": "blocking",
            })
            rec_map = {
                "provenance": "Run: python tools/compliance/slsa_attestation_generator.py --project-id {pid} --generate",
                "sbom": "Run: python tools/compliance/sbom_generator.py --project-id {pid}",
                "vex": "Run: python tools/compliance/slsa_attestation_generator.py --project-id {pid} --vex",
                "sast_results": "Run: python tools/security/sast_runner.py --project-dir <path>",
                "dependency_audit": "Run: python tools/security/dependency_auditor.py --project-dir <path>",
                "secret_detection": "Run: python tools/security/secret_detector.py --project-dir <path>",
                "compliance_assessment": "Run: python tools/testing/production_audit.py --json",
            }
            recommendations.append(
                rec_map.get(category, f"Generate {category} evidence").format(pid=project_id)
            )
        elif not artifact["required"] and not artifact["available"]:
            gaps.append({
                "category": category,
                "description": artifact["description"],
                "severity": "warning",
            })

    return {
        "project_id": project_id,
        "valid": len([g for g in gaps if g["severity"] == "blocking"]) == 0,
        "bundle_summary": bundle["summary"],
        "gaps": gaps,
        "gap_count": len(gaps),
        "blocking_gaps": len([g for g in gaps if g["severity"] == "blocking"]),
        "warning_gaps": len([g for g in gaps if g["severity"] == "warning"]),
        "recommendations": recommendations,
    }


def main():
    parser = argparse.ArgumentParser(
        description="DoD SWFT Evidence Bundler"
    )
    parser.add_argument("--project-id", required=True, help="Project ID", dest="project_id")
    parser.add_argument("--bundle", action="store_true", help="Bundle SWFT evidence")
    parser.add_argument("--validate", action="store_true", help="Validate SWFT evidence completeness")
    parser.add_argument("--output-dir", help="Output directory for evidence files", dest="output_dir")
    parser.add_argument("--db", help="Database path")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    args = parser.parse_args()

    db = Path(args.db) if args.db else None

    if not any([args.bundle, args.validate]):
        args.bundle = True  # Default action

    if args.bundle:
        result = bundle_swft_evidence(args.project_id, args.output_dir, db_path=db)
    else:
        result = validate_swft_bundle(args.project_id, db_path=db)

    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        if args.bundle:
            s = result["summary"]
            print(f"\n=== SWFT Evidence Bundle ===")
            print(f"  Bundle ID: {result['bundle_id']}")
            print(f"  Readiness: {s['readiness_pct']}%")
            print(f"  Required: {s['required_met']}/{s['required_total']}")
            print(f"  Optional: {s['optional_met']}/{s['optional_total']}")
            print(f"  All Required Met: {s['all_required_met']}")
        else:
            print(f"\n=== SWFT Validation ===")
            print(f"  Valid: {result['valid']}")
            print(f"  Blocking Gaps: {result['blocking_gaps']}")
            print(f"  Warning Gaps: {result['warning_gaps']}")
            if result["recommendations"]:
                print(f"  Recommendations:")
                for rec in result["recommendations"]:
                    print(f"    - {rec}")


if __name__ == "__main__":
    main()
