# [TEMPLATE: CUI // SP-CTI]
"""Tests for the Black-hat / PTW workspace routes in
tools/dashboard/api/proposals.py (prop-cap-13):
  GET  /opportunities/<id>/ptw/analysis      (rate_benchmarker.ptw_analysis + SCG gate)
  GET  /opportunities/<id>/ptw/leaderboard   (competitor_profiler.get_leaderboard)
  POST /opportunities/<id>/ptw/vendor-profile (competitor_profiler.profile_vendor)
  POST /opportunities/<id>/ptw/bid-score     (bayesian_bid_scorer.score_opportunity)
  GET/POST /opportunities/<id>/ptw/blackhat  (list/create competitor black-hat models)
  PUT/DELETE /blackhat/<id>                  (update/delete a black-hat model)

Backend model (tools/govcon/competitor_profiler.py), PTW analysis
(tools/govcon/rate_benchmarker.py::ptw_analysis), bid scoring
(tools/govcon/bayesian_bid_scorer.py), all 8 routes, the SCG aggregation
warning (prop-sec-03), Bell-LaPadula price masking (prop-sec-01), and the
tools/dashboard/templates/proposals/ptw.html workspace page were already
fully implemented and explicitly tagged "(prop-cap-13)" in the source before
this file was written. Zero pytest-collected coverage existed (only a
Selenium e2e_*.py script, which pytest does not collect). This file closes
that gap.
"""
import sqlite3

import pytest
from flask import Flask, g

from tools.db.storage import translate_sql
from tools.security.security_context import SecurityContext


