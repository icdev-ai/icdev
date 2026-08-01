# CUI // SP-CTI
"""Tests for the unified component registry loader.

These tests lock in parity between the new `args/component_registry.yaml` and
the legacy hardcoded lists in `tools/dashboard/app.py`:
  - _CANVAS_DEFS
  - _APP_DEFS
  - _CANVAS_ROUTES
  - _CANVAS_MAP (IQE dispatch)

They also verify that the registry loader is deterministic, read-only, and
handles missing/invalid entries gracefully.
"""

from __future__ import annotations

import re
import sqlite3

import pytest

from tools.config.component_registry import ComponentRegistry, get_registry, log_component_audit, reset_registry_singleton


# ---------------------------------------------------------------------------
# Expected legacy values from tools/dashboard/app.py (as of Phase 0)
# ---------------------------------------------------------------------------

_EXPECTED_CANVAS_DEFS = [
    ("idc", "ICDEV_IDC_ENABLED", "tools.infra_canvas.blueprint", "infra_bp"),
    ("ndc", "ICDEV_NDC_ENABLED", "tools.network.blueprint", "create_network_blueprint"),
    ("sdc", "ICDEV_SDC_ENABLED", "tools.security_canvas.blueprint", "create_security_blueprint"),
    ("bdc", "ICDEV_BDC_ENABLED", "tools.boundary_canvas.blueprint", "create_boundary_blueprint"),
    ("pdc", "ICDEV_PDC_ENABLED", "tools.pipeline.blueprint", "create_pipeline_blueprint"),
    ("odc", "ICDEV_ODC_ENABLED", "tools.observability_canvas.blueprint", "create_observability_blueprint"),
    ("ddc", "ICDEV_DDC_ENABLED", "tools.data_canvas.blueprint", "create_data_canvas_blueprint"),
    ("qdc", "ICDEV_QDC_ENABLED", "tools.qdc_canvas.blueprint", "qdc_bp"),
    ("mdc", "ICDEV_MIGRATION_CANVAS_ENABLED", "tools.migration_canvas.blueprint", "create_migration_blueprint"),
    ("aadc", "ICDEV_AADC_ENABLED", "tools.agentic_ai_canvas.blueprint", "aadc_bp"),
    ("aimc", "ICDEV_AIML_CANVAS_ENABLED", "tools.aiml_canvas.blueprint", "create_aiml_blueprint"),
    ("ohc", "ICDEV_OPS_HUB_ENABLED", "tools.ops_hub.blueprint", "create_ops_hub_blueprint"),
    ("mission_canvas", "ICDEV_MISSION_CANVAS_ENABLED", "tools.mission_canvas.blueprint", "create_mission_canvas_blueprint"),
    ("nocc", "ICDEV_NOCC_ENABLED", "tools.noc_canvas.blueprint", "create_noc_canvas_blueprint"),
    ("pmc", "ICDEV_PMC_ENABLED", "tools.pmc_canvas.blueprint", "create_pmc_blueprint"),
    ("ccc", "ICDEV_CCC_ENABLED", "tools.ccc_canvas.blueprint", "create_ccc_blueprint"),
    ("dsoc", "ICDEV_DSOC_ENABLED", "tools.dsoc_canvas.blueprint", "create_dsoc_blueprint"),
    ("aiify", "ICDEV_AIIFY_ENABLED", "tools.aiify.blueprint", "aiify_bp"),
    ("aiify_compat", "ICDEV_AIIFY_ENABLED", "tools.aiify.blueprint", "aiify_compat_bp"),
    ("dic", "ICDEV_DIC_ENABLED", "tools.document_intelligence.blueprint", "dic_bp"),
    ("demo_runner", "ICDEV_DEMO_RUNNER_ENABLED", "tools.showcase.blueprint", "demo_runner_bp"),
    ("slides", "ICDEV_SLIDES_ENABLED", "tools.slides.blueprint", "slides_bp"),
    ("ace", "ICDEV_ACE_ENABLED", "icdev.tools.ace.blueprint", "ace_bp"),
    ("aisg", "ICDEV_AISG_ENABLED", "tools.aisg.blueprint", "bp"),
    ("integrity", "ICDEV_INTEGRITY_ENABLED", "tools.integrity.blueprint", "create_integrity_blueprint"),
    ("foundry", "ICDEV_FOUNDRY_ENABLED", "tools.foundry.blueprint", "create_foundry_blueprint"),
    ("logs", "ICDEV_LOGS_ENABLED", "tools.logging.blueprint", "create_logs_blueprint"),
]

_EXPECTED_CANVAS_DEFAULTS_TRUE = {"ndc", "sdc", "aimc", "mission_canvas", "ohc", "integrity", "logs"}

