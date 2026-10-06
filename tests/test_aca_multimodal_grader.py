# CUI // SP-CTI
"""m-t1-11-multimodal step 2 is gradeable, and its grader discriminates.

The step was step_type='coding' with no starter and no test, so Run always answered
"This step has no verification test, so it cannot be graded" — a coding step nobody
could complete. It now ships a sandbox starter (simulate_vision_call stands in for
the vision API) and a grader that runs after the learner's code in one namespace.

Pinned in the shape of tests/test_aca_vv_integrity_refusal.py: a grader is only
worth having if it REFUSES — the untouched starter, an empty file, and the
`sys.exit(0)` bypass must all fail, and a solution written from the lesson must pass.
The DB-row half is pinned too: _seed_steps is INSERT OR IGNORE and the discovered-
asset reconcile never reaches this catalogue slug (its prose names m11-multimodal),
so an already-seeded database needs reconcile_builtin_step_assets.
"""
from __future__ import annotations

import pytest

from _academy_conn import academy_conn

from apps.forge_academy.code_runner import run_code
from apps.forge_academy.content_loader import (
    BUILTIN_STEPS,
    CONTENT_ROOT,
    reconcile_builtin_step_assets,
)

SLUG = "m-t1-11-multimodal"


def _step2() -> dict:
    return next(s for s in BUILTIN_STEPS[SLUG] if s["step_num"] == 2)


def _assets() -> tuple[str, str]:
    st = _step2()
    starter = (CONTENT_ROOT / st["starter_code_path"]).read_text(encoding="utf-8")
    test = (CONTENT_ROOT / st["test_code_path"]).read_text(encoding="utf-8")
    return starter, test


# Written from the lesson's method table and the starter's TODOs — nothing more.
SOLUTION_METHODS = '''

class DocumentClassifier(DocumentClassifier):
    def _encode_image(self, image_input):
        if isinstance(image_input, str):
            p = pathlib.Path(image_input)
            data = p.read_bytes()
            ext = p.suffix.lower()
            media_type = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"
        else:
            data, media_type = image_input, "image/png"
        return base64.standard_b64encode(data).decode("utf-8"), media_type

    def _build_messages(self, b64, media_type):
        cats = ", ".join(self.categories)
        prompt = (f"Classify the document into ONE of: {cats}. Reply ONLY with JSON "
                  'having "category", "confidence" and "reason".')
        return [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
            {"type": "text", "text": prompt},
        ]}]

    def classify(self, image_input):
        b64, media_type = self._encode_image(image_input)
        response = simulate_vision_call(self._build_messages(b64, media_type))
        raw = response["content"][0]["text"].strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            return ClassificationResult("unknown", 0.0, "reply was not JSON", False)
        confidence = float(result.get("confidence", 0.0))
        return ClassificationResult(result.get("category", "unknown"), confidence,
                                    result.get("reason", ""), confidence >= self.threshold)
'''


def test_step2_declares_a_starter_and_a_test_that_exist():
    st = _step2()
    assert st["step_type"] == "coding"
    for key in ("starter_code_path", "test_code_path"):
        assert st.get(key), f"step 2 declares no {key}"
        assert (CONTENT_ROOT / st[key]).is_file(), f"{st[key]} does not exist"


def test_the_untouched_starter_fails():
    starter, test = _assets()
    assert run_code(starter, test_code=test)["passed"] is False, (
        "submitting the starter unchanged must not pass — the TODOs are the exercise"
    )


@pytest.mark.parametrize("non_solution", [
    "# nothing\n",
    "import sys; sys.exit(0)\n",
], ids=["empty", "sys-exit-bypass"])
def test_a_non_solution_fails(non_solution):
    _starter, test = _assets()
    assert run_code(non_solution, test_code=test)["passed"] is False


def test_the_starter_plus_an_early_exit_fails():
    starter, test = _assets()
    result = run_code(starter + "\nimport sys; sys.exit(0)\n", test_code=test)
    assert result["passed"] is False


def test_a_hardcoded_answer_fails():
    """The grader's replies are random; a classifier that ignores them cannot pass."""
    starter, test = _assets()
    cheat = starter + SOLUTION_METHODS.replace(
        'result.get("category", "unknown")', '"Government form"'
    )
    assert run_code(cheat, test_code=test)["passed"] is False


def test_a_solution_written_from_the_lesson_passes():
    starter, test = _assets()
    result = run_code(starter + SOLUTION_METHODS, test_code=test)
    assert result["passed"] is True, result["stderr"][-1500:]
    assert "PASS" in result["stdout"]


def test_an_already_seeded_row_gets_the_assets(tmp_path):
    """INSERT OR IGNORE kept the old row; the builtin reconcile must fill and commit it."""
    path = str(tmp_path / "academy.db")
    setup = academy_conn(path)
    setup.executescript(f"""
        CREATE TABLE fa_missions (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE,
          title TEXT, is_active INTEGER DEFAULT 1);
        CREATE TABLE fa_mission_steps (id INTEGER PRIMARY KEY AUTOINCREMENT,
          mission_id INTEGER, step_num INTEGER, title TEXT,
          step_type TEXT DEFAULT 'watch', content_path TEXT,
          starter_code_path TEXT DEFAULT '', test_code_path TEXT DEFAULT '');
        INSERT INTO fa_missions (id, slug, title) VALUES (7, '{SLUG}', 'Multimodal AI');
        INSERT INTO fa_mission_steps (mission_id, step_num, title, step_type)
          VALUES (7, 2, 'Image-in-Prompt', 'coding');
    """)
    setup.commit()
    setup.close()

    writer = academy_conn(path)
    assert reconcile_builtin_step_assets(writer) >= 1
    writer.close()

    reader = academy_conn(path)
    row = reader.execute(
        "SELECT step_type, starter_code_path, test_code_path FROM fa_mission_steps "
        "WHERE mission_id=7 AND step_num=2"
    ).fetchone()
    st = _step2()
    assert tuple(row) == ("coding", st["starter_code_path"], st["test_code_path"])
