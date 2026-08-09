#!/usr/bin/env python3
# CUI // SP-CTI
"""Hypothesis Generator — scanner-tier LLM + template fallback (D-AR-1).

Generates experiment hypotheses from domain analysis. Uses qwen3.5
(zero Claude tokens) when available, falls back to deterministic
template-based generation for air-gap safety.

Usage:
    python tools/autoresearch/hypothesis_generator.py --domain compliance --max 5 --json
    python tools/autoresearch/hypothesis_generator.py --from-signals --domain compliance --json
    python tools/autoresearch/hypothesis_generator.py --health --json
"""

import argparse
import hashlib
import json
import random
from pathlib import Path
import sys

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_ROOT = Path(__file__).resolve().parent.parent.parent

from tools.common.helpers import now_iso


def _load_program(domain: str) -> dict:
    """Load experiment program config for a domain."""
    program_path = _ROOT / "args" / "experiment_programs" / f"{domain}.yaml"
    if not program_path.exists():
        return {}
    try:
        import yaml

        with open(program_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        # Fallback: parse YAML manually for key fields
        return {"domain": domain}


def _content_hash(text: str) -> str:
    """SHA-256 content hash for dedup."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _template_hypotheses(domain: str, config: dict, max_hypotheses: int = 10, seed: int = 42) -> list:
    """Deterministic template-based hypothesis generation (air-gap fallback).

    Reads example_hypotheses from program config and generates candidates.
    """
    rng = random.Random(seed)
    candidates = []
    categories = config.get("categories", {})
    category_order = config.get("category_order", list(categories.keys()))

    for category_name in category_order:
        cat_config = categories.get(category_name, {})
        examples = cat_config.get("example_hypotheses", [])
        risk = cat_config.get("risk", "medium")

        for hypothesis_text in examples:
            candidates.append(
                {
                    "hypothesis": hypothesis_text,
                    "category": category_name,
                    "modifications": {},
                    "estimated_impact": "medium",
                    "risk_level": risk,
                    "source": "template",
                    "content_hash": _content_hash(hypothesis_text),
                }
            )

    # Shuffle and limit
    rng.shuffle(candidates)
    return candidates[:max_hypotheses]


def _llm_hypotheses(domain: str, config: dict, current_metric: float, max_hypotheses: int = 10) -> list:
    """Generate hypotheses via scanner-tier LLM (qwen3.5)."""
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
    except (ImportError, Exception):
        return []

    categories = config.get("categories", {})
    category_desc = "\n".join(f"- {name}: {cat.get('description', '')}" for name, cat in categories.items())

    prompt = f"""You are an autonomous research agent running experiments to improve
the "{domain}" domain of an ICDEV™ compliance platform.

Current metric value: {current_metric:.4f}
Metric to optimize: {config.get("objective", {}).get("metric_name", "unknown")}
Direction: {config.get("objective", {}).get("direction", "maximize")}

Available experiment categories:
{category_desc}

Modifiable paths: {config.get("modifiable_paths", [])}

Generate {max_hypotheses} specific, testable hypotheses for experiments.
Each must target ONE specific change (Karpathy one-change rule).

Respond as JSON array:
[{{"hypothesis": "...", "category": "...", "estimated_impact": "high|medium|low"}}]"""

    try:
        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
        )
        response = router.invoke("hypothesis_generation", request)
        text = response.content if response and response.content else ""
        # Parse JSON from response
        if "```" in text:
            lines = text.split("\n")
            json_lines = []
            in_json = False
            for line in lines:
                if line.strip().startswith("```"):
                    in_json = not in_json
                    continue
                if in_json:
                    json_lines.append(line)
            text = "\n".join(json_lines)
        # Find JSON array
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            text = text[start : end + 1]
        hypotheses = json.loads(text)
        candidates = []
        for h in hypotheses[:max_hypotheses]:
            hypothesis_text = h.get("hypothesis", "")
            if not hypothesis_text:
                continue
            candidates.append(
                {
                    "hypothesis": hypothesis_text,
                    "category": h.get("category", ""),
                    "modifications": {},
                    "estimated_impact": h.get("estimated_impact", "medium"),
                    "risk_level": "medium",
                    "source": "llm_generated",
                    "content_hash": _content_hash(hypothesis_text),
                }
            )
        return candidates
    except (json.JSONDecodeError, Exception):
        return []


def generate_hypotheses(
    domain: str, max_hypotheses: int = 10, current_metric: float = 0.0, seed: int = 42, source_signals: list = None
) -> dict:
    """Generate experiment hypotheses for a domain.

    1. Load program config
    2. Try LLM generation (scanner-tier qwen3.5)
    3. Fall back to template-based if LLM unavailable
    4. Merge with signal-sourced hypotheses
    """
    config = _load_program(domain)
    if not config:
        return {
            "domain": domain,
            "success": False,
            "error": f"No program config found for domain: {domain}",
            "hypotheses": [],
            "timestamp": now_iso(),
        }

    # Try LLM first
    candidates = _llm_hypotheses(domain, config, current_metric, max_hypotheses)

    # Fallback to templates
    if not candidates:
        candidates = _template_hypotheses(domain, config, max_hypotheses, seed)

    # Merge signal-sourced hypotheses
    if source_signals:
        signal_candidates = generate_from_signals(source_signals, domain)
        candidates.extend(signal_candidates)

    # Dedup by content hash
    seen_hashes = set()
    unique = []
    for c in candidates:
        h = c.get("content_hash", "")
        if h not in seen_hashes:
            seen_hashes.add(h)
            unique.append(c)
    candidates = unique[:max_hypotheses]

    return {
        "domain": domain,
        "success": True,
        "hypotheses": candidates,
        "count": len(candidates),
        "source": candidates[0]["source"] if candidates else "none",
        "timestamp": now_iso(),
    }


def generate_from_signals(signals: list, domain: str) -> list:
    """Convert Innovation/Creative/Research signals into hypotheses."""
    candidates = []
    for signal in signals:
        title = signal.get("title", "")
        description = signal.get("description", "")
        hypothesis_text = f"Integrate signal: {title} — {description[:200]}"
        candidates.append(
            {
                "hypothesis": hypothesis_text,
                "category": "architecture",
                "modifications": {},
                "estimated_impact": "medium",
                "risk_level": "medium",
                "source": signal.get("source_type", "innovation"),
                "signal_id": signal.get("id"),
                "content_hash": _content_hash(hypothesis_text),
            }
        )
    return candidates


def health_check() -> dict:
    """Health check for hypothesis generator."""
    llm_available = False
    try:
        from tools.llm.router import LLMRouter

        LLMRouter()  # Verify instantiation
        llm_available = True
    except (ImportError, Exception):
        pass

    programs_dir = _ROOT / "args" / "experiment_programs"
    programs = list(programs_dir.glob("*.yaml")) if programs_dir.exists() else []

    return {
        "status": "healthy",
        "llm_available": llm_available,
        "template_fallback": True,
        "programs_found": len(programs),
        "program_domains": [p.stem for p in programs],
        "timestamp": now_iso(),
    }


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Hypothesis Generator")
    parser.add_argument("--domain", type=str, help="Domain to generate for")
    parser.add_argument("--max", type=int, default=10, help="Max hypotheses")
    parser.add_argument("--from-signals", action="store_true", help="Generate from signals")
    parser.add_argument("--health", action="store_true", help="Health check")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.health:
        result = health_check()
    elif args.domain:
        result = generate_hypotheses(args.domain, max_hypotheses=args.max)
    else:
        parser.print_help()
        return

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
