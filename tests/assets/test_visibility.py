# CUI // SP-CTI
"""Pytest suite — asset visibility that cannot fabricate a percentage (rmf-vis-01).

WHAT IS PINNED HERE is the set of invariants that FAIL GREEN if broken — the
ones where a wrong implementation returns a plausible number and no test
notices:

  * ``visibility_pct`` is None with no denominator, and the render says the
    words "not assessed". A 0.0 or a 100.0 there is arithmetically fine and
    factually a fabrication.
  * corroboration counts distinct (asset, source) PAIRS. A source that
    reports one asset forty times must not be able to manufacture depth —
    the ``odc_gap_scores`` defect, which a row count reproduces exactly.
  * an empty asset set yields depth None, never 0.0.
  * the ranked pick is by RANK, and the loser is REPORTED rather than
    averaged in.
  * a numerator over its denominator is NOT clamped to 100.
  * a synthetic row is excluded from the numerator and COUNTED, not dropped.
  * ``derived_if_mib`` yields None — never 0 — when no device carries
    interface data.

Two SQLite databases, deliberately: the identity table and the network canvas
are separate files on SQLite, so building the fixture as two files is what
proves the join is done in Python and would not silently return nothing.

Patching is shim-aware: module objects are resolved via importlib and the
attribute set on that exact object, so the tools.* -> icdev.tools.* shim
cannot route a patch to a different module.
"""
from __future__ import annotations

import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.assets import visibility as vis  # noqa: E402
from tools.db.storage import StorageConnection  # noqa: E402

_ASSET_DDL = (
    Path(__file__).resolve().parents[2]
    / "tools" / "db" / "migrations" / "20260902205902_asset_identity" / "up.sql"
)
_SNAPSHOT_DDL = (
    Path(__file__).resolve().parents[2]
    / "tools" / "db" / "migrations"
    / "20260902223458_asset_visibility_snapshots" / "up.sql"
)

