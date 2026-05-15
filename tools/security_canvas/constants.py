# [CUI // SP-CTI]
"""ICDEV™ Security Design Canvas — Constants and reference data.

Security object palette, STRIDE threat categories, NIST 800-53 control families,
risk matrix, assessment rules, and NDC→SDC node type mapping.

No external dependencies — stdlib only.
"""

# ── Security Object Palette ──────────────────────────────────────────────────
# Four categories, ~30 total objects for the canvas toolbox.

SECURITY_OBJECTS = {
    "assets": [
        {
            "type": "asset-server",
            "label": "Server / VM",
            "icon": "server",
            "desc": "Compute instance (physical or virtual)",
        },
        {"type": "asset-database", "label": "Database", "icon": "database", "desc": "Relational or NoSQL data store"},
        {"type": "asset-client", "label": "Client / User", "icon": "user", "desc": "End-user device or browser"},
        {"type": "asset-storage", "label": "Storage (S3/Blob)", "icon": "hdd", "desc": "Object or block storage"},
        {
            "type": "asset-network",
            "label": "Network Device",
            "icon": "sitemap",
            "desc": "Router, switch, or load balancer",
        },
        {"type": "asset-container", "label": "Container / Pod", "icon": "cube", "desc": "Docker container or K8s pod"},
        {
            "type": "asset-lambda",
            "label": "Serverless Function",
            "icon": "bolt",
            "desc": "Lambda, Cloud Function, or Azure Function",
        },
        {
            "type": "asset-registry",
            "label": "Container Registry",
            "icon": "archive",
            "desc": "Image registry (ECR, ACR, Harbor)",
        },
        {
            "type": "asset-vdi-host",
            "label": "VDI Session Host",
            "icon": "session-host",
            "desc": "Virtual desktop session host (RDSH/AVD/Citrix VDA)",
            "nist_families": ["SC", "CM", "SI"],
        },
        {
            "type": "asset-thin-client",
            "label": "Thin/Zero Client",
            "icon": "thin-client",
            "desc": "Thin or zero client endpoint (IGEL/Wyse/Teradici)",
            "nist_families": ["IA", "SC", "PE"],
        },
        {
            "type": "asset-profile-store",
            "label": "Profile Store",
            "icon": "profile-store",
            "desc": "User profile container storage (FSLogix/UPM)",
            "nist_families": ["SC", "AC", "MP"],
        },
    ],
    "controls": [
        {
            "type": "ctrl-firewall",
            "label": "Firewall / WAF",
            "icon": "shield-alt",
            "desc": "Network or web application firewall",
        },
        {
            "type": "ctrl-idp",
            "label": "IdP / MFA",
            "icon": "id-badge",
            "desc": "Identity provider with multi-factor authentication",
        },
        {
            "type": "ctrl-kms",
            "label": "KMS / HSM",
            "icon": "key",
            "desc": "Key management service or hardware security module",
        },
        {
            "type": "ctrl-siem",
            "label": "SIEM / SOC",
            "icon": "eye",
            "desc": "Security information and event management",
        },
        {
            "type": "ctrl-ids",
            "label": "IDS / IPS",
            "icon": "exclamation-triangle",
            "desc": "Intrusion detection / prevention system",
        },
        {"type": "ctrl-pam", "label": "PAM", "icon": "user-lock", "desc": "Privileged access management"},
        {
            "type": "ctrl-scanner",
            "label": "Vulnerability Scanner",
            "icon": "search",
            "desc": "SAST, DAST, or infrastructure scanner",
        },
        {
            "type": "ctrl-encryption",
            "label": "Encryptor",
            "icon": "lock",
            "desc": "Encryption appliance or TLS terminator",
        },
        {
            "type": "ctrl-session-policy",
            "label": "Session Policy",
            "icon": "session-policy",
            "desc": "VDI session controls — clipboard, USB, printing, watermark, timeout",
            "nist_controls": ["AC-4", "AC-11", "AC-12", "SC-10"],
        },
        {
            "type": "ctrl-vdi-gateway",
            "label": "VDI Gateway",
            "icon": "vdi-gateway",
            "desc": "Secure remote access gateway for VDI (RD Gateway/Citrix GW/UAG)",
            "nist_controls": ["AC-17", "SC-7", "SC-8"],
        },
        {
            "type": "ctrl-image-hardening",
            "label": "Image Hardening",
            "icon": "image-harden",
            "desc": "Golden image STIG hardening and integrity verification",
            "nist_controls": ["CM-2", "CM-6", "SI-7", "SA-10"],
        },
    ],
    "threats": [
        {
            "type": "threat-actor",
            "label": "Threat Actor",
            "icon": "skull-crossbones",
            "desc": "External attacker or insider threat",
        },
        {"type": "threat-malware", "label": "Malware", "icon": "bug", "desc": "Virus, ransomware, or worm"},
        {
            "type": "threat-phishing",
            "label": "Phishing",
            "icon": "envelope-open",
            "desc": "Social engineering attack via email",
        },
        {
            "type": "threat-exploit",
            "label": "Exploit",
            "icon": "crosshairs",
            "desc": "Known or zero-day vulnerability exploit",
        },
        {"type": "threat-dos", "label": "DoS / DDoS", "icon": "network-wired", "desc": "Denial of service attack"},
        {
            "type": "threat-supply",
            "label": "Supply Chain",
            "icon": "truck",
            "desc": "Compromised dependency or build system",
        },
        {
            "type": "threat-insider",
            "label": "Insider Threat",
            "icon": "user-secret",
            "desc": "Malicious or negligent insider",
        },
        {
            "type": "threat-session-hijack",
            "label": "Session Hijack",
            "icon": "session-hijack",
            "desc": "VDI session takeover via stolen token, credential replay, or MitM",
            "stride": "S",
            "mitre": "T1563",
        },
        {
            "type": "threat-clipboard-exfil",
            "label": "Clipboard Exfil",
            "icon": "clipboard-exfil",
            "desc": "Data exfiltration via clipboard copy/paste from virtual desktop",
            "stride": "I",
            "mitre": "T1115",
        },
        {
            "type": "threat-gpu-escape",
            "label": "GPU/VM Escape",
            "icon": "gpu-escape",
            "desc": "Breakout from virtual desktop via GPU driver or hypervisor vulnerability",
            "stride": "E",
            "mitre": "T1611",
        },
        {
            "type": "threat-profile-tampering",
            "label": "Profile Tamper",
            "icon": "profile-tamper",
            "desc": "Manipulation of roaming profile or FSLogix container to persist malware",
            "stride": "T",
            "mitre": "T1547",
        },
    ],
    "boundaries": [
        {
            "type": "boundary-network",
            "label": "Network Zone",
            "icon": "border-all",
            "desc": "Network segmentation boundary (VLAN, VPC)",
        },
        {"type": "boundary-internet", "label": "Internet", "icon": "globe", "desc": "Public internet / untrusted zone"},
        {"type": "boundary-cloud", "label": "Cloud CSP", "icon": "cloud", "desc": "Cloud service provider boundary"},
        {"type": "boundary-bcap", "label": "BCAP", "icon": "shield", "desc": "DoD Boundary Cloud Access Point"},
        {
            "type": "boundary-authorization",
            "label": "Auth Boundary",
            "icon": "border-style",
            "desc": "FedRAMP authorization boundary",
        },
        {"type": "boundary-enclave", "label": "Enclave", "icon": "lock", "desc": "Classified or isolated enclave"},
        {
            "type": "boundary-dmz",
            "label": "DMZ",
            "icon": "minus-circle",
            "desc": "Demilitarized zone between trust levels",
        },
        {
            "type": "boundary-vdi-session",
            "label": "VDI Session Zone",
            "icon": "vdi-session",
            "desc": "Trust boundary isolating VDI session hosts from infrastructure",
            "classification_levels": ["CUI", "SECRET"],
        },
    ],
    "digital_twin": [
        {
            "type": "twin-attack-graph",
            "label": "Attack Graph Twin",
            "icon": "ag",
            "desc": "BAS-style digital twin — snapshots the STRIDE/attack graph and enumerates all paths from entry points to high-value targets",
        },
        {
            "type": "twin-posture",
            "label": "Security Posture Twin",
            "icon": "sp",
            "desc": "Continuous security posture twin — tracks risk score delta across topology changes with PASS/WARN/FAIL verdict",
        },
        {
            "type": "twin-bas",
            "label": "BAS Replay Engine",
            "icon": "br",
            "desc": "Breach and Attack Simulation replay — maps Caldera ability IDs to ATT&CK technique IDs for automated path validation",
        },
        {
            "type": "twin-mitre-delta",
            "label": "MITRE Delta Detector",
            "icon": "md",
            "desc": "Surfaces new or resolved ATT&CK technique exposures introduced by a proposed topology change",
        },
    ],
}

