/* CUI // SP-CTI — Pipeline Design Canvas: Node Styles + Type Sets + Stage Data
 * Pure data module (pdx-ux-01 split of pipeline-canvas.js). No behaviour, no
 * DOM/graph access — only top-level `const` tables consumed by the other
 * pipeline-*.js modules loaded after this file on canvas.html.
 *
 * Load order contract (classic scripts, shared global lexical environment):
 *   1. pipeline-node-styles.js  ← this file (data)
 *   2. pipeline-canvas-core.js
 *   3. pipeline-ndc-bridge.js
 *   4. pipeline-snippets.js
 *   5. pipeline-iac.js
 *   6. pipeline-analysis.js
 * No ES modules / bundler / framework — every declaration stays top-level so
 * inline canvas.html handlers and data-attribute listeners keep resolving them.
 */

'use strict';

// ── Node Styles (100+ types) ─────────────────────────────────────────────────
// Each: { fill, stroke, label, symbol }
// Color families: orchestration=blue, source=green, build=cyan, test=purple,
// package=violet, gate=red, deploy=orange, monitor=teal, compliance=emerald,
// cross-domain=crimson, mesh=pink

const NODE_STYLES = {
  // ── Orchestration (blue) ──
  'cicd-gitlab':         { fill: '#0f2b3a', stroke: '#3498db', label: 'GitLab CI',      symbol: 'GL' },
  'cicd-jenkins':        { fill: '#0f2b3a', stroke: '#3498db', label: 'Jenkins',         symbol: 'JK' },
  'cicd-tekton':         { fill: '#0f2b3a', stroke: '#3498db', label: 'Tekton',          symbol: 'TK' },
  'cicd-github-actions': { fill: '#0f2b3a', stroke: '#3498db', label: 'GitHub Actions',  symbol: 'GH' },
  'cicd-argo-workflows': { fill: '#0f2b3a', stroke: '#3498db', label: 'Argo Workflows',  symbol: 'AW' },
  'cicd-drone':          { fill: '#0f2b3a', stroke: '#3498db', label: 'Drone',           symbol: 'DR' },
  'gitops-argocd':       { fill: '#0f2b3a', stroke: '#2980b9', label: 'ArgoCD',          symbol: 'AC' },
  'gitops-flux':         { fill: '#0f2b3a', stroke: '#2980b9', label: 'Flux CD',         symbol: 'FX' },
  'aws-codepipeline':    { fill: '#1a2a0f', stroke: '#ff9900', label: 'CodePipeline',    symbol: 'CP' },
  'aws-codebuild':       { fill: '#1a2a0f', stroke: '#ff9900', label: 'CodeBuild',       symbol: 'CB' },
  'aws-codedeploy':      { fill: '#1a2a0f', stroke: '#ff9900', label: 'CodeDeploy',      symbol: 'CD' },
  'az-pipelines':        { fill: '#0f1a2b', stroke: '#0078d4', label: 'Azure Pipelines', symbol: 'AP' },
  'gcp-cloudbuild':      { fill: '#0f2b1a', stroke: '#4285f4', label: 'Cloud Build',     symbol: 'GB' },
  'gcp-deploy':          { fill: '#0f2b1a', stroke: '#4285f4', label: 'Cloud Deploy',    symbol: 'GD' },
  'oci-devops':          { fill: '#2b0f0f', stroke: '#f80000', label: 'OCI DevOps',      symbol: 'OD' },
  'ibm-cd':              { fill: '#0f0f2b', stroke: '#1261fe', label: 'IBM CD',          symbol: 'IC' },

  // ── Source Control (green) ──
  'scm-gitlab':          { fill: '#0f2b0f', stroke: '#27ae60', label: 'GitLab',          symbol: 'GL' },
  'scm-gitea':           { fill: '#0f2b0f', stroke: '#27ae60', label: 'Gitea',           symbol: 'GT' },
  'scm-forgejo':         { fill: '#0f2b0f', stroke: '#27ae60', label: 'Forgejo',         symbol: 'FJ' },
  'scm-bitbucket':       { fill: '#0f2b0f', stroke: '#27ae60', label: 'Bitbucket',       symbol: 'BB' },
  'aws-codecommit':      { fill: '#1a2a0f', stroke: '#ff9900', label: 'CodeCommit',      symbol: 'CC' },
  'az-repos':            { fill: '#0f1a2b', stroke: '#0078d4', label: 'Azure Repos',     symbol: 'AR' },
  'gcp-source':          { fill: '#0f2b1a', stroke: '#4285f4', label: 'Cloud Source',    symbol: 'GS' },
  'oci-code-repos':      { fill: '#2b0f0f', stroke: '#f80000', label: 'OCI Code',        symbol: 'OR' },
  'branch-policy':       { fill: '#0f2b0f', stroke: '#2ecc71', label: 'Branch Policy',   symbol: 'BP' },
  'commit-signing':      { fill: '#0f2b0f', stroke: '#2ecc71', label: 'Commit Sign',     symbol: 'CS' },

  // ── Build (cyan) ──
  'build-runner':        { fill: '#0f2b2b', stroke: '#1abc9c', label: 'Build Runner',    symbol: 'BR' },
  'build-kaniko':        { fill: '#0f2b2b', stroke: '#1abc9c', label: 'Kaniko',          symbol: 'KN' },
  'build-buildah':       { fill: '#0f2b2b', stroke: '#1abc9c', label: 'Buildah',         symbol: 'BH' },
  'build-docker':        { fill: '#0f2b2b', stroke: '#1abc9c', label: 'Docker Build',    symbol: 'DK' },
  'build-bazel':         { fill: '#0f2b2b', stroke: '#1abc9c', label: 'Bazel',           symbol: 'BZ' },
  'build-gradle':        { fill: '#0f2b2b', stroke: '#1abc9c', label: 'Gradle',          symbol: 'GR' },
  'build-maven':         { fill: '#0f2b2b', stroke: '#1abc9c', label: 'Maven',           symbol: 'MV' },

  // ── Security Scanning (purple) ──
  'scan-sast':           { fill: '#1a0f2b', stroke: '#9b59b6', label: 'SAST',            symbol: 'SA' },
  'scan-sonarqube':      { fill: '#1a0f2b', stroke: '#9b59b6', label: 'SonarQube',       symbol: 'SQ' },
  'scan-semgrep':        { fill: '#1a0f2b', stroke: '#9b59b6', label: 'Semgrep',         symbol: 'SG' },
  'scan-codeql':         { fill: '#1a0f2b', stroke: '#9b59b6', label: 'CodeQL',          symbol: 'CQ' },
  'scan-bandit':         { fill: '#1a0f2b', stroke: '#9b59b6', label: 'Bandit',          symbol: 'BN' },
  'scan-spotbugs':       { fill: '#1a0f2b', stroke: '#9b59b6', label: 'SpotBugs',        symbol: 'SB' },
  'aws-codeguru':        { fill: '#1a2a0f', stroke: '#ff9900', label: 'CodeGuru',        symbol: 'CG' },
  'scan-dast':           { fill: '#1a0f2b', stroke: '#8e44ad', label: 'DAST',            symbol: 'DA' },
  'scan-zap':            { fill: '#1a0f2b', stroke: '#8e44ad', label: 'OWASP ZAP',       symbol: 'ZP' },
  'scan-nuclei':         { fill: '#1a0f2b', stroke: '#8e44ad', label: 'Nuclei',          symbol: 'NU' },
  'scan-burp':           { fill: '#1a0f2b', stroke: '#8e44ad', label: 'Burp Suite',      symbol: 'BS' },
  'scan-sca':            { fill: '#1a0f2b', stroke: '#9b59b6', label: 'SCA',             symbol: 'SC' },
  'scan-trivy':          { fill: '#1a0f2b', stroke: '#9b59b6', label: 'Trivy',           symbol: 'TV' },
  'scan-grype':          { fill: '#1a0f2b', stroke: '#9b59b6', label: 'Grype',           symbol: 'GY' },
  'scan-snyk':           { fill: '#1a0f2b', stroke: '#9b59b6', label: 'Snyk',            symbol: 'SK' },
  'scan-dep-check':      { fill: '#1a0f2b', stroke: '#9b59b6', label: 'Dep-Check',       symbol: 'DC' },
  'scan-iac':            { fill: '#1a0f2b', stroke: '#9b59b6', label: 'IaC Scan',        symbol: 'IC' },
  'scan-checkov':        { fill: '#1a0f2b', stroke: '#9b59b6', label: 'Checkov',         symbol: 'CK' },
  'scan-tfsec':          { fill: '#1a0f2b', stroke: '#9b59b6', label: 'tfsec',           symbol: 'TF' },
  'scan-kics':           { fill: '#1a0f2b', stroke: '#9b59b6', label: 'KICS',            symbol: 'KI' },
  'scan-secret':         { fill: '#2b0f1a', stroke: '#e74c3c', label: 'Secret Detect',   symbol: 'SD' },
  'scan-gitleaks':       { fill: '#2b0f1a', stroke: '#e74c3c', label: 'Gitleaks',        symbol: 'GK' },
  'scan-trufflehog':     { fill: '#2b0f1a', stroke: '#e74c3c', label: 'TruffleHog',      symbol: 'TH' },
  'scan-detect-secrets': { fill: '#2b0f1a', stroke: '#e74c3c', label: 'detect-secrets',  symbol: 'DS' },
  'scan-container':      { fill: '#1a0f2b', stroke: '#8e44ad', label: 'Container Scan',  symbol: 'CN' },
  'scan-anchore':        { fill: '#1a0f2b', stroke: '#8e44ad', label: 'Anchore',         symbol: 'AE' },
  'scan-neuvector':      { fill: '#1a0f2b', stroke: '#8e44ad', label: 'NeuVector',       symbol: 'NV' },
  'aws-inspector':       { fill: '#1a2a0f', stroke: '#ff9900', label: 'Inspector',       symbol: 'IN' },
  'az-defender':         { fill: '#0f1a2b', stroke: '#0078d4', label: 'Defender',         symbol: 'DF' },
  'gcp-artifact-analysis':{ fill: '#0f2b1a', stroke: '#4285f4', label: 'Artifact Analysis',symbol: 'AA' },
  'ibm-vuln-advisor':    { fill: '#0f0f2b', stroke: '#1261fe', label: 'Vuln Advisor',    symbol: 'VA' },
  'scan-license':        { fill: '#1a0f2b', stroke: '#9b59b6', label: 'License Scan',    symbol: 'LS' },

  // ── Artifact Management (violet) ──
  'registry-generic':    { fill: '#200f2b', stroke: '#8e44ad', label: 'Registry',        symbol: 'CR' },
  'registry-harbor':     { fill: '#200f2b', stroke: '#8e44ad', label: 'Harbor',           symbol: 'HB' },
  'registry-nexus':      { fill: '#200f2b', stroke: '#8e44ad', label: 'Nexus',            symbol: 'NX' },
  'registry-jfrog':      { fill: '#200f2b', stroke: '#8e44ad', label: 'Artifactory',      symbol: 'JF' },
  'registry-zot':        { fill: '#200f2b', stroke: '#8e44ad', label: 'Zot',              symbol: 'ZT' },
  'aws-ecr':             { fill: '#1a2a0f', stroke: '#ff9900', label: 'ECR',              symbol: 'EC' },
  'az-acr':              { fill: '#0f1a2b', stroke: '#0078d4', label: 'ACR',              symbol: 'AC' },
  'gcp-gar':             { fill: '#0f2b1a', stroke: '#4285f4', label: 'Artifact Registry',symbol: 'GA' },
  'oci-cr':              { fill: '#2b0f0f', stroke: '#f80000', label: 'OCI CR',           symbol: 'OC' },
  'ibm-cr':              { fill: '#0f0f2b', stroke: '#1261fe', label: 'IBM CR',           symbol: 'IR' },
  'registry-ironbank':   { fill: '#2b1a0f', stroke: '#d35400', label: 'Iron Bank',        symbol: 'IB' },
  'sbom-store':          { fill: '#200f2b', stroke: '#8e44ad', label: 'SBOM Store',       symbol: 'SB' },
  'package-repo':        { fill: '#200f2b', stroke: '#8e44ad', label: 'Package Repo',     symbol: 'PK' },

  // ── Supply Chain (gold) ──
  'sign-cosign':         { fill: '#2b2a0f', stroke: '#f1c40f', label: 'Cosign',           symbol: 'CO' },
  'sign-notation':       { fill: '#2b2a0f', stroke: '#f1c40f', label: 'Notation',         symbol: 'NT' },
  'sign-dct':            { fill: '#2b2a0f', stroke: '#f1c40f', label: 'DCT',              symbol: 'DT' },
  'attest-in-toto':      { fill: '#2b2a0f', stroke: '#f39c12', label: 'in-toto',          symbol: 'IT' },
  'attest-slsa-gen':     { fill: '#2b2a0f', stroke: '#f39c12', label: 'SLSA Gen',         symbol: 'SL' },
  'verify-slsa':         { fill: '#2b2a0f', stroke: '#f39c12', label: 'SLSA Verify',      symbol: 'SV' },
  'sbom-syft':           { fill: '#2b2a0f', stroke: '#f1c40f', label: 'Syft SBOM',        symbol: 'SY' },
  'sbom-cyclonedx':      { fill: '#2b2a0f', stroke: '#f1c40f', label: 'CycloneDX',        symbol: 'CD' },
  'sbom-spdx':           { fill: '#2b2a0f', stroke: '#f1c40f', label: 'SPDX',             symbol: 'SP' },
  'vex-openvex':         { fill: '#2b2a0f', stroke: '#f1c40f', label: 'OpenVEX',          symbol: 'VX' },
  'gcp-binary-auth':     { fill: '#0f2b1a', stroke: '#4285f4', label: 'Binary Auth',      symbol: 'BA' },
  'ibm-portieris':       { fill: '#0f0f2b', stroke: '#1261fe', label: 'Portieris',        symbol: 'PT' },

  // ── Policy & Governance (red) ──
  'policy-opa':          { fill: '#2b0f0f', stroke: '#e94560', label: 'OPA/Rego',         symbol: 'OP' },
  'policy-kyverno':      { fill: '#2b0f0f', stroke: '#e94560', label: 'Kyverno',          symbol: 'KV' },
  'policy-gatekeeper':   { fill: '#2b0f0f', stroke: '#e94560', label: 'Gatekeeper',       symbol: 'GK' },
  'policy-kubewarden':   { fill: '#2b0f0f', stroke: '#e94560', label: 'Kubewarden',       symbol: 'KW' },
  'aws-config':          { fill: '#1a2a0f', stroke: '#ff9900', label: 'Config Rules',     symbol: 'CF' },
  'az-policy':           { fill: '#0f1a2b', stroke: '#0078d4', label: 'Azure Policy',     symbol: 'AZ' },
  'gate-manual':         { fill: '#2b1a0f', stroke: '#f39c12', label: 'Manual Gate',      symbol: 'MG' },
  'gate-automated':      { fill: '#2b1a0f', stroke: '#e67e22', label: 'Auto Gate',        symbol: 'AG' },
  'gate-vuln-threshold': { fill: '#2b0f0f', stroke: '#e74c3c', label: 'Vuln Gate',        symbol: 'VT' },
  'gate-deploy-window':  { fill: '#2b1a0f', stroke: '#f39c12', label: 'Deploy Window',    symbol: 'DW' },

  // ── Secrets & Keys (dark blue) ──
  'vault-hashicorp':     { fill: '#0f0f2b', stroke: '#5b6abf', label: 'Vault',            symbol: 'HV' },
  'vault-openbao':       { fill: '#0f0f2b', stroke: '#5b6abf', label: 'OpenBao',          symbol: 'OB' },
  'aws-secrets':         { fill: '#1a2a0f', stroke: '#ff9900', label: 'Secrets Mgr',      symbol: 'SM' },
  'aws-kms':             { fill: '#1a2a0f', stroke: '#ff9900', label: 'AWS KMS',          symbol: 'KM' },
  'az-keyvault':         { fill: '#0f1a2b', stroke: '#0078d4', label: 'Key Vault',        symbol: 'KV' },
  'gcp-secret':          { fill: '#0f2b1a', stroke: '#4285f4', label: 'Secret Mgr',       symbol: 'GS' },
  'gcp-kms':             { fill: '#0f2b1a', stroke: '#4285f4', label: 'Cloud KMS',        symbol: 'CK' },
  'oci-vault':           { fill: '#2b0f0f', stroke: '#f80000', label: 'OCI Vault',        symbol: 'OV' },
  'ibm-secrets':         { fill: '#0f0f2b', stroke: '#1261fe', label: 'IBM Secrets',      symbol: 'IS' },
  'ibm-hpcs':            { fill: '#0f0f2b', stroke: '#1261fe', label: 'HPCS',             symbol: 'HP' },
  'kms-generic':         { fill: '#0f0f2b', stroke: '#5b6abf', label: 'KMS',              symbol: 'KM' },
  'hsm-generic':         { fill: '#0f0f2b', stroke: '#5b6abf', label: 'HSM',              symbol: 'HS' },
  'cert-manager':        { fill: '#0f0f2b', stroke: '#5b6abf', label: 'cert-manager',     symbol: 'CM' },
  'sealed-secrets':      { fill: '#0f0f2b', stroke: '#5b6abf', label: 'Sealed Secrets',   symbol: 'SS' },
  'sops':                { fill: '#0f0f2b', stroke: '#5b6abf', label: 'SOPS',             symbol: 'SO' },
  'external-secrets':    { fill: '#0f0f2b', stroke: '#5b6abf', label: 'Ext Secrets',      symbol: 'ES' },

  // ── Deploy Targets (orange) ──
  'k8s-cluster':         { fill: '#2b1a0f', stroke: '#e67e22', label: 'Kubernetes',       symbol: 'K8' },
  'aws-eks':             { fill: '#1a2a0f', stroke: '#ff9900', label: 'EKS',              symbol: 'EK' },
  'az-aks':              { fill: '#0f1a2b', stroke: '#0078d4', label: 'AKS',              symbol: 'AK' },
  'gcp-gke':             { fill: '#0f2b1a', stroke: '#4285f4', label: 'GKE',              symbol: 'GK' },
  'oci-oke':             { fill: '#2b0f0f', stroke: '#f80000', label: 'OKE',              symbol: 'OK' },
  'ibm-iks':             { fill: '#0f0f2b', stroke: '#1261fe', label: 'IKS',              symbol: 'IK' },
  'openshift':           { fill: '#2b0f0f', stroke: '#ee0000', label: 'OpenShift',        symbol: 'OS' },
  'rke2':                { fill: '#2b1a0f', stroke: '#e67e22', label: 'RKE2',             symbol: 'R2' },
  'k3s':                 { fill: '#2b1a0f', stroke: '#e67e22', label: 'K3s',              symbol: 'K3' },
  'deploy-bigbang':      { fill: '#2b1a0f', stroke: '#d35400', label: 'Big Bang',         symbol: 'BB' },
  'deploy-serverless':   { fill: '#2b1a0f', stroke: '#e67e22', label: 'Serverless',       symbol: 'SL' },
  'deploy-vm':           { fill: '#2b1a0f', stroke: '#e67e22', label: 'VM Target',        symbol: 'VM' },
  'deploy-edge':         { fill: '#2b1a0f', stroke: '#e67e22', label: 'Edge',             symbol: 'ED' },
  'deploy-canary':       { fill: '#2b1a0f', stroke: '#f39c12', label: 'Canary',           symbol: 'CY' },
  'deploy-bluegreen':    { fill: '#2b1a0f', stroke: '#f39c12', label: 'Blue-Green',       symbol: 'BG' },
  'deploy-feature-flag': { fill: '#2b1a0f', stroke: '#f39c12', label: 'Feature Flag',     symbol: 'FF' },

  // ── Monitoring (teal) ──
  'mon-prometheus':      { fill: '#0f2b2b', stroke: '#1abc9c', label: 'Prometheus',       symbol: 'PM' },
  'mon-grafana':         { fill: '#0f2b2b', stroke: '#1abc9c', label: 'Grafana',          symbol: 'GR' },
  'mon-loki':            { fill: '#0f2b2b', stroke: '#1abc9c', label: 'Loki',             symbol: 'LK' },
  'mon-tempo':           { fill: '#0f2b2b', stroke: '#1abc9c', label: 'Tempo',            symbol: 'TP' },
  'mon-otel':            { fill: '#0f2b2b', stroke: '#1abc9c', label: 'OpenTelemetry',    symbol: 'OT' },
  'mon-elk':             { fill: '#0f2b2b', stroke: '#1abc9c', label: 'ELK Stack',        symbol: 'EL' },
  'mon-fluentbit':       { fill: '#0f2b2b', stroke: '#1abc9c', label: 'Fluent Bit',       symbol: 'FB' },
  'aws-cloudwatch':      { fill: '#1a2a0f', stroke: '#ff9900', label: 'CloudWatch',       symbol: 'CW' },
  'aws-guardduty':       { fill: '#1a2a0f', stroke: '#ff9900', label: 'GuardDuty',        symbol: 'GD' },
  'az-monitor':          { fill: '#0f1a2b', stroke: '#0078d4', label: 'Azure Monitor',    symbol: 'AM' },
  'az-sentinel':         { fill: '#0f1a2b', stroke: '#0078d4', label: 'Sentinel',         symbol: 'SE' },
  'gcp-monitoring':      { fill: '#0f2b1a', stroke: '#4285f4', label: 'Cloud Monitor',    symbol: 'GM' },
  'gcp-scc':             { fill: '#0f2b1a', stroke: '#4285f4', label: 'SCC',              symbol: 'SC' },
  'mon-falco':           { fill: '#0f2b2b', stroke: '#16a085', label: 'Falco',            symbol: 'FC' },
  'mon-wazuh':           { fill: '#0f2b2b', stroke: '#16a085', label: 'Wazuh',            symbol: 'WZ' },
  'mon-soar':            { fill: '#0f2b2b', stroke: '#1abc9c', label: 'SOAR',             symbol: 'SR' },
  'mon-pagerduty':       { fill: '#0f2b2b', stroke: '#1abc9c', label: 'PagerDuty',        symbol: 'PD' },

  // ── Compliance (emerald) ──
  'comp-dashboard':      { fill: '#0f2b1a', stroke: '#16a085', label: 'Compliance',       symbol: 'CD' },
  'comp-evidence':       { fill: '#0f2b1a', stroke: '#16a085', label: 'Evidence Locker',  symbol: 'EL' },
  'comp-oscal':          { fill: '#0f2b1a', stroke: '#16a085', label: 'OSCAL Export',     symbol: 'OX' },
  'comp-stigman':        { fill: '#0f2b1a', stroke: '#16a085', label: 'STIG Manager',     symbol: 'SM' },
  'comp-openscap':       { fill: '#0f2b1a', stroke: '#16a085', label: 'OpenSCAP',         symbol: 'OS' },
  'comp-inspec':         { fill: '#0f2b1a', stroke: '#16a085', label: 'InSpec',            symbol: 'IS' },
  'aws-securityhub':     { fill: '#1a2a0f', stroke: '#ff9900', label: 'Security Hub',     symbol: 'SH' },
  'aws-audit':           { fill: '#1a2a0f', stroke: '#ff9900', label: 'Audit Mgr',        symbol: 'AU' },
  'az-defender-cloud':   { fill: '#0f1a2b', stroke: '#0078d4', label: 'Defender Cloud',   symbol: 'DC' },
  'ibm-scc':             { fill: '#0f0f2b', stroke: '#1261fe', label: 'IBM SCC',          symbol: 'SC' },

  // ── Cross-Domain (crimson) ──
  'cds-guard':           { fill: '#2b0f1a', stroke: '#c0392b', label: 'CDS Guard',        symbol: 'GD' },
  'cds-data-diode':      { fill: '#2b0f1a', stroke: '#c0392b', label: 'Data Diode',       symbol: 'DD' },
  'cds-emulator':        { fill: '#2b0f1a', stroke: '#e74c3c', label: 'CDS Emulator',     symbol: 'EM' },
  'cds-transfer':        { fill: '#2b0f1a', stroke: '#c0392b', label: 'Transfer Svc',     symbol: 'TS' },
  'boundary-commercial': { fill: '#1a2b0f', stroke: '#27ae60', label: 'Commercial',       symbol: 'CC' },
  'boundary-govcloud':   { fill: '#1a1a2b', stroke: '#f39c12', label: 'GovCloud',         symbol: 'GC' },
  'boundary-secret':     { fill: '#2b0f0f', stroke: '#e74c3c', label: 'SECRET',           symbol: 'SC' },
  'boundary-topsecret':  { fill: '#2b0f0f', stroke: '#c0392b', label: 'TOP SECRET',       symbol: 'TS' },
  'pipeline-nipr':       { fill: '#0f2b0f', stroke: '#27ae60', label: 'NIPR Pipeline',    symbol: 'NP' },
  'pipeline-sipr':       { fill: '#2b1a0f', stroke: '#e74c3c', label: 'SIPR Pipeline',    symbol: 'SP' },
  'pipeline-jwics':      { fill: '#2b0f0f', stroke: '#c0392b', label: 'JWICS Pipeline',   symbol: 'JP' },
  'sneakernet':          { fill: '#2b0f1a', stroke: '#c0392b', label: 'Sneakernet',       symbol: 'SN' },
  'vuln-db-mirror':      { fill: '#2b0f1a', stroke: '#e74c3c', label: 'Vuln DB Mirror',   symbol: 'VM' },
  'package-mirror':      { fill: '#2b0f1a', stroke: '#e74c3c', label: 'Package Mirror',   symbol: 'PM' },

  // ── Service Mesh (pink) ──
  'mesh-istio':          { fill: '#2b0f2b', stroke: '#e91e63', label: 'Istio',            symbol: 'IS' },
  'mesh-linkerd':        { fill: '#2b0f2b', stroke: '#e91e63', label: 'Linkerd',          symbol: 'LD' },
  'mesh-consul':         { fill: '#2b0f2b', stroke: '#e91e63', label: 'Consul',           symbol: 'CC' },

  // ── SRE / Reliability (cyan) ──
  'sre-slo':             { fill: '#0f2b2b', stroke: '#00bcd4', label: 'SLO',              symbol: 'SLO' },
  'sre-sli':             { fill: '#0f2b2b', stroke: '#00bcd4', label: 'SLI Metric',       symbol: 'SLI' },
  'sre-error-budget':    { fill: '#0f2b2b', stroke: '#00bcd4', label: 'Error Budget',     symbol: 'EB'  },
  'sre-burn-rate':       { fill: '#0f2b2b', stroke: '#00bcd4', label: 'Burn Rate',        symbol: 'BR'  },
  'sre-incident':        { fill: '#0f2b2b', stroke: '#00acc1', label: 'Incident Mgr',     symbol: 'INC' },
  'sre-postmortem':      { fill: '#0f2b2b', stroke: '#00acc1', label: 'Postmortem',       symbol: 'PM'  },
  'sre-oncall':          { fill: '#0f2b2b', stroke: '#00acc1', label: 'On-Call',           symbol: 'OC'  },
  'sre-statuspage':      { fill: '#0f2b2b', stroke: '#00acc1', label: 'Status Page',      symbol: 'SP'  },
  'sre-runbook':         { fill: '#0f2b2b', stroke: '#0097a7', label: 'Runbook',           symbol: 'RB'  },
  'sre-self-heal':       { fill: '#0f2b2b', stroke: '#0097a7', label: 'Self-Healing',     symbol: 'SH'  },
  'sre-chaos':           { fill: '#2b0f1a', stroke: '#ff5722', label: 'Chaos Exp',        symbol: 'CX'  },
  'sre-chaos-litmus':    { fill: '#2b0f1a', stroke: '#ff5722', label: 'Litmus',           symbol: 'LT'  },
  'aws-fis':             { fill: '#1a2a0f', stroke: '#ff9900', label: 'AWS FIS',          symbol: 'FI'  },
  'az-chaos-studio':     { fill: '#0f1a2b', stroke: '#0078d4', label: 'Chaos Studio',     symbol: 'CS'  },
  'sre-dora':            { fill: '#0f2b2b', stroke: '#00e5ff', label: 'DORA Metrics',     symbol: 'DO'  },
  'sre-dora-deploy-freq':{ fill: '#0f2b2b', stroke: '#00e5ff', label: 'Deploy Freq',      symbol: 'DF'  },
  'sre-dora-lead-time':  { fill: '#0f2b2b', stroke: '#00e5ff', label: 'Lead Time',        symbol: 'LT'  },
  'sre-dora-cfr':        { fill: '#0f2b2b', stroke: '#00e5ff', label: 'Failure Rate',     symbol: 'CF'  },
  'sre-dora-mttr':       { fill: '#0f2b2b', stroke: '#00e5ff', label: 'MTTR',             symbol: 'MT'  },
  'sre-resilience':      { fill: '#0f2b2b', stroke: '#00bcd4', label: 'Resilience Score', symbol: 'RS'  },
  'aws-resilience-hub':  { fill: '#1a2a0f', stroke: '#ff9900', label: 'Resilience Hub',   symbol: 'RH'  },
  'aws-cw-slo':          { fill: '#1a2a0f', stroke: '#ff9900', label: 'CW SLO',           symbol: 'CL'  },
  'aws-incident-mgr':    { fill: '#1a2a0f', stroke: '#ff9900', label: 'Incident Mgr',     symbol: 'IM'  },
  'gcp-service-mon':     { fill: '#0f2b1a', stroke: '#4285f4', label: 'Service Mon',      symbol: 'SM'  },
  'az-advisor-rel':      { fill: '#0f1a2b', stroke: '#0078d4', label: 'Advisor Rel',      symbol: 'AR'  },
  'ibm-instana':         { fill: '#0f0f2b', stroke: '#1261fe', label: 'Instana',          symbol: 'IN'  },
  'sre-openslo':         { fill: '#0f2b2b', stroke: '#00bcd4', label: 'OpenSLO',          symbol: 'OS'  },
  'sre-sloth':           { fill: '#0f2b2b', stroke: '#00bcd4', label: 'Sloth',            symbol: 'SL'  },
  'sre-pyrra':           { fill: '#0f2b2b', stroke: '#00bcd4', label: 'Pyrra',            symbol: 'PY'  },
  'sre-pagerduty':       { fill: '#0f2b2b', stroke: '#00acc1', label: 'PagerDuty',        symbol: 'PD'  },
  'sre-grafana-oncall':  { fill: '#0f2b2b', stroke: '#00acc1', label: 'Grafana OnCall',   symbol: 'GO'  },
  'sre-opsgenie':        { fill: '#0f2b2b', stroke: '#00acc1', label: 'Opsgenie',         symbol: 'OG'  },
  'sre-backstage':       { fill: '#0f2b2b', stroke: '#00bcd4', label: 'Backstage',        symbol: 'BS'  },
  'sre-circuit-breaker': { fill: '#0f2b2b', stroke: '#0097a7', label: 'Circuit Breaker',  symbol: 'CB'  },
  'sre-retry':           { fill: '#0f2b2b', stroke: '#0097a7', label: 'Retry+Backoff',    symbol: 'RT'  },
  'sre-bulkhead':        { fill: '#0f2b2b', stroke: '#0097a7', label: 'Bulkhead',         symbol: 'BH'  },

  // ── Infrastructure Bridge (NDC ↔ PDC) — gold/bronze ──
  'ndc-topology':        { fill: '#2b2b0f', stroke: '#d4a017', label: 'NDC Topology',     symbol: 'NET' },
  'ndc-vpc':             { fill: '#2b2b0f', stroke: '#d4a017', label: 'NDC VPC/VNet',     symbol: 'VPC' },
  'hybrid-directconnect':{ fill: '#2b1a0f', stroke: '#cd7f32', label: 'Direct Connect',   symbol: 'DX'  },
  'hybrid-vpn':          { fill: '#2b1a0f', stroke: '#cd7f32', label: 'Site-to-Site VPN', symbol: 'VPN' },
  'hybrid-transit':      { fill: '#2b1a0f', stroke: '#cd7f32', label: 'Transit Hub',      symbol: 'TGW' },
  'hybrid-peering':      { fill: '#2b1a0f', stroke: '#cd7f32', label: 'Cloud Peering',    symbol: 'PER' },
  'onprem-datacenter':   { fill: '#1a1a0f', stroke: '#b8860b', label: 'On-Prem DC',       symbol: 'DC'  },
  'onprem-colo':         { fill: '#1a1a0f', stroke: '#b8860b', label: 'Colocation',       symbol: 'CO'  },
  'onprem-edge':         { fill: '#1a1a0f', stroke: '#b8860b', label: 'Edge Site',        symbol: 'ED'  },
};