class _TranslatingConn:
    """Wraps a raw sqlite3 connection, translating %s -> ? before executing.

    Needed for the rate_benchmarker/bayesian_bid_scorer/competitor_profiler
    helper modules the routes call into, which use get_connection() with no
    db_path arg and Postgres-style %s SQL (same root cause as prop-fix-10/11,
    prop-cap-11, prop-cap-12).
    """
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        return self._conn.execute(translate_sql(sql, backend="sqlite"), params)

    def executemany(self, sql, seq):
        return self._conn.executemany(translate_sql(sql, backend="sqlite"), seq)

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    db_file = tmp_path / "ptw_blackhat_test.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE proposal_opportunities (
            id TEXT PRIMARY KEY,
            title TEXT,
            naics_code TEXT,
            classification TEXT DEFAULT 'CUI',
            compartments TEXT DEFAULT '[]'
        );
        CREATE TABLE govcon_awards (
            id TEXT PRIMARY KEY,
            agency TEXT, naics_code TEXT, awardee_name TEXT,
            award_amount REAL, award_date TEXT, set_aside_type TEXT
        );
    """)
    conn.execute(
        "INSERT INTO proposal_opportunities (id, title, naics_code, classification, compartments) "
        "VALUES ('opp-1', 'Test Opportunity', '541512', 'CUI', '[]')"
    )
    conn.commit()
    conn.close()

    import tools.dashboard.api.proposals as proposals_mod

    monkeypatch.setattr(proposals_mod, "DB_PATH", db_file)
    # rate_benchmarker/bayesian_bid_scorer/competitor_profiler call
    # get_connection() with no db_path -- resolved dynamically per-call via
    # this env var (same pattern as prop-cap-12's test_govcon_pwin_api.py).
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_file))
    return db_file


#: The write routes carry ``@require_role("admin", "capture_mgr", "pm")``, which
#: reads ``g.current_user`` and 401s when it is absent. This fixture supplies it
#: the way every other authed blueprint test in the tree does (see
#: ``tests/cortex/test_rest_api.py::make_client``).
#:
#: WHY THIS WAS MISSING (rem-hyg-10). Two PRs landed the same evening on
#: PARALLEL branches: 90d7b0ecf added RBAC to the proposals write endpoints at
#: 19:53, and 146cbb77d added this file at 20:56 against a tree that did not yet
#: have it. Each was green on its own branch and they were jointly broken on
#: merge — a semantic merge conflict no textual check can see. Ten of the twenty
#: tests here have returned 401 ever since, and nobody noticed because the file
#: sits in ``args/ci_test_backlog.txt`` and CI has never run it.
AUTHED_USER = {"id": "u-test", "role": "capture_mgr", "tenant_id": "t-test"}


def _make_app(db, user=AUTHED_USER):
    from tools.dashboard.api.proposals import proposals_api

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(proposals_api)

    @app.before_request
    def _simulate_auth():
        if user is not None:
            g.current_user = dict(user)
        # user=None: leave g.current_user unset, i.e. an anonymous caller

    return app


@pytest.fixture()
def app(db):
    """An authenticated caller holding a role the write routes accept."""
    return _make_app(db)


@pytest.fixture()
def anon_app(db):
    """An ANONYMOUS caller — used to prove the guard still refuses."""
    return _make_app(db, user=None)


def _conn(db_file):
    c = sqlite3.connect(str(db_file))
    c.row_factory = sqlite3.Row
    return c


def _seed_competitor_awards(db_file, n=1, agency="DoD", naics="541512"):
    conn = _conn(db_file)
    conn.executemany(
        "INSERT INTO govcon_awards (id, agency, naics_code, awardee_name, award_amount, award_date, set_aside_type) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (f"aw-{i}", agency, naics, f"Vendor {i}", 1_000_000 + i * 10_000, "2026-01-01", None)
            for i in range(n)
        ],
    )
    conn.commit()
    conn.close()


# ── ptw/analysis ─────────────────────────────────────────────────────────


class TestPtwAnalysis:
    def test_no_competitor_award_data_returns_low_confidence(self, app):
        with app.test_client() as client:
            resp = client.get("/api/proposals/opportunities/opp-1/ptw/analysis")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["recommendation"] == "competitive"
            assert data["confidence"] < 0.5

    def test_404_opportunity_still_returns_ptw_payload(self, app):
        # ptw_analysis() is keyed on opportunity_id but doesn't require the
        # opportunity to exist (no competitor rows -> low-confidence default).
        with app.test_client() as client:
            resp = client.get("/api/proposals/opportunities/nonexistent/ptw/analysis")
            assert resp.status_code == 200

    def test_scg_warning_appears_at_threshold(self, app, db):
        from tools.govcon.rate_benchmarker import _get_db as rb_get_db, _ensure_tables
        conn = rb_get_db()
        _ensure_tables(conn)
        conn.executemany(
            "INSERT INTO pg_competitor_awards (id, opportunity_id, competitor_name, award_amount, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            [(f"pca-{i}", "opp-1", f"Vendor {i}", 1_000_000, "2026-01-01") for i in range(3)],
        )
        conn.commit()
        conn.close()
        with app.test_client() as client:
            resp = client.get("/api/proposals/opportunities/opp-1/ptw/analysis")
            data = resp.get_json()
            assert data["competitor_count"] == 3
            assert "scg_warning" in data

    def test_no_scg_warning_below_threshold(self, app, db):
        from tools.govcon.rate_benchmarker import _get_db as rb_get_db, _ensure_tables
        conn = rb_get_db()
        _ensure_tables(conn)
        conn.execute(
            "INSERT INTO pg_competitor_awards (id, opportunity_id, competitor_name, award_amount, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            ("pca-1", "opp-1", "Vendor 1", 1_000_000, "2026-01-01"),
        )
        conn.commit()
        conn.close()
        with app.test_client() as client:
            resp = client.get("/api/proposals/opportunities/opp-1/ptw/analysis")
            data = resp.get_json()
            assert "scg_warning" not in data


# ── ptw/leaderboard ──────────────────────────────────────────────────────


class TestPtwLeaderboard:
    def test_defaults_naics_filter_from_opportunity(self, app, db):
        _seed_competitor_awards(db, n=1, naics="541512")
        conn = _conn(db)
        conn.execute(
            "INSERT INTO govcon_awards (id, agency, naics_code, awardee_name, award_amount, award_date) "
            "VALUES ('aw-other', 'DoD', '999999', 'Off-NAICS Vendor', 5000000, '2026-01-01')"
        )
        conn.commit()
        conn.close()
        with app.test_client() as client:
            resp = client.get("/api/proposals/opportunities/opp-1/ptw/leaderboard")
            assert resp.status_code == 200
            vendors = {row["vendor"] for row in resp.get_json()["leaderboard"]}
            assert "Off-NAICS Vendor" not in vendors

    def test_explicit_naics_query_param_overrides_default(self, app, db):
        _seed_competitor_awards(db, n=1, naics="999999")
        with app.test_client() as client:
            resp = client.get("/api/proposals/opportunities/opp-1/ptw/leaderboard?naics=999999")
            vendors = {row["vendor"] for row in resp.get_json()["leaderboard"]}
            assert vendors == {"Vendor 0"}

    def test_scg_warning_at_leaderboard_threshold(self, app, db):
        for i in range(3):
            conn = _conn(db)
            conn.execute(
                "INSERT INTO govcon_awards (id, agency, naics_code, awardee_name, award_amount, award_date) "
                "VALUES (?, 'DoD', '541512', ?, 1000000, '2026-01-01')",
                (f"aw-v{i}", f"Vendor {i}"),
            )
            conn.commit()
            conn.close()
        with app.test_client() as client:
            resp = client.get("/api/proposals/opportunities/opp-1/ptw/leaderboard")
            assert "scg_warning" in resp.get_json()


# ── ptw/vendor-profile ───────────────────────────────────────────────────


class TestPtwVendorProfile:
    def test_requires_vendor_name(self, app):
        with app.test_client() as client:
            resp = client.post("/api/proposals/opportunities/opp-1/ptw/vendor-profile", json={})
            assert resp.status_code == 400

    def test_profiles_vendor(self, app, db):
        conn = _conn(db)
        conn.execute(
            "INSERT INTO govcon_awards (id, agency, naics_code, awardee_name, award_amount, award_date) "
            "VALUES ('aw-1', 'DoD', '541512', 'Booz Allen', 1000000, '2026-01-01')"
        )
        conn.commit()
        conn.close()
        with app.test_client() as client:
            resp = client.post(
                "/api/proposals/opportunities/opp-1/ptw/vendor-profile",
                json={"vendor_name": "Booz Allen"},
            )
            assert resp.status_code == 200
            assert resp.get_json()["total_awards"] == 1


# ── ptw/bid-score ────────────────────────────────────────────────────────


class TestPtwBidScore:
    def test_returns_score_and_optimal_order(self, app):
        with app.test_client() as client:
            resp = client.post(
                "/api/proposals/opportunities/opp-1/ptw/bid-score",
                json={"dimensions": {"capability_fit": 0.8}},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert "score" in data
            assert "optimal_order" in data


# ── blackhat CRUD ────────────────────────────────────────────────────────


class TestBlackhatCrud:
    def test_create_requires_competitor_name(self, app):
        with app.test_client() as client:
            resp = client.post(
                "/api/proposals/opportunities/opp-1/ptw/blackhat", json={}
            )
            assert resp.status_code == 400

    def test_create_then_list_round_trip(self, app):
        with app.test_client() as client:
            create_resp = client.post(
                "/api/proposals/opportunities/opp-1/ptw/blackhat",
                json={
                    "competitor_name": "Leidos",
                    "approach_hypothesis": "Incumbent recompete",
                    "price_estimate_low": 4_000_000,
                    "price_estimate_high": 4_500_000,
                    "ptw_posture": "aggressive",
                },
            )
            assert create_resp.status_code == 201
            bh_id = create_resp.get_json()["id"]

            list_resp = client.get("/api/proposals/opportunities/opp-1/ptw/blackhat")
            assert list_resp.status_code == 200
            data = list_resp.get_json()
            assert data["count"] == 1
            assert data["assessments"][0]["id"] == bh_id
            assert data["assessments"][0]["competitor_name"] == "Leidos"
            assert data["assessments"][0]["ptw_posture"] == "aggressive"

    def test_create_invalid_posture_defaults_to_competitive(self, app):
        with app.test_client() as client:
            create_resp = client.post(
                "/api/proposals/opportunities/opp-1/ptw/blackhat",
                json={"competitor_name": "Serco", "ptw_posture": "not-a-real-posture"},
            )
            bh_id = create_resp.get_json()["id"]
            list_resp = client.get("/api/proposals/opportunities/opp-1/ptw/blackhat")
            row = next(a for a in list_resp.get_json()["assessments"] if a["id"] == bh_id)
            assert row["ptw_posture"] == "competitive"

    def test_update_blackhat_assessment(self, app):
        with app.test_client() as client:
            create_resp = client.post(
                "/api/proposals/opportunities/opp-1/ptw/blackhat",
                json={"competitor_name": "Acme Federal"},
            )
            bh_id = create_resp.get_json()["id"]

            update_resp = client.put(
                f"/api/proposals/blackhat/{bh_id}",
                json={"win_strategy": "Differentiate on AI tooling"},
            )
            assert update_resp.status_code == 200

            list_resp = client.get("/api/proposals/opportunities/opp-1/ptw/blackhat")
            row = next(a for a in list_resp.get_json()["assessments"] if a["id"] == bh_id)
            assert row["win_strategy"] == "Differentiate on AI tooling"

    def test_update_requires_at_least_one_field(self, app):
        with app.test_client() as client:
            create_resp = client.post(
                "/api/proposals/opportunities/opp-1/ptw/blackhat",
                json={"competitor_name": "Acme Federal"},
            )
            bh_id = create_resp.get_json()["id"]
            resp = client.put(f"/api/proposals/blackhat/{bh_id}", json={})
            assert resp.status_code == 400

    def test_delete_blackhat_assessment(self, app):
        with app.test_client() as client:
            create_resp = client.post(
                "/api/proposals/opportunities/opp-1/ptw/blackhat",
                json={"competitor_name": "SAIC"},
            )
            bh_id = create_resp.get_json()["id"]

            delete_resp = client.delete(f"/api/proposals/blackhat/{bh_id}")
            assert delete_resp.status_code == 200

            list_resp = client.get("/api/proposals/opportunities/opp-1/ptw/blackhat")
            assert list_resp.get_json()["count"] == 0

    def test_list_ordered_most_recent_first(self, app):
        with app.test_client() as client:
            client.post(
                "/api/proposals/opportunities/opp-1/ptw/blackhat",
                json={"competitor_name": "First"},
            )
            client.post(
                "/api/proposals/opportunities/opp-1/ptw/blackhat",
                json={"competitor_name": "Second"},
            )
            data = client.get("/api/proposals/opportunities/opp-1/ptw/blackhat").get_json()
            assert data["assessments"][0]["competitor_name"] == "Second"


# ── Bell-LaPadula price masking (prop-sec-01) ────────────────────────────


class TestPtwPriceMasking:
    """_mask_ptw_sensitive masks price_estimate_low/high unless the current
    security context can read SECRET (clearance_level >= 3, per
    classification_manager.get_clearance_order)."""

    def test_no_security_context_leaves_prices_unmasked(self):
        from tools.dashboard.api.proposals import _mask_ptw_sensitive

        row = {"price_estimate_low": 100.0, "price_estimate_high": 200.0}
        result = _mask_ptw_sensitive(row)
        assert result["price_estimate_low"] == 100.0
        assert result["price_estimate_high"] == 200.0

    def test_cui_clearance_masks_prices(self, app):
        from tools.dashboard.api.proposals import _mask_ptw_sensitive

        with app.test_request_context():
            g.security_context = SecurityContext(clearance_level=1)  # CUI
            row = {"price_estimate_low": 100.0, "price_estimate_high": 200.0}
            result = _mask_ptw_sensitive(row)
            assert result["price_estimate_low"] is None
            assert result["price_estimate_high"] is None
            assert result["price_estimate_low_masked"] is True
            assert result["price_estimate_high_masked"] is True

    def test_secret_clearance_leaves_prices_unmasked(self, app):
        from tools.dashboard.api.proposals import _mask_ptw_sensitive

        with app.test_request_context():
            g.security_context = SecurityContext(clearance_level=3)  # SECRET
            row = {"price_estimate_low": 100.0, "price_estimate_high": 200.0}
            result = _mask_ptw_sensitive(row)
            assert result["price_estimate_low"] == 100.0
            assert "price_estimate_low_masked" not in result


# ── The RBAC guard itself (rem-hyg-10) ───────────────────────────────────


#: Every write route on this workspace, with a minimal valid body. Adding a
#: write route without adding it here is the gap that let the 401s sit unseen.
_WRITE_ROUTES = [
    ("post", "/api/proposals/opportunities/opp-1/ptw/vendor-profile", {"vendor_name": "V"}),
    ("post", "/api/proposals/opportunities/opp-1/ptw/bid-score", {}),
    ("post", "/api/proposals/opportunities/opp-1/ptw/blackhat", {"competitor_name": "C"}),
]


class TestWriteRoutesRequireARole:
    """The 401s were a real guard doing its job against a test that never
    authenticated — so the fix is to authenticate, NOT to relax the routes.

    These assert the guard is still there. Without them, someone "fixing" a
    future 401 by deleting the decorator would turn every one of the twenty
    tests above green while opening the endpoints to anonymous callers.
    """

    @pytest.mark.parametrize(("method", "path", "body"), _WRITE_ROUTES)
    def test_an_anonymous_caller_is_refused(self, anon_app, method, path, body):
        with anon_app.test_client() as client:
            resp = getattr(client, method)(path, json=body)
        assert resp.status_code == 401, (
            f"{path} answered {resp.status_code} to an anonymous caller — the "
            "@require_role guard is gone"
        )

    @pytest.mark.parametrize(("method", "path", "body"), _WRITE_ROUTES)
    def test_a_wrong_role_is_refused(self, db, method, path, body):
        """403, not 401: authenticated but not permitted. The two are different
        answers and collapsing them hides which one is failing."""
        app = _make_app(db, user={"id": "u-viewer", "role": "viewer"})
        with app.test_client() as client:
            resp = getattr(client, method)(path, json=body)
        assert resp.status_code == 403, f"{path} let role=viewer through"

    def test_a_read_route_stays_open_to_an_anonymous_caller(self, anon_app):
        """The GET routes carry no decorator and the ten passing tests above
        depend on that. Pinning it so a blanket `@require_role` sweep is a
        deliberate decision rather than a silent one."""
        with anon_app.test_client() as client:
            resp = client.get("/api/proposals/opportunities/opp-1/ptw/blackhat")
        assert resp.status_code == 200