# ── STRIDE Threat Categories ────────────────────────────────────────────────

STRIDE_CATEGORIES = {
    "S": {
        "name": "Spoofing",
        "description": "Pretending to be something or someone other than yourself.",
        "nist_controls": ["IA-2", "IA-3", "IA-5", "IA-8", "SC-23"],
        "typical_targets": ["asset-client", "ctrl-idp", "asset-server"],
    },
    "T": {
        "name": "Tampering",
        "description": "Modifying data or code without authorization.",
        "nist_controls": ["SI-7", "SC-8", "SC-28", "AU-10", "CM-3"],
        "typical_targets": ["asset-database", "asset-storage", "asset-server"],
    },
    "R": {
        "name": "Repudiation",
        "description": "Claiming to not have performed an action.",
        "nist_controls": ["AU-2", "AU-3", "AU-6", "AU-10", "AU-12"],
        "typical_targets": ["asset-client", "asset-server", "ctrl-siem"],
    },
    "I": {
        "name": "Information Disclosure",
        "description": "Exposing information to unauthorized individuals.",
        "nist_controls": ["SC-8", "SC-13", "SC-28", "AC-3", "AC-4"],
        "typical_targets": ["asset-database", "asset-storage", "ctrl-kms"],
    },
    "D": {
        "name": "Denial of Service",
        "description": "Denying or degrading service to valid users.",
        "nist_controls": ["SC-5", "CP-7", "CP-8", "CP-10", "SI-17"],
        "typical_targets": ["asset-server", "ctrl-firewall", "asset-network"],
    },
    "E": {
        "name": "Elevation of Privilege",
        "description": "Gaining capabilities without proper authorization.",
        "nist_controls": ["AC-6", "AC-2", "CM-5", "CM-7", "SC-4"],
        "typical_targets": ["asset-server", "ctrl-pam", "asset-container"],
    },
}

