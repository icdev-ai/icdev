# CUI // SP-CTI
"""Pipeline Design Canvas — Export Engine.

Generates pipeline definitions from canvas graph:
- GitLab CI YAML
- GitHub Actions YAML
- Jenkinsfile (declarative)
- Tekton Pipeline YAML
- Azure Pipelines YAML
- draw.io XML
- SVG diagram

For Terraform, Ansible, and OSCAL export, delegates to existing ICDEV modules:
- tools/infra/terraform_generator*.py
- tools/infra/ansible_generator.py
- tools/compliance/oscal_generator.py
"""

from datetime import datetime, timezone

from tools.pipeline.constants import PIPELINE_STAGES


def export_pipeline(graph, name, fmt):
    """Export a pipeline graph to the specified format.

    Args:
        graph: dict with 'nodes' and 'edges' lists
        name: pipeline name
        fmt: export format string

    Returns:
        dict with 'format', 'filename', 'content'
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    safe_name = name.replace(" ", "_").lower()

    if fmt == "gitlab_ci":
        content = _to_gitlab_ci(nodes, edges, name)
        return {"format": fmt, "filename": f"{safe_name}.gitlab-ci.yml", "content": content}

    if fmt == "github_actions":
        content = _to_github_actions(nodes, edges, name)
        return {"format": fmt, "filename": f"{safe_name}-ci.yml", "content": content}

    if fmt == "jenkinsfile":
        content = _to_jenkinsfile(nodes, edges, name)
        return {"format": fmt, "filename": "Jenkinsfile", "content": content}

    if fmt == "tekton":
        content = _to_tekton(nodes, edges, name)
        return {"format": fmt, "filename": f"{safe_name}-pipeline.yaml", "content": content}

    if fmt == "azure_pipelines":
        content = _to_azure_pipelines(nodes, edges, name)
        return {"format": fmt, "filename": "azure-pipelines.yml", "content": content}

    if fmt == "drawio":
        content = _to_drawio(nodes, edges, name)
        return {"format": fmt, "filename": f"{safe_name}.drawio", "content": content}

    if fmt == "svg":
        content = _to_svg(nodes, edges, name)
        return {"format": fmt, "filename": f"{safe_name}.svg", "content": content}

    if fmt == "csv":
        content = _to_csv(nodes, edges)
        return {"format": fmt, "filename": f"{safe_name}_inventory.csv", "content": content}

    if fmt == "cloudformation":
        content = _to_cloudformation(nodes, edges, name)
        return {"format": fmt, "filename": f"{safe_name}-cfn.yaml", "content": content}

    if fmt == "bicep":
        content = _to_bicep(nodes, edges, name)
        return {"format": fmt, "filename": f"{safe_name}.bicep", "content": content}

    if fmt == "openslo":
        content = _to_openslo(nodes, edges, name)
        return {"format": fmt, "filename": f"{safe_name}-slos.yaml", "content": content}

    if fmt == "runbook_manifest":
        content = _to_runbook_manifest(nodes, edges, name)
        return {"format": fmt, "filename": f"{safe_name}-runbooks.yaml", "content": content}

    raise ValueError(f"Unknown export format: {fmt}")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _group_by_stage(nodes):
    """Group nodes by their pipeline stage."""
    stages = {}
    for node in nodes:
        stage = node.get("stage") or _infer_stage(node.get("type", ""))
        stages.setdefault(stage, []).append(node)
    return stages


def _infer_stage(node_type):
    """Infer pipeline stage from node type prefix."""
    prefix_map = {
        "scm-": "source",
        "branch-": "source",
        "commit-": "source",
        "build-": "build",
        "cicd-": "build",
        "scan-": "test",
        "sbom-": "build",
        "registry-": "package",
        "sign-": "package",
        "attest-": "package",
        "policy-": "policy_gate",
        "gate-": "policy_gate",
        "gcp-binary": "policy_gate",
        "deploy-": "deploy_prod",
        "k8s-": "deploy_prod",
        "aws-eks": "deploy_prod",
        "az-aks": "deploy_prod",
        "gcp-gke": "deploy_prod",
        "oci-oke": "deploy_prod",
        "openshift": "deploy_prod",
        "rke2": "deploy_prod",
        "mesh-": "deploy_prod",
        "mon-": "monitor",
        "aws-cloudwatch": "monitor",
        "az-monitor": "monitor",
        "comp-": "compliance",
    }
    for prefix, stage in prefix_map.items():
        if node_type.startswith(prefix):
            return stage
    return "build"


def _build_adjacency(edges):
    """Build adjacency list from edges."""
    adj = {}
    for e in edges:
        adj.setdefault(e["source"], []).append(e["target"])
    return adj


def _get_stage_order(stage_key):
    return PIPELINE_STAGES.get(stage_key, {}).get("order", 99)


# ── GitLab CI ─────────────────────────────────────────────────────────────────


def _to_gitlab_ci(nodes, edges, name):
    stages_map = _group_by_stage(nodes)
    ordered = sorted(stages_map.keys(), key=_get_stage_order)

    lines = [
        f"# Auto-generated from Pipeline Design Canvas: {name}",
        f"# Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "stages:",
    ]
    for s in ordered:
        lines.append(f"  - {s}")
    lines.append("")

    for stage_key in ordered:
        stage_nodes = stages_map[stage_key]
        for node in stage_nodes:
            job_name = node.get("label", node.get("type", "job")).replace(" ", "_").lower()
            lines.extend(
                [
                    f"{job_name}:",
                    f"  stage: {stage_key}",
                    f"  # Tool: {node.get('type', 'unknown')}",
                    "  script:",
                    f'    - echo "Running {node.get("label", "step")}"',
                ]
            )
            config = node.get("config") or {}
            if config.get("tool"):
                lines.append(f"    # Tool config: {config['tool']}")
            lines.append("")

    return "\n".join(lines)


# ── GitHub Actions ────────────────────────────────────────────────────────────


def _to_github_actions(nodes, edges, name):
    stages_map = _group_by_stage(nodes)
    ordered = sorted(stages_map.keys(), key=_get_stage_order)

    lines = [
        f"# Auto-generated from Pipeline Design Canvas: {name}",
        f"name: {name}",
        "",
        "on:",
        "  push:",
        "    branches: [main]",
        "  pull_request:",
        "    branches: [main]",
        "",
        "jobs:",
    ]

    prev_jobs = []
    for stage_key in ordered:
        stage_nodes = stages_map[stage_key]
        job_id = stage_key.replace("_", "-")
        job_name = PIPELINE_STAGES.get(stage_key, {}).get("label", stage_key)
        lines.extend(
            [
                f"  {job_id}:",
                f"    name: {job_name}",
                "    runs-on: ubuntu-latest",
            ]
        )
        if prev_jobs:
            lines.append(f"    needs: [{', '.join(prev_jobs)}]")
        lines.append("    steps:")
        lines.append("      - uses: actions/checkout@v4")
        for node in stage_nodes:
            lines.extend(
                [
                    f"      - name: {node.get('label', 'Step')}",
                    f'        run: echo "Running {node.get("type", "tool")}"',
                ]
            )
        lines.append("")
        prev_jobs = [job_id]

    return "\n".join(lines)


# ── Jenkinsfile ───────────────────────────────────────────────────────────────


def _to_jenkinsfile(nodes, edges, name):
    stages_map = _group_by_stage(nodes)
    ordered = sorted(stages_map.keys(), key=_get_stage_order)

    lines = [
        f"// Auto-generated from Pipeline Design Canvas: {name}",
        "pipeline {",
        "    agent any",
        "",
        "    stages {",
    ]

    for stage_key in ordered:
        stage_nodes = stages_map[stage_key]
        stage_label = PIPELINE_STAGES.get(stage_key, {}).get("label", stage_key)
        lines.extend(
            [
                f"        stage('{stage_label}') {{",
                "            steps {",
            ]
        )
        for node in stage_nodes:
            node_label = node.get("label", "step")
            node_type = node.get("type", "")
            lines.append(f"                echo 'Running {node_label} ({node_type})'")
        lines.extend(
            [
                "            }",
                "        }",
            ]
        )

    lines.extend(
        [
            "    }",
            "",
            "    post {",
            "        always {",
            "            echo 'Pipeline complete'",
            "        }",
            "    }",
            "}",
        ]
    )
    return "\n".join(lines)


# ── Tekton ────────────────────────────────────────────────────────────────────


def _to_tekton(nodes, edges, name):
    safe = name.replace(" ", "-").lower()
    stages_map = _group_by_stage(nodes)
    ordered = sorted(stages_map.keys(), key=_get_stage_order)

    lines = [
        f"# Auto-generated Tekton Pipeline: {name}",
        "apiVersion: tekton.dev/v1",
        "kind: Pipeline",
        "metadata:",
        f"  name: {safe}",
        "spec:",
        "  tasks:",
    ]

    prev_task = None
    for stage_key in ordered:
        task_name = stage_key.replace("_", "-")
        lines.extend(
            [
                f"    - name: {task_name}",
                "      taskRef:",
                f"        name: {task_name}-task",
            ]
        )
        if prev_task:
            lines.append(f'      runAfter: ["{prev_task}"]')
        lines.append(f"      # Nodes: {', '.join(n.get('label', '') for n in stages_map[stage_key])}")
        prev_task = task_name

    return "\n".join(lines)


# ── Azure Pipelines ──────────────────────────────────────────────────────────


def _to_azure_pipelines(nodes, edges, name):
    stages_map = _group_by_stage(nodes)
    ordered = sorted(stages_map.keys(), key=_get_stage_order)

    lines = [
        f"# Auto-generated Azure Pipelines: {name}",
        "trigger:",
        "  - main",
        "",
        "pool:",
        "  vmImage: 'ubuntu-latest'",
        "",
        "stages:",
    ]

    for stage_key in ordered:
        stage_label = PIPELINE_STAGES.get(stage_key, {}).get("label", stage_key)
        stage_id = stage_key.replace("_", "")
        lines.extend(
            [
                f"  - stage: {stage_id}",
                f"    displayName: '{stage_label}'",
                "    jobs:",
                f"      - job: {stage_id}_job",
                "        steps:",
            ]
        )
        for node in stages_map[stage_key]:
            n_label = node.get("label", "Step")
            lines.extend(
                [
                    f"          - script: echo 'Running {n_label}'",
                    f"            displayName: '{n_label}'",
                ]
            )
        lines.append("")

    return "\n".join(lines)


# ── draw.io XML ──────────────────────────────────────────────────────────────


def _to_drawio(nodes, edges, name):
    cells = ['<?xml version="1.0" encoding="UTF-8"?>']
    cells.append(f'<mxfile host="pipeline-canvas" modified="{datetime.now(timezone.utc).isoformat()}">')
    cells.append(f'  <diagram name="{name}" id="pipeline">')
    cells.append("    <mxGraphModel>")
    cells.append("      <root>")
    cells.append('        <mxCell id="0"/>')
    cells.append('        <mxCell id="1" parent="0"/>')

    for i, node in enumerate(nodes, 2):
        x = node.get("x", i * 150)
        y = node.get("y", 200)
        label = node.get("label", node.get("type", ""))
        cells.append(
            f'        <mxCell id="{node.get("id", i)}" value="{label}" '
            f'style="rounded=1;whiteSpace=wrap;" vertex="1" parent="1">'
            f'<mxGeometry x="{x}" y="{y}" width="120" height="60" as="geometry"/></mxCell>'
        )

    for j, edge in enumerate(edges):
        cells.append(
            f'        <mxCell id="e{j}" value="{edge.get("label", "")}" '
            f'style="edgeStyle=orthogonalEdgeStyle;" edge="1" parent="1" '
            f'source="{edge["source"]}" target="{edge["target"]}"/>'
        )

    cells.append("      </root>")
    cells.append("    </mxGraphModel>")
    cells.append("  </diagram>")
    cells.append("</mxfile>")
    return "\n".join(cells)


# ── SVG ──────────────────────────────────────────────────────────────────────


def _to_svg(nodes, edges, name):
    max_x = max((n.get("x", 0) for n in nodes), default=0) + 200
    max_y = max((n.get("y", 0) for n in nodes), default=0) + 150

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{max_x}" height="{max_y}" viewBox="0 0 {max_x} {max_y}">',
        f"<title>{name}</title>",
        "<style>",
        "  rect { fill: #1a1a2e; stroke: #3498db; stroke-width: 2; rx: 6; }",
        "  text { fill: #eaeaea; font-family: sans-serif; font-size: 11px; text-anchor: middle; }",
        "  line { stroke: #e94560; stroke-width: 2; marker-end: url(#arrow); }",
        "  marker#arrow > path { fill: #e94560; }",
        "</style>",
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">',
        '<path d="M 0 0 L 10 5 L 0 10 z"/></marker></defs>',
    ]

    node_map = {n.get("id"): n for n in nodes}
    for node in nodes:
        x = node.get("x", 100)
        y = node.get("y", 100)
        label = node.get("label", node.get("type", ""))
        lines.append(f'<rect x="{x}" y="{y}" width="110" height="50"/>')
        lines.append(f'<text x="{x + 55}" y="{y + 30}">{label}</text>')

    for edge in edges:
        src = node_map.get(edge["source"])
        tgt = node_map.get(edge["target"])
        if src and tgt:
            x1 = src.get("x", 0) + 110
            y1 = src.get("y", 0) + 25
            x2 = tgt.get("x", 0)
            y2 = tgt.get("y", 0) + 25
            lines.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"/>')

    lines.append("</svg>")
    return "\n".join(lines)


# ── CSV ──────────────────────────────────────────────────────────────────────


def _to_csv(nodes, edges):
    lines = ["id,label,type,stage,x,y"]
    for n in nodes:
        stage = n.get("stage") or _infer_stage(n.get("type", ""))
        lines.append(
            f"{n.get('id', '')},{n.get('label', '')},{n.get('type', '')},{stage},{n.get('x', 0)},{n.get('y', 0)}"
        )
    return "\n".join(lines)


# ── AWS CloudFormation ───────────────────────────────────────────────────────


def _to_cloudformation(nodes, edges, name):
    """Generate AWS CloudFormation template from pipeline graph."""
    safe = name.replace(" ", "-").lower()

    try:
        from tools.pipeline.deploy_catalog import DEPLOY_CATALOG, MANAGED
    except ImportError:
        DEPLOY_CATALOG = {}
        MANAGED = "managed"

    lines = [
        f"# Auto-generated CloudFormation: {name}",
        f"# Generated: {datetime.now(timezone.utc).isoformat()}",
        "AWSTemplateFormatVersion: '2010-09-09'",
        f"Description: 'DevSecOps Pipeline Infrastructure - {name}'",
        "",
        "Parameters:",
        "  Environment:",
        "    Type: String",
        "    Default: staging",
        "    AllowedValues: [dev, staging, prod]",
        "  VpcCidr:",
        "    Type: String",
        "    Default: 10.0.0.0/16",
        "",
        "Resources:",
    ]

    # VPC
    has_k8s = any(n.get("type", "").startswith("aws-eks") or n.get("type", "") == "k8s-cluster" for n in nodes)
    if has_k8s:
        lines.extend(
            [
                "  VPC:",
                "    Type: AWS::EC2::VPC",
                "    Properties:",
                "      CidrBlock: !Ref VpcCidr",
                "      EnableDnsSupport: true",
                "      EnableDnsHostnames: true",
                "      Tags:",
                "        - Key: Name",
                f"          Value: {safe}-vpc",
                "",
            ]
        )

    # Map managed AWS resources
    resource_idx = 0
    for node in nodes:
        ntype = node.get("type", "")
        info = DEPLOY_CATALOG.get(ntype, {})
        if info.get("category") != MANAGED:
            continue
        tf = info.get("terraform", {})
        aws_type = tf.get("aws", "")
        if not aws_type:
            continue

        resource_idx += 1
        label = node.get("label", ntype).replace(" ", "").replace("-", "")

        # Map Terraform resource types to CFN types
        cfn_map = {
            "aws_ecr_repository": ("AWS::ECR::Repository", f"RepositoryName: {safe}-{label.lower()}"),
            "aws_eks_cluster": (
                "AWS::EKS::Cluster",
                f"Name: {safe}-cluster\n      Version: '1.30'\n      RoleArn: !GetAtt EKSRole.Arn",
            ),
            "aws_kms_key": (
                "AWS::KMS::Key",
                f"Description: {safe} encryption key\n      KeyPolicy:\n        Statement:\n          - Effect: Allow\n            Principal:\n              AWS: !Sub 'arn:aws:iam::${{AWS::AccountId}}:root'\n            Action: 'kms:*'\n            Resource: '*'",
            ),
            "aws_secretsmanager_secret": (
                "AWS::SecretsManager::Secret",
                f"Name: {safe}-{label.lower()}\n      Description: Secret for {label}",
            ),
            "aws_codepipeline": ("AWS::CodePipeline::Pipeline", f"Name: {safe}-pipeline"),
            "aws_codebuild_project": (
                "AWS::CodeBuild::Project",
                f"Name: {safe}-build\n      ServiceRole: !GetAtt CodeBuildRole.Arn\n      Source:\n        Type: NO_SOURCE\n        BuildSpec: buildspec.yml\n      Environment:\n        Type: LINUX_CONTAINER\n        ComputeType: BUILD_GENERAL1_MEDIUM\n        Image: aws/codebuild/amazonlinux2-x86_64-standard:5.0",
            ),
            "aws_guardduty_detector": ("AWS::GuardDuty::Detector", "Enable: true"),
            "aws_securityhub_account": ("AWS::SecurityHub::Hub", ""),
            "aws_inspector2_enabler": ("AWS::Inspector::AssessmentTarget", f"AssessmentTargetName: {safe}-inspector"),
            "aws_config_configuration_recorder": (
                "AWS::Config::ConfigurationRecorder",
                f"Name: {safe}-config\n      RecordingGroup:\n        AllSupported: true",
            ),
            "aws_cloudwatch_log_group": (
                "AWS::Logs::LogGroup",
                f"LogGroupName: /devops/{safe}\n      RetentionInDays: 90",
            ),
            "aws_codedeploy_app": (
                "AWS::CodeDeploy::Application",
                f"ApplicationName: {safe}-deploy\n      ComputePlatform: ECS",
            ),
        }

        cfn_type, props = cfn_map.get(aws_type, ("AWS::CloudFormation::WaitConditionHandle", ""))
        lines.extend(
            [
                f"  {label}{resource_idx}:",
                f"    Type: {cfn_type}",
                "    Properties:",
                f"      {props}" if props else "      {}",
                "",
            ]
        )

    # IAM roles for EKS/CodeBuild if needed
    if has_k8s:
        lines.extend(
            [
                "  EKSRole:",
                "    Type: AWS::IAM::Role",
                "    Properties:",
                "      AssumeRolePolicyDocument:",
                "        Statement:",
                "          - Effect: Allow",
                "            Principal:",
                "              Service: eks.amazonaws.com",
                "            Action: sts:AssumeRole",
                "      ManagedPolicyArns:",
                "        - arn:aws:iam::aws:policy/AmazonEKSClusterPolicy",
                "",
            ]
        )

    lines.extend(
        [
            "Outputs:",
            "  StackName:",
            "    Value: !Ref AWS::StackName",
        ]
    )

    return "\n".join(lines)


# ── Azure Bicep ──────────────────────────────────────────────────────────────


def _to_bicep(nodes, edges, name):
    """Generate Azure Bicep template from pipeline graph."""
    safe = name.replace(" ", "-").lower()

    try:
        from tools.pipeline.deploy_catalog import DEPLOY_CATALOG, MANAGED
    except ImportError:
        DEPLOY_CATALOG = {}
        MANAGED = "managed"

    lines = [
        f"// Auto-generated Bicep: {name}",
        f"// Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "targetScope = 'resourceGroup'",
        "",
        "@allowed(['dev', 'staging', 'prod'])",
        "param environment string = 'staging'",
        f"param projectName string = '{safe}'",
        "param location string = resourceGroup().location",
        "",
    ]

    # AKS if needed
    has_k8s = any(n.get("type", "").startswith("az-aks") or n.get("type", "") == "k8s-cluster" for n in nodes)
    if has_k8s:
        lines.extend(
            [
                "resource aks 'Microsoft.ContainerService/managedClusters@2024-01-01' = {",
                "  name: '${projectName}-aks'",
                "  location: location",
                "  identity: { type: 'SystemAssigned' }",
                "  properties: {",
                "    kubernetesVersion: '1.30'",
                "    dnsPrefix: projectName",
                "    agentPoolProfiles: [{",
                "      name: 'default'",
                "      count: 3",
                "      vmSize: 'Standard_D4s_v3'",
                "      mode: 'System'",
                "    }]",
                "  }",
                "}",
                "",
            ]
        )

    # Map Azure managed resources
    for node in nodes:
        ntype = node.get("type", "")
        info = DEPLOY_CATALOG.get(ntype, {})
        if info.get("category") != MANAGED:
            continue
        tf = info.get("terraform", {})
        azure_type = tf.get("azure", "")
        if not azure_type:
            continue

        label = node.get("label", ntype).replace(" ", "").replace("-", "").lower()

        bicep_map = {
            "azurerm_container_registry": (
                "Microsoft.ContainerRegistry/registries@2023-01-01-preview",
                "name: '${projectName}acr'\n  location: location\n  sku: { name: 'Premium' }\n  properties: { adminUserEnabled: false }",
            ),
            "azurerm_key_vault": (
                "Microsoft.KeyVault/vaults@2023-07-01",
                "name: '${projectName}-kv'\n  location: location\n  properties: {\n    tenantId: subscription().tenantId\n    sku: { family: 'A', name: 'standard' }\n    enableSoftDelete: true\n    enablePurgeProtection: true\n  }",
            ),
            "azurerm_kubernetes_cluster": None,  # Already handled above
            "azurerm_log_analytics_workspace": (
                "Microsoft.OperationalInsights/workspaces@2022-10-01",
                "name: '${projectName}-logs'\n  location: location\n  properties: { retentionInDays: 90, sku: { name: 'PerGB2018' } }",
            ),
            "azurerm_security_center_subscription_pricing": None,  # Subscription-level, not Bicep resource
            "azurerm_policy_assignment": None,  # Complex, skip inline
            "azurerm_sentinel_log_analytics_workspace_onboarding": None,  # Depends on Log Analytics
        }

        entry = bicep_map.get(azure_type)
        if entry is None:
            continue
        bicep_type, props = entry

        lines.extend(
            [
                f"resource {label} '{bicep_type}' = {{",
                f"  {props}",
                "}",
                "",
            ]
        )

    lines.extend(
        [
            "output projectName string = projectName",
            "output environment string = environment",
        ]
    )

    return "\n".join(lines)


# ── OpenSLO (Phase 4: SRE Export) ────────────────────────────────────────────


def _to_openslo(nodes, edges, name):
    """Generate OpenSLO YAML spec from SRE nodes in the canvas."""
    slo_nodes = [n for n in nodes if n.get("type", "").startswith("sre-slo")]

    lines = [
        f"# OpenSLO v1 — Auto-generated from Pipeline Design Canvas: {name}",
        f"# Generated: {datetime.now(timezone.utc).isoformat()}",
        "# Spec: https://openslo.com/",
        "",
    ]

    if not slo_nodes:
        # Generate default SLOs for any K8s/deploy targets
        deploy_nodes = [
            n for n in nodes if n.get("type", "").startswith(("k8s-", "aws-eks", "az-aks", "gcp-gke", "deploy-"))
        ]
        for dn in deploy_nodes:
            svc = dn.get("label", "service").lower().replace(" ", "-")
            lines.extend(_openslo_service_block(svc))
        if not deploy_nodes:
            lines.append("# No SLO or deploy nodes found — add sre-slo nodes to the canvas")
    else:
        for slo_node in slo_nodes:
            svc = slo_node.get("label", "service").lower().replace(" ", "-")
            config = slo_node.get("config") or {}
            lines.extend(
                _openslo_service_block(
                    svc,
                    target=config.get("target", 99.9),
                    slo_type=config.get("slo_type", "availability"),
                )
            )

    return "\n".join(lines)


def _yaml_squote(value):
    """Return a YAML single-quoted scalar, safely escaping embedded quotes.

    In YAML single-quoted style a literal single quote is escaped by doubling it.
    Callers interpolate arbitrary UI-supplied strings (e.g. a runbook
    ``trigger_pattern`` regex, which routinely contains quotes/backslashes) into
    emitted YAML; without escaping, a value like ``it's``  would break the
    document. Backslashes need no escaping in single-quoted style.
    """
    return "'" + str(value).replace("'", "''") + "'"


def _openslo_service_block(service, target=99.9, slo_type="availability"):
    """Generate a single OpenSLO service + SLO + SLI block."""
    # Coerce target to float: SLO node config comes from the UI, where the
    # `target` field arrives as a string (e.g. "99.9"). `target / 100` on a str
    # raises TypeError. Fall back to the 99.9 default on any non-numeric value.
    try:
        target = float(target)
    except (TypeError, ValueError):
        target = 99.9
    return [
        "apiVersion: openslo/v1",
        "kind: Service",
        "metadata:",
        f"  name: {service}",
        f"  displayName: {service}",
        "spec:",
        "  description: Service managed by ICDEV DevOps Pipeline Canvas",
        "---",
        "apiVersion: openslo/v1",
        "kind: SLI",
        "metadata:",
        f"  name: {service}-{slo_type}-sli",
        "spec:",
        "  ratioMetric:",
        "    counter: true",
        "    good:",
        "      metricSource:",
        "        type: Prometheus",
        "        spec:",
        f'          query: sum(rate(http_requests_total{{service="{service}",code=~"2.."}}[5m]))',
        "    total:",
        "      metricSource:",
        "        type: Prometheus",
        "        spec:",
        f'          query: sum(rate(http_requests_total{{service="{service}"}}[5m]))',
        "---",
        "apiVersion: openslo/v1",
        "kind: SLO",
        "metadata:",
        f"  name: {service}-{slo_type}",
        "spec:",
        f"  service: {service}",
        "  indicator:",
        f"    ref: {service}-{slo_type}-sli",
        "  budgetingMethod: Occurrences",
        "  objectives:",
        f"    - displayName: {slo_type} target",
        f"      target: {target / 100}",
        "  timeWindow:",
        "    - duration: 30d",
        "      isRolling: true",
        "  alertPolicies:",
        f"    - name: {service}-burn-rate-fast",
        "      description: Fast burn alert (2h window, 14.4x)",
        "      severity: critical",
        "      condition:",
        "        kind: Burnrate",
        "        threshold: 14.4",
        "        lookbackWindow: 2h",
        f"    - name: {service}-burn-rate-slow",
        "      description: Slow burn alert (6h window, 6x)",
        "      severity: warning",
        "      condition:",
        "        kind: Burnrate",
        "        threshold: 6",
        "        lookbackWindow: 6h",
        "",
    ]


# ── Runbook Manifest (Phase 4: SRE Export) ───────────────────────────────────


def _to_runbook_manifest(nodes, edges, name):
    """Generate runbook manifest YAML from SRE nodes in the canvas."""
    runbook_nodes = [n for n in nodes if n.get("type") in ("sre-runbook", "sre-self-heal")]
    incident_nodes = [n for n in nodes if n.get("type", "").startswith("sre-incident")]
    chaos_nodes = [n for n in nodes if n.get("type", "").startswith("sre-chaos")]

    lines = [
        f"# Runbook Manifest — Auto-generated from Pipeline Design Canvas: {name}",
        f"# Generated: {datetime.now(timezone.utc).isoformat()}",
        "# Execute: python tools/sre/runbook_executor.py --import manifest.yaml",
        "",
        "runbooks:",
    ]

    if not runbook_nodes:
        # Generate default runbooks for common failure patterns
        lines.extend(
            [
                "  - name: auto-restart-unhealthy-pods",
                "    description: Restart pods failing health checks",
                "    trigger_pattern: 'pod.*CrashLoopBackOff|readiness.*failed'",
                "    risk_level: green",
                "    auto_execute: true",
                "    steps:",
                "      - command: kubectl rollout restart deployment/${SERVICE}",
                "        timeout: 120",
                "      - command: kubectl rollout status deployment/${SERVICE} --timeout=120s",
                "        timeout: 130",
                "",
                "  - name: scale-up-on-high-cpu",
                "    description: Scale deployment when CPU exceeds 80%",
                "    trigger_pattern: 'cpu.*usage.*high|cpu.*exceed.*80'",
                "    risk_level: green",
                "    auto_execute: true",
                "    steps:",
                "      - command: kubectl scale deployment/${SERVICE} --replicas=$((CURRENT_REPLICAS + 2))",
                "        timeout: 60",
                "",
                "  - name: rollback-failed-deployment",
                "    description: Rollback to previous version on deploy failure",
                "    trigger_pattern: 'deploy.*failed|rollout.*failed|ImagePullBackOff'",
                "    risk_level: yellow",
                "    auto_execute: false",
                "    steps:",
                "      - command: kubectl rollout undo deployment/${SERVICE}",
                "        timeout: 120",
                "      - command: kubectl rollout status deployment/${SERVICE} --timeout=120s",
                "        timeout: 130",
                "    rollback:",
                "      - command: kubectl rollout undo deployment/${SERVICE}",
                "",
                "  - name: clear-disk-pressure",
                "    description: Clean up disk when node has DiskPressure",
                "    trigger_pattern: 'DiskPressure|disk.*full|no.*space'",
                "    risk_level: green",
                "    auto_execute: true",
                "    steps:",
                "      - command: kubectl exec ${POD} -- find /tmp -type f -mtime +7 -delete",
                "        timeout: 60",
            ]
        )
    else:
        for rb in runbook_nodes:
            rb_name = rb.get("label", "runbook").lower().replace(" ", "-")
            config = rb.get("config") or {}
            lines.extend(
                [
                    f"  - name: {rb_name}",
                    f"    description: {_yaml_squote(config.get('description', rb.get('label', '')))}",
                    f"    trigger_pattern: {_yaml_squote(config.get('trigger_pattern', '.*'))}",
                    f"    risk_level: {config.get('risk_level', 'green')}",
                    f"    auto_execute: {str(config.get('auto_execute', True)).lower()}",
                    "    steps:",
                    f"      - command: echo 'Implement {rb_name} steps'",
                    "        timeout: 120",
                    "",
                ]
            )

    # Chaos experiments
    if chaos_nodes:
        lines.extend(["", "chaos_experiments:"])
        for ch in chaos_nodes:
            ch_name = ch.get("label", "chaos").lower().replace(" ", "-")
            lines.extend(
                [
                    f"  - name: {ch_name}",
                    f"    type: {ch.get('type', 'sre-chaos')}",
                    "    description: Chaos experiment from pipeline canvas",
                    "    schedule: manual",
                    "    steady_state_hypothesis:",
                    "      - probe: http",
                    "        url: http://localhost/health",
                    "        expected_status: 200",
                    "",
                ]
            )

    # Incident escalation policy
    if incident_nodes:
        lines.extend(["", "escalation_policy:"])
        lines.extend(
            [
                "  sev1:",
                "    response_time: 5m",
                "    escalation_after: 15m",
                "    notify: [on-call-primary, on-call-secondary, engineering-lead]",
                "  sev2:",
                "    response_time: 15m",
                "    escalation_after: 1h",
                "    notify: [on-call-primary]",
                "  sev3:",
                "    response_time: 1h",
                "    escalation_after: 4h",
                "    notify: [team-channel]",
                "  sev4:",
                "    response_time: 4h",
                "    notify: [ticket-system]",
            ]
        )

    return "\n".join(lines)