_NC_DDL = """
CREATE TABLE ni_devices (
    id TEXT PRIMARY KEY, topology_id TEXT, node_id TEXT NOT NULL, label TEXT,
    device_type TEXT, vendor TEXT, model TEXT, source TEXT,
    properties_json TEXT);
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _sqlite(path: Path, ddl: str) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(ddl)
        conn.commit()
    finally:
        conn.close()


def _translated(path: Path) -> StorageConnection:
    """A StorageConnection over a SQLite file — the shape production uses."""
    raw = sqlite3.connect(str(path))
    raw.row_factory = sqlite3.Row
    return StorageConnection(raw, "sqlite")


@pytest.fixture()
def platform_db(tmp_path: Path) -> Path:
    p = tmp_path / "platform.db"
    ddl = _ASSET_DDL.read_text(encoding="utf-8") + "\n" + _SNAPSHOT_DDL.read_text(encoding="utf-8")
    # The migrations are authored for PostgreSQL; TIMESTAMP and TEXT are both
    # accepted by SQLite, so the DDL runs unchanged. That is the point of
    # reading the real file rather than hand-writing a fixture schema that can
    # drift away from the table production actually has.
    _sqlite(p, ddl)
    return p


@pytest.fixture()
def canvas_db(tmp_path: Path) -> Path:
    p = tmp_path / "network.db"
    _sqlite(p, _NC_DDL)
    return p


def _add_device(canvas: Path, device_id: str, *, fabric: str, source: str | None,
                node_id: str = "", interfaces: list | None = None) -> None:
    props = {"discovery": {"fabric": fabric, "adapter": "test",
                           "source_label": source or "", "node_id": node_id or device_id}}
    if interfaces is not None:
        props["interfaces"] = interfaces
    conn = sqlite3.connect(str(canvas))
    try:
        conn.execute(
            "INSERT INTO ni_devices (id, node_id, label, source, properties_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (device_id, node_id or device_id, node_id or device_id, source,
             json.dumps(props)),
        )
        conn.commit()
    finally:
        conn.close()


def _add_asset(platform: Path, asset_id: str, *, sources: list[str],
               ni_device_id: str | None = None, tier: str = "single_source") -> None:
    conn = sqlite3.connect(str(platform))
    try:
        conn.execute(
            "INSERT INTO asset_identity (asset_id, tenant_id, classification, "
            "fabric_key, hostname, ni_device_id, discovery_sources, "
            "corroboration_tier) VALUES (?, 'default', 'cui', ?, ?, ?, ?, ?)",
            (asset_id, asset_id, asset_id, ni_device_id, json.dumps(sources), tier),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def wired(monkeypatch, canvas_db: Path):
    """Substitute the NETWORK CANVAS connection, and nothing else.

    ``_read_ni_devices`` imports ``tools.network.db.init_db.get_connection``
    inside the function, so patching that name leaves the real reader — the
    column fallback, the properties_json parse, the fabric and evidence-class
    derivation — running unchanged over the fixture database. A fixture that
    returned pre-parsed dicts instead would prove the test's own copy of the
    parse works and say nothing about the module's.
    """
    init_db = importlib.import_module("tools.network.db.init_db")
    monkeypatch.setattr(init_db, "get_connection", lambda *a, **k: _translated(canvas_db))
    return importlib.import_module("tools.assets.visibility")


def _config(**fabrics) -> dict:
    """A minimal declaration with the four real kinds and their real ranks."""
    return {
        "readable": True,
        "config_path": "<test>",
        "kinds": [
            {"kind": "approved_cmdb", "rank": 0, "confidence": "high",
             "unit": "assets", "bias": "none_declared"},
            {"kind": "ip_allocation_plan", "rank": 1, "confidence": "medium",
             "unit": "addresses", "bias": "under_states_visibility"},
            {"kind": "dhcp_scope", "rank": 2, "confidence": "low",
             "unit": "leases", "bias": "over_states_visibility"},
            {"kind": "derived_if_mib", "rank": 3, "confidence": "inferred",
             "unit": "ports", "bias": "unknown"},
        ],
        "derived_if_mib": {"exclude_name_prefixes": ["lo", "vlan", "mgmt"],
                           "require_oper_status": []},
        "fabrics": dict(fabrics),
    }


# ---------------------------------------------------------------------------
# The acceptance criterion
# ---------------------------------------------------------------------------

class TestNoDenominatorNoPercentage:
    """visibility_pct is None and renders 'not assessed' — never 0, never 100."""

    def test_no_denominator_yields_none_not_zero_and_not_hundred(
        self, wired, platform_db, canvas_db
    ):
        _add_device(canvas_db, "d1", fabric="enterprise", source="netbox")
        _add_asset(platform_db, "a1", sources=["ni_devices"], ni_device_id="d1")

        conn = _translated(platform_db)
        try:
            report = vis.measure(conn=conn, config=_config())
        finally:
            conn.close()

        fab = next(f for f in report["fabrics"] if f["fabric_id"] == "enterprise")
        assert fab["state"] == vis.STATE_NOT_ASSESSED
        assert fab["visibility_pct"] is None
        # The two specific wrong answers, named.
        assert fab["visibility_pct"] != 0.0
        assert fab["visibility_pct"] != 100.0
        # ...and the numerator IS measured. "not assessed" is a statement
        # about the denominator, not about whether anything was counted.
        assert fab["observed_assets"] == 1

    def test_render_prints_the_words(self, wired, platform_db, canvas_db):
        _add_device(canvas_db, "d1", fabric="enterprise", source="netbox")
        _add_asset(platform_db, "a1", sources=["ni_devices"], ni_device_id="d1")
        conn = _translated(platform_db)
        try:
            text = vis.render(vis.measure(conn=conn, config=_config()))
        finally:
            conn.close()
        assert vis.NOT_ASSESSED_LABEL == "not assessed"
        assert "not assessed" in text
        # A bare "0.0%" or "100.0%" must not appear anywhere for a fabric
        # nobody has sized.
        assert "100.0%" not in text
        assert "0.0%" not in text

    def test_depth_is_reported_beside_coverage_as_a_second_number(
        self, wired, platform_db, canvas_db
    ):
        """Depth needs no denominator, so it is measured even here."""
        _add_device(canvas_db, "d1", fabric="enterprise", source="netbox")
        _add_asset(platform_db, "a1", sources=["ni_devices", "zig_device_registry"],
                   ni_device_id="d1", tier="corroborated")
        conn = _translated(platform_db)
        try:
            report = vis.measure(conn=conn, config=_config())
        finally:
            conn.close()
        fab = next(f for f in report["fabrics"] if f["fabric_id"] == "enterprise")
        assert fab["visibility_pct"] is None
        assert fab["corroboration_depth"] == 2.0
        assert fab["corroboration_pairs"] == 2
        assert fab["tiers"] == {"corroborated": 1}


# ---------------------------------------------------------------------------
# Pairs, never rows
# ---------------------------------------------------------------------------

class TestCorroborationCountsPairs:

    def test_repetition_by_one_source_is_not_corroboration(self):
        """The odc_gap_scores defect: 91 rows, one distinct value, one subject."""
        forty_times = ["zig_device_registry"] * 40
        got = vis.corroboration([
            {"asset_id": "a1", "discovery_sources": forty_times,
             "corroboration_tier": "single_source"},
        ])
        assert got["pairs"] == 1, "40 reports from ONE source is ONE pair"
        assert got["depth"] == 1.0
        assert got["corroborated_assets"] == 0

    def test_two_distinct_sources_are_corroboration(self):
        got = vis.corroboration([
            {"asset_id": "a1",
             "discovery_sources": ["ni_devices", "zig_device_registry"],
             "corroboration_tier": "corroborated"},
        ])
        assert got["pairs"] == 2
        assert got["depth"] == 2.0
        assert got["corroborated_assets"] == 1

    def test_empty_set_yields_none_depth_never_zero(self):
        got = vis.corroboration([])
        assert got["assets"] == 0
        assert got["depth"] is None, "no assets is not depth 0.0"
        assert got["corroborated_share_pct"] is None
        assert got["corroborated_assets"] is None

    def test_the_share_states_which_denominator_it_used(self):
        got = vis.corroboration([
            {"asset_id": "a1", "discovery_sources": ["x", "y"]},
            {"asset_id": "a2", "discovery_sources": ["x"]},
        ])
        assert got["corroborated_share_pct"] == 50.0
        # Its denominator is the MEASURED observed set, not a declared
        # estate — and it says so, so it cannot be read as coverage.
        assert "measured" in got["corroborated_share_denominator"]


# ---------------------------------------------------------------------------
# The ranked denominator
# ---------------------------------------------------------------------------

class TestRankedDenominator:

    def test_best_rank_wins_and_the_loser_is_reported_not_averaged(self):
        cfg = _config(enterprise=[
            {"kind": "dhcp_scope", "value": 980, "declared_by": "netops"},
            {"kind": "approved_cmdb", "value": 1420, "declared_by": "issm"},
        ])
        res = vis.resolve_denominator("enterprise", config=cfg)
        assert res["resolved"]["kind"] == "approved_cmdb"
        assert res["resolved"]["value"] == 1420
        assert [a["kind"] for a in res["alternates"]] == ["dhcp_scope"]
        # The loser is carried VERBATIM. An average of 1420 and 980 (1200)
        # must appear nowhere.
        assert res["alternates"][0]["value"] == 980
        assert res["resolved"]["value"] != 1200

    def test_an_unknown_kind_is_refused_not_defaulted_to_rank_zero(self):
        cfg = _config(enterprise=[
            {"kind": "vibes", "value": 99999},
            {"kind": "dhcp_scope", "value": 10},
        ])
        res = vis.resolve_denominator("enterprise", config=cfg)
        assert res["resolved"]["kind"] == "dhcp_scope"
        assert any(r["kind"] == "vibes" for r in res["refused"])

    def test_a_fabric_with_no_declaration_resolves_to_none(self):
        res = vis.resolve_denominator("enterprise", config=_config())
        assert res["resolved"] is None
        assert res["alternates"] == []

    def test_a_declared_zero_is_refused_with_a_reason(self):
        cfg = _config(enterprise=[{"kind": "approved_cmdb", "value": 0}])
        res = vis.resolve_denominator("enterprise", config=cfg)
        assert res["resolved"] is None
        assert "must be > 0" in res["refused"][0]["reason"]

    def test_confidence_and_source_travel_with_the_number(
        self, wired, platform_db, canvas_db
    ):
        _add_device(canvas_db, "d1", fabric="enterprise", source="netbox")
        _add_asset(platform_db, "a1", sources=["ni_devices"], ni_device_id="d1")
        cfg = _config(enterprise=[
            {"kind": "approved_cmdb", "value": 4, "declared_by": "issm",
             "as_of": "2026-08-31T00:00:00+00:00"},
        ])
        conn = _translated(platform_db)
        try:
            report = vis.measure(conn=conn, config=cfg)
        finally:
            conn.close()
        fab = next(f for f in report["fabrics"] if f["fabric_id"] == "enterprise")
        assert fab["state"] == vis.STATE_ASSESSED
        assert fab["visibility_pct"] == 25.0
        assert fab["denominator_source"] == "approved_cmdb"
        assert fab["denominator_confidence"] == "high"
        assert fab["denominator_unit"] == "assets"
        # The denominator's OWN clock, kept apart from ours.
        assert fab["denominator_as_of"] == "2026-08-31T00:00:00+00:00"
        assert fab["denominator_as_of"] != report["measured_at"]


# ---------------------------------------------------------------------------
# The numerator is not clamped, and a fabrication is not an observation
# ---------------------------------------------------------------------------

class TestNumeratorHonesty:

    def test_numerator_over_denominator_is_not_clamped_to_100(
        self, wired, platform_db, canvas_db
    ):
        for i in range(3):
            _add_device(canvas_db, f"d{i}", fabric="enterprise", source="netbox")
            _add_asset(platform_db, f"a{i}", sources=["ni_devices"], ni_device_id=f"d{i}")
        cfg = _config(enterprise=[{"kind": "approved_cmdb", "value": 2}])
        conn = _translated(platform_db)
        try:
            report = vis.measure(conn=conn, config=cfg)
        finally:
            conn.close()
        fab = next(f for f in report["fabrics"] if f["fabric_id"] == "enterprise")
        assert fab["visibility_pct"] == 150.0, "clamping hides a stale denominator"
        assert fab["numerator_exceeds_denominator"] is True

    def test_a_synthetic_row_is_excluded_and_counted_never_dropped(
        self, wired, platform_db, canvas_db
    ):
        _add_device(canvas_db, "real", fabric="enterprise", source="netbox")
        _add_device(canvas_db, "demo", fabric="enterprise", source="synthetic")
        _add_asset(platform_db, "a-real", sources=["ni_devices"], ni_device_id="real")
        _add_asset(platform_db, "a-demo", sources=["ni_devices"], ni_device_id="demo")
        conn = _translated(platform_db)
        try:
            report = vis.measure(conn=conn, config=_config())
        finally:
            conn.close()
        fab = next(f for f in report["fabrics"] if f["fabric_id"] == "enterprise")
        assert fab["observed_assets"] == 1, "a fabricated device is not an observation"
        assert fab["excluded"] == {vis.EXCLUDED_SYNTHETIC: 1}
        assert report["excluded"] == {vis.EXCLUDED_SYNTHETIC: 1}

    def test_a_fabric_whose_every_asset_was_excluded_is_unmeasurable_not_zero(
        self, wired, platform_db, canvas_db
    ):
        _add_device(canvas_db, "demo", fabric="enterprise", source="synthetic")
        _add_asset(platform_db, "a-demo", sources=["ni_devices"], ni_device_id="demo")
        cfg = _config(enterprise=[{"kind": "approved_cmdb", "value": 100}])
        conn = _translated(platform_db)
        try:
            report = vis.measure(conn=conn, config=cfg)
        finally:
            conn.close()
        fab = next(f for f in report["fabrics"] if f["fabric_id"] == "enterprise")
        assert fab["state"] == vis.STATE_UNMEASURABLE
        assert fab["observed_assets"] is None, "zero observed is not a measured zero here"
        assert fab["visibility_pct"] is None, (
            "a declared denominator must not turn an unmeasured numerator into 0%"
        )

    def test_a_declared_denominator_with_no_numerator_reports_the_winner(
        self, wired, platform_db, canvas_db
    ):
        """Found by running it: the loser rendered under a blank primary.

        The early return for an unmeasurable numerator dropped the WINNING
        denominator while keeping `alternates`, so a fabric declaring
        approved_cmdb=40 and dhcp_scope=12 rendered one line reading
        "alternate (not merged): dhcp_scope=12" and nothing else — which
        reads as though the loser had won.
        """
        _add_device(canvas_db, "demo", fabric="enterprise", source="synthetic")
        _add_asset(platform_db, "a-demo", sources=["ni_devices"], ni_device_id="demo")
        cfg = _config(enterprise=[
            {"kind": "approved_cmdb", "value": 40},
            {"kind": "dhcp_scope", "value": 12},
        ])
        conn = _translated(platform_db)
        try:
            report = vis.measure(conn=conn, config=cfg)
        finally:
            conn.close()
        fab = next(f for f in report["fabrics"] if f["fabric_id"] == "enterprise")
        assert fab["state"] == vis.STATE_UNMEASURABLE
        assert fab["visibility_pct"] is None
        # The winner is reported...
        assert fab["denominator_source"] == "approved_cmdb"
        assert fab["denominator"] == 40
        # ...and it is not confusable with the loser.
        assert [a["kind"] for a in fab["alternates"]] == ["dhcp_scope"]
        assert any("discovery gap, not 0% coverage" in n for n in fab["notes"])

    def test_an_empty_identity_table_is_unmeasurable_not_zero_percent(
        self, wired, platform_db
    ):
        conn = _translated(platform_db)
        try:
            report = vis.measure(conn=conn, config=_config(
                enterprise=[{"kind": "approved_cmdb", "value": 100}]))
        finally:
            conn.close()
        assert report["measurable"] is False
        assert "empty" in report["reason"]
        assert report["fabrics"] == []
        text = vis.render(report)
        assert "UNMEASURABLE" in text
        assert "0.0%" not in text and "100.0%" not in text


# ---------------------------------------------------------------------------
# derived_if_mib
# ---------------------------------------------------------------------------

class TestDerivedIfMib:

    def test_no_interface_data_yields_none_never_zero(self):
        got = vis.derive_if_mib(
            [{"_properties": {}}, {"_properties": {"interfaces": []}}], _config()
        )
        assert got["value"] is None, "'no switch has been walked' is not 'zero ports'"
        assert got["measurable"] is False
        assert "SNMP" in got["reason"]

    def test_counts_physical_ports_and_excludes_declared_prefixes(self):
        ifaces = [
            {"index": 1, "name": "GigabitEthernet1/0/1", "oper_status": "up"},
            {"index": 2, "name": "GigabitEthernet1/0/2", "oper_status": "down"},
            {"index": 3, "name": "Loopback0", "oper_status": "up"},
            {"index": 4, "name": "Vlan1", "oper_status": "up"},
            {"index": 5, "name": "mgmt0", "oper_status": "up"},
        ]
        got = vis.derive_if_mib([{"_properties": {"interfaces": ifaces}}], _config())
        assert got["value"] == 2, "loopback/vlan/mgmt are not attachment points"
        assert got["interfaces_excluded"] == 3
        # A DOWN access port is still a port the estate HAS. Filtering to `up`
        # would make visibility rise whenever an endpoint was switched off.
        assert got["measurable"] is True

    def test_an_undeclared_derived_kind_resolves_to_none_not_zero(self):
        cfg = _config(lab=[{"kind": "derived_if_mib"}])
        res = vis.resolve_denominator("lab", config=cfg, devices=[{"_properties": {}}])
        assert res["resolved"] is None
        assert res["refused"][0]["kind"] == "derived_if_mib"

    def test_a_port_denominator_carries_its_unit(self):
        ifaces = [{"index": i, "name": f"Ethernet{i}", "oper_status": "up"}
                  for i in range(10)]
        cfg = _config(lab=[{"kind": "derived_if_mib"}])
        res = vis.resolve_denominator(
            "lab", config=cfg, devices=[{"_properties": {"interfaces": ifaces}}]
        )
        assert res["resolved"]["value"] == 10
        assert res["resolved"]["unit"] == "ports", (
            "a percentage over ports must never be readable as a percentage "
            "over assets"
        )
        assert res["resolved"]["confidence"] == "inferred"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class TestPersistence:

    def test_source_and_confidence_are_persisted(
        self, wired, platform_db, canvas_db
    ):
        _add_device(canvas_db, "d1", fabric="enterprise", source="netbox")
        _add_asset(platform_db, "a1", sources=["ni_devices"], ni_device_id="d1")
        cfg = _config(enterprise=[
            {"kind": "approved_cmdb", "value": 4, "declared_by": "issm"}])
        conn = _translated(platform_db)
        try:
            report = vis.measure(conn=conn, config=cfg)
            wrote = vis.record_snapshot(report, conn=conn)
            assert wrote["recorded"] is True
            rows = vis.list_snapshots(fabric="enterprise", conn=conn)["snapshots"]
        finally:
            conn.close()
        assert len(rows) == 1
        row = rows[0]
        assert row["denominator_source"] == "approved_cmdb"
        assert row["denominator_confidence"] == "high"
        assert row["denominator_unit"] == "assets"
        assert row["visibility_pct"] == 25.0
        assert row["denominator_declared_by"] == "issm"

    def test_a_null_percentage_is_persisted_as_null(
        self, wired, platform_db, canvas_db
    ):
        _add_device(canvas_db, "d1", fabric="enterprise", source="netbox")
        _add_asset(platform_db, "a1", sources=["ni_devices"], ni_device_id="d1")
        conn = _translated(platform_db)
        try:
            report = vis.measure(conn=conn, config=_config())
            vis.record_snapshot(report, conn=conn)
            rows = vis.list_snapshots(fabric="enterprise", conn=conn)["snapshots"]
        finally:
            conn.close()
        assert rows[0]["visibility_pct"] is None
        assert rows[0]["state"] == vis.STATE_NOT_ASSESSED
        # The depth number IS persisted — the row is not empty just because
        # coverage could not be assessed.
        assert rows[0]["corroboration_depth"] == 1.0

    def test_an_unmeasurable_report_records_nothing_and_says_so(
        self, wired, platform_db
    ):
        """`written: 0` alone reads as "recorded, nothing to say"."""
        conn = _translated(platform_db)
        try:
            report = vis.measure(conn=conn, config=_config())
            got = vis.record_snapshot(report, conn=conn)
            rows = vis.list_snapshots(conn=conn)["snapshots"]
        finally:
            conn.close()
        assert got["recorded"] is False
        assert "UNMEASURABLE" in got["reason"]
        assert rows == []

    def test_snapshots_append_and_never_update(
        self, wired, platform_db, canvas_db
    ):
        _add_device(canvas_db, "d1", fabric="enterprise", source="netbox")
        _add_asset(platform_db, "a1", sources=["ni_devices"], ni_device_id="d1")
        conn = _translated(platform_db)
        try:
            for _ in range(3):
                vis.record_snapshot(vis.measure(conn=conn, config=_config()), conn=conn)
            rows = vis.list_snapshots(conn=conn)["snapshots"]
        finally:
            conn.close()
        ids = {r["snapshot_id"] for r in rows}
        assert len(ids) == len(rows) >= 3, "a snapshot is evidence; it is never rewritten"


# ---------------------------------------------------------------------------
# The one place a percentage is computed
# ---------------------------------------------------------------------------

class TestRateChokePoint:

    @pytest.mark.parametrize("num,den", [(5, 0), (5, None), (None, 10), (5, -1)])
    def test_rate_is_none_over_an_absent_or_empty_denominator(self, num, den):
        assert vis.visibility_pct(num, den) is None

    def test_assessed_always_carries_a_real_percentage(self, wired,
                                                       platform_db, canvas_db):
        """The one invariant: `assessed` <-> `visibility_pct is not None`.

        Asserted over EVERY branch a fabric can take rather than on one happy
        path, because the failure mode is a state label that says a
        measurement exists where none does.
        """
        _add_device(canvas_db, "real", fabric="enterprise", source="netbox")
        _add_device(canvas_db, "demo", fabric="lab-gns3", source="synthetic")
        _add_asset(platform_db, "a-real", sources=["ni_devices"], ni_device_id="real")
        _add_asset(platform_db, "a-demo", sources=["ni_devices"], ni_device_id="demo")
        _add_asset(platform_db, "a-orphan", sources=["zig_device_registry"])
        for fabrics in ({},
                        {"enterprise": [{"kind": "approved_cmdb", "value": 4}]},
                        {"lab-gns3": [{"kind": "approved_cmdb", "value": 9}]},
                        {"enterprise": [{"kind": "approved_cmdb", "value": 0}]}):
            cfg = _config(**fabrics)
            conn = _translated(platform_db)
            try:
                report = vis.measure(conn=conn, config=cfg)
            finally:
                conn.close()
            for fab in report["fabrics"]:
                assert fab["state"] in vis.VISIBILITY_STATES
                if fab["state"] == vis.STATE_ASSESSED:
                    assert fab["visibility_pct"] is not None
                    assert fab["denominator_source"] is not None
                    assert fab["denominator_confidence"] is not None
                else:
                    assert fab["visibility_pct"] is None
                    assert fab["visibility_label"] == vis.NOT_ASSESSED_LABEL

    def test_the_module_contains_no_perfect_score_fallback(self):
        """args/perfect_score_gate.yaml is ratcheted to 0; keep it that way.

        Asked of the SOURCE rather than by calling: the defect is a fallback
        arm that fires only on an empty denominator, which a call-based test
        can reach only if it already guesses the shape. And asked through the
        REAL census predicate (`scan_source`) rather than a string scan of my
        own — a second copy of the rule would have flagged this file's own
        docstring, which names the defect in prose, exactly as the census's
        first entry would otherwise have been rem-hyg-09's explanation of
        itself.
        """
        census = importlib.import_module("tools.ci.perfect_score_census")
        src = Path(vis.__file__).read_text(encoding="utf-8")
        findings = census.scan_source(src, "tools/assets/visibility.py")
        assert findings == [], f"perfect-score fallback shape present: {findings}"
