# CUI // SP-CTI
"""Single source of truth for the ODC MITRE ATT&CK technique catalog.

Historically three divergent technique catalogs coexisted in the ODC modules:

  * ``mitre_coverage_twin.MITRE_TECHNIQUE_CATALOG`` — 20 techniques carrying
    ``signal_sources`` + ``min_sources`` (coverage-gap scoring schema).
  * ``observability_canvas.sigma_generator._TECHNIQUE_DETECTIONS`` — 10
    techniques carrying per-logsource-category Sigma detection blocks.
  * ``observability.sigma_generator._DETECTIONS`` — 17 techniques carrying a
    single ``logsource`` + ``detection`` Sigma template each.

The tactics even disagreed (T1078 was ``initial_access`` in the coverage twin
but ``defense-evasion`` in both Sigma generators). This module consolidates the
*union* of all three into ``MITRE_CATALOG`` and exposes deterministic
derivation helpers so each legacy module keeps its exact public contract.

Entry schema (every field except ``name``/``tactics`` is optional)::

    "T1059": {
        "name": "Command and Scripting Interpreter",
        "tactics": ["execution"],          # canonical MITRE tactics, hyphen form,
                                            # PRIMARY first (scalar fallback = tactics[0])
        "signal_sources": [...],            # coverage-twin schema (gap scoring)
        "min_sources": 1,
        "detections": {                     # canvas sigma schema: {category: block}
            "process_creation": {"selection": {...}, "condition": "selection"},
        },
        "sigma_template": {                 # obs sigma schema: single logsource+detection
            "logsource": {"category": "process_creation", "product": "windows"},
            "detection": {"selection": {...}, "condition": "selection"},
        },
    }

Tactic-conflict resolution (validated against the MITRE ATT&CK enterprise
matrix, v14):

  * **T1078 Valid Accounts** legitimately spans Defense Evasion, Persistence,
    Privilege Escalation and Initial Access. The coverage twin's lone
    ``initial_access`` was the outlier; both Sigma generators used
    ``defense-evasion``. Resolved to ``tactics=[defense-evasion, persistence,
    privilege-escalation, initial-access]`` with **defense-evasion** as the
    primary scalar. This preserves the Sigma tag output byte-for-byte and only
    shifts the coverage twin's ``by_tactic`` grouping for T1078.

Deterministic — no LLM.
"""

from __future__ import annotations

