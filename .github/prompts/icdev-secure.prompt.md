---
mode: agent
description: "Run comprehensive security scanning (SAST, dependency audit, secret detection, container scan)"
tools:
  - terminal
  - file_search
---

# icdev-secure

Runs the full security scanning pipeline:
1. **SAST** — Static Application Security Testing (bandit for Python)
2. **Dependency Audit** — Check for known CVEs in dependencies (pip-audit, npm audit)
3. **Secret Detection** — Scan for hardcoded credentials, API keys, tokens
4. **Container Scan** — Dockerfile security checks + image vulnerability scan (trivy)
5. Evaluates security gates and records findings

## Steps

1. **Load Security Configuration**
```bash
!cat args/security_gates.yaml
```

2. **Resolve Project Directory**
If `--project-dir` not specified, detect from current context.

3. **Run SAST**
```bash
python tools/security/sast_runner.py --project-dir <path>
```

4. **Run Dependency Audit**
```bash
python tools/security/dependency_auditor.py --project-dir <path>
```

5. **Run Secret Detection**
```bash
python tools/security/secret_detector.py --project-dir <path>
```

6. **Run Container Scan**
```bash
python tools/security/container_scanner.py --project-dir <path>
```

7. **Evaluate Security Gates**
```bash
python tools/security/vuln_scanner.py --project-dir <path>
```

8. **Auto-Fix (if --fix)**
If `--fix` flag provided:
- Remove detected secrets and add to .gitignore
- Update vulnerable dependencies to patched versions

9. **Compliance Mapping**
Run the equivalent CLI command for control_map:
- Map `security.scan` to NIST controls (RA-5, SA-11, SI-2)

10. **Output Summary**
Display:
- SAST: X findings (H/M/L breakdown)
- Dependencies: X CVEs (critical/high/medium/low)

11. **Output Summary**
Display:
- SAST: X findings (H/M/L breakdown)
- Dependencies: X CVEs (critical/high/medium/low)

## Example
```
#prompt:icdev-secure --project-dir projects/my-webapp --scan all --fix
```