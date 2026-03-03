////////////////////////////////////////////////////////////////////
CONTROLLED UNCLASSIFIED INFORMATION (CUI) // SP-CTI
Distribution: Distribution D -- Authorized DoD Personnel Only
////////////////////////////////////////////////////////////////////

# INCIDENT RESPONSE PLAN (IRP)
## Per DoD Instruction 8530.01 / NIST SP 800-61 Rev 2

---

## 1. Document Control

**System Name:** {{system_name}}

**System Identifier:** {{system_id}}

**Plan Version:** {{plan_version}}

**Classification:** {{classification}}

**Date Prepared:** {{plan_date}}

**Last Review Date:** {{last_review_date}}

**Next Scheduled Review:** {{next_review_date}}

**System Owner:** {{system_owner}}

**Authorizing Official:** {{ao_name}}

**ISSM:** {{issm_name}}

**ISSO:** {{isso_name}}

### Approval Signatures

| Role | Name | Signature | Date |
|------|------|-----------|------|
| System Owner | {{system_owner}} | __________________ | ________ |
| Authorizing Official | {{ao_name}} | __________________ | ________ |
| ISSM | {{issm_name}} | __________________ | ________ |
| ISSO | {{isso_name}} | __________________ | ________ |

### Revision History

{{revision_history}}

| Version | Date | Author | Description of Changes |
|---------|------|--------|------------------------|
| 1.0 | {{plan_date}} | {{isso_name}} | Initial release |

---

## 2. Purpose and Scope

### 2.1 Purpose

This Incident Response Plan (IRP) establishes procedures for detecting, reporting, analyzing, containing, eradicating, and recovering from cybersecurity incidents affecting **{{system_name}}** ({{system_id}}). This plan ensures that incidents are handled in a manner that minimizes damage, reduces recovery time and costs, preserves evidence for potential legal or disciplinary action, and satisfies all applicable DoD reporting requirements.

### 2.2 Scope

This plan applies to all information systems, networks, and data within the authorization boundary of {{system_name}}.

**System Boundary:** {{system_boundary}}

**Operating Environment:** {{operating_environment}}

**This plan covers:**
- All hardware, software, and network components within the {{system_name}} authorization boundary
- All personnel with access to the system, including administrators, developers, end users, and contractors
- All data processed, stored, or transmitted by the system, including CUI // SP-CTI
- Interconnected systems where incidents may propagate to or from the {{system_name}} boundary
- Cloud infrastructure components hosted in the {{operating_environment}}

**This plan does not cover:**
- Physical security incidents not involving information systems (refer to Physical Security Plan)
- Incidents on systems outside the {{system_name}} authorization boundary unless they directly impact this system
- Personnel security investigations (refer to Personnel Security Program)

### 2.3 Applicable Regulations and Standards

| Document | Description | Applicability |
|----------|-------------|---------------|
| DoD Instruction 8530.01 | Cybersecurity Activities Support to DoD Information Network Operations | Primary directive for CSSP SOC engagement and reporting timelines |
| NIST SP 800-61 Rev 2 | Computer Security Incident Handling Guide | Technical framework for incident response lifecycle |
| NIST SP 800-53 Rev 5 (IR Family) | Incident Response Controls (IR-1 through IR-10) | Control requirements for authorization |
| CJCSM 6510.01B | Cyber Incident Handling Program | Joint Staff procedures for cyber incident handling |
| DoD Instruction 5200.48 | CUI Program | Requirements for protecting CUI during and after incidents |
| DFARS 252.204-7012 | Safeguarding Covered Defense Information | Contractor reporting obligations (72-hour rule) |
| DoDI 8500.01 | Cybersecurity | Overarching DoD cybersecurity policy |
| DoD Manual 5200.01 Vol 3 | DoD Information Security Program | Spillage and classified data incident procedures |

---

## 3. Roles and Responsibilities

### 3.1 Incident Commander (IC)

**Default Assignment:** {{issm_name}} (ISSM)

The Incident Commander has overall authority for managing the incident response. During a declared incident, the IC:
- Activates the Incident Response Team (IRT) and assigns roles
- Makes containment, eradication, and recovery decisions
- Authorizes system isolation, shutdown, or network disconnection
- Approves external communications and notifications
- Ensures evidence preservation procedures are followed
- Coordinates with the Authorizing Official on risk acceptance decisions
- Declares incident closure and initiates after-action review
- May delegate IC role to a qualified alternate when unavailable

**Escalation:** If the ISSM is unavailable, IC authority passes to the ISSO, then to the Security Engineer.

### 3.2 Information System Security Manager (ISSM)

**Name:** {{issm_name}}

- Serves as default Incident Commander for all incidents
- Reports incidents to the Authorizing Official ({{ao_name}})
- Coordinates with CSSP SOC ({{soc_name}}) on incident response activities
- Ensures all reporting timelines per DoDI 8530.01 are met
- Manages the Plan of Action and Milestones (POA&M) entries resulting from incidents
- Ensures lessons learned are documented and incorporated into security posture
- Validates that containment and eradication actions do not introduce new vulnerabilities
- Maintains the distribution list for this plan

### 3.3 Information System Security Officer (ISSO)

**Name:** {{isso_name}}

- Performs initial incident triage and severity classification
- Conducts preliminary technical analysis of suspected incidents
- Executes containment actions as directed by the Incident Commander
- Documents all incident response actions in the incident tracking system
- Collects and preserves initial evidence per chain-of-custody procedures
- Coordinates with system administrators on technical response actions
- Updates continuous monitoring data to reflect incident impact
- Serves as alternate Incident Commander when ISSM is unavailable

### 3.4 System Administrator

**Name:** {{system_admin}}

- Provides technical execution of containment actions (network isolation, account disablement, service shutdown)
- Performs system restoration from verified backups
- Implements configuration changes directed by the IRT
- Provides system logs, access records, and configuration data for analysis
- Verifies system integrity after eradication and before return to operations
- Maintains backup integrity and documents restoration procedures used
- Executes emergency patching and hardening actions as directed

### 3.5 Security Engineer

**Name:** {{security_engineer}}

- Conducts detailed technical analysis of incident indicators and artifacts
- Performs forensic imaging and analysis per evidence preservation procedures
- Analyzes malware samples, network captures, and system artifacts
- Identifies attack vectors, lateral movement, and full scope of compromise
- Recommends containment strategies and eradication procedures
- Develops and validates indicators of compromise (IOCs) for detection rule updates
- Conducts root cause analysis and recommends preventive measures
- Coordinates with CSSP SOC analysts on technical findings

### 3.6 CSSP SOC Liaison

