# CUI // SP-CTI — ICDEV Pipeline Design Canvas Deployment Catalog
# Classification: CUI — Controlled Unclassified Information
"""
Maps each pipeline canvas node type to its deployment method, Helm chart,
Terraform resource, and infrastructure requirements.

Deployment categories:
  managed   — CSP-native managed service (Terraform resource)
  helm      — Helm chart on K8s (terraform helm_release)
  vm        — VM install via Ansible playbook
  saas      — SaaS subscription (config snippet only, no IaC)
  ephemeral — CI pipeline step (no persistent deployment)
  boundary  — Logical boundary marker (no deployment)
"""

# ── Deployment Category Constants ─────────────────────────────────────────────
MANAGED = "managed"
HELM = "helm"
VM = "vm"
SAAS = "saas"
EPHEMERAL = "ephemeral"
BOUNDARY = "boundary"

# ── Helm Chart Registry ──────────────────────────────────────────────────────
# Pinned chart versions for reproducibility (GovCloud/IL4+ requirement)
import os as _os  # noqa: E402 — needed before HELM_CHARTS for air-gap mirror

# Air-gap override: set ICDEV_HELM_MIRROR to a local chart mirror URL.
# In air-gapped environments, all charts must be pre-downloaded to a local
# ChartMuseum, Harbor chart repo, or file:// path.
# Example: ICDEV_HELM_MIRROR=https://charts.internal.local
_HELM_MIRROR = _os.environ.get("ICDEV_HELM_MIRROR", "")

HELM_CHARTS = {
    "harbor": {"repo": "https://helm.goharbor.io", "chart": "harbor", "version": "1.15.1", "namespace": "platform"},
    "argocd": {
        "repo": "https://argoproj.github.io/argo-helm",
        "chart": "argo-cd",
        "version": "7.7.5",
        "namespace": "ci-cd",
    },
    "vault": {
        "repo": "https://helm.releases.hashicorp.com",
        "chart": "vault",
        "version": "0.29.1",
        "namespace": "security",
    },
    "openbao": {
        "repo": "https://openbao.github.io/openbao-helm",
        "chart": "openbao",
        "version": "0.7.0",
        "namespace": "security",
    },
    "sonarqube": {
        "repo": "https://SonarSource.github.io/helm-chart-sonarqube",
        "chart": "sonarqube",
        "version": "10.7.0",
        "namespace": "quality",
    },
    "trivy-operator": {
        "repo": "https://aquasecurity.github.io/helm-charts",
        "chart": "trivy-operator",
        "version": "0.24.1",
        "namespace": "security",
    },
    "falco": {
        "repo": "https://falcosecurity.github.io/charts",
        "chart": "falco",
        "version": "4.16.1",
        "namespace": "security",
    },
    "kube-prometheus-stack": {
        "repo": "https://prometheus-community.github.io/helm-charts",
        "chart": "kube-prometheus-stack",
        "version": "65.8.1",
        "namespace": "observability",
    },
    "grafana": {
        "repo": "https://grafana.github.io/helm-charts",
        "chart": "grafana",
        "version": "8.8.2",
        "namespace": "observability",
    },
    "loki": {
        "repo": "https://grafana.github.io/helm-charts",
        "chart": "loki",
        "version": "6.22.0",
        "namespace": "observability",
    },
    "tempo": {
        "repo": "https://grafana.github.io/helm-charts",
        "chart": "tempo",
        "version": "1.13.2",
        "namespace": "observability",
    },
    "kyverno": {
        "repo": "https://kyverno.github.io/kyverno",
        "chart": "kyverno",
        "version": "3.3.4",
        "namespace": "security",
    },
    "gatekeeper": {
        "repo": "https://open-policy-agent.github.io/gatekeeper/charts",
        "chart": "gatekeeper",
        "version": "3.18.2",
        "namespace": "security",
    },
    "istio-base": {
        "repo": "https://istio-release.storage.googleapis.com/charts",
        "chart": "base",
        "version": "1.24.2",
        "namespace": "istio-system",
    },
    "istiod": {
        "repo": "https://istio-release.storage.googleapis.com/charts",
        "chart": "istiod",
        "version": "1.24.2",
        "namespace": "istio-system",
    },
    "neuvector": {
        "repo": "https://neuvector.github.io/neuvector-helm",
        "chart": "core",
        "version": "2.8.2",
        "namespace": "security",
    },
    "cert-manager": {
        "repo": "https://charts.jetstack.io",
        "chart": "cert-manager",
        "version": "1.16.3",
        "namespace": "cert-manager",
    },
    "ingress-nginx": {
        "repo": "https://kubernetes.github.io/ingress-nginx",
        "chart": "ingress-nginx",
        "version": "4.12.0",
        "namespace": "ingress",
    },
    "external-secrets": {
        "repo": "https://charts.external-secrets.io",
        "chart": "external-secrets",
        "version": "0.12.1",
        "namespace": "security",
    },
    "gitlab-runner": {
        "repo": "https://charts.gitlab.io",
        "chart": "gitlab-runner",
        "version": "0.72.0",
        "namespace": "ci-cd",
    },
    "jenkins": {"repo": "https://charts.jenkins.io", "chart": "jenkins", "version": "5.8.3", "namespace": "ci-cd"},
    "nexus": {
        "repo": "https://sonatype.github.io/helm3-charts",
        "chart": "nexus-repository-manager",
        "version": "72.0.0",
        "namespace": "platform",
    },
    "linkerd": {
        "repo": "https://helm.linkerd.io/edge",
        "chart": "linkerd-control-plane",
        "version": "2024.12.5",
        "namespace": "linkerd",
    },
    "fluentbit": {
        "repo": "https://fluent.github.io/helm-charts",
        "chart": "fluent-bit",
        "version": "0.48.3",
        "namespace": "observability",
    },
    "opentelemetry": {
        "repo": "https://open-telemetry.github.io/opentelemetry-helm-charts",
        "chart": "opentelemetry-collector",
        "version": "0.108.0",
        "namespace": "observability",
    },
    "wazuh": {
        "repo": "https://wazuh.github.io/wazuh-kubernetes",
        "chart": "wazuh",
        "version": "4.9.2",
        "namespace": "security",
    },
    "tekton-pipelines": {
        "repo": "https://tekton.dev/charts",
        "chart": "tekton-pipelines",
        "version": "0.65.0",
        "namespace": "ci-cd",
    },
    "gitea": {"repo": "https://dl.gitea.com/charts", "chart": "gitea", "version": "10.6.0", "namespace": "ci-cd"},
    "litmus": {
        "repo": "https://litmuschaos.github.io/litmus-helm",
        "chart": "litmus",
        "version": "3.11.0",
        "namespace": "litmus",
    },
    "pyrra": {"repo": "https://pyrra-dev.github.io/pyrra", "chart": "pyrra", "version": "0.12.0", "namespace": "sre"},
}

