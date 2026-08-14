# CUI // SP-CTI
"""RAG retriever hot-path fixes (ctx-perf-04).

Three defects on the path every ``cortex.search`` with the ``rag`` backend
takes, all in ``tools/rag/retriever.py``:

1. ``_load_rag_config()`` re-read and re-parsed ``args/rag_config.yaml`` from
   disk on EVERY ``RAGRetriever`` construction — and Cortex constructs one per
   search — while the neighbouring ``cortex/config.py`` memoizes its own file
   per path+mtime.
2. The fallback embedding path hardcoded ``model="nomic-embed-text"``, pinning
   one vendor into code on a path whose callers swallow exceptions.
3. An embedding failure returned ``[]``, indistinguishable from "the corpus
   matched nothing" — which reached a chat user as "No matching results were
   found across the Cortex backends" when the real cause was a dead provider.

Every test here fails against the pre-fix tree. No DB service, no LLM, no
network: the config files are written to ``tmp_path`` and the embedding
provider is a stub.
"""
from __future__ import annotations

import logging

import pytest
import yaml

from tools.rag import retriever as retriever_mod
from tools.rag.config_path import CONFIG_ENV_VAR as RAG_CONFIG_ENV_VAR
from tools.rag.retriever import (
    EmbeddingUnavailableError,
    RAGRetriever,
    _embed_query,
    _load_rag_config,
    reset_rag_config_cache,
)

_MIN_CONFIG = {"rag": {"retrieval": {"final_top_k": 5}}}


@pytest.fixture
def rag_config(tmp_path, monkeypatch):
    """Point ICDEV_RAG_CONFIG at a temp rag_config.yaml with a clean memo."""
    path = tmp_path / "rag_config.yaml"
    path.write_text(yaml.safe_dump(_MIN_CONFIG), encoding="utf-8")
    monkeypatch.setenv(RAG_CONFIG_ENV_VAR, str(path))
    reset_rag_config_cache()
    yield path
    reset_rag_config_cache()


