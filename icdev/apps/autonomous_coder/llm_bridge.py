# CUI // SP-CTI
"""LLM Bridge — unified call interface for ICDEV router, Ollama, and stub backends."""

from __future__ import annotations

import json
import os
import sys
from typing import Any


def make_llm_call(backend: str = "auto") -> Any:
    """Return a callable: (messages: list[dict]) -> (response: str, tokens: int)."""
    if backend == "auto":
        backend = _detect_backend()

    if backend == "icdev":
        return _icdev_call()
    if backend == "ollama":
        return _ollama_call()
    return _stub_call()


def _detect_backend() -> str:
    # Try ICDEV router first
    try:
        sys.path.insert(0, str(_repo_root()))
        from tools.llm.router import LLMRouter
        r = LLMRouter()
        _ = r.get_provider_for_function("code_generation")
        return "icdev"
    except Exception:
        pass
    # Fall back to direct Ollama
    return "ollama"


def _icdev_call():
    sys.path.insert(0, str(_repo_root()))
    from tools.llm.router import LLMRequest, LLMRouter
    router = LLMRouter()

    def call(messages: list[dict]) -> tuple[str, int]:
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        user_msgs = [m for m in messages if m["role"] != "system"]
        req = LLMRequest(messages=user_msgs, system_prompt=system, max_tokens=4096)
        resp = router.invoke("code_generation", req)
        text = resp.content if hasattr(resp, "content") else str(resp)
        tokens = getattr(resp, "total_tokens", len(text) // 4) or len(text) // 4
        return text, tokens

    return call


def _ollama_call():
    import urllib.request

    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    model = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b")

    def call(messages: list[dict]) -> tuple[str, int]:
        payload = json.dumps({"model": model, "messages": messages, "stream": False}).encode()
        req = urllib.request.Request(
            f"{base_url}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read())
        text = data.get("message", {}).get("content", "")
        tokens = data.get("eval_count", len(text) // 4)
        return text, tokens

    return call


def _stub_call():
    """Deterministic stub — used for tests and air-gap environments with no LLM."""

    def call(messages: list[dict]) -> tuple[str, int]:
        user_msg = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        system_msg = next((m["content"] for m in messages if m["role"] == "system"), "")

        if '"steps"' in system_msg or "decompose" in system_msg.lower():
            return (
                '{"steps": ["Define inputs and outputs", "Implement core logic", '
                '"Add error handling", "Write docstring"], "language": "python", "notes": "stub plan"}',
                80,
            )
        if "code block" in system_msg or "implementation" in system_msg.lower():
            task_hint = user_msg[:60].strip().replace("\n", " ")
            code = (
                f'```python\ndef solution():\n    """Auto-generated stub for: {task_hint}"""\n'
                f'    # TODO: implement\n    pass\n```'
            )
            return code, 120
        if "verdict" in system_msg or "reviewer" in system_msg.lower():
            return '{"verdict": "warn", "score": 72, "issues": ["Stub code needs implementation"], "suggestion": "Replace pass with real logic"}', 80

        return '{"result": "stub"}', 30

    return call


def _repo_root():
    from pathlib import Path
    return Path(__file__).parent.parent.parent
