#!/usr/bin/env python3
"""Generate a project-specific Incident Response Plan per CSSP SOC requirements.
Fills {{variables}} from project data in icdev.db, applies CUI markings,
saves to project compliance directory, and logs an audit event."""

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
IR_TEMPLATE_PATH = BASE_DIR / "context" / "compliance" / "incident_response_template.md"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _get_connection(db_path=None):
    """Get a database connection."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\n"
            "Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _load_template(path=None):
    """Load the IR plan template markdown.

    Returns the template string if the file exists, or *None* so the caller
    can fall back to generating a default plan inline.
    """
    template_path = path or IR_TEMPLATE_PATH
    if not template_path.exists():
        return None
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()


def _get_project_data(conn, project_id):
    """Load project record from database."""
    row = conn.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"Project '{project_id}' not found in database.")
    return dict(row)


def _load_cui_config():
    """Load CUI marking configuration."""
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from cui_marker import load_cui_config
        return load_cui_config()
    except ImportError:
        return {
            "document_header": (
                "////////////////////////////////////////////////////////////////////\n"
                "CONTROLLED UNCLASSIFIED INFORMATION (CUI) // SP-CTI\n"
                "Distribution: Distribution D -- Authorized DoD Personnel Only\n"
                "////////////////////////////////////////////////////////////////////"
            ),
            "document_footer": (
                "////////////////////////////////////////////////////////////////////\n"
                "CONTROLLED UNCLASSIFIED INFORMATION (CUI) // SP-CTI\n"
                "////////////////////////////////////////////////////////////////////"
            ),
        }


def _load_project_defaults():
    """Load project defaults from args/project_defaults.yaml.

    Returns a dict.  If pyyaml is unavailable or the file is missing the
    function returns sensible hardcoded defaults so the tool still works in
    minimal environments.
    """
    defaults_path = BASE_DIR / "args" / "project_defaults.yaml"
    if defaults_path.exists():
        try:
            import yaml  # pyyaml — optional dependency
            with open(defaults_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except ImportError:
            pass
        except Exception as exc:
            print(f"Warning: Could not parse project defaults: {exc}", file=sys.stderr)
    return {}


def _log_audit_event(conn, project_id, action, details, file_path=None):
    """Log an audit trail event for IR plan generation."""
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details,
                affected_files, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "ir_plan_generated",
                "icdev-compliance-engine",
                action,
                json.dumps(details),
                json.dumps([str(file_path)] if file_path else []),
                "CUI",
            ),
        )
        conn.commit()
    except Exception as e:
        print(f"Warning: Could not log audit event: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Default IR plan template (used when the on-disk template is missing)
# ---------------------------------------------------------------------------

def _generate_default_template():
    """Return a comprehensive IR plan template with {{variable}} placeholders.

    Sections follow NIST SP 800-61 and CSSP SOC reporting requirements.
    """
    return """\
# Incident Response Plan

**System Name:** {{system_name}}
**System ID:** {{system_id}}
**Classification:** {{classification}}
**Plan Version:** {{plan_version}}
**Effective Date:** {{plan_date}}
**Operating Environment:** {{operating_environment}}

---

## 1. Document Control

| Field | Value |
|-------|-------|
| Document Title | Incident Response Plan — {{system_name}} |
| Version | {{plan_version}} |
| Date | {{plan_date}} |
| Classification | {{classification}} |
| Prepared By | {{prepared_by}} |
| System Owner | {{system_owner}} |
| ISSM | {{issm_name}} |
| ISSO | {{isso_name}} |

### Revision History

| Version | Date | Author | Description |
|---------|------|--------|-------------|
| {{plan_version}} | {{plan_date}} | {{prepared_by}} | {{revision_description}} |

---

## 2. Purpose and Scope

This Incident Response Plan (IRP) establishes the procedures for detecting,
reporting, analyzing, containing, eradicating, and recovering from
cybersecurity incidents affecting **{{system_name}}** ({{system_id}}).

