# CUI // SP-CTI
"""Pytest suite — canonical asset identity across the three stacks (rmf-ident-01).

THE THING BEING PROVED is the join, end to end, over REAL table shapes: a
device discovered by the NDC/PVM stack resolves to a ZIG zero-trust decision
and to an attack-surface row and to an enclave, through one identity row.
Nothing in the tree could do that before -- the three stacks keyed on
project_id, sha256(hostname) and ni_devices.id with no cross-link -- so the
happy-path test here is the acceptance criterion itself, not a unit check.

Three SQLite databases, deliberately. On PostgreSQL all three stacks share the
`icdev` database and a SQL JOIN would work; on SQLite each canvas has its own
file. Building the fixture as THREE files is what proves the join is done in
Python and would not silently return nothing on a SQLite deployment.

Patching is shim-aware: module objects are resolved via
importlib.import_module() and the attribute set on that exact object, so the
tools.* -> icdev.tools.* shim cannot route a patch to a different module.
"""
from __future__ import annotations

import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.assets import identity as ident  # noqa: E402
from tools.db.storage import StorageConnection  # noqa: E402

# --------------------------------------------------------------------------
# Schemas — the production shapes, trimmed to the columns under test.
# --------------------------------------------------------------------------

_ASSET_DDL = (
    Path(__file__).resolve().parents[1]
    / "tools" / "db" / "migrations" / "20260902205902_asset_identity" / "up.sql"
)

_ZIG_DDL = """
CREATE TABLE zig_device_registry (
    device_id TEXT PRIMARY KEY, hostname TEXT, os_platform TEXT,
    mdm_enrolled INTEGER DEFAULT 0, edr_installed INTEGER DEFAULT 0,
    nac_authorized INTEGER DEFAULT 0, last_seen_at TEXT,
    health_score REAL DEFAULT 0.0, compliance_score REAL DEFAULT 0.0,
    created_at TEXT, updated_at TEXT);
CREATE TABLE zig_nac_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, mac_address TEXT, ip_address TEXT,
    hostname TEXT, device_id TEXT, decision TEXT NOT NULL, reason TEXT,
    network_segment TEXT, violation_count INTEGER DEFAULT 0,
    operator_note TEXT, created_at TEXT);
CREATE TABLE zig_device_attestations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL,
    hostname TEXT, attestation_token TEXT NOT NULL, trust_score REAL NOT NULL,
    claims_json TEXT, verdict TEXT NOT NULL, nonce TEXT, expires_at TEXT,
    created_at TEXT);
"""

_NC_DDL = """
CREATE TABLE ni_devices (
    id TEXT PRIMARY KEY, topology_id TEXT, node_id TEXT NOT NULL, label TEXT,
    device_type TEXT, vendor TEXT, model TEXT, firmware_version TEXT);
CREATE TABLE nc_vuln_hosts (
    id TEXT PRIMARY KEY, scan_id TEXT, ip TEXT NOT NULL, fqdn TEXT DEFAULT '',
    netbios TEXT DEFAULT '', os TEXT DEFAULT '', node_id TEXT);
CREATE TABLE nc_attack_surface (
    id TEXT PRIMARY KEY, device_id TEXT, device_name TEXT NOT NULL, ip TEXT,
    cve_id TEXT NOT NULL, advisory_id TEXT, exposure_type TEXT,
    reachable INTEGER DEFAULT 0, bgp_exposed INTEGER DEFAULT 0,
    criticality INTEGER DEFAULT 3, surface_score REAL NOT NULL,
    nqe_source TEXT, nessus_scan_id TEXT, assessed_at TEXT, updated_at TEXT);
CREATE TABLE nc_boundaries (
    id TEXT PRIMARY KEY, topology_id TEXT, label TEXT NOT NULL,
    classification TEXT, node_ids TEXT DEFAULT '[]');
"""

_ZTA_DDL = """
CREATE TABLE zta_maturity_scores (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, pillar TEXT NOT NULL,
    score REAL, maturity_level TEXT, evidence TEXT, assessed_by TEXT,
    created_at TEXT);
"""


