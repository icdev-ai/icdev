# CUI // SP-CTI
# ICDEV Connector Registry — plugin registry + route registration (D66, D136)

"""
Central registry for all chat platform connectors.

Loads enabled connectors from cicd_config.yaml, instantiates them,
and registers Flask routes for their webhook endpoints.

Architecture Decisions:
    D66: Provider abstraction pattern (ABC + implementations)
    D136: Built-in connectors (Slack, Mattermost) with enable/disable toggles;
          marketplace plugins can register additional connectors

Usage:
    from tools.ci.connectors.connector_registry import ConnectorRegistry

    # At application startup
    ConnectorRegistry.load_from_config()
    ConnectorRegistry.register_routes(app)

    # Get a connector for sending messages
    slack = ConnectorRegistry.get_connector("slack")
    if slack:
        slack.send_message(channel_id, "Hello", thread_id=ts)
"""

import json
import sys
from pathlib import Path
from typing import Dict, Optional

from tools.ci.connectors.base_connector import ChatConnectorAdapter

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


class ConnectorRegistry:
    """Central registry for chat platform connectors."""

    _connectors: Dict[str, ChatConnectorAdapter] = {}

    @classmethod
    def load_from_config(cls, config: dict = None):
        """Load and register enabled connectors from cicd_config.yaml.

        Args:
            config: Optional pre-loaded config dict. If None, loads from file.
        """
        if config is None:
            config = cls._load_config()

        channels = config.get("cicd", {}).get("channels", {})

        # Slack
        slack_config = channels.get("slack", {})
        if slack_config.get("enabled", False):
            try:
                from tools.ci.connectors.slack_connector import SlackConnector
                connector = SlackConnector(slack_config)
                cls.register(connector)
                print(f"[ConnectorRegistry] Slack connector registered")
            except ImportError as e:
                print(f"[ConnectorRegistry] Slack connector unavailable: {e}")

        # Mattermost
        mm_config = channels.get("mattermost", {})
        if mm_config.get("enabled", False):
            try:
                from tools.ci.connectors.mattermost_connector import MattermostConnector
                connector = MattermostConnector(mm_config)
                cls.register(connector)
                print(f"[ConnectorRegistry] Mattermost connector registered")
            except ImportError as e:
                print(f"[ConnectorRegistry] Mattermost connector unavailable: {e}")

    @classmethod
    def register(cls, connector: ChatConnectorAdapter):
        """Register a connector."""
        cls._connectors[connector.connector_name] = connector

    @classmethod
    def unregister(cls, name: str):
        """Unregister a connector."""
        cls._connectors.pop(name, None)

    @classmethod
    def get_connector(cls, name: str) -> Optional[ChatConnectorAdapter]:
        """Get a registered connector by name."""
        return cls._connectors.get(name)

    @classmethod
    def list_connectors(cls) -> Dict[str, bool]:
        """List all registered connectors with their enabled status."""
        return {
            name: connector.is_enabled()
            for name, connector in cls._connectors.items()
        }

    @classmethod
    def register_routes(cls, app):
        """Register Flask routes for all enabled connectors.

        Args:
            app: Flask application instance.
        """
        from tools.ci.core.event_router import EventRouter

        router = EventRouter()

        for name, connector in cls._connectors.items():
            if not connector.is_enabled():
                continue

            route_path = connector.get_webhook_route()
            cls._create_route(app, connector, router, route_path)

    @classmethod
    def _create_route(cls, app, connector, router, route_path):
        """Create a Flask route for a connector."""

        def make_handler(conn, rtr):
            def handler():
                from flask import request, jsonify

                # Verify signature
                raw_body = request.get_data()
                signature = (
                    request.headers.get("X-Slack-Signature", "")
                    or request.headers.get("X-Mattermost-Signature", "")
                    or request.headers.get("X-Signature", "")
                )

                # For Slack, prepend timestamp for signature verification
                if conn.connector_name == "slack":
                    timestamp = request.headers.get(
                        "X-Slack-Request-Timestamp", ""
                    )
                    signature = f"{timestamp}:{signature}" if timestamp else signature

                if signature and not conn.verify_signature(raw_body, signature):
                    return jsonify({"error": "invalid_signature"}), 401

                # Parse payload
                payload = request.get_json(silent=True) or {}

                # Handle Slack URL verification challenge
                if (
                    conn.connector_name == "slack"
                    and payload.get("type") == "url_verification"
                ):
                    return jsonify({"challenge": payload.get("challenge", "")})

                # Parse inbound event
                envelope = conn.parse_inbound(payload)
                if not envelope:
                    return jsonify({"status": "ignored"}), 200

                # Route through EventRouter
                result = rtr.route(envelope)
                return jsonify(result), 200

            handler.__name__ = f"{conn.connector_name}_webhook_handler"
            return handler

        app.add_url_rule(
            route_path,
            endpoint=f"{connector.connector_name}_webhook",
            view_func=make_handler(connector, router),
            methods=["POST"],
        )
        print(f"[ConnectorRegistry] Registered route: POST {route_path}")

    @classmethod
    def reset(cls):
        """Clear all registered connectors (for testing)."""
        cls._connectors.clear()

    @staticmethod
    def _load_config() -> dict:
        """Load cicd_config.yaml."""
        try:
            import yaml
            config_path = PROJECT_ROOT / "args" / "cicd_config.yaml"
            if config_path.exists():
                with open(config_path) as f:
                    return yaml.safe_load(f) or {}
        except Exception:
            pass
        return {}
