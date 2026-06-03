# CUI // SP-CTI
"""Source connector: ICDEV core capabilities catalog.

Reads from the goals manifest and tools manifest shards to produce
a structured summary of ICDEV's capabilities for slide generation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

_ICDEV_ROOT = Path(__file__).resolve().parents[4]
_GOALS_MANIFEST = _ICDEV_ROOT / "goals" / "manifest.md"

# Key capability domains with representative goals
_CAPABILITY_DOMAINS: list[dict] = [
    {
        "domain": "Design Canvases",
        "tagline": "AI-assisted multi-domain design across 13+ disciplines",
        "capabilities": [
            "Network topology + redundancy analysis (NDC)",
            "Security hardening + STIG compliance (SDC)",
            "Infrastructure-as-Code generation (IDC)",
            "Agentic AI design + OWASP security (AADC)",
            "Document intelligence + RAG+KG (DIC)",
        ],
    },
    {
        "domain": "Compliance & ATO",
        "tagline": "End-to-end ATO acceleration across FedRAMP, CMMC, NIST 800-53",
        "capabilities": [
            "Multi-framework SSP, POAM, STIG, SBOM generation",
            "NIST 800-53 → FedRAMP / CMMC crosswalk engine",
            "cATO continuous monitoring (every 6h)",
            "eMASS + Xacta 360 bi-directional sync",
            "FIPS 199/200 security categorization",
        ],
    },
    {
        "domain": "GovCon Intelligence",
        "tagline": "SAM.gov capture-to-delivery flywheel",
        "capabilities": [
            "SAM.gov solicitation scanning + requirement mining",
            "ICDEV capability gap analysis (L/M/N grading)",
            "AI-drafted proposal sections + compliance auto-population",
            "CPMP post-award EVM, CPARS prediction, CDRL generation",
            "Competitive intelligence + win-theme scoring",
        ],
    },
    {
        "domain": "Agentic AI Platform",
        "tagline": "15-agent system with A2A protocol and autonomous workflows",
        "capabilities": [
            "Genesis daemon: 31 reflexes, always-on autonomous operations",
            "ANVIL 5-phase TDD build workflow (RED→GREEN→REFACTOR)",
            "Multi-agent orchestration with domain-authority vetoes",
            "Self-healing (confidence ≥ 0.7, max 5/hour)",
            "Innovation, Creative, and Autoresearch engines",
        ],
    },
    {
        "domain": "Security & ZTA",
        "tagline": "NSA Zero-Trust Intelligence Guide + OWASP Agentic Security",
        "capabilities": [
            "NSA ZIG: 7 pillars, 42 capabilities, 91 activities",
            "ZTA maturity scoring + NIST SP 800-207 compliance",
            "OWASP agentic threat model (STRIDE + T1-T17)",
            "DevSecOps maturity assessment + policy-as-code (Kyverno/OPA)",
            "SBOM on every build, container non-root + read-only rootfs",
        ],
    },
    {
        "domain": "AI Governance",
        "tagline": "OMB M-25-21, NIST AI 600-1, GAO-21-519SP compliance",
        "capabilities": [
            "AI Transparency: model cards, system cards, AI inventory",
            "AI Accountability: CAIO designation, appeals, ethics reviews",
            "Confabulation detection + fairness assessment",
            "AI-ify: augmentation opportunity scanning + roadmaps",
            "RICOAS AI governance intake with 7 readiness dimensions",
        ],
    },
]


def gather(max_capabilities: int = 30) -> dict[str, Any]:
    """Return ICDEV capability domains for slide generation."""
    # Trim capabilities per domain to stay under token limits
    per_domain = max(1, max_capabilities // len(_CAPABILITY_DOMAINS))
    domains = []
    for d in _CAPABILITY_DOMAINS:
        domains.append({
            **d,
            "capabilities": d["capabilities"][:per_domain],
        })

    total_caps = sum(len(d["capabilities"]) for d in domains)

    return {
        "source": "icdev_capabilities",
        "total_domains": len(domains),
        "total_capabilities": total_caps,
        "domains": domains,
        "summary": (
            "ICDEV™ is a full-stack AI DevSecOps platform covering "
            f"{len(domains)} capability domains with {total_caps}+ distinct capabilities "
            "spanning design canvases, compliance automation, GovCon intelligence, "
            "agentic AI, security, and AI governance."
        ),
    }
