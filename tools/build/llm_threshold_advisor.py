# CUI // SP-CTI
"""LLM Threshold Advisor — AI Augmentation paradigm: llm_generation.

Addresses the AAC opportunity pattern ``hardcoded_threshold`` (aac-opp-465).
For each hardcoded numeric comparison detected in a source file, an LLM is
invoked to analyse the surrounding context and generate a recommendation:
  - what the threshold represents
  - what config key should own it
  - the suggested value and rationale

Results are returned as structured JSON and, with --write, persisted to
``args/build_config.yaml`` under ``llm_advised_thresholds``.

Public API:
    advise_thresholds(file_path: str, *, write: bool = False) -> list[dict]

CLI:
    python tools/build/llm_threshold_advisor.py --file PATH [--write] [--json]
    python tools/build/llm_threshold_advisor.py --dir  PATH [--write] [--json]
"""

from __future__ import annotations

import argparse
import ast
import json
import pathlib
import sys
from typing import Any

import yaml

_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

_BUILD_CONFIG_PATH = _ROOT / "args" / "build_config.yaml"

# Comparison operators that indicate a threshold
_THRESHOLD_OPS = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)

# Lines of source context sent to the LLM on each side of the match
_CONTEXT_LINES = 8


# ── Config helpers ─────────────────────────────────────────────────────────────

def _load_build_config() -> dict[str, Any]:
    try:
        with open(_BUILD_CONFIG_PATH, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}


def _save_build_config(cfg: dict[str, Any]) -> None:
    with open(_BUILD_CONFIG_PATH, "w", encoding="utf-8") as fh:
        yaml.dump(cfg, fh, default_flow_style=False, sort_keys=False, allow_unicode=True)


# ── AST extraction ─────────────────────────────────────────────────────────────

def _extract_thresholds(file_path: str) -> list[dict[str, Any]]:
    """Return list of hardcoded-threshold hits from a Python source file."""
    source = pathlib.Path(file_path).read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError:
        return []

    lines = source.splitlines()
    hits: list[dict[str, Any]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, _THRESHOLD_OPS) for op in node.ops):
            continue
        comparators = [node.left, *node.comparators]
        consts = [
            c.value
            for c in comparators
            if isinstance(c, ast.Constant) and isinstance(c.value, (int, float))
        ]
        if not consts:
            continue

        line_no: int = node.lineno
        ctx_start = max(0, line_no - 1 - _CONTEXT_LINES)
        ctx_end   = min(len(lines), line_no + _CONTEXT_LINES)
        context_snippet = "\n".join(lines[ctx_start:ctx_end])

        hits.append({
            "file_path": file_path,
            "line": line_no,
            "constants": consts,
            "context_snippet": context_snippet,
            "fingerprint": f"{file_path}:{line_no}:compare",
        })

    return hits


# ── LLM invocation ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are an expert software architect advising on build configuration.
You will be shown a code snippet containing a hardcoded numeric threshold.
Your job is to:
1. Identify what the threshold represents in plain English.
2. Propose a YAML config key name (snake_case) that should own the value.
3. Recommend the appropriate value with a one-sentence rationale.
4. Suggest the replacement code that reads from the config key instead.

