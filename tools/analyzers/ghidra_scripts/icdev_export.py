# CUI // SP-CTI
# @category ICDEV
# @runtime Jython
"""Ghidra postScript: export one program's functions, imports and strings as JSON (xrv-bin-02).

THIS FILE NEVER RUNS UNDER CPython. It is handed to ``analyzeHeadless`` with
``-postScript`` and executes inside Ghidra, under Jython 2.7 (Ghidra 10.x) or
PyGhidra (11.x+), with the Ghidra script API injected as GLOBALS. Its consumer
is ``tools/analyzers/ghidra_headless.py``, which reads the JSON back.

THREE RULES IT LIVES BY, and each has a reason that is not style:

  1. NO THIRD-PARTY IMPORT. Jython 2.7 has no pip and no site-packages here;
     ``json``, ``sys`` and ``traceback`` are all that is used. This is also why
     the ``ghidra.*`` imports below sit at MODULE level and NOT inside a
     ``try``: an undeclared third-party import inside a swallowing handler is
     precisely the shape ``tools/ci/undeclared_import_census.py`` refuses
     (tsg-iso-03), and there is nothing to swallow -- if Ghidra is not here,
     this file is not running.

  2. IT ALWAYS WRITES ITS JSON, INCLUDING WHEN IT FAILS. A postScript that
     raises leaves NO file, and the caller then cannot tell "Ghidra could not
     load this artifact" from "the export step crashed". So every section is
     gathered under its own guard and a failure lands in the JSON as ``error``
     with the section left ``null``.

  3. ``null`` AND ``[]`` ARE DIFFERENT ANSWERS, and the caller's whole design
     depends on this one. ``[]`` means THIS SECTION WAS WALKED AND WAS EMPTY.
     ``null`` means the walk did not happen or raised. A section that failed
     must never be written as ``[]``, or a broken extractor reads downstream as
     a binary with no imports.

Arguments, positional, from ``getScriptArgs()``:
    <out.json> <max_functions> <max_strings>
"""
import json
import sys
import traceback

from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor

#: The Ghidra script runtime injects ``currentProgram`` / ``getScriptArgs`` as
#: globals rather than as importable names. Reading them out of ``globals()``
#: is what keeps this file free of undefined-name lint while staying honest
#: that they come from the host, not from an import.
_HOST = globals()

#: Per-function decompile budget. The whole-run budget is the caller's
#: ``-analysisTimeoutPerFile``; this one stops ONE pathological function from
#: spending it. Only the entry point is decompiled, so this is paid once.
DECOMPILE_TIMEOUT_SECONDS = 60

#: Shortest defined string kept, matching binary_triage's own floor so the two
#: analyzers do not report different string counts for one artifact.
MIN_STRING_LENGTH = 6


def _args():
    """``(out_path, max_functions, max_strings)`` with defaults that bound.

    A missing or malformed bound falls back to a FINITE default and never to
    "unlimited": this script runs unattended inside a JVM, and an unbounded
    walk over a hostile artifact is how the caller's wall budget gets spent
    with no export written at all.
    """
    raw = list(_HOST["getScriptArgs"]())
    out_path = raw[0] if raw else "icdev_export.json"

    def _int(index, default):
        try:
            value = int(raw[index])
            return value if value > 0 else default
        except (IndexError, TypeError, ValueError):
            return default

    return out_path, _int(1, 2000), _int(2, 500)


def _functions(program, limit):
    """``[{name, address, size}]``, capped. Returns ``(list, truncated)``."""
    out = []
    truncated = False
    iterator = program.getFunctionManager().getFunctions(True)
    while iterator.hasNext():
        function = iterator.next()
        if len(out) >= limit:
            truncated = True
            break
        out.append(
            {
                "name": function.getName(),
                "address": str(function.getEntryPoint()),
                "size": int(function.getBody().getNumAddresses()),
                "external": bool(function.isExternal()),
                "thunk": bool(function.isThunk()),
            }
        )
    return out, truncated


def _imports(program):
    """External symbol names -- the imported functions and data.

    ``[]`` here is a MEASURED empty: a statically linked binary genuinely
    imports nothing, and that is a real and useful finding about it.
    """
    out = []
    seen = set()
    for symbol in program.getSymbolTable().getExternalSymbols():
        name = symbol.getName()
        namespace = symbol.getParentNamespace()
        library = namespace.getName() if namespace is not None else None
        key = (library, name)
        if key in seen:
            continue
        seen.add(key)
        out.append("%s!%s" % (library, name) if library else name)
    return sorted(out)


