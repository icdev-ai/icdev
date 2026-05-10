# ICDEV LLM Router

Every LLM call in ICDEV goes through `tools/llm/router.py`. The router abstracts provider selection, fallback logic, cost optimization, and air-gap compatibility into a single interface. You never hardcode a model name — you declare an **intent** and the router picks the best available provider.

## How it works

```python
from tools.llm.router import LLMRouter

router = LLMRouter()
provider = router.get_provider_for_function("code_generation")
response = provider.chat(messages=[...], max_tokens=500)
```

The router uses `args/llm_config.yaml` to map function names (like `"code_generation"`, `"chat"`, `"embedding"`) to provider tiers. In production on GovCloud, it routes to Bedrock/Azure OpenAI. In air-gap mode (RTX 4060 Ti), it routes to local Ollama.

## Provider tiers

| Tier | When used | Models |
|------|-----------|--------|
| **Primary** | Online, sufficient VRAM | Claude, GPT-4o, Mistral via Bedrock |
| **Secondary** | Fallback, high load | Claude Haiku, GPT-3.5-turbo |
| **Local** | Air-gap, no internet | Ollama (mistral, llama3, qwen2.5) |

## FORGE Sensei uses this router

When you ask FORGE Sensei for a hint during missions, the router picks the best available LLM for the `"chat"` function. In your training environment right now, it's routing to Ollama locally — no API keys required.

Study the demo output to see the router's decision log in action.