**Scope:**
- All components within the authorization boundary of {{system_name}}
- System boundary: {{system_boundary}}
- Operating environment: {{operating_environment}}
- All personnel with administrative or user access to the system

This plan is developed in accordance with:
- NIST SP 800-61 Rev. 2 — Computer Security Incident Handling Guide
- NIST SP 800-53 Rev. 5 — IR Control Family
- CJCSM 6510.01B — Cyber Incident Handling Program
- CSSP SOC reporting requirements

---

## 3. Roles and Responsibilities

| Role | Name / Org | Responsibilities |
|------|-----------|-----------------|
| System Owner | {{system_owner}} | Overall accountability; authorizes containment/recovery actions |
| ISSM | {{issm_name}} | Security oversight; coordinates with AO and CSSP |
| ISSO | {{isso_name}} | Day-to-day security; initial triage and escalation |
| Incident Commander | {{incident_commander}} | Leads response team during active incidents |
| IR Team Lead | {{ir_team_lead}} | Technical investigation and coordination |
| SOC Analyst | {{soc_analyst}} | Monitoring, detection, initial analysis |
| System Administrator | {{system_admin}} | System-level containment and recovery actions |
| Communications Lead | {{comms_lead}} | Internal/external notifications and status updates |
| Legal / Privacy | {{legal_contact}} | Legal review, PII breach requirements |

---

## 4. Incident Classification

### 4.1 Severity Levels

| Severity | Definition | Examples |
|----------|-----------|----------|
| **Critical** | Active exploitation with confirmed data exfiltration or system compromise; mission-critical impact | APT activity, ransomware with data loss, CUI spillage to unclassified system |
| **High** | Confirmed malicious activity with potential for significant impact; degraded mission capability | Successful phishing with credential compromise, unauthorized privileged access, malware on production host |
| **Moderate** | Suspicious activity requiring investigation; limited operational impact | Failed brute-force attempts, policy violations, unauthorized software installation |
| **Low** | Minor policy deviation or informational event; no operational impact | Single failed login, minor configuration drift, expired certificate detected before impact |

### 4.2 Incident Categories (per CJCSM 6510.01B)

| Category | Description |
|----------|-------------|
| CAT 1 | Root-Level Intrusion (Unauthorized privileged access) |
| CAT 2 | User-Level Intrusion (Unauthorized user-level access) |
| CAT 3 | Unsuccessful Activity Attempt |
| CAT 4 | Denial of Service |
| CAT 5 | Non-Compliance Activity |
| CAT 6 | Reconnaissance / Scanning |
| CAT 7 | Malicious Logic (Malware) |
| CAT 8 | Investigating |
| CAT 9 | Explained Anomaly |

---

## 5. Reporting Timelines

All timelines begin from the moment of **confirmed detection**.

| Severity | Internal Report | CSSP SOC Report | Command Notification | Full Report Due |
|----------|----------------|-----------------|---------------------|-----------------|
| **Critical** | {{reporting_critical}} | {{reporting_critical}} | {{reporting_critical}} | 72 hours |
| **High** | {{reporting_high}} | {{reporting_high}} | 48 hours | 5 business days |
| **Moderate** | {{reporting_moderate}} | {{reporting_moderate}} | 5 business days | 10 business days |
| **Low** | 5 business days | Next scheduled report | N/A | 30 days |

**CSSP SOC Contact Information:**
- Phone: {{cssp_soc_phone}}
- Email: {{cssp_soc_email}}
- Ticket Portal: {{cssp_soc_portal}}

---

## 6. Detection and Analysis

### 6.1 Detection Sources

- SIEM alerts (ELK / Splunk)
- IDS/IPS notifications
- Endpoint Detection and Response (EDR)
- User reports
- Vulnerability scanning results
- Audit log anomalies (Prometheus / Grafana alerts)
- CSSP SOC notifications
- External threat intelligence feeds