// ── Snippet Type Sets (for auto-connect rules) ─────────────────────────────
// Consumed by pipeline-snippets.js (_buildIntegrationSuggestions) at runtime.

const PIPELINE_TYPE_SETS = {
  CI_ENGINE: new Set(['cicd-gitlab','cicd-jenkins','cicd-tekton','cicd-github-actions','cicd-argo-workflows','cicd-drone','aws-codepipeline','aws-codebuild','az-pipelines','gcp-cloudbuild','oci-devops','ibm-cd']),
  SCM: new Set(['scm-gitlab','scm-gitea','scm-forgejo','scm-bitbucket','aws-codecommit','az-repos','gcp-source','oci-code-repos']),
  SCANNER: new Set(['scan-sast','scan-sonarqube','scan-semgrep','scan-codeql','scan-bandit','scan-dast','scan-zap','scan-sca','scan-trivy','scan-grype','scan-snyk','scan-dep-check','scan-iac','scan-checkov','scan-container','scan-anchore','scan-neuvector']),
  REGISTRY: new Set(['registry-generic','registry-harbor','registry-nexus','registry-jfrog','aws-ecr','az-acr','gcp-gar','oci-cr','ibm-cr','registry-ironbank']),
  SIGNER: new Set(['sign-cosign','sign-notation','sign-dct','attest-in-toto','attest-slsa-gen']),
  K8S: new Set(['k8s-cluster','aws-eks','az-aks','gcp-gke','oci-oke','ibm-iks','openshift','rke2','k3s','deploy-bigbang']),
  POLICY: new Set(['policy-opa','policy-kyverno','policy-gatekeeper','policy-kubewarden','gcp-binary-auth','ibm-portieris','verify-slsa']),
  MONITOR: new Set(['mon-prometheus','mon-grafana','mon-loki','mon-falco','mon-wazuh','mon-soar','aws-cloudwatch','az-monitor','gcp-monitoring','gcp-scc','az-sentinel']),
  CLOUD_MANAGED: new Set(['aws-codepipeline','aws-codebuild','aws-ecr','aws-eks','aws-inspector','aws-guardduty','aws-cloudwatch','aws-securityhub','az-pipelines','az-acr','az-aks','az-defender','az-monitor','az-sentinel','gcp-cloudbuild','gcp-gke','gcp-gar','gcp-artifact-analysis','gcp-scc']),
  AIRGAP_INFRA: new Set(['vuln-db-mirror','package-mirror','sneakernet','cds-data-diode','cds-guard','cds-emulator','cds-transfer','pipeline-sipr','pipeline-jwics']),
  SRE_SLO: new Set(['sre-slo','sre-sli','sre-error-budget','sre-burn-rate','sre-openslo','sre-sloth','sre-pyrra','aws-cw-slo','gcp-service-mon']),
  SRE_INCIDENT: new Set(['sre-incident','sre-postmortem','sre-oncall','sre-statuspage','sre-pagerduty','sre-grafana-oncall','sre-opsgenie','aws-incident-mgr']),
  SRE_RUNBOOK: new Set(['sre-runbook','sre-self-heal']),
  SRE_CHAOS: new Set(['sre-chaos','sre-chaos-litmus','aws-fis','az-chaos-studio']),
  SRE_DORA: new Set(['sre-dora','sre-dora-deploy-freq','sre-dora-lead-time','sre-dora-cfr','sre-dora-mttr']),
  SRE_RESILIENCE: new Set(['sre-resilience','aws-resilience-hub','sre-circuit-breaker','sre-retry','sre-bulkhead']),
  NDC_BRIDGE: new Set(['ndc-topology','ndc-vpc','hybrid-directconnect','hybrid-vpn','hybrid-transit','hybrid-peering','onprem-datacenter','onprem-colo','onprem-edge']),
  HYBRID_CONNECT: new Set(['hybrid-directconnect','hybrid-vpn','hybrid-transit','hybrid-peering']),
  ONPREM: new Set(['onprem-datacenter','onprem-colo','onprem-edge','rke2','k3s','deploy-edge']),
};

