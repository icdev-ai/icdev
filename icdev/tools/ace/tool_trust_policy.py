# CUI // SP-CTI
"""Trust-tier ↔ agent-tool policy for ACE co-workers — one source of truth.

An ACE role YAML declares two things independently: a ``trust_tier``, and — in
agent mode — an ``agent_tools`` list. Nothing related them, so a role could
declare a tool its own tier is forbidden to call and the contradiction only
surfaced at run time, once per call, as a permission-denied string the LLM had
to read and route around. ``qa_agent`` shipped in exactly that state:
``trust_tier: yellow`` plus ``run_tool``, whose only purpose in that role was to
invoke the ``python tools/testing/…`` commands in its ``icdev_tools`` list — so
every call it made was denied and the role could not do its job at all.

This module is the shared table both sides read:

* :func:`is_permitted` / :func:`denial_message` back the run-time pre-tool hook
  in :meth:`icdev.tools.ace.coworker_thread.CoWorkerThread._run_agent_loop`.
* :func:`validate_role_tools` backs the load-time check in
  :meth:`icdev.tools.ace.role_loader.RoleTemplate.from_dict`, so the
  contradiction is a load failure rather than a run-time surprise.

Because both sides read the same table they cannot disagree: a tool the loader
accepts is a tool the hook will let through.

Tiers are ordered ``red < orange < yellow < green``. A tool absent from
:data:`MIN_TRUST_TIER` is unrestricted — that set is the read-only tools. An
unrecognised tier ranks below ``red`` and therefore clears nothing, so an
unknown tier fails closed.
"""
from __future__ import annotations

from typing import Any

# Least → most trusted. Membership is also the validity check for a tier string.
TIER_ORDER: tuple[str, ...] = ("red", "orange", "yellow", "green")

# Minimum trust tier required to CALL each agent tool. Absent == unrestricted.
#
# The ladder only has to describe tools that write or execute; read-only tools
# (read_file, list_files, search_files, grep_files, read_result, load_skill, …)
# are bounded by the role's folder_access scope instead, which is a different
# axis and is enforced by FileAccessBroker.
MIN_TRUST_TIER: dict[str, str] = {
    # Unbounded write / execute. Green only — this is the ladder's whole point.
    "write_file": "green",
    "run_tool": "green",
    # Narrow verification seam: allowlisted TEST-EXECUTION commands only. The
    # command must be in the role's icdev_tools allowlist AND name a module
    # under tools/testing/ AND carry no mutating flag — enforced by the "test"
    # execution profile in icdev.tools.ace.tool_runner, not by trust alone.
    # That narrowing is what makes it safe to hand to yellow.
    "run_test_tool": "yellow",
}

# What AgentToolRegistry.build() falls back to when a role declares mode: agent
# with an empty agent_tools list. Declared here so validate_role_tools() checks
# the toolset the co-worker will ACTUALLY get, not just the one it wrote down.
DEFAULT_AGENT_TOOLS: tuple[str, ...] = ("read_file", "write_file", "run_tool", "done")

# Suggested substitute when a tier is denied a tool, keyed by (tool, min tier of
# the substitute). Purely advisory — it makes the denial actionable instead of
# a dead end.
_SUBSTITUTE: dict[str, str] = {
    "run_tool": "run_test_tool",
}


def tier_rank(trust_tier: Any) -> int:
    """Return the ordinal of *trust_tier*, or ``-1`` when unrecognised.

    ``-1`` is below every entry in :data:`TIER_ORDER`, so an unknown tier
    clears no restricted tool.
    """
    try:
        return TIER_ORDER.index(str(trust_tier or "").strip().lower())
    except ValueError:
        return -1


def is_known_tier(trust_tier: Any) -> bool:
    """True when *trust_tier* is one of :data:`TIER_ORDER`."""
    return tier_rank(trust_tier) >= 0


def min_tier_for(tool_name: str) -> str | None:
    """Minimum tier required to call *tool_name*, or ``None`` if unrestricted."""
    return MIN_TRUST_TIER.get(str(tool_name))


def is_permitted(tool_name: str, trust_tier: Any) -> bool:
    """True when a co-worker at *trust_tier* may call *tool_name*."""
    required = MIN_TRUST_TIER.get(str(tool_name))
    if required is None:
        return True
    return tier_rank(trust_tier) >= tier_rank(required)


