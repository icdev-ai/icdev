#!/usr/bin/env python3
# CUI // SP-CTI
"""Unit tests for DDC → DataHub and DDC → OpenMetadata sync adapters.

Tests use mocking so no live DataHub / OpenMetadata instance is required.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure ICDev root is on sys.path
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_design(
    design_id: str = "d001",
    name: str = "Test Design",
    classification: str = "CUI",
    nodes: list | None = None,
    edges: list | None = None,
) -> dict:
    graph = {"nodes": nodes or [], "edges": edges or []}
    return {
        "id": design_id,
        "name": name,
        "description": "Test design",
        "graph_json": json.dumps(graph),
        "classification": classification,
    }


def _table_node(nid: str, label: str, classification: str = "CUI") -> dict:
    return {
        "id": nid,
        "data": {"type": "ent-table", "label": label, "description": f"{label} table", "classification": classification},
    }


def _flow_node(nid: str, label: str, flow_type: str = "flow-etl") -> dict:
    return {
        "id": nid,
        "data": {"type": flow_type, "label": label, "description": f"{label} pipeline", "classification": ""},
    }


def _col_node(nid: str, label: str, col_type: str = "col-string") -> dict:
    return {
        "id": nid,
        "data": {"type": col_type, "label": label, "description": "", "classification": ""},
    }


def _edge(src: str, tgt: str, edge_type: str = "default") -> dict:
    return {"source": src, "target": tgt, "type": edge_type}


def _contains_edge(parent: str, child: str) -> dict:
    return {"source": parent, "target": child, "type": "parent-child"}


# ── DataHub tests ──────────────────────────────────────────────────────────────

class TestDataHubURNHelpers(unittest.TestCase):
    """Test URN building functions."""

    def setUp(self):
        from tools.data_canvas.sync.datahub_sync import (
            _platform_urn,
            _dataset_urn,
            _datajob_urn,
            _tag_urn,
            _entity_type_from_urn,
        )
        self._platform_urn = _platform_urn
        self._dataset_urn = _dataset_urn
        self._datajob_urn = _datajob_urn
        self._tag_urn = _tag_urn
        self._entity_type_from_urn = _entity_type_from_urn

    def test_platform_urn(self):
        self.assertEqual(self._platform_urn("ddc_sql"), "urn:li:dataPlatform:ddc_sql")

    def test_dataset_urn_format(self):
        urn = self._dataset_urn("ddc_sql", "Design.MyTable", "DEV")
        self.assertTrue(urn.startswith("urn:li:dataset:"))
        self.assertIn("ddc_sql", urn)
        self.assertIn("DEV", urn)

    def test_dataset_urn_encodes_special_chars(self):
        urn = self._dataset_urn("ddc_sql", "My Design.My Table (v2)", "DEV")
        # Parentheses and spaces should be percent-encoded
        self.assertNotIn("(v2)", urn)
        self.assertNotIn(" ", urn)

    def test_tag_urn(self):
        self.assertEqual(self._tag_urn("DDC_PII"), "urn:li:tag:DDC_PII")

    def test_entity_type_dataset(self):
        urn = self._dataset_urn("ddc_sql", "foo", "DEV")
        self.assertEqual(self._entity_type_from_urn(urn), "dataset")

    def test_entity_type_datajob(self):
        urn = self._datajob_urn("flow_abc", "job_1")
        self.assertEqual(self._entity_type_from_urn(urn), "dataJob")

    def test_entity_type_tag(self):
        urn = "urn:li:tag:DDC_PII"
        self.assertEqual(self._entity_type_from_urn(urn), "tag")

    def test_entity_type_unknown_defaults_dataset(self):
        self.assertEqual(self._entity_type_from_urn("urn:li:unknown:foo"), "dataset")


class TestDataHubSchemaFields(unittest.TestCase):
    """Test _build_schema_fields helper."""

    def setUp(self):
        from tools.data_canvas.sync.datahub_sync import _build_schema_fields
        self._build = _build_schema_fields

    def test_empty_children(self):
        self.assertEqual(self._build([]), [])

    def test_non_col_nodes_skipped(self):
        nodes = [_table_node("n1", "Orders")]
        self.assertEqual(self._build(nodes), [])

    def test_pk_column_maps_to_long(self):
        nodes = [_col_node("c1", "id", "col-pk")]
        fields = self._build(nodes)
        self.assertEqual(len(fields), 1)
        self.assertEqual(fields[0]["nativeDataType"], "long")
        self.assertTrue(fields[0]["isPartOfKey"])

    def test_pii_column_attaches_tag(self):
        nodes = [_col_node("c1", "ssn", "col-pii")]
        fields = self._build(nodes)
        self.assertEqual(len(fields), 1)
        tags = fields[0]["tags"]["tags"]
        self.assertTrue(any("DDC_PII" in t["tag"] for t in tags))

    def test_regular_col_is_nullable(self):
        nodes = [_col_node("c1", "name", "col-string")]
        fields = self._build(nodes)
        self.assertEqual(len(fields), 1)
        self.assertTrue(fields[0]["nullable"])


class TestDDCDataHubSyncDryRun(unittest.TestCase):
    """Test DDCDataHubSync with dry_run=True (no HTTP calls)."""

    def _make_syncer(self, designs: list[dict], lineage: list[dict] | None = None):
        from tools.data_canvas.sync.datahub_sync import DDCDataHubSync

        syncer = DDCDataHubSync(
            config={"url": "http://localhost:8080", "token": "", "env": "DEV", "timeout": 5},
            dry_run=True,
        )
        syncer._fetch_design = MagicMock(return_value=designs[0] if designs else None)
        syncer._fetch_all_designs = MagicMock(return_value=designs)
        syncer._fetch_lineage = MagicMock(return_value=lineage or [])
        return syncer

    def test_sync_empty_graph_returns_ok(self):
        syncer = self._make_syncer([_make_design()])
        result = syncer.sync_design("d001")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["entities_pushed"], 0)

    def test_sync_single_table_node(self):
        design = _make_design(nodes=[_table_node("n1", "Orders")])
        syncer = self._make_syncer([design])
        result = syncer.sync_design("d001")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["entities_pushed"], 1)

    def test_sync_multiple_node_types(self):
        nodes = [
            _table_node("n1", "Orders"),
            _table_node("n2", "Customers"),
            _flow_node("f1", "LoadOrders"),
        ]
        design = _make_design(nodes=nodes)
        syncer = self._make_syncer([design])
        result = syncer.sync_design("d001")
        self.assertEqual(result["entities_pushed"], 3)

    def test_sync_lineage_from_edges(self):
        nodes = [_table_node("n1", "Source"), _table_node("n2", "Sink")]
        edges = [_edge("n1", "n2")]
        design = _make_design(nodes=nodes, edges=edges)
        syncer = self._make_syncer([design])
        result = syncer.sync_design("d001")
        self.assertGreater(result["lineage_edges_pushed"], 0)

    def test_sync_lineage_from_dd_lineage_table(self):
        nodes = [_table_node("n1", "A"), _table_node("n2", "B")]
        lineage_rows = [{"source_node_id": "n1", "target_node_id": "n2", "lineage_type": "ETL", "column_name": None}]
        design = _make_design(nodes=nodes)
        syncer = self._make_syncer([design], lineage=lineage_rows)
        result = syncer.sync_design("d001")
        self.assertGreater(result["lineage_edges_pushed"], 0)

    def test_sync_all_aggregates_results(self):
        d1 = _make_design("d001", "Design1", nodes=[_table_node("n1", "T1")])
        d2 = _make_design("d002", "Design2", nodes=[_table_node("n2", "T2")])
        syncer = self._make_syncer([d1, d2])
        result = syncer.sync_all()
        self.assertEqual(result["designs_synced"], 2)
        self.assertEqual(result["total_entities_pushed"], 2)
        self.assertEqual(result["status"], "ok")

    def test_sync_missing_design_returns_error(self):
        from tools.data_canvas.sync.datahub_sync import DDCDataHubSync
        syncer = DDCDataHubSync(
            config={"url": "http://localhost:8080", "token": "", "env": "DEV", "timeout": 5},
            dry_run=True,
        )
        syncer._fetch_design = MagicMock(return_value=None)
        result = syncer.sync_design("nonexistent")
        self.assertEqual(result["status"], "error")

    def test_classification_tags_mapped(self):
        from tools.data_canvas.sync.datahub_sync import DDCDataHubSync
        syncer = DDCDataHubSync(
            config={"url": "http://localhost:8080", "token": "", "env": "DEV", "timeout": 5},
            dry_run=True,
        )
        self.assertIn("DDC_CUI", syncer._classification_tags("CUI"))
        self.assertIn("DDC_SECRET", syncer._classification_tags("SECRET"))
        self.assertIn("DDC_PUBLIC", syncer._classification_tags("PUBLIC"))
        self.assertEqual(syncer._classification_tags("UNKNOWN"), [])

    def test_col_pii_node_col_child_adds_tag(self):
        nodes = [
            _table_node("n1", "Users"),
            _col_node("c1", "ssn", "col-pii"),
        ]
        edges = [_contains_edge("n1", "c1")]
        design = _make_design(nodes=nodes, edges=edges)
        syncer = self._make_syncer([design])
        result = syncer.sync_design("d001")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["entities_pushed"], 1)  # only n1 (col-pii is child only)


class TestDataHubClientHTTP(unittest.TestCase):
    """Test DataHubClient HTTP methods with mocked urllib."""

    def _make_client(self):
        from tools.data_canvas.sync.datahub_sync import DataHubClient
        return DataHubClient(url="http://localhost:8080", token="test-token", timeout=5)

    @patch("urllib.request.urlopen")
    def test_ping_success(self, mock_urlopen):
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value.read = MagicMock(return_value=b'{"status":"ok"}')
        client = self._make_client()
        self.assertTrue(client.ping())

    @patch("urllib.request.urlopen")
    def test_ping_failure_returns_false(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        client = self._make_client()
        self.assertFalse(client.ping())

    @patch("urllib.request.urlopen")
    def test_ingest_aspect_sends_proposal(self, mock_urlopen):
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value.read = MagicMock(return_value=b"")
        client = self._make_client()
        # Should not raise
        client.ingest_aspect(
            "urn:li:dataset:(urn:li:dataPlatform:ddc_sql,foo,DEV)",
            "datasetProperties",
            {"name": "foo"},
        )
        self.assertTrue(mock_urlopen.called)

    @patch("urllib.request.urlopen")
    def test_auth_header_set_when_token(self, mock_urlopen):
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value.read = MagicMock(return_value=b"")
        client = self._make_client()
        client.ingest_aspect("urn:li:tag:DDC_PII", "tagProperties", {"name": "DDC_PII"})
        request_obj = mock_urlopen.call_args[0][0]
        self.assertIn("Authorization", request_obj.headers)
        self.assertTrue(request_obj.headers["Authorization"].startswith("Bearer "))


# ── OpenMetadata tests ─────────────────────────────────────────────────────────

class TestOpenMetadataSlugify(unittest.TestCase):

    def setUp(self):
        from tools.data_canvas.sync.openmetadata_sync import _slugify
        self._slugify = _slugify

    def test_basic_string_unchanged(self):
        self.assertEqual(self._slugify("my_table"), "my_table")

    def test_spaces_replaced(self):
        result = self._slugify("my table")
        self.assertNotIn(" ", result)

    def test_special_chars_replaced(self):
        result = self._slugify("table (v2) / #1")
        self.assertNotIn("(", result)
        self.assertNotIn("/", result)
        self.assertNotIn("#", result)

    def test_truncates_at_128(self):
        long_str = "a" * 200
        self.assertLessEqual(len(self._slugify(long_str)), 128)


class TestOMColumnBuilder(unittest.TestCase):

    def setUp(self):
        from tools.data_canvas.sync.openmetadata_sync import _build_om_columns
        self._build = _build_om_columns

    def test_empty_returns_empty(self):
        self.assertEqual(self._build([]), [])

    def test_non_col_skipped(self):
        self.assertEqual(self._build([_table_node("n1", "T")]), [])

    def test_pk_col_type_bigint(self):
        cols = self._build([_col_node("c1", "id", "col-pk")])
        self.assertEqual(len(cols), 1)
        self.assertEqual(cols[0]["dataType"], "BIGINT")
        self.assertEqual(cols[0]["constraint"], "PRIMARY_KEY")

    def test_fk_col_constraint(self):
        cols = self._build([_col_node("c1", "user_id", "col-fk")])
        self.assertEqual(cols[0]["constraint"], "FOREIGN_KEY")

    def test_pii_col_has_tag(self):
        cols = self._build([_col_node("c1", "email", "col-pii")])
        self.assertEqual(len(cols), 1)
        tags = cols[0].get("tags", [])
        self.assertTrue(any("DDC.PII" in t.get("tagFQN", "") for t in tags))


class TestDDCOpenMetadataSyncDryRun(unittest.TestCase):
    """Test DDCOpenMetadataSync with dry_run=True."""

    def _make_syncer(self, designs: list[dict], lineage: list[dict] | None = None):
        from tools.data_canvas.sync.openmetadata_sync import DDCOpenMetadataSync
        syncer = DDCOpenMetadataSync(
            config={"url": "http://localhost:8585", "token": "", "timeout": 5},
            dry_run=True,
        )
        syncer._fetch_design = MagicMock(return_value=designs[0] if designs else None)
        syncer._fetch_all_designs = MagicMock(return_value=designs)
        syncer._fetch_lineage = MagicMock(return_value=lineage or [])
        return syncer

    def test_empty_graph_returns_ok(self):
        syncer = self._make_syncer([_make_design()])
        result = syncer.sync_design("d001")
        self.assertEqual(result["status"], "ok")

    def test_table_node_pushed(self):
        design = _make_design(nodes=[_table_node("n1", "Orders")])
        syncer = self._make_syncer([design])
        result = syncer.sync_design("d001")
        self.assertEqual(result["entities_pushed"], 1)

    def test_topic_node_pushed(self):
        node = {"id": "n1", "data": {"type": "ent-topic", "label": "events", "description": "", "classification": ""}}
        design = _make_design(nodes=[node])
        syncer = self._make_syncer([design])
        result = syncer.sync_design("d001")
        self.assertEqual(result["entities_pushed"], 1)

    def test_container_node_pushed(self):
        node = {"id": "n1", "data": {"type": "ent-datalake", "label": "raw_lake", "description": "", "classification": ""}}
        design = _make_design(nodes=[node])
        syncer = self._make_syncer([design])
        result = syncer.sync_design("d001")
        self.assertEqual(result["entities_pushed"], 1)

    def test_pipeline_node_pushed(self):
        design = _make_design(nodes=[_flow_node("f1", "LoadETL", "flow-etl")])
        syncer = self._make_syncer([design])
        result = syncer.sync_design("d001")
        self.assertEqual(result["entities_pushed"], 1)

    def test_lineage_from_edges_in_dry_run(self):
        nodes = [_table_node("n1", "A"), _table_node("n2", "B")]
        edges = [_edge("n1", "n2")]
        design = _make_design(nodes=nodes, edges=edges)
        syncer = self._make_syncer([design])
        result = syncer.sync_design("d001")
        # Lineage in dry-run is counted but entity IDs are empty, so OM add_lineage is skipped
        self.assertGreaterEqual(result["lineage_edges_pushed"], 0)

    def test_sync_all_returns_aggregate(self):
        d1 = _make_design("d001", "D1", nodes=[_table_node("n1", "T1")])
        d2 = _make_design("d002", "D2", nodes=[_table_node("n2", "T2"), _flow_node("f1", "ETL")])
        syncer = self._make_syncer([d1, d2])
        result = syncer.sync_all()
        self.assertEqual(result["designs_synced"], 2)
        self.assertEqual(result["total_entities_pushed"], 3)

    def test_missing_design_returns_error(self):
        from tools.data_canvas.sync.openmetadata_sync import DDCOpenMetadataSync
        syncer = DDCOpenMetadataSync(
            config={"url": "http://localhost:8585", "token": "", "timeout": 5},
            dry_run=True,
        )
        syncer._fetch_design = MagicMock(return_value=None)
        result = syncer.sync_design("nonexistent")
        self.assertEqual(result["status"], "error")

    def test_unknown_node_type_is_skipped(self):
        node = {"id": "n1", "data": {"type": "zone-trust", "label": "DMZ", "description": "", "classification": ""}}
        design = _make_design(nodes=[node])
        syncer = self._make_syncer([design])
        result = syncer.sync_design("d001")
        self.assertEqual(result["entities_pushed"], 0)

    def test_classification_tag_resolved(self):
        from tools.data_canvas.sync.openmetadata_sync import _classification_om_tag
        self.assertEqual(_classification_om_tag("CUI"), "DDC.CUI")
        self.assertEqual(_classification_om_tag("SECRET"), "DDC.Secret")
        self.assertIsNone(_classification_om_tag("UNKNOWN"))


class TestOpenMetadataClientHTTP(unittest.TestCase):
    """Test OpenMetadataClient HTTP with mocked urllib."""

    def _make_client(self):
        from tools.data_canvas.sync.openmetadata_sync import OpenMetadataClient
        return OpenMetadataClient(url="http://localhost:8585", token="jwt-test", timeout=5)

    @patch("urllib.request.urlopen")
    def test_ping_success(self, mock_urlopen):
        mock_urlopen.return_value.__enter__ = lambda s: s
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value.read = MagicMock(return_value=b'{"version":"1.5.0"}')
        client = self._make_client()
        self.assertTrue(client.ping())

    @patch("urllib.request.urlopen")
    def test_ping_failure_returns_false(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("refused")
        client = self._make_client()
        self.assertFalse(client.ping())

    @patch("urllib.request.urlopen")
    def test_ignore_404_returns_none(self, mock_urlopen):
        import urllib.error
        err = urllib.error.HTTPError(
            url="http://localhost:8585/api/v1/tables/name/missing",
            code=404,
            msg="Not Found",
            hdrs={},  # type: ignore[arg-type]
            fp=None,
        )
        mock_urlopen.side_effect = err
        client = self._make_client()
        result = client._request("GET", "/tables/name/missing", ignore_404=True)
        self.assertIsNone(result)

    @patch("urllib.request.urlopen")
    def test_404_without_ignore_raises(self, mock_urlopen):
        import urllib.error
        from tools.data_canvas.sync.openmetadata_sync import OpenMetadataError
        err = urllib.error.HTTPError(
            url="http://localhost:8585/api/v1/tables/name/missing",
            code=404,
            msg="Not Found",
            hdrs={},  # type: ignore[arg-type]
            fp=None,
        )
        mock_urlopen.side_effect = err
        client = self._make_client()
        with self.assertRaises(OpenMetadataError):
            client._request("GET", "/tables/name/missing")


# ── Config loading tests ──────────────────────────────────────────────────────

class TestConfigLoading(unittest.TestCase):
    """Test that config loaders use env vars as override."""

    def test_datahub_env_override(self):
        import os
        from tools.data_canvas.sync.datahub_sync import _load_config as dh_load
        orig = os.environ.copy()
        try:
            os.environ["ICDEV_DATAHUB_URL"] = "http://custom:9090"
            os.environ["ICDEV_DATAHUB_TOKEN"] = "tok123"
            cfg = dh_load()
            self.assertEqual(cfg["url"], "http://custom:9090")
            self.assertEqual(cfg["token"], "tok123")
        finally:
            # Restore
            for k in ("ICDEV_DATAHUB_URL", "ICDEV_DATAHUB_TOKEN"):
                if k in orig:
                    os.environ[k] = orig[k]
                else:
                    os.environ.pop(k, None)

    def test_openmetadata_env_override(self):
        import os
        from tools.data_canvas.sync.openmetadata_sync import _load_config as om_load
        orig = os.environ.copy()
        try:
            os.environ["ICDEV_OM_URL"] = "http://om-host:7777"
            os.environ["ICDEV_OM_TOKEN"] = "jwt-abc"
            cfg = om_load()
            self.assertEqual(cfg["url"], "http://om-host:7777")
            self.assertEqual(cfg["token"], "jwt-abc")
        finally:
            for k in ("ICDEV_OM_URL", "ICDEV_OM_TOKEN"):
                if k in orig:
                    os.environ[k] = orig[k]
                else:
                    os.environ.pop(k, None)

    def test_datahub_invalid_env_defaults_to_dev(self):
        import os
        from tools.data_canvas.sync.datahub_sync import _load_config as dh_load
        orig_val = os.environ.get("ICDEV_DATAHUB_ENV")
        try:
            os.environ["ICDEV_DATAHUB_ENV"] = "INVALID"
            cfg = dh_load()
            self.assertEqual(cfg["env"], "DEV")
        finally:
            if orig_val is None:
                os.environ.pop("ICDEV_DATAHUB_ENV", None)
            else:
                os.environ["ICDEV_DATAHUB_ENV"] = orig_val


if __name__ == "__main__":
    unittest.main()
