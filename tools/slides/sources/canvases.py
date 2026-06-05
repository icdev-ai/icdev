# CUI // SP-CTI
"""Source connector: Active canvases from ICDEV feature flags.

Returns a structured summary of all enabled canvases suitable for
injecting into the slide generation pipeline.
"""
from __future__ import annotations

import os
from typing import Any

# Map of env-flag → (display name, url_prefix, 1-line description)
_CANVAS_CATALOG: dict[str, tuple[str, str, str]] = {
    "ICDEV_NDC_ENABLED":              ("Network Design Canvas", "/network", "AI-assisted network topology design, redundancy analysis, and Ansible auto-provisioning"),
    "ICDEV_SDC_ENABLED":              ("Security Design Canvas", "/security", "STIG/NIST SP 800-53 hardening, attack surface modeling, and threat path analysis"),
    "ICDEV_IDC_ENABLED":              ("Infrastructure Design Canvas", "/infra", "Cloud/on-prem infrastructure modeling with Terraform + Ansible generation"),
    "ICDEV_DDC_ENABLED":              ("Data Design Canvas", "/data", "Data lineage, classification, and pipeline design with AI-driven quality checks"),
    "ICDEV_BDC_ENABLED":              ("Boundary Design Canvas", "/boundary", "ATO boundary impact analysis and ISA lifecycle management"),
    "ICDEV_AADC_ENABLED":             ("Agentic AI Design Canvas", "/agentic-ai", "Multi-agent system design with OWASP agentic security assessment"),
    "ICDEV_AIML_CANVAS_ENABLED":      ("AI/ML Model Canvas", "/ai-ml", "Model cards, fairness assessment, and AI governance compliance"),
    "ICDEV_AIIFY_ENABLED":            ("AI-ify Canvas", "/ai-ify", "AI augmentation opportunity scanning and implementation roadmaps"),
    "ICDEV_MIGRATION_CANVAS_ENABLED": ("Migration Canvas", "/migration-canvas", "Legacy modernization with 7Rs assessment and strangler-fig execution"),
    "ICDEV_MISSION_CANVAS_ENABLED":   ("Mission Canvas", "/mission-canvas", "Mission thread modeling and requirements-to-architecture traceability"),
    "ICDEV_DIC_ENABLED":              ("Document Intelligence Canvas", "/dic", "Enterprise RAG+KG document management with HITL classification"),
    "ICDEV_DATA_CANVAS_ENABLED":      ("Data Mapping Canvas", "/data/mapping", "AI-powered field mapping and schema transformation (MapForce-style)"),
    "ICDEV_GOVCON_ENABLED":           ("GovCon Canvas", "/govcon", "SAM.gov capture pipeline, proposal lifecycle, and CPMP post-award management"),
    "ICDEV_GENESIS_ENABLED":          ("Genesis Daemon", "/genesis", "Always-on autonomous research lab with 31 reflexes and self-healing"),
}


def gather() -> dict[str, Any]:
    """Return enabled canvases as structured content for slide generation."""
    active: list[dict] = []
    for flag, (name, url, desc) in _CANVAS_CATALOG.items():
        val = os.environ.get(flag, "false").lower()
        if val in ("true", "1", "yes"):
            active.append({"name": name, "url": url, "description": desc})

    return {
        "source": "canvases",
        "total_active": len(active),
        "canvases": active,
        "summary": (
            f"ICDEV™ has {len(active)} active design canvases providing AI-assisted "
            "workflows across network, security, infrastructure, data, compliance, and AI governance domains."
        ),
    }
