# CUI // SP-CTI
# ICDEV Webhook Server — GitHub + GitLab dual webhook support
# Adapted from ADW trigger_webhook.py with GitLab support added

"""
Webhook server for ICDEV CI/CD — receives events from GitHub and GitLab.

Listens for issue/MR events from both platforms and triggers ICDEV workflows
in the background. Returns immediately to meet webhook timeout requirements.

Endpoints:
    POST /gh-webhook  — GitHub webhook receiver
    POST /gl-webhook  — GitLab webhook receiver
    GET  /health      — Health check

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
import json
import os
import subprocess
import sys
from pathlib import Path

from flask import Flask, Request, request, jsonify

# Set up paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.ci.modules.workflow_ops import (
    extract_icdev_info,
    format_issue_message,
    AVAILABLE_ICDEV_WORKFLOWS,
    BOT_IDENTIFIER,
)
from tools.ci.modules.state import ICDevState
from tools.ci.modules.vcs import VCS
from tools.testing.utils import make_run_id, get_safe_subprocess_env, setup_logger

# Configuration
PORT = int(os.getenv("PORT", "8001"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
GITLAB_WEBHOOK_TOKEN = os.getenv("GITLAB_WEBHOOK_TOKEN", "")

# Create Flask app
app = Flask(__name__)


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


def _launch_workflow(workflow: str, issue_number: str, run_id: str,
                     platform: str, vcs: VCS = None):
    """Launch a workflow script in the background."""
    script_path = PROJECT_ROOT / "tools" / "ci" / "workflows" / f"{workflow}.py"

    cmd = [sys.executable, str(script_path), issue_number, run_id]

    print(f"Launching {workflow} for issue #{issue_number} (platform: {platform})")
    print(f"Command: {' '.join(cmd)}")

    process = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        env=get_safe_subprocess_env(),
        stdin=subprocess.DEVNULL,
    )

    print(f"Background process started (PID: {process.pid}) for issue #{issue_number}")
    print(f"Logs: agents/{run_id}/{workflow}/execution.log")


@app.route("/gh-webhook", methods=["POST"])
def github_webhook():
    """Handle GitHub webhook events."""
    try:
        # Verify signature
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not _verify_github_signature(request.data, signature):
            return jsonify({"status": "error", "message": "Invalid signature"}), 403

        event_type = request.headers.get("X-GitHub-Event", "")
        payload = request.get_json()

        action = payload.get("action", "")
        issue = payload.get("issue", {})
        issue_number = issue.get("number")

        print(f"[GitHub] event={event_type}, action={action}, issue={issue_number}")

        workflow = None
        provided_run_id = None
        trigger_reason = ""
        content_to_check = ""

        # Issue opened
        if event_type == "issues" and action == "opened" and issue_number:
            content_to_check = issue.get("body", "")
            if "icdev_" in content_to_check.lower():
                temp_id = make_run_id()
                workflow, provided_run_id = extract_icdev_info(content_to_check, temp_id)
                if workflow:
                    trigger_reason = f"New issue with {workflow} workflow"

        # Issue comment
        elif event_type == "issue_comment" and action == "created" and issue_number:
            comment = payload.get("comment", {})
            comment_body = comment.get("body", "")
            content_to_check = comment_body

            # Ignore bot comments to prevent loops
            if BOT_IDENTIFIER in comment_body:
                print(f"Ignoring ICDEV bot comment to prevent loop")
                workflow = None
            elif "icdev_" in comment_body.lower():
                temp_id = make_run_id()
                workflow, provided_run_id = extract_icdev_info(comment_body, temp_id)
                if workflow:
                    trigger_reason = f"Comment with {workflow} workflow"

        # Validate workflow constraints
        if workflow in ("icdev_build", "icdev_review") and not provided_run_id:
            print(f"{workflow} requires a run_id, skipping")
            workflow = None

        if workflow:
            run_id = provided_run_id or make_run_id()

            # Create/update state
            state = ICDevState.load(run_id)
            state.update(
                run_id=run_id,
                issue_number=str(issue_number),
                platform="github",
            )
            state.save("webhook_trigger")

            logger = setup_logger(run_id, "webhook_trigger")
            logger.info(f"Detected workflow: {workflow}")

            # Post comment to issue
            try:
                vcs = VCS(platform="github")
                vcs.comment_on_issue(
                    issue_number,
                    f"{BOT_IDENTIFIER} ICDEV Webhook: Detected `{workflow}` workflow\n\n"
                    f"Run ID: `{run_id}`\n"
                    f"Reason: {trigger_reason}\n"
                    f"Logs: `agents/{run_id}/{workflow}/`"
                )
            except Exception as e:
                logger.warning(f"Failed to post issue comment: {e}")

            # Launch workflow
            _launch_workflow(workflow, str(issue_number), run_id, "github")

            return jsonify({
                "status": "accepted",
                "issue": issue_number,
                "run_id": run_id,
                "workflow": workflow,
                "reason": trigger_reason,
            })
        else:
            return jsonify({
                "status": "ignored",
                "reason": f"Not a triggering event (event={event_type}, action={action})",
            })

    except Exception as e:
        print(f"Error processing GitHub webhook: {e}")
        return jsonify({"status": "error", "message": "Internal error"}), 200


@app.route("/gl-webhook", methods=["POST"])
def gitlab_webhook():
    """Handle GitLab webhook events."""
    try:
        # Verify token
        token = request.headers.get("X-Gitlab-Token", "")
        if not _verify_gitlab_token(token):
            return jsonify({"status": "error", "message": "Invalid token"}), 403

        payload = request.get_json()
        event_type = payload.get("object_kind", "")

        print(f"[GitLab] event={event_type}")

        workflow = None
        provided_run_id = None
        trigger_reason = ""
        issue_number = None
        content_to_check = ""

        # Issue event
        if event_type == "issue":
            attrs = payload.get("object_attributes", {})
            action = attrs.get("action", "")
            issue_number = attrs.get("iid")

            if action == "open" and issue_number:
                content_to_check = attrs.get("description", "")
                if "icdev_" in content_to_check.lower():
                    temp_id = make_run_id()
                    workflow, provided_run_id = extract_icdev_info(content_to_check, temp_id)
                    if workflow:
                        trigger_reason = f"New issue with {workflow} workflow"

        # Note (comment) event
        elif event_type == "note":
            attrs = payload.get("object_attributes", {})
            note_body = attrs.get("note", "")
            noteable_type = attrs.get("noteable_type", "")
            content_to_check = note_body

            if noteable_type == "Issue":
                issue = payload.get("issue", {})
                issue_number = issue.get("iid")

                if BOT_IDENTIFIER in note_body:
                    print(f"Ignoring ICDEV bot note to prevent loop")
                    workflow = None
                elif "icdev_" in note_body.lower():
                    temp_id = make_run_id()
                    workflow, provided_run_id = extract_icdev_info(note_body, temp_id)
                    if workflow:
                        trigger_reason = f"Note with {workflow} workflow"

        # Merge request event
        elif event_type == "merge_request":
            attrs = payload.get("object_attributes", {})
            action = attrs.get("action", "")
            mr_iid = attrs.get("iid")
            content_to_check = attrs.get("description", "")

            if action in ("open", "reopen") and "icdev_" in content_to_check.lower():
                issue_number = mr_iid
                temp_id = make_run_id()
                workflow, provided_run_id = extract_icdev_info(content_to_check, temp_id)
                if workflow:
                    trigger_reason = f"MR with {workflow} workflow"

        # Validate constraints
        if workflow in ("icdev_build", "icdev_review") and not provided_run_id:
            print(f"{workflow} requires a run_id, skipping")
            workflow = None

        if workflow and issue_number:
            run_id = provided_run_id or make_run_id()

            # Get project_id from payload
            project_id = payload.get("project", {}).get("id")

            state = ICDevState.load(run_id)
            state.update(
                run_id=run_id,
                issue_number=str(issue_number),
                platform="gitlab",
                project_id=str(project_id) if project_id else None,
            )
            state.save("webhook_trigger")

            logger = setup_logger(run_id, "webhook_trigger")
            logger.info(f"Detected workflow: {workflow}")

            # Post comment to issue
            try:
                vcs = VCS(platform="gitlab")
                vcs.comment_on_issue(
                    issue_number,
                    f"{BOT_IDENTIFIER} ICDEV Webhook: Detected `{workflow}` workflow\n\n"
                    f"Run ID: `{run_id}`\n"
                    f"Reason: {trigger_reason}\n"
                    f"Logs: `agents/{run_id}/{workflow}/`"
                )
            except Exception as e:
                logger.warning(f"Failed to post issue comment: {e}")

            # Launch workflow
            _launch_workflow(workflow, str(issue_number), run_id, "gitlab")

            return jsonify({
                "status": "accepted",
                "issue": issue_number,
                "run_id": run_id,
                "workflow": workflow,
                "reason": trigger_reason,
            })
        else:
            return jsonify({
                "status": "ignored",
                "reason": f"Not a triggering event (event={event_type})",
            })

    except Exception as e:
        print(f"Error processing GitLab webhook: {e}")
        return jsonify({"status": "error", "message": "Internal error"}), 200


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
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
            "platform_support": ["github", "gitlab"],
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


if __name__ == "__main__":
    print(f"CUI // SP-CTI")
    print(f"Starting ICDEV Webhook Server on port {PORT}")
    print(f"  GitHub endpoint: POST /gh-webhook")
    print(f"  GitLab endpoint: POST /gl-webhook")
    print(f"  Health check:    GET  /health")
    app.run(host="0.0.0.0", port=PORT, debug=False)
