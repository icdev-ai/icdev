# CUI // SP-CTI
"""OPT-64: tools/llm/eval_runner.py — declarative LLM eval harness.

Eval runner pattern from promptfoo/promptfoo (MIT license).
See https://github.com/promptfoo/promptfoo

Loads a YAML eval spec, runs each prompt against one or more models via
LLMRouter, evaluates per-prompt assertions (contains, not_contains, regex,
max_length, min_length, json_schema), and emits a markdown/json/html
report under reports/llm_evals/.

CLI:
    python tools/llm/eval_runner.py --eval code_generation_baseline
    python tools/llm/eval_runner.py --eval <name> --json --gate
    python tools/llm/eval_runner.py --eval <name> --models claude-sonnet qwen3-local

Non-goals (per OPT-64 scope):
    - no web UI
    - no LLM-as-judge
    - no red-team (see OPT-65)
    - no semantic_similarity (documented but degrades to disabled
      if no embedder configured)
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_EVAL_DIR = ROOT / "args" / "llm_evals"
DEFAULT_REPORT_DIR = ROOT / "reports" / "llm_evals"
DEFAULT_CONFIG_PATH = ROOT / "args" / "llm_eval_config.yaml"


# ────────────────────────────────────────────────────────────────────────────
# Data types
# ────────────────────────────────────────────────────────────────────────────


@dataclass
class AssertionResult:
    type: str
    passed: bool
    detail: str = ""


@dataclass
class PromptRun:
    prompt_id: str
    model: str
    ok: bool
    content: str
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    provider: str = ""
    model_id: str = ""
    error: str = ""
    assertions: List[AssertionResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.ok and all(a.passed for a in self.assertions)


@dataclass
class EvalReport:
    name: str
    description: str
    started_at: str
    finished_at: str
    total_prompts: int
    total_models: int
    runs: List[PromptRun] = field(default_factory=list)

    @property
    def summary(self) -> Dict[str, Any]:
        total = len(self.runs)
        passed = sum(1 for r in self.runs if r.passed)
        return {
            "total_runs": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": round(passed / total, 3) if total else 0.0,
        }


# ────────────────────────────────────────────────────────────────────────────
# Loaders
# ────────────────────────────────────────────────────────────────────────────


def load_eval(path_or_name) -> dict:
    """Load an eval YAML. Accepts an absolute path, relative path, or a bare
    name that resolves to args/llm_evals/<name>.yaml."""
    p = pathlib.Path(path_or_name)
    if not p.exists():
        candidate = DEFAULT_EVAL_DIR / f"{path_or_name}.yaml"
        if candidate.exists():
            p = candidate
        else:
            raise FileNotFoundError(
                f"Eval spec not found: {path_or_name} "
                f"(checked {candidate})"
            )
    with open(p, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    cfg["_source_path"] = str(p)
    return cfg


def load_runner_config() -> dict:
    """Load optional runner config — safe default if absent."""
    if not DEFAULT_CONFIG_PATH.exists():
        return {
            "default_timeout_seconds": 60,
            "default_temperature": 0.3,
            "default_max_tokens": 512,
            "report_dir": str(DEFAULT_REPORT_DIR),
        }
    with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# ────────────────────────────────────────────────────────────────────────────
# Assertions
# ────────────────────────────────────────────────────────────────────────────


def _assert_contains(content: str, value: str) -> AssertionResult:
    passed = value in content
    return AssertionResult(
        type="contains",
        passed=passed,
        detail=("" if passed else f"missing substring: {value!r}"),
    )


def _assert_not_contains(content: str, value: str) -> AssertionResult:
    passed = value not in content
    return AssertionResult(
        type="not_contains",
        passed=passed,
        detail=("" if passed else f"unexpected substring: {value!r}"),
    )


def _assert_regex(content: str, pattern: str) -> AssertionResult:
    try:
        passed = bool(re.search(pattern, content, re.MULTILINE))
    except re.error as exc:
        return AssertionResult(
            type="regex", passed=False, detail=f"bad pattern: {exc}"
        )
    return AssertionResult(
        type="regex",
        passed=passed,
        detail=("" if passed else f"pattern not matched: {pattern!r}"),
    )


def _assert_max_length(content: str, value: int) -> AssertionResult:
    length = len(content)
    passed = length <= int(value)
    return AssertionResult(
        type="max_length",
        passed=passed,
        detail=("" if passed else f"length {length} > {value}"),
    )


def _assert_min_length(content: str, value: int) -> AssertionResult:
    length = len(content)
    passed = length >= int(value)
    return AssertionResult(
        type="min_length",
        passed=passed,
        detail=("" if passed else f"length {length} < {value}"),
    )


def _assert_json_schema(content: str, schema: dict) -> AssertionResult:
    """Best-effort JSON-schema assertion. If jsonschema is installed, use it.
    Otherwise fall back to required-key presence check (top-level only)."""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        return AssertionResult(
            type="json_schema",
            passed=False,
            detail=f"content is not JSON: {exc}",
        )
    try:
        import jsonschema  # type: ignore
    except ImportError:
        required = schema.get("required") or []
        if isinstance(parsed, dict):
            missing = [k for k in required if k not in parsed]
            passed = not missing
            return AssertionResult(
                type="json_schema",
                passed=passed,
                detail=(
                    ""
                    if passed
                    else f"missing required keys: {missing} (jsonschema not installed)"
                ),
            )
        return AssertionResult(
            type="json_schema",
            passed=False,
            detail="top-level JSON is not an object",
        )
    try:
        jsonschema.validate(instance=parsed, schema=schema)
        return AssertionResult(type="json_schema", passed=True)
    except jsonschema.ValidationError as exc:
        return AssertionResult(
            type="json_schema",
            passed=False,
            detail=f"schema violation: {exc.message}",
        )


_ASSERTION_IMPLS = {
    "contains": lambda c, a: _assert_contains(c, a.get("value", "")),
    "not_contains": lambda c, a: _assert_not_contains(c, a.get("value", "")),
    "regex": lambda c, a: _assert_regex(c, a.get("pattern", "")),
    "max_length": lambda c, a: _assert_max_length(c, a.get("value", 0)),
    "min_length": lambda c, a: _assert_min_length(c, a.get("value", 0)),
    "json_schema": lambda c, a: _assert_json_schema(c, a.get("schema", {})),
}


def run_assertions(content: str, asserts: List[dict]) -> List[AssertionResult]:
    """Run a list of assertion dicts against the response content."""
    out: List[AssertionResult] = []
    for a in asserts or []:
        kind = a.get("type", "")
        fn = _ASSERTION_IMPLS.get(kind)
        if fn is None:
            out.append(
                AssertionResult(
                    type=kind or "unknown",
                    passed=False,
                    detail=f"unsupported assertion type: {kind!r}",
                )
            )
            continue
        try:
            out.append(fn(content, a))
        except Exception as exc:  # defensive — never crash the eval loop
            out.append(
                AssertionResult(
                    type=kind, passed=False, detail=f"assertion error: {exc}"
                )
            )
    return out


# ────────────────────────────────────────────────────────────────────────────
# Runner
# ────────────────────────────────────────────────────────────────────────────


def _invoke_model(
    router, model_name: str, request
):
    """Invoke a specific logical model by bypassing the routing chain.
    Returns an LLMResponse (or raises).
    """
    model_cfg = router._get_model_config(model_name)  # noqa: SLF001
    if not model_cfg:
        raise RuntimeError(f"model '{model_name}' not in llm_config.yaml")
    provider_name = model_cfg.get("provider", "")
    provider = router._get_provider(provider_name)  # noqa: SLF001
    if provider is None:
        raise RuntimeError(
            f"provider '{provider_name}' for model '{model_name}' unavailable"
        )
    model_id = model_cfg.get("model_id", "")
    return provider.invoke(request, model_id, model_cfg)


def run_eval(
    cfg: dict,
    router=None,
    model_override: Optional[List[str]] = None,
) -> EvalReport:
    """Execute the eval. `router` defaults to a live LLMRouter. For tests,
    pass a mock with `_get_model_config` / `_get_provider` attrs.
    """
    if router is None:
        from tools.llm.router import LLMRouter
        router = LLMRouter()
    from tools.llm.provider import LLMRequest

    models: List[str] = list(model_override or cfg.get("models") or [])
    if not models:
        raise ValueError("eval spec has no 'models' list")
    prompts: List[dict] = list(cfg.get("prompts") or [])
    if not prompts:
        raise ValueError("eval spec has no 'prompts' list")

    runner_cfg = load_runner_config()
    default_temperature = float(
        cfg.get("default_temperature",
                runner_cfg.get("default_temperature", 0.3))
    )
    default_max_tokens = int(
        cfg.get("default_max_tokens",
                runner_cfg.get("default_max_tokens", 512))
    )
    default_system = cfg.get("default_system", "")

    started_at = datetime.now(timezone.utc).isoformat()
    runs: List[PromptRun] = []

    for prompt in prompts:
        pid = prompt.get("id") or f"p{prompts.index(prompt)}"
        system = prompt.get("system", default_system)
        messages = prompt.get("messages") or [
            {"role": "user", "content": prompt.get("content", "")}
        ]
        asserts = prompt.get("assertions") or []
        temperature = float(prompt.get("temperature", default_temperature))
        max_tokens = int(prompt.get("max_tokens", default_max_tokens))

        for model in models:
            request = LLMRequest(
                messages=list(messages),
                system_prompt=system,
                max_tokens=max_tokens,
                temperature=temperature,
                skip_injection_scan=True,
                agent_id="llm-eval-runner",
                project_id="llm-evals",
            )
            t0 = time.time()
            try:
                response = _invoke_model(router, model, request)
                dur_ms = int((time.time() - t0) * 1000)
                content = response.content or ""
                run = PromptRun(
                    prompt_id=pid,
                    model=model,
                    ok=True,
                    content=content,
                    input_tokens=getattr(response, "input_tokens", 0),
                    output_tokens=getattr(response, "output_tokens", 0),
                    duration_ms=getattr(response, "duration_ms", 0) or dur_ms,
                    provider=getattr(response, "provider", ""),
                    model_id=getattr(response, "model_id", ""),
                )
                run.assertions = run_assertions(content, asserts)
            except Exception as exc:
                dur_ms = int((time.time() - t0) * 1000)
                run = PromptRun(
                    prompt_id=pid,
                    model=model,
                    ok=False,
                    content="",
                    duration_ms=dur_ms,
                    error=f"{type(exc).__name__}: {exc}",
                )
            runs.append(run)

    finished_at = datetime.now(timezone.utc).isoformat()
    return EvalReport(
        name=cfg.get("name", "unnamed"),
        description=cfg.get("description", ""),
        started_at=started_at,
        finished_at=finished_at,
        total_prompts=len(prompts),
        total_models=len(models),
        runs=runs,
    )


# ────────────────────────────────────────────────────────────────────────────
# Renderers
# ────────────────────────────────────────────────────────────────────────────


def render_json(report: EvalReport) -> str:
    data = {
        "name": report.name,
        "description": report.description,
        "started_at": report.started_at,
        "finished_at": report.finished_at,
        "total_prompts": report.total_prompts,
        "total_models": report.total_models,
        "summary": report.summary,
        "runs": [
            {
                **{k: v for k, v in asdict(r).items() if k != "assertions"},
                "assertions": [asdict(a) for a in r.assertions],
                "passed": r.passed,
            }
            for r in report.runs
        ],
    }
    return json.dumps(data, indent=2, sort_keys=True)


def render_markdown(report: EvalReport) -> str:
    lines: List[str] = []
    s = report.summary
    lines.append(f"# LLM Eval: {report.name}")
    if report.description:
        lines.append("")
        lines.append(f"> {report.description}")
    lines.append("")
    lines.append(
        f"**Started:** {report.started_at}  "
        f"**Finished:** {report.finished_at}"
    )
    lines.append("")
    lines.append(
        f"**Summary:** {s['passed']}/{s['total_runs']} passed "
        f"(pass rate {s['pass_rate']})"
    )
    lines.append("")
    lines.append("| Prompt | Model | Tokens (in/out) | Latency (ms) | Status | Assertions |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for r in report.runs:
        status = "PASS" if r.passed else ("ERROR" if not r.ok else "FAIL")
        assertion_cells = ", ".join(
            f"{a.type}:{'OK' if a.passed else 'X'}" for a in r.assertions
        ) or "—"
        lines.append(
            f"| {r.prompt_id} | {r.model} | "
            f"{r.input_tokens}/{r.output_tokens} | {r.duration_ms} | "
            f"{status} | {assertion_cells} |"
        )
    lines.append("")
    # Per-run detail
    for r in report.runs:
        lines.append("---")
        lines.append(f"### {r.prompt_id} @ {r.model}")
        if r.error:
            lines.append("")
            lines.append(f"**ERROR:** `{r.error}`")
        else:
            lines.append("")
            lines.append(f"provider={r.provider} model_id={r.model_id}")
            if r.assertions:
                lines.append("")
                for a in r.assertions:
                    mark = "OK" if a.passed else "FAIL"
                    detail = f" — {a.detail}" if a.detail else ""
                    lines.append(f"- [{mark}] **{a.type}**{detail}")
            lines.append("")
            lines.append("```")
            lines.append(r.content[:2000])
            if len(r.content) > 2000:
                lines.append(f"... (truncated, {len(r.content)} chars total)")
            lines.append("```")
    return "\n".join(lines)


def render_html(report: EvalReport) -> str:
    s = report.summary
    rows_html: List[str] = []
    for r in report.runs:
        if r.passed:
            status = '<span style="color:#10b981">PASS</span>'
        elif not r.ok:
            status = '<span style="color:#ef4444">ERROR</span>'
        else:
            status = '<span style="color:#f97316">FAIL</span>'
        asserts = ", ".join(
            f'<span style="color:{"#10b981" if a.passed else "#ef4444"}">'
            f"{a.type}</span>"
            for a in r.assertions
        ) or "—"
        rows_html.append(
            f"<tr><td>{_esc(r.prompt_id)}</td><td>{_esc(r.model)}</td>"
            f"<td>{r.input_tokens}/{r.output_tokens}</td>"
            f"<td>{r.duration_ms}</td><td>{status}</td>"
            f"<td>{asserts}</td></tr>"
        )
    return (
        "<!doctype html><meta charset='utf-8'>"
        f"<title>LLM Eval — {_esc(report.name)}</title>"
        "<style>body{font-family:system-ui;background:#0f172a;color:#e2e8f0;padding:24px}"
        "table{border-collapse:collapse;width:100%}th,td{border:1px solid #334155;padding:8px}"
        "th{background:#1e293b}</style>"
        f"<h1>{_esc(report.name)}</h1>"
        f"<p><em>{_esc(report.description)}</em></p>"
        f"<p>Started: {_esc(report.started_at)}<br>"
        f"Finished: {_esc(report.finished_at)}</p>"
        f"<p>Summary: <b>{s['passed']}/{s['total_runs']}</b> passed "
        f"(rate {s['pass_rate']})</p>"
        "<table><thead><tr><th>Prompt</th><th>Model</th>"
        "<th>Tokens (in/out)</th><th>Latency (ms)</th>"
        "<th>Status</th><th>Assertions</th></tr></thead><tbody>"
        + "".join(rows_html)
        + "</tbody></table>"
    )


def _esc(s: Any) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def write_report(
    report: EvalReport, output_dir: pathlib.Path
) -> Dict[str, pathlib.Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", report.name) or "eval"
    base = output_dir / f"{safe_name}-{ts}"
    paths = {
        "markdown": base.with_suffix(".md"),
        "json": base.with_suffix(".json"),
        "html": base.with_suffix(".html"),
    }
    paths["markdown"].write_text(render_markdown(report), encoding="utf-8", newline="")
    paths["json"].write_text(render_json(report), encoding="utf-8", newline="")
    paths["html"].write_text(render_html(report), encoding="utf-8", newline="")
    return paths


# ────────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="OPT-64 declarative LLM eval runner (promptfoo pattern)"
    )
    ap.add_argument("--eval", required=True, help="Eval name or YAML path")
    ap.add_argument("--models", nargs="*", default=None,
                    help="Override models list (logical names)")
    ap.add_argument("--output-dir", default=None,
                    help="Directory to write reports (defaults per config)")
    ap.add_argument("--json", action="store_true",
                    help="Emit JSON result to stdout")
    ap.add_argument("--gate", action="store_true",
                    help="Exit non-zero if any assertion fails")
    args = ap.parse_args(argv)

    try:
        cfg = load_eval(args.eval)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        report = run_eval(cfg, model_override=args.models)
    except Exception as exc:
        print(f"error: eval failed: {exc}", file=sys.stderr)
        return 2

    runner_cfg = load_runner_config()
    output_dir = pathlib.Path(
        args.output_dir or runner_cfg.get("report_dir", str(DEFAULT_REPORT_DIR))
    )
    paths = write_report(report, output_dir)

    if args.json:
        print(render_json(report))
    else:
        s = report.summary
        print(
            f"{report.name}: {s['passed']}/{s['total_runs']} passed "
            f"(rate {s['pass_rate']})"
        )
        print(f"  markdown: {paths['markdown']}")
        print(f"  json:     {paths['json']}")
        print(f"  html:     {paths['html']}")

    if args.gate and report.summary["failed"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