def _Conn(path: str):
    """A REAL StorageConnection over a temp SQLite file.

    Deliberately not a hand-rolled stand-in. The modules under test author
    ``%s`` placeholders because PostgreSQL is the primary backend, and a
    two-line ``sql.replace("%s", "?")`` imitation is a second copy of
    ``translate_sql`` that agrees with itself: it would corrupt a literal
    containing ``%s`` and knows nothing of the other rewrites. Going through
    the production wrapper means these tests exercise the same translation
    layer the runtime does.
    """
    raw = sqlite3.connect(path)
    raw.row_factory = sqlite3.Row
    return StorageConnection(raw, "sqlite")


@pytest.fixture()
def stacks(tmp_path, monkeypatch):
    """Three databases, wired the way the three stacks actually are."""
    asset_db = str(tmp_path / "icdev.db")
    zig_db = str(tmp_path / "security_canvas.db")
    nc_db = str(tmp_path / "network_canvas.db")

    raw = sqlite3.connect(asset_db)
    raw.executescript(_ASSET_DDL.read_text(encoding="utf-8"))
    raw.executescript(_ZTA_DDL)
    raw.commit()
    raw.close()

    raw = sqlite3.connect(zig_db)
    raw.executescript(_ZIG_DDL)
    raw.commit()
    raw.close()

    raw = sqlite3.connect(nc_db)
    raw.executescript(_NC_DDL)
    raw.commit()
    raw.close()

    monkeypatch.setattr(ident, "_conn", lambda: _Conn(asset_db))

    sc_init = importlib.import_module("tools.security_canvas.db.init_db")
    nc_init = importlib.import_module("tools.network.db.init_db")
    monkeypatch.setattr(sc_init, "get_connection", lambda: _Conn(zig_db))
    monkeypatch.setattr(nc_init, "get_connection", lambda: _Conn(nc_db))

    return {"asset": asset_db, "zig": zig_db, "nc": nc_db}


def _seed_all(stacks):
    """One real machine, described by all three stacks under three keys."""
    host = "icdev-srv-01"
    device_id = ident.zig_device_id(host)

    nc = sqlite3.connect(stacks["nc"])
    nc.execute(
        "INSERT INTO ni_devices (id, node_id, label, device_type, vendor, model) "
        "VALUES ('ni-1', 'node-7', ?, 'server', 'Dell', 'R740')",
        (host,),
    )
    nc.execute(
        "INSERT INTO nc_vuln_hosts (id, ip, fqdn, os, node_id) "
        "VALUES ('vh-1', '10.0.0.5', ?, 'linux', 'node-7')",
        (host,),
    )
    nc.execute(
        "INSERT INTO nc_attack_surface (id, device_name, ip, cve_id, exposure_type, "
        "reachable, criticality, surface_score) "
        "VALUES ('as-1', ?, '10.0.0.5', 'CVE-2026-1111', 'internet', 1, 5, 0.91)",
        (host,),
    )
    nc.execute(
        "INSERT INTO nc_boundaries (id, label, classification, node_ids) "
        "VALUES ('bnd-1', 'Mission Enclave A', 'CUI', ?)",
        (json.dumps(["node-7", "node-9"]),),
    )
    nc.commit()
    nc.close()

    zig = sqlite3.connect(stacks["zig"])
    zig.execute(
        "INSERT INTO zig_device_registry (device_id, hostname, os_platform, "
        "mdm_enrolled, edr_installed, nac_authorized, health_score, compliance_score) "
        "VALUES (?, ?, 'linux', 1, 1, 1, 0.88, 0.92)",
        (device_id, host),
    )
    zig.execute(
        "INSERT INTO zig_nac_events (device_id, hostname, decision, reason, "
        "network_segment, created_at) VALUES (?, ?, 'allow', 'compliant', "
        "'10.0.0.0/8', '2026-09-01T00:00:00Z')",
        (device_id, host),
    )
    zig.execute(
        "INSERT INTO zig_device_attestations (device_id, hostname, attestation_token, "
        "trust_score, verdict, created_at) VALUES (?, ?, 'tok', 0.88, 'trusted', "
        "'2026-09-01T00:00:00Z')",
        (device_id, host),
    )
    zig.commit()
    zig.close()
    return host, device_id


# --------------------------------------------------------------------------
# Derivation
# --------------------------------------------------------------------------

