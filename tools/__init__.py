"""Backward-compatibility shim: tools.* -> icdev.tools.*

Both ``import tools`` and ``from icdev import tools`` (i.e. ``icdev.tools``)
are supported for backward compatibility. The canonical package is
``icdev.tools``; this shim re-exports it under the legacy ``tools`` namespace
so that the hundreds of existing scripts, child apps, and CLAUDE.md CLI
examples that reference ``tools.*`` continue to work without modification.

Why both namespaces exist:
  - ``icdev.tools`` — the installed, pip-distributable location (``icdev/tools/``)
  - ``tools``       — the repo-root flat layout used by all legacy imports and
                      the CLAUDE.md quick-reference commands

The ``_ToolsRedirect`` module below intercepts attribute access on the
``tools`` module and resolves it to ``icdev.tools.<name>``, falling back to
normal package resolution when the sub-module doesn't exist under ``icdev.tools``.

Use the canonical absolute import form:
    from icdev.tools.llm.router import LLMRouter  # preferred
"""

import importlib
import os
import sys
import types

# Deprecation warning intentionally suppressed — this shim keeps legacy
# ``tools.*`` imports working while the codebase migrates to ``icdev.tools.*``.


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
            return importlib.import_module(f"tools.{name}")


_redirect = _ToolsRedirect(__name__, __doc__)
sys.modules[__name__] = _redirect
