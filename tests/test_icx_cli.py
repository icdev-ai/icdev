# CUI // SP-CTI
"""icx-cli-01 -- AI-Ify and Process-Ify reachable as JSON CLIs, for ICDEV[OS]'s application twin.

* ``python -m tools.aiify.engine scan`` returns the scan summary with every opportunity, its score and its
  disqualifiers, and WITHOUT ``--promote`` seeds no kanban card and registers no innovation signal;
* ``python -m tools.workflow_canvas.processify --from-doc`` returns the same draft the dashboard route builds (one
  implementation), labelled modelled with basis document; ``--observed`` builds the deterministic draft, no model.
"""
from __future__ import annotations

import json

import pytest

from tests.test_penta_aiify_scan import _MOCK_PATTERNS, aiify_env  # noqa: F401 -- `aiify_env` is a fixture


def _src(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "alpha.py").write_text("x = 1\n", encoding="utf-8")
    return src


def test_a_cli_scan_reports_every_opportunity_and_seeds_nothing(aiify_env, tmp_path, monkeypatch, capsys):  # noqa: F811
    import tools.aiify.engine as engine
    monkeypatch.setattr(engine, "detect_patterns", lambda path: [dict(p) for p in _MOCK_PATTERNS])

    def boom(*a, **k):
        raise AssertionError("a CLI scan must not seed the board")
    for name in ("_promote_top_opportunities", "_promote_phase_opportunities", "_register_innovation_signals"):
        monkeypatch.setattr(engine, name, boom)
    assert engine.main(["scan", "--input-ref", str(_src(tmp_path)), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "completed" and out["kanban_promoted"] == 0 and out["phase_promoted"] == 0
    opps = out["opportunities"]
    assert [o["module_path"] for o in opps] == [p["module_path"] for p in _MOCK_PATTERNS]
    assert all(o["score"]["composite_score"] is not None and isinstance(o["disqualifiers"], list) for o in opps)


def test_the_dashboard_scan_still_promotes(aiify_env, tmp_path, monkeypatch):  # noqa: F811
    import tools.aiify.engine as engine
    monkeypatch.setattr(engine, "detect_patterns", lambda path: [dict(p) for p in _MOCK_PATTERNS])
    called = []
    monkeypatch.setattr(engine, "_promote_top_opportunities", lambda *a: called.append("top") or 0)
    monkeypatch.setattr(engine, "_promote_phase_opportunities", lambda *a: called.append("phase") or 0)
    monkeypatch.setattr(engine, "_register_innovation_signals", lambda *a: called.append("signals") or 0)
    r = engine.run_scan("local_path", str(_src(tmp_path)), {"il_level": "il4"})
    assert called == ["top", "phase", "signals"] and "opportunities" not in r, "the route's behaviour is unchanged"


DRAFT = {"workflow_name": "Receive stock", "workflow_description": "d", "industry": "Logistics", "steps": [
    {"title": "Inspect delivery", "description": "check the pallet", "form_fields": [{"label": "Count", "type": "number"},
                                                                                       {"label": "x", "type": "bogus"}]},
    {"id": "s2", "title": "Approve receipt", "description": "", "ace_role": "receiving_clerk"}]}


def test_processify_builds_the_draft_from_a_document(tmp_path, monkeypatch, capsys):
    from tools.workflow_canvas import processify
    prompts = []
    monkeypatch.setattr(processify, "_cortex_complete", lambda p: prompts.append(p) or "noise " + json.dumps(DRAFT))
    doc = tmp_path / "receiving-sop.txt"
    doc.write_text("1. Inspect the delivery.\n2. Approve the receipt.\n", encoding="utf-8")
    assert processify.main(["--from-doc", str(doc), "--industry", "Logistics", "--json"]) == 0
    wf = json.loads(capsys.readouterr().out)["workflow"]
    assert wf["basis"] == "document" and wf["label"] == "modelled" and wf["source_name"] == "receiving-sop"
    s1, s2 = wf["steps"]
    assert s1["id"] == "step-1" and s1["ace_role"] == "doc_reviewer" and [f["type"] for f in s1["form_fields"]] == ["number", "text"]
    assert s2["id"] == "s2" and s2["ace_role"] == "receiving_clerk" and s2["dependencies"] == []
    assert "Inspect the delivery" in prompts[0] and "Industry: Logistics" in prompts[0]


def test_processify_refuses_what_it_cannot_read(tmp_path, capsys):
    from tools.workflow_canvas import processify
    bad = tmp_path / "x.xlsx"
    bad.write_bytes(b"PK")
    assert processify.main(["--from-doc", str(bad)]) == 1
    assert "Unsupported format" in json.loads(capsys.readouterr().out)["error"]
    with pytest.raises(ValueError, match="valid JSON"):
        processify.digitize("some text", complete=lambda p: "no json here")


def test_processify_from_an_observed_capture_is_deterministic_and_says_what_it_lacks(tmp_path, capsys):
    from tools.workflow_canvas import processify
    cap = tmp_path / "capture.json"
    cap.write_text(json.dumps({"status": "refused", "kind": "process", "reason": "not_authorized"}), encoding="utf-8")
    assert processify.main(["--observed", str(cap)]) == 0
    wf = json.loads(capsys.readouterr().out)["workflow"]
    assert wf["basis"] == "observed" and wf["steps"] is None and wf["steps_basis"] == "no_evidence"
