# CUI // SP-CTI
"""The vendored-source API manifest is the half of the vendor gate CI can run (ctx-enf-01).

``check_vendor_parity`` (cxo-doc-03) compares tools/cortex/client.py against the
copies compass and idea_lab vendor into their own repositories. It computes the
drift correctly and it could never block here: those are separate PRIVATE repos
the ICDEV runner does not check out, so both consumers SKIP — correctly, an
absent repo is not evidence of drift — and the finding list stays empty. Two
public methods (``reason``, ``agent``) went missing from the copies for a week
with every gate green.

The cause is repo TOPOLOGY, not the operating system: ``/srv/standalone`` skips
exactly as ``C:/AI/standalone`` did, so making the path portable fixes nothing.
What fixes it is a committed snapshot of the canonical public API, which lives in
THIS repo and therefore needs no external checkout to verify.

The first test below is the enforcement — it is what turns "somebody changed the
client and forgot to re-vendor" into a red build. The rest pin the behaviour that
makes the enforcement trustworthy: the manifest blocks even when no consumer repo
exists, and an absent consumer STILL skips rather than falsely failing.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.workflow import coherence_checker as cc
from tools.workflow import vendor_api_manifest as vam


# ---------------------------------------------------------------------------
# The live gate — runs against the real repo, no standalone checkout needed
# ---------------------------------------------------------------------------


class TestCommittedManifest:
    def test_manifest_is_committed(self):
        assert vam.manifest_path().exists(), (
            f"{cc._VENDOR_API_MANIFEST} is missing. It is the only part of the vendor "
            "gate that can fire without the consumer repos checked out — regenerate it "
            "with `python tools/workflow/vendor_api_manifest.py --write`."
        )

    def test_manifest_matches_the_canonical_sources(self):
        drift = cc.vendor_manifest_drift()
        assert not drift, (
            "A declared vendored source changed without regenerating "
            f"{cc._VENDOR_API_MANIFEST}:\n  " + "\n  ".join(drift) + "\n"
            "Run `python tools/workflow/vendor_api_manifest.py --write` AND re-vendor "
            "the file into each consumer repo (compass, idea_lab)."
        )

    def test_regeneration_is_a_no_op_on_an_unchanged_tree(self):
        # Deterministic output — no timestamp, sorted members. A manifest that
        # rewrote itself every run would be diff noise everyone learns to ignore.
        assert vam.manifest_path().read_text(encoding="utf-8") == cc.render_vendor_api_manifest()

    def test_every_declared_source_is_recorded(self):
        recorded = cc._load_vendor_api_manifest()
        assert set(cc.vendor_parity_sources()) <= set(recorded)

    def test_declared_sources_are_not_empty(self):
        # A manifest of nothing would pass every assertion above forever.
        assert cc.vendor_parity_sources(), "args/vendor_parity.yaml declares no source"


class TestConfigHygiene:
    def test_no_machine_specific_path_default(self):
        """An OS-agnostic repo must not bake one machine's drive layout into a default."""
        defaults = cc._vendor_parity_config().get("path_defaults") or {}
        for name, value in defaults.items():
            text = str(value)
            assert not (len(text) > 1 and text[1] == ":"), (
                f"path_defaults.{name}={text!r} hardcodes a Windows drive path"
            )
            assert not text.startswith("/home/"), (
                f"path_defaults.{name}={text!r} hardcodes one machine's home directory"
            )


# ---------------------------------------------------------------------------
# Hermetic behaviour — PROJECT_ROOT is a temp tree, so nothing here depends on
# whether a standalone checkout exists on the machine running the suite.
# ---------------------------------------------------------------------------

CANONICAL = '''\
"""Canonical stdlib-only client."""


class Client:
    def __init__(self, base_url):
        self.base_url = base_url

    def ask(self, question):
        return None
'''

