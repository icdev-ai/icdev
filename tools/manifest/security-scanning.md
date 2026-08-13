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
| SIPA PR diff gate | tools/integrity/pr_gates.py | Blocking pre-merge / CI security gate that runs the SIPA (Software Integrity & Provenance Assessor) pipeline over ONLY the *.py files changed on a branch (git diff `<base>...HEAD`, or `--cached` for the staged index) — copies them into a path-preserving quarantine subtree and runs ingest→scanners→capability_extractor→scoring over the subset, emitting an ALLOW / REVIEW / QUARANTINE verdict. `--gate` maps the verdict to a CI exit code (1 on a blocking verdict, QUARANTINE by default). Static-only: never executes the changed code; honors `ICDEV_INTEGRITY_ENABLED` (no-op pass when off). Full SIPA pipeline modules are in `tools/manifest/unclassified.md`. | --base origin/main [--cached] [--mode auto\|provenance_aware\|provenance_blind] [--repo-root P] [--gate] [--json] | Verdict + exit code |

| Sensitive Path Inventory | tools/security/sensitive_paths.py | The single credential-path inventory (exa-bench-09), consumed by three surfaces that each used to have their own idea of "a credential" or none at all: the `zero_access` tier in args/file_access_tiers.yaml (via tools/hooks/shared_checks.py), the `confidentiality` rule in tools/agent_runtime/approval_gate.py, and check_path_allowed() in tools/studio/executors/agent_tool_gate.py. Also classifies a shell command as a credential READ (`cat ~/.aws/credentials`, `env \| grep -i key`) — write verbs are deliberately excluded, that is exa-bench-07. Patterns live in args/sensitive_paths.yaml; stdlib-only so the pre_tool_use hook can load it by path. Complements secret_detector.py, which detects credential CONTENT rather than naming the paths. | --check PATH, --check-command CMD, --list, --json | Match + label, exit 1 when sensitive |