# ── Node Type → Deployment Mapping ───────────────────────────────────────────
# Each entry: {category, helm?, terraform?, ansible?, requires_k8s?, install_order, notes}

DEPLOY_CATALOG = {
    # ── Orchestration ────────────────────────────────────────────────────────
    "cicd-gitlab": {
        "category": HELM,
        "helm": "gitlab-runner",
        "requires_k8s": True,
        "install_order": 50,
        "notes": "GitLab Runner on K8s; GitLab server itself is SaaS or separate large Helm deploy",
    },
    "cicd-jenkins": {
        "category": HELM,
        "helm": "jenkins",
        "requires_k8s": True,
        "install_order": 50,
        "notes": "Jenkins on K8s via Helm; alternatively VM install via Ansible",
        "vm_fallback": True,
    },
    "cicd-tekton": {
        "category": HELM,
        "helm": "tekton-pipelines",
        "requires_k8s": True,
        "install_order": 50,
        "notes": "Tekton Pipelines CRDs on K8s",
    },
    "cicd-github-actions": {
        "category": SAAS,
        "notes": "GitHub-hosted runners; no IaC needed. Config via workflow YAML.",
    },
    "cicd-argo-workflows": {"category": HELM, "helm": "argocd", "requires_k8s": True, "install_order": 50},
    "cicd-drone": {"category": HELM, "requires_k8s": True, "install_order": 50, "notes": "Drone CI Helm chart"},
    "gitops-argocd": {
        "category": HELM,
        "helm": "argocd",
        "requires_k8s": True,
        "install_order": 40,
        "notes": "ArgoCD manages subsequent deployments",
    },
    "gitops-flux": {
        "category": HELM,
        "requires_k8s": True,
        "install_order": 40,
        "notes": "Flux installed via flux bootstrap, not Helm",
    },
    "aws-codepipeline": {"category": MANAGED, "terraform": {"aws": "aws_codepipeline"}, "install_order": 50},
    "aws-codebuild": {"category": MANAGED, "terraform": {"aws": "aws_codebuild_project"}, "install_order": 50},
    "aws-codedeploy": {"category": MANAGED, "terraform": {"aws": "aws_codedeploy_app"}, "install_order": 55},
    "az-pipelines": {"category": SAAS, "notes": "Azure DevOps SaaS; config via azure-pipelines.yml"},
    "gcp-cloudbuild": {"category": MANAGED, "terraform": {"gcp": "google_cloudbuild_trigger"}, "install_order": 50},
    "gcp-deploy": {
        "category": MANAGED,
        "terraform": {"gcp": "google_clouddeploy_delivery_pipeline"},
        "install_order": 55,
    },
    "oci-devops": {"category": MANAGED, "terraform": {"oci": "oci_devops_project"}, "install_order": 50},
    "ibm-cd": {"category": MANAGED, "terraform": {"ibm": "ibm_cd_toolchain"}, "install_order": 50},
    # ── Source Control ───────────────────────────────────────────────────────
    "scm-gitlab": {
        "category": SAAS,
        "notes": "GitLab.com SaaS; self-hosted requires massive Helm chart",
        "helm_optional": "gitlab",
    },
    "scm-gitea": {
        "category": HELM,
        "helm": "gitea",
        "requires_k8s": True,
        "install_order": 30,
        "notes": "Lightweight Git server on K8s",
    },
    "scm-forgejo": {
        "category": HELM,
        "requires_k8s": True,
        "install_order": 30,
        "notes": "Forgejo community fork of Gitea",
    },
    "scm-bitbucket": {"category": SAAS, "notes": "Bitbucket Cloud SaaS; Data Center is VM-based"},
    "aws-codecommit": {"category": MANAGED, "terraform": {"aws": "aws_codecommit_repository"}, "install_order": 20},
    "az-repos": {"category": SAAS, "notes": "Azure DevOps SaaS"},
    "gcp-source": {"category": MANAGED, "terraform": {"gcp": "google_sourcerepo_repository"}, "install_order": 20},
    "oci-code-repos": {"category": MANAGED, "terraform": {"oci": "oci_devops_repository"}, "install_order": 20},
    "branch-policy": {"category": EPHEMERAL, "notes": "Config in Git server settings, not IaC"},
    "commit-signing": {"category": EPHEMERAL, "notes": "Developer-side GPG/SSH config"},
    # ── Build & Compile ──────────────────────────────────────────────────────
    "build-runner": {"category": EPHEMERAL, "notes": "CI runner agent; deployed as part of CI/CD engine"},
    "build-kaniko": {"category": EPHEMERAL, "notes": "Runs as container in CI job; no persistent deploy"},
    "build-buildah": {"category": EPHEMERAL, "notes": "Runs as container in CI job"},
    "build-docker": {"category": EPHEMERAL, "notes": "Docker-in-Docker or Kaniko in CI"},
    "build-bazel": {"category": EPHEMERAL, "notes": "Build tool, installed in CI image"},
    "build-gradle": {"category": EPHEMERAL, "notes": "Build tool, installed in CI image"},
    "build-maven": {"category": EPHEMERAL, "notes": "Build tool, installed in CI image"},
    # ── Security Scanning ────────────────────────────────────────────────────
    "scan-sast": {"category": EPHEMERAL, "notes": "Generic SAST runs as CI step"},
    "scan-sonarqube": {
        "category": HELM,
        "helm": "sonarqube",
        "requires_k8s": True,
        "install_order": 60,
        "vm_fallback": True,
        "notes": "SonarQube server; needs PostgreSQL",
    },
    "scan-semgrep": {"category": EPHEMERAL, "notes": "Runs as CI step: semgrep scan"},
    "scan-codeql": {"category": EPHEMERAL, "notes": "GitHub CodeQL runs in GitHub Actions"},
    "scan-bandit": {"category": EPHEMERAL, "notes": "Python SAST: pip install bandit && bandit -r ."},
    "scan-spotbugs": {"category": EPHEMERAL, "notes": "Java SAST: runs in CI step"},
    "aws-codeguru": {
        "category": MANAGED,
        "terraform": {"aws": "aws_codegurureviewer_repository_association"},
        "install_order": 55,
    },
    "scan-dast": {"category": EPHEMERAL, "notes": "Generic DAST runs as CI step"},
    "scan-zap": {"category": EPHEMERAL, "notes": "OWASP ZAP baseline scan in CI container"},
    "scan-nuclei": {"category": EPHEMERAL, "notes": "Nuclei scan in CI container"},
    "scan-burp": {"category": SAAS, "notes": "Burp Suite Enterprise SaaS or local install"},
    "scan-sca": {"category": EPHEMERAL, "notes": "Generic SCA runs as CI step"},
    "scan-trivy": {
        "category": EPHEMERAL,
        "notes": "Trivy runs as CI step; Trivy Operator for continuous",
        "helm_optional": "trivy-operator",
    },
    "scan-grype": {"category": EPHEMERAL, "notes": "Grype runs as CI step"},
    "scan-snyk": {"category": SAAS, "notes": "Snyk SaaS; CLI runs in CI step"},
    "scan-dep-check": {"category": EPHEMERAL, "notes": "OWASP Dependency-Check as CI step"},
    "scan-iac": {"category": EPHEMERAL, "notes": "Generic IaC scan as CI step"},
    "scan-checkov": {"category": EPHEMERAL, "notes": "Checkov as CI step"},
    "scan-tfsec": {"category": EPHEMERAL, "notes": "tfsec/Trivy IaC as CI step"},
    "scan-kics": {"category": EPHEMERAL, "notes": "KICS as CI step"},
    "scan-secret": {"category": EPHEMERAL, "notes": "Secret detection as pre-commit or CI step"},
    "scan-gitleaks": {"category": EPHEMERAL, "notes": "Gitleaks as pre-commit hook + CI step"},
    "scan-trufflehog": {"category": EPHEMERAL, "notes": "TruffleHog as CI step"},
    "scan-detect-secrets": {"category": EPHEMERAL, "notes": "detect-secrets as pre-commit hook"},
    "scan-container": {"category": EPHEMERAL, "notes": "Container scan as CI step"},
    "scan-anchore": {"category": HELM, "requires_k8s": True, "install_order": 60, "notes": "Anchore Enterprise on K8s"},
    "scan-neuvector": {
        "category": HELM,
        "helm": "neuvector",
        "requires_k8s": True,
        "install_order": 70,
        "notes": "NeuVector runtime + scanning",
    },
    "aws-inspector": {"category": MANAGED, "terraform": {"aws": "aws_inspector2_enabler"}, "install_order": 55},
    "az-defender": {
        "category": MANAGED,
        "terraform": {"azure": "azurerm_security_center_subscription_pricing"},
        "install_order": 55,
    },
    "gcp-artifact-analysis": {
        "category": MANAGED,
        "terraform": {"gcp": "google_project_service"},
        "install_order": 55,
        "notes": "Enable Artifact Analysis API",
    },
    "ibm-vuln-advisor": {"category": MANAGED, "notes": "Built into IBM Container Registry"},
    "scan-license": {"category": EPHEMERAL, "notes": "License scanner as CI step"},
    # ── Artifact Management ──────────────────────────────────────────────────
    "registry-generic": {"category": HELM, "helm": "harbor", "requires_k8s": True, "install_order": 35},
    "registry-harbor": {
        "category": HELM,
        "helm": "harbor",
        "requires_k8s": True,
        "install_order": 35,
        "notes": "Harbor with Trivy scanner, Notary signing",
    },
    "registry-nexus": {
        "category": HELM,
        "helm": "nexus",
        "requires_k8s": True,
        "install_order": 35,
        "vm_fallback": True,
    },
    "registry-jfrog": {"category": SAAS, "notes": "JFrog SaaS or self-hosted (large Helm chart)", "vm_fallback": True},
    "registry-zot": {"category": HELM, "requires_k8s": True, "install_order": 35, "notes": "Lightweight OCI registry"},
    "aws-ecr": {"category": MANAGED, "terraform": {"aws": "aws_ecr_repository"}, "install_order": 25},
    "az-acr": {"category": MANAGED, "terraform": {"azure": "azurerm_container_registry"}, "install_order": 25},
    "gcp-gar": {"category": MANAGED, "terraform": {"gcp": "google_artifact_registry_repository"}, "install_order": 25},
    "oci-cr": {"category": MANAGED, "terraform": {"oci": "oci_artifacts_container_repository"}, "install_order": 25},
    "ibm-cr": {"category": MANAGED, "terraform": {"ibm": "ibm_cr_namespace"}, "install_order": 25},
    "registry-ironbank": {"category": BOUNDARY, "notes": "Iron Bank is an external DoD registry; reference only"},
    "sbom-store": {"category": MANAGED, "notes": "Stored in registry or object storage alongside artifacts"},
    "package-repo": {
        "category": HELM,
        "helm": "nexus",
        "requires_k8s": True,
        "install_order": 35,
        "notes": "Nexus for PyPI/npm/Maven proxy",
    },
    # ── Supply Chain ─────────────────────────────────────────────────────────
    "sign-cosign": {"category": EPHEMERAL, "notes": "Cosign runs as CI step; needs KMS for key storage"},
    "sign-notation": {"category": EPHEMERAL, "notes": "Notation runs as CI step"},
    "sign-dct": {"category": EPHEMERAL, "notes": "Docker Content Trust config"},
    "attest-in-toto": {"category": EPHEMERAL, "notes": "in-toto attestation in CI step"},
    "attest-slsa-gen": {"category": EPHEMERAL, "notes": "SLSA provenance generation in CI step"},
    "verify-slsa": {"category": EPHEMERAL, "notes": "SLSA verification in CI or admission controller"},
    "sbom-syft": {"category": EPHEMERAL, "notes": "Syft SBOM generation in CI step"},
    "sbom-cyclonedx": {"category": EPHEMERAL, "notes": "CycloneDX SBOM in CI step"},
    "sbom-spdx": {"category": EPHEMERAL, "notes": "SPDX SBOM in CI step"},
    "vex-openvex": {"category": EPHEMERAL, "notes": "VEX document generation in CI step"},
    "gcp-binary-auth": {
        "category": MANAGED,
        "terraform": {"gcp": "google_binary_authorization_policy"},
        "install_order": 55,
    },
    "ibm-portieris": {"category": HELM, "requires_k8s": True, "install_order": 65},
    # ── Policy & Governance ──────────────────────────────────────────────────
    "policy-opa": {"category": HELM, "helm": "gatekeeper", "requires_k8s": True, "install_order": 45},
    "policy-kyverno": {"category": HELM, "helm": "kyverno", "requires_k8s": True, "install_order": 45},
    "policy-gatekeeper": {"category": HELM, "helm": "gatekeeper", "requires_k8s": True, "install_order": 45},
    "policy-kubewarden": {"category": HELM, "requires_k8s": True, "install_order": 45},
    "aws-config": {"category": MANAGED, "terraform": {"aws": "aws_config_configuration_recorder"}, "install_order": 55},
    "az-policy": {"category": MANAGED, "terraform": {"azure": "azurerm_policy_assignment"}, "install_order": 55},
    "gate-manual": {"category": EPHEMERAL, "notes": "Manual approval gate in CI pipeline config"},
    "gate-automated": {"category": EPHEMERAL, "notes": "Automated gate in CI pipeline config"},
    "gate-vuln-threshold": {"category": EPHEMERAL, "notes": "Threshold gate in CI pipeline config"},
    "gate-deploy-window": {"category": EPHEMERAL, "notes": "Time-window gate in CI pipeline config"},
    # ── Secrets & Keys ───────────────────────────────────────────────────────
    "vault-hashicorp": {
        "category": HELM,
        "helm": "vault",
        "requires_k8s": True,
        "install_order": 30,
        "notes": "Vault with auto-unseal via KMS",
    },
    "vault-openbao": {"category": HELM, "helm": "openbao", "requires_k8s": True, "install_order": 30},
    "aws-secrets": {"category": MANAGED, "terraform": {"aws": "aws_secretsmanager_secret"}, "install_order": 20},
    "aws-kms": {"category": MANAGED, "terraform": {"aws": "aws_kms_key"}, "install_order": 15},
    "az-keyvault": {"category": MANAGED, "terraform": {"azure": "azurerm_key_vault"}, "install_order": 20},
    "gcp-secret": {"category": MANAGED, "terraform": {"gcp": "google_secret_manager_secret"}, "install_order": 20},
    "gcp-kms": {"category": MANAGED, "terraform": {"gcp": "google_kms_key_ring"}, "install_order": 15},
    "oci-vault": {"category": MANAGED, "terraform": {"oci": "oci_kms_vault"}, "install_order": 20},
    "ibm-secrets": {"category": MANAGED, "terraform": {"ibm": "ibm_sm_secret_group"}, "install_order": 20},
    "ibm-hpcs": {"category": MANAGED, "terraform": {"ibm": "ibm_hpcs"}, "install_order": 15},
    "kms-generic": {"category": MANAGED, "notes": "Use CSP-specific KMS"},
    "hsm-generic": {"category": MANAGED, "notes": "Use CSP-specific CloudHSM or HPCS"},
    "cert-manager": {"category": HELM, "helm": "cert-manager", "requires_k8s": True, "install_order": 20},
    "sealed-secrets": {"category": HELM, "requires_k8s": True, "install_order": 35},
    "sops": {"category": EPHEMERAL, "notes": "SOPS is a CLI tool, not a service"},
    "external-secrets": {"category": HELM, "helm": "external-secrets", "requires_k8s": True, "install_order": 35},
    # ── Deploy Targets ───────────────────────────────────────────────────────
    "k8s-cluster": {
        "category": MANAGED,
        "terraform": {
            "aws": "aws_eks_cluster",
            "azure": "azurerm_kubernetes_cluster",
            "gcp": "google_container_cluster",
            "oci": "oci_containerengine_cluster",
            "ibm": "ibm_container_cluster",
            "on_prem": "rke2_cluster",
        },
        "install_order": 10,
    },
    "aws-eks": {"category": MANAGED, "terraform": {"aws": "aws_eks_cluster"}, "install_order": 10},
    "az-aks": {"category": MANAGED, "terraform": {"azure": "azurerm_kubernetes_cluster"}, "install_order": 10},
    "gcp-gke": {"category": MANAGED, "terraform": {"gcp": "google_container_cluster"}, "install_order": 10},
    "oci-oke": {"category": MANAGED, "terraform": {"oci": "oci_containerengine_cluster"}, "install_order": 10},
    "ibm-iks": {"category": MANAGED, "terraform": {"ibm": "ibm_container_cluster"}, "install_order": 10},
    "openshift": {
        "category": MANAGED,
        "notes": "OpenShift via ROSA (AWS) or ARO (Azure) or self-managed",
        "install_order": 10,
    },
    "rke2": {
        "category": VM,
        "ansible": "install_rke2",
        "install_order": 10,
        "notes": "RKE2 installed on VMs via Ansible",
    },
    "k3s": {"category": VM, "ansible": "install_k3s", "install_order": 10},
    "deploy-bigbang": {
        "category": HELM,
        "requires_k8s": True,
        "install_order": 80,
        "notes": "Big Bang is a Flux-based deployment; needs Flux bootstrap",
    },
    "deploy-serverless": {
        "category": MANAGED,
        "terraform": {
            "aws": "aws_lambda_function",
            "azure": "azurerm_function_app",
            "gcp": "google_cloudfunctions2_function",
        },
        "install_order": 55,
    },
    "deploy-vm": {"category": VM, "ansible": "provision_vm", "install_order": 10},
    "deploy-edge": {"category": VM, "ansible": "install_k3s", "install_order": 10, "notes": "K3s on edge devices"},
    "deploy-canary": {"category": EPHEMERAL, "notes": "Canary strategy config in ArgoCD/Flagger"},
    "deploy-bluegreen": {"category": EPHEMERAL, "notes": "Blue-green strategy config in ArgoCD/Argo Rollouts"},
    "deploy-feature-flag": {"category": SAAS, "notes": "LaunchDarkly/Unleash SaaS or Helm for self-hosted Unleash"},
    # ── Monitoring & Response ────────────────────────────────────────────────
    "mon-prometheus": {"category": HELM, "helm": "kube-prometheus-stack", "requires_k8s": True, "install_order": 60},
    "mon-grafana": {
        "category": HELM,
        "helm": "grafana",
        "requires_k8s": True,
        "install_order": 60,
        "notes": "Included in kube-prometheus-stack; standalone chart also available",
    },
    "mon-loki": {"category": HELM, "helm": "loki", "requires_k8s": True, "install_order": 60},
    "mon-tempo": {"category": HELM, "helm": "tempo", "requires_k8s": True, "install_order": 60},
    "mon-otel": {"category": HELM, "helm": "opentelemetry", "requires_k8s": True, "install_order": 60},
    "mon-elk": {
        "category": HELM,
        "requires_k8s": True,
        "install_order": 60,
        "notes": "ELK stack via ECK operator or Bitnami chart",
    },
    "mon-fluentbit": {"category": HELM, "helm": "fluentbit", "requires_k8s": True, "install_order": 60},
    "aws-cloudwatch": {"category": MANAGED, "terraform": {"aws": "aws_cloudwatch_log_group"}, "install_order": 55},
    "aws-guardduty": {"category": MANAGED, "terraform": {"aws": "aws_guardduty_detector"}, "install_order": 55},
    "az-monitor": {"category": MANAGED, "terraform": {"azure": "azurerm_log_analytics_workspace"}, "install_order": 55},
    "az-sentinel": {
        "category": MANAGED,
        "terraform": {"azure": "azurerm_sentinel_log_analytics_workspace_onboarding"},
        "install_order": 55,
    },
    "gcp-monitoring": {
        "category": MANAGED,
        "terraform": {"gcp": "google_monitoring_notification_channel"},
        "install_order": 55,
    },
    "gcp-scc": {"category": MANAGED, "terraform": {"gcp": "google_scc_source"}, "install_order": 55},
    "mon-falco": {"category": HELM, "helm": "falco", "requires_k8s": True, "install_order": 70},
    "mon-wazuh": {"category": HELM, "helm": "wazuh", "requires_k8s": True, "install_order": 70, "vm_fallback": True},
    "mon-soar": {"category": SAAS, "notes": "SOAR platforms are typically SaaS (Splunk SOAR, Palo Alto XSOAR)"},
    "mon-pagerduty": {"category": SAAS, "notes": "PagerDuty SaaS; config via API key integration"},
    # ── Compliance & Audit ───────────────────────────────────────────────────
    "comp-dashboard": {
        "category": HELM,
        "requires_k8s": True,
        "install_order": 75,
        "notes": "Custom compliance dashboard or IBM SCC",
    },
    "comp-evidence": {"category": MANAGED, "notes": "Evidence stored in Git repo or object storage (S3/Blob/GCS)"},
    "comp-oscal": {"category": EPHEMERAL, "notes": "OSCAL export generated by ICDEV tools, not a service"},
    "comp-stigman": {
        "category": HELM,
        "requires_k8s": True,
        "install_order": 75,
        "notes": "STIG Manager web app on K8s",
    },
    "comp-openscap": {"category": EPHEMERAL, "notes": "OpenSCAP scanner runs as CI step or cron job"},
    "comp-inspec": {"category": EPHEMERAL, "notes": "InSpec runs as CI step"},
    "aws-securityhub": {"category": MANAGED, "terraform": {"aws": "aws_securityhub_account"}, "install_order": 55},
    "aws-audit": {
        "category": MANAGED,
        "terraform": {"aws": "aws_auditmanager_account_registration"},
        "install_order": 55,
    },
    "az-defender-cloud": {
        "category": MANAGED,
        "terraform": {"azure": "azurerm_security_center_subscription_pricing"},
        "install_order": 55,
    },
    "ibm-scc": {"category": MANAGED, "notes": "IBM Security and Compliance Center managed service"},
    # ── Cross-Domain ─────────────────────────────────────────────────────────
    "cds-guard": {"category": BOUNDARY, "notes": "Physical cross-domain guard; procurement, not IaC"},
    "cds-data-diode": {"category": BOUNDARY, "notes": "Physical data diode; procurement, not IaC"},
    "cds-emulator": {
        "category": VM,
        "ansible": "install_cds_emulator",
        "install_order": 80,
        "notes": "Dev/test CDS emulator on VM",
    },
    "cds-transfer": {"category": MANAGED, "notes": "Managed cross-domain transfer service"},
    "boundary-commercial": {"category": BOUNDARY, "notes": "Logical boundary marker"},
    "boundary-govcloud": {"category": BOUNDARY, "notes": "Logical boundary marker"},
    "boundary-secret": {"category": BOUNDARY, "notes": "Logical boundary marker"},
    "boundary-topsecret": {"category": BOUNDARY, "notes": "Logical boundary marker"},
    "pipeline-nipr": {"category": BOUNDARY, "notes": "Logical boundary marker"},
    "pipeline-sipr": {"category": BOUNDARY, "notes": "Logical boundary marker"},
    "pipeline-jwics": {"category": BOUNDARY, "notes": "Logical boundary marker"},
    "sneakernet": {"category": BOUNDARY, "notes": "Physical media transfer; procedure, not IaC"},
    "vuln-db-mirror": {
        "category": VM,
        "ansible": "setup_vuln_mirror",
        "install_order": 25,
        "notes": "Mirror NVD/OSV on VM with scheduled updates",
    },
    "package-mirror": {
        "category": VM,
        "ansible": "setup_package_mirror",
        "install_order": 25,
        "notes": "devpi/Verdaccio/Athens on VM",
    },
    # ── Service Mesh ─────────────────────────────────────────────────────────
    "mesh-istio": {
        "category": HELM,
        "helm": "istiod",
        "requires_k8s": True,
        "install_order": 45,
        "notes": "Install istio-base first, then istiod",
    },
    "mesh-linkerd": {"category": HELM, "helm": "linkerd", "requires_k8s": True, "install_order": 45},
    "mesh-consul": {"category": HELM, "requires_k8s": True, "install_order": 45},
    # ── SRE / Reliability ────────────────────────────────────────────────
    "sre-slo": {"category": BOUNDARY, "notes": "SLO definition — config in SLO manager or OpenSLO YAML"},
    "sre-sli": {"category": BOUNDARY, "notes": "SLI metric definition — Prometheus query or OTel metric"},
    "sre-error-budget": {"category": BOUNDARY, "notes": "Error budget policy — derived from SLO"},
    "sre-burn-rate": {"category": BOUNDARY, "notes": "Burn rate alert — Prometheus alerting rule"},
    "sre-incident": {"category": SAAS, "notes": "Incident management — PagerDuty SaaS or self-hosted Grafana OnCall"},
    "sre-postmortem": {"category": BOUNDARY, "notes": "Postmortem template — process, not infrastructure"},
    "sre-oncall": {"category": SAAS, "notes": "On-call scheduling via PagerDuty/Opsgenie"},
    "sre-statuspage": {"category": SAAS, "notes": "Status page — Atlassian Statuspage or self-hosted Cachet"},
    "sre-runbook": {"category": BOUNDARY, "notes": "Runbook definition — ICDEV runbook_executor.py"},
    "sre-self-heal": {"category": BOUNDARY, "notes": "Self-healing config — ICDEV self_heal_analyzer.py"},
    "sre-chaos": {"category": BOUNDARY, "notes": "Chaos experiment definition"},
    "sre-chaos-litmus": {
        "category": HELM,
        "helm": "litmus",
        "requires_k8s": True,
        "install_order": 75,
        "notes": "LitmusChaos operator on K8s",
    },
    "aws-fis": {"category": MANAGED, "terraform": {"aws": "aws_fis_experiment_template"}, "install_order": 75},
    "az-chaos-studio": {
        "category": MANAGED,
        "terraform": {"azure": "azurerm_chaos_studio_experiment"},
        "install_order": 75,
    },
    "sre-dora": {"category": BOUNDARY, "notes": "DORA metrics dashboard — calculated from pipeline data"},
    "sre-dora-deploy-freq": {"category": BOUNDARY, "notes": "DORA deploy frequency metric"},
    "sre-dora-lead-time": {"category": BOUNDARY, "notes": "DORA lead time metric"},
    "sre-dora-cfr": {"category": BOUNDARY, "notes": "DORA change failure rate metric"},
    "sre-dora-mttr": {"category": BOUNDARY, "notes": "DORA MTTR metric"},
    "sre-resilience": {"category": BOUNDARY, "notes": "Composite resilience score"},
    "aws-resilience-hub": {"category": MANAGED, "terraform": {"aws": "aws_resiliencehub_app"}, "install_order": 70},
    "aws-cw-slo": {
        "category": MANAGED,
        "terraform": {"aws": "aws_cloudwatch_service_level_objective"},
        "install_order": 60,
    },
    "aws-incident-mgr": {
        "category": MANAGED,
        "terraform": {"aws": "aws_ssmincidents_response_plan"},
        "install_order": 65,
    },
    "gcp-service-mon": {"category": MANAGED, "terraform": {"gcp": "google_monitoring_slo"}, "install_order": 60},
    "az-advisor-rel": {
        "category": MANAGED,
        "notes": "Azure Advisor reliability — subscription-level, no Terraform resource",
    },
    "ibm-instana": {"category": SAAS, "notes": "IBM Instana APM SaaS or self-hosted agent"},
    "sre-openslo": {"category": BOUNDARY, "notes": "OpenSLO YAML specification"},
    "sre-sloth": {
        "category": EPHEMERAL,
        "notes": "Sloth generates Prometheus rules from SLO YAML — CLI tool, no service",
    },
    "sre-pyrra": {"category": HELM, "helm": "pyrra", "requires_k8s": True, "install_order": 65},
    "sre-pagerduty": {"category": SAAS, "notes": "PagerDuty SaaS incident management"},
    "sre-grafana-oncall": {
        "category": HELM,
        "requires_k8s": True,
        "install_order": 65,
        "notes": "Grafana OnCall open-source on-call",
    },
    "sre-opsgenie": {"category": SAAS, "notes": "Atlassian Opsgenie SaaS"},
    "sre-backstage": {
        "category": HELM,
        "requires_k8s": True,
        "install_order": 70,
        "notes": "Backstage developer portal",
    },
    "sre-circuit-breaker": {
        "category": BOUNDARY,
        "notes": "Circuit breaker pattern — code-level, ICDEV circuit_breaker.py",
    },
    "sre-retry": {"category": BOUNDARY, "notes": "Retry pattern — code-level, ICDEV retry.py"},
    "sre-bulkhead": {"category": BOUNDARY, "notes": "Bulkhead pattern — code-level isolation"},
    # ── Infrastructure Bridge (NDC ↔ PDC) ────────────────────────────────
    "ndc-topology": {"category": BOUNDARY, "notes": "Reference to NDC topology — infrastructure designed there"},
    "ndc-vpc": {"category": BOUNDARY, "notes": "Reference to VPC/VNet from linked NDC topology"},
    "hybrid-directconnect": {"category": BOUNDARY, "notes": "Dedicated circuit (DX/ER/IC) — designed in NDC"},
    "hybrid-vpn": {"category": BOUNDARY, "notes": "Site-to-site VPN — designed in NDC"},
    "hybrid-transit": {"category": BOUNDARY, "notes": "Transit hub (TGW/vWAN/NCC) — designed in NDC"},
    "hybrid-peering": {"category": BOUNDARY, "notes": "Cross-cloud peering — designed in NDC"},
    "onprem-datacenter": {
        "category": VM,
        "ansible": "provision_vm",
        "install_order": 5,
        "notes": "On-premises data center",
    },
    "onprem-colo": {"category": BOUNDARY, "notes": "Colocation facility — designed in NDC"},
    "onprem-edge": {"category": VM, "ansible": "install_k3s", "install_order": 5, "notes": "Edge site with K3s"},
}


