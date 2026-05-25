# ISA and Boundary Control Report
**Generated:** 2026-05-19 23:58 UTC  
**Project:** default  
**Gate:** ✓ PASS  
**Designs:** 1  **Nodes:** 7
**Rules checked:** 6  **Passed:** 6  **Failed:** 0  **Warned:** 0  **Skipped:** 0

## Check Results

| Rule ID | Name | Status | Severity |
|---------|------|--------|----------|
| ISA-1.1 | ISA Expiry Check | ✓ PASS | info |
| ISA-1.2 | No Unclassified Data Crossing Boundary | ✓ PASS | fail |
| ISA-1.3 | VPC Endpoints Present | ✓ PASS | fail |
| ISA-1.4 | NACLs on Boundary Subnets | ✓ PASS | fail |
| ISA-1.5 | KMS Encryption for Boundary Data | ✓ PASS | warn |
| ISA-1.6 | Audit Logging for Boundary Crossings | ✓ PASS | warn |

## Reference Standards

| Rule ID | Standard | Requirement |
|---------|----------|-------------|
| ISA-1.1 | DoD 8500.01 | §3.5 ISA Expiry |
| ISA-1.2 | NIST SP 800-53 | AC-17 Remote Access |
| ISA-1.3 | NIST SP 800-53 | SC-7(4) External Telecommunications |
| ISA-1.4 | NIST SP 800-53 | SC-7 Boundary Protection |
| ISA-1.5 | NIST SP 800-53 | SC-28, FIPS 140-2 |
| ISA-1.6 | NIST SP 800-53 | AU-2, AU-12 Audit Events |