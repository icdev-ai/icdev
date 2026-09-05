# CUI // SP-CTI — floci twin adapter (flx-twin-01)
"""Guards for ``tools/twin_core/adapters/floci.py``.

THREE OF THESE ARE STRUCTURAL, and that is deliberate. Two of the three rules
this adapter exists to keep have a failure mode a behavioural test cannot see:

* a future edit threading a caller-supplied ``provenance=`` through the writer
  would still pass every behavioural test over today's callers, which all pass
  none — rmf-disc-02 hit exactly that and answered it by reading the writer's
  AST, so this does the same;
* a future edit reaching the connector directly would return the SAME rows the
  broker returns, with no authorization check and no audit row. Nothing about
  the values would look wrong.

The rest are behavioural, over a stubbed broker: the verdict ladder is a pure
function and is exercised directly, and ``take_snapshot`` is exercised against a
real SQLite database so the persisted row is asserted, not the return value.
"""
from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTER_PATH = REPO_ROOT / "tools" / "twin_core" / "adapters" / "floci.py"
MIGRATION_PATH = (
    REPO_ROOT / "tools" / "db" / "migrations"
    / "20260905070028_floci_twin_snapshots" / "up.py"
)


# ── fixtures ──────────────────────────────────────────────────────────────────

class _StubOutcome:
    """The shape ``broker.fetch`` returns. Only the fields the adapter reads."""

    def __init__(self, ok=True, connector_status="ok", rows=None, error="",
                 connector_errors=None):
        self.ok = ok
        self.connector_status = connector_status
        self.rows = list(rows or [])
        self.row_count = len(self.rows)
        self.error = error
        self.connector_errors = list(connector_errors or [])
        self.audited = True


@pytest.fixture
def floci_mod():
    from tools.twin_core.adapters import floci

    return floci


