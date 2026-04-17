# CUI // SP-CTI
"""ICDEV ODC Digital Twin — Deterministic Sigma Rule Generator.

Emits Sigma YAML rules from an observability design graph and exports
to Splunk SPL, Elastic KQL, and Microsoft Sentinel KQL.
Also provides log volume / SIEM cost estimation.

No LLM dependency — all rules are template-driven and deterministic.
"""

import uuid
from datetime import datetime, timezone

# ── MITRE technique → Sigma rule templates ────────────────────────────────────
# Each entry: (rule_id_suffix, title, technique_id, tactic, logsource, detection_str, level)
_TECHNIQUE_RULES: list[tuple] = [
    # Execution
    (
        "cmd-scripting-interpreter",
        "Command and Scripting Interpreter Execution",
        "T1059",
        "execution",
        {"product": "windows", "service": "security"},
        "EventID in (4688, 4689) and CommandLine contains ('cmd.exe', 'powershell.exe', 'wscript.exe', 'cscript.exe')",
        "high",
        "EventID IN (4688) AND (CommandLine LIKE '%cmd.exe%' OR CommandLine LIKE '%powershell%')",
        "event.code:(4688) AND (process.command_line:*cmd.exe* OR process.command_line:*powershell*)",
        "DeviceProcessEvents | where FileName in ('cmd.exe', 'powershell.exe', 'wscript.exe')",
    ),
    # Credential Access
    (
        "os-credential-dumping",
        "OS Credential Dumping Detected",
        "T1003",
        "credential-access",
        {"product": "windows", "service": "security"},
        "EventID in (4624, 4625) and LogonType in (3, 10)",
        "critical",
        "EventID IN (4624, 4625) AND LogonType IN (3, 10)",
        "event.code:(4624 OR 4625) AND winlog.event_data.LogonType:(3 OR 10)",
        "SecurityEvent | where EventID in (4624, 4625) and LogonType in (3, 10)",
    ),
    # Persistence
    (
        "account-creation",
        "New Local or Domain Account Created",
        "T1136",
        "persistence",
        {"product": "windows", "service": "security"},
        "EventID in (4720, 4722, 4741)",
        "medium",
        "EventID IN (4720, 4722, 4741)",
        "event.code:(4720 OR 4722 OR 4741)",
        "SecurityEvent | where EventID in (4720, 4722, 4741)",
    ),
    # Privilege Escalation
    (
        "valid-accounts-privilege",
        "Valid Account Privilege Escalation",
        "T1078",
        "privilege-escalation",
        {"product": "windows", "service": "security"},
        "EventID in (4672, 4728, 4732, 4756)",
        "high",
        "EventID IN (4672, 4728, 4732, 4756)",
        "event.code:(4672 OR 4728 OR 4732 OR 4756)",
        "SecurityEvent | where EventID in (4672, 4728, 4732, 4756)",
    ),
    # Lateral Movement
    (
        "remote-services-lateral",
        "Lateral Movement via Remote Services",
        "T1021",
        "lateral-movement",
        {"product": "network", "service": "firewall"},
        "dst_port in (22, 3389, 445, 5985, 5986)",
        "high",
        "dest_port IN (22, 3389, 445, 5985, 5986) | stats count by src_ip dest_ip dest_port",
        "destination.port:(22 OR 3389 OR 445 OR 5985 OR 5986)",
        "CommonSecurityLog | where DestinationPort in (22, 3389, 445, 5985, 5986)",
    ),
    # Defense Evasion
    (
        "impair-defenses",
        "Defense Evasion — Audit Log Cleared",
        "T1562",
        "defense-evasion",
        {"product": "windows", "service": "security"},
        "EventID in (1102, 4719)",
        "critical",
        "EventID IN (1102, 4719)",
        "event.code:(1102 OR 4719)",
        "SecurityEvent | where EventID in (1102, 4719)",
    ),
    # Exfiltration / Impact
    (
        "data-encrypted-impact",
        "Ransomware Activity — Data Encrypted for Impact",
        "T1486",
        "impact",
        {"product": "windows", "service": "sysmon"},
        "EventID=11 and TargetFilename contains ('.encrypted', '.locked', '.crypto')",
        "critical",
        "EventID=11 AND (TargetFilename=\"*.encrypted*\" OR TargetFilename=\"*.locked*\")",
        "file.extension:(encrypted OR locked OR crypto)",
        "SysmonEvent | where EventID == 11 and TargetFilename has_any('.encrypted', '.locked')",
    ),
    # Discovery
    (
        "scheduled-task-discovery",
        "Scheduled Task Created for Persistence",
        "T1053",
        "persistence",
        {"product": "windows", "service": "security"},
        "EventID in (4698, 4702)",
        "medium",
        "EventID IN (4698, 4702)",
        "event.code:(4698 OR 4702)",
        "SecurityEvent | where EventID in (4698, 4702)",
    ),
    # Collection
    (
        "process-injection-detection",
        "Process Injection Detected",
        "T1055",
        "defense-evasion",
        {"product": "windows", "service": "sysmon"},
        "EventID in (8, 10) and SourceImage != TargetImage",
        "high",
        "EventID IN (8, 10) AND SourceImage!=\"*\\\\lsass.exe\"",
        "event.code:(8 OR 10)",
        "SysmonEvent | where EventID in (8, 10) and SourceImage != TargetImage",
    ),
    # Application Layer Protocol
    (
        "app-layer-c2",
        "Suspicious Application Layer Protocol — C2 Beaconing",
        "T1071",
        "command-and-control",
        {"product": "network", "service": "dns"},
        "query_length > 60 or query_count > 100",
        "medium",
        "index=dns | stats count by src_ip, query | where count > 100",
        "dns.question.name:* AND dns.response_code:0",
        "DnsEvents | summarize count() by ClientIP, Name | where count_ > 100",
    ),
    # Brute Force
    (
        "brute-force-auth",
        "Brute Force Authentication Attack",
        "T1110",
        "credential-access",
        {"product": "windows", "service": "security"},
        "EventID=4625 and count > 5 within 5min",
        "high",
        "EventID=4625 | stats count by src_ip, user | where count > 5",
        "event.code:4625",
        "SecurityEvent | where EventID == 4625 | summarize count() by Account, IpAddress | where count_ > 5",
    ),
    # Cloud / IAM
    (
        "cloud-iam-anomaly",
        "Cloud IAM Privilege Escalation",
        "T1078.004",
        "privilege-escalation",
        {"product": "aws", "service": "cloudtrail"},
        "eventName in ('AssumeRole', 'CreateAccessKey', 'AttachUserPolicy')",
        "high",
        "sourcetype=aws:cloudtrail eventName IN (AssumeRole, CreateAccessKey)",
        "cloud.account.id:* AND event.action:(AssumeRole OR CreateAccessKey)",
        "AWSCloudTrail | where EventName in ('AssumeRole', 'CreateAccessKey', 'AttachUserPolicy')",
    ),
    # Container escape
    (
        "container-escape",
        "Container Escape via Privileged Execution",
        "T1611",
        "privilege-escalation",
        {"product": "linux", "service": "auditd"},
        "syscall=execve and container_id!='' and euid=0",
        "critical",
        "index=linux syscall=execve euid=0 container_id!=\"\"",
        "process.parent.name:dockerd AND user.id:0",
        "ContainerLog | where ProcessName != '' and UserName == 'root'",
    ),
]

