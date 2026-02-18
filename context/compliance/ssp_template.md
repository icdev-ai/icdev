////////////////////////////////////////////////////////////////////
CONTROLLED UNCLASSIFIED INFORMATION (CUI) // SP-CTI
Distribution: Distribution D -- Authorized DoD Personnel Only
////////////////////////////////////////////////////////////////////

# SYSTEM SECURITY PLAN (SSP)
## Per NIST SP 800-18 Rev 1 / NIST SP 800-53 Rev 5

---

## 1. Information System Name / Title

**System Name:** {{system_name}}

**System Abbreviation:** {{system_abbreviation}}

**System Unique Identifier:** {{system_id}}

> Provide the unique name and identifier assigned to the information system. This name should be consistent across all documentation including the Authorization to Operate (ATO) package.

**Example:** "ICDEV Compliance Management Platform (ICDEV-CMP)"

---

## 2. Information System Categorization

**FIPS 199 Security Categorization:**

| Impact Area        | Level       |
|--------------------|-------------|
| Confidentiality    | {{confidentiality_impact}} |
| Integrity          | {{integrity_impact}} |
| Availability       | {{availability_impact}} |

**Overall System Categorization:** {{overall_categorization}}

**CUI Category:** {{cui_category}}

**CUI Designation Indicator:** {{cui_designation}}

> Categorize the system per FIPS 199 and CNSSI 1253. The highest watermark across all three security objectives determines the overall system categorization. Include CUI category per the CUI Registry.

**Example:** A system handling Controlled Technical Information (CTI) with Moderate confidentiality, Moderate integrity, and Low availability would be categorized as Moderate overall.

---

## 3. Information System Owner

**Name:** {{system_owner_name}}

**Title:** {{system_owner_title}}

**Organization:** {{system_owner_organization}}

**Address:** {{system_owner_address}}

**Email:** {{system_owner_email}}

**Phone:** {{system_owner_phone}}

> The Information System Owner is the official responsible for the overall procurement, development, integration, modification, or operation and maintenance of an information system.

---

## 4. Authorizing Official

**Name:** {{authorizing_official_name}}

**Title:** {{authorizing_official_title}}

**Organization:** {{authorizing_official_organization}}

**Email:** {{authorizing_official_email}}

**Phone:** {{authorizing_official_phone}}

> The Authorizing Official (AO) is a senior official with the authority to formally assume responsibility for operating an information system at an acceptable level of risk.

---

## 5. Other Designated Contacts

| Role | Name | Title | Email | Phone |
|------|------|-------|-------|-------|
| ISSM | {{issm_name}} | {{issm_title}} | {{issm_email}} | {{issm_phone}} |
| ISSO | {{isso_name}} | {{isso_title}} | {{isso_email}} | {{isso_phone}} |
| System Administrator | {{sysadmin_name}} | {{sysadmin_title}} | {{sysadmin_email}} | {{sysadmin_phone}} |
| Security Engineer | {{seceng_name}} | {{seceng_title}} | {{seceng_email}} | {{seceng_phone}} |

> List all key personnel with significant information security responsibilities for this system. Include at minimum the ISSM, ISSO, system administrator, and security engineering lead.

---

## 6. Assignment of Security Responsibility

**Information System Security Manager (ISSM):** {{issm_name}}

The ISSM is responsible for:
- Ensuring the day-to-day security posture of the information system
- Coordinating with the AO and system owner on security matters
- Managing the Plan of Action & Milestones (POA&M)
- Ensuring compliance with applicable security requirements

**Information System Security Officer (ISSO):** {{isso_name}}

The ISSO is responsible for:
- Implementing and enforcing the security policy for the information system
- Conducting routine security assessments and continuous monitoring
- Maintaining security documentation including this SSP
- Reporting security incidents per organizational policy

**Additional Security Responsibilities:**

{{additional_security_responsibilities}}

---

## 7. System Operational Status

**Current Status:** {{operational_status}}

- [ ] **Operational** -- The system is currently in production and operating
- [ ] **Under Development** -- The system is being designed or developed
- [ ] **Major Modification** -- The system is undergoing a significant change
- [ ] **Other** -- {{operational_status_other}}

**Operational Date:** {{operational_date}}

