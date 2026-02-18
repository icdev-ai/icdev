# CUI // SP-CTI
# ICDEV Webhook Server — GitHub + GitLab + Slack + Mattermost webhook support
# Refactored to use EventEnvelope + EventRouter (D132, D133)

"""
Webhook server for ICDEV CI/CD — receives events from GitHub, GitLab,
Slack, and Mattermost.

All incoming webhooks are normalized into EventEnvelope objects and routed
through the central EventRouter (D132). The router handles lane-aware
session queuing (D133) and workflow dispatch.

Endpoints:
    POST /gh-webhook          — GitHub webhook receiver
    POST /gl-webhook          — GitLab webhook receiver
    POST /slack/events        — Slack Events API receiver (when enabled)
    POST /mattermost/events   — Mattermost outgoing webhook (when enabled)
    GET  /health              — Health check

Usage:
    python tools/ci/triggers/webhook_server.py
    # Or with custom port:
    PORT=8001 python tools/ci/triggers/webhook_server.py

Environment:
    PORT: Server port (default: 8001)
    WEBHOOK_SECRET: GitHub webhook secret for HMAC validation (optional)
    GITLAB_WEBHOOK_TOKEN: GitLab webhook secret token (optional)
"""

import hashlib
import hmac
import os
import subprocess
import sys
from pathlib import Path

import yaml
from flask import Flask, request, jsonify

# Set up paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.ci.core.event_envelope import EventEnvelope, BOT_IDENTIFIER
from tools.ci.core.event_router import EventRouter
from tools.ci.modules.vcs import VCS

