# Installing ICDEV on Omarchy / Arch Linux

Three ways to get the `icdev` command onto an Omarchy (or plain Arch) host. Pick
**one**, then run `icdev omarchy setup` — every path ends there.

Arch's system Python is PEP 668 *externally-managed*: `sudo pip install icdev`
is refused, and forcing it with `--break-system-packages` will fight pacman.
All three paths below install ICDEV into an isolated environment instead.

| Path | Command | Where ICDEV lives | Upgrade with |
|------|---------|-------------------|--------------|
| AUR | `yay -S icdev` | venv at `/opt/icdev`, `/usr/bin/icdev*` symlinks | `yay -Syu` |
| mise (Omarchy) | `omarchy-mise-install pipx:icdev icdev` | mise-managed pipx venv, stub in `~/.local/bin` | `mise upgrade pipx:icdev` |
| pipx | `pipx install icdev` | `~/.local/share/pipx/venvs/icdev` | `pipx upgrade icdev` |

## 1. AUR (`yay -S icdev`)

```bash
yay -S icdev
```

The package (`deploy/aur/PKGBUILD` in this repo) builds from the PyPI wheel of
the current release into a venv at `/opt/icdev` and symlinks every entry point
(`icdev`, `icdev-dashboard`, `icdev-mcp`, ...) into `/usr/bin`.

- **depends:** `python`, `git`
- **optdepends:** `postgresql` (primary storage backend; SQLite is the fallback),
  `pgvector` (vector search), `opencode` (default agent harness on Omarchy),
  `ollama` (local LLM inference)

pip fetches ICDEV's Python dependencies from PyPI while the package builds, so
the build needs network access.

## 2. mise (Omarchy's tool manager)

Omarchy manages developer tools with mise; agents are mise stubs in
`~/.local/bin`. Install ICDEV the same way:

```bash
omarchy-mise-install pipx:icdev icdev
```

This uses mise's `pipx:` backend, so ICDEV gets its own venv and no root access
is needed.

## 3. pipx

On any Linux with pipx (`sudo pacman -S python-pipx` on Arch):

```bash
pipx install icdev
pipx ensurepath   # once, if ~/.local/bin is not on PATH yet
```

## Then: `icdev omarchy setup`

```bash
icdev omarchy setup --dry-run        # print the step plan; change nothing
icdev omarchy setup --yes --start    # run it (--yes allows the sudo PostgreSQL steps)
icdev omarchy status                 # per-step state: ok / missing / n/a
```

`setup` is idempotent — re-run it after every upgrade. On a host without the
`omarchy` binary it degrades to plain-Arch behaviour and skips the
Omarchy-only steps. To remove ICDEV, run `icdev omarchy uninstall` **first**
(it reverses MCP, skill, guard and systemd registrations but never touches your
database or `.env`), then remove the package with the tool you installed it with.

## For maintainers: refreshing the AUR package

`deploy/aur/PKGBUILD` and `deploy/aur/.SRCINFO` are **generated** — never edit
`pkgver` or `sha256sums` by hand. `tools/installer/release.py` renders both from
the release wheel on every verified build, so the sha256 is that of the exact
file twine uploads. To re-render from a wheel you already have:

```bash
python tools/installer/release.py --aur-from-wheel dist/icdev-1.2.43-py3-none-any.whl
```

Publishing is then a copy of `deploy/aur/` (`PKGBUILD`, `.SRCINFO`,
`icdev.install`) into the AUR git repository for `icdev`. `release.py` never
pushes to the AUR itself.
