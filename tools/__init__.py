"""Backward-compatibility shim: tools.* -> icdev.tools.*

The ICDEV tools package has moved to icdev.tools. This shim provides
backward compatibility for existing scripts and child applications.

Update your imports:
    from tools.llm.router import LLMRouter       # old
    from icdev.tools.llm.router import LLMRouter  # new
"""

import importlib
import sys
import warnings

warnings.warn(
    "Importing from 'tools' is deprecated. Use 'from icdev.tools' instead.",
    DeprecationWarning,
    stacklevel=2,
)


class _ToolsRedirect:
    """Module redirect: tools.xxx -> icdev.tools.xxx."""

    def __getattr__(self, name):
        return importlib.import_module(f"icdev.tools.{name}")


sys.modules[__name__] = _ToolsRedirect()
