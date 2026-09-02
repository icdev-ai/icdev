# CUI // SP-CTI
"""Synthetic data generation engine for ICDEV™ showcase and demo seeding.

Generates realistic but entirely fabricated records for any of the supported
canvas domains. Used by the demo seeder (icdev demo seed) and showcase scripts.

EVERYTHING HERE IS FABRICATED, and a consumer that persists it MUST say so.
`network_devices` (rmf-disc-02) is the first generator whose output lands in a
table an evidence-ranking engine reads: ni_devices is declared `evidence_kind:
inventory` at the best precedence in args/docmod/inventory_feeds.yaml -- an
OBSERVED DEPLOYED ESTATE. A fabricated row there would be ranked above a real
design topology. Callers therefore write ni_devices.source = 'synthetic', and
that feed excludes the label by name. Do not remove the marker to make a
surface look busier.
"""
from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta, timezone
from typing import Any

DOMAINS = (
    "cyber", "network", "network_devices", "infra",
    "requirements", "documents", "knowledge",
)

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
#: Device vocabulary for the `network_devices` domain (rmf-disc-02).
#: Vendor/model pairs are REAL product names on purpose -- the rows exist so
#: the EOL scanner, the MDC inventory page and the PVM attack-surface map have
#: something structurally shaped like an estate to render, and an invented model
#: string matches no EOL catalogue entry and so exercises none of that. The rows
#: are still fabrication and are labelled `synthetic` wherever they are stored.
_DEVICE_MODELS: tuple[tuple[str, str, str], ...] = (
    ("Cisco", "Catalyst 9300-48P", "switch-l3"),
    ("Cisco", "Catalyst 6500", "switch-l3"),
    ("Cisco", "Nexus 9336C-FX2", "switch-l3"),
    ("Cisco", "ISR 4451-X", "router"),
    ("Cisco", "ASR 1001-X", "router"),
    ("Cisco", "Firepower 2130", "firewall"),
    ("Arista", "DCS-7050SX3-48YC8", "switch-l3"),
    ("Arista", "DCS-7280CR3-32P4", "switch-l3"),
    ("Juniper", "MX204", "router"),
    ("Juniper", "EX4300-48T", "switch-l2"),
    ("Juniper", "SRX1500", "firewall"),
    ("Palo Alto", "PA-3220", "firewall"),
    ("Fortinet", "FortiGate 601E", "firewall"),
    ("F5", "BIG-IP i5800", "load-balancer"),
    ("HPE", "Aruba 6300M", "switch-l2"),
    ("Dell", "PowerSwitch S5248F-ON", "switch-l3"),
)

#: Firmware strings are per-vendor because a Junos version on a Catalyst is a
#: tell that the data is fake in a way that would mislead a reviewer skimming a
#: rendered inventory table.
_DEVICE_FIRMWARE: dict[str, tuple[str, ...]] = {
    "Cisco": ("15.2(7)E3", "16.12.5b", "17.06.04", "17.09.03"),
    "Arista": ("4.28.3M", "4.29.2F", "4.30.1F"),
    "Juniper": ("20.4R3-S4", "21.4R3", "22.2R2"),
    "Palo Alto": ("10.1.9", "10.2.6", "11.0.2"),
    "Fortinet": ("7.0.12", "7.2.5", "7.4.1"),
    "F5": ("15.1.8", "16.1.3", "17.1.0"),
    "HPE": ("10.10.1000", "10.11.1010"),
    "Dell": ("10.5.3.4", "10.5.4.2"),
}

_DEVICE_SITES = (
    "DC-East", "DC-West", "Campus-North", "Campus-South",
    "Edge-Site-01", "Edge-Site-02", "DR-Facility",
)

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
            domain: One of DOMAINS ("cyber", "network", "network_devices",
                    "infra", "requirements", "documents", "knowledge").
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

    def _gen_network_devices(self, i: int) -> dict[str, Any]:
        """One fabricated network DEVICE — the ni_devices row shape.

        Distinct from `_gen_network`, which generates network SEGMENTS (a
        subnet, a VLAN, a utilisation figure). Nothing in that shape carries a
        vendor, a model or an EOL date, so it can populate a capacity chart and
        can never populate a hardware inventory. Every inventory surface on this
        platform reads the device shape.

        `eol_date` / `eos_date` are emitted for roughly half the fleet and are
        genuinely spread on both sides of today, because a fleet whose hardware
        is uniformly current exercises none of the EOL logic that reads this
        table, and one that is uniformly expired renders as an implausible wall
        of red. The other half get None — a device whose EOL nobody has recorded
        is the common real case and must not be invented as "fine".
        """
        vendor, model, device_type = self._rng.choice(_DEVICE_MODELS)
        site = self._rng.choice(_DEVICE_SITES)
        firmware = self._rng.choice(_DEVICE_FIRMWARE.get(vendor, ("unknown",)))
        # Hostname follows the site/type/index convention operators actually use,
        # so the label is recognisable in a rendered table.
        short_type = {
            "switch-l3": "sw", "switch-l2": "sw", "router": "rtr",
            "firewall": "fw", "load-balancer": "lb",
        }.get(device_type, "dev")
        hostname = f"{site.lower().replace('-', '')}-{short_type}-{i + 1:02d}"

        eol_date = eos_date = None
        if self._rng.random() < 0.5:
            # -3y .. +5y around today, so both "past EOL" and "approaching" exist.
            offset_days = self._rng.randint(-1095, 1825)
            eol_dt = datetime.now(timezone.utc) + timedelta(days=offset_days)
            eol_date = eol_dt.strftime("%Y-%m-%d")
            # End of support trails end of life; the ordering is what the EOL
            # scanner's windows are computed against.
            eos_date = (eol_dt + timedelta(days=self._rng.randint(365, 1825))).strftime("%Y-%m-%d")

        return {
            "id": _uid(self._rng, "nid"),
            "hostname": hostname,
            "label": hostname,
            "ip": f"10.{self._rng.randint(10, 99)}.{self._rng.randint(0, 255)}.{self._rng.randint(1, 254)}",
            "device_type": device_type,
            "vendor": vendor,
            "model": model,
            "firmware_version": firmware,
            "site": site,
            "rack": f"R{self._rng.randint(1, 24):02d}",
            "eol_date": eol_date,
            "eos_date": eos_date,
            "criticality": self._rng.choice(("RED", "YELLOW", "GREEN")),
            "replacement_cost": round(self._rng.uniform(2500.0, 145000.0), 2),
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
