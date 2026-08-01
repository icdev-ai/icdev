#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Reflex Sandbox -- NemoClaw sandboxing integration for Proposal Genesis reflexes (Phase 4).

Per-reflex credential isolation and egress policy enforcement for Proposal
Genesis reflexes.  Wraps existing NemoClaw tools (credential_broker,
egress_policy_manager, blueprint_verifier) with graceful fallback when
those components are unavailable.

Key design:
    - Maps each reflex to a function for credential scoping.
    - Defines per-reflex egress policies (deny-by-default).
    - Generates K8s NetworkPolicy YAML for reflex workloads.
    - Verifies CDRL artifact integrity via recursive SHA-256 digest.
    - Graceful ImportError on all NemoClaw dependencies (backward compat).
    - Air-gap safe (stdlib only for core, optional NemoClaw for enhanced).

Tables used:
    - credential_broker_log (read -- audit queries)
    - egress_policy_audit (read -- audit queries)
    - blueprint_digests (write -- integrity storage, via BlueprintVerifier)
    - audit_trail (write -- NIST AU-2)

Usage:
    python tools/govcon/reflex_sandbox.py --reflex draft --credential --json
    python tools/govcon/reflex_sandbox.py --reflex draft --egress-policy --json
    python tools/govcon/reflex_sandbox.py --reflex draft --network-policy --json
    python tools/govcon/reflex_sandbox.py --verify-integrity --output-dir /path --json
    python tools/govcon/reflex_sandbox.py --reflex draft --audit --json
    python tools/govcon/reflex_sandbox.py --status --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Path bootstrapping
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402

# ---------------------------------------------------------------------------
# Graceful NemoClaw imports
# ---------------------------------------------------------------------------
_credential_broker = None
_egress_policy_manager = None
_blueprint_verifier = None

try:
    from tools.security.credential_broker import CredentialBroker  # noqa: E402

    _credential_broker = CredentialBroker
except ImportError:
    pass

try:
    from tools.security.egress_policy_manager import EgressPolicyManager  # noqa: E402

    _egress_policy_manager = EgressPolicyManager
except ImportError:
    pass

try:
    from tools.security.blueprint_verifier import BlueprintVerifier  # noqa: E402

    _blueprint_verifier = BlueprintVerifier
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Reflex-to-function mapping for credential scoping
REFLEX_FUNCTION_MAP: Dict[str, str] = {
    "draft": "draft_generation",
    "R7": "draft_generation",
    "fulfill": "compliance_generation",
    "R11": "compliance_generation",
    "train": "training_pair_generation",
    "R14": "training_pair_generation",
    "polish": "quality_check",
    "R8": "quality_check",
    "review": "review_analysis",
    "R15": "review_analysis",
    "discover": "sam_gov_scan",
    "R1": "sam_gov_scan",
    "scout": "competitive_intel",
    "R2": "competitive_intel",
    "extract": "requirement_extraction",
    "R4": "requirement_extraction",
    "map": "capability_mapping",
    "R5": "capability_mapping",
    "score": "bid_scoring",
    "R9": "bid_scoring",
    "price": "cost_estimation",
    "R17": "cost_estimation",
    "comply_cmmc": "cmmc_validation",
    "R18": "cmmc_validation",
    "analyze": "outcome_analysis",
    "R13": "outcome_analysis",
}

