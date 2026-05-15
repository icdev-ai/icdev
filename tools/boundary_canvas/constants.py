# CUI // SP-CTI — ICDEV Boundary Design Canvas Constants
"""Boundary Design Canvas module-level constants.

Object palettes, compliance rules, and assessment frameworks for visual
authorization boundary design, ISA lifecycle, and interconnection risk.
"""

BOUNDARY_OBJECTS = {
    "systems": [
        {
            "type": "sys-internal",
            "label": "Internal System",
            "icon": "SYS",
            "desc": "System within the authorization boundary under assessment",
        },
        {
            "type": "sys-external",
            "label": "External System",
            "icon": "EXT",
            "desc": "External system outside the authorization boundary",
        },
        {
            "type": "sys-cloud",
            "label": "Cloud Service",
            "icon": "CLD",
            "desc": "Cloud service provider (CSP) system — shared responsibility model",
        },
        {
            "type": "sys-saas",
            "label": "SaaS Provider",
            "icon": "SAS",
            "desc": "SaaS provider — FedRAMP authorized or agency ATO required",
        },
        {
            "type": "sys-legacy",
            "label": "Legacy System",
            "icon": "LEG",
            "desc": "Legacy/heritage system — may lack modern security controls",
        },
        {
            "type": "sys-mobile",
            "label": "Mobile Platform",
            "icon": "MOB",
            "desc": "Mobile device platform (MDM/UEM managed)",
        },
        {
            "type": "sys-iot",
            "label": "IoT/OT System",
            "icon": "IOT",
            "desc": "IoT/OT/ICS system — Purdue model or ISA-62443 zone",
        },
        {
            "type": "sys-enclave",
            "label": "Enclave",
            "icon": "ENC",
            "desc": "Sub-system enclave with separate security controls",
        },
    ],
    "boundaries": [
        {
            "type": "bnd-ato",
            "label": "ATO Boundary",
            "icon": "ATO",
            "desc": "Authorization to Operate boundary — scope of security assessment",
        },
        {
            "type": "bnd-fedramp",
            "label": "FedRAMP Boundary",
            "icon": "FRP",
            "desc": "FedRAMP authorization boundary — CSP scope",
        },
        {
            "type": "bnd-enclave",
            "label": "Enclave Boundary",
            "icon": "ENV",
            "desc": "Network enclave within the ATO boundary",
        },
        {"type": "bnd-dmz", "label": "DMZ", "icon": "DMZ", "desc": "Demilitarized zone — internet-facing services"},
        {
            "type": "bnd-classification",
            "label": "Classification Zone",
            "icon": "CLZ",
            "desc": "Data classification boundary (CUI/SECRET/TS)",
        },
        {"type": "bnd-pci", "label": "PCI CDE", "icon": "PCI", "desc": "PCI Cardholder Data Environment boundary"},
        {"type": "bnd-hipaa", "label": "HIPAA Zone", "icon": "HIP", "desc": "HIPAA regulated zone for PHI data"},
        {"type": "bnd-scif", "label": "SCIF", "icon": "SCF", "desc": "Sensitive Compartmented Information Facility"},
    ],
    "interconnections": [
        {
            "type": "isa-vpn",
            "label": "VPN ISA",
            "icon": "VPN",
            "desc": "IPSec/IKEv2 VPN interconnection with ISA agreement",
        },
        {
            "type": "isa-dedicated",
            "label": "Dedicated Circuit",
            "icon": "DED",
            "desc": "Dedicated circuit (MPLS/DX/ER) with ISA agreement",
        },
        {
            "type": "isa-api",
            "label": "API Integration",
            "icon": "API",
            "desc": "REST/SOAP API interconnection with authentication",
        },
        {
            "type": "isa-file",
            "label": "File Transfer",
            "icon": "FTP",
            "desc": "SFTP/SCP/MFT file transfer interconnection",
        },
        {
            "type": "isa-cross-domain",
            "label": "Cross-Domain Solution",
            "icon": "CDS",
            "desc": "Cross-domain guard/diode between classification levels",
        },
        {
            "type": "isa-federation",
            "label": "Identity Federation",
            "icon": "FED",
            "desc": "SAML/OIDC identity federation between systems",
        },
        {
            "type": "isa-email",
            "label": "Email Gateway",
            "icon": "EML",
            "desc": "Secure email gateway interconnection (S/MIME, TLS)",
        },
    ],
    "controls": [
        {
            "type": "ctrl-bcap",
            "label": "BCAP",
            "icon": "BCP",
            "desc": "Boundary Cloud Access Point — DoD BCAP for cloud egress",
        },
        {"type": "ctrl-cap", "label": "CAP", "icon": "CAP", "desc": "Cloud Access Point — TIC 3.0 cloud trust zone"},
        {
            "type": "ctrl-proxy",
            "label": "Web Proxy",
            "icon": "PRX",
            "desc": "Forward/reverse proxy for boundary traversal inspection",
        },
        {
            "type": "ctrl-ids-ips",
            "label": "IDS/IPS",
            "icon": "IDS",
            "desc": "Intrusion detection/prevention at boundary",
        },
        {"type": "ctrl-dlp", "label": "DLP Gateway", "icon": "DLP", "desc": "Data loss prevention at boundary egress"},
        {
            "type": "ctrl-firewall",
            "label": "Boundary Firewall",
            "icon": "FW",
            "desc": "Stateful firewall at authorization boundary edge",
        },
        {"type": "ctrl-siem", "label": "SIEM/SOC", "icon": "SIM", "desc": "Security monitoring for boundary traffic"},
        {
            "type": "ctrl-pps",
            "label": "PPS Filter",
            "icon": "PPS",
            "desc": "Ports, Protocols, and Services restriction enforcement",
        },
        {
            "type": "ctrl-mfa",
            "label": "MFA Gateway",
            "icon": "MFA",
            "desc": "Multi-factor authentication for boundary traversal",
        },
        {
            "type": "ctrl-certificate",
            "label": "PKI/mTLS",
            "icon": "PKI",
            "desc": "Certificate-based mutual TLS for system-to-system auth",
        },
    ],
    "documentation": [
        {
            "type": "doc-isa",
            "label": "ISA Agreement",
            "icon": "ISA",
            "desc": "Interconnection Security Agreement document",
        },
        {"type": "doc-mou", "label": "MOU/MOA", "icon": "MOU", "desc": "Memorandum of Understanding / Agreement"},
        {
            "type": "doc-ato-letter",
            "label": "ATO Letter",
            "icon": "ATL",
            "desc": "Authorization to Operate letter with conditions",
        },
        {
            "type": "doc-pps-matrix",
            "label": "PPS Matrix",
            "icon": "PPM",
            "desc": "Ports, Protocols, and Services matrix document",
        },
        {
            "type": "doc-dfd",
            "label": "Data Flow Diagram",
            "icon": "DFD",
            "desc": "Data flow diagram showing classification-tagged flows",
        },
        {"type": "doc-conops", "label": "ConOps", "icon": "COP", "desc": "Concept of Operations for interconnection"},
    ],
    "digital_twin": [
        {
            "type": "twin-cato",
            "label": "cATO Twin",
            "icon": "CT",
            "desc": "Continuous Authority to Operate digital twin — snapshots cross-framework control state and enables what-if compliance simulation",
        },
        {
            "type": "twin-compliance-snapshot",
            "label": "Compliance Snapshot",
            "icon": "CS",
            "desc": "Append-only snapshot of control satisfaction status across all mapped frameworks at a point in time",
        },
        {
            "type": "twin-crosswalk-drift",
            "label": "Crosswalk Drift Detector",
            "icon": "XD",
            "desc": "Surfaces controls satisfied in one framework but not another (e.g., NIST AC-2 satisfied, CMMC AC.L2-3.1.1 not_satisfied)",
        },
        {
            "type": "twin-oscal-exporter",
            "label": "OSCAL Exporter",
            "icon": "OX",
            "desc": "Generates machine-readable OSCAL SSP, POA&M, Assessment Results, and Component Definition from snapshot state",
        },
        {
            "type": "twin-readiness-scorer",
            "label": "Readiness Scorer",
            "icon": "RS",
            "desc": "Real-time cATO readiness score (0–100) across control satisfaction %, evidence freshness %, POA&M age, and critical-control gaps",
        },
    ],
}

