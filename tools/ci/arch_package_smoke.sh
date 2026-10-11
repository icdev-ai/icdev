#!/usr/bin/env bash
# CUI // SP-CTI
# Arch Linux package smoke (omx-ci-01) -- run INSIDE an archlinux container.
#
# One script, one phase per CI step, so the job's log says which phase broke and
# the same thing reproduces on a workstation:
#
#   docker run --rm -v "$PWD:/src" -w /src archlinux:base-devel@sha256:<pin> \
#     bash -c 'for p in deps wheel package setup agents; do bash tools/ci/arch_package_smoke.sh $p || exit 1; done'
#
# Phases:
#   deps     pacman -Syu + everything below needs; a non-root `builder` user
#   wheel    build the icdev wheel FROM THIS CHECKOUT (never PyPI)
#   package  render deploy/aur/PKGBUILD from that wheel, makepkg it as `builder`,
#            namcap the PKGBUILD, check .SRCINFO, pacman -U the result
#   setup    throwaway postgres; `icdev omarchy setup --dry-run`, `--yes`, and a
#            second `--yes` that must change nothing (no omarchy -> plain-Arch path)
#   agents   npm-install opencode + pi; capability_matrix --gate, and both must be
#            available with guard_wired MEASURED present
#
# Never `|| true` a phase: a phase that cannot run is red, not green.
set -euo pipefail
# In a container job the workflow-level PYTHONPATH (${{ github.workspace }}) is
# the HOST path, which does not exist in here. Set it per command instead.
unset PYTHONPATH
# bsdtar cannot store the venv's `𝜋thon` symlink under the C locale.
export LANG=C.UTF-8 LC_ALL=C.UTF-8

PHASE="${1:?usage: arch_package_smoke.sh deps|wheel|package|setup|agents}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="${ARCH_SMOKE_WORK:-/tmp/arch-smoke}"
BUILDER=builder
PG_DATA=/var/lib/postgres/data            # tools/cli/omarchy.py PG_DATA
OPENCODE_NPM="opencode-ai@1.18.35"        # the versions omx-spike-01 measured
PI_NPM="@earendil-works/pi-coding-agent@1.1.0"

log() { printf '\n=== arch-smoke[%s] %s\n' "$PHASE" "$*"; }

# `icdev omarchy ... --json` report -> assertions. $1 = report file, $2 = python expr list.
assert_report() {
  python - "$1" "$2" <<'PY'
import json, os, sys
report = json.load(open(sys.argv[1], encoding="utf-8"))
steps = {s["step"]: s for s in report["steps"]}
failed = []
for check in sys.argv[2].split(";"):
    check = check.strip()
    if check and not eval(check, {"report": report, "steps": steps, "os": os}):  # noqa: S307 -- fixed CI expressions
        failed.append(check)
for s in report["steps"]:
    print(f"  [{s['state']:<8}] {s['step']}: {s.get('detail', '')} {s.get('error', '')}")
if failed:
    print("FAILED assertions:\n  " + "\n  ".join(failed))
    sys.exit(1)
PY
}

case "$PHASE" in
deps)
  log "pacman -Syu + build/runtime deps"
  pacman -Syu --noconfirm --needed base-devel python python-build python-pip python-setuptools \
    python-wheel git postgresql pgvector sudo namcap nodejs npm
  id -u "$BUILDER" >/dev/null 2>&1 || useradd -m "$BUILDER"
  ;;

wheel)
  log "build the wheel from $SRC"
  rm -rf "$WORK/dist" && mkdir -p "$WORK/dist"
  # A copy, so the build's egg-info/build dirs never land in the checkout.
  rm -rf "$WORK/src" && cp -a "$SRC" "$WORK/src" && rm -rf "$WORK/src/build" "$WORK/src/dist"
  (cd "$WORK/src" && python -m build --wheel --outdir "$WORK/dist")
  ls -l "$WORK/dist"
  ;;

