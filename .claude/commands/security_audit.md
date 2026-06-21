---
allowed-tools:
- Read
- Grep
- Glob
context: fork
description: 'Full security audit: OWASP Top 10, SAST findings, secrets, dependency
  vulns, auth flows.'
name: security-audit
tags:
- security
- audit
- owasp
- devsecops
---
# Security Audit

CUI // SP-CTI

## Overview

Full security audit: OWASP Top 10, SAST findings, secrets, dependency vulns, auth flows.

## Provenance

- **Source:** OpenClaw Community (SkillHub)
- **Author:** anthropic
- **Original URL:** local://official-seed/claude/claude-security-audit
- **Import Date:** 2026-06-14T15:45:42.585652+00:00
- **SHA-256:** 171dbfff4653b1697f68eae14ced8a19b603b3f433a2808b75bd8824f678d0d9
- **Scan Status:** PASSED (all 10 gates)
- **Trust Score:** 0.3
- **Registration Required:** No
- **Renewal Required:** No

## Instructions

# Security Audit

CUI // SP-CTI

## Overview

Full security audit: OWASP Top 10, SAST findings, secrets, dependency vulns, auth flows.

## Provenance

- **Enhanced by:** ICDEV™ (Innovation + Creative + Research engines)
- **Original Author:** anthropic
- **Source:** OpenClaw Community (SkillHub)
- **Author:** anthropic
- **Original Version:** 1.3.0
- **Compatibility Score:** 96/100
- **Auto-Adaptations:** 2

## Instructions

# Security Audit

Perform a comprehensive security audit of the provided code, configuration, or architecture.

## Instructions

Run through each OWASP Top 10 category against the provided artifact:

1. **A01 — Broken Access Control**: Check authorization checks, IDOR, privilege escalation paths
2. **A02 — Cryptographic Failures**: Weak algorithms, hardcoded keys, unencrypted sensitive data
3. **A03 — Injection**: SQL, command, LDAP, XPath, template injection vectors
4. **A04 — Insecure Design**: Missing threat modeling, insecure defaults, trust boundary violations
5. **A05 — Security Misconfiguration**: Debug mode on, default creds, verbose errors, CORS wildcard
6. **A06 — Vulnerable Components**: Known CVEs in dependencies (check version pinning)
7. **A07 — Auth Failures**: Session management, token expiry, brute-force protection
8. **A08 — Data Integrity Failures**: Unsigned serialization, untrusted deserialization
9. **A09 — Logging Failures**: Missing audit logs, logging sensitive data, log injection
10. **A10 — SSRF**: Unvalidated URLs, metadata endpoint exposure

For each finding:
- **Severity**: Critical / High / Medium / Low / Info
- **CWE**: Reference the CWE number
- **Location**: File and line where applicable
- **Recommendation**: Specific, actionable remediation

End with a risk-ranked summary table.

## Usage

```
/claude-security-audit $ARGUMENTS
```


## Enrichment (ICDEV™ Intelligence)

*Auto-generated on 2026-06-14 by Innovation + Creative + Research engines*

