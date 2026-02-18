# CUI // SP-CTI
# ICDEV Comment Handler — cross-platform comment/message posting (D132, D136)

"""
Unified comment/message posting across GitHub, GitLab, Slack, and Mattermost.

Routes response messages to the correct platform based on the EventEnvelope
that triggered the workflow. All responses include [ICDEV-BOT] identifier
for loop prevention.

Architecture Decisions:
    D136: Slack and Mattermost are built-in connectors with enable/disable
    D137: Slack/Mattermost responses always use threads

Usage:
    from tools.ci.core.comment_handler import CommentHandler
    handler = CommentHandler()
    handler.post_response(envelope, "Pipeline completed successfully")
"""

import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.ci.core.event_envelope import EventEnvelope, BOT_IDENTIFIER


class CommentHandler:
    """Cross-platform comment/message posting."""

    def __init__(self):
        self._vcs_cache = {}

    def post_response(self, envelope: EventEnvelope, text: str) -> Optional[str]:
        """Post a response message to the platform that triggered the event.

        Args:
            envelope: The EventEnvelope that triggered the workflow.
            text: Response text to post.

        Returns:
            Comment ID or message timestamp if successful, None otherwise.
        """
        # Always prepend bot identifier
        if BOT_IDENTIFIER not in text:
            text = f"{BOT_IDENTIFIER} {text}"

        platform = envelope.platform

        if platform in ("github", "gitlab"):
            return self._post_vcs_comment(envelope, text)
        elif platform == "slack":
            return self._post_slack_message(envelope, text)
        elif platform == "mattermost":
            return self._post_mattermost_message(envelope, text)
        else:
            # Chat plugin — try connector registry
            return self._post_via_registry(envelope, text)

    def _post_vcs_comment(self, envelope: EventEnvelope, text: str) -> Optional[str]:
        """Post comment on GitHub issue/PR or GitLab issue/MR."""
        try:
            from tools.ci.modules.vcs import VCS

            platform = envelope.platform
            if platform not in self._vcs_cache:
                self._vcs_cache[platform] = VCS(platform=platform)

            vcs = self._vcs_cache[platform]
            issue_number = envelope.session_key

            if issue_number and issue_number.isdigit():
                vcs.comment_on_issue(int(issue_number), text)
                return f"{platform}:{issue_number}"
        except Exception as e:
            print(f"Warning: Failed to post {envelope.platform} comment: {e}")
        return None

    def _post_slack_message(self, envelope: EventEnvelope, text: str) -> Optional[str]:
        """Post message to Slack channel/thread (D137: always threaded)."""
        try:
            from tools.ci.connectors.connector_registry import ConnectorRegistry
            connector = ConnectorRegistry.get_connector("slack")
            if connector:
                channel_id = envelope.metadata.get("channel_id", "")
                thread_ts = envelope.metadata.get("thread_ts", "")
                if connector.send_message(channel_id, text, thread_id=thread_ts):
                    return f"slack:{channel_id}:{thread_ts}"
        except ImportError:
            # Connectors not yet available (Phase 5)
            pass
        except Exception as e:
            print(f"Warning: Failed to post Slack message: {e}")
        return None

    def _post_mattermost_message(self, envelope: EventEnvelope, text: str) -> Optional[str]:
        """Post message to Mattermost channel/thread (D137: always threaded)."""
        try:
            from tools.ci.connectors.connector_registry import ConnectorRegistry
            connector = ConnectorRegistry.get_connector("mattermost")
            if connector:
                channel_id = envelope.metadata.get("channel_id", "")
                root_id = envelope.metadata.get("root_id", "")
                if connector.send_message(channel_id, text, thread_id=root_id):
                    return f"mattermost:{channel_id}:{root_id}"
        except ImportError:
            pass
        except Exception as e:
            print(f"Warning: Failed to post Mattermost message: {e}")
        return None

    def _post_via_registry(self, envelope: EventEnvelope, text: str) -> Optional[str]:
        """Post message via connector registry (marketplace plugins)."""
        try:
            from tools.ci.connectors.connector_registry import ConnectorRegistry
            connector = ConnectorRegistry.get_connector(envelope.platform)
            if connector:
                channel_id = envelope.metadata.get("channel_id", "")
                thread_id = envelope.metadata.get("thread_id", "")
                if connector.send_message(channel_id, text, thread_id=thread_id):
                    return f"{envelope.platform}:{channel_id}"
        except ImportError:
            pass
        except Exception as e:
            print(f"Warning: Failed to post via {envelope.platform}: {e}")
        return None

    def fetch_new_comments(
        self, envelope: EventEnvelope, since_id: str = None
    ) -> list:
        """Fetch new comments/messages since a given ID.

        Returns list of dicts: [{body, author, id, timestamp}]
        """
        platform = envelope.platform

        if platform in ("github", "gitlab"):
            return self._fetch_vcs_comments(envelope, since_id)

        # Slack/Mattermost comment fetching deferred to Phase 5 connectors
        return []

    def _fetch_vcs_comments(self, envelope: EventEnvelope, since_id: str = None) -> list:
        """Fetch VCS comments for an issue/MR."""
        try:
            from tools.ci.modules.vcs import VCS

            platform = envelope.platform
            if platform not in self._vcs_cache:
                self._vcs_cache[platform] = VCS(platform=platform)

            vcs = self._vcs_cache[platform]
            issue_number = envelope.session_key

            if issue_number and issue_number.isdigit():
                comments = vcs.fetch_issue_comments(int(issue_number))
                if since_id and comments:
                    # Filter to only new comments
                    found = False
                    new_comments = []
                    for c in comments:
                        if found:
                            new_comments.append({
                                "body": c.get("body", "") or c.get("note", ""),
                                "author": (
                                    c.get("user", {}).get("login", "")
                                    or c.get("author", {}).get("username", "")
                                ),
                                "id": str(c.get("id", "")),
                                "timestamp": c.get("created_at", ""),
                            })
                        if str(c.get("id", "")) == since_id:
                            found = True
                    return new_comments
                return [
                    {
                        "body": c.get("body", "") or c.get("note", ""),
                        "author": (
                            c.get("user", {}).get("login", "")
                            or c.get("author", {}).get("username", "")
                        ),
                        "id": str(c.get("id", "")),
                        "timestamp": c.get("created_at", ""),
                    }
                    for c in (comments or [])
                ]
        except Exception as e:
            print(f"Warning: Failed to fetch comments: {e}")
        return []
