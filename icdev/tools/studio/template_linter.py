"""
tools/studio/template_linter.py
Detect and auto-fix DAG problems in workflow YAML templates.

Catches:
  - Isolated nodes  (no edges at all)
  - Disconnected subgraphs  (multiple connected components)
  - Dangling depends_on references  (depend on a step that doesn't exist)
  - Malformed conditional edges  (a `when:` block the runner could never satisfy)

Usage:
  python tools/studio/template_linter.py --check           # report only
  python tools/studio/template_linter.py --fix             # rewrite YAMLs in-place
  python tools/studio/template_linter.py --check --json    # machine-readable
  python tools/studio/template_linter.py --check --gate    # exit 1 if any problems
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("pyyaml not installed -- pip install pyyaml", file=sys.stderr)
    sys.exit(1)

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.studio.automation_builder import CONDITION_OPERATORS  # noqa: E402

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "args" / "workflow_templates"

# ── Step schema ────────────────────────────────────────────
#
# "mcp" (dwo-mcp-03): the step names a TOOL_REGISTRY tool in `mcp_tool`
# rather than a script path in `tool`.
# "agent" (hgx-agent-01): the step names a task in `prompt` and bounds its tools
# with `agent_tools` (bundle names from args/agent_toolsets.yaml), and runs an
# agent loop instead of a script.
VALID_NODE_TYPES: frozenset[str] = frozenset(
    {"tool", "human", "approval", "mcp", "agent"}
)
VALID_AUDIENCES: frozenset[str] = frozenset({"leadership", "technical", "compliance", "board", "customer"})

# `when:` (hgx-cond-01) is an OPTIONAL conditional edge — a condition, or a list
# of conditions ANDed together, evaluated against the predecessor's recorded
# result. A step whose `when` does not hold records the `skipped` status with the
# unmet condition as its reason; its own dependents still evaluate theirs.
#
#   - id: remediate
#     depends_on: [scan]
#     when:
#       - field: status          # or steps.<step_id>.status, or output.<path>
#         operator: not_equals
#         value: success
#
# The operator vocabulary is NOT declared here: it is imported from
# `automation_builder.CONDITION_OPERATORS`, the one implementation the trigger
# filters and the builder UI already share, so the linter cannot drift from what
# the runner will actually evaluate.
VALID_CONDITION_OPERATORS: frozenset[str] = frozenset(op["id"] for op in CONDITION_OPERATORS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _deps(step: dict) -> list[str]:
    d = step.get("depends_on", [])
    if not d:
        return []
    return [d] if isinstance(d, str) else list(d)


# ---------------------------------------------------------------------------
# Union-Find for connected-components
# ---------------------------------------------------------------------------

def _components(steps: list[dict]) -> list[set[str]]:
    parent = {s["id"]: s["id"] for s in steps}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for s in steps:
        for d in _deps(s):
            if d in parent:
                union(s["id"], d)

    groups: dict[str, set[str]] = {}
    for s in steps:
        groups.setdefault(find(s["id"]), set()).add(s["id"])
    return list(groups.values())


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def validate_narrative_context(nc: dict) -> list[str]:
    """Return a list of error strings for a narrative_context block."""
    errors: list[str] = []
    audience = nc.get("audience")
    if audience is not None and audience not in VALID_AUDIENCES:
        errors.append(
            f"narrative_context.audience '{audience}' not in {sorted(VALID_AUDIENCES)}"
        )
    params = nc.get("parameters")
    if params is not None:
        for key, val in params.items():
            if not isinstance(val, (int, float)):
                errors.append(
                    f"narrative_context.parameters.{key} must be numeric, got {type(val).__name__!r}"
                )
    return errors


def validate_when(step: dict) -> list[str]:
    """Return error strings for a step's `when:` block. No block -> no errors."""
    raw = step.get("when")
    if raw is None:
        return []

    conditions = [raw] if isinstance(raw, dict) else raw
    if not isinstance(conditions, list):
        return [f"when must be a condition mapping or a list of them, got {type(raw).__name__!r}"]

    errors: list[str] = []
    for i, cond in enumerate(conditions):
        if not isinstance(cond, dict):
            errors.append(f"when[{i}] must be a mapping, got {type(cond).__name__!r}")
            continue
        if not str(cond.get("field", "") or "").strip():
            errors.append(f"when[{i}] has no 'field'")
        operator = cond.get("operator", "equals")
        if operator not in VALID_CONDITION_OPERATORS:
            errors.append(
                f"when[{i}].operator {operator!r} not in {sorted(VALID_CONDITION_OPERATORS)}"
            )
    if conditions and not _deps(step):
        # Conditions read the PREDECESSOR's result, and a step with no
        # depends_on is dispatched in the first wave — there is no predecessor
        # for it to read, so every field would resolve empty.
        errors.append("when on a step with no depends_on has no predecessor result to read")
    return errors


