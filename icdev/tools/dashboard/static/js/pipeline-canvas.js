/* CUI // SP-CTI — Pipeline Design Canvas JavaScript
 * JointJS-based visual pipeline editor for DevSecOps workflow design.
 * Mirrors network-canvas.js patterns with pipeline-specific node styles.
 */

'use strict';

// ── XSS-safe escaping (pdx-sec-02) ───────────────────────────────────────────
// Provided by pdc-escape.js (loaded before this file on canvas.html). Local
// fallbacks keep the canvas self-contained if the shared file is absent.
const escapeHtml = (typeof window !== 'undefined' && window.escapeHtml) || function (value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
};
const escapeAttr = (typeof window !== 'undefined' && window.escapeAttr) || function (value) {
  return escapeHtml(value).replace(/`/g, '&#96;');
};

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

// ── State ────────────────────────────────────────────────────────────────────

let graph, paper, selectedCell = null;
let undoStack = [], redoStack = [];
let isDirty = false, saveTimer = null;
let suppressDirty = false; // true while initial graph is loading; see setupCanvasListeners
let pipelineId = 'new';

// ── JointJS Initialization ──────────────────────────────────────────────────

function initCanvas() {
  graph = new joint.dia.Graph();
  paper = new joint.dia.Paper({
    el: document.getElementById('pc-canvas-container'),
    model: graph,
    width: 5000, height: 5000,
    gridSize: 10,
    drawGrid: { name: 'dot', args: { color: 'rgba(0,0,0,0.12)' } },
    background: { color: 'transparent' },
    defaultLink: () => new joint.shapes.standard.Link({
      attrs: { line: { stroke: '#e94560', strokeWidth: 2, targetMarker: { type: 'classic', fill: '#e94560', size: 6 } } }
    }),
    validateConnection: () => true,
    snapLinks: { radius: 20 },
    linkPinning: false,
    interactive: { labelMove: false },
  });

  // Events
  paper.on('element:pointerclick', (view) => selectCell(view.model));
  paper.on('blank:pointerclick', () => deselectAll());
  paper.on('element:pointerdblclick', (view) => {
    const newLabel = prompt('Rename:', view.model.attr('label/text') || '');
    if (newLabel !== null) { pushUndo(); view.model.attr('label/text', newLabel); markDirty(); }
  });

  // Enhanced tooltips (shared utility from canvas-tooltips.js)
  // Adds config-aware rich tooltips. Pipeline-specific TOOL_INFO added as extra fields.
  if (typeof initEnhancedTooltips === 'function') {
    initEnhancedTooltips(paper, graph, (type) => {
      const s = getStyle(type);
      return { fill: s.fill || '#0f2b3a', stroke: s.stroke || '#3498db', label: s.label || type, symbol: s.symbol || '?' };
    });
  }

  let isPanning = false, panStart = {}, translateStart = {};
  const canvasArea = document.querySelector('.pc-canvas-area');

  // Drag-to-pan using paper.translate() — works at any scroll position
  paper.on('blank:pointerdown', (evt) => {
    isPanning = true;
    panStart = { x: evt.clientX, y: evt.clientY };
    translateStart = paper.translate();
    canvasArea.classList.add('is-panning');
  });
  document.addEventListener('mousemove', (evt) => {
    if (!isPanning) return;
    paper.translate(
      translateStart.tx + (evt.clientX - panStart.x),
      translateStart.ty + (evt.clientY - panStart.y)
    );
  });
  document.addEventListener('mouseup', () => {
    if (!isPanning) return;
    isPanning = false;
    canvasArea.classList.remove('is-panning');
  });

  // Mouse-wheel zoom centered on cursor — uses translate to keep point under cursor fixed
  canvasArea.addEventListener('wheel', (evt) => {
    evt.preventDefault();
    const s0 = paper.scale().sx;
    const factor = evt.deltaY < 0 ? 1.12 : 1 / 1.12;
    const s1 = Math.max(0.08, Math.min(4, s0 * factor));
    if (Math.abs(s1 - s0) < 0.001) return;
    const t0 = paper.translate();
    const areaRect = canvasArea.getBoundingClientRect();
    const mx = evt.clientX - areaRect.left;
    const my = evt.clientY - areaRect.top;
    // Keep the paper-space point under the cursor fixed: mx = px*s + tx → tx = mx - px*s
    const newTx = mx - (mx - t0.tx) * s1 / s0;
    const newTy = my - (my - t0.ty) * s1 / s0;
    paper.scale(s1, s1);
    paper.translate(newTx, newTy);
    _updateZoomLabel(s1);
  }, { passive: false });

  // Keyboard shortcuts: +/= zoom in, - zoom out, 0 reset, f fit
  document.addEventListener('keydown', (evt) => {
    const tag = (evt.target || {}).tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    if (evt.key === '+' || evt.key === '=') { zoomIn(); evt.preventDefault(); }
    else if (evt.key === '-') { zoomOut(); evt.preventDefault(); }
    else if (evt.key === '0') { zoomReset(); evt.preventDefault(); }
    else if (evt.key === 'f' || evt.key === 'F') { zoomFit(); evt.preventDefault(); }
  });

  // Suppress auto-save during initial graph load. JointJS fires
  // `change add remove` for every cell as it materializes; we don't want
  // those to count as "user edits" and trigger savePipeline() 3s later.
  // (Fixes bug where opening a canvas wrote an UPDATE to the server that
  // blanked description/classification/target_csp — see PUT handler and
  // loadGraph() which sets/clears this flag.)
  graph.on('change add remove', () => { if (!suppressDirty) markDirty(); });
}

// ── Node Creation ───────────────────────────────────────────────────────────

function getStyle(type) {
  return NODE_STYLES[type] || { fill: '#1a1a2e', stroke: '#7a8cb0', label: type, symbol: '?' };
}

function createNode(type, x, y, label, nodeId) {
  const style = getStyle(type);
  const displayLabel = label || style.label;
  const w = 110, h = 60;

  const node = new joint.shapes.standard.Rectangle({
    id: nodeId || joint.util.uuid(),
    position: { x: x || 100, y: y || 100 },
    size: { width: w, height: h },
    attrs: {
      body: { fill: style.fill, stroke: style.stroke, strokeWidth: 2, rx: 6, ry: 6 },
      label: { text: displayLabel, fill: '#eaeaea', fontSize: 10, fontFamily: "'Segoe UI', sans-serif" },
    },
  });

  node.set('nodeType', type);
  node.set('nodeDesc', style.label + ' (' + type + ')');
  graph.addCell(node);
  return node;
}

// Get bounding box of all existing elements to compute snippet placement offset
function _getCanvasBounds() {
  const elements = graph.getElements();
  if (!elements.length) return { maxX: 0, maxY: 0, minX: 0, minY: 0 };
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  elements.forEach(el => {
    const pos = el.position();
    const size = el.size();
    minX = Math.min(minX, pos.x);
    minY = Math.min(minY, pos.y);
    maxX = Math.max(maxX, pos.x + size.width);
    maxY = Math.max(maxY, pos.y + size.height);
  });
  return { minX, minY, maxX, maxY };
}

function createLink(srcId, tgtId, label, linkId) {
  // Reject orphan endpoints — JointJS otherwise renders an invisible link
  // at origin, which looks like "links sometimes don't appear" in the UI.
  if (!srcId || !tgtId || !graph.getCell(srcId) || !graph.getCell(tgtId)) {
    console.warn('createLink: skipping orphan edge', { srcId: srcId, tgtId: tgtId, linkId: linkId });
    return null;
  }

  const link = new joint.shapes.standard.Link({
    id: linkId || joint.util.uuid(),
    source: { id: srcId },
    target: { id: tgtId },
    attrs: { line: { stroke: '#e94560', strokeWidth: 2, targetMarker: { type: 'classic', fill: '#e94560', size: 6 } } },
    labels: label ? [{ attrs: { text: { text: label, fill: '#374151', fontSize: 9 } }, position: 0.5 }] : [],
  });
  graph.addCell(link);
  return link;
}

// ── Drag & Drop ─────────────────────────────────────────────────────────────

function onDragStart(event) {
  const el = event.target.closest('[data-type]');
  if (el) {
    event.dataTransfer.setData('text/plain', el.dataset.type);
    event.dataTransfer.effectAllowed = 'copy';
  }
}

function _findFreePosition(x, y, w, h) {
  const PAD = 15;
  const occupied = graph.getElements().map(el => {
    const p = el.position(), s = el.size();
    return { x: p.x - PAD, y: p.y - PAD, x2: p.x + s.width + PAD, y2: p.y + s.height + PAD };
  });
  const overlaps = (cx, cy) => occupied.some(b => cx < b.x2 && cx + w > b.x && cy < b.y2 && cy + h > b.y);
  if (!overlaps(x, y)) return { x, y };
  const STEP = Math.max(w, h) + PAD;
  for (let ring = 1; ring <= 12; ring++) {
    const candidates = [];
    for (let i = -ring; i <= ring; i++) {
      candidates.push({ x: x + i * STEP, y: y - ring * STEP });
      candidates.push({ x: x + i * STEP, y: y + ring * STEP });
    }
    for (let j = -ring + 1; j < ring; j++) {
      candidates.push({ x: x - ring * STEP, y: y + j * STEP });
      candidates.push({ x: x + ring * STEP, y: y + j * STEP });
    }
    for (const c of candidates) {
      if (c.x >= 0 && c.y >= 0 && !overlaps(c.x, c.y)) return c;
    }
  }
  return { x: x + Math.random() * 60, y: y + Math.random() * 60 };
}

function onDrop(event) {
  event.preventDefault();
  const type = event.dataTransfer.getData('text/plain');
  if (!type) return;
  const rect = document.getElementById('pc-canvas-container').getBoundingClientRect();
  const t = paper.translate(), s = paper.scale();
  const rawX = Math.round(((event.clientX - rect.left - t.tx) / s.sx) / 10) * 10;
  const rawY = Math.round(((event.clientY - rect.top  - t.ty) / s.sy) / 10) * 10;
  const pos = _findFreePosition(rawX, rawY, 110, 60);
  pushUndo();
  const node = createNode(type, pos.x, pos.y);
  selectCell(node);
  markDirty();
  updateStatus(`Added: ${getStyle(type).label}`);
}

// ── Selection ───────────────────────────────────────────────────────────────

function selectCell(cell) {
  deselectAll();
  selectedCell = cell;
  if (cell && cell.isElement()) {
    cell.attr('body/strokeWidth', 3);
    openConfigPanel(cell);
  }
}

function deselectAll() {
  if (selectedCell && selectedCell.isElement()) {
    selectedCell.attr('body/strokeWidth', 2);
  }
  selectedCell = null;
  closeConfigPanel();
}

function deleteSelected() {
  if (selectedCell) { pushUndo(); selectedCell.remove(); selectedCell = null; markDirty(); }
}

// ── Undo / Redo ─────────────────────────────────────────────────────────────

function graphToJSON() { return graph.toJSON(); }
function loadGraphJSON(data) { graph.fromJSON(data); }

function pushUndo() { undoStack.push(graphToJSON()); redoStack = []; }
function undoAction() {
  if (!undoStack.length) return;
  redoStack.push(graphToJSON());
  loadGraphJSON(undoStack.pop());
  deselectAll();
}
function redoAction() {
  if (!redoStack.length) return;
  undoStack.push(graphToJSON());
  loadGraphJSON(redoStack.pop());
  deselectAll();
}

// ── Save / Load ─────────────────────────────────────────────────────────────

function markDirty() {
  isDirty = true;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(savePipeline, 3000);
  updateStatus('Unsaved changes...');
}

function savePipeline() {
  const graphData = graphToJSON();
  const nodes = graphData.cells.filter(c => c.type !== 'standard.Link').map(c => ({
    id: c.id, label: c.attrs?.label?.text || '', type: c.nodeType || '',
    x: c.position?.x || 0, y: c.position?.y || 0, config: c.configData || {},
  }));
  const edges = graphData.cells.filter(c => c.type === 'standard.Link').map(c => ({
    id: c.id, source: c.source?.id || '', target: c.target?.id || '',
    label: c.labels?.[0]?.attrs?.text?.text || '',
  }));
  const payload = { name: document.getElementById('pc-pipeline-name')?.value || 'Pipeline', graph_json: JSON.stringify({ nodes, edges }) };

  const method = pipelineId === 'new' ? 'POST' : 'PUT';
  const url = pipelineId === 'new' ? '/devops/api/pipelines' : `/devops/api/pipelines/${pipelineId}`;

  fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
    .then(r => r.json())
    .then(data => {
      if (data.id && pipelineId === 'new') { pipelineId = data.id; history.replaceState(null, '', `/devops/canvas/${data.id}`); }
      isDirty = false;
      updateStatus('Saved');
      // Show SDC cross-canvas compliance result if Security Canvas re-assessed
      if (data.sdc_assessment) {
        _showSdcToast(data.sdc_assessment, 'PDC');
      }
    })
    .catch(() => updateStatus('Save failed!'));
}

function _showSdcToast(sdc, source) {
  const cat1 = sdc.cat1_count || 0;
  const grade = sdc.posture_grade || '?';
  const score = sdc.risk_score != null ? sdc.risk_score.toFixed(1) : '—';
  const gradeColor = grade <= 'B' ? '#27ae60' : grade === 'C' ? '#f39c12' : '#e74c3c';
  const cat1Color = cat1 > 0 ? '#e74c3c' : '#27ae60';
  const cat1Text = cat1 > 0
    ? `<span style="color:#e74c3c;font-weight:bold;">&#9888; ${cat1} CAT1</span>`
    : `<span style="color:#27ae60;">&#10003; 0 CAT1</span>`;

  // Remove any existing SDC toast
  const existing = document.getElementById('sdc-cross-toast');
  if (existing) existing.remove();

  const toast = document.createElement('div');
  toast.id = 'sdc-cross-toast';
  toast.style.cssText = [
    'position:fixed', 'bottom:24px', 'right:24px', 'z-index:9999',
    'background:#0f1e35', 'border:1px solid ' + (cat1 > 0 ? '#c0392b' : '#1e3a6e'),
    'border-radius:8px', 'padding:12px 16px', 'min-width:260px',
    'box-shadow:0 4px 16px rgba(0,0,0,0.5)', 'font-family:sans-serif',
    'animation:fadeIn 0.2s ease',
  ].join(';');
  toast.innerHTML = `
    <div style="font-size:11px;color:#7a8cb0;margin-bottom:4px;">SDC Re-assessed (triggered by ${source} save)</div>
    <div style="display:flex;align-items:center;gap:12px;">
      <div style="font-size:2rem;font-weight:bold;color:${gradeColor};">${grade}</div>
      <div>
        <div style="font-size:13px;color:#eaeaea;">Score: ${score}</div>
        <div style="font-size:13px;">${cat1Text}</div>
      </div>
    </div>
    ${cat1 > 0 ? '<div style="margin-top:8px;font-size:11px;color:#e74c3c;">View findings on <a href="/security/posture" style="color:#e74c3c;text-decoration:underline;">Security Posture</a></div>' : ''}
    <button onclick="this.parentElement.remove()" style="position:absolute;top:8px;right:8px;background:none;border:none;color:#7a8cb0;cursor:pointer;font-size:14px;">&#10005;</button>
  `;
  toast.style.position = 'fixed';
  document.body.appendChild(toast);
  setTimeout(() => { if (toast.parentElement) toast.remove(); }, 8000);
}

function _resolveNodeOverlaps(g, padding) {
  padding = padding == null ? 20 : padding;
  const els = g.getElements();
  if (els.length < 2) return;
  for (let iter = 0; iter < 80; iter++) {
    let moved = false;
    for (let i = 0; i < els.length; i++) {
      for (let j = i + 1; j < els.length; j++) {
        const a = els[i], b = els[j];
        const ap = a.position(), as_ = a.size();
        const bp = b.position(), bs = b.size();
        const p2 = padding / 2;
        const ax1 = ap.x - p2, ay1 = ap.y - p2, ax2 = ap.x + as_.width + p2, ay2 = ap.y + as_.height + p2;
        const bx1 = bp.x - p2, by1 = bp.y - p2, bx2 = bp.x + bs.width + p2, by2 = bp.y + bs.height + p2;
        if (ax2 <= bx1 || bx2 <= ax1 || ay2 <= by1 || by2 <= ay1) continue;
        let dx = (bx1 + bx2) / 2 - (ax1 + ax2) / 2;
        let dy = (by1 + by2) / 2 - (ay1 + ay2) / 2;
        if (dx === 0 && dy === 0) { dx = 1; dy = 0; }
        const ovX = (ax2 - ax1) / 2 + (bx2 - bx1) / 2 - Math.abs(dx);
        const ovY = (ay2 - ay1) / 2 + (by2 - by1) / 2 - Math.abs(dy);
        if (ovX <= 0 || ovY <= 0) continue;
        let px = 0, py = 0;
        if (ovX <= ovY) { px = (ovX / 2 + 0.5) * (dx >= 0 ? 1 : -1); }
        else            { py = (ovY / 2 + 0.5) * (dy >= 0 ? 1 : -1); }
        a.position(ap.x - px, ap.y - py);
        b.position(bp.x + px, bp.y + py);
        moved = true;
      }
    }
    if (!moved) break;
  }
}

function loadGraph(graphJson) {
  const data = typeof graphJson === 'string' ? JSON.parse(graphJson) : graphJson;
  // Suppress dirty-flag during programmatic cell creation. Without this,
  // JointJS fires `change add remove` for every loaded cell, which the
  // auto-save handler would interpret as user edits and trigger savePipeline
  // 3s later — blanking description/classification/target_csp on the server
  // (the PUT handler reset them to defaults). See PUT handler for the
  // server-side belt-and-suspenders fix.
  suppressDirty = true;
  try {
    graph.clear();
    // Remap IDs to UUIDs to avoid collisions if the same template is loaded again
    const prefix = 'ld' + Date.now().toString(36) + '-';
    const idMap = {};
    (data.nodes || []).forEach(n => {
      const newId = prefix + (n.id || joint.util.uuid());
      idMap[n.id] = newId;
      createNode(n.type, n.x, n.y, n.label, newId);
    });

    let dropped = 0;
    (data.edges || []).forEach(e => {
      if (!e.source || !e.target || !idMap[e.source] || !idMap[e.target]) {
        dropped++;
        console.warn('loadGraph: dropping edge with missing endpoint', e);
        return;
      }
      createLink(idMap[e.source], idMap[e.target], e.label);
    });
    if (dropped > 0) {
      console.warn('loadGraph: dropped ' + dropped + ' edge(s) referencing missing nodes');
    }
    _resolveNodeOverlaps(graph, 20);
  } finally {
    suppressDirty = false;
  }
}

// ── Zoom ────────────────────────────────────────────────────────────────────

function _updateZoomLabel(s) {
  const el = document.getElementById('pc-zoom-label');
  if (el) el.textContent = Math.round((s !== undefined ? s : paper.scale().sx) * 100) + '%';
}

function _zoomAroundCenter(newScale) {
  const canvasArea = document.querySelector('.pc-canvas-area');
  const s0 = paper.scale().sx;
  const cx = canvasArea.clientWidth  / 2;
  const cy = canvasArea.clientHeight / 2;
  const newScrollX = (canvasArea.scrollLeft + cx) * newScale / s0 - cx;
  const newScrollY = (canvasArea.scrollTop  + cy) * newScale / s0 - cy;
  paper.scale(newScale, newScale);
  canvasArea.scrollLeft = newScrollX;
  canvasArea.scrollTop  = newScrollY;
  _updateZoomLabel(newScale);
}

function zoomIn()   { _zoomAroundCenter(Math.min(4,   paper.scale().sx * 1.2)); }
function zoomOut()  { _zoomAroundCenter(Math.max(0.08, paper.scale().sx / 1.2)); }
function zoomFit() {
  const area = document.querySelector('.pc-canvas-area');
  const w = area ? area.clientWidth  : 1200;
  const h = area ? area.clientHeight : 800;
  paper.scaleContentToFit({
    fittingBBox: { x: 0, y: 0, width: w, height: h },
    padding: 60,
    maxScale: 1,   // never zoom in past 100% — only zoom out to fit
  });
  _updateZoomLabel();
}
function zoomReset(){ paper.scale(1, 1); paper.translate(0, 0); _updateZoomLabel(1); }

// ── Right Panel (Properties / Analysis) ─────────────────────────────────────

function openRightPanel(title, html) {
  const panel = document.querySelector('.pc-config-panel');
  if (!panel) return;
  panel.classList.add('open');
  const header = panel.querySelector('.pc-config-header span');
  if (header) header.textContent = title;
  panel.querySelector('.pc-config-body').innerHTML = html;
}

function openConfigPanel(cell) {
  const type = cell.get('nodeType') || 'unknown';
  const style = getStyle(type);
  const label = cell.attr('label/text') || '';
  let html = `
    <label>Type</label><input value="${escapeAttr(type)}" readonly>
    <label>Label</label><input id="cfg-label" value="${escapeAttr(label)}" onchange="updateNodeLabel(this.value)">
    <label>Icon</label><input value="${escapeAttr(style.symbol)}" readonly>
    <label>Stroke Color</label><div style="width:20px;height:20px;border-radius:4px;background:${escapeAttr(style.stroke)};border:1px solid #1e3a6e;display:inline-block;vertical-align:middle;margin-top:4px;"></div>
    <span style="color:#7a8cb0;margin-left:6px;font-size:11px;">${escapeHtml(style.stroke)}</span>
  `;

  // NDC Bridge: show topology picker
  if (type === 'ndc-topology') {
    html += _section('Link to NDC Topology');
    const linkedId = cell.get('configData')?.ndc_topology_id || '';
    if (linkedId) {
      html += `<div style="padding:4px;background:#0f3460;border-radius:4px;margin:4px 0;font-size:11px;">Linked: <b>${escapeHtml(cell.get('configData')?.ndc_topology_name || linkedId)}</b></div>`;
      html += `<button class="tb-btn" style="margin:4px 0;" onclick="fetchNdcHealth('${encodeURIComponent(linkedId)}')">Refresh Health</button>`;
      html += `<a href="/network/canvas/${encodeURIComponent(linkedId)}" target="_blank" class="tb-btn" style="display:inline-block;text-decoration:none;margin:4px 0;">Open in NDC →</a>`;
      html += '<div id="ndc-health-panel"></div>';
    }
    html += '<div id="ndc-topology-list" style="margin-top:8px;"><i style="color:#7a8cb0;font-size:11px;">Loading topologies...</i></div>';
    openRightPanel('NDC Bridge', html);
    _loadNdcTopologyList(cell);
    if (linkedId) fetchNdcHealth(linkedId);
    return;
  }

  // Hybrid connectivity: show NDC reference note
  if (type.startsWith('hybrid-') || type.startsWith('onprem-') || type === 'ndc-vpc') {
    html += _section('NDC Reference');
    html += '<p style="font-size:11px;color:#7a8cb0;">This object represents infrastructure designed in the Network Design Canvas. For full configuration, open the linked NDC topology.</p>';
    const linkedTopo = _findLinkedNdcTopology();
    if (linkedTopo) {
      html += `<a href="/network/canvas/${linkedTopo}" target="_blank" class="tb-btn" style="display:inline-block;text-decoration:none;margin:4px 0;">Open NDC Topology →</a>`;
    } else {
      html += '<p style="font-size:10px;color:#f39c12;">No NDC topology linked. Add an "NDC Topology" node and link it first.</p>';
    }
  }

  openRightPanel('Properties', html);
}

function _findLinkedNdcTopology() {
  const ndcNodes = graph.getElements().filter(el => el.get('nodeType') === 'ndc-topology');
  for (const node of ndcNodes) {
    const config = node.get('configData') || {};
    if (config.ndc_topology_id) return config.ndc_topology_id;
  }
  return null;
}

function _loadNdcTopologyList(cell) {
  fetch('/network/api/topologies')
    .then(r => r.ok ? r.json() : [])
    .then(topologies => {
      const container = document.getElementById('ndc-topology-list');
      if (!container) return;
      if (!topologies.length) {
        container.innerHTML = '<p style="font-size:11px;color:#7a8cb0;">No NDC topologies found. <a href="/network/canvas/new" target="_blank" style="color:#e94560;">Create one →</a></p>';
        return;
      }
      let html = '<div style="font-size:11px;font-weight:600;margin-bottom:4px;">Available Topologies:</div>';
      topologies.forEach(t => {
        const isLinked = cell.get('configData')?.ndc_topology_id === t.id;
        const updated = t.updated_at ? String(t.updated_at).substring(0, 10) : '';
        // data-* + addEventListener: id/name (incl. cross-canvas NDC data) never
        // reach a JS/HTML-injection sink. escapeAttr guards the attribute values.
        html += `<div class="ndc-topo-row" data-topo-id="${escapeAttr(t.id)}" data-topo-name="${escapeAttr(t.name || '')}" style="background:${isLinked ? '#0f3460' : '#0f2040'};border:1px solid ${isLinked ? '#3498db' : '#1e3a6e'};border-radius:4px;padding:6px 8px;margin:3px 0;cursor:pointer;">`;
        html += `<div style="font-weight:600;font-size:11px;">${escapeHtml(t.name || 'Untitled')}${isLinked ? ' ✓' : ''}</div>`;
        html += `<div style="font-size:10px;color:#7a8cb0;">${escapeHtml(t.classification || 'public')} · ${escapeHtml(updated)}</div>`;
        html += '</div>';
      });
      container.innerHTML = html;
      container.querySelectorAll('.ndc-topo-row').forEach(row => {
        row.addEventListener('click', () => linkNdcTopology(row.dataset.topoId, row.dataset.topoName));
      });
    })
    .catch(() => {
      const container = document.getElementById('ndc-topology-list');
      if (container) container.innerHTML = '<p style="font-size:11px;color:#e74c3c;">NDC not available. Is the Network Canvas enabled?</p>';
    });
}

function linkNdcTopology(topoId, topoName) {
  if (!selectedCell || selectedCell.get('nodeType') !== 'ndc-topology') return;
  pushUndo();
  selectedCell.set('configData', {
    ...(selectedCell.get('configData') || {}),
    ndc_topology_id: topoId,
    ndc_topology_name: topoName,
  });
  selectedCell.attr('label/text', topoName || 'NDC Topology');
  markDirty();
  openConfigPanel(selectedCell); // Refresh panel
  updateStatus('Linked to NDC: ' + topoName);
}

function fetchNdcHealth(topoId) {
  const panel = document.getElementById('ndc-health-panel');
  if (panel) panel.innerHTML = '<i style="color:#7a8cb0;font-size:10px;">Analyzing...</i>';

  fetch('/network/api/topologies/' + topoId + '/cloud-analysis', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
  })
    .then(r => r.ok ? r.json() : null)
    .then(data => {
      if (!panel || !data) return;
      const score = data.cloud_health_score || 0;
      const rating = data.cloud_health_rating || 'N/A';
      const res = data.resiliency || {};
      const patterns = (data.connectivity_patterns?.detected_patterns || []).map(p => p.label).join(', ') || 'None detected';
      const csps = Object.entries(data.csp_breakdown || {}).map(([k, v]) => k.toUpperCase() + ':' + v).join(', ') || 'None';
      const antipatterns = (data.antipatterns || []).length;

      let html = _section('Infrastructure Health');
      html += _metric('Health Score', score + '/100');
      html += _bar(score, score >= 80 ? '#27ae60' : score >= 50 ? '#f39c12' : '#e74c3c');
      html += _metric('Rating', rating);
      html += _metric('Resiliency', res.tier_label || res.tier || 'Unknown');
      html += _metric('SLA', res.sla || 'None');
      html += _metric('Failover', (res.estimated_failover_sec || '?') + 's');
      html += _metric('CSP Breakdown', csps);
      html += _metric('Connectivity', patterns);
      if (antipatterns > 0) html += _metric('Anti-patterns', antipatterns + ' detected', 'Review in NDC');
      if (data.is_multi_cloud) html += _pill('MULTI-CLOUD', '#3498db');
      panel.innerHTML = html;
    })
    .catch(() => {
      if (panel) panel.innerHTML = '<p style="font-size:10px;color:#e74c3c;">Failed to fetch NDC health. Is the topology accessible?</p>';
    });
}

