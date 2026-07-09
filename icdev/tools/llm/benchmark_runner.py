from __future__ import annotations

# CUI // SP-CTI
"""Benchmark evaluation runner — tests the active LLM against standard benchmarks.

Benchmarks sourced from DeepSpec (deepseek-ai/DeepSpec) eval_datasets/:
  gsm8k, math500, humaneval, mbpp, mt-bench, alpaca, aime24, aime25, livecodebench

Usage:
    python -m icdev.tools.llm.benchmark_runner --benchmark gsm8k --limit 50 --json
    python -m icdev.tools.llm.benchmark_runner --benchmark all --json
"""

import argparse
import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class BenchmarkDataNotFoundError(FileNotFoundError):
    """Raised when benchmark data file is not available locally."""

    def __init__(self, name: str) -> None:
        self.name = name
        instructions = (
            f"Benchmark data '{name}' not found. Download from DeepSpec:\n"
            f"  gh api repos/deepseek-ai/DeepSpec/contents/eval_datasets/{name}.jsonl "
            f"--jq '.content' | base64 -d > data/benchmarks/{name}.jsonl\n"
            f"Then run with: --data-path data/benchmarks/{name}.jsonl"
        )
        super().__init__(instructions)


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkConfig:
    """Describes a benchmark dataset and how to extract answers from model output."""

    name: str
    description: str
    answer_extractor_type: str  # "last_number" | "boxed" | "code_block" | "letter" | "raw"
    dataset_url: str


@dataclass
class BenchmarkSample:
    """One input/reference pair from a benchmark dataset."""

    id: str
    prompt: str
    reference_answer: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BenchmarkResult:
    """Evaluation result for a single sample."""

    benchmark_name: str
    sample_id: str
    prompt: str
    response: str
    predicted_answer: str
    reference_answer: str
    correct: bool
    latency_ms: float
    model_id: str
    timestamp: str


# ---------------------------------------------------------------------------
# Built-in benchmark registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, BenchmarkConfig] = {
    "gsm8k": BenchmarkConfig(
        name="gsm8k",
        description="Grade school math word problems — answer is last integer after ####",
        answer_extractor_type="last_number",
        dataset_url="deepseek-ai/DeepSpec/eval_datasets/gsm8k.jsonl",
    ),
    "math500": BenchmarkConfig(
        name="math500",
        description="Competition math problems — answer in \\boxed{} expression",
        answer_extractor_type="boxed",
        dataset_url="deepseek-ai/DeepSpec/eval_datasets/math500.jsonl",
    ),
    "humaneval": BenchmarkConfig(
        name="humaneval",
        description="Code generation — extract first python code block",
        answer_extractor_type="code_block",
        dataset_url="deepseek-ai/DeepSpec/eval_datasets/humaneval.jsonl",
    ),
    "mbpp": BenchmarkConfig(
        name="mbpp",
        description="Mostly Basic Python Problems — code generation",
        answer_extractor_type="code_block",
        dataset_url="deepseek-ai/DeepSpec/eval_datasets/mbpp.jsonl",
    ),
    "aime24": BenchmarkConfig(
        name="aime24",
        description="AIME 2024 competition math — answer is last integer",
        answer_extractor_type="last_number",
        dataset_url="deepseek-ai/DeepSpec/eval_datasets/aime24.jsonl",
    ),
    "aime25": BenchmarkConfig(
        name="aime25",
        description="AIME 2025 competition math — answer is last integer",
        answer_extractor_type="last_number",
        dataset_url="deepseek-ai/DeepSpec/eval_datasets/aime25.jsonl",
    ),
    "mt-bench": BenchmarkConfig(
        name="mt-bench",
        description="Multi-turn instruction following — LLM judge scores raw output",
        answer_extractor_type="raw",
        dataset_url="deepseek-ai/DeepSpec/eval_datasets/mt-bench.jsonl",
    ),
    "alpaca": BenchmarkConfig(
        name="alpaca",
        description="Instruction following — LLM judge scores raw output",
        answer_extractor_type="raw",
        dataset_url="deepseek-ai/DeepSpec/eval_datasets/alpaca.jsonl",
    ),
}


# ---------------------------------------------------------------------------
# Answer extractors
# ---------------------------------------------------------------------------

def extract_last_number(text: str) -> str:
    """Find and return the last integer or float in text."""
    matches = re.findall(r"-?\d+(?:\.\d+)?", text)
    return matches[-1] if matches else ""


def extract_boxed(text: str) -> str:
    r"""Find the content of the last \boxed{...} expression in text."""
    matches = re.findall(r"\\boxed\{([^}]*)\}", text)
    return matches[-1] if matches else ""


