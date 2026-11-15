# CUI // SP-CTI
"""Fixtures for the ACE tests. Helpers live in tests/ace/_dispatch_support.py."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.ace._dispatch_support import TOPIC, make_role


@pytest.fixture()
def ace_env(wf_db, tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_ACE_DB_URL", str(tmp_path / "ace.db"))
    from icdev.tools.ace.db.init_db import init as init_ace_db

    init_ace_db()

    from icdev.tools.ace import auto_dispatch, trust_calibrator
    from icdev.tools.ace.controller import ACEController

    roles = [make_role("evt_auto", auto=(TOPIC,)), make_role("evt_plain")]
    monkeypatch.setattr(auto_dispatch, "_roles",
                        lambda roles_=None: roles if roles_ is None else roles_)
    trust = {"evt_auto": 0.7, "evt_plain": 0.7}
    monkeypatch.setattr(trust_calibrator, "get_trust_score", lambda rid: trust.get(rid, 0.5))

    launches: list[dict] = []

    class _Controller:
        def launch(self, **kwargs):
            launches.append(kwargs)
            return f"inst-{len(launches)}"

    monkeypatch.setattr(ACEController, "get_instance", classmethod(lambda cls: _Controller()))

    from icdev.tools.ace.event_dispatcher import ACEEventDispatcher

    return SimpleNamespace(roles=roles, trust=trust, launches=launches,
                           dispatcher=ACEEventDispatcher(roles=roles))
