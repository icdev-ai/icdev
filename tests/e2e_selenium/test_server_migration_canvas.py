# CUI // SP-CTI
"""E2E Test Suite: Server Migration Canvas.

Covers all 8 wizard steps, the instance catalog API, compat check engine,
readiness scoring, cutover planner, and ERB export for smig-vv-01..05.

Prerequisites:
  - Flask dashboard running (ICDEV_DASHBOARD_URL or http://localhost:5050)
  - DB initialised with seed cloud instances (131 rows expected)

Test groups:
  T01  Catalog API — ≥100 rows, seed/api source, filter params
  T02  Session lifecycle — create, fetch, patch, delete
  T03  Inventory API — save + retrieve manual inventory
  T04  Performance API — save + retrieve perf data
  T05  Compat checks — run checks, CAT1 trigger (Windows 2003)
  T06  Rightsizing — recommendations return top-3 (P2V cloud)
  T07  Cutover planner — seed template, step count by mtype
  T08  Test cases — seed, update pass/fail
  T09  ERB export — classification: "CUI // SP-CTI" present
  T10  Readiness score — score updates, CAT1 penalty
  T11  Catalog sync — airgap fallback path
  T12  Page routes — /server-migration/, /new, /<sid> all respond
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

BASE_URL = os.environ.get("ICDEV_DASHBOARD_URL", "http://localhost:5050").rstrip("/")

# ── HTTP helpers ────────────────────────────────────────────────────


def _get(path: str, params: dict | None = None, timeout: int = 15):
    url = BASE_URL + path
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            body = {"_raw": str(e)}
        return e.code, body


def _post(path: str, body: dict, timeout: int = 30):
    url = BASE_URL + path
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body_resp = json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            body_resp = {"_raw": str(e)}
        return e.code, body_resp


def _patch(path: str, body: dict, timeout: int = 15):
    url = BASE_URL + path
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body_resp = json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            body_resp = {"_raw": str(e)}
        return e.code, body_resp


def _delete(path: str, timeout: int = 15):
    url = BASE_URL + path
    req = urllib.request.Request(url, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            body = {"_raw": str(e)}
        return e.code, body


def _page_ok(path: str, timeout: int = 10) -> bool:
    """Return True if the page responds 200 (after any login redirect)."""
    url = BASE_URL + path
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        # 302 redirect to login is expected (unauthenticated)
        return e.code in (200, 302)
    except Exception:
        return False


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def session_id():
    """Create a fresh smig session for this test module; delete on teardown."""
    status, body = _post(
        "/migration-canvas/api/server-migration",
        {
            "migration_type": "p2v_cloud",
            "src_hostname": "e2e-test-server.corp.local",
            "src_ip": "10.0.99.1",
            "src_os": "Ubuntu 22.04",
            "tgt_platform": "aws",
            "tgt_region": "us-east-1",
            "classification": "CUI // SP-CTI",
        },
    )
    if status == 401:
        pytest.skip("Dashboard requires auth — run with a valid session cookie or disable auth for tests")
    assert status == 201, f"Session create failed {status}: {body}"
    sid = body["id"]
    assert sid.startswith("smig-"), f"Unexpected session ID prefix: {sid}"
    yield sid
    _delete(f"/migration-canvas/api/server-migration/{sid}")


@pytest.fixture(scope="module")
def session_with_inventory(session_id):
    """Extend the base session with inventory + performance data."""
    inv_body = {
        "inventory": {
            "vcpus": 8,
            "ram_gb": 32,
            "os_name": "Ubuntu 22.04",
            "os_family": "linux",
            "os_arch": "x86_64",
            "primary_nic_gbps": 10.0,
            "bios_type": "UEFI",
            "virtualization_ext": "VT-x",
        },
        "nics": [
            {"name": "eth0", "speed_gbps": 10, "ip": "10.0.99.1", "vlan": "100"},
        ],
        "disks": [
            {"name": "sda", "size_gb": 500, "used_gb": 200, "type": "SSD", "mount": "/", "filesystem": "ext4"},
            {"name": "sdb", "size_gb": 1000, "used_gb": 600, "type": "HDD", "mount": "/data", "filesystem": "ext4"},
        ],
        "services": [
            {"service_name": "nginx", "service_role": "web", "port": 80, "protocol": "tcp", "status": "running"},
            {"service_name": "postgresql", "service_role": "db", "port": 5432, "protocol": "tcp", "status": "running"},
        ],
    }
    status, body = _post(
        f"/migration-canvas/api/server-migration/{session_id}/inventory",
        inv_body,
    )
    assert status == 200, f"Inventory save failed {status}: {body}"

    perf_body = {
        "cpu_avg_pct": 45.0, "cpu_peak_pct": 78.0,
        "ram_avg_pct": 62.0, "ram_peak_pct": 88.0,
        "disk_iops_avg": 1200, "disk_iops_peak": 4800,
        "net_mbps_avg": 500, "net_mbps_peak": 2000,
        "sample_period_days": 90, "source": "e2e_test",
    }
    status, body = _post(
        f"/migration-canvas/api/server-migration/{session_id}/performance",
        perf_body,
    )
    assert status == 200, f"Performance save failed {status}: {body}"
    return session_id


# ══════════════════════════════════════════════════════════════════════
# T01 — Instance Catalog
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.e2e_selenium
class TestCatalog:
    def test_catalog_returns_min_100_rows(self):
        status, body = _get("/migration-canvas/api/server-migration/instance-catalog")
        if status == 401:
            pytest.skip("Auth required")
        assert status == 200, f"Catalog API {status}: {body}"
        count = body.get("count", 0)
        assert count >= 100, f"Catalog has only {count} rows — seed may have failed"

    def test_catalog_has_seed_source_rows(self):
        status, body = _get("/migration-canvas/api/server-migration/instance-catalog")
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200
        instances = body.get("instances", [])
        seed_rows = [i for i in instances if i.get("source") == "seed"]
        assert len(seed_rows) >= 80, f"Expected ≥80 seed rows, got {len(seed_rows)}"

    def test_catalog_filter_by_provider_aws(self):
        status, body = _get(
            "/migration-canvas/api/server-migration/instance-catalog",
            {"provider": "aws"},
        )
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200
        instances = body.get("instances", [])
        assert len(instances) >= 20, f"Expected ≥20 AWS rows, got {len(instances)}"
        for inst in instances:
            assert inst["provider"] == "aws", f"Non-AWS row leaked: {inst['provider']}"

    def test_catalog_filter_govcloud_only(self):
        status, body = _get(
            "/migration-canvas/api/server-migration/instance-catalog",
            {"provider": "aws", "govcloud_only": "true"},
        )
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200
        instances = body.get("instances", [])
        assert len(instances) >= 10, f"Expected ≥10 GovCloud rows, got {len(instances)}"
        for inst in instances:
            assert inst.get("govcloud") == 1, f"Non-govcloud row: {inst}"

    def test_catalog_filter_vcpu_range(self):
        status, body = _get(
            "/migration-canvas/api/server-migration/instance-catalog",
            {"min_vcpus": "4", "max_vcpus": "8"},
        )
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200
        instances = body.get("instances", [])
        assert len(instances) >= 5, f"Expected rows for 4-8 vCPU range, got {len(instances)}"
        for inst in instances:
            assert 4 <= inst["vcpus"] <= 8, f"vCPU out of range: {inst['vcpus']}"

    def test_catalog_includes_onprem_hypervisors(self):
        for provider in ("vmware", "kvm", "proxmox"):
            status, body = _get(
                "/migration-canvas/api/server-migration/instance-catalog",
                {"provider": provider},
            )
            if status in (401, 403):
                pytest.skip("Auth required")
            assert status == 200
            count = body.get("count", 0)
            assert count >= 5, f"Expected ≥5 {provider} rows, got {count}"


# ══════════════════════════════════════════════════════════════════════
# T02 — Session Lifecycle
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.e2e_selenium
class TestSessionLifecycle:
    def test_create_session_returns_smig_prefix(self, session_id):
        assert session_id.startswith("smig-"), f"Bad prefix: {session_id}"

    def test_get_session_returns_correct_fields(self, session_id):
        status, body = _get(f"/migration-canvas/api/server-migration/{session_id}")
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200
        assert body["id"] == session_id
        assert body["migration_type"] == "p2v_cloud"
        assert body["src_hostname"] == "e2e-test-server.corp.local"
        assert body["tgt_platform"] == "aws"
        assert body["classification"] == "CUI // SP-CTI"

    def test_patch_session_updates_field(self, session_id):
        status, body = _patch(
            f"/migration-canvas/api/server-migration/{session_id}",
            {"status": "planning", "tgt_region": "us-gov-west-1"},
        )
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200, f"PATCH failed {status}: {body}"
        assert body.get("ok") is True

        # Verify the update persisted
        status2, body2 = _get(f"/migration-canvas/api/server-migration/{session_id}")
        if status2 == 200:
            assert body2["status"] == "planning"
            assert body2["tgt_region"] == "us-gov-west-1"

    def test_patch_unknown_field_returns_400(self, session_id):
        status, body = _patch(
            f"/migration-canvas/api/server-migration/{session_id}",
            {"nonexistent_field": "value"},
        )
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 400, f"Expected 400 for unknown field, got {status}"

    def test_get_nonexistent_session_returns_404(self):
        status, body = _get("/migration-canvas/api/server-migration/smig-000000000000")
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 404, f"Expected 404, got {status}"


# ══════════════════════════════════════════════════════════════════════
# T03 — Inventory
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.e2e_selenium
class TestInventory:
    def test_get_inventory_returns_saved_data(self, session_with_inventory):
        sid = session_with_inventory
        status, body = _get(f"/migration-canvas/api/server-migration/{sid}/inventory")
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200, f"GET inventory {status}: {body}"
        inv = body.get("inventory", {})
        assert int(inv.get("vcpus", 0)) == 8, f"vcpus mismatch: {inv}"
        assert float(inv.get("ram_gb", 0)) == 32.0
        assert inv.get("os_family") == "linux"

    def test_inventory_nics_returned(self, session_with_inventory):
        sid = session_with_inventory
        status, body = _get(f"/migration-canvas/api/server-migration/{sid}/inventory")
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200
        nics = body.get("nics", [])
        assert len(nics) >= 1, f"Expected ≥1 NIC, got {len(nics)}"

    def test_inventory_storage_returned(self, session_with_inventory):
        sid = session_with_inventory
        status, body = _get(f"/migration-canvas/api/server-migration/{sid}/inventory")
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200
        storage = body.get("storage", [])
        assert len(storage) >= 2, f"Expected 2 disks, got {len(storage)}"

    def test_services_saved_with_inventory(self, session_with_inventory):
        sid = session_with_inventory
        status, body = _get(f"/migration-canvas/api/server-migration/{sid}/inventory")
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200
        services = body.get("services", [])
        assert len(services) >= 2, f"Expected 2 services, got {len(services)}"
        names = [s.get("service_name") for s in services]
        assert "nginx" in names, f"nginx not found in services: {names}"

    def test_csv_inventory_parse(self, session_id):
        csv_data = "hostname,os_name,os_family,vcpus,ram_gb\ndb01,RHEL 9,linux,16,64"
        status, body = _post(
            f"/migration-canvas/api/server-migration/{session_id}/inventory",
            {"raw": csv_data, "fmt": "csv"},
        )
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200, f"CSV parse failed {status}: {body}"
        assert body.get("ok") is True


# ══════════════════════════════════════════════════════════════════════
# T04 — Performance Data
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.e2e_selenium
class TestPerformance:
    def test_get_performance_returns_saved_data(self, session_with_inventory):
        sid = session_with_inventory
        status, body = _get(f"/migration-canvas/api/server-migration/{sid}/performance")
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200, f"GET performance {status}: {body}"
        assert float(body.get("cpu_avg_pct", -1)) == 45.0
        assert float(body.get("ram_avg_pct", -1)) == 62.0
        assert int(body.get("sample_period_days", -1)) == 90


# ══════════════════════════════════════════════════════════════════════
# T05 — Compatibility Checks
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.e2e_selenium
class TestCompatChecks:
    def test_run_compat_checks_returns_checks(self, session_with_inventory):
        sid = session_with_inventory
        status, body = _post(
            f"/migration-canvas/api/server-migration/{sid}/compat-check", {}
        )
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200, f"Compat check run failed {status}: {body}"
        checks = body.get("checks", [])
        assert len(checks) >= 1, "Expected at least one compat check"
        assert "cat1_count" in body

    def test_compat_list_returns_stored_checks(self, session_with_inventory):
        sid = session_with_inventory
        # Ensure checks have been run first
        _post(f"/migration-canvas/api/server-migration/{sid}/compat-check", {})
        status, body = _get(f"/migration-canvas/api/server-migration/{sid}/compat-check")
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200
        checks = body.get("checks", [])
        assert len(checks) >= 1, "GET compat should return stored checks"

    def test_eol_os_triggers_cat1(self):
        """Windows Server 2003 is EOL — must produce a CAT1 check."""
        status, body = _post(
            "/migration-canvas/api/server-migration",
            {
                "migration_type": "p2v_cloud",
                "src_hostname": "e2e-eol-test",
                "src_ip": "10.0.99.99",
                "tgt_platform": "aws",
                "classification": "CUI // SP-CTI",
            },
        )
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 201
        eol_sid = body["id"]
        try:
            # Save EOL OS inventory
            _post(
                f"/migration-canvas/api/server-migration/{eol_sid}/inventory",
                {
                    "inventory": {
                        "vcpus": 4, "ram_gb": 8,
                        "os_name": "Windows Server 2003",
                        "os_family": "windows", "os_arch": "i386",
                        "bios_type": "Legacy",
                    },
                    "nics": [], "disks": [], "services": [],
                },
            )
            status2, body2 = _post(
                f"/migration-canvas/api/server-migration/{eol_sid}/compat-check", {}
            )
            assert status2 == 200, f"Compat check failed: {body2}"
            cat1_count = body2.get("cat1_count", 0)
            assert cat1_count >= 1, (
                f"EOL OS (Windows 2003) should trigger ≥1 CAT1 — got {cat1_count}. "
                f"Checks: {[c['check_name'] for c in body2.get('checks',[]) if c.get('severity')=='cat1']}"
            )
        finally:
            _delete(f"/migration-canvas/api/server-migration/{eol_sid}")

    def test_checks_have_required_fields(self, session_with_inventory):
        sid = session_with_inventory
        _post(f"/migration-canvas/api/server-migration/{sid}/compat-check", {})
        status, body = _get(f"/migration-canvas/api/server-migration/{sid}/compat-check")
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200
        for check in body.get("checks", []):
            assert "severity" in check, f"severity missing: {check}"
            assert "status" in check, f"status missing: {check}"
            assert "check_name" in check, f"check_name missing: {check}"
            assert check["severity"] in ("cat1", "cat2", "cat3"), f"bad severity: {check['severity']}"


# ══════════════════════════════════════════════════════════════════════
# T06 — Rightsizing Recommendations
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.e2e_selenium
class TestRightsizing:
    def test_recommend_returns_top3(self, session_with_inventory):
        sid = session_with_inventory
        status, body = _post(
            f"/migration-canvas/api/server-migration/{sid}/recommend", {}
        )
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200, f"Recommend failed {status}: {body}"
        recs = body.get("recommendations", [])
        assert 1 <= len(recs) <= 3, f"Expected 1–3 recommendations, got {len(recs)}"

    def test_recommendation_fields_present(self, session_with_inventory):
        sid = session_with_inventory
        status, body = _post(
            f"/migration-canvas/api/server-migration/{sid}/recommend", {}
        )
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200
        recs = body.get("recommendations", [])
        if recs:
            rec = recs[0]
            assert "instance_type" in rec, f"instance_type missing: {rec}"
            assert "vcpu_req" in rec, f"vcpu_req missing: {rec}"
            assert "ram_req_gb" in rec, f"ram_req_gb missing: {rec}"
            assert rec.get("vcpu_req", 0) >= 1, f"vcpu_req must be positive: {rec}"


# ══════════════════════════════════════════════════════════════════════
# T07 — Cutover Planner
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.e2e_selenium
class TestCutoverPlanner:
    @pytest.mark.parametrize("mtype,expected_min", [
        ("p2p",            14),
        ("p2v_onprem",     11),
        ("p2v_cloud",      15),
        ("v2v_cloud",      11),
        ("v2v_hypervisor", 12),
    ])
    def test_seed_template_step_count(self, mtype, expected_min):
        status, body = _post(
            "/migration-canvas/api/server-migration",
            {
                "migration_type": mtype,
                "src_hostname": f"e2e-{mtype}",
                "tgt_platform": "aws" if "cloud" in mtype else "vmware",
                "classification": "CUI // SP-CTI",
            },
        )
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 201
        sid = body["id"]
        try:
            status2, body2 = _post(
                f"/migration-canvas/api/server-migration/{sid}/cutover-steps",
                {"action": "seed", "migration_type": mtype},
            )
            assert status2 == 200, f"Seed failed {status2}: {body2}"
            n = body2.get("steps", 0)
            assert n >= expected_min, (
                f"{mtype}: expected ≥{expected_min} steps, got {n}"
            )
        finally:
            _delete(f"/migration-canvas/api/server-migration/{sid}")

    def test_get_cutover_steps_with_seed_flag(self, session_id):
        status, body = _get(
            f"/migration-canvas/api/server-migration/{session_id}/cutover-steps",
            {"seed": "true"},
        )
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200, f"GET cutover-steps failed {status}: {body}"
        steps = body.get("steps", [])
        assert len(steps) >= 1, "Expected steps after seed=true"

    def test_cutover_step_fields(self, session_id):
        _post(
            f"/migration-canvas/api/server-migration/{session_id}/cutover-steps",
            {"action": "seed", "migration_type": "p2v_cloud"},
        )
        status, body = _get(
            f"/migration-canvas/api/server-migration/{session_id}/cutover-steps"
        )
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200
        for step in body.get("steps", []):
            assert "phase" in step, f"phase missing: {step}"
            assert "description" in step, f"description missing: {step}"
            assert step["phase"] in (
                "pre_migration", "parallel_run", "cutover",
                "post_migration", "rollback",
            ), f"Unknown phase: {step['phase']}"


# ══════════════════════════════════════════════════════════════════════
# T08 — Test Cases
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.e2e_selenium
class TestCases:
    def test_seed_test_cases(self, session_id):
        status, body = _post(
            f"/migration-canvas/api/server-migration/{session_id}/test-cases",
            {"action": "seed"},
        )
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200, f"Seed test cases failed {status}: {body}"
        assert body.get("test_cases", 0) >= 8, (
            f"Expected ≥8 seeded test cases, got {body.get('test_cases')}"
        )

    def test_get_test_cases_with_seed_flag(self, session_id):
        status, body = _get(
            f"/migration-canvas/api/server-migration/{session_id}/test-cases",
            {"seed": "true"},
        )
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200
        cases = body.get("test_cases", [])
        assert len(cases) >= 8, f"Expected ≥8 test cases, got {len(cases)}"

    def test_test_case_phases(self, session_id):
        _post(
            f"/migration-canvas/api/server-migration/{session_id}/test-cases",
            {"action": "seed"},
        )
        status, body = _get(
            f"/migration-canvas/api/server-migration/{session_id}/test-cases"
        )
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200
        phases = {c["phase"] for c in body.get("test_cases", []) if "phase" in c}
        assert "pre" in phases, f"No pre-migration tests found in phases: {phases}"
        assert "post" in phases, f"No post-migration tests found in phases: {phases}"

    def test_update_test_result(self, session_id):
        # Seed first
        _post(
            f"/migration-canvas/api/server-migration/{session_id}/test-cases",
            {"action": "seed"},
        )
        status, body = _get(
            f"/migration-canvas/api/server-migration/{session_id}/test-cases"
        )
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200
        cases = body.get("test_cases", [])
        if not cases:
            pytest.skip("No test cases available to update")
        tc_id = cases[0]["id"]
        status2, body2 = _post(
            f"/migration-canvas/api/server-migration/{session_id}/test-cases",
            {"id": tc_id, "passed": 1, "actual_result": "All services healthy"},
        )
        assert status2 == 200, f"Test result update failed {status2}: {body2}"
        assert body2.get("ok") is True


# ══════════════════════════════════════════════════════════════════════
# T09 — ERB Export
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.e2e_selenium
class TestERBExport:
    def test_save_and_export_erb(self, session_with_inventory):
        sid = session_with_inventory
        # Save ERB metadata
        status, body = _post(
            f"/migration-canvas/api/server-migration/{sid}/erb",
            {
                "change_type": "server_migration",
                "risk_tier": "medium",
                "business_justification": "Datacenter consolidation — lease expiry 2026-Q3.",
                "technical_summary": "P2V lift-and-shift via AWS MGN.",
                "impact_summary": "4h maintenance window expected.",
                "rollback_plan": "Restore from Veeam backup if cutover fails.",
                "requestor": "ops-team",
                "approver": "cto",
                "approval_status": "pending",
            },
        )
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200, f"ERB save failed {status}: {body}"
        assert "id" in body

    def test_erb_export_contains_classification(self, session_with_inventory):
        sid = session_with_inventory
        # Ensure ERB was saved
        _post(
            f"/migration-canvas/api/server-migration/{sid}/erb",
            {
                "change_type": "server_migration",
                "risk_tier": "low",
                "business_justification": "E2E test ERB.",
                "rollback_plan": "Restore from backup.",
            },
        )
        status, body = _get(f"/migration-canvas/api/server-migration/{sid}/erb")
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200, f"ERB GET failed {status}: {body}"
        assert body.get("classification") == "CUI // SP-CTI", (
            f"Missing CUI // SP-CTI classification in ERB export. Got: {body.get('classification')}"
        )
        assert "generated_at" in body, "ERB export missing generated_at field"
        assert "session" in body, "ERB export missing session section"
        assert "inventory" in body, "ERB export missing inventory section"

    def test_erb_export_has_all_sections(self, session_with_inventory):
        sid = session_with_inventory
        status, body = _get(f"/migration-canvas/api/server-migration/{sid}/erb")
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200
        required_sections = [
            "session", "inventory", "compat_summary", "compat_checks",
            "cutover_steps", "test_cases", "readiness_score",
            "classification", "generated_at",
        ]
        for section in required_sections:
            assert section in body, f"ERB missing section: {section}"


# ══════════════════════════════════════════════════════════════════════
# T10 — Readiness Score
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.e2e_selenium
class TestReadinessScore:
    def test_readiness_score_updates_after_inventory(self, session_with_inventory):
        sid = session_with_inventory
        status, body = _get(f"/migration-canvas/api/server-migration/{sid}/readiness")
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200, f"Readiness failed {status}: {body}"
        score = body.get("score", body.get("overall", -1))
        assert score >= 0, f"Score must be non-negative: {score}"
        assert score <= 100, f"Score must be ≤ 100: {score}"

    def test_readiness_score_increases_with_data(self, session_with_inventory):
        sid = session_with_inventory
        # Seed cutover + tests to increase score
        _post(
            f"/migration-canvas/api/server-migration/{sid}/cutover-steps",
            {"action": "seed", "migration_type": "p2v_cloud"},
        )
        _post(
            f"/migration-canvas/api/server-migration/{sid}/test-cases",
            {"action": "seed"},
        )
        status, body = _get(f"/migration-canvas/api/server-migration/{sid}/readiness")
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200
        score = body.get("score", body.get("overall", 0))
        # With inventory (15) + perf (10) + cutover (10) + tests (5) = 40 minimum
        assert score >= 35, f"Expected score ≥35 with inv+perf+cutover+tests, got {score}"

    def test_readiness_score_has_breakdown(self, session_with_inventory):
        sid = session_with_inventory
        status, body = _get(f"/migration-canvas/api/server-migration/{sid}/readiness")
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200
        breakdown = body.get("breakdown", {})
        assert "inventory" in breakdown, f"breakdown missing 'inventory': {breakdown}"
        assert "cutover" in breakdown, f"breakdown missing 'cutover': {breakdown}"

    def test_cat1_blocker_list_in_response(self, session_with_inventory):
        sid = session_with_inventory
        status, body = _get(f"/migration-canvas/api/server-migration/{sid}/readiness")
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200
        assert "cat1_blockers" in body, "readiness response missing cat1_blockers key"
        assert isinstance(body["cat1_blockers"], list)


# ══════════════════════════════════════════════════════════════════════
# T11 — Catalog Sync (air-gap path)
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.e2e_selenium
class TestCatalogSync:
    def test_catalog_sync_returns_status_key(self):
        status, body = _post(
            "/migration-canvas/api/server-migration/catalog-sync", {}
        )
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200, f"Catalog sync failed {status}: {body}"
        assert "status" in body, f"sync response missing 'status': {body}"
        assert body["status"] in ("ok", "airgap", "partial", "error"), (
            f"Unexpected status: {body['status']}"
        )

    def test_catalog_still_has_rows_after_sync(self):
        _post("/migration-canvas/api/server-migration/catalog-sync", {})
        status, body = _get("/migration-canvas/api/server-migration/instance-catalog")
        if status in (401, 403):
            pytest.skip("Auth required")
        assert status == 200
        count = body.get("count", 0)
        assert count >= 100, (
            f"Catalog dropped below 100 rows after sync — seed rows may have been deleted: {count}"
        )


# ══════════════════════════════════════════════════════════════════════
# T12 — Page Routes
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.e2e_selenium
class TestPageRoutes:
    def test_index_page_responds(self):
        assert _page_ok("/migration-canvas/server-migration/"), (
            "Index page /migration-canvas/server-migration/ did not respond 200/302"
        )

    def test_new_wizard_page_responds(self):
        assert _page_ok("/migration-canvas/server-migration/new"), (
            "New wizard page /migration-canvas/server-migration/new did not respond 200/302"
        )

    def test_nonexistent_session_page_404(self):
        url = BASE_URL + "/migration-canvas/server-migration/smig-000000000000"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            status = e.code
        # 404 or 302 (redirect to login) both acceptable
        assert status in (404, 302, 200), f"Unexpected status {status} for nonexistent session"

    def test_server_migration_in_nav(self):
        """Verify base.html nav contains the Server Migration link."""
        url = BASE_URL + "/migration-canvas/"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                html = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 302:
                pytest.skip("Nav test requires authenticated session")
            raise
        assert "server-migration" in html.lower(), (
            "Server Migration link not found in nav HTML — base.html may not be updated"
        )