# ── Risk Matrix (5x5) ───────────────────────────────────────────────────────
# Likelihood × Impact → Risk Score (1–25)

LIKELIHOOD_LEVELS = ["very_low", "low", "medium", "high", "very_high"]
IMPACT_LEVELS = ["very_low", "low", "medium", "high", "very_high"]

RISK_MATRIX = {
    # (likelihood_idx, impact_idx) → risk_score
    (0, 0): 1,
    (0, 1): 2,
    (0, 2): 3,
    (0, 3): 4,
    (0, 4): 5,
    (1, 0): 2,
    (1, 1): 4,
    (1, 2): 6,
    (1, 3): 8,
    (1, 4): 10,
    (2, 0): 3,
    (2, 1): 6,
    (2, 2): 9,
    (2, 3): 12,
    (2, 4): 15,
    (3, 0): 4,
    (3, 1): 8,
    (3, 2): 12,
    (3, 3): 16,
    (3, 4): 20,
    (4, 0): 5,
    (4, 1): 10,
    (4, 2): 15,
    (4, 3): 20,
    (4, 4): 25,
}

# ── NIST 800-53 Control Families ────────────────────────────────────────────

NIST_CONTROL_FAMILIES = {
    "AC": {
        "name": "Access Control",
        "examples": ["AC-2 Account Management", "AC-3 Access Enforcement", "AC-6 Least Privilege"],
    },
    "AU": {
        "name": "Audit and Accountability",
        "examples": ["AU-2 Event Logging", "AU-3 Content of Audit Records", "AU-6 Audit Review"],
    },
    "AT": {"name": "Awareness and Training", "examples": ["AT-2 Literacy Training", "AT-3 Role-Based Training"]},
    "CM": {
        "name": "Configuration Management",
        "examples": ["CM-2 Baseline Configuration", "CM-3 Change Control", "CM-7 Least Functionality"],
    },
    "CP": {
        "name": "Contingency Planning",
        "examples": ["CP-7 Alternate Processing Site", "CP-8 Telecommunications", "CP-10 Recovery"],
    },
    "IA": {
        "name": "Identification and Authentication",
        "examples": ["IA-2 User Identification", "IA-5 Authenticator Mgmt", "IA-8 Non-Org Users"],
    },
    "IR": {
        "name": "Incident Response",
        "examples": ["IR-4 Incident Handling", "IR-5 Incident Monitoring", "IR-6 Incident Reporting"],
    },
    "MA": {"name": "Maintenance", "examples": ["MA-2 Controlled Maintenance", "MA-4 Nonlocal Maintenance"]},
    "MP": {
        "name": "Media Protection",
        "examples": ["MP-2 Media Access", "MP-4 Media Storage", "MP-6 Media Sanitization"],
    },
    "PE": {
        "name": "Physical and Environmental",
        "examples": ["PE-2 Physical Access", "PE-3 Physical Access Control", "PE-6 Monitoring"],
    },
    "PL": {"name": "Planning", "examples": ["PL-2 System Security Plan", "PL-4 Rules of Behavior"]},
    "PM": {"name": "Program Management", "examples": ["PM-1 InfoSec Program Plan", "PM-9 Risk Management Strategy"]},
    "PS": {"name": "Personnel Security", "examples": ["PS-3 Personnel Screening", "PS-4 Personnel Termination"]},
    "RA": {"name": "Risk Assessment", "examples": ["RA-3 Risk Assessment", "RA-5 Vulnerability Monitoring"]},
    "SA": {
        "name": "System and Services Acquisition",
        "examples": ["SA-4 Acquisition Process", "SA-11 Developer Testing"],
    },
    "SC": {
        "name": "System and Communications Protection",
        "examples": ["SC-7 Boundary Protection", "SC-8 Transmission Confidentiality", "SC-13 Cryptographic Protection"],
    },
    "SI": {
        "name": "System and Information Integrity",
        "examples": ["SI-2 Flaw Remediation", "SI-4 System Monitoring", "SI-7 Software Integrity"],
    },
    "SR": {
        "name": "Supply Chain Risk Management",
        "examples": ["SR-2 Supply Chain Risk Plan", "SR-3 Supply Chain Controls"],
    },
    "PT": {
        "name": "PII Processing and Transparency",
        "examples": ["PT-2 Authority to Process PII", "PT-3 PII Processing Purposes"],
    },
    "CA": {
        "name": "Assessment, Authorization, and Monitoring",
        "examples": ["CA-2 Control Assessments", "CA-6 Authorization", "CA-7 Continuous Monitoring"],
    },
}

# ── Security Assessment Rules ───────────────────────────────────────────────
# 35 deterministic checks against design graph data.

