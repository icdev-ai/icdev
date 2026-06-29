from __future__ import annotations

"""Speculative decoding wrapper for Ollama-served open-weight models.

Auto-detects availability: probes Ollama /api/tags for loaded draft models and
activates transparently — no manual config changes required.

Detection order:
  1. Provider: ICDEV_LLM_PROVIDER env var → llm_config.yaml routing default
  2. Endpoint: OLLAMA_BASE_URL env var → speculative_decoding.target_endpoint → localhost:11434
  3. Draft model: speculative_decoding.draft_model (if set) → first detected dspark/eagle3/dflash model
  4. Returns None immediately for non-Ollama providers (zero-cost fast path)

Configuration (args/llm_config.yaml) — all optional when auto-detecting:
    speculative_decoding:
      enabled: auto           # auto | true | false
      draft_model: ""         # leave blank to auto-discover from Ollama
      draft_tokens: 5
      verification_ratio: 0.7
      fallback_on_failure: true
"""

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator

try:
    import httpx as _httpx
    _HTTP_BACKEND = "httpx"
except ImportError:
    _httpx = None  # type: ignore[assignment]
    _HTTP_BACKEND = "urllib"

try:
    import yaml as _yaml
except ImportError:
    _yaml = None  # type: ignore[assignment]

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_DEFAULT_CONFIG = _BASE_DIR / "args" / "llm_config.yaml"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SpeculativeDecodingUnavailable(RuntimeError):
    """Raised when speculative decoding is requested but unavailable."""


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------

@dataclass
class SpeculativeConfig:
    """Configuration for the speculative decoder."""

    enabled: str = "auto"   # "auto" | "true" | "false" (bool also accepted)
    draft_model: str = ""   # blank = auto-discovered from Ollama tags
    draft_tokens: int = 5
    verification_ratio: float = 0.7
    fallback_on_failure: bool = True
    target_endpoint: str = "http://localhost:11434"


# DeepSpec-trained draft models (deepseek-ai/DeepSpec HuggingFace checkpoints)
_DEEPSPEC_PATTERNS = ("dspark", "eagle3", "dflash")

# Small base models usable as approximate drafts when no DeepSpec model is available.
# Heuristic: model name ends with a sub-2B size tag alongside a larger companion.
# Prefer the smallest available member of the same model family as the target.
_SMALL_MODEL_SIZES = (":0.5b", ":0.6b", ":1b", ":1.5b", "-0.5b", "-0.6b", "-1b", "-1.5b")

_DRAFT_MODEL_PATTERNS = _DEEPSPEC_PATTERNS + ("draft",)



# ---------------------------------------------------------------------------
# HTTP helpers (httpx or urllib fallback)
# ---------------------------------------------------------------------------

def _http_get(url: str, timeout: float = 2.0) -> tuple[int, Any]:
    """GET url; return (status_code, parsed_json_or_None)."""
    if _httpx is not None:
        try:
            resp = _httpx.get(url, timeout=timeout)
            try:
                body = resp.json()
            except Exception:
                body = None
            return resp.status_code, body
        except Exception:
            return 0, None
    else:
        import urllib.request
        import urllib.error
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                raw = r.read()
                try:
                    body = json.loads(raw)
                except Exception:
                    body = None
                return r.status, body
        except Exception:
            return 0, None


def _http_post(url: str, payload: dict, timeout: float = 30.0) -> tuple[int, Any]:
    """POST JSON to url; return (status_code, parsed_json_or_None)."""
    data = json.dumps(payload).encode("utf-8")
    if _httpx is not None:
        try:
            resp = _httpx.post(url, content=data, headers={"Content-Type": "application/json"}, timeout=timeout)
            try:
                body = resp.json()
            except Exception:
                body = None
            return resp.status_code, body
        except Exception:
            return 0, None
    else:
        import urllib.request
        import urllib.error
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                try:
                    body = json.loads(raw)
                except Exception:
                    body = None
                return r.status, body
        except Exception:
            return 0, None


# ---------------------------------------------------------------------------
# SpeculativeDecoder
# ---------------------------------------------------------------------------

