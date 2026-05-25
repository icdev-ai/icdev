# CUI // SP-CTI
"""OPT-66: tests for check_llm_injection_patterns coherence check.

Builds synthetic tools/ subtrees under tmp_path, points PROJECT_ROOT at
them, runs the check, and asserts the expected status + findings.
"""
from __future__ import annotations

import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.workflow import coherence_checker as cc  # noqa: E402


def _make_tools_dir(tmp_path, files: dict) -> pathlib.Path:
    """Create a fake repo layout under tmp_path with the given Python files
    and return the root so PROJECT_ROOT can be monkeypatched."""
    root = tmp_path / "repo"
    tools = root / "tools"
    tools.mkdir(parents=True)
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return root


def test_injection_patterns_clean_tree(tmp_path, monkeypatch):
    repo = _make_tools_dir(tmp_path, {
        "tools/example/safe.py": (
            "from tools.llm.provider import LLMRequest\n"
            "def handler(prompt):\n"
            "    req = LLMRequest(\n"
            "        messages=[{'role':'user','content': prompt}],\n"
            "        system_prompt='You are safe.',\n"
            "    )\n"
            "    return req\n"
        )
    })
    monkeypatch.setattr(cc, "PROJECT_ROOT", repo)
    result = cc.check_llm_injection_patterns()
    assert result.status == "pass"
    assert result.check_id == "llm_injection_patterns"


def test_injection_patterns_flags_flask_request(tmp_path, monkeypatch):
    repo = _make_tools_dir(tmp_path, {
        "tools/example/unsafe.py": (
            "from flask import request\n"
            "from tools.llm.provider import LLMRequest\n"
            "def bad():\n"
            "    req = LLMRequest(\n"
            "        messages=[{'role':'user','content': request.args.get('q')}],\n"
            "    )\n"
            "    return req\n"
        )
    })
    monkeypatch.setattr(cc, "PROJECT_ROOT", repo)
    result = cc.check_llm_injection_patterns()
    assert result.status == "warn"
    assert any("unsafe.py" in f for f in result.extra)
    assert "flask request" in result.message.lower()


def test_injection_patterns_system_prompt_flagged(tmp_path, monkeypatch):
    repo = _make_tools_dir(tmp_path, {
        "tools/example/sysprompt.py": (
            "from flask import request\n"
            "from tools.llm.provider import LLMRequest\n"
            "def h():\n"
            "    s = request.form.get('sys', '')\n"
            "    req = LLMRequest(\n"
            "        messages=[{'role':'user','content':'hi'}],\n"
            "        system_prompt=s,\n"
            "    )\n"
            "    return req\n"
        )
    })
    monkeypatch.setattr(cc, "PROJECT_ROOT", repo)
    result = cc.check_llm_injection_patterns()
    # scope has `request.form` used — but the system_prompt kwarg is just
    # a Name `s`, not the flask.request expression directly. This probes
    # the expression-tree check, which should NOT flag the bare name
    # (scope-flow analysis not yet implemented). Documenting the limit.
    assert result.status == "pass"


def test_injection_patterns_sanitizer_in_scope_clears(tmp_path, monkeypatch):
    repo = _make_tools_dir(tmp_path, {
        "tools/example/guarded.py": (
            "from flask import request\n"
            "from tools.llm.provider import LLMRequest\n"
            "def sanitize(x):\n"
            "    return x.replace('<','&lt;')\n"
            "def h():\n"
            "    q = sanitize(request.args.get('q'))\n"
            "    req = LLMRequest(\n"
            "        messages=[{'role':'user','content': request.args.get('q')}],\n"
            "    )\n"
            "    return req, q\n"
        )
    })
    monkeypatch.setattr(cc, "PROJECT_ROOT", repo)
    result = cc.check_llm_injection_patterns()
    # sanitize() is called in the same function scope — warn is suppressed
    assert result.status == "pass"


def test_injection_patterns_allowlist_skips_router(tmp_path, monkeypatch):
    repo = _make_tools_dir(tmp_path, {
        "tools/llm/router.py": (
            "from flask import request\n"
            "from tools.llm.provider import LLMRequest\n"
            "def invoke_router():\n"
            "    return LLMRequest(messages=[{'content': request.args.get('x')}])\n"
        )
    })
    monkeypatch.setattr(cc, "PROJECT_ROOT", repo)
    result = cc.check_llm_injection_patterns()
    # tools/llm/router.py is in the allowlist — the unsafe callsite must
    # NOT be reported even though it would otherwise trip the check.
    assert result.status == "pass"


def test_injection_patterns_scopes_to_changed_files(tmp_path, monkeypatch):
    repo = _make_tools_dir(tmp_path, {
        "tools/a/unsafe.py": (
            "from flask import request\n"
            "from tools.llm.provider import LLMRequest\n"
            "def h():\n"
            "    return LLMRequest(messages=[{'content': request.args}])\n"
        ),
        "tools/b/unsafe.py": (
            "from flask import request\n"
            "from tools.llm.provider import LLMRequest\n"
            "def h():\n"
            "    return LLMRequest(messages=[{'content': request.args}])\n"
        ),
    })
    monkeypatch.setattr(cc, "PROJECT_ROOT", repo)
    # When restricted to tools/a, only that file should fire
    result = cc.check_llm_injection_patterns(
        changed_files=[repo / "tools" / "a" / "unsafe.py"]
    )
    assert result.status == "warn"
    assert all("tools/a" in f for f in result.extra)
    assert not any("tools/b" in f for f in result.extra)


def test_registry_includes_injection_check():
    assert "llm_injection_patterns" in cc.CHECK_REGISTRY
    assert cc._FIX_REGISTRY["llm_injection_patterns"] == "skip"
