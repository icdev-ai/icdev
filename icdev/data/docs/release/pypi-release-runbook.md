# PyPI Release Runbook

> Every time you publish `icdev` to PyPI, follow this runbook or let the
> release script do it for you. It's designed to make breakage impossible.

## Quick path — one command

```bash
python tools/installer/build_release.py
```

If it prints `RELEASE READY`, run the upload manually:

```bash
python -m twine upload dist/*
```

That's it. The release script runs the complete 5-step pipeline below and
aborts on any failure. **Never run `python -m build` directly** — you will
ship a broken wheel.

---

## What the release script does

### Step 1: Sync (`tools/installer/sync_package_tree.py --clean`)

Mirrors the full repo into the `icdev/` package directory:

- `tools/` → `icdev/tools/` (excluding `PARENT_ONLY_DIRS`)
- `goals/`, `args/`, `context/`, `hardprompts/` → `icdev/data/`
- `features/`, `docs/` → `icdev/data/`
- Runs `prebuild_bootstrap.py` to populate `icdev/data/claude_bootstrap/`
  with CLAUDE.md, `.mcp.json`, `.env.template`, `.claude/commands/`,
  `.claude/hooks/`, `.claude/settings.json.template`, `.agents/skills/`.

### Step 2: Validate (`tools/installer/validate_package_config.py --gate`)

Runs 7 guards before the wheel is built:

1. **`PARENT_ONLY_DIRS` sync** — matches across `sync_package_tree.py`,
   `pyproject.toml` `[tool.setuptools.packages.find] exclude`, and
   `MANIFEST.in recursive-exclude`.
2. **Required subsystems present** — all 9 canvases, Genesis, Oracle,
   Awareness, RAG, Kanban, WriteGuard, ANVIL, Compliance, MBSE, and all
   infrastructure modules are in `icdev/tools/`.
3. **Claude bootstrap populated** — `CLAUDE.md`, `mcp.json`,
   `.env.template`, `.claude/commands/` (>= 40 slash commands),
   `.claude/hooks/`, `.claude/skills/` all present under
   `icdev/data/claude_bootstrap/`.
4. **FORGE data dirs populated** — `icdev/data/args/`, `goals/`,
   `hardprompts/`, `context/` all exist with files.
5. **Entry points resolve** — every `[project.scripts]` target points to
   a real module (not a dangling reference).
6. **`.env.example` / `.env.sample` sync** — every variable defined in
   `.env.sample` (the comprehensive reference) also exists in
   `.env.example` (what `icdev init` seeds a new project's `.env.template`
   from, and what `tools/awareness/enablement.py` reads as its runtime
   defaults layer). A new canvas/subsystem toggle added only to
   `.env.sample` fails this check instead of silently shipping invisible
   to `pip install` users.
7. **Registry env_flags documented** — every enablement `env_flag`
   declared in `args/component_registry.yaml` (authoritative for canvases
   and components) appears in `.env.example`. This is the root-cause guard
   for the "canvas exists in the wheel but a pip-install user can't find
   it" class of bug (e.g. Document Intelligence / Tech Writer / RFI).

Any failure aborts the release.

### Step 3: Build (`python -m build`)

Cleans `dist/` and runs the standard build. Produces wheel + sdist.

### Step 4: Inspect wheel

Opens the wheel and checks for:

- **Required paths present**: `CLAUDE.md`, all 9 canvases, `genesis/`,
  `writing/` (WriteGuard), `oracle/`, `awareness/`, `rag/`, `anvil/`,
  `cli/init.py`, FORGE data dirs.
- **Forbidden paths absent**: `pulse/`, `proposal_genesis/`, `govcon/`,
  `saas/`, `marketplace/`, `trading/`, `gateway/`, `creative/`,
  `playground/`.

### Step 5: Smoke test in throwaway venv

Creates a fresh venv, installs the wheel, and runs:

- `python -c "import icdev; print(icdev.__version__)"`
- `icdev <tmp-dir> --list`

If any step fails, the release is blocked.

---

## Ship / don't-ship policy

### Ships (framework/core — users need these)

- **All 9 canvases**: boundary, security, data, infra, migration,
  observability, qdc, network (NDC), pipeline (PDC), canvas (orchestrator)
- **Autonomous engine**: genesis (daemon + reflexes), oracle, awareness, kanban
- **Core capabilities**: rag, memory, notifications, writing (WriteGuard)
- **Build frameworks**: anvil, appforge, builder
- **Compliance stack**: compliance, mbse, modernization
- **Infrastructure**: ci, db, dashboard, mcp, llm, testing, observability,
  monitor, workflow, dx, installer, cli
- **FORGE data layers**: args, goals, context, hardprompts, features, docs
- **Claude bootstrap** (93 files): CLAUDE.md, .claude/commands/,
  .claude/hooks/, .claude/skills/, .mcp.json, .env.template

### Does not ship (owner-operated)

Owner's child apps:
- `pulse`, `proposal_genesis`, `govcon`, `rfx`, `autoresearch`, `scout`,
  `creative`

Separate products:
- `trading`, `market_intel` (FathomDesk)

Parent-platform services:
- `saas`, `marketplace`, `gateway`, `playground`

The authoritative list lives in `tools/installer/sync_package_tree.py` as
`PARENT_ONLY_DIRS`. Any change must be mirrored in `pyproject.toml`
`[tool.setuptools.packages.find] exclude` and `MANIFEST.in`
`recursive-exclude`. The validator fails if any of the three drift.

---

## Adding a new subsystem

1. Add the code under `tools/<name>/`.
2. If it's **framework/core** (should ship): add the dir name to
   `REQUIRED_SUBSYSTEMS` in `validate_package_config.py`.
3. If it's a **child app / parent-only** (should NOT ship): add to
   `PARENT_ONLY_DIRS` in `sync_package_tree.py`, `exclude` in
   `pyproject.toml`, and `recursive-exclude` in `MANIFEST.in`.
4. Run `python tools/installer/build_release.py` — it will catch any
   inconsistency.

---

## User install flow (post-release)

```bash
pip install icdev                   # or `pip install icdev --prefix <path>`
cd my-new-project
icdev init                          # scaffolds CLAUDE.md, .claude/, args/, goals/, etc.
# Edit .env to add ANTHROPIC_API_KEY
icdev-init-db                       # initialize databases
icdev-dashboard                     # starts dashboard on :5050
# Open project in Claude Code — CLAUDE.md guides the agent, slash commands work
```
