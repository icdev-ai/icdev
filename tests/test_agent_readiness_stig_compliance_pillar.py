# CUI // SP-CTI
"""Tests for the STIG compliance pillar's NLP extractor and criterion functions."""
from __future__ import annotations

import pathlib
import textwrap
from unittest.mock import MagicMock, patch


from tools.ai_augmentation.agent_readiness.pillars.stig_compliance import (
    PILLAR,
    _check_cat1_remediation,
    _check_external_stig_scanner,
    _check_stig_checklist,
    _check_stig_in_docs,
    _check_stig_vids_in_code,
    _nlp_extract_stig_refs,
    _run_stig_cmd_with_death_gate,
    _verify_pid_exited,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(tmp_path: pathlib.Path, rel: str, content: str) -> pathlib.Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def _default_thresholds(**overrides):
    base = {
        "nlp_extractor_enabled": True,
        "nlp_extractor_model": "claude-haiku-4-5-20251001",
        "nlp_extractor_max_tokens": 256,
        "nlp_extractor_confidence_threshold": 0.7,
        "nlp_extractor_text_sample_chars": 2000,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _load_thresholds — config loading and fallback behaviour
# ---------------------------------------------------------------------------

class TestLoadThresholds:
    def test_returns_yaml_values_when_config_present(self, tmp_path, monkeypatch):
        cfg = tmp_path / "args" / "agent_readiness_config.yaml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(
            "pillars:\n"
            "  stig_compliance:\n"
            "    nlp_extractor:\n"
            "      enabled: false\n"
            "      model: claude-haiku-custom\n"
            "      max_tokens: 512\n"
            "      confidence_threshold: 0.85\n"
            "      text_sample_chars: 4000\n",
            encoding="utf-8",
        )
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["nlp_extractor_enabled"] is False
        assert result["nlp_extractor_model"] == "claude-haiku-custom"
        assert result["nlp_extractor_max_tokens"] == 512
        assert result["nlp_extractor_confidence_threshold"] == 0.85
        assert result["nlp_extractor_text_sample_chars"] == 4000
        mod._load_thresholds.cache_clear()

    def test_falls_back_to_defaults_when_file_absent(self, tmp_path, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", tmp_path / "nonexistent.yaml")
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["nlp_extractor_enabled"] is True
        assert result["nlp_extractor_model"] == "claude-haiku-4-5-20251001"
        assert result["nlp_extractor_max_tokens"] == 256
        assert result["nlp_extractor_confidence_threshold"] == 0.7
        assert result["nlp_extractor_text_sample_chars"] == 2000
        mod._load_thresholds.cache_clear()

    def test_falls_back_on_malformed_yaml(self, tmp_path, monkeypatch):
        cfg = tmp_path / "agent_readiness_config.yaml"
        cfg.write_text(":\tbad: yaml: [", encoding="utf-8")
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["nlp_extractor_model"] == "claude-haiku-4-5-20251001"
        mod._load_thresholds.cache_clear()

    def test_partial_config_merges_with_defaults(self, tmp_path, monkeypatch):
        cfg = tmp_path / "agent_readiness_config.yaml"
        cfg.write_text(
            "pillars:\n  stig_compliance:\n    nlp_extractor:\n      max_tokens: 1024\n",
            encoding="utf-8",
        )
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_ARGS_PATH", cfg)
        mod._load_thresholds.cache_clear()
        result = mod._load_thresholds()
        assert result["nlp_extractor_max_tokens"] == 1024
        assert result["nlp_extractor_confidence_threshold"] == 0.7  # default
        mod._load_thresholds.cache_clear()


# ---------------------------------------------------------------------------
# _nlp_extract_stig_refs — NLP extractor unit tests
# ---------------------------------------------------------------------------

class TestNlpExtractStigRefs:
    def _make_provider(self, json_response: str) -> MagicMock:
        response = MagicMock()
        response.content = json_response
        provider = MagicMock()
        provider.invoke.return_value = response
        return provider

    def test_returns_none_when_nlp_disabled(self, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: _default_thresholds(nlp_extractor_enabled=False))
        result = _nlp_extract_stig_refs("some text with V-220938", "find STIG IDs")
        assert result is None

    def test_returns_none_when_api_key_missing(self, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: _default_thresholds())
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        result = _nlp_extract_stig_refs("some text with V-220938", "find STIG IDs")
        assert result is None

    def test_returns_none_when_import_fails(self, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: _default_thresholds())
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
        with patch.dict("sys.modules", {"tools.llm.anthropic_provider": None}):
            result = _nlp_extract_stig_refs("some text", "find STIG IDs")
        assert result is None

    def test_parses_valid_json_response(self, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: _default_thresholds())
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        fake_provider = self._make_provider('{"found": true, "refs": ["V-220938", "CAT I"], "confidence": 0.95}')
        with patch("tools.llm.anthropic_provider.AnthropicLLMProvider", return_value=fake_provider), \
             patch("tools.llm.provider.LLMRequest"):
            result = _nlp_extract_stig_refs("Security patch applied per V-220938 (CAT I).", "identify STIG IDs")

        assert result is not None
        assert result["found"] is True
        assert "V-220938" in result["refs"]
        assert result["confidence"] == 0.95

    def test_extracts_json_from_surrounding_text(self, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: _default_thresholds())
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        raw = 'Here is my analysis:\n{"found": false, "refs": [], "confidence": 0.1}\nEnd of analysis.'
        fake_provider = self._make_provider(raw)
        with patch("tools.llm.anthropic_provider.AnthropicLLMProvider", return_value=fake_provider), \
             patch("tools.llm.provider.LLMRequest"):
            result = _nlp_extract_stig_refs("random text", "identify STIG IDs")

        assert result is not None
        assert result["found"] is False
        assert result["confidence"] == 0.1

    def test_returns_none_on_provider_exception(self, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: _default_thresholds())
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        broken_provider = MagicMock()
        broken_provider.invoke.side_effect = RuntimeError("API error")
        with patch("tools.llm.anthropic_provider.AnthropicLLMProvider", return_value=broken_provider), \
             patch("tools.llm.provider.LLMRequest"):
            result = _nlp_extract_stig_refs("some text", "identify STIG IDs")

        assert result is None

    def test_truncates_text_to_sample_chars(self, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: _default_thresholds(nlp_extractor_text_sample_chars=10))
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

        captured_prompts = []

        def fake_provider_factory(**kwargs):
            provider = MagicMock()
            response = MagicMock()
            response.content = '{"found": false, "refs": [], "confidence": 0.0}'
            provider.invoke.side_effect = lambda req, model_id, cfg: (captured_prompts.append(req.messages[0]["content"]) or response)
            return provider

        with patch("tools.llm.anthropic_provider.AnthropicLLMProvider", side_effect=fake_provider_factory), \
             patch("tools.llm.provider.LLMRequest", side_effect=lambda **kw: MagicMock(messages=kw["messages"])):
            _nlp_extract_stig_refs("A" * 100, "find STIG IDs")

        assert captured_prompts, "Provider was never called"
        assert "A" * 10 in captured_prompts[0]
        assert "A" * 11 not in captured_prompts[0]


# ---------------------------------------------------------------------------
# _check_stig_vids_in_code — regex fast path + NLP enhanced path
# ---------------------------------------------------------------------------

class TestCheckStigVidsInCode:
    def _patch(self, monkeypatch, **threshold_overrides):
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: _default_thresholds(**threshold_overrides))

    def test_passes_via_regex_when_vid_in_python_file(self, tmp_path, monkeypatch):
        self._patch(monkeypatch)
        _write(tmp_path, "security.py", "# STIG: V-220938 — disable weak ciphers\ncipher = 'AES256'\n")
        result = _check_stig_vids_in_code(tmp_path)
        assert result.passed
        assert "V-ID" in result.message or "stig" in result.message.lower() or "security.py" in result.message

    def test_passes_via_regex_when_vid_in_yaml_file(self, tmp_path, monkeypatch):
        self._patch(monkeypatch)
        _write(tmp_path, "config.yaml", "# V-220938 compliance setting\ntls_min_version: '1.2'\n")
        result = _check_stig_vids_in_code(tmp_path)
        assert result.passed

    def test_passes_via_regex_when_sv_rule_present(self, tmp_path, monkeypatch):
        self._patch(monkeypatch)
        _write(tmp_path, "hardening.py", "# SV-220938r1_rule — ensure audit logging\n")
        result = _check_stig_vids_in_code(tmp_path)
        assert result.passed

    def test_fails_via_regex_when_no_vids(self, tmp_path, monkeypatch):
        self._patch(monkeypatch, nlp_extractor_enabled=False)
        _write(tmp_path, "app.py", "# generic comment\nprint('hello')\n")
        result = _check_stig_vids_in_code(tmp_path)
        assert not result.passed

    def test_passes_via_nlp_when_regex_misses(self, tmp_path, monkeypatch):
        self._patch(monkeypatch, nlp_extractor_confidence_threshold=0.7)
        _write(tmp_path, "notes.py", "# We comply with the vulnerability identified as V dash two-two-oh-nine-three-eight\n")

        nlp_result = {"found": True, "refs": ["V-220938"], "confidence": 0.9}
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_nlp_extract_stig_refs", lambda text, task: nlp_result)

        result = _check_stig_vids_in_code(tmp_path)
        assert result.passed
        assert "NLP" in result.message

    def test_nlp_skipped_when_below_confidence(self, tmp_path, monkeypatch):
        self._patch(monkeypatch, nlp_extractor_confidence_threshold=0.7)
        _write(tmp_path, "app.py", "# no real STIG ref here\n")

        nlp_result = {"found": True, "refs": ["V-220938"], "confidence": 0.5}
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_nlp_extract_stig_refs", lambda text, task: nlp_result)

        result = _check_stig_vids_in_code(tmp_path)
        assert not result.passed

    def test_nlp_returns_none_falls_through_to_fail(self, tmp_path, monkeypatch):
        self._patch(monkeypatch)
        _write(tmp_path, "app.py", "# nothing here\n")

        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_nlp_extract_stig_refs", lambda text, task: None)

        result = _check_stig_vids_in_code(tmp_path)
        assert not result.passed


# ---------------------------------------------------------------------------
# _check_stig_in_docs — regex fast path + NLP enhanced path
# ---------------------------------------------------------------------------

class TestCheckStigInDocs:
    def _patch(self, monkeypatch, **threshold_overrides):
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: _default_thresholds(**threshold_overrides))

    def test_passes_via_regex_when_stig_word_in_md(self, tmp_path, monkeypatch):
        self._patch(monkeypatch)
        _write(tmp_path, "README.md", "# Compliance\nThis project follows DISA STIG guidelines.\n")
        result = _check_stig_in_docs(tmp_path)
        assert result.passed

    def test_passes_via_regex_when_vid_in_doc(self, tmp_path, monkeypatch):
        self._patch(monkeypatch)
        _write(tmp_path, "docs/security.md", "Applied fix for V-220938 per STIG checklist.\n")
        result = _check_stig_in_docs(tmp_path)
        assert result.passed

    def test_passes_via_regex_when_full_stig_name_in_txt(self, tmp_path, monkeypatch):
        self._patch(monkeypatch)
        _write(tmp_path, "docs/guide.txt", "See the Security Technical Implementation Guide for details.\n")
        result = _check_stig_in_docs(tmp_path)
        assert result.passed

    def test_fails_when_no_docs_exist(self, tmp_path, monkeypatch):
        self._patch(monkeypatch, nlp_extractor_enabled=False)
        result = _check_stig_in_docs(tmp_path)
        assert not result.passed

    def test_passes_via_nlp_when_regex_misses_natural_language(self, tmp_path, monkeypatch):
        self._patch(monkeypatch, nlp_extractor_confidence_threshold=0.7)
        _write(tmp_path, "README.md", "We follow all applicable security technical guides from the Defense Information Systems Agency.\n")

        nlp_result = {"found": True, "refs": ["DISA STIG"], "confidence": 0.88}
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_nlp_extract_stig_refs", lambda text, task: nlp_result)

        result = _check_stig_in_docs(tmp_path)
        assert result.passed
        assert "NLP" in result.message

    def test_nlp_skipped_when_no_docs(self, tmp_path, monkeypatch):
        self._patch(monkeypatch)
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        call_count = {"n": 0}
        def counting_nlp(text, task):
            call_count["n"] += 1
            return None
        monkeypatch.setattr(mod, "_nlp_extract_stig_refs", counting_nlp)
        result = _check_stig_in_docs(tmp_path)
        assert not result.passed
        assert call_count["n"] == 0

    def test_nlp_not_found_result_fails(self, tmp_path, monkeypatch):
        self._patch(monkeypatch, nlp_extractor_confidence_threshold=0.7)
        _write(tmp_path, "README.md", "No security content here.\n")

        nlp_result = {"found": False, "refs": [], "confidence": 0.95}
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_nlp_extract_stig_refs", lambda text, task: nlp_result)

        result = _check_stig_in_docs(tmp_path)
        assert not result.passed