# ISA Lifecycle States
ISA_LIFECYCLE_STATES = [
    "draft",
    "under_review",
    "approved",
    "active",
    "expiring_soon",
    "expired",
    "terminated",
]

# Classification levels (ordered by sensitivity)
CLASSIFICATION_LEVELS = [
    "PUBLIC",
    "CUI",
    "SECRET",
    "TOP SECRET",
]

# Impact levels (DoD)
IMPACT_LEVELS = [
    "IL2",
    "IL4",
    "IL5",
    "IL6",
]

# Boundary Compliance Rules
BOUNDARY_COMPLIANCE_RULES = [
    {
        "id": "BDC-BND-001",
        "title": "ATO boundary defined",
        "severity": "CAT1",
        "category": "boundary",
        "description": "Design must have at least one ATO boundary — defines the scope of security assessment (NIST CA-3).",
    },
    {
        "id": "BDC-BND-002",
        "title": "All systems inside a boundary",
        "severity": "CAT1",
        "category": "boundary",
        "description": "Every system must reside within an authorization boundary — unbounded systems are out of ATO scope.",
    },
    {
        "id": "BDC-ISA-001",
        "title": "ISA agreement for every external interconnection",
        "severity": "CAT1",
        "category": "interconnection",
        "description": "Every interconnection to an external system must have an ISA document attached (NIST CA-3).",
    },
    {
        "id": "BDC-ISA-002",
        "title": "ISA not expired",
        "severity": "CAT1",
        "category": "interconnection",
        "description": "All ISA agreements must be in active or approved state — expired ISAs are unauthorized connections.",
    },
    {
        "id": "BDC-ISA-003",
        "title": "Cross-domain solution for classification boundary crossing",
        "severity": "CAT1",
        "category": "cross_domain",
        "description": "Interconnections crossing classification zone boundaries must use a cross-domain solution (NIST AC-4(21)).",
    },
    {
        "id": "BDC-CTL-001",
        "title": "Firewall at every boundary edge",
        "severity": "CAT1",
        "category": "boundary_protection",
        "description": "Each authorization boundary must have a firewall control at its perimeter (NIST SC-7).",
    },
    {
        "id": "BDC-CTL-002",
        "title": "IDS/IPS monitoring boundary traffic",
        "severity": "CAT2",
        "category": "monitoring",
        "description": "Boundary perimeter should have IDS/IPS for intrusion detection (NIST SI-4).",
    },
    {
        "id": "BDC-CTL-003",
        "title": "SIEM collecting boundary logs",
        "severity": "CAT2",
        "category": "monitoring",
        "description": "SIEM must be connected to monitor boundary traffic and interconnection activity (NIST AU-6, SI-4).",
    },
    {
        "id": "BDC-CTL-004",
        "title": "PPS restriction enforced",
        "severity": "CAT2",
        "category": "boundary_protection",
        "description": "All interconnections should have PPS filter to restrict allowed ports/protocols/services (NIST CM-7).",
    },
    {
        "id": "BDC-CTL-005",
        "title": "MFA for external access",
        "severity": "CAT1",
        "category": "authentication",
        "description": "All interconnections allowing user access from external systems must require MFA (NIST IA-2(1)).",
    },
    {
        "id": "BDC-CTL-006",
        "title": "mTLS for system-to-system connections",
        "severity": "CAT2",
        "category": "authentication",
        "description": "System-to-system interconnections (API, file transfer) should use mutual TLS with PKI certificates (NIST IA-3, SC-8).",
    },
    {
        "id": "BDC-DOC-001",
        "title": "PPS matrix documented",
        "severity": "CAT2",
        "category": "documentation",
        "description": "Each boundary must have a PPS matrix document listing all allowed ports, protocols, and services.",
    },
    {
        "id": "BDC-DOC-002",
        "title": "Data flow diagram present",
        "severity": "CAT2",
        "category": "documentation",
        "description": "Each boundary should have a data flow diagram showing data classification at each flow.",
    },
    {
        "id": "BDC-FED-001",
        "title": "SaaS providers FedRAMP authorized",
        "severity": "CAT1",
        "category": "fedramp",
        "description": "SaaS provider systems must be FedRAMP authorized at the appropriate impact level (FedRAMP baseline).",
    },
    {
        "id": "BDC-FED-002",
        "title": "Cloud services use BCAP/CAP",
        "severity": "CAT2",
        "category": "tic",
        "description": "Cloud service interconnections should traverse a BCAP (DoD) or CAP (TIC 3.0) for traffic inspection.",
    },
]

