# CUI // SP-CTI
"""omx-guard-07-d5: `icdev omarchy setup/status` flags a metered Anthropic credential.

A harness auth store holding an Anthropic credential of type ``api_key`` (Pi) or
``api`` (opencode) is a ``failed`` billing step under ``subscription_only``; an
OAuth (subscription) credential passes. The report names the TYPE, never the value.
"""
from __future__ import annotations

import json

import pytest

from tests.test_omarchy_setup import FakeWorld, host_factory
from tools.cli import omarchy

SECRET = "sk-ant-api03-DO-NOT-PRINT"


@pytest.fixture
def host(tmp_path, monkeypatch):
    return host_factory(tmp_path, monkeypatch)(FakeWorld(omarchy_on=True, pg_ready=True))


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _pi_store(host):
    return omarchy.auth_stores(host)["pi"]


def _billing(report):
    return next(s for s in report["steps"] if s["step"] == "billing")


def _policy(host, mode):
    path = host.root / "args" / "guard_anthropic.yaml"
    path.write_text(f"anthropic_billing: {mode}\n", encoding="utf-8")


def test_api_key_credential_is_a_failed_billing_step(host, capsys):
    _write(_pi_store(host), {"anthropic": {"type": "api_key", "key": SECRET}})

    report = omarchy.run("status", host)
    step = _billing(report)
    assert step["state"] == "failed"
    assert "type=api_key" in step["detail"] and "pi" in step["detail"]
    assert report["exit"] == 1
    assert SECRET not in json.dumps(report)

    omarchy._print(report)
    assert SECRET not in capsys.readouterr().out


def test_opencode_api_type_is_found_with_type_only(host):
    _write(omarchy.auth_stores(host)["opencode"], {"anthropic": {"type": "api", "key": SECRET}})
    assert omarchy.anthropic_auth_findings(host) == [{
        "harness": "opencode", "path": str(omarchy.auth_stores(host)["opencode"]),
        "provider": "anthropic", "type": "api"}]


def test_oauth_credential_raises_no_finding(host):
    _write(_pi_store(host), {"anthropic": {"type": "oauth", "refresh": SECRET, "access": SECRET,
                                           "expires": 0}})
    _write(omarchy.auth_stores(host)["opencode"], {"anthropic": {"type": "oauth", "refresh": SECRET}})

    assert omarchy.anthropic_auth_findings(host) == []
    step = _billing(omarchy.run("status", host))
    assert step["state"] == "ok"


def test_non_anthropic_api_key_is_not_a_finding(host):
    _write(_pi_store(host), {"openai": {"type": "api_key", "key": SECRET}})
    assert omarchy.anthropic_auth_findings(host) == []


def test_allow_metered_reports_but_does_not_fail(host):
    _policy(host, "allow_metered")
    _write(_pi_store(host), {"anthropic": {"type": "api_key", "key": SECRET}})
    step = _billing(omarchy.run("status", host))
    assert step["state"] == "ok" and "allow_metered permits it" in step["detail"]


def test_billing_is_never_part_of_uninstall(host):
    _write(_pi_store(host), {"anthropic": {"type": "api_key", "key": SECRET}})
    report = omarchy.run("uninstall", host, dry_run=True)
    assert "billing" not in {s["step"] for s in report["steps"]}
    assert _pi_store(host).is_file()
