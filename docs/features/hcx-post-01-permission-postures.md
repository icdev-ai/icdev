# CUI // SP-CTI

# Permission postures — one named selector for the safety knobs (hcx-post-01)

## The problem

The knobs that decide how much damage an agent run can do were independently
settable and independently named:

| Knob | Set by | Read by |
|---|---|---|
| Approval mode | `ICDEV_SAG_APPROVAL_MODE` | `tools/agent_runtime/safety.py` |
| Command-approval mode | `ICDEV_AGENT_APPROVAL_MODE` | `tools/agent_runtime/approval_gate.py` |
| Mutation gate | `ICDEV_SAG_ALLOW_MUTATION` | `tools/agent_runtime/dispatch.py` |
| Sandbox confinement | `ICDEV_CODEX_SANDBOX` | `tools/agents/adapters/codex_cli.py` |

Nothing named the *combination*. Four variables could be in any of sixteen
states, and no artifact recorded which state a given run was in — so "what
posture was that run under?" had no answer, only four separate lookups whose
result was already gone by the time anybody asked.

## What shipped

`args/permission_postures.yaml` names two combinations and nothing else. It is a
**selector**, not a new control surface: it introduces no knob that did not
already exist, and no key that no module reads.

| Posture | Sandbox | Approval | Command gate | Mutation |
|---|---|---|---|---|
| `workspace-write` (default) | `workspace-write` | `manual` | `enforce` | denied |
| `danger-full-access` | `danger-full-access` | `off` | `off` | allowed |

`workspace-write` reproduces exactly the values the platform shipped before
postures existed, so adopting the layer changes no behaviour on its own.

## Precedence — the non-negotiable part

`tools/agent_runtime/config.py` already implemented
`explicit call argument > environment variable > YAML > built-in default`, and
its module docstring explains why the YAML layer sits *below* the environment:
an operator who exports `ICDEV_SAG_GOALS=0` in a systemd unit must not have it
silently reversed by a file they did not know to look at.

A posture is a **default-setter**. It extends that chain at the bottom:

```
explicit call argument
  >  environment variable
  >  args/agent_runtime.yaml
  >  args/permission_postures.yaml (the selected posture)
  >  built-in default
```

It supplies a value only where neither an environment variable nor an explicit
`agent_runtime.yaml` key already did. Naming a posture can never reverse an
intent stated at a higher layer.

Selecting the posture is itself layered:

```
explicit argument  >  ICDEV_PERMISSION_POSTURE  >  the file's `default:`  >  built-in `workspace-write`
```

An unknown name at any layer is discarded with a warning and resolution
continues to the next — a typo lands on the safe posture, never on an empty one.

### `danger-full-access` takes an explicit human act

The file's own `default:` key may not select a posture flagged
`requires_explicit_selection`, and a file cannot relax that flag on a built-in
posture by re-declaring it. Widening the blast radius takes an act somebody
performed — exporting the variable, or passing the name in code — never a line
in a config file that ships with the repo.

## Not shadowing itself

A posture whose keys are already pinned by `args/agent_runtime.yaml` would be
inert: both files parse, nothing goes red, and selecting a posture silently
moves nothing. The four posture-governed keys therefore ship **commented out**
in `args/agent_runtime.yaml`, with the value stated in the comment. Uncommenting
one pins that knob regardless of posture, which is a legitimate thing to want —
and is why they are commented rather than deleted.

`tests/agent_runtime/test_permission_postures.py` asserts both halves: that the
shipped config declares none of the four, and that each of the four keys changes
a resolved value between the two postures.

## What is deliberately NOT a posture key

`ICDEV_PRETOOLUSE_ENFORCE`, the per-check `ICDEV_<CHECK>_GUARD` switches,
`args/file_access_tiers.yaml` and `args/sandbox_config.yaml` are read by
`.claude/hooks/pre_tool_use.py` — a standalone subprocess that does not import
this config layer — and by the container sandbox executor. Adding keys for them
here without a reader would make the file claim a reach it does not have: the
declared-but-never-consumed defect this platform ships most. Wire the reader
first, then add the key.

## Files

| File | Change |
|---|---|
| `args/permission_postures.yaml` | new — the two postures |
| `args/agent_runtime.yaml` | the four posture-governed keys commented out |
| `tools/agent_runtime/config.py` | `PostureSet`, `load_postures()`, `posture_key=` on `flag()`/`text()`, `sandbox_mode`, posture in `describe()` and the CLI |
| `tools/agent_runtime/dispatch.py` | `mutation_allowed()` consults `allow_mutation` |
| `tools/agents/adapters/codex_cli.py` | `--sandbox` chain gains the posture layer beneath `ICDEV_CODEX_SANDBOX` |
| `tests/agent_runtime/test_permission_postures.py` | 30 tests, gated in `args/ci_test_files/core.txt` |

Each source file is mirrored to its `icdev/` twin; the tests assert the packaged
YAML mirror has not drifted.

## Verify

```bash
python -m tools.agent_runtime.config
ICDEV_PERMISSION_POSTURE=danger-full-access python -m tools.agent_runtime.config
python -m pytest tests/agent_runtime/test_permission_postures.py -q
```

## Follow-on

`hcx-post-02` records posture selection as operator intent, separately from the
knobs — `describe()["resolved"]["posture"]` already reports the name and the
layer that chose it, which is the seam that task builds on.
