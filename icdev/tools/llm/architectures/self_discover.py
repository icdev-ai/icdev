# CUI // SP-CTI
"""Self-Discover architecture (agx-search-01).

Adapted from github.com/FareedKhan-dev/all-agentic-architectures (MIT,
Copyright (c) 2025 Fareed Khan) and Zhou et al. "Self-Discover: Large Language
Models Self-Compose Reasoning Structures" (2024). Pattern only; no upstream code
vendored.

Self-Discover composes a TASK-SPECIFIC reasoning structure before solving,
instead of applying one fixed prompt to every task shape:

    SELECT    — the LLM names the reasoning modules (from a DATA bank) that fit
                this task. Python validates the names against the bank and drops
                anything unknown — the deterministic-picker guarantee: the model
                only *names* modules, Python *assembles* the structure.
    ADAPT     — the LLM rephrases each selected module to the task specifics.
    IMPLEMENT — Python assembles the adapted modules into an ordered reasoning
                structure (a plan), preserving the bank's canonical ordering.
    SOLVE     — the task is answered guided by that composed structure.

This is an OPTIONAL, registry-swappable architecture aimed at the ANVIL Architect
phase. It does NOT change the default Architect behavior — that decision belongs
to the benchmark (agx-bench-02) with measurements in hand.

LLM-agnostic: all inference via ``LLMRouter``; zero vendor-SDK imports, no
hardcoded model IDs. The SELECT step returns a small closed vocabulary (module
ids from the bank), so a 7B local model can hit it reliably; unknown ids degrade
deterministically to "drop".
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.llm.architectures.envelope import (
    ArchitectureBudget,
    ArchitectureResult,
    ArchitectureStep,
)
from tools.llm.architectures.registry import register
from tools.llm.provider import LLMRequest

_BANK_RELPATH = Path("context") / "reasoning_modules" / "architect_modules.yaml"


def _default_bank_path() -> Path:
    """Locate the bank by walking up from this file (never cwd — worktree/CI safe).

    Resolves correctly from both the live ``tools/`` tree and the ``icdev/``
    mirror: whichever ancestor actually contains ``context/reasoning_modules/``
    wins, so the single canonical bank at the repo root is found either way.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / _BANK_RELPATH
        if candidate.exists():
            return candidate
    # Fall back to the conventional repo-root location (tools/ tree).
    return here.parents[3] / _BANK_RELPATH


def load_module_bank(bank_path: Optional[Path] = None) -> List[Dict[str, str]]:
    """Load the reasoning-module bank (DATA). Returns [] if unavailable.

    Never raises: a missing/malformed bank degrades Self-Discover to an honest
    no-op rather than crashing the caller.
    """
    path = Path(bank_path) if bank_path else _default_bank_path()
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        modules = data.get("modules") or []
        out = []
        for m in modules:
            if isinstance(m, dict) and m.get("id"):
                out.append({
                    "id": str(m["id"]),
                    "name": str(m.get("name", m["id"])),
                    "description": str(m.get("description", "")).strip(),
                })
        return out
    except Exception:
        return []


