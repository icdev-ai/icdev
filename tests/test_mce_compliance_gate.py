# [TEMPLATE: CUI // SP-CTI]
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for tools.migration_canvas.compliance_gate.check_migration_compliance."""


from tools.migration_canvas.compliance_gate import check_migration_compliance


# ── helpers ──────────────────────────────────────────────────────────────────


def _check(il_level, target_env, **kwargs):
    return check_migration_compliance(il_level, target_env, **kwargs)


# ── test cases ───────────────────────────────────────────────────────────────


def test_il5_to_commercial_cloud_is_blocked():
    result = _check("IL5", "commercial")
    assert result["status"] == "block"
    assert result["proceed"] is False
    blocked = [f for f in result["findings"] if f["severity"] == "block"]
    assert blocked, "Expected at least one block-severity finding"
    assert any("commercial" in f["message"].lower() for f in blocked)


def test_il4_to_govcloud_passes():
    result = _check("IL4", "govcloud")
    assert result["status"] == "pass"
    assert result["proceed"] is True
    assert result["findings"] == []


def test_p2c_to_govcloud_without_fedramp_warns():
    # Caller explicitly declares an empty frameworks list — fedramp is absent
    result = _check("IL4", "govcloud", migration_type="p2c", frameworks=[])
    assert result["status"] == "warn"
    messages = [f["message"] for f in result["findings"]]
    assert any("fedramp" in m.lower() for m in messages)


def test_unknown_il_to_dod_env_warns():
    result = _check("IL99", "dod")
    assert result["status"] == "warn"
    assert result["proceed"] is True
    assert result["findings"], "Findings must be non-empty for unknown IL to DoD env"
    assert any("IL99" in f["message"] for f in result["findings"])


def test_il2_to_commercial_passes():
    result = _check("IL2", "commercial")
    assert result["status"] == "pass"
    assert result["proceed"] is True
    assert result["findings"] == []


def test_nist_80053_always_applied():
    for il, env in [("IL2", "commercial"), ("IL4", "govcloud"), ("IL5", "govcloud"), ("UNKNOWN", "dod")]:
        result = _check(il, env)
        assert "nist_800_53" in result["frameworks_applied"], (
            f"nist_800_53 missing from frameworks_applied for il={il} env={env}"
        )


def test_block_disables_proceed_flag():
    # IL4, IL5, IL6 all block commercial cloud; IL6 should too
    for il in ("IL4", "IL5", "IL6"):
        result = _check(il, "commercial")
        assert result["proceed"] is False, f"proceed should be False for {il} to commercial"
        assert result["status"] == "block"


def test_findings_list_populated_on_warn():
    # Two distinct warn scenarios
    warn_scenarios = [
        dict(il_level="IL4", target_env="govcloud", frameworks=[]),       # missing fedramp
        dict(il_level="UNKNOWN_LEVEL", target_env="dod"),                  # unknown IL
    ]
    for kwargs in warn_scenarios:
        result = check_migration_compliance(**kwargs)
        assert result["status"] == "warn", f"Expected warn for {kwargs}"
        assert len(result["findings"]) > 0, f"Findings must be non-empty for {kwargs}"
