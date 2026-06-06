"""ICDEV™ Studio — Canvas→Workflow Template Mapper.

Pure data module: no Flask, no dashboard imports.
Maps canvas_id → pre-filled workflow dict with steps, description, category.
"""
# CUI // SP-CTI

from __future__ import annotations

CANVAS_WORKFLOW_MAP: dict[str, dict] = {
    "idc": {
        "description": "Infrastructure resource audit and IaC generation",
        "category": "deploy",
        "steps": [
            {"id": "step_1", "name": "Inventory Resources", "tool": "tools/infra_canvas/resource_scanner.py", "node_type": "tool", "depends_on": [], "timeout": 300, "required": True},
            {"id": "step_2", "name": "Generate IaC", "tool": "tools/iac/iac_generator.py", "node_type": "tool", "depends_on": ["step_1"], "timeout": 300, "required": True},
            {"id": "step_3", "name": "Engineer Review", "tool": "", "node_type": "human", "role": "reviewer", "depends_on": ["step_2"], "timeout": 300, "required": True},
        ],
    },
    "ndc": {
        "description": "Network topology discovery and diagram generation",
        "category": "general",
        "steps": [
            {"id": "step_1", "name": "Discover Topology", "tool": "tools/network/topology_scanner.py", "node_type": "tool", "depends_on": [], "timeout": 300, "required": True},
            {"id": "step_2", "name": "Generate Diagram", "tool": "tools/network/diagram_exporter.py", "node_type": "tool", "depends_on": ["step_1"], "timeout": 300, "required": True},
            {"id": "step_3", "name": "Network Engineer Review", "tool": "", "node_type": "human", "role": "reviewer", "depends_on": ["step_2"], "timeout": 300, "required": True},
        ],
    },
    "sdc": {
        "description": "Security posture assessment and STIG remediation",
        "category": "security",
        "steps": [
            {"id": "step_1", "name": "Security Scan", "tool": "tools/security/stig_scanner.py", "node_type": "tool", "depends_on": [], "timeout": 300, "required": True},
            {"id": "step_2", "name": "Review Findings", "tool": "tools/security/findings_reporter.py", "node_type": "tool", "depends_on": ["step_1"], "timeout": 300, "required": True},
            {"id": "step_3", "name": "Generate Remediation Plan", "tool": "tools/security/remediation_planner.py", "node_type": "tool", "depends_on": ["step_2"], "timeout": 300, "required": True},
            {"id": "step_4", "name": "ISSO Approval", "tool": "", "node_type": "human", "role": "isso", "depends_on": ["step_3"], "timeout": 300, "required": True},
        ],
    },
    "bdc": {
        "description": "Boundary diagram and accreditation package generation",
        "category": "compliance",
        "steps": [
            {"id": "step_1", "name": "Boundary Scan", "tool": "tools/boundary/boundary_scanner.py", "node_type": "tool", "depends_on": [], "timeout": 300, "required": True},
            {"id": "step_2", "name": "Generate Diagram", "tool": "tools/boundary/diagram_generator.py", "node_type": "tool", "depends_on": ["step_1"], "timeout": 300, "required": True},
            {"id": "step_3", "name": "Authorization Review", "tool": "", "node_type": "human", "role": "approver", "depends_on": ["step_2"], "timeout": 300, "required": True},
        ],
    },
    "pdc": {
        "description": "CI/CD pipeline validation and deployment",
        "category": "build",
        "steps": [
            {"id": "step_1", "name": "Lint", "tool": "tools/testing/linter.py", "node_type": "tool", "depends_on": [], "timeout": 300, "required": True},
            {"id": "step_2", "name": "Run Tests", "tool": "tools/testing/test_orchestrator.py", "node_type": "tool", "depends_on": ["step_1"], "timeout": 300, "required": True},
            {"id": "step_3", "name": "Security Scan", "tool": "tools/security/security_scanner.py", "node_type": "tool", "depends_on": ["step_2"], "timeout": 300, "required": True},
            {"id": "step_4", "name": "Deploy", "tool": "tools/deploy/deployer.py", "node_type": "tool", "depends_on": ["step_3"], "timeout": 300, "required": True},
        ],
    },
    "odc": {
        "description": "Observability stack — SLO definition and runbook generation",
        "category": "general",
        "steps": [
            {"id": "step_1", "name": "Define SLOs", "tool": "tools/observability/slo_definer.py", "node_type": "tool", "depends_on": [], "timeout": 300, "required": True},
            {"id": "step_2", "name": "Generate Runbook", "tool": "tools/observability/runbook_generator.py", "node_type": "tool", "depends_on": ["step_1"], "timeout": 300, "required": True},
            {"id": "step_3", "name": "Configure Alerts", "tool": "tools/observability/alert_configurator.py", "node_type": "tool", "depends_on": ["step_2"], "timeout": 300, "required": True},
            {"id": "step_4", "name": "On-Call Approve", "tool": "", "node_type": "human", "role": "approver", "depends_on": ["step_3"], "timeout": 300, "required": True},
        ],
    },
    "ddc": {
        "description": "Data infrastructure IaC generation — schema validation, Terraform output, DBA gate",
        "category": "deploy",
        "steps": [
            {"id": "step_1", "name": "Lineage Scan", "tool": "tools/data/lineage_scanner.py", "node_type": "tool", "depends_on": [], "timeout": 300, "required": True},
            {"id": "step_2", "name": "Schema Check", "tool": "tools/data/schema_checker.py", "node_type": "tool", "depends_on": ["step_1"], "timeout": 300, "required": True},
            {"id": "step_3", "name": "Generate IaC", "tool": "tools/data/iac_generator.py", "node_type": "tool", "depends_on": ["step_2"], "timeout": 300, "required": True},
            {"id": "step_4", "name": "DBA Approval", "tool": "", "node_type": "human", "role": "approver", "depends_on": ["step_3"], "timeout": 300, "required": True},
        ],
    },
    "qdc": {
        "description": "Quality gate — test coverage and DORA metrics",
        "category": "testing",
        "steps": [
            {"id": "step_1", "name": "Run Tests", "tool": "tools/testing/test_orchestrator.py", "node_type": "tool", "depends_on": [], "timeout": 300, "required": True},
            {"id": "step_2", "name": "Coverage Report", "tool": "tools/testing/coverage_reporter.py", "node_type": "tool", "depends_on": ["step_1"], "timeout": 300, "required": True},
            {"id": "step_3", "name": "DORA Metrics", "tool": "tools/testing/dora_metrics.py", "node_type": "tool", "depends_on": ["step_2"], "timeout": 300, "required": True},
            {"id": "step_4", "name": "QA Approval", "tool": "", "node_type": "human", "role": "approver", "depends_on": ["step_3"], "timeout": 300, "required": True},
        ],
    },
    "mdc": {
        "description": "Cloud migration wave planning and execution",
        "category": "deploy",
        "steps": [
            {"id": "step_1", "name": "Discovery", "tool": "tools/migration/discovery_scanner.py", "node_type": "tool", "depends_on": [], "timeout": 300, "required": True},
            {"id": "step_2", "name": "Wave Plan", "tool": "tools/migration/wave_planner.py", "node_type": "tool", "depends_on": ["step_1"], "timeout": 300, "required": True},
            {"id": "step_3", "name": "Execute Migration", "tool": "tools/migration/migration_executor.py", "node_type": "tool", "depends_on": ["step_2"], "timeout": 300, "required": True},
            {"id": "step_4", "name": "Validate", "tool": "tools/migration/validator.py", "node_type": "tool", "depends_on": ["step_3"], "timeout": 300, "required": True},
        ],
    },
    "aadc": {
        "description": "Agentic AI workflow — agent chain design and test",
        "category": "general",
        "steps": [
            {"id": "step_1", "name": "Agent Design", "tool": "tools/studio/workflow_editor.py", "node_type": "tool", "depends_on": [], "timeout": 300, "required": True},
            {"id": "step_2", "name": "Safety Review", "tool": "tools/security/ai_safety_checker.py", "node_type": "tool", "depends_on": ["step_1"], "timeout": 300, "required": True},
            {"id": "step_3", "name": "Test Run", "tool": "tools/studio/workflow_runner.py", "node_type": "tool", "depends_on": ["step_2"], "timeout": 300, "required": True},
            {"id": "step_4", "name": "PM Approval", "tool": "", "node_type": "human", "role": "program_manager", "depends_on": ["step_3"], "timeout": 300, "required": True},
        ],
    },
    "aimc": {
        "description": "ML model training, evaluation and deployment",
        "category": "build",
        "steps": [
            {"id": "step_1", "name": "Data Prep", "tool": "tools/ml/data_prep.py", "node_type": "tool", "depends_on": [], "timeout": 300, "required": True},
            {"id": "step_2", "name": "Train Model", "tool": "tools/ml/model_trainer.py", "node_type": "tool", "depends_on": ["step_1"], "timeout": 300, "required": True},
            {"id": "step_3", "name": "Evaluate", "tool": "tools/ml/model_evaluator.py", "node_type": "tool", "depends_on": ["step_2"], "timeout": 300, "required": True},
            {"id": "step_4", "name": "Deploy", "tool": "tools/ml/model_deployer.py", "node_type": "tool", "depends_on": ["step_3"], "timeout": 300, "required": True},
            {"id": "step_5", "name": "Monitor", "tool": "tools/ml/model_monitor.py", "node_type": "tool", "depends_on": ["step_4"], "timeout": 300, "required": True},
        ],
    },
    "ohc": {
        "description": "Operations runbook automation and incident response",
        "category": "general",
        "steps": [
            {"id": "step_1", "name": "Generate Runbook", "tool": "tools/ops/runbook_generator.py", "node_type": "tool", "depends_on": [], "timeout": 300, "required": True},
            {"id": "step_2", "name": "SLA Check", "tool": "tools/ops/sla_checker.py", "node_type": "tool", "depends_on": ["step_1"], "timeout": 300, "required": True},
            {"id": "step_3", "name": "Deploy Runbook", "tool": "tools/ops/runbook_deployer.py", "node_type": "tool", "depends_on": ["step_2"], "timeout": 300, "required": True},
            {"id": "step_4", "name": "Ops Approval", "tool": "", "node_type": "human", "role": "approver", "depends_on": ["step_3"], "timeout": 300, "required": True},
        ],
    },
}


def get_canvas_workflow_template(canvas_id: str) -> dict:
    """Return pre-filled workflow dict for a canvas_id, or a generic fallback."""
    entry = CANVAS_WORKFLOW_MAP.get(canvas_id)
    if entry is None:
        return {
            "description": f"{canvas_id.upper()} workflow",
            "category": "general",
            "steps": [
                {"id": "step_1", "name": "Execute", "tool": "", "node_type": "tool", "depends_on": [], "timeout": 300, "required": True},
                {"id": "step_2", "name": "Review", "tool": "", "node_type": "human", "role": "reviewer", "depends_on": ["step_1"], "timeout": 300, "required": True},
            ],
        }
    import copy
    return copy.deepcopy(entry)


def list_canvas_ids() -> list[str]:
    """Return all supported canvas_ids."""
    return list(CANVAS_WORKFLOW_MAP.keys())
