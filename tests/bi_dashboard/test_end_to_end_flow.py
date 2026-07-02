# CUI // SP-CTI
"""End-to-end smoke test: upload -> generate -> save -> view, all against SQLite."""
from __future__ import annotations

import importlib
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from tools.bi_dashboard.db.init_db import _SCHEMA_SQLITE


@pytest.fixture
def sqlite_conn(tmp_path, monkeypatch):
    """Isolated SQLite DB with the bi_* schema applied, patched into get_connection().

    Patches via importlib.import_module + setattr (not the pytest string form) —
    tools.* / icdev.tools.* are distinct module objects for from-imports, and
    string-path monkeypatching can silently miss the one actually consulted at
    runtime by tools/bi_dashboard/db/init_db.py's local import.
    """
    db_path = tmp_path / "bi_test.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    for stmt in _SCHEMA_SQLITE.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()

    def _get_connection():
        return sqlite3.connect(str(db_path), check_same_thread=False)

    for mod_name in ("tools.db.storage", "icdev.tools.db.storage"):
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        monkeypatch.setattr(mod, "get_connection", _get_connection)

    yield db_path
    conn.close()


def test_upload_ingest_and_query_round_trip(sqlite_conn):
    from tools.bi_dashboard.data_source import get_dataset, ingest_upload, list_datasets

    csv_text = "region,sales\nEast,100\nWest,200\nEast,50\n"
    result = ingest_upload(filename="sales.csv", text=csv_text, name="Sales")
    assert result["success"] is True
    source_id = result["id"]

    datasets = list_datasets()
    assert len(datasets) == 1
    assert datasets[0]["name"] == "Sales"
    assert datasets[0]["row_count"] == 3

    dataset = get_dataset(source_id)
    assert dataset["columns"] == ["region", "sales"]
    assert dataset["dimensions"] == ["region"]
    assert dataset["measures"] == ["sales"]
    assert len(dataset["rows"]) == 3


def test_upload_rejects_xlsm(sqlite_conn):
    from tools.bi_dashboard.data_source import ingest_upload
    result = ingest_upload(filename="macro.xlsm", content_bytes=b"not real content")
    assert result["success"] is False


def test_generate_then_save_then_view_dashboard(sqlite_conn):
    from tools.bi_dashboard.data_source import ingest_upload
    from tools.bi_dashboard.dashboard_store import create_dashboard, get_dashboard
    from tools.bi_dashboard.spec_generator import generate_spec

    csv_text = "region,sales\nEast,100\nWest,200\nEast,50\n"
    upload = ingest_upload(filename="sales.csv", text=csv_text, name="Sales")
    from tools.bi_dashboard.data_source import get_dataset
    dataset = get_dataset(upload["id"])

    router = MagicMock()
    from types import SimpleNamespace
    router.invoke.return_value = SimpleNamespace(
        content='{"kind":"chart","chart_type":"bar","dimension":"region","measures":["sales"],'
                '"title":"Sales by Region","unit":""}'
    )
    with patch("tools.llm.get_router", return_value=router):
        spec, method, structure = generate_spec("show sales by region", dataset)

    assert method == "llm"
    assert spec.categories == ["East", "West"]
    assert spec.series[0].values == [150.0, 200.0]

    dashboard_id = create_dashboard(title="Sales Dashboard", tiles=[{"spec": spec.to_dict(), "w": 12}])
    saved = get_dashboard(dashboard_id)
    assert saved["title"] == "Sales Dashboard"
    assert len(saved["tiles"]) == 1
    assert saved["tiles"][0]["spec"]["categories"] == ["East", "West"]
    assert saved["tiles"][0]["w"] == 12


def test_generation_is_logged(sqlite_conn):
    from tools.bi_dashboard.dashboard_store import log_generation
    log_id = log_generation(prompt="show sales by region",
                            structure={"kind": "chart", "chart_type": "bar"}, method="heuristic")
    assert log_id

    conn = sqlite3.connect(str(sqlite_conn))
    row = conn.execute("SELECT prompt, method FROM bi_generation_log WHERE id = ?", (log_id,)).fetchone()
    conn.close()
    assert row == ("show sales by region", "heuristic")
