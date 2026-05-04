#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV™ Testing Theater Detector.

Detects test theater anti-patterns using AST + regex only.
No LLM required — fully air-gap safe.

Anti-patterns:
  1. tautological_assertion — assert x == x or assertTrue(True)
  2. mock_dominated         — >80% of function body is mock setup vs assertions
  3. fixture_theater        — pytest fixture performs real I/O instead of setup
  4. assertion_free         — test function has no assert/assertEqual/etc.
  5. hardcoded_oracle       — magic literal in assertion with no explanatory comment
  6. smoke_masquerade       — test_unit_* function only calls top-level imports
  7. always_green           — try/except that swallows AssertionError
  8. spec_drift             — Gherkin .feature step with no step definition

Severity per file: >=3 anti-patterns → block; 1-2 → warn; 0 → none.
Overall severity is the worst across all scanned files.

CLI:
  python theater_detector.py --scan <dir> [--json]
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.testing.data_types import TheaterDetectionResult  # noqa: E402

# ── Constants ─────────────────────────────────────────────────────────────────

_SKIP_DIRS: Set[str] = {
    "__pycache__", "node_modules", ".git", ".tmp", "venv", ".venv",
    "dist", "build", ".tox", ".pytest_cache",
}

# unittest + pytest assertion method names
_ASSERT_METHODS: Set[str] = {
    "assertEqual", "assertNotEqual", "assertTrue", "assertFalse",
    "assertIs", "assertIsNot", "assertIsNone", "assertIsNotNone",
    "assertIn", "assertNotIn", "assertRaises", "assertRaisesRegex",
    "assertGreater", "assertGreaterEqual", "assertLess", "assertLessEqual",
    "assertAlmostEqual", "assertNotAlmostEqual", "assertMultiLineEqual",
    "assertSequenceEqual", "assertListEqual", "assertTupleEqual",
    "assertSetEqual", "assertDictEqual", "assertCountEqual",
    "assertRegex", "assertNotRegex", "assertLogs",
}

# Lines that look like mock setup
_MOCK_SETUP_PATTERNS: List[re.Pattern] = [
    re.compile(r"\bMagicMock\b"),
    re.compile(r"\bMock\s*\("),
    re.compile(r"\bpatch\s*\("),
    re.compile(r"@patch\b"),
    re.compile(r"\.return_value\s*="),
    re.compile(r"\.side_effect\s*="),
    re.compile(r"\bmocker\."),
    re.compile(r"\bmock_\w+\s*="),
    re.compile(r"\bAsyncMock\b"),
]

# Fixture real-I/O indicators (fixture delivers feature, not precondition)
_FIXTURE_IO_PATTERNS: List[re.Pattern] = [
    re.compile(r"\brequests\.(get|post|put|delete|patch)\b"),
    re.compile(r"\bhttpx\.(get|post|put|delete|patch)\b"),
    re.compile(r"\burllib\.request\b"),
    re.compile(r"\bopen\s*\(.*?[\"'][wa][\"']"),
    re.compile(r"\.write\s*\("),
    re.compile(r"\bsubprocess\.(run|Popen|call|check_output)\b"),
    re.compile(r"\.execute\s*\(\s*['\"](?:INSERT|UPDATE|DELETE|CREATE|DROP)", re.IGNORECASE),
    re.compile(r"(?<!\w)commit\s*\("),
]

# Numeric constants that are NOT magic (too common to flag)
_BORING_INTS: Set[int] = {-1, 0, 1, 2}
_BORING_FLOATS: Set[float] = {0.0, 1.0}


# ── Internal state for one file ────────────────────────────────────────────────

class _FileResult:
    """Accumulates anti-patterns found in a single Python file."""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.antipatterns: List[str] = []
        self.details: Dict[str, Any] = {}

    def add(self, name: str, findings: List[str]) -> None:
        if findings:
            if name not in self.antipatterns:
                self.antipatterns.append(name)
            self.details.setdefault(name, []).extend(findings)

    @property
    def severity(self) -> str:
        n = len(self.antipatterns)
        if n == 0:
            return "none"
        if n <= 2:
            return "warn"
        return "block"