# ---------------------------------------------------------------------------
# _check_stig_checklist — regex fast path + NLP enhanced path
# ---------------------------------------------------------------------------

class TestCheckStigChecklist:
    def _patch(self, monkeypatch, **threshold_overrides):
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: _default_thresholds(**threshold_overrides))

    def test_passes_via_regex_when_ckl_has_vid(self, tmp_path, monkeypatch):
        self._patch(monkeypatch)
        _write(tmp_path, "checklist.ckl", "<VULN_ID>V-220938</VULN_ID>\n")
        result = _check_stig_checklist(tmp_path)
        assert result.passed

    def test_passes_via_regex_when_stig_yaml_has_doc_pattern(self, tmp_path, monkeypatch):
        self._patch(monkeypatch)
        _write(tmp_path, "stig-baseline.yaml", "benchmark: RHEL-09-STIG\nchecks: []\n")
        result = _check_stig_checklist(tmp_path)
        assert result.passed

    def test_passes_via_compliance_docs(self, tmp_path, monkeypatch):
        self._patch(monkeypatch)
        _write(tmp_path, "docs/compliance/stig-checklist.md", "# STIG Checklist\nV-220938: closed\n")
        result = _check_stig_checklist(tmp_path)
        assert result.passed

    def test_fails_when_no_checklist_files(self, tmp_path, monkeypatch):
        self._patch(monkeypatch, nlp_extractor_enabled=False)
        result = _check_stig_checklist(tmp_path)
        assert not result.passed

    def test_passes_via_nlp_for_natural_language_checklist(self, tmp_path, monkeypatch):
        self._patch(monkeypatch, nlp_extractor_confidence_threshold=0.7)
        _write(tmp_path, "checklist.ckl", "All Category One findings have been remediated per policy.\n")

        nlp_result = {"found": True, "refs": ["CAT I", "V-220938"], "confidence": 0.9}
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_nlp_extract_stig_refs", lambda text, task: nlp_result)

        result = _check_stig_checklist(tmp_path)
        assert result.passed
        assert "NLP" in result.message

    def test_nlp_skipped_when_below_confidence_threshold(self, tmp_path, monkeypatch):
        self._patch(monkeypatch, nlp_extractor_confidence_threshold=0.9)
        _write(tmp_path, "checklist.ckl", "some checklist content\n")

        nlp_result = {"found": True, "refs": ["CAT I"], "confidence": 0.8}
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_nlp_extract_stig_refs", lambda text, task: nlp_result)

        result = _check_stig_checklist(tmp_path)
        assert not result.passed


