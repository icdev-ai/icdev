# CUI // SP-CTI
"""Standalone Sigma rule generator for MITRE ATT&CK techniques (ODC pipeline).

Deterministic — no LLM. Produces well-formed Sigma YAML from a technique ID.
Complements tools/observability_canvas/sigma_generator.py (graph-centric);
this module exposes a single-technique API for the /api/sigma/generate endpoint
and for seeding sigma_template in odc_mitre_techniques.

Public API:
  generate_sigma(technique_id: str) -> str
      Returns Sigma YAML string with title: and detection: keys.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import yaml

# ---------------------------------------------------------------------------
# Technique detection templates: (logsource, detection, tactic, name)
# ---------------------------------------------------------------------------
_DETECTIONS: dict[str, dict] = {
    "T1055": {
        "name": "Process Injection",
        "tactic": "defense-evasion",
        "logsource": {"category": "process_access", "product": "windows"},
        "detection": {
            "selection": {
                "TargetImage|endswith": [
                    "\\lsass.exe",
                    "\\svchost.exe",
                    "\\explorer.exe",
                    "\\winlogon.exe",
                ],
                "GrantedAccess|contains": ["0x1010", "0x1410", "0x1438", "0x143a"],
            },
            "condition": "selection",
        },
    },
    "T1059": {
        "name": "Command and Scripting Interpreter",
        "tactic": "execution",
        "logsource": {"category": "process_creation", "product": "windows"},
        "detection": {
            "selection": {
                "CommandLine|contains": ["cmd.exe", "powershell", "bash", "sh "]
            },
            "condition": "selection",
        },
    },
    "T1059.001": {
        "name": "PowerShell",
        "tactic": "execution",
        "logsource": {"category": "process_creation", "product": "windows"},
        "detection": {
            "selection": {
                "Image|endswith": "\\powershell.exe",
                "CommandLine|contains": ["-enc ", "-EncodedCommand", "-nop ", "IEX", "Invoke-Expression"],
            },
            "condition": "selection",
        },
    },
    "T1078": {
        "name": "Valid Accounts",
        "tactic": "defense-evasion",
        "logsource": {"category": "authentication"},
        "detection": {
            "selection": {"EventID": [4624, 4648], "LogonType": [3, 10]},
            "condition": "selection",
        },
    },
    "T1003": {
        "name": "OS Credential Dumping",
        "tactic": "credential-access",
        "logsource": {"category": "process_creation", "product": "windows"},
        "detection": {
            "selection": {
                "CommandLine|contains|any": ["mimikatz", "lsass", "procdump", "sekurlsa"]
            },
            "condition": "selection",
        },
    },
    "T1110": {
        "name": "Brute Force",
        "tactic": "credential-access",
        "logsource": {"category": "authentication"},
        "detection": {
            "selection": {"EventID": 4625},
            "condition": "selection | count() > 10",
            "timeframe": "5m",
        },
    },
    "T1566": {
        "name": "Phishing",
        "tactic": "initial-access",
        "logsource": {"category": "application"},
        "detection": {
            "selection": {
                "Message|contains": ["attachment", ".exe", "macro", "phishing"]
            },
            "condition": "selection",
        },
    },
    "T1083": {
        "name": "File and Directory Discovery",
        "tactic": "discovery",
        "logsource": {"category": "process_creation"},
        "detection": {
            "selection": {
                "CommandLine|contains|any": ["dir ", "ls -", "find /"]
            },
            "condition": "selection",
        },
    },
    "T1082": {
        "name": "System Information Discovery",
        "tactic": "discovery",
        "logsource": {"category": "process_creation"},
        "detection": {
            "selection": {
                "CommandLine|contains|any": ["systeminfo", "uname -a", "hostname"]
            },
            "condition": "selection",
        },
    },
    "T1021": {
        "name": "Remote Services",
        "tactic": "lateral-movement",
        "logsource": {"category": "network_connection"},
        "detection": {
            "selection": {"dst_port": [22, 3389, 5985, 5986]},
            "filter": {"src_ip|cidr": "10.0.0.0/8"},
            "condition": "selection and not filter",
        },
    },
    "T1053": {
        "name": "Scheduled Task/Job",
        "tactic": "persistence",
        "logsource": {"category": "process_creation", "product": "windows"},
        "detection": {
            "selection": {
                "CommandLine|contains|any": ["schtasks", "at.exe", "cron"]
            },
            "condition": "selection",
        },
    },
    "T1071": {
        "name": "Application Layer Protocol",
        "tactic": "command-and-control",
        "logsource": {"category": "network_connection"},
        "detection": {
            "selection": {"dst_port": [443, 80], "bytes_out|gt": 50000},
            "condition": "selection",
        },
    },
    "T1190": {
        "name": "Exploit Public-Facing Application",
        "tactic": "initial-access",
        "logsource": {"category": "application"},
        "detection": {
            "selection": {
                "Message|contains|any": ["SQL syntax", "UNION SELECT", "../", "%2e%2e"]
            },
            "condition": "selection",
        },
    },
    "T1486": {
        "name": "Data Encrypted for Impact",
        "tactic": "impact",
        "logsource": {"category": "process_creation"},
        "detection": {
            "selection": {
                "CommandLine|contains|any": ["vssadmin delete", "bcdedit /set", "wbadmin delete"]
            },
            "condition": "selection",
        },
    },
    "T1562": {
        "name": "Impair Defenses",
        "tactic": "defense-evasion",
        "logsource": {"category": "process_creation", "product": "windows"},
        "detection": {
            "selection": {
                "CommandLine|contains|any": [
                    "Set-MpPreference -DisableRealtimeMonitoring",
                    "netsh advfirewall set",
                    "auditpol /set",
                ]
            },
            "condition": "selection",
        },
    },
    "T1218": {
        "name": "System Binary Proxy Execution",
        "tactic": "defense-evasion",
        "logsource": {"category": "process_creation", "product": "windows"},
        "detection": {
            "selection": {
                "Image|endswith|any": [
                    "\\mshta.exe",
                    "\\regsvr32.exe",
                    "\\rundll32.exe",
                    "\\certutil.exe",
                ]
            },
            "condition": "selection",
        },
    },
    "T1027": {
        "name": "Obfuscated Files or Information",
        "tactic": "defense-evasion",
        "logsource": {"category": "process_creation", "product": "windows"},
        "detection": {
            "selection": {
                "CommandLine|contains|any": ["-enc ", "base64", "frombase64string", "char("]
            },
            "condition": "selection",
        },
    },
}

# Tactic map for techniques not in _DETECTIONS
_TACTIC_MAP: dict[str, str] = {
    "T1059": "execution",
    "T1566": "initial-access",
    "T1078": "defense-evasion",
    "T1003": "credential-access",
    "T1110": "credential-access",
    "T1083": "discovery",
    "T1082": "discovery",
    "T1046": "discovery",
    "T1021": "lateral-movement",
    "T1055": "defense-evasion",
    "T1218": "defense-evasion",
    "T1027": "defense-evasion",
    "T1053": "persistence",
    "T1098": "persistence",
    "T1547": "persistence",
    "T1190": "initial-access",
    "T1133": "initial-access",
    "T1071": "command-and-control",
    "T1041": "exfiltration",
    "T1048": "exfiltration",
    "T1486": "impact",
    "T1562": "defense-evasion",
}


def generate_sigma(technique_id: str) -> str:
    """Generate a Sigma YAML detection rule for a MITRE ATT&CK technique.

    Args:
        technique_id: MITRE ATT&CK technique ID (e.g. 'T1055', 'T1059.001').

    Returns:
        Sigma YAML string containing at minimum title: and detection: keys.

    Raises:
        ValueError: if technique_id is empty or does not start with 'T'.
    """
    tid = technique_id.strip().upper()
    if not tid or not tid.startswith("T"):
        raise ValueError(f"Invalid technique_id: {technique_id!r}")

    parent_tid = tid.split(".")[0]
    tmpl = _DETECTIONS.get(tid) or _DETECTIONS.get(parent_tid)

    if tmpl:
        logsource = dict(tmpl["logsource"])
        detection = dict(tmpl["detection"])
        tactic = tmpl["tactic"]
        name = tmpl["name"]
    else:
        logsource = {"category": "process_creation"}
        detection = {
            "selection": {"Keywords|contains": [tid, tid.replace(".", "/")]},
            "condition": "selection",
        }
        tactic = _TACTIC_MAP.get(tid) or _TACTIC_MAP.get(parent_tid, "defense-evasion")
        name = tid

    rule = {
        "title": f"Detect MITRE ATT&CK {tid} — {name}",
        "id": str(uuid.uuid4()),
        "status": "experimental",
        "description": (
            f"Detects {tid} ({name}) activity. "
            "Generated by ICDEV ODC pipeline."
        ),
        "references": [f"https://attack.mitre.org/techniques/{tid.replace('.', '/')}/"],
        "author": "ICDEV ODC pipeline",
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "tags": [
            f"attack.{tid.lower().replace('.', '_')}",
            f"attack.{tactic}",
        ],
        "logsource": logsource,
        "detection": detection,
        "falsepositives": ["Legitimate administrative activity"],
        "level": "medium",
    }

    return yaml.dump(rule, default_flow_style=False, sort_keys=False, allow_unicode=True)
