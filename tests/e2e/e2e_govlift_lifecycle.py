#!/usr/bin/env python3
# CUI // SP-CTI
"""GovLift DoD IL4 Cloud Migration Tool — Full Lifecycle E2E Test.

Tests the complete build/migration lifecycle via Flask test client:
  1. All 6 page routes load (200 OK, correct title)
  2. Workload creation and inventory
  3. Wave creation and workload assignment
  4. Migration job: create → start → complete (success + failure paths)
  5. Migration rollback
  6. STIG quick scan and status update
  7. Audit log entries created throughout
  8. IQE natural-language query endpoint
  9. Overview API reflects all changes
  10. All API routes return expected JSON shape

Usage:
    python tests/e2e/e2e_govlift_lifecycle.py
    python tests/e2e/e2e_govlift_lifecycle.py --json
"""
import json
import sys
import argparse

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PASS = 0
FAIL = 0
ERRORS: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        ERRORS.append(f"{name}: {detail}")
        print(f"  FAIL  {name} -- {detail}")


def run_tests(as_json: bool = False) -> int:
    from tools.dashboard.app import create_app
    app = create_app()
    c = app.test_client()

    print("\n=== GovLift DoD IL4 — Full Lifecycle E2E Test ===\n")

    # ── 1. Page routes ────────────────────────────────────────────────────
    print("[1] Page Routes (6 pages)")
    page_checks = [
        ("/govlift", "GovLift"),
        ("/govlift/workloads", "Workload"),
        ("/govlift/waves", "Wave"),
        ("/govlift/executor", "Migration"),
        ("/govlift/stig", "STIG"),
        ("/govlift/audit", "Audit"),
    ]
    for path, keyword in page_checks:
        r = c.get(path)
        check(f"GET {path} → 200", r.status_code == 200, f"got {r.status_code}")
        html = r.data.decode("utf-8", errors="replace")
        check(f"GET {path} → contains '{keyword}'", keyword in html, "keyword missing")
        check(f"GET {path} → CUI banner", "CUI" in html, "no classification banner")

    # ── 2. Create workload ────────────────────────────────────────────────
    print("\n[2] Workload Creation")
    wl_payload = {
        "name": "ICDEV-Auth-Service",
        "workload_type": "web_app",
        "os_name": "RHEL",
        "os_version": "9.3",
        "environment": "production",
        "ip_address": "10.1.2.100",
        "cpu_cores": 8,
        "memory_gb": 32.0,
        "storage_tb": 2.0,
        "risk_level": "high",
        "notes": "CAC/PIV auth service — IL4 required",
    }
    r = c.post("/api/govlift/workloads", json=wl_payload)
    check("POST /api/govlift/workloads → 201", r.status_code == 201, f"got {r.status_code}")
    wl = r.get_json() or {}
    workload_id = wl.get("id", "")
    check("workload has id", workload_id.startswith("wl-"), f"got '{workload_id}'")
    check("workload name matches", wl.get("name") == "ICDEV-Auth-Service", str(wl.get("name")))
    check("workload risk_level=high", wl.get("risk_level") == "high", str(wl.get("risk_level")))

    r2 = c.get("/api/govlift/workloads")
    check("GET /api/govlift/workloads → 200", r2.status_code == 200)
    wl_list = r2.get_json() or {}
    check("workloads list has total", "total" in wl_list, str(wl_list.keys()))
    check("new workload in list", any(w.get("id") == workload_id for w in wl_list.get("workloads", [])), workload_id)

    # ── 3. Create wave and assign workload ────────────────────────────────
    print("\n[3] Wave Planning")
    wave_payload = {
        "name": "Wave 1 — Auth Services",
        "sequence_num": 1,
        "planned_start": "2026-06-01",
        "planned_end": "2026-06-30",
        "notes": "First wave: authentication and identity workloads",
    }
    r = c.post("/api/govlift/waves", json=wave_payload)
    check("POST /api/govlift/waves → 201", r.status_code == 201, f"got {r.status_code}")
    wave = r.get_json() or {}
    wave_id = wave.get("id", "")
    check("wave has id", wave_id.startswith("wave-"), f"got '{wave_id}'")

    r = c.post(f"/api/govlift/workloads/{workload_id}/assign-wave", json={"wave_id": wave_id})
    check("POST assign-wave → 200", r.status_code == 200, f"got {r.status_code}")
    assigned = r.get_json() or {}
    check("assigned wave_id matches", assigned.get("wave_id") == wave_id, str(assigned.get("wave_id")))

    r = c.get("/api/govlift/waves")
    check("GET /api/govlift/waves → 200", r.status_code == 200)
    wave_list = (r.get_json() or {}).get("waves", [])
    check("wave in list", any(w.get("id") == wave_id for w in wave_list), wave_id)

    # ── 4. Migration lifecycle ────────────────────────────────────────────
    print("\n[4] Migration Lifecycle")
    r = c.post("/api/govlift/migrations", json={"workload_id": workload_id, "wave_id": wave_id})
    check("POST /api/govlift/migrations → 201", r.status_code == 201, f"got {r.status_code}")
    mig = r.get_json() or {}
    mig_id = mig.get("id", "")
    check("migration has id", mig_id.startswith("mig-"), f"got '{mig_id}'")
    check("migration status=pending", mig.get("status") == "pending", str(mig.get("status")))

    r = c.post(f"/api/govlift/migrations/{mig_id}/start")
    check("POST migrations start → 200", r.status_code == 200, f"got {r.status_code}")
    started = r.get_json() or {}
    check("migration status=running", started.get("status") == "running", str(started.get("status")))

    r = c.post(f"/api/govlift/migrations/{mig_id}/complete",
               json={"success": True, "log": "Migration completed successfully. All 47 checks passed."})
    check("POST migrations complete → 200", r.status_code == 200, f"got {r.status_code}")
    completed = r.get_json() or {}
    check("migration status=completed", completed.get("status") == "completed", str(completed.get("status")))

    # ── 5. Rollback path ─────────────────────────────────────────────────
    print("\n[5] Rollback Path")
    r2 = c.post("/api/govlift/migrations", json={"workload_id": workload_id, "wave_id": wave_id})
    mig2_id = (r2.get_json() or {}).get("id", "")
    c.post(f"/api/govlift/migrations/{mig2_id}/start")
    r_roll = c.post(f"/api/govlift/migrations/{mig2_id}/rollback")
    check("POST rollback → 200", r_roll.status_code == 200, f"got {r_roll.status_code}")
    rolled = r_roll.get_json() or {}
    check("migration status=rolled_back", rolled.get("status") == "rolled_back", str(rolled.get("status")))

    # ── 6. STIG scan ─────────────────────────────────────────────────────
    print("\n[6] STIG Compliance")
    r = c.post(f"/api/govlift/stig/scan/{workload_id}")
    check("POST stig/scan → 200", r.status_code == 200, f"got {r.status_code}")
    scan = r.get_json() or {}
    check("scan_results key present", "scan_results" in scan, str(scan.keys()))
    results = scan.get("scan_results", [])
    check("at least 5 STIG checks generated", len(results) >= 5, f"got {len(results)}")
    check("checks have severity", all("severity" in s for s in results[:3]), str(results[:1]))

    # Update one check status
    if results:
        check_id = results[0].get("id", "")
        r = c.patch(f"/api/govlift/stig/{check_id}/status",
                    json={"status": "not_a_finding", "finding": "Verified compliant via automated scan"})
        check("PATCH stig status → 200", r.status_code == 200, f"got {r.status_code}")
        updated = r.get_json() or {}
        check("stig status updated", updated.get("status") == "not_a_finding", str(updated.get("status")))

    # ── 7. Audit log ─────────────────────────────────────────────────────
    print("\n[7] Audit Log")
    r = c.get("/api/govlift/audit")
    check("GET /api/govlift/audit → 200", r.status_code == 200, f"got {r.status_code}")
    audit_resp = r.get_json() or {}
    check("audit_log key present", "audit_log" in audit_resp, str(audit_resp.keys()))
    entries = audit_resp.get("audit_log", [])
    check("audit entries recorded", len(entries) >= 1, f"got {len(entries)}")

    # Manual audit entry
    r = c.post("/api/govlift/audit", json={
        "user_id": "e2e-test-runner",
        "action": "e2e_lifecycle_test",
        "resource_type": "workload",
        "resource_id": workload_id,
        "details": {"test": "govlift_lifecycle", "result": "pass"},
        "ip_address": "127.0.0.1",
        "session_id": "test-session-e2e",
    })
    check("POST /api/govlift/audit → 201", r.status_code == 201, f"got {r.status_code}")

    # ── 8. IQE query ─────────────────────────────────────────────────────
    print("\n[8] IQE Natural-Language Query")
    r = c.post("/api/iqe/dispatch", json={"question": "show me high risk workloads", "canvas": "govlift"})
    check("POST /api/iqe/dispatch → 200", r.status_code == 200, f"got {r.status_code}")

    # ── 9. Overview API ───────────────────────────────────────────────────
    print("\n[9] Overview API")
    r = c.get("/api/govlift/overview")
    check("GET /api/govlift/overview → 200", r.status_code == 200, f"got {r.status_code}")
    overview = r.get_json() or {}
    check("overview.scanner present", "scanner" in overview, str(overview.keys()))
    check("overview.waves present", "waves" in overview, str(overview.keys()))
    check("overview.migrations present", "migrations" in overview, str(overview.keys()))
    check("overview.audit present", "audit" in overview, str(overview.keys()))
    check("scanner.total >= 1", (overview.get("scanner") or {}).get("total", 0) >= 1)
    check("migrations.total >= 1", (overview.get("migrations") or {}).get("total", 0) >= 1)

    # ── 10. Migration summary API ─────────────────────────────────────────
    print("\n[10] Migration Query Filters")
    r = c.get(f"/api/govlift/migrations?workload_id={workload_id}")
    check("GET migrations?workload_id → 200", r.status_code == 200)
    m_list = (r.get_json() or {}).get("migrations", [])
    check("filtered migrations match workload", all(m.get("workload_id") == workload_id for m in m_list), str(m_list[:1]))

    # ── Summary ───────────────────────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"  PASS: {PASS}  FAIL: {FAIL}  TOTAL: {PASS+FAIL}")
    if ERRORS:
        print("\n  Failures:")
        for e in ERRORS:
            print(f"    - {e}")

    if as_json:
        print(json.dumps({"pass": PASS, "fail": FAIL, "errors": ERRORS}, indent=2))

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()
    sys.exit(run_tests(as_json=args.as_json))
