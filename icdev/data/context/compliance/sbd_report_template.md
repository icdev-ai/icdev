{{cui_banner_top}}

# Secure by Design Assessment Report

**Project:** {{project_name}}
**Project ID:** {{project_id}}
**Classification:** {{classification}}
**Assessment Date:** {{assessment_date}}
**Report Version:** {{version}}
**Assessor:** {{assessor}}
**Framework:** CISA Secure by Design + DoDI 5000.87 + NIST SP 800-218 SSDF

---

## 1. Executive Summary

**Overall SbD Score:** {{overall_score}}%
**Gate Result:** {{gate_result}}
**Domains Assessed:** {{domains_assessed}} / 14
**Critical Requirements Not Satisfied:** {{critical_not_satisfied}}

{{executive_summary}}

## 2. CISA Secure by Design Commitment Status

The following table shows compliance with the 7 CISA Secure by Design commitments:

{{cisa_commitment_table}}

## 3. Domain Assessment Summary

{{domain_scores_table}}

## 4. Detailed Domain Assessments

{{domain_details}}

## 5. Auto-Check Results

{{auto_check_results}}

## 6. Manual Review Items

The following requirements require manual verification:

{{manual_review_items}}

## 7. Findings and Remediation

### Critical Findings
{{critical_findings}}

### Remediation Recommendations
{{remediation_table}}

## 8. Evidence Artifacts

{{evidence_summary}}

## 9. NIST 800-53 Control Mapping

{{nist_control_mapping}}

## 10. Assessment Methodology

This assessment was conducted using the ICDEV SbD Assessor tool against the CISA Secure by Design requirements catalog (35 requirements across 14 domains). Automated checks were performed where possible; requirements marked as "semi" or "manual" are flagged for human review.

**Scoring Formula:** Score = 100 x (satisfied + partially_satisfied x 0.5 + risk_accepted x 0.75) / assessable_count

**Gate Logic:** PASS if 0 critical-priority requirements have status "not_satisfied"

---

**Prepared by:** {{assessor}}
**Date:** {{assessment_date}}

{{cui_banner_bottom}}
