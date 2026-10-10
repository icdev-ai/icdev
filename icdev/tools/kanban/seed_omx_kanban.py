#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed the OMX project -- ICDEV on Omarchy, harness-agnostic, opencode as the OSS default.

Backs the 2026-10-10 architecture plan (registered as ``omx`` in ``args/projects.yaml``).
ICDEV is the governed control plane; harnesses (opencode, Claude Code, Codex, Pi, ...) are
interchangeable executors. On Omarchy, ICDEV follows ``omarchy default agent``.

Operator decisions: OSS default (opencode; Pi if the spike fails); follow the desktop default;
cloud models OK; guard parity gates AUTONOMOUS dispatch only; PostgreSQL required; AUR package;
upstream-ready; the Omarchy box is the acceptance env and later a second LAN executor (manual).

Usage::

    python tools/kanban/seed_omx_kanban.py            # seed
    python tools/kanban/seed_omx_kanban.py --json     # machine-readable report
    python tools/kanban/seed_omx_kanban.py --dry-run  # print, insert nothing
"""

from __future__ import annotations

import argparse
import json
import sys

# The dispatcher hands the description verbatim to a worker with no other context,
# so the invariants for this surface travel with every task.
CONTEXT = """
PLATFORM INVARIANTS (OMX):
- ICDEV orchestrates harnesses; it is not one. Adapters implement the `AgentAdapter`
  protocol in `tools/agents/adapter_base.py` (available / prepare_prompt / invoke /
  detect_completion / parse_response) and register in `tools/agents/registry.py`.
  Templates: `tools/agents/adapters/codex_cli.py` (most honest capability profile) and
  `goose_cli.py`. Capabilities are DECLARED in `args/agent_capabilities.yaml` and probed by
  `python tools/agents/capability_matrix.py --gate` -- an overclaim is a failure.
- The guard logic lives ONCE in `tools/hooks/shared_checks.py`; the headless entry is
  `tools/airgap/hook_compat.py::run_pre_tool_check`. Never write a second copy of a check.
  Never wrap a guard in `|| true`; arming a check requires
  `python tools/hooks/fire_rate_survey.py --json` first (CLAUDE.md).
- OS- AND LLM-agnostic: pathlib, encoding='utf-8', no model IDs in Python (route through
  `LLMRouter` + `args/llm_config.yaml`), no Windows-only or Linux-only code without the
  other branch. Must pass on windows-latest AND ubuntu CI.
- Omarchy seams (v4.0.4): agents are mise stubs in ~/.local/bin; default agent via
  `omarchy default agent <name>`; skill dirs ~/.agents/skills, ~/.claude/skills,
  ~/.codex/skills, ~/.pi/agent/skills, ~/.gemini/config/skills, ~/.hermes/skills;
  agents-panel collector `omarchy-agent-usage-<id>` writes JSON to
  ~/.local/state/omarchy/agents/usage/. Every Omarchy call must degrade to "not on
  Omarchy" when the `omarchy` binary is absent -- never error.
- Plan of record: C:/Users/schuo/.claude/plans/you-are-a-software-resilient-curry.md
  (design rationale lives in the card description in args/projects.yaml).
- New tool -> row in the right `tools/manifest/<topic>.md` shard; CLI -> docs/reference/commands.md.
- New test files: add a fragment `args/ci_test_files/core.d/<task-id>.txt`; red-first gate applies.
- `tools/` is mirrored under `icdev/tools/` -- `python tools/dx/mirror_parity.py --files <a,b>`.
- Worktree-first, branch `kanban/<task-id>`; never commit on main.
"""


def _t(
    task_id: str,
    title: str,
    description: str,
    acceptance: str,
    *,
    priority: str = "medium",
    task_type: str = "build",
    depends_on: str | None = None,
    status: str = "backlog",
) -> dict:
    spec = {
        "id": task_id,
        "title": title,
        "description": description.strip() + "\n" + CONTEXT.rstrip() + "\n",
        "acceptance_criteria": acceptance.strip(),
        "task_type": task_type,
        "priority": priority,
        "status": status,
        "dispatch_source": "omx_plan_seed",
        "idempotency_key": f"omx::{task_id}",
    }
    if depends_on:
        spec["depends_on_task_id"] = depends_on
    return spec


TASKS: list[dict] = [
    # ------------------------------------------------------------------ gate
    {
        "id": "omx-gate-00",
        "title": "OMX gate -- MANUAL-MODE GATE (held: upstream PR + second-host networking)",
        "description": (
            "RISK: omx-up-01 opens a PR against the external omacom/omarchy repo under the "
            "operator's identity, and omx-host-01 exposes this host's PostgreSQL (WSL Docker, "
            "load-bearing for the CI runners) on the LAN -- an unattended build of either acts "
            "outside this repo with nobody watching.\n"
            "MANUAL GATE. Held in_progress so promote_backlog_to_scheduled never dispatches the "
            "tasks that depend on it. Release omx-up-01 only after omx-ci-01 is done and the "
            "operator has verified `icdev omarchy setup` on the real Omarchy box; release "
            "omx-host-01 last."
        ),
        "task_type": "chore",
        "priority": "low",
        "status": "in_progress",
        "dispatch_source": "omx_plan_seed",
        "idempotency_key": "omx::omx-gate-00",
    },
    # ----------------------------------------------------------------- spike
    _t(
        "omx-spike-01",
        "Spike: can opencode carry ICDEV's guard? (go/no-go for the OSS default)",
        """