# ── Union catalog ──────────────────────────────────────────────────────────────
# Keys are MITRE technique IDs. See module docstring for entry schema.
MITRE_CATALOG: dict[str, dict] = {
    # ── T1059 Command and Scripting Interpreter ───────────────────────────────
    "T1059": {
        "name": "Command and Scripting Interpreter",
        "tactics": ["execution"],
        "signal_sources": ["src-os-log", "src-endpoint", "src-container-log"],
        "min_sources": 1,
        "detections": {
            "process_creation": {
                "selection": {"CommandLine|contains": ["cmd.exe", "powershell", "bash", "sh "]},
                "condition": "selection",
            },
            "application": {
                "selection": {"Message|contains": ["exec(", "eval(", "subprocess"]},
                "condition": "selection",
            },
        },
        "sigma_template": {
            "logsource": {"category": "process_creation", "product": "windows"},
            "detection": {
                "selection": {"CommandLine|contains": ["cmd.exe", "powershell", "bash", "sh "]},
                "condition": "selection",
            },
        },
    },
    # ── T1059.001 PowerShell ──────────────────────────────────────────────────
    "T1059.001": {
        "name": "PowerShell",
        "tactics": ["execution"],
        "sigma_template": {
            "logsource": {"category": "process_creation", "product": "windows"},
            "detection": {
                "selection": {
                    "Image|endswith": "\\powershell.exe",
                    "CommandLine|contains": ["-enc ", "-EncodedCommand", "-nop ", "IEX", "Invoke-Expression"],
                },
                "condition": "selection",
            },
        },
    },
    # ── T1078 Valid Accounts (tactic conflict — see docstring) ────────────────
    "T1078": {
        "name": "Valid Accounts",
        "tactics": ["defense-evasion", "persistence", "privilege-escalation", "initial-access"],
        "signal_sources": ["src-iam", "src-os-log"],
        "min_sources": 1,
        "detections": {
            "authentication": {
                "selection": {"EventID": [4624, 4648], "LogonType": [3, 10]},
                "condition": "selection",
            },
            "cloud": {
                "selection": {"eventName": "ConsoleLogin", "responseElements|contains": "Failure"},
                "condition": "selection",
            },
        },
        "sigma_template": {
            "logsource": {"category": "authentication"},
            "detection": {
                "selection": {"EventID": [4624, 4648], "LogonType": [3, 10]},
                "condition": "selection",
            },
        },
    },
    # ── T1078.004 Valid Accounts: Cloud Accounts ──────────────────────────────
    "T1078.004": {
        "name": "Valid Accounts: Cloud Accounts",
        # Sub-technique of T1078; MITRE lists the same four tactics. Primary
        # kept as privilege-escalation to preserve the coverage-twin grouping
        # (no cross-catalog conflict — only the coverage twin defines it).
        "tactics": ["privilege-escalation", "defense-evasion", "persistence", "initial-access"],
        "signal_sources": ["src-cloud-log", "src-iam"],
        "min_sources": 1,
    },
    # ── T1071 Application Layer Protocol ──────────────────────────────────────
    "T1071": {
        "name": "Application Layer Protocol",
        "tactics": ["command-and-control"],
        "signal_sources": ["src-network-log", "src-app-log", "src-flow"],
        "min_sources": 1,
        "detections": {
            "network_connection": {
                "selection": {"dst_port": [443, 80], "bytes_out|gt": 50000},
                "condition": "selection",
            },
        },
        "sigma_template": {
            "logsource": {"category": "network_connection"},
            "detection": {
                "selection": {"dst_port": [443, 80], "bytes_out|gt": 50000},
                "condition": "selection",
            },
        },
    },
    # ── T1053 Scheduled Task/Job ──────────────────────────────────────────────
    "T1053": {
        "name": "Scheduled Task/Job",
        "tactics": ["persistence", "execution", "privilege-escalation"],
        "signal_sources": ["src-os-log", "src-endpoint"],
        "min_sources": 1,
        "sigma_template": {
            "logsource": {"category": "process_creation", "product": "windows"},
            "detection": {
                "selection": {"CommandLine|contains|any": ["schtasks", "at.exe", "cron"]},
                "condition": "selection",
            },
        },
    },
    # ── T1021 Remote Services ─────────────────────────────────────────────────
    "T1021": {
        "name": "Remote Services",
        "tactics": ["lateral-movement"],
        "signal_sources": ["src-network-log", "src-flow", "src-os-log"],
        "min_sources": 1,
        "detections": {
            "network_connection": {
                "selection": {"dst_port": [22, 3389, 5985, 5986]},
                "filter": {"src_ip|cidr": "10.0.0.0/8"},
                "condition": "selection and not filter",
            },
        },
        "sigma_template": {
            "logsource": {"category": "network_connection"},
            "detection": {
                "selection": {"dst_port": [22, 3389, 5985, 5986]},
                "filter": {"src_ip|cidr": "10.0.0.0/8"},
                "condition": "selection and not filter",
            },
        },
    },
    # ── T1055 Process Injection ───────────────────────────────────────────────
    "T1055": {
        "name": "Process Injection",
        "tactics": ["defense-evasion", "privilege-escalation"],
        "signal_sources": ["src-endpoint", "src-os-log"],
        "min_sources": 1,
        "sigma_template": {
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
    },
    # ── T1003 OS Credential Dumping ───────────────────────────────────────────
    "T1003": {
        "name": "OS Credential Dumping",
        "tactics": ["credential-access"],
        "signal_sources": ["src-endpoint", "src-os-log"],
        "min_sources": 1,
        "detections": {
            "process_creation": {
                "selection": {
                    "CommandLine|contains|any": ["mimikatz", "lsass", "procdump", "sekurlsa"],
                },
                "condition": "selection",
            },
        },
        "sigma_template": {
            "logsource": {"category": "process_creation", "product": "windows"},
            "detection": {
                "selection": {
                    "CommandLine|contains|any": ["mimikatz", "lsass", "procdump", "sekurlsa"],
                },
                "condition": "selection",
            },
        },
    },
    # ── T1110 Brute Force ─────────────────────────────────────────────────────
    "T1110": {
        "name": "Brute Force",
        "tactics": ["credential-access"],
        "signal_sources": ["src-iam", "src-os-log", "src-network-log"],
        "min_sources": 1,
        "detections": {
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
        "sigma_template": {
            "logsource": {"category": "authentication"},
            "detection": {
                "selection": {"EventID": 4625},
                "condition": "selection | count() > 10",
                "timeframe": "5m",
            },
        },
    },
    # ── T1486 Data Encrypted for Impact ───────────────────────────────────────
    "T1486": {
        "name": "Data Encrypted for Impact",
        "tactics": ["impact"],
        "signal_sources": ["src-endpoint", "src-os-log"],
        "min_sources": 1,
        "sigma_template": {
            "logsource": {"category": "process_creation"},
            "detection": {
                "selection": {
                    "CommandLine|contains|any": ["vssadmin delete", "bcdedit /set", "wbadmin delete"],
                },
                "condition": "selection",
            },
        },
    },
    # ── T1562 Impair Defenses ─────────────────────────────────────────────────
    "T1562": {
        "name": "Impair Defenses",
        "tactics": ["defense-evasion"],
        "signal_sources": ["src-os-log", "src-endpoint", "src-cloud-log"],
        "min_sources": 1,
        "sigma_template": {
            "logsource": {"category": "process_creation", "product": "windows"},
            "detection": {
                "selection": {
                    "CommandLine|contains|any": [
                        "Set-MpPreference -DisableRealtimeMonitoring",
                        "netsh advfirewall set",
                        "auditpol /set",
                    ],
                },
                "condition": "selection",
            },
        },
    },
    # ── T1136 Create Account ──────────────────────────────────────────────────
    "T1136": {
        "name": "Create Account",
        "tactics": ["persistence"],
        "signal_sources": ["src-os-log", "src-iam"],
        "min_sources": 1,
    },
    # ── T1611 Escape to Host ──────────────────────────────────────────────────
    "T1611": {
        "name": "Escape to Host",
        "tactics": ["privilege-escalation"],
        "signal_sources": ["src-container-log", "src-os-log"],
        "min_sources": 1,
    },
    # ── T1190 Exploit Public-Facing Application ───────────────────────────────
    "T1190": {
        "name": "Exploit Public-Facing Application",
        "tactics": ["initial-access"],
        "signal_sources": ["src-app-log", "src-network-log", "src-waf"],
        "min_sources": 1,
        "detections": {
            "network_connection": {
                "selection": {"dst_port": [80, 443, 8080, 8443], "bytes_in|gt": 100000},
                "condition": "selection",
            },
            "application": {
                "selection": {"Message|contains|any": ["SQL syntax", "UNION SELECT", "../", "%2e%2e"]},
                "condition": "selection",
            },
        },
        "sigma_template": {
            "logsource": {"category": "application"},
            "detection": {
                "selection": {"Message|contains|any": ["SQL syntax", "UNION SELECT", "../", "%2e%2e"]},
                "condition": "selection",
            },
        },
    },
    # ── T1505 Server Software Component ───────────────────────────────────────
    "T1505": {
        "name": "Server Software Component",
        "tactics": ["persistence"],
        "signal_sources": ["src-app-log", "src-os-log"],
        "min_sources": 1,
    },
    # ── T1048 Exfiltration Over Alternative Protocol ──────────────────────────
    "T1048": {
        "name": "Exfiltration Over Alternative Protocol",
        "tactics": ["exfiltration"],
        "signal_sources": ["src-network-log", "src-flow", "src-pcap"],
        "min_sources": 1,
    },
    # ── T1074 Data Staged ─────────────────────────────────────────────────────
    "T1074": {
        "name": "Data Staged",
        "tactics": ["collection"],
        "signal_sources": ["src-endpoint", "src-os-log", "src-db-audit"],
        "min_sources": 1,
    },
    # ── T1070 Indicator Removal ───────────────────────────────────────────────
    "T1070": {
        "name": "Indicator Removal",
        "tactics": ["defense-evasion"],
        "signal_sources": ["src-os-log", "src-endpoint"],
        "min_sources": 1,
    },
    # ── T1204 User Execution ──────────────────────────────────────────────────
    "T1204": {
        "name": "User Execution",
        "tactics": ["execution"],
        "signal_sources": ["src-endpoint", "src-os-log", "src-app-log"],
        "min_sources": 1,
    },
    # ── T1566 Phishing ────────────────────────────────────────────────────────
    "T1566": {
        "name": "Phishing",
        "tactics": ["initial-access"],
        "signal_sources": ["src-app-log", "src-network-log", "src-iam"],
        "min_sources": 1,
        "detections": {
            "application": {
                "selection": {"Message|contains": ["attachment", ".exe", "macro", "phishing"]},
                "condition": "selection",
            },
            "authentication": {
                "selection": {"EventID": 4625, "Status": "0xC000006D"},
                "condition": "selection",
            },
        },
        "sigma_template": {
            "logsource": {"category": "application"},
            "detection": {
                "selection": {"Message|contains": ["attachment", ".exe", "macro", "phishing"]},
                "condition": "selection",
            },
        },
    },
    # ── T1083 File and Directory Discovery ────────────────────────────────────
    "T1083": {
        "name": "File and Directory Discovery",
        "tactics": ["discovery"],
        "detections": {
            "process_creation": {
                "selection": {"CommandLine|contains|any": ["dir ", "ls -", "find /"]},
                "condition": "selection",
            },
        },
        "sigma_template": {
            "logsource": {"category": "process_creation"},
            "detection": {
                "selection": {"CommandLine|contains|any": ["dir ", "ls -", "find /"]},
                "condition": "selection",
            },
        },
    },
    # ── T1082 System Information Discovery ────────────────────────────────────
    "T1082": {
        "name": "System Information Discovery",
        "tactics": ["discovery"],
        "detections": {
            "process_creation": {
                "selection": {"CommandLine|contains|any": ["systeminfo", "uname -a", "hostname"]},
                "condition": "selection",
            },
        },
        "sigma_template": {
            "logsource": {"category": "process_creation"},
            "detection": {
                "selection": {"CommandLine|contains|any": ["systeminfo", "uname -a", "hostname"]},
                "condition": "selection",
            },
        },
    },
    # ── T1218 System Binary Proxy Execution ───────────────────────────────────
    "T1218": {
        "name": "System Binary Proxy Execution",
        "tactics": ["defense-evasion"],
        "sigma_template": {
            "logsource": {"category": "process_creation", "product": "windows"},
            "detection": {
                "selection": {
                    "Image|endswith|any": [
                        "\\mshta.exe",
                        "\\regsvr32.exe",
                        "\\rundll32.exe",
                        "\\certutil.exe",
                    ],
                },
                "condition": "selection",
            },
        },
    },
    # ── T1027 Obfuscated Files or Information ──────────────────────────────────
    "T1027": {
        "name": "Obfuscated Files or Information",
        "tactics": ["defense-evasion"],
        "sigma_template": {
            "logsource": {"category": "process_creation", "product": "windows"},
            "detection": {
                "selection": {
                    "CommandLine|contains|any": ["-enc ", "base64", "frombase64string", "char("],
                },
                "condition": "selection",
            },
        },
    },
    # ── Tactic-only fallback entries ──────────────────────────────────────────
    # These have no detection/signal-source data; they exist so the Sigma
    # tactic-lookup maps stay identical to the pre-consolidation behaviour.
    "T1046": {"name": "Network Service Discovery", "tactics": ["discovery"]},
    "T1098": {"name": "Account Manipulation", "tactics": ["persistence"]},
    "T1547": {"name": "Boot or Logon Autostart Execution", "tactics": ["persistence"]},
    "T1133": {"name": "External Remote Services", "tactics": ["initial-access"]},
    "T1041": {"name": "Exfiltration Over C2 Channel", "tactics": ["exfiltration"]},
}

# Default tactic for any technique absent from the catalog (matches the legacy
# Sigma generators' fallback).
DEFAULT_TACTIC = "defense-evasion"


# ── Derivation helpers ─────────────────────────────────────────────────────────


def primary_tactic(technique_id: str) -> str:
    """Return the primary (scalar) tactic for a technique, hyphen form.

    Falls back to the parent technique, then to :data:`DEFAULT_TACTIC`.
    """
    entry = MITRE_CATALOG.get(technique_id)
    if entry is None:
        entry = MITRE_CATALOG.get(technique_id.split(".")[0])
    if entry and entry.get("tactics"):
        return entry["tactics"][0]
    return DEFAULT_TACTIC


def tactics_for(technique_id: str) -> list[str]:
    """Return the full ordered tactics list (primary first) for a technique."""
    entry = MITRE_CATALOG.get(technique_id) or MITRE_CATALOG.get(technique_id.split(".")[0])
    if entry and entry.get("tactics"):
        return list(entry["tactics"])
    return [DEFAULT_TACTIC]


def build_coverage_catalog() -> dict[str, dict]:
    """Derive the coverage-twin view: techniques that carry signal sources.

    Reproduces the legacy ``MITRE_TECHNIQUE_CATALOG`` shape
    ``{tid: {name, tactic, signal_sources, min_sources}}``. The ``tactic`` is
    the primary tactic rendered in the coverage twin's underscore vocabulary.
    """
    view: dict[str, dict] = {}
    for tid, entry in MITRE_CATALOG.items():
        if "signal_sources" not in entry:
            continue
        view[tid] = {
            "name": entry["name"],
            "tactic": entry["tactics"][0].replace("-", "_"),
            "signal_sources": list(entry["signal_sources"]),
            "min_sources": entry.get("min_sources", 1),
        }
    return view


def build_canvas_detections() -> dict[str, dict]:
    """Derive the canvas-sigma view: ``{tid: {category: detection_block}}``."""
    return {
        tid: {cat: dict(block) for cat, block in entry["detections"].items()}
        for tid, entry in MITRE_CATALOG.items()
        if "detections" in entry
    }


def build_obs_detections() -> dict[str, dict]:
    """Derive the obs-sigma view: ``{tid: {name, tactic, logsource, detection}}``.

    ``tactic`` is the primary tactic in hyphen (MITRE ATT&CK tag) form.
    """
    view: dict[str, dict] = {}
    for tid, entry in MITRE_CATALOG.items():
        tmpl = entry.get("sigma_template")
        if not tmpl:
            continue
        view[tid] = {
            "name": entry["name"],
            "tactic": entry["tactics"][0],
            "logsource": dict(tmpl["logsource"]),
            "detection": dict(tmpl["detection"]),
        }
    return view


def build_tactic_map() -> dict[str, str]:
    """Derive ``{tid: primary_tactic}`` (hyphen form) for every catalog entry."""
    return {tid: entry["tactics"][0] for tid, entry in MITRE_CATALOG.items() if entry.get("tactics")}
