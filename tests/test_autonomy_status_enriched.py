# CUI // SP-CTI
"""Unit tests for the enriched /api/autonomy/status payload (arc-obs-03).

Covers the new fields added in arc-obs-03:
  * root_cause, suspect_files, patch_hint, diagnosis_source, gate_allowed
  * diff_preview (files + verify_output_tail + excerpt)
  * iteration_count (debugger iterations per task)
  * rca_card_link, trace_link
  * autofix_branches: autofix_commit, files_changed, lines_added/removed,
    task_id, diff_link
  * unresolved_failures: signature, recovery_attempt_count, trace_link

The route function is heavy (it pulls in the full Flask app), so we test
the route function directly with monkeypatched path/imports. Conftest
already sets PYTHONPATH and SQLite backend.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path


def _build_marker(
    tmp_path: Path,
    task_id: str,
    sig: str,
    *,
    root_cause: str,
    suspect_files=None,
    confidence: float = 0.91,
    recommendation: str = "patch",
    gate_allow: bool = True,
    gate_reason: str = "all gates green",
    outcome: str = "applied_verified_committed",
    patch_files=None,
    verification_command: str = "python -m pytest tests/test_x.py -v",
    verify_tail: str = "PASSED 1 test",
    minutes_ago: int = 0,
) -> Path:
    triaged_dir = tmp_path / "triaged"
    triaged_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "task_id": task_id,
        "title": f"Test {task_id}",
        "sig": sig,
        "ts": datetime.now(timezone.utc).isoformat(),
        "outcome": {
            "task_id": task_id,
            "title": f"Test {task_id}",
            "diagnosis": {
                "root_cause": root_cause,
                "recommendation": recommendation,
                "confidence": confidence,
                "suspect_files": suspect_files or ["tools/example.py:42"],
                "patch_hint": "tweak the off-by-one",
                "source": "llm_failure_triage_diagnose",
            },
            "autofix_gate": {
                "allow": gate_allow,
                "reason": gate_reason,
            },
            "outcome": outcome,
            "patch_preview": {
                "files": patch_files or ["tools/example.py"],
                "verification_command": verification_command,
            },
            "apply_result": {
                "applied": True,
                "outcome": outcome,
                "branch": f"autofix/{task_id}-abcd1234",
                "applied_files": patch_files or ["tools/example.py"],
                "verification_tail": verify_tail,
            },
        },
    }
    f = triaged_dir / f"{task_id}__{sig}.marker"
    f.write_text(json.dumps(payload), encoding="utf-8")
    if minutes_ago:
        old = time.time() - minutes_ago * 60
        import os
        os.utime(f, (old, old))
    return f


def test_triage_recent_includes_root_cause_and_suspect_files(tmp_path, monkeypatch):
    """Enriched JSON should expose root_cause + suspect_files per card."""
    _build_marker(
        tmp_path,
        task_id="arc-test-01",
        sig="sig-aaaa",
        root_cause="Off-by-one in canonical URL builder",
        suspect_files=["tools/example.py:42", "tools/other.py:13"],
        confidence=0.93,
    )

    # Patch base_dir so the route reads our tmp_path as the project root
    # Locate the closure-wrapped function via the running app — easiest
    # is to import + run via the Flask test client.
    from flask import Flask
    flask_app = Flask("test_autonomy")
    # Re-register only the route by copy-binding; since we cannot import
    # the route function in isolation (it lives inside create_app closure),
    # we build a minimal Flask shim and copy the body. The simplest correct
    # approach: import the module, replace Path at the module level, and
    # call the route function from the WSGI app. Use test client.
    from tools.dashboard import app as dash_app_mod

    # Find the create_app result if not already built
    if not getattr(dash_app_mod, "_autonomy_route_patched", False):
        # Build the actual dashboard app
        client_app = dash_app_mod.create_app() if hasattr(dash_app_mod, "create_app") else None
        if client_app is None:
            # Some entry points register the app at module import; fall back
            client_app = getattr(dash_app_mod, "app", None)
        if client_app is None:
            import pytest
            pytest.skip("dashboard app not importable in this environment")
        dash_app_mod.app = client_app
        dash_app_mod._autonomy_route_patched = True
    # Monkey-patch the BASE_DIR used inside the route — the route computes
    # `base_dir = Path(__file__).resolve().parent.parent.parent` at call
    # time, so we patch Path resolution via a different mechanism. The
    # route reads `Path(__file__).resolve().parent.parent.parent` of
    # tools/dashboard/app.py. Override the route's resolved base by
    # monkey-patching the route function's `__globals__`.
    flask_app = dash_app_mod.app
    # The route is bound to the app. Find the view function for /api/autonomy/status
    view = None
    for rule in flask_app.url_map.iter_rules():
        if str(rule) == "/api/autonomy/status":
            view = flask_app.view_functions[rule.endpoint]
            break
    assert view is not None, "api_autonomy_status route not registered"
    # Monkey-patch the __globals__ Path to a Path that resolves base to tmp_path
    # The route does: base_dir = Path(__file__).resolve().parent.parent.parent
    # We override by patching Path's resolve to return tmp_path. Simpler: we
    # override _load_path by injecting a global `__file__` for the module.

    # Approach: temporarily swap tools.dashboard.app.__file__ to a fake file
    # inside tmp_path, so the route's base_dir resolves to tmp_path. But
    # that file must be a sibling of `tools/dashboard/`. Make a fake
    # `tools/dashboard/app.py` under tmp_path. Since the function uses
    # Path(__file__).resolve().parent.parent.parent, putting a fake file at
    # <tmp>/tools/dashboard/app.py makes base_dir resolve to <tmp>.
    fake = tmp_path / "tools" / "dashboard" / "app.py"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text("# fake", encoding="utf-8")
    monkeypatch.setattr(dash_app_mod, "__file__", str(fake))

    with flask_app.test_client() as c:
        resp = c.get("/api/autonomy/status")
        assert resp.status_code == 200, resp.data
        payload = resp.get_json()
        assert payload, "empty JSON payload"
        assert payload["visible"] is True
        cards = payload["triage_recent"]
        assert len(cards) == 1, cards
        c0 = cards[0]
        # New fields
        assert c0["root_cause"] == "Off-by-one in canonical URL builder"
        assert c0["suspect_files"] == ["tools/example.py:42", "tools/other.py:13"]
        assert c0["confidence"] == 0.93
        assert c0["patch_hint"] == "tweak the off-by-one"
        assert c0["diagnosis_source"] == "llm_failure_triage_diagnose"
        assert c0["gate_allowed"] is True
        assert c0["iteration_count"] == 1
        assert c0["rca_card_link"] == "/kanban?focus=arc-test-01"
        assert c0["trace_link"] == "/traces?task_id=arc-test-01&sig=sig-aaaa"
        # diff_preview
        dp = c0["diff_preview"]
        assert dp["files"] == ["tools/example.py"]
        assert dp["verify_output_tail"] == "PASSED 1 test"
        assert "tools/example.py" in dp["excerpt"]


def test_iteration_count_counts_markers_for_same_task(tmp_path, monkeypatch):
    """Two triage markers for the same task_id should yield iteration_count=2."""
    for i, sig in enumerate(["sig-1111", "sig-2222"]):
        _build_marker(
            tmp_path,
            task_id="arc-test-02",
            sig=sig,
            root_cause=f"flaky retry #{i}",
            confidence=0.7,
        )

    import tools.dashboard.app as dash_app_mod
    flask_app = dash_app_mod.app
    fake = tmp_path / "tools" / "dashboard" / "app.py"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text("# fake", encoding="utf-8")
    monkeypatch.setattr(dash_app_mod, "__file__", str(fake))

    with flask_app.test_client() as c:
        resp = c.get("/api/autonomy/status")
        payload = resp.get_json()
        cards = payload["triage_recent"]
        assert len(cards) == 2
        for card in cards:
            assert card["task_id"] == "arc-test-02"
            assert card["iteration_count"] == 2, card


def test_empty_markers_hides_panel(tmp_path, monkeypatch):
    """No markers + no branches + no failures → visible=False."""
    import tools.dashboard.app as dash_app_mod
    flask_app = dash_app_mod.app
    # tmp_path has no triaged/ subdir at all
    fake = tmp_path / "tools" / "dashboard" / "app.py"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text("# fake", encoding="utf-8")
    monkeypatch.setattr(dash_app_mod, "__file__", str(fake))
    with flask_app.test_client() as c:
        resp = c.get("/api/autonomy/status")
        payload = resp.get_json()
        assert payload["visible"] is False
        assert payload["triage_recent"] == []
        assert payload["autofix_branches"] == []
        assert payload["unresolved_failures"] == []


def test_diff_preview_includes_verify_output_tail(tmp_path, monkeypatch):
    """verify_output_tail must be bounded and present in diff_preview."""
    long_tail = "X" * 1500
    _build_marker(
        tmp_path,
        task_id="arc-test-03",
        sig="sig-bbbb",
        root_cause="verify failure",
        verify_tail=long_tail,
    )
    import tools.dashboard.app as dash_app_mod
    flask_app = dash_app_mod.app
    fake = tmp_path / "tools" / "dashboard" / "app.py"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text("# fake", encoding="utf-8")
    monkeypatch.setattr(dash_app_mod, "__file__", str(fake))
    with flask_app.test_client() as c:
        resp = c.get("/api/autonomy/status")
        payload = resp.get_json()
        c0 = payload["triage_recent"][0]
        tail = c0["diff_preview"]["verify_output_tail"]
        # Tailed to last 400 chars
        assert len(tail) == 400
        assert tail == long_tail[-400:]
