# Operator enforcement rules — CUI // SP-CTI

**This directory ships with no rule files in it, and that is the shipped state.**

A rule blocks a tool call only when both of these are true:

1. it sets `enforce: true`, and
2. its file lives **here** — in `args/agent_rules_enforce/`, or wherever
   `ICDEV_AGENT_ENFORCE_RULES_DIR` points.

Authority is the directory, not the field. A rule under `args/agent_rules/` is
evaluated identically — same loader, same matcher, same evaluator — but its
matches are forced monitor-only, so flipping `enforce: true` on a file in the
shipped pack (by a bad merge, a rogue edit, or a well-meaning PR) cannot block
anything. `tests/test_agov_gate.py` pins that.

## Opting a rule into enforcement

1. Run it monitor-only first and read what it actually matched:
   `SELECT rule_id, count(*) FROM agent_findings GROUP BY rule_id`.
2. Copy the rule file from `args/agent_rules/<group>/<name>.yaml` into this
   directory. Copy — do not move: the pack is the maintained version, and a
   local edit that diverges is easier to reason about than a deleted upstream.
3. Set `enforce: true` and write a `deny_message` an agent can act on. It is
   shown verbatim to whoever is blocked, so say what to do instead, not what
   went wrong.
4. Bump nothing else. Same `id` is fine and is in fact useful — findings from
   the monitor period and the enforcing period then share a rule id.

Duplicate ids across the two directories are expected and not an error: the
directories are loaded as two separate rulesets. Within one directory a
duplicate id is still an error and the second file is skipped.

## Turning it off

- `ICDEV_AGENT_DETECT=0` removes the whole gate — detection and enforcement —
  from the pre-tool-use path.
- `enabled: false` on one rule takes just that rule out.
- Deleting every file here returns the platform to monitor-only.

## What this cannot do

It cannot loosen anything. The gate runs **after** every hardcoded block in
`.claude/hooks/pre_tool_use.py` (`.env` access, `rm -rf`, append-only table
modification, direct `sqlite3`, the file access tiers, branch deletion, the
worktree path), and it only ever adds a refusal. A rule here that "allows"
something has no effect; there is no allow verb.