**Primary SOC:** {{soc_name}}

The CSSP SOC Liaison is the designated point of contact between the {{system_name}} IRT and the supporting Cybersecurity Service Provider Security Operations Center.

- Submits incident tickets to CSSP SOC via {{soc_ticket_url}}
- Relays SOC directives and technical guidance to the IRT
- Provides requested technical data, logs, and artifacts to the SOC
- Coordinates SOC-provided capabilities (enhanced monitoring, threat hunting, forensic support)
- Ensures information sharing complies with classification and need-to-know requirements
- Tracks SOC ticket status and ensures timely updates

**Note:** The ISSO serves as the default CSSP SOC Liaison unless a separate individual is designated.

### 3.7 Legal Counsel

**Contact:** {{legal_contact}}

- Advises on legal implications of incident response actions
- Determines whether law enforcement notification is required
- Ensures evidence handling meets legal standards for potential prosecution
- Advises on privacy and data breach notification requirements
- Reviews external communications for legal risk
- Coordinates with DoD Office of General Counsel as needed
- Advises on contract implications for contractor-reported incidents

### 3.8 Communications Lead

**Contact:** {{comms_contact}}

- Drafts internal and external incident notifications per approved templates
- Coordinates messaging with the Incident Commander before release
- Manages stakeholder communications and status updates
- Ensures communications do not disclose sensitive technical details inappropriately
- Coordinates with Public Affairs if media interest develops
- Maintains communication logs as part of the incident record

### 3.9 Incident Response Team (IRT) Activation

The IRT is activated by the Incident Commander when an event is classified as a confirmed incident at any severity level. The IRT composition scales with severity:

| Severity | Minimum IRT Composition |
|----------|------------------------|
| Critical | IC, ISSM, ISSO, System Admin, Security Engineer, SOC Liaison, Legal, Comms |
| High | IC, ISSO, System Admin, Security Engineer, SOC Liaison |
| Moderate | IC, ISSO, System Admin or Security Engineer |
| Low | ISSO, System Admin |

---

## 4. Incident Classification

### 4.1 Incident Categories

| Category | Code | Description | Examples |
|----------|------|-------------|----------|
| Unauthorized Access | CAT-1 | Successful unauthorized logical access to a system, application, or data | Compromised credentials, privilege escalation, unauthorized admin access, session hijacking |
| Malicious Code | CAT-2 | Installation or execution of malicious software | Ransomware, trojans, rootkits, worms, unauthorized scripts, cryptominers |
| Data Breach / Exfiltration | CAT-3 | Unauthorized disclosure, removal, or loss of CUI or sensitive data | Data exfiltration, unauthorized file transfer, lost/stolen media, email of CUI to unauthorized recipient |
| Denial of Service | CAT-4 | Actions that impair the availability of systems or services | DDoS attacks, resource exhaustion, service disruption, intentional system overload |
| Insider Threat | CAT-5 | Malicious or negligent actions by authorized users | Intentional policy violations, data theft by employees, sabotage, unauthorized system modifications |
| Supply Chain Compromise | CAT-6 | Compromise introduced through third-party software, hardware, or services | Malicious dependencies, compromised vendor updates, tampered hardware, backdoored libraries |
| Improper Usage / Misuse | CAT-7 | Violations of acceptable use policies that create security risk | Unauthorized software installation, policy violations, connecting unauthorized devices, shadow IT |

### 4.2 Severity Levels

| Severity | Level | Definition | Impact Criteria |
|----------|-------|------------|-----------------|
| **Critical** | 1 | Incident causing or likely to cause catastrophic damage to national security, DoD operations, or involving widespread compromise of CUI | -- Confirmed exfiltration of CUI // SP-CTI to adversary<br>-- Root-level compromise of production systems<br>-- Active adversary with persistent access<br>-- Ransomware impacting mission-critical operations<br>-- Spillage of classified information onto unclassified systems |
| **High** | 2 | Incident causing or likely to cause significant damage to operations, data integrity, or involving confirmed unauthorized access to sensitive systems | -- Unauthorized access to systems containing CUI<br>-- Malware execution on production systems<br>-- Compromise of privileged accounts<br>-- Partial data breach affecting CUI<br>-- Insider threat with confirmed malicious activity |
| **Moderate** | 3 | Incident causing limited damage or that could escalate if not contained promptly | -- Failed but targeted intrusion attempts from known threat actors<br>-- Malware detected and contained before execution<br>-- Non-privileged account compromise<br>-- Policy violations with potential security impact<br>-- Supply chain vulnerability affecting non-production systems |
| **Low** | 4 | Minor incident with minimal operational impact, or potential incident requiring investigation | -- Isolated acceptable use violations<br>-- Unsuccessful automated attack attempts<br>-- Low-risk vulnerability exploitation attempts blocked by controls<br>-- Non-sensitive data exposure with minimal impact<br>-- Lost device with no confirmed data access |

### 4.3 Severity Determination Criteria

When classifying incident severity, evaluate the following factors:

1. **Data Sensitivity** -- What type of data is affected? CUI // SP-CTI data elevates severity by one level minimum.
2. **Scope of Compromise** -- How many systems, accounts, or users are affected?
3. **Adversary Capability** -- Is this an automated scan, opportunistic attack, or targeted advanced threat?
4. **Mission Impact** -- Does this affect mission-critical operations, availability, or data integrity?
5. **Containment Status** -- Is the threat actively spreading or has it been contained?
6. **Recoverability** -- Can affected systems and data be fully restored?

**When in doubt, classify at the higher severity level.** Severity can be downgraded as analysis provides clarity, but delayed escalation risks mission impact.

---

## 5. Reporting Timelines

### 5.1 Reporting Requirements Per DoDI 8530.01

| Severity | Initial Report Deadline | Report To | Update Frequency |
|----------|------------------------|-----------|------------------|
| **Critical** | **1 hour** from detection | CSSP SOC, ISSM, AO, US-CERT, DC3 (if applicable) | Every 2 hours until contained, then every 12 hours |
| **High** | **24 hours** from detection | CSSP SOC, ISSM, AO | Every 24 hours until resolved |
| **Moderate** | **72 hours** from detection | CSSP SOC, ISSM | Every 72 hours until resolved |
| **Low** | **5 business days** from detection | ISSM | Weekly until closed |

### 5.2 Notification Matrix

#### Critical Severity (1-Hour Reporting)

