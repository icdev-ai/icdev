# CUI // SP-CTI — ICDEV Pipeline Design Canvas Constants
# Classification: CUI — Controlled Unclassified Information
"""
Pipeline Design Canvas module-level constants.

Cloud-agnostic DevSecOps pipeline object library, CSP service equivalence,
node styles, OWASP coverage mapping, and pipeline cost estimates.
No compliance rules here — those come from existing tools/compliance/ modules.
"""

# ── Pipeline Stages (generic, cloud-agnostic) ────────────────────────────────
PIPELINE_STAGES = {
    "pre_commit": {"order": 1, "label": "Pre-Commit", "desc": "Local developer checks before push", "color": "#27ae60"},
    "source": {
        "order": 2,
        "label": "Source Control",
        "desc": "Version control, branch protection, code review",
        "color": "#2ecc71",
    },
    "build": {"order": 3, "label": "Build", "desc": "Compile, container build, SBOM generation", "color": "#3498db"},
    "test": {"order": 4, "label": "Test", "desc": "Unit, integration, SAST, SCA, IaC scanning", "color": "#9b59b6"},
    "package": {
        "order": 5,
        "label": "Package",
        "desc": "Container scan, image signing, registry push",
        "color": "#8e44ad",
    },
    "policy_gate": {
        "order": 6,
        "label": "Policy Gate",
        "desc": "Admission control, vulnerability thresholds",
        "color": "#e74c3c",
    },
    "deploy_staging": {
        "order": 7,
        "label": "Deploy (Staging)",
        "desc": "Deploy to staging, smoke tests",
        "color": "#e67e22",
    },
    "approval": {
        "order": 8,
        "label": "Approval",
        "desc": "Manual gates, automated condition checks",
        "color": "#f39c12",
    },
    "deploy_prod": {
        "order": 9,
        "label": "Deploy (Prod)",
        "desc": "Blue-green/canary, feature flags",
        "color": "#d35400",
    },
    "monitor": {"order": 10, "label": "Monitor", "desc": "Runtime security, continuous scanning", "color": "#1abc9c"},
    "sre": {"order": 11, "label": "SRE", "desc": "SLOs, error budgets, incident mgmt, chaos, DORA", "color": "#00bcd4"},
    "compliance": {
        "order": 12,
        "label": "Compliance",
        "desc": "Continuous compliance, evidence collection",
        "color": "#16a085",
    },
}

