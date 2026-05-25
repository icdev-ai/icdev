# CUI // SP-CTI
"""OPT-52: tests for tools/planning/design_twice.py."""
from __future__ import annotations

import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.planning import design_twice  # noqa: E402


# ────────────────────────────────────────────────────────────────────────────
# Constraint loader
# ────────────────────────────────────────────────────────────────────────────


def test_default_constraints_used_when_none():
    cs = design_twice.load_constraints(None)
    assert len(cs) == 4
    assert cs[0]["id"] == "minimal_surface"
    assert cs[-1]["id"] == "inspired_by_stdlib"


def test_load_constraints_from_yaml(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "constraints:\n"
        "  - id: a\n    prompt: first\n    label: A\n"
        "  - id: b\n    prompt: second\n",
        encoding="utf-8",
    )
    cs = design_twice.load_constraints(p)
    assert len(cs) == 2
    assert cs[0]["id"] == "a"
    assert cs[0]["label"] == "A"
    # Default label fallback
    assert cs[1]["label"] == "b"


def test_load_constraints_rejects_empty(tmp_path):
    p = tmp_path / "empty.yaml"
    p.write_text("constraints: []\n", encoding="utf-8")
    try:
        design_twice.load_constraints(p)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for empty constraints")


# ────────────────────────────────────────────────────────────────────────────
# Runner with fake router
# ────────────────────────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, content):
        self.content = content
        self.provider = "fake"
        self.model_id = "fake-id"
        self.input_tokens = 15
        self.output_tokens = 50
        self.duration_ms = 1


class _FakeRouter:
    def __init__(self, responses=None):
        self._i = 0
        self._responses = responses or []
        self._no_llm = False

    def is_no_llm_mode(self):
        return self._no_llm

    def invoke(self, function, request):
        if self._i < len(self._responses):
            r = self._responses[self._i]
            self._i += 1
            return _FakeResponse(r)
        return _FakeResponse("## Interface\nfn x()\n## Usage example\n"
                             "x()\n## What it hides\nstate\n"
                             "## Trade-offs\nnone")


def test_run_design_twice_happy_path():
    cs = design_twice.load_constraints(None)
    router = _FakeRouter()
    report = design_twice.run_design_twice(
        "token cache", cs, router=router,
    )
    assert len(report.variants) == 4
    assert all(v.content for v in report.variants)
    assert all(not v.error for v in report.variants)


def test_run_design_twice_handles_invoke_exception():
    class _BadRouter:
        def is_no_llm_mode(self):
            return False

        def invoke(self, function, request):
            raise RuntimeError("provider down")

    cs = design_twice.load_constraints(None)[:2]
    report = design_twice.run_design_twice(
        "x", cs, router=_BadRouter(), parallel=False,
    )
    assert len(report.variants) == 2
    assert all("provider down" in v.error for v in report.variants)


def test_run_design_twice_no_llm_mode_skeleton():
    router = _FakeRouter()
    router._no_llm = True
    cs = design_twice.load_constraints(None)
    report = design_twice.run_design_twice("foo", cs, router=router)
    assert report.no_llm is True
    assert all("skeleton" in v.content for v in report.variants)


def test_run_design_twice_sequential_mode():
    cs = design_twice.load_constraints(None)[:2]
    router = _FakeRouter(responses=[
        "## Interface\nfirst\n## Usage example\n\n## What it hides\n\n"
        "## Trade-offs\n",
        "## Interface\nsecond\n## Usage example\n\n## What it hides\n\n"
        "## Trade-offs\n",
    ])
    report = design_twice.run_design_twice(
        "seq-mod", cs, router=router, parallel=False,
    )
    assert report.variants[0].content.startswith("## Interface\nfirst")
    assert report.variants[1].content.startswith("## Interface\nsecond")


# ────────────────────────────────────────────────────────────────────────────
# Render + write
# ────────────────────────────────────────────────────────────────────────────


def test_render_markdown_contains_variants_and_comparison_table():
    cs = design_twice.load_constraints(None)
    router = _FakeRouter()
    report = design_twice.run_design_twice("auth cache", cs, router=router)
    md = design_twice.render_markdown(report)
    assert "# Design Twice: auth cache" in md
    assert "## Variants" in md
    assert "## Comparison" in md
    # Each variant label appears
    for v in report.variants:
        assert v.label in md


def test_write_report_produces_file(tmp_path):
    cs = design_twice.load_constraints(None)
    router = _FakeRouter()
    report = design_twice.run_design_twice("rate limiter", cs, router=router)
    out = tmp_path / "rl.md"
    design_twice.write_report(report, out)
    assert out.exists()
    assert "# Design Twice: rate limiter" in out.read_text(encoding="utf-8")


# ────────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────────


def test_cli_writes_output(tmp_path, monkeypatch):
    _real = design_twice.run_design_twice

    def fake_run(module, constraints, **kwargs):
        return _real(module, constraints, router=_FakeRouter(), **{
            k: v for k, v in kwargs.items() if k != "router"
        })

    monkeypatch.setattr(design_twice, "run_design_twice", fake_run)
    out_path = tmp_path / "mymod.md"
    rc = design_twice.main([
        "--module", "sample",
        "--out", str(out_path),
    ])
    assert rc == 0
    assert out_path.exists()
    assert "# Design Twice: sample" in out_path.read_text(encoding="utf-8")


def test_slug_helper():
    assert design_twice._slug("Auth Token Cache") == "auth-token-cache"
    assert design_twice._slug("Weird!!! Chars???") == "weird-chars"
    assert design_twice._slug("") == "design"