# ── Default PPS entries for common interconnection types ─────────────────────
DEFAULT_PPS = {
    "isa-vpn": [
        {"port": "500", "protocol": "UDP", "service": "IKE", "direction": "bidirectional"},
        {"port": "4500", "protocol": "UDP", "service": "IKE NAT-T", "direction": "bidirectional"},
        {"port": "0", "protocol": "ESP(50)", "service": "IPSec ESP", "direction": "bidirectional"},
    ],
    "isa-api": [
        {"port": "443", "protocol": "TCP", "service": "HTTPS/REST", "direction": "bidirectional"},
    ],
    "isa-file": [
        {"port": "22", "protocol": "TCP", "service": "SFTP/SCP", "direction": "bidirectional"},
    ],
    "isa-federation": [
        {"port": "443", "protocol": "TCP", "service": "SAML/OIDC", "direction": "bidirectional"},
    ],
    "isa-email": [
        {"port": "25", "protocol": "TCP", "service": "SMTP", "direction": "outbound"},
        {"port": "587", "protocol": "TCP", "service": "SMTP/TLS", "direction": "outbound"},
        {"port": "993", "protocol": "TCP", "service": "IMAPS", "direction": "inbound"},
    ],
    "isa-dedicated": [
        {"port": "any", "protocol": "any", "service": "Dedicated Circuit", "direction": "bidirectional"},
    ],
    "isa-cross-domain": [
        {"port": "varies", "protocol": "varies", "service": "Cross-Domain Guard", "direction": "unidirectional"},
    ],
}

