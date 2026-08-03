# CUI // SP-CTI
"""Tests for IDP tenant scoping (idp-mt-01).

The acceptance contract, one test class per clause:

  1. scorecard rows are tenant-scoped
  2. a tenant sees scores only for the components enabled FOR THEM
  3. the isolation posture is recorded, not assumed

Two habits from ``test_idp_score_history.py`` are kept deliberately. The real
shipped migrations are applied rather than a hand-written ``CREATE TABLE``,
because the bug this feature is most exposed to is an INSERT naming a column
the live schema lacks — it raises, gets swallowed, and the feature reports
success while persisting nothing. And connections come from
``tools.db.storage.get_connection`` rather than raw ``sqlite3``, because the
module writes ``%s`` placeholders for PostgreSQL and only the storage wrapper
translates them; a raw connection would make these tests assert their own
no-op.

The negative cases carry the weight here. A scoping test that only checks the
happy path passes just as happily against a function that returns everything.
"""
from __future__ import annotations

import importlib.util
import textwrap
import uuid
from pathlib import Path

import pytest

from tools.idp.score_history import get_score_trend, persist_evaluation
from tools.idp.scorecard import evaluate, parse_scorecard
from tools.iqe.executor import register_collection

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO_ROOT / "tools" / "db" / "migrations"
HISTORY_MIGRATION = MIGRATIONS / "20260802222900_idp_score_history"
TENANT_MIGRATION = MIGRATIONS / "20260803031229_idp_scorecard_history_tenant_index"

# Three components. `acme` will have `charlie` explicitly disabled and
# `alpha`/`bravo` enabled, so a correct scope is a strict subset — the only
# shape that can tell scoping apart from "returned everything".
ALL_FACTS = [
    {"key": "alpha", "has_owner": True},
    {"key": "bravo", "has_owner": True},
    {"key": "charlie", "has_owner": True},
]

CARD_YAML = """
key: test-tenancy
name: Test Tenancy
collection: test.tenancy
adapter_module: tools.iqe
evaluation:
  window: 24h
ladder:
  levels:
  - name: Bronze
    rank: 1
rules:
- identifier: has-owner
  level: Bronze
  weight: 10
  expression: foreach c in test.tenancy where c.has_owner == true select c.key
"""