**Authorization Date:** {{authorization_date}}

**Authorization Termination Date:** {{authorization_termination_date}}

> Indicate the current operational status of the system. If the system is in multiple phases, check all that apply.

---

## 8. Information System Type

**System Type:** {{system_type}}

- [ ] **Major Application** -- A system that requires special attention to security due to the risk and magnitude of harm resulting from loss, misuse, or unauthorized access
- [ ] **General Support System** -- An interconnected set of information resources under the same direct management control sharing common functionality
- [ ] **Minor Application** -- A subsystem or application hosted on a general support system

**Cloud Service Model (if applicable):** {{cloud_service_model}}
- [ ] IaaS
- [ ] PaaS
- [ ] SaaS

**Cloud Deployment Model (if applicable):** {{cloud_deployment_model}}
- [ ] Government Community Cloud
- [ ] Private Cloud
- [ ] Hybrid Cloud

---

## 9. General System Description / Purpose

### 9.1 System Function or Purpose

{{system_purpose}}

> Provide a general description of the function or purpose of the system. Describe the business processes supported, types of data processed, and the user communities served.

**Example:** "The ICDEV Compliance Management Platform automates the generation and management of security compliance artifacts including System Security Plans, Plans of Action & Milestones, STIG checklists, and Software Bills of Materials for DoD software development projects."

### 9.2 Information Types Processed

| Information Type | NIST SP 800-60 Identifier | Confidentiality | Integrity | Availability |
|-----------------|---------------------------|-----------------|-----------|--------------|
| {{info_type_1_name}} | {{info_type_1_id}} | {{info_type_1_conf}} | {{info_type_1_int}} | {{info_type_1_avail}} |
| {{info_type_2_name}} | {{info_type_2_id}} | {{info_type_2_conf}} | {{info_type_2_int}} | {{info_type_2_avail}} |

### 9.3 System Users

| User Role | Internal/External | Privilege Level | Functions Performed |
|-----------|-------------------|-----------------|---------------------|
| {{user_role_1}} | {{user_type_1}} | {{user_priv_1}} | {{user_func_1}} |
| {{user_role_2}} | {{user_type_2}} | {{user_priv_2}} | {{user_func_2}} |

---

## 10. System Environment and Special Considerations

### 10.1 Hardware Inventory

| Component | Manufacturer | Model | Location | Purpose |
|-----------|-------------|-------|----------|---------|
| {{hw_component_1}} | {{hw_mfg_1}} | {{hw_model_1}} | {{hw_loc_1}} | {{hw_purpose_1}} |
| {{hw_component_2}} | {{hw_mfg_2}} | {{hw_model_2}} | {{hw_loc_2}} | {{hw_purpose_2}} |

### 10.2 Software Inventory

| Software | Version | Vendor | Purpose | License |
|----------|---------|--------|---------|---------|
| {{sw_name_1}} | {{sw_ver_1}} | {{sw_vendor_1}} | {{sw_purpose_1}} | {{sw_license_1}} |
| {{sw_name_2}} | {{sw_ver_2}} | {{sw_vendor_2}} | {{sw_purpose_2}} | {{sw_license_2}} |

### 10.3 Network Architecture

**Network Description:** {{network_description}}

**Ports, Protocols, and Services:**

| Port | Protocol | Service | Direction | Justification |
|------|----------|---------|-----------|---------------|
| {{port_1}} | {{proto_1}} | {{service_1}} | {{direction_1}} | {{justification_1}} |
| {{port_2}} | {{proto_2}} | {{service_2}} | {{direction_2}} | {{justification_2}} |

### 10.4 Physical Environment

**Primary Location:** {{primary_location}}

**Alternate Location:** {{alternate_location}}

**Physical Security Controls:** {{physical_security_description}}

---

## 11. System Interconnections / Information Sharing

| Interconnected System | Organization | Type | Authorization | Security Agreement | Agreement Date |
|-----------------------|-------------|------|---------------|--------------------|----|
| {{interconn_sys_1}} | {{interconn_org_1}} | {{interconn_type_1}} | {{interconn_auth_1}} | {{interconn_agreement_1}} | {{interconn_date_1}} |
| {{interconn_sys_2}} | {{interconn_org_2}} | {{interconn_type_2}} | {{interconn_auth_2}} | {{interconn_agreement_2}} | {{interconn_date_2}} |

