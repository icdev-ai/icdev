# CUI // SP-CTI
"""Example custom LLM provider template.

Copy this file and implement the three required methods to create a
custom LLM provider for ICDEV.

After implementing, register in args/llm_config.yaml:

    providers:
      my_provider:
        type: openai_compatible   # or create a new type entry
        base_url: "http://my-service:8080/v1"
        api_key_env: MY_API_KEY

Usage:
    from context.llm.example_provider import ExampleProvider
    from tools.llm.provider_sdk import ProviderTestHarness

    provider = ExampleProvider(api_key="test")
    harness = ProviderTestHarness(provider)
    results = harness.run_all()
"""

from tools.llm.provider_sdk import BaseProvider, LLMRequest, LLMResponse


class ExampleProvider(BaseProvider):
    """Example custom LLM provider — replace with your implementation."""

    def __init__(self, api_key: str = "", base_url: str = ""):
        self._api_key = api_key
        self._base_url = base_url

    @property
    def provider_name(self) -> str:
        return "example"

    def invoke(self, request: LLMRequest, model_id: str, model_config: dict) -> LLMResponse:
        """Invoke your LLM API and return an LLMResponse.

        Args:
            request: Vendor-agnostic request with messages, system_prompt, etc.
            model_id: Provider-specific model ID from llm_config.yaml.
            model_config: Full model config dict from llm_config.yaml.

        Returns:
            LLMResponse with at minimum content and model_id fields.
        """
        # TODO: Replace with actual API call
        # Example: use requests, httpx, or provider SDK
        #
        # import requests
        # resp = requests.post(
        #     f"{self._base_url}/chat/completions",
        #     headers={"Authorization": f"Bearer {self._api_key}"},
        #     json={
        #         "model": model_id,
        #         "messages": request.messages,
        #         "max_tokens": request.max_tokens,
        #     },
        # )
        # data = resp.json()
        # return LLMResponse(
        #     content=data["choices"][0]["message"]["content"],
        #     model_id=model_id,
        #     provider=self.provider_name,
        # )
        raise NotImplementedError("Replace this with your API integration")

    def check_availability(self, model_id: str) -> bool:
        """Check if the model is reachable.

        Args:
            model_id: Provider-specific model identifier.

        Returns:
            True if the model can accept requests.
        """
        # TODO: Replace with a health check or ping
        # Example:
        # try:
        #     resp = requests.get(f"{self._base_url}/models")
        #     return resp.status_code == 200
        # except Exception:
        #     return False
        return False
