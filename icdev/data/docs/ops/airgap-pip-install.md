# Air-Gap pip-only Install Runbook

Installing ICDEV™ from a wheel in a disconnected (air-gapped) environment, with
**zero cloud dependencies** and no Google auth stack.

> **The one step people miss:** `pip install icdev` only puts the package on your
> `PYTHONPATH`. It does **not** create a project. You must run **`icdev init`**
> afterwards — that is what scaffolds `CLAUDE.md`, `.claude/`, and your `.env`.
> `pip install` alone leaves you with no project and an empty-looking `icdev
> status`. Always run the two together:
>
> ```bash
> pip install icdev          # (offline: see below)
> icdev init my-project      # REQUIRED — scaffolds the project
> ```

---

## 1. Choose the right extra

Pick an **air-gap-safe** extra. The critical rule: **avoid `icdev[full]`** — it
pulls `google-auth`, `google-generativeai`, and `google-cloud-aiplatform` (via
the Gemini/Vertex providers), which are not usable in a disconnected environment
and drag in a heavy transitive tree (including `tensorboard`).

| Install | Includes | Use when |
|---------|----------|----------|
| `icdev[minimal-airgap]` | Local LLM client + search | Smallest footprint; you have a local OpenAI-compatible LLM (Ollama, vLLM, llama.cpp, TGI, LM Studio) |
| `icdev[dod-il6]` | Local LLM + search + security scanning + Network Design Canvas | IL6 / SECRET air-gap deployments |
| `icdev[full-airgap]` | All non-Google providers + search + testing + security + NDC | Everything air-gap safe |
| `icdev[full]` | **everything, incl. Google providers + SaaS** | **NOT air-gap safe — do not use offline** |

All extras except `full` are air-gap safe (no `google-auth`, no `tensorboard`).
The release pipeline asserts this negative on every build
(`tools/installer/build_release.py` step 6), so the promise stays honest as the
dependency tree changes.

## 2. Build a wheelhouse (on a connected machine)

On a machine **with** network access, download the wheel and all its
dependencies for your chosen extra into a directory you can carry across the
air gap:

```bash
mkdir icdev-wheelhouse
pip download 'icdev[dod-il6]' --dest icdev-wheelhouse
# transfer icdev-wheelhouse/ to the air-gapped host (USB, one-way diode, etc.)
```

## 3. Install offline (on the air-gapped host)

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install --no-index --find-links icdev-wheelhouse 'icdev[dod-il6]'
```

`--no-index` guarantees pip never reaches out to the network; everything comes
from the local wheelhouse.

## 4. Scaffold the project (REQUIRED)

```bash
icdev init my-project
cd my-project
```

`icdev init` creates, in the target directory:

- **`CLAUDE.md`** — master instructions for Claude Code
- **`.mcp.json`** — MCP server configuration
- **`.claude/`** — `commands/`, `hooks/`, `skills/`, `settings.json`
- **`args/`, `goals/`, `hardprompts/`, `context/`** — the FORGE orchestration layer
- **`.env`** — a complete, commented file listing **every** canvas/feature flag
  from the component registry (enabled ones live, the rest present and commented
  so nothing is hidden)

Choose an install profile when prompted (or non-interactively):

```bash
icdev init my-project --profile air-gap   # apply the air-gap core profile
icdev init my-project --profile none      # registry defaults, no profile
```

On a non-interactive shell (CI, piped install) `icdev init` falls back to a
sensible default instead of blocking on the prompt (`air-gap` in an air-gap
context, else `local-dev`).

## 5. Turn features on/off

Two browser-free, network-free surfaces — either works fully offline:

```bash
icdev setup                    # interactive TUI: browse every component,
                               # SPACE to toggle, p to apply a profile, w to write .env
icdev enable dic ndc           # flip specific components on
icdev disable ndc              # flip off
icdev status                   # show what's currently enabled
icdev profile apply air-gap    # apply a whole preset from core_profiles.yaml
```

`icdev setup` is the recommended primary surface on a fresh air-gap install: it
needs no browser and no network (a dashboard settings page can't claim that when
the dashboard itself may be disabled). It shows each component's env flag and its
notable sub-pages indented underneath, so you can always connect a page to its
flag.

## 6. Point LLM routing at your local model

Air-gap installs use a local, OpenAI-compatible LLM. In `.env`:

```bash
ICDEV_LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
```

or set `type: openai_compatible` + `base_url` in `args/llm_config.yaml` for
vLLM / llama.cpp / TGI / LocalAI / LM Studio.

---

## Troubleshooting

### "A page or canvas is missing from the menu"

**This is almost always the expected default, not a bug.** Most canvases ship
**default-OFF** so a fresh install stays lean. If `/techwriter`, `/docdrift`, a
BI dashboard, etc. isn't in the nav, its component is simply disabled.

Find and flip its flag:

```bash
icdev setup            # browse every component + sub-pages; the env flag is shown inline
icdev list             # list every toggle name + its env flag(s)
icdev enable dic       # e.g. Document Intelligence Canvas (Tech Writer, DocDrift)
```

Every component's flag is also written (commented if off) into the `.env` that
`icdev init` generated, grouped by kind with its URL and description — so
searching `.env` for the page name finds the flag to set `true`.

### `icdev status` shows an all-off / "not initialized" message

You ran `icdev status` before `icdev init`. `status` now detects a missing
`.env` / `.claude` and prints the exact `icdev init` command to run. Scaffold
first, then re-run `status`.

### The install pulled `google-auth` / `tensorboard`

You installed `icdev[full]` (or an `llm-gemini` / `llm-vertex` / `image-gen`
extra). For air-gap use, reinstall with `icdev[dod-il6]`,
`icdev[full-airgap]`, or `icdev[minimal-airgap]`.

### A canvas is enabled but its page 404s

The component's env flag is on but a prerequisite is off. `icdev setup` warns
when you enable a component whose registry-declared prerequisite is disabled and
offers to enable it too; run it to reconcile.

---

## See also

- [airgap-runbook.md](airgap-runbook.md) — running ICDEV™ outside Claude Code
  (cron, CI/CD, air-gap LLM routing, headless ANVIL)
- [../reference/commands.md](../reference/commands.md) — full CLI command reference
