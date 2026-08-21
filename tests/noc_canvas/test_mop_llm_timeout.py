# CUI // SP-CTI
"""A request must not hang on the model (noc_canvas MOP generator).

THE INCIDENT. `tests/e2e/noc_canvas.spec.ts` — "create an RFC then generate a
MOP" — began failing on main at `aad63a58c` (2026-08-21 11:19), last green at
`3bb428c2d` (07:16). 190 of 191 tests in that shard passed: every NOC page load
and every NOC read API. Only the WRITE that generates a MOP failed, and it
failed with

    TimeoutError: apiRequestContext.post: Timeout 10000ms exceeded

`generate_mop` runs inside `POST /api/noc/mops/generate` and calls
`router.invoke`. `LLMRequest` carries max_tokens and effort but NO DEADLINE, so
an unreachable provider does not fail fast — it blocks on the network. That is
~0.2s on a developer machine, where the connection is refused immediately, and
MINUTES on a CI runner or an air-gapped host, where it is dropped and the client
waits out its own socket timeout.

THE EXISTING `except Exception` WAS ALWAYS CORRECT AND NEVER REACHED. A hang is
not an exception. The fallback needed a clock, not a broader catch — which is
why the template path that was written for exactly this case never ran.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.noc_canvas import mop_generator as m  # noqa: E402

RFC = {"title": "E2E firmware upgrade", "change_type": "standard",
       "risk_level": "medium"}


# --------------------------------------------------------------------------- #
# 1. The clock — the whole fix
# --------------------------------------------------------------------------- #
def test_a_hanging_model_does_not_hang_the_request(monkeypatch):
    """THE regression. Before the bound, this call never returned within the
    request's lifetime and Playwright timed out at 10s."""
    import tools.llm.router as router_mod

    class _HangingRouter:
        def invoke(self, *_a, **_k):
            time.sleep(30)

    # The REAL path: a provider that never answers, through the real
    # _invoke_bounded, through the real generate_mop.
    monkeypatch.setattr(router_mod, "LLMRouter", _HangingRouter)
    monkeypatch.setattr(m, "MOP_LLM_TIMEOUT_SECONDS", 0.3)

    started = time.monotonic()
    result = m.generate_mop(RFC, context="upgrade core routers")
    elapsed = time.monotonic() - started

    assert elapsed < 5, f"generate_mop took {elapsed:.1f}s — it is not bounded"
    assert result["steps"], "no steps returned at all"
    assert result["generated_by"] == "ai_template", (
        "a timed-out model was not recorded as a template fallback")


def test_the_bound_is_enforced_by_invoke_bounded(monkeypatch):
    """The clock lives in `_invoke_bounded`, so a slow provider is abandoned
    there rather than propagating into the handler."""
    import tools.llm.router as router_mod

    class _SlowRouter:
        def invoke(self, *_a, **_k):
            time.sleep(30)

    monkeypatch.setattr(router_mod, "LLMRouter", _SlowRouter)

    started = time.monotonic()
    got = m._invoke_bounded("prompt", timeout=0.4)
    elapsed = time.monotonic() - started

    assert got is None, "a provider that never answered returned content"
    assert elapsed < 3, f"_invoke_bounded waited {elapsed:.1f}s on a 0.4s budget"


def test_it_does_not_wait_for_the_abandoned_worker(monkeypatch):
    """`shutdown(wait=True)` would re-introduce the hang this exists to remove:
    Python cannot kill a thread, so the request must stop WAITING while the call
    finishes in the background and its result is discarded."""
    import inspect

    src = inspect.getsource(m._invoke_bounded)
    assert "shutdown(wait=False)" in src, (
        "the pool is shut down with wait=True — the handler still blocks"
    )


# --------------------------------------------------------------------------- #
# 2. The fallback still produces a usable MOP
# --------------------------------------------------------------------------- #
def test_the_template_fallback_returns_real_steps(monkeypatch):
    monkeypatch.setattr(m, "_invoke_bounded", lambda _p, _t: None)
    result = m.generate_mop(RFC)

    assert result["generated_by"] == "ai_template"
    assert isinstance(result["steps"], list) and result["steps"]
    assert result["mop_id"] and result["mop_number"]


def test_a_good_model_answer_is_still_used(monkeypatch):
    """The bound must not disable the feature — a provider that answers in time
    is used exactly as before."""
    answer = '[{"step": 1, "action": "do it", "rollback": "undo", ' \
             '"timeout_min": 5, "verification": "ok"}]'
    monkeypatch.setattr(m, "_invoke_bounded", lambda _p, _t: answer)

    result = m.generate_mop(RFC)
    assert result["generated_by"] == "ai"
    assert result["steps"][0]["action"] == "do it"


def test_a_malformed_answer_falls_back_rather_than_raising(monkeypatch):
    monkeypatch.setattr(m, "_invoke_bounded", lambda _p, _t: "not json at all")
    result = m.generate_mop(RFC)
    assert result["generated_by"] == "ai_template"
    assert result["steps"]


def test_a_raising_provider_falls_back(monkeypatch):
    import tools.llm.router as router_mod

    class _Boom:
        def invoke(self, *_a, **_k):
            raise RuntimeError("no provider configured")

    monkeypatch.setattr(router_mod, "LLMRouter", _Boom)
    assert m._invoke_bounded("prompt", timeout=5) is None


# --------------------------------------------------------------------------- #
# 3. The budget is configurable, and has a sane default
# --------------------------------------------------------------------------- #
def test_the_budget_is_bounded_by_default():
    """It sits inside an HTTP request. A default long enough to outlast a
    client's own timeout would make the bound decorative."""
    assert 0 < m.MOP_LLM_TIMEOUT_SECONDS <= 10
