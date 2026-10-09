# Phase HGX — Progressive Skill Disclosure

**Task:** `hgx-sess-02`. **Card:** HGX — Harness Agent Parity and Graph Runtime
(`args/projects.yaml`, MANUAL-ONLY, gated on `hgx-gate-00`).
**Governing rule:** extend the existing surface — reuse
`tools/skills/registry.py` and its committed cache; no second skill parser, no
second registry, no turn-time walk of `.agents/skills/`.

## The problem

Skills were invisible to the model.

- No skill tool existed in any bundle in `args/agent_toolsets.yaml`, none in
  `tools/agent_runtime/builtin_tools.py`, none in `tools/ace/agent_tools.py`.
- Nothing injected skill names or descriptions into any prompt.
- The `/skills` slash command (`commands.py::_cmd_skills`) prints names to the
  **human operator's** terminal.

So there was no progressive disclosure because there was no disclosure at all.
The entire skills lifecycle — propose → HITL approve → write `SKILL.md` →
curate — produced artifacts that only a person ever read.

A second, quieter defect sat underneath it. `skills_lifecycle.record_use()` had
**zero callers**. `use_count` never incremented, and `last_activity_at` was only
ever stamped at promotion time by `_register_promoted()`. The curator archives on
30 days of "idle", so it was grading a field nothing wrote: every promoted
auto-skill became archive-eligible exactly 30 days after promotion, however
heavily it had been used. The two defects are the same defect — nothing was
reading skills, so nothing could report having read one.

## What changed

### 1. Two tools, not one — that is what makes it progressive

`tools/agent_runtime/skill_tools.py` (new) exposes a matched pair:

| Tool | Cost | Returns |
|------|------|---------|
| `list_skills` | bounded by skill **count** | `name - description` for every indexed skill |
| `load_skill(name)` | bounded by that one body | one skill's full `SKILL.md` |

`list_skills` never opens a `SKILL.md`. It reads `load_registry()`'s committed
`registry.json` cache, which already holds `name` and `description` per skill.
That is the whole reason the listing can be always-available: its size tracks how
many skills exist, not how long they are. Descriptions are capped at 240
characters each and the listing as a whole at 16 000, so an unbounded skills tree
degrades to a truncation notice rather than to an unbounded prompt.

The agent pays for a body only after it has read the listing and decided which
one it wants. A test asserts this directly — `list_skills` is called with
`_read_skill_text` monkeypatched to raise, and multiplying every skill's recorded
body size by a million must not change the listing by one character.

### 2. `record_use()` finally has a caller

`load_skill()` calls `skills_lifecycle.record_use(resolved_name)` after a
successful read. A body load is the honest definition of "used": it is the moment
a skill's instructions actually enter a model's context. Listing does **not**
count as a use — reading a one-line description is not following a procedure.

The name recorded is the **registry key**, not what the model typed, so
`load_skill("beta")` credits `icdev-auto-beta`. The call is best-effort and
wrapped: a dead database costs the curator a data point, never the turn.

### 3. Three exposure points, one implementation

- `builtin_tools.py` folds in `skill_tools.SCHEMAS`/`HANDLERS`, so the default
  `AgentRuntime` has them without any bundle opt-in.
- `args/agent_toolsets.yaml` gains a `skills` bundle (`mutating: false`) for
  bundle-driven roles.
- `tools/ace/agent_tools.py` offers the same pair to `mode: agent` co-workers,
  opt-in per role by name.

All three resolve to the same two functions. Both tools are read-only, so they
pass the dispatch safety gate unconditionally and are eligible for the agent
loop's parallel read-only dispatch.

## Portability notes

Two OS-agnostic hazards were live here and are handled explicitly.

`registry.json` is a **committed** cache, and the copy on `main` was generated on
Windows — its `path` values look like `.agents\skills\icdev-build\SKILL.md`. On
POSIX that string is a single filename containing backslashes, not three path
segments, so joining it verbatim finds nothing. `_skill_md_path()` normalises
separators before joining and falls back to the conventional
`.agents/skills/<name>/SKILL.md` layout, which also covers a skill whose
frontmatter `name:` differs from its directory name. A test pins the
backslash-separator case.

Reads use `encoding="utf-8", errors="replace", newline=""` — byte-faithful across
platforms — and normalise CRLF afterwards. That second step is a rendering
decision for the prompt, not a rewrite of the file; the file is never written.

The repo root comes from an upward sentinel walk from `__file__`, never
`os.getcwd()`, because the module is mirrored to two directories at different
depths and the runtime is routinely launched from a worktree.

## Verification

```bash
pytest tests/agent_runtime/test_skill_tools.py -v     # 23 tests
pytest tests/agent_runtime/ -q                        # suite green
pytest tests/test_ace_mirror_parity.py -q             # icdev/ mirror in sync
```

Acceptance criteria, and where each is proven:

| Criterion | Test |
|-----------|------|
| An agent can list skills and load one body within a turn | `test_handlers_match_the_agent_loop_contract`, `test_exposed_in_the_builtin_toolset` |
| `record_use()` increments `use_count` on body load | `test_load_skill_increments_use_count`, `test_load_skill_stamps_last_activity` |
| Listing cost is bounded and independent of body size | `test_list_skills_cost_is_independent_of_body_size`, `test_list_skills_never_reads_a_body`, `test_list_skills_caps_total_size` |

## Out of band

`tools/ace/controller.py` and `tools/ace/coworker_thread.py` were re-synced to
`icdev/tools/ace/`. The observability instrumentation added in `f64643ea9` was
never mirrored, so `tests/test_ace_mirror_parity.py` was failing on `main` and on
every branch cut from it. The sync is purely additive — the mirror was missing
lines, not holding any of its own.
