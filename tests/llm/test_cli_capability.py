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


# ── env-var scope (SIPA env_secret false-positive guard) ───────────────────
#
# SIPA's env_secret sweep has previously mis-flagged the ICDEV_* routing
# overrides in this module as credential reads (e8a7daa40 — same false
# positive in cli_bridge/activate.py). Lock in the exact allowlist so the
# scope stays auditable and any future addition breaks this test until
# a scoping note + companion docstring update is added.
ALLOWED_ENV_VARS = frozenset(
    {
        "ICDEV_CLI_BRIDGE_BINARY",
        "ICDEV_CLI_HEADLESS",
        "ICDEV_CLI_MAILBOX_HEARTBEAT",
        # pytest fixtures / monkeypatch internals — not real ICDEV config.
        "PYTEST_CURRENT_TEST",
        "PYTEST_VERSION",
    }
)


def test_no_unauthorized_env_secret_reads():
    """The capability module reads only documented ICDEV_* routing overrides.

    Any ``os.environ.get`` of a var not in ALLOWED_ENV_VARS is treated as an
    unauthorized credential read. Update ALLOWED_ENV_VARS + the module
    docstring together when adding a new var.
    """
    import ast

    src = pathlib.Path(capability.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)

    reads: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Match os.environ.get("FOO") and os.environ.get("FOO", default)
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "get"
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "environ"
            and isinstance(func.value.value, ast.Name)
            and func.value.value.id == "os"
        ):
            if node.args and isinstance(node.args[0], ast.Constant):
                reads.add(node.args[0].value)

    assert reads == (reads & ALLOWED_ENV_VARS), (
        f"Unauthorized env-var reads detected in capability.py: "
        f"{sorted(reads - ALLOWED_ENV_VARS)}. "
        f"Add the var to ALLOWED_ENV_VARS in test_cli_capability.py AND "
        f"update the env-var scope block in the module docstring."
    )