### 6.2 Analysis Procedures

1. **Triage** — ISSO/SOC Analyst validates alert within 15 minutes of detection
2. **Classification** — Assign severity level and incident category (Section 4)
3. **Scope Assessment** — Determine affected systems, data, and users
4. **Impact Analysis** — Evaluate mission impact and CUI exposure risk
5. **Evidence Collection** — Begin evidence preservation (Section 9) immediately
6. **Documentation** — Create incident ticket; log all actions with timestamps

### 6.3 Indicators of Compromise (IOC) Tracking

Maintain running IOC list during analysis:
- IP addresses, domains, URLs
- File hashes (MD5, SHA-256)
- Registry modifications
- User accounts involved
- Network connections / C2 channels

---

## 7. Containment Procedures

### 7.1 Short-Term Containment (Immediate — within {{reporting_critical}} of classification)

- Isolate affected system(s) from the network
- Block identified malicious IPs/domains at firewall/WAF
- Disable compromised user accounts
- Capture volatile evidence (memory, running processes, network connections)
- Activate backup authentication mechanisms if primary is compromised
- Notify CSSP SOC per reporting timelines (Section 5)

### 7.2 Long-Term Containment (Within 24 hours)

- Apply temporary patches or workarounds
- Redirect traffic to clean systems where possible
- Implement enhanced monitoring on affected segments
- Deploy additional detection signatures
- Establish clean staging environment for recovery

### 7.3 Containment Decision Matrix

| Severity | Isolate System | Block Network | Disable Accounts | Notify CSSP |
|----------|---------------|---------------|-------------------|-------------|
| Critical | Immediate | Immediate | Immediate | {{reporting_critical}} |
| High | Within 1 hour | Within 1 hour | Case-by-case | {{reporting_high}} |
| Moderate | Case-by-case | If applicable | If applicable | {{reporting_moderate}} |
| Low | No | No | No | Next scheduled |

---

## 8. Eradication and Recovery

### 8.1 Eradication

1. Identify root cause and all attack vectors
2. Remove malware, backdoors, and unauthorized accounts
3. Patch exploited vulnerabilities
4. Reset all potentially compromised credentials
5. Validate removal with targeted scanning
6. Update IDS/IPS signatures based on findings

### 8.2 Recovery

1. Restore systems from known-good backups (verify integrity)
2. Rebuild compromised systems from hardened baselines
3. Reintroduce systems to production incrementally
4. Validate functionality through testing (smoke, integration, security)
5. Monitor recovered systems with enhanced logging for 30 days minimum
6. Confirm no re-infection over monitoring period

### 8.3 Recovery Prioritization

| Priority | Systems | RTO |
|----------|---------|-----|
| P1 — Mission Critical | Core application services, authentication | 4 hours |
| P2 — Essential | Database, API gateways, monitoring | 8 hours |
| P3 — Supporting | Development environments, documentation | 24 hours |
| P4 — Deferrable | Non-production, analytics | 72 hours |

---

## 9. Evidence Preservation

### 9.1 Collection Requirements

All evidence must be collected and preserved in accordance with federal
rules of evidence and chain-of-custody requirements.

**Collect (in order of volatility):**
1. Memory dumps (RAM)
2. Running processes and network connections
3. Temporary file systems
4. Disk images (forensic bit-for-bit copy)
5. Firewall, IDS, and SIEM logs
6. Application and system logs
7. Network traffic captures (PCAP)

### 9.2 Chain of Custody

- Record: who collected, when, from where, hash of evidence
- Store evidence in tamper-evident, access-controlled location
- Maintain custody log with every transfer documented
- All evidence classified at minimum: {{classification}}

### 9.3 Evidence Retention

- Retain all incident evidence for a minimum of **3 years**
- Critical/High incidents: retain for **6 years** or per records schedule
- Never destroy evidence while an investigation is active

---

