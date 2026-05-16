# CUI // SP-CTI
"""AI Observatory constants — canvas types, decision types, thresholds."""

CANVAS_TYPES = ["ndc", "sdc", "pdc", "bdc", "ddc", "odc", "idc", "aadc", "aimc", "mc", "genesis"]

CANVAS_LABELS = {
    "ndc":     "Network (NDC)",
    "sdc":     "Security (SDC)",
    "pdc":     "Pipeline (PDC)",
    "bdc":     "Boundary (BDC)",
    "ddc":     "Data (DDC)",
    "odc":     "Observability (ODC)",
    "idc":     "Infrastructure (IDC)",
    "aadc":    "Agentic AI (AADC)",
    "aimc":    "AI/ML (AIMC)",
    "mc":      "Migration (MC)",
    "genesis": "Genesis (Reflexes)",
}

DECISION_TYPES = [
    "compliance_finding",
    "threat_assessment",
    "boundary_impact",
    "anomaly_detection",
    "classification",
    "risk_score",
    "readiness_assessment",
    "narrative",
    "remediation",
    "confabulation_flag",
]

DECISION_TYPE_LABELS = {
    "compliance_finding":   "Compliance Finding",
    "threat_assessment":    "Threat Assessment",
    "boundary_impact":      "Boundary Impact",
    "anomaly_detection":    "Anomaly Detection",
    "classification":       "Classification",
    "risk_score":           "Risk Score",
    "readiness_assessment": "Readiness Assessment",
    "narrative":            "Narrative",
    "remediation":          "Remediation",
    "confabulation_flag":   "Confabulation Flag",
}

CONFIDENCE_THRESHOLDS = {
    "auto_remediate": 0.70,
    "review_required": 0.30,
    "high": 0.85,
    "medium": 0.50,
    "low": 0.30,
}

# Ontology class mappings for KG enrichment
AI_OBSERVATORY_ONTOLOGY_MAP: dict[str, str] = {
    "compliance_finding":   "compliance:NISTControl",
    "threat_assessment":    "security:Control",
    "boundary_impact":      "boundary:SystemBoundary",
    "anomaly_detection":    "security:Control",
    "classification":       "security:Classification",
    "risk_score":           "core:Concept",
    "readiness_assessment": "core:Concept",
    "narrative":            "core:Concept",
    "remediation":          "core:Concept",
    "confabulation_flag":   "core:Concept",
}