Respond ONLY with a JSON object with these exact keys:
{
  "description": "<what the threshold represents>",
  "config_key": "<snake_case_key>",
  "recommended_value": <number or string>,
  "rationale": "<one sentence>",
  "replacement_hint": "<suggested code line>"
}
Do not include markdown fences or any other text."""


def _invoke_llm(context_snippet: str, constants: list) -> dict[str, Any] | None:
    """Call the LLM to generate a threshold recommendation.

    Falls back to a deterministic template if the LLM is unavailable — keeps
    the tool useful in air-gap / no-API-key environments.
    """
    prompt = (
        f"Hardcoded constant(s) detected: {constants}\n\n"
        f"Source context:\n```python\n{context_snippet}\n```\n\n"
        "Provide your JSON recommendation."
    )

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=_SYSTEM_PROMPT,
            max_tokens=512,
            temperature=0.2,
            skip_injection_scan=True,
        )
        resp = router.invoke("code_generation", req)
        text = (resp.content or "").strip()
        return json.loads(text)
    except Exception:
        # Deterministic fallback — no LLM available
        return _fallback_recommendation(constants, context_snippet)


def _fallback_recommendation(constants: list, snippet: str) -> dict[str, Any]:
    """Template-based recommendation used when the LLM is unavailable."""
    val = constants[0] if constants else 0
    return {
        "description": f"Hardcoded numeric threshold ({val}) that could be made configurable.",
        "config_key": "hardcoded_threshold_value",
        "recommended_value": val,
        "rationale": "Externalise to args/build_config.yaml so it can be updated without code changes.",
        "replacement_hint": (
            f"import yaml; cfg = yaml.safe_load(open('args/build_config.yaml')); "
            f"threshold = cfg.get('hardcoded_threshold_value', {val})"
        ),
    }


# ── Public API ─────────────────────────────────────────────────────────────────

def advise_thresholds(
    file_path: str,
    *,
    write: bool = False,
) -> list[dict[str, Any]]:
    """Detect hardcoded thresholds in *file_path* and generate LLM recommendations.

    Args:
        file_path: Path to a Python source file.
        write:     When True, persist recommendations to args/build_config.yaml.

    Returns:
        List of recommendation dicts, one per detected threshold.
    """
    hits = _extract_thresholds(file_path)
    results: list[dict[str, Any]] = []

    cfg = _load_build_config() if write else {}

    for hit in hits:
        advice = _invoke_llm(hit["context_snippet"], hit["constants"])
        if advice is None:
            advice = _fallback_recommendation(hit["constants"], hit["context_snippet"])

        entry: dict[str, Any] = {
            "file_path": hit["file_path"],
            "line": hit["line"],
            "constants": hit["constants"],
            "fingerprint": hit["fingerprint"],
            "recommendation": advice,
        }
        results.append(entry)

        if write:
            advised = cfg.setdefault("llm_advised_thresholds", {})
            advised[hit["fingerprint"]] = {
                "constants": hit["constants"],
                **advice,
            }

    if write and hits:
        _save_build_config(cfg)

    return results


def advise_directory(
    dir_path: str,
    *,
    write: bool = False,
) -> list[dict[str, Any]]:
    """Run advise_thresholds on every .py file under *dir_path*."""
    results: list[dict[str, Any]] = []
    for py_file in sorted(pathlib.Path(dir_path).rglob("*.py")):
        results.extend(advise_thresholds(str(py_file), write=write))
    return results


# ── CLI ────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="LLM Threshold Advisor — generate config-driven replacements for hardcoded thresholds.",
    )
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--file", metavar="PATH", help="Single Python source file to analyse.")
    target.add_argument("--dir",  metavar="PATH", help="Directory to scan recursively for .py files.")
    p.add_argument("--write", action="store_true", help="Persist recommendations to args/build_config.yaml.")
    p.add_argument("--json",  action="store_true", dest="output_json", help="Output results as JSON.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.file:
        results = advise_thresholds(args.file, write=args.write)
    else:
        results = advise_directory(args.dir, write=args.write)

    if args.output_json:
        print(json.dumps(results, indent=2))
    else:
        if not results:
            print("No hardcoded thresholds detected.")
        for r in results:
            rec = r["recommendation"]
            print(f"\n{r['file_path']}:{r['line']} — constants {r['constants']}")
            print(f"  Description  : {rec.get('description', '')}")
            print(f"  Config key   : {rec.get('config_key', '')}")
            print(f"  Recommended  : {rec.get('recommended_value', '')}")
            print(f"  Rationale    : {rec.get('rationale', '')}")
            print(f"  Replacement  : {rec.get('replacement_hint', '')}")
        if args.write and results:
            print(f"\nRecommendations written to {_BUILD_CONFIG_PATH}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