# ---------------------------------------------------------------------------
# _check_cat1_remediation — regex fast path + NLP enhanced path
# ---------------------------------------------------------------------------

class TestCheckCat1Remediation:
    def _patch(self, monkeypatch, **threshold_overrides):
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: _default_thresholds(**threshold_overrides))

    def test_passes_via_regex_when_vid_and_cat_cooccur(self, tmp_path, monkeypatch):
        self._patch(monkeypatch)
        _write(tmp_path, "security.py", "# V-220938 — CAT I — disable anonymous FTP\n")
        result = _check_cat1_remediation(tmp_path)
        assert result.passed

    def test_passes_via_regex_with_cat_iii_and_vid(self, tmp_path, monkeypatch):
        self._patch(monkeypatch)
        _write(tmp_path, "docs/remediation.md", "Fixed V-220940 (CAT III) — low severity.\n")
        result = _check_cat1_remediation(tmp_path)
        assert result.passed

    def test_passes_when_ckl_checklist_present(self, tmp_path, monkeypatch):
        self._patch(monkeypatch)
        _write(tmp_path, "baseline.ckl", "STIG checklist content\n")
        result = _check_cat1_remediation(tmp_path)
        assert result.passed
        assert "checklist" in result.message.lower()

    def test_fails_when_no_evidence(self, tmp_path, monkeypatch):
        self._patch(monkeypatch, nlp_extractor_enabled=False)
        _write(tmp_path, "docs/notes.md", "General development notes.\n")
        result = _check_cat1_remediation(tmp_path)
        assert not result.passed

    def test_passes_via_nlp_for_prose_cat_severity(self, tmp_path, monkeypatch):
        self._patch(monkeypatch, nlp_extractor_confidence_threshold=0.7)
        _write(tmp_path, "docs/compliance.md", "All Category One vulnerabilities have been addressed and closed in the security review.\n")

        nlp_result = {"found": True, "refs": ["CAT I"], "confidence": 0.92}
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_nlp_extract_stig_refs", lambda text, task: nlp_result)

        result = _check_cat1_remediation(tmp_path)
        assert result.passed
        assert "NLP" in result.message

    def test_nlp_returns_none_does_not_crash(self, tmp_path, monkeypatch):
        self._patch(monkeypatch)
        _write(tmp_path, "docs/notes.md", "No severity info.\n")

        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_nlp_extract_stig_refs", lambda text, task: None)

        result = _check_cat1_remediation(tmp_path)
        assert not result.passed

    def test_nlp_high_confidence_low_score_passes(self, tmp_path, monkeypatch):
        self._patch(monkeypatch, nlp_extractor_confidence_threshold=0.6)
        _write(tmp_path, "docs/security-review.md", "Severity categories tracked per policy.\n")

        nlp_result = {"found": True, "refs": ["CAT II"], "confidence": 0.65}
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_nlp_extract_stig_refs", lambda text, task: nlp_result)

        result = _check_cat1_remediation(tmp_path)
        assert result.passed


