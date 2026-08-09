# CUI // SP-CTI
"""Versioned loop feedback and golden patterns (clx-fb-01, clx-gold-01).

An agent loop that re-prompts from scratch every run cannot improve: a
correction a human made yesterday is gone by this morning. Two small, durable
inputs fix that, and both are plain files under version control so a correction
is reviewable, revertable and attributable like any other change.

**Feedback** (``context/loop_feedback/<loop_id>.md``) accumulates corrections
made to a loop's output — what it got wrong, and what to do instead. Loaded into
the loop's context on every run.

**Golden patterns** (``context/golden_patterns/<name>/``) are human-written
before/after reference implementations. They are the counterweight to
LLM-generated skills: those are produced by a model and describe what it already
tends to do, whereas these are written by a person to correct what it tends to
get wrong.

Both live under ``context/`` because that is FORGE's Context layer — static
reference material fed to the model — rather than the ``.icdev/`` directory the
source analysis proposed, which would sit outside the framework's own structure
for no benefit.

A note on choosing golden patterns: pick cases where current output is
*measurably* wrong. A pattern the model already follows adds tokens and teaches
nothing, and a set with no headroom makes the loop look better than it is.
The seeded patterns each cite a live violation count.

Usage::

    from tools.agent_runtime.loop_context import build_loop_context

    extra = build_loop_context(loop_id="kanban_build", patterns=["pg_placeholders"])
    system_prompt = base_prompt + extra
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_runtime.loop_context")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
FEEDBACK_DIR = BASE_DIR / "context" / "loop_feedback"
GOLDEN_DIR = BASE_DIR / "context" / "golden_patterns"

#: Keep the injected block small enough that it cannot crowd out the actual task.
MAX_FEEDBACK_CHARS = 6_000
MAX_PATTERN_CHARS = 8_000

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def _safe(name: str) -> Optional[str]:
    """Reject anything that could escape the directory. Returns None if unsafe."""
    name = (name or "").strip()
    if not name or not _SAFE_ID.match(name) or name in (".", ".."):
        logger.warning("loop_context: refusing unsafe identifier %r", name)
        return None
    return name


def feedback_path(loop_id: str) -> Optional[Path]:
    safe = _safe(loop_id)
    return FEEDBACK_DIR / f"{safe}.md" if safe else None


def load_feedback(loop_id: str) -> str:
    """Return the accumulated corrections for ``loop_id``, or an empty string."""
    path = feedback_path(loop_id)
    if path is None or not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.warning("loop_context: cannot read %s: %s", path, exc)
        return ""
    if len(text) > MAX_FEEDBACK_CHARS:
        logger.info(
            "loop_context: feedback for %s is %d chars; truncating to %d. "
            "Consider pruning resolved corrections.",
            loop_id, len(text), MAX_FEEDBACK_CHARS,
        )
        text = text[:MAX_FEEDBACK_CHARS] + "\n… (truncated)"
    return text


def append_feedback(loop_id: str, correction: str, *, source: str = "human") -> bool:
    """Record one correction. Append-only and dated so history stays readable.

    Returns True when written. The file is intended to be committed — that is
    what makes a correction revertable and attributable.
    """
    path = feedback_path(loop_id)
    correction = (correction or "").strip()
    if path is None or not correction:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).date().isoformat()
        if not path.exists():
            path.write_text(
                f"# Loop feedback — {loop_id}\n\n"
                f"Corrections applied to this loop's output. Loaded into the "
                f"loop's context on every run.\n\n"
                f"## Corrections\n\n",
                encoding="utf-8", newline="",
            )
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"- {stamp} ({source}): {correction}\n")
        return True
    except OSError as exc:
        logger.warning("loop_context: cannot append feedback to %s: %s", path, exc)
        return False


def list_patterns() -> list[str]:
    """Names of the available golden patterns."""
    if not GOLDEN_DIR.is_dir():
        return []
    return sorted(p.name for p in GOLDEN_DIR.iterdir() if p.is_dir())


def load_pattern(name: str) -> str:
    """Return one golden pattern rendered for a prompt, or an empty string."""
    safe = _safe(name)
    if safe is None:
        return ""
    directory = GOLDEN_DIR / safe
    if not directory.is_dir():
        logger.debug("loop_context: no golden pattern named %r", safe)
        return ""

    chunks: list[str] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        try:
            body = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if body:
            chunks.append(f"### {safe}/{path.name}\n\n{body}")
    rendered = "\n\n".join(chunks)
    if len(rendered) > MAX_PATTERN_CHARS:
        rendered = rendered[:MAX_PATTERN_CHARS] + "\n… (truncated)"
    return rendered


def build_loop_context(
    *,
    loop_id: str = "",
    patterns: Optional[list[str]] = None,
) -> str:
    """Assemble the feedback + golden-pattern block to append to a system prompt.

    Returns an empty string when there is nothing to say, so a caller can append
    unconditionally without producing a stray heading.
    """
    sections: list[str] = []

    if loop_id:
        feedback = load_feedback(loop_id)
        if feedback:
            sections.append(
                "## Loop feedback\n\n"
                "Corrections previously applied to this loop's output. Follow "
                "them; they were recorded because the loop got these wrong.\n\n"
                f"{feedback}"
            )

    for name in patterns or []:
        body = load_pattern(name)
        if body:
            sections.append(
                f"## Golden pattern: {name}\n\n"
                "Human-written reference. Follow it exactly rather than "
                "improvising an equivalent.\n\n"
                f"{body}"
            )

    if not sections:
        return ""
    return "\n\n" + "\n\n".join(sections) + "\n"
