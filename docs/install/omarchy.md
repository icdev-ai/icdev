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
pipx install 'icdev[postgresql]'   # the extra carries the PostgreSQL driver
pipx ensurepath   # once, if ~/.local/bin is not on PATH yet
```

`icdev omarchy setup` configures PostgreSQL, which needs the `psycopg2` driver
in ICDEV's venv. The AUR package installs it. For a pipx install made
without the extra, run `pipx inject icdev psycopg2-binary`. Until the driver is
there, the setup `postgres` step stops with that remedy and changes nothing.

## Then: `icdev omarchy setup`

```bash
icdev omarchy setup --dry-run        # print the step plan; change nothing
icdev omarchy setup --yes            # run it (--yes allows the sudo PostgreSQL steps)
icdev omarchy status                 # per-step state: ok / missing / n/a
```

Run it as **your** user, not root. A package install (AUR, mise, pipx) keeps
the code in a directory you do not own, so `.env` is written to the first of:

1. `$ICDEV_PROJECT_ROOT`, if set;
2. the `icdev init` project you run `setup` from (a directory with `AGENTS.md`
   and `.env.template`, or any of its parents);
3. `~/.config/icdev` (`$XDG_CONFIG_HOME/icdev`).

The `env` step names the file it used. Point ICDEV at that directory with
`ICDEV_PROJECT_ROOT` when it is not an `icdev init` project.

### PostgreSQL: native or docker (`--postgres`)

```bash
icdev omarchy setup --postgres docker   # ICDEV's own PostgreSQL + pgvector container
icdev omarchy setup --postgres native   # pacman + sudo, as above
icdev omarchy setup                     # --postgres auto (the default)
```

`--postgres docker` runs one container, `icdev-postgres`, from the pgvector
image pinned **by digest** in `args/omarchy_postgres.yaml`, with a named volume
(`icdev-postgres-data`), `--restart unless-stopped`, and its port published on
**127.0.0.1 only**. A generated password goes into `.env` as
`ICDEV_PG_PASSWORD` and is never printed; setup writes `ICDEV_STORAGE_BACKEND`
and `ICDEV_PG_HOST/PORT/USER/DATABASE` (database `icdev`), waits for the
server, creates the `vector` extension and loads the ICDEV schema. It needs no
sudo: it uses your docker group. If the daemon refuses you (not in the `docker`
group, or the daemon is down) the step fails with the remedy and changes
nothing:

```bash
sudo usermod -aG docker $USER   # then log out and back in
sudo systemctl enable --now docker
```

Re-running is safe: a running container is `skipped`, a stopped one is
started, and a container running a **different** digest than the pin is
reported, never silently replaced (its volume holds your data).

`auto` never migrates a working host: the mode recorded in `.env`
(`ICDEV_OMARCHY_POSTGRES`) wins, then a healthy native install, then docker
when the daemon answers, else native. `status` names the mode it checked.
Docker mode is Linux-first; on Windows the step reports `n/a`.

**The genesis supervisor needs a source checkout.** `--start` enables the
`icdev-genesis` systemd timer, which runs `tools/genesis/launch.py`. That
supervisor writes its pid lock, logs and data under its own root, and a package
install keeps that root read-only. So on a package install the `systemd` step
reports `n/a`, and `--start` fails with that reason instead of writing a unit
that would crash-loop. To run the supervisor, work from a clone:

```bash
git clone https://github.com/icdev-ai/icdev && cd icdev
python -m tools.cli omarchy setup --yes --start
```

`setup` is idempotent — re-run it after every upgrade. On a host without the
`omarchy` binary it degrades to plain-Arch behaviour and skips the
Omarchy-only steps. To remove ICDEV, run `icdev omarchy uninstall` **first**
(it reverses MCP, skill, guard and systemd registrations but never touches your
database or `.env` — nor the `icdev-postgres` container or its volume), then remove the package with the tool you installed it with.

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
