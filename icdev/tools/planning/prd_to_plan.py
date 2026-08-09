#!/usr/bin/env python3
# CUI // SP-CTI
"""OPT-53 — Tracer-bullet vertical-slice planner.

Reads a PRD markdown file and emits a phased implementation plan where each
phase is a tracer-bullet vertical slice — a complete end-to-end path through
schema, API, UI, and tests that is demoable on its own.

Rules enforced on the output plan:
  - Each slice must deliver a narrow but COMPLETE path through every layer
  - A completed slice must be demoable or verifiable
  - Prefer many thin slices over few thick ones
  - DO NOT include file names or function names (volatile)
  - DO include durable decisions: route paths, schema shapes, data model names

Upstream reference:
  https://github.com/mattpocock/skills/tree/main/prd-to-plan (MIT)

Usage:
    python tools/planning/prd_to_plan.py --prd docs/prds/my_feature.md
    python tools/planning/prd_to_plan.py --prd feature.md --out plans/feature.md
    python tools/planning/prd_to_plan.py --prd feature.md --no-llm         # deterministic skeleton
    python tools/planning/prd_to_plan.py --prd feature.md --validate-only  # lint an existing plan
    python tools/planning/prd_to_plan.py --lint plans/foo.md --json

LLM is OPTIONAL. With --no-llm or when LLMRouter is unavailable, the tool
emits a deterministic skeleton plan template the user fills in manually.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


# ---------------------------------------------------------------------------
# Validator — detect banned tokens in phase descriptions
# ---------------------------------------------------------------------------
# File names ending in common source/test suffixes; function/class call notation.
_FILE_EXT_RE = re.compile(
    r"\b[A-Za-z_][\w./\\-]*\.(py|js|ts|tsx|jsx|go|rs|java|rb|cs|sql|html|css|yaml|yml|json)\b"
)
# Snake_case or camelCase identifiers followed by parentheses — function calls.
_FUNC_CALL_RE = re.compile(r"\b[a-z][A-Za-z0-9_]{2,}\s*\(\s*\)")


@dataclass
class LintFinding:
    line: int
    kind: str          # "file_name" | "function_call"
    match: str
    context: str


@dataclass
class LintResult:
    plan_path: str
    findings: list[LintFinding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings


def validate_plan(markdown: str, plan_path: str = "<stdin>") -> LintResult:
    """Return a LintResult for a rendered plan — flags file or function names."""
    result = LintResult(plan_path=plan_path)
    in_fence = False
    for i, line in enumerate(markdown.splitlines(), start=1):
        stripped = line.strip()
        # Toggle fenced-block state — content inside code fences is exempt
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if stripped.startswith("|---"):
            continue
        if stripped.startswith("> Upstream reference") or stripped.startswith("# "):
            continue

        for m in _FILE_EXT_RE.finditer(line):
            matched = m.group(0)
            # Allow doc paths (docs/..., plans/...) — the plan header is allowed
            # to reference its own output location. Skip matches that start with
            # 'docs/', 'plans/', or 'README' which are stable durable anchors.
            if matched.startswith(("docs/", "plans/", "README", "CLAUDE.md", "AGENTS.md")):
                continue
            result.findings.append(LintFinding(
                line=i, kind="file_name", match=matched, context=stripped[:120]))

        for m in _FUNC_CALL_RE.finditer(line):
            matched = m.group(0)
            # Filter stable natural-language verbs that look function-ish in prose
            if matched.lower() in {"run()", "log()", "do()", "and()", "or()"}:
                continue
            result.findings.append(LintFinding(
                line=i, kind="function_call", match=matched, context=stripped[:120]))

    return result


# ---------------------------------------------------------------------------
# Plan scaffolding
# ---------------------------------------------------------------------------
SKELETON_TEMPLATE = """# {title} — Implementation Plan

> Tracer-bullet vertical-slice plan (OPT-53). Each phase is a complete
> end-to-end path (data model → API → UI → tests) that is demoable alone.
> Upstream reference: https://github.com/mattpocock/skills/tree/main/prd-to-plan

## Source PRD

- **PRD:** `{prd_path}`
- **Generated:** `{generated_at}`
- **Mode:** {mode}

## Durable Architectural Decisions

These are stable across implementation (route paths, schema shapes, data model
names). Implementation details — file layouts, function names — are deliberately
omitted, since those will change during build.

{durable_decisions}

## Phased Vertical Slices

Each phase below is a tracer bullet: thin end-to-end path, demoable on its own.

{phases}

