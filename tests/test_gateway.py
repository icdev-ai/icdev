#!/usr/bin/env python3
# CUI // SP-CTI
"""Unit tests for the Remote Command Gateway (Phase 28).

Tests:
    1. CommandEnvelope + parse_command_text
    2. Security chain gates (pass/fail)
    3. Response filter (IL-aware redaction)
    4. User binder (challenge ceremony, provisioning)
    5. Command router (allowlist checking)
"""

import json
import os
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Ensure project root is on path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))


# ============================================================
# 1. CommandEnvelope + parse_command_text
# ============================================================

class TestCommandEnvelope:
    """Test CommandEnvelope dataclass and command parsing."""

    def test_default_envelope(self):
        from tools.gateway.event_envelope import CommandEnvelope
        env = CommandEnvelope()
        assert env.channel == ""
        assert env.command == ""
        assert env.id  # UUID generated
        assert env.timestamp  # ISO timestamp
        assert env.gate_results == {}

    def test_envelope_to_dict(self):
        from tools.gateway.event_envelope import CommandEnvelope
        env = CommandEnvelope(channel="telegram", command="icdev-status")
        d = env.to_dict()
        assert d["channel"] == "telegram"
        assert d["command"] == "icdev-status"
        assert "id" in d

    def test_parse_basic_command(self):
        from tools.gateway.event_envelope import parse_command_text
        cmd, args = parse_command_text("/icdev-status")
        assert cmd == "icdev-status"
        assert args == {}

    def test_parse_command_with_project(self):
        from tools.gateway.event_envelope import parse_command_text
        cmd, args = parse_command_text("/icdev-status proj-123")
        assert cmd == "icdev-status"
        assert args["project_id"] == "proj-123"

    def test_parse_command_with_flags(self):
        from tools.gateway.event_envelope import parse_command_text
        cmd, args = parse_command_text("/icdev-test --project-id proj-456 --verbose true")
        assert cmd == "icdev-test"
        assert args["project_id"] == "proj-456"
        assert args["verbose"] == "true"

    def test_parse_without_slash(self):
        from tools.gateway.event_envelope import parse_command_text
        cmd, args = parse_command_text("icdev-status proj-123")
        assert cmd == "icdev-status"
        assert args["project_id"] == "proj-123"

    def test_parse_empty(self):
        from tools.gateway.event_envelope import parse_command_text
        cmd, args = parse_command_text("")
        assert cmd == ""
        assert args == {}


# ============================================================
# 2. Security Chain Gates
# ============================================================

