# [TEMPLATE: CUI // SP-CTI]
"""omx-vllm-03: vLLM in the embedding chain, and DIMENSION safety for every store.

* the real ``vllm-embed`` declaration, pointed at a FAKE OpenAI-shaped embeddings
  endpoint on 127.0.0.1, is selected by the chain and returns its vectors;
* with ``VLLM_EMBED_MODEL`` unset the entry is SKIPPED -- never probed, never
  recorded as a failure;
* a vector whose dimension differs from what a store already holds is REFUSED
  (RAG SQLite store, memory, KG), and nothing is written.
"""

from __future__ import annotations

import json
import sqlite3
import struct
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

from tools.llm.embedding_dimension import (
    DimensionGuard,
    EmbeddingDimensionMismatch,
    blob_dimension,
    recorded_dimension,
)
from tools.llm.router import LLMRouter, LLMUnavailableError

REPO_ROOT = Path(__file__).resolve().parents[2]
FAKE_DIM = 1024


class _FakeEmbeddings(BaseHTTPRequestHandler):
    """POST /v1/embeddings -> OpenAI-shaped response of FAKE_DIM-dimension vectors."""

    calls: list = []

    def do_POST(self):  # noqa: N802 -- http.server API
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        type(self).calls.append({"path": self.path, "model": body.get("model")})
        inputs = body.get("input")
        inputs = inputs if isinstance(inputs, list) else [inputs]
        data = [
            {"object": "embedding", "index": i, "embedding": [0.001 * (i + 1)] * FAKE_DIM}
            for i in range(len(inputs))
        ]
        payload = json.dumps(
            {"object": "list", "data": data, "model": body.get("model"), "usage": {"prompt_tokens": 1, "total_tokens": 1}}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # keep pytest output clean
        pass


@pytest.fixture
def fake_vllm():
    _FakeEmbeddings.calls = []
    server = HTTPServer(("127.0.0.1", 0), _FakeEmbeddings)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()
    server.server_close()


def _real_config() -> dict:
    with open(REPO_ROOT / "args" / "llm_config.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture
def router_factory(tmp_path, monkeypatch):
    """A router over the REAL `vllm` provider + `vllm-embed` model, chain = [vllm-embed].

    The other chain entries are cloud/ollama endpoints a unit test must not call.
    """
    monkeypatch.setattr(LLMRouter, "_embedding_state_path", classmethod(lambda cls: tmp_path / "emb_state.json"))
    monkeypatch.setattr(LLMRouter, "_embedding_unavailable_at", {})
    monkeypatch.delenv("ICDEV_NO_LLM", raising=False)
    monkeypatch.delenv("ICDEV_LLM_PROXY_ENABLED", raising=False)
    monkeypatch.delenv("VLLM_API_KEY", raising=False)
    # The fake endpoint is loopback; a proxy left in the environment (the
    # proxy_resolver tests set HTTPS_PROXY on os.environ directly) would route it away.
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    real = _real_config()
    cfg = {
        "providers": {"vllm": real["providers"]["vllm"]},
        "models": {},
        "routing": {},
        "settings": {},
        "embeddings": {
            "default_chain": ["vllm-embed"],
            "models": {"vllm-embed": real["embeddings"]["models"]["vllm-embed"]},
        },
    }
    path = tmp_path / "llm_config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return lambda: LLMRouter(config_path=str(path))


# ---------------------------------------------------------------------------
# The chain
# ---------------------------------------------------------------------------


def test_vllm_embed_is_declared_after_the_local_ollama_embedder():
    emb = _real_config()["embeddings"]
    chain = emb["default_chain"]
    assert "vllm-embed" in chain
    assert chain.index("vllm-embed") > chain.index("nomic-embed-local")
    spec = emb["models"]["vllm-embed"]
    assert spec["provider"] == "vllm"
    assert spec["base_url"] == "${VLLM_EMBED_BASE_URL:-${VLLM_BASE_URL:-}}"
    assert spec["model_id"] == "${VLLM_EMBED_MODEL:-}"


def test_configured_vllm_embedder_returns_the_endpoints_vectors(fake_vllm, router_factory, monkeypatch):
    monkeypatch.setenv("VLLM_EMBED_BASE_URL", fake_vllm)
    monkeypatch.setenv("VLLM_EMBED_MODEL", "fake-embed-model")
    provider = router_factory().get_embedding_provider()

    vec = provider.embed("hello")
    batch = provider.embed_batch(["a", "b"])

    assert len(vec) == FAKE_DIM
    assert [len(v) for v in batch] == [FAKE_DIM, FAKE_DIM]
    assert batch[0] != batch[1]  # order preserved by index
    # Dimension was not declared (dimensions: 0) -- it was PROBED.
    assert provider.dimensions == FAKE_DIM
    # Locality from THE one definition, judged on the embed URL (127.0.0.1).
    assert provider.provider_name == "local"
    assert {c["model"] for c in _FakeEmbeddings.calls} == {"fake-embed-model"}
    assert all(c["path"] == "/v1/embeddings" for c in _FakeEmbeddings.calls)


def test_embed_base_url_falls_back_to_vllm_base_url(fake_vllm, router_factory, monkeypatch):
    monkeypatch.delenv("VLLM_EMBED_BASE_URL", raising=False)
    monkeypatch.setenv("VLLM_BASE_URL", fake_vllm)
    monkeypatch.setenv("VLLM_EMBED_MODEL", "fake-embed-model")
    assert len(router_factory().get_embedding_provider().embed("x")) == FAKE_DIM


def test_unset_embed_model_skips_the_entry_without_recording_a_failure(fake_vllm, router_factory, monkeypatch):
    monkeypatch.setenv("VLLM_EMBED_BASE_URL", fake_vllm)
    monkeypatch.delenv("VLLM_EMBED_MODEL", raising=False)
    router = router_factory()
    with pytest.raises(LLMUnavailableError):
        router.get_embedding_provider()
    assert _FakeEmbeddings.calls == []  # never probed
    assert "vllm-embed" not in LLMRouter._embedding_unavailable_at


def test_unset_base_urls_skip_the_entry(router_factory, monkeypatch):
    monkeypatch.delenv("VLLM_EMBED_BASE_URL", raising=False)
    monkeypatch.delenv("VLLM_BASE_URL", raising=False)
    monkeypatch.setenv("VLLM_EMBED_MODEL", "fake-embed-model")
    with pytest.raises(LLMUnavailableError):
        router_factory().get_embedding_provider()
    assert "vllm-embed" not in LLMRouter._embedding_unavailable_at


# ---------------------------------------------------------------------------
# Dimension safety
# ---------------------------------------------------------------------------


def test_guard_adopts_the_first_dimension_then_refuses_another():
    guard = DimensionGuard("s", None)
    guard.check([0.0] * 768)
    with pytest.raises(EmbeddingDimensionMismatch) as exc:
        guard.check([0.0] * FAKE_DIM)
    assert (exc.value.recorded, exc.value.incoming) == (768, FAKE_DIM)
    assert "refusing" in str(exc.value)


def test_blob_dimension_reads_both_encodings():
    from tools.rag.sqlite_vector_store import _embedding_to_blob

    assert blob_dimension(struct.pack("768f", *([0.5] * 768))) == 768
    assert blob_dimension(_embedding_to_blob([0.5] * 384, dtype="float16")) == 384
    assert blob_dimension(_embedding_to_blob([0.5] * 384, dtype="float32")) == 384
    assert blob_dimension(None) is None


def _chunk(n: int, dim: int):
    from tools.rag.vector_store_provider import VectorChunk

    return VectorChunk(chunk_id=f"c{n}", content=f"content {n}", embedding=[0.1] * dim, source_type="t")


def test_rag_store_refuses_a_mismatched_dimension_and_writes_nothing(tmp_path):
    from tools.rag.sqlite_vector_store import SQLiteVectorStore

    store = SQLiteVectorStore(db_path=tmp_path / "rag.db")
    assert store.upsert([_chunk(1, 768)]) == 1

    with pytest.raises(EmbeddingDimensionMismatch):
        store.upsert([_chunk(2, FAKE_DIM), _chunk(3, FAKE_DIM)])
    assert store.count() == 1

    assert store.upsert([_chunk(4, 768)]) == 1  # the original embedder still writes


def test_rag_store_refuses_a_mixed_first_batch_into_an_empty_store(tmp_path):
    from tools.rag.sqlite_vector_store import SQLiteVectorStore

    store = SQLiteVectorStore(db_path=tmp_path / "rag.db")
    with pytest.raises(EmbeddingDimensionMismatch):
        store.upsert([_chunk(1, 768), _chunk(2, FAKE_DIM)])
    assert store.count() == 0


class _FixedProvider:
    def __init__(self, dim):
        self.dim = dim

    def embed(self, text):
        return [0.2] * self.dim


def test_kg_embeddings_refuse_a_mismatched_dimension(tmp_path, monkeypatch):
    from tools.db.storage import StorageConnection
    from tools.knowledge_graph import enricher

    raw = sqlite3.connect(str(tmp_path / "kg.db"))
    raw.row_factory = sqlite3.Row
    raw.execute("CREATE TABLE kg_nodes (id TEXT PRIMARY KEY, graph_id TEXT, label TEXT, entity_type TEXT, embedding BLOB)")
    raw.execute("INSERT INTO kg_nodes VALUES ('n0', 'g', 'old', 'x', ?)", (struct.pack("768f", *([0.1] * 768)),))
    raw.execute("INSERT INTO kg_nodes VALUES ('n1', 'g', 'new', 'x', NULL)")
    raw.commit()
    conn = StorageConnection(raw, "sqlite")

    monkeypatch.setattr("tools.llm.get_embedding_provider", lambda: _FixedProvider(FAKE_DIM))
    result = enricher.compute_embeddings("g", conn=conn)

    assert result["status"] == "refused"
    assert result["nodes_embedded"] == 0
    assert "refusing" in result["error"]
    assert raw.execute("SELECT embedding FROM kg_nodes WHERE id='n1'").fetchone()[0] is None
    assert recorded_dimension(conn, "kg_nodes") == 768