def select_modules(named: Any, bank: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Filter model-named module ids against the bank (deterministic-picker).

    The model only *names* ids; this Python step is the authority on what is
    valid. Unknown ids are dropped, order follows the bank's canonical order so
    the composed structure is stable regardless of the order the model emitted.
    """
    valid_ids = {m["id"] for m in bank}
    named_ids = {str(x).strip() for x in (named or []) if str(x).strip()}
    chosen = named_ids & valid_ids
    return [m for m in bank if m["id"] in chosen]


def compose_structure(selected: List[Dict[str, str]]) -> str:
    """Assemble the selected modules into an ordered reasoning structure (Python)."""
    lines = []
    for i, m in enumerate(selected, 1):
        desc = m.get("adapted") or m.get("description") or ""
        lines.append(f"{i}. {m['name']}: {desc}")
    return "\n".join(lines)


def _coerce_request(task: Any) -> LLMRequest:
    if isinstance(task, LLMRequest):
        return copy.deepcopy(task)
    if isinstance(task, str):
        return LLMRequest(messages=[{"role": "user", "content": task}])
    raise TypeError(f"task must be str or LLMRequest, got {type(task).__name__}")


def _user_content(request: LLMRequest) -> str:
    for msg in request.messages or []:
        if isinstance(msg, dict) and msg.get("role") == "user":
            return str(msg.get("content", ""))
    return ""


def _content(resp) -> str:
    return (getattr(resp, "content", "") or "").strip()


def _extract_json(raw: str) -> Optional[dict]:
    if not raw:
        return None
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    try:
        return json.loads(match.group(0) if match else raw)
    except Exception:
        return None


def _degraded(reason: str, exc: Optional[Exception] = None, output: str = "") -> ArchitectureResult:
    return ArchitectureResult(
        architecture="self_discover",
        output=output,
        method="self_discover",
        degraded=True,
        stop_reason=reason,
        metadata={"error": f"{type(exc).__name__}: {exc}"} if exc else {},
    )


def self_discover(
    task,
    *,
    router=None,
    budget: Optional[ArchitectureBudget] = None,
    function: str = "architecture_run",
    bank_path: Optional[Path] = None,
    max_modules: int = 6,
    adapt: bool = True,
    **kwargs,
) -> ArchitectureResult:
    """Run SELECT -> ADAPT -> IMPLEMENT -> SOLVE over a task."""
    from tools.llm.router import LLMRouter

    router = router or LLMRouter()
    request = _coerce_request(task)
    task_text = _user_content(request)
    steps: List[ArchitectureStep] = []

    bank = load_module_bank(bank_path)
    if not bank:
        # No bank -> honestly fall back to a plain single-pass solve.
        try:
            resp = router.invoke(function, request)
            return ArchitectureResult(
                architecture="self_discover",
                output=_content(resp),
                method="self_discover",
                degraded=True,
                stop_reason="no_module_bank",
                metadata={"fallback": "single_pass"},
            )
        except Exception as exc:
            if isinstance(exc, (TypeError, ValueError, AttributeError)):
                raise
            return _degraded("unavailable", exc)

    # 1. SELECT — the model names modules; Python is the authority on validity.
    catalog = "\n".join(f"- {m['id']}: {m['name']} — {m['description']}" for m in bank)
    try:
        select_prompt = (
            "From the REASONING MODULES below, select the ids most useful for "
            "solving the TASK. Return STRICT JSON only:\n"
            '{"selected": ["<module id>", ...]}\n'
            f"Select at most {max_modules} ids.\n\n"
            f"REASONING MODULES:\n{catalog}\n\nTASK:\n{task_text}"
        )
        sel_resp = router.invoke(function, LLMRequest(
            messages=[{"role": "user", "content": select_prompt}],
            max_tokens=200, temperature=0.0,
        ))
        sel_data = _extract_json(_content(sel_resp)) or {}
        selected = select_modules(sel_data.get("selected"), bank)[:max_modules]
        steps.append(ArchitectureStep(name="select", detail={"selected": [m["id"] for m in selected]}))
    except Exception as exc:
        if isinstance(exc, (TypeError, ValueError, AttributeError)):
            raise
        return _degraded("select_unavailable", exc)

    # Deterministic fallback: if the model named nothing valid, use the Karpathy
    # core (k1-k5) — the discipline every ICDEV design task already applies.
    if not selected:
        selected = [m for m in bank if m["id"].startswith("k")][:max_modules]
        steps.append(ArchitectureStep(name="select_fallback", detail={"used": "karpathy_core"}))

    # 2. ADAPT — rephrase each selected module to the task (best-effort, optional).
    if adapt:
        try:
            adapt_prompt = (
                "Rephrase each reasoning module so it is specific to the TASK. "
                "Return STRICT JSON only:\n"
                '{"adapted": {"<module id>": "<task-specific instruction>", ...}}\n\n'
                "MODULES:\n"
                + "\n".join(f"- {m['id']}: {m['description']}" for m in selected)
                + f"\n\nTASK:\n{task_text}"
            )
            adapt_resp = router.invoke(function, LLMRequest(
                messages=[{"role": "user", "content": adapt_prompt}],
                max_tokens=500, temperature=0.2,
            ))
            adapted = (_extract_json(_content(adapt_resp)) or {}).get("adapted") or {}
            if isinstance(adapted, dict):
                for m in selected:
                    if adapted.get(m["id"]):
                        m["adapted"] = str(adapted[m["id"]])
            steps.append(ArchitectureStep(name="adapt", detail={"adapted": len(adapted)}))
        except Exception as exc:
            if isinstance(exc, (TypeError, ValueError, AttributeError)):
                raise
            # Adaptation is best-effort; fall through with the generic descriptions.
            steps.append(ArchitectureStep(name="adapt", detail={"skipped": "unavailable"}))

    # 3. IMPLEMENT — Python assembles the ordered reasoning structure.
    structure = compose_structure(selected)
    steps.append(ArchitectureStep(name="implement", detail={"module_count": len(selected)}))

    # 4. SOLVE — answer the task guided by the composed structure.
    try:
        solve_request = copy.deepcopy(request)
        guidance = (
            "Follow this task-specific reasoning structure, in order, then give "
            "the final answer:\n" + structure
        )
        solve_request.system_prompt = (
            (getattr(request, "system_prompt", "") or "") + "\n\n" + guidance
        ).strip()
        solve_resp = router.invoke(function, solve_request)
        output = _content(solve_resp)
        steps.append(ArchitectureStep(
            name="solve",
            model_ids=[getattr(solve_resp, "model_id", "") or ""],
            detail={"chars": len(output)},
        ))
    except Exception as exc:
        if isinstance(exc, (TypeError, ValueError, AttributeError)):
            raise
        return _degraded("solve_unavailable", exc)

    return ArchitectureResult(
        architecture="self_discover",
        output=output,
        steps=steps,
        method="self_discover",
        degraded=(not output),
        stop_reason="completed" if output else "empty_output",
        metadata={
            "selected_modules": [m["id"] for m in selected],
            "reasoning_structure": structure,
        },
    )


register("self_discover", self_discover, overwrite=True)
