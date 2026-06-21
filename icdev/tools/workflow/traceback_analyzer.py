# CUI // SP-CTI
"""Deterministic Python traceback analyzer — no LLM.

Parses a blob of verification / build / test output and extracts the
exception class, its message, and an ordered list of stack frames. Each
frame records the source ``file``, ``line``, ``function``, the offending
``code_line`` (when present), and whether the file lives ``in_repo``.

The *primary suspect* is the **deepest in-repo frame** — i.e. the last
in-repo frame in call order. External library / stdlib frames are kept in
the list (with ``in_repo=False``) but are never chosen as the suspect when
a first-party frame exists.

Tolerated input formats:
  * Standard CPython tracebacks (``Traceback (most recent call last):``).
  * Chained tracebacks (``During handling of the above exception...`` and
    ``The above exception was the direct cause...``). Frames from every
    segment are concatenated in textual order, so the deepest in-repo frame
    of the *final* exception wins as the suspect. The reported exception
    type / message come from the final (propagated) exception.
  * pytest long-style frames (``path/to/test.py:42: in test_foo``) and the
    pytest summary line (``path/to/test.py:42: AssertionError``).
  * pytest ``E   ExceptionType: message`` exception lines.
  * unittest and playwright(-python) output, which both surface standard
    ``File "...", line N, in func`` frames.

Robustness contract: ``parse_traceback`` NEVER raises on malformed input —
it returns an empty :class:`Traceback` (no exception type, no frames) when
nothing matches.

Public API:
    parse_traceback(text, repo_root=None) -> Traceback
    primary_suspect(tb) -> "path:line" | None
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Path fragments that mark a frame as *not* first-party (stdlib / 3rd-party
# / interpreter-internal). Matched case-insensitively against the raw path.
_EXTERNAL_MARKERS = (
    "site-packages",
    "dist-packages",
    "/lib/python",
    "\\lib\\python",
    "/usr/lib/python",
    "/usr/local/lib/python",
    "lib/python3",
    "lib\\python3",
    "<frozen",
    "<string>",
    "<stdin>",
    "/.venv/",
    "\\.venv\\",
    "/venv/",
    "\\venv\\",
    "/envs/",
    "\\envs\\",
    "python27.zip",
    "/conda",
    "\\conda",
)

# Standard CPython frame:  File "<path>", line <n>, in <func>
_FILE_FRAME_RE = re.compile(
    r'^\s*File "(?P<file>.+?)", line (?P<line>\d+), in (?P<func>.+?)\s*$'
)

# pytest long-style frame:  path/to/file.py:42: in func_name
_PYTEST_FRAME_RE = re.compile(
    r"^(?P<file>(?:[A-Za-z]:)?[^\s:][^:]*?\.py):(?P<line>\d+): in (?P<func>.+?)\s*$"
)

# pytest summary line:  path/to/file.py:42: ExceptionType[: message]
_PYTEST_SUMMARY_RE = re.compile(
    r"^(?P<file>(?:[A-Za-z]:)?[^\s:][^:]*?\.py):(?P<line>\d+): "
    r"(?P<type>[A-Za-z_][\w.]*(?:Error|Exception|Warning|Exit|Interrupt|"
    r"Timeout|Failed|Failure|NotFound))\b\s*(?P<msg>.*)$"
)

# Bare exception line at the bottom of a traceback:  ExceptionType: message
# (optionally prefixed by pytest's "E   "). The type must look like a dotted
# identifier; a trailing ": message" is optional (e.g. KeyboardInterrupt).
_EXC_LINE_RE = re.compile(
    r"^(?:E\s+)?(?P<type>[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*)"
    r"(?:: (?P<msg>.*))?\s*$"
)

# Chained-traceback separators — each starts a new exception segment.
_CHAIN_SEPARATORS = (
    "During handling of the above exception, another exception occurred:",
    "The above exception was the direct cause of the following exception:",
)


@dataclass
class Frame:
    """A single stack frame extracted from a traceback."""

    file: str
    line: int
    function: str
    code_line: str = ""
    in_repo: bool = False


@dataclass
class Traceback:
    """Structured result of parsing a traceback blob."""

    exception_type: str = ""
    exception_message: str = ""
    frames: List[Frame] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        """True if anything meaningful was extracted."""
        return bool(self.exception_type or self.frames)

    def to_dict(self) -> dict:
        return asdict(self)


def _is_in_repo(raw_path: str, repo_root: Path) -> bool:
    """Decide whether ``raw_path`` refers to a first-party repo file.

    Heuristic, filesystem-tolerant:
      * stdlib / site-packages / interpreter-internal paths -> False
      * absolute paths -> in_repo iff under ``repo_root``
      * relative paths (pytest style) -> in_repo (they are repo-relative),
        confirmed by existence under ``repo_root`` when possible.
    """
    if not raw_path:
        return False
    low = raw_path.replace("\\", "/").lower()
    for marker in _EXTERNAL_MARKERS:
        if marker.replace("\\", "/") in low:
            return False
    if raw_path.startswith("<"):  # <frozen ...>, <string>, ...
        return False

    p = Path(raw_path)
    if p.is_absolute():
        try:
            p.resolve().relative_to(repo_root.resolve())
            return True
        except (ValueError, OSError):
            return False

    # Relative path: pytest emits repo-relative paths. Confirm by existence
    # when we can, but default to True (it is not an external marker).
    try:
        if (repo_root / raw_path).exists():
            return True
    except OSError:
        pass
    return True


def _attach_in_repo(frame: Frame, repo_root: Path) -> Frame:
    frame.in_repo = _is_in_repo(frame.file, repo_root)
    return frame


def _grab_code_line(lines: List[str], idx: int) -> str:
    """Return the offending source line that follows a frame header.

    The code line is the next non-empty, indented line that is not itself a
    new frame header or exception line. Returns "" when absent.
    """
    j = idx + 1
    while j < len(lines):
        nxt = lines[j]
        if not nxt.strip():
            j += 1
            continue
        # A following frame header / summary means there is no code line.
        if (
            _FILE_FRAME_RE.match(nxt)
            or _PYTEST_FRAME_RE.match(nxt)
            or _PYTEST_SUMMARY_RE.match(nxt)
        ):
            return ""
        # pytest prefixes the offending line with ">" and the exception with
        # "E"; the actual code line is indented and is neither.
        stripped = nxt.lstrip()
        if stripped.startswith("E "):
            return ""
        return stripped.lstrip("> ").strip()
    return ""


def parse_traceback(text: str, repo_root: Optional[Path] = None) -> Traceback:
    """Parse ``text`` into a :class:`Traceback`. Never raises.

    Args:
        text: Arbitrary build / test output that may contain one or more
            (possibly chained) Python tracebacks.
        repo_root: Root used for the ``in_repo`` decision. Defaults to the
            ICDEV repository root.

    Returns:
        A :class:`Traceback`. ``matched`` is False / fields are empty when
        nothing parseable is found.
    """
    tb = Traceback()
    if not text or not isinstance(text, str):
        return tb
    repo_root = repo_root or BASE_DIR

    try:
        lines = text.splitlines()
        frames: List[Frame] = []
        # Track exception (type, msg) candidates in textual order; the final
        # one is the propagated exception we report.
        exc_candidates: List[tuple] = []

        for idx, line in enumerate(lines):
            m = _FILE_FRAME_RE.match(line)
            if m:
                frames.append(
                    _attach_in_repo(
                        Frame(
                            file=m.group("file"),
                            line=int(m.group("line")),
                            function=m.group("func").strip(),
                            code_line=_grab_code_line(lines, idx),
                        ),
                        repo_root,
                    )
                )
                continue

            m = _PYTEST_FRAME_RE.match(line)
            if m:
                frames.append(
                    _attach_in_repo(
                        Frame(
                            file=m.group("file"),
                            line=int(m.group("line")),
                            function=m.group("func").strip(),
                            code_line=_grab_code_line(lines, idx),
                        ),
                        repo_root,
                    )
                )
                continue

            # pytest summary line carries BOTH a frame and the exception type.
            m = _PYTEST_SUMMARY_RE.match(line)
            if m:
                frames.append(
                    _attach_in_repo(
                        Frame(
                            file=m.group("file"),
                            line=int(m.group("line")),
                            function="",
                        ),
                        repo_root,
                    )
                )
                exc_candidates.append(
                    (m.group("type"), (m.group("msg") or "").strip())
                )
                continue

            # Bare / pytest-"E" exception line. Only accept when it is not an
            # indented code line: standard tracebacks place it at column 0;
            # pytest uses the "E   " prefix.
            stripped = line.strip()
            if not stripped or stripped in _CHAIN_SEPARATORS:
                continue
            is_pytest_e = bool(re.match(r"^E\s+\S", line))
            is_col0 = line[:1] not in (" ", "\t")
            if is_pytest_e or is_col0:
                em = _EXC_LINE_RE.match(line)
                if em and em.group("type") not in ("Traceback", "File"):
                    # Require a recognizable exception name OR an explicit
                    # message to avoid swallowing arbitrary prose lines.
                    etype = em.group("type")
                    emsg = (em.group("msg") or "").strip()
                    looks_excy = (
                        etype.endswith("Error")
                        or etype.endswith("Exception")
                        or etype.endswith("Warning")
                        or etype in ("KeyboardInterrupt", "SystemExit",
                                     "StopIteration", "GeneratorExit")
                        or "." in etype  # dotted, e.g. socket.timeout
                        or is_pytest_e
                    )
                    if looks_excy:
                        exc_candidates.append((etype, emsg))

        tb.frames = frames
        if exc_candidates:
            tb.exception_type, tb.exception_message = exc_candidates[-1]
    except Exception as exc:  # never raise — tolerant by contract
        logger.debug("parse_traceback: tolerated parse error: %s", exc)
        return Traceback()

    return tb


def primary_suspect(tb: Traceback) -> Optional[str]:
    """Return ``"path:line"`` of the deepest in-repo frame, or None.

    The deepest frame is the last in call order (most-recent-call-last). When
    no in-repo frame exists, falls back to the deepest frame of any kind; when
    there are no frames at all, returns None.
    """
    if not tb or not tb.frames:
        return None
    for frame in reversed(tb.frames):
        if frame.in_repo:
            return f"{frame.file}:{frame.line}"
    deepest = tb.frames[-1]
    return f"{deepest.file}:{deepest.line}"


def _main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Parse a Python traceback into structured frames."
    )
    ap.add_argument(
        "--file", "-f", help="Read traceback text from FILE (default: stdin)."
    )
    ap.add_argument(
        "--repo-root", help="Repository root for in_repo detection."
    )
    ap.add_argument("--json", action="store_true", help="Emit JSON.")
    args = ap.parse_args(argv)

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    else:
        text = sys.stdin.read()

    root = Path(args.repo_root) if args.repo_root else None
    tb = parse_traceback(text, repo_root=root)
    suspect = primary_suspect(tb)

    if args.json:
        out = tb.to_dict()
        out["primary_suspect"] = suspect
        out["matched"] = tb.matched
        print(json.dumps(out, indent=2))
    else:
        if not tb.matched:
            print("No traceback found.")
            return 1
        print(f"Exception: {tb.exception_type}: {tb.exception_message}".rstrip(": "))
        print(f"Primary suspect: {suspect or '(none)'}")
        print(f"Frames ({len(tb.frames)}):")
        for fr in tb.frames:
            tag = "*" if fr.in_repo else " "
            print(f"  [{tag}] {fr.file}:{fr.line} in {fr.function}")
            if fr.code_line:
                print(f"        {fr.code_line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
