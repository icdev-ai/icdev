#!/usr/bin/env python3
# CUI // SP-CTI
"""ATLAS Red Team Scanner — opt-in AI security testing against MITRE ATLAS techniques.

Tests AI/LLM defenses by simulating adversarial scenarios mapped to specific
ATLAS techniques. Opt-in via --atlas-red-team flag (D219).

Results stored in atlas_red_team_results table. Static checks only — no actual
LLM invocations. Verifies defensive tooling and configuration exist.

Pattern: tools/security/prompt_injection_detector.py
ADRs: D219 (opt-in red team), D217 (regex+heuristic), D218 (SHA-256 hashing)

CLI:
    python tools/security/atlas_red_team.py --all --json
    python tools/security/atlas_red_team.py --technique AML.T0051 --json
    python tools/security/atlas_red_team.py --summary --project-id proj-123 --json
"""

import argparse
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

ATLAS_TECHNIQUES = {
    "AML.T0051": {"name": "LLM Prompt Injection", "method": "test_prompt_injection_resistance"},
    "AML.T0056": {"name": "LLM System Prompt Extraction", "method": "test_system_prompt_extraction"},
    "AML.T0080": {"name": "LLM Memory Poisoning", "method": "test_memory_poisoning"},
    "AML.T0086": {"name": "LLM Tool Abuse", "method": "test_tool_abuse"},
    "AML.T0057": {"name": "LLM Data Leakage", "method": "test_data_leakage"},
    "AML.T0034": {"name": "Cost Harvesting", "method": "test_cost_harvesting"},
}


