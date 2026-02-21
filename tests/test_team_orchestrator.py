# CUI // SP-CTI
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for tools.agent.team_orchestrator — DAG-based multi-agent workflow engine."""

import json
import sqlite3
from dataclasses import asdict
from unittest.mock import MagicMock, patch

import pytest

from tools.agent.team_orchestrator import (
    Subtask,
    TeamOrchestrator,
    Workflow,
    _ensure_tables,
    _get_db,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def orch_db(tmp_path):
    """Temporary database with agent_workflows and agent_subtasks tables plus
    the minimal ICDEV tables needed by the orchestrator (audit_trail, agents)."""
    db_path = tmp_path / "orchestrator.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agent_workflows (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            project_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_by TEXT DEFAULT 'orchestrator-agent',
            aggregated_result TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS agent_subtasks (
            id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            skill_id TEXT NOT NULL,
            description TEXT DEFAULT '',
            depends_on TEXT DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'pending',
            input_data TEXT,
            output_data TEXT,
            error_message TEXT DEFAULT '',
            attempt_count INTEGER DEFAULT 0,
            duration_ms INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (workflow_id) REFERENCES agent_workflows(id)
        );

        CREATE TABLE IF NOT EXISTS audit_trail (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            project_id TEXT,
            details TEXT,
            classification TEXT DEFAULT 'CUI',
            session_id TEXT,
            source_ip TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            url TEXT NOT NULL DEFAULT 'http://localhost:8443',
            status TEXT NOT NULL DEFAULT 'inactive',
            capabilities TEXT,
            last_heartbeat TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def orchestrator(orch_db):
    """TeamOrchestrator instance pointed at the temporary database."""
    return TeamOrchestrator(max_workers=2, db_path=orch_db)


# Mock LLM response used by decompose tests
MOCK_DECOMPOSITION = {
    "workflow_name": "Build REST API",
    "subtasks": [
        {
            "id": "st-arch",
            "agent_id": "architect-agent",
            "skill_id": "design_api",
            "description": "Design REST API schema",
            "depends_on": [],
        },
        {
            "id": "st-build",
            "agent_id": "builder-agent",
            "skill_id": "generate_code",
            "description": "Generate API implementation",
            "depends_on": ["st-arch"],
        },
        {
            "id": "st-test",
            "agent_id": "builder-agent",
            "skill_id": "write_tests",
            "description": "Write unit tests",
            "depends_on": ["st-build"],
        },
    ],
}


def _mock_llm_invoke(function_name, llm_request):
    """Return a deterministic LLM response mimicking structured output."""
    response = MagicMock()
    response.structured_output = MOCK_DECOMPOSITION
    response.content = json.dumps(MOCK_DECOMPOSITION)
    return response


# ---------------------------------------------------------------------------
# TestSubtaskDataclass
# ---------------------------------------------------------------------------

class TestSubtaskDataclass:
    """Subtask dataclass: defaults, serialization, field values."""

    def test_default_fields(self):
        st = Subtask(id="st-1", agent_id="builder-agent", skill_id="generate_code")
        assert st.status == "pending"
        assert st.depends_on == []
        assert st.input_data is None
        assert st.output_data is None
        assert st.error_message == ""
        assert st.attempt_count == 0
        assert st.duration_ms == 0
        assert st.description == ""

    def test_asdict_roundtrip(self):
        st = Subtask(
            id="st-x",
            agent_id="security-agent",
            skill_id="sast_scan",
            description="Run SAST",
            depends_on=["st-a", "st-b"],
        )
        d = asdict(st)
        assert d["id"] == "st-x"
        assert d["depends_on"] == ["st-a", "st-b"]
        assert d["description"] == "Run SAST"


# ---------------------------------------------------------------------------
# TestWorkflowDataclass
# ---------------------------------------------------------------------------

class TestWorkflowDataclass:
    """Workflow dataclass: defaults, subtask dict, status."""

    def test_default_fields(self):
        wf = Workflow(id="wf-1", name="Test workflow")
        assert wf.status == "pending"
        assert wf.subtasks == {}
        assert wf.project_id == ""
        assert wf.created_by == "orchestrator-agent"
        assert wf.aggregated_result is None

    def test_subtask_dict_population(self):
        wf = Workflow(id="wf-2", name="Multi")
        st = Subtask(id="s1", agent_id="a", skill_id="sk")
        wf.subtasks[st.id] = st
        assert "s1" in wf.subtasks
        assert wf.subtasks["s1"].agent_id == "a"


# ---------------------------------------------------------------------------
# TestEnsureTables
# ---------------------------------------------------------------------------

class TestEnsureTables:
    """_ensure_tables: creates agent_workflows and agent_subtasks if missing."""

    def test_creates_tables_in_empty_db(self, tmp_path):
        db_path = tmp_path / "empty.db"
        # Database file does not exist yet
        _ensure_tables(db_path)
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('agent_workflows', 'agent_subtasks') ORDER BY name"
        )
        tables = sorted([r[0] for r in cursor.fetchall()])
        conn.close()
        assert tables == ["agent_subtasks", "agent_workflows"]

    def test_idempotent(self, tmp_path):
        db_path = tmp_path / "idem.db"
        _ensure_tables(db_path)
        _ensure_tables(db_path)  # Should not raise
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='agent_workflows'")
        assert cursor.fetchone() is not None
        conn.close()


# ---------------------------------------------------------------------------
# TestDecompose
# ---------------------------------------------------------------------------

class TestDecompose:
    """decompose_task: LLM-based decomposition with fallback."""

    @patch("tools.agent.team_orchestrator._audit_log")
    def test_decompose_via_llm(self, mock_audit, orchestrator):
        """When LLM succeeds, workflow has multiple subtasks from the response."""
        mock_router = MagicMock()
        mock_router.invoke.side_effect = _mock_llm_invoke
        orchestrator._llm_router = mock_router

        wf = orchestrator.decompose_task(
            "Build a REST API with auth",
            project_id="proj-test",
        )

        assert wf.name == "Build REST API"
        assert len(wf.subtasks) == 3
        assert "st-arch" in wf.subtasks
        assert "st-build" in wf.subtasks
        assert "st-test" in wf.subtasks
        assert wf.subtasks["st-build"].depends_on == ["st-arch"]
        assert wf.subtasks["st-test"].depends_on == ["st-build"]

    @patch("tools.agent.team_orchestrator._audit_log")
    def test_decompose_fallback_on_llm_failure(self, mock_audit, orchestrator):
        """When the LLM raises, fallback produces a single subtask."""
        mock_router = MagicMock()
        mock_router.invoke.side_effect = RuntimeError("Bedrock unavailable")
        orchestrator._llm_router = mock_router

        wf = orchestrator.decompose_task(
            "Generate compliance report",
            project_id="proj-fallback",
        )

        assert len(wf.subtasks) == 1
        st = list(wf.subtasks.values())[0]
        assert st.agent_id == "builder-agent"
        assert st.skill_id == "generate_code"
        assert st.depends_on == []
        assert "compliance report" in st.description.lower()

    @patch("tools.agent.team_orchestrator._audit_log")
    def test_decompose_persists_to_db(self, mock_audit, orchestrator, orch_db):
        """After decompose, workflow and subtasks exist in the database."""
        mock_router = MagicMock()
        mock_router.invoke.side_effect = _mock_llm_invoke
        orchestrator._llm_router = mock_router

        wf = orchestrator.decompose_task("task", project_id="proj-1")

        conn = sqlite3.connect(str(orch_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM agent_workflows WHERE id = ?", (wf.id,)).fetchone()
        assert row is not None
        assert dict(row)["status"] == "pending"

        subtask_rows = conn.execute(
            "SELECT * FROM agent_subtasks WHERE workflow_id = ?", (wf.id,)
        ).fetchall()
        assert len(subtask_rows) == 3
        conn.close()


# ---------------------------------------------------------------------------
# TestExecuteWorkflow
# ---------------------------------------------------------------------------

class TestExecuteWorkflow:
    """execute_workflow: DAG-based parallel subtask execution."""

    @patch("tools.agent.team_orchestrator._audit_log")
    def test_all_subtasks_completed(self, mock_audit, orchestrator):
        """When all subtasks succeed, workflow status is 'completed'."""
        wf = Workflow(id="wf-exec-1", name="Test exec", project_id="proj-1")
        wf.subtasks["s1"] = Subtask(id="s1", agent_id="a", skill_id="sk1", depends_on=[])
        wf.subtasks["s2"] = Subtask(id="s2", agent_id="a", skill_id="sk2", depends_on=["s1"])
        orchestrator._persist_workflow(wf)

        # Mock _execute_subtask to instantly succeed
        def _mock_exec(subtask, context):
            subtask.status = "completed"
            subtask.output_data = {"result": f"done-{subtask.id}"}
            subtask.duration_ms = 10
            return subtask

        orchestrator._execute_subtask = _mock_exec
        result = orchestrator.execute_workflow(wf, timeout=30)

        assert result.status == "completed"
        assert result.aggregated_result is not None
        assert result.aggregated_result["summary"]["completed"] == 2
        assert result.aggregated_result["summary"]["failed"] == 0

    @patch("tools.agent.team_orchestrator._audit_log")
    def test_failed_subtask_marks_workflow_failed(self, mock_audit, orchestrator):
        """When all subtasks fail, workflow status is 'failed'."""
        wf = Workflow(id="wf-fail-1", name="Fail test", project_id="proj-1")
        wf.subtasks["s1"] = Subtask(id="s1", agent_id="a", skill_id="sk", depends_on=[])
        orchestrator._persist_workflow(wf)

        def _mock_exec(subtask, context):
            subtask.status = "failed"
            subtask.error_message = "Agent unreachable"
            subtask.duration_ms = 5
            return subtask

        orchestrator._execute_subtask = _mock_exec
        result = orchestrator.execute_workflow(wf, timeout=30)

        assert result.status == "failed"
        assert result.aggregated_result["summary"]["failed"] == 1

    @patch("tools.agent.team_orchestrator._audit_log")
    def test_partial_completion(self, mock_audit, orchestrator):
        """Mix of completed and failed subtasks yields partially_completed."""
        wf = Workflow(id="wf-part-1", name="Partial", project_id="proj-1")
        wf.subtasks["s1"] = Subtask(id="s1", agent_id="a", skill_id="sk1", depends_on=[])
        wf.subtasks["s2"] = Subtask(id="s2", agent_id="a", skill_id="sk2", depends_on=[])
        orchestrator._persist_workflow(wf)

        call_count = {"n": 0}

        def _mock_exec(subtask, context):
            call_count["n"] += 1
            if subtask.id == "s1":
                subtask.status = "completed"
                subtask.output_data = {"ok": True}
            else:
                subtask.status = "failed"
                subtask.error_message = "Something broke"
            subtask.duration_ms = 5
            return subtask

        orchestrator._execute_subtask = _mock_exec
        result = orchestrator.execute_workflow(wf, timeout=30)

        assert result.status == "partially_completed"


# ---------------------------------------------------------------------------
# TestWorkflowPersistence
# ---------------------------------------------------------------------------

class TestWorkflowPersistence:
    """_persist_workflow: upsert workflow and subtasks to SQLite."""

    def test_persist_and_reload(self, orchestrator, orch_db):
        """Persisted workflow can be retrieved via get_workflow_status."""
        wf = Workflow(id="wf-persist-1", name="Persist test", project_id="proj-p")
        st = Subtask(
            id="st-p1",
            agent_id="builder-agent",
            skill_id="generate_code",
            description="Build something",
            depends_on=[],
            status="completed",
            output_data={"artifact": "main.py"},
            duration_ms=150,
        )
        wf.subtasks[st.id] = st
        wf.status = "completed"
        wf.aggregated_result = {"summary": {"total": 1, "completed": 1}}
        orchestrator._persist_workflow(wf)

        status = orchestrator.get_workflow_status("wf-persist-1")
        assert status["id"] == "wf-persist-1"
        assert status["status"] == "completed"
        assert len(status["subtasks"]) == 1
        assert status["subtasks"][0]["id"] == "st-p1"
        assert status["subtasks"][0]["output_data"] == {"artifact": "main.py"}

    def test_persist_upsert_updates_status(self, orchestrator, orch_db):
        """A second persist call updates status via ON CONFLICT."""
        wf = Workflow(id="wf-upsert-1", name="Upsert", project_id="proj-u")
        wf.status = "pending"
        orchestrator._persist_workflow(wf)

        wf.status = "running"
        orchestrator._persist_workflow(wf)

        status = orchestrator.get_workflow_status("wf-upsert-1")
        assert status["status"] == "running"


# ---------------------------------------------------------------------------
# TestWorkflowStatus
# ---------------------------------------------------------------------------

class TestWorkflowStatus:
    """get_workflow_status: query workflow and subtask details from DB."""

    def test_not_found_returns_empty(self, orchestrator):
        result = orchestrator.get_workflow_status("wf-nonexistent")
        assert result == {}

    def test_json_fields_deserialized(self, orchestrator, orch_db):
        """depends_on, input_data, output_data are deserialized from JSON strings."""
        wf = Workflow(id="wf-json-1", name="JSON fields")
        st = Subtask(
            id="st-j1",
            agent_id="builder-agent",
            skill_id="generate_code",
            depends_on=["st-x", "st-y"],
            input_data={"key": "val"},
            output_data={"out": 42},
        )
        wf.subtasks[st.id] = st
        orchestrator._persist_workflow(wf)

        status = orchestrator.get_workflow_status("wf-json-1")
        sub = status["subtasks"][0]
        assert sub["depends_on"] == ["st-x", "st-y"]
        assert sub["input_data"] == {"key": "val"}
        assert sub["output_data"] == {"out": 42}

    def test_aggregated_result_deserialized(self, orchestrator, orch_db):
        """aggregated_result JSON is parsed back to a dict."""
        wf = Workflow(id="wf-agg-1", name="Aggregated")
        wf.aggregated_result = {"summary": {"total": 3, "completed": 2, "failed": 1}}
        orchestrator._persist_workflow(wf)

        status = orchestrator.get_workflow_status("wf-agg-1")
        assert isinstance(status["aggregated_result"], dict)
        assert status["aggregated_result"]["summary"]["total"] == 3


# ---------------------------------------------------------------------------
# TestBlockDownstream
# ---------------------------------------------------------------------------

class TestBlockDownstream:
    """_block_downstream: marks dependent subtasks as blocked on failure."""

    def test_block_downstream_sets_status(self, orchestrator):
        """_block_downstream marks pending dependents as blocked."""
        wf = Workflow(id="wf-block-1", name="Block test", project_id="proj-b")
        wf.subtasks["s1"] = Subtask(id="s1", agent_id="a", skill_id="sk", depends_on=[], status="failed")
        wf.subtasks["s2"] = Subtask(id="s2", agent_id="a", skill_id="sk", depends_on=["s1"], status="pending")
        wf.subtasks["s3"] = Subtask(id="s3", agent_id="a", skill_id="sk", depends_on=[], status="completed")
        orchestrator._persist_workflow(wf)

        orchestrator._block_downstream("s1", wf)

        assert wf.subtasks["s2"].status == "blocked"
        assert wf.subtasks["s3"].status == "completed"  # independent, unchanged


# CUI // SP-CTI
