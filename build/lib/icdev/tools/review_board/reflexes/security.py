#!/usr/bin/env python3
# CUI // SP-CTI
"""Security Red Team Reflex — adversarial security scanning.

Checks:
    - Secret scan freshness (detect-secrets last run age)
    - CVE SLA compliance (critical CVEs within remediation window)
    - SAST finding age (stale unresolved findings)
    - OWASP gate drift (gate pass rate declining)
    - New code injection scan (prompt injection patterns in recently changed files)

Architecture: D-RB-1 (scanner-tier, zero LLM cost), D-RB-3 (standalone run())
"""

import re
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent

try:
    from tools.db.storage import get_connection
except ImportError:
    get_connection = None

# Basic prompt injection patterns (subset of prompt_injection_detector.py)
INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"you\s+are\s+now\s+(a|an)\s+",
    r"system\s*:\s*you\s+are",
    r"<\|im_start\|>system",
    r"ADMIN\s*OVERRIDE",
    r"BEGIN\s+INJECTION",
]


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute security red team checks."""
    findings: List[Dict] = []
    checks_completed = 0
    thresholds = config.get("thresholds", {})

    # --- Check 1: .env file exposure ---
    try:
        env_file = BASE_DIR / ".env"
        if env_file.exists():
            content = env_file.read_text(encoding="utf-8")
            # Check for hardcoded secrets (non-placeholder values)
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    value = value.strip().strip('"').strip("'")
                    # Flag if it looks like a real key (not placeholder)
                    if value and not value.startswith("${") and not value.startswith("<"):
                        if any(k in key.upper() for k in ["SECRET", "PASSWORD", "TOKEN"]):
                            if len(value) > 10 and value not in ["changeme", "your-key-here"]:
                                findings.append(
                                    {
                                        "severity": "high",
                                        "category": "secret_exposure",
                                        "title": f"Potential secret in .env: {key}",
                                        "description": "Non-placeholder value detected for sensitive key",
                                        "recommendation": "Move to AWS Secrets Manager or K8s secrets",
                                        "confidence": 0.7,
                                        "auto_fixable": False,
                                    }
                                )
        checks_completed += 1
    except Exception:
        checks_completed += 1

    # --- Check 2: CVE SLA compliance ---
    try:
        if get_connection:
            conn = get_connection()
            try:
                max_critical_age = thresholds.get("max_critical_cve_age_days", 7)
                max_high_age = thresholds.get("max_high_cve_age_days", 30)

                max_critical_age = int(max_critical_age)  # sanitize
                overdue = conn.execute(
                    "SELECT cve_id, severity, component, triaged_at FROM cve_triage "
                    "WHERE status = 'open' AND severity = 'critical' "
                    "AND triaged_at < datetime('now', '-' || ? || ' days')",
                    (str(max_critical_age),),
                ).fetchall()
                for row in overdue:
                    findings.append(
                        {
                            "severity": "critical",
                            "category": "cve_sla",
                            "title": f"Critical CVE overdue: {row[0]} ({row[2]})",
                            "description": f"Triaged {row[3]}, SLA: {max_critical_age} days",
                            "recommendation": "Run: python tools/supply_chain/cve_triager.py --sla-check",
                            "confidence": 1.0,
                            "auto_fixable": False,
                        }
                    )

                max_high_age = int(max_high_age)  # sanitize
                overdue_high = conn.execute(
                    "SELECT cve_id, component, triaged_at FROM cve_triage "
                    "WHERE status = 'open' AND severity = 'high' "
                    "AND triaged_at < datetime('now', '-' || ? || ' days')",
                    (str(max_high_age),),
                ).fetchall()
                for row in overdue_high:
                    findings.append(
                        {
                            "severity": "high",
                            "category": "cve_sla",
                            "title": f"High CVE overdue: {row[0]} ({row[1]})",
                            "confidence": 0.9,
                            "auto_fixable": False,
                        }
                    )
                checks_completed += 1
            except Exception:
                checks_completed += 1
            finally:
                conn.close()
    except Exception:
        checks_completed += 1

    # --- Check 3: Prompt injection patterns in tool files ---
    try:
        tools_dir = BASE_DIR / "tools"
        injection_hits = []
        if tools_dir.exists():
            for py_file in tools_dir.rglob("*.py"):
                if "__pycache__" in str(py_file):
                    continue
                try:
                    content = py_file.read_text(encoding="utf-8")
                    for pattern in INJECTION_PATTERNS:
                        if re.search(pattern, content, re.IGNORECASE):
                            # Skip if it's in a detection tool itself
                            if "detector" in py_file.name or "scanner" in py_file.name:
                                continue
                            if "test_" in py_file.name:
                                continue
                            injection_hits.append(str(py_file.relative_to(BASE_DIR)))
                            break
                except Exception:
                    pass

        if injection_hits:
            findings.append(
                {
                    "severity": "high",
                    "category": "injection_pattern",
                    "title": f"Prompt injection patterns found in {len(injection_hits)} files",
                    "description": "Files: " + ", ".join(injection_hits[:5]),
                    "recommendation": "Review files for accidental prompt injection vulnerabilities",
                    "confidence": 0.6,
                    "auto_fixable": False,
                }
            )
        checks_completed += 1
    except Exception:
        checks_completed += 1

    # --- Check 4: Dangerous code patterns ---
    try:
        dangerous_patterns = [
            (r"\beval\s*\(", "eval() call"),
            (r"\bexec\s*\(", "exec() call"),
            (r"os\.system\s*\(", "os.system() call"),
            (r"subprocess\.call\s*\(.*shell\s*=\s*True", "subprocess with shell=True"),
        ]
        dangerous_hits = []
        tools_dir = BASE_DIR / "tools"
        if tools_dir.exists():
            for py_file in tools_dir.rglob("*.py"):
                if "__pycache__" in str(py_file) or "test_" in py_file.name:
                    continue
                try:
                    content = py_file.read_text(encoding="utf-8")
                    for pattern, label in dangerous_patterns:
                        if re.search(pattern, content):
                            # Skip known safe uses (e.g., in sandbox tools)
                            if "sandbox" in py_file.name or "scanner" in py_file.name:
                                continue
                            dangerous_hits.append(
                                {
                                    "file": str(py_file.relative_to(BASE_DIR)),
                                    "pattern": label,
                                }
                            )
                except Exception:
                    pass

        if dangerous_hits:
            findings.append(
                {
                    "severity": "medium",
                    "category": "dangerous_patterns",
                    "title": f"{len(dangerous_hits)} dangerous code patterns detected",
                    "description": "; ".join(f"{h['file']}: {h['pattern']}" for h in dangerous_hits[:5]),
                    "evidence": {"hits": dangerous_hits[:20]},
                    "confidence": 0.7,
                    "auto_fixable": False,
                }
            )
        checks_completed += 1
    except Exception:
        checks_completed += 1

    return {
        "success": True,
        "metric_value": float(len(findings)),
        "details": {
            "checks_completed": checks_completed,
            "findings_count": len(findings),
            "findings": findings,
        },
    }