def validate_agent(step: dict) -> list[str]:
    """Return error strings for a `node_type: agent` step (hgx-agent-01).

    Both keys are load-bearing and both fail quietly at run time if omitted: a
    step with no `prompt` is skipped by the runner, and one with no
    `agent_tools` is refused by the executor's default-deny allowlist. Neither
    is worth discovering mid-run, so they are linted here.
    """
    if step.get("node_type") != "agent":
        return []

    errors: list[str] = []
    if not str(step.get("prompt", "") or "").strip():
        errors.append("node_type: agent step has no 'prompt' — it will be skipped")

    tools = step.get("agent_tools")
    if tools is not None and not isinstance(tools, (str, list)):
        errors.append(
            f"agent_tools must be a list of bundle names, got {type(tools).__name__!r}"
        )
    else:
        names = tools.split(",") if isinstance(tools, str) else list(tools or [])
        if not [n for n in names if str(n).strip()]:
            errors.append(
                "node_type: agent step has no 'agent_tools' — the allowlist is "
                "default-deny, so the step is refused rather than handed every tool"
            )
    return errors


def analyze(steps: list[dict]) -> dict:
    ids = {s["id"] for s in steps}
    has_in: set[str] = set()
    has_out: set[str] = set()
    for s in steps:
        for d in _deps(s):
            has_in.add(s["id"])
            has_out.add(d)
    comps = _components(steps)
    bad_node_types = [
        (s["id"], s["node_type"])
        for s in steps
        if s.get("node_type") is not None and s["node_type"] not in VALID_NODE_TYPES
    ]
    bad_when = [
        (s["id"], err) for s in steps for err in validate_when(s)
    ]
    bad_agent = [
        (s["id"], err) for s in steps for err in validate_agent(s)
    ]
    return {
        "isolated":       [s["id"] for s in steps if s["id"] not in has_in and s["id"] not in has_out],
        "roots":          [s["id"] for s in steps if s["id"] not in has_in and s["id"] in has_out],
        "leaves":         [s["id"] for s in steps if s["id"] in has_in and s["id"] not in has_out],
        "dangling":       [d for s in steps for d in _deps(s) if d not in ids],
        "bad_node_types": bad_node_types,
        "bad_when":       bad_when,
        "bad_agent":      bad_agent,
        "components":     len(comps),
        "comp_groups":    [sorted(c) for c in sorted(comps, key=len, reverse=True)],
        "has_in":         has_in,
        "has_out":        has_out,
    }


def _connectivity_ok(info: dict) -> bool:
    """True when the GRAPH is clean — the subset `auto_fix` can actually repair.

    A malformed `when:` — or an agent step missing its `prompt` / `agent_tools`
    — is an authoring error in a step, not a missing edge, so both are excluded
    here: including them would make `auto_fix` spin its full iteration budget
    adding no edges, unable to reach `is_ok`.
    """
    return (
        not info["isolated"]
        and not info["dangling"]
        and not info["bad_node_types"]
        and info["components"] <= 1
    )


