# [CUI // SP-CTI]
"""ICDEV™ Security Design Canvas — Incident Response Runbooks.

Pre-built IR playbooks for common incident types, aligned to NIST 800-53
IR/CP/SC control families.  Each runbook follows the standard five-phase
IR lifecycle: Detect → Contain → Eradicate → Recover → Lessons Learned.
"""

from __future__ import annotations

INCIDENT_RUNBOOKS: list[dict] = [
    # ── 1. Credential Compromise ──────────────────────────────────────────
    {
        "id": "IR-CRED-001",
        "title": "Credential Compromise",
        "severity": "CAT1",
        "nist_controls": ["IR-4", "IR-6", "IA-5"],
        "description": (
            "Response playbook for compromised user or service credentials "
            "including password theft, token hijacking, and key exposure."
        ),
        "phases": [
            {
                "name": "Detect",
                "steps": [
                    "Check for unusual failed-login spike across identity providers",
                    "Identify impossible-travel login anomalies via SIEM",
                    "Detect token reuse or replay from unexpected source IPs",
                ],
                "max_duration": "1h",
                "automated": True,
            },
            {
                "name": "Contain",
                "steps": [
                    "Disable compromised account(s) in IdP",
                    "Revoke all active tokens and OAuth grants",
                    "Block offending IP addresses at WAF / network perimeter",
                ],
                "max_duration": "2h",
                "automated": True,
            },
            {
                "name": "Eradicate",
                "steps": [
                    "Force credential reset for all affected accounts",
                    "Rotate API keys, service-account secrets, and SSH keys",
                    "Audit all sessions created during compromise window",
                ],
                "max_duration": "4h",
                "automated": False,
            },
            {
                "name": "Recover",
                "steps": [
                    "Re-enable accounts with mandatory MFA enrollment",
                    "Monitor re-enabled accounts for 72 hours",
                    "Verify no lateral movement occurred during compromise",
                ],
                "max_duration": "72h",
                "automated": False,
            },
            {
                "name": "Lessons Learned",
                "steps": [
                    "Update authentication policy to address root cause",
                    "Add new detection rules derived from compromise indicators",
                    "Conduct post-incident review and update IR documentation",
                ],
                "max_duration": "5d",
                "automated": False,
            },
        ],
        "estimated_duration": "3-5 days",
        "required_roles": [
            "Incident Commander",
            "Identity & Access Engineer",
            "SOC Analyst",
            "ISSO",
        ],
        "escalation_threshold": (
            "Escalate to CISO if more than 10 accounts compromised or any privileged account is affected"
        ),
    },
    # ── 2. Ransomware ─────────────────────────────────────────────────────
    {
        "id": "IR-RANSOM-001",
        "title": "Ransomware",
        "severity": "CAT1",
        "nist_controls": ["IR-4", "CP-10", "SI-7"],
        "description": (
            "Response playbook for ransomware incidents including file "
            "encryption, ransom demands, and command-and-control activity."
        ),
        "phases": [
            {
                "name": "Detect",
                "steps": [
                    "Identify file-encryption patterns (mass rename, entropy spike)",
                    "Detect ransom-note file creation on endpoints",
                    "Correlate C2 beaconing via network traffic analysis",
                ],
                "max_duration": "1h",
                "automated": True,
            },
            {
                "name": "Contain",
                "steps": [
                    "Isolate affected hosts from the network immediately",
                    "Block lateral-movement protocols (SMB, RDP, WMI)",
                    "Preserve forensic disk image before remediation",
                ],
                "max_duration": "2h",
                "automated": True,
            },
            {
                "name": "Eradicate",
                "steps": [
                    "Identify initial entry vector (phishing, exploit, RDP)",
                    "Scan all endpoints for ransomware artifacts and persistence",
                    "Remove persistence mechanisms (scheduled tasks, registry keys)",
                ],
                "max_duration": "24h",
                "automated": False,
            },
            {
                "name": "Recover",
                "steps": [
                    "Restore systems from verified clean backups",
                    "Verify data integrity with checksums before reconnection",
                    "Perform staged reconnection of recovered hosts",
                ],
                "max_duration": "48h",
                "automated": False,
            },
            {
                "name": "Lessons Learned",
                "steps": [
                    "Patch the entry vector that enabled initial compromise",
                    "Update SIEM rules with new ransomware indicators",
                    "Schedule tabletop exercise based on this incident",
                ],
                "max_duration": "5d",
                "automated": False,
            },
        ],
        "estimated_duration": "5-7 days",
        "required_roles": [
            "Incident Commander",
            "Forensic Analyst",
            "Endpoint Engineer",
            "Backup Administrator",
            "CISO",
        ],
        "escalation_threshold": (
            "Immediate CISO notification; escalate to law enforcement "
            "if ransom demand received or PII potentially affected"
        ),
    },
    # ── 3. DDoS Attack ────────────────────────────────────────────────────
    {
        "id": "IR-DDOS-001",
        "title": "DDoS Attack",
        "severity": "CAT2",
        "nist_controls": ["SC-5", "CP-7", "CP-8"],
        "description": (
            "Response playbook for distributed denial-of-service attacks "
            "including volumetric, protocol, and application-layer floods."
        ),
        "phases": [
            {
                "name": "Detect",
                "steps": [
                    "Identify traffic spike exceeding baseline thresholds",
                    "Detect geographic anomaly in request origins",
                    "Analyze protocol anomalies (SYN flood, UDP amplification)",
                ],
                "max_duration": "30m",
                "automated": True,
            },
            {
                "name": "Contain",
                "steps": [
                    "Enable WAF rate-limiting rules for affected endpoints",
                    "Activate CDN scrubbing / DDoS mitigation service",
                    "Configure blackhole routes for confirmed attack sources",
                ],
                "max_duration": "1h",
                "automated": True,
            },
            {
                "name": "Eradicate",
                "steps": [
                    "Identify specific attack vector and traffic signature",
                    "Update firewall ACLs with targeted block rules",
                    "Coordinate with upstream ISP for source-side filtering",
                ],
                "max_duration": "4h",
                "automated": False,
            },
            {
                "name": "Recover",
                "steps": [
                    "Restore normal routing and remove emergency blackholes",
                    "Validate service health and response latency",
                    "Update capacity plan based on observed attack volume",
                ],
                "max_duration": "4h",
                "automated": False,
            },
            {
                "name": "Lessons Learned",
                "steps": [
                    "Update DDoS playbook with new attack signatures",
                    "Add auto-scaling triggers for observed traffic patterns",
                    "Review ISP/CDN SLAs against actual mitigation performance",
                ],
                "max_duration": "3d",
                "automated": False,
            },
        ],
        "estimated_duration": "1-3 days",
        "required_roles": [
            "Incident Commander",
            "Network Engineer",
            "SOC Analyst",
            "Cloud/CDN Administrator",
        ],
        "escalation_threshold": (
            "Escalate to CISO if service unavailability exceeds 30 minutes "
            "or attack volume exceeds CDN scrubbing capacity"
        ),
    },
    # ── 4. Data Breach / Exfiltration ─────────────────────────────────────
    {
        "id": "IR-BREACH-001",
        "title": "Data Breach / Exfiltration",
        "severity": "CAT1",
        "nist_controls": ["IR-4", "IR-6", "SI-4", "AU-6"],
        "description": (
            "Response playbook for confirmed or suspected data exfiltration "
            "including unauthorized data transfers, DLP alerts, and insider threats."
        ),
        "phases": [
            {
                "name": "Detect",
                "steps": [
                    "Identify unusual egress volume or data-transfer patterns",
                    "Correlate DLP alerts across email, cloud storage, endpoints",
                    "Detect access anomalies to sensitive data repositories",
                ],
                "max_duration": "1h",
                "automated": True,
            },
            {
                "name": "Contain",
                "steps": [
                    "Block identified egress paths (URLs, IPs, cloud shares)",
                    "Revoke access for suspected accounts and service principals",
                    "Enable enhanced audit logging on affected data stores",
                ],
                "max_duration": "2h",
                "automated": True,
            },
            {
                "name": "Eradicate",
                "steps": [
                    "Identify the exfiltration channel and method",
                    "Assess scope of data exposed (records, classification)",
                    "Collect and preserve forensic evidence chain-of-custody",
                ],
                "max_duration": "24h",
                "automated": False,
            },
            {
                "name": "Recover",
                "steps": [
                    "Notify affected parties per breach notification requirements",
                    "Engage legal counsel and public relations if required",
                    "Remediate access controls and close exfiltration vector",
                ],
                "max_duration": "48h",
                "automated": False,
            },
            {
                "name": "Lessons Learned",
                "steps": [
                    "Update DLP policies to cover identified exfil method",
                    "Enhance monitoring rules for data-access anomalies",
                    "Conduct formal breach review and update IR plan",
                ],
                "max_duration": "5d",
                "automated": False,
            },
        ],
        "estimated_duration": "5-10 days",
        "required_roles": [
            "Incident Commander",
            "Forensic Analyst",
            "Data Protection Officer",
            "Legal Counsel",
            "CISO",
        ],
        "escalation_threshold": (
            "Immediate CISO and legal notification; escalate to regulatory "
            "bodies if PII/PHI of more than 500 individuals is affected"
        ),
    },
    # ── 5. Insider Threat ────────────────────────────────────────────────────
    {
        "id": "IR-INSIDER-001",
        "title": "Insider Threat",
        "severity": "CAT1",
        "nist_controls": ["IR-4", "AC-2", "AU-6", "PS-4"],
        "description": (
            "Response playbook for insider threat incidents including "
            "unauthorized data access, policy violations, and malicious insider activity."
        ),
        "phases": [
            {
                "name": "Detect",
                "steps": [
                    "Identify unusual data access patterns or off-hours activity",
                    "Detect bulk downloads or access to unrelated systems",
                    "Correlate UBA alerts with HR and access control logs",
                ],
                "max_duration": "2h",
                "automated": True,
            },
            {
                "name": "Contain",
                "steps": [
                    "Restrict account scope to minimum necessary access",
                    "Enable enhanced monitoring on suspected accounts",
                    "Preserve session logs and access records",
                ],
                "max_duration": "4h",
                "automated": True,
            },
            {
                "name": "Eradicate",
                "steps": [
                    "Disable compromised or malicious accounts",
                    "Revoke all tokens and active sessions",
                    "Audit all data accessed during the incident window",
                ],
                "max_duration": "24h",
                "automated": False,
            },
            {
                "name": "Recover",
                "steps": [
                    "Review and re-baseline least-privilege assignments",
                    "Enable UBA monitoring on all high-risk accounts",
                    "Update access policies and role definitions",
                ],
                "max_duration": "48h",
                "automated": False,
            },
            {
                "name": "Lessons Learned",
                "steps": [
                    "Update insider threat program and detection rules",
                    "Schedule mandatory security awareness training",
                    "Enhance DLP rules based on observed exfiltration methods",
                ],
                "max_duration": "5d",
                "automated": False,
            },
        ],
        "estimated_duration": "5-7 days",
        "required_roles": [
            "SOC Analyst",
            "ISSO",
            "System Admin",
            "HR Liaison",
        ],
        "escalation_threshold": ("If not contained within 2 hours, escalate to CISO"),
    },
    # ── 6. Supply Chain Compromise ───────────────────────────────────────────
    {
        "id": "IR-SUPPLY-001",
        "title": "Supply Chain Compromise",
        "severity": "CAT1",
        "nist_controls": ["SR-2", "SR-3", "SI-7", "SA-12"],
        "description": (
            "Response playbook for supply chain compromise including "
            "dependency tampering, malicious packages, and SBOM drift."
        ),
        "phases": [
            {
                "name": "Detect",
                "steps": [
                    "Identify dependency hash mismatch against known-good baseline",
                    "Detect unexpected network calls from build artifacts",
                    "Monitor SBOM drift and new CVEs in dependencies",
                ],
                "max_duration": "1h",
                "automated": True,
            },
            {
                "name": "Contain",
                "steps": [
                    "Pin all dependencies to known-good versions",
                    "Block suspicious packages at registry level",
                    "Isolate affected build pipelines and artifacts",
                ],
                "max_duration": "2h",
                "automated": True,
            },
            {
                "name": "Eradicate",
                "steps": [
                    "Audit all direct and transitive dependencies",
                    "Rebuild all artifacts from verified source code",
                    "Update SBOM with verified component hashes",
                ],
                "max_duration": "24h",
                "automated": False,
            },
            {
                "name": "Recover",
                "steps": [
                    "Deploy clean builds to all environments",
                    "Verify binary integrity with checksums and signatures",
                    "Notify downstream consumers of affected artifacts",
                ],
                "max_duration": "48h",
                "automated": False,
            },
            {
                "name": "Lessons Learned",
                "steps": [
                    "Implement SLSA provenance for all build artifacts",
                    "Add dependency review gates to CI/CD pipeline",
                    "Establish registry allowlisting policy",
                ],
                "max_duration": "5d",
                "automated": False,
            },
        ],
        "estimated_duration": "5-7 days",
        "required_roles": [
            "SOC Analyst",
            "ISSO",
            "System Admin",
            "DevSecOps Engineer",
        ],
        "escalation_threshold": ("If not contained within 2 hours, escalate to CISO"),
    },
    # ── 7. Zero-Day Exploit ──────────────────────────────────────────────────
    {
        "id": "IR-ZERODAY-001",
        "title": "Zero-Day Exploit",
        "severity": "CAT1",
        "nist_controls": ["IR-4", "RA-5", "SI-2", "CA-7"],
        "description": (
            "Response playbook for zero-day exploit incidents including "
            "unpatched vulnerability exploitation and unknown attack vectors."
        ),
        "phases": [
            {
                "name": "Detect",
                "steps": [
                    "Identify IDS/IPS anomalies indicating exploitation",
                    "Detect unexpected process behavior or memory corruption",
                    "Correlate indicators of compromise across SIEM",
                ],
                "max_duration": "1h",
                "automated": True,
            },
            {
                "name": "Contain",
                "steps": [
                    "Isolate affected systems from the network",
                    "Apply virtual patching via WAF rules",
                    "Restrict network access to affected services",
                ],
                "max_duration": "2h",
                "automated": True,
            },
            {
                "name": "Eradicate",
                "steps": [
                    "Apply vendor patch when available",
                    "Scan all systems for indicators of compromise",
                    "Remove any backdoors or persistence mechanisms",
                ],
                "max_duration": "24h",
                "automated": False,
            },
            {
                "name": "Recover",
                "steps": [
                    "Restore affected systems from known-good state",
                    "Verify patch deployment across all instances",
                    "Enhance monitoring for exploitation indicators",
                ],
                "max_duration": "48h",
                "automated": False,
            },
            {
                "name": "Lessons Learned",
                "steps": [
                    "Improve patch management SLA for critical vulnerabilities",
                    "Add compensating controls for unpatched systems",
                    "Update threat model with new attack vectors",
                ],
                "max_duration": "5d",
                "automated": False,
            },
        ],
        "estimated_duration": "3-7 days",
        "required_roles": [
            "SOC Analyst",
            "ISSO",
            "System Admin",
            "Threat Intelligence Analyst",
        ],
        "escalation_threshold": ("If not contained within 2 hours, escalate to CISO"),
    },
    # ── 8. Phishing Campaign ─────────────────────────────────────────────────
    {
        "id": "IR-PHISH-001",
        "title": "Phishing Campaign",
        "severity": "CAT2",
        "nist_controls": ["IR-4", "AT-2", "IA-5", "SI-8"],
        "description": (
            "Response playbook for phishing campaigns including "
            "credential harvesting, malicious attachments, and BEC attacks."
        ),
        "phases": [
            {
                "name": "Detect",
                "steps": [
                    "Collect user reports and email gateway alerts",
                    "Detect credential reuse or impossible travel anomalies",
                    "Identify phishing domains and malicious URLs",
                ],
                "max_duration": "1h",
                "automated": True,
            },
            {
                "name": "Contain",
                "steps": [
                    "Block phishing domains at DNS and proxy level",
                    "Quarantine phishing emails across all mailboxes",
                    "Force password resets for affected users",
                ],
                "max_duration": "2h",
                "automated": True,
            },
            {
                "name": "Eradicate",
                "steps": [
                    "Remove phishing emails from all inboxes (clawback)",
                    "Revoke compromised tokens and OAuth grants",
                    "Scan affected endpoints for malware payloads",
                ],
                "max_duration": "8h",
                "automated": False,
            },
            {
                "name": "Recover",
                "steps": [
                    "Re-enable accounts with mandatory MFA enforcement",
                    "Monitor for credential stuffing attempts",
                    "Verify no lateral movement from compromised accounts",
                ],
                "max_duration": "48h",
                "automated": False,
            },
            {
                "name": "Lessons Learned",
                "steps": [
                    "Update email filtering rules with new indicators",
                    "Conduct targeted phishing simulation exercise",
                    "Update security awareness training materials",
                ],
                "max_duration": "5d",
                "automated": False,
            },
        ],
        "estimated_duration": "3-5 days",
        "required_roles": [
            "SOC Analyst",
            "ISSO",
            "System Admin",
            "Email Administrator",
        ],
        "escalation_threshold": ("If not contained within 2 hours, escalate to CISO"),
    },
    # ── 9. Cloud Misconfiguration ────────────────────────────────────────────
    {
        "id": "IR-CLOUDCONFIG-001",
        "title": "Cloud Misconfiguration",
        "severity": "CAT1",
        "nist_controls": ["CM-2", "CM-6", "SC-7", "AC-3"],
        "description": (
            "Response playbook for cloud misconfiguration incidents including "
            "public exposure, overly permissive IAM, and missing encryption."
        ),
        "phases": [
            {
                "name": "Detect",
                "steps": [
                    "Identify CSPM alerts for policy violations",
                    "Detect public exposure via external scanner",
                    "Correlate GuardDuty/Defender findings with asset inventory",
                ],
                "max_duration": "1h",
                "automated": True,
            },
            {
                "name": "Contain",
                "steps": [
                    "Apply restrictive policy on exposed resource immediately",
                    "Enable logging and monitoring on affected resources",
                    "Block public access at network and IAM level",
                ],
                "max_duration": "2h",
                "automated": True,
            },
            {
                "name": "Eradicate",
                "steps": [
                    "Fix configuration to match security baseline",
                    "Scan for data exposure during misconfiguration window",
                    "Audit access logs for unauthorized access",
                ],
                "max_duration": "8h",
                "automated": False,
            },
            {
                "name": "Recover",
                "steps": [
                    "Verify configuration drift is fully resolved",
                    "Update IaC templates to prevent recurrence",
                    "Deploy preventive guardrails (SCPs, Azure Policy)",
                ],
                "max_duration": "24h",
                "automated": False,
            },
            {
                "name": "Lessons Learned",
                "steps": [
                    "Implement preventive SCPs/policies for common misconfigs",
                    "Add pre-deploy IaC scanning to CI/CD pipeline",
                    "Tune CSPM rules to reduce false negatives",
                ],
                "max_duration": "3d",
                "automated": False,
            },
        ],
        "estimated_duration": "2-4 days",
        "required_roles": [
            "SOC Analyst",
            "ISSO",
            "System Admin",
            "Cloud Security Engineer",
        ],
        "escalation_threshold": ("If not contained within 2 hours, escalate to CISO"),
    },
    # ── 10. API Abuse / Injection ────────────────────────────────────────────
    {
        "id": "IR-APIABUSE-001",
        "title": "API Abuse / Injection",
        "severity": "CAT1",
        "nist_controls": ["SI-10", "SC-7", "AU-6", "IR-4"],
        "description": (
            "Response playbook for API abuse and injection attacks including "
            "rate limit bypass, SQL/command injection, and schema violations."
        ),
        "phases": [
            {
                "name": "Detect",
                "steps": [
                    "Identify rate limit breaches and anomalous request volumes",
                    "Detect injection patterns in API request logs",
                    "Correlate anomalous API response codes with client IPs",
                ],
                "max_duration": "30m",
                "automated": True,
            },
            {
                "name": "Contain",
                "steps": [
                    "Enable strict rate limiting on affected endpoints",
                    "Block offending IPs and revoke abused API tokens",
                    "Activate WAF injection-prevention rules",
                ],
                "max_duration": "1h",
                "automated": True,
            },
            {
                "name": "Eradicate",
                "steps": [
                    "Patch vulnerable API endpoints",
                    "Add input validation and parameterized queries",
                    "Update API schema validation rules",
                ],
                "max_duration": "8h",
                "automated": False,
            },
            {
                "name": "Recover",
                "steps": [
                    "Rotate all compromised API keys",
                    "Notify affected API consumers",
                    "Restore normal rate limiting thresholds",
                ],
                "max_duration": "24h",
                "automated": False,
            },
            {
                "name": "Lessons Learned",
                "steps": [
                    "Implement API security testing in CI/CD pipeline",
                    "Add OpenAPI schema validation for all endpoints",
                    "Enhance WAF rules with observed attack patterns",
                ],
                "max_duration": "3d",
                "automated": False,
            },
        ],
        "estimated_duration": "2-4 days",
        "required_roles": [
            "SOC Analyst",
            "ISSO",
            "System Admin",
            "API Security Engineer",
        ],
        "escalation_threshold": ("If not contained within 2 hours, escalate to CISO"),
    },
    # ── 11. Privilege Escalation ─────────────────────────────────────────────
    {
        "id": "IR-PRIVESC-001",
        "title": "Privilege Escalation",
        "severity": "CAT1",
        "nist_controls": ["AC-6", "AC-2", "AU-6", "IR-4"],
        "description": (
            "Response playbook for privilege escalation incidents including "
            "unauthorized admin access, lateral movement, and RBAC bypass."
        ),
        "phases": [
            {
                "name": "Detect",
                "steps": [
                    "Identify unexpected admin actions or role changes",
                    "Detect new admin account creation outside change windows",
                    "Correlate lateral movement indicators across SIEM",
                ],
                "max_duration": "1h",
                "automated": True,
            },
            {
                "name": "Contain",
                "steps": [
                    "Disable escalated accounts immediately",
                    "Isolate affected systems from the network",
                    "Enable break-glass monitoring on all admin sessions",
                ],
                "max_duration": "2h",
                "automated": True,
            },
            {
                "name": "Eradicate",
                "steps": [
                    "Remove all unauthorized privileges and roles",
                    "Patch the escalation vector (CVE, misconfig, etc.)",
                    "Audit all admin accounts across all systems",
                ],
                "max_duration": "24h",
                "automated": False,
            },
            {
                "name": "Recover",
                "steps": [
                    "Re-baseline RBAC/IAM to least-privilege",
                    "Deploy PAM enforcement for all privileged access",
                    "Enable just-in-time access for admin operations",
                ],
                "max_duration": "48h",
                "automated": False,
            },
            {
                "name": "Lessons Learned",
                "steps": [
                    "Implement regular least-privilege review cadence",
                    "Add PAM session recording for all admin access",
                    "Update SIEM rules for privilege escalation patterns",
                ],
                "max_duration": "5d",
                "automated": False,
            },
        ],
        "estimated_duration": "3-5 days",
        "required_roles": [
            "SOC Analyst",
            "ISSO",
            "System Admin",
            "Identity & Access Engineer",
        ],
        "escalation_threshold": ("If not contained within 2 hours, escalate to CISO"),
    },
    # ── 12. Data Integrity Violation ─────────────────────────────────────────
    {
        "id": "IR-INTEGRITY-001",
        "title": "Data Integrity Violation",
        "severity": "CAT1",
        "nist_controls": ["SI-7", "AU-10", "SC-28", "IR-4"],
        "description": (
            "Response playbook for data integrity violations including "
            "unauthorized data modification, audit log gaps, and hash mismatches."
        ),
        "phases": [
            {
                "name": "Detect",
                "steps": [
                    "Identify hash mismatch on critical data or configurations",
                    "Detect unexpected schema changes or data modifications",
                    "Correlate audit log gaps with system access events",
                ],
                "max_duration": "1h",
                "automated": True,
            },
            {
                "name": "Contain",
                "steps": [
                    "Switch affected systems to read-only mode",
                    "Preserve current state for forensic analysis",
                    "Enable enhanced audit logging on all data stores",
                ],
                "max_duration": "2h",
                "automated": True,
            },
            {
                "name": "Eradicate",
                "steps": [
                    "Identify the tampering source and method",
                    "Restore data from verified backup with integrity check",
                    "Validate data integrity across all affected stores",
                ],
                "max_duration": "24h",
                "automated": False,
            },
            {
                "name": "Recover",
                "steps": [
                    "Rebuild trust anchors and verification checksums",
                    "Deploy file integrity monitoring on all critical data",
                    "Verify all checksums against known-good baseline",
                ],
                "max_duration": "48h",
                "automated": False,
            },
            {
                "name": "Lessons Learned",
                "steps": [
                    "Implement immutable audit trail for all critical data",
                    "Add database activity monitoring with alerting",
                    "Hash all critical data with scheduled integrity checks",
                ],
                "max_duration": "5d",
                "automated": False,
            },
        ],
        "estimated_duration": "3-7 days",
        "required_roles": [
            "SOC Analyst",
            "ISSO",
            "System Admin",
            "Database Administrator",
        ],
        "escalation_threshold": ("If not contained within 2 hours, escalate to CISO"),
    },
]

