# CUI // SP-CTI
"""penta-gd-02 — AI League runnable-engine regression tests.

Covers:
  * import-smoke: every tools/gameday/*.py imports cleanly (pack.py, which
    imported the never-built tools.ai_game_engine generic engine, is gone;
    game_master no longer imports GameSession).
  * end-to-end: GameMaster.run_tournament() drives a full round via
    RoundManager with a MOCKED LLM router (no network) and records judge
    evaluations + LLMOps events, and marks the tournament completed.

Mocks the LLM router via importlib + setattr on tools.llm.router (the module
base_agent / judge_agent import at call time). Uses the shared conftest schema
(gd_ai_* DDL) + storage translate layer — no raw sqlite3 in the query path.
"""

from __future__ import annotations

import importlib
import json
import pathlib

import pytest

GAMEDAY_DIR = pathlib.Path(__file__).resolve().parents[1] / "tools" / "gameday"

_MOCK_JSON = json.dumps(
    {
        "quality_score": 0.85,
        "innovation_score": 0.75,
        "ethics_score": 1.0,
        "adversarial_score": 0.8,
        "compliance_score": 0.7,
        "judge_notes": "mock evaluation",
        "training_pairs": [{"prompt": "p", "completion": "c"}],
        "attack_plan": "mock plan",
        "findings": ["f1"],
        "confidence": 0.9,
    }
)


# ---------------------------------------------------------------------------
# import-smoke
# ---------------------------------------------------------------------------

def test_pack_module_removed():
    assert not (GAMEDAY_DIR / "pack.py").exists(), "dead pack.py should be deleted"


@pytest.mark.parametrize(
    "mod",
    sorted(p.stem for p in GAMEDAY_DIR.glob("*.py") if p.stem != "__init__"),
)
def test_gameday_module_imports(mod, monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    importlib.import_module(f"tools.gameday.{mod}")


def test_game_master_does_not_import_missing_engine():
    src = (GAMEDAY_DIR / "game_master.py").read_text(encoding="utf-8")
    import_lines = [
        ln for ln in src.splitlines()
        if ("import " in ln) and ("ai_game_engine" in ln or "GameSession" in ln)
    ]
    assert not import_lines, f"game_master must not import the missing engine: {import_lines}"


# ---------------------------------------------------------------------------
# end-to-end tournament with a mocked router
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, content: str):
        self.content = content
        self.input_tokens = 11
        self.output_tokens = 22
        self.model_id = "mock-model"
        self.provider = "mock"


class _FakeProvider:
    def invoke(self, request, model_id, cfg):
        return _FakeResp(_MOCK_JSON)


class _FakeRouter:
    def __init__(self, *a, **k):
        pass

    def get_provider_for_function(self, function):
        return _FakeProvider(), "mock-model", {}


@pytest.fixture
def league_env(tmp_path, monkeypatch):
    """Seed a temp SQLite DB with the shared schema and mock the LLM router."""
    from tests.conftest import MINIMAL_ICDEV_SCHEMA

    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.executescript(MINIMAL_ICDEV_SCHEMA)
    conn.commit()
    conn.close()

    # Mock the router on the module both base_agent and judge_agent import from.
    router_mod = importlib.import_module("tools.llm.router")
    monkeypatch.setattr(router_mod, "LLMRouter", _FakeRouter)

    # Keep the round deterministic: skip the optional agent-loop path so every
    # member falls through to GameDayAgent.run() (the mocked router).
    team_runner = importlib.import_module("tools.gameday.team_runner")
    monkeypatch.setattr(team_runner, "_AGENT_LOOP_AVAILABLE", False)

    return db_path


def test_ai_league_runs_end_to_end_and_records_evals(league_env):
    from tools.gameday.game_master import GameMaster
    from tools.db.storage import get_connection

    gm = GameMaster(tournament_name="pytest-league", round_count=1)
    summary = gm.run_tournament()

    assert summary["status"] == "completed"
    assert summary["rounds"] == 1
    tid = summary["tournament_id"]

    conn = get_connection()

    # Tournament marked completed.
    status = conn.execute(
        "SELECT status FROM gd_ai_tournaments WHERE id = %s", (tid,)
    ).fetchone()["status"]
    assert status == "completed"

    # Judge evals recorded for the round (one per team, 4 teams).
    evals = conn.execute(
        """SELECT je.team_key, je.total_score
           FROM gd_ai_judge_evals je
           JOIN gd_ai_rounds r ON r.id = je.round_id
           WHERE r.tournament_id = %s""",
        (tid,),
    ).fetchall()
    assert len(evals) >= 1, "judge must record at least one evaluation"
    team_keys = {row["team_key"] for row in evals}
    assert team_keys, "evals must be attributed to teams"

    # LLMOps events recorded (proves inference went through the router path).
    n_events = conn.execute(
        "SELECT COUNT(*) AS n FROM gd_ai_llmops_events WHERE tournament_id = %s", (tid,)
    ).fetchone()["n"]
    assert n_events >= 1

    # No model row carries a fictitious hardcoded tag.
    models = [
        row["model"]
        for row in conn.execute(
            "SELECT DISTINCT model FROM gd_ai_llmops_events WHERE tournament_id = %s", (tid,)
        ).fetchall()
    ]
    assert "qwen3.5:9b" not in models
    assert "gemma4:e4b" not in models


def test_run_tournament_persists_failure_state(league_env, monkeypatch):
    """A round that blows up must mark the tournament aborted (not silent)."""
    from tools.gameday.game_master import GameMaster
    from tools.db.storage import get_connection
    import tools.gameday.round_manager as rm_mod

    def _boom(self, round_num, scenario):
        raise RuntimeError("simulated round failure")

    monkeypatch.setattr(rm_mod.RoundManager, "run_round", _boom)

    gm = GameMaster(tournament_name="pytest-fail", round_count=1)
    with pytest.raises(RuntimeError):
        gm.run_tournament()

    conn = get_connection()
    row = conn.execute(
        "SELECT status, config_json FROM gd_ai_tournaments ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["status"] == "aborted"
    cfg = json.loads(row["config_json"] or "{}")
    assert "simulated round failure" in cfg.get("error", "")