def is_ok(info: dict) -> bool:
    return (
        _connectivity_ok(info)
        and not info.get("bad_when")
        and not info.get("bad_agent")
    )


# ---------------------------------------------------------------------------
# Auto-fix
# ---------------------------------------------------------------------------

def auto_fix(steps: list[dict]) -> tuple[list[dict], list[str]]:
    """
    Iteratively fix until fully connected:
      1. Isolated nodes  -> wire into largest component's hub
      2. Disconnected subgraphs  -> bridge smaller component into largest
    Heuristic uses YAML declaration order as a tiebreaker for direction.
    """
    steps = copy.deepcopy(steps)
    changes: list[str] = []
    idx = {s["id"]: i for i, s in enumerate(steps)}

    def by_id(sid: str) -> dict:
        return next(s for s in steps if s["id"] == sid)

    def add_dep(target: str, dep: str, reason: str) -> None:
        t = by_id(target)
        current = _deps(t)
        if dep not in current:
            current.append(dep)
            t["depends_on"] = current
            changes.append(f"  {target}.depends_on += [{dep}]  ({reason})")

    def incoming_counts() -> dict[str, int]:
        cnt: dict[str, int] = {}
        for s in steps:
            for d in _deps(s):
                cnt[s["id"]] = cnt.get(s["id"], 0) + 1
        return cnt

    for _iteration in range(len(steps) + 1):
        info = analyze(steps)
        if _connectivity_ok(info):
            # NOT `is_ok`: a malformed `when:` is an authoring error inside a
            # step, and no edge this loop can add will clear it. Gating on the
            # full check would spin the whole iteration budget wiring bogus
            # dependencies onto an already-connected graph.
            break

        # -- Fix isolated nodes ----------------------------------------------
        for iso_id in list(info["isolated"]):
            non_iso = {s["id"] for s in steps} - set(info["isolated"])
            if not non_iso:
                # All isolated: wire sequentially by YAML order
                sids = [s["id"] for s in sorted(steps, key=lambda s: idx[s["id"]])]
                for i in range(1, len(sids)):
                    add_dep(sids[i], sids[i - 1], "sequential fallback")
                break
            cnt = incoming_counts()
            hub = max(non_iso, key=lambda sid: cnt.get(sid, 0))
            if idx[iso_id] < idx[hub]:
                add_dep(hub, iso_id, "isolated feeds hub")
            else:
                add_dep(iso_id, hub, "hub feeds isolated")

        # -- Fix disconnected subgraphs --------------------------------------
        info = analyze(steps)
        if info["components"] <= 1:
            continue

        comps = _components(steps)
        main = max(comps, key=len)
        cnt = incoming_counts()

        for comp in comps:
            if comp == main:
                continue
            # Pick bridge source: leaf of smaller comp earliest in YAML
            comp_leaves = [sid for sid in comp
                           if sid in info["has_in"] and sid not in info["has_out"]]
            src = min(comp_leaves or sorted(comp), key=lambda sid: idx[sid])
            # Pick bridge target: root of main comp earliest in YAML
            main_roots = [sid for sid in main if sid not in info["has_in"]]
            dst = min(main_roots or sorted(main), key=lambda sid: idx[sid])

            if idx[src] < idx[dst]:
                add_dep(dst, src, "bridge disconnected subgraph")
            else:
                add_dep(src, dst, "bridge disconnected subgraph")

    return steps, changes


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def load_template(path: Path) -> dict:
    # newline="" on both halves: without it a template rewritten by --fix on
    # Windows gains CRLF line endings the checkout does not use, so a lint pass
    # would show up as a whole-file diff.
    with open(path, encoding="utf-8", newline="") as f:
        return yaml.safe_load(f)