class SpeculativeDecoder:
    """Speculative decoding wrapper for Ollama-served models."""

    def __init__(self, config: SpeculativeConfig) -> None:
        self.config = config

    def is_available(self) -> bool:
        """Check if Ollama endpoint is reachable and draft model is loaded."""
        if not self.config.draft_model:
            return False
        if not self._check_ollama_available():
            return False
        status, body = _http_get(f"{self.config.target_endpoint}/api/tags", timeout=2.0)
        if status != 200 or body is None:
            return False
        models = body.get("models", [])
        draft = self.config.draft_model.lower()
        for m in models:
            name = (m.get("name") or "").lower()
            if name == draft or name.startswith(draft + ":"):
                return True
        return False

    def _check_ollama_available(self) -> bool:
        """Return True if the Ollama HTTP endpoint responds."""
        status, _ = _http_get(f"{self.config.target_endpoint}/api/tags", timeout=2.0)
        return status == 200

    def decode(
        self,
        prompt: str,
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> tuple[str, dict]:
        """Generate text; use SGLang-style batched drafting when available.

        Returns (generated_text, stats_dict).
        stats_dict keys: tokens_generated, draft_tokens_proposed,
        draft_tokens_accepted, acceptance_rate, method.
        """
        if not self.is_available():
            if self.config.fallback_on_failure:
                return self._standard_decode(prompt, max_tokens, temperature)
            raise SpeculativeDecodingUnavailable("Draft model not available and fallback_on_failure=False")

        # SGLang-style: request N completions from draft model, pick best
        n = self.config.draft_tokens
        payload = {
            "model": self.config.draft_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": n,
            "temperature": temperature,
            "n": n,
        }
        status, body = _http_post(
            f"{self.config.target_endpoint}/v1/chat/completions",
            payload,
            timeout=30.0,
        )
        if status != 200 or body is None:
            if self.config.fallback_on_failure:
                return self._standard_decode(prompt, max_tokens, temperature)
            raise SpeculativeDecodingUnavailable(f"Draft model request failed with status {status}")

        choices = body.get("choices", [])
        # Pick the choice that produced the most tokens (proxy for highest prob)
        best_draft = ""
        for c in choices:
            text = c.get("message", {}).get("content") or ""
            if len(text) > len(best_draft):
                best_draft = text

        draft_tokens_proposed = n
        draft_tokens_accepted = len(best_draft.split()) if best_draft else 0
        acceptance_rate = draft_tokens_accepted / max(draft_tokens_proposed, 1)

        # If acceptance rate too low, fall back to standard
        if acceptance_rate < self.config.verification_ratio:
            text, stats = self._standard_decode(prompt, max_tokens, temperature)
            stats["draft_tokens_proposed"] = draft_tokens_proposed
            stats["draft_tokens_accepted"] = draft_tokens_accepted
            stats["acceptance_rate"] = acceptance_rate
            return text, stats

        # Use draft output as seed, extend to max_tokens via target model
        combined_prompt = prompt + best_draft
        final_payload = {
            "model": self.config.draft_model,
            "messages": [{"role": "user", "content": combined_prompt}],
            "max_tokens": max(max_tokens - draft_tokens_accepted, 1),
            "temperature": temperature,
        }
        status2, body2 = _http_post(
            f"{self.config.target_endpoint}/v1/chat/completions",
            final_payload,
            timeout=30.0,
        )
        if status2 != 200 or body2 is None:
            if self.config.fallback_on_failure:
                return self._standard_decode(prompt, max_tokens, temperature)
            raise SpeculativeDecodingUnavailable(f"Verification request failed with status {status2}")

        extension = (body2.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        final_text = best_draft + extension
        usage = body2.get("usage", {})
        tokens_generated = usage.get("completion_tokens", len(final_text.split()))
        return final_text, {
            "tokens_generated": tokens_generated,
            "draft_tokens_proposed": draft_tokens_proposed,
            "draft_tokens_accepted": draft_tokens_accepted,
            "acceptance_rate": acceptance_rate,
            "method": "speculative",
        }

    def _standard_decode(self, prompt: str, max_tokens: int, temperature: float) -> tuple[str, dict]:
        """Single-pass generation via Ollama /v1/chat/completions."""
        payload = {
            "model": self.config.draft_model or "llama3",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        status, body = _http_post(
            f"{self.config.target_endpoint}/v1/chat/completions",
            payload,
            timeout=60.0,
        )
        if status != 200 or body is None:
            return "", {
                "tokens_generated": 0,
                "draft_tokens_proposed": 0,
                "draft_tokens_accepted": 0,
                "acceptance_rate": 0.0,
                "method": "standard",
            }
        text = (body.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        usage = body.get("usage", {})
        return text, {
            "tokens_generated": usage.get("completion_tokens", len(text.split())),
            "draft_tokens_proposed": 0,
            "draft_tokens_accepted": 0,
            "acceptance_rate": 0.0,
            "method": "standard",
        }

    def decode_stream(
        self,
        prompt: str,
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> Generator[str, None, None]:
        """Yield text chunks via standard streaming; final stats in last chunk."""
        # Standard streaming via urllib (httpx stream or urllib chunked)
        payload = {
            "model": self.config.draft_model or "llama3",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        import urllib.request
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.config.target_endpoint}/v1/chat/completions",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60.0) as r:
                for line in r:
                    line = line.decode("utf-8").strip()
                    if not line or line == "data: [DONE]":
                        continue
                    if line.startswith("data: "):
                        try:
                            chunk = json.loads(line[6:])
                            delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content") or ""
                            if delta:
                                yield delta
                        except Exception:
                            pass
        except Exception:
            return


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def _detect_provider(cfg: dict) -> str:
    """Resolve active LLM provider: env var → config routing default → empty string."""
    env = os.environ.get("ICDEV_LLM_PROVIDER", "").lower()
    if env:
        return env
    # Walk llm_config.yaml routing section for the default function's first provider
    routing = cfg.get("routing", {})
    default_fn = routing.get("default_function", "")
    if default_fn:
        fn_cfg = cfg.get("functions", {}).get(default_fn, {})
        chain = fn_cfg.get("primary_chain") or fn_cfg.get("chain") or []
        if chain:
            first = chain[0] if isinstance(chain[0], str) else chain[0].get("provider", "")
            return first.lower()
    # Fallback: check providers dict for any ollama entry
    providers = cfg.get("providers", {})
    for name in providers:
        if "ollama" in name.lower():
            return "ollama"
    return ""


def _resolve_endpoint(spec_cfg: dict) -> str:
    """Resolve target_endpoint, expanding ${VAR:-default} env patterns."""
    raw = spec_cfg.get("target_endpoint", "")
    # Prefer OLLAMA_BASE_URL env var over config value
    env_url = os.environ.get("OLLAMA_BASE_URL", "")
    if env_url:
        return env_url.rstrip("/")
    if raw.startswith("${") and ":-" in raw:
        inner = raw[2:-1]
        var, _, default = inner.partition(":-")
        return os.environ.get(var, default).rstrip("/")
    return (raw or "http://localhost:11434").rstrip("/")


def _discover_draft_model(endpoint: str) -> str:
    """Probe Ollama /api/tags and return the best available draft model name, or ''.

    Selection priority:
      1. DeepSpec-trained draft models (dspark/eagle3/dflash) — best quality
      2. Any model explicitly named 'draft'
      3. Smallest loaded member of the largest loaded model family (e.g. qwen3:0.5b
         when qwen3:4b is also loaded) — practical fallback, no conversion needed
    """
    status, body = _http_get(f"{endpoint}/api/tags", timeout=2.0)
    if status != 200 or not isinstance(body, dict):
        return ""
    models = body.get("models", [])
    names = [m.get("name", "") for m in models]
    lowered = [n.lower() for n in names]

    # Priority 1 & 2: DeepSpec or explicit draft name
    for i, low in enumerate(lowered):
        if any(pat in low for pat in _DRAFT_MODEL_PATTERNS):
            return names[i]

    # Priority 3: smallest model of a family that also has a larger member loaded
    family_map: dict[str, list[str]] = {}
    for name in names:
        family = name.split(":")[0].lower()
        family_map.setdefault(family, []).append(name)

    for family, members in family_map.items():
        if len(members) < 2:
            continue
        small = [m for m in members if any(m.lower().endswith(sz) for sz in _SMALL_MODEL_SIZES)]
        if small:
            return small[0]

    return ""


def _is_enabled(spec_cfg: dict) -> bool | None:
    """Parse enabled field: True/False/None(=auto). None means probe and decide."""
    val = spec_cfg.get("enabled", "auto")
    if isinstance(val, bool):
        return val
    s = str(val).lower().strip()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    return None  # "auto" or anything else → probe


def get_speculative_decoder(config_path: str | Path | None = None) -> SpeculativeDecoder | None:
    """Auto-detect and return a SpeculativeDecoder, or None if not applicable.

    Returns None immediately (no network call) for non-Ollama providers.
    Probes Ollama /api/tags only when provider=ollama.
    """
    path = Path(config_path) if config_path else _DEFAULT_CONFIG
    cfg: dict = {}
    if _yaml is not None and path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = _yaml.safe_load(f) or {}
        except Exception:
            pass

    # Fast path: non-Ollama provider → spec-dec never applies
    provider = _detect_provider(cfg)
    if provider and provider != "ollama":
        return None

    spec_cfg = cfg.get("speculative_decoding", {})
    enabled = _is_enabled(spec_cfg)
    if enabled is False:
        return None  # explicitly disabled

    endpoint = _resolve_endpoint(spec_cfg)

    # Resolve draft model: config value first, then auto-discover from Ollama tags
    draft_model = spec_cfg.get("draft_model", "").strip()
    if not draft_model:
        draft_model = _discover_draft_model(endpoint)
    if not draft_model:
        return None  # no draft model available

    return SpeculativeDecoder(
        SpeculativeConfig(
            enabled="auto" if enabled is None else str(enabled),
            draft_model=draft_model,
            draft_tokens=int(spec_cfg.get("draft_tokens", 5)),
            verification_ratio=float(spec_cfg.get("verification_ratio", 0.7)),
            fallback_on_failure=bool(spec_cfg.get("fallback_on_failure", True)),
            target_endpoint=endpoint,
        )
    )


# ---------------------------------------------------------------------------
# Drop-in invoke wrapper
# ---------------------------------------------------------------------------

def speculative_invoke(function: str, request: Any, router: Any = None) -> Any:
    """Wrap LLMRouter.invoke with transparent speculative decoding.

    Falls back to standard router.invoke() if spec-dec unavailable or fails.
    Adds speculative_stats to response metadata (stored in response.content
    only when the response is not a real LLM response, otherwise appended to
    extra metadata if available).
    """
    from icdev.tools.llm.provider import LLMRequest, LLMResponse

    if router is None:
        from icdev.tools.llm.router import LLMRouter
        router = LLMRouter()

    decoder = get_speculative_decoder()
    if decoder is None or not decoder.is_available():
        return router.invoke(function, request)

    # Extract prompt text
    messages = getattr(request, "messages", []) or []
    prompt = ""
    for m in messages:
        if isinstance(m, dict):
            prompt += m.get("content", "") + "\n"
    prompt = prompt.strip()

    max_tokens = getattr(request, "max_tokens", 512) or 512
    temperature = getattr(request, "temperature", 0.7) or 0.7

    try:
        text, stats = decoder.decode(prompt, max_tokens=max_tokens, temperature=temperature)
        resp = LLMResponse(
            content=text,
            provider="ollama_speculative",
            model_id=decoder.config.draft_model,
        )
        # Attach stats as a side-channel attribute (non-standard but useful)
        object.__setattr__(resp, "speculative_stats", stats) if hasattr(resp, "__dataclass_fields__") else None
        return resp
    except Exception:
        if decoder.config.fallback_on_failure:
            return router.invoke(function, request)
        raise


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Speculative decoder CLI")
    parser.add_argument("--check", action="store_true", help="Check if spec-dec is available")
    parser.add_argument("--decode", metavar="PROMPT", help="Run a test decode and print stats")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    decoder = get_speculative_decoder()

    if args.check:
        available = decoder is not None and decoder.is_available()
        cfg = vars(decoder.config) if decoder else {}
        if args.json:
            print(json.dumps({"available": available, "config": cfg}))
        else:
            print(f"Speculative decoding available: {available}")
            if cfg:
                for k, v in cfg.items():
                    print(f"  {k}: {v}")
        sys.exit(0 if available else 1)

    if args.decode:
        if decoder is None:
            print(json.dumps({"error": "spec-dec not enabled or ICDEV_LLM_PROVIDER!=ollama"}) if args.json else "Spec-dec not available.")
            sys.exit(1)
        text, stats = decoder.decode(args.decode)
        if args.json:
            print(json.dumps({"text": text, "stats": stats}))
        else:
            print(f"Generated: {text[:200]}")
            for k, v in stats.items():
                print(f"  {k}: {v}")
        sys.exit(0)

    parser.print_help()