# The exact drift this task exists for: canonical grew a public method.
CANONICAL_GREW_A_METHOD = CANONICAL + "\n    def reason(self, prompt):\n        return None\n"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """A throwaway PROJECT_ROOT with one declared source, its manifest, no consumer.

    Deliberately models the CI runner: the consumer path points into a directory
    that does not exist, so the copy comparison can only ever skip.
    """
    monkeypatch.setattr(cc, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(vam, "PROJECT_ROOT", tmp_path, raising=False)
    _write(tmp_path / "tools" / "cortex" / "client.py", CANONICAL)
    _write(
        tmp_path / "args" / "vendor_parity.yaml",
        "path_defaults: {}\n"
        "vendored_copies:\n"
        "  - source: tools/cortex/client.py\n"
        "    consumers:\n"
        "      - name: compass\n"
        f'        path: "{(tmp_path / "absent" / "cortex_client.py").as_posix()}"\n',
    )
    _write(tmp_path / "args" / "vendor_api_manifest.json", cc.render_vendor_api_manifest())
    return tmp_path


CHANGED = [Path("tools/cortex/client.py")]


class TestManifestEnforcement:
    def test_in_sync_manifest_passes_with_no_consumer_present(self, repo):
        result = cc.check_vendor_parity(CHANGED)
        assert result.status == "pass", result.message
        assert any("skipped" in line for line in result.actual), result.actual

    def test_changed_source_without_regeneration_fails(self, repo):
        """The exact case that passed before ctx-enf-01: no consumer checked out."""
        _write(repo / "tools" / "cortex" / "client.py", CANONICAL_GREW_A_METHOD)
        result = cc.check_vendor_parity(CHANGED)
        assert result.status == "fail", result.message
        assert any("Client.reason" in line for line in result.missing), result.missing

    def test_changed_source_fails_on_the_full_repo_sweep_too(self, repo):
        # Consumer drift is WARN on the full sweep because it is machine-dependent.
        # Manifest drift is not machine-dependent, so it blocks in both tiers.
        _write(repo / "tools" / "cortex" / "client.py", CANONICAL_GREW_A_METHOD)
        result = cc.check_vendor_parity(None)
        assert result.status == "fail", result.message

    def test_regenerating_the_manifest_clears_the_failure(self, repo):
        _write(repo / "tools" / "cortex" / "client.py", CANONICAL_GREW_A_METHOD)
        assert cc.check_vendor_parity(CHANGED).status == "fail"
        _write(repo / "args" / "vendor_api_manifest.json", cc.render_vendor_api_manifest())
        assert cc.check_vendor_parity(CHANGED).status == "pass"

    def test_deleting_the_manifest_fails_rather_than_disarming(self, repo):
        (repo / "args" / "vendor_api_manifest.json").unlink()
        result = cc.check_vendor_parity(CHANGED)
        assert result.status == "fail", result.message
        assert any("no entry" in line for line in result.missing), result.missing

    def test_removing_a_public_method_is_also_drift(self, repo):
        _write(repo / "tools" / "cortex" / "client.py", CANONICAL.replace(
            "    def ask(self, question):\n        return None\n", ""
        ))
        result = cc.check_vendor_parity(CHANGED)
        assert result.status == "fail", result.message

    def test_docstring_only_edit_is_not_drift(self, repo):
        # Same rationale as the copy comparison: this is a CALLABLE-SURFACE gate,
        # not a byte gate. Failing on prose would make it noise and get it turned off.
        _write(
            repo / "tools" / "cortex" / "client.py",
            CANONICAL.replace('"""Canonical stdlib-only client."""', '"""Reworded."""'),
        )
        assert cc.check_vendor_parity(CHANGED).status == "pass"

    def test_editing_the_manifest_alone_puts_sources_back_in_scope(self, repo):
        """Hand-editing the manifest must not be the one edit the gate skips."""
        path = repo / "args" / "vendor_api_manifest.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["sources"]["tools/cortex/client.py"]["public_api"].append("Client.invented(self)")
        _write(path, json.dumps(data, indent=2) + "\n")
        result = cc.check_vendor_parity([Path("args/vendor_api_manifest.json")])
        assert result.status == "fail", result.message

    def test_unrelated_change_stays_out_of_scope(self, repo):
        _write(repo / "tools" / "cortex" / "client.py", CANONICAL_GREW_A_METHOD)
        result = cc.check_vendor_parity([Path("tools/dashboard/app.py")])
        assert result.status == "pass", result.message


class TestCli:
    def test_check_exits_zero_when_in_sync(self, repo, capsys):
        assert vam.main([]) == 0
        assert "matches" in capsys.readouterr().out

    def test_check_exits_one_on_drift(self, repo, capsys):
        _write(repo / "tools" / "cortex" / "client.py", CANONICAL_GREW_A_METHOD)
        assert vam.main([]) == 1
        assert "DRIFT" in capsys.readouterr().out

    def test_write_regenerates_and_then_verifies(self, repo, capsys):
        _write(repo / "tools" / "cortex" / "client.py", CANONICAL_GREW_A_METHOD)
        assert vam.main(["--write"]) == 0
        capsys.readouterr()
        assert vam.main([]) == 0

    def test_write_is_idempotent(self, repo):
        assert vam.write_manifest() is False

    def test_json_output_is_machine_readable(self, repo, capsys):
        assert vam.main(["--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["in_sync"] is True
        assert payload["sources"] == ["tools/cortex/client.py"]

    def test_generated_manifest_is_lf(self, repo):
        _write(repo / "tools" / "cortex" / "client.py", CANONICAL_GREW_A_METHOD)
        vam.write_manifest()
        assert b"\r\n" not in vam.manifest_path().read_bytes()
