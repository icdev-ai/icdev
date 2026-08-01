# CUI // SP-CTI
"""Tests for tools.pipeline.studio_steps — the live-engine PDC workflow adapter.

Guards the pdx-sec-05 security fix: the retired tools/pdc trio graded a
hardcoded 6-node demo pipeline and emitted fabricated PASS/gate results
whenever its (non-existent) backing table was absent. The replacement MUST:

  * read the REAL design from the live ``pipelines`` table, and
  * FAIL LOUDLY (non-zero exit, gate FAIL, no artifacts) on a missing design —
    never grading demo data and never emitting PASS on missing input.
"""

import io
import json
import sqlite3
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.pipeline import studio_steps  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────────

# A graph the live detector will flag: CI engine + K8s deploy, zero scanners →
# AP-PDC-001 (critical: no security scanning). Uses the LIVE node vocab
# (cicd-gitlab / k8s-cluster), not the dead pdc vocab (stage-*/target-*).
_INSECURE_GRAPH = {
    "nodes": [
        {"id": "src", "label": "GitLab", "type": "scm-gitlab"},
        {"id": "ci", "label": "GitLab CI", "type": "cicd-gitlab"},
        {"id": "k8s", "label": "Kubernetes", "type": "k8s-cluster"},
    ],
    "edges": [
        {"id": "e1", "source": "src", "target": "ci"},
        {"id": "e2", "source": "ci", "target": "k8s"},
    ],
}


def _make_conn(rows):
    """In-memory SQLite ``pipelines`` table wrapped as a StorageConnection."""
    from tools.db.storage import StorageConnection

    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.execute(
        "CREATE TABLE pipelines (id TEXT PRIMARY KEY, name TEXT, graph_json TEXT)"
    )
    for pid, name, graph in rows:
        raw.execute(
            "INSERT INTO pipelines (id, name, graph_json) VALUES (?, ?, ?)",
            (pid, name, json.dumps(graph)),
        )
    raw.commit()
    return StorageConnection(raw, "sqlite")


def _run(argv):
    """Invoke the CLI, returning (exit_code, parsed_json_stdout)."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = studio_steps.main(argv)
    return code, json.loads(buf.getvalue().strip())


# ── FAIL-LOUD: missing / default pipeline id ─────────────────────────────────


@pytest.mark.parametrize("step", ["scan", "antipattern", "iac"])
def test_missing_pipeline_id_fails_loud(step):
    """'default'/absent id → exit 1, gate FAIL, no artifacts, no demo data."""
    conn = _make_conn([])  # empty table
    with patch("tools.pipeline.db.init_db.get_connection", return_value=conn):
        code, out = _run(["--step", step, "--project-id", "default", "--json"])

    assert code == 1
    assert out["gate"] == "FAIL"
    assert out["status"] == "failed"
    assert out["artifacts"] == []
    assert "error" in out
    # The fabrication smell test: never a PASS, never invented findings.
    assert out.get("antipatterns_detected", 0) == 0
    assert out.get("files_generated", 0) == 0


@pytest.mark.parametrize("step", ["scan", "antipattern", "iac"])
def test_nonexistent_pipeline_id_fails_loud(step):
    """A real-looking id absent from the table → loud failure, not demo data."""
    conn = _make_conn([("pl-exists", "Other", _INSECURE_GRAPH)])
    with patch("tools.pipeline.db.init_db.get_connection", return_value=conn):
        code, out = _run(["--step", step, "--pipeline-id", "pl-missing", "--json"])

    assert code == 1
    assert out["gate"] == "FAIL"
    assert out["artifacts"] == []
    assert "pl-missing" in out["error"]


# ── Real detections flow through for a seeded design ─────────────────────────


def test_scan_seeded_graph_real_detections():
    conn = _make_conn([("pl-1", "Insecure Pipeline", _INSECURE_GRAPH)])
    with patch("tools.pipeline.db.init_db.get_connection", return_value=conn):
        code, out = _run(["--step", "scan", "--pipeline-id", "pl-1", "--json"])

    # Scan is informational → exit 0 even when it reports a failing gate.
    assert code == 0
    assert out["pipeline_id"] == "pl-1"
    assert out["node_count"] == 3
    # Real engine flagged the missing-security-scanning critical anti-pattern.
    assert out["antipatterns_detected"] >= 1
    assert out["critical"] >= 1
    assert out["gate"] == "FAIL"
    assert out["artifacts"] and out["artifacts"][0]["type"] == "md"
    # Live vocab surfaced — not the dead stage-*/target-* demo vocab.
    assert "cicd-gitlab" in out["node_types"]


def test_antipattern_seeded_graph_gates_on_critical():
    conn = _make_conn([("pl-1", "Insecure Pipeline", _INSECURE_GRAPH)])
    with patch("tools.pipeline.db.init_db.get_connection", return_value=conn):
        code, out = _run(["--step", "antipattern", "--pipeline-id", "pl-1", "--json"])

    # Critical anti-pattern present → the step BLOCKS the workflow (exit 1).
    assert code == 1
    assert out["gate"] == "FAIL"
    assert out["status"] == "failed"
    assert out["critical"] >= 1
    assert "slsa_score" in out


def test_iac_seeded_graph_generates_real_bundle():
    conn = _make_conn([("pl-1", "Insecure Pipeline", _INSECURE_GRAPH)])
    with patch("tools.pipeline.db.init_db.get_connection", return_value=conn):
        code, out = _run(["--step", "iac", "--pipeline-id", "pl-1", "--json"])

    # A real bundle is generated + validated (gate is PASS or FAIL, never demo).
    assert out["gate"] in ("PASS", "FAIL")
    assert out["files_generated"] > 0
    assert out["artifacts"], "IaC step must emit real artifacts on disk"
    # At least one Terraform artifact for the downstream terraform_plan executor.
    assert any(a["type"] == "tf" for a in out["artifacts"])
    for a in out["artifacts"]:
        assert (studio_steps._ROOT / a["path"]).exists()
    assert code == (0 if out["gate"] == "PASS" else 1)
