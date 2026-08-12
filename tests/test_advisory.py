# [CUI // SP-CTI]
"""Tests for tools/network/advisory.py and the NQE client stack it sits on.

History (tsg-dead-01): this file used to specify a *different* advisory module —
``ingest_advisory``, ``run_impact_assessment``, ``_fwd_reachable``,
``get_nqe_client``, ``_build_template_nql`` and a ``_fwd_reach_cache`` global.
None of those symbols has ever existed in ``tools/network/advisory.py``; they
were added in the same commit as a module implementing a different API, so the
24 tests covering them failed on their first run and every run after.

That superseded design also assumed the ``nc_advisories`` shape from migration
220 (``affected_models_json``, ``cvss_score``, ``extraction_confidence``). The
canvas initialiser in ``tools/network/db/init_db.py`` defines a *different*
``nc_advisories`` — the one ``advisory.py`` writes and the one the live
``/network/advisory-history`` page and CSV export read. The tests below specify
that live module instead of the one that was never built.

Coverage retained from the original file: NQEClient, FallbackNQEClient and
nql_translator, all of which exercise real, shipped code.
"""
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tests._sql_compat import translating  # noqa: E402


# ---------------------------------------------------------------------------
# In-memory SQLite standing in for the network canvas DB.
#
# Mirrors the nc_advisories columns defined in tools/network/db/init_db.py —
# the schema advisory.py actually writes. CHECK constraints are inlined here
# (init_db.py carries them as @@CK@@ macros expanded at init time).
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nc_advisories (
    id                   TEXT PRIMARY KEY,
    cve_id               TEXT NOT NULL,
    vendor               TEXT NOT NULL DEFAULT '',
    severity             TEXT NOT NULL DEFAULT 'medium'
                             CHECK(severity IN ('critical','high','medium','low','informational')),
    published_date       TEXT,
    total_devices        INTEGER DEFAULT 0,
    impacted_devices     INTEGER DEFAULT 0,
    remediation_pct      REAL DEFAULT 0.0,
    data_source          TEXT DEFAULT 'manual',
    hitl_status          TEXT DEFAULT 'pending',
    hitl_approved_by     TEXT,
    hitl_approved_at     TEXT,
    description          TEXT,
    remediation_guidance TEXT,
    status               TEXT DEFAULT 'open',
    created_at           TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at           TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def _make_mem_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _translating_conn(mem_conn: sqlite3.Connection):
    """Wrap the in-memory DB in the sanctioned placeholder-translating connection."""
    return translating(mem_conn, unclosable=True)


def _patch_conn(mem_conn: sqlite3.Connection):
    """Patch the get_connection advisory.py imported at module scope.

    Handing runtime code a RAW sqlite3 connection would defeat placeholder
    translation: advisory.py's get_advisory/create_advisory use %s, which raises
    'near "%": syntax error' on bare SQLite. _sql_compat.translating delegates to
    the same translate_sql the runtime storage layer uses, so these tests
    exercise the real translation path rather than a hand-rolled stand-in.
    unclosable=True keeps the in-memory DB alive across the several connections
    advisory.py opens and closes per call.
    """
    return patch(
        "tools.network.advisory.get_connection",
        new=lambda: _translating_conn(mem_conn),
    )


# ---------------------------------------------------------------------------
# Tests: create_advisory / get_advisory
# ---------------------------------------------------------------------------

class TestCreateAdvisory(unittest.TestCase):
    def setUp(self):
        self._mem = _make_mem_db()

    def test_returns_created_row(self):
        with _patch_conn(self._mem):
            from tools.network.advisory import create_advisory
            row = create_advisory({"cve_id": "CVE-2024-00001", "vendor": "cisco"})
        self.assertIsInstance(row, dict)
        self.assertEqual(row["cve_id"], "CVE-2024-00001")
        self.assertEqual(row["vendor"], "cisco")

    def test_assigns_uuid_id(self):
        with _patch_conn(self._mem):
            from tools.network.advisory import create_advisory
            row = create_advisory({"cve_id": "CVE-2024-00002"})
        self.assertIsInstance(row["id"], str)
        self.assertEqual(len(row["id"]), 36)  # uuid4 string form

    def test_row_persisted(self):
        with _patch_conn(self._mem):
            from tools.network.advisory import create_advisory
            row = create_advisory({"cve_id": "CVE-2024-00003", "severity": "critical"})
        stored = self._mem.execute(
            "SELECT * FROM nc_advisories WHERE id = ?", (row["id"],)
        ).fetchone()
        self.assertIsNotNone(stored)
        self.assertEqual(stored["severity"], "critical")

    def test_defaults_applied(self):
        with _patch_conn(self._mem):
            from tools.network.advisory import create_advisory
            row = create_advisory({"cve_id": "CVE-2024-00004"})
        self.assertEqual(row["severity"], "medium")
        self.assertEqual(row["status"], "open")
        self.assertEqual(row["hitl_status"], "pending")
        self.assertEqual(row["data_source"], "manual")

    def test_get_advisory_returns_none_when_absent(self):
        with _patch_conn(self._mem):
            from tools.network.advisory import get_advisory
            self.assertIsNone(get_advisory("no-such-id"))

    def test_get_advisory_roundtrip(self):
        with _patch_conn(self._mem):
            from tools.network.advisory import create_advisory, get_advisory
            created = create_advisory({"cve_id": "CVE-2024-00005", "vendor": "juniper"})
            fetched = get_advisory(created["id"])
        self.assertEqual(fetched["id"], created["id"])
        self.assertEqual(fetched["vendor"], "juniper")


# ---------------------------------------------------------------------------
# Tests: list_advisories / list_vendors
# ---------------------------------------------------------------------------

class TestListAdvisories(unittest.TestCase):
    def setUp(self):
        self._mem = _make_mem_db()
        with _patch_conn(self._mem):
            from tools.network.advisory import create_advisory
            create_advisory({
                "cve_id": "CVE-2024-10001", "vendor": "cisco",
                "severity": "critical", "published_date": "2024-01-15", "status": "open",
            })
            create_advisory({
                "cve_id": "CVE-2024-10002", "vendor": "juniper",
                "severity": "low", "published_date": "2024-06-20", "status": "closed",
            })

    def test_returns_all_by_default(self):
        with _patch_conn(self._mem):
            from tools.network.advisory import list_advisories
            rows = list_advisories()
        self.assertEqual(len(rows), 2)
        self.assertIsInstance(rows[0], dict)

    def test_filter_by_vendor(self):
        with _patch_conn(self._mem):
            from tools.network.advisory import list_advisories
            rows = list_advisories(vendor="cisco")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cve_id"], "CVE-2024-10001")

    def test_filter_by_severity(self):
        with _patch_conn(self._mem):
            from tools.network.advisory import list_advisories
            rows = list_advisories(severity="low")
        self.assertEqual([r["vendor"] for r in rows], ["juniper"])

    def test_filter_by_status(self):
        with _patch_conn(self._mem):
            from tools.network.advisory import list_advisories
            rows = list_advisories(status="closed")
        self.assertEqual(len(rows), 1)

    def test_filter_by_date_range(self):
        with _patch_conn(self._mem):
            from tools.network.advisory import list_advisories
            rows = list_advisories(date_from="2024-06-01", date_to="2024-12-31")
        self.assertEqual([r["cve_id"] for r in rows], ["CVE-2024-10002"])

    def test_ordered_by_published_date_desc(self):
        with _patch_conn(self._mem):
            from tools.network.advisory import list_advisories
            rows = list_advisories()
        self.assertEqual(
            [r["published_date"] for r in rows], ["2024-06-20", "2024-01-15"]
        )

    def test_limit_respected(self):
        with _patch_conn(self._mem):
            from tools.network.advisory import list_advisories
            rows = list_advisories(limit=1)
        self.assertEqual(len(rows), 1)

    def test_list_vendors_distinct_and_sorted(self):
        with _patch_conn(self._mem):
            from tools.network.advisory import list_vendors
            vendors = list_vendors()
        self.assertEqual(vendors, ["cisco", "juniper"])

    def test_list_vendors_excludes_blank(self):
        with _patch_conn(self._mem):
            from tools.network.advisory import create_advisory, list_vendors
            create_advisory({"cve_id": "CVE-2024-10003", "vendor": ""})
            vendors = list_vendors()
        self.assertNotIn("", vendors)


# ---------------------------------------------------------------------------
# Tests: NQEClient (live Forward Networks REST client)
# ---------------------------------------------------------------------------

class TestNQEClient(unittest.TestCase):
    def test_run_query_returns_dict_on_http_error(self):
        from tools.network.nqe_client import NQEClient
        mock_err = MagicMock(side_effect=Exception("connection refused"))
        with patch("tools.http.client.request", mock_err):
            client = NQEClient(api_key="key", base_url="http://invalid.localhost:9999")
            result = client.run_query("foreach d in network.devices select d.hostname")
        self.assertIsInstance(result, dict)
        self.assertIn("rows", result)
        self.assertEqual(result["source"], "fwd-live")

    def test_run_query_success(self):
        from tools.network.nqe_client import NQEClient
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"items": [{"hostname": "rtr-01"}], "total": 1}
        mock_resp.raise_for_status = MagicMock()

        with patch("tools.http.client.request", return_value=mock_resp):
            client = NQEClient(api_key="key", base_url="http://fwd.example.com")
            result = client.run_query("foreach d in network.devices select d.hostname")

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["source"], "fwd-live")
        self.assertEqual(len(result["rows"]), 1)

    def test_run_query_has_total_field(self):
        from tools.network.nqe_client import NQEClient
        with patch("tools.http.client.request", side_effect=Exception("conn")):
            client = NQEClient(api_key="k", base_url="http://bad.localhost:1")
            result = client.run_query("nql")
        self.assertIn("total", result)


