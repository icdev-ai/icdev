# CUI // SP-CTI
"""LLM Threshold Advisor — AAC generator for hardcoded_threshold → llm_generation.

Given a detected hardcoded numeric threshold in source code, this module uses an
LLM to produce:
  - A human-readable explanation of the threshold's purpose
  - A configurable replacement (env var, YAML key, or runtime argument)
  - A ready-to-use code snippet replacing the literal with a dynamic lookup
  - A confidence rating and fallback recommendation

Public API
----------
advise_threshold(context: ThresholdContext) -> ThresholdAdvice
"""
from __future__ import annotations

import os
import textwrap
from dataclasses import dataclass
from typing import Any

# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class ThresholdContext:
    """Everything the advisor needs to reason about the hardcoded threshold."""
    module_path: str
    function_name: str
    line_number: int
    original_line: str          # raw source line containing the literal
    threshold_value: Any        # the numeric value (int or float)
    operator: str               # '<', '>', '<=', '>=', '=='
    language: str = "python"
    surrounding_code: str = ""  # up to 10 lines of context around the threshold


@dataclass
class ThresholdAdvice:
    """LLM-generated replacement recommendation."""
    purpose_explanation: str
    config_key: str             # e.g. "REQUESTS_MAX_RETRIES" or "http.connect_timeout_s"
    config_type: str            # "env_var" | "yaml_key" | "function_arg"
    default_value: Any
    replacement_snippet: str    # drop-in Python/Java/Go/… code
    confidence: float           # 0.0–1.0; < 0.6 = low confidence, use fallback
    fallback_recommendation: str
    model_used: str = ""
    raw_response: str = ""
    error: str = ""


# ── Prompt templates ───────────────────────────────────────────────────────────

_SYSTEM_PROMPT = textwrap.dedent("""\
    You are an expert software architect specializing in replacing hardcoded
    numeric constants with dynamic, environment-aware configuration.
    Your output must be valid JSON with no markdown fences.
""")

_USER_TEMPLATE = textwrap.dedent("""\
    A static code analysis scan flagged a hardcoded numeric threshold in the
    following {language} code.

    File: {module_path}
    Function: {function_name}
    Line {line_number}: {original_line}
    Threshold value: {threshold_value}
    Operator: {operator}

    Surrounding context:
    {surrounding_code}

    Please analyze this threshold and respond with a JSON object containing:
    {{
      "purpose_explanation": "<one-sentence explanation of what this threshold controls>",
      "config_key": "<recommended config key, e.g. REQUESTS_CONNECT_TIMEOUT>",
      "config_type": "<one of: env_var, yaml_key, function_arg>",
      "default_value": <recommended default — same type as the original>,
      "replacement_snippet": "<complete drop-in code replacing the hardcoded literal with a dynamic lookup, using the config_key above>",
      "confidence": <float 0.0-1.0 reflecting how certain you are this is a configurable threshold>,
      "fallback_recommendation": "<what to do if confidence is low, e.g. 'leave as-is — this is a protocol constant'>"
    }}
""")


# ── LLM invocation ─────────────────────────────────────────────────────────────

