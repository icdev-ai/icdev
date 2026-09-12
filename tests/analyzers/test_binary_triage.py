# CUI // SP-CTI
"""tools/analyzers/binary_triage.py -- a compiled artifact is an observable (xrv-bin-01).

EVERY FIXTURE IS GENERATED IN-TEST. Nothing binary is committed: a repository
that carries executables in its test tree is one `git add` away from carrying a
sample somebody actually found in the wild, and the point here is the PARSER,
which a hand-built header exercises exactly as well as a real one. The headers
below are the real fixed layouts (PE/COFF, ELF64) written with ``struct``, and
the module was additionally measured against real artifacts on this host --
``python.exe``, ``kernel32.dll`` and a glibc ``/bin/true`` -- before these were
written.

THE ASSERTIONS THAT MATTER ARE THE ONES ABOUT ABSENCE. An empty list and an
unmeasured one are different answers, and several tests here exist only to
prove the module never spells one as the other.
"""
from __future__ import annotations

import importlib.util
import json
import struct
import subprocess
import sys
import types
from pathlib import Path

import pytest

from tools.analyzers import binary_triage as bt

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Fixture builders -- real layouts, built byte by byte
# ---------------------------------------------------------------------------


def _elf64_header(*, machine: int = 0x3E, sh_off: int = 0, sh_num: int = 0,
                  sh_entsize: int = 64, sh_strndx: int = 0) -> bytes:
    """A 64-byte ELF64 little-endian header. ``sh_num=0`` means no section table."""
    head = bytearray(64)
    head[0:4] = b"\x7fELF"
    head[4] = 2      # EI_CLASS = ELFCLASS64
    head[5] = 1      # EI_DATA  = ELFDATA2LSB
    head[6] = 1      # EI_VERSION
    struct.pack_into("<H", head, 16, 2)            # e_type = ET_EXEC
    struct.pack_into("<H", head, 18, machine)      # e_machine
    struct.pack_into("<I", head, 20, 1)            # e_version
    struct.pack_into("<Q", head, 40, sh_off)       # e_shoff
    struct.pack_into("<H", head, 52, 64)           # e_ehsize
    struct.pack_into("<H", head, 58, sh_entsize)   # e_shentsize
    struct.pack_into("<H", head, 60, sh_num)       # e_shnum
    struct.pack_into("<H", head, 62, sh_strndx)    # e_shstrndx
    return bytes(head)


def _elf64_with_sections(text_payload: bytes) -> bytes:
    """An ELF64 with a null section, a PROGBITS ``.text`` and a ``.shstrtab``."""
    strtab = b"\x00.text\x00.shstrtab\x00"
    header_area = 64 + 3 * 64          # ELF header + three 64-byte section headers
    text_off = header_area
    strtab_off = text_off + len(text_payload)

    def _sh(name_off: int, sh_type: int, offset: int, size: int) -> bytes:
        entry = bytearray(64)
        struct.pack_into("<I", entry, 0, name_off)
        struct.pack_into("<I", entry, 4, sh_type)
        struct.pack_into("<Q", entry, 24, offset)
        struct.pack_into("<Q", entry, 32, size)
        return bytes(entry)

    body = _elf64_header(sh_off=64, sh_num=3, sh_strndx=2)
    body += _sh(0, 0, 0, 0)                                   # SHT_NULL
    body += _sh(1, 1, text_off, len(text_payload))            # .text, SHT_PROGBITS
    body += _sh(7, 3, strtab_off, len(strtab))                # .shstrtab, SHT_STRTAB
    body += text_payload + strtab
    return body


def _pe64(section_payload: bytes, *, machine: int = 0x8664, name: bytes = b".text") -> bytes:
    """A minimal PE32+ with an MZ stub, a PE signature, one COFF section."""
    e_lfanew = 0x40
    stub = bytearray(e_lfanew)
    stub[0:2] = b"MZ"
    struct.pack_into("<I", stub, 0x3C, e_lfanew)

    coff = struct.pack("<HHIIIHH", machine, 1, 0, 0, 0, 0, 0x0022)
    table_off = e_lfanew + 4 + 20
    raw_ptr = table_off + 40
    section = struct.pack(
        "<8sIIIIIIHHI",
        name.ljust(8, b"\x00"),
        len(section_payload),   # VirtualSize
        0x1000,                 # VirtualAddress
        len(section_payload),   # SizeOfRawData
        raw_ptr,                # PointerToRawData
        0, 0, 0, 0, 0x60000020,
    )
    return bytes(stub) + b"PE\x00\x00" + coff + section + section_payload


