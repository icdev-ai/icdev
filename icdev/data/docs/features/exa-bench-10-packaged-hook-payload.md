# CUI // SP-CTI

# The packaged PreToolUse hook — payload and check subset

**Task:** `#exa-bench-10` · **Card:** EXA — External Adoption · **Landed:** 2026-08-12

## The defect

`icdev/data/claude_bootstrap/claude/hooks/pre_tool_use.py` resolved its
implementation as

```python
REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_CHECKS_PATH = REPO_ROOT / "tools" / "hooks" / "shared_checks.py"
```

and loaded it with `importlib.util.spec_from_file_location` + `exec_module`.

In **this** repo that resolves to `<repo>/tools/hooks/shared_checks.py` and
works. In a project scaffolded by `icdev init` it resolves to
`<project>/tools/hooks/shared_checks.py`, and the bootstrap payload shipped **no
`tools/` directory at all** — 154 files, none under `tools/`, and `BOOTSTRAP_MAP`
in `tools/cli/init.py` had no entry that would create one. So the hook raised
`FileNotFoundError` and exited non-zero on **every tool call in every generated
project**.

What made it invisible: the loader is deliberately not wrapped in `try/except` —
its own docstring says *"a guard that cannot load must fail loudly, not silently
stop guarding"* — but
`icdev/data/claude_bootstrap/claude/settings.json.template` wraps each hook in
`|| true` (10 occurrences), so the shell returned 0 and the failure never
surfaced. The fail-loud design was correct and was being silenced by the
wrapper.

Net effect: every `icdev init` project believed it had a PreToolUse guard and
had none. Reproduced on `main` at `3ae46fd9e`; the hook exited 1 with
`FileNotFoundError: ...\tools\hooks\shared_checks.py`.

## The decision

The card named three defensible options. **Option (b), the documented subset,
was chosen**, hardened so the subset is measured rather than claimed.

| Option | Verdict |
|---|---|
| (a) Full check set | Rejected. `shared_checks.py` itself is stdlib-only, but three of its checks reach into `tools/git/worktree_paths.py`, `tools/quality/review_loop.py` and `tools/agent_detect/gate.py`, which pull in the `tools` compatibility shim and from there most of the platform. Shipping that transitive tree into a user project to power three checks — two of which encode *this repo's* conventions — is a large payload for no benefit. |
| **(b) Documented subset** | **Chosen.** `tools/hooks/shared_checks.py` ships beside the hook. Everything whose dependency is the stdlib, `git`, or `args/` runs. The three that need ICDEV's own modules do not, and are named. |
| (c) Named no-op with a reason printed once | Rejected as the primary behaviour: 8 of the 11 checks work perfectly well in a generated project, and a no-op would throw away a real guard to avoid explaining a partial one. Its *good idea* — never be silently inert — is kept, as `--self-test`. |

### Why the subset is what it is

`icdev init` copies `args/` (315 files, including `file_access_tiers.yaml` and
`agent_egress_policy.yaml`) but no `tools/`. Measured in a real scaffold:

**Active (8/11)** — `check_env_file_access`, `check_dangerous_rm`,
`check_git_danger`, `check_append_only_write`, `check_direct_sqlite_usage`,
`check_branch_deletion`, `check_file_access_tiers`, `check_network_egress`.

**Inert (3/11)** — and each is inert for a reason that is also why shipping it
would be pointless:

| Check | Needs | Why it does not belong in a user project anyway |
|---|---|---|
| `check_worktree_path` | `tools/git/worktree_paths.py` | Enforces *ICDEV's* sanctioned worktree roots. A user project has its own layout. |
| `check_review_loop_precommit` | `tools/quality/review_loop.py` | Runs ICDEV's ruff-gate self-green over staged files. |
| `check_agent_rules` | `tools/agent_detect/gate.py` | Evaluates operator rule packs from `args/agent_rules_enforce/`, plus writes to `agent_findings`. |

All three already fail **open** inside `shared_checks.py` when their module is
absent, so a generated project is not blocked. That is correct behaviour and
completely invisible — which is the platform's signature defect. So:

```bash
python .claude/hooks/pre_tool_use.py --self-test
```

prints the active set, the inert set with the missing dependency for each, and
which copy of the implementation actually loaded. `--minimal` scaffolds (no
`args/`) correctly report `check_file_access_tiers` and `check_network_egress`
as inert too — the report is computed from what is on disk, not declared.

## What changed

1. **`tools/installer/prebuild_bootstrap.py`** — a new `SOURCES` entry ships
   `tools/hooks/shared_checks.py` to `claude/hooks/shared_checks.py`. It sits
   after the `.claude/hooks` directory copy, which would not otherwise pick up
   a file that does not live in that directory.
2. **`.claude/hooks/pre_tool_use.py`** — the loader is generic over a new
   module-level `PAYLOAD_MODULES` tuple and tries two candidates, most
   authoritative first: `<root>/tools/hooks/<name>` (this repo, canonical) then
   `<hook dir>/<name>` (the scaffolded project). Still no `try/except` — but the
   raise now names both candidates and the fix. Adds `payload_status()` and
   `--self-test`.
3. **`tools/workflow/coherence_checker.py::check_bootstrap_parity`** — a
   payload-completeness rule. For every packaged hook it AST-reads
   `PAYLOAD_MODULES` and asserts each named module (a) shipped into
   `claude/hooks/` and (b) is byte-identical to `tools/hooks/<name>`. Derived
   from the hooks, so a second hook that grows a payload is covered by declaring
   it in the tuple its own loader already iterates. Mirrored to
   `icdev/tools/workflow/`.
4. **`args/bootstrap_parity.yaml`** — documents the second rule and why it has
   **no** declaration list and **no** grandfather entry.
5. **`tests/test_bootstrap_hook_payload.py`** — scaffolds `icdev init` into a
   temp directory, runs the hook as Claude Code runs it (JSON on stdin), and
   asserts it exits 0 on a benign call, exits 2 on `rm -rf /` and on `.env`
   access, and reports exactly the three expected inert checks. Two more tests
   drive the gate red (payload absent, payload stale) — a rule that cannot fail
   is not a gate.

## Ordering — this mattered

`#exa-bench-05` removes `|| true` from **the repo's own** `.claude/settings.json`.
If that same removal reached `settings.json.template` before this landed,
generated projects would go from "hook silently does nothing" to "every tool call
fails" — strictly worse. **This change does not touch
`settings.json.template`;** its 10 `|| true` occurrences are unchanged and
deliberately so. With the packaging fixed, removing them is now safe to do
separately.

## Verify

```bash
python tools/workflow/coherence_checker.py --check bootstrap_parity --json
pytest tests/test_bootstrap_hook_payload.py -q
python tools/installer/prebuild_bootstrap.py          # regenerates the payload
```
