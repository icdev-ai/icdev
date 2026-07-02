# CUI // SP-CTI
"""ACE LLM Step executor — runs bare-string role steps via LLMRouter.

Called by CoWorkerThread._normalise_step() when a step is a plain string
with no 'tool' key. Produces a message in ace_coworker_messages and returns
the LLM response text so the step result is visible in the Activity Log.
"""
from __future__ import annotations

from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.ace.llm_step")

_DB_ENV = "ICDEV_ACE_DB_URL"

_STEP_PROMPTS: dict[str, str] = {
    "analyze_requirements": (
        "You are a senior software engineer. Analyze the following problem statement "
        "and list the key requirements, acceptance criteria, and technical constraints. "
        "Be concise and structured."
    ),
    "write_tests": (
        "You are a TDD practitioner. Based on the requirements analysis, write the "
        "key test cases (as pseudocode or Python pytest stubs) that must pass for "
        "this feature to be complete."
    ),
    "implement_code": (
        "You are an AI developer. Implement the code changes needed to satisfy the "
        "requirements. Reference specific files and functions. Be concrete."
    ),
    "run_tests": (
        "You are a QA engineer. Describe the test execution plan: which tests to run, "
        "expected results, and how to verify success."
    ),
    "refactor": (
        "You are a code reviewer. Identify any refactoring opportunities in the "
        "implementation. Keep it minimal — only changes that reduce complexity."
    ),
    "update_manifest": (
        "You are a documentation maintainer. List any manifest or documentation files "
        "that need updating based on the implementation changes."
    ),
    "codelens_scan": (
        "You are a static analysis tool. Review the code changes for: (1) unused imports, "
        "(2) dead code, (3) missing type hints, (4) security anti-patterns. Report findings."
    ),
    "coherence_check": (
        "You are a coherence validator. Check that the implementation is consistent with "
        "ICDEV FORGE framework conventions: tool registration, import namespaces, "
        "classification markings, and compliance gates."
    ),
    "e2e_run": (
        "You are a QA automation engineer. Design the end-to-end test scenario for this "
        "change: what pages/APIs to hit, what to assert, and what edge cases to cover."
    ),
    # Documentation domain steps
    "audit_existing_docs": (
        "You are a technical writer auditing ICDEV project documentation. "
        "List every documentation file (docs/, CLAUDE.md, tools/manifest/, args/) "
        "that is stale, incomplete, or missing sections relative to the current codebase. "
        "Be specific: file path, what is missing, and why it matters."
    ),
    "identify_gaps": (
        "You are a technical writer identifying documentation gaps. "
        "Based on recent code changes and the audit, produce a prioritized list of gaps: "
        "(1) missing CLI commands in docs/reference/commands.md, "
        "(2) outdated architecture descriptions, "
        "(3) broken cross-references. Format as a numbered list."
    ),
    "draft_updates": (
        "You are a technical writer drafting documentation updates. "
        "For each gap identified, write the corrected or new documentation text. "
        "Include exact file paths and section headings. Be precise and concise."
    ),
    "produce_report": (
        "You are a technical writer producing the final documentation gap report. "
        "Summarize: (1) files reviewed, (2) gaps found, (3) updates drafted, "
        "(4) remaining open items. This report will be stored as an ACE artifact."
    ),
    "review_draft": (
        "You are a documentation reviewer checking a draft for quality. "
        "Verify: accuracy of commands, completeness of sections, "
        "clarity of explanations, and adherence to ICDEV FORGE style (concise, imperative, no padding). "
        "List any issues found."
    ),
    "cross_reference_check": (
        "You are a documentation reviewer checking cross-references. "
        "Verify all internal links, file paths, command examples, and section references "
        "are valid and point to files that exist in the current codebase."
    ),
    "verify_completeness": (
        "You are a documentation reviewer verifying completeness. "
        "Confirm every new tool, CLI command, canvas, or API endpoint introduced in recent commits "
        "has a corresponding entry in docs/reference/commands.md or tools/manifest/. "
        "List anything that is still undocumented."
    ),
    "sign_off": (
        "You are a documentation reviewer providing final sign-off. "
        "State clearly: (1) APPROVED — all gaps addressed, or (2) REJECTED with specific "
        "remaining issues. If approved, produce the acceptance statement."
    ),
}

_DEFAULT_PROMPT = (
    "You are an AI co-worker executing a specific task step. "
    "Perform the step described below thoroughly and concisely."
)


def invoke(
    step_name: str,
    instance_id: str,
    coworker_id: str,
    llm_function: str = "code_generation",
    problem_text: str = "",
    role_description: str = "",
) -> str:
    """Invoke the LLM for a bare-string role step and persist the result.

    Returns the LLM response text (or an error string on failure).
    """
    system_prompt = _STEP_PROMPTS.get(step_name, _DEFAULT_PROMPT)
    user_prompt = (
        f"STEP: {step_name.replace('_', ' ').title()}\n\n"
        f"ROLE: {role_description or 'AI co-worker'}\n\n"
        f"PROBLEM:\n{problem_text or '(no problem description provided)'}\n\n"
        f"Execute this step and provide your output."
    )

    result_text = _invoke_llm(llm_function, system_prompt, user_prompt, step_name)
    _persist_message(instance_id, coworker_id, step_name, result_text)
    return result_text


def _invoke_llm(llm_function: str, system_prompt: str, user_prompt: str, step_name: str) -> str:
    """Try LLMRouter; fall back to a deterministic placeholder on failure."""
    try:
        from tools.llm.router import LLMRouter, LLMRequest

        router = LLMRouter()
        req = LLMRequest(
            function=llm_function,
            system=system_prompt,
            prompt=user_prompt,
            max_tokens=1024,
        )
        response = router.invoke(llm_function, req)
        text = (
            response.text
            if hasattr(response, "text")
            else str(response)
        )
        logger.info("ace llm_step %s: got %d chars from LLMRouter", step_name, len(text))
        return text
    except Exception as exc:
        logger.warning("ace llm_step %s: LLMRouter failed (%s), using stub", step_name, exc)
        return (
            f"[Step: {step_name}] LLM invocation unavailable ({exc}). "
            f"To enable AI-driven steps, configure an LLM provider in .env "
            f"(OPENAI_API_KEY, ANTHROPIC_API_KEY, or OLLAMA_BASE_URL)."
        )


def _persist_message(instance_id: str, coworker_id: str, step_name: str, text: str) -> None:
    """Write the step result to ace_coworker_messages."""
    try:
        from icdev.tools.db.storage import get_canvas_connection

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn = get_canvas_connection(_DB_ENV)
        try:
            conn.execute(
                "INSERT INTO ace_coworker_messages "
                "(instance_id, coworker_id, role, content, created_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (instance_id, coworker_id, f"step:{step_name}", text, now),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("ace llm_step persist failed for %s/%s: %s", instance_id, step_name, exc)