# ── Pipeline Object Library (12 categories) ──────────────────────────────────
# Each object: {type, label, icon, desc, stage?, csp_mapping?, license?}
PIPELINE_OBJECTS = {
    "orchestration": [
        # On-premises / Cloud-agnostic
        {
            "type": "cicd-gitlab",
            "label": "GitLab CI",
            "icon": "GL",
            "desc": "GitLab CI/CD pipeline engine (self-hosted or SaaS)",
            "stage": "build",
            "license": "MIT/Proprietary",
        },
        {
            "type": "cicd-jenkins",
            "label": "Jenkins",
            "icon": "JK",
            "desc": "Jenkins automation server — most mature OSS CI/CD",
            "stage": "build",
            "license": "MIT",
        },
        {
            "type": "cicd-tekton",
            "label": "Tekton",
            "icon": "TK",
            "desc": "Kubernetes-native CI/CD (CNCF, DoD Platform One)",
            "stage": "build",
            "license": "Apache-2.0",
        },
        {
            "type": "cicd-github-actions",
            "label": "GitHub Actions",
            "icon": "GH",
            "desc": "GitHub-native CI/CD workflows",
            "stage": "build",
        },
        {
            "type": "cicd-argo-workflows",
            "label": "Argo Workflows",
            "icon": "AW",
            "desc": "Kubernetes workflow engine for complex DAG pipelines",
            "stage": "build",
            "license": "Apache-2.0",
        },
        {
            "type": "cicd-drone",
            "label": "Drone / Woodpecker",
            "icon": "DR",
            "desc": "Container-native CI — lightweight alternative to Jenkins",
            "stage": "build",
            "license": "Apache-2.0",
        },
        {
            "type": "gitops-argocd",
            "label": "ArgoCD",
            "icon": "AC",
            "desc": "GitOps continuous delivery for Kubernetes (Big Bang addon)",
            "stage": "deploy_prod",
            "license": "Apache-2.0",
        },
        {
            "type": "gitops-flux",
            "label": "Flux CD",
            "icon": "FX",
            "desc": "CNCF GitOps toolkit for Kubernetes",
            "stage": "deploy_prod",
            "license": "Apache-2.0",
        },
        # CSP-specific
        {
            "type": "aws-codepipeline",
            "label": "CodePipeline",
            "icon": "CP",
            "desc": "AWS CI/CD orchestration service",
            "stage": "build",
            "csp": "aws",
        },
        {
            "type": "aws-codebuild",
            "label": "CodeBuild",
            "icon": "CB",
            "desc": "AWS managed build service",
            "stage": "build",
            "csp": "aws",
        },
        {
            "type": "aws-codedeploy",
            "label": "CodeDeploy",
            "icon": "CD",
            "desc": "AWS deployment automation (EC2/ECS/Lambda)",
            "stage": "deploy_prod",
            "csp": "aws",
        },
        {
            "type": "az-pipelines",
            "label": "Azure Pipelines",
            "icon": "AP",
            "desc": "Azure DevOps CI/CD pipelines",
            "stage": "build",
            "csp": "azure",
        },
        {
            "type": "gcp-cloudbuild",
            "label": "Cloud Build",
            "icon": "GB",
            "desc": "GCP serverless CI/CD — SLSA Level 3 native",
            "stage": "build",
            "csp": "gcp",
        },
        {
            "type": "gcp-deploy",
            "label": "Cloud Deploy",
            "icon": "GD",
            "desc": "GCP managed continuous delivery to GKE/Cloud Run",
            "stage": "deploy_prod",
            "csp": "gcp",
        },
        {
            "type": "oci-devops",
            "label": "OCI DevOps",
            "icon": "OD",
            "desc": "OCI DevOps Build + Deployment Pipelines",
            "stage": "build",
            "csp": "oci",
        },
        {
            "type": "ibm-cd",
            "label": "IBM Continuous Delivery",
            "icon": "IC",
            "desc": "IBM Cloud Tekton-based toolchains (CI/CD/CC)",
            "stage": "build",
            "csp": "ibm",
        },
    ],
    "source_control": [
        {
            "type": "scm-gitlab",
            "label": "GitLab",
            "icon": "GL",
            "desc": "GitLab source control (self-hosted or SaaS)",
            "stage": "source",
            "license": "MIT/Proprietary",
        },
        {
            "type": "scm-gitea",
            "label": "Gitea",
            "icon": "GTA",
            "desc": "Lightweight Go-based Git hosting — minimal attack surface",
            "stage": "source",
            "license": "MIT",
        },
        {
            "type": "scm-forgejo",
            "label": "Forgejo",
            "icon": "FJ",
            "desc": "Community fork of Gitea — fully FOSS",
            "stage": "source",
            "license": "MIT",
        },
        {
            "type": "scm-bitbucket",
            "label": "Bitbucket Server",
            "icon": "BB",
            "desc": "Atlassian Bitbucket Data Center (self-hosted)",
            "stage": "source",
        },
        {
            "type": "aws-codecommit",
            "label": "CodeCommit",
            "icon": "CC",
            "desc": "AWS managed Git (deprecated — consider GitLab)",
            "stage": "source",
            "csp": "aws",
        },
        {
            "type": "az-repos",
            "label": "Azure Repos",
            "icon": "AR",
            "desc": "Azure DevOps Git repositories",
            "stage": "source",
            "csp": "azure",
        },
        {
            "type": "gcp-source",
            "label": "Cloud Source Repos",
            "icon": "GS",
            "desc": "GCP source code repositories",
            "stage": "source",
            "csp": "gcp",
        },
        {
            "type": "oci-code-repos",
            "label": "OCI Code Repos",
            "icon": "OR",
            "desc": "OCI code repositories with trigger support",
            "stage": "source",
            "csp": "oci",
        },
        {
            "type": "branch-policy",
            "label": "Branch Policy",
            "icon": "BP",
            "desc": "Branch protection rules (required reviews, status checks)",
            "stage": "source",
        },
        {
            "type": "commit-signing",
            "label": "Commit Signing",
            "icon": "CS",
            "desc": "GPG/SSH commit signature verification",
            "stage": "source",
        },
    ],
    "build_compile": [
        {
            "type": "build-runner",
            "label": "Build Runner",
            "icon": "BR",
            "desc": "Generic build agent/runner (shell, Docker, K8s executor)",
            "stage": "build",
        },
        {
            "type": "build-kaniko",
            "label": "Kaniko",
            "icon": "KN",
            "desc": "Rootless container builder (no Docker daemon required)",
            "stage": "build",
            "license": "Apache-2.0",
        },
        {
            "type": "build-buildah",
            "label": "Buildah",
            "icon": "BH",
            "desc": "OCI container builder (daemonless, rootless)",
            "stage": "build",
            "license": "Apache-2.0",
        },
        {
            "type": "build-docker",
            "label": "Docker Build",
            "icon": "DK",
            "desc": "Docker image builder (BuildKit/multistage)",
            "stage": "build",
        },
        {
            "type": "build-bazel",
            "label": "Bazel",
            "icon": "BZ",
            "desc": "Bazel build system — hermetic, reproducible builds",
            "stage": "build",
            "license": "Apache-2.0",
        },
        {
            "type": "build-gradle",
            "label": "Gradle",
            "icon": "GR",
            "desc": "Gradle build tool (Java/Kotlin/Android)",
            "stage": "build",
        },
        {
            "type": "build-maven",
            "label": "Maven",
            "icon": "MV",
            "desc": "Apache Maven build system (Java)",
            "stage": "build",
            "license": "Apache-2.0",
        },
    ],
    "security_scanning": [
        # SAST
        {
            "type": "scan-sast",
            "label": "SAST (Generic)",
            "icon": "SA",
            "desc": "Static Application Security Testing",
            "stage": "test",
        },
        {
            "type": "scan-sonarqube",
            "label": "SonarQube",
            "icon": "SQ",
            "desc": "Code quality + SAST — multi-language quality gates",
            "stage": "test",
            "license": "LGPL/Proprietary",
        },
        {
            "type": "scan-semgrep",
            "label": "Semgrep",
            "icon": "SG",
            "desc": "Lightweight SAST with custom rule authoring",
            "stage": "test",
            "license": "LGPL",
        },
        {
            "type": "scan-codeql",
            "label": "CodeQL",
            "icon": "CQ",
            "desc": "GitHub Advanced Security semantic SAST",
            "stage": "test",
        },
        {
            "type": "scan-bandit",
            "label": "Bandit",
            "icon": "BN",
            "desc": "Python-specific SAST — zero dependencies",
            "stage": "test",
            "license": "Apache-2.0",
        },
        {
            "type": "scan-spotbugs",
            "label": "SpotBugs",
            "icon": "SB",
            "desc": "Java bytecode analysis (successor to FindBugs)",
            "stage": "test",
            "license": "LGPL",
        },
        {
            "type": "aws-codeguru",
            "label": "CodeGuru",
            "icon": "CG",
            "desc": "AWS AI-powered code reviewer and profiler",
            "stage": "test",
            "csp": "aws",
        },
        # DAST
        {
            "type": "scan-dast",
            "label": "DAST (Generic)",
            "icon": "DA",
            "desc": "Dynamic Application Security Testing",
            "stage": "test",
        },
        {
            "type": "scan-zap",
            "label": "OWASP ZAP",
            "icon": "ZP",
            "desc": "OWASP ZAP — industry standard DAST",
            "stage": "test",
            "license": "Apache-2.0",
        },
        {
            "type": "scan-nuclei",
            "label": "Nuclei",
            "icon": "NU",
            "desc": "Template-based vulnerability scanner (8000+ templates)",
            "stage": "test",
            "license": "MIT",
        },
        {
            "type": "scan-burp",
            "label": "Burp Suite",
            "icon": "BS",
            "desc": "Burp Suite — enterprise DAST + manual testing",
            "stage": "test",
        },
        # SCA
        {
            "type": "scan-sca",
            "label": "SCA (Generic)",
            "icon": "SC",
            "desc": "Software Composition Analysis — dependency scanning",
            "stage": "test",
        },
        {
            "type": "scan-trivy",
            "label": "Trivy",
            "icon": "TV",
            "desc": "Aqua Trivy — all-in-one: containers, IaC, SCA, SBOM",
            "stage": "test",
            "license": "Apache-2.0",
        },
        {
            "type": "scan-grype",
            "label": "Grype",
            "icon": "GY",
            "desc": "Anchore Grype vulnerability scanner (pairs with Syft)",
            "stage": "test",
            "license": "Apache-2.0",
        },
        {
            "type": "scan-snyk",
            "label": "Snyk",
            "icon": "SK",
            "desc": "Snyk — SaaS-first SCA + container scanning",
            "stage": "test",
        },
        {
            "type": "scan-dep-check",
            "label": "OWASP Dep-Check",
            "icon": "DC",
            "desc": "OWASP Dependency-Check — NVD-based SCA",
            "stage": "test",
            "license": "Apache-2.0",
        },
        # IaC
        {
            "type": "scan-iac",
            "label": "IaC Scan (Generic)",
            "icon": "IC",
            "desc": "Infrastructure-as-Code security scanning",
            "stage": "test",
        },
        {
            "type": "scan-checkov",
            "label": "Checkov",
            "icon": "CK",
            "desc": "Bridgecrew Checkov — Terraform, K8s, Docker, Helm",
            "stage": "test",
            "license": "Apache-2.0",
        },
        {
            "type": "scan-tfsec",
            "label": "tfsec",
            "icon": "TF",
            "desc": "Terraform-specific scanner (now part of Trivy)",
            "stage": "test",
            "license": "MIT",
        },
        {
            "type": "scan-kics",
            "label": "KICS",
            "icon": "KI",
            "desc": "Checkmarx KICS — multi-platform IaC scanning",
            "stage": "test",
            "license": "Apache-2.0",
        },
        # Secret detection
        {
            "type": "scan-secret",
            "label": "Secret Detection",
            "icon": "SD",
            "desc": "Credential/secret scanning in source code",
            "stage": "pre_commit",
        },
        {
            "type": "scan-gitleaks",
            "label": "Gitleaks",
            "icon": "GK",
            "desc": "Gitleaks — Git history + pre-commit secret detection",
            "stage": "pre_commit",
            "license": "MIT",
        },
        {
            "type": "scan-trufflehog",
            "label": "TruffleHog",
            "icon": "TH",
            "desc": "TruffleHog — entropy + regex, verified secrets",
            "stage": "pre_commit",
            "license": "AGPL",
        },
        {
            "type": "scan-detect-secrets",
            "label": "detect-secrets",
            "icon": "DS",
            "desc": "Yelp detect-secrets — baseline-aware, low false positives",
            "stage": "pre_commit",
            "license": "Apache-2.0",
        },
        # Container scanning
        {
            "type": "scan-container",
            "label": "Container Scan",
            "icon": "CN",
            "desc": "Container image vulnerability scanning",
            "stage": "package",
        },
        {
            "type": "scan-anchore",
            "label": "Anchore Enterprise",
            "icon": "AE",
            "desc": "Anchore — policy-based container vetting (Iron Bank std)",
            "stage": "package",
        },
        {
            "type": "scan-neuvector",
            "label": "NeuVector",
            "icon": "NV",
            "desc": "NeuVector — runtime + scan (Big Bang core, SUSE)",
            "stage": "monitor",
            "license": "Apache-2.0",
        },
        {
            "type": "aws-inspector",
            "label": "Inspector",
            "icon": "IN",
            "desc": "AWS Inspector — container/workload vulnerability scan",
            "stage": "package",
            "csp": "aws",
        },
        {
            "type": "az-defender",
            "label": "Defender for Containers",
            "icon": "DF",
            "desc": "Microsoft Defender — ACR + AKS security",
            "stage": "package",
            "csp": "azure",
        },
        {
            "type": "gcp-artifact-analysis",
            "label": "Artifact Analysis",
            "icon": "AA",
            "desc": "GCP container analysis + SBOM + VEX",
            "stage": "package",
            "csp": "gcp",
        },
        {
            "type": "ibm-vuln-advisor",
            "label": "Vulnerability Advisor",
            "icon": "VA",
            "desc": "IBM Container Registry Vulnerability Advisor",
            "stage": "package",
            "csp": "ibm",
        },
        # License scanning
        {
            "type": "scan-license",
            "label": "License Scanner",
            "icon": "LS",
            "desc": "Open source license compliance scanner",
            "stage": "test",
        },
    ],
    "artifact_management": [
        {
            "type": "registry-generic",
            "label": "Container Registry",
            "icon": "CR",
            "desc": "Generic OCI-compliant container registry",
            "stage": "package",
        },
        {
            "type": "registry-harbor",
            "label": "Harbor",
            "icon": "HB",
            "desc": "CNCF Harbor — scanning, signing, replication, RBAC",
            "stage": "package",
            "license": "Apache-2.0",
        },
        {
            "type": "registry-nexus",
            "label": "Nexus Repository",
            "icon": "NX",
            "desc": "Sonatype Nexus — multi-format (Docker, Maven, npm, PyPI)",
            "stage": "package",
        },
        {
            "type": "registry-jfrog",
            "label": "JFrog Artifactory",
            "icon": "JF",
            "desc": "JFrog Artifactory — universal artifact management",
            "stage": "package",
        },
        {
            "type": "registry-zot",
            "label": "Zot",
            "icon": "ZT",
            "desc": "OCI-native lightweight registry",
            "stage": "package",
            "license": "Apache-2.0",
        },
        {
            "type": "aws-ecr",
            "label": "ECR",
            "icon": "EC",
            "desc": "AWS Elastic Container Registry",
            "stage": "package",
            "csp": "aws",
        },
        {
            "type": "az-acr",
            "label": "ACR",
            "icon": "AC",
            "desc": "Azure Container Registry (+ ACR Tasks)",
            "stage": "package",
            "csp": "azure",
        },
        {
            "type": "gcp-gar",
            "label": "Artifact Registry",
            "icon": "GA",
            "desc": "GCP Artifact Registry (multi-format + remote repos)",
            "stage": "package",
            "csp": "gcp",
        },
        {
            "type": "oci-cr",
            "label": "OCI Container Registry",
            "icon": "OC",
            "desc": "OCI Container Registry (Docker V2, Helm)",
            "stage": "package",
            "csp": "oci",
        },
        {
            "type": "ibm-cr",
            "label": "IBM Container Registry",
            "icon": "IR",
            "desc": "IBM Cloud Container Registry + Vulnerability Advisor",
            "stage": "package",
            "csp": "ibm",
        },
        {
            "type": "registry-ironbank",
            "label": "Iron Bank",
            "icon": "IB",
            "desc": "DoD Platform One Iron Bank — hardened, vetted base images",
            "stage": "package",
        },
        {
            "type": "sbom-store",
            "label": "SBOM Store",
            "icon": "SB",
            "desc": "Software Bill of Materials storage/archive",
            "stage": "package",
        },
        {
            "type": "package-repo",
            "label": "Package Repo",
            "icon": "PK",
            "desc": "Language package repository (npm, PyPI, Maven, NuGet)",
            "stage": "package",
        },
    ],
    "supply_chain": [
        {
            "type": "sign-cosign",
            "label": "Cosign",
            "icon": "CO",
            "desc": "Sigstore Cosign — keyless or key-pair image signing",
            "stage": "package",
            "license": "Apache-2.0",
        },
        {
            "type": "sign-notation",
            "label": "Notation",
            "icon": "NT",
            "desc": "CNCF Notary v2 / Notation — OCI artifact signing",
            "stage": "package",
            "license": "Apache-2.0",
        },
        {
            "type": "sign-dct",
            "label": "Docker Content Trust",
            "icon": "DT",
            "desc": "Docker Content Trust via Notary v1",
            "stage": "package",
        },
        {
            "type": "attest-in-toto",
            "label": "in-toto",
            "icon": "IT",
            "desc": "in-toto supply chain layout verification + attestation",
            "stage": "package",
            "license": "Apache-2.0",
        },
        {
            "type": "attest-slsa-gen",
            "label": "SLSA Generator",
            "icon": "SL",
            "desc": "SLSA provenance generator (L1-L3 attestation)",
            "stage": "package",
        },
        {
            "type": "verify-slsa",
            "label": "SLSA Verifier",
            "icon": "SV",
            "desc": "SLSA provenance verification before deployment",
            "stage": "policy_gate",
        },
        {
            "type": "sbom-syft",
            "label": "Syft (SBOM)",
            "icon": "SY",
            "desc": "Anchore Syft — CycloneDX + SPDX SBOM generator",
            "stage": "build",
            "license": "Apache-2.0",
        },
        {
            "type": "sbom-cyclonedx",
            "label": "CycloneDX Tools",
            "icon": "CD",
            "desc": "OWASP CycloneDX SBOM format + language-specific tools",
            "stage": "build",
            "license": "Apache-2.0",
        },
        {
            "type": "sbom-spdx",
            "label": "SPDX Tools",
            "icon": "SP",
            "desc": "Linux Foundation SPDX SBOM format (ISO/IEC 5962:2021)",
            "stage": "build",
            "license": "Apache-2.0",
        },
        {
            "type": "vex-openvex",
            "label": "OpenVEX",
            "icon": "VX",
            "desc": "Vulnerability Exploitability eXchange — suppress known FPs",
            "stage": "package",
        },
        {
            "type": "gcp-binary-auth",
            "label": "Binary Authorization",
            "icon": "BA",
            "desc": "GCP Binary Authorization — deploy-time attestation gate",
            "stage": "policy_gate",
            "csp": "gcp",
        },
        {
            "type": "ibm-portieris",
            "label": "Portieris",
            "icon": "PT",
            "desc": "IBM Portieris — K8s image admission controller",
            "stage": "policy_gate",
            "license": "Apache-2.0",
        },
        # Rust-specific supply chain provenance
        {
            "type": "sc-cargo-vet",
            "label": "cargo-vet",
            "icon": "CV",
            "desc": "Mozilla cargo-vet — Rust dependency audit enforcement; requires explicit audits for each crate version before use in CI",
            "stage": "build",
            "license": "MIT/Apache-2.0",
        },
        {
            "type": "sc-cargo-auditable",
            "label": "cargo-auditable",
            "icon": "CA",
            "desc": "rust-secure-code/cargo-auditable — embeds full dependency tree in Rust binaries for post-build SBOM extraction and vulnerability scanning",
            "stage": "build",
            "license": "MIT/Apache-2.0",
        },
    ],
    "policy_governance": [
        {
            "type": "policy-opa",
            "label": "OPA / Rego",
            "icon": "OP",
            "desc": "Open Policy Agent — general-purpose Rego policy engine",
            "stage": "policy_gate",
            "license": "Apache-2.0",
        },
        {
            "type": "policy-kyverno",
            "label": "Kyverno",
            "icon": "KV",
            "desc": "Kubernetes-native YAML policy engine (Big Bang core)",
            "stage": "policy_gate",
            "license": "Apache-2.0",
        },
        {
            "type": "policy-gatekeeper",
            "label": "Gatekeeper",
            "icon": "GK",
            "desc": "OPA Gatekeeper — K8s admission controller",
            "stage": "policy_gate",
            "license": "Apache-2.0",
        },
        {
            "type": "policy-kubewarden",
            "label": "Kubewarden",
            "icon": "KW",
            "desc": "WebAssembly-based K8s policy engine",
            "stage": "policy_gate",
            "license": "Apache-2.0",
        },
        {
            "type": "aws-config",
            "label": "AWS Config Rules",
            "icon": "CF",
            "desc": "AWS Config — continuous resource compliance evaluation",
            "stage": "compliance",
            "csp": "aws",
        },
        {
            "type": "az-policy",
            "label": "Azure Policy",
            "icon": "AZ",
            "desc": "Azure Policy + Gatekeeper for governance",
            "stage": "compliance",
            "csp": "azure",
        },
        {
            "type": "gate-manual",
            "label": "Manual Approval",
            "icon": "MG",
            "desc": "Manual approval gate (human-in-the-loop)",
            "stage": "approval",
        },
        {
            "type": "gate-automated",
            "label": "Auto Gate",
            "icon": "AG",
            "desc": "Automated condition-based gate (alarm check, test pass)",
            "stage": "policy_gate",
        },
        {
            "type": "gate-vuln-threshold",
            "label": "Vuln Threshold",
            "icon": "VT",
            "desc": "Vulnerability count/severity threshold gate",
            "stage": "policy_gate",
        },
        {
            "type": "gate-deploy-window",
            "label": "Deploy Window",
            "icon": "DW",
            "desc": "Time-based deployment window restriction",
            "stage": "approval",
        },
    ],
    "secrets_keys": [
        {
            "type": "vault-hashicorp",
            "label": "HashiCorp Vault",
            "icon": "HV",
            "desc": "Vault — dynamic secrets, PKI, encryption-as-a-service",
            "stage": "build",
            "license": "BUSL",
        },
        {
            "type": "vault-openbao",
            "label": "OpenBao",
            "icon": "OB",
            "desc": "OpenBao — fully OSS Vault fork (Linux Foundation)",
            "stage": "build",
            "license": "MPL-2.0",
        },
        {
            "type": "aws-secrets",
            "label": "Secrets Manager",
            "icon": "SM",
            "desc": "AWS Secrets Manager — rotation, IAM access",
            "stage": "build",
            "csp": "aws",
        },
        {
            "type": "aws-kms",
            "label": "AWS KMS",
            "icon": "KM",
            "desc": "AWS Key Management Service",
            "stage": "build",
            "csp": "aws",
        },
        {
            "type": "az-keyvault",
            "label": "Key Vault",
            "icon": "KV",
            "desc": "Azure Key Vault — secrets, keys, certificates",
            "stage": "build",
            "csp": "azure",
        },
        {
            "type": "gcp-secret",
            "label": "Secret Manager",
            "icon": "GS",
            "desc": "GCP Secret Manager — versioning, rotation, IAM",
            "stage": "build",
            "csp": "gcp",
        },
        {
            "type": "gcp-kms",
            "label": "Cloud KMS",
            "icon": "CK",
            "desc": "GCP Cloud KMS + Cloud HSM (FIPS 140-2 L3)",
            "stage": "build",
            "csp": "gcp",
        },
        {
            "type": "oci-vault",
            "label": "OCI Vault",
            "icon": "OV",
            "desc": "OCI Vault — managed key and secret storage",
            "stage": "build",
            "csp": "oci",
        },
        {
            "type": "ibm-secrets",
            "label": "IBM Secrets Manager",
            "icon": "IS",
            "desc": "IBM Cloud Secrets Manager",
            "stage": "build",
            "csp": "ibm",
        },
        {
            "type": "ibm-hpcs",
            "label": "Hyper Protect Crypto",
            "icon": "HP",
            "desc": "IBM HPCS — FIPS 140-2 Level 4 HSM (highest)",
            "stage": "build",
            "csp": "ibm",
        },
        {
            "type": "kms-generic",
            "label": "KMS",
            "icon": "KM",
            "desc": "Generic Key Management Service",
            "stage": "build",
        },
        {
            "type": "hsm-generic",
            "label": "HSM",
            "icon": "HS",
            "desc": "Hardware Security Module (FIPS 140-2/3)",
            "stage": "build",
        },
        {
            "type": "cert-manager",
            "label": "cert-manager",
            "icon": "CM",
            "desc": "K8s certificate automation (Let's Encrypt, Venafi)",
            "stage": "build",
            "license": "Apache-2.0",
        },
        {
            "type": "sealed-secrets",
            "label": "Sealed Secrets",
            "icon": "SS",
            "desc": "Bitnami Sealed Secrets — encrypt secrets for Git storage",
            "stage": "build",
            "license": "Apache-2.0",
        },
        {
            "type": "sops",
            "label": "SOPS",
            "icon": "SO",
            "desc": "Mozilla SOPS — encrypt files with KMS/PGP/age",
            "stage": "build",
            "license": "MPL",
        },
        {
            "type": "external-secrets",
            "label": "External Secrets",
            "icon": "ES",
            "desc": "K8s operator syncing from Vault/AWS/Azure/GCP",
            "stage": "build",
            "license": "Apache-2.0",
        },
    ],
    "deploy_targets": [
        {
            "type": "k8s-cluster",
            "label": "Kubernetes",
            "icon": "K8",
            "desc": "Generic Kubernetes cluster",
            "stage": "deploy_prod",
            "license": "Apache-2.0",
        },
        {
            "type": "aws-eks",
            "label": "EKS",
            "icon": "EK",
            "desc": "AWS Elastic Kubernetes Service",
            "stage": "deploy_prod",
            "csp": "aws",
        },
        {
            "type": "az-aks",
            "label": "AKS",
            "icon": "AK",
            "desc": "Azure Kubernetes Service",
            "stage": "deploy_prod",
            "csp": "azure",
        },
        {
            "type": "gcp-gke",
            "label": "GKE",
            "icon": "GK",
            "desc": "Google Kubernetes Engine",
            "stage": "deploy_prod",
            "csp": "gcp",
        },
        {
            "type": "oci-oke",
            "label": "OKE",
            "icon": "OK",
            "desc": "OCI Kubernetes Engine",
            "stage": "deploy_prod",
            "csp": "oci",
        },
        {
            "type": "ibm-iks",
            "label": "IKS",
            "icon": "IK",
            "desc": "IBM Kubernetes Service",
            "stage": "deploy_prod",
            "csp": "ibm",
        },
        {
            "type": "openshift",
            "label": "OpenShift",
            "icon": "OS",
            "desc": "Red Hat OpenShift (enterprise K8s + built-in security)",
            "stage": "deploy_prod",
        },
        {
            "type": "rke2",
            "label": "RKE2",
            "icon": "R2",
            "desc": "Rancher RKE2 — hardened K8s, CIS-compliant by default",
            "stage": "deploy_prod",
            "license": "Apache-2.0",
        },
        {
            "type": "k3s",
            "label": "K3s",
            "icon": "K3",
            "desc": "Rancher K3s — lightweight K8s for edge/resource-constrained",
            "stage": "deploy_prod",
            "license": "Apache-2.0",
        },
        {
            "type": "deploy-bigbang",
            "label": "Big Bang",
            "icon": "BB",
            "desc": "DoD Platform One Big Bang — GitOps K8s deployment bundle",
            "stage": "deploy_prod",
        },
        {
            "type": "deploy-serverless",
            "label": "Serverless",
            "icon": "SL",
            "desc": "Serverless function target (Lambda/Functions/Cloud Run)",
            "stage": "deploy_prod",
        },
        {
            "type": "deploy-vm",
            "label": "VM Target",
            "icon": "VM",
            "desc": "Virtual machine deployment target (EC2/Azure VM/GCE)",
            "stage": "deploy_prod",
        },
        {
            "type": "deploy-edge",
            "label": "Edge Device",
            "icon": "ED",
            "desc": "Edge/IoT deployment target (K3s/MicroK8s)",
            "stage": "deploy_prod",
        },
        {
            "type": "deploy-canary",
            "label": "Canary Deploy",
            "icon": "CY",
            "desc": "Canary deployment strategy (progressive rollout)",
            "stage": "deploy_prod",
        },
        {
            "type": "deploy-bluegreen",
            "label": "Blue-Green Deploy",
            "icon": "BG",
            "desc": "Blue-green deployment strategy (zero-downtime cutover)",
            "stage": "deploy_prod",
        },
        {
            "type": "deploy-feature-flag",
            "label": "Feature Flag",
            "icon": "FF",
            "desc": "Feature flag service (LaunchDarkly, Unleash, Flagsmith)",
            "stage": "deploy_prod",
        },
        # K8s cluster hygiene
        {
            "type": "k8s-eraser",
            "label": "Eraser",
            "icon": "ER",
            "desc": "eraser-dev/eraser — K8s unused container image garbage collection; removes non-running images from cluster nodes to reclaim disk",
            "stage": "monitor",
            "license": "Apache-2.0",
        },
    ],
    "monitoring_response": [
        {
            "type": "mon-prometheus",
            "label": "Prometheus",
            "icon": "PM",
            "desc": "Prometheus metrics collection (Big Bang core)",
            "stage": "monitor",
            "license": "Apache-2.0",
        },
        {
            "type": "mon-grafana",
            "label": "Grafana",
            "icon": "GR",
            "desc": "Grafana dashboards/alerting (Big Bang core)",
            "stage": "monitor",
            "license": "AGPL",
        },
        {
            "type": "mon-loki",
            "label": "Loki",
            "icon": "LK",
            "desc": "Grafana Loki — log aggregation (Big Bang core)",
            "stage": "monitor",
            "license": "AGPL",
        },
        {
            "type": "mon-tempo",
            "label": "Tempo",
            "icon": "TP",
            "desc": "Grafana Tempo — distributed tracing (Big Bang core)",
            "stage": "monitor",
            "license": "AGPL",
        },
        {
            "type": "mon-otel",
            "label": "OpenTelemetry",
            "icon": "OT",
            "desc": "CNCF OpenTelemetry — vendor-neutral telemetry collection",
            "stage": "monitor",
            "license": "Apache-2.0",
        },
        {
            "type": "mon-elk",
            "label": "ELK Stack",
            "icon": "EL",
            "desc": "Elasticsearch + Logstash + Kibana (Big Bang alt)",
            "stage": "monitor",
        },
        {
            "type": "mon-fluentbit",
            "label": "Fluent Bit",
            "icon": "FB",
            "desc": "Lightweight log forwarder (Big Bang core)",
            "stage": "monitor",
            "license": "Apache-2.0",
        },
        {
            "type": "aws-cloudwatch",
            "label": "CloudWatch",
            "icon": "CW",
            "desc": "AWS CloudWatch — metrics, logs, alarms, X-Ray",
            "stage": "monitor",
            "csp": "aws",
        },
        {
            "type": "aws-guardduty",
            "label": "GuardDuty",
            "icon": "GD",
            "desc": "AWS GuardDuty — threat detection",
            "stage": "monitor",
            "csp": "aws",
        },
        {
            "type": "az-monitor",
            "label": "Azure Monitor",
            "icon": "AM",
            "desc": "Azure Monitor + Container Insights + Log Analytics",
            "stage": "monitor",
            "csp": "azure",
        },
        {
            "type": "az-sentinel",
            "label": "Sentinel",
            "icon": "SE",
            "desc": "Microsoft Sentinel — cloud-native SIEM",
            "stage": "monitor",
            "csp": "azure",
        },
        {
            "type": "gcp-monitoring",
            "label": "Cloud Monitoring",
            "icon": "GM",
            "desc": "GCP Cloud Monitoring + Logging + Trace",
            "stage": "monitor",
            "csp": "gcp",
        },
        {
            "type": "gcp-scc",
            "label": "Security Command Center",
            "icon": "SC",
            "desc": "GCP SCC — unified security + risk dashboard",
            "stage": "monitor",
            "csp": "gcp",
        },
        {
            "type": "mon-falco",
            "label": "Falco",
            "icon": "FC",
            "desc": "Falco — runtime security monitoring (eBPF syscall)",
            "stage": "monitor",
            "license": "Apache-2.0",
        },
        {
            "type": "mon-wazuh",
            "label": "Wazuh",
            "icon": "WZ",
            "desc": "Wazuh — open source SIEM + XDR (OSSEC fork)",
            "stage": "monitor",
            "license": "GPL",
        },
        {
            "type": "mon-soar",
            "label": "SOAR",
            "icon": "SR",
            "desc": "Security Orchestration, Automation & Response",
            "stage": "monitor",
        },
        {
            "type": "mon-pagerduty",
            "label": "PagerDuty",
            "icon": "PD",
            "desc": "PagerDuty incident management + alerting",
            "stage": "monitor",
        },
    ],
    "compliance_audit": [
        {
            "type": "comp-dashboard",
            "label": "Compliance Dashboard",
            "icon": "CD",
            "desc": "Compliance status dashboard + posture management",
            "stage": "compliance",
        },
        {
            "type": "comp-evidence",
            "label": "Evidence Locker",
            "icon": "EL",
            "desc": "Compliance evidence collection store (Git or COS)",
            "stage": "compliance",
        },
        {
            "type": "comp-oscal",
            "label": "OSCAL Export",
            "icon": "OX",
            "desc": "NIST OSCAL compliance export (SSP, POAM, AR)",
            "stage": "compliance",
        },
        {
            "type": "comp-stigman",
            "label": "STIG Manager",
            "icon": "SM",
            "desc": "DISA STIG Manager integration",
            "stage": "compliance",
        },
        {
            "type": "comp-openscap",
            "label": "OpenSCAP",
            "icon": "OS",
            "desc": "OpenSCAP — SCAP content scanner + remediation",
            "stage": "compliance",
            "license": "LGPL",
        },
        {
            "type": "comp-inspec",
            "label": "InSpec",
            "icon": "IS",
            "desc": "Chef InSpec — compliance-as-code for infrastructure",
            "stage": "compliance",
        },
        {
            "type": "aws-securityhub",
            "label": "Security Hub",
            "icon": "SH",
            "desc": "AWS Security Hub — CSPM + compliance",
            "stage": "compliance",
            "csp": "aws",
        },
        {
            "type": "aws-audit",
            "label": "Audit Manager",
            "icon": "AU",
            "desc": "AWS Audit Manager — compliance evidence collection",
            "stage": "compliance",
            "csp": "aws",
        },
        {
            "type": "az-defender-cloud",
            "label": "Defender for Cloud",
            "icon": "DC",
            "desc": "Microsoft Defender for Cloud — CSPM",
            "stage": "compliance",
            "csp": "azure",
        },
        {
            "type": "ibm-scc",
            "label": "IBM SCC",
            "icon": "SC",
            "desc": "IBM Security and Compliance Center — posture mgmt",
            "stage": "compliance",
            "csp": "ibm",
        },
    ],
    "cross_domain": [
        {
            "type": "cds-guard",
            "label": "Cross-Domain Guard",
            "icon": "GD",
            "desc": "Cross-Domain Solution guard (Owl, BAE, Forcepoint)",
            "stage": "deploy_prod",
        },
        {
            "type": "cds-data-diode",
            "label": "Data Diode",
            "icon": "DD",
            "desc": "Hardware one-way data diode (air-gap transfer)",
            "stage": "deploy_prod",
        },
        {
            "type": "cds-emulator",
            "label": "CDS Emulator",
            "icon": "EM",
            "desc": "Cross-domain emulator for dev/test (simulates guard)",
            "stage": "deploy_staging",
        },
        {
            "type": "cds-transfer",
            "label": "Transfer Service",
            "icon": "TS",
            "desc": "Managed cross-domain transfer service (ITAR, CUI→SECRET)",
            "stage": "deploy_prod",
        },
        {
            "type": "boundary-commercial",
            "label": "Commercial Cloud",
            "icon": "CC",
            "desc": "Commercial cloud boundary (IL2, unclassified)",
            "stage": "deploy_prod",
        },
        {
            "type": "boundary-govcloud",
            "label": "GovCloud",
            "icon": "GC",
            "desc": "GovCloud boundary (IL4/IL5, CUI, ITAR)",
            "stage": "deploy_prod",
        },
        {
            "type": "boundary-secret",
            "label": "Secret Cloud",
            "icon": "SC",
            "desc": "Secret cloud boundary (IL6, classified SIPR)",
            "stage": "deploy_prod",
        },
        {
            "type": "boundary-topsecret",
            "label": "Top Secret Cloud",
            "icon": "TS",
            "desc": "Top Secret cloud boundary (JWICS, SCI)",
            "stage": "deploy_prod",
        },
        {
            "type": "pipeline-nipr",
            "label": "NIPR Pipeline",
            "icon": "NP",
            "desc": "Unclassified pipeline (NIPRNet)",
            "stage": "build",
        },
        {
            "type": "pipeline-sipr",
            "label": "SIPR Pipeline",
            "icon": "SP",
            "desc": "Classified pipeline (SIPRNet, air-gapped)",
            "stage": "build",
        },
        {
            "type": "pipeline-jwics",
            "label": "JWICS Pipeline",
            "icon": "JP",
            "desc": "Top Secret/SCI pipeline (JWICS, air-gapped)",
            "stage": "build",
        },
        {
            "type": "sneakernet",
            "label": "Sneakernet Transfer",
            "icon": "SN",
            "desc": "Physical media transfer for air-gapped environments",
            "stage": "deploy_prod",
        },
        {
            "type": "vuln-db-mirror",
            "label": "Vuln DB Mirror",
            "icon": "VM",
            "desc": "Offline vulnerability database mirror (NVD, OSV, Trivy)",
            "stage": "test",
        },
        {
            "type": "package-mirror",
            "label": "Package Mirror",
            "icon": "PM",
            "desc": "Air-gapped package mirror (devpi, Verdaccio, Athens)",
            "stage": "build",
        },
    ],
    "service_mesh": [
        {
            "type": "mesh-istio",
            "label": "Istio",
            "icon": "IS",
            "desc": "Istio service mesh — mTLS, traffic policies (Big Bang)",
            "stage": "deploy_prod",
            "license": "Apache-2.0",
        },
        {
            "type": "mesh-linkerd",
            "label": "Linkerd",
            "icon": "LD",
            "desc": "Linkerd service mesh — lightweight, CNCF graduated",
            "stage": "deploy_prod",
            "license": "Apache-2.0",
        },
        {
            "type": "mesh-consul",
            "label": "Consul Connect",
            "icon": "CC",
            "desc": "HashiCorp Consul Connect service mesh",
            "stage": "deploy_prod",
        },
    ],
    "sre_reliability": [
        # SLO / SLI / Error Budget
        {
            "type": "sre-slo",
            "label": "SLO Definition",
            "icon": "SLO",
            "desc": "Service Level Objective — availability, latency p95/p99, error rate, throughput targets with error budget tracking. Uses ICDEV SLO Manager.",
            "stage": "sre",
        },
        {
            "type": "sre-sli",
            "label": "SLI Metric",
            "icon": "SLI",
            "desc": "Service Level Indicator — measurable metric feeding an SLO (request latency, error ratio, availability %). Collected via Prometheus/OTel.",
            "stage": "sre",
        },
        {
            "type": "sre-error-budget",
            "label": "Error Budget",
            "icon": "EB",
            "desc": "Error budget policy — remaining failure allowance. Burns faster on incidents; when exhausted, freezes feature deploys until budget recovers.",
            "stage": "sre",
        },
        {
            "type": "sre-burn-rate",
            "label": "Burn Rate Alert",
            "icon": "BR",
            "desc": "Multi-window burn rate alerting — fast burn (2h window, 14.4x) and slow burn (6h window, 6x) per Google SRE Workbook.",
            "stage": "sre",
        },
        # Incident Management
        {
            "type": "sre-incident",
            "label": "Incident Manager",
            "icon": "INC",
            "desc": "Incident lifecycle: created → triaging → mitigating → resolved → postmortem → closed. Uses ICDEV Incident Commander with MTTR tracking.",
            "stage": "sre",
        },
        {
            "type": "sre-postmortem",
            "label": "Postmortem",
            "icon": "PM",
            "desc": "Blameless postmortem template — timeline, root cause, impact, action items, lessons learned. Required for SEV1/SEV2.",
            "stage": "sre",
        },
        {
            "type": "sre-oncall",
            "label": "On-Call Schedule",
            "icon": "OC",
            "desc": "On-call rotation management — primary/secondary, escalation policies, handoff procedures.",
            "stage": "sre",
        },
        {
            "type": "sre-statuspage",
            "label": "Status Page",
            "icon": "SP",
            "desc": "Internal/external service status page — component status, incident communication, maintenance windows.",
            "stage": "sre",
        },
        # Runbooks & Automation
        {
            "type": "sre-runbook",
            "label": "Runbook",
            "icon": "RB",
            "desc": "Automated runbook — regex-triggered, risk-tiered (green/yellow/orange), step-by-step with rollback. Uses ICDEV Runbook Executor.",
            "stage": "sre",
        },
        {
            "type": "sre-self-heal",
            "label": "Self-Healing",
            "icon": "SH",
            "desc": "Self-healing automation — confidence-scored remediation (>=0.7 auto, 0.3-0.7 suggest, <0.3 escalate). Uses ICDEV Self-Heal Analyzer.",
            "stage": "sre",
        },
        # Chaos Engineering
        {
            "type": "sre-chaos",
            "label": "Chaos Experiment",
            "icon": "CX",
            "desc": "Chaos engineering experiment — fault injection with steady-state hypothesis validation and automatic rollback on SLO breach.",
            "stage": "sre",
        },
        {
            "type": "sre-chaos-litmus",
            "label": "Litmus Chaos",
            "icon": "LT",
            "desc": "LitmusChaos — CNCF K8s-native chaos: pod kill, network loss, CPU stress, disk fill. CRD-based experiments.",
            "stage": "sre",
            "license": "Apache-2.0",
        },
        {
            "type": "aws-fis",
            "label": "AWS FIS",
            "icon": "FI",
            "desc": "AWS Fault Injection Service — managed chaos: EC2, ECS, EKS, RDS, AZ disruption with CloudWatch stop conditions.",
            "stage": "sre",
            "csp": "aws",
        },
        {
            "type": "az-chaos-studio",
            "label": "Chaos Studio",
            "icon": "CS",
            "desc": "Azure Chaos Studio — managed fault injection: VM, AKS, Cosmos DB, NSG, Key Vault, Redis.",
            "stage": "sre",
            "csp": "azure",
        },
        # DORA Metrics
        {
            "type": "sre-dora",
            "label": "DORA Metrics",
            "icon": "DO",
            "desc": "DORA 4 Key Metrics dashboard — deployment frequency, lead time, change failure rate, MTTR. The bridge between DevOps velocity and reliability.",
            "stage": "sre",
        },
        {
            "type": "sre-dora-deploy-freq",
            "label": "Deploy Frequency",
            "icon": "DF",
            "desc": "DORA: How often code deploys to production. Elite = multiple/day. Measured from CI/CD pipeline completion events.",
            "stage": "sre",
        },
        {
            "type": "sre-dora-lead-time",
            "label": "Lead Time",
            "icon": "LT",
            "desc": "DORA: Commit to production. Elite = <1 hour. Measured from git commit timestamp to deploy timestamp.",
            "stage": "sre",
        },
        {
            "type": "sre-dora-cfr",
            "label": "Change Failure Rate",
            "icon": "CF",
            "desc": "DORA: % of deployments causing failure/rollback. Elite = <5%. Tracks security-caused rollbacks separately.",
            "stage": "sre",
        },
        {
            "type": "sre-dora-mttr",
            "label": "MTTR",
            "icon": "MT",
            "desc": "DORA: Mean Time to Restore service. Elite = <1 hour. Correlates with error budget — faster MTTR = less budget consumed.",
            "stage": "sre",
        },
        # Resilience Scoring
        {
            "type": "sre-resilience",
            "label": "Resilience Score",
            "icon": "RS",
            "desc": "Composite resilience score (0-100) combining SLO compliance, incident MTTR, chaos test results, and DORA metrics.",
            "stage": "sre",
        },
        {
            "type": "aws-resilience-hub",
            "label": "Resilience Hub",
            "icon": "RH",
            "desc": "AWS Resilience Hub — RTO/RPO assessment, resilience score, drift detection.",
            "stage": "sre",
            "csp": "aws",
        },
        # CSP SLO/Observability services
        {
            "type": "aws-cw-slo",
            "label": "CloudWatch SLO",
            "icon": "CL",
            "desc": "AWS CloudWatch Service Level Objectives — native SLO definition with burn-rate alerting (launched 2024).",
            "stage": "sre",
            "csp": "aws",
        },
        {
            "type": "aws-incident-mgr",
            "label": "Incident Manager",
            "icon": "IM",
            "desc": "AWS Systems Manager Incident Manager — auto-create incidents from alarms, runbook execution, on-call schedules.",
            "stage": "sre",
            "csp": "aws",
        },
        {
            "type": "gcp-service-mon",
            "label": "Service Monitoring",
            "icon": "SM",
            "desc": "GCP Service Monitoring — first-class SLO API with auto-discovered services, error budget burn-rate alerts.",
            "stage": "sre",
            "csp": "gcp",
        },
        {
            "type": "az-advisor-rel",
            "label": "Advisor Reliability",
            "icon": "AR",
            "desc": "Azure Advisor Reliability Score — per-subscription score based on Well-Architected Framework.",
            "stage": "sre",
            "csp": "azure",
        },
        {
            "type": "ibm-instana",
            "label": "Instana",
            "icon": "IN",
            "desc": "IBM Instana — full-stack APM with 1-second granularity, auto-discovery, native SLO management.",
            "stage": "sre",
            "csp": "ibm",
        },
        # OpenSLO / SLO frameworks
        {
            "type": "sre-openslo",
            "label": "OpenSLO",
            "icon": "OS",
            "desc": "OpenSLO spec — vendor-neutral YAML for SLO/SLI/alert policy definitions. Define once, deploy to any backend.",
            "stage": "sre",
        },
        {
            "type": "sre-sloth",
            "label": "Sloth",
            "icon": "SL",
            "desc": "Sloth — generates Prometheus recording rules and multi-window burn-rate alerts from SLO YAML definitions.",
            "stage": "sre",
            "license": "Apache-2.0",
        },
        {
            "type": "sre-pyrra",
            "label": "Pyrra",
            "icon": "PY",
            "desc": "Pyrra — K8s-native SLO controller with web UI, reads SLO CRDs, generates Prometheus rules.",
            "stage": "sre",
            "license": "Apache-2.0",
        },
        # On-call / Incident
        {
            "type": "sre-pagerduty",
            "label": "PagerDuty",
            "icon": "PD",
            "desc": "PagerDuty — incident management, on-call scheduling, event intelligence (ML alert grouping), runbook automation.",
            "stage": "sre",
        },
        {
            "type": "sre-grafana-oncall",
            "label": "Grafana OnCall",
            "icon": "GO",
            "desc": "Grafana OnCall — open-source on-call management with schedules, escalations, and integrations.",
            "stage": "sre",
            "license": "Apache-2.0",
        },
        {
            "type": "sre-opsgenie",
            "label": "Opsgenie",
            "icon": "OG",
            "desc": "Atlassian Opsgenie — alert management, on-call scheduling, incident response orchestration.",
            "stage": "sre",
        },
        # Service Catalog
        {
            "type": "sre-backstage",
            "label": "Backstage",
            "icon": "BS",
            "desc": "Spotify Backstage — developer portal with service catalog, ownership, SLO dashboards, tech docs. Reduces MTTR by centralizing 'who owns this?'.",
            "stage": "sre",
            "license": "Apache-2.0",
        },
        # Resilience primitives
        {
            "type": "sre-circuit-breaker",
            "label": "Circuit Breaker",
            "icon": "CB",
            "desc": "Circuit breaker pattern — CLOSED/OPEN/HALF_OPEN state machine. Prevents cascade failures. Uses ICDEV circuit_breaker.py.",
            "stage": "sre",
        },
        {
            "type": "sre-retry",
            "label": "Retry + Backoff",
            "icon": "RT",
            "desc": "Retry with exponential backoff + jitter — handles transient failures without overwhelming the upstream service.",
            "stage": "sre",
        },
        {
            "type": "sre-bulkhead",
            "label": "Bulkhead",
            "icon": "BH",
            "desc": "Bulkhead isolation pattern — limits concurrent requests per service to prevent resource exhaustion cascade.",
            "stage": "sre",
        },
    ],
    "infrastructure_bridge": [
        # NDC Bridge — links PDC pipeline to an NDC topology for infrastructure
        {
            "type": "ndc-topology",
            "label": "NDC Topology",
            "icon": "NET",
            "desc": "Link to Network Design Canvas topology — provides VPC, subnets, connectivity (DirectConnect, ExpressRoute, VPN), compliance, and ATO package. Click to select topology.",
            "stage": "deploy_prod",
        },
        {
            "type": "ndc-vpc",
            "label": "NDC VPC/VNet",
            "icon": "VPC",
            "desc": "Reference a VPC/VNet from a linked NDC topology — inherits subnets, security groups, and route tables.",
            "stage": "deploy_prod",
        },
        # Hybrid connectivity (references NDC but visible in PDC for pipeline awareness)
        {
            "type": "hybrid-directconnect",
            "label": "Direct Connect",
            "icon": "DX",
            "desc": "AWS Direct Connect / Azure ExpressRoute / GCP Interconnect — dedicated circuit from on-prem to cloud. Design in NDC, reference here.",
            "stage": "deploy_prod",
        },
        {
            "type": "hybrid-vpn",
            "label": "Site-to-Site VPN",
            "icon": "VPN",
            "desc": "IPSec VPN tunnel between on-prem and cloud. Design in NDC, reference here.",
            "stage": "deploy_prod",
        },
        {
            "type": "hybrid-transit",
            "label": "Transit Hub",
            "icon": "TGW",
            "desc": "Transit Gateway / Virtual WAN / NCC — multi-VPC/multi-cloud hub. Design in NDC, reference here.",
            "stage": "deploy_prod",
        },
        {
            "type": "hybrid-peering",
            "label": "Cloud Peering",
            "icon": "PER",
            "desc": "Cross-cloud peering (AWS↔Azure, GCP↔AWS). Design in NDC, reference here.",
            "stage": "deploy_prod",
        },
        # On-premises endpoints
        {
            "type": "onprem-datacenter",
            "label": "On-Prem DC",
            "icon": "DC",
            "desc": "On-premises data center — source or target for hybrid pipeline deployment.",
            "stage": "deploy_prod",
        },
        {
            "type": "onprem-colo",
            "label": "Colocation",
            "icon": "CO",
            "desc": "Colocation facility — meet-me room, cross-connects. Design in NDC.",
            "stage": "deploy_prod",
        },
        {
            "type": "onprem-edge",
            "label": "Edge Site",
            "icon": "ED",
            "desc": "Edge / tactical site — K3s, limited connectivity, intermittent cloud access.",
            "stage": "deploy_prod",
        },
    ],
    "desktop_image": [
        {
            "type": "img-source",
            "label": "Base Image Source",
            "icon": "ISO",
            "desc": "OS base image — Windows Server/10/11 ISO or marketplace image from Iron Bank/Azure Gallery",
        },
        {
            "type": "img-customize",
            "label": "Image Customization",
            "icon": "CST",
            "desc": "Packer / Azure Image Builder / EC2 Image Builder — install agents, drivers, apps",
        },
        {
            "type": "img-harden",
            "label": "STIG Hardening",
            "icon": "STG",
            "desc": "Apply DISA STIG GPOs — Windows VDI STIG, .NET STIG, browser STIG",
        },
        {
            "type": "img-optimize",
            "label": "VDI Optimization",
            "icon": "OPT",
            "desc": "Citrix Optimizer / VMware OSOT / BIS-F — strip unnecessary services for VDI",
        },
        {
            "type": "img-scan",
            "label": "Image Security Scan",
            "icon": "ISC",
            "desc": "SCAP compliance scan + vulnerability assessment of golden image before publish",
        },
        {
            "type": "img-sign",
            "label": "Image Signing",
            "icon": "SGN",
            "desc": "Cryptographic signing of golden image — integrity verification at deploy time",
        },
        {
            "type": "img-publish",
            "label": "Image Publish",
            "icon": "PUB",
            "desc": "Publish to Azure Compute Gallery / vSphere Content Library / AWS AMI",
        },
        {
            "type": "img-deploy-pool",
            "label": "Deploy to Host Pool",
            "icon": "DHP",
            "desc": "Rolling deployment of golden image to AVD host pool / Citrix MCS / Horizon pool",
        },
        {
            "type": "img-rollback",
            "label": "Image Rollback",
            "icon": "RBK",
            "desc": "Rollback host pool to previous golden image version on failure",
        },
        {
            "type": "img-lifecycle",
            "label": "Image Lifecycle",
            "icon": "LCY",
            "desc": "Image version tracking, retention policy, and deprecation schedule",
        },
    ],
    "config_testing": [
        # Ansible role / playbook testing
        {
            "type": "test-molecule",
            "label": "Ansible Molecule",
            "icon": "ML",
            "desc": "Ansible-native test framework — provision, verify, and destroy role scenarios",
            "stage": "test",
            "license": "MIT",
        },
        {
            "type": "test-ansible-navigator",
            "label": "Ansible Navigator",
            "icon": "AN",
            "desc": "Ansible Navigator TUI — interactive execution environment validation for playbooks and collections",
            "stage": "test",
            "license": "Apache-2.0",
        },
        # Jenkinsfile unit testing
        {
            "type": "test-jenkins-pipeline-unit",
            "label": "Jenkins Pipeline Unit",
            "icon": "JP",
            "desc": "Jenkinsci/JenkinsPipelineUnit — unit tests for Declarative and Scripted Jenkinsfiles",
            "stage": "test",
            "license": "Apache-2.0",
        },
    ],
    "iac_lifecycle": [
        # IaC refactor helpers — manage Terraform state through structural changes
        {
            "type": "tf-tfautomv",
            "label": "tfautomv",
            "icon": "TM",
            "desc": "busser/tfautomv — auto-generates `terraform moved` blocks when resources are renamed or reorganized; eliminates manual state surgery",
            "stage": "build",
            "license": "MIT",
        },
        {
            "type": "tf-tfmigrate",
            "label": "tfmigrate",
            "icon": "TG",
            "desc": "minamijoyo/tfmigrate — file-based Terraform state migrations across workspaces and modules; runs in CI to apply state changes safely without manual `terraform state mv`",
            "stage": "build",
            "license": "MIT",
        },
    ],
    "digital_twin": [
        {
            "type": "twin-pipeline",
            "label": "Pipeline Twin",
            "icon": "DT",
            "desc": "Digital twin node for this pipeline — enables snapshot/simulate/what-if before merging changes",
            "stage": "policy_gate",
        },
        {
            "type": "twin-snapshot",
            "label": "Snapshot Trigger",
            "icon": "SS",
            "desc": "Triggers an append-only snapshot of the current pipeline DAG state for baseline comparison",
            "stage": "build",
        },
        {
            "type": "twin-simulator",
            "label": "What-If Simulator",
            "icon": "WI",
            "desc": "Pre-merge what-if engine — compares delta graph against baseline snapshot and emits PASS/WARN/FAIL verdict",
            "stage": "policy_gate",
        },
        {
            "type": "twin-drift-detector",
            "label": "Drift Detector",
            "icon": "DD",
            "desc": "Continuously compares live pipeline state against last approved snapshot; emits drift events on deviation",
            "stage": "monitor",
        },
        {
            "type": "twin-slsa-gate",
            "label": "SLSA Gate (Twin)",
            "icon": "SG",
            "desc": "Twin-aware SLSA level evaluator — asserts minimum provenance level before delta is allowed to proceed",
            "stage": "policy_gate",
            "license": "Apache-2.0",
        },
    ],
}

