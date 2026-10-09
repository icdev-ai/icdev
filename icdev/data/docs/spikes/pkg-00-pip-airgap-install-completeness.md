# CUI // SP-CTI

# PKG-00 — `pip install icdev` completeness for air-gapped, pip-only installs

**Date:** 2026-07-25
**Trigger:** User ran `pip install icdev` in an air-gap environment. `.claude/` and its
subfolders were absent, no `.env` sample appeared, and `/techwriter` + `/docdrift` never
showed up in the Canvases menu.
**Verdict:** The wheel is **not** broken. The payload ships correctly. The failures are
(1) a scaffolding step nobody knows to run, (2) 52 of 66 components shipping OFF with no
reachable switch, and (3) a release gate that dry-runs the one thing that matters.

---

## 1. Evidence — what the wheel actually contains

Built `icdev-1.2.39-py3-none-any.whl` from the current tree and inspected it directly
(6611 files). The bootstrap payload **is present**:

```
icdev/data/claude_bootstrap/            128 files
  .env.sample
  .env.template
  CLAUDE.md
  mcp.json
  claude/settings.json.template
  claude/commands/                       84
  claude/hooks/                          10
  claude/skills/                         29
icdev/data/args/component_registry.yaml    1
```

So `.claude` content and an env sample **do** ship. They just never reach the user's
project on their own.

---

## 2. Root causes

### Cause 1 — `pip install icdev` scaffolds nothing, by design

The payload lives *inside* the installed package. `icdev init <dir>` is what copies it
out. From `tools/cli/init.py:86-92`:

```python
("data/claude_bootstrap/CLAUDE.md",                    "CLAUDE.md"),
("data/claude_bootstrap/mcp.json",                     ".mcp.json"),
("data/claude_bootstrap/.env.template",                ".env.template"),
("data/claude_bootstrap/claude/commands",              ".claude/commands"),
("data/claude_bootstrap/claude/hooks",                 ".claude/hooks"),
("data/claude_bootstrap/claude/settings.json.template", ".claude/settings.json"),
("data/claude_bootstrap/claude/skills",                ".claude/skills"),
```

`init_project()` then copies `.env.template` → `.env`. All of it works. The user simply
had no way to know the step existed. This is a discoverability defect, not a build defect.

### Cause 2 — 52 of 66 components ship default-OFF, with the switch in a file that does not exist yet

`/techwriter` and `/docdrift` are **sub-pages of the DIC canvas**, and both are wired
correctly:

- routes: `tools/document_intelligence/blueprint.py:589` (`/docdrift`), `:659` (`/techwriter`)
- nav links: `args/component_registry.yaml:887,899`

But the DIC registry entry declares:

```yaml
env_flag: ICDEV_DIC_ENABLED
default_enabled: false
```

Counted across the registry: **66 components, 14 default-ON, 52 default-OFF**
(24 canvases, 15 core extensions, 9 features, 4 child apps). A fresh install therefore
renders 14 of 66 — and since component toggles are read from `.env`, which only exists
after `icdev init`, there was no way to switch anything on.

### Cause 3 — the release smoke test dry-runs the critical step

`tools/installer/build_release.py` runs a genuine 5-stage pipeline
(`sync_package_tree.py --clean` → `validate_package_config.py --gate` → `python -m build`
→ wheel inspection → throwaway-venv smoke test), and `sync_package_tree.py:317-338` does
invoke `prebuild_bootstrap.py`. The design is sound.

The hole: the smoke test calls `icdev init <dir> --list`, which only *reports* what it
would copy. Nothing asserts that files landed, that `.env` was created, or that the
registry loaded its components. A wheel where `icdev init` silently copies zero files
passes today.

---

## 3. Secondary findings

| Finding | Evidence | Impact |
|---|---|---|
| **`icdev enable` cannot reach 21 of 64 registry env flags** | `TOGGLES` in `tools/cli/enable.py` is a hand-maintained dict (45 friendly names → flag lists) that drifted from `component_registry.yaml` | `strategos`, `cpmp`, `innovation`, `forge_academy`, `hitl`, `ontology`, `gameday`, `admin_console`, `standards_catalog`, `govlift`, `cam`, `mc`, `forecast`, `ai_observatory`, `cache_savings`, `sdc_demo`, and the 4 `*_IQE_*` flags have **no CLI path**. Directly violates the CLAUDE.md rule against parallel lists in `enable.py`. |
| **Bootstrap snapshot drifts from live `.claude/`** | live `commands`=97 vs snapshot 84; `hooks`=11 vs 10; `skills`=25 vs 29 (snapshot retains entries that no longer exist) | Only `sync_package_tree.py` → `prebuild_bootstrap.py` refreshes it. A plain `python -m build` skips it and nothing gates the staleness. Released wheels can carry a months-old command set. |
| **Version drift in the field** | user on 1.2.29, repo at 1.2.39 | Ten releases of divergence; symptoms get misattributed to packaging. |
| **`.claude/agents/` is not in `_BOOTSTRAP_MAP`** | `tools/cli/init.py:86-92` | Currently moot (0 files live) but will silently fail to ship the day agents are added. |

---

## 4. Air-gap constraints (pip-only)

Confirmed workable as-is:
- The wheel is fully self-contained — the bootstrap payload is package data, no network fetch.
- `icdev init` is pure `shutil` file copy — no network, no post-install hook.
- Correct profiles: `icdev[dod-il6]` (local LLM only) or `icdev[minimal-airgap]`.
- **Avoid `icdev[full]`** — pulls `google-generativeai` → `google-auth`, not air-gap safe.
  `icdev[full-airgap]` is the everything-profile that stays clean.

---

## 5. Recommendation (decided with the user 2026-07-25)

1. **`icdev setup` — interactive stdlib-only TUI** as the primary on/off surface.
   Registry-driven, so it covers all 66 components including the 21 the CLI cannot
   currently reach. Shows sub-pages under their parent (`DIC → /techwriter, /docdrift`)
   so the exact confusion that triggered this analysis cannot recur. No browser, no
   network — the right shape for air-gap.
2. **`icdev init` prompts for a profile** (minimal / developer / govcloud / dod-il6 / full),
   with `--profile <name>` for non-interactive callers, and writes a **complete commented
   `.env` listing every registry flag** — enabled ones live, the rest present and
   documented, so no feature is ever invisible again.
3. **Make `enable.py` registry-driven** — delete the hand-maintained `TOGGLES` dict.
4. **Harden the release gate** so this cannot regress: real (not `--list`) init into a temp
   dir, assert file counts and `.env` creation, assert the registry loads all components,
   and add a **bootstrap-freshness gate** that fails the build when the snapshot diverges
   from live `.claude/`.

**Non-goals:** enabling all 52 default-OFF components (contradicts `min_il` gating and
adds migration cost at first start); a dashboard settings page as the *primary* surface
(chicken-and-egg on a fresh install where the dashboard may itself be off); any
post-install hook (unreliable in wheels, and hostile in air-gap).
