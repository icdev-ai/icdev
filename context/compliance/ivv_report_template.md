{{cui_banner_top}}

# Independent Verification & Validation (IV&V) Certification Report

**Project:** {{project_name}}
**Project ID:** {{project_id}}
**Classification:** {{classification}}
**Assessment Date:** {{assessment_date}}
**Report Version:** {{version}}
**IV&V Authority:** {{ivv_authority}}
**Framework:** IEEE 1012-2016, DoDI 5000.87

---

## 1. Executive Summary

**Verification Score:** {{verification_score}}%
**Validation Score:** {{validation_score}}%
**Overall IV&V Score:** {{overall_score}}%
**Gate Result:** {{gate_result}}
**Certification Recommendation:** {{certification_recommendation}}

{{executive_summary}}

## 2. Independence Declaration

This Independent Verification and Validation assessment was conducted separately from the development team per IEEE 1012 and DoD requirements. The IV&V engine operates with:
- **Organizational Independence:** Separate assessment authority from development
- **Technical Independence:** Independent analysis tools and criteria
- **Financial Independence:** Assessment budget separate from development
- **Authority:** Gate authority to block releases based on findings

## 3. Verification Results

Verification ("Are we building the product right?") evaluates process compliance across 8 process areas.

### 3.1 Process Area Scores

{{process_area_scores_table}}

### 3.2 Process Area Details

{{process_area_details}}

## 4. Validation Results

Validation ("Are we building the right product?") evaluates outcome correctness.

### 4.1 Test Verification Results
{{test_verification_results}}

### 4.2 Integration Verification Results
{{integration_verification_results}}

## 5. Requirements Traceability Matrix Summary

{{rtm_summary}}

**RTM Coverage:** {{rtm_coverage}}%
**Requirements with Full Trace:** {{rtm_full_trace_count}}
**Requirements with Gaps:** {{rtm_gap_count}}
**Orphan Tests:** {{rtm_orphan_tests}}

## 6. IV&V Findings

### 6.1 Critical Findings
{{critical_findings}}

### 6.2 High Findings
{{high_findings}}

### 6.3 Moderate Findings
{{moderate_findings}}

### 6.4 Low Findings
{{low_findings}}

### 6.5 Findings Summary
| Severity | Open | Resolved | Accepted Risk | Deferred | Total |
|----------|------|----------|---------------|----------|-------|
{{findings_summary_table}}

## 7. Certification Recommendation

**Recommendation:** {{certification_recommendation}}

### Criteria Applied:
- **CERTIFY:** Overall score >= 80%, 0 critical findings, all process areas >= 60%
- **CONDITIONAL:** Overall score >= 60%, 0 critical findings, conditions listed
- **DENY:** Overall score < 60% OR critical findings unresolved

### Conditions (if applicable):
{{conditions}}

## 8. Evidence Index

{{evidence_index}}

## 9. Assessment Methodology

This assessment was conducted using the ICDEV IV&V Assessor tool against the IEEE 1012 requirements catalog (30 requirements across 9 process areas).

**Scoring Formula:**
- Verification Score = average of process area pass rates
- Validation Score = average of Test + Integration area pass rates
- Overall Score = 0.6 x Verification + 0.4 x Validation

**Gate Logic:** PASS if 0 critical findings remain open

---

**Prepared by:** {{ivv_authority}}
**Date:** {{assessment_date}}
**Next Review:** {{next_review_date}}

{{cui_banner_bottom}}
