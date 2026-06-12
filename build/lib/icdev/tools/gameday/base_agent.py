# CUI // SP-CTI
"""AI GameDay League — base agent backed by local Ollama.

All 16 team members inherit from GameDayAgent. Each call goes to
Ollama /api/chat (never /api/generate) with a structured JSON response request.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import time
from typing import Any

from .constants import OLLAMA_BASE_URL, DEFAULT_AGENT_MODEL
from .db import log_llmops_event

log = get_logger(__name__)

try:
    import requests as _requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


class GameDayAgent:
    """Single team member agent backed by Ollama /api/chat."""

    def __init__(
        self,
        name: str,
        role: str,
        team_key: str,
        specialty: str,
        system_prompt: str,
        model: str = DEFAULT_AGENT_MODEL,
        ollama_url: str = OLLAMA_BASE_URL,
        time_budget_seconds: int = 480,  # 8 min default
    ):
        self.name = name
        self.role = role
        self.team_key = team_key
        self.specialty = specialty
        self.system_prompt = system_prompt
        self.model = model
        self.ollama_url = ollama_url.rstrip("/")
        self.time_budget_seconds = time_budget_seconds

    # ──────────────────────────────────────────────────────────────────────────

    def run(
        self,
        user_prompt: str,
        context: dict | None = None,
        tournament_id: int | None = None,
        round_id: int | None = None,
    ) -> dict[str, Any]:
        """Run one inference call. Returns parsed JSON dict + metadata."""
        if not HAS_REQUESTS:
            return self._unavailable("requests library not installed")

        context_str = ""
        if context:
            context_str = "\n\nContext from previous team members:\n" + json.dumps(context, indent=2)

        messages = [
            {"role": "user", "content": user_prompt + context_str},
        ]

        payload = {
            "model": self.model,
            "messages": messages,
            "system": self.system_prompt,
            "stream": False,
            "options": {
                "temperature": 0.7,
                "num_predict": 2048,
            },
        }

        t0 = time.time()
        latency_ms = 0
        prompt_tokens = 0
        completion_tokens = 0
        error_msg = None

        try:
            resp = _requests.post(
                f"{self.ollama_url}/api/chat",
                json=payload,
                timeout=self.time_budget_seconds,
            )
            resp.raise_for_status()
            data = resp.json()
            latency_ms = int((time.time() - t0) * 1000)

            raw_content = data.get("message", {}).get("content", "")
            prompt_tokens = data.get("prompt_eval_count", 0) or 0
            completion_tokens = data.get("eval_count", 0) or 0

            parsed = self._parse_json_response(raw_content)

        except Exception as exc:
            latency_ms = int((time.time() - t0) * 1000)
            error_msg = str(exc)
            log.warning("[%s/%s] inference error: %s", self.team_key, self.role, exc)
            parsed = {"error": error_msg, "raw": ""}
            raw_content = ""

        if tournament_id is not None:
            log_llmops_event(
                tournament_id=tournament_id,
                round_id=round_id,
                team_key=self.team_key,
                member_role=self.role,
                model=self.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=latency_ms,
                error=error_msg,
            )

        return {
            "team_key":          self.team_key,
            "member_role":       self.role,
            "model":             self.model,
            "parsed":            parsed,
            "raw_content":       raw_content if not error_msg else "",
            "tokens_used":       prompt_tokens + completion_tokens,
            "latency_ms":        latency_ms,
            "error":             error_msg,
        }

    # ──────────────────────────────────────────────────────────────────────────

    def _parse_json_response(self, content: str) -> dict:
        """Extract JSON from model response, tolerating markdown fences."""
        content = content.strip()
        # Strip markdown fence if present
        if content.startswith("```"):
            lines = content.splitlines()
            inner = []
            in_fence = False
            for line in lines:
                if line.startswith("```") and not in_fence:
                    in_fence = True
                    continue
                if line.startswith("```") and in_fence:
                    break
                if in_fence:
                    inner.append(line)
            content = "\n".join(inner)

        # Find first { ... } block
        start = content.find("{")
        end   = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(content[start:end + 1])
            except json.JSONDecodeError:
                pass

        # Fallback: return as raw text payload
        return {"raw_text": content, "parse_failed": True}

    def _unavailable(self, reason: str) -> dict:
        return {
            "team_key":    self.team_key,
            "member_role": self.role,
            "model":       self.model,
            "parsed":      {"error": reason},
            "raw_content": "",
            "tokens_used": 0,
            "latency_ms":  0,
            "error":       reason,
        }