_EXPECTED_APP_DEFS = [
    ("hitl_workflow", "ICDEV_HITL_ENABLED", "tools.workflow_hitl.blueprint", "create_wf_page_blueprint"),
    ("forge_academy", "ICDEV_FORGE_ACADEMY_ENABLED", "apps.forge_academy.blueprint", "academy_bp"),
    ("gameday", "ICDEV_GAMEDAY_ENABLED", "apps.ai_gameday.blueprint", "bp"),
    ("innovation", "ICDEV_INNOVATION_ENABLED", "apps.innovation.blueprint", "innovation_bp"),
]

_EXPECTED_CANVAS_ROUTES = {
    "idc": "/infra",
    "ndc": "/network",
    "sdc": "/security",
    "bdc": "/boundary",
    "pdc": "/devops",
    "odc": "/observability",
    "ddc": "/data",
    "qdc": "/quality",
    "mdc": "/migration-canvas",
    "aimc": "/ai-ml",
    "ohc": "",
    "mission_canvas": "/mission-canvas",
    "nocc": "",
    "pmc": "",
    "ccc": "",
    "dsoc": "",
    "integrity": "",
    "foundry": "",
    "logs": "",
}

# Subset of the legacy _CANVAS_MAP used for IQE dispatch.
_EXPECTED_IQE_MAP_SUBSET = {
    "ndc": ("tools.iqe.adapters.ndc", [
        "network.topologies", "network.devices", "network.objects",
        "network.circuits", "network.sites", "network.ai_decisions",
        "network.partners", "network.agreements_expiring",
    ]),
    "sdc": ("tools.iqe.adapters.security", [
        "attack.nodes", "attack.edges", "attack.paths", "security.ai_decisions",
    ]),
    "pdc": ("tools.iqe.adapters.pipeline", [
        "pipeline.snapshots", "pipeline.nodes", "pipeline.edges", "pipeline.ai_decisions",
    ]),
    "ddc": ("tools.iqe.adapters.data", [
        "data.lineage.edges", "data.classifications", "data.ai_decisions",
    ]),
    "idc": ("tools.iqe.adapters.infra", [
        "infra.resources", "infra.snapshots", "infra.ai_decisions",
    ]),
    "odc": ("tools.iqe.adapters.observability", [
        "mitre.techniques", "mitre.coverage", "mitre.gaps", "observability.ai_decisions",
    ]),
    "bdc": ("tools.iqe.adapters.bdc", [
        "bdc.designs", "bdc.assessments", "bdc.isas", "bdc.alerts", "bdc.ai_decisions",
    ]),
    "aadc": ("tools.iqe.adapters.aadc", [
        "aadc.designs", "aadc.assessments", "aadc.artifacts", "aadc.ai_decisions",
    ]),
    "aimc": ("tools.iqe.adapters.aimc", [
        "aimc.designs", "aimc.nodes", "aimc.assessments", "aimc.artifacts", "aimc.ai_decisions",
    ]),
    "ohc": ("tools.iqe.adapters.ohc", [
        "ohc.experiments", "ohc.runs", "ohc.models", "ohc.datasets", "ohc.adapters", "ohc.drift_events",
    ]),
    "qdc": ("tools.iqe.adapters.qdc", [
        "qdc.designs", "qdc.assessments", "qdc.gate_results", "qdc.ai_decisions",
    ]),
    "dic": ("tools.iqe.adapters.dic", [
        "dic.drift_events", "dic.regen_queue", "dic.ssp_fragments",
    ]),
    "integrity": ("tools.iqe.adapters.integrity", [
        "integrity.assessments", "integrity.capabilities", "integrity.findings", "integrity.verdicts",
    ]),
}

