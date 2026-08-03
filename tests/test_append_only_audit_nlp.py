# CUI // SP-CTI
"""Tests for append_only_audit pillar — NLP extractor and criteria checks."""
from __future__ import annotations

import pathlib
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mod():
    import tools.ai_augmentation.agent_readiness.pillars.append_only_audit as m
    return m


def _write(path: pathlib.Path, name: str, content: str) -> pathlib.Path:
    f = path / name
    f.write_text(content, encoding="utf-8")
    return f


# ---------------------------------------------------------------------------
# _load_thresholds
# ---------------------------------------------------------------------------

class TestLoadThresholds:
    def test_defaults_when_file_absent(self, tmp_path, monkeypatch):
        m = _mod()
        monkeypatch.setattr(m, "_ARGS_PATH", tmp_path / "nonexistent.yaml")
        m._load_thresholds.cache_clear()
        thresholds = m._load_thresholds()
        assert thresholds["nlp_extractor_enabled"] is True
        assert thresholds["nlp_extractor_model"] == "claude-haiku-4-5-20251001"
        assert thresholds["nlp_extractor_max_tokens"] == 256
        assert thresholds["nlp_extractor_confidence_threshold"] == pytest.approx(0.7)
        assert thresholds["nlp_extractor_text_sample_chars"] == 2000
        m._load_thresholds.cache_clear()

    def test_reads_from_yaml(self, tmp_path, monkeypatch):
        m = _mod()
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text(
            "pillars:\n"
            "  append_only_audit:\n"
            "    nlp_extractor:\n"
            "      enabled: false\n"
            "      model: claude-haiku-4-5-20251001\n"
            "      max_tokens: 128\n"
            "      confidence_threshold: 0.85\n"
            "      text_sample_chars: 1000\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(m, "_ARGS_PATH", cfg)
        m._load_thresholds.cache_clear()
        t = m._load_thresholds()
        assert t["nlp_extractor_enabled"] is False
        assert t["nlp_extractor_max_tokens"] == 128
        assert t["nlp_extractor_confidence_threshold"] == pytest.approx(0.85)
        assert t["nlp_extractor_text_sample_chars"] == 1000
        m._load_thresholds.cache_clear()

    def test_falls_back_on_malformed_yaml(self, tmp_path, monkeypatch):
        m = _mod()
        cfg = tmp_path / "bad.yaml"
        cfg.write_text(":\tbad yaml [", encoding="utf-8")
        monkeypatch.setattr(m, "_ARGS_PATH", cfg)
        m._load_thresholds.cache_clear()
        t = m._load_thresholds()
        assert t["nlp_extractor_enabled"] is True
        m._load_thresholds.cache_clear()


# ---------------------------------------------------------------------------
# _nlp_extract_audit_mutations
# ---------------------------------------------------------------------------

class TestNlpExtractAuditMutations:
    def test_returns_none_when_no_api_key(self, tmp_path, monkeypatch):
        m = _mod()
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        result = m._nlp_extract_audit_mutations("some code", "find mutations")
        assert result is None

    def test_returns_none_when_disabled(self, tmp_path, monkeypatch):
        m = _mod()
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr(
            m, "_load_thresholds",
            lambda: {**m._DEFAULTS, "nlp_extractor_enabled": False},
        )
        result = m._nlp_extract_audit_mutations("some code", "find mutations")
        assert result is None

    def test_returns_none_on_import_error(self, monkeypatch):
        m = _mod()
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr(m, "_load_thresholds", lambda: dict(m._DEFAULTS))
        import builtins
        real_import = builtins.__import__

        def _broken_import(name, *args, **kwargs):
            if "anthropic_provider" in name or "llm.provider" in name:
                raise ImportError("no module")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _broken_import)
        result = m._nlp_extract_audit_mutations("some code", "find mutations")
        assert result is None

    def test_parses_valid_json_response(self, monkeypatch):
        m = _mod()
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr(m, "_load_thresholds", lambda: dict(m._DEFAULTS))

        fake_response = MagicMock()
        fake_response.content = '{"found": true, "refs": ["audit_log.delete()"], "confidence": 0.95}'

        fake_provider = MagicMock()
        fake_provider.invoke.return_value = fake_response

        with patch.dict("sys.modules", {
            "tools.llm.anthropic_provider": MagicMock(AnthropicLLMProvider=MagicMock(return_value=fake_provider)),
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock(return_value=MagicMock())),
        }):
            result = m._nlp_extract_audit_mutations("session.query(AuditLog).delete()", "find mutations")

        assert result is not None
        assert result["found"] is True
        assert "audit_log.delete()" in result["refs"]
        assert result["confidence"] == pytest.approx(0.95)

    def test_returns_none_on_invoke_exception(self, monkeypatch):
        m = _mod()
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr(m, "_load_thresholds", lambda: dict(m._DEFAULTS))

        fake_provider = MagicMock()
        fake_provider.invoke.side_effect = RuntimeError("connection refused")

        with patch.dict("sys.modules", {
            "tools.llm.anthropic_provider": MagicMock(AnthropicLLMProvider=MagicMock(return_value=fake_provider)),
            "tools.llm.provider": MagicMock(LLMRequest=MagicMock(return_value=MagicMock())),
        }):
            result = m._nlp_extract_audit_mutations("some code", "find mutations")

        assert result is None

    def test_truncates_text_to_sample_size(self, monkeypatch):
        m = _mod()
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        thresholds = {**m._DEFAULTS, "nlp_extractor_text_sample_chars": 10}
        monkeypatch.setattr(m, "_load_thresholds", lambda: thresholds)

        captured_prompts = []

        fake_response = MagicMock()
        fake_response.content = '{"found": false, "refs": [], "confidence": 0.1}'
        fake_provider = MagicMock()
        fake_provider.invoke.return_value = fake_response

        class CapturingRequest:
            def __init__(self, messages, max_tokens):
                captured_prompts.append(messages[0]["content"])

        with patch.dict("sys.modules", {
            "tools.llm.anthropic_provider": MagicMock(AnthropicLLMProvider=MagicMock(return_value=fake_provider)),
            "tools.llm.provider": MagicMock(LLMRequest=CapturingRequest),
        }):
            m._nlp_extract_audit_mutations("A" * 200, "task")

        assert len(captured_prompts) == 1
        # The prompt should contain only 10 chars of the code sample
        assert "A" * 10 in captured_prompts[0]
        assert "A" * 11 not in captured_prompts[0]


