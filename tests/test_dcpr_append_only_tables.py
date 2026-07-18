# CUI // SP-CTI
"""dcpr-db-03 — verify Data Design Canvas audit/run-log tables are protected.

The APPEND_ONLY_TABLES list lives inside
``is_append_only_table_modification`` in ``.claude/hooks/pre_tool_use.py``.
Because that module sits under a dotted directory, it is loaded by file path.
The test exercises the public function: a UPDATE/DELETE Bash command against a
protected table must be blocked (return True); a table deliberately excluded
because it is legitimately mutated at runtime must NOT be blocked.
"""

import importlib.util
from pathlib import Path

import pytest

_HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "pre_tool_use.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("dcpr_pre_tool_use", _HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Tables added by dcpr-db-03 (audit trails + insert-only run/scan logs).
PROTECTED = [
    "dd_audit",
    "dm_audit",
    "dm_policy_audit_log",
    "dm_csp_sync_log",
    "dm_contract_test_runs",
    "dd_query_history",
    "dd_anomaly_runs",
    "dd_quality_runs",
    "dd_pii_scans",
]

# Deliberately EXCLUDED — legitimately mutated/deleted at runtime.
EXCLUDED = [
    "dd_lineage",              # blueprint deletes lineage edges
    "ddc_runbook_executions",  # blueprint updates status + deletes
]


@pytest.mark.parametrize("table", PROTECTED)
def test_protected_tables_block_delete(table):
    mod = _load_hook()
    assert mod.is_append_only_table_modification(
        "Bash", {"command": f"sqlite3 data/data_canvas.db 'DELETE FROM {table} WHERE id=1'"}
    ) is True


@pytest.mark.parametrize("table", PROTECTED)
def test_protected_tables_block_update(table):
    mod = _load_hook()
    assert mod.is_append_only_table_modification(
        "Bash", {"command": f"sqlite3 data/data_canvas.db 'UPDATE {table} SET x=1'"}
    ) is True


@pytest.mark.parametrize("table", EXCLUDED)
def test_excluded_tables_not_blocked(table):
    mod = _load_hook()
    assert mod.is_append_only_table_modification(
        "Bash", {"command": f"sqlite3 data/data_canvas.db 'DELETE FROM {table} WHERE id=1'"}
    ) is False