def _write(tmp_path: Path, name: str, blob: bytes) -> Path:
    target = tmp_path / name
    target.write_bytes(blob)
    return target


# ---------------------------------------------------------------------------
# Format identification
# ---------------------------------------------------------------------------


def test_a_minimal_elf_header_is_identified_with_its_architecture(tmp_path):
    path = _write(tmp_path, "tiny.elf", _elf64_header())
    report = bt.triage(path)

    assert report["status"] == bt.STATUS_OK
    assert report["format"] == bt.FORMAT_ELF
    assert report["arch"] == "x86_64"
    assert report["sha256_scope"] == "whole_file"
    assert report["size"] == 64 and report["bytes_read"] == 64


def test_an_elf_with_no_section_table_reports_a_MEASURED_empty_list(tmp_path):
    """``[]`` here is a finding: the table was read and it is empty.

    The distinction this whole module turns on -- an empty list came back from a
    parse, ``None`` means nobody looked -- and a stripped object is the one case
    where the honest answer really is the empty list.
    """
    report = bt.triage(_write(tmp_path, "stripped.elf", _elf64_header()))

    assert report["sections"] == []
    assert report["sections_basis"] == bt.BASIS_PARSED
    # ...and with nothing to measure, packed_hint is None rather than False.
    assert report["packed_hint"] is None
    assert report["packed_basis"] == "no_section_entropy_measured"


def test_an_elf_section_table_is_walked_with_names_and_entropy(tmp_path):
    payload = bytes(range(256)) * 4        # uniform: entropy 8.0 by construction
    report = bt.triage(_write(tmp_path, "sect.elf", _elf64_with_sections(payload)))

    names = [s["name"] for s in report["sections"]]
    assert ".text" in names and ".shstrtab" in names
    text = next(s for s in report["sections"] if s["name"] == ".text")
    assert text["size"] == len(payload)
    assert text["entropy"] == pytest.approx(8.0, abs=0.01)
    assert text["entropy_basis"] == bt.BASIS_PARSED


def test_a_zero_length_section_is_empty_not_truncated(tmp_path):
    """The ELF null entry has no bytes; that is not a missed read."""
    report = bt.triage(_write(tmp_path, "sect.elf", _elf64_with_sections(b"\x00" * 32)))
    null_entry = report["sections"][0]

    assert null_entry["size"] == 0
    assert null_entry["entropy"] is None
    assert null_entry["entropy_basis"] == "empty_section"


def test_a_minimal_pe_is_identified_and_its_section_table_walked(tmp_path):
    report = bt.triage(_write(tmp_path, "tiny.exe", _pe64(b"A" * 64)))

    assert report["status"] == bt.STATUS_OK
    assert report["format"] == bt.FORMAT_PE
    assert report["arch"] == "x86_64"
    assert [s["name"] for s in report["sections"]] == [".text"]
    assert report["sections"][0]["size"] == 64


def test_an_mz_with_no_reachable_pe_signature_is_not_called_pe(tmp_path):
    """A DOS stub is not a PE, and answering ``pe`` promises a table that is absent."""
    stub = bytearray(0x40)
    stub[0:2] = b"MZ"
    struct.pack_into("<I", stub, 0x3C, 0x40)      # points past the file
    report = bt.triage(_write(tmp_path, "dos.exe", bytes(stub)))

    assert report["format"] == bt.FORMAT_UNKNOWN
    assert report["format_note"] == "mz_without_pe_header"
    assert report["status"] == bt.STATUS_UNSUPPORTED_FORMAT