package)
  WHEEL="$(ls "$WORK"/dist/icdev-*.whl)"
  PKG="$WORK/pkg"
  rm -rf "$PKG" && mkdir -p "$PKG"
  log "render PKGBUILD + .SRCINFO from $(basename "$WHEEL")"
  # emit_aur is the ONE renderer (release.py); import it by path, stdlib-only.
  python - "$SRC" "$WHEEL" "$PKG" <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / "tools" / "installer"))
from release import emit_aur
out = emit_aur(Path(sys.argv[2]), Path(sys.argv[3]))
print(json.dumps(out, indent=2))
sys.exit(0 if out["ok"] else 1)
PY
  cp "$SRC/deploy/aur/icdev.install" "$WHEEL" "$PKG/"   # the wheel is the local source file
  chown -R "$BUILDER:$BUILDER" "$PKG"

  log ".SRCINFO must equal makepkg --printsrcinfo"
  sudo -u "$BUILDER" bash -c "cd '$PKG' && makepkg --printsrcinfo" > "$WORK/srcinfo.makepkg"
  diff -u "$PKG/.SRCINFO" "$WORK/srcinfo.makepkg"

  log "namcap PKGBUILD"
  (cd "$PKG" && namcap PKGBUILD) | tee "$WORK/namcap.txt"
  if grep -q ' E: ' "$WORK/namcap.txt"; then echo "namcap reported errors"; exit 1; fi

  log "makepkg as $BUILDER (integrity check runs against the rendered sha256)"
  sudo -u "$BUILDER" bash -c "cd '$PKG' && makepkg --noconfirm --cleanbuild"

  log "pacman -U"
  pacman -U --noconfirm "$PKG"/icdev-*.pkg.tar.*
  /usr/bin/icdev --help >/dev/null
  test -x /opt/icdev/bin/python
  ;;

setup)
  # A throwaway cluster at the path the setup step probes, started WITHOUT
  # systemd (a container has none). With it ready, setup skips initdb + systemctl
  # and runs the role/database/extension/schema actions for real.
  log "throwaway postgres"
  if [ ! -f "$PG_DATA/PG_VERSION" ]; then
    install -d -o postgres -g postgres "$PG_DATA" /run/postgresql
    sudo -u postgres initdb --locale=C.UTF-8 --encoding=UTF8 --auth=trust -D "$PG_DATA"
  fi
  pg_isready -q || sudo -u postgres pg_ctl -D "$PG_DATA" -l /tmp/pg.log -w start || { cat /tmp/pg.log; exit 1; }

  # As a NON-root user with no ICDEV_PROJECT_ROOT (omx-setup-02): the package
  # root /opt/icdev/.../icdev is root-owned, so .env must land in the user's
  # ~/.config/icdev. sudo is how setup reaches the postgres user, as on a desktop.
  echo "$BUILDER ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/90-arch-smoke
  chmod 0440 /etc/sudoers.d/90-arch-smoke
  PROJECT="$WORK/project"                  # a plain directory: no `icdev init` markers
  rm -rf "$PROJECT" && install -d -o "$BUILDER" -g "$BUILDER" "$PROJECT"
  USER_HOME="$(getent passwd "$BUILDER" | cut -d: -f6)"
  USER_ENV="$USER_HOME/.config/icdev/.env"
  PKG_ENV="$(/opt/icdev/bin/python -c 'import icdev, pathlib; print(pathlib.Path(icdev.__file__).parent / ".env")')"
  rm -f "$USER_ENV"
  # Run from the project like a user would: from the checkout, `python -m icdev...`
  # (the schema step) would import the CHECKOUT's icdev, not the installed one.
  cd "$PROJECT"
  command -v omarchy >/dev/null && { echo "omarchy must be absent for the plain-Arch path"; exit 1; }
  as_user() { sudo -u "$BUILDER" -H env -u ICDEV_PROJECT_ROOT LANG="$LANG" LC_ALL="$LC_ALL" "$@"; }
  # The report is written whatever the exit code, and assert_report checks
  # report['exit'] -- so a failing run prints its steps instead of dying silent.
  setup_report() { local out=$1 rc=0; shift; as_user icdev omarchy setup "$@" --json > "$out" || rc=$?; echo "  exit $rc"; }

  log "icdev omarchy setup --dry-run (as $BUILDER)"
  setup_report "$WORK/dry.json" --dry-run
  assert_report "$WORK/dry.json" "report['exit'] == 0; report['omarchy'] is False;
    'not on Omarchy' in report['host']; steps['postgres']['state'] == 'planned';
    '$USER_ENV' in ' '.join(steps['env']['actions']); not os.path.exists('$USER_ENV')"

  log "icdev omarchy setup --yes (as $BUILDER)"
  setup_report "$WORK/yes.json" --yes
  assert_report "$WORK/yes.json" "report['exit'] == 0; steps['postgres']['state'] == 'done';
    steps['env']['state'] == 'done'; steps['systemd']['state'] == 'n/a'"
  grep -q '^ICDEV_STORAGE_BACKEND=postgresql' "$USER_ENV"
  test "$(stat -c %U "$USER_ENV")" = "$BUILDER"
  if [ -e "$PKG_ENV" ]; then echo "setup wrote into the package dir: $PKG_ENV"; exit 1; fi

  log "icdev omarchy setup --yes again: idempotent"
  setup_report "$WORK/yes2.json" --yes
  assert_report "$WORK/yes2.json" "report['exit'] == 0; steps['postgres']['state'] == 'skipped';
    steps['env']['state'] == 'skipped'"

  log "--start on a package install refuses, and says why"
  setup_report "$WORK/start.json" --yes --start
  assert_report "$WORK/start.json" "report['exit'] == 1; steps['systemd']['state'] == 'failed';
    'needs a source checkout' in steps['systemd']['detail']"

  log "a schema load that died part-way is retried, never reported skipped"
  sudo -u postgres psql -v ON_ERROR_STOP=1 -d icdev -c 'CREATE TABLE public.schema_bootstrap_partial (started_at timestamptz)'
  setup_report "$WORK/partial.json" --dry-run
  assert_report "$WORK/partial.json" "steps['postgres']['state'] == 'planned'"
  sudo -u postgres psql -v ON_ERROR_STOP=1 -d icdev -c 'DROP TABLE public.schema_bootstrap_partial'
  ;;

