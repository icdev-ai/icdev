#!/usr/bin/env python3
# CUI // SP-CTI
"""DevSecOps & Zero Trust Architecture MCP server.

Tools:
    devsecops_profile_create  - Create/update DevSecOps profile for a project
    devsecops_profile_get     - Get project's current DevSecOps profile
    devsecops_maturity_assess - Assess DevSecOps maturity level
    zta_maturity_score        - Score ZTA maturity across 7 pillars
    zta_assess                - Run NIST 800-207 ZTA assessment
    pipeline_security_generate - Generate profile-driven pipeline security stages
    policy_generate           - Generate Kyverno/OPA admission policies
    service_mesh_generate     - Generate Istio/Linkerd service mesh configs
    network_segmentation_generate - Generate ZTA micro-segmentation policies
    attestation_verify        - Verify image/SBOM attestations
    zta_posture_check         - Check ZTA posture for cATO readiness
    pdp_config_generate       - Generate PDP/PEP configuration

Runs as an MCP server over stdio with Content-Length framing.
"""

import json
import os
import sys
import traceback
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

sys.path.insert(0, str(BASE_DIR))
from tools.mcp.base_server import MCPServer  # noqa: E402


# ---------------------------------------------------------------------------
# Lazy tool imports
# ---------------------------------------------------------------------------

def _import_tool(module_path, func_name):
    """Dynamically import a function from a module."""
    try:
        import importlib
        mod = importlib.import_module(module_path)
        return getattr(mod, func_name, None)
    except (ImportError, ModuleNotFoundError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def handle_devsecops_profile_create(args: dict) -> dict:
    """Create or update a DevSecOps profile for a project."""
    create = _import_tool("tools.devsecops.profile_manager", "create_profile")
    if not create:
        return {"error": "profile_manager not available"}
    project_id = args.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}
    return create(
        project_id,
        maturity_level=args.get("maturity_level"),
        stages=args.get("stages"),
        stage_configs=args.get("stage_configs"),
    )


def handle_devsecops_profile_get(args: dict) -> dict:
    """Get project's current DevSecOps profile."""
    get = _import_tool("tools.devsecops.profile_manager", "get_profile")
    if not get:
        return {"error": "profile_manager not available"}
    project_id = args.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}
    return get(project_id)


def handle_devsecops_maturity_assess(args: dict) -> dict:
    """Assess DevSecOps maturity level and gaps."""
    assess = _import_tool("tools.devsecops.profile_manager", "assess_maturity")
    if not assess:
        return {"error": "profile_manager not available"}
    project_id = args.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}
    return assess(project_id)


def handle_zta_maturity_score(args: dict) -> dict:
    """Score ZTA maturity across 7 pillars."""
    pillar = args.get("pillar")
    project_id = args.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}

    if pillar:
        score_fn = _import_tool("tools.devsecops.zta_maturity_scorer", "score_pillar")
        if not score_fn:
            return {"error": "zta_maturity_scorer not available"}
        return score_fn(project_id, pillar)
    else:
        score_all = _import_tool("tools.devsecops.zta_maturity_scorer", "score_all_pillars")
        if not score_all:
            return {"error": "zta_maturity_scorer not available"}
        return score_all(project_id)


def handle_zta_assess(args: dict) -> dict:
    """Run NIST 800-207 ZTA assessment."""
    try:
        from tools.compliance.nist_800_207_assessor import NIST800207Assessor
        assessor = NIST800207Assessor()
        project_id = args.get("project_id")
        if not project_id:
            return {"error": "project_id is required"}
        if args.get("gate"):
            return assessor.evaluate_gate(project_id)
        return assessor.assess(project_id, project_dir=args.get("project_dir"))
    except Exception as e:
        return {"error": f"NIST 800-207 assessor error: {e}"}


def handle_pipeline_security_generate(args: dict) -> dict:
    """Generate profile-driven pipeline security stages."""
    gen = _import_tool("tools.devsecops.pipeline_security_generator", "generate_security_stages")
    if not gen:
        return {"error": "pipeline_security_generator not available"}
    project_id = args.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}
    return gen(project_id)


def handle_policy_generate(args: dict) -> dict:
    """Generate Kyverno or OPA admission policies."""
    engine = args.get("engine", "kyverno")
    project_id = args.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}

    if engine == "kyverno":
        gen = _import_tool("tools.devsecops.policy_generator", "generate_kyverno_policies")
    else:
        gen = _import_tool("tools.devsecops.policy_generator", "generate_opa_policies")

    if not gen:
        return {"error": "policy_generator not available"}
    return gen(project_id)