def test_a_thin_macho_reports_its_arch_and_declines_its_sections(tmp_path):
    blob = struct.pack(">I", 0xFEEDFACF) + struct.pack(">i", 0x01000007) + b"\x00" * 24
    report = bt.triage(_write(tmp_path, "thin.macho", blob))

    assert report["format"] == bt.FORMAT_MACHO
    assert report["arch"] == "x86_64"
    # Load commands are not walked here. `[]` would read as "no sections".
    assert report["sections"] is None
    assert report["sections_basis"] == bt.BASIS_FORMAT_UNSUPPORTED


def test_a_java_class_file_is_not_filed_as_a_macho(tmp_path):
    """``0xCAFEBABE`` is shared, and the collision is reported rather than guessed.

    A class file's four bytes after the magic are minor/major version -- major
    is 45-68 for every Java release -- where a fat header's are a slice count.
    """
    java = struct.pack(">IHH", 0xCAFEBABE, 0, 65) + b"\x00" * 32
    report = bt.triage(_write(tmp_path, "Thing.class", java))

    assert report["format"] == bt.FORMAT_UNKNOWN
    assert report["format_note"] == "ambiguous_magic"

    fat = struct.pack(">II", 0xCAFEBABE, 2) + b"\x00" * 32
    assert bt.detect_format(fat) == (bt.FORMAT_MACHO, "fat")


def test_an_unknown_format_reads_unsupported_format_and_never_ok(tmp_path):
    report = bt.triage(_write(tmp_path, "notes.txt", b"plain text, no magic here\n" * 8))

    assert report["status"] == bt.STATUS_UNSUPPORTED_FORMAT
    assert report["format"] == bt.FORMAT_UNKNOWN
    # It was still READ: the digest and the strings are real findings.
    assert report["sha256"] and report["strings"]


# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------


def test_over_the_byte_cap_reads_truncated_and_labels_the_digest_scope(tmp_path):
    path = _write(tmp_path, "big.elf", _elf64_header() + b"Z" * 4096)
    report = bt.triage(path, max_bytes=128)

    assert report["status"] == bt.STATUS_TRUNCATED
    assert report["bytes_read"] == 128
    assert report["size"] == 64 + 4096
    # The digest covers the PREFIX. Quoting it as the artifact's identity is the
    # mistake the scope field exists to prevent.
    assert report["sha256_scope"] == "prefix"
    assert report["limits"]["max_bytes"] == 128


def test_the_byte_cap_is_read_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv(bt.ENV_MAX_BYTES, "96")
    report = bt.triage(_write(tmp_path, "big.elf", _elf64_header() + b"Z" * 1024))

    assert report["bytes_read"] == 96
    assert report["status"] == bt.STATUS_TRUNCATED


def test_a_capped_string_list_says_it_was_capped(tmp_path):
    # NUL-separated: printable runs are only bounded by non-printable bytes, so
    # a space-joined blob is ONE run, not 200 -- which is how the char cap below
    # came to be needed.
    blob = b"".join(b"marker%04d\x00" % n for n in range(200))
    report = bt.triage(_write(tmp_path, "strings.bin", blob), max_strings=5)

    assert len(report["strings"]) == 5
    assert report["strings_truncated"] is True


def test_one_enormous_printable_run_cannot_put_the_whole_file_on_the_report(tmp_path):
    """A text artifact is one run end to end, so ``max_strings`` bounds nothing.

    Found by the test above: a 200-token blob joined with spaces came back as a
    single string, so on a 64 MiB file the "capped at 500 strings" bound would
    have admitted a 64 MiB one.
    """
    report = bt.triage(_write(tmp_path, "wall.txt", b"A" * 100_000))

    assert len(report["strings"]) == 1
    assert len(report["strings"][0]) == bt.MAX_STRING_LENGTH
    assert report["strings_truncated"] is True
    assert report["limits"]["max_string_length"] == bt.MAX_STRING_LENGTH


def test_a_section_table_past_the_bytes_read_is_none_not_a_short_list(tmp_path):
    """A partial table would read as "this binary has one section"."""
    path = _write(tmp_path, "sect.elf", _elf64_with_sections(b"\x11" * 64))
    report = bt.triage(path, max_bytes=80)

    assert report["sections"] is None
    assert report["sections_basis"] == bt.BASIS_TRUNCATED
    assert report["packed_hint"] is None