# ---------------------------------------------------------------------------
# _check_no_audit_mutations
# ---------------------------------------------------------------------------

class TestCheckNoAuditMutations:
    def test_passes_on_clean_files(self, tmp_path):
        _write(tmp_path, "clean.py", "db.execute('INSERT INTO audit_log VALUES (?)', (evt,))\n")
        m = _mod()
        result = m._check_no_audit_mutations(tmp_path)
        assert result.criterion_id == "no-audit-mutations"
        assert result.passed is True

    def test_fails_on_sql_update_of_audit_table(self, tmp_path):
        _write(tmp_path, "bad.py", "cursor.execute('UPDATE audit_log SET x=1 WHERE id=?', (id,))\n")
        m = _mod()
        result = m._check_no_audit_mutations(tmp_path)
        assert result.passed is False
        assert "mutation" in result.message.lower() or "audit" in result.message.lower()

    def test_fails_on_sql_delete_of_log_table(self, tmp_path):
        _write(tmp_path, "bad.py", "DELETE FROM event_log WHERE ts < '2024-01-01';\n")
        m = _mod()
        result = m._check_no_audit_mutations(tmp_path)
        assert result.passed is False

    def test_nlp_called_when_regex_passes(self, tmp_path, monkeypatch):
        _write(tmp_path, "orm.py", "session.query(AuditLog).delete()\n")
        m = _mod()
        nlp_calls = []

        def fake_nlp(text, task):
            nlp_calls.append(text)
            return {"found": True, "refs": ["AuditLog.delete()"], "confidence": 0.95}

        monkeypatch.setattr(m, "_nlp_extract_audit_mutations", fake_nlp)
        monkeypatch.setattr(
            m, "_load_thresholds",
            lambda: {**m._DEFAULTS, "nlp_extractor_confidence_threshold": 0.7},
        )
        result = m._check_no_audit_mutations(tmp_path)
        assert len(nlp_calls) > 0
        assert result.passed is False
        assert "NLP" in result.message

    def test_nlp_not_called_when_regex_already_fails(self, tmp_path, monkeypatch):
        _write(tmp_path, "bad.py", "UPDATE audit_log SET col=1;\n")
        m = _mod()
        nlp_calls = []

        def fake_nlp(text, task):
            nlp_calls.append(text)
            return None

        monkeypatch.setattr(m, "_nlp_extract_audit_mutations", fake_nlp)
        result = m._check_no_audit_mutations(tmp_path)
        assert result.passed is False
        assert len(nlp_calls) == 0

    def test_nlp_low_confidence_does_not_fail(self, tmp_path, monkeypatch):
        _write(tmp_path, "clean.py", "# insert only code\n")
        m = _mod()

        def fake_nlp(text, task):
            return {"found": True, "refs": ["something"], "confidence": 0.3}

        monkeypatch.setattr(m, "_nlp_extract_audit_mutations", fake_nlp)
        monkeypatch.setattr(
            m, "_load_thresholds",
            lambda: {**m._DEFAULTS, "nlp_extractor_confidence_threshold": 0.7},
        )
        result = m._check_no_audit_mutations(tmp_path)
        assert result.passed is True


# ---------------------------------------------------------------------------
# _check_audit_log_inserts
# ---------------------------------------------------------------------------

