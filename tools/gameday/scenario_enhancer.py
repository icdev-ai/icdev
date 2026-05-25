# CUI // SP-CTI
"""AI GameDay League — scenario enhancer.

After each tournament completes, uses the accumulated training pairs and
judge notes to generate an enhanced next-scenario using the local Ollama
model (qwen3.5:9b).  The enhanced scenario is written back to the
CYBER_SCENARIOS list for the next run.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import os

from .constants import DEFAULT_AGENT_MODEL, OLLAMA_BASE_URL

log = get_logger(__name__)

try:
    import requests as _requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

_ENHANCER_SYSTEM = """\
You are a cyber security scenario designer for an AI red/blue team GameDay competition.
Given the results of a completed tournament round, enhance the next scenario to:
1. Address gaps or weaknesses observed in Red and Blue team outputs
2. Introduce a novel attack vector based on current threat intelligence
3. Increase complexity by one notch from the previous round
4. Ensure the scenario remains completable within 60 minutes

Output ONLY valid JSON matching this schema:
{
  "id": "cs-enhanced-<N>",
  "name": "...",
  "description": "...",
  "attack_brief": "...",
  "defense_brief": "...",
  "innovation_brief": "...",
  "compliance_brief": "..."
}
"""


def enhance_scenario(
    base_scenario: dict,
    round_summaries: list[dict],
    tournament_id: int,
    ollama_url: str | None = None,
) -> dict:
    """Generate an enhanced scenario based on tournament outcomes.

    Falls back to the base_scenario if Ollama is unavailable.
    """
    if not HAS_REQUESTS:
        return base_scenario

    url = (ollama_url or os.environ.get("OLLAMA_BASE_URL", OLLAMA_BASE_URL)).rstrip("/")

    # Build context from round summaries
    insights = []
    for s in round_summaries[-3:]:   # Use last 3 rounds for context
        winner = s.get("judge_result", {}).get("round_winner", "?")
        insights.append({
            "round":    s.get("round_num"),
            "scenario": s.get("scenario", {}).get("name", "?"),
            "winner":   winner,
            "scores":   {
                tk: v.get("total_score", 0)
                for tk, v in s.get("judge_result", {}).get("results", {}).items()
            },
        })

    prompt = (
        f"Previous scenario:\n{json.dumps(base_scenario, indent=2)}\n\n"
        f"Tournament insights (last {len(insights)} rounds):\n"
        f"{json.dumps(insights, indent=2)}\n\n"
        f"Generate an enhanced next scenario."
    )

    payload = {
        "model": DEFAULT_AGENT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "system": _ENHANCER_SYSTEM,
        "stream": False,
        "options": {"temperature": 0.8, "num_predict": 1024},
    }

    try:
        resp = _requests.post(f"{url}/api/chat", json=payload, timeout=120)
        resp.raise_for_status()
        content = resp.json().get("message", {}).get("content", "")
        enhanced = _parse_scenario(content)
        if enhanced:
            log.info("[ScenarioEnhancer] Generated enhanced scenario: %s", enhanced.get("name"))
            return enhanced
    except Exception as exc:
        log.warning("[ScenarioEnhancer] Enhancement failed: %s — using base scenario", exc)

    return base_scenario


def _parse_scenario(content: str) -> dict | None:
    content = content.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        inner, in_fence = [], False
        for line in lines:
            if line.startswith("```") and not in_fence:
                in_fence = True
                continue
            if line.startswith("```") and in_fence:
                break
            if in_fence:
                inner.append(line)
        content = "\n".join(inner)

    start, end = content.find("{"), content.rfind("}")
    if start != -1 and end != -1:
        try:
            data = json.loads(content[start:end + 1])
            required = ("id", "name", "attack_brief", "defense_brief")
            if all(k in data for k in required):
                return data
        except json.JSONDecodeError:
            pass
    return None
