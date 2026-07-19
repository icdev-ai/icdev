# CUI // SP-CTI
"""Tests for check_llm_router_api coherence rule (nav-llm-02).

LLMRouter exposes only invoke(fn, LLMRequest); a wave of shipped call sites
invoked a nonexistent router.complete() inside try/except, leaving permanently
dead LLM paths masked by fallbacks (fixed in PR #569). This rule prevents
regression of that class.

Each test writes synthetic offender / non-offender files under a tmp repo,
points cc.PROJECT_ROOT at it, and asserts detection + every false-positive
guard from the check's docstring.
"""
from __future__ import annotations

import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.workflow import coherence_checker as cc  # noqa: E402


def _run(tmp_path, monkeypatch, files: dict):
    """Write {relpath: body} into a tmp repo, run check_llm_router_api on it."""
    repo = tmp_path / "repo"
    for rel, body in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    monkeypatch.setattr(cc, "PROJECT_ROOT", repo)
    return cc.check_llm_router_api()


# ---------------------------------------------------------------------------
# Offenders → FAIL and flagged
# ---------------------------------------------------------------------------

OFFENDER_COMPLETE = (
    "from tools.llm.router import LLMRouter\n"
    "from tools.llm.provider import LLMRequest\n"
    "def gen(req):\n"
    "    router = LLMRouter()\n"
    "    result = router.complete(req)\n"
    "    return result\n"
)


def test_offender_router_complete_flagged(tmp_path, monkeypatch):
    result = _run(tmp_path, monkeypatch, {"tools/foo/gen.py": OFFENDER_COMPLETE})
    assert result.status == "fail", result.message
    assert result.check_id == "llm_router_api"
    assert len(result.extra) == 1
    assert "tools/foo/gen.py:5" in result.extra[0].replace("\\", "/")
    assert "invoke(fn, LLMRequest)" in result.extra[0]


def test_offender_router_chat_flagged(tmp_path, monkeypatch):
    body = (
        "from tools.llm.router import LLMRouter\n"
        "def gen(req):\n"
        "    router = LLMRouter()\n"
        "    return router.chat(req)\n"
    )
    result = _run(tmp_path, monkeypatch, {"tools/foo/gen.py": body})
    assert result.status == "fail", result.message
    assert any("chat()" in v for v in result.extra)


def test_offender_via_get_router_flagged(tmp_path, monkeypatch):
    body = (
        "from tools.llm import get_router\n"
        "def gen(req):\n"
        "    r = get_router()\n"
        "    return r.complete(req)\n"
    )
    result = _run(tmp_path, monkeypatch, {"tools/foo/gen.py": body})
    assert result.status == "fail", result.message


def test_offender_self_attribute_binding_flagged(tmp_path, monkeypatch):
    body = (
        "from tools.llm.router import LLMRouter\n"
        "class Gen:\n"
        "    def __init__(self):\n"
        "        self.router = LLMRouter()\n"
        "    def run(self, req):\n"
        "        return self.router.complete(req)\n"
    )
    result = _run(tmp_path, monkeypatch, {"tools/foo/gen.py": body})
    assert result.status == "fail", result.message
    assert any("self.router" in v for v in result.extra)


def test_offender_under_apps_and_icdev_roots(tmp_path, monkeypatch):
    result = _run(
        tmp_path,
        monkeypatch,
        {
            "apps/innovation/a.py": OFFENDER_COMPLETE,
            "icdev/tools/foo/b.py": OFFENDER_COMPLETE,
        },
    )
    assert result.status == "fail", result.message
    joined = " ".join(result.extra).replace("\\", "/")
    assert "apps/innovation/a.py" in joined
    assert "icdev/tools/foo/b.py" in joined


# ---------------------------------------------------------------------------
# Non-offenders / false-positive guards → PASS
# ---------------------------------------------------------------------------

def test_router_invoke_not_flagged(tmp_path, monkeypatch):
    body = (
        "from tools.llm.router import LLMRouter\n"
        "def gen(req):\n"
        "    router = LLMRouter()\n"
        "    return router.invoke('feasibility_study', req)\n"
    )
    result = _run(tmp_path, monkeypatch, {"tools/foo/gen.py": body})
    assert result.status == "pass", result.message


def test_cortex_facade_complete_not_flagged(tmp_path, monkeypatch):
    # Valid Cortex facade — cortex_api is never router-bound.
    body = (
        "from tools.llm.router import LLMRouter\n"
        "from tools.cortex import api as cortex_api\n"
        "def gen(prompt):\n"
        "    router = LLMRouter()  # used elsewhere via invoke\n"
        "    cx = cortex_api.complete(prompt=prompt)\n"
        "    return cx\n"
    )
    result = _run(tmp_path, monkeypatch, {"tools/foo/gen.py": body})
    assert result.status == "pass", result.message


def test_provider_complete_receiver_not_flagged(tmp_path, monkeypatch):
    # `.complete(` on a provider object (not router-bound) must not flag,
    # even in a file that imports LLMRouter.
    body = (
        "from tools.llm.router import LLMRouter\n"
        "def gen(provider, model_id, prompt):\n"
        "    router = LLMRouter()  # noqa: used elsewhere\n"
        "    return provider.complete(model_id=model_id, prompt=prompt)\n"
    )
    result = _run(tmp_path, monkeypatch, {"tools/foo/gen.py": body})
    assert result.status == "pass", result.message


def test_provider_dir_skipped_even_if_router_bound(tmp_path, monkeypatch):
    # Files under tools/llm/providers/ are skipped outright (provider SDK home).
    result = _run(
        tmp_path, monkeypatch,
        {"tools/llm/providers/ollama_provider.py": OFFENDER_COMPLETE},
    )
    assert result.status == "pass", result.message


def test_provider_module_file_skipped(tmp_path, monkeypatch):
    # tools/llm/provider.py is skipped outright.
    result = _run(tmp_path, monkeypatch, {"tools/llm/provider.py": OFFENDER_COMPLETE})
    assert result.status == "pass", result.message


def test_string_literal_and_comment_not_flagged(tmp_path, monkeypatch):
    body = (
        "from tools.llm.router import LLMRouter\n"
        "SEED = 'replace router.complete() calls with invoke()'\n"
        "# router.complete( is dead — do not use\n"
        "def gen(req):\n"
        "    router = LLMRouter()\n"
        "    return router.invoke('fn', req)\n"
    )
    result = _run(tmp_path, monkeypatch, {"tools/foo/gen.py": body})
    assert result.status == "pass", result.message


def test_tests_dir_excluded(tmp_path, monkeypatch):
    # tests/ is excluded from the scan.
    result = _run(tmp_path, monkeypatch, {"tools/tests/test_gen.py": OFFENDER_COMPLETE})
    assert result.status == "pass", result.message


def test_ollama_chat_client_not_flagged(tmp_path, monkeypatch):
    # `.chat(` on an ollama client (not router-bound) must not flag.
    body = (
        "from tools.llm.router import LLMRouter\n"
        "import ollama\n"
        "def gen(messages):\n"
        "    router = LLMRouter()  # used via invoke\n"
        "    client = ollama.Client()\n"
        "    return client.chat(model='x', messages=messages)\n"
    )
    result = _run(tmp_path, monkeypatch, {"tools/foo/gen.py": body})
    assert result.status == "pass", result.message