def get_deploy_info(node_type):
    """Get deployment info for a node type."""
    return DEPLOY_CATALOG.get(node_type, {"category": BOUNDARY, "notes": "Unknown node type"})


def get_helm_chart_url(chart_key):
    """Get Helm chart repo URL, respecting air-gap mirror override.

    If ICDEV_HELM_MIRROR is set, returns the mirror URL for all charts.
    Otherwise returns the upstream URL from HELM_CHARTS.
    """
    chart = HELM_CHARTS.get(chart_key)
    if not chart:
        return None
    if _HELM_MIRROR:
        return {**chart, "repo": _HELM_MIRROR}
    return chart


def get_helm_chart(chart_key):
    """Get Helm chart details by key."""
    return HELM_CHARTS.get(chart_key)


def classify_nodes(nodes):
    """Classify a list of canvas nodes by deployment category.

    Returns dict keyed by category with lists of (node, deploy_info) tuples.
    """
    result = {MANAGED: [], HELM: [], VM: [], SAAS: [], EPHEMERAL: [], BOUNDARY: []}
    for node in nodes:
        ntype = node.get("type", "")
        info = get_deploy_info(ntype)
        result[info["category"]].append({"node": node, "deploy": info})
    return result


def resolve_dependencies(nodes):
    """Determine what infrastructure layers are needed for the given nodes.

    Returns dict with booleans: needs_vpc, needs_k8s, needs_vm, needs_db, etc.
    """
    classified = classify_nodes(nodes)
    node_types = set(n.get("type", "") for n in nodes)

    needs_k8s = any(get_deploy_info(n.get("type", "")).get("requires_k8s", False) for n in nodes)
    needs_vm = bool(classified[VM])
    needs_managed = bool(classified[MANAGED])

    # Determine CSP from managed service types
    detected_csps = set()
    for item in classified[MANAGED]:
        tf = item["deploy"].get("terraform", {})
        detected_csps.update(tf.keys())
    # Also check node type prefixes
    for nt in node_types:
        if nt.startswith("aws-"):
            detected_csps.add("aws")
        elif nt.startswith("az-"):
            detected_csps.add("azure")
        elif nt.startswith("gcp-"):
            detected_csps.add("gcp")
        elif nt.startswith("oci-"):
            detected_csps.add("oci")
        elif nt.startswith("ibm-"):
            detected_csps.add("ibm")

    return {
        "needs_vpc": needs_k8s or needs_managed or needs_vm,
        "needs_k8s": needs_k8s,
        "needs_vm": needs_vm,
        "needs_managed": needs_managed,
        "detected_csps": sorted(detected_csps) or ["on_prem"],
        "primary_csp": sorted(detected_csps)[0] if detected_csps else "on_prem",
        "helm_releases": sorted(set(item["deploy"]["helm"] for item in classified[HELM] if item["deploy"].get("helm"))),
        "managed_resources": [
            {"type": item["node"].get("type"), "terraform": item["deploy"].get("terraform", {})}
            for item in classified[MANAGED]
            if item["deploy"].get("terraform")
        ],
        "vm_playbooks": sorted(
            set(item["deploy"]["ansible"] for item in classified[VM] if item["deploy"].get("ansible"))
        ),
        "saas_integrations": [
            {"type": item["node"].get("type"), "notes": item["deploy"].get("notes", "")} for item in classified[SAAS]
        ],
        "ci_steps": [
            {"type": item["node"].get("type"), "label": item["node"].get("label", "")} for item in classified[EPHEMERAL]
        ],
    }