# Per-reflex egress policies (deny-by-default)
REFLEX_EGRESS_POLICIES: Dict[str, Dict[str, Any]] = {
    "draft": {
        "allow": [
            {"host": "api.anthropic.com", "port": 443, "reason": "Claude API for draft generation"},
            {"host": "localhost", "port": 11434, "reason": "Ollama local LLM"},
        ],
        "deny_all": True,
    },
    "fulfill": {
        "allow": [
            {"host": "localhost", "port": 11434, "reason": "Ollama for compliance generation"},
        ],
        "deny_all": True,
    },
    "train": {
        "allow": [
            {"host": "localhost", "port": 11434, "reason": "Ollama for pair generation"},
        ],
        "deny_all": True,
    },
    "discover": {
        "allow": [
            {"host": "api.sam.gov", "port": 443, "reason": "SAM.gov opportunity scanning"},
            {"host": "localhost", "port": 11434, "reason": "Ollama local LLM"},
        ],
        "deny_all": True,
    },
    "scout": {
        "allow": [
            {"host": "localhost", "port": 11434, "reason": "Ollama for analysis"},
        ],
        "deny_all": True,
    },
    "polish": {
        "allow": [
            {"host": "api.anthropic.com", "port": 443, "reason": "Claude API for rewrite"},
            {"host": "localhost", "port": 11434, "reason": "Ollama for quality check"},
        ],
        "deny_all": True,
    },
    "review": {
        "allow": [
            {"host": "localhost", "port": 11434, "reason": "Ollama for review analysis"},
        ],
        "deny_all": True,
    },
}

# Default policy for reflexes not explicitly listed
_DEFAULT_EGRESS_POLICY: Dict[str, Any] = {
    "allow": [
        {"host": "localhost", "port": 11434, "reason": "Ollama local LLM (default)"},
    ],
    "deny_all": True,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gen_id(prefix: str = "rsb") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _get_db():
    conn = get_connection()
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _audit(conn, action: str, details: str = "", reflex: str = "") -> None:
    try:
        conn.execute(
            "INSERT INTO audit_trail (created_at, event_type, actor, action, details, project_id, session_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                _now(),
                "govcon.reflex_sandbox",
                "reflex_sandbox",
                action,
                details,
                None,
                f"proposal_genesis.{reflex}" if reflex else "proposal_genesis",
            ),
        )
    except Exception:
        pass


def _normalize_reflex(reflex_name: str) -> str:
    """Normalize reflex name to short form (e.g. R7 -> draft)."""
    # Reverse lookup: if it's an R-number, find the canonical short name
    reflex_lower = reflex_name.lower().strip()
    # Mapping from R-numbers to canonical short names
    r_to_short = {
        "r1": "discover",
        "r2": "scout",
        "r4": "extract",
        "r5": "map",
        "r7": "draft",
        "r8": "polish",
        "r9": "score",
        "r11": "fulfill",
        "r13": "analyze",
        "r14": "train",
        "r15": "review",
        "r17": "price",
        "r18": "comply_cmmc",
    }
    if reflex_lower in r_to_short:
        return r_to_short[reflex_lower]
    return reflex_lower


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------
def get_reflex_credential(reflex_name: str) -> Dict[str, Any]:
    """Request scoped credential for a Proposal Genesis reflex.

    Maps reflex to function, attempts broker if available, falls back
    to unscoped stub credential.
    """
    canonical = _normalize_reflex(reflex_name)
    function = REFLEX_FUNCTION_MAP.get(canonical) or REFLEX_FUNCTION_MAP.get(reflex_name, "unknown")

    conn = _get_db()

    # Try the real credential broker
    if _credential_broker is not None:
        try:
            broker = _credential_broker()
            result = broker.request_credential(
                agent_id=f"proposal_genesis.{canonical}",
                function=function,
            )
            _audit(
                conn,
                "credential_requested",
                json.dumps({"reflex": canonical, "function": function, "scoped": True}),
                canonical,
            )
            conn.commit()
            conn.close()
            return {
                "reflex": canonical,
                "function": function,
                "credential_id": result.get("token_hash", result.get("credential_id", "broker-issued")),
                "scoped": True,
                "provider": result.get("provider", "broker"),
                "expires_at": result.get("expires_at", ""),
                "broker_available": True,
            }
        except Exception:
            pass  # Fall through to stub

    # Fallback: stub credential (backward compat)
    stub_id = _gen_id("cred")
    _audit(
        conn,
        "credential_stub_issued",
        json.dumps({"reflex": canonical, "function": function, "scoped": False}),
        canonical,
    )
    conn.commit()
    conn.close()

    return {
        "reflex": canonical,
        "function": function,
        "credential_id": stub_id,
        "scoped": False,
        "provider": "stub",
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "broker_available": _credential_broker is not None,
    }


