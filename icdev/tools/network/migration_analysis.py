# [CUI // SP-CTI]
"""ICDEV™ Network Migration Analysis Engine.

Given a topology, its device running-configs, and any planned migration phases,
produce a structured assessment across six dimensions the field team cares about
during a migration:

    1. recommendations   — what to do, prioritized
    2. anomalies          — patterns / outliers / issues in the as-is estate
    3. deviations         — drift from approved docs (naming convention, SOPs)
    4. misconfigurations  — concrete config-level defects (security baseline)
    5. topology_changes   — what the migration adds / changes / retires
    6. risks              — scored risk register

LLM-driven with a deterministic rule fallback (FORGE: AI orchestrates, rules
execute). The deterministic pass ALWAYS runs and is authoritative for hard
findings (telnet, EOL, naming); the LLM enriches with narrative + extra insight
when a provider is available, and is skipped cleanly offline.

Standards live in ``args/network_standards.yaml`` — edit there, not here.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

def _find_standards_path() -> Path:
    """Locate args/network_standards.yaml from either the repo root or the
    icdev/ package mirror (parents[2] differs between the two copies)."""
    here = Path(__file__).resolve()
    for base in (here.parents[2], here.parents[3], Path.cwd()):
        cand = base / "args" / "network_standards.yaml"
        if cand.exists():
            return cand
    return here.parents[2] / "args" / "network_standards.yaml"


_STANDARDS_PATH = _find_standards_path()

CATEGORIES = (
    "recommendations", "anomalies", "deviations",
    "misconfigurations", "topology_changes", "risks",
)
_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


# ── Standards loader ────────────────────────────────────────────────────────

_DEFAULT_STANDARDS = {
    "naming": {"pattern": r"^[A-Z0-9]{2,6}-[A-Z0-9]{2,8}-[0-9]{2}$",
               "description": "SITE-ROLE-NN", "max_length": 32, "allowed_roles": []},
    "sop_requirements": [],
    "security_rules": [],
    "lifecycle": {"eol_warning_days": 365, "max_firmware_versions_per_model": 1,
                  "critical_threshold": 70},
    "risk_weights": {"critical": 40, "high": 20, "medium": 8, "low": 3, "info": 0},
}


def load_standards() -> dict:
    """Load network standards from YAML, falling back to safe defaults."""
    try:
        import yaml
        with open(_STANDARDS_PATH, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        merged = dict(_DEFAULT_STANDARDS)
        merged.update(data)
        return merged
    except Exception:
        logger.warning("network_standards.yaml not loadable — using defaults", exc_info=True)
        return dict(_DEFAULT_STANDARDS)


# ── Multi-vendor running-config parser ──────────────────────────────────────

def detect_vendor(config: str) -> str:
    c = config.lower()
    if "set deviceconfig" in c or "pan-os" in c:
        return "Palo Alto"
    if "host-name" in c and "{" in config:
        return "Juniper"
    if re.search(r"^hostname \S", config, re.MULTILINE) or "ios-xe" in c or "ios xe" in c:
        return "Cisco"
    if "arista" in c or "eos" in c:
        return "Arista"
    return "Unknown"


def parse_config(config: str, vendor_hint: str = "") -> dict:
    """Extract a normalized feature dict from a device running-config.

    Vendor-agnostic best-effort: looks for the markers each NOS uses for NTP,
    syslog, AAA, SNMP, SSH/telnet, BGP/OSPF, interfaces, HA, banners.
    """
    out = {
        "hostname": "", "site": "", "role": "",
        "ntp": False, "syslog": False, "name_servers": [],
        "telnet_enabled": False, "ssh_enabled": False, "no_ssh": False,
        "snmp_communities": [], "snmp_v3": False,
        "snmp_default_community": False, "snmp_v2_only": False,
        "aaa": False, "tacacs": False, "login_banner": False,
        "bgp_asn": "", "ospf": False, "ha": False,
        "interfaces": [], "vlans": [], "vendor": vendor_hint or "",
        "interface_count": 0,
    }
    if not config:
        return out
    c = config
    lc = c.lower()
    out["vendor"] = vendor_hint or detect_vendor(c)

    # hostname
    for pat in (r"host-name\s+([A-Za-z0-9._-]+)", r"^hostname\s+([A-Za-z0-9._-]+)",
                r"system\s+hostname\s+([A-Za-z0-9._-]+)"):
        m = re.search(pat, c, re.MULTILINE)
        if m:
            out["hostname"] = m.group(1).rstrip(";")
            break

    # site / role from header comments
    m = re.search(r"(?:Site|site)\s*[:\-]\s*([^\n;]+)", c)
    if m:
        out["site"] = m.group(1).strip()
    m = re.search(r"(?:Role|role)\s*[:\-]\s*([^\n;]+)", c)
    if m:
        out["role"] = m.group(1).strip()[:80]

    # services
    out["ntp"] = bool(re.search(r"\bntp\b", lc))
    out["syslog"] = bool(re.search(r"syslog|logging host|logging\s+\d", lc))
    out["name_servers"] = re.findall(r"name-server\s*\{?\s*([\d.]+)", c) or \
        re.findall(r"name-server\s+([\d.]+)", c) or \
        re.findall(r"dns-setting servers \S+ ([\d.]+)", c)
    out["aaa"] = bool(re.search(r"\baaa\b|tacacs|radius", lc))
    out["tacacs"] = "tacacs" in lc
    out["login_banner"] = bool(re.search(r"banner|login-message|message-of-the-day|set deviceconfig system login-banner", lc))

    # management protocols
    out["telnet_enabled"] = bool(re.search(r"^\s*transport input.*telnet|set system services telnet|service telnet", c, re.MULTILINE | re.IGNORECASE)) \
        or bool(re.search(r"\btelnet\b", lc) and "no telnet" not in lc and "disable" not in lc and "telnet" in lc and ("services telnet" in lc or "input telnet" in lc))
    out["ssh_enabled"] = bool(re.search(r"\bssh\b|set system services ssh|transport input ssh|ip ssh", lc))
    out["no_ssh"] = not out["ssh_enabled"]

    # SNMP
    comms = re.findall(r"snmp-server community\s+(\S+)", c) + re.findall(r"community\s+([A-Za-z0-9_]+)\s*\{", c)
    out["snmp_communities"] = comms
    out["snmp_v3"] = bool(re.search(r"snmp.*v3|snmp-server group.*v3|usm", lc))
    out["snmp_default_community"] = any(x.lower() in ("public", "private") for x in comms)
    has_snmp = bool(re.search(r"\bsnmp\b", lc))
    out["snmp_v2_only"] = has_snmp and not out["snmp_v3"]

    # routing
    m = re.search(r"autonomous-system\s+(\d+)|router bgp\s+(\d+)|local-as\s+(\d+)", c)
    if m:
        out["bgp_asn"] = next((g for g in m.groups() if g), "")
    out["ospf"] = bool(re.search(r"ospf", lc))

    # HA
    out["ha"] = bool(re.search(r"\bha\b|redundancy|chassis cluster|high-availability|hsrp|vrrp|802\.3ad|port-channel|\bae\d", lc))

    # interfaces (vendor-spanning)
    ifaces = []
    for m in re.finditer(r"([gx]e-\d+/\d+/\d+|et-\d+/\d+/\d+|ae\d+)\s*\{\s*description\s+\"([^\"]*)\"", c):
        ifaces.append({"name": m.group(1), "description": m.group(2)})
    for m in re.finditer(r"^interface\s+(\S+)", c, re.MULTILINE):
        ifaces.append({"name": m.group(1), "description": ""})
    for m in re.finditer(r"set network interface ethernet (\S+)", c):
        ifaces.append({"name": m.group(1), "description": ""})
    # dedupe by name
    seen = {}
    for it in ifaces:
        seen.setdefault(it["name"], it)
    out["interfaces"] = list(seen.values())
    out["interface_count"] = len(out["interfaces"])

    # vlans
    out["vlans"] = sorted({int(v) for v in re.findall(r"vlan\s+(\d{1,4})", lc)})

    return out


# ── Graph enrichment (so info-boxes populate for ANY topology) ──────────────

_AWS_TYPES = {
    "aws-vpc", "aws-tgw", "aws-dx", "aws-dx-gw", "aws-vpn", "aws-ga",
    "aws-cloudwan", "aws-gwlb", "aws-nfw", "aws-alb", "aws-gw-ep", "aws-r53",
    "aws-ad", "aws-kms", "aws-ct", "aws-privatelink", "aws-netmgr",
    "aws-guardduty", "aws-securityhub", "aws-shield",
    "az-vnet", "gcp-vpc", "cloud-region",
}


def _node_meta(node: dict) -> dict:
    """Merged view of a node's config+meta (config wins where both present)."""
    meta = dict(node.get("meta") or {})
    cfg = node.get("config") or {}
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except Exception:
            cfg = {}
    for k, v in cfg.items():
        meta.setdefault(k, v)
    return meta