# ── CSP Service Equivalence (maps generic concepts across CSPs) ──────────────
CSP_SERVICE_EQUIVALENCE = {
    "ci_cd_engine": {
        "label": "CI/CD Engine",
        "aws": {"service": "CodePipeline + CodeBuild", "type": "aws-codepipeline"},
        "azure": {"service": "Azure Pipelines / GitHub Actions", "type": "az-pipelines"},
        "gcp": {"service": "Cloud Build + Cloud Deploy", "type": "gcp-cloudbuild"},
        "oci": {"service": "OCI DevOps", "type": "oci-devops"},
        "ibm": {"service": "Continuous Delivery (Tekton)", "type": "ibm-cd"},
        "on_prem": {"service": "GitLab CI / Jenkins / Tekton"},
    },
    "sast": {
        "label": "Static Analysis (SAST)",
        "aws": {"service": "CodeGuru Security", "type": "aws-codeguru"},
        "azure": {"service": "GitHub Advanced Security (CodeQL)", "type": "scan-codeql"},
        "gcp": {"service": "Third-party (SonarQube, Semgrep)"},
        "oci": {"service": "Third-party"},
        "ibm": {"service": "AppScan + SonarQube"},
        "on_prem": {"service": "SonarQube / Semgrep / Bandit"},
    },
    "container_registry": {
        "label": "Container Registry",
        "aws": {"service": "ECR", "type": "aws-ecr"},
        "azure": {"service": "ACR", "type": "az-acr"},
        "gcp": {"service": "Artifact Registry", "type": "gcp-gar"},
        "oci": {"service": "Container Registry", "type": "oci-cr"},
        "ibm": {"service": "Container Registry", "type": "ibm-cr"},
        "on_prem": {"service": "Harbor / Nexus / JFrog"},
    },
    "container_scan": {
        "label": "Container Scanning",
        "aws": {"service": "Inspector + ECR Scanning", "type": "aws-inspector"},
        "azure": {"service": "Defender for Containers", "type": "az-defender"},
        "gcp": {"service": "Artifact Analysis", "type": "gcp-artifact-analysis"},
        "oci": {"service": "Vulnerability Scanning Service"},
        "ibm": {"service": "Vulnerability Advisor", "type": "ibm-vuln-advisor"},
        "on_prem": {"service": "Trivy / Anchore / Grype"},
    },
    "secret_management": {
        "label": "Secret Management",
        "aws": {"service": "Secrets Manager + KMS", "type": "aws-secrets"},
        "azure": {"service": "Key Vault", "type": "az-keyvault"},
        "gcp": {"service": "Secret Manager + Cloud KMS", "type": "gcp-secret"},
        "oci": {"service": "OCI Vault", "type": "oci-vault"},
        "ibm": {"service": "Secrets Manager + Key Protect", "type": "ibm-secrets"},
        "on_prem": {"service": "HashiCorp Vault / OpenBao"},
    },
    "policy_engine": {
        "label": "Policy Engine",
        "aws": {"service": "Config Rules + SCP", "type": "aws-config"},
        "azure": {"service": "Azure Policy + Gatekeeper", "type": "az-policy"},
        "gcp": {"service": "Binary Authorization + Policy Controller", "type": "gcp-binary-auth"},
        "oci": {"service": "Security Zones"},
        "ibm": {"service": "Portieris + CRA", "type": "ibm-portieris"},
        "on_prem": {"service": "OPA/Gatekeeper / Kyverno"},
    },
    "kubernetes": {
        "label": "Kubernetes Platform",
        "aws": {"service": "EKS", "type": "aws-eks"},
        "azure": {"service": "AKS", "type": "az-aks"},
        "gcp": {"service": "GKE", "type": "gcp-gke"},
        "oci": {"service": "OKE", "type": "oci-oke"},
        "ibm": {"service": "IKS / OpenShift", "type": "ibm-iks"},
        "on_prem": {"service": "RKE2 / OpenShift / K3s"},
    },
    "monitoring": {
        "label": "Monitoring & Observability",
        "aws": {"service": "CloudWatch + X-Ray + GuardDuty", "type": "aws-cloudwatch"},
        "azure": {"service": "Monitor + Sentinel", "type": "az-monitor"},
        "gcp": {"service": "Cloud Monitoring + SCC", "type": "gcp-monitoring"},
        "oci": {"service": "OCI Monitoring + Cloud Guard"},
        "ibm": {"service": "Sysdig + LogDNA + SCC"},
        "on_prem": {"service": "Prometheus + Grafana + Loki + Falco"},
    },
    "iac_engine": {
        "label": "Infrastructure as Code",
        "aws": {"service": "CloudFormation / CDK"},
        "azure": {"service": "Bicep / ARM Templates"},
        "gcp": {"service": "Terraform + Config Connector"},
        "oci": {"service": "Resource Manager (Terraform)"},
        "ibm": {"service": "Schematics (Terraform + Ansible)"},
        "on_prem": {"service": "Terraform + Ansible + Pulumi"},
    },
    "siem": {
        "label": "SIEM / Threat Detection",
        "aws": {"service": "Security Lake + GuardDuty", "type": "aws-guardduty"},
        "azure": {"service": "Microsoft Sentinel", "type": "az-sentinel"},
        "gcp": {"service": "Security Command Center", "type": "gcp-scc"},
        "oci": {"service": "Cloud Guard"},
        "ibm": {"service": "IBM SCC", "type": "ibm-scc"},
        "on_prem": {"service": "Wazuh / ELK + Falco"},
    },
    "compliance_dashboard": {
        "label": "Compliance Posture Management",
        "aws": {"service": "Security Hub + Audit Manager", "type": "aws-securityhub"},
        "azure": {"service": "Defender for Cloud", "type": "az-defender-cloud"},
        "gcp": {"service": "Security Command Center", "type": "gcp-scc"},
        "oci": {"service": "Cloud Guard"},
        "ibm": {"service": "Security and Compliance Center", "type": "ibm-scc"},
        "on_prem": {"service": "OpenSCAP / InSpec / Prowler"},
    },
    "slo_management": {
        "label": "SLO Management",
        "aws": {"service": "CloudWatch SLOs (2024)", "type": "aws-cw-slo"},
        "azure": {"service": "Azure Monitor + Workbooks (manual)"},
        "gcp": {"service": "Service Monitoring SLO API (mature)", "type": "gcp-service-mon"},
        "oci": {"service": "OCI APM (manual SLI)"},
        "ibm": {"service": "Instana SLOs", "type": "ibm-instana"},
        "on_prem": {"service": "Sloth / Pyrra + Prometheus"},
    },
    "chaos_engineering": {
        "label": "Chaos Engineering",
        "aws": {"service": "Fault Injection Service", "type": "aws-fis"},
        "azure": {"service": "Chaos Studio", "type": "az-chaos-studio"},
        "gcp": {"service": "Litmus on GKE (no native)"},
        "oci": {"service": "Litmus on OKE (no native)"},
        "ibm": {"service": "Litmus on IKS (no native)"},
        "on_prem": {"service": "LitmusChaos / Chaos Mesh"},
    },
    "incident_management": {
        "label": "Incident Management",
        "aws": {"service": "SSM Incident Manager", "type": "aws-incident-mgr"},
        "azure": {"service": "Alert Action Groups + PagerDuty"},
        "gcp": {"service": "Cloud Incident Management"},
        "oci": {"service": "Notifications + PagerDuty"},
        "ibm": {"service": "AIOps (AI-driven correlation)", "type": "ibm-instana"},
        "on_prem": {"service": "Grafana OnCall / PagerDuty self-hosted"},
    },
    "resilience_scoring": {
        "label": "Resilience Scoring",
        "aws": {"service": "Resilience Hub (0-100)", "type": "aws-resilience-hub"},
        "azure": {"service": "Advisor Reliability Score", "type": "az-advisor-rel"},
        "gcp": {"service": "Custom (SLO compliance dashboards)"},
        "oci": {"service": "Cloud Guard (security-focused)"},
        "ibm": {"service": "SCC posture score"},
        "on_prem": {"service": "Custom (SLO compliance %)"},
    },
}

