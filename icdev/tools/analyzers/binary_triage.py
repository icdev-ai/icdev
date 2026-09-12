#!/usr/bin/env python3
# CUI // SP-CTI
"""Binary triage -- a compiled artifact is an OBSERVABLE (xrv-bin-01).

WHY. Measured 2026-09-12, this tree has ZERO binary analysis. There is no
PE/ELF/Mach-O parser, no ``strings``, no YARA rule set and no decompiler:
``grep -rE 'ghidra|radare|capstone|lief|pefile|elf'`` over ``tools/``,
``args/`` and ``docs/`` finds a research watchlist and nothing that opens a
compiled file. The three subsystems that look like they might all decline:
``tools/modernization/*`` and ``extract_source_ir`` require SOURCE;
``sbom_generator`` parses dependency MANIFESTS, never the artifact;
``slsa_verify`` / ``attestation_verify`` check SIGNATURES OVER a digest and
never open the bytes the digest covers. So an operator holding a vendor binary,
a firmware image or a dropped sample had nothing in the platform to point at it.

``args/analyzer_contract.yaml`` is a ready socket (anz-con-01/disp-01/rate-01):
add an observable type and an ``analyzers:`` entry, run
``contract.py --validate``, and the artifact is dispatchable over the existing
fan-out and the ``analyzer_dispatch`` MCP tool with NO dispatcher code change.
This module is the callable that entry names.

FOUR STATUSES, AND THEY ARE NEVER MERGED, because each sends a reader
somewhere different:

    ok                  the file was read and identified
    truncated           the file is larger than ``ICDEV_BINARY_MAX_BYTES`` and
                        only a PREFIX was read. Everything reported is a
                        statement about that prefix, and says so.
    unsupported_format  the bytes were read and carry no magic this module
                        knows. NOT a failure to read, and NOT "it is clean".
    source_unreadable   the path does not exist, is not a file, or the read
                        raised. NOTHING is reported about the artifact.

AN EMPTY LIST AND AN UNMEASURED ONE ARE DIFFERENT ANSWERS. ``sections`` and
``imports`` are ``None`` -- never ``[]`` -- whenever this module did not look,
and the reason is named beside them in ``sections_basis`` / ``imports_basis``:

    parsed              a parse ran and produced this list (``[]`` is MEASURED)
    library_unavailable pefile / pyelftools is not installed on this host
    library_failed      it IS installed and raised
    format_unsupported  no parser here reads that format's table
    truncated           the structure sits past the bytes this run read, AND
                        the read really was cut short by the byte cap
    malformed_header    the header did not read, or it points past the end of a
                        file that WAS read end to end -- which is the artifact
                        lying, not a bound this run hit. The walkers cannot tell
                        those apart from a buffer alone; ``triage`` can, and
                        re-labels, because one sends a reader to a bigger cap
                        and the other does not

WHAT IS PURE PYTHON AND WHAT NEEDS A LIBRARY, and why the line is there. The
format magic, the architecture, the SHA-256, the strings and the PE/ELF SECTION
TABLE are all fixed-layout reads and are done here with ``struct`` -- so entropy
and ``packed_hint`` are measurable on EVERY deployment, including an air-gapped
one with no wheels. IMPORTS are not: resolving a PE import directory means
mapping RVAs through the section table, and an ELF's imported symbols live in
its dynamic symbol table. Those go to ``pefile`` / ``pyelftools``, which are
DECLARED in ``requirements.txt`` -- the tsg-iso-03 census refuses an undeclared
third-party import behind a handler that swallows, and an optional import whose
absence is silent is exactly that defect.

``packed_hint`` IS ``None`` WHEN IT WAS NOT MEASURED, never ``False``. A
``False`` from a run that never computed an entropy would read as "this binary
is not packed", which is a claim about the artifact rather than about the run.

THE MACH-O AMBIGUITY IS REPORTED, NOT GUESSED. ``0xCAFEBABE`` is a Mach-O FAT
header AND a Java class file, and the two are told apart only by what follows:
a fat header's ``nfat_arch`` is a small slice count, a class file's same four
bytes are its minor/major version (major 45-68 for every Java release). This
module accepts it as Mach-O only for ``nfat_arch`` in 1..16 and otherwise
reports ``unknown`` with ``ambiguous_magic`` -- a guess here would file a Java
class as an executable.

SANDBOX POSTURE: ``sandboxed`` (docs/security/sandbox-coverage.md, Gap 71). The
input is an arbitrary compiled artifact and the caller may be an anonymous
dispatch. The deployment caveat in that entry is MEASURED, not predicted: on
this host 2026-09-12 a real dispatch reached a real container and died in
``ModuleNotFoundError: No module named 'tools'``, because the image declared in
``args/sandbox_config.yaml`` is the stock ``python:3.12-slim`` and the sandbox
driver runs ``importlib.import_module`` on the declared module INSIDE it. Two
different reports, and they are different findings: ``error`` naming that
import when a sandbox IS available and cannot carry the platform, and
``sandbox_unavailable`` when there is no sandbox at all. Both fail loudly
rather than degrading to in-process, which is the intended trade.

NOTHING HERE EXECUTES THE ARTIFACT. No ``subprocess``, no ``ctypes``, no
``importlib`` of anything the file names, nothing unpacked to disk, nothing
written to any table. The file is opened ``rb``, bounded, and read.

Usage:
    python -m tools.analyzers.binary_triage /path/to/artifact --json
    python -m tools.analyzers.binary_triage /path/to/artifact
    python -m tools.analyzers.binary_triage /path/to/artifact --max-strings 50

    >>> from icdev.tools.analyzers.binary_triage import triage
    >>> report = triage("/bin/ls")
    >>> report["format"], report["status"]
    ('elf', 'ok')

Exit codes: 0 a report was produced, whatever it says (including
``source_unreadable`` -- "I could not read that file" IS the report); 2 no
report could be produced at all.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "FORMATS",
    "STATUSES",
    "detect_format",
    "shannon_entropy",
    "triage",
]

# ---------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------

FORMAT_PE = "pe"
FORMAT_ELF = "elf"
FORMAT_MACHO = "macho"
FORMAT_UNKNOWN = "unknown"
FORMATS: Tuple[str, ...] = (FORMAT_PE, FORMAT_ELF, FORMAT_MACHO, FORMAT_UNKNOWN)

STATUS_OK = "ok"
STATUS_TRUNCATED = "truncated"
STATUS_UNSUPPORTED_FORMAT = "unsupported_format"
STATUS_SOURCE_UNREADABLE = "source_unreadable"
STATUSES: Tuple[str, ...] = (
    STATUS_OK,
    STATUS_TRUNCATED,
    STATUS_UNSUPPORTED_FORMAT,
    STATUS_SOURCE_UNREADABLE,
)

#: Why a structural list is ``None``. Each names a DIFFERENT repair, so they are
#: never collapsed into one "unavailable".
BASIS_PARSED = "parsed"
BASIS_LIBRARY_UNAVAILABLE = "library_unavailable"
BASIS_LIBRARY_FAILED = "library_failed"
BASIS_FORMAT_UNSUPPORTED = "format_unsupported"
BASIS_TRUNCATED = "truncated"
BASIS_MALFORMED = "malformed_header"

# ---------------------------------------------------------------------------
# Bounds. Every one is reported on the result; a hit bound is never a quietly
# short list.
# ---------------------------------------------------------------------------

ENV_MAX_BYTES = "ICDEV_BINARY_MAX_BYTES"
DEFAULT_MAX_BYTES = 64 * 1024 * 1024  # 64 MiB

ENV_MAX_STRINGS = "ICDEV_BINARY_MAX_STRINGS"
DEFAULT_MAX_STRINGS = 500

#: A malformed header can claim 65,535 sections. The table walk stops here.
MAX_SECTIONS = 512

DEFAULT_MIN_STRING_LENGTH = 6
MAX_VERSION_HINTS = 32

#: Longest single run kept. A text file is ONE printable run from end to end, so
#: without this a 64 MiB artifact puts a 64 MiB string on the report and the
#: `max_strings` cap bounds nothing at all. Reported in ``limits``.
MAX_STRING_LENGTH = 256

#: Shannon entropy at or above this, over a section's own bytes, is the usual
#: packer/compressor signature. A HINT, and named one: a legitimately compressed
#: resource section clears it too.
PACKED_ENTROPY_THRESHOLD = 7.2

# ---------------------------------------------------------------------------
# Magic
# ---------------------------------------------------------------------------

_ELF_MAGIC = b"\x7fELF"
_MZ_MAGIC = b"MZ"
_PE_SIGNATURE = b"PE\x00\x00"
#: Thin Mach-O, both widths and both byte orders. Unambiguous.
_MACHO_THIN = {
    b"\xfe\xed\xfa\xce",
    b"\xfe\xed\xfa\xcf",
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
}
_MACHO_BIG_ENDIAN_THIN = {b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf"}
_MACHO_FAT_BE = b"\xca\xfe\xba\xbe"
_MACHO_FAT_LE = b"\xbe\xba\xfe\xca"
#: A "fat binary" with more slices than this is not a fat binary -- it is the
#: Java class file that shares the magic. See the module docstring.
_MACHO_FAT_MAX_ARCHS = 16

#: ELF ``e_machine`` -> architecture name. Not exhaustive by design: an unlisted
#: value is reported as ``elf:0xNN`` rather than as ``None``, because "I read a
#: machine field I have no name for" is not "I read nothing".
_ELF_MACHINES = {
    0x02: "sparc",
    0x03: "x86",
    0x08: "mips",
    0x14: "ppc",
    0x15: "ppc64",
    0x16: "s390",
    0x28: "arm",
    0x2A: "superh",
    0x32: "ia64",
    0x3E: "x86_64",
    0xB7: "aarch64",
    0xF3: "riscv",
}

#: PE COFF ``Machine`` -> architecture name.
_PE_MACHINES = {
    0x014C: "x86",
    0x0166: "mips",
    0x01C0: "arm",
    0x01C4: "armv7",
    0x0200: "ia64",
    0x5032: "riscv32",
    0x5064: "riscv64",
    0x8664: "x86_64",
    0xAA64: "aarch64",
}

#: Mach-O ``cputype`` -> architecture name (low 24 bits; bit 24 is the 64-bit flag).
_MACHO_CPUS = {
    7: "x86",
    12: "arm",
    18: "ppc",
}

_ELF_SHT_NOBITS = 8

_ASCII_RUN_TEMPLATE = rb"[\x20-\x7e]{%d,}"
_UTF16LE_RUN_TEMPLATE = rb"(?:[\x20-\x7e]\x00){%d,}"
_VERSION_TOKEN = re.compile(r"\b\d+\.\d+(?:\.\d+){0,2}\b")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------


def detect_format(blob: bytes) -> Tuple[str, Optional[str]]:
    """Identify *blob* from its magic bytes.

    Returns ``(format, note)``. ``note`` is set only when the identification
    itself is interesting -- today that is the ``0xCAFEBABE`` collision between
    a Mach-O fat header and a Java class file, which is REPORTED rather than
    resolved by preference.
    """
    if len(blob) < 4:
        return FORMAT_UNKNOWN, "too_short"
    head = blob[:4]
    if head == _ELF_MAGIC:
        return FORMAT_ELF, None
    if head in _MACHO_THIN:
        return FORMAT_MACHO, None
    if head in (_MACHO_FAT_BE, _MACHO_FAT_LE) and len(blob) >= 8:
        order = ">" if head == _MACHO_FAT_BE else "<"
        (nfat,) = struct.unpack_from(order + "I", blob, 4)
        if 1 <= nfat <= _MACHO_FAT_MAX_ARCHS:
            return FORMAT_MACHO, "fat"
        return FORMAT_UNKNOWN, "ambiguous_magic"
    if blob[:2] == _MZ_MAGIC:
        if len(blob) >= 0x40:
            (e_lfanew,) = struct.unpack_from("<I", blob, 0x3C)
            if 0 < e_lfanew < len(blob) - 4 and blob[e_lfanew : e_lfanew + 4] == _PE_SIGNATURE:
                return FORMAT_PE, None
        # MZ with no reachable PE signature is a DOS executable, or a PE whose
        # header sits past the bytes this run read. Either way it is not a PE
        # this module can walk, and answering `pe` would promise a section table
        # that is not there.
        return FORMAT_UNKNOWN, "mz_without_pe_header"
    return FORMAT_UNKNOWN, None


# ---------------------------------------------------------------------------
# Entropy
# ---------------------------------------------------------------------------


def shannon_entropy(blob: bytes) -> Optional[float]:
    """Shannon entropy of *blob* in bits per byte, 0.0-8.0.

    ``None`` over an empty input: entropy is undefined with no bytes, and 0.0
    there would read as "perfectly uniform", which is a measurement.
    """
    if not blob:
        return None
    counts = [0] * 256
    for byte in blob:
        counts[byte] += 1
    total = float(len(blob))
    entropy = 0.0
    for count in counts:
        if count:
            share = count / total
            entropy -= share * math.log2(share)
    return round(entropy, 4)


# ---------------------------------------------------------------------------
# Section tables -- pure Python, so entropy is measurable on every deployment
# ---------------------------------------------------------------------------


def _section(
    name: str,
    offset: int,
    size: int,
    blob: bytes,
    *,
    occupies_file: bool = True,
) -> Dict[str, Any]:
    if not occupies_file:
        # SHT_NOBITS (.bss and friends) occupies no file bytes. Its entropy is
        # not 0.0 -- there is nothing to measure.
        return {
            "name": name,
            "size": size,
            "entropy": None,
            "entropy_basis": "no_file_bytes",
        }
    if size <= 0:
        # A zero-length section (the ELF null entry, an empty .bss) has no
        # entropy to measure. Distinct from `truncated`: nothing was missed.
        return {"name": name, "size": size, "entropy": None, "entropy_basis": "empty_section"}
    if offset < 0 or offset >= len(blob):
        return {
            "name": name,
            "size": size,
            "entropy": None,
            "entropy_basis": BASIS_TRUNCATED,
        }
    body = blob[offset : offset + size]
    if len(body) < size:
        return {
            "name": name,
            "size": size,
            "entropy": shannon_entropy(body),
            "entropy_basis": BASIS_TRUNCATED,
            "bytes_measured": len(body),
        }
    return {
        "name": name,
        "size": size,
        "entropy": shannon_entropy(body),
        "entropy_basis": BASIS_PARSED,
    }


def _pe_sections(blob: bytes) -> Tuple[Optional[List[Dict[str, Any]]], str, Optional[str]]:
    """PE section table. Returns ``(sections, basis, arch)``."""
    try:
        (e_lfanew,) = struct.unpack_from("<I", blob, 0x3C)
        machine, n_sections, _stamp, _psym, _nsym, opt_size, _chars = struct.unpack_from(
            "<HHIIIHH", blob, e_lfanew + 4
        )
    except struct.error:
        return None, BASIS_MALFORMED, None
    arch = _PE_MACHINES.get(machine, "pe:0x%04x" % machine)
    if n_sections > MAX_SECTIONS:
        return None, BASIS_MALFORMED, arch
    table = e_lfanew + 4 + 20 + opt_size
    sections: List[Dict[str, Any]] = []
    for index in range(n_sections):
        base = table + index * 40
        try:
            raw_name, _vsize, _vaddr, raw_size, raw_ptr = struct.unpack_from(
                "<8sIIII", blob, base
            )
        except struct.error:
            # The table itself ran past the bytes this run read. A partial list
            # would read as "this binary has three sections".
            return None, BASIS_TRUNCATED, arch
        name = raw_name.rstrip(b"\x00").decode("utf-8", errors="replace")
        sections.append(_section(name, raw_ptr, raw_size, blob))
    return sections, BASIS_PARSED, arch


def _elf_sections(blob: bytes) -> Tuple[Optional[List[Dict[str, Any]]], str, Optional[str]]:
    """ELF section table. Returns ``(sections, basis, arch)``."""
    if len(blob) < 20:
        return None, BASIS_MALFORMED, None
    is_64 = blob[4] == 2
    order = "<" if blob[5] == 1 else ">"
    try:
        (machine,) = struct.unpack_from(order + "H", blob, 18)
    except struct.error:
        return None, BASIS_MALFORMED, None
    arch = _ELF_MACHINES.get(machine, "elf:0x%04x" % machine)
    try:
        if is_64:
            (sh_off,) = struct.unpack_from(order + "Q", blob, 40)
            sh_entsize, sh_num, sh_strndx = struct.unpack_from(order + "HHH", blob, 58)
        else:
            (sh_off,) = struct.unpack_from(order + "I", blob, 32)
            sh_entsize, sh_num, sh_strndx = struct.unpack_from(order + "HHH", blob, 46)
    except struct.error:
        return None, BASIS_MALFORMED, arch
    if sh_num == 0 or sh_off == 0:
        # An object with its section table stripped genuinely has none. That is
        # a MEASURED empty list, not an absence.
        return [], BASIS_PARSED, arch
    if sh_num > MAX_SECTIONS or sh_entsize == 0:
        return None, BASIS_MALFORMED, arch
    if sh_off + sh_num * sh_entsize > len(blob):
        return None, BASIS_TRUNCATED, arch

    def _entry(index: int) -> Optional[Tuple[int, int, int, int]]:
        base = sh_off + index * sh_entsize
        try:
            sh_name, sh_type = struct.unpack_from(order + "II", blob, base)
            if is_64:
                (sh_offset,) = struct.unpack_from(order + "Q", blob, base + 24)
                (sh_size,) = struct.unpack_from(order + "Q", blob, base + 32)
            else:
                (sh_offset,) = struct.unpack_from(order + "I", blob, base + 16)
                (sh_size,) = struct.unpack_from(order + "I", blob, base + 20)
        except struct.error:
            return None
        return sh_name, sh_type, sh_offset, sh_size

    strtab_base: Optional[int] = None
    if sh_strndx < sh_num:
        header = _entry(sh_strndx)
        if header is not None:
            strtab_base = header[2]

    sections: List[Dict[str, Any]] = []
    for index in range(sh_num):
        header = _entry(index)
        if header is None:
            return None, BASIS_TRUNCATED, arch
        sh_name, sh_type, sh_offset, sh_size = header
        name = "[%d]" % index
        if strtab_base is not None:
            start = strtab_base + sh_name
            if 0 <= start < len(blob):
                end = blob.find(b"\x00", start)
                if end != -1:
                    name = blob[start:end].decode("utf-8", errors="replace") or name
        sections.append(
            _section(name, sh_offset, sh_size, blob, occupies_file=sh_type != _ELF_SHT_NOBITS)
        )
    return sections, BASIS_PARSED, arch


def _relabel_if_not_truncated(
    sections: Optional[List[Dict[str, Any]]],
    basis: str,
    *,
    read_was_truncated: bool,
) -> Tuple[Optional[List[Dict[str, Any]]], str]:
    """Re-label ``truncated`` as ``malformed_header`` when the read was COMPLETE.

    The table walkers see only a buffer, so "this offset is past the end" is
    ambiguous to them: the file may have been cut short by the byte cap, or the
    header may be claiming a section that is not in the file. ``triage`` is the
    one place that knows which, and the two send a reader somewhere different --
    raise ``ICDEV_BINARY_MAX_BYTES`` and try again, versus the artifact itself is
    malformed. Reporting ``truncated`` for a file read end to end would send
    every reader to the first repair, which cannot work.
    """
    if read_was_truncated:
        return sections, basis
    if basis == BASIS_TRUNCATED:
        basis = BASIS_MALFORMED
    if sections:
        for section in sections:
            if section.get("entropy_basis") == BASIS_TRUNCATED:
                section["entropy_basis"] = BASIS_MALFORMED
    return sections, basis


def _macho_arch(blob: bytes) -> Optional[str]:
    head = blob[:4]
    if head not in _MACHO_THIN or len(blob) < 8:
        return None
    order = ">" if head in _MACHO_BIG_ENDIAN_THIN else "<"
    try:
        (cputype,) = struct.unpack_from(order + "i", blob, 4)
    except struct.error:
        return None
    wide = bool(cputype & 0x01000000)
    name = _MACHO_CPUS.get(cputype & 0x00FFFFFF)
    if name is None:
        return "macho:%d" % (cputype & 0x00FFFFFF)
    if wide:
        return {"x86": "x86_64", "arm": "arm64", "ppc": "ppc64"}.get(name, name + "64")
    return name


# ---------------------------------------------------------------------------
# Imports -- the one half that needs a declared third-party parser
# ---------------------------------------------------------------------------


def _pe_imports(path: Path) -> Tuple[Optional[List[str]], str, Optional[str]]:
    try:
        import pefile  # noqa: PLC0415 -- optional, DECLARED in requirements.txt
    except ImportError as exc:
        return None, BASIS_LIBRARY_UNAVAILABLE, "pefile: %s" % exc
    try:
        image = pefile.PE(str(path), fast_load=True)
        image.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]]
        )
        names: List[str] = []
        for entry in getattr(image, "DIRECTORY_ENTRY_IMPORT", None) or []:
            dll = entry.dll.decode("utf-8", errors="replace") if entry.dll else "?"
            for imported in entry.imports or []:
                symbol = (
                    imported.name.decode("utf-8", errors="replace")
                    if imported.name
                    else "#%s" % imported.ordinal
                )
                names.append("%s!%s" % (dll, symbol))
        image.close()
        return names, BASIS_PARSED, None
    except Exception as exc:  # pefile raises PEFormatError and more
        return None, BASIS_LIBRARY_FAILED, "%s: %s" % (type(exc).__name__, exc)


def _elf_imports(path: Path) -> Tuple[Optional[List[str]], str, Optional[str]]:
    try:
        from elftools.elf.elffile import ELFFile  # noqa: PLC0415 -- DECLARED in requirements.txt
    except ImportError as exc:
        return None, BASIS_LIBRARY_UNAVAILABLE, "pyelftools: %s" % exc
    try:
        names: List[str] = []
        with open(path, "rb") as handle:
            elf = ELFFile(handle)
            for section in elf.iter_sections():
                if section.header.get("sh_type") != "SHT_DYNSYM":
                    continue
                for symbol in section.iter_symbols():
                    undefined = symbol.entry.get("st_shndx") in ("SHN_UNDEF", 0)
                    kind = symbol.entry.get("st_info", {}).get("type")
                    if symbol.name and undefined and kind in ("STT_FUNC", "STT_OBJECT"):
                        names.append(symbol.name)
        return names, BASIS_PARSED, None
    except Exception as exc:
        return None, BASIS_LIBRARY_FAILED, "%s: %s" % (type(exc).__name__, exc)


# ---------------------------------------------------------------------------
# Strings
# ---------------------------------------------------------------------------


def _extract_strings(blob: bytes, *, min_length: int, cap: int) -> Tuple[List[str], bool]:
    """Printable runs, ASCII and UTF-16LE, deduplicated, in file order.

    Returns ``(strings, truncated)``. ``truncated`` is what keeps a capped list
    from reading as the whole set. UTF-16LE is scanned as well as ASCII because
    the PE version block is UTF-16 -- an ASCII-only scan misses exactly the
    strings a triage wants most.
    """
    length = max(1, int(min_length))
    seen: Dict[str, None] = {}
    truncated = False
    for pattern, decoder in (
        (_ASCII_RUN_TEMPLATE % length, "ascii"),
        (_UTF16LE_RUN_TEMPLATE % length, "utf-16-le"),
    ):
        for match in re.finditer(pattern, blob):
            if len(seen) >= cap:
                truncated = True
                break
            text = match.group().decode(decoder, errors="replace")
            if len(text) > MAX_STRING_LENGTH:
                text = text[:MAX_STRING_LENGTH]
                truncated = True
            seen.setdefault(text, None)
        if len(seen) >= cap:
            truncated = True
            break
    return list(seen), truncated


def _version_hints(strings: List[str]) -> List[str]:
    hints: Dict[str, None] = {}
    for text in strings:
        for token in _VERSION_TOKEN.findall(text):
            if len(hints) >= MAX_VERSION_HINTS:
                return list(hints)
            hints.setdefault(token, None)
    return list(hints)


# ---------------------------------------------------------------------------
# The analyzer entrypoint
# ---------------------------------------------------------------------------


def triage(
    path: Any,
    *,
    max_bytes: Optional[int] = None,
    max_strings: Optional[int] = None,
    min_string_length: int = DEFAULT_MIN_STRING_LENGTH,
) -> Dict[str, Any]:
    """Triage the compiled artifact at *path*. Never raises.

    This is the callable ``args/analyzer_contract.yaml`` declares for the
    ``binary`` observable (``binding.observable_arg: path``). Every failure is a
    named status on the returned mapping rather than an exception, because the
    dispatcher reports a raise as ``error`` -- which says the analyzer broke,
    not that the artifact could not be read.
    """
    limit_bytes = max_bytes if max_bytes is not None else _env_int(ENV_MAX_BYTES, DEFAULT_MAX_BYTES)
    limit_strings = (
        max_strings if max_strings is not None else _env_int(ENV_MAX_STRINGS, DEFAULT_MAX_STRINGS)
    )
    limits = {
        "max_bytes": limit_bytes,
        "max_strings": limit_strings,
        "min_string_length": min_string_length,
        "max_string_length": MAX_STRING_LENGTH,
        "max_sections": MAX_SECTIONS,
    }
    report: Dict[str, Any] = {
        "path": str(path),
        "generated_at": _now(),
        "status": STATUS_SOURCE_UNREADABLE,
        "reason": None,
        "format": None,
        "format_note": None,
        "sha256": None,
        "sha256_scope": None,
        "size": None,
        "bytes_read": None,
        "arch": None,
        "sections": None,
        "sections_basis": None,
        "imports": None,
        "imports_basis": None,
        "imports_detail": None,
        "strings": None,
        "strings_truncated": None,
        "version_hints": None,
        "packed_hint": None,
        "packed_basis": None,
        "limits": limits,
        "taxonomy": [],
    }

    try:
        # Path() itself raises on a non-path argument, and an analyzer that
        # raises is reported `error` by the dispatcher -- "the analyzer broke",
        # which is a different finding from "that is not a readable artifact".
        target = Path(path)
        if not target.is_file():
            report["reason"] = "not_a_file"
            return report
        size = target.stat().st_size
        with open(target, "rb") as handle:
            blob = handle.read(limit_bytes)
    except (OSError, TypeError, ValueError) as exc:
        report["reason"] = "%s: %s" % (type(exc).__name__, exc)
        return report

    report["size"] = size
    report["bytes_read"] = len(blob)
    report["sha256"] = hashlib.sha256(blob).hexdigest()
    truncated = len(blob) < size
    # The digest covers WHAT WAS READ. Labelling the scope is what stops a
    # prefix hash being quoted as the artifact's identity.
    report["sha256_scope"] = "prefix" if truncated else "whole_file"

    fmt, note = detect_format(blob)
    report["format"] = fmt
    report["format_note"] = note

    strings, strings_truncated = _extract_strings(
        blob, min_length=min_string_length, cap=limit_strings
    )
    report["strings"] = strings
    report["strings_truncated"] = strings_truncated
    report["version_hints"] = _version_hints(strings)

    if fmt == FORMAT_PE:
        sections, basis, arch = _pe_sections(blob)
        imports, imports_basis, detail = _pe_imports(target)
    elif fmt == FORMAT_ELF:
        sections, basis, arch = _elf_sections(blob)
        imports, imports_basis, detail = _elf_imports(target)
    elif fmt == FORMAT_MACHO:
        # Mach-O load commands are not walked here, and saying so is the point:
        # an empty section list for a Mach-O would read as a binary with no
        # sections rather than as a format this module does not parse.
        sections, basis, arch = None, BASIS_FORMAT_UNSUPPORTED, _macho_arch(blob)
        imports, imports_basis, detail = None, BASIS_FORMAT_UNSUPPORTED, None
    else:
        sections, basis, arch = None, BASIS_FORMAT_UNSUPPORTED, None
        imports, imports_basis, detail = None, BASIS_FORMAT_UNSUPPORTED, None

    sections, basis = _relabel_if_not_truncated(sections, basis, read_was_truncated=truncated)
    report["sections"] = sections
    report["sections_basis"] = basis
    report["arch"] = arch
    report["imports"] = imports
    report["imports_basis"] = imports_basis
    report["imports_detail"] = detail

    if sections is None:
        report["packed_hint"] = None
        report["packed_basis"] = basis
    else:
        measured = [s["entropy"] for s in sections if s.get("entropy") is not None]
        if not measured:
            report["packed_hint"] = None
            report["packed_basis"] = "no_section_entropy_measured"
        else:
            report["packed_hint"] = max(measured) >= PACKED_ENTROPY_THRESHOLD
            report["packed_basis"] = BASIS_PARSED

    if truncated:
        report["status"] = STATUS_TRUNCATED
    elif fmt == FORMAT_UNKNOWN:
        report["status"] = STATUS_UNSUPPORTED_FORMAT
    else:
        report["status"] = STATUS_OK

    report["taxonomy"] = _taxonomy(report)
    return report


def _taxonomy(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Taxonomy tags for the dispatcher, matching the contract declaration.

    A predicate that was NOT MEASURED emits no tag. The declared levels are
    ``info`` / ``suspicious`` / ``malicious`` and none of them can spell
    "unknown", so tagging an unmeasured ``packed`` as ``info`` would report a
    benign verdict for a question nobody asked. The reason lives on
    ``packed_basis`` instead.
    """
    tags: List[Dict[str, Any]] = [
        {"predicate": "format", "level": "info", "value": report.get("format")}
    ]
    if report.get("packed_hint") is not None:
        tags.append(
            {
                "predicate": "packed",
                "level": "suspicious" if report["packed_hint"] else "info",
                "value": report["packed_hint"],
            }
        )
    if report.get("imports") is not None:
        tags.append({"predicate": "imports", "level": "info", "value": len(report["imports"])})
    if report.get("strings") is not None:
        tags.append({"predicate": "strings", "level": "info", "value": len(report["strings"])})
    return tags


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _render(report: Dict[str, Any]) -> str:
    lines = [
        "binary triage: %s" % report["path"],
        "  status         %s%s"
        % (report["status"], "  (%s)" % report["reason"] if report.get("reason") else ""),
    ]
    if report["status"] == STATUS_SOURCE_UNREADABLE:
        lines.append("  nothing is reported about the artifact.")
        return "\n".join(lines)
    note = "  (%s)" % report["format_note"] if report.get("format_note") else ""
    lines += [
        "  format         %s%s" % (report["format"], note),
        "  arch           %s" % (report["arch"] or "not determined"),
        "  sha256         %s  [%s]" % (report["sha256"], report["sha256_scope"]),
        "  size           %s bytes (read %s)" % (report["size"], report["bytes_read"]),
    ]
    sections = report["sections"]
    if sections is None:
        lines.append("  sections       not measured (%s)" % report["sections_basis"])
    else:
        lines.append("  sections       %d" % len(sections))
        for section in sections[:20]:
            entropy = section["entropy"]
            shown = (
                "%.2f" % entropy if entropy is not None else "--  (%s)" % section["entropy_basis"]
            )
            lines.append(
                "                   %-20s %10s  H=%s" % (section["name"], section["size"], shown)
            )
    imports = report["imports"]
    if imports is None:
        lines.append("  imports        not measured (%s)" % report["imports_basis"])
        if report.get("imports_detail"):
            lines.append("                 %s" % report["imports_detail"])
    else:
        lines.append("  imports        %d" % len(imports))
    packed = report["packed_hint"]
    lines.append(
        "  packed_hint    %s"
        % (packed if packed is not None else "not measured (%s)" % report["packed_basis"])
    )
    strings = report["strings"] or []
    lines.append(
        "  strings        %d%s" % (len(strings), "  (capped)" if report["strings_truncated"] else "")
    )
    hints = report["version_hints"] or []
    lines.append("  version_hints  %s" % (", ".join(hints[:8]) if hints else "none found"))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.analyzers.binary_triage",
        description="Triage a compiled executable, shared library or firmware image.",
    )
    parser.add_argument("path", help="Path to the artifact")
    parser.add_argument("--json", action="store_true", help="Machine-readable report")
    parser.add_argument("--max-bytes", type=int, default=None, help="Override " + ENV_MAX_BYTES)
    parser.add_argument("--max-strings", type=int, default=None, help="Override " + ENV_MAX_STRINGS)
    parser.add_argument(
        "--min-string-length",
        type=int,
        default=DEFAULT_MIN_STRING_LENGTH,
        help="Shortest printable run counted as a string",
    )
    args = parser.parse_args(argv)

    try:
        report = triage(
            args.path,
            max_bytes=args.max_bytes,
            max_strings=args.max_strings,
            min_string_length=args.min_string_length,
        )
    except Exception as exc:  # a report could not be produced at all
        print("binary triage could not run: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(_render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
