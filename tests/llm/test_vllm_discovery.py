# CUI // SP-CTI
"""omx-vllm-02: vLLM discovery against a FAKE OpenAI-compatible server.

No GPU and no running vLLM: a stdlib http.server on a free port answers
/v1/models (with vLLM's max_model_len) and /v1/chat/completions (accepting or
refusing a tools request the way vLLM does with/without --enable-auto-tool-choice).
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from tools.llm import vllm_discovery
from tools.llm.router import LLMRouter

REPO_ROOT = Path(__file__).resolve().parents[2]


class _FakeVLLM:
    """A served-model list plus a tool-call mode: 'on' (200), 'off' (400), 'broken' (500)."""

    def __init__(self, models, tool_mode="on"):
        self.models = models
        self.tool_mode = tool_mode
        self.tool_probes = 0
        self.model_lists = 0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # keep pytest output clean
                pass

            def _send(self, code, payload):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/v1/models":
                    fake.model_lists += 1
                    self._send(200, {"object": "list", "data": [
                        {"id": mid, "object": "model", "max_model_len": mml}
                        for mid, mml in fake.models.items()
                    ]})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.path != "/v1/chat/completions":
                    self._send(404, {"error": "not found"})
                    return
                if body.get("tools"):
                    fake.tool_probes += 1
                    if fake.tool_mode == "off":
                        self._send(400, {"message": '"auto" tool choice requires '
                                                    "--enable-auto-tool-choice and --tool-call-parser to be set"})
                        return
                    if fake.tool_mode == "broken":
                        self._send(500, {"message": "engine dead"})
                        return
                self._send(200, {"choices": [{"message": {"role": "assistant", "content": "p"}}]})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    vllm_discovery.clear_caches()
    for var in ("VLLM_BASE_URL", "VLLM_MODEL", "VLLM_API_KEY", "ICDEV_LLM_DEFAULT_MODEL", "ICDEV_LLM_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    yield
    vllm_discovery.clear_caches()


def _router(tmp_path, chain=("vllm-local", "fallback-local")):
    cfg = {
        "providers": {
            "vllm": {"type": "openai_compatible", "locality": "local",
                     "base_url": "${VLLM_BASE_URL:-http://localhost:8000/v1}", "api_key_env": "VLLM_API_KEY"},
        },
        "models": {
            "vllm-local": {"provider": "vllm", "model_id": "${VLLM_MODEL:-}",
                           "context_window": 4096, "max_output_tokens": 2048, "supports_tools": False},
            "fallback-local": {"provider": "vllm", "model_id": "fallback-model"},
        },
        "routing": {"default": {"chain": list(chain)}, "proposal_drafting": {"chain": list(chain)}},
        "settings": {
            "ollama_discovery": {"enabled": False},
            "vllm_discovery": {"enabled": True, "probe_providers": ["vllm"],
                               "probe_only_if_env": "VLLM_BASE_URL", "probe_tools": True,
                               "timeout_seconds": 3, "refresh_interval_seconds": 3600},
        },
    }
    path = tmp_path / "llm_config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return LLMRouter(config_path=str(path))


def test_served_models_register_with_max_model_len_and_probed_tool_flag(tmp_path, monkeypatch):
    with _FakeVLLM({"Qwen/Qwen2.5-1.5B-Instruct-AWQ": 8192, "tiny": 2048}, tool_mode="on") as fake:
        monkeypatch.setenv("VLLM_BASE_URL", fake.base_url)
        models = _router(tmp_path)._config["models"]

    qwen = models["vllm:Qwen/Qwen2.5-1.5B-Instruct-AWQ"]
    assert qwen["provider"] == "vllm"
    assert qwen["model_id"] == "Qwen/Qwen2.5-1.5B-Instruct-AWQ"
    assert qwen["context_window"] == 8192
    assert qwen["supports_tools"] is True and qwen["tool_call_probe"] == "supported"
    assert "tool_use" in qwen["_default_capabilities"]
    assert models["vllm:tiny"]["context_window"] == 2048
    assert models["vllm:tiny"]["max_output_tokens"] == 2048  # capped at the window


def test_server_without_auto_tool_choice_is_registered_without_tools(tmp_path, monkeypatch):
    with _FakeVLLM({"tiny": 4096}, tool_mode="off") as fake:
        monkeypatch.setenv("VLLM_BASE_URL", fake.base_url)
        tiny = _router(tmp_path)._config["models"]["vllm:tiny"]
    assert tiny["supports_tools"] is False
    assert tiny["tool_call_probe"] == "unsupported"


def test_tool_probe_runs_once_and_is_cached(tmp_path, monkeypatch):
    with _FakeVLLM({"a": 4096, "b": 4096}, tool_mode="off") as fake:
        monkeypatch.setenv("VLLM_BASE_URL", fake.base_url)
        _router(tmp_path)
        _router(tmp_path)
        vllm_discovery.probe_tool_support(fake.base_url, "a")
        assert fake.tool_probes == 2  # one per served model, never repeated


def test_inconclusive_probe_is_not_cached_and_never_claims_tools(tmp_path, monkeypatch):
    with _FakeVLLM({"a": 4096}, tool_mode="broken") as fake:
        monkeypatch.setenv("VLLM_BASE_URL", fake.base_url)
        a = _router(tmp_path)._config["models"]["vllm:a"]
        assert a["supports_tools"] is False and a["tool_call_probe"] == "unknown"
        vllm_discovery.probe_tool_support(fake.base_url, "a")
        assert fake.tool_probes == 2  # unknown is re-asked, not remembered


def test_vllm_local_takes_served_facts_when_vllm_model_is_set(tmp_path, monkeypatch):
    with _FakeVLLM({"tiny": 3072}, tool_mode="on") as fake:
        monkeypatch.setenv("VLLM_BASE_URL", fake.base_url)
        monkeypatch.setenv("VLLM_MODEL", "tiny")
        router = _router(tmp_path)
    alias = router._config["models"]["vllm-local"]
    assert alias["model_id"] == "tiny"
    assert alias["context_window"] == 3072 and alias["supports_tools"] is True
    assert not alias.get("_model_id_unset")


def test_vllm_model_unset_makes_alias_unavailable_and_chain_falls_through(tmp_path, monkeypatch):
    router = _router(tmp_path)
    assert router._config["models"]["vllm-local"]["_model_id_unset"] is True

    class _Up:
        def check_availability(self, model_id):
            return True

    monkeypatch.setattr(router, "_get_provider", lambda name: _Up())
    assert router._check_model_available("vllm-local") is False
    _provider, model_id, _cfg = router.get_provider_for_function("proposal_drafting")
    assert model_id == "fallback-model"


def test_discovery_does_not_dial_when_vllm_base_url_is_unset(tmp_path):
    with _FakeVLLM({"a": 4096}) as fake:
        models = _router(tmp_path)._config["models"]
        assert fake.model_lists == 0
    assert not [k for k in models if k.startswith("vllm:")]


def test_status_report_shows_the_endpoint_and_served_models(monkeypatch):
    with _FakeVLLM({"tiny": 2048}, tool_mode="on") as fake:
        monkeypatch.setenv("VLLM_BASE_URL", fake.base_url)
        cfg = {
            "providers": {"vllm": {"type": "openai_compatible", "locality": "local",
                                   "base_url": "${VLLM_BASE_URL:-http://localhost:8000/v1}"}},
            "models": {"vllm-local": {"provider": "vllm", "model_id": ""}},
            "settings": {"vllm_discovery": {"enabled": True, "probe_providers": ["vllm"]}},
        }
        report = vllm_discovery.status_report(cfg)
    entry = report["providers"][0]
    assert entry["base_url"] == fake.base_url and entry["reachable"] is True
    assert entry["counts_as_local"] is True
    assert entry["served_models"][0]["max_model_len"] == 2048
    assert entry["served_models"][0]["tool_call"] == "supported"
    assert entry["registered_as"] == ["vllm:tiny"]
    assert report["vllm_local"]["available"] is False  # VLLM_MODEL unset


def test_status_report_names_an_unreachable_endpoint(monkeypatch):
    with _FakeVLLM({}) as fake:
        dead = fake.base_url
    monkeypatch.setenv("VLLM_BASE_URL", dead)
    cfg = {"providers": {"vllm": {"type": "openai_compatible", "locality": "local",
                                  "base_url": "${VLLM_BASE_URL:-}"}},
           "settings": {"vllm_discovery": {"enabled": True, "probe_providers": ["vllm"]}}}
    entry = vllm_discovery.status_report(cfg, timeout=2)["providers"][0]
    assert entry["reachable"] is False and entry["error"]


def test_shipped_config_places_vllm_local_after_every_ollama_entry():
    cfg = yaml.safe_load((REPO_ROOT / "args" / "llm_config.yaml").read_text(encoding="utf-8"))
    models, routing = cfg["models"], cfg["routing"]
    assert models["vllm-local"]["provider"] == "vllm"
    assert models["vllm-local"]["model_id"] == "${VLLM_MODEL:-}"
    assert cfg["settings"]["vllm_discovery"]["probe_providers"] == ["vllm"]

    carrying = {k for k, v in routing.items() if isinstance(v, dict) and "vllm-local" in (v.get("chain") or [])}
    for fn in ("proposal_drafting", "bid_scoring", "color_review", "rfi_writer_drafting",
               "rfi_editor_drafting", "rfi_reviewer_review", "rfi_researcher_knowledge",
               "rfi_compliance_assessment", "code_generation"):
        assert fn in carrying, fn
    for fn in carrying:
        chain = routing[fn]["chain"]
        pos = chain.index("vllm-local")
        ollama = [i for i, m in enumerate(chain) if models.get(m, {}).get("provider") == "ollama"]
        assert ollama and max(ollama) < pos, (fn, chain)


def test_icdev_llm_doctor_reports_the_endpoint(monkeypatch, capsys):
    from tools.cli.__main__ import main as icdev_main

    with _FakeVLLM({"tiny": 2048}, tool_mode="off") as fake:
        monkeypatch.setenv("VLLM_BASE_URL", fake.base_url)
        assert icdev_main(["llm", "doctor", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    vllm = next(p for p in report["providers"] if p["provider"] == "vllm")
    assert vllm["base_url"] == fake.base_url and vllm["reachable"] is True
    assert vllm["served_models"] == [{"id": "tiny", "max_model_len": 2048, "tool_call": "unsupported",
                                      "tool_evidence": vllm["served_models"][0]["tool_evidence"]}]
    assert icdev_main(["llm"]) == 2