# ── OWASP Top 10 Coverage Map ────────────────────────────────────────────────
OWASP_COVERAGE = {
    "scan-sast": ["A03-Injection", "A07-XSS", "A08-Deserialization", "A04-Insecure-Design"],
    "scan-sonarqube": ["A03-Injection", "A07-XSS", "A08-Deserialization", "A04-Insecure-Design"],
    "scan-semgrep": ["A03-Injection", "A07-XSS", "A08-Deserialization"],
    "scan-codeql": ["A03-Injection", "A07-XSS", "A08-Deserialization", "A04-Insecure-Design"],
    "scan-bandit": ["A03-Injection", "A07-XSS"],
    "scan-sca": ["A06-Vuln-Components"],
    "scan-trivy": ["A06-Vuln-Components", "A05-Misconfig"],
    "scan-grype": ["A06-Vuln-Components"],
    "scan-snyk": ["A06-Vuln-Components"],
    "scan-dep-check": ["A06-Vuln-Components"],
    "scan-dast": ["A01-Broken-Access", "A03-Injection", "A07-XSS", "A05-Misconfig"],
    "scan-zap": ["A01-Broken-Access", "A03-Injection", "A07-XSS", "A05-Misconfig"],
    "scan-nuclei": ["A01-Broken-Access", "A05-Misconfig", "A07-XSS"],
    "scan-iac": ["A05-Misconfig"],
    "scan-checkov": ["A05-Misconfig"],
    "scan-tfsec": ["A05-Misconfig"],
    "scan-kics": ["A05-Misconfig"],
    "scan-secret": ["A02-Crypto-Failures"],
    "scan-gitleaks": ["A02-Crypto-Failures"],
    "scan-trufflehog": ["A02-Crypto-Failures"],
    "scan-detect-secrets": ["A02-Crypto-Failures"],
    "scan-container": ["A06-Vuln-Components", "A05-Misconfig"],
    "scan-anchore": ["A06-Vuln-Components", "A05-Misconfig"],
    "scan-neuvector": ["A06-Vuln-Components", "A05-Misconfig", "A09-Logging-Monitoring"],
    "mon-falco": ["A09-Logging-Monitoring", "A10-SSRF"],
    "mon-wazuh": ["A09-Logging-Monitoring"],
    "scan-license": ["A06-Vuln-Components"],
}