# ── Lookup index (built once at import time) ──────────────────────────────────
_RUNBOOK_INDEX: dict[str, dict] = {rb["id"]: rb for rb in INCIDENT_RUNBOOKS}


def get_all_runbooks() -> list[dict]:
    """Return every incident-response runbook."""
    return INCIDENT_RUNBOOKS


def get_runbook_by_id(runbook_id: str) -> dict | None:
    """Return a single runbook by its ID, or None if not found."""
    return _RUNBOOK_INDEX.get(runbook_id)


def get_applicable_runbooks(findings: list[dict]) -> list[dict]:
    """Return runbooks relevant to a set of assessment findings.

    Matching rules:
    - authentication-related findings  → Credential Compromise, Phishing Campaign
    - encryption / segmentation gaps   → Data Breach / Exfiltration, Cloud Misconfiguration
    - encryption + auth findings       → API Abuse / Injection
    - monitoring gaps                  → DDoS Attack
    - access_control findings          → Insider Threat, Privilege Escalation
    - supply_chain findings            → Supply Chain Compromise
    - data_protection findings         → Data Integrity Violation
    - Any CAT1 finding                 → Ransomware, Zero-Day Exploit
    """
    matched_ids: set[str] = set()

    _auth_keywords = {"authentication", "credential", "password", "mfa", "login", "identity"}
    _crypto_keywords = {"encryption", "segmentation", "tls", "ssl", "cipher", "data-at-rest"}
    _monitor_keywords = {"monitoring", "logging", "detection", "siem", "alerting", "ids", "ips"}
    _access_keywords = {
        "access_control",
        "access-control",
        "rbac",
        "iam",
        "privilege",
        "authorization",
        "least-privilege",
    }
    _supply_keywords = {"supply_chain", "supply-chain", "dependency", "sbom", "package", "registry"}
    _data_keywords = {"data_protection", "data-protection", "integrity", "tampering", "hash", "checksum"}

    for finding in findings:
        text = " ".join(
            [
                str(finding.get("category", "")),
                str(finding.get("title", "")),
                str(finding.get("description", "")),
            ]
        ).lower()

        if any(kw in text for kw in _auth_keywords):
            matched_ids.add("IR-CRED-001")
            matched_ids.add("IR-PHISH-001")
            matched_ids.add("IR-APIABUSE-001")

        if any(kw in text for kw in _crypto_keywords):
            matched_ids.add("IR-BREACH-001")
            matched_ids.add("IR-CLOUDCONFIG-001")
            matched_ids.add("IR-APIABUSE-001")

        if any(kw in text for kw in _monitor_keywords):
            matched_ids.add("IR-DDOS-001")

        if any(kw in text for kw in _access_keywords):
            matched_ids.add("IR-INSIDER-001")
            matched_ids.add("IR-PRIVESC-001")

        if any(kw in text for kw in _supply_keywords):
            matched_ids.add("IR-SUPPLY-001")

        if any(kw in text for kw in _data_keywords):
            matched_ids.add("IR-INTEGRITY-001")

        severity = str(finding.get("severity", "")).upper()
        if severity == "CAT1":
            matched_ids.add("IR-RANSOM-001")
            matched_ids.add("IR-ZERODAY-001")

    return [rb for rb in INCIDENT_RUNBOOKS if rb["id"] in matched_ids]