def _call_llm(system: str, user: str, model: str) -> tuple[str, str]:
    """Return (raw_text, model_used). Tries Anthropic SDK first, then OpenAI."""
    # Anthropic path
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            msg = client.messages.create(
                model=model,
                max_tokens=1024,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return msg.content[0].text, model
        except Exception as exc:
            # Fall through to OpenAI or stub
            _last_err = str(exc)

    # OpenAI-compatible path (e.g. local Ollama)
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    openai_base = os.environ.get("OPENAI_BASE_URL", "")
    if openai_key or openai_base:
        try:
            import openai as _oai
            client = _oai.OpenAI(
                api_key=openai_key or "ollama",
                base_url=openai_base or None,
            )
            resp = client.chat.completions.create(
                model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=1024,
            )
            used = resp.model or "openai"
            return resp.choices[0].message.content or "", used
        except Exception:
            pass

    return "", "none"


# ── Core advisor ───────────────────────────────────────────────────────────────

def advise_threshold(ctx: ThresholdContext, model: str = "claude-haiku-4-5-20251001") -> ThresholdAdvice:
    """Analyse a hardcoded threshold and return an LLM-generated replacement plan.

    Falls back to a deterministic stub when no LLM credentials are available,
    so the function is always safe to call in air-gap environments.
    """
    user_prompt = _USER_TEMPLATE.format(
        language=ctx.language,
        module_path=ctx.module_path,
        function_name=ctx.function_name,
        line_number=ctx.line_number,
        original_line=ctx.original_line.strip(),
        threshold_value=ctx.threshold_value,
        operator=ctx.operator,
        surrounding_code=ctx.surrounding_code or "(not available)",
    )

    raw, model_used = _call_llm(_SYSTEM_PROMPT, user_prompt, model)

    if raw:
        return _parse_response(raw, model_used)

    return _deterministic_fallback(ctx)


def _parse_response(raw: str, model_used: str) -> ThresholdAdvice:
    import json as _json
    try:
        data = _json.loads(raw)
        return ThresholdAdvice(
            purpose_explanation=data.get("purpose_explanation", ""),
            config_key=data.get("config_key", "THRESHOLD_VALUE"),
            config_type=data.get("config_type", "env_var"),
            default_value=data.get("default_value"),
            replacement_snippet=data.get("replacement_snippet", ""),
            confidence=float(data.get("confidence", 0.5)),
            fallback_recommendation=data.get("fallback_recommendation", ""),
            model_used=model_used,
            raw_response=raw,
        )
    except Exception as exc:
        return ThresholdAdvice(
            purpose_explanation="",
            config_key="THRESHOLD_VALUE",
            config_type="env_var",
            default_value=None,
            replacement_snippet="",
            confidence=0.0,
            fallback_recommendation="LLM response could not be parsed.",
            model_used=model_used,
            raw_response=raw,
            error=str(exc),
        )


def _deterministic_fallback(ctx: ThresholdContext) -> ThresholdAdvice:
    """Rule-based stub used when no LLM is available."""
    val = ctx.threshold_value
    # Heuristic: hex literals that look like version encodings → low confidence
    is_version_like = isinstance(val, int) and val > 0x010000
    if is_version_like:
        return ThresholdAdvice(
            purpose_explanation=(
                "Appears to be a hex-encoded version number (e.g. 0x020B00 = 2.11.0). "
                "These are protocol/metadata constants, not operational thresholds."
            ),
            config_key="",
            config_type="env_var",
            default_value=val,
            replacement_snippet="",
            confidence=0.15,
            fallback_recommendation=(
                "Leave as-is — this is a version metadata constant, not a configurable threshold."
            ),
            model_used="deterministic_fallback",
        )

    # Generic numeric threshold
    env_key = f"THRESHOLD_{str(val).replace('.', '_').upper()}"
    snippet = _build_snippet(ctx, env_key)
    return ThresholdAdvice(
        purpose_explanation=(
            f"Hardcoded numeric threshold ({val}) controlling a comparison in "
            f"`{ctx.function_name}`. Making this configurable avoids code changes "
            "when the threshold needs tuning."
        ),
        config_key=env_key,
        config_type="env_var",
        default_value=val,
        replacement_snippet=snippet,
        confidence=0.55,
        fallback_recommendation="Verify the threshold is not a protocol constant before making it configurable.",
        model_used="deterministic_fallback",
    )


def _build_snippet(ctx: ThresholdContext, env_key: str) -> str:
    val = ctx.threshold_value
    if ctx.language == "python":
        cast = "float" if isinstance(val, float) else "int"
        return (
            f"import os\n"
            f"{env_key} = {cast}(os.environ.get('{env_key}', {val!r}))\n"
            f"# Original: {ctx.original_line.strip()}\n"
            f"# Replace literal {val!r} with {env_key}"
        )
    if ctx.language == "java":
        return (
            f"// Original: {ctx.original_line.strip()}\n"
            f"final double {env_key} = Double.parseDouble(\n"
            f"    System.getenv(\"{env_key}\") != null\n"
            f"        ? System.getenv(\"{env_key}\") : \"{val}\");"
        )
    return f"// Replace hardcoded {val!r} with {env_key} from environment/config"


# ── CLI entry point ────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="LLM Threshold Advisor (AAC generator)")
    parser.add_argument("--module-path", required=True)
    parser.add_argument("--function-name", default="<unknown>")
    parser.add_argument("--line-number", type=int, default=0)
    parser.add_argument("--original-line", required=True, help="Source line with the literal")
    parser.add_argument("--threshold-value", required=True, help="Numeric value as string")
    parser.add_argument("--operator", default=">")
    parser.add_argument("--language", default="python")
    parser.add_argument("--surrounding-code", default="")
    parser.add_argument("--model", default="claude-haiku-4-5-20251001")
    parser.add_argument("--json", dest="as_json", action="store_true")
    args = parser.parse_args()

    try:
        thresh = int(args.threshold_value, 0)
    except ValueError:
        thresh = float(args.threshold_value)

    ctx = ThresholdContext(
        module_path=args.module_path,
        function_name=args.function_name,
        line_number=args.line_number,
        original_line=args.original_line,
        threshold_value=thresh,
        operator=args.operator,
        language=args.language,
        surrounding_code=args.surrounding_code,
    )
    advice = advise_threshold(ctx, model=args.model)

    if args.as_json:
        import dataclasses
        print(json.dumps(dataclasses.asdict(advice), indent=2))
    else:
        print(f"Purpose : {advice.purpose_explanation}")
        print(f"Config  : {advice.config_type.upper()} → {advice.config_key}")
        print(f"Default : {advice.default_value}")
        print(f"Confidence: {advice.confidence:.2f}")
        print(f"Fallback: {advice.fallback_recommendation}")
        if advice.replacement_snippet:
            print(f"\nReplacement snippet:\n{advice.replacement_snippet}")


if __name__ == "__main__":
    main()