@pytest.fixture
def captured_logs():
    """Records from an ICDEV logger.

    ``get_logger`` sets ``propagate = False``, so ``caplog`` — which hangs off
    the root logger — sees nothing from these modules. Attach a handler to the
    logger itself instead.
    """
    attached: list[tuple] = []

    def _capture(logger, level=logging.ERROR) -> list[logging.LogRecord]:
        records: list[logging.LogRecord] = []

        class _Handler(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = _Handler(level=level)
        logger.addHandler(handler)
        attached.append((logger, handler))
        return records

    yield _capture
    for logger, handler in attached:
        logger.removeHandler(handler)


@pytest.fixture
def parse_counter(monkeypatch):
    """Count every ``yaml.safe_load`` the retriever performs."""
    calls: list[int] = []
    real = yaml.safe_load

    def counting_safe_load(stream, *args, **kwargs):
        calls.append(1)
        return real(stream, *args, **kwargs)

    monkeypatch.setattr(yaml, "safe_load", counting_safe_load)
    return calls


# ---------------------------------------------------------------------------
# 1. Config memoization (mtime-keyed, matching cortex/config.py)
# ---------------------------------------------------------------------------


def test_config_is_parsed_once_across_two_loads(rag_config, parse_counter):
    assert _load_rag_config() == _MIN_CONFIG
    assert len(parse_counter) == 1
    assert _load_rag_config() == _MIN_CONFIG
    assert len(parse_counter) == 1, "config was re-parsed from disk on the 2nd load"


def test_second_search_does_not_reparse_the_config(rag_config, parse_counter):
    """A second Cortex search must not re-parse the file.

    Each ``cortex.search`` with the rag backend constructs a ``RAGRetriever``
    (``retriever_common.run_rag_search``), and ``__init__`` is where the config
    is read — so two constructions is exactly two searches' worth of config
    loading.
    """
    RAGRetriever(tenant_id="t1")
    assert len(parse_counter) == 1
    RAGRetriever(tenant_id="t1")
    RAGRetriever(tenant_id="t2")
    assert len(parse_counter) == 1, "each search re-parsed args/rag_config.yaml"


def test_memo_is_mtime_keyed_so_an_edited_config_is_picked_up(
    rag_config, parse_counter
):
    """The memo must not outlive the file it describes."""
    assert _load_rag_config()["rag"]["retrieval"]["final_top_k"] == 5
    assert len(parse_counter) == 1

    edited = {"rag": {"retrieval": {"final_top_k": 9}}}
    rag_config.write_text(yaml.safe_dump(edited), encoding="utf-8")
    stat = rag_config.stat()
    # Windows mtime granularity is coarse enough that a rewrite inside the same
    # tick keeps the old mtime; push it forward explicitly rather than sleeping.
    import os

    os.utime(rag_config, (stat.st_atime, stat.st_mtime + 10))

    assert _load_rag_config()["rag"]["retrieval"]["final_top_k"] == 9
    assert len(parse_counter) == 2


def test_refresh_bypasses_the_memo(rag_config, parse_counter):
    _load_rag_config()
    _load_rag_config(refresh=True)
    assert len(parse_counter) == 2


def test_missing_config_returns_empty_and_is_not_cached(tmp_path, monkeypatch):
    monkeypatch.setenv(RAG_CONFIG_ENV_VAR, str(tmp_path / "absent.yaml"))
    reset_rag_config_cache()
    assert _load_rag_config() == {}


# ---------------------------------------------------------------------------
# 2. The embedding model id is routed by config, never hardcoded
# ---------------------------------------------------------------------------


class _RawClient:
    """OpenAI-style client with no ``.embed`` — the fallback embedding path."""

    def __init__(self):
        self.model_used = None
        outer = self

        class _Embeddings:
            def create(self, input, model):  # noqa: A002 — mirrors the SDK
                outer.model_used = model
                return type(
                    "Resp", (), {"data": [type("D", (), {"embedding": [0.1, 0.2]})()]}
                )()

        self.embeddings = _Embeddings()


@pytest.fixture
def llm_config(tmp_path, monkeypatch):
    """A temp llm_config.yaml declaring one embedding model."""
    path = tmp_path / "llm_config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "embeddings": {
                    "default_chain": ["test-embed"],
                    "models": {
                        "test-embed": {
                            "provider": "ollama",
                            "model_id": "embed-model-from-config",
                        }
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ICDEV_LLM_CONFIG", str(path))
    return path


def test_fallback_embed_model_comes_from_llm_config(llm_config):
    client = _RawClient()
    assert _embed_query(client, "some query") == [0.1, 0.2]
    assert client.model_used == "embed-model-from-config"


def test_provider_with_embed_needs_no_model_id(llm_config):
    """An ICDEV EmbeddingProvider carries its own model — don't name one."""

    class _Provider:
        def embed(self, text):
            return [0.3]

    assert _embed_query(_Provider(), "q") == [0.3]


def test_undeclared_embedding_model_raises_rather_than_guessing(
    tmp_path, monkeypatch
):
    """No literal fallback: guessing an id is the defect being fixed."""
    empty = tmp_path / "llm_config.yaml"
    empty.write_text(yaml.safe_dump({"embeddings": {}}), encoding="utf-8")
    monkeypatch.setenv("ICDEV_LLM_CONFIG", str(empty))
    with pytest.raises(LookupError):
        _embed_query(_RawClient(), "q")


def test_no_hardcoded_embedding_model_id_in_the_retriever():
    from pathlib import Path

    source = Path(retriever_mod.__file__).read_text(encoding="utf-8")
    assert 'model="nomic-embed-text"' not in source
    assert "model='nomic-embed-text'" not in source


# ---------------------------------------------------------------------------
# 3. An embedding failure is not a zero-result
# ---------------------------------------------------------------------------


def test_missing_provider_raises_instead_of_returning_empty(rag_config, monkeypatch):
    monkeypatch.setattr(retriever_mod, "_get_embedding_provider", lambda: None)
    with pytest.raises(EmbeddingUnavailableError):
        RAGRetriever(tenant_id="t1").search("anything")


def test_failing_embed_call_raises_and_logs_at_error(
    rag_config, monkeypatch, captured_logs
):
    class _Broken:
        def embed(self, text):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(retriever_mod, "_get_embedding_provider", lambda: _Broken())
    records = captured_logs(retriever_mod.logger)
    with pytest.raises(EmbeddingUnavailableError) as excinfo:
        RAGRetriever(tenant_id="t1").search("anything")
    assert "connection refused" in str(excinfo.value)
    assert any(
        "embedding failed" in record.getMessage() for record in records
    ), "an embedding failure left no ERROR log to tell it from an empty corpus"


def test_provider_chain_failure_is_logged_not_swallowed(monkeypatch, captured_logs):
    """``_get_embedding_provider`` returning None must say WHY in the log."""
    import importlib

    def _boom(*args, **kwargs):
        raise RuntimeError("no embedding provider available in chain")

    # ``tools.llm`` and ``icdev.tools.llm`` are DISTINCT module objects (the
    # tools/ shim), and the retriever imports whichever its own namespace root
    # resolves to — patch both or the fake is never installed and this test
    # quietly probes the real provider chain.
    for name in ("tools.llm", "icdev.tools.llm"):
        monkeypatch.setattr(
            importlib.import_module(name), "get_embedding_provider", _boom
        )
    records = captured_logs(retriever_mod.logger)
    assert retriever_mod._get_embedding_provider() is None
    assert any(
        "no embedding provider" in record.getMessage().lower() for record in records
    )


# ---------------------------------------------------------------------------
# 3b. ...and the Cortex layer tells the user which one it was
# ---------------------------------------------------------------------------


def test_cortex_rag_adapter_records_the_embedding_failure(monkeypatch):
    from tools.cortex import search_service

    class _Failing:
        def __init__(self, tenant_id=""):
            pass

        def search(self, query, **kwargs):
            raise EmbeddingUnavailableError("no embedding provider is available")

    monkeypatch.setattr(
        search_service,
        "_backend",
        lambda module: type("M", (), {"RAGRetriever": _Failing,
                                      "EmbeddingUnavailableError":
                                          EmbeddingUnavailableError}),
    )
    out = search_service.search_rag("q")
    assert list(out) == []
    assert [e["stage"] for e in out.errors] == ["embedding"]
    assert out.errors[0]["backend"] == "rag"


def test_every_adapter_records_its_failure_not_just_rag(monkeypatch):
    """The other three backends had the same defect — an empty list on death."""
    from tools.cortex import search_service

    def _explode(module):
        raise RuntimeError("backend module is down")

    monkeypatch.setattr(search_service, "_backend", _explode)
    for adapter, backend in (
        (search_service.search_graph, "graph"),
        (search_service.search_dic, "dic"),
        (search_service.search_kb, "kb"),
    ):
        out = adapter("q")
        assert list(out) == []
        assert out.errors[0]["backend"] == backend
        assert out.errors[0]["stage"] == "error"


def test_backend_results_is_a_plain_list_to_every_other_consumer():
    from tools.cortex.search_service import BackendResults

    results = BackendResults(["a", "b"], errors=[{"backend": "rag"}])
    assert isinstance(results, list)
    assert len(results) == 2 and results[0] == "a"
    assert list(results) == ["a", "b"]
    assert getattr(BackendResults(), "errors") == []


def test_chat_answer_distinguishes_failure_from_no_match():
    from tools.cortex.blueprint import _response_from_search
    from tools.cortex.search_service import BackendResults

    genuine = _response_from_search(BackendResults([]))
    assert genuine["answer"] == (
        "No matching results were found across the Cortex backends."
    )
    assert genuine["degraded"] is False
    assert genuine["governance"]["outcomes"]["retrieval"] == "warn"

    failed = _response_from_search(
        BackendResults(
            [],
            errors=[
                {
                    "backend": "rag",
                    "stage": "embedding",
                    "message": "no embedding provider is available",
                }
            ],
        )
    )
    assert failed["answer"] != genuine["answer"]
    assert "no embedding provider is available" in failed["answer"]
    assert failed["degraded"] is True
    assert failed["governance"]["outcomes"]["retrieval"] == "error"
    assert failed["governance"]["backend_errors"][0]["stage"] == "embedding"


def test_hits_from_a_surviving_backend_are_not_reported_as_degraded():
    """One backend failing must not turn a partial answer into an error."""
    from tools.cortex.blueprint import _response_from_search
    from tools.cortex.search_service import BackendResults

    hit = type("R", (), {"content": "an actual answer", "citation": None})()
    resp = _response_from_search(
        BackendResults([hit], errors=[{"backend": "rag", "stage": "embedding",
                                       "message": "down"}])
    )
    assert resp["degraded"] is False
    assert "an actual answer" in resp["answer"]
