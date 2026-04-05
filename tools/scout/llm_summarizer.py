#!/usr/bin/env python3
# CUI // SP-CTI
"""Scout LLM Summarizer — Ollama-only synthesis of daily findings.

Uses scanner-tier LLM (qwen3.5 via Ollama) to produce a concise synthesis
of all Scout findings. Zero Claude cost. Gracefully degrades if Ollama
is unavailable — digest is generated without synthesis section.
"""

import sys
from pathlib import Path
from typing import List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def _build_prompt(findings: List[dict]) -> str:
    """Build a structured prompt from findings across all pillars."""
    sections = {}
    for f in findings:
        pillar = f.get("pillar", "unknown")
        if pillar not in sections:
            sections[pillar] = []
        sections[pillar].append(f)

    prompt_parts = [
        "You are an AI DevOps analyst reviewing daily Scout scan results for ICDEV™, "
        "an Intelligent Certified Development platform for Gov/DoD applications.\n\n"
        "Summarize the key findings below in 2-3 paragraphs. Focus on:\n"
        "1. The most impactful discovery for improving ICDEV™\n"
        "2. Emerging trends worth watching\n"
        "3. Specific actionable recommendations\n\n"
        "Be concise and direct. No fluff.\n\n"
        "--- FINDINGS ---\n"
    ]

    for pillar, items in sections.items():
        prompt_parts.append(f"\n## {pillar.upper()} ({len(items)} findings)\n")
        for item in sorted(items, key=lambda x: x.get("relevance_score", 0), reverse=True)[:10]:
            score = item.get("relevance_score", 0)
            prompt_parts.append(f"- [{score:.2f}] {item['title']}: {item.get('description', '')[:150]}\n")

    return "".join(prompt_parts)


def synthesize(findings: List[dict], config: dict = None) -> Optional[str]:
    """Use Ollama to synthesize findings into a summary paragraph.

    Returns None if Ollama is unavailable or synthesis fails.
    Never raises — caller gets None and omits synthesis section.
    """
    if not findings:
        return None

    config = config or {}
    llm_cfg = config.get("llm", {})
    function_name = llm_cfg.get("function_name", "memory_consolidation")
    max_tokens = llm_cfg.get("summarize_max_tokens", 2048)

    prompt = _build_prompt(findings)

    # Try via LLM router (scanner tier → Ollama)
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.3,
            skip_injection_scan=True,  # Trusted internal pipeline
        )
        response = router.invoke(function_name, request)
        if response and response.content:
            return response.content.strip()
    except Exception:
        pass

    # Fallback: direct Ollama call
    try:
        from tools.llm.ollama_provider import OllamaProvider

        provider = OllamaProvider(base_url="http://localhost:11434")
        from tools.llm.provider import LLMRequest as LR

        req = LR(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.3,
        )
        resp = provider.invoke(req, "qwen3.5:latest", {})
        if resp and resp.content:
            return resp.content.strip()
    except Exception:
        pass

    return None