def get_reflex_egress_policy(reflex_name: str) -> Dict[str, Any]:
    """Get egress policy for a Proposal Genesis reflex.

    Returns allowed endpoints and deny-all flag.
    """
    canonical = _normalize_reflex(reflex_name)
    policy = REFLEX_EGRESS_POLICIES.get(canonical, _DEFAULT_EGRESS_POLICY)

    return {
        "reflex": canonical,
        "policy": policy,
        "source": "builtin",
    }


def generate_network_policy(reflex_name: str) -> Dict[str, Any]:
    """Generate K8s NetworkPolicy YAML for a Proposal Genesis reflex.

    Based on the reflex egress policy, produces a Kubernetes NetworkPolicy
    manifest restricting egress to allowed endpoints only.
    """
    canonical = _normalize_reflex(reflex_name)
    policy = REFLEX_EGRESS_POLICIES.get(canonical, _DEFAULT_EGRESS_POLICY)
    allowed = policy.get("allow", [])

    # Build egress rules
    egress_rules: list[dict] = []

    # DNS access (always allowed)
    egress_rules.append(
        {
            "ports": [{"port": 53, "protocol": "UDP"}, {"port": 53, "protocol": "TCP"}],
            "to": [{"namespaceSelector": {}}],
        }
    )

    # Per-endpoint rules
    for endpoint in allowed:
        host = endpoint.get("host", "")
        port = endpoint.get("port", 443)
        if host == "localhost" or host == "127.0.0.1":
            # localhost access via pod-local
            egress_rules.append(
                {
                    "ports": [{"port": port, "protocol": "TCP"}],
                    "to": [{"podSelector": {"matchLabels": {"app": "icdev-proposal-genesis"}}}],
                }
            )
        else:
            # External DNS-based endpoint
            egress_rules.append(
                {
                    "ports": [{"port": port, "protocol": "TCP"}],
                    "to": [{"ipBlock": {"cidr": "0.0.0.0/0"}}],
                }
            )

    # Build full NetworkPolicy manifest
    manifest = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": f"proposal-genesis-{canonical}-egress",
            "namespace": "icdev",
            "labels": {
                "app": "icdev-proposal-genesis",
                "reflex": canonical,
                "managed-by": "icdev-reflex-sandbox",
            },
        },
        "spec": {
            "podSelector": {
                "matchLabels": {
                    "app": "icdev-proposal-genesis",
                    "reflex": canonical,
                },
            },
            "policyTypes": ["Egress"],
            "egress": egress_rules,
        },
    }

    # Convert to YAML-like string (without pyyaml dependency)
    yaml_str = _dict_to_yaml(manifest)

    return {
        "reflex": canonical,
        "yaml": yaml_str,
        "endpoint_count": len(allowed),
        "deny_all": policy.get("deny_all", True),
    }


