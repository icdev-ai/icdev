#!/usr/bin/env python3
# CUI // SP-CTI
"""Ontology enrichment for intake requirements.

Maps each requirement to ICDEV ontology concept IRIs and domain tags based on
its type and text content.  Falls back gracefully when the ontology DB is
unavailable.

Usage:
    from tools.requirements.ontology_enricher import enrich_requirements
    enriched = enrich_requirements(req_list, session_context={})
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# Requirement type → ontology concept IRI
_TYPE_IRI: Dict[str, str] = {
    "functional":     "https://ontology.icdev.mil/req#FunctionalRequirement",
    "non_functional": "https://ontology.icdev.mil/req#NonFunctionalRequirement",
    "security":       "https://ontology.icdev.mil/req#SecurityRequirement",
    "compliance":     "https://ontology.icdev.mil/req#ComplianceRequirement",
    "data":           "https://ontology.icdev.mil/req#DataRequirement",
    "integration":    "https://ontology.icdev.mil/req#IntegrationRequirement",
    "performance":    "https://ontology.icdev.mil/req#PerformanceRequirement",
    "interface":      "https://ontology.icdev.mil/req#InterfaceRequirement",
}

# Keyword → related concept IRI (ordered most-specific first)
_KEYWORD_CONCEPTS: List[tuple] = [
    (re.compile(r'\bosint\b|\bintelligence\b|\bsurveillance\b|\bisr\b', re.I),
     "https://ontology.icdev.mil/mission#IntelligenceSurveillance"),
    (re.compile(r'\bencrypt\b|\bcryptograph\b|\bfips\b|\bfips.140', re.I),
     "https://ontology.icdev.mil/security#Cryptography"),
    (re.compile(r'\baccess control\b|\brbac\b|\babac\b|\bauthoriz\b|\bcac\b|\bpiv\b', re.I),
     "https://ontology.icdev.mil/security#AccessControl"),
    (re.compile(r'\baudit trail\b|\bimmutable\b|\baudit log\b', re.I),
     "https://ontology.icdev.mil/security#AuditTrail"),
    (re.compile(r'\bnist\b|\bfedramp\b|\bcmmc\b|\bato\b|\bil4\b|\bil5\b|\bil6\b', re.I),
     "https://ontology.icdev.mil/compliance#FederalCompliance"),
    (re.compile(r'\bdata mesh\b|\bdata lake\b|\bdatalake\b|\bcentral.repo\b', re.I),
     "https://ontology.icdev.mil/data#DataMesh"),
    (re.compile(r'\bingest\b|\bpipeline\b|\bfeed\b|\bstream\b|\bscrape\b', re.I),
     "https://ontology.icdev.mil/pipeline#DataIngestion"),
    (re.compile(r'\b<5\s*ms\b|\blatency\b|\bthroughput\b|\bsla\b|\bresponse time\b', re.I),
     "https://ontology.icdev.mil/quality#PerformanceSLA"),
    (re.compile(r'\bml\b|\bmachine.learning\b|\bpredict\b|\banomaly\b|\bai\b', re.I),
     "https://ontology.icdev.mil/aiml#MachineLearning"),
    (re.compile(r'\bci.?cd\b|\bsast\b|\bdast\b|\bsca\b|\bdevsecops\b|\bpipeline\b', re.I),
     "https://ontology.icdev.mil/security#DevSecOps"),
    (re.compile(r'\bdashboard\b|\bvisual\b|\bui\b|\banalyst\b|\bleadership\b', re.I),
     "https://ontology.icdev.mil/boundary#UserInterface"),
    (re.compile(r'\bscale\b|\bhorizontal\b|\bavailab\b|\bresilience\b', re.I),
     "https://ontology.icdev.mil/infra#Scalability"),
    (re.compile(r'\bcui\b|\bclassif\b|\bmarking\b|\bhandl', re.I),
     "https://ontology.icdev.mil/compliance#ClassificationControl"),
    (re.compile(r'\bmodel card\b|\bhuman.in.the.loop\b|\bhitl\b|\bai govern\b', re.I),
     "https://ontology.icdev.mil/aiml#AIGovernance"),
]

# Lookup from live ontology DB (domain → list of class IRIs)
_DOMAIN_TYPE_MAP: Dict[str, str] = {
    "security":    "security",
    "compliance":  "compliance",
    "data":        "data",
    "integration": "pipeline",
    "performance": "quality",
    "interface":   "boundary",
}


def enrich_requirements(
    requirements: List[Dict[str, Any]],
    session_context: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Add ontology_type_iri and ontology_concepts to each requirement dict.

    Mutates each dict in-place; also returns the list for convenience.
    """
    for req in requirements:
        req_type = req.get("type", "functional")
        req["ontology_type_iri"] = _TYPE_IRI.get(req_type, _TYPE_IRI["functional"])

        text = req.get("text", "") + " " + req.get("criteria", "")
        concepts: List[str] = []
        for pattern, concept_iri in _KEYWORD_CONCEPTS:
            if pattern.search(text) and concept_iri not in concepts:
                concepts.append(concept_iri)

        req["ontology_concepts"] = concepts

    # Best-effort enrichment from live ontology_classes table
    _try_db_enrichment(requirements)
    return requirements


def _try_db_enrichment(requirements: List[Dict[str, Any]]) -> None:
    """Augment ontology_concepts with IRIs from the live ontology DB. No-op on error."""
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        conn.set_security_context(None)

        rows = conn.execute(
            "SELECT domain, class_iri FROM ontology_classes WHERE domain IN ('security','data','compliance','aiml','pipeline','quality','boundary','infra') LIMIT 100"
        ).fetchall()
        conn.close()

        if not rows:
            return

        domain_iris: Dict[str, List[str]] = {}
        for domain, iri in rows:
            domain_iris.setdefault(domain, []).append(iri)

        for req in requirements:
            req_type = req.get("type", "")
            db_domain = _DOMAIN_TYPE_MAP.get(req_type)
            if db_domain and db_domain in domain_iris:
                db_iri = domain_iris[db_domain][0]
                if db_iri not in req["ontology_concepts"]:
                    req["ontology_concepts"].append(db_iri)
    except Exception:
        pass