# Legacy CLI toggles from tools/cli/enable.py
_EXPECTED_CLI_TOGGLES = {
    "boundary": ["ICDEV_BDC_ENABLED", "ICDEV_BOUNDARY_ENABLED"],
    "data": ["ICDEV_DDC_ENABLED", "ICDEV_DATA_CANVAS_ENABLED"],
    "infra": ["ICDEV_IDC_ENABLED", "ICDEV_INFRA_ENABLED"],
    "network": ["ICDEV_NDC_ENABLED", "ICDEV_NETWORK_ENABLED"],
    "observability": ["ICDEV_ODC_ENABLED", "ICDEV_OBSERVABILITY_ENABLED"],
    "pipeline": ["ICDEV_PDC_ENABLED", "ICDEV_PIPELINE_ENABLED"],
    "security": ["ICDEV_SDC_ENABLED", "ICDEV_SECURITY_ENABLED"],
    "quality": ["ICDEV_QDC_ENABLED"],
    "migration": ["ICDEV_MIGRATION_CANVAS_ENABLED"],
    "canvas-kg": ["ICDEV_CANVAS_KG_ENABLED"],
    "rag": ["RAG_ENABLED"],
    "govcon": ["ICDEV_GOVCON_ENABLED"],
    "finetune": ["FINETUNE_ENABLED"],
    "filesync": ["ICDEV_FILESYNC_ENABLED"],
    "cui-banner": ["ICDEV_CUI_BANNER_ENABLED"],
    "byok": ["ICDEV_BYOK_ENABLED"],
    "two-tier-llm": ["LLM_TWO_TIER_ENABLED"],
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """Return a registry instance bound to a temp env and isolated singleton."""
    reset_registry_singleton()
    env = {"ICDEV_STORAGE_BACKEND": "sqlite"}
    # Ensure we load the real registry file, not any temp copy.
    r = ComponentRegistry(env=env)
    yield r
    reset_registry_singleton()


# ---------------------------------------------------------------------------
# Parity tests
# ---------------------------------------------------------------------------


def test_registry_has_all_legacy_canvas_defs(registry):
    """Every legacy _CANVAS_DEFS tuple appears in the registry."""
    for key, env_flag, module, blueprint_attr in _EXPECTED_CANVAS_DEFS:
        comp = registry.get(key)
        assert comp is not None, f"missing canvas {key}"
        assert comp.kind == "canvas"
        assert comp.env_flag == env_flag
        assert comp.module == module
        assert comp.blueprint_attr == blueprint_attr


def test_registry_has_all_legacy_app_defs(registry):
    """Every legacy _APP_DEFS tuple appears in the registry."""
    for key, env_flag, module, blueprint_attr in _EXPECTED_APP_DEFS:
        comp = registry.get(key)
        assert comp is not None, f"missing child_app {key}"
        assert comp.kind == "child_app"
        assert comp.env_flag == env_flag
        assert comp.module == module
        assert comp.blueprint_attr == blueprint_attr


def test_registry_default_enabled_matches_legacy(registry):
    """Every canvas that was default-enabled under _CANVAS_DEFAULTS_TRUE still is.

    Superset, not equality: canvases added after the registry migration may also be
    default-enabled. Pinning exact equality made every new canvas fail this test,
    which is a snapshot of a completed migration, not a policy on future canvases.
    """
    enabled = registry.get_default_enabled_flags()
    regressed = _EXPECTED_CANVAS_DEFAULTS_TRUE - set(enabled)
    assert not regressed, f"canvases silently default-disabled since migration: {sorted(regressed)}"


def test_registry_url_prefixes_match_legacy_routes(registry):
    """Registry url_prefix values match legacy _CANVAS_ROUTES."""
    prefixes = registry.get_url_prefixes()
    mismatches = []
    for key, expected in _EXPECTED_CANVAS_ROUTES.items():
        actual = prefixes.get(key)
        if actual != expected:
            mismatches.append(f"{key}: expected {expected!r}, got {actual!r}")
    assert not mismatches, "url_prefix mismatches: " + "; ".join(mismatches)


def test_registry_iqe_mapping_matches_legacy_subset(registry):
    """Registry IQE mapping still contains every legacy entry.

    Subset, not equality: a canvas may register additional collections over time
    (ndc has gained eight since this snapshot). The guarantee worth pinning is that
    no legacy collection was dropped in the move off the hardcoded _CANVAS_MAP.
    """
    iqe_map = registry.get_iqe_mapping()
    for key, (expected_module, expected_collections) in _EXPECTED_IQE_MAP_SUBSET.items():
        assert key in iqe_map, f"missing IQE mapping for {key}"
        module, collections = iqe_map[key]
        assert module == expected_module
        dropped = set(expected_collections) - set(collections)
        assert not dropped, f"{key} dropped legacy IQE collections: {sorted(dropped)}"


def test_registry_cli_toggles_match_legacy(registry):
    """Registry-derived CLI toggles match the legacy tools/cli/enable.py TOGGLES."""
    toggles = registry.get_cli_toggles()
    for name, flags in _EXPECTED_CLI_TOGGLES.items():
        assert toggles.get(name) == flags, f"toggle {name} mismatch: got {toggles.get(name)}"


def test_registry_cli_descriptions_present(registry):
    """Every CLI toggle has a description."""
    descriptions = registry.get_cli_descriptions()
    toggles = registry.get_cli_toggles()
    assert set(descriptions.keys()) >= set(toggles.keys())
    for name in toggles:
        assert descriptions[name]


def test_every_registry_env_flag_is_cli_reachable(registry):
    """Drift guard (pkg-reg-01): every env flag declared in the registry —
    primary ``env_flag`` and any ``extra_env_flags`` — must be reachable from
    `icdev enable/disable` via get_cli_toggles().

    This is the test that stops the 21-flag gap (core_extension / child_app
    env flags with no CLI path) from returning. It names the offending flags,
    not a bare count, so a failure points straight at the component.
    """
    all_flags: set[str] = set()
    for c in registry.list_all():
        if c.env_flag:
            all_flags.add(c.env_flag)
        all_flags.update(c.extra_env_flags)

    reachable: set[str] = set()
    for flags in registry.get_cli_toggles().values():
        reachable.update(flags)

    unreachable = sorted(all_flags - reachable)
    assert not unreachable, (
        "registry env flags with NO CLI path (add them to the CLI surface "
        f"or give them an env_flag-less kind): {unreachable}"
    )


def test_cli_enable_toggles_are_registry_derived():
    """tools/cli/enable.py must derive TOGGLES from the registry, not a literal
    dict — the anti-drift guarantee CLAUDE.md requires."""
    from tools.cli import enable as enable_mod

    assert enable_mod.TOGGLES == enable_mod._REGISTRY.get_cli_toggles()
    # Every toggle the CLI exposes resolves to a non-empty flag list.
    for name, flags in enable_mod.TOGGLES.items():
        assert flags, f"toggle {name} has no env flags"


# ---------------------------------------------------------------------------
# Loader behavior tests
# ---------------------------------------------------------------------------


def test_registry_counts(registry):
    """No component kind lost members since the registry migration.

    Lower bounds rather than exact counts: the registry is meant to grow, and an
    exact assertion turns "someone added a canvas" into a test failure. The counts
    below are the Phase-0 migration snapshot and must never regress.
    """
    assert len(registry.list_all(kind="canvas")) >= 28
    assert len(registry.list_all(kind="child_app")) >= 4
    assert len(registry.list_all(kind="feature")) >= 8
    assert len(registry.list_all(kind="core_extension")) >= 15
    assert len(registry) >= 55
    # Every canvas the legacy _CANVAS_DEFS knew about is still registered.
    keys = {c.key for c in registry.list_all(kind="canvas")}
    legacy = {k for k, _, _, _ in _EXPECTED_CANVAS_DEFS}
    assert legacy <= keys, f"canvases dropped from the registry: {sorted(legacy - keys)}"


def test_component_enablement_uses_primary_env_flag(registry):
    """is_enabled respects env_flag + default_enabled only."""
    comp = registry.get("dic")
    assert comp is not None
    assert comp.is_enabled({"ICDEV_DIC_ENABLED": "false"}) is False
    assert comp.is_enabled({"ICDEV_DIC_ENABLED": "true"}) is True
    assert comp.is_enabled({"ICDEV_DIC_ENABLED": "1"}) is True
    assert comp.is_enabled({"ICDEV_DIC_ENABLED": "yes"}) is True
    # Default when missing
    assert comp.is_enabled({}) is False  # dic default_enabled=false

    # Default-enabled canvas
    ndc = registry.get("ndc")
    assert ndc.is_enabled({}) is True
    assert ndc.is_enabled({"ICDEV_NDC_ENABLED": "false"}) is False


def test_feature_component_has_no_blueprint(registry):
    """Feature toggles have no module/blueprint and resolve to None."""
    rag = registry.get("rag")
    assert rag.kind == "feature"
    assert rag.module is None
    assert rag.blueprint_attr is None
    assert rag.get_blueprint() is None
    assert registry.get_blueprint("rag") is None


def test_get_blueprint_returns_none_for_missing_module(registry):
    """Invalid module paths return None without raising."""
    # Use a synthetic registry so we don't need a real bad module on disk.
    from tools.config.component_registry import Component
    fake = Component(
        key="fake",
        kind="canvas",
        cli_name="fake",
        display_name="Fake",
        description="",
        env_flag="ICDEV_FAKE_ENABLED",
        extra_env_flags=[],
        default_enabled=False,
        module="definitely.not.a.real.module",
        blueprint_attr="bp",
        url_prefix="/fake",
        min_il="IL2",
        min_tier="community",  # required field added to Component after this test was written
        default_roles=[],
        nav={},
        iqe={},
        completeness={},
        raw={},
    )
    assert fake.get_blueprint() is None


def test_registry_handles_missing_file(tmp_path):
    """Missing registry file loads empty components gracefully."""
    missing = tmp_path / "missing.yaml"
    r = ComponentRegistry(registry_path=missing, env={})
    assert len(r) == 0
    assert r.get_iqe_mapping() == {}
    assert r.get_toggles() == {}


def test_registry_singleton_reset():
    """Singleton can be reset for tests."""
    reset_registry_singleton()
    r1 = get_registry()
    r2 = get_registry()
    assert r1 is r2
    reset_registry_singleton()
    r3 = get_registry()
    assert r3 is not r1
    reset_registry_singleton()


def test_nav_context_has_sections(registry):
    """Navigation context groups enabled/disabled components by section/label."""
    ctx = registry.get_nav_context()
    sections = ctx["sections"]
    assert "Canvases" in sections
    assert "Build" in sections
    assert "Ops" in sections
    # Components are grouped by label within each section.
    canvas_items = [
        item
        for section in sections.values()
        for group in section["groups"]
        for item in group["items"]
    ]
    canvas_keys = {item["key"] for item in canvas_items}
    assert "dic" in canvas_keys
    assert "ndc" in canvas_keys


def test_nav_context_links_have_required_keys(registry):
    """Every nav link declares at least label and href."""
    ctx = registry.get_nav_context()
    for section in ctx["sections"].values():
        for group in section["groups"]:
            for item in group["items"]:
                assert item["links"], f"{item['key']} has no nav links"
                for link in item["links"]:
                    assert link["label"], f"{item['key']} has a link without label"
                    assert link["href"], f"{item['key']} has a link without href"


def test_nav_context_default_dashboard_for_components_without_links(registry):
    """A component with a non-empty url_prefix but no explicit nav.links falls
    back to a single "Dashboard" link pointing at that url_prefix."""
    # finetune declares a nav section and url_prefix (/finetune) but no links.
    ctx = registry.get_nav_context()
    finetune = None
    for section in ctx["sections"].values():
        for group in section["groups"]:
            for item in group["items"]:
                if item["key"] == "finetune":
                    finetune = item
    assert finetune is not None
    assert finetune["links"][0]["label"] == "Dashboard"
    assert finetune["links"][0]["href"] == "/finetune/"


def test_nav_context_no_component_links_to_bare_root(registry):
    """cnr-nav-01: no sidebar item may link to bare '/' (the home dashboard).

    Regression guard for the "Operations / NOC Operations take me back to the
    main dashboard" bug: components with url_prefix '' and a nav section but no
    explicit links used to fall back to href '/'.
    """
    ctx = registry.get_nav_context()
    offenders = []
    for section in ctx["sections"].values():
        for group in section["groups"]:
            for item in group["items"]:
                for link in item["links"]:
                    if link["href"] == "/":
                        offenders.append(item["key"])
    assert not offenders, f"components link to bare '/': {offenders}"


def test_normalize_nav_links_explicit_links_pass_through():
    """Explicitly declared links are sanitized and returned unchanged in href."""
    nav = {
        "links": [
            {"label": "Operations", "href": "/ops"},
            {"label": "Details", "href": "/ops/details", "style": "color:#f00;"},
        ]
    }
    out = ComponentRegistry._normalize_nav_links(nav, "")
    assert [(l_["label"], l_["href"]) for l_ in out] == [
        ("Operations", "/ops"),
        ("Details", "/ops/details"),
    ]
    assert out[1]["style"] == "color:#f00;"


def test_normalize_nav_links_empty_prefix_no_links_yields_no_link():
    """cnr-nav-01: empty url_prefix + no declared links must NOT yield href '/'."""
    out = ComponentRegistry._normalize_nav_links({}, "")
    assert out == []
    # Also when nav has a section/label but still no links.
    out2 = ComponentRegistry._normalize_nav_links(
        {"section": "Canvases", "label": "Operations"}, ""
    )
    assert out2 == []


def test_normalize_nav_links_nonempty_prefix_no_links_gets_dashboard():
    """A component with a real url_prefix but no links still gets a Dashboard link."""
    out = ComponentRegistry._normalize_nav_links({}, "/finetune")
    assert len(out) == 1
    assert out[0]["label"] == "Dashboard"
    assert out[0]["href"] == "/finetune/"


# ---------------------------------------------------------------------------
# Tenant component overrides
# ---------------------------------------------------------------------------


class _FakeConn:
    """A SQLite connection that behaves like tools/db/storage.py's wrapper.

    Two behaviours matter and a raw sqlite3.Connection has neither:

    * Runtime SQL is authored for PostgreSQL, so it uses %s placeholders. The real
      wrapper rewrites them via translate_sql(..., backend="sqlite").
    * The real context manager commits on clean exit and rolls back on error.
      contextlib.closing merely closes.

    Without both, every `with get_connection() as conn: conn.execute(INSERT ...)`
    under test either raised (and was swallowed by the caller's except) or was
    rolled back, so tenant overrides and audit rows never persisted.
    """

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        from tools.db.storage import translate_sql

        return self._conn.execute(translate_sql(sql, backend="sqlite"), params or ())

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self._conn.rollback()
        else:
            self._conn.commit()
        self._conn.close()
        return False


def _sqlite_conn_factory(db_path):
    """Return a get_connection-style factory bound to a SQLite file."""
    def _factory():
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return _FakeConn(conn)
    return _factory


def test_tenant_component_override_falls_back_to_env(icdev_db, monkeypatch):
    """No tenant override means env/default decides enablement."""
    reset_registry_singleton()
    from tools.db import storage
    monkeypatch.setattr(
        storage, "get_connection", _sqlite_conn_factory(icdev_db)
    )
    env = {"ICDEV_DIC_ENABLED": "true"}
    registry = ComponentRegistry(env=env)
    assert registry.is_enabled("dic") is True
    assert registry.is_enabled_for_tenant("dic", "tenant-a") is True
    reset_registry_singleton()


def test_tenant_component_override_can_disable_and_re_enable(icdev_db, monkeypatch):
    """Tenant override can both disable and re-enable a component."""
    reset_registry_singleton()
    from tools.db import storage
    monkeypatch.setattr(
        storage, "get_connection", _sqlite_conn_factory(icdev_db)
    )
    env = {"ICDEV_DIC_ENABLED": "true"}
    registry = ComponentRegistry(env=env)

    assert registry.is_enabled_for_tenant("dic", "tenant-b") is True

    registry.set_tenant_component_override(
        "tenant-b", "dic", False, updated_by="test"
    )
    assert registry.is_enabled_for_tenant("dic", "tenant-b") is False

    registry.set_tenant_component_override("tenant-b", "dic", True)
    assert registry.is_enabled_for_tenant("dic", "tenant-b") is True

    registry.clear_tenant_component_override("tenant-b", "dic")
    assert registry.is_enabled_for_tenant("dic", "tenant-b") is True

    overrides = registry.list_tenant_overrides("tenant-b")
    assert not overrides
    reset_registry_singleton()


def test_tenant_component_override_emits_audit_log(icdev_db, monkeypatch):
    """set_tenant_component_override and clear_tenant_component_override are audited."""
    reset_registry_singleton()
    from tools.db import storage
    monkeypatch.setattr(
        storage, "get_connection", _sqlite_conn_factory(icdev_db)
    )
    registry = ComponentRegistry(env={"ICDEV_DIC_ENABLED": "true"})

    registry.set_tenant_component_override(
        "tenant-c", "dic", False, updated_by="audit-tester"
    )
    registry.clear_tenant_component_override("tenant-c", "dic")

    with _sqlite_conn_factory(icdev_db)() as conn:
        rows = conn.execute(
            "SELECT event_type, actor, component_key FROM component_audit_log "
            "WHERE tenant_id = ? ORDER BY recorded_at",
            ("tenant-c",),
        ).fetchall()

    assert len(rows) == 2
    assert rows[0]["event_type"] == "tenant_override_set"
    assert rows[0]["actor"] == "audit-tester"
    assert rows[0]["component_key"] == "dic"
    assert rows[1]["event_type"] == "tenant_override_clear"
    assert rows[1]["component_key"] == "dic"
    reset_registry_singleton()


def test_log_component_audit_swallows_errors(monkeypatch):
    """Audit failures do not propagate to callers."""
    from tools.db import storage

    def _broken_factory():
        raise RuntimeError("db down")

    monkeypatch.setattr(storage, "get_connection", _broken_factory)
    # Should not raise.
    log_component_audit(event_type="test", component_key="dic")


# ---------------------------------------------------------------------------
# Schema / validation tests
# ---------------------------------------------------------------------------


def test_canvas_components_have_required_completeness_fields(registry):
    """Every canvas declares the 8-point completeness gate fields."""
    for comp in registry.iter_canvases():
        assert comp.completeness.get("blueprint") is True, f"{comp.key} missing blueprint completeness"
        assert comp.completeness.get("nav_link") is not None, f"{comp.key} missing nav_link completeness"


# ---------------------------------------------------------------------------
# 8-point completeness validator tests
# ---------------------------------------------------------------------------


def test_validate_canvas_completeness_unknown_key(registry):
    report = registry.validate_canvas_completeness("not_a_real_canvas")
    assert report.key == "not_a_real_canvas"
    assert not report.passed
    assert any(item.point == "registered" and not item.present for item in report.items)


def test_validate_canvas_completeness_non_canvas(registry):
    report = registry.validate_canvas_completeness("rag")
    assert not report.passed
    assert any(item.point == "registered" and "not canvas" in item.message for item in report.items)


def test_validate_canvas_completeness_real_canvas_reports_items(registry):
    """A real canvas (mdc): the validator runs and reports per-point findings."""
    report = registry.validate_canvas_completeness("mdc")
    points = {item.point: item for item in report.items}
    assert "page_template" in points
    assert "blueprint_route" in points
    assert "nav_link" in points
    assert points["page_template"].present is True


def test_validate_canvas_completeness_synthetic_canvas_passes(tmp_path, monkeypatch):
    """Build a synthetic registry + filesystem that satisfies all 8 points."""
    import yaml

    root = tmp_path / "repo"
    root.mkdir()

    # Create a fake canvas package
    canvas_pkg = root / "tools" / "synth_canvas"
    canvas_pkg.mkdir(parents=True)
    (canvas_pkg / "__init__.py").write_text("", encoding="utf-8")
    (canvas_pkg / "blueprint.py").write_text(
        "from flask import Blueprint\nbp = Blueprint('synth', __name__)\n@bp.route('/')\ndef index(): pass\n",
        encoding="utf-8",
    )
    (canvas_pkg / "constants.py").write_text("KEY = 'synth'\n", encoding="utf-8")
    (canvas_pkg / "engine.py").write_text("def analyze(): pass\n", encoding="utf-8")

    # Templates
    (root / "tools" / "dashboard" / "templates" / "synth").mkdir(parents=True)
    (root / "tools" / "dashboard" / "templates" / "synth" / "page.html").write_text(
        '{% extends "base.html" %}', encoding="utf-8"
    )
    (root / "icdev" / "tools" / "dashboard" / "templates" / "synth").mkdir(parents=True)
    (root / "icdev" / "tools" / "dashboard" / "templates" / "synth" / "page.html").write_text(
        '{% extends "base.html" %}', encoding="utf-8"
    )

    # Migrations
    (root / "tools" / "synth_canvas" / "db" / "migrations").mkdir(parents=True)
    (root / "tools" / "synth_canvas" / "db" / "migrations" / "001_init.sql").write_text("", encoding="utf-8")

    # IQE
    (root / "tools" / "iqe" / "adapters").mkdir(parents=True)
    (root / "tools" / "iqe" / "adapters" / "synth.py").write_text("", encoding="utf-8")
    (root / "context" / "iqe" / "queries" / "synth").mkdir(parents=True)
    (root / "context" / "iqe" / "queries" / "synth" / "default.yaml").write_text("", encoding="utf-8")

    registry_yaml = root / "args" / "component_registry.yaml"
    registry_yaml.parent.mkdir(parents=True)
    registry_yaml.write_text(
        yaml.safe_dump(
            {
                "components": [
                    {
                        "key": "synth",
                        "kind": "canvas",
                        "cli_name": "synth",
                        "display_name": "Synthetic Canvas",
                        "description": "Test canvas",
                        "env_flag": "ICDEV_SYNTH_ENABLED",
                        "extra_env_flags": [],
                        "default_enabled": False,
                        "module": "tools.synth_canvas.blueprint",
                        "blueprint_attr": "bp",
                        "url_prefix": "/synth",
                        "min_il": "IL2",
                        "default_roles": [],
                        "nav": {"section": "Canvases", "label": "Synthetic"},
                        "iqe": {
                            "adapter_module": "tools.iqe.adapters.synth",
                            "collections": ["synth.items"],
                        },
                        "completeness": {
                            "blueprint": True,
                            "template": "tools/dashboard/templates/synth/page.html",
                            "constants": "tools/synth_canvas/constants.py",
                            "db_migration": "tools/synth_canvas/db/migrations",
                            "iqe_adapter": True,
                            "nav_link": True,
                            "seed_queries": "context/iqe/queries/synth",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    from tools.config.component_registry import ComponentRegistry

    registry = ComponentRegistry(registry_path=registry_yaml, env={})
    report = registry.validate_canvas_completeness("synth", repo_root=root)

    assert report.passed, [item for item in report.items if not item.present]
    for item in report.items:
        if item.required:
            assert item.present, f"{item.point}: {item.message}"


# ---------------------------------------------------------------------------
# get_iqe_path_canvas() — registry-derived PATH_CANVAS (cvx-nav-01)
# ---------------------------------------------------------------------------

# Canvas keys used by the detector that are registered in app.py rather than in
# component_registry.yaml (app-only IQE adapters / ai-brief-only renderers).
# `canvas_compliance` is the Canvas Posture page: an @app.route in app.py with a
# template but no blueprint and no IQE adapter — unlike `canvas_health`, which is
# a full registered component. Added to PATH_CANVAS by cnr-cc-02 without a
# matching entry here, which is what this guard is for.
_PATH_CANVAS_APP_ONLY_KEYS = {
    "updates",
    "zta",
    "dat",
    "cpmp_deliverables",
    "canvas_compliance",
}

# Previously missing from the hardcoded PATH_CANVAS arrays — the whole point of
# cvx-nav-01. These must all appear in the registry-derived map.
_PATH_CANVAS_TARGETS = {"qdc", "cortex", "rfi_canvas", "wfc", "canvas_health", "cwk"}


def _first_match(entries, path):
    """Mirror the JS detector: first entry whose regex matches wins."""
    for src, canvas in entries:
        if re.search(src, path):
            return canvas
    return "ndc"


def test_get_iqe_path_canvas_returns_ordered_pairs(registry):
    entries = registry.get_iqe_path_canvas()
    assert isinstance(entries, list) and entries
    for item in entries:
        assert isinstance(item, tuple) and len(item) == 2
        src, canvas = item
        assert isinstance(src, str) and src.startswith("^")
        assert isinstance(canvas, str) and canvas


def test_get_iqe_path_canvas_includes_previously_missing_canvases(registry):
    """All seven canvases missing from the legacy hardcoded arrays are present."""
    canvases = {c for _, c in registry.get_iqe_path_canvas()}
    missing = _PATH_CANVAS_TARGETS - canvases
    assert not missing, f"registry-derived PATH_CANVAS still missing: {sorted(missing)}"


def test_get_iqe_path_canvas_regex_sources_compile(registry):
    """Every emitted regex source is a valid pattern (Python == JS here)."""
    for src, _canvas in registry.get_iqe_path_canvas():
        re.compile(src)  # raises on malformed pattern


def test_get_iqe_path_canvas_cwk_ace_collision_rule(registry):
    """The documented collision rule: /coworkers → cwk resolves before
    /coworker → ace (longest path first), and each routes deterministically."""
    entries = registry.get_iqe_path_canvas()
    keys = [c for _, c in entries]
    assert keys.index("cwk") < keys.index("ace"), "cwk must precede ace"
    assert _first_match(entries, "/coworkers") == "cwk"
    assert _first_match(entries, "/coworkers/123") == "cwk"
    assert _first_match(entries, "/coworker") == "ace"
    assert _first_match(entries, "/coworker/launch") == "ace"


def test_get_iqe_path_canvas_specific_before_broad(registry):
    """More specific paths must win over broader ones and regex catch-alls."""
    entries = registry.get_iqe_path_canvas()
    cases = {
        "/ai-ml/foo": "aimc",
        "/ai-observatory": "ai_observatory",
        "/ai-security": "aisg",          # broad ^/ai- catch-all
        "/migration-canvas/projects": "cam",
        "/migration-canvas": "mc",
        "/monitoring/forecast": "forecast",
        "/monitoring": "logs",
        "/quality": "qdc",
        "/workflow-canvas": "wfc",
        "/health/canvases": "canvas_health",
        "/rfi/list": "rfi_canvas",
        "/network": "ndc",
        "/twin": "ndc",
    }
    for path, expected in cases.items():
        assert _first_match(entries, path) == expected, path


def test_get_iqe_path_canvas_keys_are_registered_or_app_known(registry):
    """Guard against typos: every canvas key is either a registered component
    or a known app-registered detector key."""
    valid = {c.key for c in registry} | _PATH_CANVAS_APP_ONLY_KEYS
    unknown = {c for _, c in registry.get_iqe_path_canvas() if c not in valid}
    assert not unknown, f"unknown canvas keys in PATH_CANVAS: {sorted(unknown)}"


def test_get_iqe_path_canvas_auto_appends_new_canvas(tmp_path):
    """A canvas with a url_prefix + IQE adapter is auto-appended even when it is
    not listed explicitly in the iqe_path_canvas block."""
    import yaml as _yaml

    registry_yaml = tmp_path / "component_registry.yaml"
    registry_yaml.write_text(
        _yaml.safe_dump(
            {
                "components": [
                    {
                        "key": "zeta",
                        "kind": "canvas",
                        "display_name": "Zeta Canvas",
                        "env_flag": "ICDEV_ZETA_ENABLED",
                        "module": "tools.zeta.blueprint",
                        "blueprint_attr": "zeta_bp",
                        "url_prefix": "/zeta",
                        "iqe": {
                            "adapter_module": "tools.iqe.adapters.zeta",
                            "collections": ["zeta.things"],
                        },
                    }
                ],
                "iqe_path_canvas": [
                    {"path": "/other", "canvas": "other"},
                ],
            }
        ),
        encoding="utf-8",
    )
    reg = ComponentRegistry(registry_path=registry_yaml, env={})
    entries = reg.get_iqe_path_canvas()
    canvases = {c for _, c in entries}
    assert "zeta" in canvases, "canvas with url_prefix + IQE adapter must auto-append"
    assert _first_match(entries, "/zeta/x") == "zeta"


# ---------------------------------------------------------------------------
# Packaged-wheel registry discovery (regression: 1.2.37/1.2.38)
# ---------------------------------------------------------------------------

def test_find_repo_root_discovers_packaged_data_args_layout(tmp_path, monkeypatch):
    """A pip-installed wheel ships the registry under ``icdev/data/args/``.

    Regression for 1.2.37/1.2.38, where ``_find_repo_root`` probed only
    ``<parent>/args/`` and so resolved zero components after ``pip install``:
    ``icdev status`` crashed and ``icdev list`` came back empty.
    """
    import importlib

    mod = importlib.import_module("tools.config.component_registry")

    # Mimic the wheel layout: <site-packages>/icdev/{data/args,tools/config}
    pkg = tmp_path / "icdev"
    (pkg / "data" / "args").mkdir(parents=True)
    (pkg / "tools" / "config").mkdir(parents=True)
    registry = pkg / "data" / "args" / "component_registry.yaml"
    registry.write_text("components: []\n", encoding="utf-8")
    module_file = pkg / "tools" / "config" / "component_registry.py"
    module_file.write_text("", encoding="utf-8")

    monkeypatch.setattr(mod, "__file__", str(module_file))
    base, found = mod._find_repo_root()

    assert found == registry, "packaged data/args layout must be discovered"
    assert base == pkg


def test_find_repo_root_prefers_source_args_layout(tmp_path, monkeypatch):
    """The source checkout's ``<root>/args/`` still wins when both exist."""
    import importlib

    mod = importlib.import_module("tools.config.component_registry")

    root = tmp_path / "repo"
    (root / "args").mkdir(parents=True)
    (root / "data" / "args").mkdir(parents=True)
    (root / "tools" / "config").mkdir(parents=True)
    src = root / "args" / "component_registry.yaml"
    src.write_text("components: []\n", encoding="utf-8")
    (root / "data" / "args" / "component_registry.yaml").write_text(
        "components: []\n", encoding="utf-8"
    )
    module_file = root / "tools" / "config" / "component_registry.py"
    module_file.write_text("", encoding="utf-8")

    monkeypatch.setattr(mod, "__file__", str(module_file))
    _base, found = mod._find_repo_root()

    assert found == src, "source args/ layout must take precedence"
