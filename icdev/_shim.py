# CUI // SP-CTI
"""One module object under both names: ``tools.X`` and ``icdev.tools.X``.

THE DEFECT
----------
In a source checkout ``tools/`` and ``icdev/tools/`` are two copies of the
same files, and ``tools/__init__.py`` only redirects ATTRIBUTE access
(``from tools import db``) onto ``icdev.tools``. A submodule import STATEMENT
(``import tools.db.storage``, ``from tools.kanban.cli import main``) goes
through ``tools.__path__`` and loads the physical file under ``tools/`` as a
SECOND module object::

    import tools.genesis.reflexes.kanban        -> tools/...
    import icdev.tools.genesis.reflexes.kanban  -> icdev/tools/...
    a is b  ->  False                          (args/mirror_parity_gate.yaml)

Two objects means two singletons, two registries, and a monkeypatch that lands
on whichever copy the code under test did NOT import — 311 test files patch
``"tools.…"`` strings, 24 patch ``"icdev.tools.…"``. It is also why PR #1542's
fix was only half live for three and a half hours.

THE FIX, AND WHICH WAY IT POINTS
--------------------------------
A meta-path finder, installed by ``tools/__init__.py`` in a source checkout,
that answers ``icdev.tools.<rest>`` with the module object ALREADY bound to
``tools.<rest>`` — imported through the normal path machinery from the
physical ``tools/`` file — registered under both names.

The direction is deliberate and is NOT the one the words "canonical namespace
``icdev.tools``" suggest. 2,054 modules compute a data/args/database path from
``Path(__file__)``; had ``tools.X`` been aliased onto the ``icdev/tools/`` copy
instead, every one of them would have re-rooted onto ``<repo>/icdev/args/``
(stale packaged copies) and the checked-in ``icdev/data/icdev.db`` — silently.
Aliasing towards the physical ``tools/`` tree is the direction that changes
nothing for the 3,060 files that import ``from tools.`` today and fixes the
identity of the rest. Which tree is physical can be revisited once the
self-root census (xit-decl-03) reaches zero for a package; this module does
not care.

THREE CASES ARE NOT ALIASED:

* the name is not under ``icdev.tools.`` (or IS ``icdev.tools`` itself) —
  the finder returns None and the import system proceeds as before;
* no ``tools.<rest>`` module exists — 66 files exist only in the mirror;
* ``tools/<rest>.py`` is a BACK-COMPAT SHIM onto its own mirror twin (a
  5-line ``tools/billing/tier.py`` over a 34-line implementation; ``llm/
  agent_loop.py`` 98 over 2,775). Five exist. For these the REAL module is
  the mirror copy, and BOTH names resolve to it -- ``tools.llm.agent_loop``
  included -- so the shim file is never executed as a module of its own.
  Letting it load separately left the aliased parent package carrying
  whichever child was imported last as its attribute while sys.modules held
  both, and a monkeypatch that walks attributes landed on the wrong one.

For the last two the finder resolves the ``icdev/tools/`` file EXPLICITLY
rather than returning None: once a parent package is aliased its ``__path__``
is ``tools/<pkg>``, so the default path search for a child would look in the
wrong tree and load the very shim being avoided.

NEVER ACTIVE in the wheel (there is no ``tools/`` package, and
``icdev/__init__.py`` already aliases ``tools`` -> ``icdev.tools``), and never
active in a project that has its OWN ``tools/`` package beside a pip-installed
``icdev``: ``install()`` refuses unless ``tools/`` and ``icdev/`` are siblings.

``python -m icdev.tools.x`` keeps working: the alias loader implements
``get_code`` by delegating to the physical module's loader, which is what
``runpy`` asks for.
"""
from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

CANONICAL_PREFIX = "icdev.tools"
PHYSICAL_PREFIX = "tools"

_SELF_SHIM_RE = re.compile(r"^\s*from\s+icdev\.tools\.([\w.]+)\s+import\b", re.M)
#: A tools/ file this small, importing its own twin, is a shim and not a module.
_SHIM_MAX_LINES = 200
_SHIM_MAX_RATIO = 0.5