function closeConfigPanel() {
  const panel = document.querySelector('.pc-config-panel');
  if (panel) panel.classList.remove('open');
}

function updateNodeLabel(val) {
  if (selectedCell) { pushUndo(); selectedCell.attr('label/text', val); markDirty(); }
}

// ── Templates & Snippets ────────────────────────────────────────────────────

function openTemplatesModal() { document.getElementById('pc-templates-modal')?.classList.add('open'); }
function closeTemplatesModal(){ document.getElementById('pc-templates-modal')?.classList.remove('open'); }

function loadTemplate(tplId) {
  fetch(`/devops/api/templates/${tplId}/load`, { method: 'POST', headers: { 'Content-Type': 'application/json' } })
    .then(r => r.json())
    .then(data => { if (data.id) { window.location.href = `/devops/canvas/${data.id}`; } });
}

function toggleSnippets() { document.querySelector('.pc-snippet-drawer')?.classList.toggle('open'); }

// ── Classification & Security Levels ────────────────────────────────────────

const CLASSIFICATION_RANK = { 'public': 0, 'IL2': 0, 'unclassified': 0, 'CUI': 1, 'IL4': 1, 'IL5': 2, 'SECRET': 3, 'IL6': 3, 'TOP SECRET': 4 };

function _nodeClassification(el) {
  const type = el.get ? el.get('nodeType') || '' : el.type || '';
  const label = el.get ? (el.attr?.('label/text') || '') : el.label || '';
  const config = el.get ? (el.get('configData') || {}) : el.config || {};
  if (config.classification) return config.classification;
  if (config.network === 'SIPR' || /SIPR|SECRET|IL6/i.test(label)) return 'SECRET';
  if (/JWICS|TOP.SECRET|TS\b/i.test(label)) return 'TOP SECRET';
  if (/CUI|IL4|IL5|govcloud/i.test(label)) return 'CUI';
  if (type.startsWith('pipeline-sipr') || type.startsWith('boundary-secret')) return 'SECRET';
  if (type.startsWith('pipeline-jwics') || type.startsWith('boundary-topsecret')) return 'TOP SECRET';
  if (type.startsWith('boundary-govcloud')) return 'CUI';
  if (type.startsWith('boundary-commercial')) return 'public';
  return null; // Unknown — treat as same-level
}

