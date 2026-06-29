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

import contextlib
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
    ("iop", "ICDEV_INFO_OPS_ENABLED", "tools.info_ops.blueprint", "create_info_ops_blueprint"),
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
    "iop": "/info-ops",
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
    """The set of default-enabled canvases matches _CANVAS_DEFAULTS_TRUE."""
    assert registry.get_default_enabled_flags() == _EXPECTED_CANVAS_DEFAULTS_TRUE


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
    """Registry IQE mapping contains expected legacy entries."""
    iqe_map = registry.get_iqe_mapping()
    for key, (expected_module, expected_collections) in _EXPECTED_IQE_MAP_SUBSET.items():
        assert key in iqe_map, f"missing IQE mapping for {key}"
        module, collections = iqe_map[key]
        assert module == expected_module
        assert collections == expected_collections


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


# ---------------------------------------------------------------------------
# Loader behavior tests
# ---------------------------------------------------------------------------


def test_registry_counts(registry):
    """Expected high-level counts."""
    assert len(registry.list_all(kind="canvas")) == 28
    assert len(registry.list_all(kind="child_app")) == 4
    assert len(registry.list_all(kind="feature")) == 8
    assert len(registry.list_all(kind="core_extension")) == 15  # +admin_console
    assert len(registry) == 55  # +admin_console


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
    """A component with no explicit nav.links falls back to a Dashboard link."""
    # The rag feature toggle has nav metadata but no explicit links.
    ctx = registry.get_nav_context()
    rag = None
    for section in ctx["sections"].values():
        for group in section["groups"]:
            for item in group["items"]:
                if item["key"] == "rag":
                    rag = item
    assert rag is not None
    assert rag["links"][0]["label"] == "Dashboard"


# ---------------------------------------------------------------------------
# Tenant component overrides
# ---------------------------------------------------------------------------


def _sqlite_conn_factory(db_path):
    """Return a get_connection-style factory bound to a SQLite file."""
    def _factory():
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return contextlib.closing(conn)
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


def test_validate_canvas_completeness_info_ops_reports_items(registry):
    """info_ops is a real canvas; the validator runs and reports per-point findings."""
    report = registry.validate_canvas_completeness("iop")
    points = {item.point: item for item in report.items}
    assert "page_template" in points
    assert "blueprint_route" in points
    assert "nav_link" in points
    # info_ops uses index.html; the validator finds it via the legacy-name fallback scan.
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
