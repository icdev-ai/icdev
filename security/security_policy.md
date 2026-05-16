# [TEMPLATE: CUI // SP-CTI]
# ICDEV Security Policy
# Classification: CUI // SP-CTI | Impact Level: IL4

## Purpose

This document defines the high-level security policy for ICDEV deployments.

## Scope

Applies to all ICDEV runtime environments, CI/CD pipelines, container images,
and data stores handling CUI or higher classifications.

## Policy Statements

1. **Classification Handling**
   - All artifacts default to CUI // SP-CTI (IL4).
   - IL5 and IL6 environments require explicit overrides in `baseline.yaml`.

2. **Access Control**
   - Multi-factor authentication (MFA) is required for all administrative access.
   - Sessions expire after 30 minutes of inactivity.
   - Account lockout occurs after 5 failed authentication attempts.

3. **Encryption**
   - Data at rest: AES-256-GCM.
   - Data in transit: TLS 1.3 minimum.
   - Key management: FIPS 140-2 Level 2 or higher.

4. **Audit & Logging**
   - Audit trails are append-only and immutable.
   - Logs are retained for 365 days.
   - Forward security-relevant events to SIEM.

5. **Container Security**
   - Containers run as non-root.
   - Read-only root filesystem.
   - No new privileges granted at runtime.

6. **Compliance**
   - SBOM generated on every build.
   - CUI markings required on all artifacts.
   - CAT 1 STIG findings block deployment.

## Review

Review this policy annually or after any control change.
