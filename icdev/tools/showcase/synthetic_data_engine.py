# CUI // SP-CTI
"""Synthetic data generation engine for ICDEV™ showcase and demo seeding.

Generates realistic but entirely fabricated records for any of the 6 supported
canvas domains. Used by the demo seeder (icdev demo seed) and showcase scripts.
"""
from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta, timezone
from typing import Any

DOMAINS = ("cyber", "network", "infra", "requirements", "documents", "knowledge")

_CVE_NAMES = [
    "OpenSSL RCE", "Apache Log4j JNDI Injection", "Spring4Shell EL Injection",
    "Nginx HTTP/2 DoS", "PostgreSQL Privilege Escalation", "Redis Command Injection",
    "Linux Kernel UAF", "Sudo Heap Overflow", "Bash Shellshock", "Heartbleed TLS Leak",
    "Struts2 Remote Code Execution", "Jenkins Arbitrary File Read",
]
_SEVERITIES = ("critical", "high", "medium", "low", "informational")
_VULN_STATUSES = ("open", "in_progress", "resolved", "accepted")
_PROTOCOLS = ("TCP", "UDP", "HTTPS", "SSH", "TLS", "HTTP", "SNMP", "BGP", "OSPF")
_REGIONS = ("us-east-1", "us-west-2", "eu-central-1", "ap-southeast-1", "us-gov-west-1")
_CLOUD_TYPES = ("ec2", "rds", "s3", "lambda", "eks", "ecs", "elb", "cloudfront", "elasticache")
_REQUIREMENT_TYPES = ("functional", "non_functional", "security", "compliance", "performance")
_DOC_TYPES = ("policy", "sop", "specification", "report", "brief", "assessment", "plan")
_KNOWLEDGE_TAGS = (
    "architecture", "security", "devops", "cloud", "networking",
    "compliance", "ai-ml", "automation", "observability",
)


def _ts(rng: random.Random, days_ago_max: int = 90) -> str:
    offset = timedelta(days=rng.randint(0, days_ago_max), hours=rng.randint(0, 23))
    dt = datetime.now(timezone.utc) - offset
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _uid(rng: random.Random, prefix: str = "id") -> str:
    raw = str(rng.random()).encode()
    return prefix + "-" + hashlib.sha256(raw).hexdigest()[:12]


