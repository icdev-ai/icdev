# [TEMPLATE: CUI // SP-CTI]
"""Channel adapters for the Remote Command Gateway.

:data:`ADAPTER_CLASSES` is the single channel-name → class map. It lives here
rather than inside ``gateway_agent._load_adapters`` because the gateway's Flask
app is no longer the only caller: ``tools/agent_runtime/inbox_channel.py``
(agov-inbox-03) builds an adapter from an agent process to mirror a pending
approval out to a channel. Two copies of this map would drift, and the way that
drift shows up is a channel that accepts commands but silently cannot be
delivered to.
"""

from typing import Any, Dict, Optional

from tools.gateway.adapters.base import BaseChannelAdapter
from tools.gateway.adapters.email_channel import EmailAdapter
from tools.gateway.adapters.github import GitHubAdapter
from tools.gateway.adapters.gitlab import GitLabAdapter
from tools.gateway.adapters.internal import InternalChatAdapter
from tools.gateway.adapters.mattermost import MattermostAdapter
from tools.gateway.adapters.skype import SkypeAdapter
from tools.gateway.adapters.slack import SlackAdapter
from tools.gateway.adapters.teams import TeamsAdapter
from tools.gateway.adapters.telegram import TelegramAdapter

# Channel name as it appears under `channels:` in args/remote_gateway_config.yaml.
ADAPTER_CLASSES: Dict[str, Any] = {
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


def build_adapter(channel_name: str, channel_config: Dict[str, Any]) -> Optional[BaseChannelAdapter]:
    """Instantiate one adapter, or ``None`` for an unknown/unconstructable channel.

    Deliberately does NOT consult ``enabled`` or the environment mode — that is
    :meth:`BaseChannelAdapter.is_available`'s job and the caller's decision.
    Returning ``None`` instead of raising keeps a misconfigured channel from
    turning a delivery attempt into an exception the caller has to catch.
    """
    cls = ADAPTER_CLASSES.get(channel_name)
    if cls is None:
        return None
    try:
        return cls(channel_config or {})
    except Exception:  # noqa: BLE001 — a broken channel config is not the caller's crash
        return None


__all__ = [
    "ADAPTER_CLASSES",
    "BaseChannelAdapter",
    "build_adapter",
    "EmailAdapter",
    "GitHubAdapter",
    "GitLabAdapter",
    "InternalChatAdapter",
    "MattermostAdapter",
    "SkypeAdapter",
    "SlackAdapter",
    "TeamsAdapter",
    "TelegramAdapter",
]
