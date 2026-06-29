"""BPMN 2.0 XML export for Process-Ify workflows."""
from __future__ import annotations
import uuid
import xml.etree.ElementTree as ET


def workflow_to_bpmn(wf: dict) -> str:
    """Convert a processified workflow dict to BPMN 2.0 XML string."""
    steps = wf.get("steps", [])
    wf_name = wf.get("workflow_name") or wf.get("name") or "Workflow"

    def uid() -> str:
        return f"id_{uuid.uuid4().hex[:8]}"

    root = ET.Element("definitions", {
        "xmlns": "http://www.omg.org/spec/BPMN/20100524/MODEL",
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "targetNamespace": "http://icdev.ai/processify",
        "id": uid(),
    })

    collab = ET.SubElement(root, "collaboration", {"id": uid()})
    part_id = uid()
    ET.SubElement(collab, "participant", {
        "id": part_id,
        "name": wf_name,
        "processRef": f"proc_{part_id}",
    })

    proc = ET.SubElement(root, "process", {"id": f"proc_{part_id}", "isExecutable": "false"})

    # Collect unique roles (preserve order)
    roles: list[str] = []
    seen: set[str] = set()
    for s in steps:
        r = (s.get("assignee_role") or "Unassigned").strip() or "Unassigned"
        if r not in seen:
            roles.append(r)
            seen.add(r)
    if not roles:
        roles = ["Unassigned"]

    lane_set = ET.SubElement(proc, "laneSet", {"id": uid()})
    role_lane_map: dict[str, tuple[str, ET.Element]] = {}
    for r in roles:
        lid = uid()
        lane = ET.SubElement(lane_set, "lane", {"id": lid, "name": r})
        role_lane_map[r] = (lid, lane)

    # Start event — assign to first lane
    start_id = uid()
    ET.SubElement(proc, "startEvent", {"id": start_id, "name": "Start"})
    ET.SubElement(role_lane_map[roles[0]][1], "flowNodeRef").text = start_id

    # Service tasks — one per workflow step
    task_ids: list[str] = []
    for i, step in enumerate(steps):
        tid = uid()
        role = (step.get("assignee_role") or "Unassigned").strip() or "Unassigned"
        if role not in role_lane_map:
            role = "Unassigned"
        ET.SubElement(proc, "serviceTask", {
            "id": tid,
            "name": step.get("title") or f"Step {i + 1}",
        })
        task_ids.append(tid)
        ET.SubElement(role_lane_map[role][1], "flowNodeRef").text = tid

    # End event — assign to last lane
    end_id = uid()
    ET.SubElement(proc, "endEvent", {"id": end_id, "name": "End"})
    ET.SubElement(role_lane_map[roles[-1]][1], "flowNodeRef").text = end_id

    # Sequence flows connecting all nodes in order
    all_nodes = [start_id] + task_ids + [end_id]
    for i in range(len(all_nodes) - 1):
        ET.SubElement(proc, "sequenceFlow", {
            "id": uid(),
            "sourceRef": all_nodes[i],
            "targetRef": all_nodes[i + 1],
        })

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")