opencode (sst/opencode, MIT) is the proposed OSS default harness. Before any adapter is
built, PROVE the two properties the plan depends on. Scratch work goes under
`docs/research/omx-spike-01/` (a findings .md plus the minimal plugin/script used);
no production code in this task.

DO:
1. Install opencode (npm `opencode-ai` or the official installer) in a sandbox dir.
2. Write a minimal opencode plugin using the `tool.execute.before` hook that REFUSES a
   `bash` tool call whose command contains `rm -rf` (throw / reject). Run
   `opencode run "<prompt that triggers rm -rf in a temp dir>"` and record whether the
   tool call was actually prevented (file still exists) -- not merely logged.
3. From the plugin, shell out to `python -c "from tools.airgap.hook_compat import
   run_pre_tool_check; ..."` with the tool name + input and map its verdict to
   allow/deny. Record the exact input JSON shape opencode passes to the hook
   (tool name, args keys) and the mapping to ICDEV's `tool_name/tool_input` shape.
4. Capture `opencode run --format json` (or the current equivalent) output for a
   successful run and a failed run: completion signal, final message, token usage,
   exit codes. Note the flags for non-interactive/auto-approve mode and model selection
   (`--model provider/model`).
5. Note opencode's MCP config location/format and its AGENTS.md / skills discovery.
6. If ANY of (2) or (3) fails, repeat steps 2-4 for Pi (`pi` coding agent) and record that
   the default falls back to Pi.

DONE WHEN: `docs/research/omx-spike-01/findings.md` states GO or NO-GO for opencode with
evidence (command, output excerpt, file-still-exists proof) for each property.
""",
        "findings.md exists and records, with reproduced command output: (a) whether an opencode "
        "tool.execute.before plugin PREVENTS a tool call (file survives), (b) a working bridge to "
        "hook_compat.run_pre_tool_check with the input-shape mapping, (c) the run --format json "
        "schema for success and failure, (d) a GO/NO-GO verdict; on NO-GO, the same for Pi.",
        priority="critical",
        task_type="research",
    ),
    # ----------------------------------------------------------------- adapt
    _t(
        "omx-adapt-01",
        "opencode_cli AgentAdapter + capability declaration (probe-verified)",
        """
Build `tools/agents/adapters/opencode_cli.py` implementing `AgentAdapter`, using the
spike's findings (`docs/research/omx-spike-01/findings.md`) for flags and output schema.
Model it on `codex_cli.py`: `available()` = binary on PATH (`shutil.which("opencode")`);
`invoke()` runs `opencode run --format json [--model <from env/router>] <prompt>` in the
task worktree with a timeout; `detect_completion` / `parse_response` parse the JSON
envelope (final text, usage tokens, error).

- Register in `tools/agents/registry.py` (adapter map) and add an `opencode_cli` entry to
  `args/agent_adapters.yaml` (enabled: true; do NOT change fallback_order yet -- that is
  omx-select-01).
- Declare capabilities in `args/agent_capabilities.yaml` HONESTLY (only what the spike
  proved). Add a probe for opencode in `tools/agents/capability_matrix.py` so
  `--gate` passes with no overclaim. Add a `guard_wired` capability key declared `false`
  for now (omx-guard-01 flips it).
- Model ids come from env/`args/llm_config.yaml`, never literals in Python.

