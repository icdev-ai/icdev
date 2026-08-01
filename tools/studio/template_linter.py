"""
tools/studio/template_linter.py
Detect and auto-fix DAG problems in workflow YAML templates.

Catches:
  - Isolated nodes  (no edges at all)
  - Disconnected subgraphs  (multiple connected components)
  - Dangling depends_on references  (depend on a step that doesn't exist)

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

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "args" / "workflow_templates"

# "mcp" (dwo-mcp-03): the step names a TOOL_REGISTRY tool in `mcp_tool`
# rather than a script path in `tool`.
VALID_NODE_TYPES: frozenset[str] = frozenset({"tool", "human", "approval", "mcp"})
VALID_AUDIENCES: frozenset[str] = frozenset({"leadership", "technical", "compliance", "board", "customer"})


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
    return {
        "isolated":       [s["id"] for s in steps if s["id"] not in has_in and s["id"] not in has_out],
        "roots":          [s["id"] for s in steps if s["id"] not in has_in and s["id"] in has_out],
        "leaves":         [s["id"] for s in steps if s["id"] in has_in and s["id"] not in has_out],
        "dangling":       [d for s in steps for d in _deps(s) if d not in ids],
        "bad_node_types": bad_node_types,
        "components":     len(comps),
        "comp_groups":    [sorted(c) for c in sorted(comps, key=len, reverse=True)],
        "has_in":         has_in,
        "has_out":        has_out,
    }


def is_ok(info: dict) -> bool:
    return (
        not info["isolated"]
        and not info["dangling"]
        and not info["bad_node_types"]
        and info["components"] <= 1
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
        if is_ok(info):
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
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_template(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
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
            "isolated": [], "dangling": [], "bad_node_types": [],
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
            "narrative_context_errors": nc_errors,
            "status": "ok" if ok else "fail",
        }
        if info["components"] > 1:
            entry["comp_groups"] = info["comp_groups"]

        if not ok:
            any_bad = True
            if not check_only:
                patched, changes = auto_fix(steps)
                data["steps"] = patched
                save_template(path, data)
                entry["auto_fixed"] = changes
                entry["status"] = "fixed"

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