SECURITY_ASSESSMENT_RULES = [
    # Authentication (CAT1)
    {
        "id": "SEC-AUTH-001",
        "title": "All data flows must be authenticated",
        "severity": "CAT1",
        "category": "authentication",
        "check": "all_flows_authenticated",
    },
    {
        "id": "SEC-AUTH-002",
        "title": "IdP/MFA required for user-facing assets",
        "severity": "CAT1",
        "category": "authentication",
        "check": "idp_for_user_assets",
    },
    {
        "id": "SEC-AUTH-003",
        "title": "PAM required for privileged access",
        "severity": "CAT2",
        "category": "authentication",
        "check": "pam_for_privileged",
    },
    # Encryption (CAT1)
    {
        "id": "SEC-ENC-001",
        "title": "All boundary-crossing flows must be encrypted",
        "severity": "CAT1",
        "category": "encryption",
        "check": "boundary_flows_encrypted",
    },
    {
        "id": "SEC-ENC-002",
        "title": "KMS/HSM present for key management",
        "severity": "CAT1",
        "category": "encryption",
        "check": "kms_present",
    },
    {
        "id": "SEC-ENC-003",
        "title": "Data at rest encryption for databases",
        "severity": "CAT1",
        "category": "encryption",
        "check": "db_encryption_at_rest",
    },
    # Segmentation (CAT1)
    {
        "id": "SEC-SEG-001",
        "title": "Trust boundaries defined between zones",
        "severity": "CAT1",
        "category": "segmentation",
        "check": "boundaries_defined",
    },
    {
        "id": "SEC-SEG-002",
        "title": "Firewall between internet and internal assets",
        "severity": "CAT1",
        "category": "segmentation",
        "check": "firewall_at_boundary",
    },
    {
        "id": "SEC-SEG-003",
        "title": "No direct internet-to-database flows",
        "severity": "CAT1",
        "category": "segmentation",
        "check": "no_direct_inet_db",
    },
    # Logging & Monitoring (CAT2)
    {
        "id": "SEC-LOG-001",
        "title": "SIEM present in design",
        "severity": "CAT2",
        "category": "logging",
        "check": "siem_present",
    },
    {
        "id": "SEC-LOG-002",
        "title": "All assets send logs to SIEM",
        "severity": "CAT2",
        "category": "logging",
        "check": "all_assets_logged",
    },
    {
        "id": "SEC-LOG-003",
        "title": "Database audit logging enabled",
        "severity": "CAT2",
        "category": "logging",
        "check": "db_audit_logging",
    },
    # Monitoring (CAT2)
    {
        "id": "SEC-MON-001",
        "title": "IDS/IPS present for network monitoring",
        "severity": "CAT2",
        "category": "monitoring",
        "check": "ids_present",
    },
    {
        "id": "SEC-MON-002",
        "title": "Vulnerability scanner in design",
        "severity": "CAT2",
        "category": "monitoring",
        "check": "scanner_present",
    },
    # Access Control (CAT2)
    {
        "id": "SEC-AC-001",
        "title": "Least privilege: no shared admin accounts",
        "severity": "CAT2",
        "category": "access_control",
        "check": "no_shared_admin",
    },
    {
        "id": "SEC-AC-002",
        "title": "Service-to-service authentication (mTLS/tokens)",
        "severity": "CAT2",
        "category": "access_control",
        "check": "s2s_auth",
    },
    # Data Protection (CAT2)
    {
        "id": "SEC-DP-001",
        "title": "Data classification labels on all storage assets",
        "severity": "CAT2",
        "category": "data_protection",
        "check": "data_classification",
    },
    {
        "id": "SEC-DP-002",
        "title": "DLP controls for sensitive data egress",
        "severity": "CAT2",
        "category": "data_protection",
        "check": "dlp_present",
    },
    # Incident Response (CAT2)
    {
        "id": "SEC-IR-001",
        "title": "SIEM connected to alerting pipeline",
        "severity": "CAT2",
        "category": "incident_response",
        "check": "siem_alerting",
    },
    {
        "id": "SEC-IR-002",
        "title": "Incident response runbook referenced",
        "severity": "CAT3",
        "category": "incident_response",
        "check": "ir_runbook",
    },
    # Supply Chain (CAT2)
    {
        "id": "SEC-SC-001",
        "title": "Container registry with admission control",
        "severity": "CAT2",
        "category": "supply_chain",
        "check": "registry_admission",
    },
    {
        "id": "SEC-SC-002",
        "title": "SBOM generation for all deployable artifacts",
        "severity": "CAT2",
        "category": "supply_chain",
        "check": "sbom_present",
    },
    # General (CAT3)
    {
        "id": "SEC-GEN-001",
        "title": "All assets labeled with descriptive names",
        "severity": "CAT3",
        "category": "documentation",
        "check": "assets_labeled",
    },
    {
        "id": "SEC-GEN-002",
        "title": "Design has at least one trust boundary",
        "severity": "CAT3",
        "category": "documentation",
        "check": "has_boundaries",
    },
    {
        "id": "SEC-GEN-003",
        "title": "Threats identified and documented",
        "severity": "CAT3",
        "category": "documentation",
        "check": "threats_documented",
    },
    # Endpoint Protection (CAT2)
    {
        "id": "SEC-EDR-001",
        "title": "EDR/XDR deployed on servers and endpoints",
        "severity": "CAT2",
        "category": "monitoring",
        "check": "edr_present",
    },
    # Cloud Posture (CAT2)
    {
        "id": "SEC-CSPM-001",
        "title": "CSPM scanning cloud infrastructure",
        "severity": "CAT2",
        "category": "monitoring",
        "check": "cspm_present",
    },
    # Backup & DR (CAT2)
    {
        "id": "SEC-DR-001",
        "title": "Backup and disaster recovery strategy present",
        "severity": "CAT2",
        "category": "contingency",
        "check": "backup_present",
    },
    # Secret Management (CAT2)
    {
        "id": "SEC-SECRET-001",
        "title": "Centralized secret management (Vault/KMS)",
        "severity": "CAT2",
        "category": "access_control",
        "check": "secret_mgmt_present",
    },
    # mTLS (CAT1)
    {
        "id": "SEC-MTLS-001",
        "title": "mTLS enforced on service-to-service flows",
        "severity": "CAT1",
        "category": "encryption",
        "check": "mtls_s2s",
    },
    # API Gateway (CAT2)
    {
        "id": "SEC-APIGW-001",
        "title": "API gateway with WAF for external APIs",
        "severity": "CAT2",
        "category": "segmentation",
        "check": "api_gateway_protected",
    },
    # Zero Trust (CAT1)
    {
        "id": "SEC-ZT-001",
        "title": "Zero Trust: all flows both authenticated AND encrypted",
        "severity": "CAT1",
        "category": "authentication",
        "check": "zero_trust_posture",
    },
    # Container Admission (CAT2)
    {
        "id": "SEC-ADMIT-001",
        "title": "Container admission control (Kyverno/OPA)",
        "severity": "CAT2",
        "category": "supply_chain",
        "check": "admission_control_present",
    },
    # Encryption Algorithm Compliance (CAT2)
    {
        "id": "SEC-CRYPTO-001",
        "title": "FIPS 140-2/3 validated cryptographic modules",
        "severity": "CAT2",
        "category": "encryption",
        "check": "fips_crypto_validated",
    },
    # Configuration Hardening (CAT2)
    {
        "id": "SEC-HARDEN-001",
        "title": "OS/platform hardened to CIS/STIG baseline",
        "severity": "CAT2",
        "category": "configuration",
        "check": "hardening_baseline",
    },
    # VDI Security Rules
    {
        "id": "SEC-VDI-001",
        "title": "VDI session policy enforced",
        "severity": "CAT1",
        "category": "vdi_security",
        "description": "All VDI session hosts must have a session policy control (clipboard/USB/print restrictions) — data exfiltration risk without policy enforcement.",
        "check": "vdi_session_policy",
    },
    {
        "id": "SEC-VDI-002",
        "title": "VDI gateway required for external access",
        "severity": "CAT1",
        "category": "vdi_security",
        "description": "External users must connect through a VDI gateway — no direct RDP/PCoIP from internet to session hosts.",
        "check": "vdi_gateway_required",
    },
    {
        "id": "SEC-VDI-003",
        "title": "Session hosts in dedicated trust boundary",
        "severity": "CAT2",
        "category": "vdi_security",
        "description": "VDI session hosts must reside within a dedicated VDI session zone boundary, isolated from general compute.",
        "check": "vdi_boundary_isolation",
    },
    {
        "id": "SEC-VDI-004",
        "title": "Profile store encrypted at rest",
        "severity": "CAT1",
        "category": "vdi_security",
        "description": "FSLogix/UPM profile stores must have KMS/HSM encryption control connected — user profiles contain CUI.",
        "check": "vdi_profile_encrypted",
    },
    {
        "id": "SEC-VDI-005",
        "title": "Thin clients authenticated before VDI access",
        "severity": "CAT2",
        "category": "vdi_security",
        "description": "Thin/zero client assets must have IdP/MFA control connected — unauthenticated endpoints are session hijack vectors.",
        "check": "vdi_endpoint_authenticated",
    },
    {
        "id": "SEC-VDI-006",
        "title": "Golden image integrity verified",
        "severity": "CAT2",
        "category": "vdi_security",
        "description": "VDI session hosts should have image hardening control — ensures STIG-compliant golden images and detects drift.",
        "check": "vdi_image_integrity",
    },
]

