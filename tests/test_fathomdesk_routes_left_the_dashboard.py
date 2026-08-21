# CUI // SP-CTI
"""P4(a) of the ICDEV[domain] split: the FathomDesk pages and the /api/trading/* routes are no longer part of
the ICDEV[IT] dashboard. They lived on a frozen, cut-over system (the data moved to icdev_ft on 2026-08-21)
and imported tools.trading at request time; the tree itself is removed by the later P4 families, so this
test pins the door shut FIRST -- a route that came back would start importing a package that is leaving.

Red-first: on the pre-change tree every one of these routes is registered, so this file fails there.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GONE = ("/fathomdesk", "/fathomdesk/trap-events", "/fathomdesk/api/traps", "/fathomdesk/api/reflex-observations",
        "/api/trading/market", "/api/trading/chart/<ticker>")


def test_the_dashboard_source_registers_none_of_the_fathomdesk_routes():
    src = (REPO / "tools" / "dashboard" / "app.py").read_text(encoding="utf-8")
    present = [r for r in GONE if re.search(r"@app\.route\(\"" + re.escape(r) + r"\"", src)]
    assert present == [], f"FathomDesk routes still registered in tools/dashboard/app.py: {present}"
    assert "create_ta_blueprint" not in src, "the TA Patterns blueprint hook (tools.trading.ta) is still wired"
    assert "_derive_chart_provenance" not in src, "the /api/trading/chart helper outlived its route"
    # the icdev/ mirror says the same thing
    mirror = (REPO / "icdev" / "tools" / "dashboard" / "app.py").read_text(encoding="utf-8")
    assert not any(re.search(r"@app\.route\(\"" + re.escape(r) + r"\"", mirror) for r in GONE)


def test_the_nav_and_the_page_list_no_longer_point_at_them():
    nav = (REPO / "tools" / "dashboard" / "templates" / "base.html").read_text(encoding="utf-8")
    assert "/fathomdesk" not in nav, "base.html still links to /fathomdesk"
    pages = (REPO / ".claude" / "commands" / "start.md").read_text(encoding="utf-8")
    assert "`/fathomdesk`" not in pages and "`/fathomdesk/trap-events`" not in pages
    for tpl in ("fathomdesk.html", "fathomdesk_trap_events.html"):
        assert not (REPO / "tools" / "dashboard" / "templates" / tpl).exists(), f"{tpl} still ships"
        assert not (REPO / "icdev" / "tools" / "dashboard" / "templates" / tpl).exists(), f"mirror still ships {tpl}"


def test_the_live_app_answers_404_for_every_removed_route():
    import importlib

    app_mod = importlib.import_module("tools.dashboard.app")
    app = app_mod.create_app(testing=True) if hasattr(app_mod, "create_app") else app_mod.app
    app.config["TESTING"] = True
    with app.test_client() as c:
        # the app may send an unauthenticated request for an UNKNOWN page to /login (302) before it 404s;
        # what matters is that a removed route is treated exactly like a path that never existed
        # ... and an unknown /api/* path gets the API's own answer (401), so the baseline is per prefix
        def answer(path: str) -> tuple[int, str]:
            r = c.get(path)
            return r.status_code, r.headers.get("Location", "").split("?")[0]

        baselines = {"/api/": answer("/api/never-existed-xyz"), "": answer("/fathomdesk-never-existed-xyz")}
        for route in GONE:
            path = route.replace("<ticker>", "SPY")
            baseline = baselines["/api/"] if path.startswith("/api/") else baselines[""]
            got = answer(path)
            assert got == baseline, f"{path} answered {got}; an unknown path of that kind answers {baseline} -- the route is still served"