class TestFabricKey:
    def test_mac_beats_hostname_beats_ip(self):
        # Preference is by how hard the identifier is to REASSIGN. A hostname
        # is renameable and an IP is a lease; both would split one asset into
        # several rows over time if they outranked a burned-in MAC.
        assert ident.fabric_key(hostname="h", mac_address="00:1b:44:11:3a:b7",
                                mgmt_ip="10.0.0.1") == "mac:00:1b:44:11:3a:b7"
        assert ident.fabric_key(hostname="H", mgmt_ip="10.0.0.1") == "host:h"
        assert ident.fabric_key(mgmt_ip="10.0.0.1") == "ip:10.0.0.1"

    def test_no_identifier_is_none_not_a_key(self):
        # None makes the caller SKIP the row. A synthesised key would give the
        # asset a new id on every sweep, so a nightly discovery would grow one
        # row per night per unidentifiable device.
        assert ident.fabric_key() is None
        assert ident.fabric_key(hostname="", mac_address="", mgmt_ip="") is None

    def test_prefix_keeps_namespaces_apart(self):
        # A host literally named "10.0.0.1" must not collide with the asset at
        # that address.
        assert ident.fabric_key(hostname="10.0.0.1") != ident.fabric_key(mgmt_ip="10.0.0.1")

    def test_partial_mac_is_rejected_not_padded(self):
        # Eleven hex digits is not a MAC. Accepting it would let two unrelated
        # assets normalise onto one fabric key.
        assert ident.normalise_mac("00:1b:44:11:3a") is None
        assert ident.fabric_key(hostname="h", mac_address="00:1b:44:11:3a") == "host:h"

    def test_mac_separator_forms_normalise_together(self):
        forms = ["00-1B-44-11-3A-B7", "001b.4411.3ab7", "00:1b:44:11:3a:b7"]
        assert len({ident.normalise_mac(f) for f in forms}) == 1

    def test_asset_id_is_deterministic_across_processes(self):
        assert ident.asset_id_for("host:x") == ident.asset_id_for("host:x")
        assert ident.asset_id_for("host:x") != ident.asset_id_for("host:y")


class TestZigFingerprintIsOneDefinition:
    def test_every_zig_site_delegates_to_this_module(self):
        # The rule was written out by hand at five sites. A resolver that
        # re-implemented it a sixth time could drift from the key it claims to
        # resolve onto -- so this asserts the DELEGATION, not the value.
        import tools.security_canvas.device_compliance_scanner as dcs
        import tools.security_canvas.device_attestation_engine as att
        import tools.security_canvas.edr_deployment_controller as edr
        import tools.security_canvas.mdm_enrollment_manager as mdm
        import tools.security_canvas.device_pillar_orchestrator as orch

        for mod in (att, edr, mdm, orch):
            assert mod.zig_device_id is ident.zig_device_id, mod.__name__
        assert dcs._device_fingerprint("h") == ident.zig_device_id("h")

    def test_no_zig_module_still_spells_the_rule_by_hand(self):
        root = Path(__file__).resolve().parents[1] / "tools" / "security_canvas"
        offenders = [
            p.name for p in root.glob("*.py")
            if "sha256(hostname" in p.read_text(encoding="utf-8")
        ]
        assert offenders == [], f"hand-written fingerprint rule survives in {offenders}"


class TestCorroboration:
    def test_repetition_by_one_source_is_not_corroboration(self):
        # `odc_gap_scores` held 91 rows carrying ONE distinct value for ONE
        # subject. Counting rows rates a single stuck writer as extremely well
        # corroborated.
        assert ident.corroboration_tier(["ni_devices"] * 40) == ident.TIER_SINGLE
        assert ident.corroboration_tier(["a", "b"]) == ident.TIER_CORROBORATED

    def test_no_source_is_unconfirmed_not_single(self):
        assert ident.corroboration_tier([]) == ident.TIER_UNCONFIRMED

    def test_human_confirmation_outranks_source_count(self):
        assert ident.corroboration_tier([], human_confirmed=True) == ident.TIER_AUTHORITATIVE


class TestClassificationMethod:
    def test_declared_set_is_the_card_s_set(self):
        assert ident.CLASSIFICATION_METHODS == ("rule", "oui", "model", "human_confirmed")

    def test_an_undeclared_method_is_refused_not_stored(self, stacks):
        with pytest.raises(ValueError):
            ident.upsert_asset(hostname="h", classification_method="guessed")

    def test_null_method_is_its_own_state(self, stacks):
        # NULL means NOTHING classified this asset. Defaulting it to 'rule'
        # would let an RMF package report every asset as adjudicated.
        row = ident.upsert_asset(hostname="unclassified-host", source="manual")
        assert row["classification_method"] is None
        assert ident.stats()["classification_method"]["unclassified"] == 1

    def test_classification_is_a_label_never_a_banner(self, stacks):
        # A banner ('CUI // SP-CTI') matches no clearance at any level, so the
        # row would be written, retained and invisible under RLS.
        row = ident.upsert_asset(hostname="h", source="manual")
        assert row["classification"] == "cui"
        assert "//" not in row["classification"]


