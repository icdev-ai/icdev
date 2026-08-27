"""Backward-compatibility shim: tools.* -> icdev.tools.*

The ICDEV™ tools package has moved to icdev.tools. This shim provides
backward compatibility for existing scripts and child applications.

Use the canonical absolute import form:
    from icdev.tools.llm.router import LLMRouter  # preferred
"""

__all__ = ["LLMRouter"]

import importlib
import os
import sys
import types

_tools_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_tools_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# xit-decl-02: in a source checkout, make ``icdev.tools.X`` the SAME module
# object as ``tools.X``. Without this a submodule import STATEMENT loaded the
# physical tools/ file while attribute access (below) returned the icdev/tools/
# copy — two objects, two singletons, a monkeypatch on the wrong one. The finder
# refuses to install anywhere tools/ and icdev/ are not siblings (the wheel, a
# scaffolded project), so nothing changes there. See icdev/core/shim.py.
from icdev import _shim as _core_shim  # noqa: E402

_core_shim.install(__file__)

from icdev.tools.llm.router import LLMRouter  # noqa: E402
_ICDEV_TOOLS_BASE = "icdev.tools"  # namespace root for dynamic importlib calls


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
        canonical = f"{_ICDEV_TOOLS_BASE}.{name}"
        try:
            return importlib.import_module(canonical)
        except ModuleNotFoundError as exc:
            # Only fall through when the target module itself is absent. A
            # missing *dependency* inside it must keep its own message rather
            # than being relabelled "tools.<name> does not exist".
            if exc.name != canonical:
                raise
        local = f"{__name__}.{name}"
        try:
            return importlib.import_module(f".{name}", package=__name__)
        except ModuleNotFoundError as exc:
            if exc.name != local:
                raise
            # PEP 562: an absent module attribute is an AttributeError, not an
            # ImportError. Raising ModuleNotFoundError here breaks every
            # hasattr(tools, ...) probe — including pytest's Package collector,
            # which asks for `setUpModule` and cannot collect any test under
            # tools/ if the question raises. `from tools import x` still fails
            # with ImportError, because Python converts AttributeError for it.
            raise AttributeError(
                f"module {__name__!r} has no attribute {name!r}"
            ) from exc


_redirect = _ToolsRedirect(__name__, __doc__)
sys.modules[__name__] = _redirect