# ── NDC → SDC Node Type Mapping ─────────────────────────────────────────────
# Maps Network Design Canvas node types to Security Design Canvas asset types.

NODE_TYPE_MAPPING = {
    # Network devices → assets
    "router": "asset-network",
    "switch-l3": "asset-network",
    "switch-l2": "asset-network",
    "server": "asset-server",
    "wap": "asset-network",
    "mpls-pe": "asset-network",
    "mpls-p": "asset-network",
    "route-reflector": "asset-network",
    # Security devices → controls
    "firewall": "ctrl-firewall",
    "aws-nfw": "ctrl-firewall",
    "az-fw": "ctrl-firewall",
    "gcp-armor": "ctrl-firewall",
    "aws-waf": "ctrl-firewall",
    "az-nsg": "ctrl-firewall",
    "oci-waf": "ctrl-firewall",
    "oci-nsg": "ctrl-firewall",
    "siem": "ctrl-siem",
    "network-tap": "ctrl-ids",
    "hsm": "ctrl-kms",
    # Cloud constructs → boundaries
    "aws-vpc": "boundary-cloud",
    "az-vnet": "boundary-cloud",
    "gcp-vpc": "boundary-cloud",
    "oci-vcn": "boundary-cloud",
    "cloud": "boundary-internet",
    # Encryption
    "fips-140-l1": "ctrl-encryption",
    "fips-140-l2": "ctrl-encryption",
    "fips-140-l3": "ctrl-encryption",
    "fips-140-l4": "ctrl-encryption",
    "type1-encryptor": "ctrl-encryption",
    "kg-175d": "ctrl-encryption",
    "kg-175g": "ctrl-encryption",
    "kg-250": "ctrl-encryption",
    "kg-340": "ctrl-encryption",
    "kg-245x": "ctrl-encryption",
    "kg-255": "ctrl-encryption",
    "macsec": "ctrl-encryption",
    # DNS / Cloud services
    "aws-r53": "asset-server",
    "az-dns": "asset-server",
    "gcp-dns": "asset-server",
    # Transit / hybrid
    "aws-dx": "asset-network",
    "az-er": "asset-network",
    "gcp-ic": "asset-network",
    "oci-fc": "asset-network",
    "aws-tgw": "asset-network",
    "az-vwan": "asset-network",
    "sdwan-overlay": "asset-network",
}

