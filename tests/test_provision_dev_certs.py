# [TEMPLATE: CUI // SP-CTI]
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for tools.a2a.provision_dev_certs — cross-platform dev TLS provisioning."""

from tools.a2a.provision_dev_certs import provision, AGENT_NAMES


class TestProvisionDevCerts:
    """provision_dev_certs: generate CA + per-agent certificates."""

    def test_generates_all_certs(self, tmp_path):
        """Provision creates CA and all agent certs in the certs dir."""
        # Override CERTS_DIR to a temp location so we don't pollute the real one
        import tools.a2a.provision_dev_certs as mod
        original_certs_dir = mod.CERTS_DIR
        mod.CERTS_DIR = tmp_path / "test_certs"
        try:
            result = provision(clean=True)
            assert result["ca_exists"] is True
            assert (mod.CERTS_DIR / "ca.crt").exists()
            assert (mod.CERTS_DIR / "ca.key").exists()
            assert result["all_exist"] is True
            for name in AGENT_NAMES:
                info = result["agents"][name]
                assert info["exists"] is True
                assert Path(info["cert"]).exists()
                assert Path(info["key"]).exists()
        finally:
            mod.CERTS_DIR = original_certs_dir

    def test_check_mode_reports_missing(self, tmp_path):
        """Check mode returns all_exist=False when certs are missing."""
        import tools.a2a.provision_dev_certs as mod
        original_certs_dir = mod.CERTS_DIR
        mod.CERTS_DIR = tmp_path / "empty_certs"
        try:
            result = provision(check=True)
            assert result["all_exist"] is False
            assert result["ca_exists"] is False
        finally:
            mod.CERTS_DIR = original_certs_dir

    def test_check_mode_reports_present(self, tmp_path):
        """Check mode returns all_exist=True after generation."""
        import tools.a2a.provision_dev_certs as mod
        original_certs_dir = mod.CERTS_DIR
        mod.CERTS_DIR = tmp_path / "present_certs"
        try:
            provision(clean=True)
            result = provision(check=True)
            assert result["all_exist"] is True
            assert result["ca_exists"] is True
        finally:
            mod.CERTS_DIR = original_certs_dir

    def test_clean_regenerates(self, tmp_path):
        """Clean mode removes old certs and regenerates."""
        import tools.a2a.provision_dev_certs as mod
        original_certs_dir = mod.CERTS_DIR
        mod.CERTS_DIR = tmp_path / "regen_certs"
        try:
            provision(clean=False)
            first_ca = (mod.CERTS_DIR / "ca.crt").read_bytes()
            provision(clean=True)
            second_ca = (mod.CERTS_DIR / "ca.crt").read_bytes()
            assert first_ca != second_ca
        finally:
            mod.CERTS_DIR = original_certs_dir


# [TEMPLATE: CUI // SP-CTI]