# ── Source-type → rules mapping ────────────────────────────────────────────────
# Which source types trigger which subset of rules
_SOURCE_TECHNIQUE_MAP: dict[str, list[str]] = {
    "src-os-log":       ["T1059", "T1003", "T1136", "T1078", "T1562", "T1486", "T1053", "T1055"],
    "src-network-log":  ["T1021", "T1071"],
    "src-cloud-log":    ["T1078.004"],
    "src-container-log":["T1611"],
    "src-endpoint":     ["T1059", "T1003", "T1055", "T1562"],
    "src-iam":          ["T1078", "T1110", "T1078.004"],
    "src-app-log":      ["T1071", "T1110"],
    "src-db-audit":     ["T1003"],
    "src-flow":         ["T1021"],
    "src-wef":          ["T1059", "T1003", "T1078", "T1562"],
}

# ── Sigma YAML template ────────────────────────────────────────────────────────

def _sigma_yaml_rule(
    rule_id: str,
    title: str,
    technique_id: str,
    tactic: str,
    logsource: dict,
    detection_condition: str,
    level: str,
    generated_at: str,
) -> str:
    logsource_lines = "\n".join(f"    {k}: {v}" for k, v in logsource.items())
    tactic_tag = tactic.replace("-", "_")
    return f"""title: {title}
id: {rule_id}
status: experimental
description: >
    ICDEV ODC Digital Twin — auto-generated detection rule for MITRE ATT&CK {technique_id}.
    Generated: {generated_at}
author: ICDEV ODC Twin
date: {generated_at[:10]}
tags:
    - attack.{tactic_tag}
    - attack.{technique_id.lower().replace('.', '_')}
logsource:
{logsource_lines}
detection:
    selection:
        condition: {detection_condition}
    condition: selection
falsepositives:
    - Administrative activity
    - Authorized security tooling
level: {level}
"""


