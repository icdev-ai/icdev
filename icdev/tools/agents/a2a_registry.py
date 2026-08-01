#!/usr/bin/env python3
# CUI // SP-CTI
"""A2A discovery registry — capability-first agent lookup (adapt-iii-01/02).

Inspired by iii-hq/iii's unified-trigger pattern: agents register capabilities,
callers query by capability name rather than port.  Replaces hard-coded port
lookups with a name-based capability index.

The 15 ICDEV A2A agents (ports 8443–8458) are seeded from args/agent_config.yaml
at startup via `seed_from_config()`.

Usage (standalone):
    python tools/agents/a2a_registry.py --list --json
    python tools/agents/a2a_registry.py --discover security_scan --json
    python tools/agents/a2a_registry.py --ping-all --json

Usage (programmatic):
    from tools.agents.a2a_registry import AgentRegistry
    registry = AgentRegistry()
    registry.seed_from_config()
    agents = registry.discover("code_generation")
"""
from __future__ import annotations

import argparse
import json
from tools.logging.icdev_logger import get_logger
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

logger = get_logger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
_CONFIG_PATH = BASE_DIR / "args" / "agent_config.yaml"

# Capability → agent name mapping seeded from CLAUDE.md port table
_DEFAULT_CAPABILITY_MAP: dict[str, list[str]] = {
    "orchestration": ["orchestrator"],
    "architecture": ["architect"],
    "code_generation": ["builder"],
    "compliance_review": ["compliance"],
    "security_scan": ["security"],
    "security_audit": ["security"],
    "infrastructure": ["infrastructure"],
    "mbse": ["mbse"],
    "modernization": ["modernization"],
    "requirements_analysis": ["requirements"],
    "supply_chain_analysis": ["supply_chain"],
    "simulation": ["simulation"],
    "devsecops": ["devsecops_zta"],
    "zero_trust": ["devsecops_zta"],
    "gateway": ["gateway"],
    "knowledge_retrieval": ["knowledge"],
    "monitoring": ["monitor"],
}


@dataclass
class AgentEntry:
    name: str
    port: int
    capabilities: list[str]
    health_url: str = ""
    description: str = ""
    tier: str = "domain"
    host: str = "127.0.0.1"

    def base_url(self) -> str:
        return f"https://{self.host}:{self.port}"


