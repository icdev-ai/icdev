#!/usr/bin/env python3
# CUI // SP-CTI
"""Code Quality Analyzer — AST-based self-analysis for ICDEV.

Phase 52 (D331-D337). Read-only, advisory-only. Never modifies source files.
Computes per-function metrics (cyclomatic/cognitive complexity, nesting depth,
parameter count, LOC) and file-level aggregates. Detects code smells. Stores
append-only time-series in code_quality_metrics table for trend tracking.

Usage:
    python tools/analysis/code_analyzer.py --project-dir tools/ --json
    python tools/analysis/code_analyzer.py --project-dir tools/ --store --json
    python tools/analysis/code_analyzer.py --file tools/analysis/code_analyzer.py --json
    python tools/analysis/code_analyzer.py --project-id proj-123 --trend --json
"""

import argparse
import ast
import hashlib
import json
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
CONFIG_PATH = BASE_DIR / "args" / "code_quality_config.yaml"

# ---------------------------------------------------------------------------
# Excluded dirs (consistent with modular_design_analyzer.py)
# ---------------------------------------------------------------------------
_EXCLUDE_DIRS = {
    "venv", ".venv", "env", "node_modules", ".git", "__pycache__",
    "build", "dist", ".tox", ".eggs", "vendor", "target", "bin", "obj",
    ".tmp", "playwright",
}

_LANG_EXT: Dict[str, Tuple[str, ...]] = {
    "python": (".py",),
    "java": (".java",),
    "go": (".go",),
    "typescript": (".ts", ".tsx"),
    "javascript": (".js", ".jsx"),
    "rust": (".rs",),
    "csharp": (".cs",),
}

_EXT_TO_LANG: Dict[str, str] = {}
for _lang, _exts in _LANG_EXT.items():
    for _ext in _exts:
        _EXT_TO_LANG[_ext] = _lang


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_DEFAULT_SMELL_THRESHOLDS = {
    "max_function_loc": 50,
    "max_nesting": 4,
    "max_complexity": 10,
    "max_params": 5,
    "max_methods_per_class": 10,
}

_DEFAULT_MAINTAINABILITY_WEIGHTS = {
    "complexity": 0.30,
    "smell_density": 0.20,
    "test_health": 0.20,
    "coupling": 0.15,
    "coverage": 0.15,
}


def _parse_config() -> Dict[str, Any]:
    """Lightweight YAML parser (same pattern as modular_design_analyzer)."""
    cfg: Dict[str, Any] = {
        "smell_thresholds": dict(_DEFAULT_SMELL_THRESHOLDS),
        "maintainability_weights": dict(_DEFAULT_MAINTAINABILITY_WEIGHTS),
    }
    if not CONFIG_PATH.exists():
        return cfg
    try:
        section = ""
        for line in CONFIG_PATH.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not line.startswith(" ") and stripped.endswith(":"):
                section = stripped.rstrip(":")
                continue
            if section and ":" in stripped:
                key, _, val = stripped.partition(":")
                key = key.strip()
                val = val.strip()
                if section in ("smell_thresholds", "maintainability_weights",
                               "audit_thresholds", "innovation_engine"):
                    target = cfg.setdefault(section, {})
                    try:
                        target[key] = float(val) if "." in val else int(val)
                    except ValueError:
                        target[key] = val
    except Exception:
        pass
    return cfg


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    p = db_path or DB_PATH
    if not p.exists():
        raise FileNotFoundError(f"Database not found: {p}")
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _uid() -> str:
    return f"cqm-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Python AST visitors (copied from legacy_analyzer.py — D333)
# ---------------------------------------------------------------------------

