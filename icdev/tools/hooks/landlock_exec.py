#!/usr/bin/env python3
# CUI // SP-CTI
"""omx-guard-05 (L3): restrict filesystem WRITES with Landlock, then exec a command.

The Linux fallback when bubblewrap is absent (``write_sandbox`` picks one). This
process confines ITSELF -- every write right is handled, only the ``--rw`` trees
get them back -- and then ``execvp``s the command, which inherits the domain and
can never leave it. Reads, execution and the network are not handled, so they
stay open (LLM providers are remote).

Stdlib only, run as ``python -I <this file>``: it executes before the harness
starts, with the task worktree as cwd, and must import nothing from there.

FAILS CLOSED. The runner only routes here after ``write_sandbox`` probed Landlock
as available, so a confinement that fails now is a surprise: the command is NOT
run, and the reason is on stderr with exit ``REFUSED_EXIT``.

Usage::

    python -I tools/hooks/landlock_exec.py --rw /work --rw /tmp -- <argv...>
    python -I tools/hooks/landlock_exec.py --abi      # print the ABI (0 = none)
"""
from __future__ import annotations

import ctypes
import os
import stat
import struct
import sys
from typing import List, Sequence, Tuple

REFUSED_EXIT = 125

# Syscall numbers are shared by every architecture (the unified table).
_SYS_CREATE_RULESET = 444
_SYS_ADD_RULE = 445
_SYS_RESTRICT_SELF = 446
_CREATE_RULESET_VERSION = 1
_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38

_WRITE_FILE = 1 << 1
_REMOVE_DIR = 1 << 4
_REMOVE_FILE = 1 << 5
_MAKE_CHAR = 1 << 6
_MAKE_DIR = 1 << 7
_MAKE_REG = 1 << 8
_MAKE_SOCK = 1 << 9
_MAKE_FIFO = 1 << 10
_MAKE_BLOCK = 1 << 11
_MAKE_SYM = 1 << 12
_REFER = 1 << 13      # ABI 2
_TRUNCATE = 1 << 14   # ABI 3

_WRITES_V1 = (_WRITE_FILE | _REMOVE_DIR | _REMOVE_FILE | _MAKE_CHAR | _MAKE_DIR
              | _MAKE_REG | _MAKE_SOCK | _MAKE_FIFO | _MAKE_BLOCK | _MAKE_SYM)
#: The rights a rule on a FILE (not a directory) may carry.
_FILE_RIGHTS = _WRITE_FILE | _TRUNCATE


def _libc():
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return libc


def abi() -> int:
    """The kernel's Landlock ABI version; 0 when Landlock is absent or disabled."""
    if not sys.platform.startswith("linux"):
        return 0
    try:
        version = _libc().syscall(_SYS_CREATE_RULESET, None, ctypes.c_size_t(0),
                                  ctypes.c_uint32(_CREATE_RULESET_VERSION))
    except (OSError, AttributeError):
        return 0
    return int(version) if version > 0 else 0


def handled_rights(version: int) -> int:
    rights = _WRITES_V1
    if version >= 2:
        rights |= _REFER
    if version >= 3:
        rights |= _TRUNCATE
    return rights


def _fail(msg: str) -> None:
    raise OSError(msg)


def restrict_writes(roots: Sequence[str]) -> Tuple[int, List[str]]:
    """Confine this process: writes only beneath ``roots``. Returns (abi, skipped)."""
    version = abi()
    if version < 1:
        _fail("Landlock is not available on this kernel")
    libc = _libc()
    handled = handled_rights(version)
    attr = struct.pack("=Q", handled)
    ruleset = libc.syscall(_SYS_CREATE_RULESET, ctypes.c_char_p(attr),
                           ctypes.c_size_t(len(attr)), ctypes.c_uint32(0))
    if ruleset < 0:
        _fail(f"landlock_create_ruleset: {os.strerror(ctypes.get_errno())}")
    skipped: List[str] = []
    try:
        for root in roots:
            try:
                fd = os.open(root, os.O_PATH | os.O_CLOEXEC)
            except OSError:
                skipped.append(root)  # nothing there to write into
                continue
            try:
                is_dir = stat.S_ISDIR(os.fstat(fd).st_mode)
                allowed = handled if is_dir else handled & _FILE_RIGHTS
                rule = struct.pack("=Qi", allowed, fd)
                if libc.syscall(_SYS_ADD_RULE, ctypes.c_int(ruleset),
                                ctypes.c_int(_RULE_PATH_BENEATH),
                                ctypes.c_char_p(rule), ctypes.c_uint32(0)) != 0:
                    _fail(f"landlock_add_rule({root}): {os.strerror(ctypes.get_errno())}")
            finally:
                os.close(fd)
        if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            _fail(f"prctl(NO_NEW_PRIVS): {os.strerror(ctypes.get_errno())}")
        if libc.syscall(_SYS_RESTRICT_SELF, ctypes.c_int(ruleset), ctypes.c_uint32(0)) != 0:
            _fail(f"landlock_restrict_self: {os.strerror(ctypes.get_errno())}")
    finally:
        os.close(ruleset)
    return version, skipped


def parse(argv: Sequence[str]) -> Tuple[List[str], List[str]]:
    roots: List[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--":
            return roots, list(argv[i + 1:])
        if argv[i] == "--rw" and i + 1 < len(argv):
            roots.append(argv[i + 1])
            i += 2
            continue
        raise SystemExit(f"landlock_exec: unexpected argument {argv[i]!r}")
    return roots, []


def main(argv: Sequence[str]) -> int:
    if list(argv) == ["--abi"]:
        print(abi())
        return 0
    roots, command = parse(argv)
    if not command:
        sys.stderr.write("landlock_exec: no command after --\n")
        return 2
    try:
        restrict_writes(roots)
    except OSError as exc:
        sys.stderr.write(f"ICDEV sandbox: refusing to run unconfined: {exc}\n")
        return REFUSED_EXIT
    sys.stdout.flush()
    try:
        os.execvp(command[0], command)
    except OSError as exc:
        sys.stderr.write(f"landlock_exec: cannot exec {command[0]}: {exc}\n")
        return 127
    return 0  # unreachable


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