# ── Log volume / SIEM cost estimation ────────────────────────────────────────────

# Estimated GB/day per source type (conservative mid-range estimates)
_VOLUME_GB_DAY: dict[str, float] = {
    "src-app-log":       0.5,
    "src-os-log":        0.8,
    "src-network-log":   2.0,
    "src-cloud-log":     0.3,
    "src-container-log": 1.0,
    "src-db-audit":      0.4,
    "src-wef":           0.6,
    "src-flow":          3.0,
    "src-pcap":          15.0,
    "src-endpoint":      0.7,
    "src-metric":        0.2,
    "src-trace":         0.3,
    "src-vulnerability": 0.1,
    "src-iam":           0.2,
}

# SIEM cost per GB ingested (approximate, USD)
_SIEM_COST_PER_GB: dict[str, float] = {
    "Splunk Cloud":         2.25,
    "Microsoft Sentinel":   2.46,
    "Elastic Cloud":        1.00,
    "Google Chronicle":     0.65,
    "IBM QRadar SaaS":      1.80,
}


def estimate_log_volume(nodes: list, retention_days: int = 90) -> dict:
    """Estimate daily log volume and SIEM cost given a list of graph nodes.

    Args:
        nodes: List of node dicts from a design graph.
        retention_days: Days of log retention for storage estimates.

    Returns:
        Dict with gb_per_day, total_gb, cost_estimates per SIEM, per_source_breakdown.
    """
    per_source: dict[str, float] = {}
    for node in nodes:
        ntype = node.get("type", "")
        vol = _VOLUME_GB_DAY.get(ntype)
        if vol is not None:
            per_source[node.get("label", ntype)] = vol

    total_per_day = sum(per_source.values())
    total_gb = round(total_per_day * retention_days, 1)

    cost_estimates = {
        siem: round(total_gb * cost_per_gb, 2)
        for siem, cost_per_gb in _SIEM_COST_PER_GB.items()
    }

    return {
        "gb_per_day": round(total_per_day, 2),
        "total_gb": total_gb,
        "retention_days": retention_days,
        "per_source_breakdown": per_source,
        "cost_estimates": cost_estimates,
    }


# ── Public API ─────────────────────────────────────────────────────────────────


