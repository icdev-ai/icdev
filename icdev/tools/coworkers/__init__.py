# CUI // SP-CTI
"""Backward-compat shim: tools.coworkers -> icdev.tools.coworkers."""
from icdev.tools.coworkers import CoWorker, list_coworkers, get_coworker, build_chat_link, build_persona_seed  # noqa: F401

__all__ = ["CoWorker", "list_coworkers", "get_coworker", "build_chat_link", "build_persona_seed"]
