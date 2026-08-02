# Pipeline Design Scan Report
**Generated:** 2026-08-02 02:25 UTC  
**Pipeline:** Insecure Pipeline (`pl-1`)  
**Nodes:** 3  **Edges:** 2  
**Gate:** FAIL

## Node Types

- `cicd-gitlab`
- `k8s-cluster`
- `scm-gitlab`

## Anti-Patterns Detected

### [AP-PDC-001] No security scanning in pipeline (CRITICAL)
Pipeline has CI/CD engine and deploy target but zero security scanners (SAST, SCA, DAST, container scan, secret detection). This violates all DevSecOps frameworks.
**Recommendation:** Add at minimum: SAST (Semgrep/SonarQube), SCA (Trivy/Grype), and secret detection (Gitleaks).

### [AP-PDC-002] Direct deploy to production without approval gate (CRITICAL)
Pipeline deploys directly to production K8s/VM without a manual or automated approval gate. This bypasses change control.
**Recommendation:** Add a manual approval gate or automated condition check (vulnerability threshold, SLO health) before production deployment.

### [AP-PDC-004] Secret management missing (HIGH)
Pipeline references secrets (API keys, credentials) but has no secret management tool (Vault, KMS, Sealed Secrets). Secrets may be hardcoded or in env vars.
**Recommendation:** Add HashiCorp Vault, AWS Secrets Manager, or Azure Key Vault. Use External Secrets Operator for K8s.

### [AP-PDC-005] No SBOM generation (HIGH)
Pipeline builds and deploys software without generating a Software Bill of Materials. SBOM is required by EO 14028 for federal software.
**Recommendation:** Add Syft or CycloneDX SBOM generation in the build stage.

### [AP-PDC-007] No runtime security monitoring (HIGH)
Pipeline deploys to production but has no runtime security monitoring (Falco, NeuVector, Wazuh). Post-deploy threats are invisible.
**Recommendation:** Add Falco for K8s runtime threat detection or NeuVector for container security.

### [AP-PDC-009] No SLO defined for production services (MEDIUM)
Pipeline deploys to production but has no SLO tracking. Without SLOs, there's no objective measure of service health or error budget.
**Recommendation:** Add SLO Manager with availability and latency targets. Use burn-rate alerting.

### [AP-PDC-014] No policy admission controller on K8s (MEDIUM)
Pipeline deploys to Kubernetes without Kyverno, OPA/Gatekeeper, or Binary Authorization. Unsigned/non-compliant images can be deployed.
**Recommendation:** Add Kyverno or OPA Gatekeeper to enforce image signing, source verification, and pod security standards.

### [AP-PDC-015] Single CI/CD engine (no pipeline resilience) (MEDIUM)
Entire pipeline depends on a single CI/CD engine. If GitLab/Jenkins/Tekton goes down, no builds or deploys can run.
**Recommendation:** For production-critical pipelines, consider a secondary CI engine or ensure the primary has HA (GitLab HA, Jenkins HA).
