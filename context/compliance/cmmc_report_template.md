{{cui_banner_top}}

# CMMC Level {{level}} Assessment Report

**System Name:** {{system_name}}
**Project ID:** {{project_id}}
**Impact Level:** {{impact_level}}
**CMMC Level:** {{cmmc_level}}
**Assessment Date:** {{assessment_date}}
**Report Version:** {{version}}
**Assessor:** {{assessor}}
**Assessment Type:** {{assessment_type}}
**Framework:** CMMC v2.0 (32 CFR Part 170), NIST SP 800-171 Rev 2

---

## 1. Executive Summary

**Overall Readiness Score:** {{overall_score}}%
**Gate Result:** {{gate_result}}
**Domains Assessed:** {{domains_assessed}} / 14
**Practices Not Met:** {{practices_not_met}}

{{executive_summary}}

---

## 2. System Information

| Field | Value |
|-------|-------|
| System Name | {{system_name}} |
| Project ID | {{project_id}} |
| Impact Level | {{impact_level}} |
| CMMC Level | {{cmmc_level}} |
| Assessment Date | {{assessment_date}} |
| Assessment Type | {{assessment_type}} |
| Assessor | {{assessor}} |
| Report Version | {{version}} |

---

## 3. Assessment Summary

| Status | Count | Percentage |
|--------|------:|----------:|
| **Total Practices** | {{total_practices}} | 100% |
| Met | {{practices_met_count}} | {{practices_met_pct}}% |
| Not Met | {{practices_not_met_count}} | {{practices_not_met_pct}}% |
| Partially Met | {{practices_partially_met_count}} | {{practices_partially_met_pct}}% |
| Not Applicable | {{practices_na_count}} | {{practices_na_pct}}% |
| Not Assessed | {{practices_not_assessed_count}} | {{practices_not_assessed_pct}}% |

---

## 4. Domain Analysis

The following table summarizes assessment results for each of the 14 CMMC domains:

{{domain_scores_table}}

### Domain Detail

{{domain_details}}

---

## 5. NIST 800-171 Alignment

CMMC Level 2 practices are aligned to NIST SP 800-171 Rev 2 security requirements.
The following table maps assessed practices to their corresponding NIST 800-171 identifiers:

{{nist_171_mapping}}

---

## 6. Gap Analysis

The following practices have a status of **not_met** and require remediation before CMMC certification:

{{gap_analysis}}

---

## 7. Readiness Score

**Overall Readiness Score:** {{overall_score}}%

**Scoring Formula:** Score = 100 x (met + partially_met x 0.5) / assessable_count
(assessable_count excludes not_applicable practices)

| Domain | Score | Status |
|--------|------:|--------|
{{readiness_by_domain}}

---

## 8. Gate Evaluation

**Gate Logic:** PASS if 0 Level {{level}} practices have status "not_met"

| Criteria | Result |
|----------|--------|
| Level {{level}} Practices Not Met | {{practices_not_met}} |
| Gate Result | **{{gate_result}}** |
| Certification Readiness | {{certification_readiness}} |

{{gate_details}}

---

## 9. Recommendations

{{recommendations}}

---

## 10. Evidence References

The following evidence artifacts were referenced during this assessment:

{{evidence_references}}

---

## 11. Assessment Methodology

This assessment was conducted using the ICDEV CMMC Assessor tool against the CMMC v2.0 practice catalog. CMMC Level 2 includes 110 practices aligned with NIST SP 800-171 Rev 2. Level 3 adds 24 practices from NIST SP 800-172 for a total of 134 practices.

Automated checks were performed where possible. Practices marked as "semi" or "manual" require human verification by a certified CMMC Third Party Assessment Organization (C3PAO).

**Scoring Formula:** Score = 100 x (met + partially_met x 0.5) / assessable_count

**Gate Logic:** PASS if 0 Level {{level}} practices have status "not_met"

---

**Prepared by:** {{assessor}}
**Date:** {{assessment_date}}
**Classification:** {{classification}}

{{cui_banner_bottom}}