def enrich_graph(graph: dict, conn, topo_id: str | None = None) -> dict:
    """Populate node['meta'] from node.config + ni_devices + parsed configs.

    Returns the same graph object (mutated). Best-effort: never raises.
    """
    nodes = graph.get("nodes", [])
    if not nodes:
        return graph

    # Index ni_devices + configs by node_id (and id) for this topology.
    dev_by_node: dict[str, dict] = {}
    cfg_by_dev: dict[str, str] = {}
    try:
        rows = conn.execute(
            "SELECT * FROM ni_devices" + (" WHERE topology_id=?" if topo_id else ""),
            ((topo_id,) if topo_id else ()),
        ).fetchall()
        for r in rows:
            d = dict(r) if hasattr(r, "keys") else {}
            for key in (d.get("node_id"), d.get("id"), d.get("label")):
                if key:
                    dev_by_node[str(key)] = d
        crows = conn.execute(
            "SELECT device_id, config_text FROM ni_device_configs"
        ).fetchall()
        for r in crows:
            d = dict(r) if hasattr(r, "keys") else {}
            if d.get("config_text"):
                cfg_by_dev[str(d["device_id"])] = d["config_text"]
    except Exception:
        logger.debug("ni_devices/configs unavailable for enrichment", exc_info=True)

    for n in nodes:
        meta = _node_meta(n)
        nid = str(n.get("id", ""))
        dev = dev_by_node.get(nid) or dev_by_node.get(str(n.get("label", "")))
        if dev:
            meta.setdefault("vendor", dev.get("vendor"))
            meta.setdefault("model", dev.get("model"))
            meta.setdefault("firmware", dev.get("firmware_version"))
            meta.setdefault("eol_date", dev.get("eol_date"))
            meta.setdefault("eos_date", dev.get("eos_date"))
            meta.setdefault("site", dev.get("site"))
            meta.setdefault("criticality", dev.get("criticality_score"))
            meta.setdefault("downstream_count", dev.get("downstream_count"))
            if dev.get("eol_date"):
                n.setdefault("eol", dev["eol_date"])
            if dev.get("vendor"):
                n.setdefault("vendor", dev["vendor"])
            if dev.get("model"):
                n.setdefault("model", dev["model"])
            # parse running config if present
            cfg_text = cfg_by_dev.get(str(dev.get("id", "")))
            if cfg_text:
                parsed = parse_config(cfg_text, dev.get("vendor", ""))
                n["_parsed_config"] = parsed
                meta.setdefault("mgmt_ip", parsed.get("name_servers") and None)
                if parsed.get("bgp_asn"):
                    meta.setdefault("bgp_asn", parsed["bgp_asn"])
                    meta.setdefault("bgp_session_count", max(1, len(parsed.get("interfaces", [])) // 4))
                meta["ssh_enabled"] = parsed.get("ssh_enabled")
                meta["telnet_enabled"] = parsed.get("telnet_enabled")
                meta["snmp_version"] = "v3" if parsed.get("snmp_v3") else ("v2c" if parsed.get("snmp_v2_only") else "")
                if parsed.get("ha"):
                    meta.setdefault("ha_pair", True)
                if parsed.get("vlans"):
                    meta.setdefault("vlans", parsed["vlans"])
                if parsed.get("ospf"):
                    meta.setdefault("ospf_areas", ["0"])
                meta.setdefault("port_count", parsed.get("interface_count") or 24)

        # bgp_asn / asn from node config
        cfg = n.get("config") or {}
        if isinstance(cfg, dict):
            if cfg.get("bgp_asn"):
                meta.setdefault("bgp_asn", cfg["bgp_asn"])
                meta.setdefault("bgp_session_count", 2)
            if cfg.get("asn"):
                meta.setdefault("bgp_asn", cfg["asn"])
                meta.setdefault("bgp_session_count", 2)
            if cfg.get("cidr"):
                meta.setdefault("subnets", [cfg["cidr"]])
            if cfg.get("role"):
                meta.setdefault("role", cfg["role"])
        n["meta"] = meta
    return graph


# ── Deterministic finding builders ──────────────────────────────────────────

def _finding(category, severity, title, detail="", device=None, evidence="",
             recommendation="", standard_ref="", source="rule") -> dict:
    return {
        "id": uuid.uuid4().hex[:8],
        "category": category,
        "severity": severity,
        "device": device,
        "title": title,
        "detail": detail,
        "evidence": evidence,
        "recommendation": recommendation,
        "standard_ref": standard_ref,
        "source": source,
    }


def _device_label(node: dict, parsed: dict | None = None) -> str:
    if parsed and parsed.get("hostname"):
        return parsed["hostname"]
    return node.get("label") or node.get("id") or "device"


def _check_naming(nodes, std) -> list[dict]:
    findings = []
    nm = std.get("naming", {})
    pat = nm.get("pattern")
    allowed = set(nm.get("allowed_roles") or [])
    maxlen = int(nm.get("max_length") or 64)
    if not pat:
        return findings
    rx = re.compile(pat, re.IGNORECASE)
    for n in nodes:
        parsed = n.get("_parsed_config") or {}
        name = parsed.get("hostname") or n.get("label") or ""
        # only assess physical/managed devices (skip cloud constructs)
        if (n.get("type") or "").lower() in _AWS_TYPES:
            continue
        if not name:
            continue
        if not rx.match(name):
            findings.append(_finding(
                "deviations", "low",
                f"Hostname '{name}' violates naming standard",
                detail=f"Approved convention: {nm.get('description', pat)}.",
                device=name, evidence=name,
                recommendation=f"Rename to match {nm.get('description', pat)} during the migration window.",
                standard_ref="SOP-NAMING-01",
            ))
        elif allowed:
            parts = name.upper().split("-")
            if len(parts) >= 2 and parts[1] not in allowed:
                findings.append(_finding(
                    "deviations", "low",
                    f"Hostname '{name}' uses non-standard role token '{parts[1]}'",
                    detail=f"Allowed role tokens: {', '.join(sorted(allowed))}.",
                    device=name, evidence=parts[1],
                    recommendation="Align role token with the approved list.",
                    standard_ref="SOP-NAMING-01",
                ))
        if len(name) > maxlen:
            findings.append(_finding(
                "deviations", "low", f"Hostname '{name}' exceeds {maxlen} chars",
                device=name, standard_ref="SOP-NAMING-01",
            ))
    return findings


def _check_sop(nodes, std) -> list[dict]:
    findings = []
    reqs = std.get("sop_requirements") or []
    for n in nodes:
        parsed = n.get("_parsed_config")
        if not parsed:
            continue  # no config to assess
        name = _device_label(n, parsed)
        for req in reqs:
            key = req.get("key")
            val = parsed.get(key)
            present = bool(val)
            if not present:
                findings.append(_finding(
                    "deviations", req.get("severity", "medium"),
                    f"{name}: missing {req.get('label', key)}",
                    detail=f"Approved SOP requires {req.get('label', key)} on every managed device.",
                    device=name,
                    recommendation=f"Add {req.get('label', key)} to the target config before cutover.",
                    standard_ref=req.get("ref", ""),
                ))
    return findings


def _check_misconfig(nodes, std) -> list[dict]:
    findings = []
    rules = std.get("security_rules") or []
    for n in nodes:
        parsed = n.get("_parsed_config")
        if not parsed:
            continue
        name = _device_label(n, parsed)
        for rule in rules:
            key = rule.get("key")
            if parsed.get(key) == rule.get("when", True):
                ev = ""
                if key == "snmp_default_community":
                    ev = ", ".join(parsed.get("snmp_communities", []))
                findings.append(_finding(
                    "misconfigurations", rule.get("severity", "high"),
                    f"{name}: {rule.get('title', key)}",
                    detail=rule.get("title", key),
                    device=name, evidence=ev,
                    recommendation=rule.get("recommendation", ""),
                    standard_ref=rule.get("ref", ""),
                ))
    return findings


def _check_anomalies(nodes, edges, std) -> list[dict]:
    findings = []
    life = std.get("lifecycle", {})
    eol_days = int(life.get("eol_warning_days", 365))
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    # EOL / EOS proximity
    for n in nodes:
        meta = n.get("meta") or {}
        eol = meta.get("eol_date") or n.get("eol")
        if not eol:
            continue
        try:
            eol_dt = datetime.fromisoformat(str(eol).split("T")[0]).replace(tzinfo=timezone.utc)
        except Exception:
            continue
        days = (eol_dt - now).days
        if days < 0:
            findings.append(_finding(
                "anomalies", "high",
                f"{_device_label(n)} is past End-of-Life ({eol})",
                detail="Vendor no longer issues security or bug fixes; unsupported in production.",
                device=_device_label(n), evidence=str(eol),
                recommendation="Prioritize replacement; this device should be a migration target.",
                standard_ref="LCM-EOL",
            ))
        elif days <= eol_days:
            findings.append(_finding(
                "anomalies", "medium",
                f"{_device_label(n)} reaches EOL in {days} days ({eol})",
                device=_device_label(n), evidence=str(eol),
                recommendation="Schedule refresh within the current migration program.",
                standard_ref="LCM-EOL",
            ))

    # Firmware consistency per model
    fw_by_model: dict[str, set] = {}
    for n in nodes:
        meta = n.get("meta") or {}
        model = meta.get("model")
        fw = meta.get("firmware")
        if model and fw:
            fw_by_model.setdefault(model, set()).add(fw)
    maxfw = int(life.get("max_firmware_versions_per_model", 1))
    for model, versions in fw_by_model.items():
        if len(versions) > maxfw:
            findings.append(_finding(
                "anomalies", "medium",
                f"Firmware drift on {model}: {len(versions)} versions in fleet",
                detail="Mixed firmware complicates support and can cause protocol incompatibility.",
                evidence=", ".join(sorted(versions)),
                recommendation="Standardize on a single golden firmware version per model.",
                standard_ref="SOP-NET-031",
            ))

    # Single points of failure (degree-1 critical-ish nodes)
    deg: dict[str, int] = {}
    for e in edges:
        for k in (e.get("source"), e.get("target")):
            if k:
                deg[k] = deg.get(k, 0) + 1
    for n in nodes:
        meta = n.get("meta") or {}
        if (n.get("type") or "").lower() in _AWS_TYPES:
            continue
        d = deg.get(n.get("id"), 0)
        crit = float(meta.get("criticality") or 0)
        if d == 1 and not meta.get("ha_pair") and crit >= 50:
            findings.append(_finding(
                "anomalies", "high",
                f"{_device_label(n)} appears to be a single point of failure",
                detail="Critical device with a single uplink and no HA partner.",
                device=_device_label(n),
                recommendation="Add a redundant path / HA pair as part of the target design.",
                standard_ref="SOP-NET-040",
            ))
    return findings


def _topology_changes(phases, current_graph) -> list[dict]:
    findings = []
    id_to_label = {n.get("id"): (n.get("label") or n.get("id")) for n in current_graph.get("nodes", [])}
    for ph in phases:
        props = ph.get("properties_json") or ph.get("properties") or {}
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except Exception:
                props = {}
        new = props.get("new_devices") or []
        chg = props.get("changing_devices") or []
        ret = props.get("retiring_devices") or []
        pn = ph.get("phase_num", "?")
        title = ph.get("title", "")
        for d in new:
            findings.append(_finding(
                "topology_changes", "info", f"Phase {pn}: introduce {id_to_label.get(d, d)}",
                detail=title, device=id_to_label.get(d, d),
                recommendation="Stage, pre-validate, and lab-test before insertion.",
                standard_ref=f"PHASE-{pn}", source="rule"))
        for d in chg:
            findings.append(_finding(
                "topology_changes", "medium", f"Phase {pn}: reconfigure {id_to_label.get(d, d)}",
                detail=title, device=id_to_label.get(d, d),
                recommendation="Capture pre-change config + rollback plan.",
                standard_ref=f"PHASE-{pn}", source="rule"))
        for d in ret:
            findings.append(_finding(
                "topology_changes", "medium", f"Phase {pn}: decommission {id_to_label.get(d, d)}",
                detail=title, device=id_to_label.get(d, d),
                recommendation="Confirm no residual dependencies before power-down.",
                standard_ref=f"PHASE-{pn}", source="rule"))
    return findings


def _build_risks(all_findings, phases, std) -> list[dict]:
    """Roll high-severity findings + phase risk into a risk register."""
    risks = []
    for f in all_findings:
        if f["category"] in ("risks", "topology_changes", "recommendations"):
            continue
        if f["severity"] in ("critical", "high"):
            risks.append(_finding(
                "risks", f["severity"],
                f"Risk: {f['title']}",
                detail=f.get("detail") or f["title"],
                device=f.get("device"),
                recommendation=f.get("recommendation", ""),
                standard_ref=f.get("standard_ref", ""),
            ))
    # phase-level risk
    for ph in phases:
        rc = ph.get("rollback_criteria")
        if (ph.get("duration_days") or 0) <= 1:
            risks.append(_finding(
                "risks", "high",
                f"Phase {ph.get('phase_num','?')} '{ph.get('title','')}' is a same-day cutover",
                detail="Compressed maintenance window leaves little room for validation/rollback.",
                recommendation=f"Confirm rollback trigger: {rc or 'define explicit go/no-go'}.",
                standard_ref="CHG-MGMT",
            ))
    return risks


def _summary(findings_by_cat, std) -> dict:
    weights = std.get("risk_weights", {})
    sev_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    total = 0
    score = 0
    for cat, items in findings_by_cat.items():
        for f in items:
            total += 1
            sev_counts[f["severity"]] = sev_counts.get(f["severity"], 0) + 1
            if cat != "recommendations":
                score += weights.get(f["severity"], 0)
    # readiness 0-100 (100 = clean); cap penalty
    readiness = max(0, 100 - min(score, 100))
    posture = ("High Risk" if readiness < 40 else
               "Elevated" if readiness < 70 else
               "Manageable" if readiness < 90 else "Low Risk")
    return {
        "total_findings": total,
        "by_severity": sev_counts,
        "by_category": {c: len(v) for c, v in findings_by_cat.items()},
        "readiness_score": readiness,
        "risk_posture": posture,
    }


# ── LLM enrichment ──────────────────────────────────────────────────────────

def _llm_enrich(topo_name, det, phases, nodes) -> dict | None:
    """Ask the LLM for an executive narrative + extra recommendations.

    Returns {'narrative': str, 'recommendations': [finding,...]} or None.
    """
    try:
        from tools.llm.router import LLMRouter, LLMRequest
        router = LLMRouter()
        if not router.has_any_llm():
            return None
    except Exception:
        return None

    facts = {
        "topology": topo_name,
        "device_count": len(nodes),
        "phase_count": len(phases),
        "summary": det["summary"],
        "top_findings": [
            {"category": f["category"], "severity": f["severity"], "title": f["title"]}
            for cat in ("misconfigurations", "deviations", "anomalies")
            for f in det[cat][:6]
        ],
        "phases": [{"n": p.get("phase_num"), "title": p.get("title")} for p in phases],
    }
    schema = {
        "type": "object",
        "properties": {
            "narrative": {"type": "string"},
            "recommendations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "severity": {"type": "string", "enum": ["critical", "high", "medium", "low", "info"]},
                        "title": {"type": "string"},
                        "detail": {"type": "string"},
                        "recommendation": {"type": "string"},
                    },
                    "required": ["severity", "title", "recommendation"],
                },
            },
        },
        "required": ["narrative", "recommendations"],
    }
    sys = (
        "You are a senior network migration architect reviewing a brownfield network "
        "migration. Given the deterministic scan results, write a concise executive "
        "narrative (3-5 sentences) of migration readiness, then propose prioritized, "
        "actionable recommendations that go beyond the raw findings (sequencing, "
        "validation, rollback, dependencies). Be specific and grounded in the facts."
    )
    try:
        req = LLMRequest(
            messages=[{"role": "user", "content": json.dumps(facts, indent=2)}],
            system_prompt=sys,
            output_schema=schema,
            max_tokens=1500,
            temperature=0.4,
            classification="CUI",
        )
        resp = router.invoke("network_migration_analysis", req)
        data = resp.structured_output if resp else None
        if not data and resp and resp.content:
            data = json.loads(resp.content)
        if not data:
            return None
        recs = []
        for r in (data.get("recommendations") or [])[:8]:
            recs.append(_finding(
                "recommendations", r.get("severity", "medium"),
                r.get("title", "Recommendation"),
                detail=r.get("detail", ""),
                recommendation=r.get("recommendation", ""),
                standard_ref="AI-ADVISOR", source="llm",
            ))
        return {"narrative": data.get("narrative", ""), "recommendations": recs}
    except Exception:
        logger.warning("LLM migration enrichment failed — using deterministic only", exc_info=True)
        return None


