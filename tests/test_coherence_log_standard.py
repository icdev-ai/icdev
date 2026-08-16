# CUI // SP-CTI
"""Unit tests for coherence_checker check_log_standard_compliance (LOG-10)."""
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestCheckLogStandardCompliance:
    def test_pass_when_no_raw_logging(self, tmp_path, monkeypatch):
        from tools.workflow import coherence_checker as mod
        # Patch PROJECT_ROOT so it scans a controlled directory
        monkeypatch.setattr(mod, "PROJECT_ROOT", tmp_path)
        tools_dir = tmp_path / "tools" / "mymodule"
        tools_dir.mkdir(parents=True)
        (tools_dir / "clean.py").write_text(
            "from tools.logging.icdev_logger import get_logger\nlog = get_logger('x')\n",
            encoding="utf-8",
        )
        result = mod.check_log_standard_compliance()
        assert result.status == "pass"
        assert result.missing == []

    def test_fail_when_raw_logging_getLogger(self, tmp_path, monkeypatch):
        from tools.workflow import coherence_checker as mod
        monkeypatch.setattr(mod, "PROJECT_ROOT", tmp_path)
        tools_dir = tmp_path / "tools" / "badmodule"
        tools_dir.mkdir(parents=True)
        (tools_dir / "dirty.py").write_text(
            "import logging\nlog = logging.getLogger('x')\n",
            encoding="utf-8",
        )
        result = mod.check_log_standard_compliance()
        assert result.status == "fail"
        assert len(result.missing) >= 1
        assert any("dirty.py" in v for v in result.missing)

    def test_excludes_logging_package_itself(self, tmp_path, monkeypatch):
        from tools.workflow import coherence_checker as mod
        monkeypatch.setattr(mod, "PROJECT_ROOT", tmp_path)
        logging_dir = tmp_path / "tools" / "logging"
        logging_dir.mkdir(parents=True)
        (logging_dir / "icdev_logger.py").write_text(
            "import logging\nlog = logging.getLogger('x')\n",
            encoding="utf-8",
        )
        result = mod.check_log_standard_compliance()
        # Should NOT flag the logging package itself
        assert result.status == "pass"

    # --- prefer-get_logger-then-fall-back (hcx-vv-02) -------------------------
    #
    # A migration or other bootstrap module may run at a point where
    # `tools.logging` is not importable yet. Preferring get_logger() and dropping
    # to stdlib logging in the handler is the CORRECT shape there — the
    # alternative is no logger at all. The check flagged it, which pushed authors
    # toward deleting the working branch. What must NOT be exempted is a module
    # that merely mentions get_logger somewhere, or a bare fallback that never
    # tried get_logger at all.

    FALLBACK_SRC = (
        "try:\n"
        "    from tools.logging.icdev_logger import get_logger\n"
        "    log = get_logger('x')\n"
        "except Exception:\n"
        "    import logging\n"
        "    log = logging.getLogger('x')\n"
    )

    def _scan(self, tmp_path, monkeypatch, name, src):
        from tools.workflow import coherence_checker as mod
        monkeypatch.setattr(mod, "PROJECT_ROOT", tmp_path)
        tools_dir = tmp_path / "tools" / "mymodule"
        tools_dir.mkdir(parents=True, exist_ok=True)
        (tools_dir / name).write_text(src, encoding="utf-8")
        return mod.check_log_standard_compliance()

    def test_pass_when_raw_logging_is_a_fallback_for_get_logger(self, tmp_path, monkeypatch):
        result = self._scan(tmp_path, monkeypatch, "fallback.py", self.FALLBACK_SRC)
        assert result.status == "pass"
        assert result.missing == []

    def test_fail_when_fallback_never_tried_get_logger(self, tmp_path, monkeypatch):
        # Same except-handler shape, but nothing ever preferred get_logger.
        result = self._scan(
            tmp_path, monkeypatch, "bare_fallback.py",
            "try:\n"
            "    import something\n"
            "except Exception:\n"
            "    import logging\n"
            "    log = logging.getLogger('x')\n",
        )
        assert result.status == "fail"
        assert any("bare_fallback.py" in v for v in result.missing)

    def test_fail_when_raw_call_sits_outside_the_handler(self, tmp_path, monkeypatch):
        # Importing get_logger must not launder an unconditional raw call.
        result = self._scan(
            tmp_path, monkeypatch, "laundered.py",
            "from tools.logging.icdev_logger import get_logger\n"
            "import logging\n"
            "log = logging.getLogger('x')\n",
        )
        assert result.status == "fail"
        assert any("laundered.py" in v for v in result.missing)

    def test_real_migration_with_the_fallback_shape_is_not_flagged(self):
        """The concrete file that failed the gate — asserted by path, not by shape."""
        from tools.workflow.coherence_checker import check_log_standard_compliance
        result = check_log_standard_compliance()
        assert not any(
            "seed_canvas_grants_for_existing_tenants" in v for v in result.missing
        ), result.missing

    def test_check_id(self, tmp_path, monkeypatch):
        from tools.workflow import coherence_checker as mod
        monkeypatch.setattr(mod, "PROJECT_ROOT", tmp_path)
        (tmp_path / "tools").mkdir(parents=True)
        result = mod.check_log_standard_compliance()
        assert result.check_id == "log_standard"

    def test_registered_in_check_registry(self):
        from tools.workflow.coherence_checker import CHECK_REGISTRY
        assert "log_standard" in CHECK_REGISTRY
