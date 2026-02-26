#!/usr/bin/env python3
# CUI // SP-CTI
"""SLSA v1.0 Provenance Generator + VEX Document Generator.

Generates SLSA (Supply-chain Levels for Software Artifacts) v1.0 provenance
statements and VEX (Vulnerability Exploitability eXchange) documents from
ICDEV build pipeline evidence. Extends existing attestation_manager.py (D341).

Architecture Decisions:
  D341: SLSA attestation generator extends existing attestation_manager.py.
        Produces SLSA v1.0 provenance from build pipeline evidence.
  D342: CycloneDX version upgrade is backward-compatible with --spec-version flag.

Usage:
  python tools/compliance/slsa_attestation_generator.py --project-id proj-test --generate --json
  python tools/compliance/slsa_attestation_generator.py --project-id proj-test --vex --json
  python tools/compliance/slsa_attestation_generator.py --project-id proj-test --verify --json
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

# SLSA v1.0 specification constants
SLSA_PROVENANCE_TYPE = "https://slsa.dev/provenance/v1"
IN_TOTO_STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
SLSA_BUILD_TYPE = "https://icdev.mil/build/v1"

# SLSA level requirements
SLSA_LEVEL_REQUIREMENTS = {
    0: {"description": "No guarantees", "requirements": []},
    1: {
        "description": "Documentation of the build process",
        "requirements": ["build_process_documented"],
    },
    2: {
        "description": "Tamper resistance of the build service",
        "requirements": [
            "build_process_documented",
            "version_controlled_source",
            "build_service_authenticated",
        ],
    },
    3: {
        "description": "Extra resistance to specific threats",
        "requirements": [
            "build_process_documented",
            "version_controlled_source",
            "build_service_authenticated",
            "build_as_code",
            "ephemeral_environment",
            "isolated_builds",
        ],
    },
    4: {
        "description": "Highest level of confidence",
        "requirements": [
            "build_process_documented",
            "version_controlled_source",
            "build_service_authenticated",
            "build_as_code",
            "ephemeral_environment",
            "isolated_builds",
            "hermetic_builds",
            "reproducible_builds",
        ],
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


def _collect_build_evidence(project_id: str, conn) -> dict:
    """Collect build evidence from ICDEV databases."""
    evidence = {
        "build_process_documented": False,
        "version_controlled_source": False,
        "build_service_authenticated": False,
        "build_as_code": False,
        "ephemeral_environment": False,
        "isolated_builds": False,
        "hermetic_builds": False,
        "reproducible_builds": False,
    }

    if not conn:
        return evidence

    try:
        # Check for pipeline audit records (build_process_documented)
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM devsecops_pipeline_audit WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        if row and row["cnt"] > 0:
            evidence["build_process_documented"] = True

        # Check for SBOM records (version_controlled_source)
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM sbom_records WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        if row and row["cnt"] > 0:
            evidence["version_controlled_source"] = True

        # Check for devsecops profile (build_service_authenticated)
        row = conn.execute(
            "SELECT active_stages FROM devsecops_profiles WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        if row:
            stages = json.loads(row["active_stages"] or "[]")
            if "image_signing" in stages:
                evidence["build_service_authenticated"] = True
            if "sbom_attestation" in stages:
                evidence["build_as_code"] = True

        # Check for K8s deployment evidence (ephemeral_environment, isolated_builds)
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM audit_trail WHERE project_id = ? AND event_type LIKE '%deploy%'",
            (project_id,),
        ).fetchone()
        if row and row["cnt"] > 0:
            evidence["ephemeral_environment"] = True
            evidence["isolated_builds"] = True

        # Check for attestation verification (hermetic_builds)
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM devsecops_pipeline_audit WHERE project_id = ? AND stage = 'image_signing'",
            (project_id,),
        ).fetchone()
        if row and row["cnt"] > 0:
            evidence["hermetic_builds"] = True

    except sqlite3.OperationalError:
        pass

    return evidence


def _determine_slsa_level(evidence: dict) -> int:
    """Determine SLSA level from collected evidence."""
    for level in [4, 3, 2, 1]:
        reqs = SLSA_LEVEL_REQUIREMENTS[level]["requirements"]
        if all(evidence.get(r, False) for r in reqs):
            return level
    return 0


def generate_slsa_provenance(
    project_id: str,
    build_info: dict = None,
    db_path: Path = None,
) -> dict:
    """Generate SLSA v1.0 provenance statement.

    Args:
        project_id: Project identifier.
        build_info: Optional build metadata (commit, branch, pipeline_id).
        db_path: Optional database path override.

    Returns:
        dict with provenance statement, SLSA level, and evidence summary.
    """
    conn = _get_connection(db_path)
    now = datetime.now(timezone.utc)
    build_info = build_info or {}

    try:
        evidence = _collect_build_evidence(project_id, conn)
        slsa_level = _determine_slsa_level(evidence)

        # Collect subjects (built artifacts)
        subjects = []
        if conn:
            try:
                rows = conn.execute(
                    "SELECT * FROM sbom_records WHERE project_id = ? ORDER BY created_at DESC LIMIT 5",
                    (project_id,),
                ).fetchall()
                for row in rows:
                    version = row["version"] if row["version"] else "1"
                    file_path = row["file_path"] if row["file_path"] else f"sbom-{row['id']}"
                    subjects.append({
                        "name": file_path,
                        "digest": {
                            "sha256": hashlib.sha256(
                                (row["id"] + version).encode()
                            ).hexdigest()
                        },
                    })
            except (sqlite3.OperationalError, KeyError):
                pass

        # Build the in-toto v1 statement
        provenance = {
            "_type": IN_TOTO_STATEMENT_TYPE,
            "subject": subjects or [{"name": f"project-{project_id}", "digest": {"sha256": hashlib.sha256(project_id.encode()).hexdigest()}}],
            "predicateType": SLSA_PROVENANCE_TYPE,
            "predicate": {
                "buildDefinition": {
                    "buildType": SLSA_BUILD_TYPE,
                    "externalParameters": {
                        "repository": build_info.get("repository", "https://gitlab.mil/icdev"),
                        "ref": build_info.get("commit", "HEAD"),
                        "branch": build_info.get("branch", "main"),
                    },
                    "internalParameters": {
                        "project_id": project_id,
                        "icdev_version": "1.0",
                        "build_pipeline": build_info.get("pipeline_id", "local"),
                    },
                    "resolvedDependencies": [],
                },
                "runDetails": {
                    "builder": {
                        "id": build_info.get("builder_id", "https://icdev.mil/builder/v1"),
                        "version": {"icdev": "1.0"},
                    },
                    "metadata": {
                        "invocationId": build_info.get("pipeline_id", str(uuid.uuid4())),
                        "startedOn": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "finishedOn": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                },
            },
        }

        # Populate resolved dependencies from SBOM
        if conn:
            try:
                rows = conn.execute(
                    "SELECT * FROM sbom_records WHERE project_id = ? ORDER BY created_at DESC LIMIT 1",
                    (project_id,),
                ).fetchall()
                for row in rows:
                    provenance["predicate"]["buildDefinition"]["resolvedDependencies"].append({
                        "uri": f"sbom://{project_id}/{row['id']}",
                        "digest": {"sha256": hashlib.sha256(row["id"].encode()).hexdigest()},
                    })
            except sqlite3.OperationalError:
                pass

        return {
            "project_id": project_id,
            "slsa_level": slsa_level,
            "slsa_level_description": SLSA_LEVEL_REQUIREMENTS[slsa_level]["description"],
            "provenance": provenance,
            "evidence": evidence,
            "evidence_met": sum(1 for v in evidence.values() if v),
            "evidence_total": len(evidence),
            "generated_at": now.isoformat(),
        }
    finally:
        if conn:
            conn.close()


def generate_vex_document(
    project_id: str,
    db_path: Path = None,
) -> dict:
    """Generate VEX (Vulnerability Exploitability eXchange) document.

    Collects vulnerability data from ICDEV databases and produces a
    CycloneDX VEX document mapping vulnerabilities to exploitability status.

    Args:
        project_id: Project identifier.
        db_path: Optional database path override.

    Returns:
        dict with VEX document and vulnerability summary.
    """
    conn = _get_connection(db_path)
    now = datetime.now(timezone.utc)

    try:
        vulnerabilities = []
        vuln_summary = {"total": 0, "not_affected": 0, "affected": 0, "fixed": 0, "under_investigation": 0}

        if conn:
            try:
                # Check vulnerability records
                rows = conn.execute(
                    """SELECT * FROM vulnerability_records
                       WHERE project_id = ?
                       ORDER BY severity DESC, created_at DESC
                       LIMIT 100""",
                    (project_id,),
                ).fetchall()
                for row in rows:
                    status = row["status"] if row["status"] else "under_investigation"
                    severity = row["severity"] if row["severity"] else "unknown"
                    justification = row["justification"] if row["justification"] else ""
                    vuln = {
                        "id": row["id"],
                        "source": {"name": "ICDEV Vulnerability Scanner"},
                        "ratings": [{"severity": severity}],
                        "analysis": {
                            "state": status,
                            "justification": justification,
                        },
                    }
                    vulnerabilities.append(vuln)
                    vuln_summary["total"] += 1
                    if status in vuln_summary:
                        vuln_summary[status] += 1
            except sqlite3.OperationalError:
                pass

            try:
                # Check CVE triage records
                rows = conn.execute(
                    """SELECT * FROM cve_triage
                       WHERE project_id = ?
                       ORDER BY cvss_score DESC
                       LIMIT 50""",
                    (project_id,),
                ).fetchall()
                for row in rows:
                    cve_id = row["cve_id"] if row["cve_id"] else str(uuid.uuid4())
                    cvss_score = row["cvss_score"] if row["cvss_score"] else 0.0
                    triage_decision = row["triage_decision"] if row["triage_decision"] else "under_investigation"
                    vuln = {
                        "id": cve_id,
                        "source": {"name": "ICDEV CVE Triager"},
                        "ratings": [{"score": cvss_score, "method": "CVSSv3"}],
                        "analysis": {
                            "state": triage_decision,
                        },
                    }
                    vulnerabilities.append(vuln)
                    vuln_summary["total"] += 1
            except sqlite3.OperationalError:
                pass

        vex_document = {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "version": 1,
            "serialNumber": f"urn:uuid:{uuid.uuid4()}",
            "metadata": {
                "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "tools": [{"vendor": "ICDEV", "name": "slsa_attestation_generator", "version": "1.0"}],
                "component": {"name": project_id, "type": "application"},
            },
            "vulnerabilities": vulnerabilities,
        }

        return {
            "project_id": project_id,
            "vex_document": vex_document,
            "vulnerability_summary": vuln_summary,
            "generated_at": now.isoformat(),
        }
    finally:
        if conn:
            conn.close()


def verify_slsa_level(
    project_id: str,
    target_level: int = 3,
    db_path: Path = None,
) -> dict:
    """Verify project meets a target SLSA level.

    Args:
        project_id: Project identifier.
        target_level: Target SLSA level (0-4, default 3).
        db_path: Optional database path override.

    Returns:
        dict with verification result, gaps, and recommendations.
    """
    conn = _get_connection(db_path)

    try:
        evidence = _collect_build_evidence(project_id, conn)
        current_level = _determine_slsa_level(evidence)
        target_reqs = SLSA_LEVEL_REQUIREMENTS.get(target_level, SLSA_LEVEL_REQUIREMENTS[3])

        gaps = []
        recommendations = []
        for req in target_reqs["requirements"]:
            if not evidence.get(req, False):
                gaps.append(req)
                rec_map = {
                    "build_process_documented": "Create DevSecOps profile with pipeline audit logging",
                    "version_controlled_source": "Generate SBOM for the project (sbom_generator.py)",
                    "build_service_authenticated": "Enable image signing in DevSecOps profile",
                    "build_as_code": "Enable SBOM attestation in DevSecOps profile",
                    "ephemeral_environment": "Deploy via K8s with ephemeral build pods",
                    "isolated_builds": "Configure isolated CI/CD runners",
                    "hermetic_builds": "Enable hermetic build configuration",
                    "reproducible_builds": "Configure reproducible build settings",
                }
                recommendations.append(rec_map.get(req, f"Address requirement: {req}"))

        return {
            "project_id": project_id,
            "target_level": target_level,
            "current_level": current_level,
            "meets_target": current_level >= target_level,
            "target_description": target_reqs["description"],
            "gaps": gaps,
            "gap_count": len(gaps),
            "recommendations": recommendations,
            "evidence": evidence,
        }
    finally:
        if conn:
            conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="SLSA v1.0 Provenance Generator + VEX Document Generator"
    )
    parser.add_argument("--project-id", required=True, help="Project ID", dest="project_id")
    parser.add_argument("--generate", action="store_true", help="Generate SLSA provenance")
    parser.add_argument("--vex", action="store_true", help="Generate VEX document")
    parser.add_argument("--verify", action="store_true", help="Verify SLSA level")
    parser.add_argument("--target-level", type=int, default=3, help="Target SLSA level (0-4)", dest="target_level")
    parser.add_argument("--db", help="Database path")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    args = parser.parse_args()

    db = Path(args.db) if args.db else None

    if not any([args.generate, args.vex, args.verify]):
        args.generate = True  # Default action

    results = {}

    if args.generate:
        results["provenance"] = generate_slsa_provenance(args.project_id, db_path=db)

    if args.vex:
        results["vex"] = generate_vex_document(args.project_id, db_path=db)

    if args.verify:
        results["verification"] = verify_slsa_level(args.project_id, args.target_level, db_path=db)

    if args.json_output:
        print(json.dumps(results, indent=2, default=str))
    else:
        for key, result in results.items():
            print(f"\n=== {key.upper()} ===")
            if key == "provenance":
                print(f"  SLSA Level: {result['slsa_level']}")
                print(f"  Description: {result['slsa_level_description']}")
                print(f"  Evidence: {result['evidence_met']}/{result['evidence_total']}")
            elif key == "vex":
                s = result["vulnerability_summary"]
                print(f"  Total Vulnerabilities: {s['total']}")
                print(f"  Not Affected: {s['not_affected']}")
                print(f"  Affected: {s['affected']}")
                print(f"  Fixed: {s['fixed']}")
            elif key == "verification":
                print(f"  Target Level: {result['target_level']}")
                print(f"  Current Level: {result['current_level']}")
                print(f"  Meets Target: {result['meets_target']}")
                if result["gaps"]:
                    print(f"  Gaps ({result['gap_count']}):")
                    for gap in result["gaps"]:
                        print(f"    - {gap}")


if __name__ == "__main__":
    main()
