#!/usr/bin/env python3
# CUI // SP-CTI
"""Tool Chain Validator — detect suspicious agent tool call sequences (D258).

Validates agent tool call sequences against YAML-defined rules using a
sliding window approach. Detects multi-step attack patterns like
secrets-then-external, privilege-escalation chains, and rapid tool bursts.

Pattern: tools/security/prompt_injection_detector.py (regex patterns, severity, CLI with --gate)
ADRs: D258 (tool chain rules in YAML), D6 (append-only event log)

CLI:
    python tools/security/tool_chain_validator.py --check --agent-id agent-1 --session-id sess-1 --json
    python tools/security/tool_chain_validator.py --rules --json
    python tools/security/tool_chain_validator.py --gate --project-id proj-123 --json
"""

import argparse
import json
import sqlite3
import uuid
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from fnmatch import fnmatch
from pathlib import Path
from typing import Dict, List, Optional

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
CONFIG_PATH = BASE_DIR / "args" / "owasp_agentic_config.yaml"


def _load_config() -> Dict:
    """Load tool chain config from YAML."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("tool_chain", {})
    return {}


class ToolChainValidator:
    """Sliding-window tool-call-sequence validator (D258).

    Maintains per-agent/session sliding windows and checks tool call
    sequences against configured rules. Logs violations to
    tool_chain_events (append-only, D6).
    """

    def __init__(self, db_path: Optional[Path] = None, config: Optional[Dict] = None):
        self._db_path = db_path or DB_PATH
        self._config = config or _load_config()
        self._window_size = self._config.get("window_size", 10)
        self._rules = self._config.get("rules", [])
        # In-memory sliding windows keyed by (agent_id, session_id)
        self._windows: Dict[str, List[Dict]] = defaultdict(list)

    def record_tool_call(
        self,
        agent_id: str,
        session_id: str,
        tool_name: str,
        project_id: Optional[str] = None,
    ) -> List[Dict]:
        """Record a tool call and check against all rules.

        Returns:
            List of violation dicts (empty if no violations).
        """
        key = f"{agent_id}:{session_id}"
        now = datetime.now(timezone.utc)

        self._windows[key].append({
            "tool_name": tool_name,
            "timestamp": now.isoformat(),
        })

        # Trim to window size
        if len(self._windows[key]) > self._window_size:
            self._windows[key] = self._windows[key][-self._window_size:]

        violations = []

        # Check sequence rules
        for rule in self._rules:
            if "sequence_pattern" in rule:
                v = self._check_sequence(key, rule, agent_id, session_id, project_id)
                if v:
                    violations.append(v)
            elif "burst_threshold" in rule:
                v = self._check_burst(key, rule, agent_id, session_id, project_id)
                if v:
                    violations.append(v)

        return violations

    def _check_sequence(
        self, key: str, rule: Dict, agent_id: str, session_id: str,
        project_id: Optional[str],
    ) -> Optional[Dict]:
        """Check if the sliding window matches a sequence rule."""
        patterns = rule.get("sequence_pattern", [])
        if len(patterns) < 2:
            return None

        max_gap = rule.get("max_gap", 5)
        window = self._windows[key]
        tool_names = [e["tool_name"] for e in window]

        # Search for first pattern match, then second within max_gap
        for i, name in enumerate(tool_names):
            if self._matches_pattern_group(name, patterns[0]):
                # Look for second pattern within max_gap steps
                for j in range(i + 1, min(i + 1 + max_gap, len(tool_names))):
                    if self._matches_pattern_group(tool_names[j], patterns[1]):
                        violation = {
                            "rule_id": rule.get("id", "unknown"),
                            "rule_name": rule.get("name", "unknown"),
                            "description": rule.get("description", ""),
                            "severity": rule.get("severity", "medium"),
                            "action": rule.get("action", "flag"),
                            "matched_tools": [tool_names[i], tool_names[j]],
                            "gap": j - i,
                            "agent_id": agent_id,
                            "session_id": session_id,
                        }
                        self._log_event(
                            agent_id=agent_id,
                            session_id=session_id,
                            tool_name=tool_names[j],
                            tool_sequence=tool_names[max(0, i):j + 1],
                            rule_matched=rule.get("id"),
                            severity=rule.get("severity", "medium"),
                            action=rule.get("action", "flag"),
                            project_id=project_id,
                        )
                        return violation
        return None

    def _check_burst(
        self, key: str, rule: Dict, agent_id: str, session_id: str,
        project_id: Optional[str],
    ) -> Optional[Dict]:
        """Check if tool call rate exceeds burst threshold."""
        threshold = rule.get("burst_threshold", 20)
        window_seconds = rule.get("burst_window_seconds", 60)
        window = self._windows[key]

        if len(window) < 2:
            return None

        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(seconds=window_seconds)).isoformat()
        recent = [e for e in window if e["timestamp"] >= cutoff]

        if len(recent) >= threshold:
            violation = {
                "rule_id": rule.get("id", "unknown"),
                "rule_name": rule.get("name", "rapid_tool_burst"),
                "description": rule.get("description", ""),
                "severity": rule.get("severity", "medium"),
                "action": rule.get("action", "warn"),
                "call_count": len(recent),
                "threshold": threshold,
                "window_seconds": window_seconds,
                "agent_id": agent_id,
                "session_id": session_id,
            }
            self._log_event(
                agent_id=agent_id,
                session_id=session_id,
                tool_name="burst_detection",
                tool_sequence=[e["tool_name"] for e in recent],
                rule_matched=rule.get("id"),
                severity=rule.get("severity", "medium"),
                action=rule.get("action", "warn"),
                project_id=project_id,
            )
            return violation
        return None

    @staticmethod
    def _matches_pattern_group(tool_name: str, pattern_group: str) -> bool:
        """Check if tool_name matches any pattern in a pipe-separated group.

        Pattern format: "*secret*|*credential*|*key_vault*"
        Uses fnmatch for glob-style matching.
        """
        for pattern in pattern_group.split("|"):
            pattern = pattern.strip()
            if fnmatch(tool_name.lower(), pattern.lower()):
                return True
        return False

    def _log_event(
        self,
        agent_id: str,
        session_id: str,
        tool_name: str,
        tool_sequence: List[str],
        rule_matched: Optional[str],
        severity: str,
        action: str,
        project_id: Optional[str] = None,
        context: Optional[Dict] = None,
    ) -> Optional[str]:
        """Log a tool chain event (append-only, D6)."""
        if not self._db_path.exists():
            return None

        entry_id = str(uuid.uuid4())
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute(
                """INSERT INTO tool_chain_events
                   (id, project_id, agent_id, session_id, tool_name,
                    tool_sequence_json, rule_matched, severity, action,
                    context_json, classification, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CUI', ?)""",
                (
                    entry_id, project_id, agent_id, session_id, tool_name,
                    json.dumps(tool_sequence), rule_matched, severity, action,
                    json.dumps(context) if context else None,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
            conn.close()
            return entry_id
        except Exception:
            return None

    def check_session(self, agent_id: str, session_id: str) -> List[Dict]:
        """Return current window state for an agent/session."""
        key = f"{agent_id}:{session_id}"
        return list(self._windows.get(key, []))

    def get_rules(self) -> List[Dict]:
        """Return configured tool chain rules."""
        return list(self._rules)

    def evaluate_gate(self, project_id: Optional[str] = None) -> Dict:
        """Evaluate security gate: check for unresolved critical violations.

        Returns:
            Dict with pass/fail, blocking conditions, and violation counts.
        """
        result = {
            "gate": "owasp_agentic_tool_chain",
            "passed": True,
            "blocking": [],
            "warnings": [],
            "total_violations": 0,
            "critical_violations": 0,
            "high_violations": 0,
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

            # Count violations by severity (last 24h)
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            for severity in ("critical", "high", "medium"):
                row = conn.execute(
                    f"SELECT COUNT(*) FROM tool_chain_events "
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
                            f"{count} critical tool chain violation(s) in last 24h"
                        )
                elif severity == "high":
                    result["high_violations"] = count
                    if count > 3:
                        result["warnings"].append(
                            f"{count} high-severity tool chain violations in last 24h"
                        )

            conn.close()
        except Exception as e:
            result["warnings"].append(f"Gate evaluation error: {str(e)}")

        return result


def main():
    parser = argparse.ArgumentParser(
        description="Tool Chain Validator — detect suspicious tool call sequences (D258)"
    )
    parser.add_argument("--check", action="store_true", help="Check current session state")
    parser.add_argument("--rules", action="store_true", help="List configured rules")
    parser.add_argument("--gate", action="store_true", help="Evaluate security gate")
    parser.add_argument("--agent-id", help="Agent ID")
    parser.add_argument("--session-id", help="Session ID")
    parser.add_argument("--project-id", help="Project ID for gate evaluation")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    validator = ToolChainValidator()

    if args.rules:
        result = {"rules": validator.get_rules(), "window_size": validator._window_size}
    elif args.gate:
        result = validator.evaluate_gate(project_id=args.project_id)
    elif args.check:
        if not args.agent_id or not args.session_id:
            print("Error: --check requires --agent-id and --session-id", file=__import__("sys").stderr)
            __import__("sys").exit(1)
        result = {"window": validator.check_session(args.agent_id, args.session_id)}
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if args.rules:
            rules = result.get("rules", [])
            print(f"Tool Chain Rules ({len(rules)} configured, window={result.get('window_size', 10)})")
            for r in rules:
                print(f"  [{r.get('severity', '?')}] {r.get('id', '?')}: {r.get('name', '?')} — {r.get('description', '')}")
        elif args.gate:
            status = "PASS" if result["passed"] else "FAIL"
            print(f"Tool Chain Gate: {status}")
            for b in result.get("blocking", []):
                print(f"  [BLOCK] {b}")
            for w in result.get("warnings", []):
                print(f"  [WARN] {w}")
        elif args.check:
            window = result.get("window", [])
            print(f"Session Window ({len(window)} calls)")
            for entry in window:
                print(f"  {entry['timestamp']}: {entry['tool_name']}")


if __name__ == "__main__":
    main()
