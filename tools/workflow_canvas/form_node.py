# CUI // SP-CTI
"""Workflow Forms Canvas — form_intake node type.

Registers a 'form_intake' tool in the workflow tool catalog so the visual
workflow editor can embed a Form Intake checkpoint into a DAG pipeline.
At execution time the node records a wfc_workflow_form_nodes row and emits
a HITL checkpoint that blocks pipeline progress until the linked form is
submitted.
"""
from __future__ import annotations

FORM_INTAKE_TOOL: dict = {
    "id": "form_intake",
    "name": "Form Intake",
    "category": "forms",
    "description": "Pause the workflow and collect structured input via a linked form before continuing.",
    "icon": "clipboard",
    "config_schema": {
        "form_id": {"type": "string", "required": True, "description": "ID of the form to present"},
        "required_before_next": {"type": "boolean", "default": True, "description": "Block pipeline progress until submitted"},
        "node_label": {"type": "string", "description": "Display label for this intake step"},
    },
}