def is_backcompat_shim(tools_file: Path, dotted: str, mirror_file: Path | None = None) -> bool:
    """True when ``tools_file`` merely re-exports ``icdev.tools.<dotted>``.

    Mirrors the predicate ``tools/installer/sync_package_tree.py::
    _is_backcompat_shim`` uses to refuse overwriting a real implementation
    with its shim — same shape, same reason, kept here so ``icdev.core``
    imports nothing from ``tools``.
    """
    try:
        text = tools_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if not any(m.group(1) == dotted for m in _SELF_SHIM_RE.finditer(text)):
        return False
    n_lines = text.count("\n") + 1
    if n_lines > _SHIM_MAX_LINES:
        return False
    if mirror_file is not None and mirror_file.exists():
        try:
            twin_lines = mirror_file.read_text(encoding="utf-8", errors="replace").count("\n") + 1
        except OSError:
            return True
        return n_lines <= twin_lines * _SHIM_MAX_RATIO
    return True


class _AliasLoader(importlib.abc.Loader):
    """Hand back an existing module object instead of executing anything."""

    def __init__(self, module: ModuleType, target_name: str):
        self._module = module
        self._target_name = target_name

    def create_module(self, spec):  # noqa: ARG002 - signature fixed by importlib
        return self._module

    def exec_module(self, module):  # noqa: ARG002
        return None

    # runpy (`python -m icdev.tools.x`) asks the loader for the code object.
    def get_code(self, fullname):  # noqa: ARG002
        spec = getattr(self._module, "__spec__", None)
        loader = getattr(spec, "loader", None)
        if loader is not None and hasattr(loader, "get_code"):
            return loader.get_code(self._target_name)
        return None

    def get_source(self, fullname):  # noqa: ARG002
        spec = getattr(self._module, "__spec__", None)
        loader = getattr(spec, "loader", None)
        if loader is not None and hasattr(loader, "get_source"):
            return loader.get_source(self._target_name)
        return None

    def is_package(self, fullname):  # noqa: ARG002
        return hasattr(self._module, "__path__")


class IcdevToolsAliasFinder(importlib.abc.MetaPathFinder):
    """``icdev.tools.<rest>`` -> the module already importable as ``tools.<rest>``."""

    def __init__(self, tools_dir: Path, mirror_dir: Path):
        self.tools_dir = tools_dir
        self.mirror_dir = mirror_dir
        self._shim_cache: dict[str, bool] = {}
        self.aliased: set[str] = set()
        self.fell_through: set[str] = set()

    # -- helpers ------------------------------------------------------------
    def _physical_files(self, rest: str) -> tuple[Path, Path]:
        parts = rest.split(".")
        module_file = self.tools_dir.joinpath(*parts).with_suffix(".py")
        package_dir = self.tools_dir.joinpath(*parts)
        return module_file, package_dir

    def _is_shim(self, rest: str) -> bool:
        cached = self._shim_cache.get(rest)
        if cached is not None:
            return cached
        module_file, _ = self._physical_files(rest)
        result = False
        if module_file.is_file():
            twin = self.mirror_dir.joinpath(*rest.split(".")).with_suffix(".py")
            result = is_backcompat_shim(module_file, rest, twin)
        self._shim_cache[rest] = result
        return result

    def _mirror_spec(self, fullname: str, rest: str):
        """A real file spec for ``icdev/tools/<rest>`` — searched EXPLICITLY.

        Returning None here would hand the lookup to the normal path finder,
        which searches the PARENT package's ``__path__`` — and when that parent
        is aliased, its ``__path__`` is ``tools/<pkg>``, so the fall-through
        child would resolve against the wrong tree and load the very shim we
        are falling through to avoid (observed: ``icdev.tools.llm.agent_loop``
        executing ``tools/llm/agent_loop.py`` and importing itself).
        """
        parts = rest.split(".")
        search = [str(self.mirror_dir.joinpath(*parts[:-1]))]
        return importlib.machinery.PathFinder.find_spec(fullname, search)

    def _alias_spec(self, fullname: str, module: ModuleType, target_name: str):
        spec = importlib.machinery.ModuleSpec(
            fullname,
            _AliasLoader(module, target_name),
            origin=getattr(module, "__file__", None),
            is_package=hasattr(module, "__path__"),
        )
        if hasattr(module, "__path__"):
            spec.submodule_search_locations = list(module.__path__)
        spec.has_location = bool(spec.origin)
        self.aliased.add(fullname)
        return spec

    # -- MetaPathFinder ----------------------------------------------------
    def find_spec(self, fullname, path=None, target=None):  # noqa: ARG002
        if fullname.startswith(PHYSICAL_PREFIX + "."):
            # The SHIM case, from the other side: ``tools/<rest>.py`` is a tiny
            # re-export of ``icdev.tools.<rest>``. If the shim file were allowed
            # to load as its own module, the aliased parent package would carry
            # whichever child was imported LAST as its attribute while
            # sys.modules held both -- and a monkeypatch that walks attributes
            # (pytest's string form) would land on a different object from the
            # one ``from icdev.tools.x import f`` resolves. Observed:
            # tests/studio/test_agent_tool_gate.py importing the real module at
            # collection made tests/agent_runtime/test_event_recorder.py's
            # patches invisible. So the shim name resolves to the REAL module
            # too: one object under both names, for every module.
            rest = fullname[len(PHYSICAL_PREFIX) + 1:]
            if rest and self._is_shim(rest):
                canonical = f"{CANONICAL_PREFIX}.{rest}"
                try:
                    module = importlib.import_module(canonical)
                except ModuleNotFoundError as exc:
                    if exc.name and (exc.name == canonical or canonical.startswith(exc.name + ".")):
                        return None
                    raise
                return self._alias_spec(fullname, module, canonical)
            return None
        if not fullname.startswith(CANONICAL_PREFIX + "."):
            return None
        rest = fullname[len(CANONICAL_PREFIX) + 1:]
        if not rest:
            return None
        module_file, package_dir = self._physical_files(rest)
        # A directory without __init__.py is a NAMESPACE package (tools/config
        # is one) and is just as physical as a regular one.
        if not (module_file.is_file() or package_dir.is_dir()):
            return self._mirror_spec(fullname, rest)  # exists only in the mirror
        if self._is_shim(rest):
            self.fell_through.add(fullname)
            return self._mirror_spec(fullname, rest)  # the real module IS the mirror copy

        physical_name = f"{PHYSICAL_PREFIX}.{rest}"
        try:
            module = importlib.import_module(physical_name)
        except ModuleNotFoundError as exc:
            if exc.name and (exc.name == physical_name or physical_name.startswith(exc.name + ".")):
                return None
            raise
        return self._alias_spec(fullname, module, physical_name)