def _load_migration(directory: Path, name: str):
    """Import a shipped migration by path (its dir name is not an identifier)."""
    spec = importlib.util.spec_from_file_location(
        f"_mig_{directory.name}_{name}", directory / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _card():
    import yaml

    return parse_scorecard(
        yaml.safe_load(textwrap.dedent(CARD_YAML)), source_path="<test>"
    )


class _FakeComponent:
    def __init__(self, key: str, default_enabled: bool = True):
        self.key = key
        self.default_enabled = default_enabled


class _FakeRegistry:
    """Stands in for ComponentRegistry.

    Only the two methods :mod:`tools.idp.tenancy` actually calls. Using a fake
    rather than the real registry keeps the test about scope resolution instead
    of about whichever 66 components happen to be registered today.
    """

    def __init__(self, keys, enabled=None):
        self._components = [_FakeComponent(k) for k in keys]
        self._enabled = set(keys if enabled is None else enabled)

    def list_all(self):
        return list(self._components)

    def is_enabled(self, key):
        return key in self._enabled


@pytest.fixture
def facts():
    """Fact source behind the IQE collection ``test.tenancy``."""
    register_collection("test.tenancy", lambda conn=None: [dict(r) for r in ALL_FACTS])
    return ALL_FACTS


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """Storage connection over temp SQLite with BOTH shipped migrations applied."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    from tools.db.storage import get_connection

    connection = get_connection(db_path=str(tmp_path / "tenancy.db"))
    _load_migration(HISTORY_MIGRATION, "up").up(connection)
    _load_migration(TENANT_MIGRATION, "up").up(connection)
    connection.execute(
        """CREATE TABLE IF NOT EXISTS tenant_component_overrides (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            component_key TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            updated_by TEXT DEFAULT 'system',
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (tenant_id, component_key)
        )"""
    )
    connection.commit()
    yield connection
    try:
        connection.close()
    except Exception:  # noqa: BLE001
        pass


def _override(conn, tenant_id: str, component_key: str, enabled: bool) -> None:
    conn.execute(
        "INSERT INTO tenant_component_overrides "
        "(id, tenant_id, component_key, enabled, updated_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (uuid.uuid4().hex, tenant_id, component_key, 1 if enabled else 0, "2026-08-03"),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Acceptance 2 — a tenant sees only the components enabled for them
# ---------------------------------------------------------------------------


class TestScopeResolution:
    def test_no_tenant_is_the_whole_estate(self, conn):
        """``None`` is the platform's own view, not an empty scope."""
        from tools.idp.tenancy import enabled_component_keys

        assert enabled_component_keys(None, conn) is None

    def test_disabled_component_is_outside_the_tenants_scope(self, conn):
        from tools.idp.tenancy import enabled_component_keys

        registry = _FakeRegistry(["alpha", "bravo", "charlie"])
        _override(conn, "acme", "charlie", False)

        scope = enabled_component_keys("acme", conn, registry=registry)
        assert scope == frozenset({"alpha", "bravo"})
        assert "charlie" not in scope, "an explicitly disabled component must not be scored"

    def test_override_can_enable_something_the_env_disables(self, conn):
        """An explicit override wins over the environment default, both ways."""
        from tools.idp.tenancy import enabled_component_keys

        registry = _FakeRegistry(["alpha", "bravo"], enabled=["alpha"])
        _override(conn, "acme", "bravo", True)

        assert enabled_component_keys("acme", conn, registry=registry) == frozenset(
            {"alpha", "bravo"}
        )

    def test_registry_failure_yields_the_empty_scope_not_the_estate(self, conn):
        """Fail closed. The whole point of the module.

        A tenant is known; the registry cannot be read. Returning ``None``
        here would hand that tenant every component in the platform because a
        YAML failed to load.
        """
        from tools.idp.tenancy import enabled_component_keys

        class Broken:
            def list_all(self):
                raise RuntimeError("registry unavailable")

        assert enabled_component_keys("acme", conn, registry=Broken()) == frozenset()

    def test_missing_overrides_table_falls_back_to_env_defaults(self, tmp_path, monkeypatch):
        """A degraded lookup must not widen the scope beyond the env default."""
        monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
        from tools.db.storage import get_connection
        from tools.idp.tenancy import enabled_component_keys

        bare = get_connection(db_path=str(tmp_path / "bare.db"))
        try:
            registry = _FakeRegistry(["alpha", "bravo"], enabled=["alpha"])
            assert enabled_component_keys("acme", bare, registry=registry) == frozenset(
                {"alpha"}
            )
        finally:
            bare.close()


class TestAmbientScope:
    def test_explicit_binding_beats_the_environment(self, monkeypatch):
        from tools.idp.tenancy import active_tenant_id, tenant_scope

        monkeypatch.setenv("ICDEV_TENANT_ID", "from-env")
        assert active_tenant_id() == "from-env"

        with tenant_scope("bound"):
            assert active_tenant_id() == "bound"
        assert active_tenant_id() == "from-env", "the scope must not leak past its block"

    def test_binding_none_pins_the_platform_view(self, monkeypatch):
        """``None`` bound explicitly is a scope, not an absence of one.

        This is what lets the internal reflex keep the platform's series even
        when it runs inside a tenant's request context.
        """
        from tools.idp.tenancy import active_tenant_id, tenant_scope

        monkeypatch.setenv("ICDEV_TENANT_ID", "from-env")
        with tenant_scope(None):
            assert active_tenant_id() is None


class TestAdapterScoping:
    """Scoping must live in the adapter — every IDP read path goes through it."""

    def test_adapter_reduces_rows_to_the_tenants_components(self, conn, monkeypatch):
        import tools.iqe.adapters.idp as adapter

        monkeypatch.setattr(adapter, "_CACHE", [dict(r) for r in ALL_FACTS], raising=False)
        _override(conn, "acme", "charlie", False)

        import tools.idp.tenancy as tenancy

        monkeypatch.setattr(
            tenancy,
            "enabled_component_keys",
            lambda tid, c=None, registry=None: frozenset({"alpha", "bravo"}),
        )

        rows = adapter.components_adapter(conn, tenant_id="acme")
        assert {r["key"] for r in rows} == {"alpha", "bravo"}

        unscoped = adapter.components_adapter(conn, tenant_id=None)
        assert {r["key"] for r in unscoped} == {"alpha", "bravo", "charlie"}

    def test_scope_failure_yields_no_rows_for_a_known_tenant(self, conn, monkeypatch):
        """Fail closed at the adapter too, not just in the resolver."""
        import tools.idp.tenancy as tenancy
        import tools.iqe.adapters.idp as adapter

        monkeypatch.setattr(adapter, "_CACHE", [dict(r) for r in ALL_FACTS], raising=False)

        def boom(*a, **k):
            raise RuntimeError("scope lookup failed")

        monkeypatch.setattr(tenancy, "scope_component_rows", boom)
        assert adapter.components_adapter(conn, tenant_id="acme") == []


# ---------------------------------------------------------------------------
# Acceptance 1 — scorecard rows are tenant-scoped
# ---------------------------------------------------------------------------


class TestPersistedRowsAreScoped:
    def test_inserted_columns_all_exist_in_the_live_schema(self, conn):
        """The swallowed-INSERT bug class, pinned for the tenant column."""
        from tools.idp.score_history import INSERT_COLUMNS

        live = {
            str(dict(row)["name"])
            for row in conn.execute(
                "PRAGMA table_info(idp_scorecard_history)"
            ).fetchall()
        }
        assert "tenant_id" in live, "the migration must guarantee tenant_id"
        assert not set(INSERT_COLUMNS) - live

    def test_evaluation_carries_its_tenant_into_the_persisted_row(self, conn, facts):
        """A stored score must never be ambiguous about whose estate it graded."""
        report = evaluate(_card(), conn=conn, tenant_id="acme")
        assert report["tenant_id"] == "acme"

        result = persist_evaluation(report, conn, window="24h")
        assert result["tenant_id"] == "acme"

        rows = conn.execute(
            "SELECT DISTINCT tenant_id FROM idp_scorecard_history"
        ).fetchall()
        assert {dict(r)["tenant_id"] for r in rows} == {"acme"}

    def test_platform_evaluation_stores_null_not_a_string(self, conn, facts):
        persist_evaluation(evaluate(_card(), conn=conn), conn, window="24h")
        rows = conn.execute(
            "SELECT tenant_id FROM idp_scorecard_history WHERE tenant_id IS NULL"
        ).fetchall()
        assert len(rows) == len(ALL_FACTS)


class TestReadsFilterOnTenant:
    """A nullable column nobody filters on is decoration."""

    @pytest.fixture
    def mixed(self, conn, facts):
        persist_evaluation(evaluate(_card(), conn=conn, tenant_id="acme"), conn, window="24h")
        persist_evaluation(evaluate(_card(), conn=conn, tenant_id="globex"), conn, window="24h")
        persist_evaluation(evaluate(_card(), conn=conn), conn, window="24h")
        return conn

    def test_a_tenant_trend_excludes_other_tenants(self, mixed):
        trend = get_score_trend("alpha", "test-tenancy", mixed, tenant_id="acme")
        assert trend["data_points"] == 1
        assert trend["tenant_id"] == "acme"

    def test_the_default_read_is_the_platform_series_not_everything(self, mixed):
        """The regression that matters most.

        Omitting the predicate for a ``None`` tenant would make the DEFAULT
        read cross-tenant, quietly absorbing every customer's scores into
        ICDEV's own trend line. Three rows exist for `alpha`; the platform
        series is exactly one of them.
        """
        trend = get_score_trend("alpha", "test-tenancy", mixed)
        assert trend["data_points"] == 1, "default read must not blend tenants"

        every = get_score_trend("alpha", "test-tenancy", mixed, all_tenants=True)
        assert every["data_points"] == 3, "cross-tenant reporting is available on request"
        assert every["all_tenants"] is True

    def test_the_due_check_is_per_tenant(self, conn, facts):
        """Unscoped, the first tenant to record a window blocks everyone else."""
        from tools.idp.score_history import is_due

        card = _card()
        persist_evaluation(
            evaluate(card, conn=conn, tenant_id="acme"), conn, window=card.window
        )

        acme_due, _, _ = is_due(card, conn, tenant_id="acme")
        globex_due, _, _ = is_due(card, conn, tenant_id="globex")
        platform_due, _, _ = is_due(card, conn)

        assert acme_due is False, "acme already recorded this window"
        assert globex_due is True, "another tenant's write must not suppress this one"
        assert platform_due is True, "nor may it suppress the platform series"


# ---------------------------------------------------------------------------
# Acceptance 3 — the posture is recorded, not assumed
# ---------------------------------------------------------------------------


class TestIsolationPosture:
    def test_the_decision_record_exists(self):
        doc = REPO_ROOT / "docs" / "security" / "idp-tenant-isolation-posture.md"
        assert doc.exists(), "the posture decision must live in a doc"
        text = doc.read_text(encoding="utf-8")
        assert "app_layer_only" in text
        assert "Decision" in text

    def test_posture_is_read_from_config_and_points_at_the_record(self):
        from tools.idp.tenancy import posture

        stance = posture()
        assert stance["isolation_posture"] == "app_layer_only"
        assert stance["decision_record"].endswith("idp-tenant-isolation-posture.md")
        assert (REPO_ROOT / stance["decision_record"]).exists(), (
            "the config must point at a document that is actually there"
        )

    def test_external_offering_is_not_approved_by_default(self):
        """The card's whole point: this must not be inherited silently."""
        from tools.idp.tenancy import posture

        assert posture()["external_offering_approved"] is False

    def test_approval_without_a_named_approver_does_not_count(self, tmp_path, monkeypatch):
        """A signature-shaped hole is not a signature."""
        import tools.idp.tenancy as tenancy

        config = tmp_path / "idp_tenancy.yaml"
        config.write_text(
            "tenancy:\n"
            "  isolation_posture: app_layer_only\n"
            "  external_offering_approved: true\n"
            "  approved_by: ''\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(tenancy, "POSTURE_CONFIG", config)
        assert tenancy.posture()["external_offering_approved"] is False

        config.write_text(
            "tenancy:\n"
            "  isolation_posture: app_layer_only\n"
            "  external_offering_approved: true\n"
            "  approved_by: A Named Human\n",
            encoding="utf-8",
        )
        assert tenancy.posture()["external_offering_approved"] is True

    def test_unreadable_config_reports_unapproved(self, tmp_path, monkeypatch):
        """A decision nobody can read is not a decision that has been made."""
        import tools.idp.tenancy as tenancy

        monkeypatch.setattr(tenancy, "POSTURE_CONFIG", tmp_path / "absent.yaml")
        stance = tenancy.posture()
        assert stance["external_offering_approved"] is False
        assert "config_error" in stance

    def test_blueprint_reports_scope_on_every_read_payload(self):
        """A response silent about its scope invites the wrong reading.

        Source-level, matching how the rest of the IDP suite asserts route
        wiring — importing the blueprint drags in the whole portal and a live
        DB, which would make this a portal test rather than a scoping one.
        """
        src = (REPO_ROOT / "tools/idp/blueprint.py").read_text(encoding="utf-8")
        assert "def _tenancy()" in src
        # The catalog, the scorecard report and — the one that matters —
        # arbitrary IQE over the same collection.
        assert src.count('"tenancy": _tenancy()') >= 3
        assert "tenancy=_tenancy()" in src, "the page needs it too, not just the APIs"

    def test_blueprint_tenancy_helper_never_raises(self, monkeypatch):
        """A portal that cannot describe its scoping still serves scoped rows."""
        import tools.idp.blueprint as blueprint
        import tools.idp.tenancy as tenancy

        def boom(*a, **k):
            raise RuntimeError("tenant context unavailable")

        monkeypatch.setattr(tenancy, "tenant_context", boom)
        assert blueprint._tenancy() == {}, "must degrade to silent, not to a claim"

    def test_page_template_only_warns_when_scoped(self):
        """The mirror is checked by test_idp_portal; this checks the guard."""
        page = (
            REPO_ROOT / "tools/dashboard/templates/idp/page.html"
        ).read_text(encoding="utf-8")
        assert "{% if tenancy and tenancy.scoped %}" in page
        assert "tenancy.decision_record" in page

    def test_tenant_context_notices_only_when_scoped(self, conn, monkeypatch):
        """An internal render is not an external offering."""
        import tools.idp.tenancy as tenancy

        monkeypatch.setattr(
            tenancy,
            "enabled_component_keys",
            lambda tid, c=None: None if not tid else frozenset({"alpha"}),
        )

        platform = tenancy.tenant_context(None, conn)
        assert platform["scoped"] is False
        assert platform["notice"] == "", "the platform view carries no external warning"

        scoped = tenancy.tenant_context("acme", conn)
        assert scoped["scoped"] is True
        assert scoped["scope_size"] == 1
        assert "not yet approved" in scoped["notice"]
