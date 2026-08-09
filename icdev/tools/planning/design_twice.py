# CUI // SP-CTI
"""OPT-52: tools/planning/design_twice.py — parallel constraint exploration.

Adapted from mattpocock/skills/design-an-interface (MIT).
See https://github.com/mattpocock/skills/tree/main/design-an-interface

Implements Ousterhout's 'Design It Twice' rule: your first idea is
unlikely to be the best, so generate several radically different designs
in parallel (each with a different *constraint*), then compare them.
Each variant goes through LLMRouter with its own system prompt.

Output: a single markdown file with all variants and a side-by-side
comparison table.

CLI:
    python tools/planning/design_twice.py \\
        --module "auth token cache" \\
        --out designs/auth-token-cache.md

    python tools/planning/design_twice.py \\
        --module "rate limiter" \\
        --constraints-file args/design_twice_default.yaml

LLM-agnostic: every call goes through tools.llm.router.LLMRouter. If the
router is in ICDEV_NO_LLM mode, the tool emits a skeleton markdown file
with the same structure so a human can fill in the designs manually.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import pathlib
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_CONSTRAINTS_FILE = ROOT / "args" / "design_twice_default.yaml"
DEFAULT_OUTPUT_DIR = ROOT / "designs"

# Four default constraint variants (matches the upstream skill's default set).
_DEFAULT_CONSTRAINTS = [
    {
        "id": "minimal_surface",
        "label": "Minimal surface",
        "prompt": (
            "Minimize method/function count. Aim for 1-3 public entry "
            "points. Hide everything else behind composition. Optimize "
            "for 'the smallest API that still solves the problem'."
        ),
    },
    {
        "id": "maximum_flexibility",
        "label": "Maximum flexibility",
        "prompt": (
            "Maximize flexibility and extensibility. Assume the caller "
            "will want to plug in new behaviors, swap strategies, and "
            "observe internal events. Prefer composition, strategy "
            "patterns, and explicit extension points."
        ),
    },
    {
        "id": "common_case_optimized",
        "label": "Common case optimized",
        "prompt": (
            "Optimize hard for the most common case. Make the common "
            "path a single obvious call with sensible defaults. Rare "
            "cases can require more code as long as the 80% path is "
            "trivial."
        ),
    },
    {
        "id": "inspired_by_stdlib",
        "label": "Inspired by stdlib",
        "prompt": (
            "Take inspiration from well-designed standard libraries "
            "(Python stdlib, Go stdlib, Rust std). Favor orthogonality, "
            "familiar naming, and composition with existing types."
        ),
    },
]


@dataclass
class DesignVariant:
    id: str
    label: str
    constraint_prompt: str
    content: str = ""
    error: str = ""
    duration_ms: int = 0
    provider: str = ""
    model_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class DesignReport:
    module: str
    description: str
    variants: List[DesignVariant] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    no_llm: bool = False


# ────────────────────────────────────────────────────────────────────────────
# Loader
# ────────────────────────────────────────────────────────────────────────────


def load_constraints(path: Optional[pathlib.Path]) -> List[dict]:
    """Load a constraints YAML if given. Falls back to the built-in 4."""
    if path is None:
        return [dict(c) for c in _DEFAULT_CONSTRAINTS]
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    constraints = list(raw.get("constraints") or [])
    if not constraints:
        raise ValueError(f"{path}: constraints list is empty")
    normalized: List[dict] = []
    for i, c in enumerate(constraints):
        normalized.append({
            "id": c.get("id") or f"variant_{i + 1}",
            "label": c.get("label") or c.get("id") or f"Variant {i + 1}",
            "prompt": c.get("prompt", ""),
        })
    return normalized


# ────────────────────────────────────────────────────────────────────────────
# Runner
# ────────────────────────────────────────────────────────────────────────────


_SHARED_SYSTEM_PROMPT = (
    "You are a senior software architect. The user will describe a "
    "module and a constraint. Produce ONE design for that module in "
    "markdown with these exact section headers (no more, no less):\n\n"
    "## Interface\n"
    "(signatures or pseudo-code, no implementation)\n\n"
    "## Usage example\n"
    "(minimal code showing the happy path)\n\n"
    "## What it hides\n"
    "(the internals the API deliberately does not expose)\n\n"
    "## Trade-offs\n"
    "(what this design sacrifices to satisfy the constraint)\n"
)


def _invoke_one_variant(
    router,
    module: str,
    variant: dict,
    max_tokens: int,
    temperature: float,
) -> DesignVariant:
    from tools.llm.provider import LLMRequest

    v = DesignVariant(
        id=variant["id"],
        label=variant["label"],
        constraint_prompt=variant["prompt"],
    )
    system = (
        _SHARED_SYSTEM_PROMPT
        + "\nConstraint for this variant:\n"
        + variant["prompt"]
    )
    user_msg = (
        f"Design the following module under the constraint above.\n\n"
        f"Module: {module}\n"
    )
    request = LLMRequest(
        messages=[{"role": "user", "content": user_msg}],
        system_prompt=system,
        max_tokens=max_tokens,
        temperature=temperature,
        skip_injection_scan=True,
        agent_id="design-twice",
        project_id="design-twice",
    )
    t0 = time.time()
    try:
        response = router.invoke("code_generation", request)
        v.content = response.content or ""
        v.provider = getattr(response, "provider", "")
        v.model_id = getattr(response, "model_id", "")
        v.input_tokens = getattr(response, "input_tokens", 0)
        v.output_tokens = getattr(response, "output_tokens", 0)
    except Exception as exc:
        v.error = f"{type(exc).__name__}: {exc}"
    v.duration_ms = int((time.time() - t0) * 1000)
    return v


def run_design_twice(
    module: str,
    constraints: List[dict],
    router=None,
    max_tokens: int = 900,
    temperature: float = 0.7,
    parallel: bool = True,
) -> DesignReport:
    """Execute each constraint variant and return a DesignReport."""
    if router is None:
        from tools.llm.router import LLMRouter
        router = LLMRouter()

    report = DesignReport(
        module=module,
        description=(
            "Parallel constraint exploration via the Design It Twice "
            "pattern (Ousterhout)."
        ),
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    # Honor ICDEV_NO_LLM — produce a skeleton and return.
    try:
        if hasattr(router, "is_no_llm_mode") and router.is_no_llm_mode():
            for c in constraints:
                report.variants.append(DesignVariant(
                    id=c["id"], label=c["label"],
                    constraint_prompt=c["prompt"],
                    content=(
                        "_(skeleton — ICDEV_NO_LLM is set; fill in "
                        "manually)_\n\n"
                        "## Interface\n\n## Usage example\n\n"
                        "## What it hides\n\n## Trade-offs\n"
                    ),
                ))
            report.no_llm = True
            report.finished_at = datetime.now(timezone.utc).isoformat()
            return report
    except Exception:
        pass

    if parallel and len(constraints) > 1:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(len(constraints), 4)
        ) as pool:
            futures = [
                pool.submit(
                    _invoke_one_variant,
                    router, module, c, max_tokens, temperature,
                )
                for c in constraints
            ]
            report.variants = [f.result() for f in futures]
    else:
        report.variants = [
            _invoke_one_variant(router, module, c, max_tokens, temperature)
            for c in constraints
        ]

    report.finished_at = datetime.now(timezone.utc).isoformat()
    return report


# ────────────────────────────────────────────────────────────────────────────
# Renderer
# ────────────────────────────────────────────────────────────────────────────


def render_markdown(report: DesignReport) -> str:
    lines: List[str] = []
    lines.append(f"# Design Twice: {report.module}")
    lines.append("")
    lines.append(f"> {report.description}")
    lines.append("")
    lines.append(
        f"Started: {report.started_at}  "
        f"Finished: {report.finished_at}"
    )
    if report.no_llm:
        lines.append("")
        lines.append("**Note:** ICDEV_NO_LLM is set — the variants below "
                     "are skeletons. Fill them in by hand.")
    lines.append("")
    lines.append("## Variants")
    lines.append("")
    for v in report.variants:
        lines.append(f"### {v.label} (`{v.id}`)")
        lines.append("")
        lines.append(f"_Constraint:_ {v.constraint_prompt}")
        lines.append("")
        if v.error:
            lines.append(f"**Error:** `{v.error}`")
        else:
            lines.append(f"_provider={v.provider} model={v.model_id}_")
            lines.append("")
            lines.append(v.content.strip() or "_(empty response)_")
        lines.append("")

    # Side-by-side comparison table
    lines.append("## Comparison")
    lines.append("")
    lines.append(
        "| Variant | Provider | Tokens (in/out) | Latency (ms) | Headers present |"
    )
    lines.append("| --- | --- | --- | --- | --- |")
    required_sections = ("## Interface", "## Usage example",
                         "## What it hides", "## Trade-offs")
    for v in report.variants:
        headers = [s for s in required_sections if s in v.content]
        lines.append(
            f"| {v.label} | {v.provider} | {v.input_tokens}/{v.output_tokens} "
            f"| {v.duration_ms} | {len(headers)}/4 |"
        )
    return "\n".join(lines)


def write_report(
    report: DesignReport,
    output_path: pathlib.Path,
) -> pathlib.Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_markdown(report), encoding="utf-8", newline="")
    return output_path


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", text.lower()).strip("-") or "design"


# ────────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="OPT-52 Design Twice — parallel constraint exploration"
    )
    ap.add_argument("--module", required=True,
                    help="Short description of the module to design")
    ap.add_argument("--constraints-file", default=None,
                    help="YAML file with constraint variants")
    ap.add_argument("--out", default=None, help="Output markdown path")
    ap.add_argument("--max-tokens", type=int, default=900)
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--sequential", action="store_true",
                    help="Run variants sequentially (default: parallel)")
    args = ap.parse_args(argv)

    try:
        constraints_path = (
            pathlib.Path(args.constraints_file) if args.constraints_file
            else (DEFAULT_CONSTRAINTS_FILE
                  if DEFAULT_CONSTRAINTS_FILE.exists() else None)
        )
        constraints = load_constraints(constraints_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        report = run_design_twice(
            args.module,
            constraints,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            parallel=not args.sequential,
        )
    except Exception as exc:
        print(f"error: design run failed: {exc}", file=sys.stderr)
        return 2

    out_path = pathlib.Path(
        args.out or (DEFAULT_OUTPUT_DIR / f"{_slug(args.module)}.md")
    )
    write_report(report, out_path)
    print(f"wrote {out_path}")
    print(
        f"  variants: {len(report.variants)}  "
        f"errors: {sum(1 for v in report.variants if v.error)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
