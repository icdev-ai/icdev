#!/usr/bin/env python3
# CUI // SP-CTI
"""rea-wire-01 — admit the two reverse-engineering provenance event types.

`re_analysis.authorized` and `re_analysis.refused` were added to
`tools/audit/audit_logger.py::VALID_EVENT_TYPES`. `tools/mcp_client/registry.py`
writes one of them for every call to an external RE server: the target's
sha256 and the operator's authorization basis, or the reason it was refused.
Without the rebuilt CHECK the write is rejected on PostgreSQL and the
authorized path refuses the call (it fails closed on a provenance row it could
not write), so the constraint is load-bearing, not cosmetic.

Same shape as 20260808064841_audit_event_types_sbom_revision: the body is
generated from the constant by `rebuild_event_type_constraint`, never respelled
here. SQLite is a no-op (it cannot ALTER a CHECK; fresh databases generate it
at init).
"""


def up(conn):
    from tools.audit.audit_logger import rebuild_event_type_constraint

    rebuilt = rebuild_event_type_constraint(conn)
    if not rebuilt:
        print(
            "  audit_trail.event_type CHECK left as-is (SQLite cannot ALTER a CHECK). "
            "Fresh databases generate it from VALID_EVENT_TYPES."
        )
    return {"constraint_rebuilt": rebuilt}