| Step | Action | Responsible | Contact Method | Timeline |
|------|--------|-------------|----------------|----------|
| 1 | Classify incident as Critical | ISSO | -- | Immediate |
| 2 | Notify ISSM / Incident Commander | ISSO | Phone + secure email | Within 15 minutes |
| 3 | Notify CSSP SOC | ISSO / SOC Liaison | {{soc_phone}} + {{soc_ticket_url}} | Within 30 minutes |
| 4 | Notify Authorizing Official | ISSM | Phone + secure email | Within 45 minutes |
| 5 | Notify US-CERT (if required) | ISSM | us-cert.cisa.gov portal | Within 1 hour |
| 6 | Notify DC3 (if cyber espionage / APT) | ISSM | DC3 portal | Within 1 hour |
| 7 | Notify Legal Counsel | IC | Phone | Within 1 hour |
| 8 | Notify Communications Lead | IC | Phone + secure email | Within 1 hour |
| 9 | Activate full IRT | IC | Phone tree | Within 1 hour |

#### High Severity (24-Hour Reporting)

| Step | Action | Responsible | Contact Method | Timeline |
|------|--------|-------------|----------------|----------|
| 1 | Classify incident as High | ISSO | -- | Immediate |
| 2 | Notify ISSM / Incident Commander | ISSO | Phone + secure email | Within 2 hours |
| 3 | Notify CSSP SOC | SOC Liaison | {{soc_ticket_url}} + {{soc_email}} | Within 4 hours |
| 4 | Notify Authorizing Official | ISSM | Secure email | Within 12 hours |
| 5 | Activate IRT (scaled composition) | IC | Phone + email | Within 4 hours |

#### Moderate Severity (72-Hour Reporting)

| Step | Action | Responsible | Contact Method | Timeline |
|------|--------|-------------|----------------|----------|
| 1 | Classify incident as Moderate | ISSO | -- | Immediate |
| 2 | Notify ISSM | ISSO | Secure email | Within 24 hours |
| 3 | Notify CSSP SOC | SOC Liaison | {{soc_ticket_url}} | Within 48 hours |
| 4 | Assign IRT members | IC | Email | Within 48 hours |

#### Low Severity (5 Business Day Reporting)

| Step | Action | Responsible | Contact Method | Timeline |
|------|--------|-------------|----------------|----------|
| 1 | Document incident | ISSO | Incident tracking system | Within 24 hours |
| 2 | Notify ISSM | ISSO | Secure email | Within 3 business days |
| 3 | Log with CSSP SOC (if required) | SOC Liaison | {{soc_ticket_url}} | Within 5 business days |

### 5.3 Contractor Reporting Obligations

Per DFARS 252.204-7012, contractors must report cyber incidents affecting covered defense information to DC3 within 72 hours of discovery. This obligation exists in addition to the timelines above and requires:
- Submission via the DC3 DIBNet portal
- Preservation of all images, logs, and artifacts for at least 90 days
- Provision of access to additional information or equipment as required for forensic analysis

---

## 6. Detection and Analysis

### 6.1 Detection Sources

| Source | Description | Monitoring Frequency | Responsible |
|--------|-------------|---------------------|-------------|
| SIEM Alerts | Correlation rules across log sources, anomaly detection | Continuous (real-time) | SOC / ISSO |
| IDS/IPS | Network-based and host-based intrusion detection | Continuous (real-time) | Security Engineer |
| Endpoint Detection and Response (EDR) | Host-level behavioral analysis, process monitoring | Continuous (real-time) | Security Engineer |
| Vulnerability Scanner | Identification of exploited or exploitable vulnerabilities | Scheduled + on-demand | ISSO |
| User Reports | Personnel reporting suspicious activity or anomalies | As received | Help Desk / ISSO |
| CSSP SOC Notifications | Threat intelligence, indicator feeds, directed actions | As received | SOC Liaison |
| Automated Security Scanning | SAST, dependency audit, secret detection, container scanning | Per CI/CD pipeline and scheduled | Security Engineer |
| Audit Log Review | Analysis of authentication, authorization, and admin activity logs | Daily review + real-time alerts | ISSO |
| Threat Intelligence Feeds | STIX/TAXII feeds, DoD threat advisories, CSSP IOC distribution | Continuous ingestion | SOC Liaison |
| File Integrity Monitoring | Detection of unauthorized changes to critical system files | Continuous (real-time) | System Admin |

### 6.2 Initial Analysis Procedures

Upon receiving an alert or report of a potential incident, the ISSO (or designated initial responder) shall:

**Step 1: Validate the Event**
- Confirm the alert is not a false positive by correlating with additional data sources
- Check for known maintenance windows, authorized changes, or testing activities
- Verify the affected systems are within the {{system_name}} authorization boundary

**Step 2: Gather Initial Data**
- Identify affected systems (hostnames, IP addresses, services)
- Determine the timeline (when did the activity begin, when was it detected)
- Collect relevant log entries from SIEM, system logs, network logs, and application logs
- Identify the user accounts involved (source and target)
- Document initial findings in the incident tracking system

**Step 3: Determine Scope**
- Identify all systems that may be affected (lateral movement indicators)
- Determine what data may be at risk (CUI, PII, authentication credentials)
- Assess whether the incident is ongoing or has concluded
- Check for related alerts or activity in adjacent timeframes

**Step 4: Classify Severity**
- Apply the severity determination criteria from Section 4.3
- Assign an incident category from Section 4.1
- Document the classification rationale
- Initiate the appropriate notification timeline from Section 5

**Step 5: Notify and Escalate**
- Follow the notification matrix for the assigned severity level
- Brief the Incident Commander with: what happened, when, what is affected, current status, recommended immediate actions
- Request IRT activation if needed

### 6.3 Analysis Tools and Techniques

| Tool/Technique | Purpose | When Used |
|----------------|---------|-----------|
| Log correlation (SIEM) | Identify related events across data sources | All incidents |
| Network packet capture | Analyze network communications, data exfiltration | CAT-1, CAT-2, CAT-3 |
| Memory forensics | Analyze running processes, detect fileless malware | CAT-1, CAT-2 |
| Disk forensics | Recover deleted files, analyze file system artifacts | CAT-1, CAT-2, CAT-3, CAT-5 |
| Malware analysis (static/dynamic) | Determine malware capabilities, C2 infrastructure | CAT-2, CAT-6 |
| IOC matching | Compare artifacts against known threat indicators | All incidents |
| Timeline reconstruction | Build comprehensive timeline of attacker activity | Critical and High severity |
| Behavioral analysis | Identify anomalous user or process behavior | CAT-5, CAT-7 |

---

## 7. Containment Procedures

### 7.1 Containment Strategy Selection

The Incident Commander selects the containment strategy based on:
- The incident category and severity
- Whether the adversary is actively present on the network
- The potential for collateral damage from containment actions
- Mission-critical status of affected systems
- Evidence preservation requirements

