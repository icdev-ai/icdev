# CUI // SP-CTI
"""Unit tests for tools/llm/cli_bridge/capability.py.

Covers the backend-selection probes used by the provider's dynamic
subprocess-else-mailbox resolution (uclb-job-06):

  - is_cli_headless_capable: env override, PATH lookup, absolute-path checks
  - mailbox_worker_alive: no source ⇒ False, fresh/stale/malformed heartbeat
"""
from __future__ import annotations

import importlib
import pathlib
import sys
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

capability = importlib.import_module("tools.llm.cli_bridge.capability")


# ── is_cli_headless_capable ──────────────────────────────────────────────────


def test_headless_override_true(monkeypatch):
    monkeypatch.setenv("ICDEV_CLI_HEADLESS", "true")
    assert capability.is_cli_headless_capable() is True


def test_headless_override_false_beats_present_binary(monkeypatch):
    monkeypatch.setenv("ICDEV_CLI_HEADLESS", "false")
    # Even if the binary resolves, the override forces False.
    monkeypatch.setattr(capability.shutil, "which", lambda name: "/usr/bin/claude")
    assert capability.is_cli_headless_capable() is False


def test_headless_path_lookup_hit(monkeypatch):
    monkeypatch.delenv("ICDEV_CLI_HEADLESS", raising=False)
    monkeypatch.delenv("ICDEV_CLI_BRIDGE_BINARY", raising=False)
    monkeypatch.setattr(capability.shutil, "which", lambda name: "/usr/bin/claude")
    assert capability.is_cli_headless_capable() is True


def test_headless_path_lookup_miss(monkeypatch):
    monkeypatch.delenv("ICDEV_CLI_HEADLESS", raising=False)
    monkeypatch.delenv("ICDEV_CLI_BRIDGE_BINARY", raising=False)
    monkeypatch.setattr(capability.shutil, "which", lambda name: None)
    assert capability.is_cli_headless_capable() is False


def test_headless_absolute_path_missing_file(monkeypatch, tmp_path):
    monkeypatch.delenv("ICDEV_CLI_HEADLESS", raising=False)
    missing = tmp_path / "claude"
    assert capability.is_cli_headless_capable(str(missing)) is False


def test_headless_custom_binary_env(monkeypatch):
    monkeypatch.delenv("ICDEV_CLI_HEADLESS", raising=False)
    monkeypatch.setenv("ICDEV_CLI_BRIDGE_BINARY", "my-claude")
    seen = {}

    def fake_which(name):
        seen["name"] = name
        return "/opt/my-claude"

    monkeypatch.setattr(capability.shutil, "which", fake_which)
    assert capability.is_cli_headless_capable() is True
    assert seen["name"] == "my-claude"


# ── mailbox_worker_alive ─────────────────────────────────────────────────────


def test_mailbox_no_heartbeat_is_dead(monkeypatch):
    monkeypatch.delenv("ICDEV_CLI_MAILBOX_HEARTBEAT", raising=False)
    assert capability.mailbox_worker_alive() is False


def test_mailbox_fresh_heartbeat_is_alive(monkeypatch):
    recent = datetime.now(timezone.utc) - timedelta(seconds=5)
    monkeypatch.setenv("ICDEV_CLI_MAILBOX_HEARTBEAT", recent.isoformat())
    assert capability.mailbox_worker_alive(stale_seconds=90) is True


def test_mailbox_stale_heartbeat_is_dead(monkeypatch):
    old = datetime.now(timezone.utc) - timedelta(seconds=600)
    monkeypatch.setenv("ICDEV_CLI_MAILBOX_HEARTBEAT", old.isoformat())
    assert capability.mailbox_worker_alive(stale_seconds=90) is False


def test_mailbox_malformed_heartbeat_is_dead(monkeypatch):
    monkeypatch.setenv("ICDEV_CLI_MAILBOX_HEARTBEAT", "not-a-timestamp")
    assert capability.mailbox_worker_alive() is False