# ---------------------------------------------------------------------------
# Post-death verification gate unit tests
# ---------------------------------------------------------------------------

class TestVerifyPidExited:
    def test_returns_true_for_nonexistent_pid(self):
        # A very high PID that almost certainly doesn't exist.
        result = _verify_pid_exited(pid=999999999, poll_interval=0.01, max_wait=0.1)
        assert result is True

    def test_returns_true_after_process_exits(self):
        import subprocess as sp
        proc = sp.Popen(["python", "-c", "pass"], stdout=sp.PIPE, stderr=sp.PIPE)
        proc.wait()
        result = _verify_pid_exited(proc.pid, poll_interval=0.01, max_wait=1.0)
        assert result is True


class TestRunStigCmdWithDeathGate:
    def test_success_path_with_real_python(self, tmp_path):
        success, output = _run_stig_cmd_with_death_gate(
            ["python", "-c", "print('stig-ok')"],
            cwd=tmp_path,
        )
        assert success is True
        assert "stig-ok" in output

    def test_failure_on_nonzero_exit(self, tmp_path):
        success, output = _run_stig_cmd_with_death_gate(
            ["python", "-c", "import sys; sys.exit(1)"],
            cwd=tmp_path,
        )
        assert success is False

    def test_not_found_returns_false(self, tmp_path):
        success, output = _run_stig_cmd_with_death_gate(
            ["__no_such_stig_scanner_binary__"],
            cwd=tmp_path,
        )
        assert success is False
        assert "not found" in output

    def test_gate_rejects_when_pid_still_present(self, tmp_path, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        # Simulate the gate detecting the PID is still present.
        monkeypatch.setattr(mod, "_verify_pid_exited", lambda pid, **kw: False)
        success, output = _run_stig_cmd_with_death_gate(
            ["python", "-c", "pass"],
            cwd=tmp_path,
        )
        assert success is False
        assert "verification FAILED" in output
        assert "backlog" in output

    def test_timeout_returns_false(self, tmp_path):
        # A command that sleeps longer than timeout.
        success, output = _run_stig_cmd_with_death_gate(
            ["python", "-c", "import time; time.sleep(60)"],
            cwd=tmp_path,
            timeout=1,
        )
        assert success is False
        assert "timed out" in output


class TestCheckExternalStigScanner:
    def test_skipped_when_no_scanner_on_path(self, tmp_path, monkeypatch):
        import subprocess as sp
        def fake_run(cmd, **kw):
            raise FileNotFoundError
        monkeypatch.setattr(sp, "run", fake_run)
        result = _check_external_stig_scanner(tmp_path)
        assert result.skipped is True
        assert result.passed is False

    def test_passes_when_scanner_succeeds_and_pid_gone(self, tmp_path, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        import subprocess as sp

        fake_probe = sp.CompletedProcess(args=["oscap", "--version"], returncode=0, stdout=b"OpenSCAP 1.3", stderr=b"")
        monkeypatch.setattr(sp, "run", lambda cmd, **kw: fake_probe)
        monkeypatch.setattr(mod, "_run_stig_cmd_with_death_gate", lambda cmd, **kw: (True, "OpenSCAP 1.3"))

        result = _check_external_stig_scanner(tmp_path)
        assert result.passed is True
        assert "verified gone" in result.message

    def test_gate_failure_surfaces_as_failed_not_backlog(self, tmp_path, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        import subprocess as sp

        fake_probe = sp.CompletedProcess(args=["oscap", "--version"], returncode=0, stdout=b"OpenSCAP 1.3", stderr=b"")
        monkeypatch.setattr(sp, "run", lambda cmd, **kw: fake_probe)
        gate_msg = "Post-death verification FAILED: PID 12345 still present after subprocess reported exit (cmd: oscap). Result rejected — do not promote to backlog."
        monkeypatch.setattr(mod, "_run_stig_cmd_with_death_gate", lambda cmd, **kw: (False, gate_msg))

        result = _check_external_stig_scanner(tmp_path)
        assert result.passed is False
        assert result.skipped is False
        assert "verification FAILED" in result.message


# ---------------------------------------------------------------------------
# PILLAR integration — structure and run
# ---------------------------------------------------------------------------

class TestStigCompliancePillarIntegration:
    def test_pillar_has_expected_criteria(self):
        ids = {c.id for c in PILLAR.criteria}
        assert ids == {"stig-vids-in-code", "stig-in-docs", "stig-checklist", "cat1-remediation", "external-stig-scanner"}

    def test_pillar_id_and_name(self):
        assert PILLAR.id == "stig-compliance"
        assert "STIG" in PILLAR.name

    def test_score_all_pass(self, tmp_path, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: _default_thresholds(nlp_extractor_enabled=False))

        # Create artifacts that satisfy all four criteria via regex alone
        _write(tmp_path, "security.py", "# V-220938 — CAT I — patch applied\n")
        _write(tmp_path, "README.md", "# Compliance\nFollows DISA STIG V-220938.\n")
        _write(tmp_path, "checklist.ckl", "<VULN_ID>V-220938</VULN_ID>\n")

        results = PILLAR.run(tmp_path)
        score = PILLAR.score(results)
        assert score["passed"] == score["total"]

    def test_score_all_fail_with_empty_repo(self, tmp_path, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: _default_thresholds(nlp_extractor_enabled=False))

        results = PILLAR.run(tmp_path)
        score = PILLAR.score(results)
        assert score["passed"] == 0

    def test_nlp_drives_all_pass(self, tmp_path, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.stig_compliance as mod
        monkeypatch.setattr(mod, "_load_thresholds", lambda: _default_thresholds(nlp_extractor_confidence_threshold=0.7))

        # No regex-detectable content — create placeholder files for NLP to scan
        _write(tmp_path, "security.py", "# Comply with applicable security requirements.\n")
        _write(tmp_path, "README.md", "# Security\nAll controls addressed.\n")
        _write(tmp_path, "checklist.ckl", "All findings remediated.\n")
        _write(tmp_path, "docs/remediation.md", "Category one vulnerabilities closed.\n")

        nlp_result = {"found": True, "refs": ["V-220938", "CAT I"], "confidence": 0.9}
        monkeypatch.setattr(mod, "_nlp_extract_stig_refs", lambda text, task: nlp_result)

        results = PILLAR.run(tmp_path)
        score = PILLAR.score(results)
        assert score["passed"] == score["total"]
