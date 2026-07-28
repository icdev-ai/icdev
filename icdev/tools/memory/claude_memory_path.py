# CUI // SP-CTI
"""Locate Claude Code's auto-memory directory for this checkout.

Claude Code stores per-project auto-memory under
``$USERPROFILE/.claude/projects/<project-slug>/memory``, where ``<project-slug>``
is the absolute path of the project root with its separators and drive colon
flattened to hyphens (``C:\\ai\\icdev`` -> ``C--ai-icdev``).

Three call sites used to hardcode that slug as the literal ``C--AI-ICDev``:
``tools/memory/wiki_tool_query.py`` and two in ``tools/ace/controller.py``. That
string is correct for exactly one checkout on one machine — and only because
Windows compares paths case-insensitively; the real directory is lowercase.
Relocate the checkout, rename the folder, or run on a case-sensitive
filesystem and the lookups silently resolve to nothing, so ACE's cross-session
memory degrades with no error and no log line.

``memory_write.update_crossrefs`` already derived the slug correctly. This module
is that derivation, extracted so every caller shares one definition.

Set ``ICDEV_CLAUDE_MEMORY_DIR`` to override for operators who relocate the
directory or run under a non-standard profile.
"""
from __future__ import annotations

import os
from pathlib import Path

#: Project root — this file lives at <root>/tools/memory/claude_memory_path.py
BASE_DIR = Path(__file__).resolve().parent.parent.parent

ENV_OVERRIDE = "ICDEV_CLAUDE_MEMORY_DIR"


def project_slug(base_dir: Path | None = None) -> str:
    """Return Claude Code's project-directory slug for *base_dir*.

    ``C:\\ai\\icdev`` -> ``C--ai-icdev``. Case is preserved: the slug mirrors the
    path as spelled on disk.
    """
    root = Path(base_dir) if base_dir is not None else BASE_DIR
    return (
        str(root)
        .replace("\\", "-")
        .replace("/", "-")
        .replace(":", "-")
        .lstrip("-")
    )


def claude_memory_dir(base_dir: Path | None = None) -> Path:
    """Return the auto-memory directory for this checkout.

    Honours ``ICDEV_CLAUDE_MEMORY_DIR`` when set. The returned path is *not*
    guaranteed to exist — callers that need it to exist should check, so a
    missing directory stays visible instead of being silently treated as empty.
    """
    override = os.environ.get(ENV_OVERRIDE)
    if override:
        return Path(override).expanduser()

    userprofile = Path(os.environ.get("USERPROFILE", Path.home()))
    return userprofile / ".claude" / "projects" / project_slug(base_dir) / "memory"
