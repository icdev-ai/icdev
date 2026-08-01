# CUI // SP-CTI
"""IQE WFC adapter — Workflow Forms Canvas collections.

Collections:
    wfc.forms         — studio_forms table
    wfc.workflows     — studio_workflows table
    wfc.submissions   — studio_form_submissions table
    wfc.templates     — built-in FORM_TEMPLATES list

Tenant filtering (cnr-wfc-04(d)): the backing studio_forms / studio_workflows /
studio_form_submissions tables carry NO tenant_id or classification column, so
there is nothing to filter on at the SQL level — a WHERE tenant_id=? clause
would raise on every query. Row-level tenant isolation for these tables is a
schema change (add tenant_id + backfill + RLS predicate) tracked as a separate
platform item and is intentionally NOT attempted here. This matches the known
studio/proposal tenant-column gap noted elsewhere in the platform.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools.iqe.adapters import IQEAdapter
from tools.db.storage import get_connection

_COLLECTIONS = [
    "wfc.forms",
    "wfc.workflows",
    "wfc.submissions",
    "wfc.templates",
]


class WFCAdapter(IQEAdapter):
    def list_collections(self) -> list[str]:
        return _COLLECTIONS

    def get_collection(self, collection: str, topology_id: str | None = None) -> list[dict]:
        if collection == "wfc.forms":
            return self._query("SELECT * FROM studio_forms ORDER BY updated_at DESC LIMIT 200")
        if collection == "wfc.workflows":
            return self._query("SELECT * FROM studio_workflows ORDER BY updated_at DESC LIMIT 200")
        if collection == "wfc.submissions":
            return self._query(
                "SELECT * FROM studio_form_submissions ORDER BY submitted_at DESC LIMIT 500"
            )
        if collection == "wfc.templates":
            from tools.studio.form_builder import FORM_TEMPLATES
            return FORM_TEMPLATES
        return []

    def _query(self, sql: str) -> list[dict]:
        conn = get_connection()
        try:
            rows = conn.execute(sql).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []
        finally:
            conn.close()
