{{cui_banner_top}}

# IV&V Verification Checklist

**Project:** {{project_name}}
**Project ID:** {{project_id}}
**Classification:** {{classification}}
**Assessment Date:** {{assessment_date}}
**IV&V Authority:** {{ivv_authority}}
**Framework:** IEEE 1012-2016, DoDI 5000.87, DoDI 8510.01

---

## Independence Declaration

This verification and validation assessment is conducted independently from the development team per IEEE 1012 requirements. The IV&V engine operates with separate assessment criteria, tools, and authority to gate releases.

---

## Process Area Checklists

### 1. Requirements Verification (REQ)
- [ ] IVV-01: Requirements documented (requirements.md, user stories, .feature files)
- [ ] IVV-02: Requirements consistent (no conflicts between features)
- [ ] IVV-03: Requirements testable (each has corresponding test case)
- [ ] IVV-04: Requirements traced (RTM links requirements to design, code, tests)

### 2. Design Verification (DES)
- [ ] IVV-05: Architecture documented (architecture.md, ADR, system design)
- [ ] IVV-06: Threat model reviewed (STRIDE/PASTA analysis)
- [ ] IVV-07: Security patterns followed (defense in depth, least privilege)

### 3. Code Verification (CODE)
- [ ] IVV-08: Independent SAST completed (scan results available)
- [ ] IVV-09: Coding standards enforced (linter/formatter configured)
- [ ] IVV-10: Code reviews completed and approved
- [ ] IVV-11: Complexity metrics within thresholds

### 4. Test Verification (TEST)
- [ ] IVV-12: Test coverage >= 80% for critical systems
- [ ] IVV-13: Test plan documented or structured tests/ directory
- [ ] IVV-14: Security test cases present
- [ ] IVV-15: BDD features have step implementations

### 5. Integration Verification (INT)
- [ ] IVV-16: Interface tests verify module interactions
- [ ] IVV-17: End-to-end tests exist
- [ ] IVV-18: API contract tests in place

### 6. Traceability Analysis (RTM)
- [ ] IVV-19: Requirements Traceability Matrix exists
- [ ] IVV-20: Bidirectional traceability (forward + backward)
- [ ] IVV-21: Gap analysis completed (untested reqs, orphan tests)

### 7. Security Verification (SEC)
- [ ] IVV-22: Independent security assessment conducted
- [ ] IVV-23: Penetration test results available
- [ ] IVV-24: Critical/high vulnerabilities remediated

### 8. Build/Deploy Verification (BLD)
- [ ] IVV-25: CI/CD pipeline includes security stages
- [ ] IVV-26: Build artifacts have SBOM/checksums
- [ ] IVV-27: Containers STIG-hardened
- [ ] IVV-28: Rollback mechanism verified

### 9. Process Compliance (PROC)
- [ ] IVV-29: TDD adherence verified (tests before code)
- [ ] IVV-30: All security/compliance gates passed

---

## Summary

**Total Requirements:** 30
**Verified:** {{verified_count}}
**Failed:** {{failed_count}}
**Deferred:** {{deferred_count}}
**Not Assessed:** {{not_assessed_count}}

{{cui_banner_bottom}}
