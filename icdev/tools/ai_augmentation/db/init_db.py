# CUI // SP-CTI
"""Canvas DB connection for the AI Augmentation Canvas (AAC).

AAC tables (aac_scans, aac_audit_log) are defined in pg_consolidated.sql
and have no classification/tenant_id columns, so get_canvas_connection()
is required to bypass the global RLS predicate.
"""
from tools.db.storage import get_canvas_connection

_ENV_VAR = "AAC_DATABASE_URL"


def get_connection():
    return get_canvas_connection(_ENV_VAR)


def init_db():
    """No-op — AAC tables are created by pg_consolidated.sql / main DB bootstrap."""
