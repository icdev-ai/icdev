# CUI // SP-CTI
"""Channel delivery and reply resolution for the approval inbox (agov-inbox-03).

agov-inbox-01 gave the ask a durable home (``approval_items``) and agov-inbox-02
made that home reachable from the reversibility gate without changing the gate.
Neither of them tells anyone. This module is the mirror: it delivers a pending
item to a messaging channel and turns the human's reply back into a resolution.

## No new transport

Every gateway adapter already exposes the same
``BaseChannelAdapter.send_message(channel_user_id, text, thread_id)`` — Slack,
Teams, Telegram, Mattermost and email — over ``urllib`` / ``smtplib`` from the
standard library. This module calls that seam and adds **no HTTP client of its
own**; ``tests/test_inbox_channel.py`` asserts the absence, because a second
transport here would be a second place to get TLS, proxying and IL handling
wrong.

## The correlation token is the whole design

A delivered message carries ``[icdev:<item_id>]``. A reply resolves the item
named by the token in that reply and nothing else.

**A reply with no recognisable token is IGNORED.** Not applied to the most
recent pending item, not applied to the only pending item, not applied at all.
The tempting fallback — "there is one thing waiting, they must mean that one" —
is precisely how a bare "yes" approves the wrong irreversible action. Two
distinct tokens in one reply are ignored for the same reason: ambiguity is
refused, never guessed. :func:`extract_token` returns ``None`` in both cases and
:func:`resolve_from_reply` stops there.

## In-app is the store of record; the channel is a mirror

:func:`deliver` never raises and never touches item state. A send that fails,
times out, or returns ``False`` produces a :class:`DeliveryResult` with
``delivered=False`` and leaves the item ``pending`` — so it is still in the
inbox, still answerable in-app, and still subject to the expiry sweep. A mirror
that cannot be written must never lose or auto-resolve the thing it mirrors.

## Outbound goes through the IL response filter

The composed body runs through :func:`tools.gateway.response_filter.filter_response`
against the destination channel's ``max_il`` before it leaves. ``render_summary``
already keeps argument *values* out, but a tool name, a policy rule or a
caller-supplied body can still carry a CUI marking, and Telegram is an IL4
channel. The filter is applied to the body only; the correlation footer is
appended **after** filtering and after truncation, so redacting or shortening a
message can never destroy the token that makes the reply resolvable.

## Inbound still traverses all eight gates

An approval arriving from a chat channel is exactly the case
``tools/gateway/security_chain.py`` exists for. :func:`resolve_from_reply`
refuses to settle anything unless the envelope's ``gate_results`` shows all
eight gates passed, so calling this module directly is not a way around the
chain. The reply is validated as the ``icdev-approve`` command
(:data:`APPROVAL_COMMAND`), which has its own entry in
``args/remote_gateway_config.yaml`` — the same synthetic-command shape
``agent_mode.prepare_agent_envelope`` already uses, and for the same reason: the
chain runs unchanged.

Who may answer is gate 3's identity binding plus the optional ``approvers``
allowlist in ``args/approval_inbox_routing.yaml`` (empty by default, fail-closed
once set).

## A free-text reply does not approve anything

Intent is ``approve``, ``deny``, or — when the reply is neither, or is somehow
both — ``answer``. An ``answer`` is returned to the caller and the item stays
``pending``: an approval must be an explicit, unambiguous act, and "hold on, let
me check" is not one. The item remains answerable and still expires to
``denied`` on its own clock. There is no third resolution and no new table; the
text is not written to the append-only decision log, only a SHA-256 of it, for
the same reason argument values are never stored.

CLI::

    python tools/agent_runtime/inbox_channel.py --route --json
    python tools/agent_runtime/inbox_channel.py --deliver <item_id> --json
    python tools/agent_runtime/inbox_channel.py --deliver-pending --inbox ops --json
    python tools/agent_runtime/inbox_channel.py --parse "approve [icdev:ai-1234]" --json
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.agent_runtime.approval_inbox import (  # noqa: E402
    DEFAULT_INBOX,
    ApprovalItem,
    get,
    list_pending,
    resolve,
)
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.agent_runtime.inbox_channel")

# `tools.gateway` is imported LAZILY, inside the functions that need it. It has
# no icdev/ twin (there is no icdev/tools/gateway), so a module-level import
# would make this file's own mirror unimportable in a wheel — and rendering,
# routing and token parsing all work without a gateway installed.

ROUTING_CONFIG_PATH = _REPO_ROOT / "args" / "approval_inbox_routing.yaml"
GATEWAY_CONFIG_PATH = _REPO_ROOT / "args" / "remote_gateway_config.yaml"

# The allowlisted command an inbound approval reply is validated as. Declared in
# args/remote_gateway_config.yaml so operators, not this module, decide which
# channels may carry one.
APPROVAL_COMMAND = "icdev-approve"

# All eight gates of tools/gateway/security_chain.py, by the names
# run_security_chain writes into envelope.gate_results.
REQUIRED_GATES = (
    "signature",
    "bot_replay",
    "identity",
    "authentication",
    "classification",
    "rbac",
    "rate_limit",
    "domain_authority",
)

PERSONA_ENV = "ICDEV_APPROVAL_PERSONA"

DEFAULT_MAX_BODY_CHARS = 3000
DEFAULT_DASHBOARD_URL = "/agent-approvals"

# ---------------------------------------------------------------------------
# Correlation token
# ---------------------------------------------------------------------------
# `icdev` is matched case-insensitively (mail clients and mobile keyboards
# capitalise); the id is captured verbatim. The id charset is deliberately
# narrower than "anything": approval_inbox mints `ai-<16 hex>`, and a permissive
# pattern would let a stray bracket in prose look like a token.
_TOKEN_RE = re.compile(r"\[\s*icdev\s*:\s*([A-Za-z0-9][A-Za-z0-9_.-]{0,63})\s*\]", re.IGNORECASE)

_WORD_RE = re.compile(r"[a-z']+")

# Keyword intents. Single letters ("y", "n") are deliberately absent — "n/a"
# tokenises to {"n", "a"} and must not read as a denial.
APPROVE_WORDS = frozenset(
    {
        "approve", "approved", "approving", "allow", "allowed", "yes", "yep",
        "yeah", "ok", "okay", "confirm", "confirmed", "proceed", "go", "lgtm",
        "ship", "accept", "accepted", "authorise", "authorize", "authorised",
        "authorized",
    }
)
DENY_WORDS = frozenset(
    {
        "deny", "denied", "disallow", "no", "nope", "not", "never", "don't",
        "dont", "reject", "rejected", "refuse", "refused", "cancel", "cancelled",
        "canceled", "stop", "abort", "block", "blocked", "veto", "halt",
    }
)

# Substring-matched, because emoji are not word characters. The base codepoint
# is enough: a variation selector or skin-tone modifier follows it.
APPROVE_EMOJI = ("\U0001f44d", "✅", "✔", "\U0001f7e2", "\U0001f197")  # 👍 ✅ ✔ 🟢 🆗
DENY_EMOJI = ("\U0001f44e", "❌", "✖", "\U0001f6d1", "\U0001f534", "⛔")  # 👎 ❌ ✖ 🛑 🔴 ⛔

INTENT_APPROVE = "approve"
INTENT_DENY = "deny"
INTENT_ANSWER = "answer"

# ---------------------------------------------------------------------------
# Reply outcomes
# ---------------------------------------------------------------------------
OUTCOME_APPROVED = "approved"
OUTCOME_DENIED = "denied"
OUTCOME_ANSWERED = "answered"
OUTCOME_IGNORED_NO_TOKEN = "ignored_no_token"
OUTCOME_IGNORED_UNVERIFIED = "ignored_unverified"
OUTCOME_IGNORED_NOT_AUTHORIZED = "ignored_not_authorized"
OUTCOME_UNKNOWN_ITEM = "unknown_item"
OUTCOME_ALREADY_RESOLVED = "already_resolved"

SETTLING_OUTCOMES = (OUTCOME_APPROVED, OUTCOME_DENIED)


def format_token(item_id: str) -> str:
    """The correlation tag that makes a reply resolvable: ``[icdev:<item_id>]``."""
    return f"[icdev:{item_id}]"


def extract_token(text: Any) -> Optional[str]:
    """The one item id named by ``text``, or ``None``.

    ``None`` for no token **and** for two or more distinct tokens. Both are
    "which item did they mean?", and the answer to that question is never a
    guess — a bare "yes" must resolve nothing rather than resolve the wrong
    thing. The same token repeated (quoted mail replies do this) is one token.
    """
    found = _TOKEN_RE.findall(str(text or ""))
    unique = set(found)
    if len(unique) != 1:
        if unique:
            logger.info("inbox_channel: reply names %d distinct items — ignoring", len(unique))
        return None
    return unique.pop()


def detect_intent(text: Any) -> str:
    """``approve`` / ``deny`` / ``answer`` for a reply body.

    The correlation token is stripped first so an item id can never contribute a
    keyword. A reply carrying BOTH an approve and a deny signal ("do not
    approve") is :data:`INTENT_ANSWER`, not a coin flip.
    """
    body = _TOKEN_RE.sub(" ", str(text or ""))
    words = set(_WORD_RE.findall(body.lower()))
    approve = bool(words & APPROVE_WORDS) or any(e in body for e in APPROVE_EMOJI)
    deny = bool(words & DENY_WORDS) or any(e in body for e in DENY_EMOJI)
    if approve and deny:
        return INTENT_ANSWER
    if approve:
        return INTENT_APPROVE
    if deny:
        return INTENT_DENY
    return INTENT_ANSWER


# ---------------------------------------------------------------------------
# Routing — session override → persona default → global default
# ---------------------------------------------------------------------------
@dataclass
class Route:
    """Where one item is mirrored to, and which tier decided it."""

    inbox: str = DEFAULT_INBOX
    channel: str = ""
    channel_user_id: str = ""
    thread_id: str = ""
    source: str = "default"

    @property
    def has_channel(self) -> bool:
        """False means in-app only — a valid configuration, not a failure."""
        return bool(self.channel and self.channel_user_id)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_ROUTE_KEYS = ("inbox", "channel", "channel_user_id", "thread_id")


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except Exception as exc:  # noqa: BLE001
        logger.warning("inbox_channel: PyYAML unavailable (%s); using defaults", exc)
        return {}
    try:
        if not path.exists():
            return {}
        with open(path, encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle)
    except Exception as exc:  # noqa: BLE001
        logger.warning("inbox_channel: could not read %s: %s", path, exc)
        return {}
    return loaded if isinstance(loaded, dict) else {}


def load_routing(path: Optional[Path] = None) -> dict[str, Any]:
    """Read ``args/approval_inbox_routing.yaml``. Missing file → empty config."""
    return _load_yaml(Path(path) if path else ROUTING_CONFIG_PATH)


def load_gateway_config(path: Optional[Path] = None) -> dict[str, Any]:
    """Read ``args/remote_gateway_config.yaml`` — channels, IL bounds, allowlist."""
    return _load_yaml(Path(path) if path else GATEWAY_CONFIG_PATH)


def _session_id() -> str:
    for key in ("ICDEV_SESSION_ID", "CLAUDE_SESSION_ID"):
        value = os.environ.get(key)
        if value:
            return value
    return ""


def _tier(config: dict[str, Any], group: str, key: str) -> dict[str, Any]:
    if not key:
        return {}
    bucket = config.get(group)
    if not isinstance(bucket, dict):
        return {}
    entry = bucket.get(key)
    return entry if isinstance(entry, dict) else {}


def resolve_route(
    *,
    session_id: str = "",
    persona: str = "",
    inbox: str = "",
    config: Optional[dict[str, Any]] = None,
) -> Route:
    """Resolve a delivery route: session override → persona default → global.

    Tiers are merged key by key, most specific last, and an empty value does not
    override — so a session may redirect only ``channel_user_id`` and inherit
    everything else. ``source`` names the most specific tier that contributed,
    which is what makes "why did this page the wrong person?" answerable.

    An explicit ``inbox`` argument wins over every tier: the caller already knows
    which queue the item was filed in.
    """
    cfg = config if config is not None else load_routing()
    sid = session_id or _session_id()
    who = persona or os.environ.get(PERSONA_ENV, "")

    merged: dict[str, Any] = {}
    source = "default"
    for tier_name, values in (
        ("default", cfg.get("default") if isinstance(cfg.get("default"), dict) else {}),
        (f"persona:{who}", _tier(cfg, "personas", who)),
        (f"session:{sid}", _tier(cfg, "sessions", sid)),
    ):
        contributed = False
        for key in _ROUTE_KEYS:
            value = values.get(key)
            if value not in (None, ""):
                merged[key] = str(value)
                contributed = True
        if contributed:
            source = tier_name

    route = Route(
        inbox=inbox or merged.get("inbox", DEFAULT_INBOX),
        channel=merged.get("channel", ""),
        channel_user_id=merged.get("channel_user_id", ""),
        thread_id=merged.get("thread_id", ""),
        source=source,
    )
    return route


def list_approvers(config: Optional[dict[str, Any]] = None) -> list[str]:
    """ICDEV user ids permitted to settle an item from a channel reply.

    Empty means "any identity that cleared all eight gates", which is deliberate
    parity with the console approver: that trusts whoever holds the terminal.
    Non-empty is enforced fail-closed by :func:`resolve_from_reply`.
    """
    cfg = config if config is not None else load_routing()
    raw = cfg.get("approvers")
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(x).strip() for x in raw if str(x).strip()]


def _delivery_setting(config: dict[str, Any], key: str, fallback: Any) -> Any:
    section = config.get("delivery")
    if not isinstance(section, dict):
        return fallback
    value = section.get(key)
    return fallback if value in (None, "") else value


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def channel_max_il(channel: str, gateway_config: Optional[dict[str, Any]] = None) -> str:
    """The destination channel's ``max_il``, defaulting to the strictest useful bound.

    An unknown channel gets ``IL2`` — the least permissive value in the filter's
    ordering — so an unconfigured destination redacts more, never less.
    """
    cfg = gateway_config if gateway_config is not None else load_gateway_config()
    channels = cfg.get("channels")
    entry = channels.get(channel) if isinstance(channels, dict) else None
    if isinstance(entry, dict):
        return str(entry.get("max_il") or "IL2")
    return "IL2"


def render_delivery(
    item: ApprovalItem,
    *,
    max_il: str = "IL2",
    dashboard_url: str = DEFAULT_DASHBOARD_URL,
    max_body_chars: int = DEFAULT_MAX_BODY_CHARS,
) -> tuple[str, bool, str]:
    """Compose the deliverable message. Returns ``(text, was_filtered, detected_il)``.

    Order matters and is load-bearing:

    1. compose title + body (both already argument-value-free by construction),
    2. run the IL response filter for ``max_il`` — a CUI marking on an IL4
       channel is replaced by the redaction notice,
    3. truncate,
    4. **then** append the ``[icdev:<id>]`` footer.

    Steps 2 and 3 can each replace or cut the body; doing them before step 4 is
    what guarantees the token survives, and a delivered message without its
    token is a message whose reply can never be correlated.
    """
    content = f"{item.title}\n\n{item.body}" if item.body else item.title

    try:
        from tools.gateway.response_filter import filter_response, truncate_response
    except Exception as exc:  # noqa: BLE001
        # Fail closed on the CONTENT, not on the delivery: without the filter we
        # cannot prove the body is safe for this channel, so send the notice
        # instead of the body. The item is still answerable in-app.
        logger.error("inbox_channel: response_filter unavailable (%s) — redacting body", exc)
        text = (
            "[REDACTED] The IL response filter is unavailable, so this approval "
            "body cannot be shown on this channel. Open it in the ICDEV dashboard."
        )
        return f"{text}\n\n{_footer(item)}", True, "unknown"

    filtered, was_filtered, detected_il = filter_response(
        content,
        max_il,
        envelope_id=item.item_id,
        dashboard_url=dashboard_url or DEFAULT_DASHBOARD_URL,
    )
    filtered = truncate_response(filtered, max_length=max(200, int(max_body_chars)))
    return f"{filtered}\n\n{_footer(item)}", was_filtered, detected_il


def _footer(item: ApprovalItem) -> str:
    """The correlation footer. Machine-generated — it carries no item content."""
    return (
        f"{format_token(item.item_id)}\n"
        "Reply `approve` or `deny` AND keep the tag above in your reply — a reply "
        "without it is ignored, never applied to whichever item is most recent."
    )


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------
@dataclass
class DeliveryResult:
    """Outcome of one mirror attempt. ``delivered=False`` is never a resolution."""

    item_id: str
    delivered: bool = False
    channel: str = ""
    channel_user_id: str = ""
    text: str = ""
    was_filtered: bool = False
    detected_il: str = ""
    skipped: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Instantiating an adapter is deferred to the gateway package so this module
# does not grow a second copy of the channel→class map.
def build_adapter(channel: str, gateway_config: Optional[dict[str, Any]] = None):
    """Build a configured channel adapter, or ``None`` if it cannot be built.

    Uses ``tools.gateway.adapters.build_adapter`` — the same registry
    ``gateway_agent`` loads from, never a second copy of the map.
    """
    cfg = gateway_config if gateway_config is not None else load_gateway_config()
    try:
        from tools.gateway.adapters import build_adapter as _build
    except Exception as exc:  # noqa: BLE001
        logger.error("inbox_channel: gateway adapters unavailable: %s", exc)
        return None
    channels = cfg.get("channels")
    channel_config = channels.get(channel, {}) if isinstance(channels, dict) else {}
    return _build(channel, channel_config if isinstance(channel_config, dict) else {})


def deliver(
    item: ApprovalItem,
    *,
    route: Optional[Route] = None,
    adapter: Any = None,
    session_id: str = "",
    persona: str = "",
    routing_config: Optional[dict[str, Any]] = None,
    gateway_config: Optional[dict[str, Any]] = None,
) -> DeliveryResult:
    """Mirror one item to its channel. Never raises; never changes item state.

    Every failure path — no route, no adapter, a raising ``send_message``, a
    ``send_message`` returning ``False`` — returns ``delivered=False`` and leaves
    the item exactly as it was. In-app is the store of record: a broken mirror
    must not lose the ask and must certainly not resolve it.
    """
    routing = routing_config if routing_config is not None else load_routing()
    resolved = route or resolve_route(
        session_id=session_id or item.session_id,
        persona=persona,
        inbox=item.inbox,
        config=routing,
    )
    result = DeliveryResult(
        item_id=item.item_id,
        channel=resolved.channel,
        channel_user_id=resolved.channel_user_id,
    )

    if not resolved.has_channel:
        result.skipped = True
        result.error = "no channel route configured — in-app only"
        logger.info("inbox_channel: %s stays in-app only (%s)", item.item_id, resolved.source)
        return result

    try:
        gw = gateway_config if gateway_config is not None else load_gateway_config()
        text, was_filtered, detected_il = render_delivery(
            item,
            max_il=channel_max_il(resolved.channel, gw),
            dashboard_url=str(_delivery_setting(routing, "dashboard_url", DEFAULT_DASHBOARD_URL)),
            max_body_chars=int(_delivery_setting(routing, "max_body_chars", DEFAULT_MAX_BODY_CHARS)),
        )
        result.text = text
        result.was_filtered = was_filtered
        result.detected_il = detected_il

        transport = adapter if adapter is not None else build_adapter(resolved.channel, gw)
        if transport is None:
            result.error = f"no adapter for channel {resolved.channel!r}"
            logger.warning("inbox_channel: %s not delivered — %s", item.item_id, result.error)
            return result

        sent = transport.send_message(resolved.channel_user_id, text, resolved.thread_id)
    except Exception as exc:  # noqa: BLE001 — a mirror never decides
        result.error = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "inbox_channel: delivery of %s failed (item stays pending): %s",
            item.item_id, result.error,
        )
        return result

    result.delivered = bool(sent)
    if not result.delivered:
        result.error = f"{resolved.channel} adapter reported the send failed"
        logger.warning("inbox_channel: %s — %s (item stays pending)", item.item_id, result.error)
    else:
        logger.info(
            "inbox_channel: %s delivered to %s:%s (filtered=%s)",
            item.item_id, resolved.channel, resolved.channel_user_id, was_filtered,
        )
    return result


def make_channel_deliverer(
    *,
    persona: str = "",
    session_id: str = "",
    adapter: Any = None,
    routing_config: Optional[dict[str, Any]] = None,
    gateway_config: Optional[dict[str, Any]] = None,
) -> Callable[[ApprovalItem], DeliveryResult]:
    """Build the ``deliver=`` callable ``make_inbox_approver`` accepts.

    ``make_inbox_approver(deliver=make_channel_deliverer(persona="ops"))`` is the
    whole wiring — the approver already treats a raising or failing deliverer as
    "the item stays pending", so this adds no new failure mode to the gate.
    """
    def _deliver(item: ApprovalItem) -> DeliveryResult:
        return deliver(
            item,
            adapter=adapter,
            persona=persona,
            session_id=session_id,
            routing_config=routing_config,
            gateway_config=gateway_config,
        )

    return _deliver


def deliver_pending(
    *,
    inbox: str = "",
    session_id: str = "",
    persona: str = "",
    limit: int = 50,
    adapter: Any = None,
) -> list[DeliveryResult]:
    """Mirror every pending item in a queue. Best-effort, one result per item."""
    items = list_pending(inbox=inbox or None, session_id=session_id or None, limit=limit)
    return [deliver(x, adapter=adapter, persona=persona) for x in items]


# ---------------------------------------------------------------------------
# Reply resolution
# ---------------------------------------------------------------------------
@dataclass
class ReplyResolution:
    """What one inbound reply did. ``outcome`` is the only thing to branch on."""

    outcome: str
    item_id: str = ""
    intent: str = ""
    answer: str = ""
    actor: str = ""
    channel: str = ""
    reason: str = ""
    item: Optional[ApprovalItem] = field(default=None, repr=False)

    @property
    def settled(self) -> bool:
        """True only when this reply moved an item to a terminal state."""
        return self.outcome in SETTLING_OUTCOMES

    @property
    def approved(self) -> bool:
        return self.outcome == OUTCOME_APPROVED

    def to_dict(self) -> dict[str, Any]:
        payload = {k: v for k, v in asdict(self).items() if k != "item"}
        payload["item"] = self.item.to_dict() if self.item else None
        payload["settled"] = self.settled
        return payload


def _attr(obj: Any, name: str, default: Any = "") -> Any:
    if isinstance(obj, dict):
        value = obj.get(name, default)
    else:
        value = getattr(obj, name, default)
    return default if value is None else value


def _gates_passed(reply: Any) -> bool:
    """True only when all eight gates are recorded as passed on the envelope."""
    results = _attr(reply, "gate_results", {}) or {}
    if not isinstance(results, dict):
        return False
    return all(results.get(gate) is True for gate in REQUIRED_GATES)


def _reply_digest(text: str) -> str:
    """SHA-256 of the reply body.

    The words themselves are NOT written to the append-only decision log. A
    human typing into Slack can paste anything, and ``agent_approval_log`` is a
    row that can never be deleted — the digest keeps the decision verifiable
    against the channel's own record without copying it here.
    """
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _audit_ignored(reply: Any, outcome: str, detail: str) -> None:
    """Record a refused inbound reply. Best-effort — never blocks the refusal."""
    try:
        from tools.audit.audit_logger import log_event
    except Exception:  # noqa: BLE001
        return
    try:
        log_event(
            event_type="remote_command_rejected",
            actor=str(_attr(reply, "icdev_user_id") or _attr(reply, "channel_user_id") or "unknown"),
            action=f"Approval reply ignored ({outcome}): {detail}",
            details=str(
                {
                    "outcome": outcome,
                    "channel": str(_attr(reply, "channel")),
                    "envelope_id": str(_attr(reply, "id")),
                    "reply_sha256": _reply_digest(str(_attr(reply, "raw_text"))),
                }
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("inbox_channel: audit of ignored reply failed: %s", exc)


def resolve_from_reply(
    reply: Any,
    *,
    require_gates: bool = True,
    routing_config: Optional[dict[str, Any]] = None,
) -> ReplyResolution:
    """Turn one inbound reply into a resolution — or into nothing at all.

    ``reply`` is a :class:`~tools.gateway.event_envelope.CommandEnvelope` (or any
    object/dict exposing ``raw_text``, ``icdev_user_id``, ``channel`` and
    ``gate_results``) that has already been through
    :func:`tools.gateway.security_chain.run_security_chain`.

    The order of the checks is the safety property:

    1. **all eight gates passed** — otherwise ``ignored_unverified``. This module
       is not a way around the chain,
    2. **exactly one ``[icdev:<id>]`` token** — otherwise ``ignored_no_token``.
       Nothing is resolved. A bare "yes" is not applied to the most recent
       pending item, because guessing which approval it meant is how the wrong
       thing gets approved,
    3. **the actor may answer** — otherwise ``ignored_not_authorized``,
    4. **the item exists and is pending** — otherwise ``unknown_item`` /
       ``already_resolved``. A second reply naming a settled item is a no-op, not
       a contradicting second decision,
    5. **intent** — ``approve``/``deny`` settle it; anything else is ``answered``
       and the item stays pending.
    """
    text = str(_attr(reply, "raw_text") or "")
    channel = str(_attr(reply, "channel") or "")
    actor = str(_attr(reply, "icdev_user_id") or "")

    if require_gates and not _gates_passed(reply):
        detail = "envelope did not clear all 8 security gates"
        logger.warning("inbox_channel: reply ignored — %s", detail)
        _audit_ignored(reply, OUTCOME_IGNORED_UNVERIFIED, detail)
        return ReplyResolution(
            outcome=OUTCOME_IGNORED_UNVERIFIED, channel=channel, actor=actor, reason=detail
        )

    item_id = extract_token(text)
    if not item_id:
        detail = "no single [icdev:<item_id>] correlation token in the reply"
        logger.info("inbox_channel: reply ignored — %s", detail)
        _audit_ignored(reply, OUTCOME_IGNORED_NO_TOKEN, detail)
        return ReplyResolution(
            outcome=OUTCOME_IGNORED_NO_TOKEN, channel=channel, actor=actor, reason=detail
        )

    approvers = list_approvers(routing_config)
    if approvers and actor not in approvers:
        detail = f"{actor or 'unresolved identity'} is not in the approvers allowlist"
        logger.warning("inbox_channel: reply to %s ignored — %s", item_id, detail)
        _audit_ignored(reply, OUTCOME_IGNORED_NOT_AUTHORIZED, detail)
        return ReplyResolution(
            outcome=OUTCOME_IGNORED_NOT_AUTHORIZED,
            item_id=item_id,
            channel=channel,
            actor=actor,
            reason=detail,
        )

    item = get(item_id)
    if item is None:
        detail = f"no approval item {item_id}"
        logger.info("inbox_channel: reply ignored — %s", detail)
        _audit_ignored(reply, OUTCOME_UNKNOWN_ITEM, detail)
        return ReplyResolution(
            outcome=OUTCOME_UNKNOWN_ITEM,
            item_id=item_id,
            channel=channel,
            actor=actor,
            reason=detail,
        )

    intent = detect_intent(text)

    if not item.is_pending:
        detail = f"{item_id} is already {item.state} ({item.resolution or 'no resolution'})"
        logger.info("inbox_channel: reply is a no-op — %s", detail)
        return ReplyResolution(
            outcome=OUTCOME_ALREADY_RESOLVED,
            item_id=item_id,
            intent=intent,
            channel=channel,
            actor=actor,
            reason=detail,
            item=item,
        )

    if intent == INTENT_ANSWER:
        detail = "reply carried no unambiguous approve/deny signal; item stays pending"
        logger.info("inbox_channel: %s answered but not settled — %s", item_id, detail)
        return ReplyResolution(
            outcome=OUTCOME_ANSWERED,
            item_id=item_id,
            intent=intent,
            answer=text,
            channel=channel,
            actor=actor,
            reason=detail,
            item=item,
        )

    approved = intent == INTENT_APPROVE
    settled = resolve(
        item_id,
        approved=approved,
        resolved_by=actor,
        reason=(
            f"{'approved' if approved else 'denied'} by {actor or 'unknown'} via a "
            f"{channel or 'channel'} reply carrying {format_token(item_id)} "
            f"(reply sha256={_reply_digest(text)})"
        ),
    )
    if settled is None:
        # Lost the race with another resolver (a CLI, the expiry sweep, a second
        # reply). The store's conditional UPDATE is what makes that safe.
        current = get(item_id)
        detail = f"{item_id} was settled by someone else before this reply landed"
        logger.info("inbox_channel: %s", detail)
        return ReplyResolution(
            outcome=OUTCOME_ALREADY_RESOLVED,
            item_id=item_id,
            intent=intent,
            channel=channel,
            actor=actor,
            reason=detail,
            item=current,
        )

    _wake(item_id)
    logger.info(
        "inbox_channel: %s %s by %s via %s", item_id, settled.resolution, actor or "unknown", channel
    )
    return ReplyResolution(
        outcome=OUTCOME_APPROVED if approved else OUTCOME_DENIED,
        item_id=item_id,
        intent=intent,
        channel=channel,
        actor=actor,
        reason=f"settled {settled.resolution} from a {channel or 'channel'} reply",
        item=settled,
    )


def _wake(item_id: str) -> None:
    """Nudge an in-process waiter. Optimisation only — the DB poll is load-bearing."""
    try:
        from tools.agent_runtime.inbox_approver import wake

        wake(item_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("inbox_channel: wake(%s) skipped: %s", item_id, exc)


# ---------------------------------------------------------------------------
# Gateway integration
# ---------------------------------------------------------------------------
def is_approval_reply(reply: Any) -> bool:
    """True when the raw text carries exactly one correlation token."""
    return extract_token(_attr(reply, "raw_text")) is not None


def prepare_approval_reply_envelope(envelope: Any) -> bool:
    """Rewrite an inbound approval reply to the ``icdev-approve`` command.

    Same shape as :func:`tools.gateway.agent_mode.prepare_agent_envelope` and for
    the same reason: the envelope is normalised BEFORE the chain so all eight
    gates run on it unchanged. No synthetic allowlist entry is injected — the
    ``icdev-approve`` entry lives in ``args/remote_gateway_config.yaml``, so if an
    operator has not permitted it on this channel the classification and RBAC
    gates reject the reply, which is the correct fail-closed outcome.

    Returns True when the envelope was rewritten.
    """
    item_id = extract_token(_attr(envelope, "raw_text"))
    if not item_id:
        return False
    try:
        envelope.args = dict(getattr(envelope, "args", {}) or {})
        envelope.args["approval_item_id"] = item_id
        envelope.command = APPROVAL_COMMAND
    except Exception as exc:  # noqa: BLE001
        logger.warning("inbox_channel: could not prepare approval reply envelope: %s", exc)
        return False
    return True


_ACK = {
    OUTCOME_APPROVED: "Approved {token}. The waiting action may proceed.",
    OUTCOME_DENIED: "Denied {token}. The action will not run.",
    OUTCOME_ANSWERED: (
        "Noted, but {token} is still pending — it needs an explicit `approve` or "
        "`deny`. It will expire on its own clock, and an expiry is a denial."
    ),
    OUTCOME_ALREADY_RESOLVED: "{token} was already settled. Nothing changed.",
    OUTCOME_UNKNOWN_ITEM: "There is no approval item {token}.",
    OUTCOME_IGNORED_NOT_AUTHORIZED: "You are not authorised to answer {token}.",
    OUTCOME_IGNORED_UNVERIFIED: "That reply could not be verified, so it was ignored.",
    OUTCOME_IGNORED_NO_TOKEN: (
        "Ignored: no [icdev:<item_id>] tag in that reply. Keep the tag from the "
        "original message — a reply without one is never applied to a pending item."
    ),
}


def acknowledgement(resolution: ReplyResolution, *, max_il: str = "IL2") -> str:
    """The text sent back to the channel. Runs through the IL response filter.

    It names an item id and an outcome and nothing else, so there is nothing for
    the filter to redact — it runs anyway because "outbound text goes through
    response_filter" is easier to keep true than to keep track of exceptions to.
    """
    token = format_token(resolution.item_id) if resolution.item_id else "that item"
    text = _ACK.get(resolution.outcome, "Reply processed.").format(token=token)
    try:
        from tools.gateway.response_filter import filter_response

        filtered, _was, _il = filter_response(text, max_il, envelope_id=resolution.item_id)
        return filtered
    except Exception:  # noqa: BLE001
        return text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Deliver approval-inbox items to a channel and inspect replies "
        "(agov-inbox-03)."
    )
    parser.add_argument("--route", action="store_true", help="show the resolved route")
    parser.add_argument("--deliver", metavar="ITEM_ID", help="mirror one item to its channel")
    parser.add_argument(
        "--deliver-pending", action="store_true", help="mirror every pending item"
    )
    parser.add_argument(
        "--parse",
        metavar="TEXT",
        help="show the token and intent for a reply WITHOUT resolving anything",
    )
    parser.add_argument("--inbox", default="", help="queue name")
    parser.add_argument("--persona", default="", help="persona whose route to use")
    parser.add_argument("--session-id", default="", help="session whose override to use")
    parser.add_argument("--limit", type=int, default=50, help="with --deliver-pending")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    def emit(payload: Any) -> None:
        if args.json:
            print(json.dumps(payload, indent=2, default=str))
        else:
            print(payload)

    if args.parse is not None:
        token = extract_token(args.parse)
        emit(
            {
                "item_id": token,
                "intent": detect_intent(args.parse),
                "would_resolve": bool(token) and detect_intent(args.parse) != INTENT_ANSWER,
                "note": "--parse never resolves anything",
            }
        )
        return 0

    if args.route:
        emit(
            resolve_route(
                session_id=args.session_id, persona=args.persona, inbox=args.inbox
            ).to_dict()
        )
        return 0

    if args.deliver:
        item = get(args.deliver)
        if item is None:
            emit({"error": f"no item {args.deliver}"})
            return 1
        result = deliver(item, persona=args.persona, session_id=args.session_id)
        emit(result.to_dict())
        return 0 if (result.delivered or result.skipped) else 1

    if args.deliver_pending:
        results = deliver_pending(
            inbox=args.inbox,
            session_id=args.session_id,
            persona=args.persona,
            limit=args.limit,
        )
        emit(
            {
                "delivered": [r.item_id for r in results if r.delivered],
                "skipped": [r.item_id for r in results if r.skipped],
                "failed": [
                    {"item_id": r.item_id, "error": r.error}
                    for r in results
                    if not r.delivered and not r.skipped
                ],
                "count": len(results),
            }
        )
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
