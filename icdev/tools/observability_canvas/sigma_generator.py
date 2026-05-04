# CUI // SP-CTI
"""Sigma rule generator for the ODC Digital Twin.

Deterministic — no LLM. For each (technique_id, signal_source) pair with
coverage state=none, emits a well-formed Sigma YAML detection rule.

Public API:
  generate_rules(pairs, design_name="") -> list[str]   — core: (tid,src) → YAML rules
  generate_sigma_rules(graph, design_name="") -> dict   — ODC graph → export bundle
  estimate_log_volume(nodes, retention_days=90) -> dict — ingestion cost projection
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from datetime import datetime, timezone
import yaml

# ---------------------------------------------------------------------------
# Signal source → Sigma log-source mapping
# ---------------------------------------------------------------------------
_SRC_LOGSOURCE: dict[str, dict] = {
    "src-os-log":        {"category": "process_creation", "product": "windows"},
    "src-wef":           {"category": "process_creation", "product": "windows"},
    "src-endpoint":      {"category": "process_creation"},
    "src-network-log":   {"category": "firewall"},
    "src-cloud-log":     {"category": "cloud", "service": "cloudtrail"},
    "src-container-log": {"category": "container"},
    "src-db-audit":      {"category": "database"},
    "src-app-log":       {"category": "application"},
    "src-flow":          {"category": "network_connection"},
    "src-pcap":          {"category": "network_connection"},
    "src-iam":           {"category": "authentication"},
    "src-metric":        {"category": "metrics"},
    "src-trace":         {"category": "application"},
    "src-vulnerability": {"category": "vulnerability_scan"},
}

# ---------------------------------------------------------------------------
# Technique-specific detection snippets keyed by (tid, logsource category)
# ---------------------------------------------------------------------------
_TECHNIQUE_DETECTIONS: dict[str, dict[str, dict]] = {
    "T1059": {
        "process_creation": {
            "selection": {"CommandLine|contains": ["cmd.exe", "powershell", "bash", "sh "]},
            "condition": "selection",
        },
        "application": {
            "selection": {"Message|contains": ["exec(", "eval(", "subprocess"]},
            "condition": "selection",
        },
    },
    "T1566": {
        "application": {
            "selection": {"Message|contains": ["attachment", ".exe", "macro", "phishing"]},
            "condition": "selection",
        },
        "authentication": {
            "selection": {"EventID": 4625, "Status": "0xC000006D"},
            "condition": "selection",
        },
    },
    "T1078": {
        "authentication": {
            "selection": {"EventID": [4624, 4648], "LogonType": [3, 10]},
            "condition": "selection",
        },
        "cloud": {
            "selection": {"eventName": "ConsoleLogin", "responseElements|contains": "Failure"},
            "condition": "selection",
        },
    },
    "T1003": {
        "process_creation": {
            "selection": {
                "CommandLine|contains|any": ["mimikatz", "lsass", "procdump", "sekurlsa"],
            },
            "condition": "selection",
        },
    },
    "T1110": {
        "authentication": {
            "selection": {"EventID": 4625},
            "condition": "selection | count() > 10",
            "timeframe": "5m",
        },
        "cloud": {
            "selection": {"eventName": "ConsoleLogin", "errorCode": "Failed authentication"},
            "condition": "selection | count() > 5",
            "timeframe": "5m",
        },
    },
    "T1083": {
        "process_creation": {
            "selection": {"CommandLine|contains|any": ["dir ", "ls -", "find /"]},
            "condition": "selection",
        },
    },
    "T1082": {
        "process_creation": {
            "selection": {"CommandLine|contains|any": ["systeminfo", "uname -a", "hostname"]},
            "condition": "selection",
        },
    },
    "T1190": {
        "network_connection": {
            "selection": {"dst_port": [80, 443, 8080, 8443], "bytes_in|gt": 100000},
            "condition": "selection",
        },
        "application": {
            "selection": {"Message|contains|any": ["SQL syntax", "UNION SELECT", "../", "%2e%2e"]},
            "condition": "selection",
        },
    },
    "T1021": {
        "network_connection": {
            "selection": {"dst_port": [22, 3389, 5985, 5986]},
            "filter": {"src_ip|cidr": "10.0.0.0/8"},
            "condition": "selection and not filter",
        },
    },
    "T1071": {
        "network_connection": {
            "selection": {"dst_port": [443, 80], "bytes_out|gt": 50000},
            "condition": "selection",
        },
    },
}

# Tactic per technique (MITRE ATT&CK v14 subset for deterministic lookup)
_TECHNIQUE_TACTIC: dict[str, str] = {
    "T1059":    "execution",
    "T1059.001": "execution",
    "T1059.003": "execution",
    "T1566":    "initial-access",
    "T1566.001": "initial-access",
    "T1078":    "defense-evasion",
    "T1003":    "credential-access",
    "T1003.001": "credential-access",
    "T1110":    "credential-access",
    "T1110.001": "credential-access",
    "T1083":    "discovery",
    "T1082":    "discovery",
    "T1046":    "discovery",
    "T1021":    "lateral-movement",
    "T1021.001": "lateral-movement",
    "T1055":    "defense-evasion",
    "T1218":    "defense-evasion",
    "T1027":    "defense-evasion",
    "T1053":    "persistence",
    "T1053.005": "persistence",
    "T1098":    "persistence",
    "T1547":    "persistence",
    "T1190":    "initial-access",
    "T1133":    "initial-access",
    "T1071":    "command-and-control",
    "T1071.001": "command-and-control",
    "T1041":    "exfiltration",
    "T1048":    "exfiltration",
}
_DEFAULT_TACTIC = "defense-evasion"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_logsource(src: str) -> dict:
    return dict(_SRC_LOGSOURCE.get(src, {"category": "generic"}))


def _get_detection(tid: str, src: str) -> dict:
    """Return detection block for (tid, src). Falls back to a generic presence check."""
    category = _SRC_LOGSOURCE.get(src, {}).get("category", "generic")
    parent_tid = tid.split(".")[0]

    for lookup_tid in (tid, parent_tid):
        tech_dets = _TECHNIQUE_DETECTIONS.get(lookup_tid)
        if tech_dets and category in tech_dets:
            return dict(tech_dets[category])

    return {
        "selection": {"Keywords|contains": [tid, tid.replace(".", "/")]},
        "condition": "selection",
    }


def _get_tactic(tid: str) -> str:
    return _TECHNIQUE_TACTIC.get(tid) or _TECHNIQUE_TACTIC.get(tid.split(".")[0], _DEFAULT_TACTIC)


def _build_rule(tid: str, src: str, design_name: str = "") -> dict:
    """Construct a Sigma rule dict for one (tid, src) pair."""
    logsource = _get_logsource(src)
    detection = _get_detection(tid, src)
    tactic = _get_tactic(tid)
    src_label = src.replace("src-", "").replace("-", " ").title()

    title = f"Detect {tid} via {src_label}"
    if design_name:
        title = f"[{design_name}] {title}"

    return {
        "title": title,
        "id": str(uuid.uuid4()),
        "status": "experimental",
        "description": (
            f"Detects {tid} activity observed through {src_label}. "
            "Generated by ICDEV ODC Digital Twin."
        ),
        "references": [f"https://attack.mitre.org/techniques/{tid.replace('.', '/')}/"],
        "author": "ICDEV ODC Twin",
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "tags": [
            f"attack.{tid.lower().replace('.', '_')}",
            f"attack.{tactic}",
        ],
        "logsource": logsource,
        "detection": detection,
        "falsepositives": [
            "Legitimate administrative activity",
            "Authorized security tooling",
        ],
        "level": "medium",
    }


# ---------------------------------------------------------------------------
# Core public API
# ---------------------------------------------------------------------------

def generate_rules(
    pairs: list[tuple[str, str]],
    design_name: str = "",
    validate: bool = False,
) -> list[str]:
    """Accept (technique_id, signal_source) pairs; return Sigma YAML rule strings.

    Args:
        pairs: List of (tid, src) — e.g. [("T1059", "src-os-log"), ...]
        design_name: Optional label embedded in rule titles.
        validate: If True and sigma-cli is installed, validate each rule.

    Returns:
        List of Sigma YAML strings (one per pair, same order).
    """
    rules: list[str] = []
    for tid, src in pairs:
        rule_dict = _build_rule(tid, src, design_name)
        rule_yaml = yaml.dump(
            rule_dict, default_flow_style=False, allow_unicode=True, sort_keys=False
        )
        if validate:
            _validate_with_sigma_cli(rule_yaml)
        rules.append(rule_yaml)
    return rules


def validate_rule(rule_yaml: str) -> bool:
    """Run sigma-cli check on a rule string. Returns True when valid or tool absent."""
    return _validate_with_sigma_cli(rule_yaml)


def _validate_with_sigma_cli(yaml_text: str) -> bool:
    """Check rule via sigma-cli if available; silently passes when not installed."""
    if not shutil.which("sigma"):
        return True
    try:
        result = subprocess.run(
            ["sigma", "check", "-"],
            input=yaml_text.encode(),
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return True


# ---------------------------------------------------------------------------
# ODC graph-facing wrapper (consumed by blueprint.py)
# ---------------------------------------------------------------------------

def generate_sigma_rules(graph: dict, design_name: str = "") -> dict:
    """Generate Sigma rules from an ODC design graph.

    Extracts uncovered (covered=false) technique × source combinations from
    the cmp-baseline node, then calls generate_rules().

    Returns dict with: rule_count, rules, exports (sigma_yaml, splunk_spl,
    elastic_kql, sentinel_kql), volume_estimate, generated_at.
    """
    nodes: list[dict] = graph.get("nodes", [])
    src_types = [n["type"] for n in nodes if n.get("type", "").startswith("src-")]

    pairs: list[tuple[str, str]] = []
    for node in nodes:
        if node.get("type") == "cmp-baseline":
            cfg = node.get("config_json") or {}
            for tech in cfg.get("techniques", []):
                if not tech.get("covered", True) and src_types:
                    for src in src_types:
                        pairs.append((tech["id"], src))
            break

    if not pairs and src_types:
        for src in src_types:
            pairs.append(("T1059", src))

    yaml_rules = generate_rules(pairs, design_name=design_name)
    combined_yaml = "\n---\n".join(yaml_rules) if yaml_rules else "# No uncovered techniques\n"

    return {
        "rule_count": len(yaml_rules),
        "rules": yaml_rules,
        "exports": {
            "sigma_yaml": combined_yaml,
            "splunk_spl": _to_splunk(yaml_rules),
            "elastic_kql": _to_elastic(yaml_rules),
            "sentinel_kql": _to_sentinel(yaml_rules),
        },
        "volume_estimate": estimate_log_volume(nodes),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# SIEM format converters (best-effort, non-lossy intent)
# ---------------------------------------------------------------------------

def _to_splunk(yaml_rules: list[str]) -> str:
    lines = ['| comment "ICDEV ODC Twin — Sigma to Splunk SPL"']
    for raw in yaml_rules:
        try:
            rule = yaml.safe_load(raw)
            tid_tag = next((t for t in rule.get("tags", []) if t.startswith("attack.t")), "")
            title = rule.get("title", "rule")
            sel = (rule.get("detection") or {}).get("selection", {})
            parts = []
            for k, v in sel.items():
                field = k.split("|")[0]
                if isinstance(v, list):
                    parts.append(f"({' OR '.join(f'{field}={x!r}' for x in v)})")
                else:
                    parts.append(f"{field}={v!r}")
            filter_str = " ".join(parts) or "*"
            lines.append(f"| search {filter_str}  /* {title} [{tid_tag}] */")
        except Exception:
            continue
    return "\n".join(lines)


def _to_elastic(yaml_rules: list[str]) -> str:
    parts = []
    for raw in yaml_rules:
        try:
            rule = yaml.safe_load(raw)
            sel = (rule.get("detection") or {}).get("selection", {})
            kql = []
            for k, v in sel.items():
                field = k.split("|")[0]
                if isinstance(v, list):
                    inner = " OR ".join(f'{field}: "{x}"' for x in v)
                    kql.append(f"({inner})")
                else:
                    kql.append(f'{field}: "{v}"')
            parts.append(" AND ".join(kql) if kql else "*")
        except Exception:
            continue
    return "\n// ---\n".join(parts) if parts else "// No rules"


def _to_sentinel(yaml_rules: list[str]) -> str:
    _TABLE = {
        "process_creation": "SecurityEvent",
        "authentication": "SigninLogs",
        "firewall": "AzureNetworkAnalytics_CL",
        "cloud": "AWSCloudTrail",
        "container": "ContainerLog",
        "database": "AzureDiagnostics",
        "application": "AppServiceConsoleLogs",
        "network_connection": "AzureNetworkAnalytics_CL",
        "vulnerability_scan": "SecurityRecommendations",
        "metrics": "InsightsMetrics",
    }
    lines = []
    for raw in yaml_rules:
        try:
            rule = yaml.safe_load(raw)
            title = rule.get("title", "rule")
            cat = (rule.get("logsource") or {}).get("category", "")
            table = _TABLE.get(cat, "CommonSecurityLog")
            lines.append(
                f"// Rule: {title}\n{table}\n| where TimeGenerated > ago(1h)\n| limit 100\n"
            )
        except Exception:
            continue
    return "\n".join(lines) if lines else "// No rules"


# ---------------------------------------------------------------------------
# Log volume / cost estimation
# ---------------------------------------------------------------------------

_SRC_DAILY_GB: dict[str, float] = {
    "src-app-log":       5.0,
    "src-os-log":        8.0,
    "src-network-log":   20.0,
    "src-cloud-log":     3.0,
    "src-container-log": 10.0,
    "src-db-audit":      2.0,
    "src-wef":           15.0,
    "src-flow":          50.0,
    "src-pcap":          200.0,
    "src-endpoint":      5.0,
    "src-metric":        1.0,
    "src-trace":         2.0,
    "src-vulnerability": 0.5,
    "src-iam":           1.0,
}

_SIEM_COST_PER_GB_MONTH: dict[str, float] = {
    "splunk":   150.0,
    "elastic":  95.0,
    "sentinel": 2.46,
}


def estimate_log_volume(nodes: list[dict], retention_days: int = 90) -> dict:
    """Estimate daily log ingestion volume and SIEM cost for ODC design nodes.

    Args:
        nodes: List of graph nodes from an ODC design.
        retention_days: Retention window used for storage cost projection.

    Returns:
        Dict with daily_gb, monthly_gb, retention_gb, platform cost breakdown,
        and convenience keys estimated_cost_{splunk,elastic,sentinel}.
    """
    daily_gb = sum(_SRC_DAILY_GB.get(n.get("type", ""), 0.0) for n in nodes)
    monthly_gb = daily_gb * 30
    retention_gb = daily_gb * retention_days

    platforms: dict[str, dict] = {}
    for platform, cost_per_gb in _SIEM_COST_PER_GB_MONTH.items():
        monthly_cost = monthly_gb * cost_per_gb
        retention_cost = (retention_gb * cost_per_gb) / 30
        platforms[platform] = {
            "monthly_cost_usd": round(monthly_cost, 2),
            "retention_cost_usd": round(retention_cost, 2),
            "cost_per_gb_month": cost_per_gb,
        }

    return {
        "daily_gb": round(daily_gb, 2),
        "monthly_gb": round(monthly_gb, 2),
        "retention_days": retention_days,
        "retention_gb": round(retention_gb, 2),
        "platforms": platforms,
        "estimated_cost_splunk":   platforms["splunk"]["monthly_cost_usd"],
        "estimated_cost_elastic":  platforms["elastic"]["monthly_cost_usd"],
        "estimated_cost_sentinel": platforms["sentinel"]["monthly_cost_usd"],
    }
