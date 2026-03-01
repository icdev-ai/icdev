////////////////////////////////////////////////////////////////////
CONTROLLED UNCLASSIFIED INFORMATION (CUI) // SP-CTI
Distribution: Distribution D -- Authorized DoD Personnel Only
////////////////////////////////////////////////////////////////////

# FedRAMP {{baseline}} Baseline Security Assessment Report

**System Name:** {{system_name}}
**Project ID:** {{project_id}}
**Classification:** {{classification}}
**Impact Level:** {{impact_level}}
**FedRAMP Baseline:** {{baseline}}
**Assessment Date:** {{assessment_date}}
**Report Version:** {{version}}
**Assessor:** {{assessor}}
**Framework:** FedRAMP Rev 5 (NIST SP 800-53 Rev 5)

---

## 1. Executive Summary

**Overall Readiness Score:** {{readiness_score}}%
**Gate Result:** {{gate_result}}
**Total Controls Assessed:** {{total_controls}}
**Controls Satisfied:** {{controls_satisfied}}
**Controls Other Than Satisfied:** {{controls_other_than_satisfied}}
**Critical Controls Not Satisfied:** {{critical_controls_not_satisfied}}

{{executive_summary}}

---

## 2. System Information

| Field | Value |
|-------|-------|
| System Name | {{system_name}} |
| Project ID | {{project_id}} |
| Impact Level | {{impact_level}} |
| FedRAMP Baseline | {{baseline}} |
| Classification | {{classification}} |
| Cloud Environment | {{cloud_environment}} |
| Assessment Date | {{assessment_date}} |
| Assessor | {{assessor}} |

---

## 3. Assessment Summary

| Status | Count | Percentage |
|--------|------:|----------:|
| Satisfied | {{controls_satisfied}} | {{pct_satisfied}}% |
| Other Than Satisfied | {{controls_other_than_satisfied}} | {{pct_other_than_satisfied}}% |
| Not Applicable | {{controls_not_applicable}} | {{pct_not_applicable}}% |
| Risk Accepted | {{controls_risk_accepted}} | {{pct_risk_accepted}}% |
| Not Assessed | {{controls_not_assessed}} | {{pct_not_assessed}}% |
| **Total** | **{{total_controls}}** | **100%** |

---

## 4. Control Family Analysis

The following table summarizes implementation status by NIST 800-53 control family:

{{control_family_table}}

### 4.1 Control Family Details

{{control_family_details}}

---

## 5. Gap Analysis

The following controls have not achieved "satisfied" status and require remediation:

{{gap_analysis_table}}

### 5.1 Gap Summary

- **Total Gaps Identified:** {{total_gaps}}
- **Critical Gaps (Other Than Satisfied):** {{controls_other_than_satisfied}}
- **Not Yet Assessed:** {{controls_not_assessed}}
- **Estimated Remediation Effort:** {{remediation_effort}}

---

## 6. Readiness Score

**Overall FedRAMP Readiness Score:** {{readiness_score}}%

**Scoring Formula:** Score = 100 x (satisfied + risk_accepted x 0.75) / (total - not_applicable)

| Readiness Level | Threshold | Current Status |
|-----------------|-----------|----------------|
| Ready for Assessment | >= 80% | {{readiness_level_80}} |
| Conditionally Ready | >= 60% | {{readiness_level_60}} |
| Not Ready | < 60% | {{readiness_level_below_60}} |

**Current Readiness Level:** {{readiness_level}}

---

## 7. Gate Evaluation

The FedRAMP security gate evaluates whether the system meets minimum authorization requirements.

**Gate Result:** {{gate_result}}

**Gate Criteria:**
- 0 "other_than_satisfied" critical controls (AC-2, IA-2, SC-7, AU-2, CM-6) = {{critical_control_gate}}
- Readiness score >= 80% = {{readiness_gate}}
- All control families assessed = {{family_coverage_gate}}

| Gate Check | Requirement | Result |
|------------|-------------|--------|
| Critical Controls | 0 other_than_satisfied on critical controls | {{critical_control_gate}} |
| Readiness Threshold | Score >= 80% | {{readiness_gate}} |
| Family Coverage | All families have assessments | {{family_coverage_gate}} |
| **Overall Gate** | **All checks must PASS** | **{{gate_result}}** |

---

## 8. Recommendations

{{recommendations}}

### 8.1 Prioritized Remediation Plan

{{remediation_plan}}

---

## 9. Evidence References

The following evidence artifacts were collected during this assessment:

{{evidence_table}}

### 9.1 Evidence Coverage

- **Controls with Evidence:** {{controls_with_evidence}}
- **Controls without Evidence:** {{controls_without_evidence}}
- **Evidence Coverage Rate:** {{evidence_coverage_pct}}%

---

## 10. Assessor Information

| Field | Value |
|-------|-------|
| Assessor | {{assessor}} |
| Assessment Engine | ICDEV Compliance Engine v{{icdev_version}} |
| Assessment Date | {{assessment_date}} |
| Report Generated | {{generation_timestamp}} |
| Report Version | {{version}} |

---

## 11. Assessment Methodology

This assessment was conducted using the ICDEV FedRAMP Assessment Engine against the NIST SP 800-53 Rev 5 control catalog at the {{baseline}} baseline. Each control was evaluated for implementation status and supporting evidence.

**Scoring Formula:** Readiness Score = 100 x (satisfied + risk_accepted x 0.75) / (total - not_applicable)

**Gate Logic:** PASS if 0 "other_than_satisfied" critical controls (AC-2, IA-2, SC-7, AU-2, CM-6) AND readiness score >= 80%

**Applicable Standards:**
- NIST SP 800-53 Rev 5
- FedRAMP {{baseline}} Baseline
- NIST SP 800-37 Rev 2 (Risk Management Framework)
- OMB Circular A-130

---

**Prepared by:** {{assessor}}
**Date:** {{assessment_date}}

////////////////////////////////////////////////////////////////////
CUI // SP-CTI | Department of Defense
////////////////////////////////////////////////////////////////////
