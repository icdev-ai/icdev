# CUI // SP-CTI
"""Claude Code's prompt-cache counters survive the CLI bridge (cch-obs-04).

THE DEFECT. Claude Code's result JSON reports `cache_read_input_tokens` and
`cache_creation_input_tokens`. `subprocess_backend` parsed `usage` and took only
input/output, `cli_llm_jobs` had nowhere to put them, the LLMResponse never carried them,
and `router._log_telemetry` — which reads them off the response with `getattr(..., 0)` —
stored 0 for all 626 claude-cli calls on this board.

The cache dashboard then classified the provider `unreported`, meaning "the transport
reports no counters". The transport reports them fine; this pipeline threw them away. And
`args/cache_effectiveness.yaml` DECLARED `reports_cache_tokens: false`, so the claim and the
(missing) evidence agreed with each other and nothing could contradict either.

The tests below follow one call through every hop, because the value was lost at a different
hop than the one that reported it.
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import subprocess as _sp
import time

import pytest
import yaml

from tools.llm.cli_bridge import job_store, subprocess_backend

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# The DDL and fixtures below mirror tests/llm/test_cli_backends.py rather than importing
# from it: `job_db` is module-local there, `tests/llm` is not a package so a relative import
# fails outright, and coupling two test modules' collection order to share a fixture is worse
# than a short duplicated CREATE TABLE. The two cache columns below are the ones migration
# 20260827223358 adds; the tests in this file assert on them, so a fixture that lagged the
# migration would fail here rather than silently pass.
_CLI_LLM_JOBS_DDL = """
CREATE TABLE cli_llm_jobs (
    id             TEXT PRIMARY KEY,
    function       TEXT NOT NULL DEFAULT '',
    prompt         TEXT NOT NULL DEFAULT '',
    system_prompt  TEXT DEFAULT '',
    model_id       TEXT,
    backend        TEXT DEFAULT 'auto',
    status         TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'running', 'done', 'error')),
    result         TEXT,
    error          TEXT,
    context_id     TEXT,
    input_tokens   INTEGER DEFAULT 0,
    output_tokens  INTEGER DEFAULT 0,
    cache_read_input_tokens     INTEGER DEFAULT 0,
    cache_creation_input_tokens INTEGER DEFAULT 0,
    tenant_id      TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at     TEXT,
    updated_at     TEXT,
    claimed_at     TEXT,
    completed_at   TEXT
);
"""


@pytest.fixture
def job_db(tmp_path, monkeypatch):
    """Real SQLite cli_llm_jobs wired into job_store.get_connection."""
    db_path = tmp_path / "cli_jobs.db"
    seed = sqlite3.connect(str(db_path))
    seed.executescript(_CLI_LLM_JOBS_DDL)
    seed.commit()
    seed.close()
    # job_store authors %s placeholders for PostgreSQL and relies on StorageConnection to
    # rewrite them; a bare sqlite3 connection drops that layer.
    from _sql_compat import connect as _tconnect

    monkeypatch.setattr(job_store, "get_connection", lambda: _tconnect(db_path))
    return db_path


@pytest.fixture(autouse=True)
def _binary_on_path(monkeypatch):
    monkeypatch.setattr(subprocess_backend.shutil, "which", lambda _b: "/usr/bin/claude")


def _stub_run(stdout="", returncode=0):
    def _run(cmd, **kwargs):
        return _sp.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")

    return _run


def _wait_terminal(job_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = job_store.get_job(job_id)
        if row and row["status"] in ("done", "error"):
            return row
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} never reached a terminal state")

USAGE = {
    "input_tokens": 100,
    "output_tokens": 20,
    "cache_read_input_tokens": 4000,
    "cache_creation_input_tokens": 500,
}
PAYLOAD = json.dumps({"result": "ok", "is_error": False, "usage": USAGE})


# ---------------------------------------------------------------------------
# hop 1 — the agent adapter's envelope
# ---------------------------------------------------------------------------


def test_the_agent_adapter_carries_the_counters_instead_of_summing_them():
    """It used to return input_tokens = 100 + 4000 + 500 and nothing else."""
    from tools.agents.adapters.claude_cli import _parse_cli_json

    _text, env = _parse_cli_json(PAYLOAD)
    assert env["cache_read_input_tokens"] == 4000
    assert env["cache_creation_input_tokens"] == 500
    assert env["input_tokens"] == 100, (
        "input_tokens must stay RAW. Anthropic's accounting is DISJOINT — it already "
        "excludes cache reads and writes, and by_provider._split_tokens adds them back. "
        "Pre-summing here double-counts every cached token."
    )
    # The old summed meaning is still available under its own name.
    assert env["prompt_tokens_total"] == 4600


def test_the_adapter_degrades_to_zero_when_usage_is_absent():
    from tools.agents.adapters.claude_cli import _parse_cli_json

    _text, env = _parse_cli_json(json.dumps({"result": "ok"}))
    assert env["cache_read_input_tokens"] == 0
    assert env["cache_creation_input_tokens"] == 0


# ---------------------------------------------------------------------------
# hop 2 — the subprocess backend parses them out of the CLI's stdout
# ---------------------------------------------------------------------------


def test_the_backend_persists_the_counters_on_the_job(job_db, monkeypatch):
    """The hop the value was actually lost at."""
    job_id = job_store.create_job(function="f", prompt="p", backend="subprocess")
    monkeypatch.setattr(subprocess_backend.subprocess, "run", _stub_run(stdout=PAYLOAD))
    subprocess_backend.dispatch(job_id, "subprocess")
    row = _wait_terminal(job_id)

    assert row["status"] == "done"
    assert row["input_tokens"] == 100, "raw, not summed"
    assert row["cache_read_input_tokens"] == 4000
    assert row["cache_creation_input_tokens"] == 500


def test_unparseable_stdout_still_completes_with_zero_counters(job_db, monkeypatch):
    """The except branch has to define them, or completion raises NameError."""
    job_id = job_store.create_job(function="f", prompt="p", backend="subprocess")
    monkeypatch.setattr(subprocess_backend.subprocess, "run", _stub_run(stdout="not json"))
    subprocess_backend.dispatch(job_id, "subprocess")
    row = _wait_terminal(job_id)

    assert row["status"] == "done"
    assert row["cache_read_input_tokens"] == 0
    assert row["cache_creation_input_tokens"] == 0


# ---------------------------------------------------------------------------
# hop 3 — the provider puts them on the response the router logs
# ---------------------------------------------------------------------------


def test_the_response_carries_the_counters_for_the_telemetry_writer():
    """`router._log_telemetry` reads these with getattr(response, ..., 0).

    A response that never sets them records 0, which is exactly how 626 calls reported no
    caching while the CLI was reporting it on every one.
    """
    from tools.llm.cli_bridge.cli_provider import CLILLMProvider

    provider = CLILLMProvider()
    job = {
        "result": "ok", "model_id": "claude-x", "input_tokens": 100, "output_tokens": 20,
        "cache_read_input_tokens": 4000, "cache_creation_input_tokens": 500,
    }
    response = provider._response_from_job(job, "claude-x", 0.0)
    assert getattr(response, "cache_read_input_tokens", None) == 4000
    assert getattr(response, "cache_creation_input_tokens", None) == 500
    assert response.input_tokens == 100


def test_a_job_without_the_columns_reads_zero_not_an_attribute_error():
    """Rows written before the migration have no such keys."""
    from tools.llm.cli_bridge.cli_provider import CLILLMProvider

    response = CLILLMProvider()._response_from_job(
        {"result": "ok", "input_tokens": 5, "output_tokens": 1}, "m", 0.0
    )
    assert response.cache_read_input_tokens == 0
    assert response.cache_creation_input_tokens == 0


# ---------------------------------------------------------------------------
# the declaration must match what the pipeline now reports
# ---------------------------------------------------------------------------


def _claim(provider: str) -> dict:
    cfg = yaml.safe_load(
        (REPO_ROOT / "args" / "cache_effectiveness.yaml").read_text(encoding="utf-8")
    )
    return (cfg.get("providers") or {}).get(provider) or {}


def test_claude_cli_is_declared_to_report_cache_tokens():
    """The self-sealing half of the defect.

    `reports_cache_tokens: false` made every zero read as `unreported` rather than as a
    measured 0%, so the declaration could never be contradicted by the data it was wrong
    about. Now that the counters flow, the claim has to say so.
    """
    claim = _claim("claude-cli")
    assert claim.get("reports_cache_tokens") is True
    assert claim.get("token_accounting") == "disjoint", (
        "Anthropic reports input_tokens DISJOINT from cache tokens; declaring `inclusive` "
        "would subtract cached tokens that were never in the input count"
    )


def test_claude_cli_stays_unpriced():
    """It is the SUBSCRIPTION path, not the metered API.

    Pricing it at the API rate card would invent a dollar saving for spend that never
    happened — the exact fabrication the per-provider split exists to remove.
    """
    assert _claim("claude-cli").get("usd_basis") == "unpriced"


@pytest.mark.parametrize("provider", ["anthropic", "bedrock"])
def test_the_metered_anthropic_paths_are_unchanged(provider):
    """This card touches the CLI bridge only; the API providers must not move."""
    claim = _claim(provider)
    assert claim.get("reports_cache_tokens") is True
    assert claim.get("token_accounting") == "disjoint"
    assert claim.get("usd_basis") == "priced"


def test_a_disjoint_claim_is_not_double_counted():
    """The reason input_tokens is left raw, asserted against the real splitter."""
    from tools.cache_savings.by_provider import _split_tokens

    total, uncached = _split_tokens("disjoint", 100, 4000, 500)
    assert total == 4600 and uncached == 100
    # If the backend had pre-summed, `input_tokens` would already be 4600 and the splitter
    # would report 9100 — every cached token counted twice.
    inflated, _ = _split_tokens("disjoint", 4600, 4000, 500)
    assert inflated == 9100