@pytest.fixture
def floci_db(tmp_path, monkeypatch):
    """A per-test SQLite database carrying the SHIPPED floci_twin_snapshots DDL.

    Built by running the migration's own ``_ddl`` rather than by pasting the
    schema here: a hand-copied DDL in a test harness is how a test starts
    passing against a table shape that no longer exists in production.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "floci_twin.db"
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

    mig = _load_migration()
    raw = sqlite3.connect(str(db_path))
    raw.executescript(mig._ddl("sqlite") + ";")
    raw.commit()
    raw.close()
    return db_path


def _load_migration():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_flx_twin_mig", MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── registration + uniform surface ────────────────────────────────────────────

def test_adapter_is_discovered_and_carries_the_uniform_surface():
    """Filesystem discovery finds it; all four methods exist."""
    from tools.twin_core.registry import TwinRegistry

    TwinRegistry.discover(force=True)
    adapter = TwinRegistry.get("floci")
    assert adapter is not None, "floci adapter not discovered from the adapters package"
    for method in ("take_snapshot", "simulate_delta", "list_snapshots", "latest_status"):
        assert callable(getattr(adapter, method)), f"missing {method}"
    assert adapter.snapshot_table == "floci_twin_snapshots"


def test_declared_key_is_in_the_component_registry():
    """`floci` is a real ICDEV component (flx-compose-02 declared it)."""
    from tools.config.component_registry import get_registry

    component = get_registry().get("floci")
    assert component is not None, "floci is not declared in component_registry.yaml"
    # A core_extension, not a canvas — it has no page, so the 8-point page
    # completeness gate does not apply to this adapter (flx-compose-02).
    assert getattr(component, "kind", None) == "core_extension"


def test_the_grant_covers_every_table_the_adapter_reads(floci_mod):
    """The adapter never asks for a table the flx-bridge-02 grant omits.

    A table outside the grant is refused by the broker, which would make the
    snapshot read `unknown` forever with nothing on screen to say why.
    """
    from tools.databridge import broker

    grant = next(
        (c for c in broker.load_manifest().get("connectors", [])
         if c.get("name") == floci_mod.CONNECTOR),
        None,
    )
    assert grant is not None, "no floci grant in databridge_agent_access.yaml"
    granted = set(grant.get("tables") or [])
    from tools.databridge.connectors.floci_connector import TABLES

    assert set(TABLES) <= granted, f"tables outside the grant: {set(TABLES) - granted}"
    assert floci_mod.BROKER_AGENT_ID in (grant.get("agents") or [])


# ── the verdict ladder (pure) ─────────────────────────────────────────────────

def test_classify_read_never_reads_a_refusal_as_an_answer(floci_mod):
    """A denied fetch carries no connector status; it must not read as `ok`."""
    denied = _StubOutcome(ok=False, connector_status="", error="not granted")
    assert floci_mod.classify_read(denied, "health") == floci_mod.READ_DENIED


def test_classify_read_keeps_unsupported_apart_from_empty(floci_mod):
    """`unsupported_without_docker` is not an answer with zero rows."""
    unsupported = _StubOutcome(connector_status="unsupported_without_docker")
    empty = _StubOutcome(connector_status="ok", rows=[])
    assert floci_mod.classify_read(unsupported, "lambda_functions") == \
        floci_mod.READ_UNSUPPORTED_DOCKER
    assert floci_mod.classify_read(empty, "s3_buckets") == floci_mod.READ_ANSWERED


def test_classify_read_treats_an_unknown_status_as_an_error(floci_mod):
    """A connector state nobody anticipated must never become a `pass`."""
    weird = _StubOutcome(connector_status="something_new")
    assert floci_mod.classify_read(weird, "s3_buckets") == floci_mod.READ_ERROR


def test_verdict_pass_requires_every_table_to_answer(floci_mod):
    from tools.databridge.connectors.floci_connector import TABLES

    reads = {t: floci_mod.READ_ANSWERED for t in TABLES}
    assert floci_mod.classify_verdict(reads) == ("pass", "all_tables_answered")


def test_verdict_warn_on_a_docker_backed_table(floci_mod):
    from tools.databridge.connectors.floci_connector import TABLES

    reads = {t: floci_mod.READ_ANSWERED for t in TABLES}
    reads["lambda_functions"] = floci_mod.READ_UNSUPPORTED_DOCKER
    assert floci_mod.classify_verdict(reads) == ("warn", "unsupported_without_docker")


def test_verdict_fail_only_when_a_reachable_emulator_errors(floci_mod):
    """An error on an emulated SERVICE is `fail`; on the HEALTH path it is not."""
    from tools.databridge.connectors.floci_connector import TABLES

    reachable = {t: floci_mod.READ_ANSWERED for t in TABLES}
    reachable["s3_buckets"] = floci_mod.READ_ERROR
    assert floci_mod.classify_verdict(reachable) == ("fail", "emulator_errors")

    # The emulator's own health path failing is UNREACHABILITY — nothing was
    # measured — and must not be reported as the estate failing.
    unreachable = {t: floci_mod.READ_ERROR for t in TABLES}
    verdict, basis = floci_mod.classify_verdict(
        unreachable, granted=True, emulator_enabled=True
    )
    assert (verdict, basis) == ("unknown", "unreachable")


def test_verdict_unknown_is_never_pass(floci_mod):
    """Every unmeasured shape is `unknown`, and none of them is `pass`."""
    from tools.databridge.connectors.floci_connector import TABLES

    cases = {
        "unmeasured": ({}, {}),
        "disabled": ({t: floci_mod.READ_DISABLED for t in TABLES}, {}),
        "broker_denied": (
            {t: floci_mod.READ_DENIED for t in TABLES},
            {"granted": False, "emulator_enabled": True},
        ),
    }
    for expected_basis, (reads, kwargs) in cases.items():
        verdict, basis = floci_mod.classify_verdict(reads, **kwargs)
        assert verdict == "unknown", f"{expected_basis} produced {verdict}"
        assert basis == expected_basis


def test_a_missing_sdk_is_warn_not_fail(floci_mod):
    """boto3 is undeclared here; its absence is OUR gap, not the emulator's."""
    from tools.databridge.connectors.floci_connector import TABLES

    reads = {t: floci_mod.READ_ANSWERED for t in TABLES}
    reads["s3_buckets"] = floci_mod.READ_SDK_UNAVAILABLE
    assert floci_mod.classify_verdict(reads) == ("warn", "sdk_unavailable")


