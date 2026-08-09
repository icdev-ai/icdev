#!/usr/bin/env python3
"""Two CLIs imported symbols that had never been authored (kax-conflict-06).

  * tools/agent/skill_router.py and tools/supply_chain/isa_manager.py imported
    ``row_to_dict_json`` from tools/common/helpers.py, which only ever defined
    ``row_to_dict``.
  * tools/security/vuln_scanner.py imported ``process_scan_result`` from
    tools/security/boundary_tagger.py, which did not exist at all.

Both were documented (isa_manager pins the ``json_fields=`` signature;
tools/manifest/security-scanning.md and goals/security_scan.md Step 5 specify
the tagger's tiers, CLI, and outputs), so the missing code was authored to
those specs rather than deleted.

The launch test below runs each CLI with a SANITISED environment -- PYTHONPATH
removed -- because this box exports ``PYTHONPATH=C:\\AI\\ICDev``, which silently
satisfies ``import tools`` from the shared checkout and hides the very defect
being tested.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._sql_compat import translating  # noqa: E402
from tools.common.helpers import row_to_dict, row_to_dict_json  # noqa: E402
from tools.security import boundary_tagger as bt  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

#: Modules that could not be imported at all before this fix.
CLIS = [
    "tools/agent/skill_router.py",
    "tools/security/vuln_scanner.py",
    "tools/supply_chain/isa_manager.py",
    "tools/security/boundary_tagger.py",
]


def _sanitised_launcher(script: Path) -> subprocess.CompletedProcess:
    """Run ``python <script> --help`` with no PYTHONPATH to help it.

    Running a file BY PATH puts the FILE's directory on sys.path, never the
    repo root -- so the module's own ``sys.path.insert`` bootstrap is the only
    thing that can make ``import tools...`` work. Inheriting PYTHONPATH would
    mask a missing or mis-ordered bootstrap.
    """
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


# ---------------------------------------------------------------------------
# Acceptance criterion 1 -- both CLIs start
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rel", CLIS)
@pytest.mark.parametrize("base", ["", "icdev/"])
def test_cli_starts_with_a_sanitised_syspath(base, rel):
    script = REPO / (base + rel)
    if not script.exists():
        pytest.skip(f"no mirror at {base + rel}")
    proc = _sanitised_launcher(script)
    assert proc.returncode == 0, (
        f"`python {base + rel} --help` exited {proc.returncode}:\n"
        f"{proc.stderr[-2000:]}"
    )
    assert "usage" in (proc.stdout + proc.stderr).lower()


@pytest.mark.parametrize("base", ["", "icdev/"])
def test_the_previously_missing_symbols_are_importable(base):
    """Guard the exact names whose absence killed the modules on import."""
    root = REPO / base if base else REPO
    helpers = (root / "tools/common/helpers.py").read_text(encoding="utf-8")
    assert "def row_to_dict_json" in helpers, f"{base}tools/common/helpers.py lost row_to_dict_json"
    tagger = root / "tools/security/boundary_tagger.py"
    assert tagger.exists(), f"{base}tools/security/boundary_tagger.py is missing again"
    assert "def process_scan_result" in tagger.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# row_to_dict_json -- real decoding behaviour, not just importability
# ---------------------------------------------------------------------------


def _row(**cols) -> sqlite3.Row:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    names = ", ".join(f'"{k}"' for k in cols)
    marks = ", ".join("?" for _ in cols)
    conn.execute(f"CREATE TABLE t ({', '.join(f'{k} TEXT' for k in cols)})")
    conn.execute(f"INSERT INTO t ({names}) VALUES ({marks})", tuple(cols.values()))
    conn.commit()  # conftest fails teardown on a connection left mid-transaction
    return conn.execute("SELECT * FROM t").fetchone()


class TestRowToDictJson:
    def test_auto_decodes_json_object_columns(self):
        """skill_router does `caps.get("skills")` -- it needs a dict, not a str."""
        row = _row(id="agent-1", capabilities=json.dumps({"skills": [{"id": "build"}]}))
        out = row_to_dict_json(row)
        assert isinstance(out["capabilities"], dict)
        assert out["capabilities"]["skills"][0]["id"] == "build"
        # ...and the un-decoded helper really does hand back a string, which is
        # what made the distinction necessary in the first place.
        assert isinstance(row_to_dict(row)["capabilities"], str)

    def test_auto_decodes_json_array_columns(self):
        row = _row(ports=json.dumps(["443/tcp", "22/tcp"]))
        assert row_to_dict_json(row)["ports"] == ["443/tcp", "22/tcp"]

    def test_named_json_fields_are_decoded_and_others_left_alone(self):
        """isa_manager's contract: row_to_dict_json(row, json_fields=(...))."""
        row = _row(
            data_types_shared=json.dumps(["CUI"]),
            note=json.dumps(["not", "requested"]),
        )
        out = row_to_dict_json(row, json_fields=("data_types_shared",))
        assert out["data_types_shared"] == ["CUI"]
        assert out["note"] == '["not", "requested"]'

    def test_non_json_and_scalar_values_survive_untouched(self):
        row = _row(name="Agent One", count="42", broken="{not json", empty="")
        out = row_to_dict_json(row)
        assert out == {"name": "Agent One", "count": "42", "broken": "{not json", "empty": ""}

    def test_missing_named_field_is_not_invented(self):
        out = row_to_dict_json(_row(a="1"), json_fields=("nope",))
        assert out == {"a": "1"}

    def test_none_row_yields_empty_dict(self):
        assert row_to_dict_json(None) == {}
        assert row_to_dict_json(None, json_fields=("x",)) == {}

    def test_null_column_is_preserved_as_none(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE t (capabilities TEXT)")
        conn.execute("INSERT INTO t (capabilities) VALUES (NULL)")
        conn.commit()
        row = conn.execute("SELECT * FROM t").fetchone()
        assert row_to_dict_json(row)["capabilities"] is None


# ---------------------------------------------------------------------------
# boundary_tagger -- tier rules from goals/security_scan.md Step 5
# ---------------------------------------------------------------------------


class TestClassifyFinding:
    @pytest.mark.parametrize(
        "finding,expected",
        [
            ({"source": "secrets", "severity": "LOW"}, "RED"),
            ({"source": "sast", "severity": "CRITICAL"}, "RED"),
            ({"source": "dependency/python", "severity": "CRITICAL"}, "RED"),
            ({"source": "sast", "severity": "HIGH", "type": "B602"}, "RED"),
            ({"source": "sast", "severity": "HIGH", "type": "B105"}, "RED"),
            ({"source": "container/dockerfile:Dockerfile", "type": "DS007",
              "severity": "HIGH"}, "RED"),
            ({"source": "container/dockerfile:Dockerfile", "type": "DS001",
              "severity": "HIGH"}, "ORANGE"),
            ({"source": "container/dockerfile:Dockerfile", "type": "DS006",
              "severity": "HIGH"}, "ORANGE"),
            ({"source": "sast", "severity": "HIGH", "type": "B404"}, "ORANGE"),
            ({"source": "dependency/javascript", "severity": "HIGH"}, "ORANGE"),
            ({"source": "sast", "severity": "MEDIUM", "type": "B303"}, "YELLOW"),
            ({"source": "sast", "severity": "MODERATE"}, "YELLOW"),
            ({"source": "sast", "severity": "LOW"}, "GREEN"),
            ({"source": "sast", "severity": "UNKNOWN"}, "GREEN"),
            ({"source": "sast"}, "GREEN"),
        ],
    )
    def test_tier_rules(self, finding, expected):
        assert bt.classify_finding(finding)["tier"] == expected

    def test_a_secret_outranks_its_reported_severity(self):
        """Secrets are ATO-invalidating even when the detector says LOW."""
        assert bt.classify_finding({"source": "secrets", "severity": "LOW"})["tier"] == "RED"

    def test_score_and_delay_track_the_tier_bands(self):
        red = bt.classify_finding({"source": "secrets"})
        green = bt.classify_finding({"source": "sast", "severity": "LOW"})
        assert 76 <= red["risk_score"] <= 100 and red["ato_delay_days"] == 180
        assert 0 <= green["risk_score"] <= 25 and green["ato_delay_days"] == 0

    def test_a_high_bandit_id_outside_the_red_list_stays_orange(self):
        """Guards against the RED list silently swallowing every HIGH SAST hit."""
        assert "B404" not in bt.RED_BANDIT_TESTS
        assert bt.classify_finding(
            {"source": "sast", "severity": "HIGH", "type": "B404"}
        )["tier"] == "ORANGE"


# ---------------------------------------------------------------------------
# collect_findings / process_scan_result
# ---------------------------------------------------------------------------


def _aggregate() -> dict:
    """A scan aggregate in the exact shape vuln_scanner.run_all_scans builds."""
    return {
        "project_id": "proj-test",
        "scans": {
            # Flat result dict.
            "sast": {
                "findings": [
                    {"test_id": "B602", "severity": "HIGH", "issue_text": "subprocess shell=True",
                     "file": "app.py", "line": 10},
                    {"test_id": "B303", "severity": "MEDIUM", "issue_text": "md5 used",
                     "file": "util.py", "line": 3},
                ]
            },
            # Nested: keyed by language.
            "dependency": {
                "python": {"findings": [
                    {"vulnerability_id": "CVE-2024-1", "severity": "CRITICAL",
                     "title": "rce", "package": "evil"},
                ]},
                "javascript": {"findings": [
                    {"vulnerability_id": "CVE-2024-2", "severity": "LOW", "title": "minor",
                     "package": "left-pad"},
                ]},
            },
            "secrets": {"findings": [{"type": "aws_key", "file": ".env", "line": 1}]},
            # Nested: keyed by artifact.
            "container": {
                "dockerfile:Dockerfile": {"findings": [
                    {"check_id": "DS001", "severity": "HIGH", "name": "Running as root"},
                ]},
            },
        },
    }


class TestCollectFindings:
    def test_walks_both_flat_and_nested_scan_shapes(self):
        findings = bt.collect_findings(_aggregate())
        assert sorted(f["source"] for f in findings) == [
            "container/dockerfile:Dockerfile",   # nested by artifact
            "dependency/javascript",             # nested by language
            "dependency/python",
            "sast",                              # flat
            "sast",
            "secrets",                           # flat
        ]

    def test_secrets_are_forced_to_critical(self):
        secret = [f for f in bt.collect_findings(_aggregate()) if f["source"] == "secrets"][0]
        assert secret["severity"] == "CRITICAL"

    def test_empty_aggregate_is_not_an_error(self):
        assert bt.collect_findings({}) == []
        assert bt.collect_findings({"scans": {"sast": None}}) == []


class TestProcessScanResult:
    def test_produces_the_summary_vuln_scanner_reads(self):
        """vuln_scanner indexes highest_tier and all four tier_counts keys
        unguarded -- a missing key is a KeyError at the end of every scan."""
        agg = _aggregate()
        summary = bt.process_scan_result(agg, project_id="proj-test")
        assert agg["boundary_impact_summary"] is summary
        assert summary["highest_tier"] == "RED"
        for tier in ("RED", "ORANGE", "YELLOW", "GREEN"):
            assert isinstance(summary["tier_counts"][tier], int)
        assert summary["tier_counts"]["RED"] == 3      # B602, CVE CRITICAL, secret
        assert summary["tier_counts"]["ORANGE"] == 1   # DS001
        assert summary["tier_counts"]["YELLOW"] == 1   # B303
        assert summary["tier_counts"]["GREEN"] == 1    # LOW dep
        assert summary["requires_ato_action"] is True
        assert summary["full_stop"] is True
        assert isinstance(summary["assessments"], list)

    def test_tags_the_original_finding_objects_in_place(self):
        agg = _aggregate()
        bt.process_scan_result(agg, project_id="proj-test")
        sast = agg["scans"]["sast"]["findings"]
        assert sast[0]["boundary_impact"]["tier"] == "RED"
        assert sast[1]["boundary_impact"]["tier"] == "YELLOW"

    def test_clean_scan_is_green_and_needs_no_action(self):
        agg = {"scans": {"sast": {"findings": []}}}
        summary = bt.process_scan_result(agg)
        assert summary["highest_tier"] == "GREEN"
        assert summary["requires_ato_action"] is False
        assert summary["full_stop"] is False
        assert summary["tier_counts"] == {"GREEN": 0, "YELLOW": 0, "ORANGE": 0, "RED": 0}

    def test_persistence_is_skipped_without_a_system_id(self):
        """boundary_impact_assessments.system_id is NOT NULL with an FK to
        ato_system_registry, so tagging must degrade rather than write junk."""
        agg = _aggregate()
        summary = bt.process_scan_result(
            agg, project_id="proj-test", system_id=None, create_assessments=True
        )
        assert summary["assessments"] == []
        assert "system_id" in summary["assessments_skipped_reason"]

    def test_no_persistence_attempted_when_nothing_is_actionable(self, monkeypatch):
        called = []
        monkeypatch.setattr(bt, "_persist_assessments", lambda *a, **k: called.append(a) or [])
        summary = bt.process_scan_result(
            {"scans": {"sast": {"findings": [{"severity": "LOW"}]}}},
            project_id="p", system_id="s", create_assessments=True,
        )
        assert called == []
        assert summary["assessments"] == []


class TestPersistAssessments:
    """Exercise the real INSERT against the real DDL, so a column that does not
    exist in boundary_impact_assessments fails here instead of at scan time."""

    def _db(self, tmp_path):
        ddl = (REPO / "tools/db/init_icdev_db.py").read_text(encoding="utf-8")
        start = ddl.index("CREATE TABLE IF NOT EXISTS boundary_impact_assessments")
        create = ddl[start:ddl.index(");", start) + 2]
        conn = sqlite3.connect(str(tmp_path / "t.db"))
        conn.row_factory = sqlite3.Row
        # Drop the FK clauses -- the parent tables are out of scope here; the
        # column list and the impact_tier/impact_category CHECKs are the point.
        conn.execute(create.replace("REFERENCES intake_sessions(id)", "")
                     .replace("REFERENCES projects(id)", "")
                     .replace("REFERENCES ato_system_registry(id)", "")
                     .replace("REFERENCES intake_requirements(id)", "")
                     .replace("REFERENCES safe_decomposition(id)", ""))
        conn.commit()
        return conn

    def test_writes_one_row_per_actionable_finding(self, tmp_path, monkeypatch):
        conn = self._db(tmp_path)
        monkeypatch.setattr(bt, "get_connection",
                            lambda *a, **k: translating(conn, unclosable=True))
        agg = _aggregate()
        summary = bt.process_scan_result(
            agg, project_id="proj-test", system_id="sys-test", create_assessments=True
        )
        # 3 RED + 1 ORANGE are actionable; YELLOW/GREEN are not.
        assert len(summary["assessments"]) == 4
        rows = conn.execute(
            "SELECT * FROM boundary_impact_assessments ORDER BY impact_tier"
        ).fetchall()
        assert len(rows) == 4
        assert sorted(r["impact_tier"] for r in rows) == ["ORANGE", "RED", "RED", "RED"]
        for row in rows:
            assert row["project_id"] == "proj-test"
            assert row["system_id"] == "sys-test"
            assert row["assessed_by"] == "security/boundary_tagger"
            assert row["impact_description"]
            assert row["risk_score"] >= 51            # actionable tiers only
            assert json.loads(row["affected_components"])
            assert json.loads(row["remediation_required"])

    def test_impact_category_satisfies_the_check_constraint(self, tmp_path, monkeypatch):
        """A category outside the CHECK list would raise IntegrityError; the
        insert is wrapped in a try/except, so assert the rows really landed."""
        conn = self._db(tmp_path)
        monkeypatch.setattr(bt, "get_connection",
                            lambda *a, **k: translating(conn, unclosable=True))
        agg = _aggregate()
        bt.process_scan_result(
            agg, project_id="p", system_id="s", create_assessments=True
        )
        cats = {r["impact_category"]
                for r in conn.execute("SELECT impact_category FROM boundary_impact_assessments")}
        assert cats, "every insert was swallowed -- no row landed"
        allowed = {"architecture", "data_flow", "authentication", "authorization", "network",
                   "encryption", "logging", "boundary_change", "new_interconnection",
                   "data_type_change", "component_addition"}
        assert cats <= allowed, f"category outside the CHECK constraint: {cats - allowed}"


class TestEvaluateGate:
    def test_red_blocks(self):
        gate = bt.evaluate_gate({"highest_tier": "RED", "tier_counts": {"RED": 2}})
        assert gate["passed"] is False and gate["blocking"] is True

    def test_orange_warns_but_does_not_block(self):
        gate = bt.evaluate_gate({"highest_tier": "ORANGE", "tier_counts": {"ORANGE": 1}})
        assert gate["passed"] is True and gate["blocking"] is False

    @pytest.mark.parametrize("tier", ["YELLOW", "GREEN"])
    def test_low_tiers_pass(self, tier):
        gate = bt.evaluate_gate({"highest_tier": tier, "tier_counts": {}})
        assert gate["passed"] is True and gate["blocking"] is False


# ---------------------------------------------------------------------------
# CLI behaviour
# ---------------------------------------------------------------------------


class TestCli:
    def _run(self, tmp_path, aggregate, *extra):
        report = tmp_path / "scan.json"
        report.write_text(json.dumps(aggregate), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, str(REPO / "tools/security/boundary_tagger.py"),
             "--report", str(report), "--json", *extra],
            cwd=str(REPO), env=env, capture_output=True, text=True, timeout=120,
        )

    def test_emits_valid_json_with_the_tier_summary(self, tmp_path):
        proc = self._run(tmp_path, _aggregate())
        assert proc.returncode == 0, proc.stderr[-2000:]
        payload = json.loads(proc.stdout)
        assert payload["boundary_impact_summary"]["highest_tier"] == "RED"

    def test_gate_exits_nonzero_on_red(self, tmp_path):
        proc = self._run(tmp_path, _aggregate(), "--gate")
        assert proc.returncode == 1
        assert json.loads(proc.stdout)["gate"]["blocking"] is True

    def test_gate_exits_zero_on_a_clean_scan(self, tmp_path):
        proc = self._run(tmp_path, {"scans": {"sast": {"findings": []}}}, "--gate")
        assert proc.returncode == 0, proc.stderr[-2000:]
        assert json.loads(proc.stdout)["gate"]["passed"] is True

    def test_missing_report_exits_nonzero_without_a_traceback(self, tmp_path):
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        proc = subprocess.run(
            [sys.executable, str(REPO / "tools/security/boundary_tagger.py"),
             "--report", str(tmp_path / "nope.json")],
            cwd=str(REPO), env=env, capture_output=True, text=True, timeout=120,
        )
        assert proc.returncode == 1
        assert "Traceback" not in proc.stderr