# ── MITRE ATT&CK Techniques ───────────────────────────────────────────────
# Maps tactic keys to tactic metadata and techniques with detection controls.

MITRE_ATTACK_TECHNIQUES = {
    "initial_access": {
        "tactic_id": "TA0001",
        "name": "Initial Access",
        "techniques": [
            {
                "id": "T1190",
                "name": "Exploit Public-Facing Application",
                "detectable_by": ["ctrl-firewall", "ctrl-ids"],
                "severity": "high",
            },
            {"id": "T1566", "name": "Phishing", "detectable_by": ["ctrl-idp"], "severity": "high"},
            {
                "id": "T1133",
                "name": "External Remote Services",
                "detectable_by": ["ctrl-firewall", "ctrl-pam"],
                "severity": "high",
            },
            {
                "id": "T1078",
                "name": "Valid Accounts",
                "detectable_by": ["ctrl-idp", "ctrl-siem"],
                "severity": "critical",
            },
        ],
    },
    "execution": {
        "tactic_id": "TA0002",
        "name": "Execution",
        "techniques": [
            {
                "id": "T1059",
                "name": "Command and Scripting Interpreter",
                "detectable_by": ["ctrl-edr", "ctrl-siem"],
                "severity": "high",
            },
            {
                "id": "T1203",
                "name": "Exploitation for Client Execution",
                "detectable_by": ["ctrl-edr", "ctrl-ids"],
                "severity": "high",
            },
        ],
    },
    "persistence": {
        "tactic_id": "TA0003",
        "name": "Persistence",
        "techniques": [
            {
                "id": "T1098",
                "name": "Account Manipulation",
                "detectable_by": ["ctrl-idp", "ctrl-siem", "ctrl-pam"],
                "severity": "high",
            },
            {"id": "T1136", "name": "Create Account", "detectable_by": ["ctrl-idp", "ctrl-siem"], "severity": "medium"},
        ],
    },
    "privilege_escalation": {
        "tactic_id": "TA0004",
        "name": "Privilege Escalation",
        "techniques": [
            {
                "id": "T1068",
                "name": "Exploitation for Privilege Escalation",
                "detectable_by": ["ctrl-edr", "ctrl-ids"],
                "severity": "critical",
            },
            {"id": "T1078", "name": "Valid Accounts", "detectable_by": ["ctrl-pam", "ctrl-siem"], "severity": "high"},
        ],
    },
    "defense_evasion": {
        "tactic_id": "TA0005",
        "name": "Defense Evasion",
        "techniques": [
            {"id": "T1070", "name": "Indicator Removal", "detectable_by": ["ctrl-siem"], "severity": "high"},
            {
                "id": "T1562",
                "name": "Impair Defenses",
                "detectable_by": ["ctrl-siem", "ctrl-edr"],
                "severity": "critical",
            },
        ],
    },
    "credential_access": {
        "tactic_id": "TA0006",
        "name": "Credential Access",
        "techniques": [
            {"id": "T1110", "name": "Brute Force", "detectable_by": ["ctrl-idp", "ctrl-siem"], "severity": "high"},
            {
                "id": "T1555",
                "name": "Credentials from Password Stores",
                "detectable_by": ["ctrl-pam", "ctrl-dlp"],
                "severity": "critical",
            },
            {
                "id": "T1552",
                "name": "Unsecured Credentials",
                "detectable_by": ["ctrl-scanner", "ctrl-dlp"],
                "severity": "high",
            },
        ],
    },
    "discovery": {
        "tactic_id": "TA0007",
        "name": "Discovery",
        "techniques": [
            {
                "id": "T1046",
                "name": "Network Service Discovery",
                "detectable_by": ["ctrl-ids", "ctrl-siem"],
                "severity": "medium",
            },
            {"id": "T1087", "name": "Account Discovery", "detectable_by": ["ctrl-siem"], "severity": "medium"},
        ],
    },
    "lateral_movement": {
        "tactic_id": "TA0008",
        "name": "Lateral Movement",
        "techniques": [
            {
                "id": "T1021",
                "name": "Remote Services",
                "detectable_by": ["ctrl-pam", "ctrl-ids", "ctrl-firewall"],
                "severity": "high",
            },
            {
                "id": "T1210",
                "name": "Exploitation of Remote Services",
                "detectable_by": ["ctrl-ids", "ctrl-edr"],
                "severity": "critical",
            },
        ],
    },
    "collection": {
        "tactic_id": "TA0009",
        "name": "Collection",
        "techniques": [
            {
                "id": "T1005",
                "name": "Data from Local System",
                "detectable_by": ["ctrl-dlp", "ctrl-edr"],
                "severity": "high",
            },
            {
                "id": "T1530",
                "name": "Data from Cloud Storage",
                "detectable_by": ["ctrl-dlp", "ctrl-cspm"],
                "severity": "high",
            },
        ],
    },
    "exfiltration": {
        "tactic_id": "TA0010",
        "name": "Exfiltration",
        "techniques": [
            {
                "id": "T1041",
                "name": "Exfiltration Over C2 Channel",
                "detectable_by": ["ctrl-firewall", "ctrl-dlp", "ctrl-siem"],
                "severity": "critical",
            },
            {
                "id": "T1048",
                "name": "Exfiltration Over Alternative Protocol",
                "detectable_by": ["ctrl-firewall", "ctrl-ids", "ctrl-dlp"],
                "severity": "critical",
            },
        ],
    },
    "impact": {
        "tactic_id": "TA0040",
        "name": "Impact",
        "techniques": [
            {
                "id": "T1486",
                "name": "Data Encrypted for Impact (Ransomware)",
                "detectable_by": ["ctrl-edr", "ctrl-siem"],
                "severity": "critical",
            },
            {
                "id": "T1485",
                "name": "Data Destruction",
                "detectable_by": ["ctrl-siem", "ctrl-edr"],
                "severity": "critical",
            },
            {
                "id": "T1499",
                "name": "Endpoint Denial of Service",
                "detectable_by": ["ctrl-firewall", "ctrl-ids"],
                "severity": "high",
            },
        ],
    },
}

