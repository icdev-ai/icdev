#!/usr/bin/env python3
# CUI // SP-CTI
"""Pytest: A2A callback to parent ICDEV verification.

Tests the callback client's JSON-RPC 2.0 payload construction and
error handling when communicating with parent ICDEV.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent


class TestA2ACallback:
    """Test A2A callback client functionality."""

    def test_callback_client_exists(self):
        """A2A callback client should exist."""
        client_path = BASE_DIR / "tools" / "a2a" / "icdev_callback_client.py"
        assert client_path.exists(), "icdev_callback_client.py not found"

    def test_callback_client_compiles(self):
        """Callback client should be valid Python."""
        import py_compile
        client_path = BASE_DIR / "tools" / "a2a" / "icdev_callback_client.py"
        if client_path.exists():
            py_compile.compile(str(client_path), doraise=True)

    @patch.dict("os.environ", {"ICDEV_PARENT_CALLBACK_URL": ""})
    def test_no_url_returns_error(self):
        """call_parent should return error when URL not configured."""
        import importlib.util
        client_path = BASE_DIR / "tools" / "a2a" / "icdev_callback_client.py"
        if not client_path.exists():
            pytest.skip("No callback client")
        spec = importlib.util.spec_from_file_location("callback", client_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        result = mod.call_parent("test.method")
        assert "error" in result

    @patch("urllib.request.urlopen")
    @patch.dict("os.environ", {
        "ICDEV_PARENT_CALLBACK_URL": "https://parent:8443/a2a"
    })
    def test_json_rpc_payload(self, mock_urlopen):
        """call_parent should send valid JSON-RPC 2.0 payload."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps(
            {"jsonrpc": "2.0", "id": "test", "result": {"ok": True}}
        ).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        import importlib.util
        client_path = BASE_DIR / "tools" / "a2a" / "icdev_callback_client.py"
        if not client_path.exists():
            pytest.skip("No callback client")
        spec = importlib.util.spec_from_file_location("callback", client_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        mod.call_parent("system.ping", {"echo": "hello"})
        assert mock_urlopen.called
        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        payload = json.loads(request.data.decode())
        assert payload["jsonrpc"] == "2.0"
        assert payload["method"] == "system.ping"
        assert "id" in payload

    @patch("urllib.request.urlopen")
    @patch.dict("os.environ", {
        "ICDEV_PARENT_CALLBACK_URL": "https://parent:8443/a2a"
    })
    def test_json_rpc_params_included(self, mock_urlopen):
        """call_parent should include params in JSON-RPC payload."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = json.dumps(
            {"jsonrpc": "2.0", "id": "test", "result": {"ok": True}}
        ).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        import importlib.util
        client_path = BASE_DIR / "tools" / "a2a" / "icdev_callback_client.py"
        if not client_path.exists():
            pytest.skip("No callback client")
        spec = importlib.util.spec_from_file_location("callback", client_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        test_params = {"project_id": "proj-123", "status": "complete"}
        mod.call_parent("task.complete", test_params)

        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        payload = json.loads(request.data.decode())
        assert payload.get("params") == test_params

    def test_health_check_no_url(self):
        """check_health should return False when URL not configured."""
        import importlib.util
        client_path = BASE_DIR / "tools" / "a2a" / "icdev_callback_client.py"
        if not client_path.exists():
            pytest.skip("No callback client")
        spec = importlib.util.spec_from_file_location("callback", client_path)
        mod = importlib.util.module_from_spec(spec)
        with patch.dict("os.environ", {"ICDEV_PARENT_CALLBACK_URL": ""}):
            spec.loader.exec_module(mod)
            assert mod.check_health() is False