def handle_service_mesh_generate(args: dict) -> dict:
    """Generate Istio or Linkerd service mesh configs."""
    mesh = args.get("mesh", "istio")
    project_id = args.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}

    if mesh == "istio":
        gen = _import_tool("tools.devsecops.service_mesh_generator", "generate_istio_config")
    else:
        gen = _import_tool("tools.devsecops.service_mesh_generator", "generate_linkerd_config")

    if not gen:
        return {"error": "service_mesh_generator not available"}
    return gen(project_id)


def handle_network_segmentation_generate(args: dict) -> dict:
    """Generate ZTA micro-segmentation network policies."""
    project_path = args.get("project_path")
    namespaces = args.get("namespaces", [])

    if args.get("microsegmentation"):
        gen = _import_tool("tools.devsecops.network_segmentation_generator", "generate_microsegmentation")
        if not gen:
            return {"error": "network_segmentation_generator not available"}
        services = args.get("services", [])
        return gen(project_path or "/tmp", services)
    else:
        gen = _import_tool("tools.devsecops.network_segmentation_generator", "generate_namespace_isolation")
        if not gen:
            return {"error": "network_segmentation_generator not available"}
        return gen(project_path or "/tmp", namespaces)


def handle_attestation_verify(args: dict) -> dict:
    """Verify image/SBOM attestations."""
    verify = _import_tool("tools.devsecops.attestation_manager", "verify_attestation")
    if not verify:
        return {"error": "attestation_manager not available"}
    project_id = args.get("project_id")
    image = args.get("image")
    if not project_id or not image:
        return {"error": "project_id and image are required"}
    return verify(project_id, image)


def handle_zta_posture_check(args: dict) -> dict:
    """Check ZTA posture for cATO readiness."""
    project_id = args.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}

    # Get ZTA maturity score
    score_all = _import_tool("tools.devsecops.zta_maturity_scorer", "score_all_pillars")
    if not score_all:
        return {"error": "zta_maturity_scorer not available"}

    result = score_all(project_id)
    posture = {
        "project_id": project_id,
        "overall_score": result.get("overall_score", 0),
        "overall_maturity": result.get("overall_maturity", "traditional"),
        "pillar_scores": result.get("pillar_scores", {}),
        "weakest_pillars": result.get("weakest_pillars", []),
        "cato_ready": result.get("overall_score", 0) >= 0.34,
        "recommendation": result.get("recommendation", ""),
    }
    return posture


def handle_pdp_config_generate(args: dict) -> dict:
    """Generate PDP/PEP configuration."""
    project_id = args.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}

    if args.get("pep"):
        gen = _import_tool("tools.devsecops.pdp_config_generator", "generate_pep_config")
        if not gen:
            return {"error": "pdp_config_generator not available"}
        return gen(project_id, mesh=args.get("mesh", "istio"),
                   pdp_type=args.get("pdp_type", "disa_icam"))
    else:
        gen = _import_tool("tools.devsecops.pdp_config_generator", "generate_pdp_reference")
        if not gen:
            return {"error": "pdp_config_generator not available"}
        return gen(project_id, pdp_type=args.get("pdp_type", "disa_icam"))


# ---------------------------------------------------------------------------
# Server registration
# ---------------------------------------------------------------------------

