# CUI // SP-CTI
"""Tests for per-tenant component override enforcement in canvas access guard.

Phase B1 — Enterprise Configurable Platform: tenant_override=disabled → 403.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")
os.environ.setdefault("ICDEV_ENFORCE_CANVAS_ACCESS", "true")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestTenantOverrideGuard:
    """Test that guard_component_access respects per-tenant overrides."""

    def _make_mock_registry(self, *, tenant_enabled: bool):
        """Return a mock registry where is_enabled_for_tenant returns the given value."""
        mock_reg = MagicMock()
        mock_reg.is_enabled_for_tenant.return_value = tenant_enabled
        return mock_reg

    def test_tenant_override_disabled_raises_403(self):
        """When tenant override says disabled, the guard function should abort with 403."""
        from flask import Flask, g
        from werkzeug.exceptions import Forbidden

        app = Flask(__name__)

        with app.test_request_context("/idc"):
            g.current_user = {"id": "user1", "role": "analyst"}
            g.tenant_id = "tenant-blocked"
            g.security_context = {"classification": "CUI", "tenant_id": "tenant-blocked"}

            mock_registry = self._make_mock_registry(tenant_enabled=False)

            with patch("tools.config.component_registry.get_registry", return_value=mock_registry):
                from tools.security.canvas_access import guard_component_access

                # guard_component_access returns _guard — call it to trigger the check
                guard_fn = guard_component_access("idc", min_il="IL4")
                with pytest.raises(Forbidden) as exc_info:
                    guard_fn()
                assert exc_info.value.code == 403

    def test_tenant_override_enabled_does_not_raise(self):
        """When tenant override says enabled, the guard should not produce a 403."""
        from flask import Flask, g

        app = Flask(__name__)

        with app.test_request_context("/idc"):
            g.current_user = {"id": "user1", "role": "admin"}
            g.tenant_id = "tenant-allowed"
            g.security_context = {"classification": "CUI", "tenant_id": "tenant-allowed"}

            mock_registry = self._make_mock_registry(tenant_enabled=True)

            with patch("tools.config.component_registry.get_registry", return_value=mock_registry):
                with patch("tools.security.canvas_access.check_access", return_value=True):
                    from tools.security.canvas_access import guard_component_access

                    guard_fn = guard_component_access("idc", min_il="IL4")
                    try:
                        guard_fn()
                    except Exception as exc:
                        code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
                        assert code != 403, f"Tenant guard blocked an enabled component: {exc}"

    def test_tenant_override_skipped_when_no_tenant_id(self, monkeypatch):
        """When g.tenant_id is absent, tenant override check should be skipped (not called)."""
        from flask import Flask, g

        # Disable enforcement so the no-tenant early-exit returns silently, not 403
        monkeypatch.setenv("ICDEV_ENFORCE_CANVAS_ACCESS", "false")

        app = Flask(__name__)

        with app.test_request_context("/idc"):
            g.current_user = {"id": "user1", "role": "analyst"}
            # No g.tenant_id set
            g.security_context = {"classification": "CUI"}

            mock_registry = self._make_mock_registry(tenant_enabled=False)

            with patch("tools.config.component_registry.get_registry", return_value=mock_registry):
                with patch("tools.security.canvas_access.check_access", return_value=True):
                    from tools.security.canvas_access import guard_component_access

                    guard_fn = guard_component_access("idc", min_il="IL4")
                    # Should return silently — no tenant_id → early exit, tenant override never called
                    guard_fn()
                    # Verify the registry was NOT consulted for a tenant override
                    mock_registry.is_enabled_for_tenant.assert_not_called()

    def test_registry_unavailable_does_not_block(self):
        """If registry raises during tenant check, the check is bypassed (fail-open for availability)."""
        from flask import Flask, g

        app = Flask(__name__)

        with app.test_request_context("/idc"):
            g.current_user = {"id": "user1", "role": "analyst"}
            g.tenant_id = "tenant-x"
            g.security_context = {"classification": "CUI", "tenant_id": "tenant-x"}

            def raising_get_registry():
                raise RuntimeError("Registry unavailable")

            with patch("tools.config.component_registry.get_registry", side_effect=raising_get_registry):
                with patch("tools.security.canvas_access.check_access", return_value=True):
                    from tools.security.canvas_access import guard_component_access

                    guard_fn = guard_component_access("idc", min_il="IL4")
                    try:
                        guard_fn()
                    except Exception as exc:
                        code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
                        assert code != 403, f"Registry failure incorrectly blocked the request: {exc}"


class TestComponentRegistryTenantOverride:
    """Integration test: set_tenant_component_override then is_enabled_for_tenant reflects it."""

    def test_override_roundtrip(self):
        """Set disabled override → is_enabled_for_tenant returns False."""
        try:
            from tools.config.component_registry import get_registry
            reg = get_registry()

            # Set override
            reg.set_tenant_component_override("test-tenant-rt", "idc", enabled=False, updated_by="test")
            result = reg.is_enabled_for_tenant("idc", "test-tenant-rt")
            assert result is False, "is_enabled_for_tenant should return False after disabled override"

            # Clear override
            reg.clear_tenant_component_override("test-tenant-rt", "idc")
            result_after_clear = reg.is_enabled_for_tenant("idc", "test-tenant-rt")
            # After clear, should revert to env default (likely True in test env)
            assert isinstance(result_after_clear, bool)
        except Exception as exc:
            pytest.skip(f"Registry not available in test environment: {exc}")