ALL_OWASP_TOP_10 = [
    "A01-Broken-Access",
    "A02-Crypto-Failures",
    "A03-Injection",
    "A04-Insecure-Design",
    "A05-Misconfig",
    "A06-Vuln-Components",
    "A07-XSS",
    "A08-Deserialization",
    "A09-Logging-Monitoring",
    "A10-SSRF",
]

# ── Pipeline Cost Estimates (USD) ────────────────────────────────────────────
PIPELINE_COSTS = {
    "per_run": {
        "aws-codebuild": {"per_min": 0.005, "avg_min": 10},
        "aws-codepipeline": {"per_min": 0.0, "avg_min": 0, "monthly_fixed": 1.00},
        "gcp-cloudbuild": {"per_min": 0.003, "avg_min": 10},
        "az-pipelines": {"per_min": 0.0, "avg_min": 10, "note": "1800 free min/mo, $0.008/min after"},
        "cicd-github-actions": {"per_min": 0.008, "avg_min": 10},
        "cicd-gitlab": {"per_min": 0.0, "avg_min": 10, "note": "Self-hosted: compute cost only"},
        "cicd-jenkins": {"per_min": 0.0, "avg_min": 10, "note": "Self-hosted: compute cost only"},
        "cicd-tekton": {"per_min": 0.0, "avg_min": 10, "note": "Self-hosted: compute cost only"},
        "scan-sonarqube": {"per_run": 0.0, "note": "Self-hosted or SaaS plan"},
        "scan-trivy": {"per_run": 0.0, "note": "Open source, free"},
        "scan-grype": {"per_run": 0.0, "note": "Open source, free"},
        "scan-zap": {"per_run": 0.0, "note": "Open source, free"},
        "scan-gitleaks": {"per_run": 0.0, "note": "Open source, free"},
        "scan-checkov": {"per_run": 0.0, "note": "Open source, free"},
    },
    "monthly": {
        "cicd-gitlab": {"cost": 29, "unit": "per user/mo", "tier": "Premium"},
        "scan-snyk": {"cost": 0, "unit": "free tier", "note": "Paid plans from $98/mo"},
        "registry-harbor": {"cost": 0, "note": "Open source, compute cost only"},
        "vault-hashicorp": {"cost": 0, "note": "Open source (BUSL); Enterprise: $1.58/hr"},
        "vault-openbao": {"cost": 0, "note": "Fully open source (MPL-2.0)"},
        "scan-sonarqube": {"cost": 0, "note": "Community free; Developer $150/yr"},
    },
}