## Acceptance

- Every phase has a demo checklist and at least one verifiable outcome.
- Plan passes the tracer-bullet lint validator (no file or function names leak into phase descriptions).
- The durable decisions above do not change between phases; only implementation does.
"""


DEFAULT_PHASES = [
    {
        "name": "Phase 1 — Skeleton tracer bullet",
        "stories": [
            "As an operator I can submit one minimal input through the new data model.",
            "As an operator I can fetch that input back via the new route.",
        ],
        "demo_checklist": [
            "POST to the new route returns 200 with the submitted payload echoed.",
            "GET returns the stored record.",
            "A minimal UI button round-trips the payload.",
        ],
    },
    {
        "name": "Phase 2 — Validation + error paths",
        "stories": [
            "As an operator I see clear validation errors on bad input.",
            "As an operator I see a graceful error state when the backend fails.",
        ],
        "demo_checklist": [
            "Invalid payload returns 422 with a structured error body.",
            "UI renders the error inline with the offending field.",
            "Server logs record the failure with a correlation id.",
        ],
    },
    {
        "name": "Phase 3 — Persistence + audit",
        "stories": [
            "As a reviewer I can see an append-only audit trail of submissions.",
            "As an operator my submission survives a server restart.",
        ],
        "demo_checklist": [
            "Restarting the service preserves submissions.",
            "Audit list endpoint returns the sequence of actions with actor + timestamp.",
            "UI shows a read-only audit log for each record.",
        ],
    },
]


def _render_phases(phases: list[dict]) -> str:
    out = []
    for i, ph in enumerate(phases, start=1):
        out.append(f"### {ph['name']}")
        out.append("")
        out.append("**User stories:**")
        out.append("")
        for s in ph.get("stories", []):
            out.append(f"- {s}")
        out.append("")
        out.append("**Demo checklist:**")
        out.append("")
        for d in ph.get("demo_checklist", []):
            out.append(f"- [ ] {d}")
        out.append("")
    return "\n".join(out)


def _render_decisions(decisions: list[str]) -> str:
    if not decisions:
        return "_TBD — extract from the PRD or propose durable decisions here._"
    return "\n".join(f"- {d}" for d in decisions)


# ---------------------------------------------------------------------------
# PRD parsing (deterministic extraction of durable decisions)
# ---------------------------------------------------------------------------
_DECISION_HEADING_RE = re.compile(
    r"^#{1,4}\s*(Durable Decisions|Architectural Decisions|Decisions)\b",
    re.IGNORECASE)


def _extract_durable_decisions(prd_text: str) -> list[str]:
    """Parse a 'Durable Decisions' / 'Architectural Decisions' section from the PRD.

    Returns bullet items from the first matching section, or [] if none.
    """
    lines = prd_text.splitlines()
    items: list[str] = []
    in_section = False
    for line in lines:
        if _DECISION_HEADING_RE.match(line.strip()):
            in_section = True
            continue
        if in_section:
            if line.startswith("#"):
                break
            m = re.match(r"\s*[-*]\s+(.+?)\s*$", line)
            if m:
                items.append(m.group(1))
    return items


# ---------------------------------------------------------------------------
# LLM enrichment (optional)
# ---------------------------------------------------------------------------
def _try_llm_enrichment(prd_text: str, title: str) -> dict[str, Any] | None:
    """Ask the LLMRouter to propose durable decisions + slices. Returns None
    if no provider is available — caller must handle fallback."""
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
    except Exception:
        return None

    router = LLMRouter()
    system = (
        "You are a senior engineer planning a tracer-bullet delivery. "
        "Extract DURABLE architectural decisions from the PRD (route paths, "
        "schema shapes, data model names — NOT file or function names). "
        "Then propose 3–6 thin vertical slices. Each slice must: "
        "(a) cover data-model+API+UI+tests end-to-end, "
        "(b) be demoable on its own, "
        "(c) contain NO file or function names. "
        "Output JSON: {\"decisions\": [...], \"phases\": [{\"name\": ..., "
        "\"stories\": [...], \"demo_checklist\": [...]}]}."
    )
    user_msg = f"PRD title: {title}\n\nPRD body:\n\n{prd_text[:12000]}"
    req = LLMRequest(
        system_prompt=system,
        messages=[{"role": "user", "content": user_msg}],
        max_tokens=3000,
        temperature=0.2,
    )
    try:
        resp = router.invoke("code_generation", req)
    except Exception:
        return None
    content = (getattr(resp, "content", None) or "").strip()
    if not content:
        return None
    # Extract JSON out of the content (tolerate ```json fences).
    m = re.search(r"\{[\s\S]*\}", content)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def _title_from_prd(prd_text: str, prd_path: Path) -> str:
    for line in prd_text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return prd_path.stem.replace("_", " ").title()


def _slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "feature"


def build_plan(prd_path: Path, *, use_llm: bool = True,
               mode_override: str | None = None) -> tuple[str, dict]:
    """Return (rendered_markdown, metadata) for the given PRD."""
    from datetime import datetime, timezone
    prd_text = prd_path.read_text(encoding="utf-8", errors="replace")
    title = _title_from_prd(prd_text, prd_path)

    # Always start from the PRD's own Decisions section if present
    decisions = _extract_durable_decisions(prd_text)
    phases = list(DEFAULT_PHASES)
    mode = "deterministic-skeleton"

    if use_llm:
        llm_out = _try_llm_enrichment(prd_text, title)
        if llm_out:
            if llm_out.get("decisions"):
                decisions = list(llm_out["decisions"])
            if llm_out.get("phases"):
                phases = list(llm_out["phases"])
            mode = "llm-enriched"

    rendered = SKELETON_TEMPLATE.format(
        title=title,
        prd_path=str(prd_path),
        generated_at=datetime.now(timezone.utc).isoformat(),
        mode=mode_override or mode,
        durable_decisions=_render_decisions(decisions),
        phases=_render_phases(phases),
    )
    return rendered, {
        "title": title,
        "slug": _slugify(title),
        "mode": mode_override or mode,
        "decision_count": len(decisions),
        "phase_count": len(phases),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--prd", metavar="PATH", help="Path to a PRD markdown file")
    g.add_argument("--lint", metavar="PLAN_PATH",
                   help="Lint an existing plan (no PRD needed)")
    p.add_argument("--out", metavar="PATH", help="Write plan to this file")
    p.add_argument("--no-llm", action="store_true",
                   help="Skip LLM enrichment; emit deterministic skeleton")
    p.add_argument("--validate-only", action="store_true",
                   help="Render plan in memory and exit 1 if validator fails")
    p.add_argument("--json", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # Lint mode
    if args.lint:
        plan_path = Path(args.lint)
        if not plan_path.exists():
            print(f"ERROR: plan not found: {plan_path}", file=sys.stderr)
            return 1
        result = validate_plan(plan_path.read_text(encoding="utf-8"), str(plan_path))
        payload = {
            "plan_path": result.plan_path,
            "ok": result.ok,
            "findings": [asdict(f) for f in result.findings],
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"Lint: {plan_path} — {'PASS' if result.ok else 'FAIL'}")
            for f in result.findings[:20]:
                print(f"  L{f.line} [{f.kind}] {f.match!r} — {f.context[:80]}")
        return 0 if result.ok else 1

    if not args.prd:
        print("ERROR: --prd PATH or --lint PLAN_PATH required", file=sys.stderr)
        return 2

    prd_path = Path(args.prd)
    if not prd_path.exists():
        print(f"ERROR: PRD not found: {prd_path}", file=sys.stderr)
        return 1

    rendered, meta = build_plan(prd_path, use_llm=not args.no_llm)
    lint = validate_plan(rendered)

    if args.validate_only:
        payload = {"meta": meta, "ok": lint.ok,
                   "findings": [asdict(f) for f in lint.findings]}
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"validate-only: {'PASS' if lint.ok else 'FAIL'} ({len(lint.findings)} finding(s))")
        return 0 if lint.ok else 1

    # Resolve output path
    if args.out:
        out_path = Path(args.out)
    else:
        out_path = BASE_DIR / "plans" / f"{meta['slug']}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered, encoding="utf-8", newline="")

    summary = {
        "meta": meta,
        "out_path": str(out_path),
        "bytes_written": len(rendered.encode("utf-8")),
        "lint": {"ok": lint.ok, "findings": [asdict(f) for f in lint.findings]},
    }
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"wrote {out_path}  ({summary['bytes_written']} bytes, "
              f"{meta['phase_count']} phases, mode={meta['mode']})")
        if not lint.ok:
            print(f"  WARN: {len(lint.findings)} lint finding(s)")
            for f in lint.findings[:5]:
                print(f"    L{f.line} [{f.kind}] {f.match!r}")
    # Exit 1 if lint fails so CI catches leaked file / function names
    return 1 if not lint.ok else 0


if __name__ == "__main__":
    sys.exit(main())