// ── Boundary Boxes (Stage Lanes) — colors + labels ──────────────────────────
// Consumed by pipeline-canvas-core.js (boundary boxes, legend) and
// pipeline-analysis.js (_renderExecTime stage breakdown) at runtime.

const STAGE_COLORS = {
  'pre_commit': '#27ae60', 'source': '#2ecc71', 'build': '#3498db',
  'test': '#9b59b6', 'package': '#8e44ad', 'policy_gate': '#e74c3c',
  'deploy_staging': '#e67e22', 'approval': '#f39c12', 'deploy_prod': '#d35400',
  'monitor': '#1abc9c', 'compliance': '#16a085',
  // Cross-domain
  'sre': '#00bcd4',
  'cross_domain': '#c0392b',
  'infrastructure': '#d4a017',
};

const STAGE_LABELS = {
  'pre_commit': 'Pre-Commit', 'source': 'Source Control', 'build': 'Build',
  'test': 'Test & Scan', 'package': 'Package & Sign', 'policy_gate': 'Policy Gate',
  'deploy_staging': 'Deploy (Staging)', 'approval': 'Approval', 'deploy_prod': 'Deploy (Prod)',
  'monitor': 'Monitor', 'sre': 'SRE / Reliability', 'compliance': 'Compliance', 'cross_domain': 'Cross-Domain',
  'infrastructure': 'Infrastructure (NDC)',
};
