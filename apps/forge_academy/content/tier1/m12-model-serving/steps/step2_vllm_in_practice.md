---
ontology_id: icdev:mission:m12-model-serving:step:2
step_class: icdev:Lesson
---

# vLLM in Practice — Serving Several Models

You will learn how to stand up vLLM behind an OpenAI-compatible endpoint, how to serve more than one model (separate servers, or LoRA adapters on one base model), when tensor parallelism is needed, how vLLM compares with Ollama, TGI, SGLang and managed APIs, and what changes in an air-gapped enclave.

> Current as of October 2026. Command-line flags below are from the vLLM documentation at authoring time; run `vllm serve --help` on the version you deploy, because flags are added and renamed between releases. No benchmark or price is quoted here on purpose — measure on your own hardware and prompts.

## An OpenAI-compatible endpoint

vLLM ships an HTTP server that speaks the OpenAI API shape (`/v1/chat/completions`, `/v1/completions`, `/v1/models`, and `/v1/embeddings` for embedding models):

```bash
vllm serve Qwen/Qwen2.5-7B-Instruct --port 8000 \
    --max-model-len 16384 --gpu-memory-utilization 0.90
```

Because the API shape is OpenAI's, any OpenAI client works by changing only the base URL. That is exactly how ICDEV declares it in `args/llm_config.yaml`:

```yaml
providers:
  vllm:
    type: openai_compatible
    base_url: ${VLLM_BASE_URL:-http://localhost:8000/v1}
  mistral_vllm:
    type: openai_compatible
    base_url: ${MISTRAL_VLLM_BASE_URL:-http://localhost:8100/v1}
```

Two important flags: `--max-model-len` caps context length (and therefore the KV cache one request can claim), and `--gpu-memory-utilization` is the fraction of GPU memory vLLM may take for weights plus KV cache.

## Serving several models

A vLLM server process serves **one base model**. There are two ways to offer more than one model:

**1. One server per base model, a router in front.** Run each model in its own process on its own port (or its own GPU), and let a router choose between them. ICDEV's config above is that pattern: `vllm` on port 8000 and `mistral_vllm` on port 8100 are two independent servers. Each one is a separate failure domain — one can be down while the other is healthy — which is why a router needs health tracking (you build this in the next step).

**2. LoRA adapters on one base model.** If your "models" are fine-tunes of the same base, serve the base once and load small LoRA adapters on top:

```bash
vllm serve meta-llama/Llama-3.1-8B-Instruct --enable-lora \
    --lora-modules sql-lora=/models/adapters/sql contracts-lora=/models/adapters/contracts \
    --max-loras 4
```

Clients select an adapter by putting its name (`sql-lora`) in the request's `model` field. The base weights are loaded once and the adapters are small, so many specialised variants share one GPU — far cheaper than one full copy per fine-tune. `--max-loras` bounds how many adapters can be active in a single batch.

## Tensor parallelism

When a model's weights plus a useful KV cache do not fit on one GPU, **tensor parallelism** splits each layer's weight matrices across several GPUs, which compute their slice in parallel and exchange results every layer:

```bash
vllm serve meta-llama/Llama-3.1-70B-Instruct --tensor-parallel-size 4
```

Because GPUs talk every layer, tensor parallelism wants a fast interconnect (NVLink within one node). Spanning nodes is usually done with **pipeline parallelism** (`--pipeline-parallel-size`), which gives each GPU group a contiguous range of layers and talks far less often. Rule of thumb: tensor-parallel within a node, pipeline-parallel across nodes.

## Choosing a server

| Option | Strengths | Watch out for |
|--------|-----------|---------------|
| **vLLM** | PagedAttention, continuous batching, LoRA multiplexing, broad model and quantization support, OpenAI-compatible server | Built for GPU servers; one base model per process |
| **SGLang** | High-throughput server; **RadixAttention** reuses KV cache across requests sharing a prefix via a radix tree, which helps agent and multi-turn workloads; strong structured-output support | Smaller ecosystem than vLLM |
| **TGI** (Hugging Face Text Generation Inference) | Mature, Hugging Face integration | Check the project's maintenance status before adopting — at authoring, upstream had signalled maintenance mode and pointed users toward vLLM and SGLang |
| **Ollama** | One-command install on a laptop or workstation, GGUF models via llama.cpp, CPU or modest GPU, OpenAI-compatible `/v1` endpoint, loads and unloads models on demand | Tuned for one user or a small team, not high-concurrency serving |
| **Managed APIs** (Bedrock, Azure OpenAI, Vertex AI, vendor APIs) | No GPUs to run, frontier models, elastic capacity | Data leaves your boundary; per-token billing; you must confirm the service is authorised at your impact level |

A common shape is all of them at once: Ollama on developer machines, vLLM or SGLang for shared in-boundary serving, and a managed API as an optional overflow tier where policy allows. A router sends each request to the right tier — which is what `tools/llm/router.py` does.

## Air-gap considerations

In an enclave with no internet, the things a server normally fetches at start-up must already be inside:

- **Weights** — download, checksum and transfer them in advance, then serve from a local path (`vllm serve /models/qwen2.5-7b-instruct`) rather than a hub id.
- **Hub lookups** — set `HF_HUB_OFFLINE=1` so the server never tries to reach the Hugging Face Hub and fails fast if a file is missing.
- **Container images** — pin images by **digest**, not tag, and import them through your approved transfer process.
- **Tokenizer and chat template** — they ship with the model files; a missing chat template silently changes how prompts are formatted.
- **The router** — every chain must end in a model that is reachable inside the enclave, or a cloud outage becomes your outage. ICDEV's chains end in a local model for this reason.

## Key takeaways

- vLLM exposes an OpenAI-compatible endpoint, so clients change only their base URL.
- One process serves one base model: serve several models with several servers plus a router, or with LoRA adapters on a shared base.
- Tensor parallelism splits layers across GPUs within a node; pipeline parallelism spans nodes.
- Pick the server for the job: Ollama for workstations, vLLM or SGLang for shared serving, managed APIs where data may leave the boundary.
- Air-gapped serving means local weights, `HF_HUB_OFFLINE=1`, digest-pinned images, and fallback chains that end inside the enclave.