function _isAirgapped(snippetData) {
  const tags = snippetData.tags || [];
  const il = (snippetData.impact_level || '').toUpperCase();
  const cl = (snippetData.classification_level || '').toUpperCase();
  return tags.includes('airgap') || il === 'IL6' || cl === 'SECRET' || cl === 'TOP SECRET' ||
         (snippetData.graph_json?.nodes || []).some(n => ['pipeline-sipr','pipeline-jwics','cds-data-diode','sneakernet','vuln-db-mirror','package-mirror'].includes(n.type));
}

function _crossDomainViolation(srcClass, tgtClass) {
  const srcRank = CLASSIFICATION_RANK[srcClass] ?? 1;
  const tgtRank = CLASSIFICATION_RANK[tgtClass] ?? 1;
  if (Math.abs(srcRank - tgtRank) >= 2) return { blocked: true, reason: 'Cross-domain: requires CDS Guard or data diode (CNSS Policy 15, SC-7)' };
  if (srcRank >= 3 && tgtRank <= 0) return { blocked: true, reason: 'SECRET/TS cannot connect directly to public (requires CDS)' };
  if (srcRank !== tgtRank && srcRank >= 2) return { blocked: false, reason: 'Different classification levels — encryption required (SC-8, SC-13)' };
  return null;
}

// ── Snippet Type Sets (for auto-connect rules) ─────────────────────────────

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

// ── Smart Snippet Insertion ─────────────────────────────────────────────────

function loadSnippet(snippetId) {
  fetch(`/devops/api/snippets/${snippetId}`)
    .then(r => r.json())
    .then(data => {
      if (!data.graph_json) return;
      pushUndo();
      const g = data.graph_json;
      const snippetNodes = g.nodes || [];
      const snippetEdges = g.edges || [];
      const isAirgap = _isAirgapped(data);
      const snippetClass = data.classification_level || 'CUI';

      // ── Step 1: Collision-free placement (spiral search) ──
      const existingBoxes = graph.getElements().map(el => {
        const p = el.position(), s = el.size();
        return { x: p.x - 20, y: p.y - 20, w: s.width + 40, h: s.height + 40 };
      });

      // Compute snippet bounding box
      const snMinX = Math.min(...snippetNodes.map(n => n.x || 0));
      const snMinY = Math.min(...snippetNodes.map(n => n.y || 0));
      const snMaxX = Math.max(...snippetNodes.map(n => (n.x || 0) + 110));
      const snMaxY = Math.max(...snippetNodes.map(n => (n.y || 0) + 60));
      const snW = snMaxX - snMinX + 60;
      const snH = snMaxY - snMinY + 60;

      // Spiral search for free space
      const bounds = _getCanvasBounds();
      let baseX = bounds.maxX > 0 ? bounds.maxX + 100 : 50;
      let baseY = bounds.minY !== Infinity ? bounds.minY : 50;
      for (let ring = 0; ring < 8; ring++) {
        const offsets = [
          [baseX + ring * 200, baseY],
          [baseX + ring * 200, baseY + snH + 80],
          [baseX + ring * 200, baseY - snH - 80],
        ];
        let found = false;
        for (const [ox, oy] of offsets) {
          const overlaps = existingBoxes.some(b =>
            ox < b.x + b.w && ox + snW > b.x && oy < b.y + b.h && oy + snH > b.y
          );
          if (!overlaps) { baseX = ox; baseY = oy; found = true; break; }
        }
        if (found) break;
      }

      const offsetX = baseX - snMinX;
      const offsetY = baseY - snMinY;

      // ── Step 2: Insert nodes with unique IDs ──
      const prefix = 'sn' + Date.now().toString(36) + '-';
      const idMap = {};
      const newNodes = [];
      snippetNodes.forEach(n => {
        const newId = prefix + (n.id || joint.util.uuid());
        idMap[n.id] = newId;
        const node = createNode(n.type, (n.x || 0) + offsetX, (n.y || 0) + offsetY, n.label, newId);
        newNodes.push({ id: newId, type: n.type, label: n.label, el: node });
      });

      // ── Step 3: Insert edges with remapped IDs ──
      snippetEdges.forEach(e => {
        createLink(idMap[e.source] || e.source, idMap[e.target] || e.target, e.label);
      });

      // ── Step 4: Build integration suggestions ──
      const suggestions = _buildIntegrationSuggestions(newNodes, data, isAirgap, snippetClass);

      // ── Step 5: Show integration guide if there are existing nodes ──
      // Show integration guide if there are existing nodes on canvas
      const existingCount = graph.getElements().filter(el => !newNodes.some(sn => sn.id === el.id) && !el.get('isBoundary')).length;
      if (existingCount > 0) {
        _showIntegrationGuide(data, suggestions, isAirgap, snippetClass);
      }

      markDirty();
      toggleSnippets();
      updateStatus(`Loaded snippet: ${data.name} (${newNodes.length} nodes)`);
    });
}