class TestSecurityChain:
    """Test individual security gates."""

    def _make_envelope(self, **kwargs):
        from tools.gateway.event_envelope import CommandEnvelope
        defaults = {
            "channel": "slack",
            "channel_user_id": "U123",
            "command": "icdev-status",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        defaults.update(kwargs)
        return CommandEnvelope(**defaults)

    def test_gate2_bot_rejected(self):
        from tools.gateway.security_chain import gate_2_bot_replay
        env = self._make_envelope(is_bot=True)
        result = gate_2_bot_replay(env, {})
        assert not result.passed
        assert "bot" in result.reason.lower()

    def test_gate2_replay_rejected(self):
        from tools.gateway.security_chain import gate_2_bot_replay
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        env = self._make_envelope(timestamp=old_time)
        config = {"security": {"signature": {"replay_window_seconds": 300}}}
        result = gate_2_bot_replay(env, config)
        assert not result.passed
        assert "old" in result.reason.lower()

    def test_gate2_valid(self):
        from tools.gateway.security_chain import gate_2_bot_replay
        env = self._make_envelope()
        result = gate_2_bot_replay(env, {})
        assert result.passed

    def test_gate3_no_binding(self):
        from tools.gateway.security_chain import gate_3_identity
        env = self._make_envelope()
        with patch("tools.gateway.security_chain.resolve_binding", return_value=None):
            result = gate_3_identity(env, {})
        assert not result.passed
        assert "no active binding" in result.reason

    def test_gate3_with_binding(self):
        from tools.gateway.security_chain import gate_3_identity
        env = self._make_envelope()
        binding = {
            "id": "bind-123",
            "icdev_user_id": "user@mil",
            "tenant_id": "tenant-abc",
        }
        with patch("tools.gateway.security_chain.resolve_binding", return_value=binding):
            result = gate_3_identity(env, {})
        assert result.passed
        assert env.binding_id == "bind-123"
        assert env.icdev_user_id == "user@mil"

    def test_gate5_classification_pass(self):
        from tools.gateway.security_chain import gate_5_classification
        env = self._make_envelope(command="icdev-status")
        channel_config = {"max_il": "IL5"}
        allowlist = [{"command": "icdev-status", "max_il": "IL5"}]
        result = gate_5_classification(env, channel_config, allowlist)
        assert result.passed

    def test_gate5_classification_fail(self):
        from tools.gateway.security_chain import gate_5_classification
        env = self._make_envelope(command="icdev-comply")
        channel_config = {"max_il": "IL2"}  # low channel
        allowlist = [{"command": "icdev-comply", "max_il": "IL5"}]
        result = gate_5_classification(env, channel_config, allowlist)
        assert not result.passed
        assert "channel max" in result.reason

    def test_gate6_rbac_read_allowed(self):
        from tools.gateway.security_chain import gate_6_rbac
        env = self._make_envelope(command="icdev-status", user_role="viewer",
                                  channel="slack")
        allowlist = [{"command": "icdev-status", "category": "read", "channels": "*"}]
        result = gate_6_rbac(env, allowlist)
        assert result.passed

    def test_gate6_rbac_write_denied_for_viewer(self):
        from tools.gateway.security_chain import gate_6_rbac
        env = self._make_envelope(command="icdev-intake", user_role="viewer",
                                  channel="internal_chat")
        allowlist = [{"command": "icdev-intake", "category": "write",
                      "channels": "internal_chat"}]
        result = gate_6_rbac(env, allowlist)
        assert not result.passed
        assert "cannot perform" in result.reason

    def test_gate6_channel_not_allowed(self):
        from tools.gateway.security_chain import gate_6_rbac
        env = self._make_envelope(command="icdev-build", user_role="developer",
                                  channel="telegram")
        allowlist = [{"command": "icdev-build", "category": "execute",
                      "channels": "internal_chat"}]
        result = gate_6_rbac(env, allowlist)
        assert not result.passed
        assert "not allowed on channel" in result.reason


# ============================================================
# 3. Response Filter
# ============================================================

class TestResponseFilter:
    """Test IL-aware response filtering."""

    def test_detect_public(self):
        from tools.gateway.response_filter import detect_response_il
        assert detect_response_il("Hello world, status OK") == "IL2"

    def test_detect_cui(self):
        from tools.gateway.response_filter import detect_response_il
        assert detect_response_il("CUI // SP-CTI\nProject status: healthy") == "IL5"

    def test_detect_secret(self):
        from tools.gateway.response_filter import detect_response_il
        assert detect_response_il("SECRET // NOFORN\nClassified data") == "IL6"

    def test_filter_no_redaction(self):
        from tools.gateway.response_filter import filter_response
        text = "Status: OK, all tests passing"
        filtered, was_filtered, il = filter_response(text, "IL5")
        assert not was_filtered
        assert filtered == text
        assert il == "IL2"

    def test_filter_redaction(self):
        from tools.gateway.response_filter import filter_response
        text = "CUI // SP-CTI\nSSP generated for project X"
        filtered, was_filtered, il = filter_response(text, "IL2")
        assert was_filtered
        assert "REDACTED" in filtered
        assert il in ("IL4", "IL5")

    def test_filter_secret_on_cui_channel(self):
        from tools.gateway.response_filter import filter_response
        text = "SECRET // NOFORN\nClassified assessment results"
        filtered, was_filtered, il = filter_response(text, "IL5")
        assert was_filtered
        assert "REDACTED" in filtered

    def test_truncate_short(self):
        from tools.gateway.response_filter import truncate_response
        text = "short message"
        assert truncate_response(text, 100) == text

    def test_truncate_long(self):
        from tools.gateway.response_filter import truncate_response
        text = "x" * 5000
        result = truncate_response(text, 4000)
        assert len(result) <= 4000
        assert "truncated" in result


# ============================================================
# 4. User Binder
# ============================================================

class TestUserBinder:
    """Test binding ceremony and management."""

    @pytest.fixture
    def db_path(self, tmp_path):
        """Create a temporary DB with the remote_user_bindings table."""
        db = tmp_path / "test.db"
        conn = sqlite3.connect(str(db))
        conn.executescript("""
            CREATE TABLE remote_user_bindings (
                id TEXT PRIMARY KEY,
                channel TEXT NOT NULL,
                channel_user_id TEXT NOT NULL,
                icdev_user_id TEXT,
                tenant_id TEXT,
                binding_status TEXT DEFAULT 'pending',
                bound_at TEXT,
                revoked_at TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(channel, channel_user_id)
            );
        """)
        conn.commit()
        conn.close()
        return db

    def test_create_challenge(self):
        from tools.gateway.user_binder import create_challenge
        code = create_challenge("telegram", "user123")
        assert len(code) == 8  # 4 bytes hex = 8 chars
        assert code == code.upper()

    def test_verify_challenge(self, db_path):
        from tools.gateway.user_binder import create_challenge, verify_challenge
        code = create_challenge("telegram", "user123")
        result = verify_challenge(code, "analyst@mil", "tenant-1", db_path)
        assert result["success"]
        assert "binding_id" in result

    def test_verify_expired_challenge(self, db_path):
        from tools.gateway.user_binder import (
            create_challenge, verify_challenge, _ACTIVE_CHALLENGES
        )
        code = create_challenge("telegram", "user123", ttl_minutes=0)
        # Manually expire
        _ACTIVE_CHALLENGES[code]["expires_at"] = (
            datetime.now(timezone.utc) - timedelta(minutes=1)
        ).isoformat()
        result = verify_challenge(code, "analyst@mil", db_path=db_path)
        assert not result["success"]
        assert "expired" in result["error"].lower()

    def test_provision_binding(self, db_path):
        from tools.gateway.user_binder import provision_binding, resolve_binding
        result = provision_binding(
            "mattermost", "mm-user-1", "admin@enclave.mil", "tenant-1", db_path
        )
        assert result["success"]

        # Verify resolution
        binding = resolve_binding("mattermost", "mm-user-1", db_path)
        assert binding is not None
        assert binding["icdev_user_id"] == "admin@enclave.mil"
        assert binding["binding_status"] == "active"

    def test_revoke_binding(self, db_path):
        from tools.gateway.user_binder import (
            provision_binding, revoke_binding, resolve_binding
        )
        result = provision_binding(
            "slack", "U456", "dev@mil", "tenant-1", db_path
        )
        bid = result["binding_id"]

        ok = revoke_binding(bid, "security review", db_path)
        assert ok

        # Should no longer resolve
        binding = resolve_binding("slack", "U456", db_path)
        assert binding is None

    def test_list_bindings(self, db_path):
        from tools.gateway.user_binder import provision_binding, list_bindings
        provision_binding("slack", "U1", "user1@mil", "", db_path)
        provision_binding("telegram", "T1", "user2@mil", "", db_path)

        all_bindings = list_bindings(db_path=db_path)
        assert len(all_bindings) == 2

        slack_only = list_bindings(channel="slack", db_path=db_path)
        assert len(slack_only) == 1
        assert slack_only[0]["channel"] == "slack"


# ============================================================
# 5. Command Router — Allowlist
# ============================================================

class TestCommandRouter:
    """Test command allowlist checking."""

    ALLOWLIST = [
        {"command": "icdev-status", "category": "read", "channels": "*",
         "max_il": "IL5", "requires_confirmation": False},
        {"command": "icdev-test", "category": "execute",
         "channels": "slack,teams,internal_chat",
         "max_il": "IL5", "requires_confirmation": True},
        {"command": "icdev-deploy", "category": "execute", "channels": "",
         "max_il": "IL5", "requires_confirmation": True},
        {"command": "icdev-build", "category": "execute",
         "channels": "internal_chat",
         "max_il": "IL5", "requires_confirmation": True},
    ]

    def test_allowed_everywhere(self):
        from tools.gateway.command_router import is_command_allowed
        allowed, entry = is_command_allowed("icdev-status", "telegram", self.ALLOWLIST)
        assert allowed

    def test_allowed_on_specific_channel(self):
        from tools.gateway.command_router import is_command_allowed
        allowed, _ = is_command_allowed("icdev-test", "slack", self.ALLOWLIST)
        assert allowed

    def test_not_allowed_on_channel(self):
        from tools.gateway.command_router import is_command_allowed
        allowed, _ = is_command_allowed("icdev-test", "telegram", self.ALLOWLIST)
        assert not allowed

    def test_deploy_disabled(self):
        from tools.gateway.command_router import is_command_allowed
        allowed, _ = is_command_allowed("icdev-deploy", "slack", self.ALLOWLIST)
        assert not allowed

    def test_deploy_disabled_internal(self):
        from tools.gateway.command_router import is_command_allowed
        allowed, _ = is_command_allowed("icdev-deploy", "internal_chat", self.ALLOWLIST)
        assert not allowed

    def test_unknown_command(self):
        from tools.gateway.command_router import is_command_allowed
        allowed, entry = is_command_allowed("unknown-cmd", "slack", self.ALLOWLIST)
        assert not allowed
        assert entry is None

    def test_requires_confirmation(self):
        from tools.gateway.command_router import requires_confirmation
        assert requires_confirmation("icdev-test", self.ALLOWLIST)
        assert not requires_confirmation("icdev-status", self.ALLOWLIST)

    def test_internal_only_command(self):
        from tools.gateway.command_router import is_command_allowed
        allowed, _ = is_command_allowed("icdev-build", "internal_chat", self.ALLOWLIST)
        assert allowed
        allowed, _ = is_command_allowed("icdev-build", "slack", self.ALLOWLIST)
        assert not allowed


# ============================================================
# 6. Adapter Base
# ============================================================

class TestAdapterBase:
    """Test adapter availability logic."""

    def test_available_connected(self):
        from tools.gateway.adapters.base import BaseChannelAdapter

        class DummyAdapter(BaseChannelAdapter):
            def verify_signature(self, p, s): return True
            def parse_webhook(self, d, h): return None
            def send_message(self, u, t, th=""): return True

        adapter = DummyAdapter("test", {
            "enabled": True, "requires_internet": True,
            "min_il": "IL2", "max_il": "IL5",
        })
        assert adapter.is_available("connected")
        assert not adapter.is_available("air_gapped")

    def test_available_air_gapped(self):
        from tools.gateway.adapters.base import BaseChannelAdapter

        class DummyAdapter(BaseChannelAdapter):
            def verify_signature(self, p, s): return True
            def parse_webhook(self, d, h): return None
            def send_message(self, u, t, th=""): return True

        adapter = DummyAdapter("test", {
            "enabled": True, "requires_internet": False,
            "min_il": "IL2", "max_il": "IL6",
        })
        assert adapter.is_available("connected")
        assert adapter.is_available("air_gapped")

    def test_disabled_adapter(self):
        from tools.gateway.adapters.base import BaseChannelAdapter

        class DummyAdapter(BaseChannelAdapter):
            def verify_signature(self, p, s): return True
            def parse_webhook(self, d, h): return None
            def send_message(self, u, t, th=""): return True

        adapter = DummyAdapter("test", {"enabled": False})
        assert not adapter.is_available("connected")
        assert not adapter.is_available("air_gapped")
