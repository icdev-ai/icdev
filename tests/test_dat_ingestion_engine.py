# CUI // SP-CTI
"""Tests for the DAT ingestion pipeline (task-07b5fecc70-d1).

Verifies the acceptance criterion: the engine reads from all three configured
source locations and outputs a unified JSON manifest with per-source record counts.
"""

import json
from pathlib import Path

import pytest
import yaml

# Ensure repo root on path
import sys
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.dat.ingestion_engine import (
    build_manifest,
    read_backchannel_logs,
    read_state_dept_cables,
    read_unsc_schedule,
    run,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def cables_dir(tmp_path):
    d = tmp_path / "cables"
    d.mkdir()
    (d / "q1.csv").write_text(
        "cable_id,origin,classification\n"
        "C001,Embassy Tokyo,CUI\n"
        "C002,Embassy Paris,CUI\n",
        encoding="utf-8",
    )
    (d / "q2.csv").write_text(
        "cable_id,origin,classification\n"
        "C003,Embassy Nairobi,CUI\n",
        encoding="utf-8",
    )
    return d


@pytest.fixture()
def unsc_dir(tmp_path):
    d = tmp_path / "unsc"
    d.mkdir()
    (d / "may.json").write_text(
        json.dumps({"meetings": [{"id": "M1"}, {"id": "M2"}, {"id": "M3"}]}),
        encoding="utf-8",
    )
    (d / "june.json").write_text(
        json.dumps({"meetings": [{"id": "M4"}]}),
        encoding="utf-8",
    )
    return d


@pytest.fixture()
def backchannel_dir(tmp_path):
    d = tmp_path / "backchannel"
    d.mkdir()
    (d / "feed1.log").write_text(
        "# header comment\n"
        "2026-05-01T00:00:00Z | channel=alpha | ref=BC001\n"
        "2026-05-02T00:00:00Z | channel=beta  | ref=BC002\n"
        "\n",
        encoding="utf-8",
    )
    (d / "feed2.log").write_text(
        "2026-05-03T00:00:00Z | channel=gamma | ref=BC003\n",
        encoding="utf-8",
    )
    return d


# ---------------------------------------------------------------------------
# Unit tests — individual readers
# ---------------------------------------------------------------------------


def test_read_state_dept_cables(cables_dir):
    result = read_state_dept_cables({"source_path": str(cables_dir)})
    assert result["status"] == "ok"
    assert result["record_count"] == 3  # 2 + 1 data rows
    assert result["files_read"] == 2
    assert result["errors"] == []


def test_read_unsc_schedule(unsc_dir):
    result = read_unsc_schedule({"source_path": str(unsc_dir), "records_key": "meetings"})
    assert result["status"] == "ok"
    assert result["record_count"] == 4  # 3 + 1
    assert result["files_read"] == 2


def test_read_backchannel_logs(backchannel_dir):
    result = read_backchannel_logs({"source_path": str(backchannel_dir), "comment_prefix": "#"})
    assert result["status"] == "ok"
    assert result["record_count"] == 3  # comments and blanks excluded
    assert result["files_read"] == 2


def test_read_cables_missing_path():
    result = read_state_dept_cables({"source_path": "/nonexistent/path"})
    assert result["status"] == "path_not_found"
    assert result["record_count"] == 0


def test_read_unsc_missing_path():
    result = read_unsc_schedule({"source_path": "/nonexistent/path"})
    assert result["status"] == "path_not_found"
    assert result["record_count"] == 0


def test_read_backchannel_missing_path():
    result = read_backchannel_logs({"source_path": "/nonexistent/path"})
    assert result["status"] == "path_not_found"
    assert result["record_count"] == 0


# ---------------------------------------------------------------------------
# Unit test — manifest builder
# ---------------------------------------------------------------------------


def test_build_manifest():
    cables = {"source": "state_dept_cables", "status": "ok", "record_count": 5, "files_read": 1, "errors": []}
    unsc = {"source": "unsc_schedule", "status": "ok", "record_count": 3, "files_read": 1, "errors": []}
    back = {"source": "backchannel_logs", "status": "ok", "record_count": 7, "files_read": 2, "errors": []}
    manifest = build_manifest(cables, unsc, back, "args/dat_config.yaml")

    assert manifest["total_records"] == 15
    assert manifest["status"] == "ok"
    assert manifest["classification"] == "CUI"
    assert "manifest_id" in manifest
    assert "run_at" in manifest
    assert set(manifest["sources"].keys()) == {"state_dept_cables", "unsc_schedule", "backchannel_logs"}


# ---------------------------------------------------------------------------
# Integration test — full pipeline (acceptance criterion)
# ---------------------------------------------------------------------------


def test_full_pipeline_reads_all_three_sources_and_produces_manifest(
    cables_dir, unsc_dir, backchannel_dir, tmp_path
):
    """Acceptance criterion: reads all 3 sources, outputs unified manifest with counts."""
    manifest_path = tmp_path / "manifest.json"
    cfg = {
        "state_dept_cables": {"source_path": str(cables_dir), "enabled": True},
        "unsc_schedule": {"source_path": str(unsc_dir), "records_key": "meetings", "enabled": True},
        "backchannel_logs": {"source_path": str(backchannel_dir), "comment_prefix": "#", "enabled": True},
        "output": {"manifest_path": str(manifest_path)},
        "database": {"path": str(tmp_path / "dat.db"), "audit_table": "dat_ingestion_log"},
    }
    cfg_file = tmp_path / "dat_config.yaml"
    cfg_file.write_text(yaml.dump(cfg), encoding="utf-8")

    result = run(config_path=cfg_file, dry_run=False)

    # All three sources present
    assert "state_dept_cables" in result["sources"]
    assert "unsc_schedule" in result["sources"]
    assert "backchannel_logs" in result["sources"]

    # Each source has a non-negative record count
    for source_name, source_data in result["sources"].items():
        assert source_data["record_count"] >= 0, f"{source_name} count should be non-negative"

    # Total equals sum of individual counts
    assert result["total_records"] == sum(
        v["record_count"] for v in result["sources"].values()
    )

    # Manifest written to disk
    assert manifest_path.exists()
    on_disk = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert on_disk["total_records"] == result["total_records"]