// ── Auto-Connect Rule Engine ────────────────────────────────────────────────

function _buildIntegrationSuggestions(snippetNodes, snippetData, isAirgap, snippetClass) {
  const existingEls = graph.getElements().filter(el => !el.get('isBoundary') && !snippetNodes.some(sn => sn.id === el.id));
  if (!existingEls.length) return [];

  const suggestions = [];

  snippetNodes.forEach(sn => {
    const snType = sn.type;
    const snClass = snippetClass;

    existingEls.forEach(existing => {
      const exType = existing.get('nodeType') || '';
      const exLabel = existing.attr('label/text') || '';
      const exClass = _nodeClassification(existing) || 'CUI';

      // Classification check
      const violation = _crossDomainViolation(snClass, exClass);
      if (violation && violation.blocked) return; // Hard block

      // Air-gap check: snippet is air-gapped but existing node is cloud-managed
      if (isAirgap && PIPELINE_TYPE_SETS.CLOUD_MANAGED.has(exType)) return; // No cloud connections for air-gapped

      // ── Rule 1: SCM → CI engine (source triggers build)
      if (PIPELINE_TYPE_SETS.SCM.has(snType) && PIPELINE_TYPE_SETS.CI_ENGINE.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'push trigger', reason: 'Source control triggers CI pipeline', priority: 1, protocol: 'webhook' });
      }
      if (PIPELINE_TYPE_SETS.CI_ENGINE.has(snType) && PIPELINE_TYPE_SETS.SCM.has(exType)) {
        suggestions.push({ from: existing, to: sn, label: 'push trigger', reason: 'Source control triggers CI pipeline', priority: 1, protocol: 'webhook' });
      }

      // ── Rule 2: CI engine → Scanner (build triggers scan)
      if (PIPELINE_TYPE_SETS.CI_ENGINE.has(snType) && PIPELINE_TYPE_SETS.SCANNER.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'scan', reason: 'CI triggers security scanning', priority: 2, protocol: 'CI step' });
      }

      // ── Rule 3: CI/Scanner → Registry (push artifacts)
      if ((PIPELINE_TYPE_SETS.CI_ENGINE.has(snType) || PIPELINE_TYPE_SETS.SCANNER.has(snType)) && PIPELINE_TYPE_SETS.REGISTRY.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'push image', reason: 'Push built/scanned artifacts to registry', priority: 3, protocol: 'OCI/Docker' });
      }
      if (PIPELINE_TYPE_SETS.REGISTRY.has(snType) && PIPELINE_TYPE_SETS.CI_ENGINE.has(exType)) {
        suggestions.push({ from: existing, to: sn, label: 'push image', reason: 'Push built artifacts to registry', priority: 3, protocol: 'OCI/Docker' });
      }

      // ── Rule 4: Registry → K8s deploy (deploy from registry)
      if (PIPELINE_TYPE_SETS.REGISTRY.has(snType) && PIPELINE_TYPE_SETS.K8S.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'deploy', reason: 'Deploy images from registry to cluster', priority: 4, protocol: 'kubectl/ArgoCD' });
      }
      if (PIPELINE_TYPE_SETS.K8S.has(snType) && PIPELINE_TYPE_SETS.REGISTRY.has(exType)) {
        suggestions.push({ from: existing, to: sn, label: 'pull image', reason: 'Cluster pulls images from registry', priority: 4, protocol: 'OCI pull' });
      }

      // ── Rule 5: Signer → Registry (sign artifacts in registry)
      if (PIPELINE_TYPE_SETS.SIGNER.has(snType) && PIPELINE_TYPE_SETS.REGISTRY.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'sign', reason: 'Sign artifacts in registry', priority: 3, protocol: 'Cosign/Notation' });
      }

      // ── Rule 6: Policy engine → K8s (admission control)
      if (PIPELINE_TYPE_SETS.POLICY.has(snType) && PIPELINE_TYPE_SETS.K8S.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'admission', reason: 'Policy admission control on cluster', priority: 4, protocol: 'webhook' });
      }

      // ── Rule 7: Monitor → K8s (observe cluster)
      if (PIPELINE_TYPE_SETS.MONITOR.has(snType) && PIPELINE_TYPE_SETS.K8S.has(exType)) {
        suggestions.push({ from: existing, to: sn, label: 'metrics/logs', reason: 'Monitor cluster health and security', priority: 5, protocol: 'Prometheus/syslog' });
      }
      if (PIPELINE_TYPE_SETS.K8S.has(snType) && PIPELINE_TYPE_SETS.MONITOR.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'metrics/logs', reason: 'Cluster sends telemetry to monitoring', priority: 5, protocol: 'Prometheus/syslog' });
      }

      // ── Rule 8: Air-gap infra → scanner (offline DB feeds scanner)
      if (snType === 'vuln-db-mirror' && PIPELINE_TYPE_SETS.SCANNER.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'offline DB', reason: 'Offline vulnerability database for air-gapped scanning', priority: 2, protocol: 'file' });
      }
      if (snType === 'package-mirror' && PIPELINE_TYPE_SETS.CI_ENGINE.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'dependencies', reason: 'Package mirror provides dependencies in air-gap', priority: 2, protocol: 'HTTP' });
      }

      // ── Rule 9: Monitor → SLO (metrics feed SLOs)
      if (PIPELINE_TYPE_SETS.MONITOR.has(snType) && PIPELINE_TYPE_SETS.SRE_SLO.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'SLI metrics', reason: 'Monitoring feeds SLI metrics into SLO tracking', priority: 3, protocol: 'Prometheus/OTel' });
      }
      if (PIPELINE_TYPE_SETS.SRE_SLO.has(snType) && PIPELINE_TYPE_SETS.MONITOR.has(exType)) {
        suggestions.push({ from: existing, to: sn, label: 'SLI metrics', reason: 'Monitoring feeds SLI metrics into SLO tracking', priority: 3, protocol: 'Prometheus/OTel' });
      }

      // ── Rule 10: SLO → Incident (error budget breach triggers incident)
      if (PIPELINE_TYPE_SETS.SRE_SLO.has(snType) && PIPELINE_TYPE_SETS.SRE_INCIDENT.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'budget breach', reason: 'Error budget exhaustion triggers incident creation', priority: 3, protocol: 'webhook/alert' });
      }

      // ── Rule 11: Incident → Runbook (incident triggers automated response)
      if (PIPELINE_TYPE_SETS.SRE_INCIDENT.has(snType) && PIPELINE_TYPE_SETS.SRE_RUNBOOK.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'auto-respond', reason: 'Incident triggers automated runbook execution', priority: 3, protocol: 'webhook' });
      }

      // ── Rule 12: Chaos → K8s (chaos experiments target cluster)
      if (PIPELINE_TYPE_SETS.SRE_CHAOS.has(snType) && PIPELINE_TYPE_SETS.K8S.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'fault inject', reason: 'Chaos experiment injects faults into cluster', priority: 4, protocol: 'K8s API' });
      }

      // ── Rule 13: K8s → SRE resilience (cluster feeds resilience scoring)
      if (PIPELINE_TYPE_SETS.K8S.has(snType) && PIPELINE_TYPE_SETS.SRE_RESILIENCE.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'resilience data', reason: 'Cluster health data feeds resilience scoring', priority: 5, protocol: 'metrics' });
      }

      // ── Rule 14: CI → DORA (pipeline events feed DORA metrics)
      if (PIPELINE_TYPE_SETS.CI_ENGINE.has(snType) && PIPELINE_TYPE_SETS.SRE_DORA.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'pipeline events', reason: 'CI/CD pipeline events feed DORA metric calculation', priority: 4, protocol: 'webhook/API' });
      }

      // ── Rule 15: NDC topology → K8s (infrastructure hosts cluster)
      if (snType === 'ndc-topology' && PIPELINE_TYPE_SETS.K8S.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'hosts', reason: 'NDC topology provides network infrastructure for K8s cluster', priority: 1, protocol: 'VPC/VNet' });
      }
      if (PIPELINE_TYPE_SETS.K8S.has(snType) && exType === 'ndc-topology') {
        suggestions.push({ from: existing, to: sn, label: 'hosts', reason: 'NDC topology provides network infrastructure for K8s cluster', priority: 1, protocol: 'VPC/VNet' });
      }

      // ── Rule 10: Hybrid connectivity → on-prem or cloud K8s
      if (PIPELINE_TYPE_SETS.HYBRID_CONNECT.has(snType) && PIPELINE_TYPE_SETS.K8S.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'connectivity', reason: 'Hybrid link provides network path to cluster', priority: 2, protocol: snType.includes('vpn') ? 'IPSec' : 'dedicated circuit' });
      }
      if (PIPELINE_TYPE_SETS.HYBRID_CONNECT.has(snType) && PIPELINE_TYPE_SETS.ONPREM.has(exType)) {
        suggestions.push({ from: sn, to: existing, label: 'connectivity', reason: 'Hybrid link connects to on-prem infrastructure', priority: 2, protocol: snType.includes('vpn') ? 'IPSec' : 'dedicated circuit' });
      }

      // ── Rule 11: On-prem DC → K8s/registry (on-prem hosts services)
      if (PIPELINE_TYPE_SETS.ONPREM.has(snType) && (PIPELINE_TYPE_SETS.K8S.has(exType) || PIPELINE_TYPE_SETS.REGISTRY.has(exType))) {
        suggestions.push({ from: sn, to: existing, label: 'hosts', reason: 'On-premises infrastructure hosts this service', priority: 3, protocol: 'LAN' });
      }

      // Add encryption warning for cross-classification
      if (violation && !violation.blocked) {
        const last = suggestions[suggestions.length - 1];
        if (last) last.warning = violation.reason;
      }
    });
  });

  // Deduplicate and sort by priority
  const seen = new Set();
  return suggestions
    .filter(s => {
      const key = (s.from.id || s.from.el?.id) + '->' + (s.to.id || s.to.get?.('id'));
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .sort((a, b) => a.priority - b.priority)
    .slice(0, 12);
}

// ── Integration Guide Overlay ───────────────────────────────────────────────

function _showIntegrationGuide(snippetData, suggestions, isAirgap, snippetClass) {
  const clsBadge = snippetClass === 'SECRET' ? '#e74c3c' : snippetClass === 'CUI' ? '#f39c12' : '#27ae60';
  let html = _section('Snippet: ' + escapeHtml(snippetData.name));
  html += '<div style="margin-bottom:8px;">';
  html += _pill(snippetClass, clsBadge);
  html += _pill(snippetData.impact_level || 'IL4', '#0f3460');
  html += _pill('SLSA ' + (snippetData.slsa_level || 'L0'), '#5b6abf');
  if (isAirgap) html += _pill('AIR-GAPPED', '#c0392b');
  html += '</div>';
  if (snippetData.description) {
    html += '<p style="font-size:11px;color:#7a8cb0;margin:0 0 8px;">' + escapeHtml(snippetData.description) + '</p>';
  }

  // Security warnings
  if (isAirgap) {
    html += '<div style="background:#2b0f0f;border-left:3px solid #e74c3c;padding:6px 10px;border-radius:0 4px 4px 0;margin-bottom:8px;font-size:11px;">';
    html += '<b style="color:#e74c3c;">Air-Gapped Mode</b><br>';
    html += 'Cloud-managed services are excluded from auto-connect. Only on-premises and self-hosted tools are suggested.';
    html += '</div>';
  }

  // Suggestions
  if (suggestions.length) {
    html += _section('Suggested Connections (' + suggestions.length + ')');
    suggestions.forEach((s, idx) => {
      const fromLabel = s.from.label || s.from.attr?.('label/text') || '?';
      const toLabel = s.to.label || s.to.attr?.('label/text') || '?';
      const fromId = s.from.id || s.from.el?.id;
      const toId = s.to.id || s.to.get?.('id');

      html += '<div style="background:#0f2040;border:1px solid #1e3a6e;border-radius:6px;padding:8px;margin:4px 0;">';
      html += '<div style="display:flex;justify-content:space-between;align-items:center;">';
      html += '<div><span style="color:#3498db;font-weight:600;">' + escapeHtml(fromLabel) + '</span>';
      html += ' <span style="color:#e94560;">→</span> ';
      html += '<span style="color:#27ae60;font-weight:600;">' + escapeHtml(toLabel) + '</span></div>';
      // data-* + addEventListener: node ids/label never enter a JS string sink.
      html += '<button class="tb-btn integ-connect-btn" style="font-size:10px;padding:2px 8px;" data-from="' + escapeAttr(fromId) + '" data-to="' + escapeAttr(toId) + '" data-label="' + escapeAttr(s.label || '') + '">Connect</button>';
      html += '</div>';
      html += '<div style="font-size:10px;color:#7a8cb0;margin-top:3px;">' + s.reason + '</div>';
      html += '<div style="font-size:9px;color:#5a6e8c;">Protocol: ' + (s.protocol || 'auto') + '</div>';
      if (s.warning) {
        html += '<div style="font-size:9px;color:#f39c12;margin-top:2px;">⚠ ' + s.warning + '</div>';
      }
      html += '</div>';
    });
    html += '<button class="tb-btn" style="margin-top:8px;width:100%;text-align:center;" onclick="applyAllSuggestions()">Connect All Suggested</button>';
  } else {
    html += '<p style="font-size:11px;color:#7a8cb0;">No automatic connections suggested. Drag links manually to connect nodes.</p>';
  }

  openRightPanel('Integration Guide', html);
  // Wire Connect buttons via listeners (data-* args, no inline JS-string sink).
  document.querySelectorAll('.pc-config-body .integ-connect-btn').forEach(btn => {
    btn.addEventListener('click', e => applyIntegrationLink(
      btn.dataset.from, btn.dataset.to, btn.dataset.label, e.currentTarget));
  });
  // Store suggestions for "Connect All"
  window._pendingSuggestions = suggestions;
}

function applyIntegrationLink(fromId, toId, label, el) {
  pushUndo();
  createLink(fromId, toId, label);
  markDirty();
  // Visual feedback: flash the button
  const btn = el || (typeof event !== 'undefined' ? event?.target : null);
  if (btn) { if (btn.style) btn.style.background = '#27ae60'; btn.textContent = '✓'; }
}

function applyAllSuggestions() {
  const suggestions = window._pendingSuggestions || [];
  if (!suggestions.length) return;
  pushUndo();
  suggestions.forEach(s => {
    const fromId = s.from.id || s.from.el?.id;
    const toId = s.to.id || s.to.get?.('id');
    createLink(fromId, toId, s.label || '');
  });
  markDirty();
  updateStatus('Applied ' + suggestions.length + ' connections');
  window._pendingSuggestions = [];
}

// ── Validate IaC ─────────────────────────────────────────────────────────────

function validateIaC() {
  if (pipelineId === 'new') { alert('Save pipeline first'); return; }
  updateStatus('Validating IaC (Layers 1-3)...');
  fetch(`/devops/api/validate/${pipelineId}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ max_layer: 3 }),
  })
    .then(r => r.json())
    .then(data => {
      if (data.error) { alert(data.error); return; }
      window._lastValidationData = data;
      _renderValidation(data);
      updateStatus('Validation: ' + (data.validation || {}).gate);
    })
    .catch(err => { updateStatus('Validation failed'); alert(err); });
}

function _renderValidation(data) {
  const v = data.validation || {};
  const s = v.summary || {};
  let html = _section('IaC Validation Results');
  const gateColor = v.gate === 'pass' ? '#1e8449' : '#c0392b';
  html += `<div style="text-align:center;margin:8px 0;">
    <span style="font-size:28px;font-weight:800;color:${gateColor};">${(v.gate || '?').toUpperCase()}</span>
    <div style="font-size:11px;color:#4a5568;">Gate: ${s.passed||0} pass, ${s.failed||0} fail, ${s.warned||0} warn</div>
  </div>`;
  html += _bar(s.total > 0 ? (s.passed / s.total * 100) : 0, gateColor);

  // Auto-fix banner
  const fixable = (v.results || []).filter(r => (r.status === 'warn' || r.status === 'fail') && r.fix_hint);
  if (fixable.length > 0) {
    html += `<div style="margin:8px 0;padding:7px 10px;background:#fef9e7;border:1px solid #f39c12;border-radius:6px;display:flex;align-items:center;justify-content:space-between;">
      <span style="font-size:11px;font-weight:600;color:#7d6608;">⚠ ${fixable.length} issue(s) have suggested fix${fixable.length > 1 ? 'es' : ''}</span>
      <button class="tb-btn" style="background:#d5f0e0;color:#1e7e34;border-color:#1e7e34;font-size:11px;padding:2px 8px;font-weight:600;" onclick="autoFixAllWarnings()">✦ Auto-fix All</button>
    </div>`;
  }

  const layers = {1: 'Syntax', 2: 'Schema', 3: 'Policy', 4: 'Plan', 5: 'Deploy Test'};
  for (let layer = 1; layer <= (v.layers_run || 3); layer++) {
    const layerResults = (v.results || []).filter(r => r.layer === layer);
    if (!layerResults.length) continue;
    html += _section('Layer ' + layer + ': ' + (layers[layer] || ''));
    layerResults.forEach((r, idx) => {
      const icons  = {pass:'✓', fail:'✗', warn:'⚠', skip:'→'};
      const colors = {pass:'#1e8449', fail:'#c0392b', warn:'#b7770d', skip:'#4a5568'};
      const bgs    = {pass:'#f0fff4', fail:'#fff5f5', warn:'#fffbf0', skip:'#f8f8f8'};
      const rid = `vr-${layer}-${idx}`;
      const needsAction = r.status === 'warn' || r.status === 'fail';

      html += `<div id="${rid}" style="margin:3px 0;padding:6px 8px;border-left:3px solid ${colors[r.status]};background:${bgs[r.status]};border-radius:0 4px 4px 0;">`;
      html += `<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:4px;">`;
      html += `<div style="flex:1;min-width:0;">
        <span style="color:${colors[r.status]};font-size:13px;margin-right:4px;">${icons[r.status]}</span>
        <b style="font-size:11px;color:#1a1a2e;">${escapeHtml(r.check)}</b>`;
      if (r.file) html += ` <span style="color:#4a5568;font-size:10px;">(${escapeHtml(r.file)})</span>`;
      html += `<div style="font-size:10px;color:#4a5568;margin-top:2px;">${escapeHtml(r.message)}</div>`;
      if (r.details && r.details.length) {
        r.details.forEach(d => { html += `<div style="font-size:9px;color:#4a5568;margin-left:12px;">— ${escapeHtml(d)}</div>`; });
      }
      html += `</div>`;
      if (needsAction) {
        html += `<div style="display:flex;flex-direction:column;gap:3px;flex-shrink:0;">`;
        if (r.fix_hint) html += `<button class="tb-btn" style="font-size:10px;padding:2px 7px;background:#dce8fb;color:#1a5276;border-color:#aed6f1;" onclick="_toggleFix('${rid}')">Fix →</button>`;
        html += `<button class="tb-btn" style="font-size:10px;padding:2px 7px;color:#4a5568;" onclick="_dismissItem('${rid}')">Dismiss</button>`;
        html += `</div>`;
      }
      html += `</div>`;

      // Fix card (hidden by default)
      if (r.fix_hint) {
        const safeSnippet = (r.fix_snippet || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        html += `<div id="${rid}-fix" style="display:none;margin-top:6px;padding:8px;background:#eaf3fb;border-radius:4px;border:1px solid #aed6f1;">`;
        html += `<div style="font-size:10px;font-weight:700;color:#1a5276;margin-bottom:4px;">💡 Suggested Fix</div>`;
        html += `<div style="font-size:10px;color:#2c3e50;margin-bottom:6px;">${escapeHtml(r.fix_hint)}</div>`;
        if (r.fix_snippet) {
          html += `<pre style="font-size:9px;background:#d6eaf8;padding:6px;border-radius:3px;white-space:pre-wrap;color:#1a1a2e;margin:0 0 6px;font-family:var(--pc-mono);">${safeSnippet}</pre>`;
          html += `<button class="tb-btn" style="font-size:10px;padding:2px 8px;background:#1a5276;color:#fff;" onclick="_copyFix(this,'${rid}')">Copy Snippet</button>`;
        }
        html += `</div>`;
      }
      html += `</div>`;
    });
  }
  html += `<p style="font-size:10px;color:#4a5568;margin-top:8px;">Layers 1-3 run offline (air-gap safe). Layer 4+ requires terraform binary or cloud credentials.</p>`;
  openRightPanel('IaC Validation', html);
}