TESTS: `tests/agents/test_opencode_cli_adapter.py` -- fake `opencode` binary (a tiny script
on a temp PATH) emitting the recorded JSON; assert command line, parse of success/failure,
timeout handling, `available()` False when absent.
""",
        "opencode_cli adapter registered; ICDEV_AGENT_ADAPTER=opencode_cli selects it; "
        "capability_matrix --gate exits 0 with opencode included; new adapter tests green alone "
        "and in-suite and gated via args/ci_test_files/core.d/omx-adapt-01.txt.",
        priority="high",
        depends_on="omx-spike-01",
    ),
    _t(
        "omx-adapt-02",
        "pi_cli AgentAdapter (second OSS harness) + capability declaration",
        """
Same shape as omx-adapt-01 for the Pi coding agent (`pi`, shipped by Omarchy; skills dir
~/.pi/agent/skills). Use the spike's Pi notes if it recorded them; otherwise discover
Pi's non-interactive/JSON mode first and record it in the adapter docstring.

- `tools/agents/adapters/pi_cli.py`, registry entry, `args/agent_adapters.yaml` entry
  (enabled), honest `args/agent_capabilities.yaml` declaration incl. `guard_wired: false`,
  capability_matrix probe.
- Tests with a fake `pi` binary, as in adapt-01.
""",
        "pi_cli adapter registered and selectable via ICDEV_AGENT_ADAPTER=pi_cli; capability_matrix "
        "--gate exits 0; tests green alone + in-suite, gated via core.d/omx-adapt-02.txt.",
        priority="medium",
        depends_on="omx-select-01",
    ),
    # ----------------------------------------------------------------- guard
    _t(
        "omx-guard-01",
        "opencode guard plugin -> hook_compat.run_pre_tool_check (fire-rate surveyed)",
        """
Ship ICDEV's PreToolUse guard into opencode WITHOUT a second copy of the checks.

- Plugin source in `icdev/data/harness_plugins/opencode/icdev-guard.(js|ts)` (packaged data)
  using `tool.execute.before`: maps opencode's tool call to ICDEV's `{tool_name, tool_input}`
  shape (mapping from the spike), invokes a small Python entry
  `python -m tools.hooks.harness_guard --harness opencode` (new, thin) that calls
  `tools/airgap/hook_compat.py::run_pre_tool_check` and prints a JSON verdict; the plugin
  throws on deny with the check's reason. Fail OPEN on a broken bridge (matches
  pre_tool_use.py main()), but log it.
- Kill switches reuse the existing env names (`ICDEV_PRETOOLUSE_ENFORCE`, per-check
  `ICDEV_<CHECK>_GUARD`); add `=monitor` behaviour if hook_compat supports it.
- Installer helper: `icdev harness install-guard opencode [--project DIR|--global]` copies
  the plugin into opencode's plugin dir.
