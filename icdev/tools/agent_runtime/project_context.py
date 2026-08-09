# CUI // SP-CTI
"""Project-instruction loading for the SAG runtime (hgx-sess-01).

WHY THIS EXISTS

The standalone agent runtime never read the project's own instructions. Grepping
``tools/agent_runtime/`` for ``CLAUDE.md`` finds a dozen hits and every one of
them uses the name as a *repo-root sentinel* for an upward directory walk — no
file is ever opened and put in a prompt. ``AGENTS.md`` and ``memory/MEMORY.md``
were not handled at all. What the agent actually received was the six-line
``_DEFAULT_SYSTEM_PROMPT``, the operator profile, and a handful of memory hits,
so it re-derived (or violated) conventions the repo states plainly in writing.

WHAT THIS DOES

At session start the runtime prepends a **budgeted** block containing:

  1. ``CLAUDE.md``        — the project's instructions to coding agents
  2. ``AGENTS.md``        — the agent-facing companion file
  3. ``memory/MEMORY.md`` — the curated long-term memory index
  4. project state        — reuses ``tools/project/session_context_builder.py``
                            (compliance posture, dev profile, suggested
                            workflows); this module does not re-implement it.

BUDGETING IS THE WHOLE POINT

These files are large — CLAUDE.md alone is ~11k tokens by the platform
estimator. Injected whole into a 32k-window local model they would eat a third
of the window before the user has typed anything, and the agent loop's context
compressor would then start discarding the *conversation* to make room for
boilerplate. So the block is sized against
:func:`tools.llm.context_budget.floor_window_for_function` — the smallest window
any model that could serve this routing function might have — never against a
constant. A large-window cloud model gets the files intact; a 32k local model
gets a proportionally truncated block with an explicit marker naming what was
cut and where to read it (the agent has ``read_file``). It degrades; it never
silently swallows the window.

Per-section shares mean truncation pressure lands where it costs least: the
project-state summary is squeezed before the project's own rules are.

LLM-agnostic: no model IDs — the window is resolved by routing function through
the same config the router reads. OS-agnostic: repo root from ``__file__`` (never
``os.getcwd()`` — the runtime is routinely launched from a worktree), every read
is ``encoding="utf-8", newline=""``, and paths are ``pathlib`` throughout.

CLI::

    python tools/agent_runtime/project_context.py --json
    python tools/agent_runtime/project_context.py --function code_generation
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path

import sys

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[2] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
# Named _IMPORT_ROOT, not _REPO_ROOT: this module already binds that name below
# to a sentinel walk, which answers a different question.
_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(_IMPORT_ROOT))

from tools.llm.context_budget import (
    available_input_tokens,
    estimate_tokens,
    floor_window_for_function,
)
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_runtime.project_context")


# Resolve the repository root from this file's location, never from cwd — the
# runtime is routinely launched from a git worktree or a subdirectory. This
# module is mirrored to BOTH <repo>/tools/agent_runtime/ and
# <repo>/icdev/tools/agent_runtime/, which sit at different depths, so we walk
# upward to a sentinel rather than counting parents (same rule as builtin_tools).
def _find_repo_root(start: Path) -> Path:
    sentinels = ("pyproject.toml", ".git", "CLAUDE.md")
    for parent in [start, *start.parents]:
        if any((parent / s).exists() for s in sentinels):
            return parent
    return start.parents[1] if len(start.parents) > 1 else start


_REPO_ROOT = _find_repo_root(Path(__file__).resolve().parent)


def repo_root() -> Path:
    """Repository root, resolved from ``__file__`` (worktree-safe)."""
    return _REPO_ROOT


#: Fraction of the *available* input window this block may occupy. The rest of
#: the window belongs to the conversation, tool results and retrieved memory.
#: A share (not a token count) is what makes the block scale with the model:
#: 25% of a 200k window is the whole corpus intact; 25% of 32k is a summary.
WINDOW_SHARE = 0.25

#: Below this a section is dropped rather than shaved to a stub. Two hundred
#: tokens of CLAUDE.md is not "the project's instructions", it is noise that
#: reads like instructions — strictly worse than an honest omission.
MIN_SECTION_TOKENS = 200

#: Reserved out of a section's allowance for the truncation marker itself.
_MARKER_TOKENS = 48

#: Documents to load, in priority order, with the share of the block each may
#: take. Unused allowance rolls forward to later sections.
DOCUMENTS: tuple[tuple[str, str, float], ...] = (
    ("CLAUDE.md", "Project instructions (CLAUDE.md)", 0.50),
    ("AGENTS.md", "Agent instructions (AGENTS.md)", 0.20),
    ("memory/MEMORY.md", "Long-term memory index (memory/MEMORY.md)", 0.15),
)

#: Share for the `session_context_builder` project-state summary (lowest
#: priority: it is derived state the agent can re-query, not a rule).
PROJECT_STATE_SHARE = 0.15
_PROJECT_STATE_TITLE = "Project state (session_context_builder)"

_HEADER = (
    "# Project context (loaded at session start)\n"
    "The following are this repository's own instructions to you, plus its "
    "current state. Treat them as binding: they override your defaults. Some "
    "sections may be truncated to fit the context window — use `read_file` on "
    "the named path when you need the rest."
)

ENV_DISABLE = "ICDEV_SAG_PROJECT_CONTEXT"
ENV_DISABLE_STATE = "ICDEV_SAG_PROJECT_STATE"

# Legacy private aliases — kept because they were the module's only names for
# these before hgx-cfg-01 made them public for the config layer to reference.
_ENV_DISABLE = ENV_DISABLE
_ENV_DISABLE_STATE = ENV_DISABLE_STATE


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def context_enabled() -> bool:
    """Whether the project-context block is injected at all.

    ``ICDEV_SAG_PROJECT_CONTEXT`` → ``args/agent_runtime.yaml`` → on. The env
    var is read first and still wins (hgx-cfg-01).
    """
    try:
        from tools.agent_runtime.config import load_config

        return load_config().subsystem_enabled(
            "project_context", env=ENV_DISABLE, default=True
        )
    except Exception as exc:  # noqa: BLE001 — config is a layer, not a dependency
        logger.debug("project_context: config layer unavailable: %s", exc)
        return _env_flag(ENV_DISABLE)


def project_state_enabled() -> bool:
    """Whether the (DB-backed) project-state summary is included in the block.

    ``ICDEV_SAG_PROJECT_STATE`` → ``args/agent_runtime.yaml`` → on.
    """
    try:
        from tools.agent_runtime.config import load_config

        return load_config().flag(
            "subsystems.project_context.include_project_state",
            env=ENV_DISABLE_STATE,
            default=True,
        )
    except Exception as exc:  # noqa: BLE001 — config is a layer, not a dependency
        logger.debug("project_context: config layer unavailable: %s", exc)
        return _env_flag(ENV_DISABLE_STATE)


@dataclass
class Section:
    """One loadable block of project context."""

    title: str
    #: Repo-relative path the agent can read for the full text ('' if derived).
    source: str
    text: str
    share: float


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def _read_document(path: Path) -> str:
    """Read a UTF-8 document without newline translation.

    ``newline=""`` keeps the file's own line endings out of Python's translation
    layer (the OS-agnostic rule for any file an agent touches); we then normalise
    CRLF to LF for the *prompt copy* only, so a Windows checkout does not spend
    tokens on carriage returns. The file on disk is never rewritten here.
    """
    try:
        with path.open("r", encoding="utf-8", newline="") as fh:
            raw = fh.read()
    except Exception as exc:  # noqa: BLE001 — a missing/unreadable doc is not fatal
        logger.debug("project_context: cannot read %s: %s", path, exc)
        return ""
    return raw.replace("\r\n", "\n").replace("\r", "\n").strip()


def _project_state_markdown(root: Path) -> str:
    """Render the existing session-context builder's markdown (best-effort).

    Reuses ``tools/project/session_context_builder.py`` rather than duplicating
    project detection, compliance posture and workflow suggestions. ``directory``
    is passed explicitly so detection does not depend on ``os.getcwd()``.
    """
    try:
        from tools.project.session_context_builder import (
            build_session_context,
            render_markdown,
        )

        context = build_session_context(directory=str(root))
        return (render_markdown(context) or "").strip()
    except Exception as exc:  # noqa: BLE001 — advisory context, never blocking
        logger.debug("project_context: session context unavailable: %s", exc)
        return ""


def collect_sections(
    root: Path | None = None, *, include_project_state: bool = True
) -> list[Section]:
    """Load every available context section, highest priority first.

    Missing files are skipped silently (a project without an ``AGENTS.md`` is
    normal), so the returned list is exactly what exists.
    """
    base = Path(root) if root is not None else repo_root()
    sections: list[Section] = []
    for rel, title, share in DOCUMENTS:
        text = _read_document(base / rel)
        if text:
            sections.append(Section(title=title, source=rel, text=text, share=share))
    if include_project_state:
        state = _project_state_markdown(base)
        if state:
            sections.append(
                Section(
                    title=_PROJECT_STATE_TITLE,
                    source="",
                    text=state,
                    share=PROJECT_STATE_SHARE,
                )
            )
    return sections


# ---------------------------------------------------------------------------
# Budgeting
# ---------------------------------------------------------------------------
def context_budget_tokens(
    llm_function: str,
    *,
    system_prompt: str = "",
    reserved_output: int = 1024,
    share: float = WINDOW_SHARE,
) -> int:
    """Tokens this block may occupy for ``llm_function``.

    Derived from :func:`floor_window_for_function` — the MINIMUM window across
    the routed chain, because ``two_tier`` applies before chain resolution, RL
    re-ranking reorders it after, and the CLI bridge is prepended at invoke
    time. Budgeting for the largest member and being served by the smallest is
    an overflow the caller cannot recover from.
    """
    usable = available_input_tokens(
        llm_function, system_prompt=system_prompt, reserved_output=reserved_output
    )
    return max(int(usable * share), 0)


def _truncate_to_tokens(text: str, budget: int, source: str) -> tuple[str, int]:
    """Trim ``text`` to ``budget`` tokens at a line boundary.

    Returns ``(text, omitted_lines)``. Truncation is always announced: an agent
    that cannot tell a complete rule set from a truncated one will confidently
    act on half of it.
    """
    if estimate_tokens(text) <= budget:
        return text, 0
    lines = text.split("\n")
    body_budget = max(budget - _MARKER_TOKENS, 0)
    kept: list[str] = []
    used = 0
    for line in lines:
        cost = estimate_tokens(line) + 1  # +1 for the newline
        if used + cost > body_budget:
            break
        kept.append(line)
        used += cost
    omitted = len(lines) - len(kept)
    where = f"read `{source}` for the full text" if source else "re-run the builder"
    kept.append(
        f"\n[... {omitted} of {len(lines)} lines omitted to fit the context "
        f"budget — {where} ...]"
    )
    return "\n".join(kept), omitted


def plan_sections(sections: list[Section], budget: int) -> list[dict]:
    """Allocate ``budget`` across ``sections`` and render each to its allowance.

    Allowance is cumulative: a section that does not spend its share leaves the
    remainder to the ones after it, so a small ``AGENTS.md`` buys ``MEMORY.md``
    room instead of being wasted. A section whose allowance falls below
    :data:`MIN_SECTION_TOKENS` is dropped whole and reported.
    """
    plan: list[dict] = []
    allocated = 0
    spent = 0
    for sec in sections:
        allocated += int(budget * sec.share)
        allowance = allocated - spent
        full_tokens = estimate_tokens(sec.text)
        if allowance < MIN_SECTION_TOKENS:
            plan.append(
                {
                    "title": sec.title,
                    "source": sec.source,
                    "included": False,
                    "truncated": False,
                    "omitted_lines": sec.text.count("\n") + 1,
                    "tokens": 0,
                    "full_tokens": full_tokens,
                    "text": "",
                }
            )
            continue
        text, omitted = _truncate_to_tokens(sec.text, allowance, sec.source)
        tokens = estimate_tokens(text)
        spent += tokens
        plan.append(
            {
                "title": sec.title,
                "source": sec.source,
                "included": True,
                "truncated": omitted > 0,
                "omitted_lines": omitted,
                "tokens": tokens,
                "full_tokens": full_tokens,
                "text": text,
            }
        )
    return plan


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def build_project_context(
    *,
    llm_function: str,
    system_prompt: str = "",
    root: Path | None = None,
    include_project_state: bool = True,
    budget_tokens: int | None = None,
) -> str:
    """Render the budgeted project-context preamble ('' when there is nothing).

    ``budget_tokens`` overrides the computed budget (tests, callers that already
    know what they can spend); otherwise it comes from
    :func:`context_budget_tokens`.
    """
    return describe(
        llm_function=llm_function,
        system_prompt=system_prompt,
        root=root,
        include_project_state=include_project_state,
        budget_tokens=budget_tokens,
    )["text"]


def describe(
    *,
    llm_function: str,
    system_prompt: str = "",
    root: Path | None = None,
    include_project_state: bool = True,
    budget_tokens: int | None = None,
) -> dict:
    """Build the preamble and report how the budget was spent.

    Returns ``{text, budget, window, tokens_used, sections}``. The accounting is
    the contract: a caller (or ``--json``) can always show which instruction
    files were truncated and by how much, which is the difference between a
    budget and a silent truncation.
    """
    sections = collect_sections(root, include_project_state=include_project_state)
    window = floor_window_for_function(llm_function)
    budget = (
        int(budget_tokens)
        if budget_tokens is not None
        else context_budget_tokens(llm_function, system_prompt=system_prompt)
    )
    empty = {
        "text": "",
        "budget": budget,
        "window": window,
        "tokens_used": 0,
        "sections": [],
    }
    if not sections or budget <= 0:
        return empty

    plan = plan_sections(sections, budget)
    parts: list[str] = []
    for item in plan:
        if not item["included"]:
            continue
        parts.append(f"## {item['title']}\n\n{item['text']}")
    if not parts:
        return {**empty, "sections": [{k: v for k, v in i.items() if k != "text"} for i in plan]}

    dropped = [i["title"] for i in plan if not i["included"]]
    body = "\n\n".join(parts)
    if dropped:
        body += "\n\n[Omitted for context budget: " + "; ".join(dropped) + "]"
    text = f"{_HEADER}\n\n{body}"
    return {
        "text": text,
        "budget": budget,
        "window": window,
        "tokens_used": estimate_tokens(text),
        "sections": [{k: v for k, v in i.items() if k != "text"} for i in plan],
    }


def build_for_runtime(llm_function: str, system_prompt: str = "") -> str:
    """Entry point the runtime calls at session start. Never raises.

    ``ICDEV_SAG_PROJECT_CONTEXT=0`` disables the block entirely;
    ``ICDEV_SAG_PROJECT_STATE=0`` keeps the instruction files but skips the
    (DB-backed) project-state summary. Both fall back to
    ``args/agent_runtime.yaml`` when unset (hgx-cfg-01).
    """
    if not context_enabled():
        return ""
    try:
        return build_project_context(
            llm_function=llm_function,
            system_prompt=system_prompt,
            include_project_state=project_state_enabled(),
        )
    except Exception as exc:  # noqa: BLE001 — context injection is best-effort
        logger.debug("project_context: injection skipped: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Show the project-context block the SAG runtime injects."
    )
    ap.add_argument(
        "--function",
        default="code_generation",
        help="Routing function to budget against (default: code_generation).",
    )
    ap.add_argument(
        "--no-project-state",
        action="store_true",
        help="Skip the session_context_builder summary (no DB access).",
    )
    ap.add_argument("--json", action="store_true", help="Emit the budget accounting.")
    args = ap.parse_args(argv)

    report = describe(
        llm_function=args.function,
        include_project_state=not args.no_project_state,
    )
    if args.json:
        print(json.dumps({k: v for k, v in report.items() if k != "text"}, indent=2))
    else:
        print(report["text"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