agents)
  log "install opencode + pi ($OPENCODE_NPM, $PI_NPM)"
  # npm >= 11 blocks install scripts by default; opencode-ai's postinstall is what
  # links its platform binary, so allow exactly that one and nothing else.
  npm install -g --no-fund --no-audit --allow-scripts=opencode-ai "$OPENCODE_NPM" "$PI_NPM"
  opencode --version
  pi --version

  # The wheel phase's copy of the checkout: writable (the matrix logs to .logs/),
  # so a read-only workstation mount behaves exactly like the CI checkout.
  TREE="$WORK/src"
  log "capability_matrix --gate ($TREE source, installed venv's dependencies)"
  cd "$TREE"
  PYTHONPATH="$TREE" /opt/icdev/bin/python tools/agents/capability_matrix.py --gate
  PYTHONPATH="$TREE" /opt/icdev/bin/python tools/agents/capability_matrix.py --json > "$WORK/matrix.json"
  python - "$WORK/matrix.json" <<'PY'
import json, sys
m = json.load(open(sys.argv[1], encoding="utf-8"))
bad = []
for name, entry in sorted(m["adapters"].items()):
    if entry.get("error"):
        bad.append(f"{name}: {entry['error']}")
        continue
    gw = entry["capabilities"]["guard_wired"]["actual"]
    print(f"  {name:<18} available={entry['available']!s:<5} guard_wired={gw}")
for name in ("opencode_cli", "pi_cli"):
    entry = m["adapters"].get(name) or {}
    if not entry.get("available"):
        bad.append(f"{name}: binary installed but adapter reports not available")
    if (entry.get("capabilities") or {}).get("guard_wired", {}).get("actual") != "present":
        bad.append(f"{name}: guard_wired is not measured present")
if m.get("overclaimed"):
    bad.append(f"overclaimed: {m['overclaimed']}")
if bad:
    print("FAILED:\n  " + "\n  ".join(bad))
    sys.exit(1)
PY
  ;;

*)
  echo "unknown phase: $PHASE" >&2
  exit 2
  ;;
esac