def extract_code_block(text: str) -> str:
    """Find and return content of the first ```python ... ``` block."""
    match = re.search(r"```python\s*\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Fallback: any ``` block
    match = re.search(r"```\s*\n(.*?)```", text, re.DOTALL)
    return match.group(1).strip() if match else ""


def extract_raw(text: str) -> str:
    """Return text unchanged."""
    return text


_EXTRACTORS = {
    "last_number": extract_last_number,
    "boxed": extract_boxed,
    "code_block": extract_code_block,
    "raw": extract_raw,
    # "letter" for MCQ-style; falls back to last_number pattern on A/B/C/D
    "letter": lambda t: (re.findall(r"\b([A-D])\b", t) or [""])[-1],
}


# ---------------------------------------------------------------------------
# DB schema
# ---------------------------------------------------------------------------

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS benchmark_eval_runs (
    id TEXT PRIMARY KEY,
    benchmark_name TEXT NOT NULL,
    sample_id TEXT NOT NULL,
    model_id TEXT,
    correct INTEGER,
    latency_ms REAL,
    predicted_answer TEXT,
    reference_answer TEXT,
    run_group_id TEXT,
    created_at TEXT DEFAULT (datetime('now'))
)
"""

# PostgreSQL variant (detected at persist time)
_CREATE_TABLE_SQL_PG = """
CREATE TABLE IF NOT EXISTS benchmark_eval_runs (
    id TEXT PRIMARY KEY,
    benchmark_name TEXT NOT NULL,
    sample_id TEXT NOT NULL,
    model_id TEXT,
    correct BOOLEAN,
    latency_ms REAL,
    predicted_answer TEXT,
    reference_answer TEXT,
    run_group_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
)
"""


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class BenchmarkRunner:
    """Evaluates the active LLM against DeepSpec benchmark datasets."""

    def __init__(self, llm_function: str = "benchmark_eval") -> None:
        from icdev.tools.llm.router import LLMRouter
        self._router = LLMRouter()
        self._llm_function = llm_function

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def load_samples(
        self,
        benchmark_name: str,
        limit: int | None = None,
        data_path: str | None = None,
    ) -> list[BenchmarkSample]:
        """Load benchmark samples from a local JSONL file."""
        if data_path is None:
            raise BenchmarkDataNotFoundError(benchmark_name)

        p = Path(data_path)
        if not p.exists():
            raise BenchmarkDataNotFoundError(benchmark_name)

        samples: list[BenchmarkSample] = []
        with p.open(encoding="utf-8") as fh:
            for idx, line in enumerate(fh):
                if limit is not None and idx >= limit:
                    break
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                prompt = obj.get("prompt") or obj.get("question") or obj.get("input") or ""
                ref = obj.get("answer") or obj.get("canonical_solution") or obj.get("output") or ""
                samples.append(BenchmarkSample(
                    id=obj.get("task_id") or obj.get("id") or str(idx),
                    prompt=prompt,
                    reference_answer=str(ref),
                    metadata={k: v for k, v in obj.items() if k not in ("prompt", "question", "input", "answer")},
                ))
        return samples

    # ------------------------------------------------------------------
    # Single sample
    # ------------------------------------------------------------------

    def run_sample(self, sample: BenchmarkSample, benchmark_name: str) -> BenchmarkResult:
        """Invoke the LLM on one sample and return a scored result."""
        from icdev.tools.llm.provider import LLMRequest
        import time

        cfg = _REGISTRY.get(benchmark_name)
        extractor_type = cfg.answer_extractor_type if cfg else "raw"
        extractor = _EXTRACTORS.get(extractor_type, extract_raw)

        request = LLMRequest(
            messages=[{"role": "user", "content": sample.prompt}],
            skip_injection_scan=True,
        )

        t0 = time.monotonic()
        try:
            response = self._router.invoke(self._llm_function, request)
            raw_text = response.content
            model_id = response.model_id
        except Exception as exc:
            raw_text = f"[ERROR] {exc}"
            model_id = ""
        latency_ms = (time.monotonic() - t0) * 1000.0

        predicted = extractor(raw_text)
        correct = _answers_equal(predicted, sample.reference_answer)

        return BenchmarkResult(
            benchmark_name=benchmark_name,
            sample_id=sample.id,
            prompt=sample.prompt,
            response=raw_text,
            predicted_answer=predicted,
            reference_answer=sample.reference_answer,
            correct=correct,
            latency_ms=round(latency_ms, 2),
            model_id=model_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    # ------------------------------------------------------------------
    # Full benchmark
    # ------------------------------------------------------------------

    def run_benchmark(
        self,
        benchmark_name: str,
        limit: int | None = None,
        data_path: str | None = None,
        workers: int = 4,
    ) -> list[BenchmarkResult]:
        """Run a full benchmark with parallel workers."""
        samples = self.load_samples(benchmark_name, limit=limit, data_path=data_path)

        results: list[BenchmarkResult] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(self.run_sample, s, benchmark_name): s
                for s in samples
            }
            for fut in as_completed(futures):
                try:
                    results.append(fut.result())
                except Exception as exc:
                    s = futures[fut]
                    results.append(BenchmarkResult(
                        benchmark_name=benchmark_name,
                        sample_id=s.id,
                        prompt=s.prompt,
                        response=f"[ERROR] {exc}",
                        predicted_answer="",
                        reference_answer=s.reference_answer,
                        correct=False,
                        latency_ms=0.0,
                        model_id="",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                    ))
        return results

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def score(self, results: list[BenchmarkResult]) -> dict[str, Any]:
        """Aggregate results into an accuracy summary dict."""
        if not results:
            return {"accuracy": 0.0, "total": 0, "correct": 0, "avg_latency_ms": 0.0,
                    "benchmark_name": "", "model_id": ""}
        total = len(results)
        correct = sum(1 for r in results if r.correct)
        avg_latency = sum(r.latency_ms for r in results) / total
        return {
            "benchmark_name": results[0].benchmark_name,
            "model_id": results[0].model_id,
            "total": total,
            "correct": correct,
            "accuracy": round(correct / total, 4),
            "avg_latency_ms": round(avg_latency, 2),
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def persist_results(
        self,
        results: list[BenchmarkResult],
        conn: Any = None,
        run_group_id: str | None = None,
    ) -> None:
        """Write results to benchmark_eval_runs table."""
        run_group_id = run_group_id or str(uuid.uuid4())
        _own_conn = conn is None
        try:
            if _own_conn:
                from icdev.tools.db.storage import get_connection
                conn = get_connection()

            # Try to create table — use PG syntax if available, SQLite otherwise
            try:
                conn.execute(_CREATE_TABLE_SQL_PG)
                conn.commit()
            except Exception:
                try:
                    conn.execute(_CREATE_TABLE_SQL)
                    conn.commit()
                except Exception:
                    pass

            for r in results:
                try:
                    conn.execute(
                        """
                        INSERT INTO benchmark_eval_runs
                            (id, benchmark_name, sample_id, model_id, correct,
                             latency_ms, predicted_answer, reference_answer, run_group_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            str(uuid.uuid4()),
                            r.benchmark_name,
                            r.sample_id,
                            r.model_id,
                            r.correct,
                            r.latency_ms,
                            r.predicted_answer,
                            r.reference_answer,
                            run_group_id,
                        ),
                    )
                except Exception:
                    # Try SQLite placeholder style
                    conn.execute(
                        """
                        INSERT INTO benchmark_eval_runs
                            (id, benchmark_name, sample_id, model_id, correct,
                             latency_ms, predicted_answer, reference_answer, run_group_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid.uuid4()),
                            r.benchmark_name,
                            r.sample_id,
                            r.model_id,
                            int(r.correct),
                            r.latency_ms,
                            r.predicted_answer,
                            r.reference_answer,
                            run_group_id,
                        ),
                    )
            conn.commit()
        except Exception:
            pass  # DB not initialized — results not persisted; caller can retry
        finally:
            if _own_conn and conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _answers_equal(predicted: str, reference: str) -> bool:
    """Normalise and compare two answer strings."""
    p = predicted.strip().lower().replace(",", "").replace(" ", "")
    r = reference.strip().lower().replace(",", "").replace(" ", "")
    return p == r


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run DeepSpec benchmark evaluations against the active LLM.")
    p.add_argument("--benchmark", required=True,
                   help=f"Benchmark name or 'all'. Available: {', '.join(_REGISTRY)}")
    p.add_argument("--limit", type=int, default=None, help="Max samples per benchmark")
    p.add_argument("--data-path", dest="data_path", default=None,
                   help="Path to local JSONL file for the benchmark")
    p.add_argument("--json", action="store_true", dest="output_json", help="Emit JSON output")
    p.add_argument("--workers", type=int, default=4, help="Parallel worker threads")
    return p


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    args = _build_parser().parse_args(argv)
    runner = BenchmarkRunner()

    names = list(_REGISTRY) if args.benchmark == "all" else [args.benchmark]
    all_scores: list[dict] = []

    for name in names:
        try:
            results = runner.run_benchmark(
                name,
                limit=args.limit,
                data_path=args.data_path,
                workers=args.workers,
            )
            score = runner.score(results)
            all_scores.append(score)
            runner.persist_results(results)
        except BenchmarkDataNotFoundError as exc:
            if args.output_json:
                all_scores.append({"benchmark_name": name, "error": str(exc)})
            else:
                print(str(exc))

    if args.output_json:
        print(json.dumps(all_scores, indent=2))
    else:
        for s in all_scores:
            if "error" in s:
                print(f"[{s['benchmark_name']}] ERROR: {s['error']}")
            else:
                print(
                    f"[{s['benchmark_name']}] accuracy={s['accuracy']:.2%}  "
                    f"({s['correct']}/{s['total']})  "
                    f"avg_latency={s['avg_latency_ms']:.0f}ms  "
                    f"model={s['model_id']}"
                )


if __name__ == "__main__":
    main()