# ── Pipeline Compliance Frameworks ───────────────────────────────────────────
# Actual rules come from existing tools/compliance/ modules. This just defines
# the framework metadata for the canvas compliance panel.
PIPELINE_COMPLIANCE_FRAMEWORKS = {
    "nist_ssdf": {"name": "NIST SSDF (SP 800-218)", "practices": ["PO", "PS", "PW", "RV"]},
    "slsa": {"name": "SLSA Framework", "levels": [0, 1, 2, 3, 4]},
    "dod_devsecops": {
        "name": "DoD DevSecOps Reference Design",
        "phases": ["Design", "Instantiate", "Verify", "Operate"],
    },
    "fedramp": {"name": "FedRAMP DevSecOps", "baselines": ["Moderate", "High"]},
    "cmmc": {"name": "CMMC Level 2+", "levels": [2, 3]},
    "cisa_sbd": {"name": "CISA Secure by Design", "pledge_goals": 7},
}

# ── Pipeline Stage Suggestions (next-stage recommendations) ─────────────────
PIPELINE_STAGE_SUGGESTIONS = {
    # VDI / Desktop Image pipeline flow
    "img-source": ["img-customize"],
    "img-customize": ["img-harden", "img-optimize"],
    "img-harden": ["img-optimize", "img-scan"],
    "img-optimize": ["img-scan"],
    "img-scan": ["img-sign"],
    "img-sign": ["img-publish"],
    "img-publish": ["img-deploy-pool"],
    "img-deploy-pool": ["img-rollback", "img-lifecycle"],
}