def test_denial_basis_separates_off_from_unreachable(floci_mod):
    """`saas_base.connect` collapses both into one refusal; this splits them."""
    assert floci_mod.denial_basis(True, False) == "disabled"
    assert floci_mod.denial_basis(True, True) == "unreachable"
    assert floci_mod.denial_basis(False, True) == "broker_denied"
    # Neither signal determined: the least specific claim, never a guess.
    assert floci_mod.denial_basis(None, None) == "broker_denied"


# ── behavioural: the snapshot that lands ──────────────────────────────────────

def _stub_broker(monkeypatch, floci_mod, statuses: dict, rows: dict | None = None):
    """Point the adapter's broker at a fake, per table."""
    rows = rows or {}

    def _fetch(agent_id, connector, table, **kwargs):
        assert agent_id == floci_mod.BROKER_AGENT_ID
        assert connector == floci_mod.CONNECTOR
        status = statuses.get(table, "ok")
        if status == "__denied__":
            return _StubOutcome(ok=False, connector_status="", error="refused")
        return _StubOutcome(connector_status=status, rows=rows.get(table, []))

    monkeypatch.setattr(floci_mod.broker, "fetch", _fetch)
    monkeypatch.setattr(
        floci_mod.broker, "list_available",
        lambda agent_id="": [{"connector": floci_mod.CONNECTOR}],
    )


def test_snapshot_persists_emulated_provenance_and_a_measured_count(
    floci_db, floci_mod, monkeypatch
):
    from tools.databridge.connectors.floci_connector import TABLES
    from tools.twin_core.registry import TwinRegistry

    _stub_broker(
        monkeypatch, floci_mod,
        {t: "ok" for t in TABLES},
        rows={"s3_buckets": [{"Name": "a"}, {"Name": "b"}]},
    )
    adapter = TwinRegistry.get("floci")
    snap = adapter.take_snapshot("local", label="unit")

    assert snap["verdict"] == "pass"
    assert snap["persisted"] is True
    # A MEASURED count, from the estate tables only — `health`/`services`
    # describe the emulator and must not inflate it.
    assert snap["resource_count"] == 2

    raw = sqlite3.connect(str(floci_db))
    row = raw.execute(
        "SELECT provenance, target_csp, region, verdict, verdict_basis, resource_count "
        "FROM floci_twin_snapshots"
    ).fetchone()
    raw.close()
    assert row[0] == "emulated"
    assert (row[1], row[2]) == ("aws", "us-gov-west-1")
    assert (row[3], row[4]) == ("pass", "all_tables_answered")
    assert row[5] == 2


def test_an_unmeasured_snapshot_records_null_never_zero(floci_db, floci_mod, monkeypatch):
    """The card's whole point: unknown is not a clean bill of health."""
    from tools.databridge.connectors.floci_connector import TABLES
    from tools.twin_core.registry import TwinRegistry

    _stub_broker(monkeypatch, floci_mod, {t: "__denied__" for t in TABLES})
    adapter = TwinRegistry.get("floci")
    snap = adapter.take_snapshot("local")

    assert snap["verdict"] == "unknown"
    assert snap["resource_count"] is None

    raw = sqlite3.connect(str(floci_db))
    row = raw.execute(
        "SELECT verdict, resource_count, provenance FROM floci_twin_snapshots"
    ).fetchone()
    raw.close()
    assert row[0] == "unknown"
    assert row[1] is None, "an unmeasured estate must be NULL, never 0"
    assert row[2] == "emulated"


def test_a_caller_cannot_talk_the_writer_out_of_emulated(floci_db, floci_mod, monkeypatch):
    """Behavioural half of the provenance rule; the AST test is the other half."""
    from tools.databridge.connectors.floci_connector import TABLES
    from tools.twin_core.registry import TwinRegistry

    _stub_broker(monkeypatch, floci_mod, {t: "ok" for t in TABLES})
    adapter = TwinRegistry.get("floci")
    adapter.take_snapshot("local", label="x", provenance="observed", source="discovery")

    raw = sqlite3.connect(str(floci_db))
    provenances = {r[0] for r in raw.execute("SELECT provenance FROM floci_twin_snapshots")}
    raw.close()
    assert provenances == {"emulated"}