def save_template(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("# CUI // SP-CTI\n")
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run(check_only: bool, as_json: bool, gate: bool) -> int:
    results = []
    any_bad = False

    for path in sorted(TEMPLATES_DIR.glob("*.yaml")):
        try:
            data = load_template(path)
        except Exception as e:
            results.append({"file": path.name, "error": str(e)})
            continue

        steps = data.get("steps", [])
        nc_errors: list[str] = []
        nc = data.get("narrative_context")
        if nc is not None:
            nc_errors = validate_narrative_context(nc)

        if not steps and not nc_errors:
            continue

        info = analyze(steps) if steps else {
            "isolated": [], "dangling": [], "bad_node_types": [], "bad_when": [],
            "bad_agent": [],
            "components": 0, "comp_groups": [], "has_in": set(), "has_out": set(),
        }
        ok = is_ok(info) and not nc_errors

        entry: dict = {
            "file": path.name,
            "id": path.stem,
            "steps": len(steps),
            "components": info["components"],
            "isolated": info["isolated"],
            "dangling": info["dangling"],
            "bad_node_types": info["bad_node_types"],
            "bad_when": info["bad_when"],
            "bad_agent": info["bad_agent"],
            "narrative_context_errors": nc_errors,
            "status": "ok" if ok else "fail",
        }
        if info["components"] > 1:
            entry["comp_groups"] = info["comp_groups"]

        if not ok:
            any_bad = True
            if not check_only:
                patched, changes = auto_fix(steps)
                if changes:
                    # Guarded: with nothing to add, rewriting would still push
                    # the whole file back through yaml.dump and reformat a
                    # template this pass did not repair.
                    data["steps"] = patched
                    save_template(path, data)
                entry["auto_fixed"] = changes
                # `auto_fix` only ever adds EDGES. A malformed `when:`, an agent
                # step missing its keys, and a narrative_context error all
                # survive it untouched, so reporting "fixed" here would claim a
                # repair that did not happen and let the next --check be the
                # first thing to notice.
                repatched = analyze(patched)
                entry["status"] = (
                    "fail"
                    if (repatched["bad_when"] or repatched["bad_agent"] or nc_errors)
                    else "fixed"
                )

        results.append(entry)

    if as_json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            tag = {"ok": "OK   ", "fail": "FAIL ", "fixed": "FIXED"}.get(r.get("status", ""), "?    ")
            line = f"  [{tag}] {r['file']}"
            if r.get("isolated"):
                line += f"  isolated={r['isolated']}"
            if r.get("dangling"):
                line += f"  dangling={r['dangling']}"
            if r.get("bad_node_types"):
                line += f"  bad_node_types={r['bad_node_types']}"
            if r.get("bad_when"):
                line += f"  bad_when={r['bad_when']}"
            if r.get("bad_agent"):
                line += f"  bad_agent={r['bad_agent']}"
            if r.get("narrative_context_errors"):
                line += f"  narrative_context_errors={r['narrative_context_errors']}"
            if r.get("components", 1) > 1:
                line += f"  subgraphs={r['components']}"
            print(line)
            for c in r.get("auto_fixed", []):
                print(c)

        n_ok = sum(1 for r in results if r.get("status") == "ok")
        n_fx = sum(1 for r in results if r.get("status") == "fixed")
        n_fail = sum(1 for r in results if r.get("status") == "fail")
        print(f"\n  {n_ok} ok | {n_fx} fixed | {n_fail} still failing")

    if gate and any_bad and check_only:
        return 1
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Workflow template DAG linter/fixer")
    ap.add_argument("--check", action="store_true", help="Report only, no file writes")
    ap.add_argument("--fix",   action="store_true", help="Auto-fix problems in-place")
    ap.add_argument("--json",  action="store_true", help="JSON output")
    ap.add_argument("--gate",  action="store_true", help="Exit 1 if problems found (CI use)")
    args = ap.parse_args()
    sys.exit(run(check_only=not args.fix, as_json=args.json, gate=args.gate))


if __name__ == "__main__":
    main()
