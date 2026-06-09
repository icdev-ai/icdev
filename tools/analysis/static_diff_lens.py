#!/usr/bin/env python3
# CUI // SP-CTI
"""Static-analysis-on-diff — bounded CodeLens run over CHANGED FUNCTIONS.

Picks up where ``tools.analysis.code_lens`` stops: that module reports a
whole-file gate. The autofix debugger needs per-function findings so the
LLM diagnosis can localize the bug to a specific function instead of a
specific file. This module:

  1. Computes ``git diff main..kanban/<task>`` (or any branch range).
  2. Parses the diff to extract the set of *changed* line ranges per file.
  3. Loads the post-image of each changed file and uses the AST to map
     every changed line to the enclosing function/method (when any).
  4. Runs ``CodeAnalyzer.analyze_python_file`` on each changed file, then
     filters the per-function metrics to those whose function range
     overlaps the diff.
  5. Augments findings with three diff-aware detectors that pure code_lens
     cannot produce: None-deref risk, missing-return, and an off-by-one
     heuristic for changed comparison/assignment sites.
  6. Returns a deterministic, JSON-serializable structure intended to be
     dropped into the EvidenceBundle / diagnose prompt as a ``static_findings``
     key.

Bounding: the analysis is capped at ``--max-files`` (default 25) and
``--max-findings`` (default 50) to stay cheap. Non-Python files are
counted but skipped for AST analysis (code_lens supports them via
``analyze_non_python_file``; we record the file but do not surface
per-function findings for them — they go in ``files_analyzed`` as
skipped=true).

Usage:
    python tools/analysis/static_diff_lens.py --task-id arc-dbg-01 --json
    python tools/analysis/static_diff_lens.py --base main --head HEAD --json
    python tools/analysis/static_diff_lens.py --task-id arc-dbg-01 --max-files 10 --json
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


# ---------------------------------------------------------------------------
# Diff parsing
# ---------------------------------------------------------------------------

_HUNK_RE = re.compile(
    r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@",
)


@dataclass
class ChangedRange:
    """Inclusive 1-based line range in the post-image that was changed."""

    start: int
    end: int

    def overlaps(self, other_start: int, other_end: int) -> bool:
        return not (self.end < other_start or self.start > other_end)


@dataclass
class FileDiff:
    """Diff metadata for one file: which ranges of the post-image changed."""

    path: str  # repo-relative
    status: str  # "M" | "A" | "D" | "R" | "C"
    post_ranges: List[ChangedRange] = field(default_factory=list)
    pre_ranges: List[ChangedRange] = field(default_factory=list)


def _parse_diff(diff_text: str) -> List[FileDiff]:
    """Parse ``git diff`` output into per-file ChangedRange lists.

    Handles ``diff --git``, ``rename``, ``copy``, ``new file``,
    ``deleted file``, and standard ``@@`` hunks. Context lines are
    counted toward the post-image line number (so a function whose
    signature is unchanged but whose body changed is still picked up
    via the body lines).
    """
    files: List[FileDiff] = []
    current: Optional[FileDiff] = None
    in_hunk = False
    post_line = 0
    pre_line = 0

    for raw in diff_text.splitlines():
        line = raw
        if line.startswith("diff --git "):
            if current is not None:
                files.append(current)
            # diff --git a/<path> b/<path>
            m = re.match(r"diff --git a/(.+?) b/(.+?)$", line)
            path = m.group(2) if m else ""
            current = FileDiff(path=path, status="M")
            in_hunk = False
            continue
        if current is None:
            continue
        if line.startswith("new file mode"):
            current.status = "A"
            continue
        if line.startswith("deleted file mode"):
            current.status = "D"
            continue
        if line.startswith("rename "):
            current.status = "R"
            continue
        if line.startswith("copy "):
            current.status = "C"
            continue
        if line.startswith("index ") or line.startswith("similarity ") \
                or line.startswith("dissimilarity ") or line.startswith("Binary "):
            continue
        if line.startswith("--- ") or line.startswith("+++ "):
            continue
        if line.startswith("@@"):
            m = _HUNK_RE.match(line)
            if not m:
                in_hunk = False
                continue
            pre_start = int(m.group(1))
            pre_count = int(m.group(2) or "1")
            post_start = int(m.group(3))
            post_count = int(m.group(4) or "1")
            pre_line = pre_start
            post_line = post_start
            in_hunk = True
            # Track the entire hunk as "changed" (post image)
            if post_count > 0 and current.status != "D":
                current.post_ranges.append(
                    ChangedRange(start=post_start, end=post_start + max(post_count, 1) - 1)
                )
            if pre_count > 0 and current.status != "A":
                current.pre_ranges.append(
                    ChangedRange(start=pre_start, end=pre_start + max(pre_count, 1) - 1)
                )
            continue
        if not in_hunk:
            continue
        if line.startswith("+"):
            current.post_ranges.append(ChangedRange(start=post_line, end=post_line))
            post_line += 1
        elif line.startswith("-"):
            pre_line += 1
        else:
            # Context line — increments both
            pre_line += 1
            post_line += 1
    if current is not None:
        files.append(current)
    return files


def _coalesce_ranges(ranges: List[ChangedRange]) -> List[ChangedRange]:
    """Merge overlapping/adjacent ranges so a single diff hunk = one range."""
    if not ranges:
        return []
    sorted_r = sorted(ranges, key=lambda r: r.start)
    merged = [ChangedRange(start=sorted_r[0].start, end=sorted_r[0].end)]
    for r in sorted_r[1:]:
        last = merged[-1]
        if r.start <= last.end + 1:
            last.end = max(last.end, r.end)
        else:
            merged.append(ChangedRange(start=r.start, end=r.end))
    return merged


def get_diff(base: str, head: str, cwd: Path) -> List[FileDiff]:
    """Run ``git diff`` between two refs and parse the result.

    Tries ``git diff <base>..<head>`` first; falls back to
    ``git diff <base>...<head>`` if the three-dot form is needed. Returns
    an empty list on any subprocess failure (we never raise — the
    snapshot caller expects graceful degradation).
    """
    for ref in (f"{base}..{head}", f"{base}...{head}"):
        try:
            r = subprocess.run(
                ["git", "diff", "--no-color", "--unified=0", ref],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        except Exception:
            continue
        if r.returncode == 0 and r.stdout:
            files = _parse_diff(r.stdout)
            for f in files:
                f.post_ranges = _coalesce_ranges(f.post_ranges)
                f.pre_ranges = _coalesce_ranges(f.pre_ranges)
            return files
    return []


# ---------------------------------------------------------------------------
# AST helpers — map line ranges to enclosing function/method
# ---------------------------------------------------------------------------

@dataclass(eq=False)
class FunctionSpan:
    name: str
    class_name: Optional[str]
    start: int  # 1-based, inclusive (def line)
    end: int    # 1-based, inclusive (last stmt line)

    def __hash__(self) -> int:
        return hash((self.class_name, self.name, self.start, self.end))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FunctionSpan):
            return NotImplemented
        return (
            self.class_name == other.class_name
            and self.name == other.name
            and self.start == other.start
            and self.end == other.end
        )

    def contains_line(self, line: int) -> bool:
        return self.start <= line <= self.end


def _function_spans_for_file(source: str) -> List[FunctionSpan]:
    """Return top-level + nested function/method spans in a Python source.

    For diff analysis, nested defs are INCLUDED in the enclosing
    function's range (because the inner def lines are within the outer
    body). A future enhancement could surface nested defs separately.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    spans: List[FunctionSpan] = []

    def visit(node: ast.AST, class_name: Optional[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end = getattr(child, "end_lineno", None) or child.lineno
                spans.append(
                    FunctionSpan(
                        name=child.name,
                        class_name=class_name,
                        start=child.lineno,
                        end=end,
                    )
                )
                # Recurse for nested defs so closures get captured
                visit(child, class_name=class_name)
            elif isinstance(child, ast.ClassDef):
                for grand in ast.iter_child_nodes(child):
                    if isinstance(grand, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        end = getattr(grand, "end_lineno", None) or grand.lineno
                        spans.append(
                            FunctionSpan(
                                name=grand.name,
                                class_name=child.name,
                                start=grand.lineno,
                                end=end,
                            )
                        )
                        # Nested defs inside methods
                        visit(grand, class_name=child.name)
                # Also capture nested defs at class body level (unusual)
                visit(child, class_name=None)

    visit(tree, class_name=None)
    return spans


def _span_intersection(
    spans: List[FunctionSpan], ranges: List[ChangedRange]
) -> List[Tuple[FunctionSpan, ChangedRange]]:
    """For each (span, range) pair that overlaps, emit a tuple."""
    if not spans or not ranges:
        return []
    hits: List[Tuple[FunctionSpan, ChangedRange]] = []
    for span in spans:
        for rng in ranges:
            if rng.overlaps(span.start, span.end):
                hits.append((span, rng))
    return hits


# ---------------------------------------------------------------------------
# Diff-aware detectors
# ---------------------------------------------------------------------------

# Heuristic: lines that introduce a None comparison / None assignment /
# None return are flagged as a None-deref risk if the diff also touches
# a deref site (obj.attr / obj[key] / obj()).
_NONE_TOKENS = re.compile(r"\bNone\b")
_DEREF_TOKENS = re.compile(r"\.(?:[A-Za-z_]\w*)\s*[([]|\)\s*$")

# Off-by-one heuristic: changed < / <= / > / >= comparison or slice.
_OBO_TOKENS = re.compile(r"^\s*[-+].*\b(?:<\s*=|>\s*=|<|>|range|len\()")


@dataclass
class Finding:
    file: str
    function: str
    class_name: Optional[str]
    function_start: int
    function_end: int
    diff_range: Tuple[int, int]
    kind: str  # one of: complexity_spike, unhandled_branch, none_deref_risk, off_by_one, missing_return, maintainability_low, long_function, deep_nesting, high_complexity
    severity: str  # "info" | "warning" | "critical"
    detail: str
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["diff_range"] = list(self.diff_range)
        return d


def _detect_diff_smells(
    file_path: str,
    source: str,
    spans: List[FunctionSpan],
    ranges: List[ChangedRange],
) -> List[Finding]:
    """Diff-aware detectors that operate on the *changed lines only*.

    These complement code_lens by flagging issues that only matter in
    the context of the new edit. We DO NOT raise findings for code that
    was unchanged in the diff.
    """
    findings: List[Finding] = []
    src_lines = source.splitlines()

    for span, rng in _span_intersection(spans, ranges):
        for ln in range(max(rng.start, span.start), min(rng.end, span.end) + 1):
            if 1 <= ln <= len(src_lines):
                line_text = src_lines[ln - 1]
                if _OBO_TOKENS.match(line_text) and re.search(r"[<>]=?|range\(|len\(", line_text):
                    findings.append(
                        Finding(
                            file=file_path,
                            function=span.name,
                            class_name=span.class_name,
                            function_start=span.start,
                            function_end=span.end,
                            diff_range=(rng.start, rng.end),
                            kind="off_by_one",
                            severity="warning",
                            detail=f"line {ln}: comparison/slice changed — verify boundary ({line_text.strip()[:80]})",
                            metrics={"line": ln},
                        )
                    )
                if _NONE_TOKENS.search(line_text) and _DEREF_TOKENS.search(line_text):
                    findings.append(
                        Finding(
                            file=file_path,
                            function=span.name,
                            class_name=span.class_name,
                            function_start=span.start,
                            function_end=span.end,
                            diff_range=(rng.start, rng.end),
                            kind="none_deref_risk",
                            severity="warning",
                            detail=f"line {ln}: None compared and deref pattern nearby — ensure None-check precedes use",
                            metrics={"line": ln},
                        )
                    )
    return findings


def _detect_missing_return(
    file_path: str,
    source: str,
    spans: List[FunctionSpan],
    ranges: List[ChangedRange],
) -> List[Finding]:
    """Flag functions whose body changed and which lack an obvious return.

    A function with a non-None return annotation (or is named like a
    predicate/getter) and no `return` statement in the new body is a
    missing-return smell — frequently the cause of cascading NoneType
    errors far from the actual edit.
    """
    findings: List[Finding] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return findings

    func_by_line: Dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", None) or node.lineno
            for ln in range(node.lineno, end + 1):
                func_by_line[ln] = node

    for span, rng in _span_intersection(spans, ranges):
        node = func_by_line.get(span.start)
        if node is None:
            continue
        # Only flag non-void functions
        is_void = (
            node.returns is None
            and not node.name.startswith("get_")
            and not node.name.startswith("is_")
            and not node.name.startswith("has_")
            and not node.name.startswith("can_")
            and not node.name.startswith("should_")
            and not node.name.startswith("compute_")
        )
        if is_void:
            continue
        # Walk body for any return
        for child in ast.walk(node):
            if isinstance(child, ast.Return):
                break
        else:
            findings.append(
                Finding(
                    file=file_path,
                    function=span.name,
                    class_name=span.class_name,
                    function_start=span.start,
                    function_end=span.end,
                    diff_range=(rng.start, rng.end),
                    kind="missing_return",
                    severity="warning",
                    detail=(
                        f"function '{span.name}' body changed but no return statement found; "
                        "may return None implicitly and crash callers"
                    ),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Per-file runner — wraps CodeAnalyzer for the changed function set
# ---------------------------------------------------------------------------

def _run_codelens_for_file(
    file_path: Path,
    spans: List[FunctionSpan],
    ranges: List[ChangedRange],
) -> List[Finding]:
    """Run code_analyzer on a single file, filter to changed functions.

    Each function metric from the analyzer is converted into a Finding
    (one per smell kind, plus a maintainability-low / complexity-spike
    finding when the metric crosses thresholds). Findings carry the
    function range + diff range so the LLM can quote them directly.
    """
    from tools.analysis.code_analyzer import CodeAnalyzer

    findings: List[Finding] = []
    try:
        analyzer = CodeAnalyzer(project_dir=str(file_path.parent))
        metrics = analyzer.analyze_python_file(file_path)
    except Exception as exc:
        return [
            Finding(
                file=str(file_path),
                function="<module>",
                class_name=None,
                function_start=0,
                function_end=0,
                diff_range=(0, 0),
                kind="unhandled_branch",
                severity="warning",
                detail=f"analyzer error: {exc}",
            )
        ]

    # metric entries for changed functions only
    hit_spans = {span for span, _ in _span_intersection(spans, ranges)}
    for m in metrics:
        if not m.get("function_name"):
            continue  # file-level aggregate, skip
        if m.get("class_name") and m.get("function_name"):
            qualified = f"{m['class_name']}.{m['function_name']}"
        else:
            qualified = m["function_name"]
        # find matching span
        match_span: Optional[FunctionSpan] = None
        for s in hit_spans:
            if s.class_name == m.get("class_name") and s.name == m["function_name"]:
                match_span = s
                break
        if match_span is None:
            continue
        # Find the diff range for this specific function
        diff_range: Tuple[int, int] = (match_span.start, match_span.start)
        for s, r in _span_intersection([match_span], ranges):
            diff_range = (r.start, r.end)
            break

        smells = []
        try:
            smells = json.loads(m.get("smells_json") or "[]")
        except Exception:
            smells = []
        cc = m.get("cyclomatic_complexity", 0)
        cog = m.get("cognitive_complexity", 0)
        loc = m.get("loc", 0)
        nest = m.get("nesting_depth", 0)
        maint = m.get("maintainability_score", 1.0)

        if "high_complexity" in smells or cc >= 15:
            findings.append(
                Finding(
                    file=str(file_path),
                    function=qualified,
                    class_name=m.get("class_name"),
                    function_start=match_span.start,
                    function_end=match_span.end,
                    diff_range=diff_range,
                    kind="complexity_spike",
                    severity="warning" if cc < 25 else "critical",
                    detail=f"cyclomatic_complexity={cc}, cognitive={cog}",
                    metrics={"cc": cc, "cog": cog},
                )
            )
        if "long_function" in smells or loc >= 50:
            findings.append(
                Finding(
                    file=str(file_path),
                    function=qualified,
                    class_name=m.get("class_name"),
                    function_start=match_span.start,
                    function_end=match_span.end,
                    diff_range=diff_range,
                    kind="long_function",
                    severity="warning" if loc < 100 else "critical",
                    detail=f"loc={loc}",
                    metrics={"loc": loc},
                )
            )
        if "deep_nesting" in smells or nest >= 4:
            findings.append(
                Finding(
                    file=str(file_path),
                    function=qualified,
                    class_name=m.get("class_name"),
                    function_start=match_span.start,
                    function_end=match_span.end,
                    diff_range=diff_range,
                    kind="deep_nesting",
                    severity="warning",
                    detail=f"nesting_depth={nest}",
                    metrics={"nesting_depth": nest},
                )
            )
        if maint < 0.4:
            findings.append(
                Finding(
                    file=str(file_path),
                    function=qualified,
                    class_name=m.get("class_name"),
                    function_start=match_span.start,
                    function_end=match_span.end,
                    diff_range=diff_range,
                    kind="maintainability_low",
                    severity="critical",
                    detail=f"maintainability_score={maint:.2f}",
                    metrics={"maintainability": maint},
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

@dataclass
class StaticDiffResult:
    task_id: str
    base: str
    head: str
    files_in_diff: int
    files_analyzed: List[Dict[str, Any]] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)
    summary: Dict[str, int] = field(default_factory=dict)
    truncated: bool = False
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "base": self.base,
            "head": self.head,
            "files_in_diff": self.files_in_diff,
            "files_analyzed": self.files_analyzed,
            "findings": [f.to_dict() for f in self.findings],
            "summary": self.summary,
            "truncated": self.truncated,
            "notes": self.notes,
        }


def run(
    task_id: str = "",
    base: str = "main",
    head: Optional[str] = None,
    cwd: Optional[Path] = None,
    max_files: int = 25,
    max_findings: int = 50,
) -> StaticDiffResult:
    """Run code_lens on the functions changed in a diff.

    Args:
        task_id: Kanban task ID (used for branch resolution when ``head`` is
            omitted — defaults to ``kanban/<task_id>``).
        base: Base ref for the diff (default ``main``).
        head: Head ref; defaults to ``kanban/<task_id>`` or the current
            HEAD if task_id is empty.
        cwd: Working directory (defaults to ``BASE_DIR``).
        max_files: Cap on files analyzed (default 25).
        max_findings: Cap on findings surfaced (default 50).

    Returns:
        StaticDiffResult — dataclass with ``to_dict()`` for JSON.
    """
    cwd = cwd or BASE_DIR
    if not head:
        if task_id:
            head = f"kanban/{task_id}"
        else:
            head = "HEAD"

    result = StaticDiffResult(task_id=task_id, base=base, head=head, files_in_diff=0)
    diff_files = get_diff(base, head, cwd)
    result.files_in_diff = len(diff_files)
    if not diff_files:
        result.notes.append(f"empty diff between {base} and {head} (or git unavailable)")
        return result

    # Filter to analyzable files (.py)
    py_files = [f for f in diff_files if f.path.endswith(".py") and f.status != "D"]
    if len(py_files) > max_files:
        result.notes.append(
            f"diff has {len(py_files)} .py files; truncated to first {max_files}"
        )
        result.truncated = True
        py_files = py_files[:max_files]

    for fd in diff_files:
        if fd.status == "D":
            result.files_analyzed.append({
                "path": fd.path, "status": fd.status, "skipped": True,
                "reason": "deleted",
            })

    for fd in py_files:
        file_path = cwd / fd.path
        if not file_path.exists():
            result.files_analyzed.append({
                "path": fd.path, "status": fd.status, "skipped": True,
                "reason": "not on disk",
            })
            continue
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            result.files_analyzed.append({
                "path": fd.path, "status": fd.status, "skipped": True,
                "reason": f"read error: {exc}",
            })
            continue
        spans = _function_spans_for_file(source)
        codelens_findings = _run_codelens_for_file(file_path, spans, fd.post_ranges)
        diff_findings = _detect_diff_smells(fd.path, source, spans, fd.post_ranges)
        missing_ret = _detect_missing_return(fd.path, source, spans, fd.post_ranges)
        all_findings = codelens_findings + diff_findings + missing_ret
        # de-dupe by (function, kind)
        seen: set = set()
        deduped: List[Finding] = []
        for f in all_findings:
            key = (f.function, f.kind, f.diff_range)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(f)
        result.findings.extend(deduped)
        result.files_analyzed.append({
            "path": fd.path,
            "status": fd.status,
            "skipped": False,
            "functions_changed": len({s.name for s, _ in _span_intersection(spans, fd.post_ranges)}),
            "findings_count": len(deduped),
        })

    # sort + cap
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    result.findings.sort(key=lambda f: (severity_order.get(f.severity, 3), f.file, f.function))
    if len(result.findings) > max_findings:
        result.notes.append(
            f"findings truncated: {len(result.findings)} > max_findings={max_findings}"
        )
        result.findings = result.findings[:max_findings]
        result.truncated = True

    # summary
    summary: Dict[str, int] = {}
    for f in result.findings:
        key = f"{f.severity}:{f.kind}"
        summary[key] = summary.get(key, 0) + 1
    result.summary = summary
    return result


# ---------------------------------------------------------------------------
# Snapshot hook — used by self_debug.snapshot()
# ---------------------------------------------------------------------------

def snapshot_evidence(
    task_id: str = "",
    base: str = "main",
    head: Optional[str] = None,
    cwd: Optional[Path] = None,
    max_files: int = 25,
    max_findings: int = 50,
) -> Dict[str, Any]:
    """Drop-in dict for the EvidenceBundle: ``{"static_findings": ...}``.

    Returns a small dict so it stays under the snapshot char budget. The
    full result is also written to ``.tmp/arc_static/{task_id}.json`` for
    post-mortem inspection; the returned dict carries a pointer to that
    file plus a short in-band summary.
    """
    try:
        r = run(
            task_id=task_id,
            base=base,
            head=head,
            cwd=cwd,
            max_files=max_files,
            max_findings=max_findings,
        )
    except Exception as exc:  # never raise
        return {
            "static_findings": {"error": f"static_diff_lens crashed: {exc}"},
            "static_findings_count": 0,
        }

    payload = r.to_dict()
    # Persist full payload for post-mortem
    try:
        out_dir = (cwd or BASE_DIR) / ".tmp" / "arc_static"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{task_id or 'adhoc'}.json"
        out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    except Exception:
        pass

    # Return bounded inline copy (char cap ~4 KB)
    inline = {
        "task_id": payload["task_id"],
        "base": payload["base"],
        "head": payload["head"],
        "files_in_diff": payload["files_in_diff"],
        "files_analyzed": payload["files_analyzed"][:10],
        "findings": payload["findings"][:15],
        "findings_total": len(payload["findings"]),
        "summary": payload["summary"],
        "truncated": payload["truncated"],
        "notes": payload["notes"],
        "full_path": str(out_path) if 'out_path' in dir() else None,
    }
    return {
        "static_findings": inline,
        "static_findings_count": inline["findings_total"],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--task-id", default="", help="Kanban task ID (resolves head=kanban/<id>)")
    p.add_argument("--base", default="main", help="Base ref (default: main)")
    p.add_argument("--head", default=None, help="Head ref (default: kanban/<task-id> or HEAD)")
    p.add_argument("--cwd", default=None, help="Working directory (default: repo root)")
    p.add_argument("--max-files", type=int, default=25)
    p.add_argument("--max-findings", type=int, default=50)
    p.add_argument("--json", dest="json_output", action="store_true")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    cwd = Path(args.cwd).resolve() if args.cwd else None
    r = run(
        task_id=args.task_id,
        base=args.base,
        head=args.head,
        cwd=cwd,
        max_files=args.max_files,
        max_findings=args.max_findings,
    )
    if args.json_output:
        print(json.dumps(r.to_dict(), indent=2, default=str))
    else:
        print(f"static_diff_lens: {r.task_id or '<adhoc>'} {r.base}..{r.head}")
        print(f"  files_in_diff={r.files_in_diff}  findings={len(r.findings)}  truncated={r.truncated}")
        for f in r.findings[:20]:
            sev = f.severity.upper()
            print(f"  [{sev:8}] {f.file}:{f.function_start}-{f.function_end}  {f.kind}: {f.detail[:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
