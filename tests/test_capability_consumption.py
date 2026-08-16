# CUI // SP-CTI
"""Tests for tools/awareness/capability_consumption.py (#exa-live-01).

The headline test is :func:`test_five_known_inert_cases_report_zero`: with every
telemetry table present but holding nothing the capability could have produced,
all five documented incidents — the audit chain writer, MCPToolAuthorizer,
prompt_registry, GEPA and the inert reflexes — must report zero consumption.

That is deliberately asserted against a seeded fixture rather than the live
database. Two of the five have since been wired (exa-policy-05 put
MCPToolAuthorizer on the MCP HTTP surface, exa-audit-03 is landing the chain
writer), and the shared PostgreSQL instance additionally carries test residue
from sibling sessions, so a live assertion would measure whatever else ran today.
The fixture pins the *measurement*; the live run reports the *state*.

Every zero-assertion is paired with a positive control on the same table, so a
test can never pass because the probe silently failed — the failure mode the
tool itself exists to detect.
"""
from __future__ import annotations

import importlib
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

capcon = importlib.import_module("tools.awareness.capability_consumption")

NOW = datetime.now(timezone.utc)
IN_WINDOW = (NOW - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.%f%z")
OUT_OF_WINDOW = (NOW - timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%S.%f%z")

# Only the columns the probes actually read. Kept deliberately narrow: a fixture
# that mirrors production DDL drifts from it silently, and these seven tables
# are owned by seven different migrations.
SCHEMA = [
    """CREATE TABLE genesis_reflex_state (
        reflex_name TEXT PRIMARY KEY, enabled INTEGER, last_run_at TEXT,
        total_runs INTEGER DEFAULT 0)""",
    """CREATE TABLE studio_mcp_dispatch_audit (
        audit_id TEXT PRIMARY KEY, tool TEXT, decision TEXT, recorded_at TEXT)""",
    """CREATE TABLE agent_approval_log (
        id INTEGER PRIMARY KEY, tool_name TEXT, rule TEXT, decision TEXT,
        decided_at TEXT)""",
    """CREATE TABLE audit_platform (
        id INTEGER PRIMARY KEY, tenant_id TEXT, user_id TEXT, event_type TEXT,
        action TEXT, details TEXT, recorded_at TEXT)""",
    """CREATE TABLE prompt_versions (
        id TEXT PRIMARY KEY, prompt_name TEXT, version INTEGER, status TEXT,
        updated_at TEXT)""",
    """CREATE TABLE audit_trail (
        id INTEGER PRIMARY KEY, event_type TEXT, actor TEXT, created_at TEXT,
        hash TEXT, previous_hash TEXT, signature TEXT)""",
    """CREATE TABLE agent_improvement_artifacts (
        artifact_id TEXT PRIMARY KEY, skill_used TEXT, composite_score REAL,
        baseline_score REAL, status TEXT, applied_count INTEGER DEFAULT 0,
        applied_at TEXT)""",
    """CREATE TABLE runtime_invocations (
        id TEXT PRIMARY KEY, surface TEXT NOT NULL, name TEXT NOT NULL,
        started_at TEXT NOT NULL, status TEXT, error_class TEXT)""",
]

CONFIG = {
    "window_days": 30,
    "inert_threshold": 0,
    "max_listed_units": 100,
    "classes": {name: {"enabled": True} for name in capcon.PROBES},
    "known_inert_cases": [
        {"id": "audit_chain_writer", "capability_class": "audit_chain", "metric": "events"},
        {"id": "mcp_tool_authorizer", "capability_class": "mcp_tool_authorization",
         "metric": "events"},
        {"id": "prompt_registry", "capability_class": "prompt_template", "metric": "events"},
        {"id": "gepa_optimizer", "capability_class": "skill_optimizer", "metric": "events"},
        {"id": "inert_reflexes", "capability_class": "reflex", "metric": "inert_units"},
    ],
}


def _seed(db_path, statements=(), skip_tables=()):
    """Create the telemetry tables in a fresh SQLite file and apply *statements*."""
    raw = sqlite3.connect(str(db_path))
    try:
        for ddl in SCHEMA:
            if any(f"CREATE TABLE {t} " in ddl for t in skip_tables):
                continue
            raw.execute(ddl)
        for sql, params in statements:
            raw.execute(sql, params)
        raw.commit()
    finally:
        raw.close()


@pytest.fixture
def conn_factory(tmp_path, monkeypatch):
    """Hand back a StorageConnection over a seeded temp SQLite database.

    Deliberately a real ``get_connection`` rather than a bare ``sqlite3.connect``
    so the %s -> ? translation the production code relies on stays in the loop.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.delenv("ICDEV_DATABASE_URL", raising=False)
    made = []

    def _make(statements=(), skip_tables=(), name="cap.db"):
        db_path = tmp_path / name
        _seed(db_path, statements, skip_tables)
        from tools.db.storage import get_connection

        conn = get_connection(db_path=str(db_path))
        made.append(conn)
        return conn

    yield _make
    for conn in made:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _by_class(report):
    return {c["capability_class"]: c for c in report["classes"]}


def _by_case(report):
    return {c["id"]: c for c in report["known_inert_cases"]}


# ---------------------------------------------------------------------------
# The acceptance test
# ---------------------------------------------------------------------------


def test_five_known_inert_cases_report_zero(conn_factory):
    """All five documented incidents report zero consumption, and say so."""
    # Every row here is the *near miss* that made each case look alive:
    #  - reflexes with state rows but a last run over a year ago
    #  - 78k-scale audit volume where not one row carries a chain hash
    #  - a full GEPA queue where composite == baseline, so nothing is selectable
    #  - prompt_versions rows stuck in 'draft', never activated
    #  - MCP authz decisions that predate the window
    statements = [
        ("INSERT INTO genesis_reflex_state (reflex_name, enabled, last_run_at, total_runs) "
         "VALUES (?, 1, ?, 0)", ("research", OUT_OF_WINDOW)),
        ("INSERT INTO genesis_reflex_state (reflex_name, enabled, last_run_at, total_runs) "
         "VALUES (?, 1, NULL, 0)", ("audit",)),
        ("INSERT INTO audit_trail (event_type, actor, created_at, hash, previous_hash) "
         "VALUES ('code_generated', 'writer', ?, NULL, NULL)", (IN_WINDOW,)),
        ("INSERT INTO audit_trail (event_type, actor, created_at, hash, previous_hash) "
         "VALUES ('code_generated', 'writer', ?, NULL, NULL)", (IN_WINDOW,)),
        ("INSERT INTO audit_trail (event_type, actor, created_at, hash, previous_hash) "
         "VALUES ('proof', 'old-harness', ?, 'abc', '000')", (OUT_OF_WINDOW,)),
        ("INSERT INTO audit_platform (event_type, details, recorded_at) "
         "VALUES ('mcp.authz', ?, ?)",
         ('{"rbac_role": "developer", "enforced": false}', OUT_OF_WINDOW)),
        ("INSERT INTO prompt_versions (id, prompt_name, version, status, updated_at) "
         "VALUES ('p1', 'karpathy_principles', 1, 'draft', ?)", (IN_WINDOW,)),
        ("INSERT INTO agent_improvement_artifacts "
         "(artifact_id, skill_used, composite_score, baseline_score, status, applied_count) "
         "VALUES ('a1', 'build', 1.0, 1.0, 'pending', 0)", ()),
        ("INSERT INTO agent_improvement_artifacts "
         "(artifact_id, skill_used, composite_score, baseline_score, status, applied_count) "
         "VALUES ('a2', 'test', 0.9, 0.9, 'pending', 0)", ()),
    ]
    report = capcon.collect(conn=conn_factory(statements), config=CONFIG)
    cases = _by_case(report)
    classes = _by_class(report)

    assert report["totals"]["unmeasurable_classes"] == 0, (
        "a case that cannot be measured must never be scored as zero: "
        f"{[c['capability_class'] for c in report['classes'] if not c['telemetry_available']]}"
    )
    assert set(cases) == {
        "audit_chain_writer", "mcp_tool_authorizer", "prompt_registry",
        "gepa_optimizer", "inert_reflexes",
    }

    for case_id in ("audit_chain_writer", "mcp_tool_authorizer", "prompt_registry",
                    "gepa_optimizer"):
        case = cases[case_id]
        assert case["measured"] is True, case
        assert case["value"] == 0, f"{case_id} should have zero consumption: {case}"
        assert case["still_inert"] is True, case

    # The reflex case is per-unit: the class total can look healthy while an
    # individual reflex has never fired, which is exactly xbm-wake-02.
    reflex_case = cases["inert_reflexes"]
    assert reflex_case["measured"] is True
    assert reflex_case["still_inert"] is True
    assert reflex_case["value"] > 0
    assert classes["reflex"]["consumed"] == 0
    assert classes["reflex"]["events"] == 0
    assert classes["reflex"]["inert"] == classes["reflex"]["declared"]

    # Volume without consumption is the whole defect: audit rows were written,
    # none of them chained.
    chain = classes["audit_chain"]
    assert chain["extra"]["audit_rows_in_window"] == 2
    assert chain["extra"]["hashed_rows_in_window"] == 0
    assert chain["extra"]["coverage_pct"] == 0.0

    # A full GEPA queue that its own predicate can never select from.
    gepa = classes["skill_optimizer"]
    assert gepa["extra"]["pending_artifacts"] == 2
    assert gepa["extra"]["artifacts_matching_selection_predicate"] == 0


# ---------------------------------------------------------------------------
# Positive controls — the zeroes above must be real, not a broken probe
# ---------------------------------------------------------------------------


def test_in_window_consumption_is_counted(conn_factory):
    """The same probes report non-zero when the telemetry actually has rows."""
    statements = [
        ("INSERT INTO genesis_reflex_state (reflex_name, enabled, last_run_at, total_runs) "
         "VALUES ('research', 1, ?, 12)", (IN_WINDOW,)),
        ("INSERT INTO audit_trail (event_type, actor, created_at, hash, previous_hash) "
         "VALUES ('code_generated', 'writer', ?, 'h1', '000')", (IN_WINDOW,)),
        ("INSERT INTO audit_platform (event_type, details, recorded_at) "
         "VALUES ('mcp.authz', ?, ?)",
         ('{"rbac_role": "developer", "enforced": true}', IN_WINDOW)),
        ("INSERT INTO prompt_versions (id, prompt_name, version, status, updated_at) "
         "VALUES ('p1', 'karpathy_principles', 1, 'active', ?)", (IN_WINDOW,)),
        ("INSERT INTO agent_improvement_artifacts "
         "(artifact_id, skill_used, composite_score, baseline_score, status, "
         " applied_count, applied_at) "
         "VALUES ('a1', 'build', 0.9, 0.5, 'applied', 1, ?)", (IN_WINDOW,)),
        ("INSERT INTO agent_improvement_artifacts "
         "(artifact_id, skill_used, composite_score, baseline_score, status, applied_count) "
         "VALUES ('a2', 'test', 0.9, 0.5, 'pending', 0)", ()),
    ]
    report = capcon.collect(conn=conn_factory(statements), config=CONFIG)
    cases = _by_case(report)
    classes = _by_class(report)

    assert cases["audit_chain_writer"]["value"] == 1
    assert cases["audit_chain_writer"]["still_inert"] is False
    assert cases["mcp_tool_authorizer"]["value"] == 1
    assert cases["mcp_tool_authorizer"]["still_inert"] is False
    assert cases["prompt_registry"]["value"] == 1
    assert cases["prompt_registry"]["still_inert"] is False
    assert cases["gepa_optimizer"]["value"] == 1
    assert cases["gepa_optimizer"]["still_inert"] is False

    assert classes["reflex"]["consumed"] == 1
    assert classes["mcp_tool_authorization"]["extra"]["verdicts_enforced"] == 1
    assert classes["skill_optimizer"]["extra"]["artifacts_matching_selection_predicate"] == 1
    assert classes["audit_chain"]["extra"]["coverage_pct"] == 100.0


def test_gepa_predicate_rejects_zero_delta_but_accepts_real_gain(conn_factory):
    """Selection mirrors gepa_optimizer: >= 0.60 composite AND >= 0.05 over baseline."""
    statements = [
        # equal scores — the live shape, delta 0
        ("INSERT INTO agent_improvement_artifacts "
         "(artifact_id, skill_used, composite_score, baseline_score, status, applied_count) "
         "VALUES ('eq', 'build', 1.0, 1.0, 'pending', 0)", ()),
        # real gain but below the composite floor
        ("INSERT INTO agent_improvement_artifacts "
         "(artifact_id, skill_used, composite_score, baseline_score, status, applied_count) "
         "VALUES ('low', 'test', 0.50, 0.10, 'pending', 0)", ()),
        # above the floor with a real gain — the only selectable one
        ("INSERT INTO agent_improvement_artifacts "
         "(artifact_id, skill_used, composite_score, baseline_score, status, applied_count) "
         "VALUES ('ok', 'review', 0.80, 0.60, 'pending', 0)", ()),
        # scored, but not pending
        ("INSERT INTO agent_improvement_artifacts "
         "(artifact_id, skill_used, composite_score, baseline_score, status, applied_count) "
         "VALUES ('done', 'ship', 0.90, 0.10, 'applied', 1)", ()),
    ]
    report = capcon.collect(
        conn=conn_factory(statements), config=CONFIG, only=["skill_optimizer"]
    )
    gepa = _by_class(report)["skill_optimizer"]
    assert gepa["extra"]["pending_artifacts"] == 3
    assert gepa["extra"]["artifacts_matching_selection_predicate"] == 1


# ---------------------------------------------------------------------------
# Unmeasurable must never masquerade as zero
# ---------------------------------------------------------------------------


def test_missing_telemetry_table_is_unmeasurable_not_zero(conn_factory):
    """A dropped telemetry table reports telemetry_available=false, not events=0."""
    conn = conn_factory(skip_tables=("audit_trail", "prompt_versions"))
    report = capcon.collect(conn=conn, config=CONFIG)
    classes = _by_class(report)

    for name in ("audit_chain", "prompt_template"):
        assert classes[name]["telemetry_available"] is False, name
        assert classes[name]["unmeasured_reason"], name
        assert classes[name]["events"] == 0  # the field is zero...
        # ...but the case must NOT be reported as a measured inert zero.
    cases = _by_case(report)
    assert cases["prompt_registry"]["measured"] is False
    assert cases["prompt_registry"]["value"] is None
    assert cases["prompt_registry"]["still_inert"] is None
    assert cases["audit_chain_writer"]["measured"] is False

    assert report["totals"]["unmeasurable_classes"] == 2
    # Unmeasurable classes are excluded from the totals rather than counted as
    # inert, so a broken probe cannot inflate the "everything is fine" number.
    assert "audit_chain" not in report["totals"]["fully_inert_classes"]
    assert "prompt_template" not in report["totals"]["fully_inert_classes"]


def test_missing_hash_column_is_unmeasurable(conn_factory, tmp_path):
    """audit_trail without migration 149 is unmeasurable, not a zero-coverage chain."""
    db_path = tmp_path / "nochain.db"
    raw = sqlite3.connect(str(db_path))
    try:
        for ddl in SCHEMA:
            if "CREATE TABLE audit_trail " in ddl:
                ddl = ("CREATE TABLE audit_trail (id INTEGER PRIMARY KEY, "
                       "event_type TEXT, actor TEXT, created_at TEXT)")
            raw.execute(ddl)
        raw.commit()
    finally:
        raw.close()
    from tools.db.storage import get_connection

    conn = get_connection(db_path=str(db_path))
    try:
        report = capcon.collect(conn=conn, config=CONFIG, only=["audit_chain"])
    finally:
        conn.close()
    chain = _by_class(report)["audit_chain"]
    assert chain["telemetry_available"] is False
    assert "migration 149" in chain["unmeasured_reason"]


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "age_days,window_days,expect_events",
    [(1, 30, 1), (45, 30, 0), (45, 90, 1), (1, 1, 1)],
)
def test_window_is_configurable(conn_factory, age_days, window_days, expect_events):
    """The lookback actually filters — same rows, different windows, different counts."""
    # Pad INWARD, not outward. `timedelta(days=age_days, hours=1)` makes a
    # "1 day old" row 25 hours old, which cannot be inside a 1-day window —
    # the (1, 1, 1) case was arithmetically unsatisfiable and failed every
    # run. The hour of slack is there to keep the row off the boundary, so
    # it has to move the row further INSIDE its nominal age, not past it.
    stamp = (
        NOW - timedelta(days=age_days) + timedelta(hours=1)
    ).strftime("%Y-%m-%dT%H:%M:%S.%f%z")
    conn = conn_factory([
        ("INSERT INTO audit_trail (event_type, actor, created_at, hash, previous_hash) "
         "VALUES ('code_generated', 'writer', ?, 'h1', '000')", (stamp,)),
    ])
    report = capcon.collect(
        conn=conn, config=CONFIG, window_days=window_days, only=["audit_chain"]
    )
    assert report["window_days"] == window_days
    assert _by_class(report)["audit_chain"]["events"] == expect_events


def test_space_separated_timestamps_are_not_dropped(conn_factory):
    """A PG-formatted ISO string ('YYYY-MM-DD HH:MM:SS+00') still falls in the window.

    Both separators appear in these columns depending on whether Python or
    PostgreSQL wrote the row. A bound formatted with 'T' sorts above a
    same-instant space-separated value and silently drops it.
    """
    stamp = (NOW - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S+00")
    conn = conn_factory([
        ("INSERT INTO audit_trail (event_type, actor, created_at, hash, previous_hash) "
         "VALUES ('code_generated', 'writer', ?, 'h1', '000')", (stamp,)),
    ])
    report = capcon.collect(conn=conn, config=CONFIG, window_days=1, only=["audit_chain"])
    assert _by_class(report)["audit_chain"]["events"] == 1


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


def test_report_uses_only_existing_telemetry_tables(conn_factory):
    """No probe may invent a telemetry table — every source predates this tool."""
    allowed = {
        "genesis_reflex_state", "studio_mcp_dispatch_audit", "agent_approval_log",
        "audit_platform", "prompt_versions", "audit_trail", "agent_improvement_artifacts",
        # migration 341 — the runtime telemetry table, four surfaces older than
        # the extension one hcx-live-02 records dispatches on.
        "runtime_invocations",
    }
    report = capcon.collect(conn=conn_factory(), config=CONFIG)
    for cls in report["classes"]:
        table = cls["telemetry_table"].split(" ")[0]
        assert table in allowed, f"{cls['capability_class']} reads unknown table {table}"


def test_shipped_config_covers_every_probe():
    """args/capability_consumption.yaml must describe every class the tool probes."""
    cfg = capcon.load_config()
    assert set(cfg.get("classes") or {}) == set(capcon.PROBES)
    covered = {c["capability_class"] for c in cfg.get("known_inert_cases") or []}
    assert covered <= set(capcon.PROBES)
    assert len(cfg.get("known_inert_cases") or []) == 5


def test_class_filter_restricts_the_report(conn_factory):
    report = capcon.collect(conn=conn_factory(), config=CONFIG, only=["reflex"])
    assert [c["capability_class"] for c in report["classes"]] == ["reflex"]


def test_disabled_class_is_skipped(conn_factory):
    config = dict(CONFIG)
    config["classes"] = dict(CONFIG["classes"])
    config["classes"]["reflex"] = {"enabled": False}
    report = capcon.collect(conn=conn_factory(), config=config)
    assert "reflex" not in _by_class(report)


# ---------------------------------------------------------------------------
# extension_hook_point (hcx-live-02)
# ---------------------------------------------------------------------------
# The seam args/extension_config.yaml declares ten points for. Nothing counted a
# dispatch until hcx-live-02, so "consumed" and "never called in the platform's
# history" were the same reading. Eight of the ten have no dispatch call site.


def _dispatch_row(point, when=IN_WINDOW, status="ok", surface="extension"):
    return (
        "INSERT INTO runtime_invocations (id, surface, name, started_at, status) "
        "VALUES (?, ?, ?, ?, ?)",
        (f"inv-{point}-{when}-{status}-{surface}", surface, point, when, status),
    )


def test_a_hook_point_nothing_dispatches_reports_inert(conn_factory):
    """The negative, with a positive control on the same table beside it.

    Without the control this test would pass just as well if the probe silently
    read nothing at all — the failure mode the tool exists to detect.
    """
    report = capcon.collect(
        conn=conn_factory([_dispatch_row("chat_message_after")]),
        config=CONFIG,
        only=["extension_hook_point"],
    )
    cls = _by_class(report)["extension_hook_point"]

    assert cls["telemetry_available"] is True
    assert cls["declared"] == 10
    assert cls["consumed"] == 1, "the positive control did not register"
    assert "tool_execute_after" in cls["inert_units"]
    assert "chat_message_after" not in cls["inert_units"]


def test_rows_from_another_surface_are_not_counted_as_dispatches(conn_factory):
    """``runtime_invocations`` is shared. An MCP tool named after a hook point
    must not launder that point into looking consumed."""
    report = capcon.collect(
        conn=conn_factory([_dispatch_row("agent_start", surface="mcp")]),
        config=CONFIG,
        only=["extension_hook_point"],
    )
    cls = _by_class(report)["extension_hook_point"]

    assert cls["consumed"] == 0
    assert "agent_start" in cls["inert_units"]


def test_a_dispatch_whose_handler_failed_still_counts_as_consumption(conn_factory):
    """Consumed and broken is a different reading from never called.

    Folding a failing dispatch into the inert count would hide a wired-and-
    broken hook behind the same number as a hook nobody calls.
    """
    report = capcon.collect(
        conn=conn_factory([_dispatch_row("chat_message_before", status="error")]),
        config=CONFIG,
        only=["extension_hook_point"],
    )
    cls = _by_class(report)["extension_hook_point"]

    assert "chat_message_before" not in cls["inert_units"]
    assert cls["extra"]["failed_dispatch_events"] == 1
    assert cls["extra"]["points_with_failures"] == ["chat_message_before"]


def test_a_point_disabled_in_config_is_not_counted_as_declared(
    conn_factory, tmp_path, monkeypatch
):
    """Stood down on purpose is not the defect — the rule probe_reflex applies.

    Also the reason the disabled set is reported in ``extra`` rather than just
    dropped: a point missing from the declared count for a good reason and one
    missing because somebody deleted it look identical in the total.
    """
    override = tmp_path / "extension_config.yaml"
    override.write_text(
        "extensions:\n"
        "  hook_points:\n"
        "    agent_start:\n"
        "      enabled: false\n"
        "    memory_save_after:\n"
        "      enabled: false\n",
        encoding="utf-8",
    )
    real = capcon._repo_file
    monkeypatch.setattr(
        capcon, "_repo_file",
        lambda rel: override if rel.endswith("extension_config.yaml") else real(rel),
    )

    report = capcon.collect(
        conn=conn_factory(), config=CONFIG, only=["extension_hook_point"]
    )
    cls = _by_class(report)["extension_hook_point"]

    assert cls["declared"] == 8
    assert cls["extra"]["disabled_in_config"] == ["agent_start", "memory_save_after"]
    assert cls["extra"]["enum_points_total"] == 10


def test_an_absent_runtime_invocations_is_unmeasurable_not_zero(conn_factory):
    report = capcon.collect(
        conn=conn_factory(skip_tables=("runtime_invocations",)),
        config=CONFIG,
        only=["extension_hook_point"],
    )
    cls = _by_class(report)["extension_hook_point"]

    assert cls["telemetry_available"] is False
    assert "runtime_invocations" in cls["unmeasured_reason"]


def test_the_enum_is_read_without_importing_the_module():
    """Importing it builds the singleton, which auto-loads nine chat builtins.

    A measurement tool that executes eleven extension modules to count ten names
    reports on the importer as much as on the seam — and this probe runs twice
    per commit inside check_capability_liveness.
    """
    import sys

    sys.modules.pop("tools.extensions.extension_manager", None)
    points = capcon._extension_points_from_source()

    assert "tool_execute_before" in points and len(points) == 10
    assert "tools.extensions.extension_manager" not in sys.modules
