# CUI // SP-CTI
"""Channel delivery and reply resolution for the approval inbox (agov-inbox-03).

The acceptance properties, each with a class here:

  1. A reply carrying ``[icdev:<id>]`` resolves THAT item and only that item.
  2. A reply with no token resolves NOTHING. Not "the most recent pending item",
     not "the only pending item" — nothing. Guessing which approval a bare
     "yes" meant is how the wrong irreversible action gets approved, so the
     test seeds a second pending item and asserts both are untouched.
  3. A reply whose token names an already-settled item is a no-op: no second,
     contradicting decision.
  4. A delivery failure leaves the item ``pending``. In-app is the store of
     record; a mirror that cannot be written must not lose or resolve the ask.
  5. The delivered body goes through ``response_filter`` — a seeded CUI marker
     is redacted on a channel that may not carry it, and the correlation token
     survives the redaction, because a delivered message whose token was
     redacted away can never be answered.
  6. No new HTTP client is introduced. Asserted against the module source, not
     by inspection, because the adapters' existing stdlib transport is the whole
     reason this card is small.

The schema comes from the migration's own DDL, so a column added there and not
here fails in this file rather than at runtime inside a swallowed exception.
"""
from __future__ import annotations

import importlib
import importlib.util
import inspect
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

from tests._sql_compat import translating
from tools.agent_runtime.approval_inbox import (
    STATE_PENDING,
    STATE_RESOLVED,
    TABLE,
    ApprovalItem,
    enqueue,
    get,
    resolve,
)
from tools.agent_runtime import inbox_channel as ch

REPO_ROOT = Path(__file__).resolve().parents[1]

# Seeded into an item body. If this string reaches a channel that may not carry
# CUI, the response filter was not applied.
CUI_MARKER = "CUI // SP-CTI"

TELEGRAM_ROUTING = {
    "default": {
        "inbox": "default",
        "channel": "telegram",
        "channel_user_id": "123456789",
    },
    "delivery": {"max_body_chars": 3000, "dashboard_url": "/agent-approvals"},
}

# Telegram is IL4 in the real gateway config; TestChannelIsIL4 pins that so this
# stub cannot quietly disagree with the file the product reads.
GATEWAY = {"channels": {"telegram": {"max_il": "IL4"}, "mattermost": {"max_il": "IL6"}}}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _approval_items_ddl() -> str:
    return (
        REPO_ROOT / "tools" / "db" / "migrations"
        / "20260809203855_agov_approval_items" / "up.sql"
    ).read_text(encoding="utf-8")


