# [CUI // SP-CTI]
"""Regression tests for tools/network/remediation_simulator.py Layer 2 (NQE).

tsg-dead-01: ``_run_nqe_layer`` lazily imported ``_fwd_reachable`` and
``get_nqe_client`` from ``tools.network.advisory``. Neither symbol has ever
existed there, so every call raised ImportError, was swallowed by a bare
``except Exception``, and returned ``{"verdict": "skipped", ...}``.

That is what made it dangerous rather than merely broken: "skipped" is also the
correct answer when the Forward Networks API is genuinely unreachable, so a
permanently dead layer was indistinguishable from normal degraded operation.

The guarantees pinned here:
  1. The layer really executes — pass/warn are reachable, not just "skipped".
  2. A wiring defect (missing symbol, wrong signature) RAISES. It must never be
     laundered into "skipped" again.
  3. Every "skipped" carries a ``reason``, so a dead layer is legible.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.network import remediation_simulator as rs  # noqa: E402


class _StubClient:
    """Minimal NQE client honouring the real run_query(nql, network_id=None)."""

    def __init__(self, rows=None, source="local_mapping", error=None):
        self._rows = rows if rows is not None else []
        self._source = source
        self._error = error
        self.calls = []

    def run_query(self, nql, network_id=None):
        self.calls.append((nql, network_id))
        return {
            "rows": self._rows,
            "source": self._source,
            "error": self._error,
            "nql": nql,
        }


def _patch_client(client):
    return patch.object(rs, "_nqe_client", lambda: client)


_DEVICE_ROWS = [
    {"label": "rtr-01", "object_type": "router", "config": {"osVersion": "7.3.1"}},
    {"label": "sw-02", "object_type": "switch", "config": {"osVersion": "9.1.0"}},
]


# ---------------------------------------------------------------------------
# 1. The layer actually runs
# ---------------------------------------------------------------------------

class TestLayerExecutes(unittest.TestCase):
    def test_import_of_real_symbols_succeeds(self):
        """The original defect: this import raised ImportError on every call."""
        client = rs._nqe_client()
        self.assertTrue(hasattr(client, "run_query"))

    def test_live_call_does_not_report_an_import_error(self):
        """Reproduction from the card — must no longer carry a swallowed _error."""
        result = rs._run_nqe_layer(
            {"device_name": "d", "current_version": "1", "project_id": "p"}
        )
        self.assertNotIn("_error", result)
        self.assertIn(result["verdict"], ("pass", "warn", "skipped"))

    def test_query_uses_the_real_run_query_signature(self):
        client = _StubClient(rows=_DEVICE_ROWS)
        with _patch_client(client):
            rs._run_nqe_layer({"device_name": "rtr-01", "current_version": "7.3.1",
                               "project_id": "net-001"})
        self.assertEqual(len(client.calls), 1)
        nql, network_id = client.calls[0]
        self.assertIn("network.devices", nql)
        self.assertEqual(network_id, "net-001")

    def test_warn_when_device_still_reports_current_version(self):
        with _patch_client(_StubClient(rows=_DEVICE_ROWS)):
            result = rs._run_nqe_layer(
                {"device_name": "rtr-01", "current_version": "7.3.1"}
            )
        self.assertEqual(result["verdict"], "warn")
        self.assertEqual(result["findings"][0]["check_id"], "nqe-version-match")

    def test_pass_when_device_has_moved_off_current_version(self):
        with _patch_client(_StubClient(rows=_DEVICE_ROWS)):
            result = rs._run_nqe_layer(
                {"device_name": "rtr-01", "current_version": "7.4.1"}
            )
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(result["findings"], [])

    def test_warn_when_device_absent_from_snapshot(self):
        with _patch_client(_StubClient(rows=_DEVICE_ROWS)):
            result = rs._run_nqe_layer(
                {"device_name": "rtr-99", "current_version": "7.3.1"}
            )
        self.assertEqual(result["verdict"], "warn")
        self.assertEqual(result["findings"][0]["check_id"], "nqe-device-absent")

    def test_device_name_match_is_case_insensitive(self):
        with _patch_client(_StubClient(rows=_DEVICE_ROWS)):
            result = rs._run_nqe_layer(
                {"device_name": "RTR-01", "current_version": "7.3.1"}
            )
        self.assertEqual(result["verdict"], "warn")

    def test_reads_live_fwd_field_names_too(self):
        """A live FWD snapshot uses its own field names, not nc_nodes columns."""
        rows = [{"hostname": "rtr-01", "osVersion": "7.3.1"}]
        with _patch_client(_StubClient(rows=rows)):
            result = rs._run_nqe_layer(
                {"device_name": "rtr-01", "current_version": "7.3.1"}
            )
        self.assertEqual(result["verdict"], "warn")


# ---------------------------------------------------------------------------
# 2. Wiring defects must surface
# ---------------------------------------------------------------------------

class TestWiringErrorsPropagate(unittest.TestCase):
    def test_wrong_signature_raises_instead_of_skipping(self):
        """The exact shape of the original bug: caller and client disagree."""

        class WrongSignature:
            def run_query(self, network_id=None, query=None):
                return {}

        with _patch_client(WrongSignature()):
            with self.assertRaises(TypeError):
                rs._run_nqe_layer({"device_name": "d", "current_version": "1"})

    def test_missing_method_raises_instead_of_skipping(self):
        class NoRunQuery:
            pass

        with _patch_client(NoRunQuery()):
            with self.assertRaises(AttributeError):
                rs._run_nqe_layer({"device_name": "d", "current_version": "1"})

    def test_import_error_raises_instead_of_skipping(self):
        def _boom():
            raise ImportError("no module named nqe_client")

        with patch.object(rs, "_nqe_client", _boom):
            with self.assertRaises(ImportError):
                rs._run_nqe_layer({"device_name": "d", "current_version": "1"})

    def test_transport_error_still_degrades_gracefully(self):
        """Genuine runtime conditions SHOULD still degrade — with a reason."""

        class Exploding:
            def run_query(self, nql, network_id=None):
                raise OSError("connection reset")

        with _patch_client(Exploding()):
            result = rs._run_nqe_layer({"device_name": "d", "current_version": "1"})
        self.assertEqual(result["verdict"], "skipped")
        self.assertEqual(result["reason"], "query_failed")
        self.assertIn("connection reset", result["_error"])


# ---------------------------------------------------------------------------
# 3. "skipped" is always explained
# ---------------------------------------------------------------------------

class TestSkippedAlwaysHasReason(unittest.TestCase):
    def test_no_device_name(self):
        result = rs._run_nqe_layer({"device_name": "", "current_version": "1"})
        self.assertEqual(result["verdict"], "skipped")
        self.assertEqual(result["reason"], "no_device_name")

    def test_no_snapshot_data(self):
        with _patch_client(_StubClient(rows=[])):
            result = rs._run_nqe_layer({"device_name": "d", "current_version": "1"})
        self.assertEqual(result["verdict"], "skipped")
        self.assertEqual(result["reason"], "no_snapshot_data")

    def test_client_reported_error_becomes_the_reason(self):
        with _patch_client(_StubClient(rows=[], source="empty", error="unmapped")):
            result = rs._run_nqe_layer({"device_name": "d", "current_version": "1"})
        self.assertEqual(result["verdict"], "skipped")
        self.assertEqual(result["reason"], "unmapped")

    def test_every_skipped_path_carries_a_reason(self):
        cases = [
            {"device_name": "", "current_version": "1"},
            {"device_name": "d", "current_version": "1"},
        ]
        with _patch_client(_StubClient(rows=[])):
            for row in cases:
                result = rs._run_nqe_layer(row)
                if result["verdict"] == "skipped":
                    self.assertTrue(result.get("reason"), f"no reason for {row}")


# ---------------------------------------------------------------------------
# 4. Client selection
# ---------------------------------------------------------------------------

class TestClientSelection(unittest.TestCase):
    def test_returns_live_client_when_credentials_present(self):
        from tools.network.nqe_client import NQEClient
        with patch.dict(
            "os.environ",
            {"NQE_API_KEY": "test-key", "NQE_BASE_URL": "http://fwd.example.com"},
        ):
            self.assertIsInstance(rs._nqe_client(), NQEClient)

    def test_returns_fallback_when_no_credentials(self):
        from tools.network.nqe_client import FallbackNQEClient
        with patch.dict("os.environ", {"NQE_API_KEY": "", "NQE_BASE_URL": ""}):
            self.assertIsInstance(rs._nqe_client(), FallbackNQEClient)

    def test_returns_fallback_when_only_key_present(self):
        from tools.network.nqe_client import FallbackNQEClient
        with patch.dict("os.environ", {"NQE_API_KEY": "k", "NQE_BASE_URL": ""}):
            self.assertIsInstance(rs._nqe_client(), FallbackNQEClient)


if __name__ == "__main__":
    unittest.main()
