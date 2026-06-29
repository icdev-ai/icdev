# CUI // SP-CTI
"""Tests for NQEClient, FallbackNQEClient, and NQL translator (nqe-infra-08)."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

_REPO = Path(__file__).parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")


# ─── NQEClient ───────────────────────────────────────────────────────────────

class TestNQEClient:
    """Tests for the live NQEClient (Forward Networks API wrapper)."""

    def _client(self):
        from tools.network.nqe_client import NQEClient
        return NQEClient(api_key="test-key-abc", base_url="https://fwd.example.com")

    def test_client_sets_authorization_header(self):
        """NQEClient must send Authorization: Bearer <api_key>."""
        client = self._client()
        headers = client._headers()
        assert headers["Authorization"] == "Bearer test-key-abc"

    def test_client_sets_content_type_json(self):
        """Content-Type header must be application/json."""
        client = self._client()
        assert client._headers()["Content-Type"] == "application/json"

    def test_payload_includes_nql_query(self):
        """Payload must include query field with NQL string."""
        client = self._client()
        payload = client._payload("network.devices[platform.ostype]", None)
        assert payload["query"] == "network.devices[platform.ostype]"

    def test_payload_includes_network_id_when_set(self):
        """network_id passed to payload as networkId when supplied."""
        client = self._client()
        payload = client._payload("network.devices", "topo-abc")
        assert payload["networkId"] == "topo-abc"

    def test_payload_omits_network_id_when_none(self):
        """network_id omitted from payload when not supplied."""
        client = self._client()
        payload = client._payload("network.devices", None)
        assert "networkId" not in payload

    def test_run_query_returns_rows_on_success(self):
        """run_query returns dict with rows, total, source on HTTP 200."""
        client = self._client()
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {"items": [{"hostname": "rtr-1"}], "total": 1}
        with patch("tools.http.client.request", return_value=fake_resp):
            result = client.run_query("network.devices")
        assert result["source"] == "fwd-live"
        assert len(result["rows"]) == 1
        assert result["rows"][0]["hostname"] == "rtr-1"

    def test_run_query_returns_error_on_http_failure(self):
        """run_query returns error key when HTTP call raises."""
        client = self._client()
        with patch("tools.http.client.request", side_effect=ConnectionError("refused")):
            result = client.run_query("network.devices")
        assert "error" in result
        assert result["rows"] == []

    def test_run_query_returns_error_on_bad_json(self):
        """run_query returns error key when response JSON parse fails."""
        client = self._client()
        fake_resp = MagicMock()
        fake_resp.raise_for_status.return_value = None
        fake_resp.json.side_effect = ValueError("not json")
        with patch("tools.http.client.request", return_value=fake_resp):
            result = client.run_query("network.devices")
        assert "error" in result


# ─── FallbackNQEClient ───────────────────────────────────────────────────────

class TestFallbackNQEClient:
    """Tests for FallbackNQEClient local IQE fallback."""

    def _client(self):
        from tools.network.nqe_client import FallbackNQEClient
        return FallbackNQEClient()  # no NQE_BASE_URL → local-only

    def test_empty_query_returns_empty_rows(self):
        """Empty/blank NQL returns {rows:[], source:'empty', error:...}."""
        client = self._client()
        result = client.run_query("")
        assert result["rows"] == []
        assert result["source"] == "empty"

    def test_run_query_returns_dict_with_required_keys(self):
        """run_query always returns rows, iqe, source, nql, error."""
        client = self._client()
        result = client.run_query("network.devices[platform.ostype]")
        for key in ("rows", "iqe", "source", "nql", "error"):
            assert key in result, f"Missing key: {key}"

    def test_mapped_nql_returns_local_source(self):
        """NQL with a known mapping returns source='local_mapping'."""
        client = self._client()
        with patch("tools.network.nqe_client._NQL_TO_IQE", {"network.devices": "foreach d in network.devices select {d.label}"}), \
             patch("tools.network.nqe_client._run_iqe_local", return_value=[{"label": "rtr-1"}]):
            result = client.run_query("network.devices")
        assert result["source"] == "local_mapping"
        assert result["rows"] == [{"label": "rtr-1"}]

    def test_unmapped_nql_returns_empty_source(self):
        """Completely unknown NQL path returns source='empty' and no rows."""
        client = self._client()
        with patch("tools.network.nqe_client._NQL_TO_IQE", {}):
            result = client.run_query("network.unknown_collection_xyz")
        assert result["rows"] == []
        assert result["source"] == "empty"

    def test_foreach_passthrough_runs_local(self):
        """IQE foreach syntax passes through to _run_iqe_local as heuristic."""
        client = self._client()
        iqe = "foreach d in network.devices select {d.label}"
        with patch("tools.network.nqe_client._run_iqe_local", return_value=[{"label": "sw-1"}]):
            result = client.run_query(iqe)
        assert result["source"] == "local_heuristic"
        assert result["rows"] == [{"label": "sw-1"}]


# ─── NQL Translator ──────────────────────────────────────────────────────────

class TestNqlTranslator:
    """Tests for nl_to_nql — natural language → NQL."""

    def test_empty_text_returns_fallback(self):
        """Empty input returns the default broad device query."""
        from tools.network.nql_translator import nl_to_nql
        result = nl_to_nql("")
        assert "network.devices" in result

    def test_blank_text_returns_fallback(self):
        """Whitespace-only input returns the default broad device query."""
        from tools.network.nql_translator import nl_to_nql
        result = nl_to_nql("   ")
        assert "network.devices" in result

    def test_context_with_vendor_builds_deterministic_nql(self):
        """Structured context with vendor produces deterministic NQL (no LLM)."""
        from tools.network.nql_translator import nl_to_nql
        context = {"vendor": "cisco", "affected_models": ["ASR9001"], "affected_versions": ["7.3.1"]}
        result = nl_to_nql("affected devices", context=context)
        assert "network.devices" in result
        assert "cisco" in result.lower() or "ASR9001" in result

    def test_context_without_models_returns_empty_or_fallback(self):
        """Context with no models/versions → deterministic path yields '' → LLM or fallback."""
        from tools.network.nql_translator import nl_to_nql
        # No models/versions → _context_to_nql returns '' → falls through to LLM/fallback
        with patch("tools.llm.get_router", side_effect=ImportError):
            result = nl_to_nql("show BGP peers", context={"vendor": "cisco"})
        assert isinstance(result, str)

    def test_llm_path_strips_code_fences(self):
        """NQL inside ```nql…``` code fences is extracted correctly."""
        from tools.network.nql_translator import nl_to_nql
        mock_router = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = "```nql\nforeach d in network.devices select {d.hostname}\n```"
        mock_router.invoke.return_value = mock_resp
        with patch("tools.llm.get_router", return_value=mock_router):
            result = nl_to_nql("list all devices")
        assert result.startswith("foreach")
        assert "```" not in result

    def test_llm_non_foreach_response_returns_fallback(self):
        """LLM response that doesn't start with 'foreach' triggers fallback."""
        from tools.network.nql_translator import nl_to_nql
        mock_router = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = "I cannot translate that query."
        mock_router.invoke.return_value = mock_resp
        with patch("tools.llm.get_router", return_value=mock_router):
            result = nl_to_nql("nonsense input please")
        assert "network.devices" in result
