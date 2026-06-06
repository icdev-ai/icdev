# icdev-secure — Step-by-Step Reference

## Steps

### 1. Load Security Configuration
```bash
!cat args/security_gates.yaml
```

### 2. Resolve Project Directory
If `--project-dir` not specified, detect from current context.

### 3. Run SAST
```bash
python tools/security/sast_runner.py --project-dir <path>
```
- Uses bandit for Python files
- Reports: severity levels (HIGH/MEDIUM/LOW), CWE IDs, line numbers

### 4. Run Dependency Audit
```bash
python tools/security/dependency_auditor.py --project-dir <path>
```
- Checks requirements.txt (pip-audit) and package.json (npm audit)
- Reports: CVE IDs, affected packages, fix versions

### 5. Run Secret Detection
```bash
python tools/security/secret_detector.py --project-dir <path>
```
- 10 built-in regex patterns (AWS keys, passwords, tokens, private keys, etc.)
- Uses detect-secrets baseline if available
- Reports: file, line number, secret type

### 6. Run Container Scan
```bash
python tools/security/container_scanner.py --project-dir <path>
```
- Static Dockerfile analysis (10 checks: root user, COPY vs ADD, pinned versions, etc.)
- Trivy image scan if available
- Reports: severity, check name, fix suggestion

### 7. Evaluate Security Gates
```bash
python tools/security/vuln_scanner.py --project-dir <path>
```
- Gate thresholds from security_gates.yaml:
  - SAST: 0 HIGH severity findings
  - Dependencies: 0 critical CVEs
  - Secrets: 0 detected secrets
  - Container: no root user, no unpatched CVEs
- PASS/FAIL determination

### 8. Auto-Fix (if --fix)
If `--fix` flag provided:
- Remove detected secrets and add to .gitignore
- Update vulnerable dependencies to patched versions
- Fix Dockerfile issues (add USER, pin versions)

### 9. Compliance Mapping
Run the CLI command or use MCP tool `control_map` MCP tool from icdev-compliance:
- Map `security.scan` to NIST controls (RA-5, SA-11, SI-2)

### 10. Output Summary
Display:
- SAST: X findings (H/M/L breakdown)
- Dependencies: X CVEs (critical/high/medium/low)
- Secrets: X detected (MUST be 0 to pass)
- Container: X issues
- Gate: PASS/FAIL
- Compliance controls satisfied