**Types:** ISA (Interconnection Security Agreement), MOU/MOA (Memorandum of Understanding/Agreement), SLA (Service Level Agreement)

> Document all connections to external systems and the nature of information shared. Each interconnection should have a formal agreement in place.

---

## 12. Applicable Laws, Regulations, and Standards

| Law / Regulation / Standard | Description | Applicability |
|-----------------------------|-------------|---------------|
| FISMA | Federal Information Security Modernization Act | {{fisma_applicability}} |
| NIST SP 800-53 Rev 5 | Security and Privacy Controls | {{nist_800_53_applicability}} |
| NIST SP 800-171 Rev 2 | Protecting CUI in Nonfederal Systems | {{nist_800_171_applicability}} |
| DFARS 252.204-7012 | Safeguarding Covered Defense Information | {{dfars_applicability}} |
| CMMC 2.0 | Cybersecurity Maturity Model Certification | {{cmmc_applicability}} |
| DoD CUI Program | DoD Instruction 5200.48 | {{dod_cui_applicability}} |
| FedRAMP | Federal Risk and Authorization Management Program | {{fedramp_applicability}} |
| {{additional_law_1}} | {{additional_law_1_desc}} | {{additional_law_1_applicability}} |

> List all federal laws, directives, regulations, policies, and standards that apply to this system. Include specific DFARS clauses for contractor systems.

---

## 13. Minimum Security Controls

**Applicable Control Baseline:** {{control_baseline}} (per NIST SP 800-53 Rev 5)

**Impact Level:** {{impact_level}}

**Total Controls Required:** {{total_controls_required}}

**Controls Implemented:** {{controls_implemented}}

**Controls Planned:** {{controls_planned}}

**Controls Not Applicable:** {{controls_not_applicable}}

### Control Family Summary

| Family | Code | Total | Implemented | Planned | N/A |
|--------|------|-------|-------------|---------|-----|
| Access Control | AC | {{ac_total}} | {{ac_implemented}} | {{ac_planned}} | {{ac_na}} |
| Audit and Accountability | AU | {{au_total}} | {{au_implemented}} | {{au_planned}} | {{au_na}} |
| Configuration Management | CM | {{cm_total}} | {{cm_implemented}} | {{cm_planned}} | {{cm_na}} |
| Identification and Authentication | IA | {{ia_total}} | {{ia_implemented}} | {{ia_planned}} | {{ia_na}} |
| System and Communications Protection | SC | {{sc_total}} | {{sc_implemented}} | {{sc_planned}} | {{sc_na}} |
| System and Services Acquisition | SA | {{sa_total}} | {{sa_implemented}} | {{sa_planned}} | {{sa_na}} |
| Risk Assessment | RA | {{ra_total}} | {{ra_implemented}} | {{ra_planned}} | {{ra_na}} |
| Assessment, Authorization, Monitoring | CA | {{ca_total}} | {{ca_implemented}} | {{ca_planned}} | {{ca_na}} |

> The minimum security controls are determined by the system categorization (Section 2). See Section 15 for detailed control implementation statements.

---

## 14. Information System Security Plan Approval Date

**Plan Prepared By:** {{plan_prepared_by}}

**Date Prepared:** {{date_prepared}}

**Plan Approved By:** {{plan_approved_by}}

**Date Approved:** {{date_approved}}

**Next Review Date:** {{next_review_date}}

**Document Version:** {{document_version}}

| Version | Date | Author | Description of Changes |
|---------|------|--------|----------------------|
| {{version_1}} | {{version_1_date}} | {{version_1_author}} | {{version_1_changes}} |
| {{version_2}} | {{version_2_date}} | {{version_2_author}} | {{version_2_changes}} |

---

## 15. Security Control Implementation

> For each applicable control, provide an implementation statement describing how the control is satisfied. Include the control identifier, title, implementation status, responsible role, and a narrative description of how the control is implemented within the system boundary.

{{control_implementations}}

### Control Implementation Template

For each control, the following information is documented:

---

#### {{control_id}}: {{control_title}}

**Implementation Status:** {{control_status}}

