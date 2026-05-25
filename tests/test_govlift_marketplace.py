# CUI // SP-CTI
"""Tests for tools.govlift.marketplace — Phase 3 Runbook Marketplace."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _govlift_db(tmp_path, monkeypatch):
    """Isolated SQLite DB with govlift schema for every test.

    get_connection() reads env vars at call time, so monkeypatch is sufficient.
    """
    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

    from tools.govlift.db.init_db import init_govlift_db
    init_govlift_db()
    yield db_path


@pytest.fixture()
def template():
    from tools.govlift.runbook_engine import create_template
    return create_template(
        name="STIG RHEL9 Remediation",
        category="stig_remediation",
        steps=[
            {"name": "Run baseline scan", "description": "DISA STIG CAT1 check", "depends_on": None},
            {"name": "Apply remediations", "description": "Automated fixes", "depends_on": 1},
            {"name": "Re-scan and verify", "description": "Confirm CAT1 closed", "depends_on": 2},
        ],
        description="Automated STIG remediation for RHEL 9",
        workload_type="web_app",
        author="stig-team",
    )


@pytest.fixture()
def submitted_item(template):
    from tools.govlift.marketplace import publish_template
    return publish_template(
        template_id=template["id"],
        author="stig-team",
        description="Community STIG runbook for RHEL 9",
    )


@pytest.fixture()
def approved_item(submitted_item):
    from tools.govlift.marketplace import approve_item
    return approve_item(
        submitted_item["id"],
        reviewer="security-officer",
        notes="Reviewed and approved — CAT1 checks validated",
    )


# ---------------------------------------------------------------------------
# Publish / submit tests
# ---------------------------------------------------------------------------


def test_publish_template_returns_id(submitted_item):
    assert submitted_item.get("id", "").startswith("mp-")


def test_publish_template_status_is_submitted(submitted_item):
    assert submitted_item["status"] == "submitted"


def test_publish_template_author_stored(submitted_item):
    assert submitted_item["author"] == "stig-team"


def test_publish_template_invalid_id_raises():
    from tools.govlift.marketplace import publish_template
    with pytest.raises(ValueError, match="not found"):
        publish_template("rbt-nonexistent", author="x")


def test_publish_template_sets_title_from_template(submitted_item, template):
    assert submitted_item["title"] == template["name"]


# ---------------------------------------------------------------------------
# List / get tests
# ---------------------------------------------------------------------------


def test_list_items_returns_list(submitted_item):
    from tools.govlift.marketplace import list_items
    items = list_items()
    assert isinstance(items, list)
    assert any(i["id"] == submitted_item["id"] for i in items)


def test_list_items_filter_by_status(submitted_item):
    from tools.govlift.marketplace import list_items
    results = list_items(status="submitted")
    assert all(i["status"] == "submitted" for i in results)
    assert any(i["id"] == submitted_item["id"] for i in results)


def test_list_items_filter_excludes_wrong_status(submitted_item):
    from tools.govlift.marketplace import list_items
    results = list_items(status="approved")
    assert all(i["id"] != submitted_item["id"] for i in results)


def test_list_items_filter_by_author(submitted_item):
    from tools.govlift.marketplace import list_items
    results = list_items(author="stig-team")
    assert any(i["id"] == submitted_item["id"] for i in results)


def test_get_item_returns_dict(submitted_item):
    from tools.govlift.marketplace import get_item
    item = get_item(submitted_item["id"])
    assert item["id"] == submitted_item["id"]
    assert item["title"] == "STIG RHEL9 Remediation"


def test_get_item_nonexistent_returns_empty():
    from tools.govlift.marketplace import get_item
    result = get_item("mp-nonexistent")
    assert result == {}


# ---------------------------------------------------------------------------
# Approve / reject tests
# ---------------------------------------------------------------------------


def test_approve_item_transitions_status(approved_item):
    assert approved_item["status"] == "approved"


def test_approve_item_stores_reviewer(approved_item):
    assert approved_item["reviewed_by"] == "security-officer"


def test_approve_item_stores_notes(approved_item):
    assert "CAT1 checks" in approved_item["review_notes"]


def test_approve_item_sets_published_at(approved_item):
    assert approved_item["published_at"] is not None


def test_approve_nonexistent_raises():
    from tools.govlift.marketplace import approve_item
    with pytest.raises(ValueError, match="not found"):
        approve_item("mp-nonexistent", reviewer="admin")


def test_reject_item_transitions_status(submitted_item):
    from tools.govlift.marketplace import reject_item
    result = reject_item(submitted_item["id"], reviewer="officer", notes="Missing STIG mapping")
    assert result["status"] == "rejected"
    assert result["review_notes"] == "Missing STIG mapping"


def test_reject_nonexistent_raises():
    from tools.govlift.marketplace import reject_item
    with pytest.raises(ValueError, match="not found"):
        reject_item("mp-nonexistent", reviewer="officer")


def test_approve_already_approved_raises(approved_item):
    from tools.govlift.marketplace import approve_item
    with pytest.raises(ValueError, match="Can only approve items"):
        approve_item(approved_item["id"], reviewer="other-officer")


# ---------------------------------------------------------------------------
# Download tests
# ---------------------------------------------------------------------------


def test_download_approved_item_returns_template(approved_item):
    from tools.govlift.marketplace import download_item
    result = download_item(approved_item["id"])
    assert "template" in result
    assert result["template"].get("name") == "STIG RHEL9 Remediation"


def test_download_increments_counter(approved_item):
    from tools.govlift.marketplace import download_item, get_item
    download_item(approved_item["id"])
    item = get_item(approved_item["id"])
    assert item["downloads"] >= 1


def test_download_not_approved_raises(submitted_item):
    from tools.govlift.marketplace import download_item
    with pytest.raises(ValueError, match="Only approved items can be downloaded"):
        download_item(submitted_item["id"])


def test_download_nonexistent_raises():
    from tools.govlift.marketplace import download_item
    with pytest.raises(ValueError, match="not found"):
        download_item("mp-nonexistent")


# ---------------------------------------------------------------------------
# Stats tests
# ---------------------------------------------------------------------------


def test_get_stats_returns_dict(submitted_item):
    from tools.govlift.marketplace import get_stats
    stats = get_stats()
    assert isinstance(stats, dict)
    assert "total_items" in stats
    assert "approved_items" in stats
    assert "total_downloads" in stats


def test_get_stats_counts_items(submitted_item):
    from tools.govlift.marketplace import get_stats
    stats = get_stats()
    assert stats["total_items"] >= 1


def test_get_stats_approved_count(approved_item):
    from tools.govlift.marketplace import get_stats
    stats = get_stats()
    assert stats["approved_items"] >= 1


def test_get_stats_empty_db():
    from tools.govlift.marketplace import get_stats
    stats = get_stats()
    assert stats["total_downloads"] >= 0