# ── NIST 800-53 controls relevant to boundary design ────────────────────────
BOUNDARY_NIST_CONTROLS = {
    "CA-3": "System Interconnections",
    "CA-3(5)": "Restrictions on External System Connections",
    "SC-7": "Boundary Protection",
    "SC-7(3)": "Access Points",
    "SC-7(4)": "External Telecommunications Services",
    "SC-7(5)": "Deny by Default / Allow by Exception",
    "SC-7(7)": "Split Tunneling for Remote Devices",
    "SC-7(8)": "Route Traffic to Authenticated Proxy Servers",
    "SC-7(9)": "Restrict Threatening Outgoing Traffic",
    "SC-7(18)": "Fail Secure",
    "SC-8": "Transmission Confidentiality and Integrity",
    "SC-8(1)": "Cryptographic Protection",
    "AC-4": "Information Flow Enforcement",
    "AC-4(21)": "Physical / Logical Separation of Information Flows",
    "AC-17": "Remote Access",
    "AC-20": "Use of External Systems",
    "IA-2(1)": "Multi-factor Authentication to Privileged Accounts",
    "IA-3": "Device Identification and Authentication",
    "SI-4": "System Monitoring",
    "AU-6": "Audit Record Review, Analysis, and Reporting",
    "CM-7": "Least Functionality",
}

