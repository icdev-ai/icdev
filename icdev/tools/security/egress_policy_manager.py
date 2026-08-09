#!/usr/bin/env python3
# CUI // SP-CTI
"""Egress Policy Manager — NemoClaw-adapted per-agent network policies (D-NC-2).

Composable YAML policy presets define which external endpoints each agent role
can reach.  Policies are merged at deploy time into K8s NetworkPolicy manifests.
Each agent role starts with a deny-all base and layers on role-specific egress
rules (NemoClaw composable policy preset pattern).

Key design:
    - Deny-by-default base policy for all agents.
    - Per-role presets in args/agent_network_policies/*.yaml.
    - Custom per-project overrides in args/agent_network_policies/custom/.
    - Merge with conflict resolution: deny always wins over allow.
    - K8s NetworkPolicy manifest generation for each agent role.
    - Policy changes logged to append-only audit table (NIST AU, D6).

CLI:
    python tools/security/egress_policy_manager.py --resolve --role builder --json
    python tools/security/egress_policy_manager.py --generate --role builder --json
    python tools/security/egress_policy_manager.py --validate --role builder --json
    python tools/security/egress_policy_manager.py --diff --role builder --json
    python tools/security/egress_policy_manager.py --list-roles --json
    python tools/security/egress_policy_manager.py --audit --json
"""

import argparse
import hashlib
import json
import sqlite3
import uuid
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

try:
    import yaml
except ImportError:
    yaml = None

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
POLICY_DIR = BASE_DIR / "args" / "agent_network_policies"

# Default agent roles with port mappings (from args/agent_config.yaml)
AGENT_ROLES = {
    "orchestrator": {"port": 8443, "tier": "core"},
    "architect": {"port": 8444, "tier": "core"},
    "builder": {"port": 8445, "tier": "domain"},
    "compliance": {"port": 8446, "tier": "domain"},
    "security": {"port": 8447, "tier": "domain"},
    "infrastructure": {"port": 8448, "tier": "domain"},
    "knowledge": {"port": 8449, "tier": "support"},
    "monitor": {"port": 8450, "tier": "support"},
    "mbse": {"port": 8451, "tier": "domain"},
    "modernization": {"port": 8452, "tier": "domain"},
    "requirements": {"port": 8453, "tier": "domain"},
    "supply_chain": {"port": 8454, "tier": "domain"},
    "simulation": {"port": 8455, "tier": "domain"},
    "integration": {"port": 8456, "tier": "domain"},
    "devsecops": {"port": 8457, "tier": "domain"},
    "gateway": {"port": 8458, "tier": "domain"},
}

# Built-in base policy (deny-all egress except internal mesh + DNS)
BASE_POLICY = {
    "name": "base",
    "description": "Default deny-all egress with internal mesh and DNS",
    "egress": {
        "allow_internal_mesh": True,
        "allow_dns": True,
        "allowed_endpoints": [],
        "denied_endpoints": [],
    },
}