# ── Main detector ──────────────────────────────────────────────────────────────

class TheaterDetector:
    """Scan a test directory for the 8 theater anti-patterns."""

    def detect(self, test_dir: Path) -> TheaterDetectionResult:
        """Scan *test_dir* and return an aggregate TheaterDetectionResult."""
        t0 = time.monotonic()
        test_dir = Path(test_dir).resolve()

        all_antipatterns: List[str] = []
        all_details: Dict[str, Any] = {}
        worst: str = "none"  # "none" < "warn" < "block"

        py_files = self._collect_py_files(test_dir)
        feature_files = [
            f for f in test_dir.rglob("*.feature")
            if not any(s in f.parts for s in _SKIP_DIRS)
        ]

        # Per-file Python checks
        for py_file in py_files:
            fr = self._check_file(py_file)
            if fr.antipatterns:
                key = self._rel(py_file, test_dir)
                all_details[key] = {
                    "antipatterns": fr.antipatterns,
                    "severity": fr.severity,
                    "findings": fr.details,
                }
                for ap in fr.antipatterns:
                    if ap not in all_antipatterns:
                        all_antipatterns.append(ap)
                worst = _worse(worst, fr.severity)

        # Spec-drift: cross-file, only when .feature files exist
        if feature_files:
            drift = self._check_spec_drift(feature_files, py_files, test_dir)
            if drift:
                if "spec_drift" not in all_antipatterns:
                    all_antipatterns.append("spec_drift")
                all_details["spec_drift"] = drift
                worst = _worse(worst, "warn")

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return TheaterDetectionResult(
            file_path=str(test_dir),
            antipatterns_found=all_antipatterns,
            severity=worst,
            details=all_details,
            passed=(worst == "none"),
            duration_ms=elapsed_ms,
        )

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _collect_py_files(self, root: Path) -> List[Path]:
        return sorted(
            f for f in root.rglob("*.py")
            if not any(s in f.parts for s in _SKIP_DIRS)
        )

    @staticmethod
    def _rel(path: Path, base: Path) -> str:
        try:
            return str(path.relative_to(base))
        except ValueError:
            return str(path)

    # ── Per-file orchestrator ──────────────────────────────────────────────────

    def _check_file(self, file_path: Path) -> _FileResult:
        fr = _FileResult(file_path)

        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return fr

        try:
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError:
            return fr

        lines = source.splitlines()

        fr.add("tautological_assertion", self._tautological_assertion(tree))
        fr.add("assertion_free",        self._assertion_free(tree))
        fr.add("mock_dominated",        self._mock_dominated(tree, lines))
        fr.add("always_green",          self._always_green(tree))
        fr.add("hardcoded_oracle",      self._hardcoded_oracle(tree, lines))
        fr.add("smoke_masquerade",      self._smoke_masquerade(tree))
        fr.add("fixture_theater",       self._fixture_theater(tree, lines))
        return fr

    # ── Check 1: tautological_assertion ───────────────────────────────────────

    def _tautological_assertion(self, tree: ast.AST) -> List[str]:
        hits: List[str] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Assert):
                test = node.test
                # assert True
                if isinstance(test, ast.Constant) and test.value is True:
                    hits.append(f"line {node.lineno}: assert True — always passes")
                # assert x == x  /  assert x is x
                elif isinstance(test, ast.Compare) and len(test.comparators) == 1:
                    if _ast_eq(test.left, test.comparators[0]):
                        hits.append(
                            f"line {node.lineno}: assert with identical operands "
                            f"({ast.dump(test.left)[:40]})"
                        )

            elif isinstance(node, ast.Call):
                func = node.func
                mname = func.attr if isinstance(func, ast.Attribute) else (
                    func.id if isinstance(func, ast.Name) else None
                )

                if mname == "assertTrue" and node.args:
                    if isinstance(node.args[0], ast.Constant) and node.args[0].value is True:
                        hits.append(f"line {node.lineno}: assertTrue(True) — always passes")

                elif mname == "assertFalse" and node.args:
                    if isinstance(node.args[0], ast.Constant) and node.args[0].value is False:
                        hits.append(f"line {node.lineno}: assertFalse(False) — always passes")

                elif mname in {"assertEqual", "assertIs"} and len(node.args) >= 2:
                    if _ast_eq(node.args[0], node.args[1]):
                        hits.append(f"line {node.lineno}: {mname} with identical operands")

        return hits

    # ── Check 2: mock_dominated ────────────────────────────────────────────────

    def _mock_dominated(self, tree: ast.AST, lines: List[str]) -> List[str]:
        hits: List[str] = []

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test"):
                continue

            start = node.body[0].lineno - 1
            end = getattr(node.body[-1], "end_lineno", node.body[-1].lineno)
            func_lines = lines[start:end]

            mock_n = assert_n = blank_n = 0
            for ln in func_lines:
                stripped = ln.strip()
                if not stripped:
                    blank_n += 1
                    continue
                if any(p.search(stripped) for p in _MOCK_SETUP_PATTERNS):
                    mock_n += 1
                if (
                    stripped.startswith("assert ")
                    or any(f"{m}(" in stripped for m in _ASSERT_METHODS)
                ):
                    assert_n += 1

            non_blank = len(func_lines) - blank_n
            # Only flag when there ARE assertions but mocks dominate
            if non_blank > 5 and assert_n > 0 and mock_n / non_blank > 0.80:
                hits.append(
                    f"line {node.lineno}: {node.name} — "
                    f"{mock_n / non_blank:.0%} mock setup ({mock_n}/{non_blank} lines)"
                )

        return hits

    # ── Check 3: fixture_theater ───────────────────────────────────────────────

    def _fixture_theater(self, tree: ast.AST, lines: List[str]) -> List[str]:
        hits: List[str] = []

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not self._is_fixture(node):
                continue

            start = node.body[0].lineno - 1
            end = getattr(node.body[-1], "end_lineno", node.body[-1].lineno)
            body_text = "\n".join(lines[start:end])

            matched = [p.pattern for p in _FIXTURE_IO_PATTERNS if p.search(body_text)]
            if matched:
                hits.append(
                    f"line {node.lineno}: fixture '{node.name}' performs real I/O — "
                    f"use a precondition, not a feature ({matched[0][:40]})"
                )

        return hits

    @staticmethod
    def _is_fixture(node: ast.FunctionDef) -> bool:
        for d in node.decorator_list:
            name = None
            if isinstance(d, ast.Name):
                name = d.id
            elif isinstance(d, ast.Attribute):
                name = d.attr
            elif isinstance(d, ast.Call):
                f = d.func
                name = f.attr if isinstance(f, ast.Attribute) else (
                    f.id if isinstance(f, ast.Name) else None
                )
            if name == "fixture":
                return True
        return False

    # ── Check 4: assertion_free ────────────────────────────────────────────────

    def _assertion_free(self, tree: ast.AST) -> List[str]:
        hits: List[str] = []

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test"):
                continue
            if not self._has_any_assertion(node):
                hits.append(f"line {node.lineno}: {node.name} has no assertions")

        return hits

    @staticmethod
    def _has_any_assertion(func_node: ast.FunctionDef) -> bool:
        for child in ast.walk(func_node):
            if isinstance(child, ast.Assert):
                return True
            if isinstance(child, ast.Call):
                f = child.func
                mname = f.attr if isinstance(f, ast.Attribute) else (
                    f.id if isinstance(f, ast.Name) else None
                )
                if mname in _ASSERT_METHODS or mname == "raises":
                    return True
            # with pytest.raises(...):
            if isinstance(child, ast.With):
                for item in child.items:
                    ctx = item.context_expr
                    if isinstance(ctx, ast.Call):
                        f = ctx.func
                        if isinstance(f, ast.Attribute) and f.attr == "raises":
                            return True
        return False

    # ── Check 5: hardcoded_oracle ──────────────────────────────────────────────

    def _hardcoded_oracle(self, tree: ast.AST, lines: List[str]) -> List[str]:
        hits: List[str] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Assert):
                continue
            for child in ast.walk(node.test):
                if not isinstance(child, ast.Constant):
                    continue
                val = child.value
                if isinstance(val, bool) or val is None:
                    continue
                if isinstance(val, int) and val in _BORING_INTS:
                    continue
                if isinstance(val, float) and val in _BORING_FLOATS:
                    continue
                if isinstance(val, str) and len(val) <= 3:
                    continue

                # Check same line and up to 2 lines above for a comment
                lineno_0 = child.lineno - 1  # 0-indexed
                has_comment = any(
                    "#" in lines[i]
                    for i in (lineno_0, lineno_0 - 1, lineno_0 - 2)
                    if 0 <= i < len(lines)
                )
                if not has_comment:
                    hits.append(
                        f"line {child.lineno}: magic literal {val!r} in assertion — "
                        "add a comment explaining the expected value"
                    )

        return hits[:10]  # cap noise

    # ── Check 6: smoke_masquerade ──────────────────────────────────────────────

    def _smoke_masquerade(self, tree: ast.AST) -> List[str]:
        """test_unit_* functions that only reference top-level imported names."""
        hits: List[str] = []

        # Gather top-level imports
        top_imports: Set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_imports.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    name = alias.asname or alias.name
                    if name != "*":
                        top_imports.add(name)

        _BUILTINS = {"self", "cls", "True", "False", "None", "assert"}

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_unit_"):
                continue

            body_names: Set[str] = set()
            for child in ast.walk(node):
                if child is node:
                    continue
                if isinstance(child, ast.Name):
                    body_names.add(child.id)

            meaningful = {n for n in body_names if n not in _BUILTINS and not n.startswith("_")}
            if meaningful and meaningful.issubset(top_imports):
                hits.append(
                    f"line {node.lineno}: {node.name} — named 'unit_' but only "
                    f"calls top-level imports {sorted(meaningful)[:5]}"
                )

        return hits

    # ── Check 7: always_green ──────────────────────────────────────────────────

    def _always_green(self, tree: ast.AST) -> List[str]:
        hits: List[str] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            for handler in node.handlers:
                if not self._handler_is_trivial(handler.body):
                    continue

                exc = handler.type
                if exc is None:
                    hits.append(f"line {node.lineno}: bare except swallows all errors — test always passes")
                    continue

                names: List[str] = []
                if isinstance(exc, ast.Name):
                    names = [exc.id]
                elif isinstance(exc, ast.Tuple):
                    names = [e.id for e in exc.elts if isinstance(e, ast.Name)]

                if "AssertionError" in names:
                    hits.append(f"line {node.lineno}: except AssertionError swallowed — test always passes")
                elif any(n in {"Exception", "BaseException"} for n in names):
                    hits.append(f"line {node.lineno}: broad except swallows all exceptions — test always passes")

        return hits

    @staticmethod
    def _handler_is_trivial(body: List[ast.stmt]) -> bool:
        """True if the handler body is pass/continue/simple print — not a re-raise."""
        _LOG_NAMES = {"print", "log", "logger", "logging"}
        _LOG_ATTRS = {"info", "debug", "warning", "warn", "error", "exception", "critical"}
        for stmt in body:
            if isinstance(stmt, (ast.Pass, ast.Continue)):
                continue
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                f = stmt.value.func
                if isinstance(f, ast.Name) and f.id in _LOG_NAMES:
                    continue
                if isinstance(f, ast.Attribute) and f.attr in _LOG_ATTRS:
                    continue
            if isinstance(stmt, ast.Raise):
                return False
            return False  # anything else is non-trivial
        return True

    # ── Check 8: spec_drift ────────────────────────────────────────────────────

    def _check_spec_drift(
        self,
        feature_files: List[Path],
        py_files: List[Path],
        test_dir: Path,
    ) -> List[Dict[str, Any]]:
        defined_steps = self._collect_step_defs(py_files)
        hits: List[Dict[str, Any]] = []

        _STEP_RE = re.compile(
            r"^\s*(?:Given|When|Then|And|But)\s+(.+)$", re.IGNORECASE
        )

        for feat in feature_files:
            try:
                text = feat.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                m = _STEP_RE.match(line)
                if not m:
                    continue
                step = m.group(1).strip()
                if not self._step_matched(step, defined_steps):
                    hits.append({
                        "feature_file": self._rel(feat, test_dir),
                        "line": lineno,
                        "step": step,
                    })

        return hits

    @staticmethod
    def _collect_step_defs(py_files: List[Path]) -> List[Tuple[str, Optional[re.Pattern]]]:
        """Extract step patterns from @given/@when/@then/@step decorators."""
        _DEC_RE = re.compile(
            r'@(?:[\w.]+\.)?\s*(?:given|when|then|step)\s*\(\s*(["\'])(.*?)\1',
            re.IGNORECASE,
        )
        defs: List[Tuple[str, Optional[re.Pattern]]] = []
        for f in py_files:
            try:
                src = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in _DEC_RE.finditer(src):
                pat_str = m.group(2)
                # Behave patterns: {param} → wildcard
                regex_str = re.sub(r"\{[^}]+\}", ".+", pat_str)
                try:
                    compiled: Optional[re.Pattern] = re.compile(
                        "^" + regex_str + "$", re.IGNORECASE
                    )
                except re.error:
                    compiled = None
                defs.append((pat_str, compiled))
        return defs

    @staticmethod
    def _step_matched(
        step: str,
        defs: List[Tuple[str, Optional[re.Pattern]]],
    ) -> bool:
        for pat_str, compiled in defs:
            if compiled and compiled.match(step):
                return True
            if pat_str.lower() == step.lower():
                return True
        return False


