"""ICDEV™ Studio — Workflow Narrative Engine (WNE) Context Builder.

Parses a workflow YAML template, performs topological sort (Kahn's algorithm),
groups sorted steps into phases, and emits a WorkflowContext for downstream
narrative generation.  No LLM.  No DB.  Air-gap safe.

CLI:
    python tools/studio/wne/context_builder.py --build <yaml_path> --json
"""

from __future__ import annotations

import argparse
import json
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Union

import yaml


# ── Dataclasses ────────────────────────────────────────────────────────────────


@dataclass
class Phase:
    name: str
    nodes: list[str]
    phase_type: str  # tool | human | approval | mixed


@dataclass
class DecisionPoint:
    node_id: str
    name: str
    role: str
    doc_template: str


@dataclass
class ApprovalGate:
    node_id: str
    name: str
    policy: str
    role: str


@dataclass
class WorkflowContext:
    template_name: str
    audience: str
    org_name: str
    program_name: str
    classification: str
    purpose: str
    timeframe_months: int
    parameters: dict
    phases: list[Phase]
    decision_points: list[DecisionPoint]
    approval_gates: list[ApprovalGate]


# ── Builder ────────────────────────────────────────────────────────────────────


class WorkflowContextBuilder:
    """Build a WorkflowContext from a workflow YAML template."""

    def build(self, yaml_path_or_dict: Union[str, Path, dict]) -> WorkflowContext:
        data = self._load(yaml_path_or_dict)
        template_name = self._template_name(yaml_path_or_dict, data)
        nc = data.get("narrative_context") or {}
        steps = data.get("steps") or []
        step_map: dict[str, Any] = {s["id"]: s for s in steps if "id" in s}

        topo_order = self._kahn_sort(step_map)
        phase_of, phase_lists = self._assign_phases(topo_order, step_map)
        phases = self._build_phases(phase_lists, step_map)
        decision_points = self._collect_decision_points(topo_order, step_map)
        approval_gates = self._collect_approval_gates(topo_order, step_map)

        return WorkflowContext(
            template_name=template_name,
            audience=nc.get("audience", ""),
            org_name=nc.get("org_name", ""),
            program_name=nc.get("program_name", ""),
            classification=nc.get("classification", "CUI"),
            purpose=nc.get("purpose", ""),
            timeframe_months=int(nc.get("timeframe_months") or 0),
            parameters=dict(nc.get("parameters") or {}),
            phases=phases,
            decision_points=decision_points,
            approval_gates=approval_gates,
        )

    # ── Loaders ────────────────────────────────────────────────────────────────

    @staticmethod
    def _load(src: Union[str, Path, dict]) -> dict:
        if isinstance(src, dict):
            return src
        with open(src, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    @staticmethod
    def _template_name(src: Union[str, Path, dict], data: dict) -> str:
        if isinstance(src, dict):
            return data.get("description", "unnamed")
        return Path(src).stem

    # ── Dependency helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _normalize_deps(raw: Any) -> list[str]:
        if not raw:
            return []
        if isinstance(raw, str):
            return [raw]
        return list(raw)

    @staticmethod
    def _node_type(step: dict) -> str:
        return (step.get("node_type") or "tool").strip()

    # ── Kahn topological sort ──────────────────────────────────────────────────

    def _kahn_sort(self, step_map: dict[str, Any]) -> list[str]:
        in_degree: dict[str, int] = {sid: 0 for sid in step_map}
        adj: dict[str, list[str]] = {sid: [] for sid in step_map}

        for sid, step in step_map.items():
            for dep in self._normalize_deps(step.get("depends_on")):
                if dep in step_map:
                    adj[dep].append(sid)
                    in_degree[sid] += 1

        queue: deque[str] = deque(sid for sid, deg in in_degree.items() if deg == 0)
        order: list[str] = []

        while queue:
            node = queue.popleft()
            order.append(node)
            for succ in adj[node]:
                in_degree[succ] -= 1
                if in_degree[succ] == 0:
                    queue.append(succ)

        # Degrade gracefully: append any unreached nodes (cycle or orphan)
        seen = set(order)
        for sid in step_map:
            if sid not in seen:
                order.append(sid)

        return order

    # ── Phase assignment ───────────────────────────────────────────────────────

    def _assign_phases(
        self, topo_order: list[str], step_map: dict[str, Any]
    ) -> tuple[dict[str, int], dict[int, list[str]]]:
        """Assign each node a phase number.

        Human and approval nodes are always isolated in their own phase by
        assigning them a fractional offset (+0.5) relative to their natural
        depth, ensuring they never share a phase with tool nodes.  Fractional
        phase values are renumbered to consecutive integers afterward.
        """
        natural: dict[str, float] = {}
        for node_id in topo_order:
            step = step_map[node_id]
            known_deps = [
                d for d in self._normalize_deps(step.get("depends_on"))
                if d in natural
            ]
            if not known_deps:
                base = 0.0
            else:
                base = max(natural[d] for d in known_deps) + 1.0

            nt = self._node_type(step)
            natural[node_id] = base + 0.5 if nt in ("human", "approval") else base

        # Renumber fractional values to consecutive integers (preserving order)
        sorted_values = sorted(set(natural.values()))
        remap = {v: i for i, v in enumerate(sorted_values)}

        phase_of: dict[str, int] = {nid: remap[natural[nid]] for nid in topo_order}

        phase_lists: dict[int, list[str]] = {}
        for nid in topo_order:
            phase_lists.setdefault(phase_of[nid], []).append(nid)

        return phase_of, phase_lists

    # ── Phase list → Phase dataclass ───────────────────────────────────────────

    def _build_phases(
        self, phase_lists: dict[int, list[str]], step_map: dict[str, Any]
    ) -> list[Phase]:
        phases: list[Phase] = []
        for ph_num in sorted(phase_lists):
            nodes = phase_lists[ph_num]
            types = {self._node_type(step_map[n]) for n in nodes}

            if "human" in types and "approval" in types:
                phase_type = "mixed"
            elif "human" in types:
                phase_type = "human"
            elif "approval" in types:
                phase_type = "approval"
            else:
                phase_type = "tool"

            if len(nodes) == 1:
                name = step_map[nodes[0]].get("name", nodes[0])
            else:
                name = f"Phase {ph_num + 1}"

            phases.append(Phase(name=name, nodes=nodes, phase_type=phase_type))

        return phases

    # ── Gate collectors ────────────────────────────────────────────────────────

    def _collect_decision_points(
        self, topo_order: list[str], step_map: dict[str, Any]
    ) -> list[DecisionPoint]:
        out: list[DecisionPoint] = []
        for node_id in topo_order:
            step = step_map[node_id]
            if self._node_type(step) == "human":
                out.append(
                    DecisionPoint(
                        node_id=node_id,
                        name=step.get("name", node_id),
                        role=step.get("role", ""),
                        doc_template=step.get("doc_template", ""),
                    )
                )
        return out

    def _collect_approval_gates(
        self, topo_order: list[str], step_map: dict[str, Any]
    ) -> list[ApprovalGate]:
        out: list[ApprovalGate] = []
        for node_id in topo_order:
            step = step_map[node_id]
            if self._node_type(step) == "approval":
                out.append(
                    ApprovalGate(
                        node_id=node_id,
                        name=step.get("name", node_id),
                        policy=step.get("approval_policy", ""),
                        role=step.get("role", ""),
                    )
                )
        return out


# ── CLI ────────────────────────────────────────────────────────────────────────


def _to_serializable(obj: Any) -> Any:
    if isinstance(obj, (Phase, DecisionPoint, ApprovalGate, WorkflowContext)):
        return asdict(obj)
    raise TypeError(f"Not serializable: {type(obj)}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Build WorkflowContext from a workflow YAML template."
    )
    parser.add_argument("--build", metavar="YAML_PATH", required=True)
    parser.add_argument("--json", dest="as_json", action="store_true")
    args = parser.parse_args(argv)

    ctx = WorkflowContextBuilder().build(args.build)

    if args.as_json:
        print(json.dumps(asdict(ctx), indent=2, default=str))
    else:
        print(ctx)


if __name__ == "__main__":
    main()