window._toggleFix = function(rid) {
  const el = document.getElementById(rid + '-fix');
  if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
};

window._dismissItem = function(rid) {
  const el = document.getElementById(rid);
  if (el) { el.style.opacity = '0.4'; el.style.pointerEvents = 'none'; }
};

window._copyFix = function(btn, rid) {
  const fixDiv = document.getElementById(rid + '-fix');
  if (!fixDiv) return;
  const pre = fixDiv.querySelector('pre');
  if (!pre) return;
  navigator.clipboard.writeText(pre.textContent).then(() => {
    const orig = btn.textContent;
    btn.textContent = '✓ Copied!';
    setTimeout(() => { btn.textContent = orig; }, 1500);
  }).catch(() => {
    // Fallback for browsers that block clipboard
    const ta = document.createElement('textarea');
    ta.value = pre.textContent;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    btn.textContent = '✓ Copied!';
    setTimeout(() => { btn.textContent = 'Copy Snippet'; }, 1500);
  });
};

window.autoFixAllWarnings = function() {
  const data = window._lastValidationData;
  if (!data) return;
  const v = data.validation || {};
  const fixable = (v.results || []).filter(r => (r.status === 'warn' || r.status === 'fail') && r.fix_hint);
  if (!fixable.length) return;
  updateStatus(`Applying ${fixable.length} fix(es)...`);
  fetch(`/devops/api/validate/${pipelineId}/fix`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fixes: fixable.map(r => ({ check: r.check, file: r.file, fix_action: r.fix_action || r.check })) }),
  })
    .then(r => r.json())
    .then(result => {
      if (result.error) { alert(result.error); return; }
      const n = result.fixed || 0;
      updateStatus(`Applied ${n} fix(es) — re-validating...`);
      window._lastValidationData = result;
      _renderValidation(result);
      updateStatus('Validation: ' + (result.validation || {}).gate);
    })
    .catch(err => { updateStatus('Auto-fix failed'); console.error(err); });
};

