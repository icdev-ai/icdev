# CUI // SP-CTI
"""Unit tests for check_vendor_parity (cxo-doc-03).

Guards the OUT-OF-REPO vendored-copy invariant: a stdlib-only module that
standalone apps copy verbatim (tools/cortex/client.py) must never grow a public
method that its declared copies lack. The drift is latent — the copies simply
lag until someone calls the missing method — so only a check catches it.

Hermetic: PROJECT_ROOT is monkeypatched to a temp tree, so these never depend on
whether C:/AI/standalone is checked out on the machine running them.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.workflow import coherence_checker as cc


CANONICAL = '''\
"""Canonical stdlib-only client."""


class Client:
    def __init__(self, base_url, api_key=""):
        self.base_url = base_url

    def ask(self, question, *, timeout=None):
        return None

    def _private(self):
        return None


def helper(value):
    return value
'''

# Same public surface, different header + CRLF-ish cosmetics: must NOT be drift.
VENDORED_IN_SYNC = '''\
# VENDORED from icdev tools/cortex/client.py (canonical source) 2026-08-02.
# Keep byte-identical apart from this header.
"""Canonical stdlib-only client."""


class Client:
    def __init__(self, base_url, api_key=""):
        self.base_url = base_url

    def ask(self, question, *, timeout=None):
        """Docstrings and comments differ freely."""
        return None


def helper(value):
    return value
'''


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """A throwaway PROJECT_ROOT holding a canonical source + its config."""
    monkeypatch.setattr(cc, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("VENDOR_TEST_ROOT", raising=False)
    _write(tmp_path / "tools" / "cortex" / "client.py", CANONICAL)
    return tmp_path


def _config(repo: Path, consumer_path: str) -> None:
    """Declare one vendor target pointing at *consumer_path* (a posix path)."""
    _write(
        repo / "args" / "vendor_parity.yaml",
        "path_defaults: {}\n"
        "vendored_copies:\n"
        "  - source: tools/cortex/client.py\n"
        "    consumers:\n"
        "      - name: compass\n"
        f'        path: "{consumer_path}"\n',
    )


CHANGED = [Path("tools/cortex/client.py")]


class TestVendorParity:
    def test_in_sync_copy_passes(self, repo):
        copy = _write(repo / "external" / "cortex_client.py", VENDORED_IN_SYNC)
        _config(repo, copy.as_posix())
        result = cc.check_vendor_parity(CHANGED)
        assert result.status == "pass", result.message

    def test_missing_method_fails_on_changed_scope(self, repo):
        lagging = VENDORED_IN_SYNC.replace(
            "    def ask(self, question, *, timeout=None):\n"
            '        """Docstrings and comments differ freely."""\n'
            "        return None\n",
            "",
        )
        copy = _write(repo / "external" / "cortex_client.py", lagging)
        _config(repo, copy.as_posix())
        result = cc.check_vendor_parity(CHANGED)
        assert result.status == "fail", result.message
        assert any("Client.ask" in m for m in result.missing), result.missing

    def test_renamed_parameter_is_drift(self, repo):
        copy = _write(
            repo / "external" / "cortex_client.py",
            VENDORED_IN_SYNC.replace("def ask(self, question,", "def ask(self, query,"),
        )
        _config(repo, copy.as_posix())
        result = cc.check_vendor_parity(CHANGED)
        assert result.status == "fail", result.message

    def test_copy_ahead_of_canonical_is_allowed(self, repo):
        ahead = VENDORED_IN_SYNC + "\n\ndef not_yet_upstream():\n    return 1\n"
        copy = _write(repo / "external" / "cortex_client.py", ahead)
        _config(repo, copy.as_posix())
        result = cc.check_vendor_parity(CHANGED)
        assert result.status == "pass", result.message

    def test_absent_consumer_path_skips_cleanly(self, repo):
        _config(repo, (repo / "nowhere" / "cortex_client.py").as_posix())
        result = cc.check_vendor_parity(CHANGED)
        assert result.status == "pass", result.message
        assert any("skipped" in a for a in result.actual), result.actual

    def test_unresolved_placeholder_skips_cleanly(self, repo):
        # No env var, no path_defaults entry -> consumer is skipped, not failed.
        _config(repo, "${VENDOR_TEST_ROOT}/cortex_client.py")
        result = cc.check_vendor_parity(CHANGED)
        assert result.status == "pass", result.message
        assert any("skipped" in a for a in result.actual), result.actual

    def test_placeholder_resolves_from_env(self, repo, monkeypatch):
        copy = _write(repo / "external" / "cortex_client.py", VENDORED_IN_SYNC)
        monkeypatch.setenv("VENDOR_TEST_ROOT", copy.parent.as_posix())
        _config(repo, "${VENDOR_TEST_ROOT}/cortex_client.py")
        result = cc.check_vendor_parity(CHANGED)
        assert result.status == "pass", result.message
        assert any("in sync" in a for a in result.actual), result.actual

    def test_full_repo_drift_is_warn_not_fail(self, repo):
        lagging = VENDORED_IN_SYNC.replace("def helper(value):", "def helper_renamed(value):")
        copy = _write(repo / "external" / "cortex_client.py", lagging)
        _config(repo, copy.as_posix())
        result = cc.check_vendor_parity(None)
        assert result.status == "warn", result.message

    def test_unrelated_change_is_out_of_scope(self, repo):
        lagging = VENDORED_IN_SYNC.replace("def helper(value):", "def helper_renamed(value):")
        copy = _write(repo / "external" / "cortex_client.py", lagging)
        _config(repo, copy.as_posix())
        result = cc.check_vendor_parity([Path("tools/dashboard/app.py")])
        assert result.status == "pass", result.message

    def test_no_config_passes(self, repo):
        result = cc.check_vendor_parity(CHANGED)
        assert result.status == "pass", result.message

    def test_registered_in_check_registry(self):
        assert cc.CHECK_REGISTRY["vendor_parity"] is cc.check_vendor_parity


class TestPublicApi:
    def test_private_members_excluded_dunder_init_kept(self):
        api = cc._public_api(CANONICAL)
        assert "Client.__init__(self, base_url, api_key=...)" in api
        assert "Client.ask(self, question, *, timeout=...)" in api
        assert "class Client" in api
        assert "helper(value)" in api
        assert not any("_private" in entry for entry in api)

    def test_default_value_change_is_not_drift(self):
        # Only the PRESENCE of a default is recorded — annotations and default
        # values are formatting noise between a file and its verbatim copy.
        other = CANONICAL.replace('api_key=""', "api_key='x'")
        assert cc._public_api(CANONICAL) == cc._public_api(other)
