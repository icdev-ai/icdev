# CUI // SP-CTI
"""A process review and the bundle preview on whatever the process database is (icx-mdz-03).

In the PG tier (ICDEV_PYTEST_PG=1, tests/pg_tier_allowlist.txt) that is PostgreSQL: a person's
``decl-wft-mdz-process-review`` step is written to the append-only ``wf_decision_steps`` by ``declared.take_step`` and
read back by ``bundle_features.review_states`` (the Modernisation page, the exporter and the MCP tool), and the
modernisation twin previews each application's bundle from ``legacy_applications``. In the normal suite it is the
conftest SQLite database (``wf_db``).

No fixture here redirects the backend on PG or opens a raw connection. The ledger is append-only and may be shared,
so every assertion is scoped to an application id this file mints.
"""
from __future__ import annotations

import os
import uuid

import pytest


def _pg() -> bool:
    return os.environ.get("ICDEV_PYTEST_PG", "").lower() in ("1", "true", "yes")


@pytest.fixture(autouse=True)
def platform_db(request):
    """The conftest SQLite platform DB, unless this is the PG tier (the ambient PG)."""
    return None if _pg() else request.getfixturevalue("wf_db")


def _process(digest: str) -> dict:
    return {"id": "cart-actions", "signature": {"steps": ["view:cart"], "digest": digest, "rules_digest": "r" * 64}}


def test_a_process_review_is_recorded_and_read_back_at_its_digest():
    from tools.modernization import bundle_features, decisions
    from tools.workflow_hitl import declared

    app = f"mdz03-app-{uuid.uuid4().hex[:8]}"
    step = declared.take_step(decisions.TEMPLATE_PROCESS, surface="migration_canvas.api_modernisation_process_review",
                              decision="confirm", subject=bundle_features.subject(app, "cart-actions"),
                              reason="mdz03 ambient: the cart flow", actor={"id": "u-mdz03", "role": "pm"},
                              payload={"app_id": app, "process_id": "cart-actions", "signature_digest": "d" * 64})
    assert step["affirmative"] is True
    procs = [_process("d" * 64)]
    bundle_features.review_states(app, procs)
    assert procs[0]["review"]["state"] == "reviewed" and procs[0]["review"]["by"] == "u-mdz03"
    changed = [_process("e" * 64)]
    bundle_features.review_states(app, changed)
    assert changed[0]["review"]["state"] == "stale", "a review at another digest is not a review of this process"
    other = [_process("d" * 64)]
    bundle_features.review_states(f"{app}-other", other)
    assert other[0]["review"] == {"state": "unreviewed"}


def test_the_twin_previews_the_bundles_it_can_read():
    from tools.twin_core.adapters.modernisation import ModernisationGovernanceTwin

    ev = ModernisationGovernanceTwin().collect()
    if not _pg():   # the conftest SQLite DB has no legacy tables: unverified, never an empty list
        assert ev["bundles"] is None and "legacy_applications" in ev["errors"]["bundles"]
        return
    assert "bundles" not in ev["errors"], ev["errors"]
    assert isinstance(ev["bundles"], list)
    assert all(("error" in b) or {"features", "processes", "missing"} <= set(b) for b in ev["bundles"])
