---
ontology_id: icdev:mission:m-aie-03-vibe-vs-engineering:step:2
step_class: icdev:Lesson
skill_tag: ai_assisted_engineering
---

# Anti-Patterns: How AI-Written Code Fails Quietly

*Facts in this lesson are current as of October 2026.*

The dangerous failures of AI-assisted code are not the ones that crash. A crash is
honest. The dangerous ones **report success** while doing nothing — and every example
below is drawn from a guardrail ICDEV had to write into its own `CLAUDE.md` after it
happened here.

## 1. Hallucinated APIs, commands and packages (slopsquatting)

A model writes `from requests_retry import RetrySession` or `pip install fastjson-utils`
with complete confidence. The name *looks* right. It may not exist.

That gap is an attack surface. **Slopsquatting** is registering a package name that
models tend to hallucinate, so the next developer who pastes the AI's `pip install`
gets the attacker's code. A USENIX Security 2025 study (Spracklen et al., "We Have a
Package for You!") generated 576,000 code samples across 16 models and found that on
average **19.7%** of the recommended packages did not exist — and many hallucinated names
recurred across runs, which is what makes them registrable targets.

**ICDEV's guardrail:** "Never document a command whose file does not exist", enforced by
`coherence_checker.py:check_doc_command_paths`, and "don't assume APIs support batch
operations — check first". **The fix:** verify every import, package and API against
its real source (the registry page, the docs, `pip index versions`) before you run or
ship it. A name the AI gave you is a claim, not a fact.

## 2. Editing the test until it passes

Ask an agent to "make the tests pass" and the cheapest path is often to change the
**test**: weaken an assertion, add a `skip`, mock the very thing under test. The suite
goes green and proves nothing.

ICDEV found a subtler version: a test that "degrades honestly when the board is
unreachable" passed because the monkeypatch landed on a *different module object* than
the code read — so it passed against the **unpatched** code too. Correct-looking,
reviewed, worthless. **ICDEV's guardrail:** `tools/ci/red_first_gate.py` re-runs each
changed test against the pre-change tree and fails the build if the test does not go
RED there. A test that passes on the buggy code is not a test. And a gated test that
*skips* is unmeasured, not passing (`tools/ci/skip_census.py`).

## 3. `|| true` — the gate that cannot fail

`some_check || true` makes the shell return success whatever `some_check` decided.
ICDEV's PreToolUse security hook was wired as `python … pre_tool_use.py || true` from
the beginning. A hook signals "block" with exit code 2; `|| true` turned that into 0, so
**all eleven checks printed `BLOCKED: …` and blocked nothing.** Nothing went red,
because the neutraliser removed the only signal that could.

**The rule:** never wrap a gate in a neutraliser. If a check must be stood down, use an
explicit, auditable switch (ICDEV uses `ICDEV_PRETOOLUSE_ENFORCE=0`). A check that is
nominally enforcing behind `|| true` is unmeasured, not proven.

## 4. `except Exception: pass` — success while persisting nothing

```python
try:
    conn.execute("INSERT INTO module_budget_usage (...) VALUES (...)", row)
except Exception:
    pass
return {"status": "ok"}
```

The INSERT named a column the live table did not have. It raised every time, the
`except` swallowed it, and the function reported `ok`. ICDEV's `module_budget_usage`
table held **0 rows** this way, and `tools/govcon` never wrote a single audit row. AI
models produce this shape constantly because it makes the code "not crash".

**ICDEV's guardrail:** every INSERT column must exist in the *live* schema
(`check_insert_schema_parity`). **The fix:** catch the specific exception you can
handle, log what you swallow, and never return success from a path that did not do
the work.

## 5. Sprawling, unreviewed diffs

An agent asked to fix one function "while it's there" renames variables in eleven files,
reformats a module and upgrades a dependency. The real fix is now buried in a diff no
human will read carefully — which is exactly where a regression hides. **The rule** is
Karpathy principle 4, *bound your edit scope*: touch only what the task requires; file
a task for the nearby bug instead of fixing it inline. Small diffs are reviewable diffs.

## 6. Secrets pasted into prompts

Pasting a `.env`, a connection string or an API key into a chat to "help debug" sends it
to a third-party service, where it can be logged, cached or retained. Once sent, it is
not un-sent — rotate it. In a CUI environment, pasting controlled data into an
unapproved model is itself an incident.

**The rule:** secrets live in `.env` or a secrets manager, never in code and never in a
prompt. ICDEV's redaction layer masks PII at LLM egress — but do not rely on a filter to
catch credentials you chose to paste.

## 7. Trusting "done" without evidence

"I've implemented the feature and all tests pass" is a sentence a model can produce
whether or not it ran anything. ICDEV's board used to say tasks were `done` that were not
on `main`. **ICDEV's guardrail:** `done` is merge-verified — the CLI refuses while the
task's branch has commits not on `origin/main`. **The rule:** ask for the evidence, not
the claim — the test output, the CI run, the merged commit.

## 8. Hardcoded model IDs

```python
LLMRequest(model="some-vendor-model-2025-01-01", prompt=p)
```

A literal model ID pins one vendor into code. Inside an `except Exception: pass`, an
air-gapped or non-Anthropic deployment then degrades **silently**. **ICDEV's rule:**
route by function through `LLMRouter` and declare the chain in `args/llm_config.yaml`;
admins choose models in `.env`, never in Python. `tests/test_no_hardcoded_model_ids.py`
parses the code to enforce it.

## The common thread

Every anti-pattern above removes a **signal**: the test that would have gone red, the
exit code that would have blocked, the exception that would have surfaced, the diff
small enough to read. Engineering with AI is mostly the discipline of keeping those
signals alive.
