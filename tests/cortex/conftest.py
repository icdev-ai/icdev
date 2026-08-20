# CUI // SP-CTI
"""Shared fixtures for tests/cortex/.

icdev_logger.get_logger() sets ``propagate=False`` on its loggers, which
blocks pytest's caplog handler (attached to the root logger). Re-enable
propagation on the cortex search_service logger for the duration of each
test so caplog assertions work.
"""
from __future__ import annotations

import pytest

from tools.cortex import metrics, search_service


@pytest.fixture(autouse=True)
def _propagate_search_service_logs():
    logger = search_service.logger
    old = logger.propagate
    logger.propagate = True
    yield
    logger.propagate = old


@pytest.fixture(autouse=True)
def _reset_metrics_memo():
    """metrics.summarize() memoizes across calls; drop it around every test.

    The memo key folds in ICDEV_DB_PATH so tests pointing at their own tmp_path
    DB already miss each other, but a test that does NOT repoint the DB would
    otherwise inherit a previous test's rollup.
    """
    metrics.reset_memo()
    yield
    metrics.reset_memo()


@pytest.fixture(autouse=True)
def _deterministic_intent_classification(monkeypatch):
    """No test under tests/cortex/ may block on a live intent-classifier call.

    ``POST /cortex/api/chat`` routes through ``cortex.intent_router.route()``,
    which asks ``chat_router.intent_classifier.classify()`` for a canvas signal.
    That classifier answers from its keyword taxonomy when the score is
    confident, and otherwise falls through to ``_llm_classify`` — a real
    ``cortex.classify`` round-trip through ``LLMRouter``.

    On a developer machine the provider chain refuses fast (~0.2s) and the cost
    is invisible. On the CI runner nothing is reachable and every connect dies
    on a timeout instead, so the SAME call costs ~139s. Measured on GitHub run
    32352491214: five such calls made ``tests/cortex/test_chat_routing.py``
    cost 699.2s — **39.0% of the entire 1791.2s gated suite**, and a single
    indivisible unit that no shard count can partition around
    (``shard_timings.py --balance --shards 6`` reports the same floor as
    ``--shards 4``). The other ten tests in that file total 0.53s. The same call
    reaches ``test_blueprint_routes.py`` once and ``test_rest_agent.py`` eleven
    times.

    Stubbing ``_llm_classify`` — rather than ``intent_router._base_signal`` —
    is the SMALLEST cut that removes the network:

      * ``_score_message``'s keyword taxonomy still runs, so a confidently
        classified design-canvas message still contributes its ``+2`` to the
        agent score and the routing decision under test is the real one.
      * the replacement returns ``_intake_default(...)``, which is EXACTLY what
        ``_llm_classify`` itself returns when no provider is reachable — the
        documented, air-gap-safe degradation path, not a fiction.

    It also removes a real order-dependence rather than only a cost: with a
    model server up, a design-flavoured verdict from that call adds ``+2`` to
    the agent score, which is how "draft an email to the security team" once
    routed to ``agent`` instead of ``complete``. See
    ``TestIntentRouter._deterministic_base_signal`` in test_chat_routing.py,
    which cuts deeper still because those tests assert the deterministic
    fallback path itself.

    ``tools.chat_router.intent_classifier`` and its ``icdev.`` twin are two
    distinct module objects (verified 2026-08-20), so both are patched. The LLM
    path is real behaviour, is not disabled in production, and keeps its own
    coverage in tests/chat_router/test_intent_classifier_cortex_adoption.py —
    which is in a different directory and untouched by this fixture.
    """
    import importlib

    for name in ("tools.chat_router.intent_classifier",
                 "icdev.tools.chat_router.intent_classifier"):
        try:
            module = importlib.import_module(name)
        except Exception:  # noqa: BLE001 — a missing mirror must not fail the suite
            continue
        monkeypatch.setattr(
            module, "_llm_classify",
            lambda text, _m=module: _m._intake_default(
                "LLM classification stubbed by tests/cortex/conftest.py"
            ),
        )
