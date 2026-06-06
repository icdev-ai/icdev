# CUI // SP-CTI
"""Smoke-test every Claude Code model tier in parallel.

Pings ``opus``, ``sonnet``, and ``haiku`` with a fixed prompt, captures
each response, and reports a single overall pass/fail. Used by the
testing framework to confirm the model wiring before kicking off a
real workflow.

Implements the contract documented in
``docs/rewrite/adw/specs/tools/testing/test_agent_models.md`` (OPT-75
Phase 3 clean-room rewrite).
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Tuple


PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.ci.modules.agent import prompt_claude_code  # noqa: E402
from tools.testing.data_types import AgentPromptRequest  # noqa: E402
from tools.testing.utils import make_run_id  # noqa: E402


MODELS: List[str] = ["opus", "sonnet", "haiku"]

TEST_PROMPT: str = (
    "You are a helpful assistant. Please respond to this test with:\n"
    "1. Confirm you received this message\n"
    "2. State which model you are\n"
    "3. Say \"Test successful!\"\n\n"
    "Keep your response brief."
)


# ────────────────────────────────────────────────────────────────────────────
# Per-model probe
# ────────────────────────────────────────────────────────────────────────────


def _output_file_for(run_id: str, model: str) -> Path:
    return PROJECT_ROOT / "agents" / run_id / f"agent_test_{model}.jsonl"


def _print_banner(model: str) -> None:
    bar = "=" * 50
    print(f"\n{bar}")
    print(f"Testing model: {model}")
    print(bar)


def test_model(model: str, run_id: str) -> Tuple[bool, str]:
    """Send the test prompt to ``model`` and report success or failure."""
    _print_banner(model)
    output_path = _output_file_for(run_id, model)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    request = AgentPromptRequest(
        prompt=TEST_PROMPT,
        agent_name=f"test_{model}",
        model=model,
        output_file=str(output_path),
    )

    try:
        response = prompt_claude_code(request)
    except Exception as exc:
        message = f"Exception: {exc}"
        print(f"FAIL {model} — Exception!")
        print(message)
        return False, f"{model}: {message}"

    if getattr(response, "success", False):
        preview = (getattr(response, "output", "") or "")[:200]
        session = getattr(response, "session_id", None)
        print(f"PASS {model} — Success!")
        print(f"Session ID: {session}")
        print(f"Preview: {preview}...")
        return True, f"{model}: Success"

    error_text = getattr(response, "output", "") or "no output"
    print(f"FAIL {model} — Failed!")
    print(f"Error: {error_text}")
    return False, f"{model}: {error_text}"


# ────────────────────────────────────────────────────────────────────────────
# Driver
# ────────────────────────────────────────────────────────────────────────────


def _run_in_parallel(
    models: List[str], run_id: str,
) -> Dict[str, Tuple[bool, str]]:
    results: Dict[str, Tuple[bool, str]] = {}
    with ThreadPoolExecutor(max_workers=max(1, len(models))) as pool:
        futures = {
            pool.submit(test_model, model, run_id): model
            for model in models
        }
        for future in as_completed(futures):
            model = futures[future]
            try:
                results[model] = future.result()
            except Exception as exc:
                results[model] = (False, f"{model}: Exception: {exc}")
    return results


def main(argv: List[str] = None) -> int:
    run_id = make_run_id()

    print("CUI // SP-CTI")
    print("Testing Claude Code agent with different models (parallel)")
    print(f"Run ID: {run_id}")
    print(f"Models: {', '.join(MODELS)}")

    results = _run_in_parallel(MODELS, run_id)

    print("\n" + "=" * 50)
    print("Test Summary")
    print("=" * 50)

    all_success = True
    for model in MODELS:
        if model not in results:
            all_success = False
            print(f"FAIL — {model}: not run")
            continue
        success, message = results[model]
        status = "PASS" if success else "FAIL"
        if not success:
            all_success = False
        print(f"{status} — {message}")

    overall = "All tests passed!" if all_success else "Some tests failed!"
    print(f"\nOverall: {overall}")
    return 0 if all_success else 1


if __name__ == "__main__":
    sys.exit(main())