class ATLASRedTeamScanner:
    """Static red team scanner for MITRE ATLAS AI/LLM techniques.

    All tests are static checks — verify defensive tooling and config exist.
    Results stored append-only per D6.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or DB_PATH
        self._ensure_table()

    def _ensure_table(self):
        if not self._db_path.exists():
            return
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute("""CREATE TABLE IF NOT EXISTS atlas_red_team_results (
                id TEXT PRIMARY KEY, project_id TEXT,
                technique TEXT NOT NULL, technique_name TEXT NOT NULL,
                passed INTEGER NOT NULL DEFAULT 0,
                tests_run INTEGER NOT NULL DEFAULT 0, tests_passed INTEGER NOT NULL DEFAULT 0,
                findings_json TEXT, scanned_at TEXT NOT NULL,
                classification TEXT DEFAULT 'CUI')""")
            conn.commit()
            conn.close()
        except Exception:
            pass

    # -- Helpers for test methods ------------------------------------------
    @staticmethod
    def _read(path: Path) -> str:
        """Read file content or return empty string."""
        if path.exists():
            return path.read_text(encoding="utf-8", errors="ignore")
        return ""

    @staticmethod
    def _result(technique: str, name: str, tests_run: int, tests_passed: int,
                findings: List[Dict]) -> dict:
        return {
            "technique": technique, "name": name,
            "passed": tests_run == tests_passed,
            "tests_run": tests_run, "tests_passed": tests_passed,
            "findings": findings, "scanned_at": datetime.now(timezone.utc).isoformat(),
        }

    def _check(self, findings, tests, exists: bool, test_name: str,
               severity: str, fail_msg: str):
        """Run a boolean check — appends finding on failure, returns updated counters."""
        tests[0] += 1
        if exists:
            tests[1] += 1
        else:
            findings.append({"test": test_name, "severity": severity, "message": fail_msg})

    # -- Run all / individual ----------------------------------------------
    def run_all_tests(self, project_id: Optional[str] = None) -> dict:
        results, total_run, total_passed, all_ok = [], 0, 0, True
        for tid, info in ATLAS_TECHNIQUES.items():
            r = getattr(self, info["method"])(project_id=project_id)
            results.append(r)
            total_run += r["tests_run"]
            total_passed += r["tests_passed"]
            if not r["passed"]:
                all_ok = False
            self._store_result(r, project_id=project_id)
        return {
            "passed": all_ok, "techniques_tested": len(results),
            "techniques_passed": sum(1 for r in results if r["passed"]),
            "total_tests_run": total_run, "total_tests_passed": total_passed,
            "results": results, "scanned_at": datetime.now(timezone.utc).isoformat(),
        }

    def run_technique(self, technique_id: str, project_id: Optional[str] = None) -> dict:
        info = ATLAS_TECHNIQUES.get(technique_id)
        if not info:
            return {"error": f"Unknown technique: {technique_id}",
                    "valid_techniques": list(ATLAS_TECHNIQUES.keys())}
        r = getattr(self, info["method"])(project_id=project_id)
        self._store_result(r, project_id=project_id)
        return r

    # -- AML.T0051: Prompt Injection Resistance ----------------------------
    def test_prompt_injection_resistance(self, project_id: Optional[str] = None) -> dict:
        f, t = [], [0, 0]  # findings, [tests_run, tests_passed]
        det = BASE_DIR / "tools" / "security" / "prompt_injection_detector.py"
        content = self._read(det)
        self._check(f, t, det.exists(), "detector_exists", "critical",
                    "prompt_injection_detector.py not found")
        for cat in ("role_hijacking", "delimiter_attack", "instruction_injection",
                    "data_exfiltration", "encoded_payload"):
            self._check(f, t, f'"{cat}"' in content or f"'{cat}'" in content,
                       f"category_{cat}", "high",
                       f"Pattern category '{cat}' not found in detector" if det.exists()
                       else f"Cannot verify '{cat}' -- detector missing")
        self._check(f, t, "evaluate_gate" in content, "gate_evaluation", "high",
                   "Gate evaluation method not found in detector" if det.exists()
                   else "Cannot verify gate evaluation -- detector missing")
        return self._result("AML.T0051", "LLM Prompt Injection", t[0], t[1], f)

    # -- AML.T0056: System Prompt Extraction -------------------------------
    def test_system_prompt_extraction(self, project_id: Optional[str] = None) -> dict:
        f, t = [], [0, 0]
        det_content = self._read(BASE_DIR / "tools" / "security" / "prompt_injection_detector.py")
        self._check(f, t, "system_prompt_reveal" in det_content or "AML.T0056" in det_content,
                   "extraction_patterns", "high" if det_content else "critical",
                   "No system prompt extraction patterns in detector" if det_content
                   else "Prompt injection detector not found")
        self._check(f, t, (BASE_DIR / "tools" / "gateway" / "response_filter.py").exists(),
                   "response_filter_exists", "high",
                   "Response filter (tools/gateway/response_filter.py) not found")
        self._check(f, t, (BASE_DIR / "tools" / "compliance" / "classification_manager.py").exists(),
                   "classification_filtering", "high",
                   "Classification manager not found -- output filtering at risk")
        return self._result("AML.T0056", "LLM System Prompt Extraction", t[0], t[1], f)

    # -- AML.T0080: Memory Poisoning ---------------------------------------
    def test_memory_poisoning(self, project_id: Optional[str] = None) -> dict:
        f, t = [], [0, 0]
        mw = BASE_DIR / "tools" / "memory" / "memory_write.py"
        content = self._read(mw)
        self._check(f, t, mw.exists(), "memory_write_exists", "critical",
                   "Memory write tool not found")
        self._check(f, t, "content_hash" in content and "sha256" in content.lower(),
                   "content_hash_dedup", "high",
                   "Content-hash deduplication (D179) not found in memory_write")
        self._check(f, t, "VALID_TYPES" in content or "valid_types" in content.lower(),
                   "type_validation", "medium",
                   "Memory type validation (enum check) not found")
        self._check(f, t,
                   "UPDATE memory_entries" not in content and "DELETE FROM memory_entries" not in content,
                   "append_only", "critical",
                   "Memory write contains UPDATE/DELETE on memory_entries -- violates D6")
        return self._result("AML.T0080", "LLM Memory Poisoning", t[0], t[1], f)

    # -- AML.T0086: Tool Abuse ---------------------------------------------
    def test_tool_abuse(self, project_id: Optional[str] = None) -> dict:
        f, t = [], [0, 0]
        gc_path = BASE_DIR / "args" / "remote_gateway_config.yaml"
        gc = self._read(gc_path)
        self._check(f, t, "command_allowlist:" in gc, "command_allowlist_exists", "critical",
                   "command_allowlist missing from gateway config" if gc
                   else "remote_gateway_config.yaml not found")
        # D138: deploy disabled on remote channels
        deploy_ok = False
        if "icdev-deploy" in gc:
            sec = gc[gc.find("icdev-deploy"):gc.find("icdev-deploy") + 200]
            deploy_ok = 'channels: ""' in sec or "channels: ''" in sec
        self._check(f, t, deploy_ok, "deploy_disabled_remote", "critical",
                   "icdev-deploy not disabled on remote channels (D138)")
        # Execute commands require confirmation
        exec_ok = True
        if gc:
            for sec in gc.split("category: execute")[1:]:
                if "requires_confirmation: false" in sec[:200]:
                    exec_ok = False
                    break
        self._check(f, t, exec_ok and bool(gc), "execute_confirmation", "high",
                   "Execute-category commands found without confirmation" if gc
                   else "Cannot verify -- gateway config missing")
        self._check(f, t, (BASE_DIR / "tools" / "gateway" / "user_binder.py").exists(),
                   "user_binding", "high",
                   "User binder not found -- D136 at risk")
        return self._result("AML.T0086", "LLM Tool Abuse", t[0], t[1], f)

    # -- AML.T0057: Data Leakage -------------------------------------------
    def test_data_leakage(self, project_id: Optional[str] = None) -> dict:
        f, t = [], [0, 0]
        self._check(f, t, (BASE_DIR / "tools" / "compliance" / "classification_manager.py").exists(),
                   "classification_manager_exists", "critical", "Classification manager not found")
        ucm = self._read(BASE_DIR / "tools" / "compliance" / "universal_classification_manager.py")
        self._check(f, t, "composite" in ucm.lower() or "banner" in ucm.lower(),
                   "composite_markings", "high",
                   "Universal classification manager missing or lacks composite marking support")
        rf = self._read(BASE_DIR / "tools" / "gateway" / "response_filter.py")
        self._check(f, t, "max_il" in rf or "classification" in rf.lower(),
                   "il_response_filtering", "critical" if not rf else "high",
                   "Response filter not found -- D135 at risk" if not rf
                   else "Response filter lacks IL-based content stripping")
        self._check(f, t, (BASE_DIR / "args" / "cui_markings.yaml").exists(),
                   "cui_marking_config", "high",
                   "CUI markings config (args/cui_markings.yaml) not found")
        return self._result("AML.T0057", "LLM Data Leakage", t[0], t[1], f)

    # -- AML.T0034: Cost Harvesting ----------------------------------------
    def test_cost_harvesting(self, project_id: Optional[str] = None) -> dict:
        f, t = [], [0, 0]
        rl = (BASE_DIR / "tools" / "saas" / "rate_limiter.py").exists()
        if not rl:
            gc = self._read(BASE_DIR / "args" / "remote_gateway_config.yaml")
            rl = "rate_limit" in gc
        self._check(f, t, rl, "rate_limiting", "critical",
                   "No rate limiting mechanism found")
        cli = self._read(BASE_DIR / "args" / "cli_config.yaml")
        self._check(f, t, "max_tokens_per_run" in cli, "token_budget", "high",
                   "Token budget (max_tokens_per_run) not configured" if cli
                   else "cli_config.yaml not found -- no token budget enforcement")
        tel = BASE_DIR / "tools" / "security" / "ai_telemetry_logger.py"
        tel_content = self._read(tel)
        self._check(f, t, tel.exists(), "telemetry_logger", "high",
                   "AI telemetry logger not found -- usage monitoring absent")
        self._check(f, t, "detect_anomalies" in tel_content and "cost_spike" in tel_content,
                   "cost_anomaly_detection", "medium",
                   "Cost anomaly detection not found in telemetry logger")
        return self._result("AML.T0034", "Cost Harvesting", t[0], t[1], f)

    # -- DB storage (append-only per D6) -----------------------------------
    def _store_result(self, result: dict, project_id: Optional[str] = None):
        if not self._db_path.exists():
            return None
        entry_id = str(uuid.uuid4())
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute(
                """INSERT INTO atlas_red_team_results
                   (id, project_id, technique, technique_name, passed,
                    tests_run, tests_passed, findings_json, scanned_at, classification)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (entry_id, project_id, result.get("technique", ""),
                 result.get("name", ""), 1 if result.get("passed") else 0,
                 result.get("tests_run", 0), result.get("tests_passed", 0),
                 json.dumps(result.get("findings", [])),
                 result.get("scanned_at", datetime.now(timezone.utc).isoformat()), "CUI"))
            conn.commit()
            conn.close()
            return entry_id
        except Exception:
            return None

    # -- Query stored results ----------------------------------------------
    def get_results(self, project_id: Optional[str] = None,
                    technique: Optional[str] = None) -> list:
        if not self._db_path.exists():
            return []
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.row_factory = sqlite3.Row
            wh, params = [], []
            if project_id:
                wh.append("project_id = ?"); params.append(project_id)
            if technique:
                wh.append("technique = ?"); params.append(technique)
            where = (" WHERE " + " AND ".join(wh)) if wh else ""
            rows = conn.execute(
                f"SELECT * FROM atlas_red_team_results{where} ORDER BY scanned_at DESC",
                params).fetchall()
            conn.close()
            return [{"id": r["id"], "project_id": r["project_id"],
                     "technique": r["technique"], "technique_name": r["technique_name"],
                     "passed": bool(r["passed"]), "tests_run": r["tests_run"],
                     "tests_passed": r["tests_passed"],
                     "findings": json.loads(r["findings_json"]) if r["findings_json"] else [],
                     "scanned_at": r["scanned_at"]} for r in rows]
        except Exception:
            return []

    def get_summary(self, project_id: Optional[str] = None) -> dict:
        if not self._db_path.exists():
            return {"error": "Database not found", "techniques": {}}
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.row_factory = sqlite3.Row
            where, params = "", []
            if project_id:
                where = " WHERE project_id = ?"; params = [project_id]
            rows = conn.execute(
                f"""SELECT r.* FROM atlas_red_team_results r INNER JOIN (
                    SELECT technique, MAX(scanned_at) AS latest
                    FROM atlas_red_team_results{where} GROUP BY technique
                ) l ON r.technique = l.technique AND r.scanned_at = l.latest""",
                params).fetchall()
            conn.close()
            techs, total_r, total_p, ok = {}, 0, 0, True
            for r in rows:
                p = bool(r["passed"])
                techs[r["technique"]] = {"name": r["technique_name"], "passed": p,
                    "tests_run": r["tests_run"], "tests_passed": r["tests_passed"],
                    "scanned_at": r["scanned_at"]}
                total_r += r["tests_run"]; total_p += r["tests_passed"]
                if not p:
                    ok = False
            return {"overall_passed": ok if techs else False,
                    "techniques_tested": len(techs),
                    "techniques_passed": sum(1 for v in techs.values() if v["passed"]),
                    "total_tests_run": total_r, "total_tests_passed": total_p,
                    "techniques": techs, "project_id": project_id}
        except Exception as e:
            return {"error": str(e), "techniques": {}}