def _strings(program, limit):
    """Defined strings in address order, capped. Returns ``(list, truncated)``.

    ``DefinedDataIterator`` is imported HERE rather than at module level
    because its package moved between Ghidra 9.x and 10.x; a module-level
    import that raises would take the whole export down, and the caller would
    lose the functions and imports it could otherwise have had. This is the one
    place a narrow guard is correct, and the failure is still RECORDED rather
    than swallowed -- it propagates to the section's own ``null``.
    """
    from ghidra.program.util import DefinedDataIterator

    out = []
    truncated = False
    for data in DefinedDataIterator.definedStrings(program):
        if len(out) >= limit:
            truncated = True
            break
        try:
            value = data.getValue()
        except Exception:  # noqa: BLE001 -- one undecodable datum, not the walk
            continue
        if value is None:
            continue
        text = str(value)
        if len(text) >= MIN_STRING_LENGTH:
            out.append(text)
    return out, truncated


def _entry_decompiled(program):
    """C for the entry point. ``(text_or_None, basis)``.

    ONE function, not the program: decompiling every function of a real binary
    is minutes to hours and the caller's budget is wall-clock. The basis names
    which absence this is, because "no entry point was found" and "the
    decompiler refused" send a reader to different places.
    """
    entry = program.getImageBase()
    manager = program.getFunctionManager()
    function = manager.getFunctionAt(entry)
    if function is None:
        # The image base is rarely the entry function. Fall back to a function
        # named like one before giving up -- and say which route answered.
        for candidate in ("entry", "main", "_start", "WinMain", "DllMain"):
            iterator = manager.getFunctions(True)
            while iterator.hasNext():
                item = iterator.next()
                if item.getName() == candidate:
                    function = item
                    break
            if function is not None:
                break
    if function is None:
        return None, "no_entry_point"

    decompiler = DecompInterface()
    try:
        if not decompiler.openProgram(program):
            return None, "decompiler_unavailable"
        result = decompiler.decompileFunction(
            function, DECOMPILE_TIMEOUT_SECONDS, ConsoleTaskMonitor()
        )
        if result is None or not result.decompileCompleted():
            return None, "decompile_incomplete"
        code = result.getDecompiledFunction()
        if code is None:
            return None, "decompile_empty"
        return str(code.getC()), "parsed"
    finally:
        decompiler.dispose()


def main():
    out_path, max_functions, max_strings = _args()
    program = _HOST.get("currentProgram")

    payload = {
        "program": None,
        "functions": None,
        "functions_truncated": None,
        "imports": None,
        "strings": None,
        "strings_truncated": None,
        "entry_decompiled": None,
        "entry_basis": None,
        "error": None,
    }
    errors = []

    if program is None:
        # analyzeHeadless ran the postScript with no program -- an import that
        # produced nothing. Written, not raised, so the caller reads a reason
        # rather than `export_absent`.
        payload["error"] = "no currentProgram: the import produced no program"
        _write(out_path, payload)
        return

    try:
        payload["program"] = {
            "name": program.getName(),
            "format": str(program.getExecutableFormat()),
            "language": str(program.getLanguageID()),
            "compiler": str(program.getCompilerSpec().getCompilerSpecID()),
            "image_base": str(program.getImageBase()),
            "sha256": program.getExecutableSHA256(),
        }
    except Exception:  # noqa: BLE001
        errors.append("program metadata: %s" % traceback.format_exc().strip().splitlines()[-1])

    try:
        payload["functions"], payload["functions_truncated"] = _functions(program, max_functions)
    except Exception:  # noqa: BLE001 -- section stays null, failure is recorded
        errors.append("functions: %s" % traceback.format_exc().strip().splitlines()[-1])

    try:
        payload["imports"] = _imports(program)
    except Exception:  # noqa: BLE001
        errors.append("imports: %s" % traceback.format_exc().strip().splitlines()[-1])

    try:
        payload["strings"], payload["strings_truncated"] = _strings(program, max_strings)
    except Exception:  # noqa: BLE001
        errors.append("strings: %s" % traceback.format_exc().strip().splitlines()[-1])

    try:
        payload["entry_decompiled"], payload["entry_basis"] = _entry_decompiled(program)
    except Exception:  # noqa: BLE001
        payload["entry_basis"] = "decompiler_failed"
        errors.append("entry: %s" % traceback.format_exc().strip().splitlines()[-1])

    if errors:
        payload["error"] = "; ".join(errors)
    _write(out_path, payload)


def _write(out_path, payload):
    """Write the export. A failure here goes to stderr, which the caller keeps
    as ``stderr_tail`` -- it is the only channel left once the file is gone."""
    try:
        handle = open(out_path, "w")
        try:
            json.dump(payload, handle, indent=2)
        finally:
            handle.close()
    except Exception:  # noqa: BLE001
        sys.stderr.write("icdev_export: could not write %s\n%s\n" % (out_path, traceback.format_exc()))


main()