def create_server() -> MCPServer:
    server = MCPServer(name="icdev-devsecops", version="1.0.0")

    server.register_tool(
        name="devsecops_profile_create",
        description="Create or update a DevSecOps profile for a project. Sets maturity level and active pipeline security stages.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project identifier"},
                "maturity_level": {"type": "string", "description": "Target maturity level (level_1_initial through level_5_optimized)"},
                "stages": {"type": "array", "items": {"type": "string"}, "description": "Explicit list of active stage IDs"},
            },
            "required": ["project_id"],
        },
        handler=handle_devsecops_profile_create,
    )

    server.register_tool(
        name="devsecops_profile_get",
        description="Get a project's current DevSecOps profile including maturity level, active stages, and stage configurations.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project identifier"},
            },
            "required": ["project_id"],
        },
        handler=handle_devsecops_profile_get,
    )

    server.register_tool(
        name="devsecops_maturity_assess",
        description="Assess DevSecOps maturity level, identify gaps for next level, and provide recommendations.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project identifier"},
            },
            "required": ["project_id"],
        },
        handler=handle_devsecops_maturity_assess,
    )

    server.register_tool(
        name="zta_maturity_score",
        description="Score ZTA maturity across all 7 DoD pillars (User Identity, Device, Network, Application/Workload, Data, Visibility/Analytics, Automation/Orchestration). Returns per-pillar scores and weighted aggregate.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project identifier"},
                "pillar": {"type": "string", "description": "Score a specific pillar (optional — omit for all 7)"},
            },
            "required": ["project_id"],
        },
        handler=handle_zta_maturity_score,
    )

    server.register_tool(
        name="zta_assess",
        description="Run NIST SP 800-207 Zero Trust Architecture assessment. Evaluates ZTA requirements against project artifacts and crosswalks to NIST 800-53.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project identifier"},
                "project_dir": {"type": "string", "description": "Path to project source code for automated checks"},
                "gate": {"type": "boolean", "description": "Evaluate gate pass/fail only"},
            },
            "required": ["project_id"],
        },
        handler=handle_zta_assess,
    )

    server.register_tool(
        name="pipeline_security_generate",
        description="Generate GitLab CI security stages based on the project's DevSecOps profile. Only includes stages active in the profile.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project identifier"},
            },
            "required": ["project_id"],
        },
        handler=handle_pipeline_security_generate,
    )

    server.register_tool(
        name="policy_generate",
        description="Generate Kyverno or OPA/Gatekeeper admission policies for pod security, image registry restriction, label enforcement, and resource limits.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project identifier"},
                "engine": {"type": "string", "enum": ["kyverno", "opa"], "description": "Policy engine (default: kyverno)"},
            },
            "required": ["project_id"],
        },
        handler=handle_policy_generate,
    )

    server.register_tool(
        name="service_mesh_generate",
        description="Generate Istio or Linkerd service mesh configurations including mTLS, authorization policies, traffic routing, and circuit breaking.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project identifier"},
                "mesh": {"type": "string", "enum": ["istio", "linkerd"], "description": "Service mesh type (default: istio)"},
            },
            "required": ["project_id"],
        },
        handler=handle_service_mesh_generate,
    )

    server.register_tool(
        name="network_segmentation_generate",
        description="Generate Kubernetes NetworkPolicy manifests for ZTA micro-segmentation: default-deny per namespace, DNS exceptions, per-service policies.",
        input_schema={
            "type": "object",
            "properties": {
                "project_path": {"type": "string", "description": "Target project directory"},
                "namespaces": {"type": "array", "items": {"type": "string"}, "description": "Namespace names for isolation policies"},
                "microsegmentation": {"type": "boolean", "description": "Generate per-service micro-segmentation policies"},
                "services": {"type": "array", "items": {"type": "string"}, "description": "Service names for micro-segmentation"},
            },
        },
        handler=handle_network_segmentation_generate,
    )

    server.register_tool(
        name="attestation_verify",
        description="Verify image signing and SBOM attestations. Returns verification commands for cosign CLI.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project identifier"},
                "image": {"type": "string", "description": "Container image reference (e.g., registry/app:v1.0)"},
            },
            "required": ["project_id", "image"],
        },
        handler=handle_attestation_verify,
    )

    server.register_tool(
        name="zta_posture_check",
        description="Check ZTA posture for cATO readiness. Returns overall maturity score, per-pillar scores, and whether the project meets minimum ZTA requirements for continuous authorization.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project identifier"},
            },
            "required": ["project_id"],
        },
        handler=handle_zta_posture_check,
    )

    server.register_tool(
        name="pdp_config_generate",
        description="Generate PDP (Policy Decision Point) reference documentation and PEP (Policy Enforcement Point) configs for Istio/Linkerd pointing to external identity/access providers.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project identifier"},
                "pdp_type": {"type": "string", "description": "PDP provider type (disa_icam, zscaler, palo_alto_prisma, crowdstrike, microsoft_entra, custom)"},
                "pep": {"type": "boolean", "description": "Generate PEP config instead of PDP reference"},
                "mesh": {"type": "string", "enum": ["istio", "linkerd"], "description": "Service mesh for PEP generation"},
            },
            "required": ["project_id"],
        },
        handler=handle_pdp_config_generate,
    )

    return server


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    server = create_server()
    server.run()


if __name__ == "__main__":
    main()
