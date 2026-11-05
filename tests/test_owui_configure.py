#!/usr/bin/env python3
# CUI // SP-CTI
"""Open WebUI configurator: ICDEV must be the only backend (owui-api-03).

A fake Open WebUI admin API holds config in memory and records every call, so
these tests can assert that ``--plan`` never writes, that ``--check`` names
each bypass, and that ``--apply`` leaves exactly one model connection -- ICDEV.
Payload shapes follow Open WebUI 0.13 (routers/openai.py, ollama.py, configs.py).
"""

import json
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.openwebui import configure  # noqa: E402

OWUI = "http://owui.test"
ICDEV = "http://icdev.test:8443"


class FakeOpenWebUI:
    def __init__(self, openai=None, ollama=None, connections=None, tool_servers=None):
        self.store = {
            configure.OPENAI_CONFIG: openai or {"ENABLE_OPENAI_API": False, "OPENAI_API_BASE_URLS": [],
                                                "OPENAI_API_KEYS": [], "OPENAI_API_CONFIGS": {}},
            configure.OLLAMA_CONFIG: ollama or {"ENABLE_OLLAMA_API": False, "OLLAMA_BASE_URLS": [],
                                                "OLLAMA_API_CONFIGS": {}},
            configure.CONNECTIONS_CONFIG: connections or {"ENABLE_DIRECT_CONNECTIONS": False,
                                                          "ENABLE_BASE_MODELS_CACHE": False},
            configure.TOOL_SERVERS_CONFIG: tool_servers or {"TOOL_SERVER_CONNECTIONS": []},
        }
        self.calls = []

    def __call__(self, method, url, body=None):
        path = url[len(OWUI):]
        self.calls.append((method, path))
        if method == "GET":
            return json.loads(json.dumps(self.store[path]))
        key = path[: -len("/update")] if path.endswith("/update") else path
        self.store[key] = body
        return body


def bypassing():
    """The config this machine's Open WebUI demo is likely in: direct Ollama + a raw OpenAI key."""
    return FakeOpenWebUI(
        openai={"ENABLE_OPENAI_API": True, "OPENAI_API_BASE_URLS": ["https://api.openai.com/v1"],
                "OPENAI_API_KEYS": ["sk-real"], "OPENAI_API_CONFIGS": {}},
        ollama={"ENABLE_OLLAMA_API": True, "OLLAMA_BASE_URLS": ["http://localhost:11434"],
                "OLLAMA_API_CONFIGS": {}},
        connections={"ENABLE_DIRECT_CONNECTIONS": True, "ENABLE_BASE_MODELS_CACHE": False},
        tool_servers={"TOOL_SERVER_CONNECTIONS": [{"url": "http://mcpo.local:8000", "config": {"enable": True}}]},
    )


def _run(fake, *args, capsys):
    code = configure.main(["--owui-url", OWUI, "--icdev-url", ICDEV, *args], http=fake)
    return code, capsys.readouterr().out


def test_check_names_every_bypass(capsys):
    code, out = _run(bypassing(), "--check", "--json", capsys=capsys)
    assert code == 1
    rules = {(f["rule"], f["target"]) for f in json.loads(out)["findings"]}
    assert rules == {
        ("ollama_direct", "http://localhost:11434"),
        ("openai_non_icdev", "https://api.openai.com/v1"),
        ("user_direct_connections", "ENABLE_DIRECT_CONNECTIONS"),
        ("foreign_tool_server", "http://mcpo.local:8000"),
        ("no_icdev_connection", ICDEV + "/v1"),
    }


def test_check_passes_on_a_governed_config(capsys):
    fake = FakeOpenWebUI(
        openai={"ENABLE_OPENAI_API": True, "OPENAI_API_BASE_URLS": [ICDEV + "/v1/"],
                "OPENAI_API_KEYS": ["icdev_x"], "OPENAI_API_CONFIGS": {}},
        tool_servers={"TOOL_SERVER_CONNECTIONS": [{"url": ICDEV + "/mcp/v1/?toolset=chat_readonly"}]},
    )
    code, out = _run(fake, "--check", capsys=capsys)
    assert code == 0, out


def test_a_disabled_connection_is_not_a_bypass():
    state = {"openai": {"ENABLE_OPENAI_API": True,
                        "OPENAI_API_BASE_URLS": [ICDEV + "/v1", "https://api.openai.com/v1"],
                        "OPENAI_API_CONFIGS": {"1": {"enable": False}}}}
    assert configure.find_bypasses(state, ICDEV) == []


def test_plan_never_writes_and_never_prints_a_key(monkeypatch, capsys):
    monkeypatch.setenv(configure.ICDEV_KEY_ENV, "icdev_secret_value")
    fake = bypassing()
    code, out = _run(fake, "--plan", "--with-tools", capsys=capsys)
    assert code == 0
    assert all(m == "GET" for m, _ in fake.calls)
    assert "icdev_secret_value" not in out and "sk-real" not in out


def test_apply_leaves_icdev_as_the_only_backend(monkeypatch, capsys):
    monkeypatch.setenv(configure.ICDEV_KEY_ENV, "icdev_secret_value")
    fake = bypassing()
    code, out = _run(fake, "--apply", "--with-tools", capsys=capsys)
    assert code == 0, out
    assert json.loads(out)["remaining_findings"] == []
    openai = fake.store[configure.OPENAI_CONFIG]
    assert openai["OPENAI_API_BASE_URLS"] == [ICDEV + "/v1"]
    assert openai["OPENAI_API_KEYS"] == ["icdev_secret_value"]
    assert fake.store[configure.OLLAMA_CONFIG]["ENABLE_OLLAMA_API"] is False
    assert fake.store[configure.CONNECTIONS_CONFIG]["ENABLE_DIRECT_CONNECTIONS"] is False
    (tool,) = fake.store[configure.TOOL_SERVERS_CONFIG]["TOOL_SERVER_CONNECTIONS"]
    assert tool["url"] == ICDEV + "/mcp/v1/?toolset=chat_readonly"
    assert tool["type"] == "mcp"
    assert "icdev_secret_value" not in out


def test_apply_refuses_without_an_icdev_key(monkeypatch, capsys):
    monkeypatch.delenv(configure.ICDEV_KEY_ENV, raising=False)
    fake = bypassing()
    code, _ = _run(fake, "--apply", capsys=capsys)
    assert code == 2
    assert all(m == "GET" for m, _ in fake.calls)


def test_unreadable_config_is_exit_2_not_governed(capsys):
    def broken(method, url, body=None):
        raise OSError("connection refused")

    code, _ = _run(broken, "--check", capsys=capsys)
    assert code == 2


def test_env_block_starts_governed():
    env = configure.env_block(ICDEV)
    assert env["ENABLE_OLLAMA_API"] == "false"
    assert env["OPENAI_API_BASE_URL"] == ICDEV + "/v1"
    assert env["ENABLE_DIRECT_CONNECTIONS"] == "false"
    assert "icdev_" not in json.dumps(env)


@pytest.mark.parametrize("missing_env", [configure.ADMIN_TOKEN_ENV])
def test_no_admin_token_is_exit_2(monkeypatch, capsys, missing_env):
    monkeypatch.delenv(missing_env, raising=False)
    assert configure.main(["--icdev-url", ICDEV, "--check"]) == 2