def _approval_log_ddl() -> str:
    path = (
        REPO_ROOT / "tools" / "db" / "migrations"
        / "20260803002224_agent_approval_log" / "up.py"
    )
    spec = importlib.util.spec_from_file_location("_m_agent_approval_log_ch", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module._DDL


def _module(name: str):
    """The module object the product code actually resolves.

    ``tools.x`` in ``sys.modules`` is the compat shim's entry and
    ``import tools.x`` can bind the canonical ``icdev.tools.x`` — two different
    objects. The product imports these from inside its functions, so patching
    the wrong one would make every assertion below assert its own no-op.
    """
    importlib.import_module(name)
    return sys.modules[name]


@pytest.fixture
def inbox_db(monkeypatch, tmp_path):
    """Both real tables in one file DB, behind the production ``%s`` translation."""
    db_path = tmp_path / "inbox_channel.db"
    boot = sqlite3.connect(str(db_path))
    boot.executescript(_approval_items_ddl())
    boot.executescript(_approval_log_ddl())
    boot.commit()
    boot.close()

    def _open(*_a, **_k):
        return translating(sqlite3.connect(str(db_path), timeout=30.0))

    storage = _module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", _open)
    monkeypatch.setattr(storage, "table_exists", lambda _c, _t: True)
    monkeypatch.setenv("ICDEV_APPROVAL_ACTOR", "test-operator")
    monkeypatch.delenv(ch.PERSONA_ENV, raising=False)
    monkeypatch.delenv("ICDEV_SESSION_ID", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    yield db_path


@pytest.fixture
def audit_calls(monkeypatch):
    """Capture ``audit_logger.log_event`` instead of writing to a real audit table."""
    calls: list[dict[str, Any]] = []
    module = _module("tools.audit.audit_logger")
    monkeypatch.setattr(module, "log_event", lambda **kw: calls.append(kw))
    return calls


class FakeAdapter:
    """A ``BaseChannelAdapter``-shaped stub. Records what it was asked to send."""

    def __init__(self, *, result: bool = True, raises: BaseException | None = None):
        self.result, self.raises = result, raises
        self.sent: list[tuple[str, str, str]] = []

    def send_message(self, channel_user_id: str, text: str, thread_id: str = "") -> bool:
        self.sent.append((channel_user_id, text, thread_id))
        if self.raises is not None:
            raise self.raises
        return self.result


def _pending(**overrides) -> ApprovalItem:
    payload = {
        "tool_name": "git_push",
        "tier": "irreversible",
        "title": "[IRREVERSIBLE] git_push",
        "body": f"Tool: git_push\nTier: irreversible\nClassification: {CUI_MARKER}",
        "inbox": "default",
        "tool_input": {"remote": "origin", "branch": "main"},
    }
    payload.update(overrides)
    return enqueue(**payload)


def _envelope(text: str, *, gates: bool = True, actor: str = "jane.doe", channel: str = "slack"):
    from tools.gateway.event_envelope import CommandEnvelope

    env = CommandEnvelope(
        channel=channel,
        channel_user_id="U123",
        raw_text=text,
        command="icdev-approve",
    )
    env.icdev_user_id = actor
    if gates:
        env.gate_results = {gate: True for gate in ch.REQUIRED_GATES}
    return env


def _states(db_path: Path) -> dict[str, str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {r[0]: r[1] for r in conn.execute(f"SELECT item_id, state FROM {TABLE}")}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The correlation token
# ---------------------------------------------------------------------------
class TestCorrelationToken:
    def test_round_trips(self):
        assert ch.extract_token(ch.format_token("ai-abc123")) == "ai-abc123"

    @pytest.mark.parametrize(
        "text",
        [
            "approve [icdev:ai-1]",
            "[icdev:ai-1] yes please",
            "yes\n\n> On Monday ICDEV wrote:\n> [icdev:ai-1]",
            "APPROVE [ICDEV: ai-1 ]",
        ],
    )
    def test_found_anywhere_in_the_body(self, text):
        assert ch.extract_token(text) == "ai-1"

    def test_the_same_token_repeated_is_one_token(self):
        # Quoted mail replies do this on every hop.
        assert ch.extract_token("yes [icdev:ai-1]\n> [icdev:ai-1]") == "ai-1"

    def test_two_distinct_tokens_are_refused(self):
        # Ambiguity is refused, never guessed — the same rule as no token at all.
        assert ch.extract_token("approve [icdev:ai-1] and [icdev:ai-2]") is None

    @pytest.mark.parametrize("text", ["", None, "yes", "approve please", "[icdev:]", "icdev:ai-1"])
    def test_absent_or_malformed_is_none(self, text):
        assert ch.extract_token(text) is None


class TestIntent:
    @pytest.mark.parametrize(
        "text", ["approve", "yes", "LGTM", "ok, proceed", "approve [icdev:ai-1]", "👍", "✅"]
    )
    def test_approve(self, text):
        assert ch.detect_intent(text) == ch.INTENT_APPROVE

    @pytest.mark.parametrize("text", ["deny", "no", "reject this", "stop", "👎", "❌"])
    def test_deny(self, text):
        assert ch.detect_intent(text) == ch.INTENT_DENY

    @pytest.mark.parametrize(
        "text",
        [
            "what does this touch?",
            "do not approve",          # both signals — refused, not a coin flip
            "yes 👎",
            "",
        ],
    )
    def test_ambiguous_or_free_text_is_an_answer(self, text):
        assert ch.detect_intent(text) == ch.INTENT_ANSWER

    def test_an_item_id_cannot_contribute_a_keyword(self):
        # The token is stripped before the keyword scan; "no" inside an id must
        # not read as a denial.
        assert ch.detect_intent("[icdev:ai-nope-no-deny]") == ch.INTENT_ANSWER


# ---------------------------------------------------------------------------
# Routing: session override → persona default → global default
# ---------------------------------------------------------------------------
class TestRouting:
    CONFIG = {
        "default": {"inbox": "default", "channel": "internal_chat", "channel_user_id": "ops-room"},
        "personas": {"overnight": {"inbox": "ops", "channel": "slack", "channel_user_id": "C1"}},
        "sessions": {"sess-1": {"channel_user_id": "U-oncall"}},
    }

    def test_global_default_when_nothing_matches(self):
        route = ch.resolve_route(config=self.CONFIG)
        assert (route.channel, route.channel_user_id, route.source) == (
            "internal_chat", "ops-room", "default",
        )

    def test_persona_overrides_default(self):
        route = ch.resolve_route(persona="overnight", config=self.CONFIG)
        assert (route.inbox, route.channel, route.channel_user_id) == ("ops", "slack", "C1")
        assert route.source == "persona:overnight"

    def test_session_overrides_persona(self):
        route = ch.resolve_route(persona="overnight", session_id="sess-1", config=self.CONFIG)
        assert route.source == "session:sess-1"
        assert route.channel_user_id == "U-oncall"
        # Merged key by key: the session named only a recipient, so the
        # persona's channel and inbox are still in force.
        assert (route.channel, route.inbox) == ("slack", "ops")

    def test_unknown_persona_falls_through_to_default(self):
        route = ch.resolve_route(persona="nobody", config=self.CONFIG)
        assert route.channel == "internal_chat"

    def test_explicit_inbox_argument_wins(self):
        assert ch.resolve_route(inbox="urgent", config=self.CONFIG).inbox == "urgent"

    def test_no_channel_means_in_app_only_not_an_error(self):
        route = ch.resolve_route(config={"default": {"inbox": "default"}})
        assert route.has_channel is False

    def test_persona_env_is_the_fallback(self, monkeypatch):
        monkeypatch.setenv(ch.PERSONA_ENV, "overnight")
        assert ch.resolve_route(config=self.CONFIG).channel == "slack"

    def test_shipped_config_parses_and_ships_no_channel(self):
        # The repo default must be in-app only: shipping a real destination
        # would page a stranger on first install.
        route = ch.resolve_route(config=ch.load_routing())
        assert route.has_channel is False
        assert ch.list_approvers(ch.load_routing()) == []


# ---------------------------------------------------------------------------
# Rendering: the IL filter runs, and the token survives it
# ---------------------------------------------------------------------------
class TestRendering:
    def test_seeded_cui_marker_is_redacted_on_an_il4_channel(self):
        item = ApprovalItem(
            item_id="ai-1", origin="sag", tool_name="git_push", tier="irreversible",
            title="[IRREVERSIBLE] git_push", body=f"Classification: {CUI_MARKER}",
        )
        text, was_filtered, detected_il = ch.render_delivery(item, max_il="IL4")
        assert was_filtered is True
        assert detected_il == "IL5"
        assert CUI_MARKER not in text
        assert "[REDACTED]" in text

    def test_the_token_survives_redaction(self):
        # The filter replaces the whole body. If the footer were inside what it
        # replaced, the delivered message could never be answered.
        item = ApprovalItem(
            item_id="ai-1", origin="sag", tool_name="git_push", tier="irreversible",
            title="[IRREVERSIBLE] git_push", body=f"Classification: {CUI_MARKER}",
        )
        text, _filtered, _il = ch.render_delivery(item, max_il="IL4")
        assert "[icdev:ai-1]" in text
        assert ch.extract_token(text) == "ai-1"

    def test_the_token_survives_truncation(self):
        item = ApprovalItem(
            item_id="ai-1", origin="sag", tool_name="t", tier="irreversible",
            title="t", body="x" * 50_000,
        )
        text, _filtered, _il = ch.render_delivery(item, max_il="IL6", max_body_chars=500)
        assert ch.extract_token(text) == "ai-1"
        assert "truncated" in text

    def test_a_channel_that_may_carry_cui_keeps_the_body(self):
        item = ApprovalItem(
            item_id="ai-1", origin="sag", tool_name="git_push", tier="irreversible",
            title="[IRREVERSIBLE] git_push", body=f"Classification: {CUI_MARKER}",
        )
        text, was_filtered, _il = ch.render_delivery(item, max_il="IL6")
        assert was_filtered is False
        assert CUI_MARKER in text

    def test_an_unknown_channel_gets_the_strictest_bound(self):
        # Unconfigured must redact more, never less.
        assert ch.channel_max_il("no-such-channel", GATEWAY) == "IL2"


class TestChannelIsIL4:
    def test_telegram_is_il4_in_the_shipped_gateway_config(self):
        # Pins the premise of TestRendering against the file the product reads.
        assert ch.channel_max_il("telegram", ch.load_gateway_config()) == "IL4"

    def test_the_approval_reply_command_is_allowlisted(self):
        entries = ch.load_gateway_config().get("command_allowlist", [])
        entry = next(e for e in entries if e.get("command") == ch.APPROVAL_COMMAND)
        # IL2 because the reply carries no output of its own — that keeps it
        # workable on an IL4 channel, which is where an unattended run pages.
        assert entry["max_il"] == "IL2"
        assert "telegram" in entry["channels"]


# ---------------------------------------------------------------------------
# Delivery failure never loses or resolves the item
# ---------------------------------------------------------------------------
class TestDeliveryFailureLeavesItemPending:
    def test_a_successful_delivery_sends_the_rendered_text(self, inbox_db):
        item = _pending()
        adapter = FakeAdapter()
        result = ch.deliver(
            item, adapter=adapter, routing_config=TELEGRAM_ROUTING, gateway_config=GATEWAY
        )
        assert result.delivered is True
        assert len(adapter.sent) == 1
        recipient, text, _thread = adapter.sent[0]
        assert recipient == "123456789"
        assert ch.extract_token(text) == item.item_id
        # Seeded CUI, IL4 channel: the filter ran on the way out.
        assert CUI_MARKER not in text
        assert result.was_filtered is True
        assert get(item.item_id).state == STATE_PENDING

    def test_adapter_returning_false_leaves_it_pending(self, inbox_db):
        item = _pending()
        result = ch.deliver(
            item, adapter=FakeAdapter(result=False),
            routing_config=TELEGRAM_ROUTING, gateway_config=GATEWAY,
        )
        assert result.delivered is False
        assert result.error
        assert get(item.item_id).state == STATE_PENDING

    def test_adapter_raising_leaves_it_pending_and_does_not_propagate(self, inbox_db):
        item = _pending()
        result = ch.deliver(
            item, adapter=FakeAdapter(raises=RuntimeError("slack 503")),
            routing_config=TELEGRAM_ROUTING, gateway_config=GATEWAY,
        )
        assert result.delivered is False
        assert "slack 503" in result.error
        assert get(item.item_id).state == STATE_PENDING

    def test_no_route_is_skipped_not_failed_and_stays_pending(self, inbox_db):
        item = _pending()
        result = ch.deliver(
            item, routing_config={"default": {"inbox": "default"}}, gateway_config=GATEWAY
        )
        assert (result.delivered, result.skipped) == (False, True)
        assert get(item.item_id).state == STATE_PENDING

    def test_an_unbuildable_channel_stays_pending(self, inbox_db):
        item = _pending()
        result = ch.deliver(
            item,
            routing_config={"default": {"channel": "no-such-channel", "channel_user_id": "x"}},
            gateway_config=GATEWAY,
        )
        assert result.delivered is False
        assert "no adapter" in result.error
        assert get(item.item_id).state == STATE_PENDING

    def test_the_approver_deliverer_seam_takes_this_shape(self, inbox_db):
        # make_inbox_approver(deliver=...) is the whole wiring; it must accept
        # the callable this module builds without an adapter shim.
        adapter = FakeAdapter()
        deliverer = ch.make_channel_deliverer(
            adapter=adapter, routing_config=TELEGRAM_ROUTING, gateway_config=GATEWAY
        )
        item = _pending()
        assert deliverer(item).delivered is True


# ---------------------------------------------------------------------------
# 1. A token resolves that item and only that item
# ---------------------------------------------------------------------------
class TestReplyResolvesExactlyOneItem:
    def test_approve_settles_the_named_item_only(self, inbox_db):
        target, bystander = _pending(), _pending()

        result = ch.resolve_from_reply(_envelope(f"approve {ch.format_token(target.item_id)}"))

        assert result.outcome == ch.OUTCOME_APPROVED
        assert result.settled is True
        assert result.item_id == target.item_id
        states = _states(inbox_db)
        assert states[target.item_id] == STATE_RESOLVED
        assert states[bystander.item_id] == STATE_PENDING

    def test_deny_settles_the_named_item_only(self, inbox_db):
        target, bystander = _pending(), _pending()

        result = ch.resolve_from_reply(_envelope(f"deny {ch.format_token(target.item_id)}"))

        assert result.outcome == ch.OUTCOME_DENIED
        assert get(target.item_id).is_approved is False
        assert get(bystander.item_id).state == STATE_PENDING

    def test_the_resolver_is_the_bound_identity_not_the_channel_user(self, inbox_db):
        item = _pending()
        ch.resolve_from_reply(_envelope(f"yes {ch.format_token(item.item_id)}", actor="jane.doe"))
        assert get(item.item_id).resolved_by == "jane.doe"

    def test_an_emoji_reply_resolves(self, inbox_db):
        item = _pending()
        result = ch.resolve_from_reply(_envelope(f"👍 {ch.format_token(item.item_id)}"))
        assert result.outcome == ch.OUTCOME_APPROVED

    def test_a_decision_row_is_written(self, inbox_db):
        item = _pending()
        ch.resolve_from_reply(_envelope(f"approve {ch.format_token(item.item_id)}"))
        conn = sqlite3.connect(str(inbox_db))
        try:
            rows = conn.execute(
                "SELECT actor, reason, input_sha256 FROM agent_approval_log "
                "WHERE decision = 'approved'"
            ).fetchall()
        finally:
            conn.close()
        assert len(rows) == 1
        actor, reason, input_sha256 = rows[0]
        assert actor == "jane.doe"
        # Carried from enqueue: the resolver never saw the arguments.
        assert input_sha256
        # The reply's own words are NOT copied into a row that can never be
        # deleted; only a digest that keeps it verifiable against the channel.
        assert "sha256=" in reason


# ---------------------------------------------------------------------------
# 2. No token resolves NOTHING
# ---------------------------------------------------------------------------
class TestReplyWithNoTokenResolvesNothing:
    def test_a_bare_yes_is_ignored_even_with_exactly_one_pending_item(self, inbox_db):
        only_one = _pending()

        result = ch.resolve_from_reply(_envelope("yes"))

        assert result.outcome == ch.OUTCOME_IGNORED_NO_TOKEN
        assert result.settled is False
        assert get(only_one.item_id).state == STATE_PENDING

    def test_a_bare_yes_is_not_applied_to_the_most_recent_item(self, inbox_db):
        older, newer = _pending(), _pending()

        ch.resolve_from_reply(_envelope("approve!"))

        assert set(_states(inbox_db).values()) == {STATE_PENDING}
        assert get(older.item_id).state == STATE_PENDING
        assert get(newer.item_id).state == STATE_PENDING

    def test_two_distinct_tokens_resolve_nothing(self, inbox_db):
        first, second = _pending(), _pending()
        text = f"approve {ch.format_token(first.item_id)} {ch.format_token(second.item_id)}"

        assert ch.resolve_from_reply(_envelope(text)).outcome == ch.OUTCOME_IGNORED_NO_TOKEN
        assert set(_states(inbox_db).values()) == {STATE_PENDING}

    def test_an_ignored_reply_is_audited(self, inbox_db, audit_calls):
        _pending()
        ch.resolve_from_reply(_envelope("yes"))
        assert any(c.get("event_type") == "remote_command_rejected" for c in audit_calls)

    def test_a_reply_that_did_not_clear_the_gates_resolves_nothing(self, inbox_db):
        item = _pending()
        env = _envelope(f"approve {ch.format_token(item.item_id)}", gates=False)

        result = ch.resolve_from_reply(env)

        assert result.outcome == ch.OUTCOME_IGNORED_UNVERIFIED
        assert get(item.item_id).state == STATE_PENDING

    @pytest.mark.parametrize("missing", ch.REQUIRED_GATES)
    def test_every_single_gate_is_load_bearing(self, inbox_db, missing):
        item = _pending()
        env = _envelope(f"approve {ch.format_token(item.item_id)}")
        env.gate_results[missing] = False

        assert ch.resolve_from_reply(env).outcome == ch.OUTCOME_IGNORED_UNVERIFIED
        assert get(item.item_id).state == STATE_PENDING

    def test_an_actor_outside_the_approvers_allowlist_resolves_nothing(self, inbox_db):
        item = _pending()
        result = ch.resolve_from_reply(
            _envelope(f"approve {ch.format_token(item.item_id)}", actor="mallory"),
            routing_config={"approvers": ["jane.doe"]},
        )
        assert result.outcome == ch.OUTCOME_IGNORED_NOT_AUTHORIZED
        assert get(item.item_id).state == STATE_PENDING

    def test_a_token_naming_no_item_resolves_nothing(self, inbox_db):
        untouched = _pending()
        result = ch.resolve_from_reply(_envelope("approve [icdev:ai-doesnotexist]"))
        assert result.outcome == ch.OUTCOME_UNKNOWN_ITEM
        assert get(untouched.item_id).state == STATE_PENDING

    def test_free_text_does_not_approve_and_leaves_it_pending(self, inbox_db):
        item = _pending()
        result = ch.resolve_from_reply(
            _envelope(f"hold on, what repo is that? {ch.format_token(item.item_id)}")
        )
        assert result.outcome == ch.OUTCOME_ANSWERED
        assert result.settled is False
        assert result.answer.startswith("hold on")
        assert get(item.item_id).state == STATE_PENDING


# ---------------------------------------------------------------------------
# 3. A token naming a settled item is a no-op
# ---------------------------------------------------------------------------
class TestAlreadyResolvedIsANoOp:
    def test_a_second_contradicting_reply_changes_nothing(self, inbox_db):
        item = _pending()
        ch.resolve_from_reply(_envelope(f"deny {ch.format_token(item.item_id)}"))
        before = get(item.item_id)

        result = ch.resolve_from_reply(_envelope(f"approve {ch.format_token(item.item_id)}"))

        assert result.outcome == ch.OUTCOME_ALREADY_RESOLVED
        assert result.settled is False
        after = get(item.item_id)
        assert (after.state, after.resolution, after.resolved_at) == (
            before.state, before.resolution, before.resolved_at,
        )

    def test_no_second_decision_row_is_written(self, inbox_db):
        item = _pending()
        ch.resolve_from_reply(_envelope(f"approve {ch.format_token(item.item_id)}"))
        ch.resolve_from_reply(_envelope(f"deny {ch.format_token(item.item_id)}"))

        conn = sqlite3.connect(str(inbox_db))
        try:
            count = conn.execute("SELECT COUNT(*) FROM agent_approval_log").fetchone()[0]
        finally:
            conn.close()
        assert count == 1

    def test_a_reply_to_an_item_settled_elsewhere_is_a_no_op(self, inbox_db):
        # The CLI, the expiry sweep or the dashboard got there first.
        item = _pending()
        resolve(item.item_id, approved=False, resolved_by="expiry-sweep")

        result = ch.resolve_from_reply(_envelope(f"approve {ch.format_token(item.item_id)}"))

        assert result.outcome == ch.OUTCOME_ALREADY_RESOLVED
        assert get(item.item_id).is_approved is False


# ---------------------------------------------------------------------------
# Gateway wiring
# ---------------------------------------------------------------------------
class TestGatewayWiring:
    def test_a_reply_is_normalised_to_the_allowlisted_command(self):
        env = _envelope("approve [icdev:ai-1]")
        env.command = "approve"
        assert ch.prepare_approval_reply_envelope(env) is True
        assert env.command == ch.APPROVAL_COMMAND
        assert env.args["approval_item_id"] == "ai-1"

    def test_an_ordinary_message_is_left_alone(self):
        env = _envelope("icdev-status")
        env.command = "icdev-status"
        assert ch.prepare_approval_reply_envelope(env) is False
        assert env.command == "icdev-status"

    def test_the_acknowledgement_names_the_item(self):
        res = ch.ReplyResolution(outcome=ch.OUTCOME_APPROVED, item_id="ai-1")
        assert "[icdev:ai-1]" in ch.acknowledgement(res)

    def test_the_no_token_acknowledgement_explains_the_refusal(self):
        text = ch.acknowledgement(ch.ReplyResolution(outcome=ch.OUTCOME_IGNORED_NO_TOKEN))
        assert "Ignored" in text

    def test_one_adapter_registry_serves_both_callers(self):
        from tools.gateway import adapters, gateway_agent

        # gateway_agent must consume the shared map, not carry a second copy —
        # a channel present in one and absent from the other is a channel that
        # accepts commands and cannot be delivered to.
        assert gateway_agent.ADAPTER_CLASSES is adapters.ADAPTER_CLASSES
        for channel in ("slack", "teams", "telegram", "mattermost", "email"):
            assert adapters.build_adapter(channel, {}) is not None
        assert adapters.build_adapter("no-such-channel", {}) is None

    def test_every_adapter_exposes_the_one_send_seam(self):
        from tools.gateway import adapters

        for name, cls in adapters.ADAPTER_CLASSES.items():
            sig = inspect.signature(cls.send_message)
            assert list(sig.parameters)[1:4] == [
                "channel_user_id", "text", "thread_id",
            ], f"{name} does not expose the shared send_message seam"


class TestAdaptersAdmitAnApprovalReply:
    """A reply that never reaches the gateway can never resolve anything.

    Every ``parse_webhook`` drops a message that does not start with a command
    prefix. That is right for commands and wrong for a reply: the human is
    answering a question ICDEV asked them, and "approve" has no prefix. Without
    this, delivery works, resolution is unreachable, and the feature looks
    finished.
    """

    def test_slack_admits_a_tagged_reply(self):
        from tools.gateway.adapters import SlackAdapter

        env = SlackAdapter({}).parse_webhook(
            {"event": {"type": "message", "user": "U1", "text": "approve [icdev:ai-1]"}}, {}
        )
        assert env is not None
        assert ch.extract_token(env.raw_text) == "ai-1"

    def test_slack_still_drops_ordinary_chatter(self):
        from tools.gateway.adapters import SlackAdapter

        assert SlackAdapter({}).parse_webhook(
            {"event": {"type": "message", "user": "U1", "text": "lunch?"}}, {}
        ) is None

    def test_telegram_admits_a_tagged_reply(self):
        from tools.gateway.adapters import TelegramAdapter

        env = TelegramAdapter({}).parse_webhook(
            {
                "message": {
                    "message_id": 1,
                    "from": {"id": 42, "username": "jane"},
                    "chat": {"id": 42, "type": "private"},
                    "text": "deny [icdev:ai-1]",
                }
            },
            {},
        )
        assert env is not None and ch.extract_token(env.raw_text) == "ai-1"

    def test_internal_chat_admits_a_tagged_reply(self):
        from tools.gateway.adapters import InternalChatAdapter

        env = InternalChatAdapter({}).parse_webhook(
            {"user_id": "u1", "message": "approve [icdev:ai-1]"}, {}
        )
        assert env is not None and ch.extract_token(env.raw_text) == "ai-1"


class TestWebhookEndToEnd:
    """The gateway route itself: delivered ask → reply → settled item."""

    @pytest.fixture
    def client(self, inbox_db, monkeypatch):
        from tools.gateway import gateway_agent, security_chain

        # Gate 3 is the identity binding; the rest run for real.
        monkeypatch.setattr(
            security_chain,
            "resolve_binding",
            lambda channel, uid, db_path=None: {"id": "b1", "icdev_user_id": "jane.doe"},
        )
        monkeypatch.setattr(
            security_chain, "_rate_check_db", lambda *_a, **_k: (None, 0)
        )
        # The ordinary command path is not under test here and must not fan out
        # into a subprocess just to prove the approval branch was NOT taken.
        monkeypatch.setattr(
            gateway_agent,
            "execute_command",
            lambda *_a, **_k: {
                "output": "ok", "success": True, "audit_id": "",
                "filtered": False, "execution_time_ms": 1,
            },
        )
        app = gateway_agent.create_app()
        app.config["TESTING"] = True
        return app.test_client()

    def _post(self, client, message: str):
        return client.post(
            "/gateway/internal", json={"user_id": "u1", "message": message}
        )

    def test_a_tagged_reply_settles_the_item_through_the_route(self, client, inbox_db):
        item = _pending()

        response = self._post(client, f"approve {ch.format_token(item.item_id)}")

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["status"] == "approval_reply"
        assert payload["outcome"] == ch.OUTCOME_APPROVED
        assert get(item.item_id).is_approved is True

    def test_an_untagged_message_settles_nothing_through_the_route(self, client, inbox_db):
        item = _pending()

        response = self._post(client, "/icdev-status")

        assert response.get_json().get("status") != "approval_reply"
        assert get(item.item_id).state == STATE_PENDING


# ---------------------------------------------------------------------------
# 6. No new HTTP client
# ---------------------------------------------------------------------------
class TestNoNewTransport:
    FORBIDDEN = (
        "import requests",
        "from requests",
        "import httpx",
        "from httpx",
        "import aiohttp",
        "urllib.request",
        "from urllib",
        "http.client",
        "import socket",
        "import smtplib",
    )

    def test_the_module_introduces_no_http_client(self):
        source = Path(ch.__file__).read_text(encoding="utf-8")
        # Skip the prose: the docstring explains WHY there is no new client.
        body = source.split('"""', 2)[-1]
        offenders = [needle for needle in self.FORBIDDEN if needle in body]
        assert not offenders, f"a new transport crept in: {offenders}"

    def test_delivery_goes_through_the_adapter_seam(self, inbox_db):
        # Nothing is sent except via BaseChannelAdapter.send_message.
        adapter = FakeAdapter()
        ch.deliver(
            _pending(), adapter=adapter,
            routing_config=TELEGRAM_ROUTING, gateway_config=GATEWAY,
        )
        assert len(adapter.sent) == 1