## 10. Communication Plan

### 10.1 Internal Communications

| Audience | Method | Timing | Owner |
|----------|--------|--------|-------|
| IR Team | Secure chat / phone bridge | Immediate | IR Team Lead |
| System Owner | Phone + encrypted email | Within {{reporting_critical}} (Critical) | ISSO |
| ISSM | Phone + encrypted email | Within {{reporting_critical}} (Critical) | ISSO |
| All system users | Encrypted email | As directed by Incident Commander | Communications Lead |
| Leadership | Briefing | Within 4 hours (Critical/High) | System Owner |

### 10.2 External Communications

| Audience | Method | Timing | Owner |
|----------|--------|--------|-------|
| CSSP SOC | CSSP reporting portal / phone | Per Section 5 timelines | ISSM |
| Authorizing Official | Encrypted email / briefing | Within 24 hours (Critical/High) | ISSM |
| US-CERT | Per federal reporting requirements | Per US-CERT timelines | ISSM |
| Law Enforcement | Phone / in-person | If criminal activity suspected | Legal / Privacy |
| Affected individuals | Written notification | Per PII breach requirements | Legal / Privacy |

### 10.3 Communication Security

- All incident communications must use encrypted channels
- Do not discuss incident details on unclassified/unencrypted systems
- Apply {{classification}} markings to all incident documentation

---

## 11. Escalation Matrix

| Severity | Response Time | First Notify | Escalate To | Command Notify |
|----------|--------------|-------------|-------------|----------------|
| **Critical** | Immediate | ISSO + IR Team | ISSM + System Owner + CSSP SOC | Within {{reporting_critical}} |
| **High** | Within 1 hour | ISSO + IR Team | ISSM + System Owner | Within {{reporting_high}} |
| **Moderate** | Within 4 hours | ISSO | ISSM (if needed) | Within {{reporting_moderate}} |
| **Low** | Next business day | ISSO | N/A | N/A |

### Escalation Triggers

Escalate to next level immediately if any of the following occur:
- Incident scope expands beyond initial assessment
- CUI confirmed exfiltrated or exposed
- Additional systems compromised
- Containment measures fail
- Media inquiry received
- Incident duration exceeds expected resolution time by 2x

---

## 12. CSSP SOC Integration

### 12.1 Reporting Requirements

- Submit initial incident report to CSSP SOC within timelines in Section 5
- Provide updates every **4 hours** for Critical, **12 hours** for High
- Submit final incident report within **10 business days** of closure

### 12.2 CSSP SOC Support

The CSSP SOC may provide:
- Threat intelligence and IOC correlation
- Network-level containment assistance
- Forensic analysis support
- Coordination with other affected organizations
- Situational awareness reporting

### 12.3 Information Sharing

- Share IOCs with CSSP SOC for cross-organizational defense
- Sanitize data before sharing to protect sources and methods
- All shared information marked: {{classification}}
- Follow TLP (Traffic Light Protocol) designations as directed by CSSP

---

## 13. Testing and Exercises

### 13.1 Exercise Schedule

| Exercise Type | Frequency | Participants | Duration |
|--------------|-----------|-------------|----------|
| Tabletop Exercise | Annually (minimum) | Full IR team + System Owner | 2-4 hours |
| Communications Test | Quarterly | All POCs in escalation matrix | 1 hour |
| Functional Exercise | Annually | IR team + SOC + system admins | 4-8 hours |
| Full-Scale Exercise | Every 2 years | All stakeholders + CSSP | 1-2 days |

### 13.2 Exercise Requirements

- Scenarios must cover Critical and High severity incidents
- Include at least one CUI spillage scenario per year
- Document lessons learned within 5 business days of exercise
- Update this plan within 30 days if exercises reveal gaps

### 13.3 After-Action Reviews

Every incident (real or exercise) must produce an after-action report:
1. Timeline of events
2. What worked well
3. What needs improvement
4. Specific action items with owners and deadlines
5. Updates required to this plan

