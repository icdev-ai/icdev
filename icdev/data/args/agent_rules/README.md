# Agent detection rule pack — CUI // SP-CTI

Declarative detection rules over the agent activity ICDEV already records
(AGOV/DET). One rule per file. The loader and single-event evaluator live in
`tools/agent_detect/rules.py` (agov-det-03); the sequence evaluator that runs
`sequence:` rules is agov-det-04; findings land in the append-only
`agent_findings` table via `tools/agent_detect/findings.py` (agov-det-05).

## Every rule here is monitor-only, and that is deliberate

**Every rule shipped in this directory sets `enforce: false`.** A rule pack that
blocks on install takes down live sessions the first time it is wrong, and a
detection rule written against a normalized event view *will* be wrong at first —
that is what the monitor period is for. Enforcement is opted into per rule by an
operator, in an operator-controlled directory, and is wired in agov-det-06. A PR
that sets `enforce: true` on a file under `args/agent_rules/` is a mistake, and
`tests/test_agov_rule_pack.py` fails on it.

A finding is a **rule match, not proof of execution**. It records that the
platform observed a pattern in the event stream. It does not establish that a
command ran, that it succeeded, or that its effect was what the rule name says.

## Layout

| Directory      | What it covers                                                |
|----------------|---------------------------------------------------------------|
| `secrets/`     | Reads of credential material                                   |
| `exfil/`       | Movement of data to somewhere the platform does not control    |
| `persistence/` | Changes that survive the session                               |
| `tamper/`      | Changes to the audit trail or to the guardrails themselves     |
| `chains/`      | Multi-step `sequence:` rules — the capability single-action checks cannot express |

## Rule schema

```yaml
id: group.stable_name        # dot-separated, stable — findings cite it forever
version: "1"                 # bump when the matcher's meaning changes
title: One line a reviewer reads first
severity: info|low|medium|high|critical
tags: [T1552.001, ...]       # MITRE ATT&CK ids welcome
enabled: true
enforce: false               # ALWAYS false in this directory
deny_message: shown only if an operator enables enforcement (<= 512 bytes)
expr:                        # exactly one of expr / sequence
  event_type: [file.read]
  file_path_glob: ["**/.env"]
```

Matcher keys operate on the normalized `AgentEvent` (agov-det-01) and the parsed
shell view (agov-det-02): `event_type`, `tool_name`, `actor`, `file_path_glob`,
`command_name`, `command_matches`, `url_matches`, `argv_contains`, and the
`not_`-prefixed negation of each. **Keys within a rule AND together; the list
under one key ORs.**

`sequence:` rules take `within` (duration) and/or `within_events` (count),
2–8 ordered `steps` (each a matcher), and `max_matches`. Steps must match in
order but need not be adjacent, and all candidate events stay inside one
`(session_id, agent, project_id, source)` partition — ICDEV runs many concurrent
sessions against one database, so a cross-session chain would be a false-positive
generator rather than a detection.

## Tuning before enabling enforcement

Rules that depend on the parsed shell view do not fire at all on a command that
did not parse (command substitution, control flow, `eval`). That is the
conservative half of agov-det-02 and is intentional: falling back to substring
matching is the exact fail-open the parsed view exists to fix.
