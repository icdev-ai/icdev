# CUI // SP-CTI
"""Fixtures for template-chunking acceptance (oss-chunk-03).

Checked in and static, so the 1:1 assertions are reproducible. Shapes follow
real exports: NIST SP 800-53 control text and a DISA STIG checklist, including
the awkward parts that make naive splitting fail — control ENHANCEMENTS
(``AC-2 (1)``) that must stay with their parent control's chunk boundary rather
than starting a spurious one, and multi-paragraph Check/Fix text inside a rule.
"""

#: 8 controls, two of them with enhancements. A correct oscal_catalog chunking
#: yields exactly 8 chunks — one per control, enhancements folded in.
OSCAL_CATALOG = """# NIST SP 800-53 Rev 5 — selected controls

AC-1 Policy and Procedures
The organization develops, documents, and disseminates an access control policy
that addresses purpose, scope, roles, responsibilities, management commitment,
coordination among organizational entities, and compliance.

AC-2 Account Management
The organization manages information system accounts, including establishing
conditions for group and role membership, and specifying authorized users.

AC-2 (1) Automated System Account Management
The organization employs automated mechanisms to support the management of
information system accounts.

AC-3 Access Enforcement
The information system enforces approved authorizations for logical access to
information and system resources.

AU-2 Event Logging
The organization identifies the types of events that the system is capable of
logging in support of the audit function.

AU-6 Audit Record Review, Analysis, and Reporting
The organization reviews and analyzes information system audit records for
indications of inappropriate or unusual activity.

AU-6 (3) Correlate Audit Record Repositories
The organization analyzes and correlates audit records across different
repositories to gain organization-wide situational awareness.

SC-7 Boundary Protection
The information system monitors and controls communications at the external
boundary of the system and at key internal boundaries.

SI-4 System Monitoring
The organization monitors the information system to detect attacks and
indicators of potential attacks.
"""

#: Distinct control identifiers a correct chunking must not split apart.
OSCAL_CONTROL_IDS = ["AC-1", "AC-2", "AC-3", "AU-2", "AU-6", "SC-7", "SI-4"]

#: 5 rules with multi-paragraph Check/Fix text. Correct stig_checklist chunking
#: yields exactly 5 chunks — one per rule.
STIG_CHECKLIST = """DISA STIG — Application Security and Development

V-222387
Severity: CAT I
Rule Title: The application must enforce approved authorizations.
Discussion: Access control policies control access between active entities and
passive entities.
Check Text: Review the application documentation. If the application does not
enforce approved authorizations, this is a finding.
Fix Text: Configure the application to enforce approved authorizations.

V-222388
Severity: CAT II
Rule Title: The application must use multifactor authentication.
Discussion: Multifactor authentication reduces the risk of compromised
credentials being used to access the application.
Check Text: Review the authentication configuration. If MFA is not enabled for
privileged accounts, this is a finding.
Fix Text: Enable multifactor authentication for all privileged accounts.

V-222389
Severity: CAT II
Rule Title: The application must generate audit records.
Discussion: Without audit records it is impossible to establish, correlate, and
investigate events.
Check Text: Verify the application generates audit records for all auditable
events. If it does not, this is a finding.
Fix Text: Configure audit record generation for all required event types.

V-222390
Severity: CAT III
Rule Title: The application must protect audit information from unauthorized
modification.
Discussion: Audit information that is modified loses its evidentiary value.
Check Text: Review the audit file permissions. If unauthorized users can modify
audit records, this is a finding.
Fix Text: Restrict audit record permissions to authorized personnel only.

V-222391
Severity: CAT I
Rule Title: The application must not contain hard-coded credentials.
Discussion: Hard-coded credentials cannot be rotated and are visible to anyone
with source access.
Check Text: Scan the source for embedded credentials. If any are present, this
is a finding.
Fix Text: Move all credentials to the configured secret store.
"""

STIG_RULE_IDS = ["V-222387", "V-222388", "V-222389", "V-222390", "V-222391"]

#: A general document with no structure to exploit. Chunking this must be
#: byte-identical to the pre-template sliding window — that is what "no
#: regression on general documents" means.
GENERAL_PROSE = (
    "The system shall maintain continuous monitoring of security controls. "
    "Continuous monitoring provides ongoing awareness of threats and "
    "vulnerabilities to support organizational risk management decisions. "
) * 60