class SyntheticDataEngine:
    """Generate realistic synthetic records for a given canvas domain.

    All output is deterministic when a seed is supplied, making it safe to
    re-run without creating duplicate-looking data.
    """

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed if seed is not None else 42)

    def generate(self, domain: str, records: int = 100) -> list[dict[str, Any]]:
        """Return a list of synthetic record dicts for *domain*.

        Args:
            domain: One of DOMAINS ("cyber", "network", "infra",
                    "requirements", "documents", "knowledge").
            records: Number of records to generate.

        Raises:
            ValueError: If *domain* is not in DOMAINS.
        """
        if domain not in DOMAINS:
            raise ValueError(
                f"Unknown domain {domain!r}. Valid domains: {DOMAINS}"
            )
        gen = getattr(self, f"_gen_{domain}")
        return [gen(i) for i in range(records)]

    # ------------------------------------------------------------------
    # Domain generators
    # ------------------------------------------------------------------

    def _gen_cyber(self, i: int) -> dict[str, Any]:
        name = self._rng.choice(_CVE_NAMES)
        severity = self._rng.choice(_SEVERITIES)
        year = self._rng.randint(2022, 2024)
        cve_num = self._rng.randint(1000, 99999)
        return {
            "id": _uid(self._rng, "vuln"),
            "cve_id": f"CVE-{year}-{cve_num:05d}",
            "name": name,
            "severity": severity,
            "cvss_score": round(self._rng.uniform(2.0, 10.0), 1),
            "status": self._rng.choice(_VULN_STATUSES),
            "asset": f"host-{i:03d}.demo.internal",
            "port": self._rng.choice([22, 80, 443, 8080, 5432, 6379]),
            "detected_at": _ts(self._rng),
            "description": f"Synthetic demo vulnerability: {name} (CVE-{year}-{cve_num:05d})",
        }

    def _gen_network(self, i: int) -> dict[str, Any]:
        octet2 = self._rng.randint(0, 255)
        octet3 = self._rng.randint(0, 255)
        return {
            "id": _uid(self._rng, "net"),
            "name": f"segment-{i:03d}",
            "subnet": f"10.{octet2}.{octet3}.0/24",
            "protocol": self._rng.choice(_PROTOCOLS),
            "bandwidth_mbps": self._rng.choice([100, 1000, 10000, 40000]),
            "utilization_pct": round(self._rng.uniform(5.0, 95.0), 1),
            "region": self._rng.choice(_REGIONS),
            "vlan_id": self._rng.randint(1, 4094),
            "status": self._rng.choice(("active", "degraded", "planned", "maintenance")),
            "created_at": _ts(self._rng),
        }

    def _gen_infra(self, i: int) -> dict[str, Any]:
        resource_type = self._rng.choice(_CLOUD_TYPES)
        return {
            "id": _uid(self._rng, "res"),
            "name": f"demo-{resource_type}-{i:03d}",
            "type": resource_type,
            "region": self._rng.choice(_REGIONS),
            "environment": self._rng.choice(("prod", "staging", "dev", "test")),
            "cpu_cores": self._rng.choice([2, 4, 8, 16, 32, 64]),
            "ram_gb": self._rng.choice([4, 8, 16, 32, 64, 128]),
            "monthly_cost_usd": round(self._rng.uniform(20.0, 5000.0), 2),
            "status": self._rng.choice(("running", "stopped", "pending", "terminated")),
            "tags": {"env": self._rng.choice(("prod", "dev")), "team": f"team-{i % 5 + 1}"},
            "created_at": _ts(self._rng),
        }

    def _gen_requirements(self, i: int) -> dict[str, Any]:
        req_type = self._rng.choice(_REQUIREMENT_TYPES)
        return {
            "id": _uid(self._rng, "req"),
            "req_id": f"REQ-{i + 1:04d}",
            "title": f"Demo requirement {i + 1}: {req_type.replace('_', ' ').title()} requirement",
            "type": req_type,
            "priority": self._rng.choice(("critical", "high", "medium", "low")),
            "status": self._rng.choice(("draft", "approved", "implemented", "verified", "deferred")),
            "acceptance_criteria": (
                f"The system shall satisfy {req_type} criteria "
                f"for demo asset demo-{i:03d} with zero defects."
            ),
            "source": self._rng.choice(("stakeholder", "regulatory", "technical", "design")),
            "created_at": _ts(self._rng),
        }

    def _gen_documents(self, i: int) -> dict[str, Any]:
        doc_type = self._rng.choice(_DOC_TYPES)
        return {
            "id": _uid(self._rng, "doc"),
            "title": f"Demo {doc_type.upper()} Document {i + 1:03d}",
            "type": doc_type,
            "classification": self._rng.choice(("unclassified", "cui", "controlled")),
            "page_count": self._rng.randint(5, 250),
            "status": self._rng.choice(("draft", "review", "approved", "archived", "superseded")),
            "author": f"demo.user.{i % 5 + 1:02d}@icdev.example",
            "version": f"{self._rng.randint(1, 5)}.{self._rng.randint(0, 9)}",
            "created_at": _ts(self._rng),
        }

    def _gen_knowledge(self, i: int) -> dict[str, Any]:
        tag = self._rng.choice(_KNOWLEDGE_TAGS)
        tag2 = self._rng.choice(_KNOWLEDGE_TAGS)
        return {
            "id": _uid(self._rng, "kb"),
            "title": f"Knowledge Article {i + 1:03d}: {tag.title()} Guide",
            "tags": list({tag, tag2}),
            "source": self._rng.choice(("internal", "external", "ai-generated", "imported")),
            "confidence_score": round(self._rng.uniform(0.5, 1.0), 2),
            "view_count": self._rng.randint(0, 10000),
            "status": self._rng.choice(("active", "archived", "draft", "under_review")),
            "related_ids": [],
            "created_at": _ts(self._rng),
        }