def _deterministic_recommendations(findings_by_cat, phases) -> list[dict]:
    recs = []
    mis = findings_by_cat["misconfigurations"]
    crit = [f for f in mis if f["severity"] == "critical"]
    if crit:
        recs.append(_finding(
            "recommendations", "critical",
            "Remediate CAT I security defects before any cutover",
            detail=f"{len(crit)} critical misconfiguration(s) found (e.g. {crit[0]['title']}).",
            recommendation="Bake fixes into the target/golden config; do not migrate insecure config forward.",
            standard_ref="STIG"))
    eol = [f for f in findings_by_cat["anomalies"] if "End-of-Life" in f["title"] or "EOL" in f["title"]]
    if eol:
        recs.append(_finding(
            "recommendations", "high",
            "Sequence EOL/EOS devices first",
            detail=f"{len(eol)} device(s) at or near end-of-life.",
            recommendation="Make lifecycle-expired hardware the leading migration wave.",
            standard_ref="LCM"))
    if findings_by_cat["deviations"]:
        recs.append(_finding(
            "recommendations", "medium",
            "Normalize naming + SOP gaps in the target build",
            detail=f"{len(findings_by_cat['deviations'])} drift item(s) from approved docs.",
            recommendation="Use the migration as the opportunity to bring configs to standard.",
            standard_ref="SOP"))
    if phases:
        recs.append(_finding(
            "recommendations", "medium",
            "Pre-validate each phase in a lab and rehearse rollback",
            detail=f"{len(phases)} phase(s) planned.",
            recommendation="Require a signed go/no-go gate with tested rollback per phase.",
            standard_ref="CHG-MGMT"))
    return recs


