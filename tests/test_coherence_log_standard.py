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

    def test_check_id(self, tmp_path, monkeypatch):
        from tools.workflow import coherence_checker as mod
        monkeypatch.setattr(mod, "PROJECT_ROOT", tmp_path)
        (tmp_path / "tools").mkdir(parents=True)
        result = mod.check_log_standard_compliance()
        assert result.check_id == "log_standard"

    def test_registered_in_check_registry(self):
        from tools.workflow.coherence_checker import CHECK_REGISTRY
        assert "log_standard" in CHECK_REGISTRY