_B = "https://icdev.dev/ontology/boundary#"

BOUNDARY_ONTOLOGY_MAP: dict[str, str] = {
    # Systems
    "sys-internal":            f"{_B}SystemBoundary.Internal",
    "sys-external":            f"{_B}SystemBoundary.External",
    "sys-cloud":               f"{_B}SystemBoundary.Cloud",
    "sys-saas":                f"{_B}SystemBoundary.SaaS",
    "sys-legacy":              f"{_B}SystemBoundary.Legacy",
    "sys-mobile":              f"{_B}SystemBoundary.Mobile",
    "sys-iot":                 f"{_B}SystemBoundary.IoT",
    "sys-enclave":             f"{_B}SystemBoundary.Enclave",
    # Authorization boundaries
    "bnd-ato":                 f"{_B}AuthorizationBoundary.ATO",
    "bnd-fedramp":             f"{_B}AuthorizationBoundary.FedRAMP",
    "bnd-enclave":             f"{_B}AuthorizationBoundary.Enclave",
    "bnd-dmz":                 f"{_B}AuthorizationBoundary.DMZ",
    "bnd-classification":      f"{_B}AuthorizationBoundary.Classification",
    "bnd-pci":                 f"{_B}AuthorizationBoundary.PCI",
    "bnd-hipaa":               f"{_B}AuthorizationBoundary.HIPAA",
    "bnd-scif":                f"{_B}AuthorizationBoundary.SCIF",
    # Interconnections (ISA)
    "isa-vpn":                 f"{_B}Interconnection.VPN",
    "isa-dedicated":           f"{_B}Interconnection.Dedicated",
    "isa-api":                 f"{_B}Interconnection.API",
    "isa-file":                f"{_B}Interconnection.File",
    "isa-cross-domain":        f"{_B}Interconnection.CrossDomain",
    "isa-federation":          f"{_B}Interconnection.Federation",
    "isa-email":               f"{_B}Interconnection.Email",
    # Boundary controls
    "ctrl-bcap":               f"{_B}BoundaryControl.BCAP",
    "ctrl-cap":                f"{_B}BoundaryControl.CAP",
    "ctrl-proxy":              f"{_B}BoundaryControl.Proxy",
    "ctrl-ids-ips":            f"{_B}BoundaryControl.IDSIPS",
    "ctrl-dlp":                f"{_B}BoundaryControl.DLP",
    "ctrl-firewall":           f"{_B}BoundaryControl.Firewall",
    "ctrl-siem":               f"{_B}BoundaryControl.SIEM",
    "ctrl-pps":                f"{_B}BoundaryControl.PPS",
    "ctrl-mfa":                f"{_B}BoundaryControl.MFA",
    "ctrl-certificate":        f"{_B}BoundaryControl.Certificate",
    # Documents
    "doc-isa":                 f"{_B}Document.ISA",
    "doc-mou":                 f"{_B}Document.MOU",
    "doc-ato-letter":          f"{_B}Document.ATOLetter",
    "doc-pps-matrix":          f"{_B}Document.PPSMatrix",
    "doc-dfd":                 f"{_B}Document.DFD",
    "doc-conops":              f"{_B}Document.ConOps",
    # Digital twins
    "twin-cato":               f"{_B}DigitalTwin.cATO",
    "twin-compliance-snapshot":f"{_B}DigitalTwin.ComplianceSnapshot",
    "twin-crosswalk-drift":    f"{_B}DigitalTwin.CrosswalkDrift",
    "twin-oscal-exporter":     f"{_B}DigitalTwin.OSCALExporter",
    "twin-readiness-scorer":   f"{_B}DigitalTwin.ReadinessScorer",
}