def test_a_header_claiming_more_sections_than_the_bound_is_refused(tmp_path):
    blob = _elf64_header(sh_off=64, sh_num=60000, sh_strndx=0) + b"\x00" * 256
    report = bt.triage(_write(tmp_path, "lying.elf", blob))

    assert report["sections"] is None
    assert report["sections_basis"] == bt.BASIS_MALFORMED


def test_a_header_pointing_past_a_file_read_WHOLE_is_malformed_not_truncated(tmp_path):
    """The two send a reader to different repairs, so they are not one word.

    A table walker sees only a buffer and cannot tell "the cap cut this short"
    from "the header is lying". ``triage`` knows which, and re-labels -- because
    ``truncated`` tells every reader to raise ``ICDEV_BINARY_MAX_BYTES``, and on
    a file read end to end that cannot work.
    """
    # sh_off far past a file that is read in FULL: nothing was cut short.
    blob = _elf64_header(sh_off=1_000_000, sh_num=3, sh_strndx=0) + b"\x00" * 64
    whole = bt.triage(_write(tmp_path, "lying.elf", blob))
    assert whole["status"] == bt.STATUS_OK           # the read itself succeeded
    assert whole["sections_basis"] == bt.BASIS_MALFORMED

    # The SAME header, now genuinely cut short by the cap: truncated stands.
    cut = bt.triage(_write(tmp_path, "lying2.elf", blob), max_bytes=70)
    assert cut["status"] == bt.STATUS_TRUNCATED
    assert cut["sections_basis"] == bt.BASIS_TRUNCATED


# ---------------------------------------------------------------------------
# Strings and version hints
# ---------------------------------------------------------------------------


def test_utf16le_strings_are_read_as_well_as_ascii(tmp_path):
    """The PE version block is UTF-16LE; an ASCII-only scan misses it."""
    blob = _elf64_header() + "ProductVersion 3.14.0".encode("utf-16-le")
    report = bt.triage(_write(tmp_path, "wide.elf", blob))

    assert any("ProductVersion" in s for s in report["strings"])
    assert "3.14.0" in report["version_hints"]


def test_a_run_shorter_than_the_minimum_is_not_a_string(tmp_path):
    report = bt.triage(_write(tmp_path, "short.bin", b"\x00ab\x00cd\x00" * 4), min_string_length=6)
    assert report["strings"] == []


# ---------------------------------------------------------------------------
# packed_hint
# ---------------------------------------------------------------------------


def test_a_high_entropy_section_raises_the_packed_hint(tmp_path):
    import os as _os

    payload = _os.urandom(8192)     # entropy ~8.0
    report = bt.triage(_write(tmp_path, "packed.elf", _elf64_with_sections(payload)))

    assert report["packed_hint"] is True
    assert report["packed_basis"] == bt.BASIS_PARSED
    assert {"predicate": "packed", "level": "suspicious", "value": True} in report["taxonomy"]


def test_a_low_entropy_section_lowers_it(tmp_path):
    report = bt.triage(_write(tmp_path, "plain.elf", _elf64_with_sections(b"A" * 8192)))

    assert report["packed_hint"] is False
    assert {"predicate": "packed", "level": "info", "value": False} in report["taxonomy"]


def test_an_unmeasured_packed_hint_is_none_and_emits_NO_taxonomy_tag(tmp_path):
    """``info`` would be a benign verdict on a question nobody asked.

    The declared levels are info/suspicious/malicious and none of them spells
    "unknown", so the tag is omitted and the reason goes on ``packed_basis``.
    """
    report = bt.triage(_write(tmp_path, "notes.txt", b"no magic at all here\n" * 4))

    assert report["packed_hint"] is None
    assert report["packed_basis"] == bt.BASIS_FORMAT_UNSUPPORTED
    assert [t for t in report["taxonomy"] if t["predicate"] == "packed"] == []


# ---------------------------------------------------------------------------
# Imports -- the three bases, each reached deliberately
# ---------------------------------------------------------------------------