---

## 14. Plan Maintenance

### 14.1 Review Schedule

- **Annual review** (minimum) — full plan review and update
- **Post-incident review** — within 30 days of any significant incident
- **Post-exercise review** — within 30 days of any IR exercise
- **Personnel change** — update within 10 business days when key POCs change
- **System change** — update when authorization boundary changes

### 14.2 Distribution

This plan is distributed to all personnel listed in Section 3 (Roles and
Responsibilities) and stored in the project compliance directory.

### 14.3 Approval

| Role | Name | Signature | Date |
|------|------|-----------|------|
| System Owner | {{system_owner}} | _________________ | __________ |
| ISSM | {{issm_name}} | _________________ | __________ |
| ISSO | {{isso_name}} | _________________ | __________ |
| Authorizing Official | {{authorizing_official}} | _________________ | __________ |

---

*Generated by ICDEV Compliance Engine v{{icdev_version}} on {{generation_date}}*
"""


# ---------------------------------------------------------------------------
# Variable substitution
# ---------------------------------------------------------------------------

def _build_variables(project, defaults):
    """Build the {{variable}} substitution dictionary."""
    now = datetime.utcnow()

    # Pull infra settings from project defaults if available
    infra = defaults.get("infrastructure", {})
    cloud = infra.get("cloud", "aws-govcloud")
    region = infra.get("region", "us-gov-west-1")
    default_env = f"AWS GovCloud ({region})" if "govcloud" in cloud.lower() else f"{cloud} ({region})"

    # Reporting timelines (hardcoded per CSSP SOC standards)
    reporting = {
        "critical": "1 hour",
        "high": "24 hours",
        "moderate": "72 hours",
    }

    variables = {
        # System identification
        "system_name": project.get("name", "UNNAMED SYSTEM"),
        "system_id": project.get("id", ""),
        "classification": "CUI // SP-CTI",

        # Reporting timelines
        "reporting_critical": reporting["critical"],
        "reporting_high": reporting["high"],
        "reporting_moderate": reporting["moderate"],

        # POC placeholders — filled from project metadata or left as TBD
        "system_owner": project.get("system_owner", "[TBD]"),
        "issm_name": project.get("issm_name", "[TBD]"),
        "isso_name": project.get("isso_name", "[TBD]"),
        "incident_commander": project.get("incident_commander", "[TBD]"),
        "ir_team_lead": project.get("ir_team_lead", "[TBD]"),
        "soc_analyst": project.get("soc_analyst", "[TBD]"),
        "system_admin": project.get("system_admin", "[TBD]"),
        "comms_lead": project.get("comms_lead", "[TBD]"),
        "legal_contact": project.get("legal_contact", "[TBD]"),
        "authorizing_official": project.get("authorizing_official", "[TBD]"),

        # CSSP SOC contact placeholders
        "cssp_soc_phone": project.get("cssp_soc_phone", "[TBD — Obtain from CSSP]"),
        "cssp_soc_email": project.get("cssp_soc_email", "[TBD — Obtain from CSSP]"),
        "cssp_soc_portal": project.get("cssp_soc_portal", "[TBD — Obtain from CSSP]"),

        # Plan metadata
        "plan_version": "1.0",
        "plan_date": now.strftime("%Y-%m-%d"),
        "prepared_by": "ICDEV Compliance Engine",
        "revision_description": "Initial Incident Response Plan generation",

        # Environment
        "operating_environment": project.get("operating_environment", default_env),
        "system_boundary": project.get(
            "system_boundary",
            "[TBD - Define authorization boundary]",
        ),

        # Generation metadata
        "icdev_version": "1.0",
        "generation_date": now.strftime("%Y-%m-%d %H:%M UTC"),
    }

    return variables


def _substitute_variables(template, variables):
    """Replace {{variable_name}} placeholders in the template."""
    def replacer(match):
        key = match.group(1).strip()
        return str(variables.get(key, match.group(0)))
    return re.sub(r"\{\{(\w+)\}\}", replacer, template)


# ---------------------------------------------------------------------------
# Core generation function
# ---------------------------------------------------------------------------

def generate_ir_plan(project_id, output_dir=None, db_path=None):
    """Generate a complete Incident Response Plan for a project.

    Args:
        project_id: The project identifier.
        output_dir:  Override output directory (file is written inside it).
        db_path:     Override database path.

    Returns:
        dict with ``file_path``, ``version``, and ``project_id``.
    """
    conn = _get_connection(db_path)
    try:
        # 1. Load project data
        project = _get_project_data(conn, project_id)

        # 2. Load IR template (falls back to generated default)
        template = _load_template()
        if template is None:
            template = _generate_default_template()

        # 3. Load project defaults
        defaults = _load_project_defaults()

        # 4. Build variable substitution dict
        variables = _build_variables(project, defaults)

        # 5. Determine version — increment if a prior plan exists
        existing = conn.execute(
            """SELECT file_path FROM audit_trail
               WHERE project_id = ? AND event_type = 'ir_plan_generated'
               ORDER BY created_at DESC LIMIT 1""",
            (project_id,),
        ).fetchone()

        if existing:
            # Attempt to parse version from prior filename
            prior = existing["file_path"] if existing else ""
            ver_match = re.search(r"-v(\d+(?:\.\d+)?)", prior)
            if ver_match:
                prev = float(ver_match.group(1))
                new_version = f"{prev + 1.0:.1f}"
            else:
                new_version = "2.0"
            variables["plan_version"] = new_version
            variables["revision_description"] = (
                f"Updated Incident Response Plan (supersedes v{ver_match.group(1) if ver_match else '1.0'})"
            )

        version = variables["plan_version"]

        # 5. Apply variable substitution
        content = _substitute_variables(template, variables)

        # 6. Apply CUI markings
        cui_config = _load_cui_config()
        doc_header = cui_config.get("document_header", "CUI // SP-CTI").strip()
        doc_footer = cui_config.get("document_footer", "CUI // SP-CTI").strip()
        content = f"{doc_header}\n\n{content}\n\n{doc_footer}\n"

        # 7. Determine output path
        if output_dir:
            out_dir = Path(output_dir)
        else:
            dir_path = project.get("directory_path", "")
            if dir_path:
                out_dir = Path(dir_path) / "compliance"
            else:
                out_dir = BASE_DIR / ".tmp" / "compliance" / project_id

        out_dir.mkdir(parents=True, exist_ok=True)
        project_name = re.sub(r"[^a-zA-Z0-9_-]", "_", project.get("name", project_id))
        out_file = out_dir / f"incident-response-plan-v{version}.md"

        with open(out_file, "w", encoding="utf-8") as f:
            f.write(content)

        # 8. Log audit event
        _log_audit_event(conn, project_id, f"IR Plan v{version} generated", {
            "version": version,
            "system_name": variables["system_name"],
            "output_file": str(out_file),
            "classification": variables["classification"],
        }, out_file)

        result = {
            "file_path": str(out_file),
            "version": version,
            "project_id": project_id,
        }

        print("Incident Response Plan generated successfully:")
        print(f"  File: {out_file}")
        print(f"  Version: {version}")
        print(f"  System: {variables['system_name']}")
        print(f"  Classification: {variables['classification']}")

        return result

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate Incident Response Plan"
    )
    parser.add_argument("--project-id", required=True, help="Project ID")
    parser.add_argument("--output-dir", help="Output directory")
    parser.add_argument(
        "--db-path", type=Path, default=DB_PATH, help="Database path"
    )
    args = parser.parse_args()

    try:
        result = generate_ir_plan(
            args.project_id, args.output_dir, args.db_path
        )
        print(json.dumps(result, indent=2))
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
