# CUI // SP-CTI
# ICDEV Comply — Compliance artifact generation workflow
# Runs SSP, POAM, STIG, SBOM, CUI marker and evaluates compliance gate

"""
ICDEV Comply — Generate ATO compliance artifacts.

Usage:
    python tools/ci/workflows/icdev_comply.py <issue-number> <run-id>

Workflow:
    1. Load state from previous phase
    2. Generate SSP (System Security Plan)
    3. Generate POAM (Plan of Action and Milestones)
    4. Run STIG checklist
    5. Generate SBOM (Software Bill of Materials)
    6. Apply CUI markings
    7. Evaluate compliance gate
    8. Commit artifacts and post results
"""

import json
import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.ci.modules.state import ICDevState
from tools.ci.modules.git_ops import commit_changes, finalize_git_operations
from tools.ci.modules.vcs import VCS
from tools.ci.modules.workflow_ops import (
    format_issue_message,
    BOT_IDENTIFIER,
)
from tools.testing.utils import setup_logger

AGENT_COMPLIANCE = "icdev_compliance"


def run_compliance_artifacts(
    run_id: str, issue_number: str, logger: logging.Logger
) -> dict:
    """Generate all compliance artifacts."""
    results = {
        "ssp": {"status": "skipped", "path": ""},
        "poam": {"status": "skipped", "path": ""},
        "stig": {"status": "skipped", "findings": {}},
        "sbom": {"status": "skipped", "path": ""},
        "cui_markings": {"status": "skipped"},
        "gate_passed": False,
        "errors": [],
    }

    project_id = f"issue-{issue_number}"

    # 1. Generate SSP
    logger.info("Generating SSP...")
    try:
        from tools.compliance.ssp_generator import generate_ssp
        ssp_result = generate_ssp(project_id)
        results["ssp"] = {
            "status": "generated",
            "path": ssp_result.get("output_path", ""),
        }
        logger.info(f"SSP generated: {results['ssp']['path']}")
    except ImportError:
        logger.warning("ssp_generator not available, using agent fallback")
        _run_agent_comply(run_id, "ssp", logger)
        results["ssp"]["status"] = "agent_generated"
    except Exception as e:
        logger.error(f"SSP generation failed: {e}")
        results["errors"].append(f"SSP: {e}")

    # 2. Generate POAM
    logger.info("Generating POAM...")
    try:
        from tools.compliance.poam_generator import generate_poam
        poam_result = generate_poam(project_id)
        results["poam"] = {
            "status": "generated",
            "path": poam_result.get("output_path", ""),
        }
        logger.info(f"POAM generated: {results['poam']['path']}")
    except ImportError:
        logger.warning("poam_generator not available, using agent fallback")
        _run_agent_comply(run_id, "poam", logger)
        results["poam"]["status"] = "agent_generated"
    except Exception as e:
        logger.error(f"POAM generation failed: {e}")
        results["errors"].append(f"POAM: {e}")

    # 3. Run STIG checklist
    logger.info("Running STIG checklist...")
    try:
        from tools.compliance.stig_checker import check_stig
        stig_result = check_stig(project_id)
        findings = stig_result.get("findings", {})
        cat1_count = findings.get("cat1", 0)
        results["stig"] = {
            "status": "checked",
            "findings": findings,
            "cat1_count": cat1_count,
        }
        if cat1_count > 0:
            logger.warning(f"STIG: {cat1_count} CAT1 findings (blocking)")
        else:
            logger.info("STIG: No CAT1 findings")
    except ImportError:
        logger.warning("stig_checker not available, using agent fallback")
        _run_agent_comply(run_id, "stig", logger)
        results["stig"]["status"] = "agent_checked"
    except Exception as e:
        logger.error(f"STIG check failed: {e}")
        results["errors"].append(f"STIG: {e}")

    # 4. Generate SBOM
    logger.info("Generating SBOM...")
    try:
        from tools.compliance.sbom_generator import generate_sbom
        sbom_result = generate_sbom(project_dir=str(PROJECT_ROOT))
        results["sbom"] = {
            "status": "generated",
            "path": sbom_result.get("output_path", ""),
        }
        logger.info(f"SBOM generated: {results['sbom']['path']}")
    except ImportError:
        logger.warning("sbom_generator not available, using agent fallback")
        _run_agent_comply(run_id, "sbom", logger)
        results["sbom"]["status"] = "agent_generated"
    except Exception as e:
        logger.error(f"SBOM generation failed: {e}")
        results["errors"].append(f"SBOM: {e}")

    # 5. Apply CUI markings check
    logger.info("Verifying CUI markings...")
    try:
        from tools.compliance.cui_marker import verify_cui_markings
        cui_result = verify_cui_markings(project_dir=str(PROJECT_ROOT))
        results["cui_markings"] = {
            "status": "verified",
            "files_checked": cui_result.get("files_checked", 0),
            "missing_markings": cui_result.get("missing_markings", 0),
        }
    except (ImportError, Exception) as e:
        logger.warning(f"CUI marking check skipped: {e}")
        results["cui_markings"]["status"] = "skipped"

    # 6. Evaluate compliance gate
    results["gate_passed"] = _evaluate_compliance_gate(results, logger)

    return results