// ── Deploy IaC Bundle ────────────────────────────────────────────────────────

function deployBundle() {
  if (pipelineId === 'new') { alert('Save pipeline first'); return; }
  updateStatus('Generating deployment bundle...');
  fetch(`/devops/api/deploy/${pipelineId}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target_csp: 'auto' }),
  })
    .then(r => r.json())
    .then(data => {
      if (data.error) { alert(data.error); return; }
      // Show summary in right panel
      let html = _section('Deployment Summary');
      html += '<pre style="font-size:10px;white-space:pre-wrap;color:#eaeaea;background:#0f2040;padding:8px;border-radius:4px;">' + (data.summary || '') + '</pre>';
      html += _section('Generated Files (' + (data.files || []).length + ')');
      (data.files || []).forEach(f => {
        html += '<div style="font-size:10px;color:#7a8cb0;padding:1px 0;">' + f + '</div>';
      });
      html += _section('Download');
      html += '<button class="tb-btn" style="background:#27ae60;color:#fff;padding:6px 16px;margin:8px 0;" onclick="downloadBundle()">Download Bundle (.zip)</button>';
      html += '<p style="font-size:10px;color:#5a6e8c;margin-top:4px;">Contains Terraform, Helm values, Ansible playbooks, CI config, and deploy.sh</p>';
      openRightPanel('Deploy IaC Bundle', html);
      updateStatus('Bundle generated');
      // Store for download
      window._lastDeployBundle = data;
    })
    .catch(err => { updateStatus('Deploy generation failed'); alert(err); });
}

function downloadBundle() {
  if (pipelineId === 'new') { alert('Save pipeline first'); return; }
  updateStatus('Downloading zip...');
  fetch(`/devops/api/deploy/${pipelineId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target_csp: 'auto', format: 'zip' }),
  })
    .then(r => {
      if (!r.ok) throw new Error('Download failed');
      return r.blob();
    })
    .then(blob => {
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'devops-deploy-bundle.zip';
      a.click();
      URL.revokeObjectURL(a.href);
      updateStatus('Bundle downloaded');
    })
    .catch(err => { updateStatus('Download failed'); alert(err); });
}

// ── Export ───────────────────────────────────────────────────────────────────

function exportAs(fmt) {
  if (pipelineId === 'new') { alert('Save pipeline first'); return; }
  fetch(`/devops/api/export/${pipelineId}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ format: fmt }),
  })
    .then(r => r.json())
    .then(data => {
      if (data.error) { alert(data.error); return; }
      const blob = new Blob([data.content], { type: 'text/plain' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = data.filename;
      a.click();
      updateStatus(`Exported: ${data.filename}`);
    });
}

// ── Analysis (renders in right panel with narrative) ────────────────────────

function runAnalysis(type) {
  if (pipelineId === 'new') { alert('Save pipeline first'); return; }
  updateStatus('Analyzing...');
  fetch(`/devops/api/pipelines/${pipelineId}/analyze`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ analysis_type: type }),
  })
    .then(r => r.json())
    .then(data => {
      const r = data.result || {};
      let html = '';
      if (type === 'security_coverage') html = _renderOWASP(r);
      else if (type === 'slsa') html = _renderSLSA(r);
      else if (type === 'compliance') html = _renderCompliance(r);
      else if (type === 'cost') html = _renderCost(r);
      else if (type === 'execution_time') html = _renderExecTime(r);
      else if (type === 'antipatterns') html = _renderAntipatterns(r);
      else if (type === 'governance') html = _renderPDCGovernance(r);
      else html = '<pre style="font-size:11px;white-space:pre-wrap;">' + JSON.stringify(r, null, 2) + '</pre>';
      const titles = {
        security_coverage: 'OWASP Top 10 Coverage',
        slsa: 'SLSA Level Assessment',
        compliance: 'Compliance Audit',
        cost: 'Cost Estimate',
        execution_time: 'Execution Time',
        antipatterns: 'Anti-Pattern Detection',
        governance: 'Pipeline Governance',
      };
      openRightPanel(titles[type] || type, html);
      updateStatus('Analysis complete');
    })
    .catch(() => updateStatus('Analysis failed'));
}

function runScorecard() {
  if (pipelineId === 'new') { alert('Save pipeline first'); return; }
  updateStatus('Running scorecard...');
  fetch(`/devops/api/pipelines/${pipelineId}/scorecard`)
    .then(r => r.json())
    .then(data => {
      openRightPanel('Pipeline Scorecard', _renderScorecard(data));
      updateStatus('Scorecard complete');
    })
    .catch(() => updateStatus('Scorecard failed'));
}

// ── Analysis Renderers ──────────────────────────────────────────────────────

function _pill(text, color) {
  return `<span style="display:inline-block;padding:1px 8px;border-radius:10px;font-size:10px;font-weight:600;background:${color};color:#fff;margin:1px 2px;">${text}</span>`;
}
function _bar(pct, color) {
  return `<div style="background:#0f2040;border-radius:4px;height:8px;margin:4px 0 8px;overflow:hidden;"><div style="width:${pct}%;height:100%;background:${color};border-radius:4px;"></div></div>`;
}
function _metric(label, value, sub) {
  return `<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.04);"><span style="color:#7a8cb0;">${label}</span><span style="font-weight:600;font-family:var(--pc-mono);">${value}</span></div>` + (sub ? `<div style="font-size:10px;color:#5a6e8c;padding:0 0 4px;">${sub}</div>` : '');
}
function _section(title) {
  return `<div style="font-weight:700;font-size:12px;margin:12px 0 6px;padding-bottom:4px;border-bottom:1px solid #1e3a6e;color:#eaeaea;">${title}</div>`;
}

function _renderOWASP(r) {
  const pct = r.coverage_pct || 0;
  const color = pct >= 80 ? '#27ae60' : pct >= 50 ? '#f39c12' : '#e74c3c';
  let html = _metric('Coverage', pct + '%', `${r.covered_count || 0} of ${r.total_categories || 10} OWASP categories covered`);
  html += _bar(pct, color);

  // Narrative
  if (pct >= 90) html += '<p style="font-size:11px;color:#27ae60;">Excellent coverage. Your pipeline addresses nearly all OWASP Top 10 categories. Consider periodic pen testing to validate runtime behavior.</p>';
  else if (pct >= 70) html += '<p style="font-size:11px;color:#f39c12;">Good coverage with gaps. Review the uncovered categories below and consider adding targeted scanners.</p>';
  else if (pct >= 40) html += '<p style="font-size:11px;color:#e74c3c;">Moderate coverage. Significant gaps exist. Your pipeline is vulnerable to multiple OWASP Top 10 attack classes.</p>';
  else html += '<p style="font-size:11px;color:#e74c3c;">Critical: Minimal security scanning. Most OWASP Top 10 categories are unaddressed. Add SAST, SCA, and DAST scanners immediately.</p>';

  html += _section('Covered Categories');
  (r.covered || []).forEach(c => { html += _pill(c, '#27ae60'); });

  if ((r.uncovered || []).length) {
    html += _section('Uncovered (Gaps)');
    (r.uncovered || []).forEach(c => { html += _pill(c, '#e74c3c'); });
    html += '<div style="margin-top:8px;font-size:11px;color:#7a8cb0;">';
    const gapAdvice = {
      'A01-Broken-Access': 'Add DAST scanner (OWASP ZAP) to detect access control flaws at runtime.',
      'A02-Crypto-Failures': 'Add secret detection (Gitleaks, TruffleHog) to catch exposed credentials.',
      'A03-Injection': 'Add SAST scanner (Semgrep, SonarQube) for injection detection.',
      'A04-Insecure-Design': 'Add SAST with design pattern rules (CodeQL, SonarQube).',
      'A05-Misconfig': 'Add IaC scanner (Checkov, tfsec) for infrastructure misconfiguration.',
      'A06-Vuln-Components': 'Add SCA scanner (Trivy, Grype, Snyk) for dependency vulnerabilities.',
      'A07-XSS': 'Add SAST + DAST scanners for cross-site scripting detection.',
      'A08-Deserialization': 'Add SAST scanner with deserialization rules.',
      'A09-Logging-Monitoring': 'Add runtime security (Falco, Wazuh) for logging/monitoring gaps.',
      'A10-SSRF': 'Add DAST scanner and runtime monitoring for SSRF detection.',
    };
    (r.uncovered || []).forEach(c => {
      if (gapAdvice[c]) html += '<div style="margin:4px 0;"><b style="color:#e94560;">' + c + ':</b> ' + gapAdvice[c] + '</div>';
    });
    html += '</div>';
  }

  // Scanner detail
  if (r.scanner_coverage && Object.keys(r.scanner_coverage).length) {
    html += _section('Scanner Contribution');
    for (const [scanner, cats] of Object.entries(r.scanner_coverage)) {
      const style = getStyle(scanner);
      html += `<div style="margin:3px 0;"><span style="color:${style.stroke};font-weight:600;">${style.label}</span>: ${cats.map(c => _pill(c, '#0f3460')).join('')}</div>`;
    }
  }
  return html;
}

function _renderSLSA(r) {
  const level = r.achieved_level || 0;
  const colors = ['#e74c3c', '#f39c12', '#f1c40f', '#27ae60', '#16a085'];
  const color = colors[Math.min(level, 4)];
  let html = `<div style="text-align:center;margin:8px 0;"><span style="font-size:36px;font-weight:800;color:${color};">L${level}</span></div>`;
  html += _bar(level * 25, color);

  const descs = [
    'No supply chain guarantees. Artifacts have no provenance or signing.',
    'Build process is documented. Provenance exists but is not tamper-resistant.',
    'Tamper-resistant build service. Source is version-controlled, build is authenticated.',
    'Hardened builds. Build-as-code, ephemeral environments, isolated execution.',
    'Highest assurance. Hermetic and reproducible builds with full attestation chain.',
  ];
  html += `<p style="font-size:11px;color:#7a8cb0;margin:8px 0;">${descs[level]}</p>`;

  html += _section('Evidence Checklist');
  const evidence = r.evidence || {};
  const evidenceLabels = {
    build_process_documented: 'Build process documented',
    version_controlled_source: 'Source in version control',
    build_service_authenticated: 'Authenticated build service',
    build_as_code: 'Build defined as code',
    ephemeral_environment: 'Ephemeral build environment',
    isolated_builds: 'Isolated build execution',
    hermetic_builds: 'Hermetic builds (no network)',
    reproducible_builds: 'Reproducible builds',
  };
  for (const [key, label] of Object.entries(evidenceLabels)) {
    const met = evidence[key] || false;
    html += `<div style="display:flex;align-items:center;gap:6px;padding:2px 0;"><span style="color:${met ? '#27ae60' : '#e74c3c'};font-size:14px;">${met ? '&#10003;' : '&#10007;'}</span><span style="color:${met ? '#eaeaea' : '#7a8cb0'};">${label}</span></div>`;
  }

  html += _section('Attestation');
  html += _metric('Provenance', r.has_provenance ? 'Yes' : 'Missing', r.has_provenance ? 'SLSA provenance attestation found in pipeline' : 'Add SLSA Generator or in-toto node');
  html += _metric('Signing', r.has_signing ? 'Yes' : 'Missing', r.has_signing ? 'Artifact signing configured' : 'Add Cosign or Notation node');

  if (level < 3) {
    html += _section('Recommendations to Reach L' + (level + 1));
    if (level === 0) html += '<p style="font-size:11px;">Add a CI/CD engine (GitLab CI, Tekton) and an SLSA provenance generator.</p>';
    else if (level === 1) html += '<p style="font-size:11px;">Use an authenticated build service and add artifact signing (Cosign).</p>';
    else if (level === 2) html += '<p style="font-size:11px;">Switch to Tekton or Bazel for hermetic, isolated builds with ephemeral runners.</p>';
  }
  return html;
}

