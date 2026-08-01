#!/usr/bin/env python3
# CUI // SP-CTI
"""The AI coding platforms ICDEV(TM) ships instructions for — one canonical list.

ICDEV is LLM-agnostic by design: the same guardrails are published to every
major AI coding tool, not just Claude Code. That claim was true of the REPO and
false of the WHEEL. All ten platform instruction files were tracked in git and
none of them shipped, so `pip install icdev && icdev init` produced a
Claude-only project — `CLAUDE.md` and `.claude/`, with no `AGENTS.md`,
`GEMINI.md`, `.cursor/rules/`, `.github/copilot-instructions.md` or the rest.

The list lived in four places that had no way to disagree loudly:

  * `coherence_checker.check_karpathy_sync`  — audits the files in the repo
  * `installer/prebuild_bootstrap.py`        — decides what enters the wheel
  * `cli/init.py`                            — decides what lands in a project
  * `dx/instruction_generator.py`            — writes them

Only the first knew about all ten. Adding a platform to the generator did not
add it to the wheel, and nothing said so. This module is the one definition;
`tests/test_ai_platform_coverage.py` fails when a consumer falls behind it.

CLAUDE.md is deliberately NOT in this list. It is the SOURCE the others are
generated from, and it already ships through its own bootstrap entry — listing
it here would double-copy it and imply it is one platform among ten.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: ``(platform_id, repo-relative path)`` for every non-Claude AI coding tool.
#:
#: Order matches `coherence_checker.check_karpathy_sync` so a reader comparing
#: the two sees the same sequence.
AI_PLATFORM_FILES: tuple[tuple[str, str], ...] = (
    ("codex", "AGENTS.md"),
    ("cline", ".clinerules"),
    ("cursor", ".cursor/rules/icdev.mdc"),
    ("windsurf", ".windsurf/rules/icdev.md"),
    ("copilot", ".github/copilot-instructions.md"),
    ("amazonq", ".amazonq/rules/icdev.md"),
    ("junie", ".junie/guidelines.md"),
    ("gemini", "GEMINI.md"),
    ("goose", ".goosehints"),
    ("devin", "CONVENTIONS.md"),
)

#: The Claude-native files. Separate because they are the source of truth the
#: others are generated from, not peers in the platform list.
CLAUDE_FILES: tuple[str, ...] = ("CLAUDE.md",)


def platform_ids() -> tuple:
    return tuple(p for p, _ in AI_PLATFORM_FILES)


def platform_paths() -> tuple:
    return tuple(rel for _p, rel in AI_PLATFORM_FILES)


def bootstrap_name(rel_path: str) -> str:
    """Flattened name for a platform file inside the wheel's bootstrap payload.

    Wheel package-data cannot carry dot-directories reliably across build
    backends, so ``.cursor/rules/icdev.mdc`` is stored as
    ``platforms/cursor__rules__icdev.mdc`` and restored to its real path by
    `cli.init`. The mapping is reversible and lossless.
    """
    return "platforms/" + rel_path.lstrip(".").lstrip("/").replace("/", "__")


def missing_in_repo() -> list:
    """Platform files this repo declares but does not actually have."""
    return [rel for _p, rel in AI_PLATFORM_FILES if not (REPO_ROOT / rel).is_file()]


def main(argv: list | None = None) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="AI platform instruction-file coverage.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    missing = missing_in_repo()
    payload = {
        "platforms": len(AI_PLATFORM_FILES),
        "files": [{"platform": p, "path": rel, "present": (REPO_ROOT / rel).is_file()}
                  for p, rel in AI_PLATFORM_FILES],
        "missing_in_repo": missing,
        "ok": not missing,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"AI platforms: {len(AI_PLATFORM_FILES)}")
        for entry in payload["files"]:
            print(f"  [{'OK ' if entry['present'] else 'MISS'}] "
                  f"{entry['platform']:<9} {entry['path']}")
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
