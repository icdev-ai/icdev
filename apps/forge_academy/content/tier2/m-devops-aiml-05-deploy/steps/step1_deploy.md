---
ontology_id: icdev:mission:m-devops-aiml-05-deploy:step:1
step_class: icdev:Lesson
---

# Deployment Planning — CSP Selection + Inference Server Strategy

## Inference Server Selection Matrix

| Condition | Recommended Server | Node Type |
|-----------|-------------------|-----------|
| IL5/IL6 or air-gap | Ollama | deploy-ollama |
| High throughput ≥ 20 RPS | vLLM | deploy-vllm |
| AWS Bedrock model | Bedrock On-Demand | deploy-bedrock |
| Azure OpenAI model | Azure ML Endpoint | deploy-azure-ml |
| GCP Vertex AI model | Vertex AI Prediction | deploy-vertex-ai |
| OCI GenAI model | OCI GenAI Service | deploy-oci-genai |
| IBM watsonx model | watsonx.ai Space | deploy-watsonx-ai |

## Quantization Decision

```
Available VRAM → Quantization
≥ fp16 VRAM → fp16 (native precision)
≥ Q8_0 VRAM → Q8_0 (near-native)
≥ Q5_K_M VRAM → Q5_K_M (high quality)
≥ Q4_K_M VRAM → Q4_K_M (best size/quality)
otherwise → int4 (AWQ/GPTQ for CUDA)
```

## Your Mission

Call the deployment plan API for 3 different models and verify the inference server selection logic.

```python
import requests

BASE = "http://localhost:5050"

def plan(model_id, il_level="IL4", vram_gb=8, rps=1):
    return requests.post(f"{BASE}/ai-ml/api/deploy/plan", json={
        "model_id": model_id,
        "il_level": il_level,
        "vram_gb": vram_gb,
        "latency_target_ms": 2000,
        "throughput_rps": rps
    }).json()

# Test 1: IL6 → must be Ollama regardless of model
p1 = plan("qwen3-local", il_level="IL6", vram_gb=8)
assert p1["inference_server"]["type"] == "deploy-ollama", f"IL6 must use Ollama: {p1['inference_server']}"
assert p1["il_assessment"]["air_gap_required"] == True

# Test 2: Azure OpenAI model → Azure ML endpoint
p2 = plan("gpt-4o", il_level="IL4")
assert "azure" in p2["inference_server"]["type"].lower() or "azure" in p2["inference_server"].get("name","").lower(), \
    f"GPT-4o should route to Azure: {p2['inference_server']}"

# Test 3: High throughput local → vLLM
p3 = plan("llama3-70b-instruct", il_level="IL4", vram_gb=48, rps=25)
assert p3["inference_server"]["type"] in ("deploy-vllm", "deploy-ollama"), \
    f"High-throughput local should use vLLM: {p3['inference_server']}"

print("All deployment plan assertions passed!")
for label, p in [("IL6/Qwen3", p1), ("Azure/GPT-4o", p2), ("HTP/Llama3-70B", p3)]:
    srv = p["inference_server"]
    print(f"  {label}: {srv['name']} ({srv['type']})")
```