**All containment actions must be documented in real-time in the incident tracking system, including the time, action taken, person executing, and authorization.**

### 7.2 Short-Term Containment

Short-term containment actions are implemented immediately to stop the incident from spreading. These actions prioritize speed over thoroughness and may cause temporary service disruption.

| Action | Description | Authorized By | Executed By |
|--------|-------------|---------------|-------------|
| Network isolation | Disconnect affected system(s) from the network (VLAN change, firewall block, physical disconnect) | IC | System Admin |
| Account disablement | Disable compromised user accounts and reset credentials | IC | System Admin |
| Service shutdown | Stop affected services or applications | IC | System Admin |
| Firewall rule addition | Block known malicious IPs, domains, or ports at the perimeter | IC | Security Engineer |
| DNS sinkhole | Redirect malicious domain queries to prevent C2 communication | IC | Security Engineer |
| Endpoint quarantine | Use EDR to isolate an endpoint while maintaining forensic access | ISSO (Moderate/Low) or IC (High/Critical) | Security Engineer |

**Emergency Action Authority:** In situations where the IC is unreachable and delay would result in catastrophic damage (active data exfiltration of CUI, ransomware encryption in progress), the ISSO may authorize network isolation of affected systems and immediately notify the IC afterward.

### 7.3 Long-Term Containment

Long-term containment is applied after short-term measures are in place and maintains containment while allowing continued investigation and planned recovery.

| Action | Description | Authorized By | Executed By |
|--------|-------------|---------------|-------------|
| Emergency patching | Apply critical patches to close the exploited vulnerability | IC | System Admin |
| Credential rotation | Reset passwords and API keys for all potentially exposed accounts | IC | System Admin |
| Certificate revocation | Revoke and reissue TLS certificates if private keys may be compromised | IC | Security Engineer |
| Enhanced monitoring | Deploy additional logging, packet capture, or honeypot systems | IC | Security Engineer |
| Temporary access restrictions | Implement more restrictive ACLs while investigation continues | IC | System Admin |
| Alternate system deployment | Stand up clean replacement systems for mission-critical functions | IC | System Admin |

### 7.4 Evidence Preservation During Containment

Before executing any containment action that may alter evidence:

1. **Capture volatile data first** -- Running processes, network connections, memory contents, logged-in users
2. **Create forensic images** -- Full disk images of affected systems before remediation
3. **Preserve logs** -- Export and hash relevant log files from SIEM, system, application, and network sources
4. **Document the state** -- Screenshot system state, record active connections, note any anomalies
5. **Maintain chain of custody** -- Log all evidence collection actions per Section 9

---

## 8. Eradication and Recovery

### 8.1 Eradication Procedures

Eradication eliminates the root cause of the incident and all artifacts of the compromise.

**Step 1: Identify Root Cause**
- Determine the initial attack vector (phishing, vulnerability exploitation, insider action, supply chain)
- Identify all compromised systems, accounts, and data
- Map the full scope of adversary activity from initial access through current state

**Step 2: Remove Threat Artifacts**
- Delete malicious files, scripts, scheduled tasks, and persistence mechanisms
- Remove unauthorized accounts, SSH keys, and access tokens
- Clean or rebuild compromised systems from verified clean images
- Remove attacker tools and backdoors from all affected systems

**Step 3: Close Attack Vector**
- Patch the vulnerability that was exploited
- Update firewall rules, IDS/IPS signatures, and detection rules
- Implement additional controls to prevent recurrence
- Update STIG compliance baseline if configuration changes are required

**Step 4: Verify Eradication**
- Scan all affected systems with updated signatures
- Review logs for any continued adversary activity
- Validate that all IOCs associated with the incident are no longer present
- Conduct targeted vulnerability assessment of affected systems

### 8.2 Recovery Procedures

**Step 1: System Restoration**
- Restore from verified clean backups (validate backup integrity via checksums before restoration)
- Rebuild systems from approved baselines if backups may be compromised
- Apply all current patches and STIG hardening before reconnecting to the network
- Restore data from verified clean sources

**Step 2: Validation and Testing**
- Verify system functionality meets operational requirements
- Confirm all security controls are operational (authentication, authorization, logging, encryption)
- Run vulnerability scans and STIG compliance checks against restored systems
- Validate data integrity of restored information
- Test interconnections with dependent systems

**Step 3: Enhanced Monitoring**
- Implement increased monitoring on recovered systems for a minimum of 30 days
- Deploy additional detection rules based on incident IOCs
- Conduct daily log reviews for recovered systems during the monitoring period
- Set lower alert thresholds for activity related to the incident category

### 8.3 Return-to-Operations Criteria

Systems may be returned to normal operations only when ALL of the following criteria are met:

- [ ] Root cause has been identified and eliminated
- [ ] All compromised systems have been rebuilt or verified clean
- [ ] All exploited vulnerabilities have been patched
- [ ] STIG compliance has been verified on all affected systems
- [ ] Vulnerability scan shows no critical or high findings on affected systems
- [ ] All compromised credentials have been rotated
- [ ] Enhanced monitoring is in place and functioning
- [ ] Incident Commander has authorized return to operations
- [ ] CSSP SOC has been notified of planned return to operations
- [ ] ISSM has updated the POA&M with any residual risks
- [ ] AO has accepted any residual risk (if applicable)

**Authorization:** The Incident Commander authorizes return to operations. For Critical severity incidents, the Authorizing Official ({{ao_name}}) must also concur.

---

## 9. Evidence Preservation

### 9.1 Chain of Custody

All evidence collected during incident response must maintain a documented chain of custody. Each piece of evidence must be tracked from collection through final disposition.

**Chain of Custody Record (per item):**