class TestCheckAuditLogInserts:
    def test_passes_when_insert_found_by_regex(self, tmp_path):
        _write(tmp_path, "logging.py", "db.execute('INSERT INTO audit_log (event) VALUES (?)', (e,))\n")
        m = _mod()
        result = m._check_audit_log_inserts(tmp_path)
        assert result.criterion_id == "audit-log-inserts"
        assert result.passed is True

    def test_fails_when_no_inserts_found(self, tmp_path, monkeypatch):
        _write(tmp_path, "nolog.py", "# nothing here\n")
        m = _mod()
        monkeypatch.setattr(m, "_nlp_extract_audit_mutations", lambda *a, **kw: None)
        result = m._check_audit_log_inserts(tmp_path)
        assert result.passed is False

    def test_nlp_called_when_regex_finds_nothing(self, tmp_path, monkeypatch):
        _write(tmp_path, "orm.py", "session.add(AuditLog(event='login'))\n")
        m = _mod()
        nlp_calls = []

        def fake_nlp(text, task):
            nlp_calls.append(text)
            return {"found": True, "refs": ["session.add(AuditLog(...))"], "confidence": 0.9}

        monkeypatch.setattr(m, "_nlp_extract_audit_mutations", fake_nlp)
        monkeypatch.setattr(
            m, "_load_thresholds",
            lambda: {**m._DEFAULTS, "nlp_extractor_confidence_threshold": 0.7},
        )
        result = m._check_audit_log_inserts(tmp_path)
        assert len(nlp_calls) > 0
        assert result.passed is True
        assert "NLP" in result.message

    def test_nlp_low_confidence_still_fails(self, tmp_path, monkeypatch):
        _write(tmp_path, "orm.py", "session.add(Something())\n")
        m = _mod()

        def fake_nlp(text, task):
            return {"found": True, "refs": ["session.add(Something())"], "confidence": 0.4}

        monkeypatch.setattr(m, "_nlp_extract_audit_mutations", fake_nlp)
        monkeypatch.setattr(
            m, "_load_thresholds",
            lambda: {**m._DEFAULTS, "nlp_extractor_confidence_threshold": 0.7},
        )
        result = m._check_audit_log_inserts(tmp_path)
        assert result.passed is False


# ---------------------------------------------------------------------------
# _check_pre_tool_use_protection
# ---------------------------------------------------------------------------

class TestCheckPreToolUseProtection:
    def test_passes_when_hook_has_guard(self, tmp_path):
        hook = tmp_path / ".claude" / "hooks"
        hook.mkdir(parents=True)
        (hook / "pre_tool_use.py").write_text(
            "APPEND_ONLY_TABLES = ['audit_log']\n", encoding="utf-8"
        )
        m = _mod()
        result = m._check_pre_tool_use_protection(tmp_path)
        assert result.criterion_id == "pre-tool-use-protection"
        assert result.passed is True

    def test_fails_when_no_hook_present(self, tmp_path):
        m = _mod()
        result = m._check_pre_tool_use_protection(tmp_path)
        assert result.passed is False


# ---------------------------------------------------------------------------
# _check_audit_table_schema
# ---------------------------------------------------------------------------

class TestCheckAuditTableSchema:
    def test_passes_when_sql_has_audit_table(self, tmp_path):
        sql_dir = tmp_path / "migrations"
        sql_dir.mkdir()
        (sql_dir / "001_audit.sql").write_text(
            "CREATE TABLE audit_log (id SERIAL, event TEXT, ts TIMESTAMPTZ);\n"
            "INSERT INTO audit_log SELECT * FROM old_log;\n",
            encoding="utf-8",
        )
        m = _mod()
        result = m._check_audit_table_schema(tmp_path)
        assert result.criterion_id == "audit-table-schema"
        assert result.passed is True

    def test_passes_when_aac_audit_log_in_init_db(self, tmp_path):
        db_dir = tmp_path / "tools" / "ai_augmentation" / "db"
        db_dir.mkdir(parents=True)
        (db_dir / "init_db.py").write_text(
            "SQL = 'CREATE TABLE aac_audit_log (id INTEGER PRIMARY KEY)'\n",
            encoding="utf-8",
        )
        m = _mod()
        result = m._check_audit_table_schema(tmp_path)
        assert result.passed is True

    def test_fails_when_no_audit_schema_found(self, tmp_path):
        _write(tmp_path, "nothing.py", "x = 1\n")
        m = _mod()
        result = m._check_audit_table_schema(tmp_path)
        assert result.passed is False


# ---------------------------------------------------------------------------
# PILLAR registration
# ---------------------------------------------------------------------------

class TestPillarRegistration:
    def test_pillar_has_four_criteria(self):
        m = _mod()
        assert len(m.PILLAR.criteria) == 4

    def test_pillar_id(self):
        m = _mod()
        assert m.PILLAR.id == "append-only-audit"

    def test_criterion_ids(self):
        m = _mod()
        ids = {c.id for c in m.PILLAR.criteria}
        assert ids == {
            "no-audit-mutations",
            "pre-tool-use-protection",
            "audit-log-inserts",
            "audit-table-schema",
        }
