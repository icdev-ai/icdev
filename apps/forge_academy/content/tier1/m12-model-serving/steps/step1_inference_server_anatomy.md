---
ontology_id: icdev:mission:m12-model-serving:step:1
step_class: icdev:Lesson
---

# Inference-Server Anatomy

You will learn what an inference server actually does with your request — prefill, decode, the KV cache, PagedAttention, continuous batching and quantization — so you can reason about why one deployment is fast and another falls over under load.

> Facts in this lesson are current as of October 2026. Architectural ideas (prefill/decode, KV cache, PagedAttention) are stable; flag names and supported formats change between releases, so check your server's release notes before you rely on one.

## Two phases: prefill and decode

Every generation request runs in two very different phases.

**Prefill** processes the whole prompt in one pass. All prompt tokens are known up front, so the GPU computes them in parallel — this phase is **compute-bound**. Its cost grows with prompt length, and it is what you wait for before the first token appears. That wait is called **time to first token (TTFT)**.

**Decode** then produces the answer one token at a time. Each new token depends on the one before it, so decode is **sequential** and **memory-bandwidth-bound**: for every token the GPU must read the model weights and the cached state of every previous token, while doing comparatively little arithmetic. The gap between output tokens is the **inter-token latency (ITL)**, sometimes called time per output token.

This split explains the pricing you met in m01: output tokens cost more than input tokens because decode is the slow, sequential phase.

## The KV cache

Attention needs the *keys* and *values* of every earlier token. Recomputing them for every new token would make decode quadratic, so the server stores them: the **KV cache**. It is the reason decode is fast — and the reason GPU memory runs out.

KV cache size per token is:

```
2 (K and V) x layers x kv_heads x head_dim x bytes_per_value
```

For a Llama-3-8B-shaped model (32 layers, 8 KV heads via grouped-query attention, head_dim 128, FP16 = 2 bytes):

```
2 x 32 x 8 x 128 x 2 = 131,072 bytes = 128 KiB per token
8,192-token context  -> 1 GiB for ONE request
```

Weights are a fixed cost; the KV cache grows with every concurrent request and every token of context. On a serving GPU, **the KV cache — not the weights — is usually what limits how many users you can serve at once.**

## PagedAttention

Early servers reserved one contiguous KV block per request, sized for the *maximum* possible length. Most requests never reach it, so much of that memory sat reserved and unused (fragmentation), capping concurrency.

**PagedAttention** (Kwon et al., *Efficient Memory Management for Large Language Model Serving with PagedAttention*, SOSP 2023 — the paper that introduced vLLM) borrows the idea of virtual-memory paging from operating systems:

- The KV cache is split into small fixed-size **blocks** (pages).
- A per-request **block table** maps logical token positions to physical blocks, which need not be contiguous.
- Blocks are allocated on demand as the sequence grows, so waste is limited to the last, partly filled block.
- Requests that share a prefix (the same system prompt, or several samples of one prompt) can **share** physical blocks.

Less waste means more requests fit in the same GPU memory, which is where most of vLLM's throughput gain over earlier servers came from.

## Continuous batching

GPUs are efficient only when they work on many sequences at once. **Static batching** waits for a batch to fill, runs it, and cannot admit anything new until the *longest* request in the batch finishes — short requests sit idle behind long ones.

**Continuous batching** (also called in-flight or iteration-level batching) schedules at the granularity of a single decode step: after every step, finished sequences leave the batch and waiting requests join it. The GPU stays busy and short requests are not held hostage by long ones. Servers often also split long prompts into chunks (**chunked prefill**) so a big prefill does not stall everyone else's decode.

## Quantization

Quantization stores weights (and sometimes activations) in fewer bits, cutting memory and memory bandwidth — which speeds up the bandwidth-bound decode phase.

| Format | What it is | Typical use |
|--------|-----------|-------------|
| **AWQ** | Activation-aware Weight Quantization — weight-only 4-bit; protects the small set of weights that matter most for activations | Fit a larger model on a smaller GPU |
| **GPTQ** | Post-training weight-only quantization (typically 4-bit) that corrects rounding error layer by layer using second-order information | Same niche as AWQ; many pre-quantized checkpoints exist |
| **FP8** | 8-bit floating point for weights and often activations; needs hardware FP8 support (e.g. NVIDIA Hopper and Ada-generation GPUs) | Near-FP16 quality with roughly half the memory |

A rough memory budget for weights is `parameters x bytes per parameter`: an 8B-parameter model is about 16 GB in FP16, about 8 GB in FP8, and about 4–5 GB at 4-bit. Quantization trades some quality for that saving — always re-run *your own* evals on the quantized model rather than trusting the original model's scores.

## Throughput vs latency

These two goals pull against each other:

- **Throughput** — total tokens per second across all users. Bigger batches raise it.
- **Latency** — what one user feels: TTFT plus ITL x output length. Bigger batches raise per-request ITL, because each decode step now does more work.

Tuning a server means choosing a point on that curve: an interactive chat assistant wants low TTFT and steady ITL; an overnight batch summarisation job wants maximum throughput and does not care about TTFT. The knobs are the maximum batch size / number of concurrent sequences, the context length you allow (which bounds KV cache per request), and quantization.

## Key takeaways

- Prefill is parallel and compute-bound (drives TTFT); decode is sequential and memory-bandwidth-bound (drives ITL).
- The KV cache, not the weights, usually caps concurrency.
- PagedAttention pages the KV cache to cut fragmentation and enable prefix sharing.
- Continuous batching admits and retires requests every decode step.
- AWQ and GPTQ are weight-only 4-bit methods; FP8 needs hardware support.
- Larger batches buy throughput at the cost of per-request latency.
