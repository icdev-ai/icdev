#!/usr/bin/env python3
# CUI // SP-CTI
"""A2A Agent Entrypoint — container-aware agent bootstrap.

Reads agent card JSON + args/agent_config.yaml to fully initialize an A2A agent
server with all declared skills registered (stub or wired). Auto-registers the
agent in the SQLite registry on startup so the orchestrator can discover it.

Usage (inside container):
    python tools/a2a/agent_entrypoint.py --agent-id builder-agent --port 8445

This replaces the bare agent_server.py entrypoint used in docker-compose.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Ensure repo root is on PYTHONPATH
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from tools.a2a.agent_server import A2AAgentServer
from tools.a2a.task import Task, TaskStatus
from tools.a2a.agent_registry import register_agent
from tools.logging.icdev_logger import get_logger

try:
    import yaml
except ImportError:
    yaml = None  # Fallback to JSON-only if yaml missing (shouldn't happen)

logger = get_logger("a2a.entrypoint")

AGENT_CARDS_DIR = Path(__file__).resolve().parent / "agent_cards"
AGENT_CONFIG_PATH = ROOT / "args" / "agent_config.yaml"
SKILLS_DIR = ROOT / ".agents" / "skills"

# Mapping from skill_id -> (python_module_path, cli_args_pattern)
# Populated dynamically by scanning .agents/skills/ SKILL.md files
_SKILL_TOOL_MAP: Dict[str, tuple] = {}


def _load_skill_tool_map() -> Dict[str, tuple]:
    """Scan .agents/skills/icdev-*/SKILL.md for python tool commands.

    Returns dict: skill_id -> (module_path, description)
    """
    mapping: Dict[str, tuple] = {}
    if not SKILLS_DIR.exists():
        return mapping

    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue

        # Derive a canonical skill_id from directory name, e.g. icdev-build -> build
        skill_id = skill_dir.name.replace("icdev-", "")

        # Parse the markdown for any `python tools/...` or `!python tools/...` lines
        description = ""
        tool_commands: List[str] = []
        try:
            content = skill_md.read_text(encoding="utf-8")
            # Extract description from frontmatter or first paragraph
            if content.startswith("---"):
                # Simple frontmatter parser
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    front = parts[1]
                    for line in front.splitlines():
                        if line.strip().startswith("description:"):
                            description = line.split(":", 1)[1].strip().strip('"').strip("'")
                    body = parts[2]
                else:
                    body = content
            else:
                body = content

            # Find python tool invocations
            for line in body.splitlines():
                line_stripped = line.strip()
                # Match fenced bash commands with python tools/...
                if "python tools/" in line_stripped or "python -m tools" in line_stripped:
                    # Clean up markdown formatting
                    cmd = line_stripped.strip("`").strip()
                    if cmd.startswith("!"):
                        cmd = cmd[1:].strip()
                    if cmd.startswith("python "):
                        tool_commands.append(cmd)
        except Exception as exc:
            logger.warning(f"Failed to parse {skill_md}: {exc}")
            continue

        if tool_commands:
            # Store the first command as the primary tool delegate
            mapping[skill_id] = (tool_commands[0], description)
        else:
            mapping[skill_id] = ("", description)

    return mapping


def _load_agent_card(agent_id: str) -> Optional[dict]:
    """Load agent card JSON by agent_id."""
    # agent_id often ends with '-agent', strip for card lookup
    base_name = agent_id.replace("-agent", "")
    card_path = AGENT_CARDS_DIR / f"{base_name}.json"
    if not card_path.exists():
        # Try exact match
        card_path = AGENT_CARDS_DIR / f"{agent_id}.json"
    if not card_path.exists():
        logger.error(f"Agent card not found for {agent_id} (tried {card_path})")
        return None

    try:
        return json.loads(card_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error(f"Failed to load agent card {card_path}: {exc}")
        return None


def _load_agent_config(agent_id: str) -> Optional[dict]:
    """Load agent-specific config from args/agent_config.yaml."""
    if not AGENT_CONFIG_PATH.exists():
        return None
    try:
        with open(AGENT_CONFIG_PATH, encoding="utf-8") as f:
            config = yaml.safe_load(f) if yaml else json.load(f)
        agents_config = config.get("agents", {})
        # Map agent_id to config key
        for key, cfg in agents_config.items():
            if cfg.get("id") == agent_id:
                return cfg
    except Exception as exc:
        logger.warning(f"Failed to load agent config: {exc}")
    return None


def _make_stub_handler(skill_id: str, skill_name: str, description: str) -> Callable[[Task], Task]:
    """Create a stub skill handler that logs and returns a placeholder."""

    def _handler(task: Task) -> Task:
        logger.info(f"[STUB] Skill '{skill_id}' invoked — not yet wired to real implementation")
        task.output_data = {
            "status": "stub",
            "skill_id": skill_id,
            "skill_name": skill_name,
            "description": description,
            "message": f"Skill '{skill_id}' is registered but has no wired handler yet. "
            "Use the skill parser or register a real handler.",
            "input_echo": task.input_data,
        }
        task.update_status(TaskStatus.COMPLETED.value, f"Stub completion for {skill_id}")
        return task

    return _handler


def _make_tool_delegate_handler(skill_id: str, tool_command: str) -> Callable[[Task], Task]:
    """Create a skill handler that delegates to a Python CLI tool via subprocess."""

    def _handler(task: Task) -> Task:
        logger.info(f"[DELEGATE] Skill '{skill_id}' -> {tool_command}")
        try:
            # Build command with task input_data serialized as JSON arg if the tool supports --json
            cmd = tool_command
            # If the task has input_data and the command doesn't already have --json, append it
            if task.input_data and "--json" not in cmd:
                cmd += f" --json"

            # Run the tool
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                cwd=str(ROOT),
                timeout=300,  # 5 min timeout per skill invocation
            )

            output: Dict[str, Any] = {
                "command": cmd,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }

            if result.returncode == 0:
                # Try to parse JSON output
                try:
                    parsed = json.loads(result.stdout)
                    output["parsed"] = parsed
                except Exception:
                    pass
                task.output_data = output
                task.update_status(TaskStatus.COMPLETED.value, f"Delegated tool completed for {skill_id}")
            else:
                task.output_data = output
                task.update_status(TaskStatus.FAILED.value, f"Delegated tool failed (rc={result.returncode}) for {skill_id}")
        except subprocess.TimeoutExpired:
            task.output_data = {"error": "Timeout", "command": tool_command}
            task.update_status(TaskStatus.FAILED.value, f"Delegated tool timed out for {skill_id}")
        except Exception as e:
            logger.exception(f"Delegate handler failed for {skill_id}")
            task.output_data = {"error": str(e), "command": tool_command}
            task.update_status(TaskStatus.FAILED.value, f"Delegate handler error for {skill_id}")
        return task

    return _handler


def _register_skills(server: A2AAgentServer, card: dict, skill_map: Dict[str, tuple]) -> int:
    """Register all skills from the agent card. Returns count registered."""
    skills = card.get("skills", [])
    registered = 0

    for skill in skills:
        skill_id = skill["id"]
        name = skill.get("name", skill_id)
        description = skill.get("description", "")
        input_modes = skill.get("inputModes", ["application/json"])
        output_modes = skill.get("outputModes", ["application/json"])

        # Check if we have a wired tool delegate
        tool_command, _ = skill_map.get(skill_id, ("", ""))
        if tool_command:
            handler = _make_tool_delegate_handler(skill_id, tool_command)
            logger.info(f"Registering wired skill: {skill_id} -> {tool_command}")
        else:
            handler = _make_stub_handler(skill_id, name, description)
            logger.info(f"Registering stub skill: {skill_id}")

        server.register_skill(
            skill_id=skill_id,
            handler=handler,
            name=name,
            description=description,
            input_modes=input_modes,
            output_modes=output_modes,
        )
        registered += 1

    return registered


def _auto_register_in_db(agent_id: str, name: str, description: str, port: int) -> None:
    """Register this agent in the SQLite agents table so orchestrator can discover it."""
    # Inside Docker, the hostname is the container name, but for inter-agent comms
    # we use the docker-compose service name (which is also the container name).
    # The orchestrator needs to reach us via the docker network.
    host = os.environ.get("AGENT_HOST", agent_id.replace("-", "_"))  # e.g. icdev-builder
    url = f"http://{host}:{port}"
    try:
        capabilities = {
            "skills": [],  # Populated below if we can read card
            "port": port,
        }
        register_agent(
            agent_id=agent_id,
            name=name,
            description=description,
            url=url,
            capabilities=capabilities,
        )
        logger.info(f"Auto-registered agent {agent_id} at {url}")
    except Exception as exc:
        logger.warning(f"Auto-registration failed for {agent_id}: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="A2A Agent Entrypoint")
    parser.add_argument("--agent-id", required=True, help="Agent ID (e.g. builder-agent)")
    parser.add_argument("--port", type=int, required=True, help="Agent port")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--tls-cert", help="TLS certificate path")
    parser.add_argument("--tls-key", help="TLS private key path")
    parser.add_argument("--tls-ca", help="TLS CA cert for mutual TLS")
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug")
    parser.add_argument("--no-auto-register", action="store_true", help="Skip DB auto-registration")
    args = parser.parse_args()

    logger.info(f"=== A2A Agent Entrypoint ===")
    logger.info(f"Agent ID: {args.agent_id}")
    logger.info(f"Bind:     {args.host}:{args.port}")

    # 1. Load agent card
    card = _load_agent_card(args.agent_id)
    if not card:
        logger.error(f"Cannot start agent {args.agent_id}: missing agent card")
        sys.exit(1)

    name = card.get("name", args.agent_id)
    description = card.get("description", "")
    logger.info(f"Loaded agent card: {name}")

    # 2. Load agent config (for TLS, model prefs, etc.)
    agent_cfg = _load_agent_config(args.agent_id)
    if agent_cfg:
        tls_cfg = agent_cfg.get("tls", {})
        if not args.tls_cert and tls_cfg.get("cert"):
            args.tls_cert = tls_cfg["cert"]
        if not args.tls_key and tls_cfg.get("key"):
            args.tls_key = tls_cfg["key"]
        if not args.tls_ca and tls_cfg.get("ca"):
            args.tls_ca = tls_cfg["ca"]
        logger.info(f"Loaded agent config from args/agent_config.yaml")

    # 3. Load skill-tool mapping from .agents/skills/
    skill_map = _load_skill_tool_map()
    logger.info(f"Loaded {len(skill_map)} skill-tool mappings from .agents/skills/")

    # 4. Build and configure server
    server = A2AAgentServer(
        agent_id=args.agent_id,
        name=name,
        description=description,
        host=args.host,
        port=args.port,
        tls_cert=args.tls_cert,
        tls_key=args.tls_key,
        tls_ca=args.tls_ca,
    )

    # 5. Register skills
    registered_count = _register_skills(server, card, skill_map)
    logger.info(f"Registered {registered_count} skills")

    # 6. Auto-register in DB (unless disabled)
    if not args.no_auto_register:
        _auto_register_in_db(args.agent_id, name, description, args.port)

    # 7. Graceful shutdown handlers
    shutdown_requested = False

    def _signal_handler(signum, frame):
        nonlocal shutdown_requested
        if not shutdown_requested:
            shutdown_requested = True
            logger.info(f"Received signal {signum}, shutting down agent {args.agent_id}...")
            # Shut down ThreadPoolExecutor
            server._executor.shutdown(wait=False)
            sys.exit(0)

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    # 8. Start server
    logger.info(f"Starting {name} on {args.host}:{args.port}")
    server.run(debug=args.debug)


if __name__ == "__main__":
    main()
