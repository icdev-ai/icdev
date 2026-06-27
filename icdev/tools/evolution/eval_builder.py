# CUI // SP-CTI
"""NOVA SELA — Eval Dataset Builder.

Builds evaluation datasets for skill evolution from three sources (priority order):
  1. Golden JSONL files (hand-curated ground truth)
  2. Kanban task history (real execution outcomes)
  3. Synthetic LLM-generated examples (cold-start fallback)

Inspired by Hermes hermes-agent-self-evolution dataset_builder.py.

Output: EvalDataset with train / val / holdout splits.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
GOLDEN_DIR = BASE_DIR / "context" / "evolution" / "golden"


@dataclass
class EvalExample:
    """A single evaluation example for skill fitness scoring."""
    example_id: str
    task_input: str            # what the agent was asked to do
    expected_behavior: str     # rubric (not exact text — describes what good looks like)
    difficulty: str            # easy | medium | hard
    category: str              # the skill's main concern area
    source: str                # golden | kanban | synthetic
    actual_output: str = ""    # filled in during evaluation
    score: float = 0.0         # filled in by fitness.py


@dataclass
class EvalDataset:
    """Train / val / holdout split for a skill."""
    skill_name: str
    train: list[EvalExample] = field(default_factory=list)
    val: list[EvalExample] = field(default_factory=list)
    holdout: list[EvalExample] = field(default_factory=list)

    @property
    def all_examples(self) -> list[EvalExample]:
        return self.train + self.val + self.holdout

    def is_sufficient(self, min_examples: int = 6) -> bool:
        return len(self.all_examples) >= min_examples


# ────────────────────────────────────────────────────────────────────────────
# Source 1: Golden JSONL
# ────────────────────────────────────────────────────────────────────────────


def _load_golden(skill_name: str) -> list[EvalExample]:
    path = GOLDEN_DIR / f"{skill_name}.jsonl"
    if not path.exists():
        return []
    examples = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            examples.append(EvalExample(
                example_id=obj.get("id", uuid.uuid4().hex[:8]),
                task_input=obj["task_input"],
                expected_behavior=obj["expected_behavior"],
                difficulty=obj.get("difficulty", "medium"),
                category=obj.get("category", "general"),
                source="golden",
            ))
    except Exception as exc:
        logger.warning("[eval_builder] golden load failed for %s: %s", skill_name, exc)
    return examples


# ────────────────────────────────────────────────────────────────────────────
# Source 2: Kanban task history
# ────────────────────────────────────────────────────────────────────────────


def _load_kanban(skill_name: str, limit: int = 30) -> list[EvalExample]:
    """Mine completed kanban tasks whose skill matches skill_name."""
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        # skill_name maps to task_type or source column
        rows = conn.execute(
            """
            SELECT id, title, description, status, failure_count
              FROM kanban_tasks
             WHERE (task_type = %s OR source LIKE %s)
               AND status IN ('done', 'failed', 'dismissed')
             ORDER BY updated_at DESC
             LIMIT %s
            """,
            (skill_name, f"%{skill_name}%", limit),
        ).fetchall()

        examples = []
        for row in rows:
            if isinstance(row, dict):
                tid, title, desc, status, fail_count = (
                    row["id"], row["title"], row["description"] or "",
                    row["status"], row.get("failure_count", 0),
                )
            else:
                tid, title, desc, status, fail_count = row[0], row[1], row[2] or "", row[3], row[4] or 0

            outcome = "success" if status == "done" and fail_count == 0 else "needs_improvement"
            difficulty = "hard" if fail_count > 1 else ("medium" if fail_count == 1 else "easy")
            expected = (
                "Task completes successfully with all acceptance criteria met and no failures."
                if outcome == "success"
                else "Task should complete without failures. Prior attempt failed — identify and fix root cause."
            )
            examples.append(EvalExample(
                example_id=tid[:12],
                task_input=f"{title}\n\n{desc[:400]}",
                expected_behavior=expected,
                difficulty=difficulty,
                category="kanban",
                source="kanban",
            ))
        return examples
    except Exception as exc:
        logger.warning("[eval_builder] kanban load failed: %s", exc)
        return []


# ────────────────────────────────────────────────────────────────────────────
# Source 3: Synthetic (LLM-generated, cold-start fallback)
# ────────────────────────────────────────────────────────────────────────────


def _generate_synthetic(skill_name: str, skill_text: str, count: int = 8) -> list[EvalExample]:
    """Generate synthetic eval examples from skill text using LLM."""
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
        router = LLMRouter()
        prompt = (
            f"You are an eval dataset generator for the ICDEV™ platform.\n\n"
            f"Skill name: {skill_name!r}\n\n"
            f"Skill instructions (excerpt):\n{skill_text[:1200]}\n\n"
            f"Generate {count} diverse evaluation examples for this skill. "
            f"Each example should be a realistic task scenario that tests whether an AI agent "
            f"correctly follows this skill. Include a mix of easy, medium, and hard scenarios.\n\n"
            f"Output as a JSON array where each item has:\n"
            f"  task_input: the prompt the agent receives\n"
            f"  expected_behavior: rubric describing what a good response looks like\n"
            f"  difficulty: easy|medium|hard\n"
            f"  category: the skill aspect being tested\n\n"
            f"Only output the JSON array. No preamble."
        )
        req = LLMRequest(messages=[{"role": "user", "content": prompt}], max_tokens=1500, temperature=0.7)
        resp = router.invoke("code_generation", req)
        if not resp or not resp.content:
            return []
        import re
        match = re.search(r"\[.*\]", resp.content, re.DOTALL)
        if not match:
            return []
        items = json.loads(match.group())
        examples = []
        for i, item in enumerate(items[:count]):
            examples.append(EvalExample(
                example_id=f"syn-{skill_name[:6]}-{i:02d}",
                task_input=item.get("task_input", ""),
                expected_behavior=item.get("expected_behavior", ""),
                difficulty=item.get("difficulty", "medium"),
                category=item.get("category", "general"),
                source="synthetic",
            ))
        return examples
    except Exception as exc:
        logger.warning("[eval_builder] synthetic generation failed: %s", exc)
        return []


# ────────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────────


def build_dataset(
    skill_name: str,
    skill_text: str = "",
    train_ratio: float = 0.6,
    val_ratio: float = 0.2,
    min_examples: int = 6,
    max_synthetic: int = 10,
) -> EvalDataset:
    """
    Build an EvalDataset for a given skill.

    Source priority: golden → kanban → synthetic (only if combined < min_examples).
    """
    dataset = EvalDataset(skill_name=skill_name)

    # Source 1: golden
    all_examples = _load_golden(skill_name)
    logger.debug("[eval_builder] %s: %d golden examples", skill_name, len(all_examples))

    # Source 2: kanban
    kanban = _load_kanban(skill_name)
    all_examples.extend(kanban)
    logger.debug("[eval_builder] %s: %d kanban examples", skill_name, len(kanban))

    # Source 3: synthetic fallback
    if len(all_examples) < min_examples and skill_text:
        synthetic = _generate_synthetic(skill_name, skill_text, count=max_synthetic)
        all_examples.extend(synthetic)
        logger.debug("[eval_builder] %s: %d synthetic examples", skill_name, len(synthetic))

    if not all_examples:
        logger.info("[eval_builder] %s: no examples found", skill_name)
        return dataset

    # Shuffle deterministically (no random.seed — use list index-based split)
    all_examples = sorted(all_examples, key=lambda e: e.example_id)

    n = len(all_examples)
    n_train = max(1, int(n * train_ratio))
    n_val = max(1, int(n * val_ratio))

    dataset.train = all_examples[:n_train]
    dataset.val = all_examples[n_train:n_train + n_val]
    dataset.holdout = all_examples[n_train + n_val:]

    logger.info(
        "[eval_builder] %s: %d total (train=%d val=%d holdout=%d)",
        skill_name, n, len(dataset.train), len(dataset.val), len(dataset.holdout),
    )
    return dataset