# --------------------------------------------------------------------------
# Upsert
# --------------------------------------------------------------------------

class TestUpsert:
    def test_second_sighting_upserts_and_corroborates(self, stacks):
        a = ident.upsert_asset(hostname="host-a", source="ni_devices")
        b = ident.upsert_asset(hostname="HOST-A", source="zig_device_registry")
        assert a["asset_id"] == b["asset_id"]
        assert b["discovery_sources"] == ["ni_devices", "zig_device_registry"]
        assert b["corroboration_tier"] == ident.TIER_CORROBORATED
        assert len(ident.list_assets()) == 1

    def test_a_weaker_later_observation_does_not_blank_a_field(self, stacks):
        # A source that does not know the vendor reports NOTHING, not "no
        # vendor". Letting None overwrite would make the record degrade every
        # time a thinner source ran last.
        ident.upsert_asset(hostname="host-b", source="ni_devices", vendor="Cisco")
        row = ident.upsert_asset(hostname="host-b", source="nc_vuln_hosts")
        assert row["vendor"] == "Cisco"

    def test_authoritative_is_not_demoted_by_a_later_machine_sighting(self, stacks):
        ident.upsert_asset(hostname="host-c", source="manual", human_confirmed=True)
        row = ident.upsert_asset(hostname="host-c", source="ni_devices")
        assert row["corroboration_tier"] == ident.TIER_AUTHORITATIVE

    def test_unidentifiable_observation_is_skipped_not_raised(self, stacks):
        assert ident.upsert_asset(source="ni_devices") is None
        assert ident.list_assets() == []


# --------------------------------------------------------------------------
# The acceptance criterion: the join
# --------------------------------------------------------------------------

class TestDiscoveredDeviceJoinsToZtDecisionAndAttackSurface:
    def test_the_whole_chain(self, stacks):
        host, device_id = _seed_all(stacks)

        ing = ident.ingest()
        assert ing["measurable"] is True
        assert ing["sources"]["ni_devices"]["ingested"] == 1
        assert ing["sources"]["zig_device_registry"]["ingested"] == 1
        assert ing["sources"]["nc_vuln_hosts"]["ingested"] == 1
        # Three stacks, ONE asset -- the point of the fabric key.
        assert ing["total_assets"] == 1

        p = ident.asset_posture(host)
        assert p["found"] is True

        # ...resolves onto every stack's own key
        asset = p["asset"]
        assert asset["zig_device_id"] == device_id
        assert asset["ni_device_id"] == "ni-1"
        assert asset["ni_node_id"] == "node-7"
        assert asset["corroboration_tier"] == ident.TIER_CORROBORATED
        assert sorted(asset["discovery_sources"]) == [
            "nc_vuln_hosts", "ni_devices", "zig_device_registry",
        ]

        # ...joins to a ZERO-TRUST decision
        assert p["joined"]["zig"] is True
        assert p["zt_decision"]["nac"]["decision"] == "allow"
        assert p["zt_decision"]["attestation"]["verdict"] == "trusted"
        assert p["zt_decision"]["registry"]["compliance_score"] == pytest.approx(0.92)

        # ...joins to an ATTACK-SURFACE row
        assert p["joined"]["pvm"] is True
        assert [r["cve_id"] for r in p["attack_surface"]["rows"]] == ["CVE-2026-1111"]

        # ...and lands in an ENCLAVE
        assert p["enclave"]["id"] == "bnd-1"
        assert p["enclave"]["label"] == "Mission Enclave A"

    def test_ingest_is_idempotent(self, stacks):
        _seed_all(stacks)
        ident.ingest()
        first = ident.list_assets()
        ident.ingest()
        second = ident.list_assets()
        assert len(first) == len(second) == 1
        assert first[0]["asset_id"] == second[0]["asset_id"]

    def test_zta_posture_is_unbound_not_zero_when_no_project_is_linked(self, stacks):
        # The DoD pillar stack has no per-device row at ALL; it keys on
        # project_id. Inferring a project would manufacture a posture. The
        # unbound state IS the finding.
        _seed_all(stacks)
        ident.ingest()
        p = ident.asset_posture("icdev-srv-01")
        assert p["zta_posture"]["bound"] is False
        assert p["zta_posture"]["device_pillar"] is None
        assert "not bound" in p["zta_posture"]["reason"]

    def test_a_bound_asset_reads_its_project_pillar_scores(self, stacks):
        host, _ = _seed_all(stacks)
        ident.ingest()
        asset = ident.find_asset(hostname=host)
        ident.upsert_asset(hostname=host, source="manual", zta_project_id="proj-1")
        db = sqlite3.connect(stacks["asset"])
        db.execute(
            "INSERT INTO zta_maturity_scores (id, project_id, pillar, score, "
            "maturity_level, created_at) VALUES ('z1', 'proj-1', 'device', 0.62, "
            "'advanced', '2026-09-01T00:00:00Z')"
        )
        db.commit()
        db.close()
        p = ident.asset_posture(asset["asset_id"])
        assert p["zta_posture"]["bound"] is True
        assert p["joined"]["zta"] is True
        assert p["zta_posture"]["device_pillar"]["score"] == pytest.approx(0.62)


