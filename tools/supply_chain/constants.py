# CUI // SP-CTI
"""Supply Chain Intelligence — domain constants (NIST 800-161, NDAA §889)."""

RISK_TIER_ORDER = {"critical": 0, "high": 1, "moderate": 2, "low": 3}
RISK_TIER_COLORS = {
    "critical": "#f85149",
    "high": "#f0883e",
    "moderate": "#e3b341",
    "low": "#3fb950",
}

VENDOR_TYPES = ("cots", "gots", "oss", "saas", "paas", "iaas", "contractor", "subcontractor")
VENDOR_TYPE_LABELS = {
    "cots": "COTS", "gots": "GOTS", "oss": "OSS",
    "saas": "SaaS", "paas": "PaaS", "iaas": "IaaS",
    "contractor": "Contractor", "subcontractor": "Subcontractor",
}

SECTION_889_LABELS = {
    "compliant":    ("Compliant",    "#3fb950"),
    "under_review": ("Under Review", "#e3b341"),
    "prohibited":   ("Prohibited",   "#f85149"),
    "exempt":       ("Exempt",       "#58a6ff"),
}

ISA_STATUS_COLORS = {
    "draft":      "#8b949e",
    "review":     "#e3b341",
    "signed":     "#58a6ff",
    "active":     "#3fb950",
    "expiring":   "#f0883e",
    "expired":    "#f85149",
    "terminated": "#8b949e",
}

ISA_TYPES = ("isa", "mou", "moa", "sla", "ila")

CVE_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
CVE_SEVERITY_COLORS = {
    "critical": "#f85149",
    "high":     "#f0883e",
    "medium":   "#e3b341",
    "low":      "#3fb950",
}

SCRM_RISK_CATEGORIES = (
    "tampering", "counterfeit", "malicious_insertion",
    "supply_disruption", "data_exposure", "foreign_control",
    "single_source", "obsolescence",
)

RESIDUAL_RISK_ORDER = {"critical": 0, "high": 1, "moderate": 2, "low": 3}

# ISA expiring-soon window
ISA_EXPIRY_WARNING_DAYS = 90

# IQE collections for supply chain canvas
IQE_COLLECTIONS = [
    "supply_chain.vendors",
    "supply_chain.scrm_risks",
    "supply_chain.cve_triage",
    "supply_chain.isa_agreements",
]