def test_latest_status_over_no_snapshot_is_unknown(floci_db, floci_mod):
    """A twin that has never looked says so, rather than reporting green."""
    from tools.twin_core.registry import TwinRegistry

    status = TwinRegistry.get("floci").latest_status("never-seen")
    assert status["verdict"] == "unknown"
    assert status["verdict_basis"] == "no_snapshot"
    assert status["snapshot_count"] == 0


def test_simulation_carries_provenance_on_a_clean_envelope(floci_mod):
    """Not only when something is wrong — a clean simulation is emulated too."""
    from tools.twin_core.registry import TwinRegistry

    envelope = TwinRegistry.get("floci").simulate_delta("local", {"services": []})
    assert envelope["extra"]["provenance"] == "emulated"
    assert envelope["extra"]["target_csp"] == "aws"
    assert envelope["extra"]["region"] == "us-gov-west-1"
    # Static, and it says so: a delta naming no service is unscored, not a pass.
    assert envelope["verdict"] in ("unknown", "warn")


# ── structural guards ─────────────────────────────────────────────────────────

def _adapter_ast() -> ast.Module:
    return ast.parse(ADAPTER_PATH.read_text(encoding="utf-8"))


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {ADAPTER_PATH}")


def test_structurally_the_writer_takes_no_provenance(floci_mod):
    """rmf-disc-02's guard: read the WRITER, not its callers.

    A behavioural test only ever proves that today's call sites pass no
    provenance. This proves none CAN.
    """
    writer = _function(_adapter_ast(), "_persist_snapshot")
    args = writer.args
    names = {a.arg for a in (args.posonlyargs + args.args + args.kwonlyargs)}
    names |= {a.arg for a in (args.vararg, args.kwarg) if a is not None}
    assert "provenance" not in names, "_persist_snapshot must not accept a provenance"
    assert args.vararg is None and args.kwarg is None, (
        "*args/**kwargs would let a provenance reach the INSERT unnamed"
    )

    # The value bound into the INSERT is the module constant and nothing else.
    src = ast.get_source_segment(ADAPTER_PATH.read_text(encoding="utf-8"), writer) or ""
    assert "PROVENANCE," in src, "the INSERT must bind the module constant"
    assert 'snap["provenance"]' not in src and "snap.get(\"provenance\")" not in src, (
        "the writer must not take provenance from the snapshot dict a caller shaped"
    )
    assert floci_mod.PROVENANCE == "emulated"


def test_structurally_every_estate_read_goes_through_the_broker():
    """No direct connector read/write anywhere in the adapter.

    A direct call would return the same rows with no authorization decision and
    no ``databridge_agent_access_log`` row — the ungoverned side channel
    cef-fnd-03 exists to close, and nothing about the returned values would look
    wrong.
    """
    tree = _adapter_ast()
    broker_calls = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        attr = node.func.attr
        target = node.func.value
        base = target.id if isinstance(target, ast.Name) else ""
        if base == "broker" and attr == "fetch":
            broker_calls += 1
        assert not (attr in ("read", "write") and base in ("connector", "instance", "conn")), (
            f"direct connector.{attr}() in the floci twin adapter"
        )
    assert broker_calls >= 1, "the adapter must read through broker.fetch"

    # And the connector module is imported only for its DECLARATIONS and its
    # CAPABILITY PREDICATES — never for a class or function that opens a socket.
    # Enumerated rather than pattern-matched: a name is admitted here by a
    # reviewer deciding it reads no estate, which is a judgement no naming
    # convention can make.
    permitted = {
        "TABLES",              # the declared surface; pins the adapter to the grant
        "boto3_available",     # is the SDK importable in this process
        "table_needs_boto3",   # does this table use the SDK
        "table_is_docker_backed",  # does this table need a socket
        "table_service",       # which AWS service backs this table
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and "floci_connector" in (node.module or ""):
            imported = {a.name for a in node.names}
            assert imported <= permitted, (
                f"the adapter imports {imported - permitted} from the connector; only "
                f"declarations and capability predicates may be imported, because "
                f"anything that opens a socket bypasses the broker"
            )


def test_the_migration_derives_its_check_from_the_python_constant():
    """A hand-written CHECK is a second copy of the rule, and it drifts silently."""
    from tools.twin_core.schema import PROVENANCE_EMULATED, SNAPSHOT_PROVENANCES

    mig = _load_migration()
    check = mig._provenance_check()
    for value in SNAPSHOT_PROVENANCES:
        assert f"'{value}'" in check
    assert PROVENANCE_EMULATED in SNAPSHOT_PROVENANCES

    src = MIGRATION_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("twin_core.schema")
        for alias in node.names
    }
    assert "SNAPSHOT_PROVENANCES" in imported