# ── Utilities ──────────────────────────────────────────────────────────────────

def _worse(a: str, b: str) -> str:
    """Return the higher severity between two severity strings."""
    order = {"none": 0, "warn": 1, "block": 2}
    return a if order[a] >= order[b] else b


def _ast_eq(a: ast.expr, b: ast.expr) -> bool:
    """Shallow structural equality (same type + key attributes)."""
    if type(a) is not type(b):
        return False
    if isinstance(a, ast.Name):
        return a.id == b.id  # type: ignore[attr-defined]
    if isinstance(a, ast.Constant):
        return a.value == b.value  # type: ignore[attr-defined]
    if isinstance(a, ast.Attribute):
        return (
            a.attr == b.attr  # type: ignore[attr-defined]
            and _ast_eq(a.value, b.value)  # type: ignore[attr-defined]
        )
    return False


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Theater Detector — 8 anti-pattern checks on test directories"
    )
    parser.add_argument("--scan", required=True, metavar="DIR", help="Directory to scan")
    parser.add_argument("--json", action="store_true", dest="json_out", help="JSON output")
    args = parser.parse_args()

    scan_dir = Path(args.scan).resolve()
    if not scan_dir.is_dir():
        print(json.dumps({"error": f"Not a directory: {scan_dir}"}))
        sys.exit(1)

    result = TheaterDetector().detect(scan_dir)

    if args.json_out:
        out = {
            "scan_dir": result.file_path,
            "antipatterns_found": result.antipatterns_found,
            "severity": result.severity,
            "passed": result.passed,
            "duration_ms": result.duration_ms,
            "details": result.details,
        }
        print(json.dumps(out, indent=2, default=str))
    else:
        _print_human(result)

    sys.exit(0 if result.passed else 1)


def _print_human(result: TheaterDetectionResult) -> None:
    sev_label = {"none": "PASS", "warn": "WARN", "block": "BLOCK"}
    print(f"\n{'=' * 60}")
    print(f"  Theater Detector — {result.file_path}")
    print(f"  Result : {sev_label.get(result.severity, result.severity)}")
    print(f"  Elapsed: {result.duration_ms} ms")
    print(f"{'=' * 60}")

    if not result.antipatterns_found:
        print("  No anti-patterns detected.")
        return

    for ap in result.antipatterns_found:
        print(f"\n  [{ap}]")
        val = result.details.get(ap)
        if isinstance(val, list):
            for item in val[:5]:
                if isinstance(item, dict):
                    print(f"    • {item}")
                else:
                    print(f"    • {item}")
            if len(val) > 5:
                print(f"    … and {len(val) - 5} more")
        elif isinstance(val, dict):
            for fname, fdata in list(val.items())[:5]:
                print(f"    {fname}: {fdata}")


if __name__ == "__main__":
    main()
