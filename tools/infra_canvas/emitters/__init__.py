# CUI // SP-CTI
"""IDC IaC emitters — convert IDC graph nodes to infrastructure-as-code."""

from tools.infra_canvas.emitters.terraform import UnsupportedResourceError, emit_resource

__all__ = ["emit_resource", "UnsupportedResourceError"]