function _renderCompliance(r) {
  const total = (r.passed || 0) + (r.failed || 0);
  const pct = total > 0 ? Math.round(r.passed / total * 100) : 0;
  const color = pct >= 80 ? '#27ae60' : pct >= 50 ? '#f39c12' : '#e74c3c';
  let html = _metric('Score', pct + '%', `${r.passed || 0} passed, ${r.failed || 0} failed of ${total} rules`);
  html += _bar(pct, color);

  if (pct >= 90) html += '<p style="font-size:11px;color:#27ae60;">Strong compliance posture. Pipeline meets most DevSecOps framework requirements.</p>';
  else if (pct >= 60) html += '<p style="font-size:11px;color:#f39c12;">Moderate compliance. Several controls are missing. Review findings below.</p>';
  else html += '<p style="font-size:11px;color:#e74c3c;">Significant compliance gaps. Pipeline does not meet baseline DevSecOps requirements. Remediation required before ATO.</p>';

  if ((r.findings || []).length) {
    html += _section('Findings (' + r.findings.length + ')');
    // Group by severity
    const cat1 = r.findings.filter(f => f.severity === 'CAT1');
    const cat2 = r.findings.filter(f => f.severity === 'CAT2');

    if (cat1.length) {
      html += `<div style="color:#e74c3c;font-weight:700;margin:6px 0 2px;">CAT I — Critical (${cat1.length})</div>`;
      cat1.forEach(f => {
        html += `<div style="margin:4px 0;padding:6px 8px;background:#2b0f0f;border-left:3px solid #e74c3c;border-radius:0 4px 4px 0;">`;
        html += `<div style="font-weight:600;font-size:11px;">${f.rule_id}: ${f.title}</div>`;
        html += `<div style="font-size:10px;color:#7a8cb0;margin-top:2px;">${(f.frameworks || []).join(', ')}</div>`;
        html += '</div>';
      });
    }
    if (cat2.length) {
      html += `<div style="color:#f39c12;font-weight:700;margin:6px 0 2px;">CAT II — Moderate (${cat2.length})</div>`;
      cat2.forEach(f => {
        html += `<div style="margin:4px 0;padding:6px 8px;background:#2b1a0f;border-left:3px solid #f39c12;border-radius:0 4px 4px 0;">`;
        html += `<div style="font-weight:600;font-size:11px;">${f.rule_id}: ${f.title}</div>`;
        html += `<div style="font-size:10px;color:#7a8cb0;margin-top:2px;">${(f.frameworks || []).join(', ')}</div>`;
        html += '</div>';
      });
    }
  }
  return html;
}

function _renderCost(r) {
  let html = _metric('Per Run', '$' + (r.per_run_cost || 0).toFixed(4));
  html += _metric('Runs/Month', r.runs_per_month || 0);
  html += _metric('Monthly Variable', '$' + (r.monthly_variable || 0).toFixed(2), 'Per-run costs x frequency');
  html += _metric('Monthly Fixed', '$' + (r.monthly_fixed || 0).toFixed(2), 'Licensing and platform fees');
  html += `<div style="margin:12px 0;padding:10px;background:#0f3460;border-radius:6px;text-align:center;">`;
  html += `<div style="font-size:10px;color:#7a8cb0;">Estimated Monthly Total</div>`;
  html += `<div style="font-size:28px;font-weight:800;color:#3498db;">$${(r.total_monthly || 0).toFixed(2)}</div>`;
  html += '</div>';

  if ((r.details || []).length) {
    html += _section('Per-Run Breakdown');
    r.details.forEach(d => {
      const style = getStyle(d.type);
      html += _metric(style.label, '$' + (d.per_run || 0).toFixed(4));
    });
  }

  html += '<p style="font-size:10px;color:#5a6e8c;margin-top:8px;">Estimates based on public pricing. Open source tools show $0 (compute cost not included). Actual costs vary by usage.</p>';
  return html;
}

function _renderExecTime(r) {
  const total = r.total_minutes || 0;
  const color = total <= 10 ? '#27ae60' : total <= 30 ? '#f39c12' : '#e74c3c';
  let html = `<div style="text-align:center;margin:8px 0;"><span style="font-size:36px;font-weight:800;color:${color};">${total}</span><span style="font-size:14px;color:#7a8cb0;"> min</span></div>`;

  if (total <= 10) html += '<p style="font-size:11px;color:#27ae60;">Fast pipeline. Developers get quick feedback on commits.</p>';
  else if (total <= 30) html += '<p style="font-size:11px;color:#f39c12;">Moderate execution time. Consider parallelizing independent scan stages.</p>';
  else html += '<p style="font-size:11px;color:#e74c3c;">Slow pipeline. Developers may skip CI or stack commits. Parallelize stages and optimize scan scopes.</p>';

  if (r.stage_breakdown) {
    html += _section('Stage Breakdown');
    const sorted = Object.entries(r.stage_breakdown).sort((a, b) => b[1] - a[1]);
    sorted.forEach(([stage, min]) => {
      const stageLabel = STAGE_LABELS[stage] || stage;
      const stageColor = STAGE_COLORS[stage] || '#7a8cb0';
      const pct = total > 0 ? Math.round(min / total * 100) : 0;
      html += `<div style="margin:4px 0;"><div style="display:flex;justify-content:space-between;font-size:11px;"><span style="color:${stageColor};font-weight:600;">${stageLabel}</span><span>${min} min (${pct}%)</span></div>`;
      html += _bar(pct, stageColor);
      html += '</div>';
    });
  }
  return html;
}

function _renderAntipatterns(r) {
  const findings = r.findings || [];
  const total = findings.length;
  const critical = findings.filter(f => f.severity === 'critical').length;
  const high = findings.filter(f => f.severity === 'high').length;
  const medium = findings.filter(f => f.severity === 'medium').length;

  let html = '';
  if (total === 0) {
    html += '<div style="text-align:center;margin:12px 0;color:#27ae60;font-size:20px;font-weight:700;">No Anti-Patterns Detected</div>';
    html += '<p style="font-size:11px;color:#7a8cb0;text-align:center;">Your pipeline design follows DevSecOps best practices.</p>';
    return html;
  }

  html += _metric('Total Findings', total);
  if (critical) html += _metric('Critical', critical, 'Must fix before deployment');
  if (high) html += _metric('High', high, 'Strongly recommended');
  if (medium) html += _metric('Medium', medium, 'Advisory');
  html += _bar((1 - total / 15) * 100, total > 3 ? '#e74c3c' : total > 1 ? '#f39c12' : '#27ae60');

  // Group by severity
  const sevOrder = ['critical', 'high', 'medium'];
  const sevColors = {critical: '#e74c3c', high: '#f39c12', medium: '#7a8cb0'};
  const sevLabels = {critical: 'CRITICAL', high: 'HIGH', medium: 'MEDIUM'};

  sevOrder.forEach(sev => {
    const sevFindings = findings.filter(f => f.severity === sev);
    if (!sevFindings.length) return;
    html += _section(sevLabels[sev] + ' (' + sevFindings.length + ')');
    sevFindings.forEach(f => {
      html += `<div style="margin:4px 0;padding:8px;background:#0f2040;border-left:3px solid ${sevColors[sev]};border-radius:0 4px 4px 0;">`;
      html += `<div style="font-weight:600;font-size:11px;color:${sevColors[sev]};">${f.id}: ${f.title}</div>`;
      html += `<div style="font-size:10px;color:#7a8cb0;margin:3px 0;">${f.description}</div>`;
      html += `<div style="font-size:10px;color:#27ae60;margin:3px 0;"><b>Fix:</b> ${f.recommendation}</div>`;
      if (f.frameworks && f.frameworks.length) {
        html += '<div style="margin-top:3px;">';
        f.frameworks.forEach(fw => { html += _pill(fw, '#0f3460'); });
        html += '</div>';
      }
      html += '</div>';
    });
  });
  return html;
}

function _renderPDCGovernance(r) {
  const score = r.score || 0;
  const grade = r.grade || '?';
  const maturity = r.maturity || {};
  const cats = r.categories || {};
  const recs = r.recommendations || [];
  const sColor = score >= 80 ? '#27ae60' : score >= 60 ? '#f39c12' : '#e74c3c';

  let html = `<div style="background:#0f2040;border-radius:8px;padding:12px;margin-bottom:12px;text-align:center;">
    <div style="font-size:38px;font-weight:700;color:${sColor};">${score}</div>
    <div style="font-size:11px;color:#7a8cb0;">Pipeline Governance Score / 100 · Grade <strong style="color:${sColor};">${grade}</strong></div>
  </div>`;
  html += _bar(score, sColor);
  html += `<div style="background:#16213e;border:1px solid #9b59b644;border-radius:6px;padding:8px 12px;margin-bottom:12px;">
    <div style="font-size:12px;font-weight:700;color:#9b59b6;">Level ${maturity.level||1} — ${maturity.label||''}</div>
    <div style="font-size:10px;color:#5a6e8c;">${maturity.description||''}</div>
  </div>`;
  html += `<div style="font-size:11px;font-weight:700;color:#7a8cb0;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px;">Pillars (${r.passed_checks}/${r.total_checks} passed)</div>`;
  Object.entries(cats).forEach(([pillar, c]) => {
    const pct = c.pct || 0;
    const pColor = pct >= 80 ? '#27ae60' : pct >= 50 ? '#f39c12' : '#e74c3c';
    html += `<div style="margin-bottom:7px;"><div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:2px;"><span style="color:#eaeaea;">${pillar}</span><span style="color:${pColor};font-weight:600;">${pct}% (${c.passed}/${c.total})</span></div>${_bar(pct, pColor)}</div>`;
  });
  if (recs.length) {
    html += `<div style="font-size:11px;font-weight:700;color:#7a8cb0;text-transform:uppercase;letter-spacing:.5px;margin:10px 0 6px;">Top Recommendations</div>`;
    recs.slice(0, 6).forEach(rec => {
      const bColor = rec.priority === 'CAT1' ? '#e74c3c' : rec.priority === 'CAT2' ? '#f39c12' : '#7a8cb0';
      html += `<div style="padding:5px 8px;border-left:3px solid ${bColor};background:#0d1b2a;border-radius:0 4px 4px 0;margin-bottom:4px;font-size:11px;color:#eaeaea;">${rec.title}</div>`;
    });
  }
  return html;
}

