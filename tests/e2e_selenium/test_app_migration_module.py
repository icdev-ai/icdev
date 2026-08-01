# CUI // SP-CTI
"""E2E Test Suite: Application Migration Module.

Covers app inventory CRUD, CSV import, app-server binding, dependency graph,
migration order, data migration planner, and post-migration validator.

Prerequisites:
  - Flask dashboard running (ICDEV_DASHBOARD_URL or http://localhost:5050)
  - DB initialised with mc_app_inventory, mc_app_server_bindings, mc_app_dependencies,
    mc_data_migration tables (migration_canvas.db)

Test groups:
  T01  App inventory — create, list, filter, update, delete
  T02  CSV bulk import — POST /api/app-inventory/import, verify row count
  T03  App-server binding — bind, list, unbind
  T04  Dependency graph — create edge, transitive closure, verify nodes
  T05  Migration order — topological sort, dependency ordering
  T06  Data migration planner — create, get, update status
  T07  Post-migration validator — http_smoke_test, db_connectivity_check structures
  T08  Page routes — /migration-canvas/ responds
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

BASE_URL = os.environ.get("ICDEV_DASHBOARD_URL", "http://localhost:5050").rstrip("/")
_BASE_SCHEME = urllib.parse.urlparse(BASE_URL).scheme
if _BASE_SCHEME not in ("http", "https"):
    raise ValueError(f"ICDEV_DASHBOARD_URL must use http or https; got {_BASE_SCHEME!r}")
MC_BASE = f"{BASE_URL}/migration-canvas"

# ── HTTP helpers ─────────────────────────────────────────────────────────────


def _require_http_url(url: str) -> None:
    """Raise ValueError if the URL scheme is not http or https."""
    scheme = urllib.parse.urlparse(url).scheme
    if scheme not in ("http", "https"):
        raise ValueError(f"Only http/https schemes are permitted; got {scheme!r}")


def _get(path: str, params: dict | None = None, timeout: int = 15):
    url = MC_BASE + path
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"
    _require_http_url(url)
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            body = {"_raw": str(e)}
        return e.code, body


def _post(path: str, body: dict | str, content_type: str = "application/json", timeout: int = 30):
    url = MC_BASE + path
    _require_http_url(url)
    if isinstance(body, dict):
        data = json.dumps(body).encode("utf-8")
    else:
        data = body.encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": content_type},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body_resp = json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            body_resp = {"_raw": str(e)}
        return e.code, body_resp


def _put(path: str, body: dict, timeout: int = 15):
    url = MC_BASE + path
    _require_http_url(url)
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body_resp = json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            body_resp = {"_raw": str(e)}
        return e.code, body_resp


def _delete(path: str, timeout: int = 15):
    url = MC_BASE + path
    _require_http_url(url)
    req = urllib.request.Request(url, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            body = {"_raw": str(e)}
        return e.code, body


def _skip_if_auth(status: int) -> None:
    if status == 401:
        pytest.skip("Dashboard requires auth — run with a valid session cookie or disable auth for tests")


# ── T01: App Inventory CRUD ──────────────────────────────────────────────────


class TestT01AppInventoryCRUD:
    """T01 — App inventory: create, list, filter, update, delete."""

    def test_create_app(self):
        status, body = _post("/api/app-inventory", {
            "name": "e2e-test-webapp",
            "version": "1.0.0",
            "language": "Python",
            "framework": "Flask",
            "app_type": "web",
            "criticality": "high",
            "environment": "production",
            "stig_category": "cat2",
        })
        _skip_if_auth(status)
        assert status == 201, f"Create failed {status}: {body}"
        assert "id" in body
        assert body["ok"] is True
        TestT01AppInventoryCRUD._app_id = body["id"]

    def test_list_apps(self):
        if not hasattr(TestT01AppInventoryCRUD, "_app_id"):
            pytest.skip("App not created")
        status, body = _get("/api/app-inventory")
        _skip_if_auth(status)
        assert status == 200, f"List failed {status}: {body}"
        assert "apps" in body
        assert "count" in body
        ids = [a["id"] for a in body["apps"]]
        assert TestT01AppInventoryCRUD._app_id in ids, "Created app not found in list"

    def test_filter_by_criticality(self):
        if not hasattr(TestT01AppInventoryCRUD, "_app_id"):
            pytest.skip("App not created")
        status, body = _get("/api/app-inventory", {"criticality": "high"})
        _skip_if_auth(status)
        assert status == 200, f"Filter failed {status}: {body}"
        for app in body["apps"]:
            assert app["criticality"] == "high", f"Filter leak: {app['criticality']}"

    def test_get_single_app(self):
        if not hasattr(TestT01AppInventoryCRUD, "_app_id"):
            pytest.skip("App not created")
        app_id = TestT01AppInventoryCRUD._app_id
        status, body = _get(f"/api/app-inventory/{app_id}")
        _skip_if_auth(status)
        assert status == 200, f"Get failed {status}: {body}"
        assert body["id"] == app_id
        assert body["name"] == "e2e-test-webapp"

    def test_update_migration_strategy(self):
        if not hasattr(TestT01AppInventoryCRUD, "_app_id"):
            pytest.skip("App not created")
        app_id = TestT01AppInventoryCRUD._app_id
        status, body = _put(f"/api/app-inventory/{app_id}", {"migration_strategy": "replatform"})
        _skip_if_auth(status)
        assert status == 200, f"Update failed {status}: {body}"
        status2, body2 = _get(f"/api/app-inventory/{app_id}")
        assert body2.get("migration_strategy") == "replatform"

    def test_delete_app(self):
        if not hasattr(TestT01AppInventoryCRUD, "_app_id"):
            pytest.skip("App not created")
        app_id = TestT01AppInventoryCRUD._app_id
        status, body = _delete(f"/api/app-inventory/{app_id}")
        _skip_if_auth(status)
        assert status == 200, f"Delete failed {status}: {body}"
        status2, _ = _get(f"/api/app-inventory/{app_id}")
        assert status2 == 404, "App should be 404 after delete"


# ── T02: CSV Bulk Import ─────────────────────────────────────────────────────


class TestT02CSVImport:
    """T02 — CSV bulk import."""

    _csv_payload = (
        "name,version,language,framework,app_type,owner,criticality,environment,stig_category,license_type\n"
        "e2e-import-alpha,2.0,Java,Spring Boot,api,team-a,high,production,cat2,commercial\n"
        "e2e-import-beta,1.5,Go,Gin,api,team-b,medium,staging,na,open_source\n"
        "e2e-import-gamma,3.0,Python,Django,web,team-c,mission_critical,production,cat1,commercial\n"
    )

    def test_csv_import(self):
        status, body = _post(
            "/api/app-inventory/import",
            self._csv_payload,
            content_type="text/csv",
        )
        _skip_if_auth(status)
        assert status in (200, 201), f"Import failed {status}: {body}"
        assert body.get("ok") is True
        assert body.get("inserted", 0) == 3, f"Expected 3 rows, got {body.get('inserted')}"

    def test_list_after_import(self):
        status, body = _get("/api/app-inventory")
        _skip_if_auth(status)
        assert status == 200
        names = [a["name"] for a in body["apps"]]
        assert any("e2e-import" in n for n in names), "Imported apps not visible in list"


# ── T03: App-Server Binding ──────────────────────────────────────────────────


class TestT03AppServerBinding:
    """T03 — App-to-server binding: bind, list, unbind."""

    _app_id: str = ""
    _server_id: str = "fake-server-for-e2e-test"

    @pytest.fixture(autouse=True)
    def setup_app(self):
        status, body = _post("/api/app-inventory", {
            "name": "e2e-binding-test-app",
            "criticality": "medium",
        })
        if status == 401:
            pytest.skip("Auth required")
        assert status == 201
        TestT03AppServerBinding._app_id = body["id"]
        yield
        _delete(f"/api/app-inventory/{TestT03AppServerBinding._app_id}")

    def test_bind_server(self):
        app_id = self._app_id
        status, body = _post(f"/api/app-inventory/{app_id}/servers", {
            "server_id": self._server_id,
            "role": "primary",
            "port": 8080,
            "protocol": "http",
        })
        assert status == 201, f"Bind failed {status}: {body}"
        assert "id" in body

    def test_list_servers_for_app(self):
        app_id = self._app_id
        _post(f"/api/app-inventory/{app_id}/servers", {
            "server_id": self._server_id, "role": "primary"
        })
        status, body = _get(f"/api/app-inventory/{app_id}/servers")
        assert status == 200, f"List bindings failed {status}: {body}"
        assert "bindings" in body
        server_ids = [b["server_id"] for b in body["bindings"]]
        assert self._server_id in server_ids

    def test_unbind_server(self):
        app_id = self._app_id
        _post(f"/api/app-inventory/{app_id}/servers", {
            "server_id": self._server_id, "role": "primary"
        })
        status, body = _delete(f"/api/app-inventory/{app_id}/servers/{self._server_id}")
        assert status == 200, f"Unbind failed {status}: {body}"
        _, list_body = _get(f"/api/app-inventory/{app_id}/servers")
        remaining = [b["server_id"] for b in list_body.get("bindings", [])]
        assert self._server_id not in remaining


# ── T04: Dependency Graph ────────────────────────────────────────────────────


class TestT04DependencyGraph:
    """T04 — Dependency graph: create edge, transitive closure."""

    _app_a_id: str = ""
    _app_b_id: str = ""

    @pytest.fixture(autouse=True)
    def setup_apps(self):
        _, body_a = _post("/api/app-inventory", {"name": "e2e-dep-app-A"})
        _, body_b = _post("/api/app-inventory", {"name": "e2e-dep-app-B"})
        if "id" not in body_a or "id" not in body_b:
            pytest.skip("Could not create test apps (auth?)")
        TestT04DependencyGraph._app_a_id = body_a["id"]
        TestT04DependencyGraph._app_b_id = body_b["id"]
        yield
        _delete(f"/api/app-inventory/{TestT04DependencyGraph._app_a_id}")
        _delete(f"/api/app-inventory/{TestT04DependencyGraph._app_b_id}")

    def test_create_dependency(self):
        app_a, app_b = self._app_a_id, self._app_b_id
        status, body = _post(f"/api/app-inventory/{app_a}/dependencies", {
            "target_app_id": app_b,
            "dep_type": "hard",
            "target_service": "database",
            "protocol": "tcp",
            "port": 5432,
        })
        assert status == 201, f"Dep create failed {status}: {body}"
        assert "id" in body
        TestT04DependencyGraph._dep_id = body["id"]

    def test_list_dependencies(self):
        app_a, app_b = self._app_a_id, self._app_b_id
        _post(f"/api/app-inventory/{app_a}/dependencies", {"target_app_id": app_b, "dep_type": "hard"})
        status, body = _get(f"/api/app-inventory/{app_a}/dependencies")
        assert status == 200, f"List deps failed {status}: {body}"
        assert "dependencies" in body
        targets = [d.get("target_app_id") for d in body["dependencies"]]
        assert app_b in targets

    def test_dependency_graph_transitive(self):
        app_a, app_b = self._app_a_id, self._app_b_id
        _post(f"/api/app-inventory/{app_a}/dependencies", {"target_app_id": app_b, "dep_type": "hard"})
        status, body = _get(f"/api/app-inventory/{app_a}/dependency-graph")
        assert status == 200, f"Graph failed {status}: {body}"
        assert "nodes" in body and "edges" in body
        node_ids = [n["id"] for n in body["nodes"]]
        assert app_b in node_ids, f"Target app B ({app_b}) not in graph nodes: {node_ids}"


# ── T05: Migration Order ─────────────────────────────────────────────────────


class TestT05MigrationOrder:
    """T05 — Topological sort: no-dep apps first, dependents after."""

    _app_a_id: str = ""
    _app_b_id: str = ""

    @pytest.fixture(autouse=True)
    def setup_apps(self):
        _, body_a = _post("/api/app-inventory", {"name": "e2e-order-app-A-dependent"})
        _, body_b = _post("/api/app-inventory", {"name": "e2e-order-app-B-base"})
        if "id" not in body_a or "id" not in body_b:
            pytest.skip("Could not create test apps (auth?)")
        TestT05MigrationOrder._app_a_id = body_a["id"]
        TestT05MigrationOrder._app_b_id = body_b["id"]
        _post(f"/api/app-inventory/{body_a['id']}/dependencies", {
            "target_app_id": body_b["id"],
            "dep_type": "hard",
        })
        yield
        _delete(f"/api/app-inventory/{TestT05MigrationOrder._app_a_id}")
        _delete(f"/api/app-inventory/{TestT05MigrationOrder._app_b_id}")

    def test_migration_order_endpoint(self):
        status, body = _get("/api/app-inventory/migration-order")
        assert status == 200, f"Order endpoint failed {status}: {body}"
        assert "order" in body
        assert "total" in body
        assert body["total"] >= 2

    def test_b_before_a_in_order(self):
        app_a, app_b = self._app_a_id, self._app_b_id
        _, body = _get("/api/app-inventory/migration-order")
        order_ids = [item["id"] for item in body.get("order", [])]
        if app_a not in order_ids or app_b not in order_ids:
            pytest.skip("Test apps not in order result")
        idx_a = order_ids.index(app_a)
        idx_b = order_ids.index(app_b)
        assert idx_b < idx_a, f"Expected B (idx {idx_b}) before A (idx {idx_a}) — B has no deps"


# ── T06: Data Migration Planner ──────────────────────────────────────────────


class TestT06DataMigrationPlanner:
    """T06 — Data migration: create, get, update status."""

    _dm_id: str = ""

    def test_create_data_migration(self):
        status, body = _post("/api/data-migration", {
            "source_type": "postgresql",
            "source_host": "legacy-db.corp.local",
            "source_db": "app_production",
            "target_type": "postgresql",
            "target_host": "rds-govcloud.aws.com",
            "target_db": "app_production",
            "migration_method": "dump_restore",
            "estimated_size_gb": 50.0,
            "estimated_duration_minutes": 120,
            "cutover_type": "offline",
            "status": "planned",
        })
        _skip_if_auth(status)
        assert status == 201, f"Create data migration failed {status}: {body}"
        assert "id" in body
        TestT06DataMigrationPlanner._dm_id = body["id"]

    def test_get_data_migration(self):
        if not self._dm_id:
            pytest.skip("Data migration not created")
        status, body = _get(f"/api/data-migration/{self._dm_id}")
        _skip_if_auth(status)
        assert status == 200, f"Get failed {status}: {body}"
        assert body["id"] == self._dm_id
        assert body["source_type"] == "postgresql"
        assert body["status"] == "planned"

    def test_update_status_to_in_progress(self):
        if not self._dm_id:
            pytest.skip("Data migration not created")
        status, body = _put(f"/api/data-migration/{self._dm_id}/status", {"status": "in_progress"})
        _skip_if_auth(status)
        assert status == 200, f"Status update failed {status}: {body}"
        _, get_body = _get(f"/api/data-migration/{self._dm_id}")
        assert get_body.get("status") == "in_progress"
        assert get_body.get("started_at") is not None, "started_at should be set on in_progress"


# ── T07: Post-Migration Validator Functions ──────────────────────────────────


class TestT07PostMigrationValidator:
    """T07 — Verify post_migration_validator returns structured dicts."""

    def test_http_smoke_test_structure(self):
        """http_smoke_test should return a dict with status and response_time_ms."""
        from tools.migration_canvas.post_migration_validator import http_smoke_test
        result = http_smoke_test("http://httpbin.org/status/200", expected_status=200, timeout=5)
        assert isinstance(result, dict), "Expected dict result"
        assert "status" in result or "ok" in result or "error" in result, \
            f"Expected status/ok/error key in result: {result}"

    def test_db_connectivity_check_structure(self):
        """db_connectivity_check should return a dict with ok or error key."""
        from tools.migration_canvas.post_migration_validator import db_connectivity_check
        result = db_connectivity_check("127.0.0.1", 9999, "postgresql", "test_db", timeout=2)
        assert isinstance(result, dict), "Expected dict result"
        assert "ok" in result or "error" in result or "status" in result, \
            f"Expected ok/error/status key in result: {result}"


# ── T08: Page Routes ─────────────────────────────────────────────────────────


class TestT08PageRoutes:
    """T08 — Page routes respond (200 or auth redirect)."""

    def _route_ok(self, path: str) -> bool:
        url = MC_BASE + path
        _require_http_url(url)
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:  # nosec B310
                return r.status == 200
        except urllib.error.HTTPError as e:
            return e.code in (200, 302, 401)
        except Exception:
            return False

    def test_migration_canvas_root(self):
        assert self._route_ok("/"), "/migration-canvas/ should respond"

    def test_dashboard_reachable(self):
        url = BASE_URL + "/"
        _require_http_url(url)
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:  # nosec B310
                assert r.status in (200, 302)
        except urllib.error.HTTPError as e:
            assert e.code in (200, 302, 401)
        except Exception as exc:
            pytest.skip(f"Dashboard not running: {exc}")
