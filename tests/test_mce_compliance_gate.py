# CUI // SP-CTI
"""Tests for tools.migration_canvas.compliance_gate."""
from tools.migration_canvas.compliance_gate import check_migration_compliance


def test_il4_govcloud_passes():
    r = check_migration_compliance("IL4", "AWS GovCloud")
    assert r["status"] in ("pass", "warn")
    assert r["proceed"] is True
    assert "fedramp" in r["frameworks_applied"]


def test_il5_commercial_blocks():
    r = check_migration_compliance("IL5", "commercial")
    assert r["status"] == "block"
    assert r["proceed"] is False
    assert any(f["severity"] == "block" for f in r["findings"])


def test_il6_commercial_blocks():
    r = check_migration_compliance("IL6", "commercial")
    assert r["status"] == "block"
    assert r["proceed"] is False


def test_il2_commercial_passes():
    r = check_migration_compliance("IL2", "commercial")
    assert r["status"] == "pass"
    assert r["proceed"] is True


def test_nist_always_applied():
    r = check_migration_compliance("IL4", "govcloud")
    assert "nist_800_53" in r["frameworks_applied"]


def test_govcloud_adds_fedramp():
    r = check_migration_compliance("IL4", "aws govcloud")
    assert "fedramp" in r["frameworks_applied"]


def test_il5_govcloud_adds_stig():
    r = check_migration_compliance("IL5", "govcloud")
    assert "disa_stig" in r["frameworks_applied"]


def test_missing_fedramp_warns():
    r = check_migration_compliance("IL4", "govcloud", frameworks=["nist_800_53"])
    assert any(f["rule"] == "CGT-002" for f in r["findings"])


def test_unknown_il_warns():
    r = check_migration_compliance("IL99", "govcloud")
    assert r["status"] in ("warn", "block")
    assert any(f["rule"] == "CGT-004" for f in r["findings"])


def test_result_shape():
    r = check_migration_compliance("IL4", "govcloud")
    assert "proceed" in r
    assert "status" in r
    assert "findings" in r
    assert "frameworks_applied" in r
    assert isinstance(r["findings"], list)
    assert isinstance(r["frameworks_applied"], list)
