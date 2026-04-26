"""Backward-compatibility shim: tools.* -> icdev.tools.*

The ICDEV™ tools package has moved to icdev.tools. This shim provides
backward compatibility for existing scripts and child applications.

Use the canonical absolute import form:
    from icdev.tools.llm.router import LLMRouter
"""

__all__ = []

import importlib
import os
import sys
import types
import warnings

_tools_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_tools_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
# Policy: legacy 'tools.*' imports are temporarily supported via this shim while
# callers migrate to 'icdev.tools.*'. The warning below is intentionally active.
# Once all internal imports are updated, remove this shim entirely.
warnings.warn(
    "Importing from 'tools' is deprecated. Use 'from icdev.tools' instead.",
    DeprecationWarning,
    stacklevel=2,
)


class _ToolsRedirect(types.ModuleType):
    """Module redirect: tools.xxx -> icdev.tools.xxx.

    Preserves __path__ so sub-package imports (tools.dashboard.config) work.
    Falls back to normal package resolution when icdev.tools.xxx doesn't exist.
    """

    def __init__(self, name, doc):
        super().__init__(name)
        self.__doc__ = doc
        self.__path__ = [os.path.join(os.path.dirname(__file__))]
        self.__package__ = name
        self.__file__ = __file__

    def __getattr__(self, name):
        try:
            return importlib.import_module(f"icdev.tools.{name}")
        except ModuleNotFoundError:
            # Fall back to normal sub-module resolution
            return importlib.import_module(f".{name}", package=__name__)


_redirect = _ToolsRedirect(__name__, __doc__)
sys.modules[__name__] = _redirect
