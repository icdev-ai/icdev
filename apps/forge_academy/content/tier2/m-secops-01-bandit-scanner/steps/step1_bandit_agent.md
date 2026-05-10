# Build a Static Analysis Security Agent

Bandit is Python's standard SAST tool — but raw Bandit output is noisy: hundreds of findings, mixed severities, no prioritization. In this mission you'll wrap Bandit in an agent that triages findings, maps them to CWE/OWASP categories, and generates fix recommendations.

## What you'll build

```
Python source code
        │
        ▼
BanditScanner.scan(code) → raw findings list
        │
        ▼
triage_findings(findings) → { critical, high, medium, low }
        │
        ▼
generate_report(triaged) → formatted security report
```

## The triage logic

| Bandit Severity | Bandit Confidence | Agent Priority |
|-----------------|-------------------|---------------|
| HIGH            | HIGH              | CRITICAL       |
| HIGH            | MEDIUM/LOW        | HIGH          |
| MEDIUM          | HIGH              | HIGH           |
| MEDIUM          | MEDIUM            | MEDIUM         |
| LOW             | *                 | LOW            |

## CWE mapping

Your agent maps Bandit test IDs to CWE categories:
- `B102` (exec-used) → CWE-78 (OS Command Injection)
- `B105/B106/B107` (hardcoded passwords) → CWE-259 (Use of Hard-coded Password)
- `B301/B303` (pickle/md5) → CWE-327 (Use of Broken Cryptographic Algorithm)
- `B501/B502` (ssl/tls) → CWE-295 (Improper Certificate Validation)
- `B608` (sql injection) → CWE-89 (SQL Injection)

## Success criteria

- `BanditScanner.scan()` finds ≥3 issues in the vulnerable code sample
- `triage_findings()` correctly assigns CRITICAL to HIGH-HIGH findings
- `generate_report()` returns a string with finding count and at least one CWE reference
- The report includes a fix recommendation for the highest priority finding