def test_the_conftest_schema_matches_the_shipped_migration(floci_db):
    """The harness table and the production table have the SAME columns.

    conftest carries a hand-written SQLite mirror of the migration (the tests
    that never touch this adapter still need the table to exist). A mirror that
    drifts is how a test starts passing against a shape production does not
    have, so the two column sets are compared rather than trusted.
    """
    from tests.conftest import MINIMAL_ICDEV_SCHEMA

    harness = sqlite3.connect(":memory:")
    harness.executescript(MINIMAL_ICDEV_SCHEMA)
    harness_cols = {r[1] for r in harness.execute("PRAGMA table_info(floci_twin_snapshots)")}
    harness_sql = harness.execute(
        "SELECT sql FROM sqlite_master WHERE name='floci_twin_snapshots'"
    ).fetchone()[0]
    harness.close()

    shipped = sqlite3.connect(str(floci_db))
    shipped_cols = {r[1] for r in shipped.execute("PRAGMA table_info(floci_twin_snapshots)")}
    shipped.close()

    assert harness_cols == shipped_cols
    from tools.twin_core.schema import SNAPSHOT_PROVENANCES

    for value in SNAPSHOT_PROVENANCES:
        assert f"'{value}'" in harness_sql, (
            "the conftest CHECK has drifted from twin_core.schema.SNAPSHOT_PROVENANCES"
        )


def test_the_database_itself_refuses_a_bogus_provenance(floci_db):
    """The third belt: even past the writer and its AST test, the CHECK holds."""
    raw = sqlite3.connect(str(floci_db))
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute(
            "INSERT INTO floci_twin_snapshots (id, target_id, provenance) VALUES (?, ?, ?)",
            ("x", "local", "definitely_real_estate"),
        )
    raw.close()


def test_the_snapshot_payload_keeps_both_error_channels_apart(floci_mod, monkeypatch):
    """A broker refusal and a connector error are different findings."""
    from tools.databridge.connectors.floci_connector import TABLES
    from tools.twin_core.registry import TwinRegistry

    _stub_broker(monkeypatch, floci_mod, {t: "__denied__" for t in TABLES})
    adapter = TwinRegistry.get("floci")
    detail = adapter._read_all()
    for entry in detail.values():
        assert "broker_error" in entry and "connector_errors" in entry
        assert entry["broker_error"], "a denial must carry the broker's reason"
        assert entry["row_count"] is None, "an unanswered table has no row count"


def test_the_broker_relays_the_connector_status():
    """FetchOutcome must carry the connector's own status, not just its rows.

    Without this the adapter cannot tell `unsupported_without_docker` from an
    empty answer, and three of the four verdicts become unreachable.
    """
    from tools.databridge.broker import FetchOutcome

    out = FetchOutcome(ok=True, connector="floci", table="lambda_functions")
    assert hasattr(out, "connector_status") and hasattr(out, "connector_errors")
    assert "connector_status" in out.to_dict()
    # Default is the empty string, NOT "ok": a call that never reached a
    # connector has no status, and defaulting it to ok would manufacture one.
    assert out.connector_status == ""


def test_the_adapter_is_json_serializable(floci_mod, monkeypatch):
    """The observatory renders it; a non-serializable field is a 500 on a page."""
    from tools.databridge.connectors.floci_connector import TABLES
    from tools.twin_core.registry import TwinRegistry

    _stub_broker(monkeypatch, floci_mod, {t: "ok" for t in TABLES})
    adapter = TwinRegistry.get("floci")
    json.dumps(adapter.take_snapshot("local"))
    json.dumps(adapter.simulate_delta("local", {"services": ["s3"]}))
    json.dumps(adapter.describe())