# ── Compliance Crosswalk (NIST 800-53 → FedRAMP → CMMC L2) ────────────────
# Maps NIST 800-53 controls to equivalent controls in FedRAMP and CMMC.

COMPLIANCE_CROSSWALK = {
    "AC-2": {"fedramp": "AC-2", "cmmc": "AC.L2-3.1.1", "description": "Account Management"},
    "AC-3": {"fedramp": "AC-3", "cmmc": "AC.L2-3.1.2", "description": "Access Enforcement"},
    "AC-6": {"fedramp": "AC-6", "cmmc": "AC.L2-3.1.5", "description": "Least Privilege"},
    "AU-2": {"fedramp": "AU-2", "cmmc": "AU.L2-3.3.1", "description": "Event Logging"},
    "AU-3": {"fedramp": "AU-3", "cmmc": "AU.L2-3.3.1", "description": "Content of Audit Records"},
    "AU-6": {"fedramp": "AU-6", "cmmc": "AU.L2-3.3.5", "description": "Audit Review"},
    "AU-10": {"fedramp": "AU-10", "cmmc": "AU.L2-3.3.2", "description": "Non-Repudiation"},
    "AU-12": {"fedramp": "AU-12", "cmmc": "AU.L2-3.3.1", "description": "Audit Record Generation"},
    "CA-2": {"fedramp": "CA-2", "cmmc": "CA.L2-3.12.1", "description": "Control Assessments"},
    "CA-6": {"fedramp": "CA-6", "cmmc": "CA.L2-3.12.4", "description": "Authorization"},
    "CA-7": {"fedramp": "CA-7", "cmmc": "CA.L2-3.12.3", "description": "Continuous Monitoring"},
    "CM-2": {"fedramp": "CM-2", "cmmc": "CM.L2-3.4.1", "description": "Baseline Configuration"},
    "CM-3": {"fedramp": "CM-3", "cmmc": "CM.L2-3.4.3", "description": "Change Control"},
    "CM-5": {"fedramp": "CM-5", "cmmc": "CM.L2-3.4.5", "description": "Access Restrictions for Change"},
    "CM-7": {"fedramp": "CM-7", "cmmc": "CM.L2-3.4.6", "description": "Least Functionality"},
    "CP-7": {"fedramp": "CP-7", "cmmc": None, "description": "Alternate Processing Site"},
    "CP-8": {"fedramp": "CP-8", "cmmc": None, "description": "Telecommunications Services"},
    "CP-10": {"fedramp": "CP-10", "cmmc": None, "description": "System Recovery and Reconstitution"},
    "IA-2": {"fedramp": "IA-2", "cmmc": "IA.L2-3.5.1", "description": "User Identification and Authentication"},
    "IA-3": {"fedramp": "IA-3", "cmmc": "IA.L2-3.5.2", "description": "Device Identification"},
    "IA-5": {"fedramp": "IA-5", "cmmc": "IA.L2-3.5.7", "description": "Authenticator Management"},
    "IA-8": {"fedramp": "IA-8", "cmmc": "IA.L2-3.5.1", "description": "Non-Organizational User ID"},
    "IR-4": {"fedramp": "IR-4", "cmmc": "IR.L2-3.6.1", "description": "Incident Handling"},
    "IR-5": {"fedramp": "IR-5", "cmmc": "IR.L2-3.6.2", "description": "Incident Monitoring"},
    "IR-6": {"fedramp": "IR-6", "cmmc": "IR.L2-3.6.2", "description": "Incident Reporting"},
    "RA-3": {"fedramp": "RA-3", "cmmc": "RA.L2-3.11.1", "description": "Risk Assessment"},
    "RA-5": {"fedramp": "RA-5", "cmmc": "RA.L2-3.11.2", "description": "Vulnerability Monitoring"},
    "SA-4": {"fedramp": "SA-4", "cmmc": "SA.L2-3.16.1", "description": "Acquisition Process"},
    "SA-11": {"fedramp": "SA-11", "cmmc": "SA.L2-3.16.3", "description": "Developer Testing"},
    "SC-4": {"fedramp": "SC-4", "cmmc": "SC.L2-3.13.4", "description": "Information in Shared Resources"},
    "SC-5": {"fedramp": "SC-5", "cmmc": None, "description": "Denial of Service Protection"},
    "SC-7": {"fedramp": "SC-7", "cmmc": "SC.L2-3.13.1", "description": "Boundary Protection"},
    "SC-8": {"fedramp": "SC-8", "cmmc": "SC.L2-3.13.8", "description": "Transmission Confidentiality"},
    "SC-12": {"fedramp": "SC-12", "cmmc": "SC.L2-3.13.10", "description": "Cryptographic Key Management"},
    "SC-13": {"fedramp": "SC-13", "cmmc": "SC.L2-3.13.11", "description": "Cryptographic Protection"},
    "SC-23": {"fedramp": "SC-23", "cmmc": None, "description": "Session Authenticity"},
    "SC-28": {"fedramp": "SC-28", "cmmc": "SC.L2-3.13.16", "description": "Protection of Information at Rest"},
    "SI-2": {"fedramp": "SI-2", "cmmc": "SI.L2-3.14.1", "description": "Flaw Remediation"},
    "SI-4": {"fedramp": "SI-4", "cmmc": "SI.L2-3.14.6", "description": "System Monitoring"},
    "SI-7": {"fedramp": "SI-7", "cmmc": "SI.L2-3.14.4", "description": "Software Integrity Verification"},
}

