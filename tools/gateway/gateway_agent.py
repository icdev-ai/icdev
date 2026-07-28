#!/usr/bin/env python3

from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""Remote Command Gateway Agent — Flask app on port 8458.

Receives commands from messaging channels via webhooks, validates
through 8-gate security chain, executes ICDEV™ tools, and returns
classification-filtered responses.

Usage:
    python tools/gateway/gateway_agent.py
    PORT=8458 python tools/gateway/gateway_agent.py

Decision D133-D140: See remote_gateway_config.yaml for architecture decisions.
"""

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import yaml  # noqa: E402
from flask import Flask, request, jsonify  # noqa: E402

from tools.gateway.security_chain import run_security_chain  # noqa: E402
from tools.gateway.command_router import (  # noqa: E402
    execute_command,
    is_command_allowed,
    requires_confirmation,
)
from tools.gateway.user_binder import (  # noqa: E402
    create_challenge,
    verify_challenge,
    list_bindings,
    revoke_binding,
)

# Channel adapter imports
from tools.gateway.adapters.internal import InternalChatAdapter  # noqa: E402
from tools.gateway.adapters.telegram import TelegramAdapter  # noqa: E402
from tools.gateway.adapters.slack import SlackAdapter  # noqa: E402
from tools.gateway.adapters.mattermost import MattermostAdapter  # noqa: E402
from tools.gateway.adapters.teams import TeamsAdapter  # noqa: E402
from tools.gateway.adapters.github import GitHubAdapter  # noqa: E402
from tools.gateway.adapters.gitlab import GitLabAdapter  # noqa: E402
from tools.gateway.adapters.skype import SkypeAdapter  # noqa: E402
from tools.gateway.adapters.email_channel import EmailAdapter  # noqa: E402

logger = get_logger("icdev.gateway.agent")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONFIG_PATH = BASE_DIR / "args" / "remote_gateway_config.yaml"


def _load_config() -> Dict[str, Any]:
    """Load gateway config from YAML."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


def _load_adapters(config: Dict) -> Dict[str, Any]:
    """Load and filter channel adapters based on environment mode.

    D139: air_gapped mode auto-disables internet-dependent channels.
    """
    env_mode = config.get("environment", {}).get("mode", "connected")
    channels = config.get("channels", {})
    adapters = {}

    adapter_classes = {
        "internal_chat": InternalChatAdapter,
        "telegram": TelegramAdapter,
        "slack": SlackAdapter,
        "mattermost": MattermostAdapter,
        "teams": TeamsAdapter,
        "github": GitHubAdapter,
        "gitlab": GitLabAdapter,
        "skype": SkypeAdapter,
        "email": EmailAdapter,
    }

    for channel_name, channel_config in channels.items():
        cls = adapter_classes.get(channel_name)
        if not cls:
            continue

        adapter = cls(channel_config)
        if adapter.is_available(env_mode):
            adapters[channel_name] = adapter
            logger.info("Loaded adapter: %s (max_il=%s)", channel_name, adapter.max_il)
        else:
            reason = "disabled" if not adapter.enabled else "requires internet (air-gapped)"
            logger.info("Skipped adapter: %s (%s)", channel_name, reason)

    return adapters


# ---------------------------------------------------------------------------
# Flask App
# ---------------------------------------------------------------------------


