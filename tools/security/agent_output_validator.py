#!/usr/bin/env python3
# CUI // SP-CTI
"""Agent Output Validator — post-tool output content safety checker (D259).

Validates agent outputs for classification leaks, sensitive data (SSN,
credentials, private keys), size limits, and injection patterns.

Pattern: tools/security/prompt_injection_detector.py (regex patterns, severity, CLI with --gate)
ADRs: D259 (output safety via regex), D6 (append-only violation log)

CLI:
    python tools/security/agent_output_validator.py --text "some output" --json
    python tools/security/agent_output_validator.py --file /path/to/output.txt --json
    python tools/security/agent_output_validator.py --gate --project-id proj-123 --json
"""

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
CONFIG_PATH = BASE_DIR / "args" / "owasp_agentic_config.yaml"


def _load_config() -> Dict:
    """Load output validation config from YAML."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("output_validation", {})
    return {}


class AgentOutputValidator:
    """Post-tool output content safety checker (D259).

    Validates agent output text against classification patterns,
    sensitive data detectors, and size limits. Logs violations to
    agent_output_violations (append-only, D6).
    """

    def __init__(self, db_path: Optional[Path] = None, config: Optional[Dict] = None):
        self._db_path = db_path or DB_PATH
        self._config = config or _load_config()
        self._max_size = self._config.get("max_output_size_bytes", 1048576)
        self._patterns = self._config.get("classification_patterns", [])

    def validate_output(
        self,
        output_text: str,
        agent_id: str = "unknown",
        tool_name: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> Dict:
        """Validate output text for safety violations.

        Returns:
            Dict with violations list, passed bool, action (allow/warn/flag/block).
        """
        violations: List[Dict] = []
        action = "allow"

        # Check size limit
        if len(output_text.encode("utf-8")) > self._max_size:
            violations.append({
                "type": "oversized_response",
                "severity": "high",
                "action": "flag",
                "description": f"Output size ({len(output_text.encode('utf-8')):,} bytes) exceeds limit ({self._max_size:,} bytes)",
            })

        # Check classification patterns from config
        for pat_def in self._patterns:
            pattern = pat_def.get("pattern", "")
            if not pattern:
                continue
            try:
                matches = re.findall(pattern, output_text)
                if matches:
                    violations.append({
                        "type": "classification_pattern",
                        "severity": pat_def.get("severity", "medium"),
                        "action": pat_def.get("action", "flag"),
                        "description": pat_def.get("description", "Pattern matched"),
                        "pattern_name": pattern[:50],
                        "match_count": len(matches),
                    })
            except re.error:
                pass

        # Determine overall action (worst action wins)
        action_priority = {"allow": 0, "warn": 1, "flag": 2, "block": 3}
        for v in violations:
            v_action = v.get("action", "flag")
            if action_priority.get(v_action, 0) > action_priority.get(action, 0):
                action = v_action

        # Compute output hash
        output_hash = hashlib.sha256(output_text.encode("utf-8")).hexdigest()

        # Log violations to DB
        for v in violations:
            self._log_violation(
                agent_id=agent_id,
                tool_name=tool_name,
                violation_type=v["type"],
                severity=v["severity"],
                details=v,
                output_hash=output_hash,
                action_taken=v.get("action", "logged"),
                project_id=project_id,
            )

        return {
            "passed": len(violations) == 0,
            "action": action,
            "violation_count": len(violations),
            "violations": violations,
            "output_hash": output_hash,
            "output_size_bytes": len(output_text.encode("utf-8")),
        }

    def check_classification(self, text: str) -> List[Dict]:
        """Check for classification markings above CUI."""
        findings = []
        above_cui = [
            (r"(?i)\b(SECRET)\b(?!\s*Manager)", "SECRET marking detected"),
            (r"(?i)\b(TOP\s+SECRET)\b", "TOP SECRET marking detected"),
            (r"(?i)\b(TS//SCI)\b", "TS//SCI marking detected"),
            (r"(?i)\b(NOFORN)\b", "NOFORN marking detected"),
            (r"(?i)\b(ORCON)\b", "ORCON marking detected"),
        ]
        for pattern, desc in above_cui:
            if re.search(pattern, text):
                findings.append({
                    "type": "classification_leak",
                    "severity": "critical",
                    "action": "block",
                    "description": desc,
                })
        return findings

    def check_sensitive_data(self, text: str) -> List[Dict]:
        """Check for sensitive data patterns (SSN, keys, credentials)."""
        findings = []
        sensitive_patterns = [
            (r"\b\d{3}-\d{2}-\d{4}\b", "Potential SSN", "high"),
            (r"(?i)(password|api_key|secret_key|private_key)\s*[=:]\s*['\"][^'\"]{8,}", "Credential value", "critical"),
            (r"(?i)(BEGIN RSA PRIVATE KEY|BEGIN EC PRIVATE KEY|BEGIN OPENSSH PRIVATE KEY)", "Private key material", "critical"),
            (r"(?i)(AKIA[0-9A-Z]{16})", "AWS Access Key ID", "critical"),
            (r"(?i)(ghp_[A-Za-z0-9_]{36})", "GitHub Personal Access Token", "critical"),
        ]
        for pattern, desc, severity in sensitive_patterns:
            if re.search(pattern, text):
                findings.append({
                    "type": "sensitive_data",
                    "severity": severity,
                    "action": "block" if severity == "critical" else "flag",
                    "description": desc,
                })
        return findings

    def _log_violation(
        self,
        agent_id: str,
        violation_type: str,
        severity: str,
        details: Dict,
        output_hash: str,
        action_taken: str,
        tool_name: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> Optional[str]:
        """Log a violation to agent_output_violations (append-only, D6)."""
        if not self._db_path.exists():
            return None

        entry_id = str(uuid.uuid4())
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute(
                """INSERT INTO agent_output_violations
                   (id, project_id, agent_id, tool_name, violation_type,
                    severity, details_json, output_hash, action_taken,
                    classification, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'CUI', ?)""",
                (
                    entry_id, project_id, agent_id, tool_name,
                    violation_type, severity, json.dumps(details),
                    output_hash, action_taken,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
            conn.close()
            return entry_id
        except Exception:
            return None

    def evaluate_gate(self, project_id: Optional[str] = None) -> Dict:
        """Evaluate security gate: check for unresolved critical output violations.

        Returns:
            Dict with pass/fail, blocking conditions, and violation counts.
        """
        result = {
            "gate": "owasp_agentic_output_safety",
            "passed": True,
            "blocking": [],
            "warnings": [],
            "total_violations": 0,
            "critical_violations": 0,
        }

        if not self._db_path.exists():
            result["warnings"].append("Database not found — cannot evaluate gate")
            return result

        try:
            conn = sqlite3.connect(str(self._db_path))

            where = "1=1"
            params: list = []
            if project_id:
                where = "project_id = ?"
                params = [project_id]

            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

            for severity in ("critical", "high", "medium"):
                row = conn.execute(
                    f"SELECT COUNT(*) FROM agent_output_violations "
                    f"WHERE {where} AND severity = ? AND created_at >= ?",
                    params + [severity, cutoff],
                ).fetchone()
                count = row[0] if row else 0
                result["total_violations"] += count

                if severity == "critical":
                    result["critical_violations"] = count
                    if count > 0:
                        result["passed"] = False
                        result["blocking"].append(
                            f"{count} critical output violation(s) in last 24h (classification leak or credential exposure)"
                        )
                elif severity == "high":
                    if count > 5:
                        result["warnings"].append(
                            f"{count} high-severity output violations in last 24h"
                        )

            conn.close()
        except Exception as e:
            result["warnings"].append(f"Gate evaluation error: {str(e)}")

        return result


def main():
    parser = argparse.ArgumentParser(
        description="Agent Output Validator — content safety checker (D259)"
    )
    parser.add_argument("--text", help="Text to validate")
    parser.add_argument("--file", help="File to validate")
    parser.add_argument("--gate", action="store_true", help="Evaluate security gate")
    parser.add_argument("--agent-id", default="cli", help="Agent ID (default: cli)")
    parser.add_argument("--tool-name", help="Tool name that produced the output")
    parser.add_argument("--project-id", help="Project ID")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    validator = AgentOutputValidator()

    if args.text:
        result = validator.validate_output(
            output_text=args.text,
            agent_id=args.agent_id,
            tool_name=args.tool_name,
            project_id=args.project_id,
        )
    elif args.file:
        try:
            text = Path(args.file).read_text(encoding="utf-8")
        except Exception as e:
            print(f"Error reading file: {e}", file=sys.stderr)
            sys.exit(1)
        result = validator.validate_output(
            output_text=text,
            agent_id=args.agent_id,
            tool_name=args.tool_name,
            project_id=args.project_id,
        )
    elif args.gate:
        result = validator.evaluate_gate(project_id=args.project_id)
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if args.gate:
            status = "PASS" if result["passed"] else "FAIL"
            print(f"Output Safety Gate: {status}")
            for b in result.get("blocking", []):
                print(f"  [BLOCK] {b}")
            for w in result.get("warnings", []):
                print(f"  [WARN] {w}")
        else:
            status = "PASS" if result["passed"] else "FAIL"
            print(f"Output Validation: {status} (action={result['action']})")
            print(f"  Size: {result['output_size_bytes']:,} bytes")
            print(f"  Hash: {result['output_hash'][:16]}...")
            for v in result.get("violations", []):
                print(f"  [{v['severity']}] {v['type']}: {v['description']}")


if __name__ == "__main__":
    main()
