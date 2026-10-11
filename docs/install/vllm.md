# Serving a local model with vLLM (optional)

vLLM is an **optional** OpenAI-compatible inference server for OMX. It never
replaces Ollama as the default local provider, and every chain in
`args/llm_config.yaml` lists the ollama entries ahead of the vllm ones.

## Read this first: hardware

vLLM was built for datacenter GPUs. At startup it reserves a fixed share of VRAM
(`--gpu-memory-utilization`) for weights plus KV cache, and it has no partial CPU
offload of the kind Ollama gets from GGUF. On the low-end GPUs ICDEV's target
boxes carry, that leaves room for:

- a **small quantized model** (about 1.5B-3B parameters, AWQ or GPTQ),
- a **short context** (`--max-model-len 4096`),
- and weak tool-calling, because tool-calling quality at that size is weak.

So on those boxes, **use Ollama**. Use vLLM in two cases:

1. **The realistic production path:** a vLLM server on a bigger GPU somewhere
   else (LAN or cloud). Point `VLLM_BASE_URL` at it (see
   [Using a remote vLLM](#using-a-remote-vllm)).
2. **A local smoke test:** the low-VRAM profile below, to check that the
   server answers, that discovery works, and that one completion comes back.
   That is the whole acceptance test on this hardware. It says nothing about
   quality or throughput.

**Numbers policy:** this doc gives no throughput, latency or capacity figure,
because none has been measured on the target hardware. Anyone who adds one
writes it as "measured on `<hardware>`, `<date>`", or leaves it out.

## The compose recipe

`docker-compose.yml` has two services, each behind its own profile, so a plain
`docker compose up` starts neither:

| Service | Profile | Port (host) | What it serves |
|---------|---------|-------------|----------------|
| `vllm` | `vllm` | `127.0.0.1:8000` | chat/completions with tool calling |
| `vllm-embed` | `vllm-embed` | `127.0.0.1:8001` | embeddings (`--runner pooling`) |

The image is **pinned by digest**: `vllm/vllm-openai:v0.31.0@sha256:c1c9f6fd…`,
the multi-arch index digest, resolved 2026-10-10 with
`docker buildx imagetools inspect` (no pull). The same digest is the air-gap pin
in `vendor/images/images-vllm.txt`, and
`tests/test_omx_vllm_05_compose_recipe.py` keeps the two equal.
`python tools/ci/pin_census.py --check` sees no unpinned site for either
service.

Each profile is a separate step on purpose. Both engines reserve VRAM up front,
and two of them together do not fit on a small card.

```bash
docker compose --profile vllm config --quiet     # validate
docker compose --profile vllm up -d vllm         # start
docker compose logs -f vllm                      # watch weights load + KV-cache sizing
```

### Default: the low-VRAM profile

| Setting | Default | `.env` override |
|---------|---------|-----------------|
| model | `Qwen/Qwen2.5-1.5B-Instruct-AWQ` | `VLLM_MODEL` |
| `--gpu-memory-utilization` | `0.80` | `VLLM_GPU_MEMORY_UTILIZATION` |
| `--max-model-len` | `4096` | `VLLM_MAX_MODEL_LEN` |
| `--enforce-eager` | on (CUDA graphs cost VRAM) | edit `command:` |
| `--tool-call-parser` | `hermes` | `VLLM_TOOL_CALL_PARSER` |

There is no `--quantization` flag. vLLM reads the quantization from the
checkpoint's own `config.json`, so changing `VLLM_MODEL` to an unquantized model
needs no other edit.

### Large-GPU profile

This one is commented in the compose file. On a bigger card, set a larger
model, `VLLM_GPU_MEMORY_UTILIZATION=0.90` and a longer `VLLM_MAX_MODEL_LEN`, and
remove `--enforce-eager` from `command:`.

### `max_model_len` vs VRAM

The KV cache has to hold `max_model_len` tokens for at least one sequence. If
the VRAM left after loading the weights cannot hold that, vLLM **refuses to
start** and its log says so. It does not degrade quietly. To fix it, in this
order:

1. lower `VLLM_MAX_MODEL_LEN`,
2. pick a smaller or more aggressively quantized model,
3. raise `VLLM_GPU_MEMORY_UTILIZATION` (only if nothing else uses the GPU,
   including your desktop compositor).

### Choosing a tool-call parser

`--enable-auto-tool-choice` is always on. The parser has to match the model
family's chat template. A wrong parser does not error: tool calls just come
back as plain text. These names are registered in vLLM v0.31.0
(`vllm/tool_parsers/__init__.py`):

| Model family | `VLLM_TOOL_CALL_PARSER` |
|--------------|-------------------------|
| Qwen2.5 (default model) | `hermes` |
| Qwen3 | `hermes` (or `qwen3_xml` / `qwen3_coder` for the coder variants) |
| Llama 3.1 / 3.2 / 3.3 | `llama3_json` |
| Mistral / Mixtral | `mistral` |
| Phi-4-mini | `phi4_mini_json` |
| models that emit Python-style calls | `pythonic` |

For anything else, check the model card and vLLM's tool-calling docs for the
pinned release. OMX's `settings.vllm_discovery` sends a probe tools request and
records whether tool calls actually come back, so the router does not rely on
this table.

## Omarchy / Arch Linux with an NVIDIA GPU

```bash
# 1. Driver. Omarchy installs it when it detects NVIDIA; check it with:
nvidia-smi

# 2. Docker + the NVIDIA container toolkit (Arch extra repo)
sudo pacman -S --needed docker docker-compose nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl enable --now docker
sudo systemctl restart docker
sudo usermod -aG docker "$USER"     # log out and back in

# 3. Prove a container can see the GPU before involving vLLM
docker run --rm --gpus all ubuntu nvidia-smi

# 4. Serve
cd /path/to/icdev
docker compose --profile vllm up -d vllm
```

The `deploy.resources.reservations.devices` block (`driver: nvidia`, one GPU)
is how compose requests the GPU. Without the toolkit the container fails to
start with a device-driver error. It never silently falls back to running
without the GPU.

AMD or Intel GPUs: vLLM ships separate ROCm and XPU builds, and this recipe
pins neither. On those boxes, use Ollama.

## Windows via WSL2 + Docker Desktop

1. Install a current NVIDIA Windows driver. It provides the GPU inside WSL2, so
   do **not** install a Linux NVIDIA driver inside the distro.
2. In Docker Desktop, use the WSL 2 backend (Settings → General) and enable
   integration for your distro (Settings → Resources → WSL integration).
3. Check GPU passthrough: `docker run --rm --gpus all ubuntu nvidia-smi`.
4. Run `docker compose --profile vllm up -d vllm` from the repo, in PowerShell or
   in the distro.

Notes:

- `./data/vllm/hf-cache` is a bind mount. A checkout under `/mnt/c/...` crosses
  the Windows filesystem boundary and is slow for multi-GB weight files. Prefer
  a checkout inside the distro (`~/icdev`).
- WSL2 caps the memory it gives the VM (`.wslconfig`, `memory=`). Loading
  weights needs host RAM as well as VRAM.
- The ICDEV dashboard on the Windows side reaches the server at
  `http://localhost:8000/v1`, because Docker Desktop forwards the loopback-bound
  port.

## CPU-only: slow fallback, not a deployment

vLLM has a CPU backend, published as a separate image
(`vllm/vllm-openai-cpu:v0.31.0`, index digest `sha256:495c84fe…`, resolved
2026-10-10). It is **not** wired into the compose file. On a box without a
usable GPU, Ollama (GGUF on CPU) is the better choice. Use the vLLM CPU build
only to check API compatibility, and never as evidence of how anything performs.
The CPU backend sizes its KV cache from `VLLM_CPU_KVCACHE_SPACE` (GiB), not from
`--gpu-memory-utilization`. To try it, swap the image, drop the `deploy.devices`
block, set `VLLM_CPU_KVCACHE_SPACE`, and remove `--enforce-eager`.

## How the rest of OMX picks it up

Put these in `.env` (names from `.env.example` and `args/llm_config.yaml`):

```bash
VLLM_BASE_URL=http://localhost:8000/v1
VLLM_MODEL=Qwen/Qwen2.5-1.5B-Instruct-AWQ     # must equal the model being served
VLLM_API_KEY=                                 # empty for loopback; set it when serving off-host
# embeddings (only with the vllm-embed profile):
# VLLM_EMBED_BASE_URL=http://localhost:8001/v1
# VLLM_EMBED_MODEL=BAAI/bge-small-en-v1.5
```

- The compose service serves `--model ${VLLM_MODEL}`, so one variable names both
  what the server loads and what the client asks for. If `VLLM_MODEL` is unset,
  compose still serves its default, but the `vllm-local` alias expands to `""`
  and the router skips it. That is by design: no `.env` line, no vLLM routing.
- `settings.vllm_discovery` probes `/v1/models` whenever `VLLM_BASE_URL` is set.
  It replaces the alias's placeholder `context_window` with the server's
  `max_model_len`, and its `supports_tools` with the result of a real tools
  request. The compose healthcheck probes the same `/v1/models` path, so
  "healthy" means "discovery would answer".
- `VLLM_API_KEY` goes to both sides: the client sends it, and the container gets
  it in its environment (vLLM v0.31.0 declares `VLLM_API_KEY` in `vllm/envs.py`).
  When you set one, confirm the server enforces it before relying on it: a
  `curl -s -o /dev/null -w '%{http_code}' http://HOST:8000/v1/models` with no
  key should return `401`.

### Smoke test (the whole acceptance on low-end hardware)

```bash
curl -s http://localhost:8000/v1/models                          # reachable + served name
python -c "from tools.llm.router import LLMRouter; r = LLMRouter(); print(r.get_provider_for_function('code_generation'))"
curl -s http://localhost:8000/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"Qwen/Qwen2.5-1.5B-Instruct-AWQ","messages":[{"role":"user","content":"Say ok."}],"max_tokens":8}'
```

## Using a remote vLLM

On the GPU host, set `VLLM_BIND_ADDR=0.0.0.0` **and** a `VLLM_API_KEY`, and never
one without the other. Then run the same `docker compose --profile vllm up -d
vllm`. On the ICDEV box, set `VLLM_BASE_URL=http://<gpu-host>:8000/v1` and the
same `VLLM_API_KEY`.

The CUI egress boundary decides from the provider's `locality`. `providers.vllm`
is declared `local`, and `coherence_checker --check provider_locality` holds a
`local` base URL to loopback, RFC 1918 or link-local addresses. So:

- **A LAN host on an RFC 1918 address** works as-is.
- **Any other address** (a public cloud IP, or a hostname that does not resolve
  to a private address) does not count as `local`. Declare that server as a
  provider with `locality: private`, which counts as local only when
  `ICDEV_PRIVATE_IS_LOCAL=1`, or treat it as `cloud`. Do not relabel the
  existing `vllm` provider to make a remote URL pass.

## Air gap

The bundle has two halves. Both are verified by hash, and nothing is downloaded
on either side.

**Low side (connected):**

```bash
docker pull vllm/vllm-openai@sha256:c1c9f6fd5c109ba7f0546a59f5b2f15fb87f64c77782e90a27b648b42a8e67c3
HF_HOME=data/vllm/hf-cache hf download Qwen/Qwen2.5-1.5B-Instruct-AWQ
python deploy/offline/vllm_bundle.py save --model Qwen/Qwen2.5-1.5B-Instruct-AWQ
```

`save` runs `python tools/airgap/image_vendor.py --save --topic vllm`. That is
the same mechanism, and the same pin-file convention, as the floci image cache
(`vendor/images/README.md`). It then tars the model's Hugging Face cache
directory into `vendor/models/vllm/`, along with a `MANIFEST.json` (sha256 per
tar, snapshot revisions) and a `SHA256SUM`. A model or image missing from the
local cache is reported, and the run fails. It is never fetched for you.

**Move `vendor/images/vllm/` and `vendor/models/vllm/` to the high side, then:**

```bash
python deploy/offline/vllm_bundle.py verify        # re-hash; needs no docker for the weights
python deploy/offline/vllm_bundle.py load          # verify, extract weights, docker load, re-tag
```

`load` extracts the weights into `data/vllm/hf-cache` and refuses any tar member
that would land outside it. After `docker load`, the image resolves only by its
image ID: it has no tag and no repo digest. So `load` tags the verified image as
`icdev-vendored/vllm-openai:<digest prefix>` and prints the two `.env` lines to
add:

```bash
VLLM_HF_OFFLINE=1
VLLM_IMAGE=icdev-vendored/vllm-openai:c1c9f6fd5c10
```

Not yet measured: the full `save → load` round trip for the vLLM image
has not been run on the target hardware. The floci topic has been exercised
through `image_vendor`. The vLLM topic uses the same code path, but no run of
it is recorded yet.