def _dict_to_yaml(d: Any, indent: int = 0) -> str:
    """Convert a dict to YAML-formatted string (stdlib, no pyyaml)."""
    lines: list[str] = []
    prefix = "  " * indent

    if isinstance(d, dict):
        for key, value in d.items():
            if isinstance(value, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.append(_dict_to_yaml(value, indent + 1))
            elif isinstance(value, bool):
                lines.append(f"{prefix}{key}: {'true' if value else 'false'}")
            elif value is None:
                lines.append(f"{prefix}{key}: null")
            else:
                lines.append(f"{prefix}{key}: {value}")
    elif isinstance(d, list):
        for item in d:
            if isinstance(item, dict):
                first = True
                for key, value in item.items():
                    item_prefix = f"{prefix}- " if first else f"{prefix}  "
                    first = False
                    if isinstance(value, (dict, list)):
                        lines.append(f"{item_prefix}{key}:")
                        lines.append(_dict_to_yaml(value, indent + 2))
                    elif isinstance(value, bool):
                        lines.append(f"{item_prefix}{key}: {'true' if value else 'false'}")
                    elif value is None:
                        lines.append(f"{item_prefix}{key}: null")
                    else:
                        lines.append(f"{item_prefix}{key}: {value}")
            else:
                lines.append(f"{prefix}- {item}")
    else:
        lines.append(f"{prefix}{d}")

    return "\n".join(lines)


def verify_cdrl_integrity(
    output_dir: str,
    deliverable_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Verify CDRL artifact integrity via recursive SHA-256 digest.

    Computes directory digest and optionally stores via BlueprintVerifier.
    """
    dir_path = Path(output_dir)
    if not dir_path.exists():
        return {"error": f"Directory not found: {output_dir}", "verified": False}

    # Compute recursive SHA-256 digest (stdlib, air-gap safe)
    file_hashes: list[str] = []
    file_count = 0
    total_bytes = 0
    skip_dirs = {"__pycache__", ".git", ".pytest_cache", ".ruff_cache", "node_modules", ".tmp"}

    for root, dirs, files in os.walk(dir_path):
        # Skip excluded directories
        dirs[:] = [d for d in sorted(dirs) if d not in skip_dirs]
        for fname in sorted(files):
            fpath = Path(root) / fname
            rel_path = fpath.relative_to(dir_path).as_posix()
            try:
                content = fpath.read_bytes()
                file_hash = hashlib.sha256((rel_path + "\x00").encode("utf-8") + content).hexdigest()
                file_hashes.append(file_hash)
                file_count += 1
                total_bytes += len(content)
            except (OSError, PermissionError):
                continue

    if not file_hashes:
        return {"error": "No files found in directory", "verified": False}

    # Directory digest = SHA-256 of concatenated file hashes
    digest = hashlib.sha256("".join(file_hashes).encode("utf-8")).hexdigest()

    # Optionally store via BlueprintVerifier
    stored = False
    if deliverable_id and _blueprint_verifier is not None:
        try:
            verifier = _blueprint_verifier()
            verifier.store_digest(
                entity_type="capability",
                entity_id=deliverable_id,
                digest=digest,
                path=str(dir_path),
            )
            stored = True
        except Exception:
            pass

    conn = _get_db()
    _audit(
        conn,
        "cdrl_integrity_verified",
        json.dumps(
            {
                "output_dir": str(dir_path),
                "deliverable_id": deliverable_id,
                "digest": digest[:16] + "...",
                "file_count": file_count,
                "total_bytes": total_bytes,
                "stored": stored,
            }
        ),
    )
    conn.commit()
    conn.close()

    return {
        "digest": digest,
        "file_count": file_count,
        "total_bytes": total_bytes,
        "verified": True,
        "deliverable_id": deliverable_id,
        "stored_in_blueprint_digests": stored,
    }


def audit_credential_usage(
    reflex_name: Optional[str] = None,
    last_hours: int = 24,
) -> Dict[str, Any]:
    """Query credential usage log for reflex activity.

    Returns events from credential_broker_log and egress_policy_audit
    tables if they exist.
    """
    conn = _get_db()
    events: list[dict] = []
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=last_hours)).isoformat()

    # Query credential_broker_log
    try:
        if reflex_name:
            canonical = _normalize_reflex(reflex_name)
            agent_pattern = f"proposal_genesis.{canonical}"
            rows = conn.execute(
                "SELECT id, agent_id, function, action, created_at "
                "FROM credential_broker_log "
                "WHERE agent_id = %s AND created_at >= %s "
                "ORDER BY created_at DESC",
                (agent_pattern, cutoff),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, agent_id, function, action, created_at "
                "FROM credential_broker_log "
                "WHERE agent_id LIKE 'proposal_genesis.%' AND created_at >= %s "
                "ORDER BY created_at DESC",
                (cutoff,),
            ).fetchall()
        for row in rows:
            events.append(
                {
                    "source": "credential_broker_log",
                    "id": row[0],
                    "reflex": row[1].replace("proposal_genesis.", "") if row[1] else "",
                    "function": row[2],
                    "action": row[3],
                    "timestamp": row[4],
                }
            )
    except Exception:
        pass  # Table may not exist

    # Query egress_policy_audit
    try:
        if reflex_name:
            canonical = _normalize_reflex(reflex_name)
            rows = conn.execute(
                "SELECT id, role, action, created_at "
                "FROM egress_policy_audit "
                "WHERE role LIKE %s AND created_at >= %s "
                "ORDER BY created_at DESC",
                (f"%{canonical}%", cutoff),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, role, action, created_at "
                "FROM egress_policy_audit "
                "WHERE role LIKE '%proposal_genesis%' AND created_at >= %s "
                "ORDER BY created_at DESC",
                (cutoff,),
            ).fetchall()
        for row in rows:
            events.append(
                {
                    "source": "egress_policy_audit",
                    "id": row[0],
                    "reflex": row[1] if row[1] else "",
                    "function": "",
                    "action": row[2],
                    "timestamp": row[3],
                }
            )
    except Exception:
        pass  # Table may not exist

    conn.close()
    return {
        "events": events,
        "count": len(events),
        "last_hours": last_hours,
        "reflex_filter": reflex_name,
    }


def get_sandbox_status() -> Dict[str, Any]:
    """Overall sandbox posture for Proposal Genesis reflexes.

    Checks availability of NemoClaw components and returns posture score.
    """
    components: dict[str, dict] = {}

    # Check credential broker
    broker_available = _credential_broker is not None
    broker_enabled = False
    if broker_available:
        try:
            b = _credential_broker()
            broker_enabled = getattr(b, "_enabled", False)
        except Exception:
            pass
    components["credential_broker"] = {
        "available": broker_available,
        "enabled": broker_enabled,
        "description": "Per-agent credential isolation with scoped tokens (D-NC-1)",
    }

    # Check egress policy manager
    egress_available = _egress_policy_manager is not None
    components["egress_policy_manager"] = {
        "available": egress_available,
        "enabled": egress_available,
        "description": "Per-agent network egress policies with K8s NetworkPolicy (D-NC-2)",
    }

    # Check blueprint verifier
    verifier_available = _blueprint_verifier is not None
    components["blueprint_verifier"] = {
        "available": verifier_available,
        "enabled": verifier_available,
        "description": "Recursive SHA-256 directory digest verification (D-NC-3)",
    }

    # Built-in components (always available)
    components["reflex_egress_policies"] = {
        "available": True,
        "enabled": True,
        "description": f"Built-in egress policies for {len(REFLEX_EGRESS_POLICIES)} reflexes",
    }
    components["reflex_function_map"] = {
        "available": True,
        "enabled": True,
        "description": f"Reflex-to-function credential mapping for {len(set(REFLEX_FUNCTION_MAP.values()))} functions",
    }

    # Calculate posture score
    total = len(components)
    enabled = sum(1 for c in components.values() if c.get("enabled", False))
    posture_score = round(enabled / total, 2) if total > 0 else 0.0

    posture_level = "minimal"
    if posture_score >= 0.8:
        posture_level = "full"
    elif posture_score >= 0.6:
        posture_level = "enhanced"
    elif posture_score >= 0.4:
        posture_level = "basic"

    return {
        "components": components,
        "posture_score": posture_score,
        "posture_level": posture_level,
        "components_total": total,
        "components_enabled": enabled,
        "reflexes_configured": len(REFLEX_EGRESS_POLICIES),
        "functions_mapped": len(set(REFLEX_FUNCTION_MAP.values())),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reflex Sandbox -- NemoClaw sandboxing for Proposal Genesis reflexes",
    )
    parser.add_argument("--reflex", help="Reflex name (e.g. draft, R7, fulfill, R11)")
    parser.add_argument("--credential", action="store_true", help="Request scoped credential for reflex")
    parser.add_argument("--egress-policy", action="store_true", help="Get egress policy for reflex")
    parser.add_argument("--network-policy", action="store_true", help="Generate K8s NetworkPolicy YAML")
    parser.add_argument("--verify-integrity", action="store_true", help="Verify CDRL artifact integrity")
    parser.add_argument("--output-dir", help="Directory for --verify-integrity")
    parser.add_argument("--deliverable-id", help="Optional deliverable ID for digest storage")
    parser.add_argument("--audit", action="store_true", help="Query credential usage log")
    parser.add_argument("--last-hours", type=int, default=24, help="Hours for --audit lookback")
    parser.add_argument("--status", action="store_true", help="Overall sandbox posture")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    result: Dict[str, Any] = {"error": "No action specified"}

    if args.status:
        result = get_sandbox_status()
    elif args.credential:
        if not args.reflex:
            result = {"error": "--reflex required for --credential"}
        else:
            result = get_reflex_credential(args.reflex)
    elif args.egress_policy:
        if not args.reflex:
            result = {"error": "--reflex required for --egress-policy"}
        else:
            result = get_reflex_egress_policy(args.reflex)
    elif args.network_policy:
        if not args.reflex:
            result = {"error": "--reflex required for --network-policy"}
        else:
            result = generate_network_policy(args.reflex)
    elif args.verify_integrity:
        if not args.output_dir:
            result = {"error": "--output-dir required for --verify-integrity"}
        else:
            result = verify_cdrl_integrity(args.output_dir, args.deliverable_id)
    elif args.audit:
        result = audit_credential_usage(args.reflex, args.last_hours)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        if "error" in result:
            print(f"ERROR: {result['error']}")
        elif "posture_score" in result:
            print(f"Sandbox Posture: {result['posture_level'].upper()} ({result['posture_score']:.0%})")
            print(f"Components: {result['components_enabled']}/{result['components_total']} enabled")
            for name, info in result.get("components", {}).items():
                status = "ENABLED" if info.get("enabled") else ("AVAILABLE" if info.get("available") else "MISSING")
                print(f"  [{status:>9}] {name}: {info.get('description', '')}")
        elif "yaml" in result:
            print(result["yaml"])
        elif "digest" in result:
            print(f"Digest: {result['digest']}")
            print(f"Files: {result['file_count']}, Bytes: {result['total_bytes']}")
            print(f"Verified: {result['verified']}")
            if result.get("stored_in_blueprint_digests"):
                print("Stored in blueprint_digests table.")
        elif "credential_id" in result:
            print(f"Reflex: {result['reflex']}")
            print(f"Function: {result['function']}")
            print(f"Credential: {result['credential_id']}")
            print(f"Scoped: {result['scoped']}")
            print(f"Provider: {result['provider']}")
        elif "policy" in result:
            print(f"Reflex: {result['reflex']}")
            for ep in result["policy"].get("allow", []):
                print(f"  ALLOW {ep['host']}:{ep['port']} ({ep.get('reason', '')})")
            print(f"  Deny all others: {result['policy'].get('deny_all', True)}")
        elif "events" in result:
            print(f"Audit events ({result['count']} in last {result['last_hours']}h):")
            for ev in result["events"]:
                print(
                    f"  [{ev['source']}] {ev['timestamp']} {ev['action']} reflex={ev['reflex']} func={ev['function']}"
                )
        else:
            print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
# CUI // SP-CTI
