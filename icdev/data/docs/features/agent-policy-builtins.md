# Builtin Agent Policies (exa-policy-03)

**CUI // SP-CTI**

The three policies ported from omnigent that ICDEV could not express with pattern
matching alone. They sit in the ALLOW / DENY / ASK chain from
[agent-policy-chain.md](agent-policy-chain.md) (exa-policy-01) and count in the
session state from exa-policy-02.

They are also the first policies that *use* either. That matters for this card
specifically: exa-policy-01 shipped a chain with one policy in it, and
exa-policy-02 shipped a state mechanism with nothing writing to it. Both were one
release away from being the platform's signature defect — declared, registered,
`enabled: true`, and consumed by nothing.

## What each one holds

| Policy | The question it answers | Why a regex cannot |
|--------|------------------------|--------------------|
| `max_tool_calls_per_session` | How many calls has this session already made? | It is a property of the session, not of the call. |
| `git_write_allowlist` | Which branch, in which repo, may this push write? | It is a property of an **argument**, not of the tool name. |
| `risk_score` | How much risk has accrued across this session? | Fifty benign calls and one benign call are the same event to a stateless gate. |

`args/agent_approval_policy.yaml` can say `git push` is irreversible. It cannot
say *"push is fine to `feat/*` but not to `main`"*, because its tiers match a tool
name and its content patterns match a substring of the command — neither is a
statement about where the push lands.

## Instances, not copies

Each is registered as a **factory**. A chain entry carries `params:`, and the
factory builds one configured instance — omnigent's `factory_params` shape:

```yaml
chain:
  - name: git_write_allowlist
    enabled: true
    params:
      repos: ["icdev-ai/*"]
      deny_branches: [main, master, "release/*"]
      allow_branches: ["feat/*", "kanban/*"]
      on_violation: deny
      on_unknown: ask
```

Two repos with different branch rules are two entries, not two copies of a Python
function. And because every level resolves its own chain
(`policy_composition.build_level` → `policy_engine.resolve_chain`), an instance is
configurable per level for free: the server caps a session at 500 calls, a user
caps themselves at 20, and — composition being additive — the stricter wins.

## No threshold has a Python default

`limit`, `ask_at` and `deny_at` are **required**. A missing one raises
`PolicyConfigError`, which `resolve_chain` turns into a DENY naming the error.

This is deliberate and it is the design decision most likely to be "helpfully"
undone later. A default limit in Python is exactly how a configured limit becomes
a number nobody chose: the YAML reads as authoritative, the value actually in
force came from a source file, and the two disagree in silence. The same
reasoning covers two neighbouring cases, both of which are errors rather than
shrugs:

* an **unknown param key** (`dey_branches`) — a rule the operator believes is in
  force and which is not;
* **`params` on a policy that cannot take them** — accepted and ignored is the
  same failure wearing a different hat.

## Switching one off

`enabled: false` on its chain entry, per level. Each is a separate entry, so each
is independently switchable without disturbing the other two.

They ship **enabled**, with values stated in `args/agent_policy_chain.yaml`. That
is the point rather than an oversight: shipping this card's three policies inert
would be the declared-but-never-consumed defect wearing the uniform of the fix
for it. The shipped values are backstops, not budgets — 500 calls, risk 120/400,
and this repo's own branch prefixes.

## Details worth knowing

**A refused call does not accrue.** It never ran, so charging the session for it
would be wrong. The counter is still an over-count in one direction — a call this
policy allows and a *later* policy denies is counted — and that is the safe
direction, because it makes a cap fire sooner rather than later.

**Deny is case-insensitive, allow is case-sensitive.** Not symmetric, on purpose:
a deny list that misses `Main` has a hole in it, and an allow list that accepts
`Feat/x` for `feat/*` has one too. Each is the fail-closed reading of its own
list.

**Unknowable is never `allow`.** A bare `git push` writes the current branch,
whose name is not in the command string. That resolves to `on_unknown` — shipped
as `ask`, because a human can look — and never to allow. Chained commands are
judged per segment, so `git push feat/x && git push main` is denied.

**Risk is weighted by tier.** `tier_weights` keys off the reversibility tier
`approval_gate.classify` already produces, so the dangerous-tool list stays in
`args/agent_approval_policy.yaml` instead of being duplicated here. `unknown` is
weighted *high*, not low — it is what the gate gives anything not explicitly
named, which is precisely what warrants caution.

**No session id means no enforcement, and it says so.** Without one,
`get_session_state` hands out a throwaway state and a per-session limit silently
becomes no limit. `require_session: true` (the shipped default) refuses instead.

**The counters self-check.** `state_updates` are applied by
`policy_composition` — the composed hook. The single-level
`policy_engine.build_policy_hook` carries them without applying them, so a cap
wired through it would report itself enabled and never fire. Rather than leave
that to documentation, both stateful policies notice that their own increments
are not coming back and warn once, naming the cause.

## The by-path CLI trap

`policy_engine.py` publishes itself into `sys.modules` under its canonical name
when run as `__main__`. Without that, running it by path creates a second module
object: `policy_builtins` imports the canonical copy and builds `PolicyDecision`
instances of the canonical class, while `_normalize` in `__main__` checks
`isinstance` against its own — the check fails and every decision normalises to
"returned something that is not a PolicyDecision", a DENY. Silently, and only via
the CLI.

## Verification

```bash
python -m pytest tests/test_agent_policy_builtins.py -v
python tools/agent_runtime/policy_builtins.py --list --json
python tools/agent_runtime/policy_composition.py --evaluate run_command \
    --input '{"command":"git push origin main"}' --session-id demo
```

68 tests. Every policy has an explicit **DENY** test — a policy tested only on
the call it permits is a policy whose enforcement is untested, and enforcement is
the entire feature. The accrual tests drive the real composition rather than
hand-feeding a state dict, because the failure they guard against is precisely
that the increment is never applied.

## Files

| File | Role |
|------|------|
| `tools/agent_runtime/policy_builtins.py` | The three factories |
| `tools/agent_runtime/policy_engine.py` | Factory registry + `params` resolution |
| `args/agent_policy_chain.yaml` | The shipped instances |
| `tests/test_agent_policy_builtins.py` | 68 tests |