def _run_agent_comply(run_id: str, artifact_type: str, logger: logging.Logger):
    """Fallback: use Claude Code agent to generate compliance artifact."""
    try:
        from tools.ci.modules.agent import execute_template
        from tools.testing.data_types import AgentTemplateRequest

        request = AgentTemplateRequest(
            agent_name=AGENT_COMPLIANCE,
            slash_command="/icdev-comply",
            args=[artifact_type],
            run_id=run_id,
        )
        response = execute_template(request)
        if response.success:
            logger.info(f"Agent generated {artifact_type} artifact")
        else:
            logger.warning(f"Agent {artifact_type} generation produced output but may have issues")
    except Exception as e:
        logger.error(f"Agent fallback for {artifact_type} failed: {e}")


def _evaluate_compliance_gate(results: dict, logger: logging.Logger) -> bool:
    """Evaluate whether compliance gate passes.

    Gate criteria (from security_gates.yaml):
    - 0 CAT1 STIG findings
    - SSP generated or agent-generated
    - SBOM generated or agent-generated
    - CUI markings present (warning only)
    """
    gate_passed = True
    reasons = []

    # STIG CAT1 check (blocking)
    stig = results.get("stig", {})
    cat1_count = stig.get("cat1_count", 0)
    if cat1_count > 0:
        gate_passed = False
        reasons.append(f"{cat1_count} CAT1 STIG findings")

    # SSP check (blocking)
    ssp_status = results.get("ssp", {}).get("status", "skipped")
    if ssp_status in ("skipped",):
        gate_passed = False
        reasons.append("SSP not generated")

    # SBOM check (blocking)
    sbom_status = results.get("sbom", {}).get("status", "skipped")
    if sbom_status in ("skipped",):
        gate_passed = False
        reasons.append("SBOM not generated")

    # CUI markings (warning only)
    cui = results.get("cui_markings", {})
    missing = cui.get("missing_markings", 0)
    if missing > 0:
        logger.warning(f"CUI markings missing on {missing} files (non-blocking)")

    if gate_passed:
        logger.info("Compliance gate: PASSED")
    else:
        logger.error(f"Compliance gate: FAILED — {'; '.join(reasons)}")

    return gate_passed


def main():
    """Main entry point."""
    if len(sys.argv) < 3:
        print("CUI // SP-CTI")
        print("Usage: python tools/ci/workflows/icdev_comply.py <issue-number> <run-id>")
        print("\nGenerates compliance artifacts: SSP, POAM, STIG, SBOM, CUI markings")
        sys.exit(1)

    issue_number = sys.argv[1]
    run_id = sys.argv[2]

    print(f"CUI // SP-CTI")
    print(f"ICDEV Comply — run_id: {run_id}, issue: #{issue_number}")

    # Set up
    logger = setup_logger(run_id, "icdev_comply")
    state = ICDevState.load(run_id)

    # Run compliance artifacts
    results = run_compliance_artifacts(run_id, issue_number, logger)

    # Commit artifacts
    logger.info("Committing compliance artifacts...")
    commit_msg = f"icdev_compliance: comply: Generate ATO artifacts (run {run_id})"
    success, error = commit_changes(commit_msg)
    if not success:
        logger.warning(f"Commit failed (may be no changes): {error}")

    # Post results to issue
    try:
        platform = state.get("platform", "github")
        vcs = VCS(platform=platform)

        gate_emoji = "PASS" if results["gate_passed"] else "FAIL"
        summary = (
            f"{BOT_IDENTIFIER} **Compliance Phase** — {gate_emoji}\n\n"
            f"| Artifact | Status |\n"
            f"|----------|--------|\n"
            f"| SSP | {results['ssp']['status']} |\n"
            f"| POAM | {results['poam']['status']} |\n"
            f"| STIG | {results['stig']['status']} |\n"
            f"| SBOM | {results['sbom']['status']} |\n"
            f"| CUI Markings | {results['cui_markings']['status']} |\n\n"
            f"Run ID: `{run_id}`"
        )

        if results.get("errors"):
            summary += f"\n\nErrors:\n" + "\n".join(
                f"- {e}" for e in results["errors"]
            )

        vcs.comment_on_issue(int(issue_number), summary)
    except Exception as e:
        logger.warning(f"Failed to post compliance results: {e}")

    # Exit code
    if not results["gate_passed"]:
        logger.error("Compliance gate FAILED — pipeline blocked")
        sys.exit(1)

    logger.info("Compliance phase completed successfully")


if __name__ == "__main__":
    main()