# Built-in role presets (used when YAML files don't exist)
BUILTIN_PRESETS = {
    "builder": {
        "name": "builder",
        "description": "Builder agent: PyPI, npm, GitHub (git only)",
        "egress": {
            "allowed_endpoints": [
                {"host": "pypi.org", "port": 443, "protocol": "TCP", "binary": "pip"},
                {"host": "files.pythonhosted.org", "port": 443, "protocol": "TCP", "binary": "pip"},
                {"host": "registry.npmjs.org", "port": 443, "protocol": "TCP", "binary": "npm"},
                {"host": "github.com", "port": 443, "protocol": "TCP", "binary": "git"},
                {"host": "github.com", "port": 22, "protocol": "TCP", "binary": "git"},
            ],
        },
    },
    "compliance": {
        "name": "compliance",
        "description": "Compliance agent: NVD, NIST, FedRAMP feeds",
        "egress": {
            "allowed_endpoints": [
                {"host": "nvd.nist.gov", "port": 443, "protocol": "TCP"},
                {"host": "csrc.nist.gov", "port": 443, "protocol": "TCP"},
                {"host": "www.fedramp.gov", "port": 443, "protocol": "TCP"},
            ],
        },
    },
    "security": {
        "name": "security",
        "description": "Security agent: internal only (strictest)",
        "egress": {"allowed_endpoints": []},
    },
    "knowledge": {
        "name": "knowledge",
        "description": "Knowledge agent: Ollama localhost",
        "egress": {
            "allowed_endpoints": [
                {"host": "localhost", "port": 11434, "protocol": "TCP", "binary": "python"},
                {"host": "127.0.0.1", "port": 11434, "protocol": "TCP", "binary": "python"},
            ],
        },
    },
    "monitor": {
        "name": "monitor",
        "description": "Monitor agent: Prometheus, ELK, Splunk",
        "egress": {
            "allowed_endpoints": [
                {"host": "*", "port": 9090, "protocol": "TCP", "note": "Prometheus"},
                {"host": "*", "port": 9200, "protocol": "TCP", "note": "Elasticsearch"},
                {"host": "*", "port": 8089, "protocol": "TCP", "note": "Splunk HEC"},
            ],
        },
    },
    "orchestrator": {
        "name": "orchestrator",
        "description": "Orchestrator: all agent ports (mesh control) + cloud APIs",
        "egress": {
            "allowed_endpoints": [
                {"host": "*", "port": 443, "protocol": "TCP", "note": "Cloud APIs (HTTPS)"},
            ],
        },
    },
    "infrastructure": {
        "name": "infrastructure",
        "description": "Infrastructure agent: cloud provider endpoints",
        "egress": {
            "allowed_endpoints": [
                {"host": "*", "port": 443, "protocol": "TCP", "note": "Cloud APIs (HTTPS)"},
            ],
        },
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash_policy(policy: Dict) -> str:
    """Compute SHA-256 hash of policy for change tracking."""
    serialized = json.dumps(policy, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class EgressPolicyManager:
    """Per-agent network egress policy management (D-NC-2).

    Merges base deny-all + role preset + project overrides into a
    single resolved policy per agent.  Generates K8s NetworkPolicy YAML.
    """

    def __init__(self, db_path: Optional[Path] = None, policy_dir: Optional[Path] = None):
        self._db_path = db_path or DB_PATH
        self._policy_dir = policy_dir or POLICY_DIR
        self._ensure_tables()

    def _get_db(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = get_connection(db_path=str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_tables(self):
        conn = self._get_db()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS egress_policy_audit (
                id              TEXT PRIMARY KEY,
                agent_role      TEXT NOT NULL,
                policy_hash     TEXT NOT NULL,
                action          TEXT NOT NULL CHECK(action IN ('resolve', 'generate', 'apply', 'override')),
                applied_by      TEXT,
                diff_summary    TEXT,
                created_at      TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_epa_role ON egress_policy_audit(agent_role);
            CREATE INDEX IF NOT EXISTS idx_epa_created ON egress_policy_audit(created_at);
        """)
        conn.commit()
        conn.close()

    def _load_preset(self, role: str) -> Dict:
        """Load policy preset for a role from YAML or built-in."""
        if yaml and self._policy_dir.exists():
            preset_path = self._policy_dir / f"{role}.yaml"
            if preset_path.exists():
                with open(preset_path, encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
        return BUILTIN_PRESETS.get(role, {})

    def _load_custom_override(self, role: str) -> Dict:
        """Load project-specific override if available."""
        if yaml and self._policy_dir.exists():
            custom_dir = self._policy_dir / "custom"
            if custom_dir.exists():
                override_path = custom_dir / f"{role}.yaml"
                if override_path.exists():
                    with open(override_path, encoding="utf-8") as f:
                        return yaml.safe_load(f) or {}
        return {}

    def resolve_policy(self, role: str, project_overrides: Optional[Dict] = None) -> Dict:
        """Resolve the final merged policy for an agent role.

        Merge order: base → role preset → custom override → project override.
        Deny rules always win over allow rules (NemoClaw pattern).
        """
        if role not in AGENT_ROLES:
            return {"error": f"Unknown role: {role}", "known_roles": list(AGENT_ROLES.keys())}

        import copy

        merged = copy.deepcopy(BASE_POLICY)
        preset = self._load_preset(role)
        custom = self._load_custom_override(role)
        project = project_overrides or {}

        # Merge allowed endpoints (union)
        all_allowed = []
        for source in [preset, custom, project]:
            egress = source.get("egress", {})
            all_allowed.extend(egress.get("allowed_endpoints", []))

        # Collect denied endpoints (from all layers)
        all_denied = []
        for source in [merged, preset, custom, project]:
            egress = source.get("egress", {})
            all_denied.extend(egress.get("denied_endpoints", []))

        # Deny wins: remove allowed endpoints that match a deny rule
        denied_hosts = {ep.get("host", "") for ep in all_denied}
        filtered_allowed = [ep for ep in all_allowed if ep.get("host", "") not in denied_hosts]

        merged["egress"]["allowed_endpoints"] = filtered_allowed
        merged["egress"]["denied_endpoints"] = all_denied
        merged["role"] = role
        merged["agent_port"] = AGENT_ROLES[role]["port"]
        merged["tier"] = AGENT_ROLES[role]["tier"]
        merged["policy_hash"] = _hash_policy(merged)
        merged["resolved_at"] = _now()

        # Log resolution
        conn = self._get_db()
        conn.execute(
            """INSERT INTO egress_policy_audit
               (id, agent_role, policy_hash, action, diff_summary, created_at)
               VALUES (%s, %s, %s, 'resolve', %s, %s)""",
            (
                str(uuid.uuid4()),
                role,
                merged["policy_hash"],
                f"{len(filtered_allowed)} allowed, {len(all_denied)} denied",
                _now(),
            ),
        )
        conn.commit()
        conn.close()

        return merged

    def generate_k8s_manifest(self, role: str, namespace: str = "icdev") -> Dict:
        """Generate a K8s NetworkPolicy manifest for an agent role."""
        policy = self.resolve_policy(role)
        if "error" in policy:
            return policy

        egress_rules = []

        # Allow DNS (UDP 53)
        if policy["egress"].get("allow_dns", True):
            egress_rules.append(
                {
                    "ports": [{"protocol": "UDP", "port": 53}],
                    "to": [{}],
                }
            )

        # Allow internal mesh (all agent ports within namespace)
        if policy["egress"].get("allow_internal_mesh", True):
            mesh_ports = [{"protocol": "TCP", "port": info["port"]} for info in AGENT_ROLES.values()]
            # Add dashboard
            mesh_ports.append({"protocol": "TCP", "port": 5000})
            egress_rules.append(
                {
                    "ports": mesh_ports,
                    "to": [
                        {
                            "namespaceSelector": {
                                "matchLabels": {"kubernetes.io/metadata.name": namespace},
                            },
                        }
                    ],
                }
            )

        # Per-endpoint rules
        for ep in policy["egress"].get("allowed_endpoints", []):
            host = ep.get("host", "")
            port = ep.get("port", 443)
            protocol = ep.get("protocol", "TCP")
            rule = {"ports": [{"protocol": protocol, "port": port}]}
            if host and host != "*":
                # Use CIDR for localhost, else rely on DNS egress
                if host in ("localhost", "127.0.0.1"):
                    rule["to"] = [{"ipBlock": {"cidr": "127.0.0.1/32"}}]
            egress_rules.append(rule)

        manifest = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": f"icdev-{role}-egress",
                "namespace": namespace,
                "labels": {
                    "app.kubernetes.io/part-of": "icdev",
                    "app.kubernetes.io/component": role,
                    "icdev.ai/policy-hash": policy["policy_hash"][:12],
                },
                "annotations": {
                    "icdev.ai/nemoclaw-adapted": "D-NC-2",
                    "icdev.ai/resolved-at": policy["resolved_at"],
                },
            },
            "spec": {
                "podSelector": {
                    "matchLabels": {
                        "app.kubernetes.io/component": f"{role}-agent",
                    },
                },
                "policyTypes": ["Egress"],
                "egress": egress_rules,
            },
        }

        # Log generation
        conn = self._get_db()
        conn.execute(
            """INSERT INTO egress_policy_audit
               (id, agent_role, policy_hash, action, diff_summary, created_at)
               VALUES (%s, %s, %s, 'generate', %s, %s)""",
            (str(uuid.uuid4()), role, policy["policy_hash"], f"k8s_manifest_{len(egress_rules)}_rules", _now()),
        )
        conn.commit()
        conn.close()

        return manifest

    def validate_policy(self, role: str) -> Dict:
        """Validate a resolved policy for common issues."""
        policy = self.resolve_policy(role)
        if "error" in policy:
            return policy

        issues = []
        allowed = policy["egress"].get("allowed_endpoints", [])

        # Check for overly permissive rules
        wildcard_rules = [ep for ep in allowed if ep.get("host") == "*"]
        if wildcard_rules:
            issues.append(
                {
                    "severity": "warning",
                    "message": f"{len(wildcard_rules)} wildcard host rules (host='*')",
                    "recommendation": "Restrict to specific hostnames where possible",
                }
            )

        # Check for port 22 (SSH) — suspicious for non-builder
        ssh_rules = [ep for ep in allowed if ep.get("port") == 22]
        if ssh_rules and role not in ("builder", "infrastructure"):
            issues.append(
                {
                    "severity": "high",
                    "message": "SSH (port 22) allowed for non-builder/infrastructure role",
                    "recommendation": f"Remove SSH access for {role} role",
                }
            )

        # Check for no allowed endpoints (fully isolated)
        if not allowed:
            issues.append(
                {
                    "severity": "info",
                    "message": "Fully isolated — no external egress allowed",
                }
            )

        valid = all(i["severity"] != "high" for i in issues)
        return {
            "role": role,
            "valid": valid,
            "issues": issues,
            "endpoint_count": len(allowed),
            "policy_hash": policy["policy_hash"],
        }

    def diff_policies(self, role: str) -> Dict:
        """Diff current resolved policy against last applied version."""
        current = self.resolve_policy(role)
        if "error" in current:
            return current

        conn = self._get_db()
        last = conn.execute(
            """SELECT policy_hash, diff_summary, created_at
               FROM egress_policy_audit
               WHERE agent_role = %s AND action = 'apply'
               ORDER BY created_at DESC LIMIT 1""",
            (role,),
        ).fetchone()
        conn.close()

        if not last:
            return {
                "role": role,
                "changed": True,
                "reason": "no_prior_applied_policy",
                "current_hash": current["policy_hash"],
            }

        changed = last["policy_hash"] != current["policy_hash"]
        return {
            "role": role,
            "changed": changed,
            "current_hash": current["policy_hash"],
            "last_applied_hash": last["policy_hash"],
            "last_applied_at": last["created_at"],
        }

    def list_roles(self) -> List[Dict]:
        """List all known agent roles with their tier and port."""
        return [{"role": role, "port": info["port"], "tier": info["tier"]} for role, info in AGENT_ROLES.items()]

    def get_audit_log(self, role: Optional[str] = None, limit: int = 50) -> List[Dict]:
        """Get egress policy audit events."""
        conn = self._get_db()
        if role:
            rows = conn.execute(
                """SELECT * FROM egress_policy_audit
                   WHERE agent_role = %s ORDER BY created_at DESC LIMIT %s""",
                (role, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM egress_policy_audit
                   ORDER BY created_at DESC LIMIT %s""",
                (limit,),
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]


def main():
    parser = argparse.ArgumentParser(
        description="Egress Policy Manager — NemoClaw-adapted per-agent network policies (D-NC-2)"
    )
    parser.add_argument("--resolve", action="store_true", help="Resolve merged policy for role")
    parser.add_argument("--generate", action="store_true", help="Generate K8s NetworkPolicy manifest")
    parser.add_argument("--validate", action="store_true", help="Validate resolved policy")
    parser.add_argument("--diff", action="store_true", help="Diff current vs last applied")
    parser.add_argument("--list-roles", action="store_true", help="List all agent roles")
    parser.add_argument("--audit", action="store_true", help="Show audit log")
    parser.add_argument("--role", type=str, default="", help="Agent role")
    parser.add_argument("--namespace", type=str, default="icdev", help="K8s namespace")
    parser.add_argument("--limit", type=int, default=50, help="Audit log limit")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--db-path", type=Path, help="Database path override")
    args = parser.parse_args()

    mgr = EgressPolicyManager(db_path=args.db_path)

    if args.resolve:
        result = mgr.resolve_policy(args.role)
    elif args.generate:
        result = mgr.generate_k8s_manifest(args.role, namespace=args.namespace)
    elif args.validate:
        result = mgr.validate_policy(args.role)
    elif args.diff:
        result = mgr.diff_policies(args.role)
    elif args.list_roles:
        result = mgr.list_roles()
    elif args.audit:
        result = mgr.get_audit_log(role=args.role or None, limit=args.limit)
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(result)


if __name__ == "__main__":
    main()