- BEFORE arming: run `python tools/hooks/fire_rate_survey.py --json` over the transcript
  corpus (the checks are the same ones, so the survey's per-check rates apply); record the
  result in `docs/features/phase-omx-guard.md`.
- Flip `guard_wired` to true for opencode_cli in `args/agent_capabilities.yaml` and add a
  capability_matrix probe that actually invokes the bridge with a known-bad input
  (`rm -rf /`) and asserts deny.

TESTS: `tests/hooks/test_harness_guard.py` -- the Python bridge returns deny for the
dangerous-rm / .env-read / append-only-UPDATE fixtures and allow for a benign command.
""",
        "harness_guard bridge returns deny for >=3 known-bad fixtures and allow for a benign one; "
        "capability_matrix reports opencode_cli guard_wired=present via a live probe; fire-rate "
        "survey result recorded in docs/features/phase-omx-guard.md; tests gated in core.d.",
        priority="critical",
        depends_on="omx-adapt-01",
    ),
    _t(
        "omx-guard-02",
        "Runner refuses AUTONOMOUS dispatch to an adapter whose guard is not verified",
        """
Operator decision: guard parity gates autonomous dispatch only; interactive use is
unaffected.

- In the kanban dispatch path (`tools/genesis/reflexes/kanban.py`, where `pick_default` /
  `ICDEV_AGENT_ADAPTER` is consumed, ~line 5744), before spawning: consult the
  capability matrix for the chosen adapter's `guard_wired`. If not `present`, refuse the
  dispatch with a recorded reason (the task returns to backlog with a note -- reuse the
  existing dispatch-refusal/admission verdict machinery, e.g. `dispatch_admission`, do not
  invent a new status) and try the next adapter in `fallback_order` that IS guarded.
- `claude_cli` counts as guarded via `.claude/settings.json` PreToolUse -- probe that the
  hook entry exists and is not wrapped in `|| true`.
- Report mode first: env `ICDEV_GUARD_PARITY_GATE=report|enforce` (default `report`), with a
  survey command `python -m tools.agents.guard_parity --survey` listing which adapters
  would be refused. Flip default to `enforce` in this task ONLY if the survey shows the
  live default adapter is guarded.

TESTS: `tests/agents/test_guard_parity_gate.py` -- unguarded adapter refused in enforce,
reported in report mode, fallback picks a guarded adapter.
""",
        "In enforce mode an unguarded adapter is never spawned by the runner and the refusal "
        "reason is recorded on the task; report mode logs without refusing; guard_parity "
        "--survey lists every adapter's verdict; tests gated in core.d.",
        priority="critical",
        depends_on="omx-guard-01",
    ),
    # ---------------------------------------------------------------- select
    _t(
        "omx-select-01",
        "pick_default follows `omarchy default agent`; opencode_cli first in fallback_order",
        """
Selection order becomes:
1. `ICDEV_AGENT_ADAPTER` (unchanged, wins)
2. Omarchy desktop default: if `shutil.which("omarchy")`, read the configured default agent
   (find the read path: `omarchy default agent` with no arg, or the config file it writes --
   inspect the omarchy source at github.com/omacom/omarchy, `bin/omarchy-default*`), map
   name -> adapter via a table in `args/agent_adapters.yaml` (`omarchy_agent_map:
   {opencode: opencode_cli, claude: claude_cli, codex: codex_cli, pi: pi_cli, ...}`);
   unmapped names fall through.
3. `per_task_type_preference`
4. `fallback_order: [opencode_cli, claude_cli, local_llm_router]`

- Implement in `tools/agents/registry.py::pick_default`; result carries a `source` field
  (env | omarchy | preference | fallback) for logs.
- Never error when not on Omarchy; cache the omarchy read for the process lifetime.
- IMPORTANT: changing fallback_order changes THIS host's runner default. Keep
  `per_task_type_preference` on this host's `args/agent_adapters.yaml` pointing build/fix at
  claude_cli unless guard-02's survey shows opencode_cli guarded AND available here; state
  the outcome in the PR body.

TESTS: `tests/agents/test_pick_default_omarchy.py` -- fake `omarchy` binary returning each
agent; env override wins; absent binary falls through; unmapped name falls through.
""",
        "pick_default returns the mapped adapter when a fake omarchy reports a default, env still "
        "wins, absence is silent; fallback_order starts with opencode_cli; this host's runner "
        "behaviour change (or non-change) is stated in the PR; tests gated in core.d.",
        priority="high",
        depends_on="omx-guard-02",
    ),
    # -------------------------------------------------------------------- dx
    _t(
        "omx-dx-01",
        "companion: add opencode + pi platforms; icdev init makes AGENTS.md primary",
        """
- `args/companion_registry.yaml`: add `opencode` (instructions AGENTS.md; MCP config in
  opencode's format/location per the spike -- project `opencode.json` and/or
  `~/.config/opencode/`) and `pi` (instructions + skills dir ~/.pi/agent/skills; MCP if
  supported, else `mcp_support: false` honestly).
- Extend `tools/dx/mcp_config_generator.py` / `instruction_generator.py` for the two new
  formats (stdio `icdev-unified` server, same command/args as `.mcp.json`).
- `tools/cli/init.py` + `tools/installer/prebuild_bootstrap.py`: emit `AGENTS.md` as the
  primary, harness-neutral instruction file for scaffolded projects (CLAUDE.md stays, and may
  point at AGENTS.md). Do NOT touch this repo's own CLAUDE.md.
- Note memory: `companion.py --sync --write` rewrites 9 MCP configs with machine-specific
  churn -- run it with explicit `--platforms opencode,pi` and revert unrelated churn.

TESTS: generator tests for both formats; `icdev init` into a tmp dir produces AGENTS.md.
""",
        "companion generates valid opencode and pi configs pointing at icdev-unified over stdio; "
        "`icdev init <tmp>` writes AGENTS.md; tests gated in core.d/omx-dx-01.txt.",
        priority="medium",
        depends_on="omx-spike-01",
    ),
    _t(
        "omx-dx-02",
        "One cross-harness ICDEV skill, symlinked into every harness skill dir",
        """
Mirror Omarchy's own pattern: one source skill, symlinked everywhere.

- Author `icdev/data/skills/icdev/SKILL.md` (Agent Skills frontmatter: name, description)
  teaching any harness how to use ICDEV: the icdev-unified MCP tools, `icdev` CLI, kanban
  claim/release, worktree-first, and where the guard lives. Keep it short; link to docs.
- `icdev skill install [--dirs auto|<list>]`: copy the source to `~/.agents/skills/icdev`
  and symlink into each EXISTING harness dir among ~/.claude/skills, ~/.codex/skills,
  ~/.pi/agent/skills, ~/.gemini/config/skills, ~/.hermes/skills, opencode's skills dir.
  On Windows (no symlink privilege) fall back to a copy and say so. Reuse
  `tools/dx/skill_translator.py` for any per-harness frontmatter differences.
- `--uninstall` removes only links/copies it created (track them in a small manifest file).

TESTS: tmp HOME with 3 fake harness dirs -> links created only for existing dirs; uninstall
removes exactly those.
""",
        "`icdev skill install` creates ~/.agents/skills/icdev and links into each existing harness "
        "skill dir only; uninstall removes exactly what it created; works (copy fallback) on "
        "Windows CI; tests gated in core.d.",
        priority="medium",
        depends_on="omx-dx-01",
    ),
    # ----------------------------------------------------------------- linux
    _t(
        "omx-linux-01",
        "Linux is a first-class host: /start /stop, genesis scheduling, case-sensitive paths",
        """
- `/start` and `/stop` slash commands (`.claude/commands/start.md`, `stop.md`) and anything
  they call: add Linux/macOS branches (no `taskkill /f /im python.exe` -- and per memory never
  blanket-kill; stop exact PIDs from the launcher's pidfiles).
- `tools/genesis/*.ps1/.bat/.xml` Task Scheduler registrations: provide systemd `--user`
  unit + timer equivalents under `scripts/systemd/` and a `python -m tools.genesis.install_units
  --platform linux` that writes them; Windows path unchanged.
- Fix `scripts/icdev.service.template` placeholders (User, %h path, Documentation URL ->
  the GitHub repo).
- Audit case-insensitive path assumptions (comments at `tools/db/storage.py:~103`,
  `tools/kanban/cli.py:~59`, and any `.lower()` path comparisons in identity/worktree guards)
  -- compare with os.path.normcase / Path.resolve, correct on both OSes.
- Grep `tools/` for other unguarded `os.name == 'nt'`-only calls (creationflags, msvcrt,
  taskkill, `C:\\`) outside the already-guarded files and fix or list them in the PR.

TESTS: unit tests for the path comparison helper on both case behaviours; the unit writer
produces parseable systemd files (configparser).
""",
        "start/stop and genesis scheduling have working Linux paths; service template has no "
        "placeholders; path guards are case-correct on Linux and Windows; a residual-Windows-isms "
        "list (or 'none') is in the PR; tests gated in core.d and green on ubuntu + windows CI.",
        priority="medium",
        depends_on="omx-dx-02",
    ),
    # ----------------------------------------------------------------- setup
    _t(
        "omx-setup-01",
        "`icdev omarchy setup` -- one command from fresh Omarchy to a running ICDEV",
        """
New `tools/cli/omarchy.py`, wired as `icdev omarchy setup|status|uninstall` in the icdev CLI.
Idempotent; every step prints what it did / skipped; `--dry-run` supported.

Steps:
1. Detect Omarchy (`omarchy` on PATH) and installed harnesses (`which` each of opencode,
   claude, codex, pi, ...). Not on Omarchy -> still works on plain Arch, skipping desktop bits.
2. PostgreSQL (operator decision: required): if absent, print/run (with confirmation flag
   `--yes`) `sudo pacman -S --needed postgresql pgvector`, `initdb`, enable the service,
   create role + db `icdev` (name must satisfy `icdev_domain.yaml` allowed DBs /
   `assert_identity`), `CREATE EXTENSION vector`; then `icdev-init-db`.
3. `.env`: write `ICDEV_DATABASE_URL`, `ICDEV_STORAGE_BACKEND=postgresql`, leave LLM keys
   for the user (print which providers are configured / missing via the router).
4. MCP: register `icdev-unified` with each installed harness (reuse omx-dx-01 generators,
   user-level configs).
5. Skill: call `icdev skill install` (omx-dx-02).
6. Guard: `icdev harness install-guard opencode --global` if opencode present (omx-guard-01;
   skip with a note if that command is not on main yet).
7. systemd `--user` unit for `tools/genesis/launch.py` (reuse omx-linux-01 unit writer);
   enable + start only with `--start`.
`status` reports each step's state; `uninstall` reverses 4-7 (never drops the database).

TESTS: dry-run plan for (omarchy present / absent) x (pg present / absent) with all external
commands faked; idempotency (second run = all skipped).
""",
        "`icdev omarchy setup --dry-run` prints the correct step plan for all four "
        "omarchy/pg combinations with commands faked; a second run reports every step skipped; "
        "status/uninstall work; tests gated in core.d. Real-box verification is left to the "
        "operator (noted in PR).",
        priority="high",
        depends_on="omx-linux-01",
    ),
    _t(
        "omx-panel-01",
        "Omarchy agents-panel collector: omarchy-agent-usage-icdev",
        """
Omarchy's agents panel picks up any collector named `omarchy-agent-usage-<id>` that writes
one JSON record to `~/.local/state/omarchy/agents/usage/<id>.json` (contract: copy the
`claude`/`codex` collectors in omacom/omarchy `bin/` and its
`shell/plugins/agents/README.md`; record carries limits / tierLabel / usageStatusText).

- Ship `icdev/data/bin/omarchy-agent-usage-icdev` (python entry) reporting: kanban
  in-progress / backlog counts, today's LLM spend from the existing cost tooling
  (`tools/cost/session_cost.py` / cache_savings -- reuse, do not recompute), and the active
  adapter + whether its guard is wired. Unreachable DB -> `stale: true`, never fake zeros.
- `icdev omarchy setup` (omx-setup-01) installs it into ~/.local/bin and adds an
  `assets/icdev.svg` mark.

TESTS: record matches the contract keys; DB-down produces stale record, not zeros.
""",
        "Collector writes a contract-shaped JSON record with real counts, marks stale when the DB "
        "is unreachable (no zeros over an empty denominator), and is installed by omarchy setup; "
        "tests gated in core.d.",
        priority="low",
        depends_on="omx-setup-01",
    ),
    # ------------------------------------------------------------------ dist
    _t(
        "omx-dist-01",
        "AUR package `icdev` (PKGBUILD) + mise/pipx install path",
        """
- `deploy/aur/PKGBUILD` + `.SRCINFO`: builds from the PyPI sdist/wheel of the current
  release into a venv at `/opt/icdev` (Arch Python is PEP 668 externally-managed -- never
  system pip), symlinks `/usr/bin/icdev*` entry points; depends: `python`, `git`;
  optdepends: `postgresql`, `pgvector`, `opencode`, `ollama`. `post_install` message points at
  `icdev omarchy setup`.
- `tools/installer/release.py`: emit an updated PKGBUILD (pkgver + sha256sums) per release
  so publishing to AUR is a copy, not hand-editing. Do NOT publish to AUR in this task.
- Docs `docs/install/omarchy.md`: AUR (`yay -S icdev`), `omarchy-mise-install pipx:icdev icdev`,
  and pipx paths, then `icdev omarchy setup`.

TESTS: release.py renders a PKGBUILD whose pkgver/sha match a fixture wheel; `namcap`-style
lint is omx-ci-01's job.
""",
        "PKGBUILD + .SRCINFO in deploy/aur render from release.py with correct pkgver/sha256; "
        "docs/install/omarchy.md documents AUR, mise and pipx paths; tests gated in core.d.",
        priority="medium",
        depends_on="omx-setup-01",
    ),
    # -------------------------------------------------------------------- ci
    _t(
        "omx-ci-01",
        "Arch Linux CI job: makepkg, icdev omarchy setup --dry-run, adapter probe",
        """
Add a NON-required job to `.github/workflows/icdev-ci.yml` (memory: a red non-required job
on main still wedges the `--merge` door, so make it reliable before it lands, and pin the
container image by digest -- `tools/ci/pin_census.py --check`):
- `container: archlinux@sha256:<pinned>`; `pacman -Syu --noconfirm base-devel python git
  postgresql`; build `deploy/aur/PKGBUILD` with `makepkg` as a non-root user against the
  wheel built in this run; `namcap PKGBUILD`.
- Run `icdev omarchy setup --dry-run` and `icdev omarchy setup --yes` against a throwaway
  postgres in the container (no omarchy binary -> plain-Arch path).
- `python tools/agents/capability_matrix.py --gate` (adapters without binaries must report
  absent, not fail).
Run it 5x on the PR before marking ready (flake survey, per CLAUDE.md crx-test-06 spirit).
""",
        "Arch job green on 5 consecutive PR runs; image pinned by digest (pin_census passes); "
        "makepkg + namcap + setup + capability gate all exercised; job is non-required.",
        priority="medium",
        depends_on="omx-dist-01",
    ),
    # --------------------------------------------------------------- manual
    _t(
        "omx-up-01",
        "MANUAL: draft upstream Omarchy PR (Install > AI entry + ICDEV skill link)",
        """
Operator-driven. After the operator has verified `icdev omarchy setup` on the real Omarchy
box: fork omacom/omarchy and draft (as a DRAFT PR) an Install > AI menu entry that installs
ICDEV via `omarchy-mise-install pipx:icdev icdev` (or the AUR package) and runs
`icdev omarchy setup`, following Omarchy's conventions (bin/ naming, menu script style,
migrations). Keep ICDEV-side changes, if any, in a separate ICDEV PR.
RISK: acts on an external repository under the operator's GitHub identity.
""",
        "A draft PR exists on omacom/omarchy (or the operator's fork) linking to the ICDEV docs, "
        "and the operator has reviewed it; any ICDEV-side change merged separately.",
        priority="low",
        depends_on="omx-gate-00",
    ),
    _t(
        "omx-host-01",
        "MANUAL: Omarchy box as a second executor on the shared board over the LAN",
        """
Operator-driven; the box is on the same WiFi.
1. Expose this host's `icdev-postgres` (WSL Docker) on the LAN: WSL mirrored networking or a
   `netsh interface portproxy` rule + Windows Firewall rule restricted to the Omarchy box IP;
   `pg_hba.conf` host entry for that IP only, `sslmode=require` (self-signed cert ok).
   Memory: WSL is load-bearing for CI runners -- never prune/restart Docker casually.
2. Omarchy `.env`: `ICDEV_DATABASE_URL=postgresql://...@<host-ip>/icdev?sslmode=require`
   (db name `icdev` so `assert_identity` passes).
3. Add `tools/genesis/launch.py --role executor`: starts ONLY the kanban scheduler/dispatcher
   (no dashboard, genesis daemon, pr_watcher) -- this part IS code and goes through a PR.
4. Worktree roots tagged by host (`tools/git/worktree_paths.py`) and a host field in dispatch
   logs, so two hosts never share a path and attribution is clear.
5. Verify: seed 2 trivial tasks -> each claimed by exactly one host via the existing
   `kanban:task:<id>` lease, no duplicate PRs; kill the Omarchy runner mid-task -> lease
   expires, this host picks it up; PG port unreachable from any other LAN IP.
RISK: opens this host's database to the network.
""",
        "Both hosts dispatch from one board with zero duplicate PRs across 2 seeded tasks; "
        "a killed executor's task is recovered by the other host after lease expiry; PG refuses "
        "connections from a non-allowed IP; --role executor merged via PR.",
        priority="low",
        depends_on="omx-gate-00",
    ),
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Seed OMX (ICDEV on Omarchy) tasks")
    ap.add_argument("--json", action="store_true", help="JSON report to stdout")
    ap.add_argument("--dry-run", action="store_true", help="Print, insert nothing")
    args = ap.parse_args(argv)

    if args.dry_run:
        print(json.dumps({
            "dry_run": True,
            "count": len(TASKS),
            "tasks": [{"id": t["id"], "title": t["title"],
                       "depends_on": t.get("depends_on_task_id")} for t in TASKS],
        }, indent=2))
        return 0

    from tools.kanban.task_factory import create_tasks

    created = create_tasks(TASKS)
    report = {
        "created": created,
        "created_count": len(created),
        "submitted_count": len(TASKS),
        "skipped_existing": [t["id"] for t in TASKS if t["id"] not in created],
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Seeded {len(created)}/{len(TASKS)} OMX tasks")
        for tid in created:
            print(f"  + {tid}")
        if report["skipped_existing"]:
            print("  (already present: " + ", ".join(report["skipped_existing"]) + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
