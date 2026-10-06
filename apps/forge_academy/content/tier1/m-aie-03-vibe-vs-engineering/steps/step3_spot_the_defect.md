---
ontology_id: icdev:mission:m-aie-03-vibe-vs-engineering:step:3
step_class: icdev:Reflect
skill_tag: ai_assisted_engineering
---

# Spot the Defect

Each snippet below was produced by an AI assistant and each one "works" — it runs, and
most would pass a quick glance. Every snippet contains exactly one of the anti-patterns
from the previous step. Read them carefully: the questions that follow name a snippet
by its letter and ask what is wrong with it. You are graded on your answers, and you
need to pass to complete the step.

## Snippet A

```python
def save_order(conn, order):
    try:
        conn.execute(
            "INSERT INTO orders (id, total, region) VALUES (%s, %s, %s)",
            (order.id, order.total, order.region),
        )
        conn.commit()
    except Exception:
        pass
    return {"status": "saved"}
```

## Snippet B

```yaml
# .github/workflows/ci.yml
- name: Security scan
  run: bandit -r src/ --severity-level medium || true
```

## Snippet C

```python
# Original test
def test_discount_caps_at_fifty_percent():
    assert apply_discount(100, 0.8) == 50

# After the agent was asked to "make the failing test pass"
def test_discount_caps_at_fifty_percent():
    assert apply_discount(100, 0.8) == 20
```

## Snippet D

```bash
# Suggested by the assistant to "add retry support"
pip install requests-retry-session
```

```python
from requests_retry_session import RetrySession
```

## Snippet E

```python
def summarise(text):
    req = LLMRequest(model="vendor-large-2025-06-01", prompt=f"Summarise: {text}")
    try:
        return client.complete(req).text
    except Exception:
        return ""
```

## Snippet F

```text
Prompt sent to a public chatbot:

"My deploy fails. Here's my config, can you see what's wrong?
DATABASE_URL=postgresql://admin:<the real production password>@db.internal:5432/app
AWS_SECRET_ACCESS_KEY=<the real 40-character key, pasted in full>"
```

## Snippet G

```text
Task: fix the off-by-one in paginate() in api/list.py

Agent's change summary:
  api/list.py        | fixed off-by-one in paginate()
  api/auth.py        | refactored token parsing
  models/*.py (14)   | renamed fields to snake_case
  requirements.txt   | upgraded sqlalchemy 1.4 -> 2.0
  62 files changed, 1,940 insertions(+), 1,610 deletions(-)
```

## Snippet H

```text
Agent: "Done! I implemented rate limiting and all tests pass. Marking the ticket
complete."

(The session log shows no test command was run, and the branch was never pushed.)
```

## Reflect before you answer

For each snippet, ask the question from step 1: *what does it cost if this is wrong and
nobody notices?* Then ask which **signal** the snippet removed — the exception, the exit
code, the failing assertion, the reviewable diff, or the evidence.
