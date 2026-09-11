# CUI // SP-CTI
"""xrv-cost-01 -- the transcript cost reader, over synthetic transcripts only.

No host transcript is read: every test writes its own JSONL under ``tmp_path``
and passes an explicit pricing config, so the numbers here are deterministic
and the pricing table on this host (which prices NO Claude Code model) cannot
change a verdict.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.cost import session_cost as sc  # noqa: E402

CONFIG = {
    "models": {
        "priced-api": {
            "provider": "anthropic", "model_id": "priced-model-1",
            "pricing": {"input_per_1k": 0.003, "output_per_1k": 0.015},
        },
        "cloud-no-price": {
            "provider": "anthropic", "model_id": "cloud-unpriced-1",
            "pricing": {"input_per_1k": 0.0, "output_per_1k": 0.0},
        },
        "local-free": {
            "provider": "ollama", "model_id": "qwen-local",
            "pricing": {"input_per_1k": 0.0, "output_per_1k": 0.0},
        },
    }
}


def record(message_id, model, *, content=None, usage=None, ts="2026-09-11T10:00:00Z",
           record_type="assistant"):
    return {
        "type": record_type,
        "timestamp": ts,
        "cwd": "C:\\AI\\ICDev",
        "uuid": f"uuid-{message_id}",
        "message": {
            "id": message_id,
            "role": "assistant",
            "model": model,
            "content": content or [{"type": "text", "text": "ok"}],
            "usage": usage or {
                "input_tokens": 1000, "cache_read_input_tokens": 5000,
                "cache_creation_input_tokens": 2000, "output_tokens": 500,
            },
        },
    }


def write_transcript(path: Path, records, *, torn_tail: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(r) for r in records) + "\n"
    if torn_tail:
        text += '{"type": "assistant", "message": {"id": "msg_torn", "usa'
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def root(tmp_path):
    return tmp_path / "projects"


# ── reading ───────────────────────────────────────────────────────────────


def test_a_message_written_across_several_lines_is_counted_once(root):
    """Claude Code emits thinking / text / tool_use for ONE message on
    separate lines, each carrying the same id and usage."""
    path = write_transcript(root / "C--AI-ICDev" / "s1.jsonl", [
        record("msg_1", "priced-model-1",
               content=[{"type": "thinking", "thinking": "..."}]),
        record("msg_1", "priced-model-1",
               content=[{"type": "text", "text": "Editing now."}]),
        record("msg_1", "priced-model-1",
               content=[{"type": "tool_use", "name": "Edit",
                         "input": {"file_path": "x.py"}}],
               usage={"input_tokens": 1000, "cache_read_input_tokens": 5000,
                      "cache_creation_input_tokens": 2000, "output_tokens": 900}),
    ])
    usages = list(sc.iter_usage([path]))
    assert len(usages) == 1
    assert usages[0]["message_id"] == "msg_1"
    assert usages[0]["output_tokens"] == 900            # the LAST line wins
    assert usages[0]["session"] == "s1"
    assert usages[0]["cwd"] == "C:\\AI\\ICDev"
    # content blocks are UNIONED across lines: the tool_use on line 3 decides
    assert usages[0]["task_type"] == "coding"


def test_a_torn_final_line_is_skipped_not_raised(root):
    path = write_transcript(root / "C--AI-ICDev" / "s1.jsonl",
                            [record("msg_1", "priced-model-1")], torn_tail=True)
    usages = list(sc.iter_usage([path]))
    assert [u["message_id"] for u in usages] == ["msg_1"]


def test_non_assistant_records_and_records_without_usage_are_ignored(root):
    path = write_transcript(root / "C--AI-ICDev" / "s1.jsonl", [
        record("msg_u", "priced-model-1", record_type="user"),
        {"type": "assistant", "message": {"id": "msg_nousage", "model": "x",
                                          "content": []}},
        {"type": "assistant", "message": "not a dict"},
        "not even an object",
        record("msg_1", "priced-model-1"),
    ])
    assert [u["message_id"] for u in sc.iter_usage([path])] == ["msg_1"]


# ── pricing ───────────────────────────────────────────────────────────────


def test_price_table_resolves_transcript_model_ids_through_the_declared_table():
    table = sc.price_table(CONFIG)
    assert table["priced-model-1"]["basis"] == "priced"
    assert table["priced-model-1"]["input_per_1k"] == 0.003
    assert table["cloud-unpriced-1"]["basis"] == "unpriced"
    assert table["qwen-local"]["basis"] == "local_zero"
    assert "never-declared" not in table


def test_an_unknown_model_prices_to_none_never_zero(root):
    path = write_transcript(root / "C--AI-ICDev" / "s1.jsonl", [
        record("msg_1", "never-declared-model"),
    ])
    report = sc.survey(root=root, transcripts=[path], config=CONFIG)
    assert report["unpriced"] == 1
    assert report["unpriced_models"] == ["never-declared-model"]
    row = report["sessions"][0]
    assert row["cost_usd"] is None
    assert row["cost_basis"] == "unpriced"
    assert row["by_model"]["never-declared-model"]["cost_usd"] is None
    assert report["totals"]["cost_usd"] is None
    # the tokens are still reported raw
    assert row["by_model"]["never-declared-model"]["cache_read_input_tokens"] == 5000


def test_a_priced_model_prices_input_and_output_only(root):
    path = write_transcript(root / "C--AI-ICDev" / "s1.jsonl", [
        record("msg_1", "priced-model-1"),
    ])
    report = sc.survey(root=root, transcripts=[path], config=CONFIG)
    row = report["sessions"][0]
    # 1000 in * 0.003/1k + 500 out * 0.015/1k = 0.003 + 0.0075; cache tokens excluded
    assert row["cost_usd"] == pytest.approx(0.0105)
    assert row["cost_basis"] == "priced"
    assert row["cache_read_input_tokens"] == 5000
    assert row["cache_creation_input_tokens"] == 2000
    assert report["unpriced"] == 0
    assert report["by_model"]["priced-model-1"]["cost_usd"] == pytest.approx(0.0105)


def test_a_local_model_is_zero_by_basis_not_by_absence(root):
    path = write_transcript(root / "C--AI-ICDev" / "s1.jsonl", [
        record("msg_1", "qwen-local"),
    ])
    report = sc.survey(root=root, transcripts=[path], config=CONFIG)
    row = report["sessions"][0]
    assert row["cost_usd"] == 0.0
    assert row["cost_basis"] == "local_zero"
    assert report["unpriced"] == 0


def test_a_session_mixing_priced_and_unpriced_models_is_a_lower_bound(root):
    path = write_transcript(root / "C--AI-ICDev" / "s1.jsonl", [
        record("msg_1", "priced-model-1"),
        record("msg_2", "cloud-unpriced-1"),
    ])
    report = sc.survey(root=root, transcripts=[path], config=CONFIG)
    row = report["sessions"][0]
    assert row["cost_basis"] == "partial"
    assert row["cost_usd"] == pytest.approx(0.0105)
    assert row["unpriced_messages"] == 1
    assert report["unpriced"] == 1
    assert report["unpriced_models"] == ["cloud-unpriced-1"]


# ── the empty denominator ─────────────────────────────────────────────────


def test_zero_assistant_records_report_none_totals_not_zero(root):
    path = write_transcript(root / "C--AI-ICDev" / "s1.jsonl", [
        record("msg_u", "priced-model-1", record_type="user"),
    ])
    report = sc.survey(root=root, transcripts=[path], config=CONFIG)
    assert report["state"] == "unmeasurable"
    assert report["sessions_scanned"] == 1
    assert report["sessions_with_usage"] == 0
    totals = report["totals"]
    assert totals["messages"] is None
    assert totals["cost_usd"] is None
    assert totals["input_tokens"] is None
    assert totals["output_tokens"] is None
    assert totals["cache_read_share_pct"] is None
    assert report["unpriced"] is None


def test_rate_is_none_over_an_empty_denominator():
    assert sc._rate(0, 0) is None
    assert sc._rate(5, 0) is None
    assert sc._rate(1, 4) == 25.0


# ── classification ────────────────────────────────────────────────────────


@pytest.mark.parametrize("tools,text,expected", [
    ([{"name": "Agent", "command": ""}], "", "delegation"),
    ([{"name": "Bash", "command": "git status"}], "", "git_ops"),
    ([{"name": "Bash", "command": "cd x && gh pr create"}], "", "git_ops"),
    ([{"name": "Bash", "command": "pytest tests/ -q"}], "", "testing"),
    ([{"name": "Edit", "command": ""}], "", "coding"),
    ([{"name": "Bash", "command": "sed -i 's/a/b/' x.py"}], "", "coding"),
    ([{"name": "Read", "command": ""}], "", "exploration"),
    ([{"name": "Bash", "command": "grep -n foo tools/x.py"}], "", "exploration"),
    ([{"name": "Read", "command": ""}], "Here is the traceback", "debugging"),
    ([{"name": "TodoWrite", "command": ""}], "", "planning"),
    ([], "Sure, here is what that means.", "conversation"),
    ([{"name": "Bash", "command": "python tools/x.py --json"}], "", "general"),
    ([], "", "general"),
])
def test_task_type_rules_are_deterministic(tools, text, expected):
    assert sc.classify_task_type(tools, text) == expected


def test_every_rule_names_a_declared_task_type():
    for rule in sc.TASK_TYPE_RULES:
        assert rule["task_type"] in sc.TASK_TYPES
    assert {"conversation", "general"} <= set(sc.TASK_TYPES)


def test_session_task_type_is_the_majority_and_totals_split_by_type(root):
    path = write_transcript(root / "C--AI-ICDev" / "s1.jsonl", [
        record("msg_1", "priced-model-1",
               content=[{"type": "tool_use", "name": "Edit", "input": {}}]),
        record("msg_2", "priced-model-1",
               content=[{"type": "tool_use", "name": "Write", "input": {}}]),
        record("msg_3", "priced-model-1",
               content=[{"type": "tool_use", "name": "Bash",
                         "input": {"command": "pytest -q"}}]),
    ])
    report = sc.survey(root=root, transcripts=[path], config=CONFIG)
    row = report["sessions"][0]
    assert row["task_type"] == "coding"
    assert row["task_type_messages"] == {"coding": 2, "testing": 1}
    assert report["by_task_type"]["coding"]["messages"] == 2
    assert report["by_task_type"]["testing"]["messages"] == 1
    assert report["by_task_type"]["testing"]["cost_usd"] == pytest.approx(0.0105)


# ── the survey and the CLI ────────────────────────────────────────────────


def test_survey_reuses_the_transcript_seam_for_window_and_project(root):
    write_transcript(root / "C--AI-ICDev" / "s1.jsonl",
                     [record("msg_1", "priced-model-1")])
    write_transcript(root / "C--other-repo" / "s2.jsonl",
                     [record("msg_2", "priced-model-1")])
    report = sc.survey(root=root, since_days=30, project="ICDev", config=CONFIG)
    assert report["sessions_scanned"] == 1
    assert [r["session"] for r in report["sessions"]] == ["s1"]
    everything = sc.survey(root=root, since_days=30, config=CONFIG)
    assert everything["sessions_scanned"] == 2


def test_an_absent_root_exits_2_and_says_unmeasurable(tmp_path, capsys):
    rc = sc.main(["--survey", "--root", str(tmp_path / "nope"), "--json"])
    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "unmeasurable"
    assert "transcript root not found" in payload["error"]


def test_an_unknown_session_exits_2(root, capsys):
    root.mkdir(parents=True)
    rc = sc.main(["--session", "does-not-exist", "--root", str(root), "--json"])
    assert rc == 2
    assert json.loads(capsys.readouterr().out)["state"] == "unmeasurable"


def test_session_flag_reads_one_transcript(root, capsys, monkeypatch):
    monkeypatch.setattr(sc, "_load_config", lambda: CONFIG)
    write_transcript(root / "C--AI-ICDev" / "abc.jsonl",
                     [record("msg_1", "priced-model-1"),
                      record("msg_1", "priced-model-1")])     # duplicate id
    write_transcript(root / "C--AI-ICDev" / "zzz.jsonl",
                     [record("msg_9", "priced-model-1")])
    rc = sc.main(["--session", "abc", "--root", str(root), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["sessions_scanned"] == 1
    assert payload["sessions"][0]["session"] == "abc"
    assert payload["sessions"][0]["messages"] == 1
    assert payload["sessions"][0]["cost_usd"] == pytest.approx(0.0105)


def test_survey_cli_prints_per_session_cost_by_model(root, capsys, monkeypatch):
    monkeypatch.setattr(sc, "_load_config", lambda: CONFIG)
    write_transcript(root / "C--AI-ICDev" / "s1.jsonl",
                     [record("msg_1", "priced-model-1"),
                      record("msg_2", "never-declared-model")])
    rc = sc.main(["--survey", "--since-days", "7", "--project", "ICDev",
                  "--root", str(root), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["sessions_scanned"] == 1
    assert payload["unpriced"] == 1
    by_model = payload["sessions"][0]["by_model"]
    assert by_model["priced-model-1"]["cost_usd"] == pytest.approx(0.0105)
    assert by_model["never-declared-model"]["cost_usd"] is None
    assert payload["priced_tokens"].startswith("input_tokens + output_tokens only")


def test_human_report_renders_without_a_priced_model(root, capsys, monkeypatch):
    monkeypatch.setattr(sc, "_load_config", lambda: CONFIG)
    write_transcript(root / "C--AI-ICDev" / "s1.jsonl",
                     [record("msg_1", "never-declared-model")])
    assert sc.main(["--survey", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "unpriced messages 1" in out
    assert "never-declared-model" in out