def generate_sigma_rules(graph_data: dict, design_name: str = "") -> dict:
    """Generate Sigma detection rules from an observability design graph.

    Examines which signal source types are present in the design and emits
    the corresponding MITRE ATT&CK–mapped Sigma rules, plus exports for
    Splunk SPL, Elastic KQL, and Microsoft Sentinel KQL.

    Args:
        graph_data: Dict with "nodes" and "edges" lists.
        design_name: Human-readable name for comment headers.

    Returns:
        Dict: {rules, rule_count, exports: {sigma_yaml, splunk_spl, elastic_kql, sentinel_kql},
               volume_estimate, generated_at}
    """
    nodes: list[dict] = graph_data.get("nodes", [])
    generated_at = datetime.now(timezone.utc).isoformat()

    # Collect present source types
    present_source_types: set[str] = {n.get("type", "") for n in nodes}

    # Build set of technique IDs that can be detected with present sources
    covered_techniques: set[str] = set()
    for src_type, techniques in _SOURCE_TECHNIQUE_MAP.items():
        if src_type in present_source_types:
            covered_techniques.update(techniques)

    # Also include techniques from cmp-baseline nodes (manual overrides)
    import json as _json
    for node in nodes:
        if node.get("type") == "cmp-baseline":
            cfg = node.get("config_json", {})
            if isinstance(cfg, str):
                try:
                    cfg = _json.loads(cfg)
                except (ValueError, TypeError):
                    cfg = {}
            for tech in cfg.get("techniques", []):
                if tech.get("covered"):
                    covered_techniques.add(tech.get("id", ""))

    # Build rule objects for covered techniques
    # Index template rules by technique_id for O(1) lookup
    _by_technique: dict[str, tuple] = {row[2]: row for row in _TECHNIQUE_RULES}

    rules: list[dict] = []
    sigma_yaml_parts: list[str] = []
    splunk_spl_parts: list[str] = []
    elastic_kql_parts: list[str] = []
    sentinel_kql_parts: list[str] = []

    for tid in sorted(covered_techniques):
        tpl = _by_technique.get(tid)
        if tpl is None:
            continue

        (
            suffix, title, technique_id, tactic,
            logsource, detection_condition, level,
            splunk_spl, elastic_kql, sentinel_kql,
        ) = tpl

        rule_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"icdev-odc-{suffix}"))
        sigma_yaml_parts.append(
            _sigma_yaml_rule(
                rule_uuid, title, technique_id, tactic,
                logsource, detection_condition, level, generated_at,
            )
        )
        splunk_spl_parts.append(f"## {title} ({technique_id})\n{splunk_spl}\n")
        elastic_kql_parts.append(f"## {title} ({technique_id})\n{elastic_kql}\n")
        sentinel_kql_parts.append(f"// {title} ({technique_id})\n{sentinel_kql}\n")

        rules.append(
            {
                "id": rule_uuid,
                "title": title,
                "technique_id": technique_id,
                "tactic": tactic,
                "level": level,
                "logsource": logsource,
            }
        )

    sigma_header = (
        f"# ICDEV ODC Digital Twin — Sigma Rules\n"
        f"# Design: {design_name}\n"
        f"# Generated: {generated_at}\n"
        f"# Rule count: {len(rules)}\n"
        f"# Classification: CUI // SP-CTI\n\n"
    )
    splunk_header = (
        f"## ICDEV ODC — Splunk SPL Detection Queries\n"
        f"## Design: {design_name} | Generated: {generated_at}\n\n"
    )
    elastic_header = (
        f"## ICDEV ODC — Elastic KQL Detection Queries\n"
        f"## Design: {design_name} | Generated: {generated_at}\n\n"
    )
    sentinel_header = (
        f"// ICDEV ODC — Microsoft Sentinel KQL Detection Queries\n"
        f"// Design: {design_name} | Generated: {generated_at}\n\n"
    )

    sigma_yaml = sigma_header + "\n---\n".join(sigma_yaml_parts)
    splunk_spl_out = splunk_header + "\n".join(splunk_spl_parts)
    elastic_kql_out = elastic_header + "\n".join(elastic_kql_parts)
    sentinel_kql_out = sentinel_header + "\n".join(sentinel_kql_parts)

    volume_estimate = estimate_log_volume(nodes)

    return {
        "rules": rules,
        "rule_count": len(rules),
        "exports": {
            "sigma_yaml": sigma_yaml,
            "splunk_spl": splunk_spl_out,
            "elastic_kql": elastic_kql_out,
            "sentinel_kql": sentinel_kql_out,
        },
        "covered_techniques": sorted(covered_techniques),
        "volume_estimate": volume_estimate,
        "generated_at": generated_at,
    }