def denial_message(tool_name: str, trust_tier: Any) -> str:
    """Human/LLM-readable reason *tool_name* is refused at *trust_tier*."""
    name = str(tool_name)
    required = MIN_TRUST_TIER.get(name, "green")
    msg = (
        f"Permission denied: '{name}' requires {required} trust tier or higher; "
        f"this co-worker is trust_tier={str(trust_tier)!r}."
    )
    substitute = _SUBSTITUTE.get(name)
    if substitute and is_permitted(substitute, trust_tier):
        msg += f" Use '{substitute}' instead, or a read-only tool."
    else:
        msg += " Use a read-only tool or request promotion to a higher tier."
    return msg


def effective_agent_tools(agent_tools: Any, mode: Any = "agent") -> list[str]:
    """Return the toolset a co-worker will actually be built with.

    Mirrors :meth:`AgentToolRegistry.build`: an agent-mode role that declares no
    ``agent_tools`` gets :data:`DEFAULT_AGENT_TOOLS`.
    """
    tools = [str(t) for t in (agent_tools or [])]
    if tools:
        return tools
    if str(mode or "").strip().lower() == "agent":
        return list(DEFAULT_AGENT_TOOLS)
    return []


def validate_role_tools(
    role_id: str,
    trust_tier: Any,
    agent_tools: Any,
    mode: Any = "agent",
) -> list[str]:
    """Return policy violations for one role (empty list means clean).

    Flags a role that declares — explicitly or via the agent-mode default — a
    tool its ``trust_tier`` may not call, and a role whose tier is not a
    recognised rung of the ladder.
    """
    problems: list[str] = []
    declared = [str(t) for t in (agent_tools or [])]
    tools = effective_agent_tools(agent_tools, mode)
    source = "agent_tools" if declared else "the default agent toolset"

    if tools and not is_known_tier(trust_tier):
        problems.append(
            f"role {role_id!r} declares unknown trust_tier {str(trust_tier)!r} "
            f"(expected one of: {', '.join(TIER_ORDER)})"
        )

    for name in tools:
        if is_permitted(name, trust_tier):
            continue
        required = MIN_TRUST_TIER[name]
        detail = (
            f"role {role_id!r} declares {name!r} in {source} but its "
            f"trust_tier {str(trust_tier)!r} may not call it — {name!r} "
            f"requires {required!r} or higher"
        )
        substitute = _SUBSTITUTE.get(name)
        if substitute and is_permitted(substitute, trust_tier):
            detail += f" (use {substitute!r}, which {str(trust_tier)!r} may call)"
        problems.append(detail)

    return problems


def assert_role_tools_valid(
    role_id: str,
    trust_tier: Any,
    agent_tools: Any,
    mode: Any = "agent",
) -> None:
    """Raise ``ValueError`` when a role's declared tools exceed its trust tier."""
    problems = validate_role_tools(role_id, trust_tier, agent_tools, mode)
    if problems:
        raise ValueError("; ".join(problems))


# ---------------------------------------------------------------------------
# CLI — sweep every role YAML so the gate is runnable outside a test run
# ---------------------------------------------------------------------------


def check_all_roles(roles_dir: Any = None) -> dict[str, Any]:
    """Validate every role YAML on disk. Returns a JSON-serialisable report."""
    import yaml
    from pathlib import Path

    if roles_dir is None:
        from icdev._paths import get_data_path

        roles_dir = get_data_path("args") / "ace" / "roles"
    roles_dir = Path(roles_dir)

    violations: list[dict[str, Any]] = []
    unreadable: list[dict[str, str]] = []
    checked = 0
    for path in sorted(roles_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001 — report, never abort the sweep
            unreadable.append({"file": path.name, "error": str(exc)})
            continue
        checked += 1
        problems = validate_role_tools(
            data.get("role_id", path.stem),
            data.get("trust_tier"),
            data.get("agent_tools"),
            data.get("mode", "steps"),
        )
        if problems:
            violations.append({"file": path.name, "problems": problems})

    return {
        "roles_dir": str(roles_dir),
        "checked": checked,
        "violations": violations,
        "unreadable": unreadable,
        "ok": not violations,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Validate ACE role trust_tier vs declared agent_tools."
    )
    parser.add_argument("--check", action="store_true", help="Sweep every role YAML.")
    parser.add_argument("--roles-dir", default=None, help="Override the roles directory.")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args(argv)

    report = check_all_roles(args.roles_dir)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"roles checked: {report['checked']} ({report['roles_dir']})")
        for entry in report["unreadable"]:
            print(f"  UNREADABLE {entry['file']}: {entry['error']}")
        for entry in report["violations"]:
            for problem in entry["problems"]:
                print(f"  VIOLATION {entry['file']}: {problem}")
        print("OK" if report["ok"] else "FAILED")
    return 0 if report["ok"] else 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
