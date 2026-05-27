# CUI // SP-CTI
"""Subagent spawner for the agent toolkit (OPT-67).

Lets an agent delegate a sub-task to an isolated LLM invocation with a
constrained prompt. Pattern modeled after open-swe's 'task' tool and
deepagents' subagent spawner, but implemented on top of
tools.llm.router.LLMRouter so it works with any configured provider
(Anthropic, Bedrock, Vertex, Ollama, etc.).

Subagents are stateless: they receive a prompt, return a response,
then terminate. No shared memory with the parent. This is deliberate —
parallel subagent runs don't race on shared state.
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import time

logger = get_logger("icdev.agent_toolkit.subagent")


def spawn_subagent(
    prompt: str,
    system_prompt: str = "",
    function: str = "code_generation",
    max_tokens: int = 4096,
    temperature: float = 0.3,
    timeout: int = 120,
    agent_id: str = "agent_toolkit_subagent",
    project_id: str = "agent_toolkit",
    model: str = "",
) -> dict:
    """Run a sub-agent LLM call and return its response.

    Args:
        prompt: User-visible prompt (sent as role='user').
        system_prompt: Optional system prompt.
        function: ICDEV LLMRouter function name. Default 'code_generation'.
            Other useful values: 'memory_consolidation', 'narrative_generation',
            'compliance_export'. See args/llm_config.yaml routing.
        max_tokens: Response token budget.
        temperature: LLM sampling temperature. Low = deterministic.
        timeout: Wall-clock cap (soft — LLMRouter may not enforce strictly).
        agent_id: Identity for token budget tracking.
        project_id: Audit tag.
        model: Optional explicit model override. If empty, LLMRouter
            picks based on function.

    Returns:
        Dict with: content (str), provider (str), model_id (str),
        input_tokens (int), output_tokens (int), duration_ms (int),
        stop_reason (str), error (str | None).

    Does NOT raise. On failure, returns error in the dict and
    content='' so the caller can decide how to react.
    """
    result: dict = {
        "content": "",
        "provider": "",
        "model_id": "",
        "input_tokens": 0,
        "output_tokens": 0,
        "duration_ms": 0,
        "stop_reason": "",
        "error": None,
    }

    t0 = time.time()
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=system_prompt,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            agent_id=agent_id,
            project_id=project_id,
        )
        response = router.invoke(function, request)
        result["content"] = response.content or ""
        result["provider"] = response.provider or ""
        result["model_id"] = response.model_id or ""
        result["input_tokens"] = response.input_tokens or 0
        result["output_tokens"] = response.output_tokens or 0
        result["stop_reason"] = response.stop_reason or ""
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        logger.warning("spawn_subagent failed: %s", exc)

    result["duration_ms"] = int((time.time() - t0) * 1000)

    # Enforce timeout after-the-fact (informational; LLMRouter does the
    # real enforcement via provider timeouts)
    if result["duration_ms"] > timeout * 1000 and result["error"] is None:
        result["error"] = (
            f"subagent duration {result['duration_ms']}ms exceeded "
            f"timeout {timeout * 1000}ms (soft cap)"
        )

    return result