class TestAbsenceIsNotEmptiness:
    def test_an_unreadable_stack_is_not_zero_devices(self, stacks, monkeypatch):
        # An unmigrated network canvas and a network canvas with no devices
        # send you to different fixes. `ingested: 0` cannot say both.
        nc_init = importlib.import_module("tools.network.db.init_db")

        def boom():
            raise RuntimeError("no such table: ni_devices")

        monkeypatch.setattr(nc_init, "get_connection", boom)
        out = ident.ingest()
        assert out["sources"]["ni_devices"]["readable"] is False
        assert out["sources"]["ni_devices"]["rows"] is None
        assert "ingested" not in out["sources"]["ni_devices"]

    def test_an_unmigrated_inventory_is_unmeasurable_not_empty(self, tmp_path, monkeypatch):
        empty = str(tmp_path / "bare.db")
        sqlite3.connect(empty).close()
        monkeypatch.setattr(ident, "_conn", lambda: _Conn(empty))
        s = ident.stats()
        assert s["measurable"] is False
        assert s["total"] is None
        out = ident.ingest()
        assert out["measurable"] is False

    def test_an_empty_but_migrated_inventory_reports_a_measured_zero(self, stacks):
        s = ident.stats()
        assert s["measurable"] is True
        assert s["total"] == 0

    def test_zt_decision_reports_why_it_could_not_measure(self, stacks, monkeypatch):
        _seed_all(stacks)
        ident.ingest()
        sc_init = importlib.import_module("tools.security_canvas.db.init_db")

        def boom():
            raise RuntimeError("security canvas down")

        monkeypatch.setattr(sc_init, "get_connection", boom)
        p = ident.asset_posture("icdev-srv-01")
        assert p["joined"]["zig"] is False
        assert p["zt_decision"]["nac"] is None
        assert "security canvas down" in p["zt_decision"]["reason"]

    def test_stats_counts_each_resolver_separately(self, stacks):
        _seed_all(stacks)
        ident.ingest()
        s = ident.stats()
        # "how many assets does ZIG know about" and "how many does PVM know
        # about" are different questions; one 'linked' number answers neither.
        assert s["resolvers"]["zig_device_id"] == 1
        assert s["resolvers"]["ni_device_id"] == 1
        assert s["resolvers"]["zta_project_id"] == 0


# --------------------------------------------------------------------------
# The ZIG device pillar's fleet
# --------------------------------------------------------------------------