# ── Pipeline Compliance Rules (deterministic checks on graph) ────────────────
PIPELINE_COMPLIANCE_RULES = [
    {
        "id": "PDC-SSC-001",
        "title": "Source code requires branch protection",
        "severity": "CAT1",
        "category": "source_integrity",
        "frameworks": ["nist_ssdf", "slsa", "dod_devsecops"],
        "check": "branch_protection",
    },
    {
        "id": "PDC-SSC-002",
        "title": "Code review required before merge",
        "severity": "CAT1",
        "category": "source_integrity",
        "frameworks": ["nist_ssdf", "slsa", "dod_devsecops"],
        "check": "code_review_required",
    },
    {
        "id": "PDC-BLD-001",
        "title": "Builds should be hermetic (no internet)",
        "severity": "CAT2",
        "category": "build_integrity",
        "frameworks": ["slsa", "dod_devsecops"],
        "check": "hermetic_build",
    },
    {
        "id": "PDC-BLD-002",
        "title": "SBOM generated on every build",
        "severity": "CAT1",
        "category": "supply_chain",
        "frameworks": ["nist_ssdf", "dod_devsecops", "fedramp"],
        "check": "sbom_generated",
    },
    {
        "id": "PDC-BLD-003",
        "title": "Build provenance attestation generated",
        "severity": "CAT1",
        "category": "supply_chain",
        "frameworks": ["slsa", "dod_devsecops"],
        "check": "provenance_attestation",
    },
    {
        "id": "PDC-SCN-001",
        "title": "SAST scan on every commit/MR",
        "severity": "CAT1",
        "category": "security_testing",
        "frameworks": ["nist_ssdf", "dod_devsecops", "cmmc"],
        "check": "sast_present",
    },
    {
        "id": "PDC-SCN-002",
        "title": "SCA/dependency scan required",
        "severity": "CAT1",
        "category": "security_testing",
        "frameworks": ["nist_ssdf", "dod_devsecops", "fedramp"],
        "check": "sca_present",
    },
    {
        "id": "PDC-SCN-003",
        "title": "Container image scan before registry push",
        "severity": "CAT1",
        "category": "security_testing",
        "frameworks": ["dod_devsecops", "fedramp"],
        "check": "container_scan_before_push",
    },
    {
        "id": "PDC-SCN-004",
        "title": "Secret detection in pre-commit or CI",
        "severity": "CAT1",
        "category": "security_testing",
        "frameworks": ["nist_ssdf", "dod_devsecops", "cmmc"],
        "check": "secret_detection_present",
    },
    {
        "id": "PDC-SCN-005",
        "title": "IaC security scanning required",
        "severity": "CAT2",
        "category": "security_testing",
        "frameworks": ["dod_devsecops", "fedramp"],
        "check": "iac_scan_present",
    },
    {
        "id": "PDC-SCN-006",
        "title": "DAST scanning for web applications",
        "severity": "CAT2",
        "category": "security_testing",
        "frameworks": ["nist_ssdf", "dod_devsecops"],
        "check": "dast_present",
    },
    {
        "id": "PDC-SGN-001",
        "title": "Container images must be signed",
        "severity": "CAT1",
        "category": "supply_chain",
        "frameworks": ["slsa", "dod_devsecops"],
        "check": "image_signing",
    },
    {
        "id": "PDC-POL-001",
        "title": "Vulnerability threshold gate before deploy",
        "severity": "CAT1",
        "category": "policy",
        "frameworks": ["dod_devsecops", "fedramp"],
        "check": "vuln_threshold_gate",
    },
    {
        "id": "PDC-POL-002",
        "title": "Admission controller on K8s clusters",
        "severity": "CAT2",
        "category": "policy",
        "frameworks": ["dod_devsecops"],
        "check": "admission_controller",
    },
    {
        "id": "PDC-DEP-001",
        "title": "Production deploy requires approval gate",
        "severity": "CAT1",
        "category": "deployment",
        "frameworks": ["dod_devsecops", "fedramp", "cmmc"],
        "check": "prod_approval_gate",
    },
    {
        "id": "PDC-DEP-002",
        "title": "Progressive delivery (canary/blue-green)",
        "severity": "CAT2",
        "category": "deployment",
        "frameworks": ["dod_devsecops"],
        "check": "progressive_delivery",
    },
    {
        "id": "PDC-DEP-003",
        "title": "Cross-domain pipeline requires CDS/guard",
        "severity": "CAT1",
        "category": "cross_domain",
        "frameworks": ["dod_devsecops"],
        "check": "cds_for_cross_domain",
    },
    {
        "id": "PDC-MON-001",
        "title": "Runtime security monitoring enabled",
        "severity": "CAT1",
        "category": "monitoring",
        "frameworks": ["nist_ssdf", "dod_devsecops", "fedramp"],
        "check": "runtime_monitoring",
    },
    {
        "id": "PDC-MON-002",
        "title": "Continuous compliance evidence collection",
        "severity": "CAT2",
        "category": "compliance",
        "frameworks": ["fedramp", "cmmc"],
        "check": "evidence_collection",
    },
    {
        "id": "PDC-MON-003",
        "title": "Pipeline audit logging enabled",
        "severity": "CAT1",
        "category": "monitoring",
        "frameworks": ["nist_ssdf", "fedramp"],
        "check": "audit_logging",
    },
    {
        "id": "PDC-CDS-001",
        "title": "Air-gapped pipeline requires vuln DB mirror",
        "severity": "CAT1",
        "category": "cross_domain",
        "frameworks": ["dod_devsecops"],
        "check": "airgap_vuln_mirror",
    },
    {
        "id": "PDC-CDS-002",
        "title": "Air-gapped pipeline requires package mirror",
        "severity": "CAT1",
        "category": "cross_domain",
        "frameworks": ["dod_devsecops"],
        "check": "airgap_package_mirror",
    },
    # SRE rules
    {
        "id": "PDC-SRE-001",
        "title": "SLO defined for production services",
        "severity": "CAT1",
        "category": "sre",
        "frameworks": ["dod_devsecops", "fedramp"],
        "check": "slo_defined",
    },
    {
        "id": "PDC-SRE-002",
        "title": "Incident management process connected",
        "severity": "CAT1",
        "category": "sre",
        "frameworks": ["nist_ssdf", "fedramp"],
        "check": "incident_mgmt_present",
    },
    {
        "id": "PDC-SRE-003",
        "title": "Automated runbooks for common failures",
        "severity": "CAT2",
        "category": "sre",
        "frameworks": ["dod_devsecops"],
        "check": "runbooks_present",
    },
    {
        "id": "PDC-SRE-004",
        "title": "Chaos engineering for resilience validation",
        "severity": "CAT2",
        "category": "sre",
        "frameworks": ["dod_devsecops"],
        "check": "chaos_present",
    },
    {
        "id": "PDC-SRE-005",
        "title": "DORA metrics tracked for pipeline health",
        "severity": "CAT2",
        "category": "sre",
        "frameworks": ["dod_devsecops"],
        "check": "dora_tracked",
    },
    # VDI / Desktop Image rules
    {
        "id": "PC-VDI-001",
        "title": "DISA STIG applied to golden image",
        "severity": "CAT1",
        "category": "desktop_image",
        "frameworks": ["dod_devsecops"],
        "check": "img_stig_applied",
        "description": "Golden images must have applicable DISA STIGs applied before publish (Windows VDI, .NET, Browser STIGs).",
    },
    {
        "id": "PC-VDI-002",
        "title": "SCAP scan before publish",
        "severity": "CAT1",
        "category": "desktop_image",
        "frameworks": ["dod_devsecops"],
        "check": "img_scap_scan",
        "description": "Images must pass SCAP compliance scan with zero CAT1 findings before publishing to gallery.",
    },
    {
        "id": "PC-VDI-003",
        "title": "Image integrity signing",
        "severity": "CAT2",
        "category": "desktop_image",
        "frameworks": ["slsa"],
        "check": "img_signing",
        "description": "Published images must be cryptographically signed for integrity verification at deployment time (SLSA L2+).",
    },
    {
        "id": "PC-VDI-004",
        "title": "Rolling deployment with health check",
        "severity": "CAT2",
        "category": "desktop_image",
        "frameworks": ["dod_devsecops"],
        "check": "img_rolling_deploy",
        "description": "Host pool image updates must use rolling deployment with session drain and health verification.",
    },
]


def get_all_object_types():
    """Return flat list of all pipeline object type strings."""
    types = []
    for category_items in PIPELINE_OBJECTS.values():
        types.extend(item["type"] for item in category_items)
    return types


def get_object_by_type(obj_type):
    """Look up a pipeline object definition by its type string."""
    for category_items in PIPELINE_OBJECTS.values():
        for item in category_items:
            if item["type"] == obj_type:
                return item
    return None


# ── Type → Stage inference (server-side stage derivation) ────────────────────
# The canvas frontend never persists a node's ``stage`` — the save path sends
# only ``type``. Server-side scoring (stage coverage, governance) must therefore
# DERIVE the stage from the type. Two sources, in priority order:
#   1. The explicit per-type ``stage`` curated in PIPELINE_OBJECTS (authoritative).
#   2. A type-PREFIX fallback mirroring the frontend's ``autoGroupByStage``
#      prefixMap (tools/dashboard/static/js/pipeline-canvas.js) for any type not
#      present in the object library.
# Stage values are PIPELINE_STAGES keys (the 12 canonical stages).

STAGE_FROM_TYPE: dict[str, str] = {}
for _cat_items in PIPELINE_OBJECTS.values():
    for _obj in _cat_items:
        _st = _obj.get("stage")
        if _st:
            STAGE_FROM_TYPE[_obj["type"]] = _st

# Prefix → stage fallback, mirroring pipeline-canvas.js autoGroupByStage().
# ``infrastructure`` (ndc-/hybrid-/onprem-) is intentionally excluded: it is not
# one of the 12 canonical PIPELINE_STAGES, so those types have no scored stage.
STAGE_PREFIX_MAP: tuple[tuple[str, str], ...] = (
    ("scm-", "source"), ("branch-", "source"), ("commit-", "source"),
    ("build-", "build"), ("cicd-", "build"), ("sbom-", "build"),
    ("scan-", "test"),
    ("registry-", "package"), ("sign-", "package"), ("attest-", "package"),
    ("policy-", "policy_gate"), ("gate-", "policy_gate"),
    ("gitops-", "deploy_prod"), ("deploy-", "deploy_prod"),
    ("k8s-", "deploy_prod"), ("mesh-", "deploy_prod"),
    ("mon-", "monitor"),
    ("comp-", "compliance"),
    ("sre-", "sre"),
    ("cds-", "cross_domain"), ("boundary-", "cross_domain"),
    ("pipeline-", "cross_domain"),
)


def stage_from_type(node_type, explicit_stage=None):
    """Resolve a node's pipeline stage.

    An explicit stage persisted on the node wins. Otherwise the stage is derived
    from the node type: the curated PIPELINE_OBJECTS mapping first, then a
    prefix fallback mirroring the frontend ``autoGroupByStage``. Returns ``None``
    when nothing matches (e.g. ``infrastructure`` types outside the 12 stages).
    """
    if explicit_stage:
        return explicit_stage
    if not node_type:
        return None
    exact = STAGE_FROM_TYPE.get(node_type)
    if exact:
        return exact
    for _pfx, _stage in STAGE_PREFIX_MAP:
        if node_type.startswith(_pfx):
            return _stage
    return None