**Responsible Role:** {{control_responsible_role}}

**Implementation Description:**

{{control_implementation_description}}

**Evidence / Artifacts:**

{{control_evidence}}

---

## 16. Continuous Monitoring Strategy

### 16.1 Monitoring Overview

**Monitoring Approach:** {{monitoring_approach}}

**Monitoring Tools:**

| Tool | Purpose | Frequency | Owner |
|------|---------|-----------|-------|
| {{mon_tool_1}} | {{mon_purpose_1}} | {{mon_freq_1}} | {{mon_owner_1}} |
| {{mon_tool_2}} | {{mon_purpose_2}} | {{mon_freq_2}} | {{mon_owner_2}} |

### 16.2 Ongoing Authorization Activities

| Activity | Frequency | Responsible Party | Description |
|----------|-----------|-------------------|-------------|
| Vulnerability Scanning | {{vuln_scan_freq}} | {{vuln_scan_owner}} | {{vuln_scan_desc}} |
| STIG Compliance Check | {{stig_check_freq}} | {{stig_check_owner}} | {{stig_check_desc}} |
| Configuration Audit | {{config_audit_freq}} | {{config_audit_owner}} | {{config_audit_desc}} |
| Penetration Testing | {{pentest_freq}} | {{pentest_owner}} | {{pentest_desc}} |
| SBOM Review | {{sbom_review_freq}} | {{sbom_review_owner}} | {{sbom_review_desc}} |
| POA&M Review | {{poam_review_freq}} | {{poam_review_owner}} | {{poam_review_desc}} |

### 16.3 Incident Response

**Incident Response Plan Reference:** {{ir_plan_reference}}

**Incident Reporting POC:** {{ir_poc}}

**Reporting Timeline:** {{ir_timeline}}

### 16.4 Metrics and Reporting

| Metric | Target | Current | Frequency |
|--------|--------|---------|-----------|
| Vulnerability Remediation Time (Critical) | {{vuln_crit_target}} | {{vuln_crit_current}} | {{vuln_crit_freq}} |
| STIG Compliance Rate | {{stig_compliance_target}} | {{stig_compliance_current}} | {{stig_compliance_freq}} |
| POA&M Closure Rate | {{poam_closure_target}} | {{poam_closure_current}} | {{poam_closure_freq}} |
| System Availability | {{availability_target}} | {{availability_current}} | {{availability_freq}} |

---

## 17. Appendices

### Appendix A: Acronyms and Abbreviations

| Acronym | Definition |
|---------|------------|
| AO | Authorizing Official |
| ATO | Authorization to Operate |
| CUI | Controlled Unclassified Information |
| CTI | Controlled Technical Information |
| DFARS | Defense Federal Acquisition Regulation Supplement |
| FIPS | Federal Information Processing Standards |
| FISMA | Federal Information Security Modernization Act |
| ISA | Interconnection Security Agreement |
| ISSM | Information System Security Manager |
| ISSO | Information System Security Officer |
| NIST | National Institute of Standards and Technology |
| POA&M | Plan of Action & Milestones |
| SBOM | Software Bill of Materials |
| SSP | System Security Plan |
| STIG | Security Technical Implementation Guide |

### Appendix B: Referenced Documents

| Document | Version | Date |
|----------|---------|------|
| {{ref_doc_1}} | {{ref_doc_1_version}} | {{ref_doc_1_date}} |
| {{ref_doc_2}} | {{ref_doc_2_version}} | {{ref_doc_2_date}} |

### Appendix C: System Architecture Diagrams

{{architecture_diagrams}}

### Appendix D: Network Diagrams

{{network_diagrams}}

### Appendix E: Data Flow Diagrams

{{data_flow_diagrams}}

### Appendix F: POA&M Reference

**Current POA&M Location:** {{poam_location}}

**Open Items:** {{poam_open_count}}

**Overdue Items:** {{poam_overdue_count}}

---

**Document Classification:** {{classification}}

**Generated by:** ICDEV Compliance Engine v{{icdev_version}}

**Generated on:** {{generation_date}}

////////////////////////////////////////////////////////////////////
CUI // SP-CTI | Department of Defense
////////////////////////////////////////////////////////////////////