def _siblings(tools_dir: Path, icdev_dir: Path) -> bool:
    try:
        return tools_dir.resolve().parent == icdev_dir.resolve().parent
    except OSError:
        return False


def install(tools_init_file: str | Path) -> IcdevToolsAliasFinder | None:
    """Install the finder for the ``tools`` package whose ``__init__`` is given.

    Returns the finder, or None when this is not a source checkout (no sibling
    ``icdev/tools``) — in which case nothing changes.
    """
    tools_dir = Path(tools_init_file).resolve().parent
    # This module sits DIRECTLY in ``icdev/`` (it was ``icdev/core/shim.py`` until
    # xcore-cut-02, where ``.parent.parent`` was right). Getting the depth wrong here does not
    # raise -- ``_siblings`` simply returns False, ``install`` returns None, and the alias
    # finder is never installed, so ``icdev.tools.X`` and ``tools.X`` become two module objects
    # again and every module-level singleton exists twice. That is xit-decl-02 undone, and it
    # is silent: it surfaced only as 28 unrelated-looking failures in
    # tests/test_namespace_identity.py. Hence the assertion below rather than a bare comment.
    icdev_pkg = Path(__file__).resolve().parent  # icdev/
    if icdev_pkg.name != "icdev":  # pragma: no cover - a wiring mistake, not a runtime state
        raise RuntimeError(
            f"icdev/_shim.py expected to live directly in icdev/, found {icdev_pkg}. "
            "If this module moved, the depth above moved with it."
        )
    if not _siblings(tools_dir, icdev_pkg):
        return None
    mirror_dir = icdev_pkg / "tools"
    if not mirror_dir.is_dir():
        return None
    for existing in sys.meta_path:
        if isinstance(existing, IcdevToolsAliasFinder):
            return existing
    finder = IcdevToolsAliasFinder(tools_dir, mirror_dir)
    sys.meta_path.insert(0, finder)
    return finder


def installed() -> IcdevToolsAliasFinder | None:
    for existing in sys.meta_path:
        if isinstance(existing, IcdevToolsAliasFinder):
            return existing
    return None