# ---------------------------------------------------------------------------
# Tests: FallbackNQEClient
# ---------------------------------------------------------------------------

class TestFallbackNQEClient(unittest.TestCase):
    def test_run_query_returns_dict(self):
        from tools.network.nqe_client import FallbackNQEClient
        client = FallbackNQEClient()
        result = client.run_query("network.devices")
        self.assertIsInstance(result, dict)
        self.assertIn("rows", result)
        self.assertIn("source", result)

    def test_run_query_source_is_local(self):
        from tools.network.nqe_client import FallbackNQEClient
        client = FallbackNQEClient()
        result = client.run_query("network.devices")
        self.assertIn(
            result["source"],
            ("local_mapping", "local_heuristic", "icdev-internal", "empty"),
        )

    def test_run_query_with_network_id(self):
        from tools.network.nqe_client import FallbackNQEClient
        client = FallbackNQEClient()
        result = client.run_query("network.devices", network_id="net-001")
        self.assertIsInstance(result, dict)
        self.assertIn("rows", result)


# ---------------------------------------------------------------------------
# Tests: nql_translator
# ---------------------------------------------------------------------------

class TestNqlTranslator(unittest.TestCase):
    def test_returns_string(self):
        from tools.network.nql_translator import nl_to_nql
        result = nl_to_nql("Find all Cisco devices affected by CVE-2024-1234")
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_returns_foreach_nql(self):
        from tools.network.nql_translator import nl_to_nql
        # Simulate LLM unavailable — patch the import inside the function
        with patch("builtins.__import__", side_effect=ImportError):
            result = nl_to_nql("Find affected devices")
        self.assertTrue(result.lower().startswith("foreach"))

    def test_context_deterministic(self):
        """Template-based NQL generation — the surviving form of _build_template_nql."""
        from tools.network.nql_translator import nl_to_nql
        result = nl_to_nql(
            "Find affected devices",
            context={
                "vendor": "cisco",
                "affected_models": ["ASR9001"],
                "affected_versions": ["7.3.1"],
            },
        )
        self.assertIn("cisco", result)
        self.assertIn("ASR9001", result)
        self.assertIn("7.3.1", result)

    def test_empty_text_returns_fallback(self):
        from tools.network.nql_translator import nl_to_nql
        result = nl_to_nql("")
        self.assertIn("foreach", result.lower())

    def test_context_without_models_falls_through_to_llm(self):
        from tools.network.nql_translator import nl_to_nql
        result = nl_to_nql("Find all devices", context={"vendor": "cisco"})
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)


if __name__ == "__main__":
    unittest.main()
