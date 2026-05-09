# Security Scanning

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Security Scanning
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Vuln Scanner [DEPRECATED] | tools/security/vuln_scanner.py | Vulnerability scanning orchestrator | --project | Scan results |
| SAST Runner | tools/security/sast_runner.py | Multi-language SAST (Bandit, SpotBugs, gosec, clippy, ESLint-security, SecurityCodeScan) | --report, --gate | Findings |
| Dependency Auditor | tools/security/dependency_auditor.py | Multi-language dep audit (pip-audit, npm-audit, cargo-audit, govulncheck, OWASP DC, dotnet) | --report, --gate | Vulnerabilities |
| Secret Detector | tools/security/secret_detector.py | detect-secrets wrapper | --report, --gate | Secrets found |
| Container Scanner | tools/security/container_scanner.py | trivy container scanning | --image | Vulnerabilities |
| Boundary Tagger | tools/security/boundary_tagger.py | Tags SAST/secret/container/dependency findings with ATO boundary tier (GREEN/YELLOW/ORANGE/RED); auto-creates boundary_impact_assessment records for ORANGE/RED findings | --report, --project-id, --system-id, --create-assessments, --gate | Tagged findings + boundary_impact_summary |
| Blueprint Verifier | tools/security/blueprint_verifier.py | NemoClaw-adapted SHA-256 recursive directory digest for genome/marketplace/child integrity (D-NC-3) | --compute, --verify, --store, --lookup, --history, --json | Digest + verification |
| Credential Broker | tools/security/credential_broker.py | NemoClaw-adapted agent credential isolation: function-scoped tokens, auto-revocation (D-NC-1) | --request, --revoke, --audit, --status, --gate, --json | Token + grant log |
| Egress Policy Manager | tools/security/egress_policy_manager.py | NemoClaw-adapted per-agent network egress policies with deny-by-default (D-NC-2) | --resolve, --generate, --validate, --diff, --list-roles, --audit, --json | K8s NetworkPolicy |