| Field | Description |
|-------|-------------|
| Evidence ID | Unique identifier (format: {{system_id}}-INC-YYYYMMDD-NNN-E##) |
| Description | What the evidence is (disk image, log file, memory dump, etc.) |
| Source System | Hostname, IP, and system identifier of the source |
| Date/Time Collected | Timestamp of collection (UTC) |
| Collected By | Name and role of the person who collected the evidence |
| Hash (SHA-256) | Cryptographic hash computed at time of collection |
| Storage Location | Where the evidence is stored (physical and/or logical) |
| Access Log | Every person who has accessed the evidence, with timestamps |

**Chain of custody forms must be completed at the time of collection, not after the fact.**

### 9.2 Forensic Imaging Procedures

1. **Preparation** -- Verify that forensic tools are ready and write-blockers are functioning
2. **Documentation** -- Photograph the system, record serial numbers, document current state
3. **Volatile Data Collection** -- Capture memory, running processes, network connections, and open files before powering down
4. **Disk Imaging** -- Create a bit-for-bit forensic image using a write-blocker; create two copies minimum
5. **Hash Verification** -- Compute SHA-256 hashes of both the original media and each image; document and compare
6. **Secure Storage** -- Store forensic images in a secure, access-controlled location (encrypted storage recommended)
7. **Original Media** -- Secure the original media in a tamper-evident bag if physical seizure is warranted

### 9.3 Log Preservation

The following logs must be preserved for all incidents at Moderate severity and above:

| Log Source | Retention Period | Format | Storage |
|------------|-----------------|--------|---------|
| SIEM correlation logs | 1 year minimum | Raw + parsed | Secure log archive |
| System authentication logs | 1 year minimum | Syslog / Windows Event | Secure log archive |
| Network flow data | 90 days minimum | NetFlow / PCAP | Secure network storage |
| Application logs | 1 year minimum | Application-specific | Secure log archive |
| Firewall / IDS/IPS logs | 90 days minimum | Vendor format | Secure log archive |
| DNS query logs | 90 days minimum | DNS log format | Secure log archive |
| Email logs (if applicable) | 90 days minimum | MTA logs | Secure log archive |
| Audit trail (ICDEV) | Immutable / permanent | SQLite / JSON | data/icdev.db (append-only) |

**Note:** Per DFARS 252.204-7012, contractors must preserve images and logs for a minimum of 90 days following a reported cyber incident.

### 9.4 Evidence Storage Requirements

- All digital evidence must be stored on encrypted media
- Physical evidence must be stored in a locked container within a controlled-access area
- Evidence storage areas must have access logging
- Evidence integrity must be verified (hash comparison) at regular intervals and upon each access
- Evidence disposition must follow organizational records retention policies and legal hold requirements
- Evidence must not be stored on systems within the incident boundary

---

## 10. Communication Plan

### 10.1 Internal Notification Matrix

| Audience | Critical | High | Moderate | Low | Method |
|----------|----------|------|----------|-----|--------|
| Incident Commander | Immediate | 2 hours | 24 hours | 3 business days | Phone + email |
| Authorizing Official ({{ao_name}}) | 45 minutes | 12 hours | As needed | Monthly report | Phone + email |
| System Owner ({{system_owner}}) | 1 hour | 12 hours | 72 hours | Monthly report | Phone + email |
| ISSM ({{issm_name}}) | 15 minutes | 2 hours | 24 hours | 3 business days | Phone + email |
| ISSO ({{isso_name}}) | Immediate | Immediate | 4 hours | 24 hours | Phone + email |
| System Admin ({{system_admin}}) | Immediate | 1 hour | 24 hours | As needed | Phone + email |
| Security Engineer ({{security_engineer}}) | 30 minutes | 2 hours | 24 hours | As needed | Phone + email |
| Legal Counsel ({{legal_contact}}) | 1 hour | 24 hours | As needed | N/A | Phone + email |
| Communications ({{comms_contact}}) | 1 hour | 24 hours | As needed | N/A | Phone + email |
| End Users (if impacted) | 4 hours | 24 hours | As needed | N/A | Email |

### 10.2 External Notification Requirements

| Organization | When Required | Method | Timeline | Contact |
|-------------|---------------|--------|----------|---------|
| CSSP SOC ({{soc_name}}) | All confirmed incidents High and above; Moderate at ISSM discretion | Phone: {{soc_phone}}, Email: {{soc_email}}, Ticket: {{soc_ticket_url}} | Per Section 5.1 | SOC Liaison |
| US-CERT / CISA | Critical incidents; incidents affecting federal networks | us-cert.cisa.gov reporting portal | Within 1 hour (Critical) | ISSM |
| DC3 (Defense Cyber Crime Center) | Cyber espionage, APT activity, contractor incidents per DFARS | DIBNet portal | Within 72 hours (DFARS); Within 1 hour (Critical APT) | ISSM |
| Law Enforcement (DCIS, FBI, OSI) | Criminal activity, insider threat with criminal elements | Phone, coordinated through Legal | As directed by Legal Counsel | ISSM + Legal |
| Authorizing Official chain | All incidents affecting ATO status | Secure email + phone | Per Section 5.1 | ISSM |
| Interconnected system owners | Incidents that may propagate to connected systems | Secure email + phone | Within 4 hours of confirmation | ISSO |

### 10.3 Communication Templates

#### Initial Incident Notification (Internal)

```
SUBJECT: [SEVERITY] Cybersecurity Incident -- {{system_name}} -- [INC-YYYYMMDD-NNN]

CLASSIFICATION: CUI // SP-CTI

INCIDENT SUMMARY:
- Incident ID: [INC-YYYYMMDD-NNN]
- Date/Time Detected: [YYYY-MM-DD HH:MM UTC]
- Severity: [Critical/High/Moderate/Low]
- Category: [CAT-1 through CAT-7]
- Affected Systems: [List hostnames/IPs]
- Brief Description: [1-2 sentence summary]
- Current Status: [Investigating/Containing/Eradicating/Recovering]
- Incident Commander: [Name]

IMMEDIATE ACTIONS TAKEN:
- [List actions taken so far]

NEXT STEPS:
- [List planned actions]

CONTACT: [ISSO name and phone]

This notification is CUI // SP-CTI. Handle and distribute accordingly.
```

#### CSSP SOC Incident Report

```
SUBJECT: Incident Report -- {{system_name}} ({{system_id}}) -- [INC-YYYYMMDD-NNN]

CLASSIFICATION: CUI // SP-CTI

1. REPORTING ORGANIZATION: [Organization name]
2. SYSTEM: {{system_name}} ({{system_id}})
3. DATE/TIME DETECTED: [YYYY-MM-DD HH:MM UTC]
4. DATE/TIME OF INCIDENT: [YYYY-MM-DD HH:MM UTC] (if different from detection)
5. INCIDENT CATEGORY: [CAT-1 through CAT-7]
6. SEVERITY: [Critical/High/Moderate/Low]
7. AFFECTED SYSTEMS: [Hostnames, IPs, OS, function]
8. DESCRIPTION: [Detailed description of incident]
9. INDICATORS OF COMPROMISE: [IPs, domains, hashes, file names, etc.]
10. IMPACT: [Operational impact, data at risk]
11. ACTIONS TAKEN: [Containment and response actions]
12. ASSISTANCE REQUESTED: [Specific SOC support needed]
13. POC: [Name, phone, email]
14. NEXT UPDATE: [Date/time of next scheduled update]
```

#### Status Update Template

```
SUBJECT: UPDATE [#N] -- [SEVERITY] Incident -- {{system_name}} -- [INC-YYYYMMDD-NNN]

CLASSIFICATION: CUI // SP-CTI

UPDATE SUMMARY:
- Incident ID: [INC-YYYYMMDD-NNN]
- Update Number: [N]
- Current Severity: [Unchanged/Upgraded/Downgraded] -- [Level]
- Current Status: [Investigating/Containing/Eradicating/Recovering/Closed]

ACTIONS SINCE LAST UPDATE:
- [Bulleted list of actions taken]

FINDINGS:
- [New findings, analysis results, scope changes]

CURRENT CONTAINMENT STATUS:
- [Description of containment posture]

NEXT STEPS:
- [Planned actions with estimated timelines]

NEXT UPDATE: [Date/time]

CONTACT: [Name and phone]
```

---

## 11. Escalation Matrix

| Severity | Initial Response | Reporting Deadline | Incident Commander | CSSP SOC Engagement | AO Notification | Briefing Cadence |
|----------|-----------------|--------------------|--------------------|---------------------|-----------------|------------------|
| **Critical** | Immediate IRT activation | 1 hour | ISSM ({{issm_name}}) | Full engagement: real-time coordination, SOC analyst support, threat hunting | Within 45 minutes | Every 2 hours until contained |
| **High** | IRT activation within 4 hours | 24 hours | ISSM ({{issm_name}}) | Active engagement: ticket submission, log sharing, IOC exchange | Within 12 hours | Every 24 hours |
| **Moderate** | Assigned responder within 24 hours | 72 hours | ISSO ({{isso_name}}) | Standard: ticket submission, periodic updates | As needed | Every 72 hours |
| **Low** | Normal business hours response | 5 business days | ISSO ({{isso_name}}) | Minimal: documented in tracking system | Monthly reporting | Weekly until closed |

### Escalation Triggers

An incident must be escalated to the next higher severity level when any of the following occur:

- The scope of compromise expands beyond initial assessment
- CUI data is confirmed to have been exfiltrated
- Additional systems are found to be compromised
- The adversary demonstrates advanced capabilities (zero-day exploitation, custom tooling)
- Containment actions are ineffective after initial implementation
- The incident generates media or Congressional interest
- Law enforcement requests involvement
- The incident affects interconnected systems outside the {{system_name}} boundary

---

## 12. CSSP SOC Integration

### 12.1 SOC Contact Information

| Item | Detail |
|------|--------|
| SOC Name | {{soc_name}} |
| Primary Phone | {{soc_phone}} |
| Email | {{soc_email}} |
| Ticket Submission Portal | {{soc_ticket_url}} |
| Hours of Operation | 24/7/365 (for Critical/High); Business hours for Moderate/Low |
| Secure Communication Channel | Per SOC-provided secure communication procedures |

### 12.2 Ticket Submission Process

1. **Create ticket** via {{soc_ticket_url}} with incident category, severity, and initial details
2. **Receive ticket number** -- Reference this number in all subsequent communications
3. **Upload supporting data** -- Logs, IOCs, forensic artifacts as requested by SOC
4. **Respond to SOC queries** within the timeframe specified by the SOC based on severity
5. **Update ticket** with new findings, status changes, and actions taken
6. **Close ticket** only when the SOC confirms closure criteria are met

### 12.3 SOC Capabilities Available

The CSSP SOC provides the following capabilities that may be requested during incident response:

| Capability | Description | Request Method |
|------------|-------------|----------------|
| Threat Hunting | Proactive search for adversary activity based on IOCs | SOC ticket |
| Forensic Support | Advanced forensic analysis beyond local capability | SOC ticket + phone |
| Malware Analysis | Static and dynamic analysis of malware samples | SOC ticket with sample submission |
| Threat Intelligence | Contextual information on threat actors, TTPs, campaigns | SOC ticket or email |
| Enhanced Monitoring | Temporary increased monitoring of specific assets or traffic | SOC ticket |
| Network Analysis | Deep packet inspection, traffic analysis, anomaly detection | SOC ticket |
| Indicator Sharing | Distribution of IOCs to detection systems across the enterprise | Automatic per SOC procedures |
| Incident Coordination | Multi-system incident coordination across organizational boundaries | SOC ticket + phone |

### 12.4 Information Sharing Procedures

- All information shared with the CSSP SOC must be marked with appropriate CUI markings
- Share IOCs, logs, and artifacts via SOC-approved secure transfer mechanisms only
- Classify information shared based on the sensitivity of the underlying data
- Do not share classified information through CUI channels -- escalate to appropriate security authority
- Request SOC confirmation of receipt for all evidence and artifact submissions
- Maintain a log of all information shared with the SOC as part of the incident record

---

## 13. Testing and Exercises

### 13.1 Annual Tabletop Exercise

**Frequency:** At least annually (more frequently for high-value assets)

**Scope:** Full IRP walkthrough using realistic scenarios appropriate to the {{system_name}} threat profile.

**Participants:** All IRT members, CSSP SOC representative (when available), system owner, AO representative.

**Exercise Requirements:**
- Scenario must include at least one Critical severity incident
- Must exercise external notification procedures (CSSP SOC, US-CERT, DC3 as applicable)
- Must validate contact information and notification chains
- Must test decision-making processes for containment, eradication, and recovery
- Must include a CUI data breach scenario at least every other year
- Scenarios should be based on current threat intelligence and recent incidents in the DoD community

**Documentation:** Exercise plan, participant list, scenario injects, participant responses, after-action report.

### 13.2 Quarterly Communications Test

**Frequency:** Every 90 days

**Scope:** Verify all IRT contact information and communication channels are operational.

**Test Procedures:**
1. Contact each IRT member via primary and alternate contact methods
2. Verify CSSP SOC contact information and ticket submission process
3. Confirm secure communication channels are operational
4. Validate that phone trees and escalation paths are current
5. Update contact roster with any changes identified

**Documentation:** Test date, results for each contact, any failures and corrective actions.

### 13.3 After-Action Review (AAR)

An after-action review must be conducted following:
- Every actual incident at Moderate severity or above
- Every tabletop exercise
- Every significant change to the system architecture or operating environment

**AAR Requirements:**
1. Conduct within 10 business days of incident closure or exercise completion
2. Include all IRT members who participated in the response
3. Document what happened, what was planned, what went well, what needs improvement
4. Identify specific action items with owners and deadlines
5. Update this IRP based on findings
6. Update detection rules, playbooks, and procedures based on lessons learned
7. Brief the AO on significant findings

**AAR Report Template:**

| Section | Content |
|---------|---------|
| Incident/Exercise Summary | Brief description, timeline, severity |
| What Worked Well | Effective procedures, tools, coordination |
| Areas for Improvement | Gaps, delays, tool limitations, communication failures |
| Root Cause (incidents only) | Technical root cause and contributing factors |
| Action Items | Specific improvements with owner, deadline, and priority |
| IRP Updates Required | Sections of this plan that need revision |
| Training Needs | Knowledge or skill gaps identified |

---

## 14. Plan Maintenance

### 14.1 Review Schedule

| Review Type | Frequency | Responsible | Trigger |
|-------------|-----------|-------------|---------|
| Scheduled review | Annual minimum | ISSM ({{issm_name}}) | Calendar date ({{next_review_date}}) |
| Post-incident review | Within 10 business days of closure | ISSO ({{isso_name}}) | Every Moderate+ incident |
| Post-exercise review | Within 10 business days of exercise | ISSO ({{isso_name}}) | Every tabletop or communications test |
| Personnel change review | Within 5 business days | ISSO ({{isso_name}}) | Any IRT member departure or role change |
| System change review | Within 10 business days | ISSO ({{isso_name}}) | Major system architecture or boundary changes |
| Regulatory change review | Within 30 days | ISSM ({{issm_name}}) | New or updated DoD directives, NIST publications |

### 14.2 Update Triggers

This plan must be reviewed and updated when any of the following occur:

- Annual review date is reached
- A significant incident reveals gaps in current procedures
- After-action review identifies required changes
- IRT personnel changes (departures, new assignments, role changes)
- Significant changes to the {{system_name}} architecture or authorization boundary
- Changes to CSSP SOC contact information, procedures, or capabilities
- New or updated DoD directives, instructions, or NIST guidance affecting incident response
- Changes to the threat landscape relevant to {{system_name}}
- Changes to interconnected systems that affect incident response coordination
- Organizational restructuring affecting roles and responsibilities

### 14.3 Distribution List

This plan is distributed to the following personnel. Recipients are responsible for maintaining the most current version and destroying superseded copies.

| Recipient | Role | Distribution Method |
|-----------|------|---------------------|
| {{system_owner}} | System Owner | Secure electronic + printed copy |
| {{ao_name}} | Authorizing Official | Secure electronic |
| {{issm_name}} | ISSM | Secure electronic + printed copy |
| {{isso_name}} | ISSO | Secure electronic + printed copy |
| {{system_admin}} | System Administrator | Secure electronic |
| {{security_engineer}} | Security Engineer | Secure electronic |
| {{legal_contact}} | Legal Counsel | Secure electronic |
| {{comms_contact}} | Communications Lead | Secure electronic |
| {{soc_name}} | CSSP SOC (reference copy) | Secure electronic |

### 14.4 Version Control

All changes to this plan must be:
1. Reviewed and approved by the ISSM before distribution
2. Documented in the Revision History table (Section 1)
3. Distributed to all personnel on the distribution list within 5 business days
4. Acknowledged by all IRT members within 10 business days of receipt

---

## 15. Appendices

### Appendix A: Contact Roster

**CLASSIFICATION: CUI // SP-CTI -- Protect accordingly**

| Role | Name | Primary Phone | Alternate Phone | Secure Email | Location |
|------|------|---------------|-----------------|--------------|----------|
| System Owner | {{system_owner}} | ____________ | ____________ | ____________ | ____________ |
| Authorizing Official | {{ao_name}} | ____________ | ____________ | ____________ | ____________ |
| ISSM | {{issm_name}} | ____________ | ____________ | ____________ | ____________ |
| ISSO | {{isso_name}} | ____________ | ____________ | ____________ | ____________ |
| System Administrator | {{system_admin}} | ____________ | ____________ | ____________ | ____________ |
| Security Engineer | {{security_engineer}} | ____________ | ____________ | ____________ | ____________ |
| Legal Counsel | {{legal_contact}} | ____________ | ____________ | ____________ | ____________ |
| Communications Lead | {{comms_contact}} | ____________ | ____________ | ____________ | ____________ |
| CSSP SOC (Primary) | {{soc_name}} | {{soc_phone}} | ____________ | {{soc_email}} | ____________ |

**External Contacts:**

| Organization | Purpose | Phone | Email/Portal |
|-------------|---------|-------|--------------|
| US-CERT / CISA | Federal incident reporting | 1-888-282-0870 | us-cert.cisa.gov |
| DC3 (DoD Cyber Crime Center) | DoD/DIB incident reporting | 1-410-981-0104 | dc3.mil |
| FBI Cyber Division | Law enforcement | Local field office | ic3.gov |
| DCIS (Defense Criminal Investigative Service) | DoD criminal investigation | Local field office | dodig.mil/dcis |

**This contact roster must be verified quarterly (see Section 13.2).**

---

### Appendix B: Incident Report Form

```
////////////////////////////////////////////////////////////////////
CUI // SP-CTI
////////////////////////////////////////////////////////////////////

CYBERSECURITY INCIDENT REPORT FORM
System: {{system_name}} ({{system_id}})

SECTION 1: INCIDENT IDENTIFICATION
  Incident ID:          INC-____________-___
  Date/Time Detected:   ____-__-__ __:__ UTC
  Date/Time of Incident:____-__-__ __:__ UTC (if known)
  Detected By:          [ ] SIEM  [ ] IDS/IPS  [ ] EDR  [ ] User Report
                        [ ] CSSP SOC  [ ] Scanner  [ ] Audit Review
                        [ ] Other: _______________
  Reported By:          Name: ________________  Role: ________________

SECTION 2: CLASSIFICATION
  Category:  [ ] CAT-1 Unauthorized Access  [ ] CAT-2 Malicious Code
             [ ] CAT-3 Data Breach          [ ] CAT-4 Denial of Service
             [ ] CAT-5 Insider Threat       [ ] CAT-6 Supply Chain
             [ ] CAT-7 Misuse
  Severity:  [ ] Critical  [ ] High  [ ] Moderate  [ ] Low

SECTION 3: AFFECTED SYSTEMS
  Hostname(s):    ________________________________________________
  IP Address(es): ________________________________________________
  OS/Platform:    ________________________________________________
  Function:       ________________________________________________
  CUI Data at Risk: [ ] Yes  [ ] No  [ ] Unknown
  Description of CUI: ___________________________________________

SECTION 4: INCIDENT DESCRIPTION
  (Provide detailed narrative of what occurred, how it was detected,
   and the current state of the incident.)
  _______________________________________________________________
  _______________________________________________________________
  _______________________________________________________________
  _______________________________________________________________

SECTION 5: INDICATORS OF COMPROMISE
  Source IPs:     ________________________________________________
  Dest IPs:       ________________________________________________
  Domains/URLs:   ________________________________________________
  File Hashes:    ________________________________________________
  File Names:     ________________________________________________
  Other IOCs:     ________________________________________________

SECTION 6: ACTIONS TAKEN
  [ ] System isolated from network
  [ ] Affected accounts disabled
  [ ] Forensic image created
  [ ] Logs preserved
  [ ] CSSP SOC notified (Ticket #: _________)
  [ ] ISSM notified
  [ ] AO notified
  [ ] Other: ____________________________________________________
  Narrative: ____________________________________________________
  _______________________________________________________________

SECTION 7: IMPACT ASSESSMENT
  Operational Impact:  [ ] None  [ ] Minor  [ ] Significant  [ ] Severe
  Data Compromise:     [ ] None  [ ] Suspected  [ ] Confirmed
  Users Affected:      _____ (number)
  Systems Affected:    _____ (number)
  Mission Impact:      ___________________________________________

SECTION 8: SIGNATURES
  Prepared By:   ________________  Date: ________  Time: ________
  Reviewed By:   ________________  Date: ________  Time: ________
  IC Approval:   ________________  Date: ________  Time: ________

////////////////////////////////////////////////////////////////////
CUI // SP-CTI
////////////////////////////////////////////////////////////////////
```

---

### Appendix C: Evidence Collection Checklist

Use this checklist for each system involved in an incident. Complete one checklist per system.

**System:** __________________ **Incident ID:** __________________

**Collector:** ________________ **Date/Time (UTC):** ______________

#### Volatile Data (Collect FIRST -- order matters)

- [ ] System date/time and timezone
- [ ] Running processes and services (full process tree with command lines)
- [ ] Open network connections and listening ports
- [ ] Logged-in users and active sessions
- [ ] Open files and file handles
- [ ] System memory (full RAM dump)
- [ ] Network interface configuration (IP, MAC, DNS, routes)
- [ ] Scheduled tasks and cron jobs
- [ ] Loaded kernel modules / drivers
- [ ] Clipboard contents (if accessible)
- [ ] Environment variables for suspicious processes
- [ ] ARP cache and DNS cache

#### Non-Volatile Data

- [ ] Full disk forensic image (with write-blocker)
- [ ] SHA-256 hash of forensic image computed and recorded
- [ ] Second copy of forensic image created and stored separately
- [ ] System configuration files
- [ ] User account information and recent authentication logs
- [ ] Application logs (web server, database, application-specific)
- [ ] System event logs (syslog, Windows Event Log)
- [ ] Security logs (authentication, authorization, audit)
- [ ] Network device logs (firewall, router, switch, IDS/IPS)
- [ ] SIEM correlation data for the incident timeframe
- [ ] Email headers and content (if email-related incident)
- [ ] Browser history and artifacts (if relevant)
- [ ] Registry hives (Windows) or configuration databases

#### Evidence Handling

- [ ] Chain of custody form completed for each evidence item
- [ ] All evidence items assigned unique Evidence IDs
- [ ] SHA-256 hashes computed and recorded for all digital evidence
- [ ] Evidence stored in secure, access-controlled location
- [ ] Evidence storage location documented
- [ ] Evidence access log initiated
- [ ] Physical evidence placed in tamper-evident bags (if applicable)
- [ ] Original media secured and isolated (do not analyze originals)

#### Notes

```
(Document any anomalies, collection difficulties, or deviations
 from standard procedures.)
_________________________________________________________________
_________________________________________________________________
_________________________________________________________________
_________________________________________________________________
```

---

### Appendix D: Acronyms and Abbreviations

| Acronym | Definition |
|---------|------------|
| AAR | After-Action Review |
| AO | Authorizing Official |
| ATO | Authorization to Operate |
| C2 | Command and Control |
| CISA | Cybersecurity and Infrastructure Security Agency |
| CSSP | Cybersecurity Service Provider |
| CUI | Controlled Unclassified Information |
| CTI | Controlled Technical Information |
| DC3 | DoD Cyber Crime Center |
| DCIS | Defense Criminal Investigative Service |
| DFARS | Defense Federal Acquisition Regulation Supplement |
| DIB | Defense Industrial Base |
| DoDI | DoD Instruction |
| EDR | Endpoint Detection and Response |
| IC | Incident Commander |
| IDS | Intrusion Detection System |
| IOC | Indicator of Compromise |
| IPS | Intrusion Prevention System |
| IRP | Incident Response Plan |
| IRT | Incident Response Team |
| ISSM | Information System Security Manager |
| ISSO | Information System Security Officer |
| NIST | National Institute of Standards and Technology |
| PCAP | Packet Capture |
| POA&M | Plan of Action and Milestones |
| SAST | Static Application Security Testing |
| SBOM | Software Bill of Materials |
| SIEM | Security Information and Event Management |
| SOC | Security Operations Center |
| SP-CTI | Specified -- Controlled Technical Information |
| SSP | System Security Plan |
| STIG | Security Technical Implementation Guide |
| TLP | Traffic Light Protocol |
| TTP | Tactics, Techniques, and Procedures |
| US-CERT | United States Computer Emergency Readiness Team |

---

### Appendix E: Referenced Documents

| Document | Version/Date | Relevance |
|----------|-------------|-----------|
| DoD Instruction 8530.01 | March 2016 (w/ changes) | Primary directive for CSSP SOC engagement and cyber incident reporting |
| NIST SP 800-61 Rev 2 | August 2012 | Computer Security Incident Handling Guide |
| NIST SP 800-53 Rev 5 | September 2020 | Security and Privacy Controls -- IR family |
| NIST SP 800-86 | August 2006 | Guide to Integrating Forensic Techniques into IR |
| CJCSM 6510.01B | July 2012 | Cyber Incident Handling Program |
| DoDI 5200.48 | March 2020 | CUI Program |
| DoDI 8500.01 | March 2014 | Cybersecurity |
| DFARS 252.204-7012 | October 2016 | Safeguarding Covered Defense Information |
| DoD Manual 5200.01 Vol 3 | February 2012 | DoD Information Security Program |
| {{system_name}} SSP | Current version | System Security Plan for {{system_name}} |
| {{system_name}} POA&M | Current version | Plan of Action and Milestones for {{system_name}} |

---

**Document Classification:** {{classification}}

**Generated by:** ICDEV Compliance Engine v{{icdev_version}}

**Generated on:** {{generation_date}}

////////////////////////////////////////////////////////////////////
CUI // SP-CTI | Department of Defense
////////////////////////////////////////////////////////////////////