# Configuration
PORT = int(os.getenv("PORT", "8001"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
GITLAB_WEBHOOK_TOKEN = os.getenv("GITLAB_WEBHOOK_TOKEN", "")

# Load channel config
_CICD_CONFIG_PATH = PROJECT_ROOT / "args" / "cicd_config.yaml"
_CHANNEL_CONFIG = {}
try:
    if _CICD_CONFIG_PATH.exists():
        with open(_CICD_CONFIG_PATH) as f:
            _CICD_CONFIG = yaml.safe_load(f) or {}
        _CHANNEL_CONFIG = _CICD_CONFIG.get("cicd", {}).get("channels", {})
except Exception:
    pass

# Create Flask app and router
app = Flask(__name__)
router = EventRouter()


def _verify_github_signature(payload_body: bytes, signature: str) -> bool:
    """Verify GitHub webhook HMAC-SHA256 signature."""
    if not WEBHOOK_SECRET:
        return True  # Skip verification if no secret configured
    if not signature:
        return False
    expected = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(), payload_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _verify_gitlab_token(token: str) -> bool:
    """Verify GitLab webhook secret token."""
    if not GITLAB_WEBHOOK_TOKEN:
        return True  # Skip verification if no token configured
    return token == GITLAB_WEBHOOK_TOKEN


def _post_ack_comment(envelope: EventEnvelope, result: dict):
    """Post acknowledgment comment back to the issue/MR."""
    if result.get("action") != "launched":
        return
    workflow = result.get("workflow", "")
    run_id = result.get("run_id", "")
    try:
        vcs = VCS(platform=envelope.platform)
        vcs.comment_on_issue(
            int(envelope.session_key) if envelope.session_key.isdigit() else envelope.session_key,
            f"{BOT_IDENTIFIER} ICDEV Webhook: Detected `{workflow}` workflow\n\n"
            f"Run ID: `{run_id}`\n"
            f"Source: {envelope.source}\n"
            f"Logs: `agents/{run_id}/{workflow}/`"
        )
    except Exception as e:
        print(f"Warning: Failed to post ack comment: {e}")


@app.route("/gh-webhook", methods=["POST"])
def github_webhook():
    """Handle GitHub webhook events via EventEnvelope + EventRouter."""
    try:
        # Verify signature
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not _verify_github_signature(request.data, signature):
            return jsonify({"status": "error", "message": "Invalid signature"}), 403

        event_type = request.headers.get("X-GitHub-Event", "")
        payload = request.get_json()

        print(f"[GitHub] event={event_type}, action={payload.get('action', '')}")

        # Normalize into EventEnvelope
        envelope = EventEnvelope.from_github_webhook(payload, event_type)
        if envelope is None:
            return jsonify({
                "status": "ignored",
                "reason": f"Unsupported event: {event_type}/{payload.get('action', '')}",
            })

        # Route through central router
        result = router.route(envelope)

        # Post ack comment for launched workflows
        _post_ack_comment(envelope, result)

        status_code = 200
        return jsonify({"status": result["action"], **result}), status_code

    except Exception as e:
        print(f"Error processing GitHub webhook: {e}")
        return jsonify({"status": "error", "message": "Internal error"}), 200


@app.route("/gl-webhook", methods=["POST"])
def gitlab_webhook():
    """Handle GitLab webhook events via EventEnvelope + EventRouter."""
    try:
        # Verify token
        token = request.headers.get("X-Gitlab-Token", "")
        if not _verify_gitlab_token(token):
            return jsonify({"status": "error", "message": "Invalid token"}), 403

        payload = request.get_json()
        event_type = payload.get("object_kind", "")

        print(f"[GitLab] event={event_type}")

        # Normalize into EventEnvelope
        envelope = EventEnvelope.from_gitlab_webhook(payload)
        if envelope is None:
            return jsonify({
                "status": "ignored",
                "reason": f"Unsupported event: {event_type}",
            })

        # Route through central router
        result = router.route(envelope)

        # Post ack comment for launched workflows
        _post_ack_comment(envelope, result)

        return jsonify({"status": result["action"], **result})

    except Exception as e:
        print(f"Error processing GitLab webhook: {e}")
        return jsonify({"status": "error", "message": "Internal error"}), 200


# ── Slack Webhook (conditionally registered) ─────────────────────────────

def _register_slack_route():
    """Register Slack Events API webhook if enabled in config."""
    slack_config = _CHANNEL_CONFIG.get("slack", {})
    if not slack_config.get("enabled", False):
        return

    webhook_path = slack_config.get("webhook_path", "/slack/events")

    @app.route(webhook_path, methods=["POST"])
    def slack_events():
        """Handle Slack Events API webhooks."""
        try:
            payload = request.get_json()

            # Handle Slack URL verification challenge
            if payload.get("type") == "url_verification":
                return jsonify({"challenge": payload.get("challenge", "")})

            print(f"[Slack] event type={payload.get('event', {}).get('type', '')}")

            # Normalize into EventEnvelope
            envelope = EventEnvelope.from_slack_event(payload)
            if envelope is None:
                return jsonify({"status": "ignored", "reason": "Unsupported Slack event"})

            # Route through central router
            result = router.route(envelope)

            return jsonify({"status": result["action"], **result})

        except Exception as e:
            print(f"Error processing Slack event: {e}")
            return jsonify({"status": "error", "message": "Internal error"}), 200


# ── Mattermost Webhook (conditionally registered) ────────────────────────

def _register_mattermost_route():
    """Register Mattermost outgoing webhook if enabled in config."""
    mm_config = _CHANNEL_CONFIG.get("mattermost", {})
    if not mm_config.get("enabled", False):
        return

    webhook_path = mm_config.get("webhook_path", "/mattermost/events")

    @app.route(webhook_path, methods=["POST"])
    def mattermost_events():
        """Handle Mattermost outgoing webhook events."""
        try:
            payload = request.get_json()

            print(f"[Mattermost] user={payload.get('user_name', '')}")

            # Normalize into EventEnvelope
            envelope = EventEnvelope.from_mattermost_event(payload)
            if envelope is None:
                return jsonify({"status": "ignored", "reason": "Unsupported Mattermost event"})

            # Route through central router
            result = router.route(envelope)

            return jsonify({"status": result["action"], **result})

        except Exception as e:
            print(f"Error processing Mattermost event: {e}")
            return jsonify({"status": "error", "message": "Internal error"}), 200


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    # Determine enabled channels
    enabled_channels = []
    for ch_name, ch_cfg in _CHANNEL_CONFIG.items():
        if ch_cfg.get("enabled", False) or ch_name in ("github", "gitlab"):
            enabled_channels.append(ch_name)

    try:
        health_script = PROJECT_ROOT / "tools" / "testing" / "health_check.py"

        result = subprocess.run(
            [sys.executable, str(health_script)],
            capture_output=True, text=True, timeout=30,
            cwd=str(PROJECT_ROOT),
        )

        return jsonify({
            "status": "healthy" if result.returncode == 0 else "unhealthy",
            "service": "icdev-webhook-server",
            "platform_support": enabled_channels,
        })

    except subprocess.TimeoutExpired:
        return jsonify({
            "status": "unhealthy",
            "error": "Health check timed out",
        })
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "error": str(e),
        })


# Register conditional channel routes
_register_slack_route()
_register_mattermost_route()


if __name__ == "__main__":
    print("CUI // SP-CTI")
    print(f"Starting ICDEV Webhook Server on port {PORT}")
    print("  GitHub endpoint:      POST /gh-webhook")
    print("  GitLab endpoint:      POST /gl-webhook")
    slack_cfg = _CHANNEL_CONFIG.get("slack", {})
    mm_cfg = _CHANNEL_CONFIG.get("mattermost", {})
    if slack_cfg.get("enabled"):
        print(f"  Slack endpoint:       POST {slack_cfg.get('webhook_path', '/slack/events')}")
    else:
        print("  Slack:                disabled")
    if mm_cfg.get("enabled"):
        print(f"  Mattermost endpoint:  POST {mm_cfg.get('webhook_path', '/mattermost/events')}")
    else:
        print("  Mattermost:           disabled")
    print("  Health check:         GET  /health")
    app.run(host="0.0.0.0", port=PORT, debug=False)