class AgentRegistry:
    """Capability-indexed A2A agent discovery registry."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentEntry] = {}  # name → entry
        self._cap_index: dict[str, list[str]] = {}  # capability → [agent_names]

    # -------------------------------------------------------------------------
    # Registration
    # -------------------------------------------------------------------------

    def register(self, name: str, port: int, capabilities: list[str],
                 health_url: str = "", description: str = "",
                 tier: str = "domain", host: str = "127.0.0.1") -> None:
        """Register an agent with its capabilities."""
        entry = AgentEntry(
            name=name,
            port=port,
            capabilities=capabilities,
            health_url=health_url or f"https://{host}:{port}/health",
            description=description,
            tier=tier,
            host=host,
        )
        self._agents[name] = entry
        for cap in capabilities:
            self._cap_index.setdefault(cap, [])
            if name not in self._cap_index[cap]:
                self._cap_index[cap].append(name)
        logger.debug("a2a_registry: registered %r with caps=%s", name, capabilities)

    # -------------------------------------------------------------------------
    # Discovery
    # -------------------------------------------------------------------------

    def discover(self, capability: str) -> list[AgentEntry]:
        """Return agents that support the requested capability.

        Falls back to prefix matching (e.g. "security" matches "security_scan").
        """
        names = self._cap_index.get(capability)
        if not names:
            # Prefix fallback
            names = [
                agent_name
                for cap, agent_names in self._cap_index.items()
                if cap.startswith(capability) or capability.startswith(cap)
                for agent_name in agent_names
            ]
            names = list(dict.fromkeys(names))  # dedup preserving order

        return [self._agents[n] for n in names if n in self._agents]

    def get(self, name: str) -> AgentEntry | None:
        return self._agents.get(name)

    def all_agents(self) -> list[AgentEntry]:
        return list(self._agents.values())

    def list_capabilities(self) -> list[str]:
        return sorted(self._cap_index.keys())

    # -------------------------------------------------------------------------
    # Health check
    # -------------------------------------------------------------------------

    def ping_all(self, timeout: int = 3) -> dict[str, dict]:
        """Ping every registered agent's health URL.

        Returns {agent_name: {status, latency_ms, error}}. Never raises.
        """
        results: dict[str, dict] = {}
        for name, entry in self._agents.items():
            t0 = time.monotonic()
            try:
                import requests
                resp = requests.get(entry.health_url, timeout=timeout, verify=False)  # nosec B501 — internal agent health-check on loopback/LAN
                latency = round((time.monotonic() - t0) * 1000, 1)
                results[name] = {
                    "status": "ok" if resp.status_code < 400 else "degraded",
                    "latency_ms": latency,
                    "http_status": resp.status_code,
                    "error": None,
                }
            except ImportError:
                results[name] = {"status": "unknown", "latency_ms": None, "error": "requests_unavailable"}
            except Exception as exc:
                latency = round((time.monotonic() - t0) * 1000, 1)
                results[name] = {"status": "error", "latency_ms": latency, "error": str(exc)[:100]}
        return results

    # -------------------------------------------------------------------------
    # Seed from config
    # -------------------------------------------------------------------------

    def seed_from_config(self, config_path: Path | None = None) -> int:
        """Load agents from args/agent_config.yaml and register them.

        Returns number of agents registered.
        """
        path = config_path or _CONFIG_PATH
        if not path.exists():
            logger.warning("a2a_registry: config not found at %s, using built-in defaults", path)
            return self._seed_defaults()

        try:
            import yaml
            with open(path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        except Exception as exc:
            logger.warning("a2a_registry: failed to parse %s: %s — using defaults", path, exc)
            return self._seed_defaults()

        agents_cfg = raw.get("agents") or {}
        count = 0
        for agent_name, cfg in agents_cfg.items():
            if not isinstance(cfg, dict):
                continue
            port = int(cfg.get("port") or 0)
            if not port:
                continue
            caps = cfg.get("capabilities") or _DEFAULT_CAPABILITY_MAP.get(agent_name, [agent_name])
            self.register(
                name=agent_name,
                port=port,
                capabilities=list(caps),
                description=cfg.get("description") or "",
                tier=cfg.get("tier") or "domain",
                host=cfg.get("host") or "127.0.0.1",
            )
            count += 1
        if count == 0:
            return self._seed_defaults()
        return count

    def _seed_defaults(self) -> int:
        """Seed the registry from the known CLAUDE.md port table (fallback)."""
        defaults = [
            ("orchestrator", 8443, ["orchestration"], "core"),
            ("architect", 8444, ["architecture"], "core"),
            ("builder", 8445, ["code_generation", "build"], "domain"),
            ("compliance", 8446, ["compliance_review", "fedramp", "cmmc"], "domain"),
            ("security", 8447, ["security_scan", "security_audit", "vulnerability"], "domain"),
            ("infrastructure", 8448, ["infrastructure", "deployment"], "domain"),
            ("knowledge", 8449, ["knowledge_retrieval", "rag"], "support"),
            ("monitor", 8450, ["monitoring", "alerting"], "support"),
            ("mbse", 8451, ["mbse", "systems_engineering"], "domain"),
            ("modernization", 8452, ["modernization", "migration"], "domain"),
            ("requirements", 8453, ["requirements_analysis", "elicitation"], "domain"),
            ("supply_chain", 8454, ["supply_chain_analysis", "sbom"], "domain"),
            ("simulation", 8455, ["simulation", "digital_twin"], "domain"),
            ("devsecops_zta", 8456, ["devsecops", "zero_trust", "ci_cd"], "domain"),
            ("gateway", 8458, ["gateway", "routing", "mcp"], "domain"),
        ]
        for name, port, caps, tier in defaults:
            self.register(name=name, port=port, capabilities=caps, tier=tier)
        return len(defaults)


# ---------------------------------------------------------------------------
# Module-level singleton (lazy-initialized)
# ---------------------------------------------------------------------------

_default_registry: AgentRegistry | None = None


def get_registry() -> AgentRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = AgentRegistry()
        _default_registry.seed_from_config()
    return _default_registry


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli() -> None:
    parser = argparse.ArgumentParser(description="ICDEV A2A Discovery Registry (adapt-iii-01)")
    parser.add_argument("--list", action="store_true", help="List all registered agents")
    parser.add_argument("--discover", metavar="CAPABILITY", help="Discover agents by capability")
    parser.add_argument("--capabilities", action="store_true", help="List all capabilities")
    parser.add_argument("--ping-all", action="store_true", dest="ping_all", help="Ping all agent health endpoints")
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON output")
    args = parser.parse_args()

    registry = get_registry()

    if args.list:
        agents = [asdict(a) for a in registry.all_agents()]
        if args.as_json:
            print(json.dumps({"count": len(agents), "agents": agents}))
        else:
            for a in registry.all_agents():
                print(f"  {a.name:<20} port={a.port}  caps={a.capabilities}")

    elif args.discover:
        found = [asdict(a) for a in registry.discover(args.discover)]
        if args.as_json:
            print(json.dumps({"capability": args.discover, "count": len(found), "agents": found}))
        else:
            for a in registry.discover(args.discover):
                print(f"  {a.name}  port={a.port}")

    elif args.capabilities:
        caps = registry.list_capabilities()
        if args.as_json:
            print(json.dumps({"capabilities": caps}))
        else:
            for c in caps:
                print(f"  {c}")

    elif args.ping_all:
        results = registry.ping_all()
        if args.as_json:
            print(json.dumps(results))
        else:
            for name, status in results.items():
                print(f"  {name:<20} {status['status']}  latency={status.get('latency_ms')}ms")

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    _cli()