_S = "https://icdev.dev/ontology/security#"

SECURITY_ONTOLOGY_MAP: dict[str, str] = {
    # Assets
    "asset-server":             f"{_S}Asset.Server",
    "asset-database":           f"{_S}Asset.Database",
    "asset-client":             f"{_S}Asset.Client",
    "asset-storage":            f"{_S}Asset.Storage",
    "asset-network":            f"{_S}Asset.Network",
    "asset-container":          f"{_S}Asset.Container",
    "asset-lambda":             f"{_S}Asset.Lambda",
    "asset-registry":           f"{_S}Asset.Registry",
    "asset-vdi-host":           f"{_S}Asset.VDIHost",
    "asset-thin-client":        f"{_S}Asset.ThinClient",
    "asset-profile-store":      f"{_S}Asset.ProfileStore",
    # Controls
    "ctrl-firewall":            f"{_S}Control.Firewall",
    "ctrl-idp":                 f"{_S}Control.IDP",
    "ctrl-kms":                 f"{_S}Control.KMS",
    "ctrl-siem":                f"{_S}Control.SIEM",
    "ctrl-ids":                 f"{_S}Control.IDS",
    "ctrl-pam":                 f"{_S}Control.PAM",
    "ctrl-scanner":             f"{_S}Control.Scanner",
    "ctrl-encryption":          f"{_S}Control.Encryption",
    "ctrl-session-policy":      f"{_S}Control.SessionPolicy",
    "ctrl-vdi-gateway":         f"{_S}Control.VDIGateway",
    "ctrl-image-hardening":     f"{_S}Control.ImageHardening",
    # Threats
    "threat-actor":             f"{_S}Threat.Actor",
    "threat-malware":           f"{_S}Threat.Malware",
    "threat-phishing":          f"{_S}Threat.Phishing",
    "threat-exploit":           f"{_S}Threat.Exploit",
    "threat-dos":               f"{_S}Threat.DoS",
    "threat-supply":            f"{_S}Threat.SupplyChain",
    "threat-insider":           f"{_S}Threat.Insider",
    "threat-session-hijack":    f"{_S}Threat.SessionHijack",
    "threat-clipboard-exfil":   f"{_S}Threat.ClipboardExfil",
    "threat-gpu-escape":        f"{_S}Threat.GPUEscape",
    "threat-profile-tampering": f"{_S}Threat.ProfileTampering",
}
