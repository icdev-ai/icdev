# ICDEV™ Air-Gap Runbook

Operating ICDEV™ inside an air-gapped / IL5+ / SECRET enclave. Covers install,
network config, mTLS, and validation. Referenced from `CLAUDE.md`.

> **New to a pip-only air-gap install?** Start with
> [airgap-pip-install.md](airgap-pip-install.md) — the step-by-step offline
> wheelhouse install, the mandatory `icdev init`, `icdev setup` for toggling
> features, and a troubleshooting section (leading with "a page/canvas is
> missing from the menu → it's default-OFF"). This runbook covers the broader
> enclave operations below.

---

## 1. Install from a private PyPI mirror

`pip install icdev` works unchanged against a private mirror as long as pip is
pointed at it. The common setups:

### Option A — per-invocation
```bash
pip install icdev \
  --index-url https://pypi.internal.gov/simple \
  --trusted-host pypi.internal.gov
```

### Option B — user/site config (preferred for engineers)
```ini
# ~/.pip/pip.conf  (Linux)  or  %APPDATA%\pip\pip.ini  (Windows)
[global]
index-url = https://pypi.internal.gov/simple
extra-index-url = https://pypi.external-mirror.gov/simple
trusted-host = pypi.internal.gov

[install]
trusted-host = pypi.internal.gov
```

### Option C — mTLS-protected mirror
Private Artifactory / Nexus / devpi deployments often require a client
certificate. pip honours these env vars (also accepted as CLI flags):

| Env | pip flag | Purpose |
|---|---|---|
| `PIP_CLIENT_CERT` | `--client-cert` | PEM with both cert and key concatenated (pip reads both from this single file) |
| `PIP_CERT` | `--cert` | CA bundle used to verify the mirror's server cert |
| `PIP_INDEX_URL` | `--index-url` | Mirror URL |

```bash
export PIP_CLIENT_CERT=/etc/pki/icdev/pypi-client.pem   # cert + key, concatenated
export PIP_CERT=/etc/pki/icdev/enterprise-ca.pem
export PIP_INDEX_URL=https://pypi.internal.gov/simple
pip install icdev
```

For CI/CD pipelines that must not write env vars to disk, pass flags directly:
```bash
pip install icdev \
  --index-url https://pypi.internal.gov/simple \
  --client-cert /run/secrets/pypi-client.pem \
  --cert /etc/ssl/certs/enterprise-ca.pem
```

### Option D — fully disconnected (no mirror)
Stage wheels on removable media, then install from the local directory:
```bash
pip download icdev -d /mnt/transfer/wheels/ --platform manylinux2014_x86_64 \
  --python-version 3.12 --only-binary=:all:
# ship the wheelhouse into the enclave
pip install --no-index --find-links /mnt/wheels/ icdev
```

---

## 2. Mode detection

```bash
python -m tools.airgap --detect --json     # reports cloud vs air-gap
python -m tools.airgap --activate          # patches llm_config.yaml routing
```

When `ICDEV_AIRGAP=true` (or the detector flips it), the router refuses any
provider that requires internet egress.

---

## 3. Outbound mTLS (post-install)

The central HTTP client at `tools/http/client.py` reads these env vars and
applies them to every outbound call made via `get_session()` / `request()`:

| Env | Meaning |
|---|---|
| `ICDEV_MTLS_CLIENT_CERT` | Path to client certificate (PEM) |
| `ICDEV_MTLS_CLIENT_KEY` | Path to client private key (PEM) |
| `ICDEV_MTLS_CA_BUNDLE` | CA bundle for server verification |
| `ICDEV_MTLS_VERIFY` | `false` disables TLS verification (testing only) |
| `ICDEV_HTTP_TIMEOUT` | Default timeout in seconds (default 30) |
| `ICDEV_HTTP_PROXY` / `ICDEV_HTTPS_PROXY` | Proxy URLs (HTTP_PROXY / HTTPS_PROXY also honoured) |

Precedence: `ICDEV_MTLS_*` env > per-client config (a2a/eMASS/Xacta accept
both but defer to env when set).

---

## 4. PostgreSQL mTLS

`tools/db/storage.py` builds the libpq SSL connection from these env vars:

| Env | libpq param | Typical value |
|---|---|---|
| `ICDEV_PG_SSLMODE` | `sslmode` | `verify-full` |
| `ICDEV_PG_SSLCERT` | `sslcert` | `/etc/pki/icdev/pg-client.crt` |
| `ICDEV_PG_SSLKEY` | `sslkey` | `/etc/pki/icdev/pg-client.key` |
| `ICDEV_PG_SSLROOTCERT` | `sslrootcert` | `/etc/pki/icdev/pg-ca.crt` |
| `ICDEV_PG_SSLCRL` | `sslcrl` | `/etc/pki/icdev/pg.crl` |

Unset vars fall through to libpq defaults.

---

## 5. Inbound dashboard TLS

To serve the dashboard on HTTPS with optional client-cert verification:

```bash
export ICDEV_DASHBOARD_TLS_CERT=/etc/pki/icdev/dashboard.crt
export ICDEV_DASHBOARD_TLS_KEY=/etc/pki/icdev/dashboard.key
# optional — triggers mTLS (CERT_REQUIRED):
export ICDEV_DASHBOARD_TLS_CA_BUNDLE=/etc/pki/icdev/enterprise-ca.pem
icdev-dashboard --port 5050
```

For production: terminate TLS at nginx/haproxy in front of the dashboard and
leave the app bound to `127.0.0.1:5050` without TLS.

---

## 6. Cron / scheduled tasks

```bash
# /etc/cron.d/icdev-audit — nightly compliance scan
0 2 * * * icdev-user cd /opt/icdev && \
  python -c "
from tools.airgap.hook_compat import get_session_id, run_auto_commit
get_session_id()   # sets CLAUDE_SESSION_ID + ICDEV_SESSION_ID for audit trail
# ... invoke tools here (health_check, bandit, etc.) ...
run_auto_commit('chore: nightly audit auto-commit')
" >> /var/log/icdev/cron.log 2>&1
```

---

## 7. Validation

```bash
python tools/testing/health_check.py --json
python tools/testing/e2e_runner.py --run-all --mode native --json
python -m bandit -r tools/ --severity-level medium
python tools/workflow/coherence_checker.py --all --gate
```

All four should exit 0. The coherence gate is authoritative for pre-merge
readiness; the E2E suite is the sign-off gate.

---

## 8. Air-gap LLM routing

```bash
# .env — forces all routing through local Ollama, no cloud fallback
OLLAMA_BASE_URL=http://localhost:11434
ICDEV_LLM_PROVIDER=ollama
# Also set in args/llm_config.yaml: two_tier.enabled: false
```

Programmatic:
```python
from tools.airgap import is_airgap, activate_airgap
if is_airgap():
    activate_airgap()
```

---

## 9. GitLab CI gate (example)

```yaml
security-scan:
  stage: validate
  script:
    - export ICDEV_AUTO_COMMIT=true
    - python tools/testing/health_check.py --json
    - python -m bandit -r tools/ --severity-level medium
    - python -c "from tools.airgap.hook_compat import run_pre_tool_check; \
        r = run_pre_tool_check('Bash', {'command': 'git push'}); \
        exit(0 if r['allowed'] else 1)"
    - python -c "from tools.airgap.hook_compat import run_auto_commit; \
        run_auto_commit('ci: post-scan auto-commit')"
```

---

## 10. Strategos OSINT pre-staging (air-gap OSINT ingestion)

When `ICDEV_AIRGAP=true`, the OSINT harvester cannot reach internet RSS feeds
directly. Use the three-tier fallback chain to feed signals into an air-gapped
enclave:

### Tier resolution order (first reachable wins)

| Tier | Condition | Data source |
|---|---|---|
| `TIER_INTERNET` | `ICDEV_AIRGAP` unset | Live RSS/Atom feeds |
| `TIER_GITLAB` | `ICDEV_AIRGAP=true` + `GITLAB_URL` reachable + `GITLAB_OSINT_PROJECT_ID` set | GitLab CI artifact |
| `TIER_FILE_INBOX` | `ICDEV_AIRGAP=true` + JSON files in `data/osint_inbox/` | Pre-staged JSON batches |
| `TIER_NONE` | All sources unavailable | Audit row written; 0 signals; exit 0 |

### Option A — Pre-stage on the internet-connected side, transfer via removable media

```bash
# On the internet-connected machine:
python tools/strategos/osint_prestage.py --output-dir /mnt/transfer/osint_inbox/

# Copy the generated osint_prestage_<timestamp>.json files to removable media,
# then copy into the enclave's data/osint_inbox/ directory.
# The harvester will pick them up automatically on the next Genesis reflex cycle.
```

Dry-run (count without writing):
```bash
python tools/strategos/osint_prestage.py --dry-run --json
```

### Option B — GitLab CI artifact (TIER_GITLAB)

Configure a GitLab pipeline job (`osint_collect`) in the OSINT project that
runs `tools/strategos/gitlab_osint_collector.py` and uploads `osint_signals.json`
as a CI artifact. The harvester downloads and processes it automatically:

```bash
# Required env vars on the air-gapped side:
export ICDEV_AIRGAP=true
export GITLAB_URL=https://gitlab.internal.gov
export GITLAB_TOKEN=<service-account-token>
export GITLAB_OSINT_PROJECT_ID=<project-id>   # numeric project ID
export GITLAB_OSINT_REF=main                   # branch/tag (default: main)
```

### Option C — File inbox (TIER_FILE_INBOX, fallback)

Drop pre-staged JSON files into `data/osint_inbox/` before the reflex fires.
The harvester accepts any file matching `osint_prestage_*.json` or `*.json`:

```bash
# Format expected by osint_harvester:
# {"signals": [{"title": str, "body": str, "source": str, "date": str, "url": str, "geo_hint": null|str}, ...], "count": N, "prestaged_at": "..."}
ls data/osint_inbox/          # pending files
ls data/osint_inbox/processed/ # already ingested files (moved here after processing)
```

### Verifying tier resolution

```bash
python tools/strategos/tier_resolver.py --json
# Returns: {"osint_tier": "TIER_FILE_INBOX", "exec_tier": "EXEC_OLLAMA_LOCAL", ...}
```

### TIER_NONE behaviour

If all tiers are unavailable (inbox empty, GitLab unreachable, internet disabled):
- The harvester exits 0 (no failure).
- A diagnostic audit row is written to `sg_raw_signals_audit` with `reason="TIER_NONE: no source available"`.
- The Kanban task is NOT marked failed — it remains in `in_progress` and retries on the next cycle.

---

## 11. Playwright MCP without `npx`

`.mcp.json` runs the Playwright MCP server as `node <cli.js>` rather than
`npx -y @playwright/mcp@latest`. `npx` needs a registry round-trip to resolve the
package (and re-resolves a `@latest` dist-tag on **every** session start), which an
enclave cannot do. `node` against an already-installed path needs nothing.

The path is overridable, so each enclave points at wherever it installed the package:

```json
"playwright": {
  "command": "node",
  "args": ["${PLAYWRIGHT_MCP_CLI:-C:/Users/schuo/AppData/Roaming/npm/node_modules/@playwright/mcp/cli.js}"]
}
```

```bash
# In the enclave, from a downloaded tarball (no registry):
npm install -g ./playwright-mcp-0.0.78.tgz
export PLAYWRIGHT_MCP_CLI=/opt/node/lib/node_modules/@playwright/mcp/cli.js
```

Measured on a connected host, same 24-tool surface either way:

| | to `initialize` + `tools/list` |
|---|---|
| `node <cli.js>` | **0.43 s** |
| `npx -y @playwright/mcp@0.0.78` | 1.55 s |

### The browser is a separate, larger problem

The MCP server is only the driver. It cannot do anything without a browser binary, and
that is **not** shippable inside a Python wheel:

| component | size |
|---|---|
| `@playwright/mcp` + bundled `playwright-core` | 13.0 MB |
| `chromium_headless_shell` (minimum viable) | **267.3 MB** |
| full `chromium` | 412.2 MB |

PyPI's default per-file limit is 100 MB and browser builds are per-platform/arch, so
vendoring the stack into the ICDEV distribution is not an option.

**Option A — use the Chrome or Edge already on the box (no download at all).**
Preferred when the enclave cannot pull the browser bundle but ships a managed browser:

```bash
# E2E test runner (playwright.config.ts)
export ICDEV_PLAYWRIGHT_CHANNEL=chrome        # or msedge, chrome-beta, msedge-dev
# ...or point straight at the binary if it is not in a standard location:
export ICDEV_PLAYWRIGHT_EXECUTABLE="C:/Program Files/Google/Chrome/Application/chrome.exe"

# Playwright MCP server (.mcp.json)
export PLAYWRIGHT_MCP_BROWSER=chrome          # or msedge
```

Verified on a machine with both installed — Playwright drives them with no bundled
Chromium present:

| channel | launched | version |
|---|---|---|
| `chrome` | yes | 150.0.7871.187 |
| `msedge` | yes | 151.0.4129.59 |

Leaving both variables unset keeps bundled Chromium, so connected machines and CI are
unaffected.

**Option B — pre-stage the Playwright browser bundle:**

```bash
# On a connected host, then copy the directory into the enclave:
npx playwright install --with-deps chromium
# In the enclave:
export PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
```

If no browser can be staged or reused, leave the `playwright` entry out of `.mcp.json`.
The E2E **verification gate** (`tools/workflow/validated_commit.py::_run_e2e`) shells out
to Playwright separately and degrades to `not_run` on its own; only the interactive
`mcp__playwright__*` V&V step in the ANVIL commands is lost.

### The E2E test runner resolves the same way

`tools/testing/e2e_runner.py` used to invoke `npx playwright test`, which resolves a
missing package by fetching it. It now resolves the CLI from disk, in order:

1. `ICDEV_PLAYWRIGHT_CLI` — an absolute path to the playwright binary or its `cli.js`.
   Set but non-existent is a hard error, not a silent fall-through to the network.
2. `node_modules/.bin/playwright`, walking **up** from the project root — a kanban task
   runs from `.tmp/worktrees/<id>`, which has no `node_modules` of its own but sits under
   a checkout that does.
3. `npx --no-install playwright` — may only run what is already installed; it errors
   instead of reaching for the registry.

There is deliberately no bare `playwright`-on-PATH fallback: on a machine with the
**Python** `playwright` package installed, that resolves to its console script, which is a
different program that does not implement the `test` subcommand.

---

## 12. floci container-backed services — the images that are pulled at RUN TIME

**The claim this section exists to make honest:** having the `floci/floci:2.0.1`
image cached is **necessary and not sufficient**. floci does not carry the
runtimes for its container-backed services inside its own image. Lambda, RDS,
ElastiCache, OpenSearch, MSK and ECS/EC2/EKS each start a **separate container
from a separate base image**, which floci resolves **from the public internet on
first use of that service**. On a disconnected high side that pull fails at
exactly the moment a demo runs — and nothing before this told you which images
would have been fetched.

Every image below was **measured, not read off a README**: `docker events
--filter type=image` recorded while a live floci 2.0.1 was driven through each
service with boto3 (2026-09-05, Docker 28.5.1). The declaration of record is
[`args/floci_runtime_images.yaml`](../../args/floci_runtime_images.yaml); the
pins the vendor consumes are `vendor/images/images-floci-runtime.txt`, and
`tests/cloud/test_floci_runtime_images.py` asserts the two agree so one measured
fact cannot come to be spelled two ways.

### 12.1 What to vendor

| service | variant | image |
|---|---|---|
| Lambda | `python3.11` | `public.ecr.aws/lambda/python:3.11` |
| Lambda | `nodejs20.x` | `public.ecr.aws/lambda/nodejs:20` |
| RDS | `postgres` | `postgres:16.3-alpine` |
| RDS | `mysql` | `mysql:8.0.36` |
| ElastiCache | `redis` | `valkey/valkey:8` |
| ElastiCache | `memcached` | `memcached:1.6` |
| OpenSearch | — | `opensearchproject/opensearch:2.19.5` |
| MSK / Kafka | — | `redpandadata/redpanda:latest` — mutable tag |
| EC2 | — | `public.ecr.aws/amazonlinux/amazonlinux:2023` |
| EKS | — | `rancher/k3s:latest` — mutable tag |
| ECR (implied by ECS/EKS) | — | `registry:2` |

The full set vendors to **1.91 GB across 11 tars** (1,905,578,496 bytes),
measured 2026-09-05: `--save --topic floci-runtime` reported `verified` with 0
failures and `manifest_digest_verified: true` on every one. That is well under
the ~6.3 GB `docker image ls` reports for the same eleven, because `docker save`
writes each shared layer once. Re-verifying the media with no daemon at all took
2.8 s.

**Vendor the variants you declare, not the table.** The image set is a function
of declared configuration, not of the service: a `python3.11` Lambda and a
`nodejs20.x` Lambda pull *different* images, as do a postgres and a mysql RDS
instance. A deployment running one python Lambda over postgres needs four of
these eleven, not all of them. Ask:

```bash
python -m tools.cloud.runtime_images --check --services lambda,rds --variants python3.11,postgres
```

**Two of floci's own backing images are named by the mutable tag `:latest`**
(redpanda for MSK, k3s for EKS). The digests recorded are what `:latest`
resolved to on the measured date; upstream can move them under the same tag
without notice. A re-vendor must **re-measure**, never assume.

**Two things this table cannot enumerate for you, and both are yours:**

1. **Your own workload images.** ECS and EKS pull whatever image *your* task
   definition or pod spec names. During the measurement run floci pulled
   `alpine:3.19` purely because the probe's task definition said so — that is a
   workload image, not a floci runtime base, and it is deliberately absent from
   the declaration. Mirror your workload images too.
2. **Variants you add later.** A Lambda runtime or RDS engine not in the table
   above has never been measured here. `--check` reports the service as
   `variant_undetermined` rather than guessing, because guessing produces either
   a fabricated blocker or a fabricated clean bill.

### 12.2 Vendor on the connected side, load on the high side

The image cache is the delivery mechanism, per the operator decision of
2026-09-05: container-backed services reach the **local** Docker daemon on the
emulator host. **A populated local cache is a valid air-gap posture** — the
requirement is that nothing pulls at run time, not that a registry exists.

```bash
# -- LOW SIDE (connected) ------------------------------------------------
# 1. Pull every image you need into the local cache, then vendor it.
#    --save NEVER pulls: a pin absent from the cache is reported and the run
#    exits non-zero. That is the designed refusal -- resolve it with an
#    explicit `docker pull`, so nothing is fetched that nobody pinned.
python tools/airgap/image_vendor.py --save --topic floci-runtime --json

# 2. Transport vendor/images/floci-runtime/ on removable media.

# -- HIGH SIDE (disconnected) --------------------------------------------
# 3. Verify the media BEFORE loading it. Needs no docker daemon at all.
python tools/airgap/image_vendor.py --verify --topic floci-runtime --no-daemon-probe --json

# 4. Load into the local cache.
python tools/airgap/image_vendor.py --load --topic floci-runtime --json

# 5. Prove the emulator will not pull. THIS is the acceptance check.
python -m tools.cloud.runtime_images --check
```

Step 5 exits **0 satisfied / 1 blocked / 2 could not measure**. Exit 2 is not
clean: it means the docker daemon could not be asked, which proves nothing.

### 12.3 How verification by digest actually works

A pin is a **digest, never a tag** — `parse_pin` refuses `floci/floci:2.0.1`,
because a tag is mutable and a bundle built from one cannot be shown to contain
what was intended. `--verify` re-hashes every blob in the tar against its own
filename (OCI content addressing) and matches `index.json`'s manifest digest to
the pin. That is a cryptographic proof the tar holds the pinned image, and it
needs no daemon — which matters, because media is verified before there is
anywhere to load it.

**A loaded bundle carries no tag, and that is normal.** Measured 2026-09-05: an
image delivered by `docker save repo@sha256:...` then `docker load` resolves by
**neither** `repo:tag` **nor** `repo@sha256:...` — it has `RepoTags=[]` and
`RepoDigests=[]` and does not appear in `docker image ls`. It resolves by image
ID alone. So do not conclude a load failed because `docker image ls` looks
unchanged; ask `runtime_images --check`, which tries three rungs and reports
which one answered:

| basis | meaning |
|---|---|
| `present_tagged` | the ref resolves and its RepoDigest matches the pin (the pulled case) |
| `present_by_digest` | `repo@sha256:...` resolves |
| `present_by_id` | the pin's digest resolves as an image ID — **the loaded-bundle case** |
| `digest_mismatch` | present under the tag, but a **different image**. Re-vendor; do not re-mirror. |
| `absent` | no rung answered. It will pull. |

### 12.4 Telling a mirrored miss from a real outage

Both look identical from the dashboard: a container-backed service that does not
come up. They need opposite repairs, so discriminate before you debug.

**Ask the cache first — it is one command and it is decisive:**

```bash
python -m tools.cloud.runtime_images --check --json
```

| symptom | verdict | reading |
|---|---|---|
| `state: blocked`, the failing service's image in `missing` | **mirrored miss** | The image was never vendored. Vendor it on the low side; nothing on this host will fix it. |
| `state: blocked`, image in `mismatched` | **mirrored miss** | Present under the tag at a *different* digest. The bundle is stale, or was built from a moved `:latest`. Re-measure and re-vendor. |
| `state: satisfied` and the service still fails | **real outage** | The image is here. This is a floci, resource or configuration fault — debug the service. |
| `state: unmeasured` | **neither, yet** | The docker daemon could not be asked. Fix that first; you have measured nothing. |

**The corroborating signal is whether a container exists at all.** floci names
every service container `floci-*` — measured 2026-09-05: `floci-rds-db-<id>`,
`floci-opensearch-<domain>`, `floci-msk-<hex>`, `floci-eks-<cluster>`,
`floci-valkey-<group>`, `floci-memcached-<id>`, `floci-ec2-<instance-id>`,
`floci-ecr-registry`, and `floci-<function-name>-<hex>` for Lambda:

```bash
docker ps -a --filter "name=floci-"     # is there a container for the service at all?
docker logs icdev-floci --tail 100      # a pull attempt appears here
```

* **No container, and the emulator log shows a pull attempt** — a disconnected
  host cannot resolve the registry, so the pull stalls and then fails on DNS or
  connect. That is a **mirrored miss**, whatever the AWS-level error says.
* **A container exists** (running, restarting or exited) **with logs of its own**
  — the image was found and started. That is a **real outage**; read the
  container's logs, not the emulator's.

Do **not** diagnose from the AWS API response. floci surfaces a missing base
image as an ordinary service failure, so the boto3 error is the same shape
either way — which is precisely why the cache check exists.

### 12.5 The gate

`airgap-emulator-runtime-images` in `args/twin_airgap_rules.yaml` makes an
emulator configuration that would need an external pull at run time a
`deployment_blocker`. It is the only rule in that file that is **not**
deny-by-match over strings the design contains: a floci config that declares a
Lambda contains no image reference at all, so no string matcher can see this
dependency. It derives the required set from the declared services and asks the
local cache.

`unmeasured` is deliberately **not** a blocker. A host whose docker daemon
cannot be asked has proven nothing, and blocking every CI runner and reviewer
laptop is how a gate earns itself a `|| true`. It is emitted at `medium` under
the `-unmeasured` rule id, so it is visible and attributable and never folded
into either answer.

### 12.6 Sites that mandate an internal registry (flx-airgap-03)

§12.1–12.5 describe the **local-cache** posture: vendor the eleven measured
images and load them into each host before it is disconnected. A
registry-mandating site cannot do that — its images must be **served**, by an
internal mirror, to whatever daemon floci talks to.

**One rule, one question.** `airgap-emulator-runtime-images` has always asked
*would this deployment need an EXTERNAL pull at run time?* That question is
unchanged; it now has two ways to answer *no*. A cached image pulls nothing. An
uncached image whose pull is redirected to an **internal** mirror pulls
internally, so there is no public-internet dependency and no air-gap violation.
There is deliberately no second rule — two rules could disagree about what a
run-time pull is, and then a reviewer has two verdicts and no way to choose.

**Internal means what the air-gap rules already say it means.** The mirror host
is judged against `allowlist.internal_host_suffixes` in
`args/twin_airgap_rules.yaml` — the same list `airgap-internal-registry` matches
registry hosts against. Declaring a mirror is therefore *not* enough to silence
the finding: `mirror.gcr.io` is still an external pull.

#### The three `FLOCI_DOCKER_*` names

Confusing them makes a working service report a fabricated refusal. They are
three different things:

| Variable | Answers | Read by |
|---|---|---|
| `FLOCI_DOCKER_SOCKET` | how the ICDEV **host Python process** reaches a daemon | `tools/cloud/emulator.py::docker_basis()` |
| `FLOCI_DOCKER_SOCKET_MOUNT` | the compose **bind-mount source** (a host path) | `docker-compose.yml` volumes |
| `FLOCI_DOCKER_DOCKER_HOST` | the daemon **floci itself** starts service containers on | `docker-compose.yml` → the container's `DOCKER_HOST` |

Measured 2026-09-04: setting `FLOCI_DOCKER_SOCKET` to the *mount* spelling makes
`service_supported("lambda")` return a fabricated `False` on Windows. Keep them
apart.

`FLOCI_DOCKER_DOCKER_HOST` defaults to `unix:///var/run/docker.sock` — exactly
where compose mounts the host socket — so leaving it unset reproduces the
operator decision of 2026-09-05 (locally hosted Docker) rather than clearing
`DOCKER_HOST` to an empty string. Set it to `tcp://…:2376` for a remote daemon.

#### Declaring the mirror

`args/floci_registry.yaml`, shipped `enabled: false` so it cannot change a
verdict on a deployment that has not opted in.

```yaml
enabled: true
docker_host: "tcp://runtime-host.internal.example.mil:2376"
registries:
  - registry: "docker.io"
    mirror: "registry.internal.example.mil:5000"
    mechanism: daemon_registry_mirror
    username_ref: "env:FLOCI_MIRROR_USERNAME"
    password_ref: "env:FLOCI_MIRROR_PASSWORD"
  - registry: "public.ecr.aws"
    mirror: "registry.internal.example.mil:5000"
    mechanism: repository_rewrite
    username_ref: "env:FLOCI_MIRROR_USERNAME"
    password_ref: "env:FLOCI_MIRROR_PASSWORD"
```

The eleven measured images span exactly those two registries (a ref like
`postgres:16.3-alpine` names no host, and Docker Hub is the implicit default).
A test asserts that, so a third registry appearing in the table cannot leave the
worked example mirroring an incomplete set while reading as a complete posture.

**`mechanism` is load-bearing, not decoration.** Docker's `registry-mirrors`
daemon setting redirects **Docker Hub pulls only** — it does not intercept
`public.ecr.aws`. Declaring `daemon_registry_mirror` for any registry other than
`docker.io` is **refused at load time**, because believing it would report a
clean air-gap verdict for a host that still reaches Amazon on the first Lambda
invoke. Re-host those images in the mirror and declare `repository_rewrite`.

**A credential is a reference, never a literal.** `username_ref` / `password_ref`
must start with `env:`, `vault:`, `aws:` or `file:` — the same prefixes
`tools/databridge/seed_connections.py` enforces, pinned equal by a test. A
literal is **refused**, not warned about: a warning still lands the secret in
git, and this repository is public. `plain:` is not accepted even though
`tools/rag/secret_ref.py` resolves it — that prefix exists to carry a literal.
Nothing in `tools/cloud/floci_registry.py` ever *resolves* a reference; it
answers a question about air-gap posture and has no use for the value.

```bash
python -m tools.cloud.floci_registry --check            # refuse an unusable declaration
python -m tools.cloud.floci_registry --show             # the posture, credentials as REFS
python -m tools.cloud.floci_registry --origins --json   # per image: internal or EXTERNAL
python -m tools.cloud.runtime_images --check --json     # the verdict, with its `basis`
```

#### What a mirrored `satisfied` does and does not claim

`basis` is reported beside `state` and never folded into it:

| `basis` | Means |
|---|---|
| `local_cache` | every required image is on this host's disk |
| `internal_mirror` | none is cached; an internal mirror serves them all |
| `cache_and_mirror` | some cached, the rest mirrored |
| `external_pull_required` | the finding — a pull would leave the enclave |

**Mirror completeness is not verified, and the report says so.** Nothing in this
chain contacts a registry: `ALLOWED_DOCKER_COMMANDS` in
`tools/airgap/image_vendor.py` contains no `pull` and no `manifest`, and
flx-airgap-03 does not widen it. What is established is that the pull is
*internal* — the air-gap question. Whether the mirror actually holds the image is
a different question with a different repair (load the vendored bundle into the
mirror, §12.4), and folding the two together would turn an operations gap into
an air-gap violation or, far worse, the other way round.

`absent_from_cache` is therefore reported under **every** posture, so a mirrored
deployment can still see which images its hosts do not hold. It is never folded
into `missing`: "would be pulled from outside" and "is not on this disk" are
different facts, and only the first is an air-gap finding.

An **unreadable cache stays `unmeasured` under any posture** — a mirror says
where a pull goes, and can never answer what is on the disk. A **malformed
declaration** is not "no mirror": it reads external and names itself in
`registry_posture.basis`, because the fail-closed direction for an air-gap gate
is to surface the blocker, not to bless the deployment.

---

## 13. Escape hatches

| Variable | Effect |
|---|---|
| `ICDEV_MTLS_VERIFY=false` | Disables all outbound TLS verification (DEV ONLY) |
| `ICDEV_KANBAN_VERIFY_GATE=false` | Dashboard `/move` skips the done-gate (bulk migrations) |
| `ICDEV_KANBAN_VERIFY_BUDGET_SEC` | Overrides the 300s per-task verification budget |
| `ICDEV_PG_NO_FALLBACK=true` | Crash instead of falling back to SQLite when PG is unreachable |
| `PLAYWRIGHT_MCP_CLI` | Path to `@playwright/mcp/cli.js` for the MCP server (avoids `npx`; see §11) |
| `PLAYWRIGHT_MCP_BROWSER` | MCP server browser/channel — `chromium` (default), `chrome`, `msedge` |
| `ICDEV_PLAYWRIGHT_CLI` | Path to the playwright CLI for the E2E runner (avoids `npx`; see §11) |
| `ICDEV_PLAYWRIGHT_CHANNEL` | E2E runner browser channel — unset = bundled Chromium; `chrome` / `msedge` use the system browser |
| `ICDEV_PLAYWRIGHT_EXECUTABLE` | E2E runner explicit browser binary path (overrides channel lookup) |
| `PLAYWRIGHT_BROWSERS_PATH` | Pre-staged browser cache directory (see §11) |

Never set the first three in production.
