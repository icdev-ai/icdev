# Installing Ghidra for ICDEV (optional decompiler)

`tools/analyzers/ghidra_headless.py` decompiles a compiled artifact with NSA
Ghidra (Apache-2.0) when Ghidra is installed. Without it, every report says
`unavailable` and names what is missing. That is the correct answer on a host
with no Ghidra, not an error.

**Supported:** Ghidra **12.0 or later**, run through **PyGhidra**. Ghidra 12.0
made PyGhidra (CPython 3 over JPype) the default Python engine and moved Jython
out to an optional extension, so ICDEV's export script declares
`# @runtime PyGhidra`. An older Ghidra reports `unavailable` /
`ghidra_version_unsupported` and runs nothing.

**What it needs:**

| Piece | Why |
|---|---|
| Ghidra 12.x | the decompiler |
| a JDK 21 (64-bit) | Ghidra 12 needs JDK 21 or later |
| a Python 3 that can `import pyghidra` | ICDEV starts Ghidra as `<python> -m pyghidra.ghidra_launch --install-dir <home> ghidra.app.util.headless.AnalyzeHeadless ...` |

ICDEV does **not** call `support/pyghidraRun`. On its first run that script
calls `input()` to offer a pip install, and under a pipe that fails with
`EOFError`. ICDEV finds the interpreter itself, in this order:
`ICDEV_GHIDRA_PYTHON`, ICDEV's own Python, the Python ICDEV's venv was created
from, then `python3` / `python` on `PATH`. If you set `ICDEV_GHIDRA_PYTHON`,
ICDEV tries only that one.

## Rails (every run)

- **Authorized targets only.** A run needs an authorization basis: `owned`,
  `licensed` or `customer-authorized`. Pass `--authorization-basis` or set
  `ICDEV_RE_AUTHORIZATION_BASIS`. Without one, the run is refused with
  `error` / `authorization_missing` before the JVM starts. Every report records
  the target's sha256 and the basis under `provenance`.
- **Local only.** Nothing is uploaded. The Ghidra project lives in a temporary
  directory that is removed on every path.
- **Decompiled is not source.** `entry_decompiled` carries
  `evidence_basis: decompiled` and `entry_decompiled_confidence: low`. Treat it
  as a reconstruction, never as recovered source. Functions, imports and
  strings are `observed`.
- **Containment.** The analyzer posture is `sandboxed`
  (docs/security/sandbox-coverage.md, Gap 71).

## Linux: Arch / Omarchy

```bash
icdev omarchy setup --ghidra --yes      # or: python -m tools.cli omarchy setup --ghidra --yes
icdev omarchy status --ghidra
```

This installs `ghidra` and `jdk21-openjdk` from pacman (root, hence `--yes`).
Arch's package installs to `/opt/ghidra`, and ICDEV probes that path without
any configuration. The package also puts `pyghidra` into the system Python.
If no interpreter can import `pyghidra` after that, the step pip-installs it
into ICDEV's Python. It uses the wheel bundled under
`<home>/Ghidra/Features/PyGhidra/pypkg/dist` when the install ships one, and
falls back to `pyghidra>=3.0` from PyPI otherwise.

On the AUR package, `ghidra` is listed under `optdepends`.

Other distributions: unpack the release zip, install a JDK 21, set
`ICDEV_GHIDRA_HOME`, and install pyghidra as described in the Windows section.

## Windows

1. Install a 64-bit **JDK 21**, for example Eclipse Temurin 21. Put its `bin`
   on `PATH` or set `JAVA_HOME`.
2. Download the Ghidra 12.x release zip from
   <https://github.com/NationalSecurityAgency/ghidra/releases>, check its
   SHA-256 against the release page, and unpack it, for example to
   `C:\Tools\ghidra_12.1.4_PUBLIC`.
3. Install pyghidra **from the bundled wheel** into the Python ICDEV runs
   under. This works offline:

   ```powershell
   python -m pip install --no-index -f C:\Tools\ghidra_12.1.4_PUBLIC\Ghidra\Features\PyGhidra\pypkg\dist pyghidra
   ```

   **Check the Python version first.** The bundled JPype wheels cover CPython
   **3.9 through 3.13** (measured in the 12.1.4 zip: `cp39` to `cp313`
   `win_amd64`). On a newer Python, the offline install falls back to
   building JPype from source. In that case, create a 3.12 or 3.13 venv just
   for pyghidra and point `ICDEV_GHIDRA_PYTHON` at it:

   ```powershell
   py -3.12 -m venv C:\Tools\pyghidra-venv
   C:\Tools\pyghidra-venv\Scripts\python.exe -m pip install --no-index -f C:\Tools\ghidra_12.1.4_PUBLIC\Ghidra\Features\PyGhidra\pypkg\dist pyghidra
   ```

4. Set the environment in ICDEV's `.env`, or in the shell:

   ```
   ICDEV_GHIDRA_HOME=C:\Tools\ghidra_12.1.4_PUBLIC
   # only if pyghidra lives in a different interpreter:
   ICDEV_GHIDRA_PYTHON=C:\Path\To\python.exe
   ```

5. Verify:

   ```powershell
   python -m tools.analyzers.ghidra_headless C:\path\to\your\own.exe --authorization-basis owned
   ```

   Measured on Windows 11 on 2026-10-11 with Ghidra 12.1.4, Temurin 21.0.12
   and pyghidra 3.1.0 in a Python 3.12 venv, against an MSVC-built fixture
   (123,904 bytes): `ok`, 427 functions, 68 imports, 223 strings, entry
   decompiled, 27 s. The JVM heap was capped with `_JAVA_OPTIONS=-Xmx1536m`.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `ICDEV_GHIDRA_HOME` | unset | Ghidra install root (the directory holding `support/` and `Ghidra/`). If set, it is the only install ICDEV uses. |
| `ICDEV_GHIDRA_PYTHON` | unset | The interpreter that imports `pyghidra`. If set, it is the only interpreter ICDEV tries. |
| `ICDEV_RE_AUTHORIZATION_BASIS` | unset | `owned` / `licensed` / `customer-authorized`. A run is refused without it. |
| `ICDEV_GHIDRA_ENABLED` | `1` | `0` switches the backend off. The report says `disabled_by_env`. |
| `ICDEV_GHIDRA_TIMEOUT` | `600` | Wall budget in seconds. Ghidra's own analysis timeout is set 60 s below it. |
| `ICDEV_GHIDRA_MAX_CPU` | `2` | `-max-cpu` |
| `ICDEV_GHIDRA_MAX_FUNCTIONS` | `2000` | Cap on the function list. Hitting it gives `truncated`. |
| `ICDEV_GHIDRA_MAX_STRINGS` | `500` | Cap on the string list. Hitting it gives `truncated`. |

## Reading `unavailable`

| `reason` | What to do |
|---|---|
| `not_found` | Install Ghidra, or set `ICDEV_GHIDRA_HOME`. |
| `ghidra_home_invalid` | `ICDEV_GHIDRA_HOME` has no `support/analyzeHeadless` under it. Fix the path. |
| `ghidra_version_undetermined` | No readable `Ghidra/application.properties` at the home. |
| `ghidra_version_unsupported` | Upgrade to Ghidra 12.0 or later. |
| `pyghidra_unavailable` | Install pyghidra (see the pip line above). |
| `ghidra_python_invalid` | `ICDEV_GHIDRA_PYTHON` cannot import pyghidra. |
| `disabled_by_env` | `ICDEV_GHIDRA_ENABLED=0`. |
