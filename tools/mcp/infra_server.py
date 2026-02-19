#!/usr/bin/env python3
# CUI // SP-CTI
"""Infrastructure MCP server exposing IaC generation, CI/CD pipeline, and deployment tools.

Tools:
    terraform_plan    - Generate Terraform configurations for AWS GovCloud
    terraform_apply   - Apply Terraform configurations (with approval gate)
    ansible_run       - Generate and run Ansible playbooks
    k8s_deploy        - Generate Kubernetes manifests and deploy
    pipeline_generate - Generate GitLab CI/CD pipeline configuration
    rollback          - Rollback a deployment to a previous version

Runs as an MCP server over stdio with Content-Length framing.
"""

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

sys.path.insert(0, str(BASE_DIR))
from tools.mcp.base_server import MCPServer  # noqa: E402


def _import_tool(module_path, func_name):
    """Dynamically import a function. Returns None if unavailable."""
    try:
        import importlib
        mod = importlib.import_module(module_path)
        return getattr(mod, func_name, None)
    except (ImportError, ModuleNotFoundError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def handle_terraform_plan(args: dict) -> dict:
    """Generate Terraform configurations for a project."""
    generate = _import_tool("tools.infra.terraform_generator", "generate_terraform")
    if not generate:
        return {"error": "terraform_generator module not available"}

    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    output_dir = args.get("output_dir")
    modules = args.get("modules", ["vpc", "ecr", "rds"])

    return generate(project_id=project_id, output_dir=output_dir, modules=modules, db_path=str(DB_PATH))


def handle_terraform_apply(args: dict) -> dict:
    """Apply Terraform configurations. Requires explicit approval."""
    approved = args.get("approved", False)
    if not approved:
        return {
            "status": "approval_required",
            "message": "Terraform apply requires explicit approval. Set 'approved: true' to proceed.",
            "warning": "This will create/modify cloud infrastructure in AWS GovCloud.",
        }

    terraform_dir = args.get("terraform_dir")
    if not terraform_dir:
        raise ValueError("'terraform_dir' is required")

    return {
        "status": "delegated",
        "message": "Terraform apply should be executed via GitLab CI/CD pipeline for audit trail compliance.",
        "terraform_dir": terraform_dir,
        "next_step": "Commit the Terraform files and push to trigger the pipeline.",
    }


def handle_ansible_run(args: dict) -> dict:
    """Generate Ansible playbooks for a project."""
    generate = _import_tool("tools.infra.ansible_generator", "generate_ansible")
    if not generate:
        return {"error": "ansible_generator module not available"}

    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    output_dir = args.get("output_dir")
    playbook_type = args.get("playbook_type", "deploy")

    return generate(project_id=project_id, output_dir=output_dir, playbook_type=playbook_type, db_path=str(DB_PATH))


def handle_k8s_deploy(args: dict) -> dict:
    """Generate Kubernetes manifests for a project."""
    generate = _import_tool("tools.infra.k8s_generator", "generate_k8s")
    if not generate:
        return {"error": "k8s_generator module not available"}

    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    output_dir = args.get("output_dir")
    environment = args.get("environment", "staging")

    return generate(project_id=project_id, output_dir=output_dir, environment=environment, db_path=str(DB_PATH))


def handle_pipeline_generate(args: dict) -> dict:
    """Generate a GitLab CI/CD pipeline configuration."""
    generate = _import_tool("tools.infra.pipeline_generator", "generate_pipeline")
    if not generate:
        return {"error": "pipeline_generator module not available"}

    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    output_dir = args.get("output_dir")
    stages = args.get("stages", ["lint", "test", "security-scan", "build", "compliance-check", "deploy-staging", "deploy-prod"])

    return generate(project_id=project_id, output_dir=output_dir, stages=stages, db_path=str(DB_PATH))


def handle_rollback(args: dict) -> dict:
    """Rollback a deployment to a previous version."""
    rollback = _import_tool("tools.infra.rollback", "rollback_deployment")
    if not rollback:
        return {"error": "rollback module not available"}

    deployment_id = args.get("deployment_id")
    if not deployment_id:
        raise ValueError("'deployment_id' is required")

    target_version = args.get("target_version")
    reason = args.get("reason", "Manual rollback")

    return rollback(deployment_id=deployment_id, target_version=target_version, reason=reason, db_path=str(DB_PATH))


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

def create_server() -> MCPServer:
    server = MCPServer(name="icdev-infra", version="1.0.0")

    server.register_tool(
        name="terraform_plan",
        description="Generate Terraform configurations for AWS GovCloud deployment. Produces provider.tf, variables.tf, main.tf with VPC, ECR, and RDS modules.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "UUID of the project"},
                "output_dir": {"type": "string", "description": "Output directory for Terraform files"},
                "modules": {
                    "type": "array",
                    "description": "Terraform modules to generate",
                    "items": {"type": "string", "enum": ["vpc", "ecr", "rds", "s3", "iam"]},
                    "default": ["vpc", "ecr", "rds"],
                },
            },
            "required": ["project_id"],
        },
        handler=handle_terraform_plan,
    )

    server.register_tool(
        name="terraform_apply",
        description="Apply Terraform configurations. Requires explicit approval flag. In production, delegates to GitLab CI/CD pipeline.",
        input_schema={
            "type": "object",
            "properties": {
                "terraform_dir": {"type": "string", "description": "Directory containing Terraform files"},
                "approved": {"type": "boolean", "description": "Explicit approval to apply changes", "default": False},
            },
            "required": ["terraform_dir"],
        },
        handler=handle_terraform_apply,
    )

    server.register_tool(
        name="ansible_run",
        description="Generate Ansible playbooks for server configuration and application deployment.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "UUID of the project"},
                "output_dir": {"type": "string", "description": "Output directory for Ansible files"},
                "playbook_type": {
                    "type": "string",
                    "description": "Type of playbook to generate",
                    "enum": ["deploy", "configure", "harden", "backup"],
                    "default": "deploy",
                },
            },
            "required": ["project_id"],
        },
        handler=handle_ansible_run,
    )

    server.register_tool(
        name="k8s_deploy",
        description="Generate Kubernetes manifests (Deployment, Service, ConfigMap, NetworkPolicy) for a project with security hardening.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "UUID of the project"},
                "output_dir": {"type": "string", "description": "Output directory for K8s manifests"},
                "environment": {
                    "type": "string",
                    "description": "Target environment",
                    "enum": ["staging", "production"],
                    "default": "staging",
                },
            },
            "required": ["project_id"],
        },
        handler=handle_k8s_deploy,
    )

    server.register_tool(
        name="pipeline_generate",
        description="Generate a GitLab CI/CD pipeline (.gitlab-ci.yml) with 7 stages: lint, test, security-scan, build, compliance-check, deploy-staging, deploy-prod.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "UUID of the project"},
                "output_dir": {"type": "string", "description": "Output directory for pipeline config"},
                "stages": {
                    "type": "array",
                    "description": "Pipeline stages to include",
                    "items": {"type": "string"},
                },
            },
            "required": ["project_id"],
        },
        handler=handle_pipeline_generate,
    )

    server.register_tool(
        name="rollback",
        description="Rollback a deployment to a previous version. Records the rollback in audit trail and updates deployment status.",
        input_schema={
            "type": "object",
            "properties": {
                "deployment_id": {"type": "string", "description": "ID of the deployment to rollback"},
                "target_version": {"type": "string", "description": "Version to rollback to (optional, defaults to previous)"},
                "reason": {"type": "string", "description": "Reason for rollback"},
            },
            "required": ["deployment_id"],
        },
        handler=handle_rollback,
    )

    return server


if __name__ == "__main__":
    server = create_server()
    server.run()