class TestZigReadsItsFleetFromTheInventory:
    def _orch(self):
        return importlib.import_module(
            "tools.security_canvas.device_pillar_orchestrator"
        )

    def test_fleet_comes_from_asset_identity_when_populated(self, stacks):
        _seed_all(stacks)
        ident.ingest()
        fleet, source = self._orch().resolve_fleet()
        assert source == "asset_identity"
        assert [d["hostname"] for d in fleet] == ["icdev-srv-01"]
        assert fleet[0]["os_platform"] == "linux"

    def test_fixture_is_the_fallback_and_is_labelled_as_one(self, stacks):
        orch = self._orch()
        fleet, source = orch.resolve_fleet()
        assert fleet is orch.DEFAULT_FLEET
        # The label is the whole point: a maturity score over six invented
        # hostnames must not read identically to one over the real estate.
        assert source == "fixture_inventory_empty"

    def test_unreadable_inventory_is_a_different_label_from_empty(self, monkeypatch):
        orch = self._orch()
        monkeypatch.setattr(
            ident, "managed_fleet",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no such table")),
        )
        fleet, source = orch.resolve_fleet()
        assert fleet is orch.DEFAULT_FLEET
        assert source == "fixture_inventory_unreadable"

    def test_an_explicit_argument_still_wins(self, stacks):
        _seed_all(stacks)
        ident.ingest()
        given = [{"hostname": "given-01", "os_platform": "windows"}]
        fleet, source = self._orch().resolve_fleet(given)
        assert fleet is given
        assert source == "caller"

    def test_managed_fleet_skips_assets_with_no_hostname(self, stacks):
        ident.upsert_asset(mgmt_ip="10.0.0.9", source="nc_vuln_hosts")
        # An IP-keyed asset has no hostname; ZIG's scanners key on hostname, so
        # handing them an empty string would register a phantom device.
        assert ident.managed_fleet() == []


class TestTheJoinNamesColumnsThatActuallyExist:
    """The fixture above is hand-transcribed, so it can agree with itself.

    Measured on the live board 2026-09-02, ALL THREE upstreams are empty or
    absent (ni_devices 0 rows, nc_vuln_hosts 0 rows, zig_device_registry does
    not exist on PostgreSQL at all -- the ZIG scanners create it lazily on
    first run, so the device pillar has never been run against this database).
    There is therefore no live data to verify the join against, and a fixture
    written to satisfy the fixture proves nothing.

    What CAN be checked without rows is that every column the join SELECTs is
    declared in the production DDL. That is the drift this fixture is exposed
    to: a query naming a column production does not have passes here and
    raises the moment the estate has devices in it.
    """

    _ROOT = Path(__file__).resolve().parents[1]

    def _ddl(self, relpath: str) -> str:
        return (self._ROOT / relpath).read_text(encoding="utf-8")

    def test_zig_columns_the_join_reads_are_declared_in_production(self):
        src = (
            self._ddl("tools/security_canvas/device_compliance_scanner.py")
            + self._ddl("tools/security_canvas/nac_enforcer.py")
            + self._ddl("tools/security_canvas/device_attestation_engine.py")
        )
        for col in ("device_id", "hostname", "os_platform", "mdm_enrolled",
                    "edr_installed", "nac_authorized", "health_score",
                    "compliance_score", "last_seen_at", "decision", "reason",
                    "network_segment", "verdict", "trust_score", "expires_at"):
            assert col in src, f"{col} is not declared by the ZIG DDL"

    def test_network_columns_the_join_reads_are_declared_in_production(self):
        src = self._ddl("tools/network/db/init_db.py")
        for col in ("device_name", "cve_id", "exposure_type", "reachable",
                    "bgp_exposed", "criticality", "surface_score", "assessed_at",
                    "node_ids", "fqdn", "netbios"):
            assert col in src, f"{col} is not declared by the network canvas DDL"

    def test_ni_devices_has_no_hostname_column(self):
        # This is WHY ingest keys on `label` and falls back to `node_id`. If a
        # hostname column ever lands, that fallback should be revisited rather
        # than left silently preferring the label.
        src = self._ddl("tools/db/migrations/010_network_intelligence_schema/up.py")
        ddl = src.split("CREATE TABLE IF NOT EXISTS ni_devices")[1].split(");")[0]
        assert "hostname" not in ddl
        assert "label" in ddl and "node_id" in ddl


class TestSubstrateIsDeclared:
    def test_asset_identity_is_registered_as_a_substrate(self):
        # A load-bearing READ path whose table is empty degrades silently --
        # the exact shape args/capability_consumption.yaml exists to name.
        import yaml

        cfg = yaml.safe_load(
            (Path(__file__).resolve().parents[1] / "args" / "capability_consumption.yaml")
            .read_text(encoding="utf-8")
        )
        refs = {s["ref"] for s in cfg["substrates"]}
        assert "asset_identity" in refs
