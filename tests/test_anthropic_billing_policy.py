# CUI // SP-CTI
"""omx-guard-07-d1 -- the declared Anthropic billing policy and `icdev config get`."""
from __future__ import annotations

import json

import pytest

from tools.cli import config as config_cli
from tools.cli.__main__ import main as icdev_main
from tools.llm import anthropic_billing as ab


def _write(tmp_path, text):
    path = tmp_path / "guard_anthropic.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_shipped_policy_is_subscription_only():
    policy = ab.load_policy()
    assert policy["anthropic_billing"] == "subscription_only"
    assert policy["warnings"] == []
    assert policy["source"].endswith("guard_anthropic.yaml")


def test_explicit_allow_metered_is_honoured(tmp_path):
    path = _write(tmp_path, "anthropic_billing: allow_metered\n")
    assert ab.billing_mode(path) == "allow_metered"
    assert ab.subscription_only(path) is False


@pytest.mark.parametrize("text", [
    "anthropic_billing: metered\n",        # not a valid value
    "anthropic_billing: 1\n",              # wrong type
    "other_key: x\n",                      # key absent
    "- a list\n",                          # not a mapping
    "anthropic_billing: [unclosed\n",      # unparseable
])
def test_invalid_policy_fails_closed(tmp_path, text):
    policy = ab.load_policy(_write(tmp_path, text))
    assert policy["anthropic_billing"] == "subscription_only"
    assert policy["source"] == "default"
    assert policy["warnings"]


def test_missing_file_fails_closed(tmp_path):
    policy = ab.load_policy(tmp_path / "absent.yaml")
    assert policy["anthropic_billing"] == "subscription_only"
    assert policy["warnings"]


def test_icdev_config_get_prints_default(capsys):
    assert icdev_main(["config", "get", "anthropic_billing"]) == 0
    assert capsys.readouterr().out.strip() == "subscription_only"


def test_icdev_config_get_json(capsys):
    assert icdev_main(["config", "get", "anthropic_billing", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["key"] == "anthropic_billing"
    assert out["value"] == "subscription_only"


def test_icdev_config_get_reads_through_the_policy_owner(monkeypatch, tmp_path, capsys):
    path = _write(tmp_path, "anthropic_billing: allow_metered\n")
    monkeypatch.setattr(ab, "policy_path", lambda: path)
    assert config_cli.main(["get", "anthropic_billing"]) == 0
    assert capsys.readouterr().out.strip() == "allow_metered"


def test_icdev_config_get_unknown_key(capsys):
    assert icdev_main(["config", "get", "no_such_key"]) == 2
    assert "unknown key" in capsys.readouterr().err
