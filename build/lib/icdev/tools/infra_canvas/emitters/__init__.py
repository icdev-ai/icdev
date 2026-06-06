# CUI // SP-CTI
"""IDC IaC emitters — convert IDC graph nodes to infrastructure-as-code."""

from tools.infra_canvas.emitters.terraform import UnsupportedResourceError, emit_resource
from tools.infra_canvas.emitters.ansible import emit_task, emit_playbook

__all__ = ["emit_resource", "emit_task", "emit_playbook", "UnsupportedResourceError"]
