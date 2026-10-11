# CUI // SP-CTI
"""omx-adapt-04 -- `icdev omarchy setup` points each cli_bridge harness at the Claude bridge.

Reuses the stateful fake host of ``test_omarchy_setup`` (every external command
faked, ``systemctl --user`` remembers what was enabled), so "idempotent" means a
second run reports ``skipped`` AND leaves every file byte-identical.
"""
from __future__ import annotations

import json

import pytest

from tests.test_omarchy_setup import FakeWorld, _states, host_factory
from tools.cli import omarchy
from tools.llm.cli_bridge import http_bridge as hb


@pytest.fixture
def make_host(tmp_path, monkeypatch):
    return host_factory(tmp_path, monkeypatch)


def _bridge(report):
    return next(s for s in report["steps"] if s["step"] == "bridge")


def _paths(host):
    return {"token": hb.token_path(host.environ, host.home),
            "unit": host.unit_dir() / hb.UNIT_NAME,
            "opencode": hb.harness_config_path("opencode", host.environ, host.home),
            "pi": hb.harness_config_path("pi", host.environ, host.home)}


def _snapshot(paths):
    return {k: p.read_bytes() for k, p in paths.items() if p.is_file()}


def test_setup_writes_token_unit_and_both_harness_providers(make_host):
    world = FakeWorld(omarchy_on=True, pg_ready=True)
    host = make_host(world)
    paths = _paths(host)
    # A provider and an MCP entry the operator already had must survive.
    paths["opencode"].parent.mkdir(parents=True, exist_ok=True)
    paths["opencode"].write_text(json.dumps({"provider": {"ollama-cloud": {"npm": "x"}}}), encoding="utf-8")

    report = omarchy.run("setup", host)
    assert _states(report)["bridge"] == "done", _bridge(report)

    token = paths["token"].read_text(encoding="utf-8").strip()
    assert len(token) >= 32
    url = hb.bridge_url(hb.DEFAULT_PORT)

    oc = json.loads(paths["opencode"].read_text(encoding="utf-8"))
    entry = oc["provider"][hb.PROVIDER_ID]
    assert entry["npm"] == "@ai-sdk/openai-compatible"
    assert entry["options"] == {"baseURL": url, "apiKey": "{file:%s}" % paths["token"].as_posix()}
    assert set(entry["models"]) == set(hb.DEFAULT_MODELS)
    assert all(m["tool_call"] for m in entry["models"].values())
    assert "ollama-cloud" in oc["provider"]                       # kept
    assert omarchy.MCP_NAME in oc["mcp"]                          # the mcp step's entry kept

    pi = json.loads(paths["pi"].read_text(encoding="utf-8"))["providers"][hb.PROVIDER_ID]
    assert (pi["api"], pi["baseUrl"], pi["apiKey"]) == ("openai-completions", url, token)

    unit = paths["unit"].read_text(encoding="utf-8")
    assert "claude-bridge serve" in unit and "Environment=ANTHROPIC_API_KEY=\n" in unit
    assert hb.UNIT_NAME in world.timers                           # enabled + started
    assert url.startswith("http://127.0.0.1:")                    # harnesses point at loopback


def test_second_setup_is_skipped_and_byte_identical(make_host):
    world = FakeWorld(omarchy_on=True, pg_ready=True)
    host = make_host(world)
    omarchy.run("setup", host)
    before = _snapshot(_paths(host))
    assert set(before) == {"token", "unit", "opencode", "pi"}
    again = omarchy.run("setup", host)
    assert _states(again)["bridge"] == "skipped", _bridge(again)
    assert _snapshot(_paths(host)) == before


def test_dry_run_writes_nothing(make_host):
    host = make_host(FakeWorld(omarchy_on=True, pg_ready=True))
    report = omarchy.run("setup", host, dry_run=True)
    assert _states(report)["bridge"] == "planned"
    assert _snapshot(_paths(host)) == {}


def test_only_harnesses_present_on_the_host_are_configured(make_host):
    host = make_host(FakeWorld(omarchy_on=False, pg_ready=True, harnesses=("claude", "pi")))
    omarchy.run("setup", host)
    paths = _paths(host)
    assert paths["pi"].is_file()
    assert hb.PROVIDER_ID not in json.dumps(json.loads(paths["opencode"].read_text(encoding="utf-8"))
                                            if paths["opencode"].is_file() else {})


def test_no_claude_cli_means_no_bridge(make_host):
    host = make_host(FakeWorld(omarchy_on=True, pg_ready=True, harnesses=("opencode", "pi")))
    step = _bridge(omarchy.run("setup", host))
    assert step["state"] == "n/a" and "claude CLI is not installed" in step["detail"]
    assert not _paths(host)["token"].exists()


def test_no_bridge_harness_is_na(make_host):
    host = make_host(FakeWorld(omarchy_on=True, pg_ready=True, harnesses=("claude", "codex")))
    assert _states(omarchy.run("setup", host))["bridge"] == "n/a"


def test_windows_says_unsupported_for_the_unit_but_configures_harnesses(make_host):
    world = FakeWorld(omarchy_on=False, pg_ready=True)
    host = make_host(world, platform="windows")
    step = _bridge(omarchy.run("setup", host))
    assert step["state"] == "done"
    assert "unsupported on windows" in step["detail"] and "icdev claude-bridge serve" in step["detail"]
    paths = _paths(host)
    assert not paths["unit"].exists() and paths["pi"].is_file() and paths["opencode"].is_file()
    assert hb.UNIT_NAME not in world.timers


def test_uninstall_removes_only_what_the_bridge_step_wrote(make_host):
    world = FakeWorld(omarchy_on=True, pg_ready=True)
    host = make_host(world)
    paths = _paths(host)
    paths["pi"].parent.mkdir(parents=True, exist_ok=True)
    paths["pi"].write_text(json.dumps({"providers": {"vllm": {"api": "openai-completions"}}}),
                           encoding="utf-8")
    omarchy.run("setup", host)
    report = omarchy.run("uninstall", host)
    assert _states(report)["bridge"] == "done", _bridge(report)
    assert not paths["unit"].exists() and not paths["token"].exists()
    assert hb.UNIT_NAME not in world.timers
    pi = json.loads(paths["pi"].read_text(encoding="utf-8"))["providers"]
    assert hb.PROVIDER_ID not in pi and "vllm" in pi
    assert hb.PROVIDER_ID not in json.loads(paths["opencode"].read_text(encoding="utf-8")).get("provider", {})