def create_app() -> Flask:
    """Create and configure the gateway Flask application."""
    app = Flask(__name__)
    config = _load_config()
    adapters = _load_adapters(config)
    command_allowlist = config.get("command_allowlist", [])

    # Store in app context
    app.config["GATEWAY_CONFIG"] = config
    app.config["ADAPTERS"] = adapters
    app.config["COMMAND_ALLOWLIST"] = command_allowlist

    # ── Health ──────────────────────────────────────────────────
    @app.route("/health", methods=["GET"])
    def health():
        env_mode = config.get("environment", {}).get("mode", "connected")
        return jsonify(
            {
                "status": "healthy",
                "agent": "gateway",
                "port": config.get("gateway", {}).get("port", 8458),
                "environment_mode": env_mode,
                "active_channels": list(adapters.keys()),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    # ── Agent Card (A2A protocol) ──────────────────────────────
    @app.route("/.well-known/agent.json", methods=["GET"])
    def agent_card():
        card = config.get("gateway", {}).get("agent_card", {})
        return jsonify(
            {
                "name": card.get("name", "ICDEV™ Remote Command Gateway"),
                "description": card.get("description", ""),
                "version": card.get("version", "1.0.0"),
                "url": f"http://localhost:{config.get('gateway', {}).get('port', 8458)}",
                "capabilities": [
                    "remote_command_execution",
                    "classification_filtering",
                    "user_binding",
                ],
                "channels": {name: adapter.get_info() for name, adapter in adapters.items()},
            }
        )

    # ── Binding Ceremony ───────────────────────────────────────
    @app.route("/gateway/bind", methods=["POST"])
    def bind_request():
        """Initiate or complete a binding ceremony."""
        data = request.get_json(silent=True) or {}
        action = data.get("action", "initiate")

        if action == "initiate":
            channel = data.get("channel", "")
            channel_user_id = data.get("channel_user_id", "")
            if not channel or not channel_user_id:
                return jsonify({"error": "channel and channel_user_id required"}), 400

            ttl = config.get("security", {}).get("binding", {}).get("challenge_ttl_minutes", 10)
            code = create_challenge(channel, channel_user_id, ttl)
            return jsonify(
                {
                    "challenge_code": code,
                    "ttl_minutes": ttl,
                    "message": "Enter this code in the ICDEV™ dashboard or provide your API key to complete binding.",
                }
            )

        elif action == "verify":
            code = data.get("challenge_code", "")
            icdev_user_id = data.get("icdev_user_id", "")
            tenant_id = data.get("tenant_id", "")
            if not code or not icdev_user_id:
                return jsonify({"error": "challenge_code and icdev_user_id required"}), 400

            result = verify_challenge(code, icdev_user_id, tenant_id)
            status_code = 200 if result["success"] else 400
            return jsonify(result), status_code

        return jsonify({"error": f"Unknown action: {action}"}), 400

    # ── Binding Management ─────────────────────────────────────
    @app.route("/gateway/bindings", methods=["GET"])
    def get_bindings():
        channel = request.args.get("channel", "")
        status = request.args.get("status", "")
        bindings = list_bindings(channel=channel, status=status)
        return jsonify({"bindings": bindings, "count": len(bindings)})

    @app.route("/gateway/bindings/<binding_id>/revoke", methods=["POST"])
    def revoke_binding_endpoint(binding_id):
        data = request.get_json(silent=True) or {}
        reason = data.get("reason", "")
        ok = revoke_binding(binding_id, reason)
        if ok:
            return jsonify({"success": True})
        return jsonify({"error": "Binding not found or already revoked"}), 404

    # ── Dynamic Webhook Routes ─────────────────────────────────
    for channel_name, adapter in adapters.items():
        webhook_path = adapter.webhook_path
        if not webhook_path:
            continue

        _register_webhook_route(app, webhook_path, channel_name, adapter, config, command_allowlist)

    return app


def _register_webhook_route(app: Flask, path: str, channel_name: str, adapter, config: Dict, allowlist: list):
    """Register a webhook route for a channel adapter."""

    @app.route(path, methods=["POST"], endpoint=f"webhook_{channel_name}")
    def handle_webhook():
        # 1. Get raw payload for signature verification
        raw_body = request.get_data()
        headers = dict(request.headers)

        # Get signature from configured header
        sig_header = adapter.config.get("signature_header", "")
        signature = headers.get(sig_header, "") if sig_header else ""

        # 2. Verify signature via adapter
        if not adapter.verify_signature(raw_body, signature):
            return jsonify({"error": "Invalid signature"}), 401

        # 3. Parse webhook into CommandEnvelope
        data = request.get_json(silent=True) or {}

        # Handle Slack URL verification
        if data.get("type") == "url_verification":
            return jsonify({"challenge": data.get("challenge", "")})

        envelope = adapter.parse_webhook(data, headers)
        if not envelope:
            return jsonify({"status": "ignored"}), 200

        # 4. Handle /bind command specially
        if envelope.command == "bind":
            ttl = config.get("security", {}).get("binding", {}).get("challenge_ttl_minutes", 10)
            code = create_challenge(envelope.channel, envelope.channel_user_id, ttl)
            adapter.send_message(
                envelope.channel_user_id,
                f"Your binding code: `{code}`\nEnter this code in the ICDEV™ dashboard within {ttl} minutes.",
                envelope.channel_thread_id,
            )
            return jsonify({"status": "binding_initiated"}), 200

        # 4b. Agent-mode (sag-gw-01): a bound user's free-text (not a structured
        # allowlisted command) routes to the SAG agent runtime. This does NOT
        # bypass any gate — it rewrites the envelope to a synthetic 'agent'
        # command with its own allowlist entry so the full 8-gate chain below
        # runs unchanged.
        from tools.gateway.agent_mode import prepare_agent_envelope
        effective_allowlist, is_agent = prepare_agent_envelope(
            envelope, channel_name, config, allowlist
        )

        # 5. Check allowlist
        allowed, entry = is_command_allowed(envelope.command, channel_name, effective_allowlist)
        if not allowed:
            adapter.send_message(
                envelope.channel_user_id,
                f"Command `{envelope.command}` is not available on {channel_name}.",
                envelope.channel_thread_id,
            )
            return jsonify({"status": "not_allowed"}), 200

        # 6. Run security chain (same 8 gates for structured and agent commands)
        channel_config = config.get("channels", {}).get(channel_name, {})
        passed, gate_results = run_security_chain(envelope, adapter, config, channel_config, effective_allowlist)

        if not passed:
            failed = next((r for r in gate_results if not r.passed), None)
            msg = f"Command rejected: {failed.reason}" if failed else "Security check failed"
            adapter.send_message(
                envelope.channel_user_id,
                msg,
                envelope.channel_thread_id,
            )
            return jsonify({"status": "rejected", "gate": failed.gate_name if failed else "unknown"}), 200

        # 6b. Studio workflow triggers (dwo-evt-02). The envelope has cleared
        # all eight gates at this point — this hop deliberately sits AFTER the
        # chain and opens no route of its own, so an event that failed a gate
        # has already returned above and starts nothing. Fire-and-forget: the
        # webhook response must not wait on a workflow run.
        try:
            from tools.studio.event_dispatch import dispatch_envelope_async
            dispatch_envelope_async(envelope, channel_config, dict(data or {}), headers)
        except Exception as _disp_exc:  # noqa: BLE001
            # A trigger-registry problem must never turn a delivered webhook
            # into a 500 or block the command the user actually sent.
            logger.warning("studio event dispatch skipped: %s", _disp_exc)

        # 7. Check confirmation requirement
        if requires_confirmation(envelope.command, effective_allowlist):
            # For now, execute directly — confirmation flow can be added
            # with interactive buttons in future iterations
            pass

        # 8. Execute command (or run an agent turn — IL response filtering applies
        # to both; agent-mode never bypasses the response filter).
        if is_agent:
            from tools.gateway.agent_mode import handle_agent_message
            result = handle_agent_message(envelope, channel_config, config)
        else:
            result = execute_command(envelope, channel_config, config)

        # 9. Send response
        adapter.send_message(
            envelope.channel_user_id,
            result["output"],
            envelope.channel_thread_id,
        )

        return jsonify(
            {
                "status": "completed" if result["success"] else "failed",
                "audit_id": result["audit_id"],
                "filtered": result["filtered"],
                "execution_time_ms": result["execution_time_ms"],
            }
        )

    return handle_webhook


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    config = _load_config()
    gateway = config.get("gateway", {})
    host = gateway.get("host", "0.0.0.0")  # nosec B104 -- intentional bind-all for containerized/dev deployment
    port = int(os.environ.get("PORT", gateway.get("port", 8458)))
    debug = gateway.get("debug", False)

    app = create_app()

    print("CUI // SP-CTI")
    print(f"ICDEV™ Remote Command Gateway starting on {host}:{port}")
    print(f"Environment: {config.get('environment', {}).get('mode', 'connected')}")
    print(f"Active channels: {list(app.config.get('ADAPTERS', {}).keys())}")

    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()