def test_an_absent_parser_library_is_named_not_spelled_as_no_imports(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pefile", None)   # makes `import pefile` raise
    report = bt.triage(_write(tmp_path, "tiny.exe", _pe64(b"B" * 32)))

    assert report["imports"] is None
    assert report["imports_basis"] == bt.BASIS_LIBRARY_UNAVAILABLE
    assert "pefile" in report["imports_detail"]
    # ...while everything pure-Python still answers.
    assert report["format"] == bt.FORMAT_PE and report["sections"] is not None


def test_a_parser_library_that_raises_reads_library_failed_not_unavailable(tmp_path, monkeypatch):
    """Installed-and-broken and not-installed send a reader to different repairs."""
    fake = types.ModuleType("pefile")
    fake.DIRECTORY_ENTRY = {"IMAGE_DIRECTORY_ENTRY_IMPORT": 1}

    def _boom(*_args, **_kwargs):
        raise ValueError("not a PE after all")

    fake.PE = _boom
    monkeypatch.setitem(sys.modules, "pefile", fake)
    report = bt.triage(_write(tmp_path, "tiny.exe", _pe64(b"C" * 32)))

    assert report["imports"] is None
    assert report["imports_basis"] == bt.BASIS_LIBRARY_FAILED
    assert "ValueError" in report["imports_detail"]


def test_the_imports_basis_matches_what_this_host_actually_has(tmp_path):
    """One assertion that holds on both kinds of deployment.

    ``pefile`` is declared in requirements.txt, so a provisioned host parses;
    an air-gapped one without the wheel reports ``library_unavailable``. What
    must never happen is either one reporting ``[]``.
    """
    report = bt.triage(_write(tmp_path, "tiny.exe", _pe64(b"D" * 32)))
    installed = importlib.util.find_spec("pefile") is not None

    if installed:
        # A one-section PE has no import directory: `[]` here IS the measurement.
        assert report["imports_basis"] in (bt.BASIS_PARSED, bt.BASIS_LIBRARY_FAILED)
    else:
        assert report["imports_basis"] == bt.BASIS_LIBRARY_UNAVAILABLE
        assert report["imports"] is None


# ---------------------------------------------------------------------------
# source_unreadable
# ---------------------------------------------------------------------------


def test_a_missing_path_reports_nothing_about_the_artifact(tmp_path):
    report = bt.triage(tmp_path / "does-not-exist.bin")

    assert report["status"] == bt.STATUS_SOURCE_UNREADABLE
    assert report["reason"] == "not_a_file"
    for field in ("format", "sha256", "size", "strings", "sections", "packed_hint"):
        assert report[field] is None, field


def test_a_directory_is_source_unreadable_rather_than_an_empty_binary(tmp_path):
    assert bt.triage(tmp_path)["status"] == bt.STATUS_SOURCE_UNREADABLE


def test_triage_never_raises_on_a_hostile_path():
    """Including a non-path argument: ``Path(12345)`` raises ``TypeError``.

    The dispatcher reports a raise as ``error``, which says the analyzer broke.
    "That is not a readable artifact" is a different finding and must not wear
    the same badge. Caught by this test before it shipped.
    """
    for value in ("", "\x00", "//?/nope", 12345, None, b"bytes-not-str"):
        report = bt.triage(value)
        assert report["status"] == bt.STATUS_SOURCE_UNREADABLE, value
        assert report["reason"], value


# ---------------------------------------------------------------------------
# Entropy primitive
# ---------------------------------------------------------------------------


def test_entropy_is_none_over_no_bytes_and_zero_over_one_symbol():
    assert bt.shannon_entropy(b"") is None          # undefined, not "uniform"
    assert bt.shannon_entropy(b"A" * 100) == 0.0    # a MEASURED zero
    assert bt.shannon_entropy(bytes(range(256))) == pytest.approx(8.0)


# ---------------------------------------------------------------------------
# The contract, and the dispatch seam
# ---------------------------------------------------------------------------


def test_the_contract_validates_with_binary_and_binary_triage_declared():
    from tools.analyzers.contract import load_contract

    contract = load_contract()
    assert "binary" in contract.observable_types
    decl = next(d for d in contract.analyzers if d.key == "binary_triage")
    assert decl.module == "tools.analyzers.binary_triage"
    assert decl.entrypoint == "triage"
    assert decl.accepts == ("binary",)
    assert decl.binding.observable_arg == "path"
    # `sandboxed`, because this is the one analyzer whose observable is a set of
    # bytes somebody handed us rather than a row the platform already holds.
    assert decl.sandbox == "sandboxed"


def test_every_taxonomy_tag_the_module_emits_is_one_the_contract_declares(tmp_path):
    """Otherwise the dispatcher drops it into ``taxonomy_defects`` and goes partial."""
    from tools.analyzers.contract import load_contract

    decl = next(d for d in load_contract().analyzers if d.key == "binary_triage")
    report = bt.triage(_write(tmp_path, "sect.elf", _elf64_with_sections(b"E" * 512)))

    assert report["taxonomy"], "an analyzer that emits no tags declares its taxonomy for nothing"
    for tag in report["taxonomy"]:
        assert tag["predicate"] in decl.taxonomy.predicates
        assert tag["level"] in decl.taxonomy.levels


def test_analyzer_dispatch_over_the_fixture_returns_ok(tmp_path, monkeypatch):
    """The declared binding reaches the entrypoint and the tags survive.

    ``run_sandboxed`` is replaced with an in-process call, which is exactly what
    a sandbox image carrying the platform does (the driver runs
    ``importlib.import_module`` on the declared module inside the container).
    Measured on this host 2026-09-12, the real path reaches a real container and
    dies in ``ModuleNotFoundError: No module named 'tools'`` against the stock
    ``python:3.12-slim`` in ``args/sandbox_config.yaml`` -- the deployment
    caveat recorded under Gap 71, not something this test can paper over.
    """
    from tools.analyzers import dispatch as dispatch_mod

    def _in_process(decl, kwargs, **_ignored):
        return bt.triage(**kwargs)

    monkeypatch.setattr(dispatch_mod, "run_sandboxed", _in_process)

    path = _write(tmp_path, "dispatched.elf", _elf64_with_sections(b"F" * 256))
    result = dispatch_mod.dispatch("binary", str(path))

    report = next(r for r in result.reports if r.analyzer == "binary_triage")
    assert report.status == "ok", report.detail
    assert report.namespace == "BINARY"
    assert report.data["format"] == "elf"
    assert report.taxonomy_defects == ()
    assert any(t["predicate"] == "format" for t in report.taxonomy)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_the_cli_emits_json_carrying_format_sha256_and_strings(tmp_path):
    path = _write(tmp_path, "cli.elf", _elf64_with_sections(b"version 9.9.9 here"))
    proc = subprocess.run(
        [sys.executable, "-m", "tools.analyzers.binary_triage", str(path), "--json"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["format"] == "elf"
    assert len(report["sha256"]) == 64
    assert report["strings"]
    assert "9.9.9" in report["version_hints"]


def test_the_cli_exits_zero_on_an_unreadable_source(tmp_path):
    """"I could not read that file" IS the report, so it is not exit 2."""
    proc = subprocess.run(
        [sys.executable, "-m", "tools.analyzers.binary_triage", str(tmp_path / "nope"), "--json"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0
    assert json.loads(proc.stdout)["status"] == bt.STATUS_SOURCE_UNREADABLE


# ---------------------------------------------------------------------------
# Structural: the module opens a file and nothing else
# ---------------------------------------------------------------------------


def test_the_module_never_executes_the_artifact():
    """A behavioural test cannot see a future edit that adds one."""
    import ast

    source = (REPO_ROOT / "tools" / "analyzers" / "binary_triage.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    for forbidden in ("subprocess", "ctypes", "shutil", "tempfile", "socket"):
        assert forbidden not in imported, f"{forbidden} has no business in a triage parser"
    # The only dynamic imports are the two DECLARED parsers, and they parse.
    assert imported & {"pefile", "elftools"} == {"pefile", "elftools"}