# ===============================================================
# CLI
# ===============================================================
def main():
    ap = argparse.ArgumentParser(
        description="ATLAS Red Team Scanner -- MITRE ATLAS AI/LLM defense testing")
    ap.add_argument("--all", action="store_true", help="Run all ATLAS red team tests")
    ap.add_argument("--technique", help="Run specific ATLAS technique (e.g. AML.T0051)")
    ap.add_argument("--summary", action="store_true", help="Show summary of stored results")
    ap.add_argument("--project-id", help="Project ID for scoping and storage")
    ap.add_argument("--json", action="store_true", help="Output as JSON")
    args = ap.parse_args()

    scanner = ATLASRedTeamScanner()
    if args.all:
        result = scanner.run_all_tests(project_id=args.project_id)
    elif args.technique:
        result = scanner.run_technique(args.technique, project_id=args.project_id)
    elif args.summary:
        result = scanner.get_summary(project_id=args.project_id)
    else:
        ap.print_help(); return

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_human(result, is_summary=args.summary)


def _print_human(result: dict, is_summary: bool = False):
    if "error" in result:
        print(f"Error: {result['error']}"); return
    if is_summary:
        status = "PASSED" if result.get("overall_passed") else "FAILED"
        print(f"ATLAS Red Team Summary: {status}")
        print(f"  Techniques: {result.get('techniques_passed', 0)}/{result.get('techniques_tested', 0)}")
        print(f"  Tests: {result.get('total_tests_passed', 0)}/{result.get('total_tests_run', 0)}")
        for tid, i in result.get("techniques", {}).items():
            s = "PASS" if i["passed"] else "FAIL"
            print(f"  [{s}] {tid}: {i['name']} ({i['tests_passed']}/{i['tests_run']})")
        return
    if "results" in result:
        status = "PASSED" if result.get("passed") else "FAILED"
        print(f"ATLAS Red Team Scan: {status}")
        print(f"  Techniques: {result.get('techniques_passed', 0)}/{result.get('techniques_tested', 0)}")
        print(f"  Tests: {result.get('total_tests_passed', 0)}/{result.get('total_tests_run', 0)}")
        for r in result.get("results", []):
            s = "PASS" if r["passed"] else "FAIL"
            print(f"\n  [{s}] {r['technique']}: {r['name']} ({r['tests_passed']}/{r['tests_run']})")
            for finding in r.get("findings", []):
                print(f"    [{finding['severity']}] {finding['test']}: {finding['message']}")
    elif "technique" in result:
        status = "PASSED" if result.get("passed") else "FAILED"
        print(f"ATLAS Red Team -- {result['technique']}: {result['name']}: {status}")
        print(f"  Tests: {result.get('tests_passed', 0)}/{result.get('tests_run', 0)}")
        for finding in result.get("findings", []):
            print(f"  [{finding['severity']}] {finding['test']}: {finding['message']}")


if __name__ == "__main__":
    main()