function _renderScorecard(data) {
  let html = '';
  // Overall grade
  const owasp = data.security_coverage?.coverage_pct || 0;
  const slsa = data.slsa_level?.achieved_level || 0;
  const comp = data.compliance?.score_pct || 0;
  const overall = Math.round((owasp + slsa * 25 + comp) / 3);
  const grade = overall >= 90 ? 'A' : overall >= 80 ? 'B' : overall >= 60 ? 'C' : overall >= 40 ? 'D' : 'F';
  const gradeColor = { A: '#27ae60', B: '#2ecc71', C: '#f39c12', D: '#e67e22', F: '#e74c3c' }[grade];
  html += `<div style="text-align:center;margin:8px 0;"><span style="font-size:48px;font-weight:800;color:${gradeColor};">${grade}</span><div style="font-size:11px;color:#7a8cb0;">Overall Grade (${overall}%)</div></div>`;

  html += _section('Security');
  html += _metric('OWASP Coverage', owasp + '%');
  html += _bar(owasp, owasp >= 80 ? '#27ae60' : owasp >= 50 ? '#f39c12' : '#e74c3c');

  html += _section('Supply Chain');
  const slsaColor = ['#e74c3c', '#f39c12', '#f1c40f', '#27ae60', '#16a085'][Math.min(slsa, 4)];
  html += _metric('SLSA Level', 'L' + slsa);
  html += _bar(slsa * 25, slsaColor);

  html += _section('Compliance');
  html += _metric('Rules Passed', comp + '%', `${data.compliance?.passed || 0} of ${(data.compliance?.passed || 0) + (data.compliance?.failed || 0)} rules`);
  html += _bar(comp, comp >= 80 ? '#27ae60' : comp >= 50 ? '#f39c12' : '#e74c3c');

  // Anti-patterns
  const ap = data.antipatterns || {};
  if (ap.total > 0) {
    html += _section('Anti-Patterns');
    html += _metric('Critical', ap.critical || 0);
    html += _metric('High', ap.high || 0);
    html += _metric('Medium', ap.medium || 0);
    const apScore = Math.max(0, 100 - (ap.critical || 0) * 30 - (ap.high || 0) * 15 - (ap.medium || 0) * 5);
    html += _bar(apScore, apScore >= 70 ? '#27ae60' : apScore >= 40 ? '#f39c12' : '#e74c3c');
  } else {
    html += _section('Anti-Patterns');
    html += '<div style="color:#27ae60;font-size:12px;font-weight:600;">None detected</div>';
  }

  html += _section('Operations');
  html += _metric('Est. Cost/mo', '$' + (data.cost_estimate?.total_monthly || 0).toFixed(2));
  html += _metric('Execution Time', (data.execution_time?.total_minutes || 0) + ' min');
  html += _metric('Node Count', data.node_count || 0);
  html += _metric('Edge Count', data.edge_count || 0);
  html += _metric('Stage Coverage', (data.stages_covered || 0) + ' / ' + (data.total_stages || 12));

  return html;
}

// ── Utility ─────────────────────────────────────────────────────────────────

function updateStatus(msg) {
  const el = document.getElementById('pc-status');
  if (el) el.textContent = msg;
}

function newCanvas() {
  if (isDirty && !confirm('Unsaved changes. Continue?')) return;
  window.location.href = '/devops/canvas/new';
}

// ── Palette Search ──────────────────────────────────────────────────────────

function filterPalette(query) {
  const q = query.toLowerCase();
  document.querySelectorAll('.palette-item').forEach(item => {
    const text = (item.textContent || '').toLowerCase();
    const type = (item.dataset.type || '').toLowerCase();
    item.style.display = (text.includes(q) || type.includes(q)) ? '' : 'none';
  });
}

// ── Boundary Boxes (Stage Lanes) ────────────────────────────────────────────

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

function createBoundaryBox(label, x, y, w, h, color) {
  pushUndo();
  const box = new joint.shapes.standard.Rectangle({
    position: { x, y },
    size: { width: w, height: h },
    attrs: {
      body: {
        fill: 'rgba(255,255,255,0.02)',
        stroke: color || '#e94560',
        strokeWidth: 2,
        strokeDasharray: '8,4',
        rx: 10, ry: 10,
      },
      label: {
        text: label || 'Boundary',
        fill: color || '#e94560',
        fontSize: 12,
        fontWeight: 'bold',
        refX: 12,
        refY: 14,
        textAnchor: 'start',
        textVerticalAnchor: 'top',
      },
    },
    z: -1, // Behind other elements
  });
  box.set('nodeType', 'boundary-box');
  box.set('isBoundary', true);
  graph.addCell(box);
  markDirty();
  return box;
}

function autoGroupByStage() {
  pushUndo();
  // Remove existing boundary boxes
  graph.getElements().filter(el => el.get('isBoundary')).forEach(el => el.remove());

  // Group nodes by inferred stage
  const stageGroups = {};
  graph.getElements().forEach(el => {
    const type = el.get('nodeType') || '';
    if (type === 'boundary-box') return;
    const pos = el.position();
    const size = el.size();
    // Infer stage from type
    let stage = 'build';
    const prefixMap = {
      'scm-': 'source', 'branch-': 'source', 'commit-': 'source',
      'build-': 'build', 'cicd-': 'build',
      'scan-': 'test', 'sbom-': 'build',
      'registry-': 'package', 'sign-': 'package', 'attest-': 'package',
      'policy-': 'policy_gate', 'gate-': 'policy_gate',
      'deploy-': 'deploy_prod', 'k8s-': 'deploy_prod',
      'aws-eks': 'deploy_prod', 'az-aks': 'deploy_prod', 'gcp-gke': 'deploy_prod',
      'openshift': 'deploy_prod', 'rke2': 'deploy_prod', 'mesh-': 'deploy_prod',
      'mon-': 'monitor', 'aws-cloudwatch': 'monitor', 'az-monitor': 'monitor',
      'comp-': 'compliance',
      'sre-': 'sre', 'aws-fis': 'sre', 'az-chaos': 'sre', 'aws-cw-slo': 'sre',
      'aws-incident': 'sre', 'aws-resilience': 'sre', 'gcp-service-mon': 'sre',
      'az-advisor': 'sre', 'ibm-instana': 'sre',
      'cds-': 'cross_domain', 'boundary-': 'cross_domain', 'pipeline-': 'cross_domain',
      'sneakernet': 'cross_domain', 'vuln-db-': 'cross_domain', 'package-mirror': 'cross_domain',
      'ndc-': 'infrastructure', 'hybrid-': 'infrastructure', 'onprem-': 'infrastructure',
    };
    for (const [prefix, s] of Object.entries(prefixMap)) {
      if (type.startsWith(prefix) || type === prefix) { stage = s; break; }
    }
    if (!stageGroups[stage]) stageGroups[stage] = [];
    stageGroups[stage].push({ x: pos.x, y: pos.y, w: size.width, h: size.height });
  });

  // Create boundary boxes around each stage group
  for (const [stage, rects] of Object.entries(stageGroups)) {
    if (!rects.length) continue;
    const pad = 30;
    const minX = Math.min(...rects.map(r => r.x)) - pad;
    const minY = Math.min(...rects.map(r => r.y)) - pad - 18; // Extra space for label
    const maxX = Math.max(...rects.map(r => r.x + r.w)) + pad;
    const maxY = Math.max(...rects.map(r => r.y + r.h)) + pad;
    const color = STAGE_COLORS[stage] || '#7a8cb0';
    const label = STAGE_LABELS[stage] || stage;
    createBoundaryBox(label, minX, minY, maxX - minX, maxY - minY, color);
  }
  markDirty();
  updateStatus('Stage boundaries added');
}

function clearBoundaries() {
  pushUndo();
  graph.getElements().filter(el => el.get('isBoundary')).forEach(el => el.remove());
  markDirty();
  updateStatus('Boundaries cleared');
}

// ── Legend ───────────────────────────────────────────────────────────────────

function toggleLegend() {
  let legend = document.getElementById('pc-legend');
  if (legend) { legend.style.display = legend.style.display === 'none' ? 'block' : 'none'; return; }

  legend = document.createElement('div');
  legend.id = 'pc-legend';
  legend.style.cssText = 'position:fixed;bottom:12px;left:260px;background:#16213e;border:1px solid #1e3a6e;border-radius:8px;padding:12px 16px;z-index:50;font-size:11px;max-height:400px;overflow-y:auto;min-width:200px;box-shadow:0 4px 16px rgba(0,0,0,0.4);';

  let html = '<div style="font-weight:700;margin-bottom:8px;font-size:13px;color:#eaeaea;">Legend</div>';

  // Stage colors
  html += '<div style="font-weight:600;color:#7a8cb0;margin-bottom:4px;">Pipeline Stages</div>';
  for (const [key, color] of Object.entries(STAGE_COLORS)) {
    const label = STAGE_LABELS[key] || key;
    html += '<div style="display:flex;align-items:center;gap:6px;margin:2px 0;">' +
      '<span style="width:12px;height:12px;border-radius:2px;background:' + color + ';flex-shrink:0;display:inline-block;"></span>' +
      '<span style="color:#eaeaea;">' + label + '</span></div>';
  }

  // Category colors
  html += '<div style="font-weight:600;color:#7a8cb0;margin:8px 0 4px;">Tool Categories</div>';
  const categories = [
    { label: 'Orchestration', color: '#3498db' },
    { label: 'Source Control', color: '#27ae60' },
    { label: 'Build', color: '#1abc9c' },
    { label: 'Security Scanning', color: '#9b59b6' },
    { label: 'Artifact / Registry', color: '#8e44ad' },
    { label: 'Supply Chain / Signing', color: '#f1c40f' },
    { label: 'Policy / Gates', color: '#e94560' },
    { label: 'Secrets / Keys', color: '#5b6abf' },
    { label: 'Deploy Targets', color: '#e67e22' },
    { label: 'Monitoring', color: '#1abc9c' },
    { label: 'Compliance', color: '#16a085' },
    { label: 'Cross-Domain / CDS', color: '#c0392b' },
    { label: 'SRE / Reliability', color: '#00bcd4' },
    { label: 'Service Mesh', color: '#e91e63' },
    { label: 'Infrastructure (NDC Bridge)', color: '#d4a017' },
  ];
  categories.forEach(c => {
    html += '<div style="display:flex;align-items:center;gap:6px;margin:2px 0;">' +
      '<span style="width:12px;height:12px;border-radius:2px;border:2px solid ' + c.color + ';flex-shrink:0;display:inline-block;"></span>' +
      '<span style="color:#eaeaea;">' + c.label + '</span></div>';
  });

  // CSP colors
  html += '<div style="font-weight:600;color:#7a8cb0;margin:8px 0 4px;">Cloud Providers</div>';
  const csps = [
    { label: 'AWS', color: '#ff9900' },
    { label: 'Azure', color: '#0078d4' },
    { label: 'GCP', color: '#4285f4' },
    { label: 'OCI', color: '#f80000' },
    { label: 'IBM', color: '#1261fe' },
  ];
  csps.forEach(c => {
    html += '<div style="display:flex;align-items:center;gap:6px;margin:2px 0;">' +
      '<span style="width:12px;height:12px;border-radius:2px;border:2px solid ' + c.color + ';flex-shrink:0;display:inline-block;"></span>' +
      '<span style="color:#eaeaea;">' + c.label + '</span></div>';
  });

  // Close button
  html += '<div style="margin-top:8px;text-align:right;"><button class="tb-btn" onclick="toggleLegend()">Close</button></div>';

  legend.innerHTML = html;
  document.body.appendChild(legend);
}

// ── Keyboard Shortcuts ──────────────────────────────────────────────────────

document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (e.ctrlKey && e.key === 'z') { e.preventDefault(); undoAction(); }
  if (e.ctrlKey && e.key === 'y') { e.preventDefault(); redoAction(); }
  if (e.ctrlKey && e.key === 's') { e.preventDefault(); savePipeline(); }
  if (e.key === 'Delete' || e.key === 'Backspace') { deleteSelected(); }
});
