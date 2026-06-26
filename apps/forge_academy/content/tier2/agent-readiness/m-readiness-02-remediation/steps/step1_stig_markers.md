---
ontology_id: icdev:mission:m-readiness-02-remediation:step:1
step_class: icdev:coding
---
# STIG Compliance Remediation

Pillar 10 (STIG Compliance) checks for STIG vulnerability ID references in code. The pattern: `# STIG V-XXXXX` or `# STIG: V-XXXXX` in any Python file relevant to security controls.

## Adding STIG markers

```python
# STIG V-220132: Ensure the application enforces session timeout
SESSION_TIMEOUT_SECONDS = 1800

# STIG V-220160: Application must not store plaintext passwords
def hash_password(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()
```

## Your task

Write a Python script that scans a target directory and adds appropriate STIG markers to functions related to: authentication, session management, input validation, and audit logging. The script should look up which STIG IDs map to each control family and add comments to matching functions.