class _PythonComplexityVisitor(ast.NodeVisitor):
    """Count branching nodes for cyclomatic complexity estimation."""

    def __init__(self):
        self.complexity = 1

    def visit_If(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_For(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_While(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_ExceptHandler(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_With(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_Assert(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_BoolOp(self, node):
        self.complexity += len(node.values) - 1
        self.generic_visit(node)

    def visit_comprehension(self, node):
        self.complexity += 1
        self.generic_visit(node)


def _compute_python_complexity(node) -> int:
    visitor = _PythonComplexityVisitor()
    visitor.visit(node)
    return visitor.complexity


class _NestingDepthVisitor(ast.NodeVisitor):
    """Track maximum nesting depth of branching/looping constructs."""

    def __init__(self):
        self.max_depth = 0
        self._depth = 0

    def _enter(self, node):
        self._depth += 1
        if self._depth > self.max_depth:
            self.max_depth = self._depth
        self.generic_visit(node)
        self._depth -= 1

    visit_If = _enter
    visit_For = _enter
    visit_While = _enter
    visit_With = _enter
    visit_Try = _enter


class _CognitiveComplexityVisitor(ast.NodeVisitor):
    """Approximate cognitive complexity (nesting-aware branching cost)."""

    def __init__(self):
        self.score = 0
        self._depth = 0

    def _increment(self, node):
        self.score += 1 + self._depth
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    visit_If = _increment
    visit_For = _increment
    visit_While = _increment

    def visit_ExceptHandler(self, node):
        self.score += 1 + self._depth
        self.generic_visit(node)

    def visit_BoolOp(self, node):
        self.score += 1
        self.generic_visit(node)


def _compute_cognitive_complexity(node) -> int:
    visitor = _CognitiveComplexityVisitor()
    visitor.visit(node)
    return visitor.score


# ---------------------------------------------------------------------------
# Line counting
# ---------------------------------------------------------------------------

def _count_lines(source: str) -> Dict[str, int]:
    """Count total, code, comment, and blank lines in source text."""
    total = code = comment = blank = 0
    in_block = False
    for line in source.splitlines():
        total += 1
        stripped = line.strip()
        if not stripped:
            blank += 1
            continue
        if in_block:
            comment += 1
            if stripped.endswith('"""') or stripped.endswith("'''"):
                in_block = False
            continue
        if stripped.startswith("#"):
            comment += 1
        elif stripped.startswith('"""') or stripped.startswith("'''"):
            comment += 1
            marker = stripped[:3]
            if stripped.count(marker) == 1:
                in_block = True
        else:
            code += 1
    return {"loc": total, "loc_code": code, "loc_comment": comment, "loc_blank": blank}


# ---------------------------------------------------------------------------
# File iteration (from modular_design_analyzer.py)
# ---------------------------------------------------------------------------

def _iter_source_files(root: Path, extensions: Tuple[str, ...]) -> List[Path]:
    results: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDE_DIRS]
        for fname in filenames:
            if fname.endswith(extensions):
                fp = Path(dirpath) / fname
                try:
                    if fp.stat().st_size <= 1048576:
                        results.append(fp)
                except OSError:
                    pass
    return results


# ---------------------------------------------------------------------------
# Regex branch counting for non-Python languages (D333)
# ---------------------------------------------------------------------------

_BRANCH_KEYWORDS = {
    "java": [r"\bif\b", r"\belse\s+if\b", r"\bfor\b", r"\bwhile\b",
             r"\bcase\b", r"\bcatch\b", r"&&", r"\|\|"],
    "go": [r"\bif\b", r"\belse\s+if\b", r"\bfor\b", r"\bcase\b",
            r"&&", r"\|\|"],
    "typescript": [r"\bif\b", r"\belse\s+if\b", r"\bfor\b", r"\bwhile\b",
                   r"\bcase\b", r"\bcatch\b", r"&&", r"\|\|"],
    "javascript": [r"\bif\b", r"\belse\s+if\b", r"\bfor\b", r"\bwhile\b",
                   r"\bcase\b", r"\bcatch\b", r"&&", r"\|\|"],
    "rust": [r"\bif\b", r"\belse\s+if\b", r"\bfor\b", r"\bwhile\b",
             r"\bmatch\b", r"&&", r"\|\|"],
    "csharp": [r"\bif\b", r"\belse\s+if\b", r"\bfor\b", r"\bforeach\b",
               r"\bwhile\b", r"\bcase\b", r"\bcatch\b", r"&&", r"\|\|"],
}


def _regex_branch_count(source: str, lang: str) -> int:
    """Approximate cyclomatic complexity via regex for non-Python languages."""
    count = 1
    keywords = _BRANCH_KEYWORDS.get(lang, _BRANCH_KEYWORDS.get("java", []))
    for kw in keywords:
        count += len(re.findall(kw, source))
    return count


# ---------------------------------------------------------------------------
# Smell detection (D331)
# ---------------------------------------------------------------------------

def _detect_smells(metrics: Dict[str, Any], thresholds: Dict[str, int]) -> List[str]:
    """Return list of smell names detected in the given metrics dict."""
    smells = []
    if metrics.get("loc", 0) > thresholds.get("max_function_loc", 50):
        smells.append("long_function")
    if metrics.get("nesting_depth", 0) > thresholds.get("max_nesting", 4):
        smells.append("deep_nesting")
    if metrics.get("cyclomatic_complexity", 0) > thresholds.get("max_complexity", 10):
        smells.append("high_complexity")
    if metrics.get("parameter_count", 0) > thresholds.get("max_params", 5):
        smells.append("too_many_params")
    if metrics.get("function_count", 0) > thresholds.get("max_methods_per_class", 10):
        smells.append("god_class")
    return smells


# ---------------------------------------------------------------------------
# Maintainability score (D337)
# ---------------------------------------------------------------------------

def compute_maintainability_score(
    metrics: Dict[str, Any],
    weights: Optional[Dict[str, float]] = None,
) -> float:
    """Deterministic weighted average maintainability score (0.0-1.0)."""
    w = weights or _DEFAULT_MAINTAINABILITY_WEIGHTS

    cc = metrics.get("cyclomatic_complexity", 0)
    complexity_score = max(0.0, 1.0 - cc / 25.0)

    smell_count = metrics.get("smell_count", 0)
    smell_score = max(0.0, 1.0 - smell_count / 10.0)

    # test_health and coverage default to 1.0 when no runtime data
    test_health = metrics.get("test_health", 1.0)
    coverage = metrics.get("coverage", 1.0)

    import_count = metrics.get("import_count", 0)
    coupling_score = max(0.0, 1.0 - import_count / 30.0)

    score = (
        w.get("complexity", 0.30) * complexity_score
        + w.get("smell_density", 0.20) * smell_score
        + w.get("test_health", 0.20) * test_health
        + w.get("coupling", 0.15) * coupling_score
        + w.get("coverage", 0.15) * coverage
    )
    return round(max(0.0, min(1.0, score)), 4)


# ---------------------------------------------------------------------------
# CodeAnalyzer class
# ---------------------------------------------------------------------------

class CodeAnalyzer:
    """AST-based code quality analyzer. Read-only, advisory-only (D331)."""

    def __init__(
        self,
        project_dir: Optional[str] = None,
        project_id: Optional[str] = None,
        db_path: Optional[Path] = None,
    ):
        self.project_dir = Path(project_dir) if project_dir else BASE_DIR
        self.project_id = project_id
        self.db_path = db_path or DB_PATH
        self.config = _parse_config()
        self.smell_thresholds = self.config.get(
            "smell_thresholds", _DEFAULT_SMELL_THRESHOLDS
        )
        self.maint_weights = self.config.get(
            "maintainability_weights", _DEFAULT_MAINTAINABILITY_WEIGHTS
        )

    # ---- Python file analysis (AST) ----

    def analyze_python_file(self, file_path: Path) -> List[Dict[str, Any]]:
        """Analyze a Python file. Returns list of metric dicts (per-function + file-level)."""
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(file_path))
        except (SyntaxError, UnicodeDecodeError):
            return []

        line_counts = _count_lines(source)
        content_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
        rel_path = str(file_path)
        try:
            rel_path = str(file_path.relative_to(self.project_dir))
        except ValueError:
            pass

        results: List[Dict[str, Any]] = []
        file_cc_sum = 0
        file_fn_count = 0
        file_class_count = 0
        file_import_count = 0

        # Count imports
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                file_import_count += 1

        # Analyze classes and functions
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                file_class_count += 1
                class_methods = []
                for item in ast.iter_child_nodes(node):
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        m = self._analyze_python_function(
                            item, rel_path, class_name=node.name
                        )
                        class_methods.append(m)
                        results.append(m)
                        file_cc_sum += m["cyclomatic_complexity"]
                        file_fn_count += 1

                # God class smell check
                if len(class_methods) > self.smell_thresholds.get("max_methods_per_class", 10):
                    for m in class_methods:
                        if "god_class" not in json.loads(m.get("smells_json", "[]")):
                            smells = json.loads(m.get("smells_json", "[]"))
                            smells.append("god_class")
                            m["smells_json"] = json.dumps(smells)
                            m["smell_count"] = len(smells)

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                m = self._analyze_python_function(node, rel_path)
                results.append(m)
                file_cc_sum += m["cyclomatic_complexity"]
                file_fn_count += 1

        # File-level aggregate
        avg_cc = round(file_cc_sum / max(file_fn_count, 1), 2)
        total_smells = sum(r.get("smell_count", 0) for r in results)
        file_metrics = {
            "file_path": rel_path,
            "function_name": None,
            "class_name": None,
            "language": "python",
            "cyclomatic_complexity": avg_cc,
            "cognitive_complexity": 0,
            "loc": line_counts["loc"],
            "loc_code": line_counts["loc_code"],
            "loc_comment": line_counts["loc_comment"],
            "parameter_count": 0,
            "nesting_depth": 0,
            "import_count": file_import_count,
            "class_count": file_class_count,
            "function_count": file_fn_count,
            "smells_json": "[]",
            "smell_count": total_smells,
            "maintainability_score": compute_maintainability_score(
                {"cyclomatic_complexity": avg_cc, "smell_count": total_smells,
                 "import_count": file_import_count}, self.maint_weights
            ),
            "content_hash": content_hash,
        }
        results.append(file_metrics)
        return results

    def _analyze_python_function(
        self, node, file_path: str, class_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Analyze a single Python function/method AST node."""
        cc = _compute_python_complexity(node)
        cog = _compute_cognitive_complexity(node)

        nesting_v = _NestingDepthVisitor()
        nesting_v.visit(node)

        param_count = len(node.args.args)
        if class_name and node.args.args:
            param_count = max(0, param_count - 1)  # Exclude self/cls

        end_line = getattr(node, "end_lineno", None) or (node.lineno + 1)
        func_loc = max(1, end_line - node.lineno)

        metrics = {
            "file_path": file_path,
            "function_name": node.name,
            "class_name": class_name,
            "language": "python",
            "cyclomatic_complexity": cc,
            "cognitive_complexity": cog,
            "loc": func_loc,
            "loc_code": func_loc,
            "loc_comment": 0,
            "parameter_count": param_count,
            "nesting_depth": nesting_v.max_depth,
            "import_count": 0,
            "class_count": 0,
            "function_count": 0,
        }

        smells = _detect_smells(metrics, self.smell_thresholds)
        metrics["smells_json"] = json.dumps(smells)
        metrics["smell_count"] = len(smells)
        metrics["maintainability_score"] = compute_maintainability_score(
            metrics, self.maint_weights
        )
        metrics["content_hash"] = None
        return metrics

    # ---- Non-Python file analysis (regex, D333) ----

    def analyze_non_python_file(
        self, file_path: Path, lang: str,
    ) -> List[Dict[str, Any]]:
        """File-level metrics for non-Python languages via regex."""
        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return []

        line_counts = _count_lines(source)
        cc = _regex_branch_count(source, lang)
        content_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()

        rel_path = str(file_path)
        try:
            rel_path = str(file_path.relative_to(self.project_dir))
        except ValueError:
            pass

        metrics = {
            "file_path": rel_path,
            "function_name": None,
            "class_name": None,
            "language": lang,
            "cyclomatic_complexity": cc,
            "cognitive_complexity": 0,
            "loc": line_counts["loc"],
            "loc_code": line_counts["loc_code"],
            "loc_comment": line_counts["loc_comment"],
            "parameter_count": 0,
            "nesting_depth": 0,
            "import_count": 0,
            "class_count": 0,
            "function_count": 0,
            "smells_json": "[]",
            "smell_count": 0,
            "maintainability_score": compute_maintainability_score(
                {"cyclomatic_complexity": cc, "smell_count": 0}, self.maint_weights
            ),
            "content_hash": content_hash,
        }
        return [metrics]

    # ---- Directory scan ----

    def scan_directory(self, project_dir: Optional[Path] = None) -> Dict[str, Any]:
        """Scan entire project. Returns summary dict with all metrics."""
        root = project_dir or self.project_dir
        scan_id = f"scan-{uuid.uuid4().hex[:12]}"
        all_metrics: List[Dict[str, Any]] = []
        file_count = 0
        total_functions = 0
        total_smells = 0
        cc_sum = 0.0
        fn_with_cc = 0

        for lang, exts in _LANG_EXT.items():
            files = _iter_source_files(root, exts)
            for fp in files:
                file_count += 1
                if lang == "python":
                    metrics = self.analyze_python_file(fp)
                else:
                    metrics = self.analyze_non_python_file(fp, lang)

                for m in metrics:
                    m["project_id"] = self.project_id
                    m["scan_id"] = scan_id
                    all_metrics.append(m)
                    if m.get("function_name"):
                        total_functions += 1
                        cc_sum += m.get("cyclomatic_complexity", 0)
                        fn_with_cc += 1
                    total_smells += m.get("smell_count", 0)

        avg_cc = round(cc_sum / max(fn_with_cc, 1), 2)
        avg_maint = 0.0
        maint_values = [m["maintainability_score"] for m in all_metrics
                        if m.get("function_name") and m.get("maintainability_score")]
        if maint_values:
            avg_maint = round(sum(maint_values) / len(maint_values), 4)

        return {
            "scan_id": scan_id,
            "project_id": self.project_id,
            "project_dir": str(root),
            "timestamp": _now(),
            "files_analyzed": file_count,
            "total_functions": total_functions,
            "avg_cyclomatic_complexity": avg_cc,
            "total_smells": total_smells,
            "avg_maintainability_score": avg_maint,
            "metrics": all_metrics,
        }

    # ---- DB storage (append-only, D332) ----

    def store_metrics(
        self, metrics: List[Dict[str, Any]], scan_id: str,
        db_path: Optional[Path] = None,
    ) -> int:
        """Bulk INSERT into code_quality_metrics. Returns row count."""
        conn = _get_db(db_path or self.db_path)
        count = 0
        try:
            for m in metrics:
                conn.execute(
                    """INSERT INTO code_quality_metrics
                    (id, project_id, file_path, function_name, class_name,
                     language, cyclomatic_complexity, cognitive_complexity,
                     loc, loc_code, loc_comment, parameter_count, nesting_depth,
                     import_count, class_count, function_count,
                     smells_json, smell_count, maintainability_score,
                     content_hash, scan_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        _uid(), m.get("project_id"), m["file_path"],
                        m.get("function_name"), m.get("class_name"),
                        m["language"], m.get("cyclomatic_complexity", 0),
                        m.get("cognitive_complexity", 0),
                        m.get("loc", 0), m.get("loc_code", 0),
                        m.get("loc_comment", 0), m.get("parameter_count", 0),
                        m.get("nesting_depth", 0), m.get("import_count", 0),
                        m.get("class_count", 0), m.get("function_count", 0),
                        m.get("smells_json", "[]"), m.get("smell_count", 0),
                        m.get("maintainability_score", 0.0),
                        m.get("content_hash"), scan_id,
                    ),
                )
                count += 1
            conn.commit()
        finally:
            conn.close()
        return count

    # ---- Trend query ----

    def get_trend(
        self, project_id: Optional[str] = None, last_n: int = 10,
        db_path: Optional[Path] = None,
    ) -> List[Dict[str, Any]]:
        """Return maintainability score trend over last N scans."""
        conn = _get_db(db_path or self.db_path)
        try:
            rows = conn.execute(
                """SELECT scan_id,
                          AVG(maintainability_score) as avg_score,
                          AVG(cyclomatic_complexity) as avg_cc,
                          SUM(smell_count) as total_smells,
                          COUNT(*) as metric_count,
                          MIN(created_at) as scan_date
                   FROM code_quality_metrics
                   WHERE (?1 IS NULL OR project_id = ?1)
                     AND function_name IS NOT NULL
                   GROUP BY scan_id
                   ORDER BY scan_date DESC
                   LIMIT ?2""",
                (project_id, last_n),
            ).fetchall()
            return [
                {
                    "scan_id": r["scan_id"],
                    "avg_maintainability": round(r["avg_score"] or 0, 4),
                    "avg_complexity": round(r["avg_cc"] or 0, 2),
                    "total_smells": r["total_smells"] or 0,
                    "function_count": r["metric_count"],
                    "date": r["scan_date"],
                }
                for r in reversed(rows)
            ]
        except sqlite3.OperationalError:
            return []
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Code Quality Analyzer — AST self-analysis (Phase 52)"
    )
    parser.add_argument("--project-dir", help="Project root to scan")
    parser.add_argument("--file", help="Analyze a single file")
    parser.add_argument("--project-id", help="ICDEV project ID")
    parser.add_argument("--db-path", help="Override DB path")
    parser.add_argument("--store", action="store_true", help="Write results to DB")
    parser.add_argument("--trend", action="store_true", help="Show trend data")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="JSON output")
    parser.add_argument("--human", action="store_true", help="Colored terminal output")
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else None
    analyzer = CodeAnalyzer(
        project_dir=args.project_dir,
        project_id=args.project_id,
        db_path=db_path,
    )

    if args.trend:
        trend = analyzer.get_trend(args.project_id, db_path=db_path)
        result = {"trend": trend, "data_points": len(trend)}
        if args.json_output:
            print(json.dumps(result, indent=2))
        else:
            for t in trend:
                print(f"  {t['date']}  score={t['avg_maintainability']:.4f}  "
                      f"cc={t['avg_complexity']:.1f}  smells={t['total_smells']}")
        return

    if args.file:
        fp = Path(args.file)
        ext = fp.suffix
        lang = _EXT_TO_LANG.get(ext, "python")
        if lang == "python":
            metrics = analyzer.analyze_python_file(fp)
        else:
            metrics = analyzer.analyze_non_python_file(fp, lang)
        result = {"file": str(fp), "language": lang, "metrics": metrics}
    else:
        result = analyzer.scan_directory()

    if args.store:
        scan_id = result.get("scan_id", f"scan-{uuid.uuid4().hex[:12]}")
        stored = analyzer.store_metrics(result.get("metrics", []), scan_id, db_path)
        result["stored_rows"] = stored

    # Remove full metrics list for summary output
    summary = {k: v for k, v in result.items() if k != "metrics"}
    summary["metric_count"] = len(result.get("metrics", []))

    if args.json_output:
        print(json.dumps(summary, indent=2, default=str))
    elif args.human:
        print(f"\n  Code Quality Scan: {summary.get('project_dir', summary.get('file', ''))}")
        print(f"  Files: {summary.get('files_analyzed', 1)}")
        print(f"  Functions: {summary.get('total_functions', summary.get('metric_count', 0))}")
        print(f"  Avg CC: {summary.get('avg_cyclomatic_complexity', 0)}")
        print(f"  Smells: {summary.get('total_smells', 0)}")
        print(f"  Maintainability: {summary.get('avg_maintainability_score', 0):.4f}")
        if summary.get("stored_rows"):
            print(f"  Stored: {summary['stored_rows']} rows")
        print()
    else:
        print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