# ── Public entrypoint ───────────────────────────────────────────────────────

def analyze_migration(topo_id, topo_name, graph, phases, conn, use_llm=True) -> dict:
    """Run the full six-dimension migration analysis for a topology."""
    std = load_standards()
    enrich_graph(graph, conn, topo_id)
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    deviations = _check_naming(nodes, std) + _check_sop(nodes, std)
    misconfigs = _check_misconfig(nodes, std)
    anomalies = _check_anomalies(nodes, edges, std)
    topo_changes = _topology_changes(phases, graph)

    findings_by_cat = {
        "recommendations": [],
        "anomalies": anomalies,
        "deviations": deviations,
        "misconfigurations": misconfigs,
        "topology_changes": topo_changes,
        "risks": [],
    }
    findings_by_cat["risks"] = _build_risks(
        anomalies + deviations + misconfigs, phases, std)

    # Recommendations: deterministic baseline, optionally enriched by LLM.
    det_recs = _deterministic_recommendations(findings_by_cat, phases)
    findings_by_cat["recommendations"] = det_recs

    summary = _summary(findings_by_cat, std)
    narrative = ""
    generated_by = "rules"

    if use_llm:
        det_view = {**findings_by_cat, "summary": summary}
        enriched = _llm_enrich(topo_name, det_view, phases, nodes)
        if enriched:
            generated_by = "llm+rules"
            narrative = enriched.get("narrative", "")
            findings_by_cat["recommendations"] = enriched.get("recommendations", []) + det_recs

    if not narrative:
        narrative = (
            f"Assessed {len(nodes)} device(s) across {len(phases)} migration phase(s). "
            f"Scan found {summary['by_severity']['critical']} critical, "
            f"{summary['by_severity']['high']} high, and "
            f"{summary['by_severity']['medium']} medium issue(s). "
            f"Migration risk posture: {summary['risk_posture']} "
            f"(readiness {summary['readiness_score']}/100)."
        )

    # sort each category by severity
    for cat in findings_by_cat:
        findings_by_cat[cat].sort(key=lambda f: _SEV_ORDER.get(f["severity"], 5))

    return {
        "topo_id": topo_id,
        "topo_name": topo_name,
        "generated_by": generated_by,
        "narrative": narrative,
        "summary": summary,
        **findings_by_cat,
    }
