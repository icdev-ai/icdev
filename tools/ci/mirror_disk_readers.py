# CUI // SP-CTI
"""Who reads a file under ``icdev/tools/`` FROM DISK -- and who merely imports it?

WHY THE DISTINCTION IS THE WHOLE TOOL (mfx-own-07). ``icdev/tools/`` is 5,409
tracked files and 88.8 MB -- 27% of the files and 30% of the bytes every
``git worktree add`` writes under a 30 s budget that may not rise and may not
be retried. CLAUDE.md (xit-decl-02) says that in a SOURCE CHECKOUT the two
spellings are ONE module object: ``tools/__init__.py`` installs a meta-path
finder so ``import icdev.tools.X`` answers with the object already bound to
``tools.X``, and "the physical file is the one under tools/". If that were the
whole story the mirrored BYTES would never be the ones running in a worker
worktree, and a sparse checkout could skip them.

It is not the whole story, and the two ways it fails are DIFFERENT FINDINGS
that send a reader to different fixes, so they are never merged:

``disk_read``      code that opens / globs / stats a path under
                   ``icdev/tools/``. The path is built with SEPARATORS
                   (``icdev/tools``, ``icdev\\tools``, ``Path(...) / "icdev" /
                   "tools"``), never from the dotted import namespace.
``import_blocker`` a module the finder CANNOT alias, because aliasing needs a
                   ``tools/<rest>`` twin to alias ONTO. Two shapes, both
                   enumerated: a file that exists ONLY in the mirror, and a
                   ``tools/`` file that is a back-compat SHIM over its mirror
                   twin (``icdev._shim.is_backcompat_shim`` -- IMPORTED, never
                   a second copy of the predicate).

A dotted ``icdev.tools.x`` reference -- an import statement, an
``importlib.import_module``, a ``mock.patch`` target -- is NOT a disk read and
is reported under its own kind, so "nothing reads the mirror from disk" can
never be produced by a scanner that simply did not look at imports.

Usage::

    python tools/ci/mirror_disk_readers.py                 # human report
    python tools/ci/mirror_disk_readers.py --json
    python tools/ci/mirror_disk_readers.py --kind disk_read
    python tools/ci/mirror_disk_readers.py --imports        # the blockers

RESIDUAL IMPRECISION, stated rather than special-cased. ``_is_structural``
keys on POSITION -- a path at the head of a line or standing alone as a
literal -- so a FORMAT STRING that begins with the path ("icdev/tools mirror:
%d tracked files") is filed as a ``disk_read``. This module carries one such
line itself, alongside two constants (``MIRROR_ROOT``, ``_DISK_MARKERS``) that
genuinely ARE structural path references, exactly as
``scan_roots: [tools, icdev/tools]`` is in ``undeclared_import_census.py``.
The module is NOT exempted from its own rule: an exemption is a claim a
reviewer must check, and the alternative -- an "is this actually opened"
dataflow analysis -- is a different tool. The bucket is a candidate list that
section (c) of the survey then TESTS by running the gates for real.

Report only, no ``--gate`` (kpr-fix-03): it measures the TREE, not a diff.
Exit 0 = a report was produced, whatever it says; exit 2 = it could not be,
which is never the same as "nothing reads the mirror".
"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

try:  # the ONE statement of what a back-compat shim is (xit-decl-02)
    from icdev._shim import is_backcompat_shim
except Exception:  # pragma: no cover - reported, never silently re-implemented
    is_backcompat_shim = None  # type: ignore[assignment]

MIRROR_ROOT = "icdev/tools"
PHYSICAL_ROOT = "tools"

#: Extensions scanned as source text rather than parsed as Python.
TEXT_SUFFIXES = {".yaml", ".yml", ".json", ".toml", ".cfg", ".ini", ".txt", ".in",
                 ".ts", ".tsx", ".js", ".sh", ".ps1", ".bat"}

#: Path separators that make a reference a DISK path rather than a dotted
#: import. This is the discriminator the whole tool turns on.
_DISK_MARKERS = ("icdev/tools", "icdev\\tools")

KIND_DISK = "disk_read"
KIND_PROSE = "prose_mention"
KIND_IMPORT_REF = "import_reference"
KIND_MIRROR_ONLY = "import_blocker:mirror_only"
KIND_SHIM = "import_blocker:shim"

#: A text line whose path token is introduced by one of these is STRUCTURAL --
#: a packaging directive or a list entry -- not a sentence that mentions a path.
_STRUCTURAL_LEAD = ("-", "*", "include", "recursive-include", "exclude",
                    "recursive-exclude", "graft", "prune", "!", "#")


def _path_token(text: str) -> Optional[str]:
    """The maximal non-whitespace token in *text* carrying the mirror marker."""
    for tok in text.replace("\t", " ").split(" "):
        if any(m in tok for m in _DISK_MARKERS):
            return tok
    return None


def _is_structural(text: str, token: str) -> bool:
    """Is this reference a PATH the tooling resolves, or prose that names one?

    The discriminator is position, not vocabulary: a census entry and a
    packaging directive put the path at the head of the line (after at most a
    list marker or a key), while a sentence buries it. Getting this wrong in
    the permissive direction is what turns a survey of readers into a survey
    of everything that has ever mentioned the mirror -- on this tree that is
    the difference between ~200 findings and ~1,100.
    """
    stripped = text.strip()
    if stripped == token:
        return True
    head = stripped.split(token)[0].strip()
    if not head:
        return True
    # a key, a list marker, or a packaging directive
    if head.endswith(":") and " " not in head.rstrip(":"):
        return True
    words = head.replace(":", " ").split()
    return bool(words) and all(w.lower() in _STRUCTURAL_LEAD for w in words)


@dataclass
class Finding:
    kind: str
    file: str
    line: int
    evidence: str
    detail: str = ""


@dataclass
class Report:
    measured: bool
    reason: str = ""
    tracked_mirror_files: Optional[int] = None
    tracked_physical_files: Optional[int] = None
    findings: List[Finding] = field(default_factory=list)
    counts: dict = field(default_factory=dict)


def _repo_root() -> Path:
    # xit-decl-03: the ONE resolver. A `parents[2]` fallback here would be a
    # self-root site, and a wrong one in exactly the layout this module exists
    # to study -- under `icdev/tools/ci/` it climbs to `<repo>/icdev`.
    from icdev.core.paths import repo_root  # noqa: PLC0415
    return Path(repo_root(__file__))


def _tracked(root: Path) -> Optional[List[str]]:
    """Every git-TRACKED path, or None when git cannot answer.

    Tracked rather than walked: an untracked file is not in the checkout a
    worktree add produces, so counting it would describe a different tree
    from the one whose cost is in question.
    """
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=str(root),
                             capture_output=True, text=True, timeout=300)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return [p for p in out.stdout.split("\0") if p]


# --------------------------------------------------------------------------
# disk-read detection
# --------------------------------------------------------------------------

def _const_str(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _joined_segments(node: ast.AST) -> List[str]:
    """Flatten an ``a / "icdev" / "tools"`` chain into its string constants."""
    out: List[str] = []
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Div):
            stack.extend([cur.left, cur.right])
        else:
            s = _const_str(cur)
            if s is not None:
                out.append(s)
    return out


def _line(lines: List[str], n: int) -> str:
    return lines[n - 1].strip()[:160] if 0 < n <= len(lines) else ""


def _scan_python(path: Path, rel: str) -> Iterable[Finding]:
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    if ("icdev" not in src) or ("tools" not in src):
        return
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return
    lines = src.splitlines()
    seen = set()

    # Docstrings are prose by construction. Collected up front so a module
    # explaining that it does NOT read the mirror is not filed as a reader --
    # the failure mode a naive grep hits (this module's own docstring names
    # the path five times).
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            docstrings.add(id(node.value))

    for node in ast.walk(tree):
        # Import STATEMENTS are never a disk read. Reported under their own
        # kind so an empty disk_read list cannot be mistaken for "the mirror
        # is referenced nowhere".
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = getattr(node, "module", None) or ""
            names = [a.name for a in getattr(node, "names", [])]
            if mod.startswith("icdev.tools") or any(n.startswith("icdev.tools") for n in names):
                yield Finding(KIND_IMPORT_REF, rel, node.lineno, _line(lines, node.lineno),
                              "import statement -- aliased by icdev/_shim.py; reads no mirror file")
            continue

        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if any(m in v for m in _DISK_MARKERS):
                key = (node.lineno, "s")
                if key not in seen:
                    seen.add(key)
                    tok = _path_token(v) or v
                    if id(node) in docstrings:
                        yield Finding(KIND_PROSE, rel, node.lineno, v.strip()[:160],
                                      "docstring -- prose, not a resolved path")
                    elif _is_structural(v, tok):
                        yield Finding(KIND_DISK, rel, node.lineno, v.strip()[:160],
                                      "string literal carrying a path separator")
                    else:
                        yield Finding(KIND_PROSE, rel, node.lineno, v.strip()[:160],
                                      "the path is embedded in a sentence")
            elif v.startswith("icdev.tools"):
                key = (node.lineno, "d")
                if key not in seen:
                    seen.add(key)
                    yield Finding(KIND_IMPORT_REF, rel, node.lineno, v[:160],
                                  "dotted module name in a string (patch target / import_module)")

        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            segs = _joined_segments(node)
            if "icdev" in segs and "tools" in segs:
                key = (node.lineno, "j")
                if key not in seen:
                    seen.add(key)
                    yield Finding(KIND_DISK, rel, node.lineno, _line(lines, node.lineno),
                                  "Path-join chain over the segments icdev / tools")

        elif isinstance(node, ast.Call):
            args = [_const_str(a) for a in node.args]
            for i in range(len(args) - 1):
                if args[i] == "icdev" and args[i + 1] == "tools":
                    key = (node.lineno, "c")
                    if key not in seen:
                        seen.add(key)
                        yield Finding(KIND_DISK, rel, node.lineno, _line(lines, node.lineno),
                                      "adjacent 'icdev', 'tools' path segments in a call")
                    break


def _scan_text(path: Path, rel: str) -> Iterable[Finding]:
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for n, line in enumerate(src.splitlines(), 1):
        if not any(m in line for m in _DISK_MARKERS):
            continue
        tok = _path_token(line) or line
        kind = KIND_DISK if _is_structural(line, tok) else KIND_PROSE
        yield Finding(kind, rel, n, line.strip()[:160],
                      "%s %s" % (path.suffix or path.name,
                                 "entry/directive" if kind == KIND_DISK
                                 else "prose naming a path"))


# --------------------------------------------------------------------------
# import blockers
# --------------------------------------------------------------------------

def _import_blockers(root: Path, tracked: List[str]) -> List[Finding]:
    mirror = {p[len("icdev/"):] for p in tracked if p.startswith(MIRROR_ROOT + "/")}
    physical = {p for p in tracked if p.startswith(PHYSICAL_ROOT + "/")}
    out: List[Finding] = []

    for rel in sorted(mirror - physical):
        if not rel.endswith(".py"):
            continue
        out.append(Finding(KIND_MIRROR_ONLY, "icdev/" + rel, 0, rel,
                           "exists ONLY in the mirror -- the finder has no tools/ twin to "
                           "alias onto, so removing the mirror removes the module"))

    if is_backcompat_shim is None:
        out.append(Finding(KIND_SHIM, "icdev/_shim.py", 0, "",
                           "UNMEASURABLE: icdev._shim.is_backcompat_shim could not be "
                           "imported, so the shim set was NOT derived -- this is not "
                           "'there are no shims'"))
        return out

    for rel in sorted(mirror & physical):
        if not rel.endswith(".py") or rel.endswith("__init__.py"):
            continue
        dotted = rel[len(PHYSICAL_ROOT) + 1:-3].replace("/", ".")
        try:
            if is_backcompat_shim(root / rel, dotted, root / "icdev" / rel):
                out.append(Finding(KIND_SHIM, rel, 0, dotted,
                                   "tools/ file is a re-export SHIM; the implementation is "
                                   "ONLY in icdev/tools/, so BOTH names resolve to the mirror"))
        except Exception:
            continue
    return out


# --------------------------------------------------------------------------

def collect(root: Path) -> Report:
    tracked = _tracked(root)
    if tracked is None:
        return Report(measured=False, reason="git ls-files could not be read")
    mirror_n = sum(1 for p in tracked if p.startswith(MIRROR_ROOT + "/"))
    physical_n = sum(1 for p in tracked if p.startswith(PHYSICAL_ROOT + "/"))
    if mirror_n == 0:
        return Report(measured=False,
                      reason="no tracked files under %s/ -- this checkout has no mirror to "
                             "measure (a sparse worktree, or a wheel)" % MIRROR_ROOT)

    findings: List[Finding] = []
    for rel in tracked:
        # A file INSIDE the mirror referring to the mirror is the mirrored copy
        # of a tools/ finding, not a second reader.
        if rel.startswith("icdev/"):
            continue
        p = root / rel
        if not p.is_file():
            continue
        if rel.endswith(".py"):
            findings.extend(_scan_python(p, rel))
        elif p.suffix in TEXT_SUFFIXES:
            findings.extend(_scan_text(p, rel))

    findings.extend(_import_blockers(root, tracked))

    counts: dict = {}
    for f in findings:
        counts[f.kind] = counts.get(f.kind, 0) + 1
    return Report(measured=True, tracked_mirror_files=mirror_n,
                  tracked_physical_files=physical_n,
                  findings=findings, counts=counts)


def _human(rep: Report, kinds) -> str:
    if not rep.measured:
        return "UNMEASURABLE: %s\nThis is not a clean bill of health." % rep.reason
    out = ["icdev/tools mirror: %d tracked files (tools/: %d)"
           % (rep.tracked_mirror_files, rep.tracked_physical_files), ""]
    for k in kinds:
        rows = [f for f in rep.findings if f.kind == k]
        out.append("%s: %d" % (k, len(rows)))
        by_file: dict = {}
        for f in rows:
            by_file.setdefault(f.file, []).append(f)
        for fl in sorted(by_file):
            ln = ",".join(str(x.line) for x in by_file[fl][:8] if x.line)
            out.append("    %s%s" % (fl, (":" + ln) if ln else ""))
        out.append("")
    return "\n".join(out)


ALL_KINDS = (KIND_DISK, KIND_MIRROR_ONLY, KIND_SHIM, KIND_PROSE, KIND_IMPORT_REF)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Enumerate readers of the icdev/tools mirror")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--kind", choices=list(ALL_KINDS))
    ap.add_argument("--imports", action="store_true",
                    help="show the import blockers only (mirror-only modules + shims)")
    ap.add_argument("--root", default=None)
    args = ap.parse_args(argv)

    root = Path(args.root) if args.root else _repo_root()
    rep = collect(root)
    if args.json:
        payload = asdict(rep)
        payload["findings"] = [asdict(f) for f in rep.findings
                               if not args.kind or f.kind == args.kind]
        print(json.dumps(payload, indent=2))
    elif args.imports:
        print(_human(rep, (KIND_MIRROR_ONLY, KIND_SHIM)))
    else:
        print(_human(rep, (args.kind,) if args.kind else ALL_KINDS))
    return 0 if rep.measured else 2


if __name__ == "__main__":
    sys.exit(main())
