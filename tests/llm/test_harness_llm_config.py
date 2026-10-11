# CUI // SP-CTI
"""omx-vllm-04: ONE VLLM_BASE_URL -> opencode, Pi and Codex configs + routed adapter model.

No GPU and no running vLLM: a stdlib http.server on a free port answers
/v1/models (with vLLM's max_model_len) and accepts or refuses a tools request
the way vLLM does with/without --enable-auto-tool-choice.
"""

from __future__ import annotations

import importlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml

from tools.agents.adapter_base import AgentSession
from tools.agents.adapters.codex_cli import CodexCliAdapter
from tools.agents.adapters.opencode_cli import OpencodeCliAdapter
from tools.agents.adapters.pi_cli import PiCliAdapter
from tools.llm import harness_llm_config as hlc
from tools.llm import vllm_discovery
from tools.llm.router import LLMRouter

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - CI runs 3.11
    tomllib = None

SERVED = "Qwen/Qwen2.5-1.5B-Instruct-AWQ"


class _FakeVLLM:
    def __init__(self, models, tool_mode="on"):
        self.models = models
        self.tool_mode = tool_mode
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
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
                    self._send(200, {"object": "list", "data": [
                        {"id": mid, "object": "model", "max_model_len": mml}
                        for mid, mml in fake.models.items()]})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                if body.get("tools") and fake.tool_mode == "off":
                    self._send(400, {"message": '"auto" tool choice requires --enable-auto-tool-choice'})
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
    for var in ("VLLM_BASE_URL", "VLLM_MODEL", "VLLM_API_KEY", "PI_CODING_AGENT_DIR", "CODEX_HOME",
                "ICDEV_HARNESS_LLM", "ICDEV_OPENCODE_MODEL", "ICDEV_PI_MODEL", "ICDEV_CODEX_MODEL",
                "ICDEV_LLM_DEFAULT_MODEL", "ICDEV_LLM_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    yield
    vllm_discovery.clear_caches()


def _config():
    return {
        "providers": {"vllm": {"type": "openai_compatible", "locality": "local",
                               "base_url": "${VLLM_BASE_URL:-http://localhost:8000/v1}",
                               "api_key_env": "VLLM_API_KEY"}},
        "models": {
            "vllm-local": {"provider": "vllm", "model_id": "${VLLM_MODEL:-}",
                           "context_window": 4096, "supports_tools": False},
            "cloud": {"provider": "anthropic", "model_id": "cloud-model"},
        },
        "routing": {"default": {"chain": ["vllm-local"]}, "code_generation": {"chain": ["vllm-local"]}},
        "settings": {
            "ollama_discovery": {"enabled": False},
            "vllm_discovery": {"enabled": True, "probe_providers": ["vllm"],
                               "probe_only_if_env": "VLLM_BASE_URL", "probe_tools": True,
                               "timeout_seconds": 3, "refresh_interval_seconds": 3600},
        },
    }


def _read_toml(path):
    text = path.read_text(encoding="utf-8")
    return tomllib.loads(text) if tomllib else text


# ---------------------------------------------------------------------------
# One endpoint -> three harness configs
# ---------------------------------------------------------------------------
def test_one_vllm_base_url_yields_opencode_pi_and_codex_configs(tmp_path, monkeypatch):
    home = tmp_path / "home"
    oc = home / ".config" / "opencode" / "opencode.json"
    pi = home / ".pi" / "agent" / "models.json"
    cx = home / ".codex" / "config.toml"
    for path in (oc, pi, cx):
        path.parent.mkdir(parents=True)
    # Operator content that must survive.
    oc.write_text(json.dumps({"mcp": {"icdev": {"type": "local"}}, "autoupdate": False}), encoding="utf-8")
    pi.write_text(json.dumps({"providers": {"ollama": {"baseUrl": "http://127.0.0.1:11434/v1"}}}),
                  encoding="utf-8")
    cx.write_text('model = "keep-me"\n\n[mcp_servers.icdev]\ncommand = "python"\n', encoding="utf-8")

    with _FakeVLLM({SERVED: 8192}, tool_mode="on") as fake:
        monkeypatch.setenv("VLLM_BASE_URL", fake.base_url)
        report = hlc.configure_harnesses("vllm", write=True, home=home, config=_config())

    assert report["error"] is None and report["configured"] is True
    assert sorted(report["written"]) == sorted(str(p) for p in (oc, pi, cx))
    assert report["warnings"] == []

    opencode = json.loads(oc.read_text(encoding="utf-8"))
    provider = opencode["provider"]["vllm"]
    assert provider["npm"] == "@ai-sdk/openai-compatible"
    assert provider["options"]["baseURL"] == fake.base_url
    assert provider["models"][SERVED]["limit"]["context"] == 8192
    assert provider["models"][SERVED]["tool_call"] is True
    assert opencode["mcp"] == {"icdev": {"type": "local"}} and opencode["autoupdate"] is False

    pi_cfg = json.loads(pi.read_text(encoding="utf-8"))
    assert pi_cfg["providers"]["vllm"]["baseUrl"] == fake.base_url
    assert pi_cfg["providers"]["vllm"]["api"] == "openai-completions"
    assert pi_cfg["providers"]["vllm"]["models"] == [
        {"id": SERVED, "input": ["text"], "contextWindow": 8192, "maxTokens": 4096}]
    assert "ollama" in pi_cfg["providers"]

    if tomllib:
        codex = _read_toml(cx)
        assert codex["model_providers"]["vllm"]["base_url"] == fake.base_url
        assert codex["model_providers"]["vllm"]["wire_api"] == "chat"
        assert "env_key" not in codex["model_providers"]["vllm"]  # VLLM_API_KEY unset
        assert codex["model"] == "keep-me" and "icdev" in codex["mcp_servers"]


def test_rerun_replaces_the_codex_block_and_never_writes_a_secret(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("VLLM_API_KEY", "s3cret-value")
    with _FakeVLLM({"tiny": 2048}) as fake:
        monkeypatch.setenv("VLLM_BASE_URL", fake.base_url)
        hlc.configure_harnesses("vllm", write=True, home=home, config=_config())
        hlc.configure_harnesses("vllm", write=True, home=home, config=_config())

    cx = (home / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert cx.count("[model_providers.vllm]") == 1
    for path in (home / ".codex" / "config.toml", home / ".pi" / "agent" / "models.json",
                 home / ".config" / "opencode" / "opencode.json"):
        assert "s3cret-value" not in path.read_text(encoding="utf-8")
    if tomllib:
        assert tomllib.loads(cx)["model_providers"]["vllm"]["env_key"] == "VLLM_API_KEY"
    pi = json.loads((home / ".pi" / "agent" / "models.json").read_text(encoding="utf-8"))
    assert pi["providers"]["vllm"]["apiKey"] == "VLLM_API_KEY"


def test_project_scope_writes_project_opencode_json(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    with _FakeVLLM({"tiny": 2048}) as fake:
        monkeypatch.setenv("VLLM_BASE_URL", fake.base_url)
        report = hlc.configure_harnesses("vllm", write=True, home=tmp_path / "home",
                                         project=project, config=_config())
    assert str(project / "opencode.json") in report["written"]


def test_hand_written_codex_table_is_a_conflict_not_an_overwrite(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cx = home / ".codex" / "config.toml"
    cx.parent.mkdir(parents=True)
    original = '[model_providers.vllm]\nbase_url = "http://mine:9000/v1"\n'
    cx.write_text(original, encoding="utf-8")
    with _FakeVLLM({"tiny": 2048}) as fake:
        monkeypatch.setenv("VLLM_BASE_URL", fake.base_url)
        report = hlc.configure_harnesses("vllm", write=True, home=home, config=_config())
    codex = next(f for f in report["files"] if f["harness"] == "codex")
    assert "already declared by hand" in codex["error"]
    assert cx.read_text(encoding="utf-8") == original
    assert report["configured"] is False
    assert len(report["written"]) == 2  # opencode + pi still written


def test_model_without_tool_calling_produces_a_warning(tmp_path, monkeypatch):
    with _FakeVLLM({"tiny": 2048}, tool_mode="off") as fake:
        monkeypatch.setenv("VLLM_BASE_URL", fake.base_url)
        report = hlc.configure_harnesses("vllm", write=True, home=tmp_path, config=_config())
    assert len(report["warnings"]) == 1
    assert "vllm/tiny tool calling is unsupported" in report["warnings"][0]
    oc = json.loads((tmp_path / ".config" / "opencode" / "opencode.json").read_text(encoding="utf-8"))
    assert oc["provider"]["vllm"]["models"]["tiny"]["tool_call"] is False


def test_auto_is_a_no_op_without_vllm_base_url(tmp_path):
    report = hlc.configure_harnesses("auto", write=True, home=tmp_path, config=_config())
    assert report["configured"] is False and report["error"] is None
    assert "VLLM_BASE_URL is unset" in report["reason"]
    assert not any(tmp_path.iterdir())


def test_explicit_vllm_reports_an_unreachable_endpoint(tmp_path, monkeypatch):
    with _FakeVLLM({"tiny": 2048}) as fake:
        dead = fake.base_url
    monkeypatch.setenv("VLLM_BASE_URL", dead)
    report = hlc.configure_harnesses("vllm", write=True, home=tmp_path, config=_config(), timeout=1)
    assert "unreachable" in report["error"]
    auto = hlc.configure_harnesses("auto", write=True, home=tmp_path, config=_config(), timeout=1)
    assert auto["error"] is None and "unreachable" in auto["reason"]
    assert not any(tmp_path.iterdir())


# ---------------------------------------------------------------------------
# Adapters carry the routed vLLM model
# ---------------------------------------------------------------------------
def _session(**meta):
    return AgentSession(task_id="t-1", prompt="do it", working_dir="", metadata=meta)


def _adapters(monkeypatch):
    out = {"opencode": OpencodeCliAdapter(), "pi": PiCliAdapter(), "codex": CodexCliAdapter()}
    for adapter in out.values():
        monkeypatch.setattr(adapter, "resolve", lambda: "harness")
    return out


def _routed_router(tmp_path, monkeypatch, fake, tool_mode_ok=True):
    """A REAL LLMRouter over the fake server, with vllm-local the routed choice."""
    monkeypatch.setenv("VLLM_BASE_URL", fake.base_url)
    monkeypatch.setenv("VLLM_MODEL", SERVED)
    path = tmp_path / "llm_config.yaml"
    path.write_text(yaml.safe_dump(_config()), encoding="utf-8")
    router = LLMRouter(config_path=str(path))

    class _Up:
        def check_availability(self, model_id):
            return True

    monkeypatch.setattr(router, "_get_provider", lambda name: _Up())
    router_mod = importlib.import_module("tools.llm.router")
    monkeypatch.setattr(router_mod, "LLMRouter", lambda *a, **k: router)
    return router


def test_adapters_pass_the_routed_vllm_model_on_the_command_line(tmp_path, monkeypatch, capsys):
    adapters = _adapters(monkeypatch)
    with _FakeVLLM({SERVED: 8192}, tool_mode="on") as fake:
        _routed_router(tmp_path, monkeypatch, fake)
    monkeypatch.setenv("ICDEV_HARNESS_LLM", "vllm")
    session = _session(llm_function="code_generation")

    oc = adapters["opencode"].build_argv(session)
    assert oc[oc.index("--model") + 1] == f"vllm/{SERVED}"
    pi = adapters["pi"].build_argv(session)
    assert pi[pi.index("--model") + 1] == f"vllm/{SERVED}"
    cx = adapters["codex"].build_argv(session)
    assert cx[cx.index("--model") + 1] == SERVED
    assert cx[cx.index("-c") + 1] == 'model_provider="vllm"'
    assert "WARNING" not in capsys.readouterr().err  # the served model has tool calling


def test_routed_model_without_tool_calling_prints_the_warning(tmp_path, monkeypatch, capsys):
    adapters = _adapters(monkeypatch)
    with _FakeVLLM({SERVED: 8192}, tool_mode="off") as fake:
        _routed_router(tmp_path, monkeypatch, fake)
    monkeypatch.setenv("ICDEV_HARNESS_LLM", "vllm")
    argv = adapters["opencode"].build_argv(_session(llm_function="code_generation"))
    assert f"vllm/{SERVED}" in argv
    err = capsys.readouterr().err
    assert "WARNING: opencode is being pointed at vllm/" in err and "unsupported" in err


class _StubRouter:
    def __init__(self, provider, model_id):
        self._config = {"settings": {"vllm_discovery": {"probe_providers": ["vllm"]}}}
        self._choice = (object(), model_id, {"provider": provider, "model_id": model_id,
                                             "supports_tools": True})

    def get_provider_for_function(self, function):
        return self._choice


def test_no_routed_model_without_the_opt_in(monkeypatch):
    adapters = _adapters(monkeypatch)
    router_mod = importlib.import_module("tools.llm.router")
    monkeypatch.setattr(router_mod, "LLMRouter", lambda *a, **k: _StubRouter("vllm", "tiny"))
    for adapter in adapters.values():
        assert "--model" not in adapter.build_argv(_session(llm_function="code_generation"))


def test_no_routed_model_when_the_router_chose_a_non_vllm_model(monkeypatch):
    adapters = _adapters(monkeypatch)
    monkeypatch.setenv("ICDEV_HARNESS_LLM", "vllm")
    router_mod = importlib.import_module("tools.llm.router")
    monkeypatch.setattr(router_mod, "LLMRouter", lambda *a, **k: _StubRouter("anthropic", "cloud-model"))
    for adapter in adapters.values():
        assert "--model" not in adapter.build_argv(_session(llm_function="code_generation"))


def test_an_explicit_model_beats_the_routed_one(monkeypatch):
    adapters = _adapters(monkeypatch)
    monkeypatch.setenv("ICDEV_HARNESS_LLM", "vllm")
    router_mod = importlib.import_module("tools.llm.router")
    monkeypatch.setattr(router_mod, "LLMRouter", lambda *a, **k: _StubRouter("vllm", "tiny"))
    argv = adapters["pi"].build_argv(_session(llm_function="code_generation", model_id="other/m"))
    assert argv[argv.index("--model") + 1] == "other/m" and argv.count("--model") == 1


def test_a_failing_router_never_breaks_the_dispatch(monkeypatch):
    adapters = _adapters(monkeypatch)
    monkeypatch.setenv("ICDEV_HARNESS_LLM", "vllm")

    def _boom(*a, **k):
        raise RuntimeError("config unreadable")

    router_mod = importlib.import_module("tools.llm.router")
    monkeypatch.setattr(router_mod, "LLMRouter", _boom)
    argv = adapters["codex"].build_argv(_session(llm_function="code_generation"))
    assert "--model" not in argv


def test_icdev_llm_harnesses_is_wired(monkeypatch, capsys):
    from tools.cli.__main__ import main as icdev_main

    assert icdev_main(["llm", "harnesses", "--llm", "none", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["reason"] == "--llm none"