def compute_owasp_coverage(node_types):
    """Given a list of node types, compute OWASP Top 10 coverage.

    Returns dict with 'covered', 'uncovered', 'coverage_pct', and per-category detail.
    """
    covered = set()
    scanner_coverage = {}
    for nt in node_types:
        cats = OWASP_COVERAGE.get(nt, [])
        if cats:
            covered.update(cats)
            scanner_coverage[nt] = cats
    uncovered = [c for c in ALL_OWASP_TOP_10 if c not in covered]
    return {
        "covered": sorted(covered),
        "uncovered": uncovered,
        "coverage_pct": round(len(covered) / len(ALL_OWASP_TOP_10) * 100, 1),
        "scanner_coverage": scanner_coverage,
        "total_categories": len(ALL_OWASP_TOP_10),
        "covered_count": len(covered),
    }


def estimate_pipeline_cost(node_types, runs_per_month=500):
    """Estimate monthly pipeline cost from node types and run frequency.

    Returns dict with per_run_cost, monthly_fixed, total_monthly.
    """
    per_run = PIPELINE_COSTS.get("per_run", {})
    monthly = PIPELINE_COSTS.get("monthly", {})

    total_per_run = 0.0
    total_monthly_fixed = 0.0
    details = []

    for nt in node_types:
        pr = per_run.get(nt)
        if pr:
            if "per_min" in pr:
                cost = pr["per_min"] * pr.get("avg_min", 10)
            elif "per_run" in pr:
                cost = pr["per_run"]
            else:
                cost = 0.0
            total_per_run += cost
            if pr.get("monthly_fixed"):
                total_monthly_fixed += pr["monthly_fixed"]
            details.append({"type": nt, "per_run": round(cost, 4)})

        mo = monthly.get(nt)
        if mo and mo.get("cost"):
            total_monthly_fixed += mo["cost"]

    total_monthly = (total_per_run * runs_per_month) + total_monthly_fixed
    return {
        "per_run_cost": round(total_per_run, 4),
        "runs_per_month": runs_per_month,
        "monthly_variable": round(total_per_run * runs_per_month, 2),
        "monthly_fixed": round(total_monthly_fixed, 2),
        "total_monthly": round(total_monthly, 2),
        "details": details,
    }


def estimate_execution_time(nodes, edges):
    """Estimate pipeline execution time from graph nodes and edges.

    Groups nodes by stage, sums sequential stage times, applies
    parallelism discount for stages marked parallel.
    """
    stage_times = {}
    for node in nodes:
        stage = node.get("stage") or "build"
        config = node.get("config") or {}
        minutes = config.get("avg_execution_min", 5)
        if stage not in stage_times:
            stage_times[stage] = {"total_min": 0, "parallel": False}
        stage_times[stage]["total_min"] += minutes
        # Honor the parallel flag from ANY node in the stage, not just the first
        # one seen — a stage is parallel if at least one of its nodes declares it.
        if config.get("parallel", False):
            stage_times[stage]["parallel"] = True

    ordered_stages = sorted(
        PIPELINE_STAGES.keys(),
        key=lambda s: PIPELINE_STAGES[s]["order"],
    )
    total_min = 0
    for stage_key in ordered_stages:
        if stage_key in stage_times:
            st = stage_times[stage_key]
            if st["parallel"]:
                total_min += st["total_min"] * 0.6  # parallelism discount
            else:
                total_min += st["total_min"]

    return {
        "total_minutes": round(total_min, 1),
        "stage_breakdown": {k: v["total_min"] for k, v in stage_times.items()},
    }


_P = "https://icdev.dev/ontology/pipeline#"

PIPELINE_ONTOLOGY_MAP: dict[str, str] = {
    # CI/CD platforms
    "cicd-gitlab":          f"{_P}CICDPlatform.GitLab",
    "cicd-jenkins":         f"{_P}CICDPlatform.Jenkins",
    "cicd-tekton":          f"{_P}CICDPlatform.Tekton",
    "cicd-github-actions":  f"{_P}CICDPlatform.GitHubActions",
    "cicd-argo-workflows":  f"{_P}CICDPlatform.ArgoWorkflows",
    "cicd-drone":           f"{_P}CICDPlatform.Drone",
    "aws-codepipeline":     f"{_P}CICDPlatform.CodePipeline",
    "aws-codebuild":        f"{_P}CICDPlatform.CodeBuild",
    "aws-codedeploy":       f"{_P}CICDPlatform.CodeDeploy",
    "az-pipelines":         f"{_P}CICDPlatform.AzurePipelines",
    "gcp-cloudbuild":       f"{_P}CICDPlatform.CloudBuild",
    "gcp-deploy":           f"{_P}CICDPlatform.CloudDeploy",
    "oci-devops":           f"{_P}CICDPlatform.OCIDevOps",
    "ibm-cd":               f"{_P}CICDPlatform.IBMToolchain",
    # GitOps
    "gitops-argocd":        f"{_P}GitOpsPlatform.ArgoCD",
    "gitops-flux":          f"{_P}GitOpsPlatform.Flux",
    # SCM
    "scm-gitlab":           f"{_P}SCM.GitLab",
    "scm-gitea":            f"{_P}SCM.Gitea",
    "scm-forgejo":          f"{_P}SCM.Forgejo",
    "scm-bitbucket":        f"{_P}SCM.Bitbucket",
    "aws-codecommit":       f"{_P}SCM.CodeCommit",
    "az-repos":             f"{_P}SCM.AzureRepos",
    "gcp-source":           f"{_P}SCM.CloudSourceRepos",
    "oci-code-repos":       f"{_P}SCM.OCICodeRepos",
    "branch-policy":        f"{_P}SCM.BranchPolicy",
    "commit-signing":       f"{_P}SCM.CommitSigning",
    # Build tools
    "build-runner":         f"{_P}BuildTool.Runner",
    "build-kaniko":         f"{_P}BuildTool.Kaniko",
    "build-buildah":        f"{_P}BuildTool.Buildah",
    "build-docker":         f"{_P}BuildTool.Docker",
    "build-bazel":          f"{_P}BuildTool.Bazel",
    "build-gradle":         f"{_P}BuildTool.Gradle",
    "build-maven":          f"{_P}BuildTool.Maven",
    # SAST
    "scan-sast":            f"{_P}SecurityScan.SAST",
    "scan-sonarqube":       f"{_P}SecurityScan.SonarQube",
    "scan-semgrep":         f"{_P}SecurityScan.Semgrep",
    "scan-codeql":          f"{_P}SecurityScan.CodeQL",
    "scan-bandit":          f"{_P}SecurityScan.Bandit",
    "scan-spotbugs":        f"{_P}SecurityScan.SpotBugs",
    "aws-codeguru":         f"{_P}SecurityScan.CodeGuru",
    # DAST
    "scan-dast":            f"{_P}SecurityScan.DAST",
    "scan-zap":             f"{_P}SecurityScan.ZAP",
    "scan-nuclei":          f"{_P}SecurityScan.Nuclei",
    "scan-burp":            f"{_P}SecurityScan.Burp",
    # SCA
    "scan-sca":             f"{_P}SecurityScan.SCA",
    "scan-trivy":           f"{_P}SecurityScan.Trivy",
    "scan-grype":           f"{_P}SecurityScan.Grype",
    "scan-snyk":            f"{_P}SecurityScan.Snyk",
    "scan-dep-check":       f"{_P}SecurityScan.DependencyCheck",
    # IaC scanning
    "scan-iac":             f"{_P}SecurityScan.IaC",
    "scan-checkov":         f"{_P}SecurityScan.Checkov",
    "scan-tfsec":           f"{_P}SecurityScan.TFSec",
    "scan-kics":            f"{_P}SecurityScan.KICS",
    # Secret scanning
    "scan-secret":          f"{_P}SecurityScan.SecretScan",
    "scan-gitleaks":        f"{_P}SecurityScan.GitLeaks",
    "scan-trufflehog":      f"{_P}SecurityScan.TruffleHog",
    "scan-detect-secrets":  f"{_P}SecurityScan.DetectSecrets",
    # Container scanning
    "scan-container":       f"{_P}SecurityScan.Container",
    "scan-anchore":         f"{_P}SecurityScan.Anchore",
    "scan-neuvector":       f"{_P}SecurityScan.NeuVector",
    "aws-inspector":        f"{_P}SecurityScan.AWSInspector",
    "az-defender":          f"{_P}SecurityScan.AzureDefender",
    "gcp-artifact-analysis":f"{_P}SecurityScan.ArtifactAnalysis",
    "ibm-vuln-advisor":     f"{_P}SecurityScan.VulnAdvisor",
    "scan-license":         f"{_P}SecurityScan.License",
    # Registries
    "registry-generic":     f"{_P}Registry.Generic",
    "registry-harbor":      f"{_P}Registry.Harbor",
    "registry-nexus":       f"{_P}Registry.Nexus",
    "registry-jfrog":       f"{_P}Registry.JFrog",
    "registry-zot":         f"{_P}Registry.Zot",
    "aws-ecr":              f"{_P}Registry.ECR",
    "az-acr":               f"{_P}Registry.ACR",
    "gcp-gar":              f"{_P}Registry.GAR",
    "oci-cr":               f"{_P}Registry.OCICR",
    "ibm-cr":               f"{_P}Registry.IBMCR",
    "registry-ironbank":    f"{_P}Registry.IronBank",
    "sbom-store":           f"{_P}Registry.SBOMStore",
    "package-repo":         f"{_P}Registry.PackageRepo",
    # Signing & attestation
    "sign-cosign":          f"{_P}Attestation.Cosign",
    "sign-notation":        f"{_P}Attestation.Notation",
    "sign-dct":             f"{_P}Attestation.DCT",
    "attest-in-toto":       f"{_P}Attestation.InToto",
    "attest-slsa-gen":      f"{_P}Attestation.SLSAGen",
    "verify-slsa":          f"{_P}Attestation.SLSAVerify",
    "sbom-syft":            f"{_P}Attestation.Syft",
    "sbom-cyclonedx":       f"{_P}Attestation.CycloneDX",
    "sbom-spdx":            f"{_P}Attestation.SPDX",
    "vex-openvex":          f"{_P}Attestation.OpenVEX",
    "gcp-binary-auth":      f"{_P}Attestation.BinaryAuth",
    "ibm-portieris":        f"{_P}Attestation.Portieris",
    "sc-cargo-vet":         f"{_P}Attestation.CargoVet",
    "sc-cargo-auditable":   f"{_P}Attestation.CargoAuditable",
    # Policy
    "policy-opa":           f"{_P}Policy.OPA",
    "policy-kyverno":       f"{_P}Policy.Kyverno",
    "policy-gatekeeper":    f"{_P}Policy.Gatekeeper",
    "policy-kubewarden":    f"{_P}Policy.Kubewarden",
    "aws-config":           f"{_P}Policy.AWSConfig",
    "az-policy":            f"{_P}Policy.AzurePolicy",
    # Gates
    "gate-manual":          f"{_P}ApprovalGate.Manual",
    "gate-automated":       f"{_P}ApprovalGate.Automated",
    "gate-vuln-threshold":  f"{_P}ApprovalGate.VulnThreshold",
    "gate-deploy-window":   f"{_P}ApprovalGate.DeployWindow",
    # Secrets management
    "vault-hashicorp":      f"{_P}SecretsManager.Vault",
    "vault-openbao":        f"{_P}SecretsManager.OpenBao",
    "aws-secrets":          f"{_P}SecretsManager.AWSSecrets",
    "aws-kms":              f"{_P}SecretsManager.AWSKMS",
    "az-keyvault":          f"{_P}SecretsManager.AzureKeyVault",
    "gcp-secret":           f"{_P}SecretsManager.GCPSecret",
    "gcp-kms":              f"{_P}SecretsManager.GCPKMS",
    "oci-vault":            f"{_P}SecretsManager.OCIVault",
    # Deploy strategies
    "deploy-bigbang":       f"{_P}DeployStrategy.BigBang",
    "deploy-serverless":    f"{_P}DeployStrategy.Serverless",
    "deploy-vm":            f"{_P}DeployStrategy.VM",
    "deploy-edge":          f"{_P}DeployStrategy.Edge",
    "deploy-canary":        f"{_P}DeployStrategy.Canary",
    "deploy-bluegreen":     f"{_P}DeployStrategy.BlueGreen",
    "deploy-feature-flag":  f"{_P}DeployStrategy.FeatureFlag",
}
