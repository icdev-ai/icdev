# [CUI // SP-CTI]
"""Tests for tools/network/advisory.py — advisory ingestion and impact assessment."""
import json
import sqlite3
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


# ---------------------------------------------------------------------------
# In-memory SQLite DB with the nc_advisory tables
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nc_advisories (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    cve_id                 TEXT,
    vendor                 TEXT,
    title                  TEXT,
    affected_models_json   TEXT,
    affected_versions_json TEXT,
    severity               TEXT,
    cvss_score             REAL,
    advisory_text          TEXT,
    source_doc_hash        TEXT,
    source_doc_format      TEXT,
    extraction_confidence  REAL,
    created_at             TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS nc_advisory_assessments (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    advisory_id                INTEGER,
    network_id                 TEXT,
    fwd_snapshot_id            TEXT,
    nql_total                  TEXT,
    nql_impacted               TEXT,
    total_devices              INTEGER,
    impacted_count             INTEGER,
    impacted_devices_json      TEXT,
    raw_response_total_json    TEXT,
    raw_response_impacted_json TEXT,
    ai_confidence              REAL,
    reconciliation_warning     INTEGER DEFAULT 0,
    approved_by                TEXT,
    approved_at                TEXT,
    source                     TEXT,
    created_at                 TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS nc_nqe_cache (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nql_hash    TEXT,
    network_id  TEXT,
    result_json TEXT,
    created_at  TEXT,
    expires_at  TEXT
);

CREATE TABLE IF NOT EXISTS nc_nqe_audit_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        TEXT,
    user_session      TEXT,
    action            TEXT,
    input_text        TEXT,
    nql_generated     TEXT,
    fwd_snapshot_id   TEXT,
    result_summary    TEXT,
    raw_response_hash TEXT,
    confidence        REAL,
    source            TEXT,
    created_at        TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS topologies (
    id   TEXT PRIMARY KEY,
    name TEXT
);
"""


def _make_mem_db():
    """Create and return an in-memory SQLite DB with the advisory schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.execute("INSERT INTO topologies VALUES ('net-001', 'Test Network')")
    conn.commit()
    return conn


class _FakeConn:
    """Wrapper that translates %s → ? so advisory.py SQL works against SQLite."""

    def __init__(self, real_conn: sqlite3.Connection) -> None:
        self._conn = real_conn

    def execute(self, sql: str, params=()):
        sql = sql.replace("%s", "?")
        return self._conn.execute(sql, params)

    def executemany(self, sql: str, params=()):
        sql = sql.replace("%s", "?")
        return self._conn.executemany(sql, params)

    def commit(self):
        self._conn.commit()

    def close(self):
        pass  # keep alive


# ---------------------------------------------------------------------------
# Helper: patch _get_conn in advisory module
# ---------------------------------------------------------------------------

def _patch_conn(mem_conn: sqlite3.Connection):
    """Return a context-manager that patches advisory._get_conn."""
    fake = _FakeConn(mem_conn)
    return patch("tools.network.advisory._get_conn", return_value=fake)


# ---------------------------------------------------------------------------
# Tests: _fwd_reachable
# ---------------------------------------------------------------------------

class TestFwdReachable(unittest.TestCase):
    def test_returns_false_when_no_url(self):
        with patch.dict("os.environ", {"FWD_API_URL": "", "FWD_API_KEY": ""}):
            import tools.network.advisory as adv
            adv._FWD_API_URL = ""
            adv._fwd_reach_cache.clear()
            self.assertFalse(adv._fwd_reachable())

    def test_returns_cached_result(self):
        import tools.network.advisory as adv
        adv._fwd_reach_cache.update({"reachable": True, "ts": time.monotonic()})
        self.assertTrue(adv._fwd_reachable())
        adv._fwd_reach_cache.clear()

    def test_returns_bool_type(self):
        import tools.network.advisory as adv
        adv._fwd_reach_cache.update({"reachable": False, "ts": time.monotonic()})
        result = adv._fwd_reachable()
        self.assertIsInstance(result, bool)
        adv._fwd_reach_cache.clear()


# ---------------------------------------------------------------------------
# Tests: get_nqe_client
# ---------------------------------------------------------------------------

class TestGetNqeClient(unittest.TestCase):
    def test_returns_fallback_when_no_credentials(self):
        import tools.network.advisory as adv
        from tools.network.nqe_client import FallbackNQEClient
        adv._FWD_API_KEY = ""
        adv._fwd_reach_cache.clear()
        client = adv.get_nqe_client()
        self.assertIsInstance(client, FallbackNQEClient)

    def test_returns_live_client_when_credentials_and_reachable(self):
        import tools.network.advisory as adv
        from tools.network.nqe_client import NQEClient
        adv._FWD_API_KEY = "test-key"
        adv._FWD_API_URL = "http://fwd.example.com"
        adv._fwd_reach_cache.update({"reachable": True, "ts": time.monotonic()})
        client = adv.get_nqe_client()
        self.assertIsInstance(client, NQEClient)
        adv._FWD_API_KEY = ""
        adv._fwd_reach_cache.clear()

    def test_returns_fallback_when_unreachable(self):
        import tools.network.advisory as adv
        from tools.network.nqe_client import FallbackNQEClient
        adv._FWD_API_KEY = "test-key"
        adv._fwd_reach_cache.update({"reachable": False, "ts": time.monotonic()})
        client = adv.get_nqe_client()
        self.assertIsInstance(client, FallbackNQEClient)
        adv._FWD_API_KEY = ""
        adv._fwd_reach_cache.clear()


# ---------------------------------------------------------------------------
# Tests: NQEClient
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
        # existing FallbackNQEClient returns local_mapping, local_heuristic, or empty
        self.assertIn(result["source"], ("local_mapping", "local_heuristic", "icdev-internal", "empty"))

    def test_run_query_with_network_id(self):
        from tools.network.nqe_client import FallbackNQEClient
        client = FallbackNQEClient()
        result = client.run_query("network.devices", network_id="net-001")
        self.assertIsInstance(result, dict)
        self.assertIn("rows", result)


# ---------------------------------------------------------------------------
# Tests: ingest_advisory
# ---------------------------------------------------------------------------

class TestIngestAdvisory(unittest.TestCase):
    def setUp(self):
        self._mem = _make_mem_db()

    def test_returns_int(self):
        with _patch_conn(self._mem):
            from tools.network.advisory import ingest_advisory
            row_id = ingest_advisory(
                cve_id="CVE-2024-00001",
                affected_models=["ASR9001", "ASR9006"],
                affected_versions=["7.3.1", "7.4.1"],
                advisory_text="Cisco IOS-XR remote code execution vulnerability.",
            )
        self.assertIsInstance(row_id, int)
        self.assertGreater(row_id, 0)

    def test_row_persisted(self):
        with _patch_conn(self._mem):
            from tools.network.advisory import ingest_advisory
            row_id = ingest_advisory(
                cve_id="CVE-2024-00002",
                affected_models=["MX960"],
                affected_versions=["21.4R1"],
                advisory_text="Juniper JunOS DoS vulnerability.",
                source_doc_format="pdf",
                extraction_confidence=0.92,
            )
        row = self._mem.execute(
            "SELECT * FROM nc_advisories WHERE id = ?", (row_id,)
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["cve_id"], "CVE-2024-00002")
        self.assertEqual(row["source_doc_format"], "pdf")
        self.assertAlmostEqual(row["extraction_confidence"], 0.92, places=2)

    def test_affected_models_stored_as_json(self):
        with _patch_conn(self._mem):
            from tools.network.advisory import ingest_advisory
            row_id = ingest_advisory(
                cve_id="CVE-2024-00003",
                affected_models=["PA-5200", "PA-3200"],
                affected_versions=["10.1.0", "10.2.0"],
                advisory_text="Palo Alto PAN-OS vulnerability.",
            )
        row = self._mem.execute(
            "SELECT affected_models_json FROM nc_advisories WHERE id = ?", (row_id,)
        ).fetchone()
        models = json.loads(row["affected_models_json"])
        self.assertIn("PA-5200", models)

    def test_vendor_inferred_from_text(self):
        with _patch_conn(self._mem):
            from tools.network.advisory import ingest_advisory
            row_id = ingest_advisory(
                cve_id="CVE-2024-00004",
                affected_models=[],
                affected_versions=["7.0.0"],
                advisory_text="Juniper Networks BGP flaw.",
            )
        row = self._mem.execute(
            "SELECT vendor FROM nc_advisories WHERE id = ?", (row_id,)
        ).fetchone()
        self.assertEqual(row["vendor"], "juniper")

    def test_default_source_doc_format(self):
        with _patch_conn(self._mem):
            from tools.network.advisory import ingest_advisory
            row_id = ingest_advisory(
                cve_id="CVE-2024-00005",
                affected_models=[],
                affected_versions=[],
                advisory_text="Generic advisory.",
            )
        row = self._mem.execute(
            "SELECT source_doc_format FROM nc_advisories WHERE id = ?", (row_id,)
        ).fetchone()
        self.assertEqual(row["source_doc_format"], "manual")


# ---------------------------------------------------------------------------
# Tests: run_impact_assessment
# ---------------------------------------------------------------------------

class TestRunImpactAssessment(unittest.TestCase):
    def setUp(self):
        self._mem = _make_mem_db()
        # Insert a base advisory to assess
        conn = _FakeConn(self._mem)
        conn.execute(
            "INSERT INTO nc_advisories "
            "(cve_id, vendor, title, affected_models_json, affected_versions_json, advisory_text) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "CVE-2024-99999",
                "cisco",
                "Test Advisory",
                json.dumps(["ASR9001"]),
                json.dumps(["7.3.1"]),
                "Cisco IOS-XR test advisory.",
            ),
        )
        conn.commit()
        row = self._mem.execute(
            "SELECT id FROM nc_advisories WHERE cve_id = 'CVE-2024-99999'"
        ).fetchone()
        self._adv_id = row["id"]

    def _mock_client(self, total=10, impacted=3):
        """Return a MagicMock NQE client that returns fixed counts."""
        client = MagicMock()
        client.run_query.side_effect = [
            {"rows": [], "total": total, "source": "icdev-internal"},   # total NQL
            {"rows": [], "total": impacted, "source": "icdev-internal"},  # Q1
            {"rows": [], "total": impacted, "source": "icdev-internal"},  # Q2
        ]
        return client

    def test_returns_dict(self):
        with _patch_conn(self._mem), \
             patch("tools.network.advisory.get_nqe_client", return_value=self._mock_client()):
            from tools.network.advisory import run_impact_assessment
            result = run_impact_assessment(self._adv_id, "net-001")
        self.assertIsInstance(result, dict)

    def test_has_required_keys(self):
        with _patch_conn(self._mem), \
             patch("tools.network.advisory.get_nqe_client", return_value=self._mock_client()):
            from tools.network.advisory import run_impact_assessment
            result = run_impact_assessment(self._adv_id, "net-001")
        for key in ("total_devices", "impacted_count", "reconciliation_warning", "source"):
            self.assertIn(key, result, f"missing key: {key}")

    def test_total_devices_is_int(self):
        with _patch_conn(self._mem), \
             patch("tools.network.advisory.get_nqe_client", return_value=self._mock_client(total=5)):
            from tools.network.advisory import run_impact_assessment
            result = run_impact_assessment(self._adv_id, "net-001")
        self.assertIsInstance(result["total_devices"], int)
        self.assertEqual(result["total_devices"], 5)

    def test_impacted_count_is_int(self):
        with _patch_conn(self._mem), \
             patch("tools.network.advisory.get_nqe_client", return_value=self._mock_client(impacted=2)):
            from tools.network.advisory import run_impact_assessment
            result = run_impact_assessment(self._adv_id, "net-001")
        self.assertIsInstance(result["impacted_count"], int)
        self.assertEqual(result["impacted_count"], 2)

    def test_reconciliation_warning_false_when_equal(self):
        with _patch_conn(self._mem), \
             patch("tools.network.advisory.get_nqe_client", return_value=self._mock_client(impacted=5)):
            from tools.network.advisory import run_impact_assessment
            result = run_impact_assessment(self._adv_id, "net-001")
        self.assertFalse(result["reconciliation_warning"])

    def test_reconciliation_warning_true_when_diverged(self):
        """Q1 returns 100, Q2 returns 50 → >5% divergence → warning True."""
        client = MagicMock()
        client.run_query.side_effect = [
            {"rows": [], "total": 200, "source": "icdev-internal"},  # total
            {"rows": [], "total": 100, "source": "icdev-internal"},  # Q1
            {"rows": [], "total": 50, "source": "icdev-internal"},   # Q2
        ]
        with _patch_conn(self._mem), \
             patch("tools.network.advisory.get_nqe_client", return_value=client):
            from tools.network.advisory import run_impact_assessment
            result = run_impact_assessment(self._adv_id, "net-001")
        self.assertTrue(result["reconciliation_warning"])

    def test_assessment_persisted_to_db(self):
        with _patch_conn(self._mem), \
             patch("tools.network.advisory.get_nqe_client", return_value=self._mock_client()):
            from tools.network.advisory import run_impact_assessment
            run_impact_assessment(self._adv_id, "net-001")
        row = self._mem.execute(
            "SELECT * FROM nc_advisory_assessments WHERE advisory_id = ?",
            (self._adv_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["network_id"], "net-001")

    def test_audit_log_persisted(self):
        with _patch_conn(self._mem), \
             patch("tools.network.advisory.get_nqe_client", return_value=self._mock_client()):
            from tools.network.advisory import run_impact_assessment
            run_impact_assessment(self._adv_id, "net-001")
        row = self._mem.execute(
            "SELECT * FROM nc_nqe_audit_log WHERE action = 'assess'"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["action"], "assess")

    def test_raises_on_missing_advisory(self):
        with _patch_conn(self._mem):
            from tools.network.advisory import run_impact_assessment
            with self.assertRaises(ValueError):
                run_impact_assessment(99999, "net-001")

    def test_source_field_present(self):
        with _patch_conn(self._mem), \
             patch("tools.network.advisory.get_nqe_client", return_value=self._mock_client()):
            from tools.network.advisory import run_impact_assessment
            result = run_impact_assessment(self._adv_id, "net-001")
        self.assertIn(result["source"], ("fwd-live", "icdev-internal"))


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
        # context has vendor but no models/versions → deterministic fails → LLM/fallback
        result = nl_to_nql("Find all devices", context={"vendor": "cisco"})
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)


# ---------------------------------------------------------------------------
# Tests: _build_template_nql
# ---------------------------------------------------------------------------

class TestBuildTemplateNql(unittest.TestCase):
    def test_cisco_vendor_included(self):
        from tools.network.advisory import _build_template_nql
        nql = _build_template_nql({
            "vendor": "cisco",
            "affected_models_json": json.dumps(["ASR9001"]),
            "affected_versions_json": json.dumps(["7.3.1"]),
        })
        self.assertIn("cisco", nql)
        self.assertIn("ASR9001", nql)

    def test_no_models_no_versions_returns_total(self):
        from tools.network.advisory import _build_template_nql, _TOTAL_NQL
        nql = _build_template_nql({
            "vendor": "",
            "affected_models_json": json.dumps([]),
            "affected_versions_json": json.dumps([]),
        })
        self.assertEqual(nql, _TOTAL_NQL)

    def test_models_only(self):
        from tools.network.advisory import _build_template_nql
        nql = _build_template_nql({
            "vendor": "",
            "affected_models_json": json.dumps(["MX960"]),
            "affected_versions_json": json.dumps([]),
        })
        self.assertIn("MX960", nql)


if __name__ == "__main__":
    unittest.main()
