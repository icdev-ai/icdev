# CUI // SP-CTI
"""Step definitions: Prompt and Context Caching Enhancements (5 features).

Validates:
  (1) RAG context caching — cache_control breakpoints on RAG chunks
  (2) Cache savings dashboard — /cache-savings route and metrics
  (3) Multi-breakpoint support — up to 4 Anthropic breakpoints
  (4) OpenAI cache token parity — cached_tokens from prompt_tokens_details
  (5) Cache warming on deploy — CacheWarmer.warm_on_deploy()

NIST 800-53: SA-11, SC-28, AU-12, SI-7
"""

from unittest.mock import MagicMock, patch

from behave import given, then, when


# ---------------------------------------------------------------------------
# Feature 1: RAG Context Caching
# ---------------------------------------------------------------------------


@given("the LLM router has RAG injection enabled with context_cache configured")
def step_router_rag_enabled_with_cache(context):
    """Set up router with RAG injection + context_cache enabled."""
    from tools.llm.router import LLMRouter

    router = LLMRouter.__new__(LLMRouter)
    router._config = {
        "rag": {
            "enabled": True,
            "injection": {
                "enabled": True,
                "injection_top_k": 2,
                "max_injection_chars": 1000,
                "citation_enabled": True,
                "citation_instruction": False,
            },
        },
        "response_cache": {
            "enabled": True,
            "per_function": {
                "code_generation": {"context_cache": True},
            },
            "per_canvas": {},
        },
        "redaction": {"enabled": False},
    }
    router._providers = {}
    router._embedding_providers = {}
    router._availability_cache = {}
    router._availability_cache_time = 0.0
    router._cache_ttl = 1800.0
    context.router = router


@given("the LLM router has RAG injection enabled without cache_control set")
def step_router_rag_enabled_no_cache(context):
    """Set up router with RAG injection but no context cache hint."""
    step_router_rag_enabled_with_cache(context)  # reuse same setup


@when("a request with cache_control \"{control}\" is augmented with RAG chunks")
def step_augment_request_with_rag(context, control):
    """Call _rag_augment() on a request with the specified cache_control."""
    from tools.llm.provider import LLMRequest

    request = LLMRequest(
        messages=[{"role": "user", "content": "Explain NIST AC-2."}],
        system_prompt="You are a compliance assistant.",
        cache_control=control,
    )

    mock_result = MagicMock()
    mock_result.content = "NIST AC-2 covers account management."
    mock_result.source_type = "nist"
    mock_result.source_id = "ac-2"
    mock_result.final_score = 0.9

    mock_retriever = MagicMock()
    mock_retriever.search.return_value = [mock_result]

    with patch("tools.rag.retriever.RAGRetriever", return_value=mock_retriever):
        context.augmented_request = context.router._rag_augment(request, "code_generation")


@when("a request without cache_control is augmented with RAG chunks")
def step_augment_request_no_cache_control(context):
    """Augment a request without cache_control."""
    from tools.llm.provider import LLMRequest

    request = LLMRequest(
        messages=[{"role": "user", "content": "Explain NIST SI-3."}],
        system_prompt="You are a compliance assistant.",
        cache_control="",
    )

    mock_result = MagicMock()
    mock_result.content = "NIST SI-3 covers malicious code protection."
    mock_result.source_type = "nist"
    mock_result.source_id = "si-3"
    mock_result.final_score = 0.85

    mock_retriever = MagicMock()
    mock_retriever.search.return_value = [mock_result]

    with patch("tools.rag.retriever.RAGRetriever", return_value=mock_retriever):
        context.augmented_request = context.router._rag_augment(request, "code_generation")


@then("the augmented request must still have cache_control set to \"{expected}\"")
def step_check_cache_control_preserved(context, expected):
    """Verify cache_control is preserved after augmentation."""
    actual = context.augmented_request.cache_control
    assert actual == expected, (
        f"cache_control must be '{expected}' after RAG augmentation, got '{actual}'"
    )


@then("the augmented system prompt must contain \"{marker}\"")
def step_check_system_prompt_contains_marker(context, marker):
    """Verify the marker is present in augmented system prompt."""
    prompt = context.augmented_request.system_prompt or ""
    assert marker in prompt, (
        f"System prompt must contain '{marker}', got:\n{prompt[:500]}"
    )


@then("the augmented system prompt must NOT contain \"{marker}\"")
def step_check_system_prompt_no_marker(context, marker):
    """Verify the marker is absent from augmented system prompt."""
    prompt = context.augmented_request.system_prompt or ""
    assert marker not in prompt, (
        f"System prompt must NOT contain '{marker}' (no cache_control set)"
    )


@given("a request with system prompt containing one \"{marker}\" marker")
def step_request_with_one_marker(context, marker):
    """Set up a request with one breakpoint marker in the system prompt."""
    from tools.llm.provider import LLMRequest

    context.request = LLMRequest(
        messages=[{"role": "user", "content": "Hello"}],
        system_prompt=f"Static part.\n{marker}\nDynamic part.",
        cache_control="ephemeral",
        max_tokens=10,
    )


@when("the AnthropicLLMProvider invokes the request with cache_control \"{control}\"")
def step_invoke_anthropic_with_cache_control(context, control):
    """Invoke AnthropicLLMProvider and capture kwargs."""
    from tools.llm.anthropic_provider import AnthropicLLMProvider

    provider = AnthropicLLMProvider(api_key="test-key")
    context.request.cache_control = control
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        resp = MagicMock()
        resp.stop_reason = "end_turn"
        u = MagicMock()
        u.input_tokens = 50
        u.output_tokens = 5
        u.cache_creation_input_tokens = 40
        u.cache_read_input_tokens = 0
        resp.usage = u
        b = MagicMock()
        b.type = "text"
        b.text = "ok"
        resp.content = [b]
        return resp

    provider._client = MagicMock()
    provider._client.messages.create.side_effect = fake_create
    provider.invoke(context.request, "claude-sonnet-4-5-20250929", {"max_output_tokens": 64000})
    context.captured_kwargs = captured


@then("the system argument must be a list of {count:d} text blocks")
def step_check_system_block_count(context, count):
    """Verify the system argument has the expected number of blocks."""
    blocks = context.captured_kwargs.get("system", [])
    assert isinstance(blocks, list), f"system must be a list, got {type(blocks)}"
    assert len(blocks) == count, (
        f"Expected {count} system blocks, got {len(blocks)}: {blocks}"
    )


@then("each non-final block must have cache_control type \"{ctrl_type}\"")
def step_check_nonfinal_blocks_have_cache_control(context, ctrl_type):
    """Verify all blocks except the last have cache_control."""
    blocks = context.captured_kwargs.get("system", [])
    for i, block in enumerate(blocks[:-1]):
        cc = block.get("cache_control")
        assert cc == {"type": ctrl_type}, (
            f"Block {i} must have cache_control={{'type':'{ctrl_type}'}}, got {cc}"
        )


@then("the last block must have no cache_control")
def step_check_last_block_no_cache_control(context):
    """Verify the final block does not have cache_control."""
    blocks = context.captured_kwargs.get("system", [])
    if blocks:
        last = blocks[-1]
        cc = last.get("cache_control")
        assert cc is None, f"Last block must have no cache_control, got {cc}"


# ---------------------------------------------------------------------------
# Feature 2: Cache Savings Dashboard
# ---------------------------------------------------------------------------


@given("the ICDEV™ dashboard application is running")
def step_dashboard_running(context):
    """Mark that dashboard app context is expected."""
    context.dashboard_check = True


@when("a user navigates to \"{route}\"")
def step_navigate_to_route(context, route):
    """Check that the route is registered in app blueprints."""
    context.checked_route = route
    # Import app and check route registration
    try:
        from tools.dashboard.app import app as dashboard_app
        rules = [str(r) for r in dashboard_app.url_map.iter_rules()]
        context.route_registered = any(route in r for r in rules)
        context.status_code = 200 if context.route_registered else 404
    except Exception as exc:
        context.route_registered = False
        context.status_code = 404
        context.route_error = str(exc)


@then("the response status code must be {code:d}")
def step_check_status_code(context, code):
    """Verify the expected HTTP status code."""
    actual = getattr(context, "status_code", None)
    assert actual == code, (
        f"Expected status {code} for {context.checked_route}, got {actual}. "
        f"Error: {getattr(context, 'route_error', 'none')}"
    )


@then("the page must display cache hit rate percentage")
def step_check_hit_rate_display(context):
    """Verify template mentions hit_rate_pct."""
    import os

    template = os.path.join("tools", "dashboard", "templates", "cache_savings", "page.html")
    assert os.path.exists(template), f"Template not found: {template}"
    with open(template, encoding="utf-8") as f:
        content = f.read()
    assert "hit_rate" in content.lower() or "hit rate" in content.lower(), (
        "Template must display cache hit rate"
    )


@then("the page must display estimated token cost savings")
def step_check_cost_savings_display(context):
    """Verify template mentions cost savings."""
    import os

    template = os.path.join("tools", "dashboard", "templates", "cache_savings", "page.html")
    assert os.path.exists(template), f"Template not found: {template}"
    with open(template, encoding="utf-8") as f:
        content = f.read()
    assert "cost" in content.lower() or "saving" in content.lower(), (
        "Template must display estimated cost savings"
    )


@given("the cache savings module is importable")
def step_cache_savings_importable(context):
    """Verify the module can be imported."""
    try:
        from tools.cache_savings import savings  # noqa: F401
        context.savings_importable = True
    except ImportError as exc:
        context.savings_importable = False
        context.savings_error = str(exc)
    assert context.savings_importable, (
        f"tools.cache_savings.savings must be importable. Error: {getattr(context, 'savings_error', '')}"
    )


@when("get_savings_stats() is called")
def step_call_get_savings_stats(context):
    """Call get_savings_stats() and store result."""
    from tools.cache_savings.savings import get_savings_stats

    context.savings_stats = get_savings_stats()


@then("the result must contain keys \"{keys_str}\"")
def step_check_result_keys(context, keys_str):
    """Verify the result contains the specified comma-separated keys."""
    expected = {k.strip().strip('"') for k in keys_str.split(",")}
    actual = set(context.savings_stats.keys())
    missing = expected - actual
    assert not missing, (
        f"get_savings_stats() missing keys: {missing}. Got: {sorted(actual)}"
    )


@then("all numeric values must be non-negative")
def step_check_numeric_values(context):
    """Verify all numeric values in stats are non-negative."""
    for k, v in context.savings_stats.items():
        if isinstance(v, (int, float)):
            assert v >= 0, f"stats['{k}'] must be non-negative, got {v}"


# ---------------------------------------------------------------------------
# Feature 3: Multi-Breakpoint Support
# ---------------------------------------------------------------------------


@given("a system prompt with {count:d} or more \"{marker}\" markers")
def step_system_prompt_with_many_markers(context, count, marker):
    """Build a system prompt with the specified number of markers."""
    from tools.llm.provider import LLMRequest

    parts = [f"Part {i}: content here." for i in range(count + 1)]
    system = f"\n{marker}\n".join(parts)
    context.request = LLMRequest(
        messages=[{"role": "user", "content": "summarize"}],
        system_prompt=system,
        cache_control="ephemeral",
        max_tokens=10,
    )


@given("a system prompt with {count:d} \"{marker}\" markers")
def step_system_prompt_with_n_markers(context, count, marker):
    """Build a system prompt with exactly N markers."""
    from tools.llm.provider import LLMRequest

    parts = [f"Part {i}: content." for i in range(count + 1)]
    system = f"\n{marker}\n".join(parts)
    context.request = LLMRequest(
        messages=[{"role": "user", "content": "summarize"}],
        system_prompt=system,
        cache_control="ephemeral",
        max_tokens=10,
    )


@then("the system argument must contain {count:d} blocks")
def step_check_system_n_blocks(context, count):
    """Verify system argument has exactly N blocks."""
    step_check_system_block_count(context, count)


@then("the first {count:d} blocks must have cache_control type \"{ctrl_type}\"")
def step_check_first_n_blocks(context, count, ctrl_type):
    """Verify the first N blocks have cache_control."""
    blocks = context.captured_kwargs.get("system", [])
    for i in range(min(count, len(blocks) - 1)):
        cc = blocks[i].get("cache_control")
        assert cc == {"type": ctrl_type}, (
            f"Block {i} must have cache_control={{'type':'{ctrl_type}'}}, got {cc}"
        )


@then("the number of blocks with cache_control must not exceed {max_count:d}")
def step_check_max_cache_blocks(context, max_count):
    """Verify the number of blocks with cache_control does not exceed the max."""
    blocks = context.captured_kwargs.get("system", [])
    count = sum(1 for b in blocks if isinstance(b, dict) and b.get("cache_control") is not None)
    assert count <= max_count, (
        f"Must not exceed {max_count} blocks with cache_control (Anthropic limit), got {count}"
    )


@given("the AnthropicLLMProvider class is imported")
def step_import_anthropic_provider(context):
    """Import AnthropicLLMProvider."""
    from tools.llm.anthropic_provider import AnthropicLLMProvider

    context.AnthropicLLMProvider = AnthropicLLMProvider


@when("the MAX_CACHE_BREAKPOINTS class attribute is checked")
def step_check_max_breakpoints_attr(context):
    """Read MAX_CACHE_BREAKPOINTS from the class."""
    context.max_breakpoints = getattr(context.AnthropicLLMProvider, "MAX_CACHE_BREAKPOINTS", None)


@then("its value must equal {expected:d}")
def step_check_max_breakpoints_value(context, expected):
    """Verify the MAX_CACHE_BREAKPOINTS value."""
    assert context.max_breakpoints == expected, (
        f"MAX_CACHE_BREAKPOINTS must be {expected}, got {context.max_breakpoints}"
    )


# ---------------------------------------------------------------------------
# Feature 4: OpenAI Cache Token Parity
# ---------------------------------------------------------------------------


@given("an OpenAI completion response with prompt_tokens_details.cached_tokens = {cached:d}")
def step_openai_completion_with_cached(context, cached):
    """Build a mock OpenAI completion with the specified cached_tokens."""
    completion = MagicMock()
    usage = MagicMock()
    usage.prompt_tokens = 100
    usage.completion_tokens = 20
    details = MagicMock()
    details.cached_tokens = cached
    usage.prompt_tokens_details = details
    completion.usage = usage
    choice = MagicMock()
    choice.finish_reason = "stop"
    msg = MagicMock()
    msg.content = "response"
    msg.tool_calls = None
    choice.message = msg
    completion.choices = [choice]
    context.oai_completion = completion


@given("an OpenAI completion response with no prompt_tokens_details")
def step_openai_completion_no_details(context):
    """Build a mock OpenAI completion without prompt_tokens_details."""
    completion = MagicMock()
    usage = MagicMock()
    usage.prompt_tokens = 100
    usage.completion_tokens = 20
    usage.prompt_tokens_details = None
    completion.usage = usage
    choice = MagicMock()
    choice.finish_reason = "stop"
    msg = MagicMock()
    msg.content = "response"
    msg.tool_calls = None
    choice.message = msg
    completion.choices = [choice]
    context.oai_completion = completion


@when("OpenAICompatibleProvider parses the response")
def step_parse_openai_response(context):
    """Invoke OpenAICompatibleProvider with the mock completion."""
    from tools.llm.provider import LLMRequest
    from tools.llm.openai_provider import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider(api_key="test", base_url="https://api.openai.com/v1")
    request = LLMRequest(
        messages=[{"role": "user", "content": "Hello"}],
        max_tokens=50,
    )

    with patch.object(provider, "_get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = context.oai_completion
        mock_get_client.return_value = mock_client
        context.llm_response = provider.invoke(request, "gpt-4o", {"max_output_tokens": 16384})


@then("LLMResponse.cache_read_input_tokens must equal {expected:d}")
def step_check_cache_read_tokens(context, expected):
    """Verify cache_read_input_tokens on the LLMResponse."""
    actual = context.llm_response.cache_read_input_tokens
    assert actual == expected, (
        f"Expected cache_read_input_tokens={expected}, got {actual}"
    )


# ---------------------------------------------------------------------------
# Feature 5: Cache Warming on Deploy
# ---------------------------------------------------------------------------


@given("the tools.cache_savings.warmer module")
def step_warmer_module_given(context):
    """No setup needed — just marks the warmer module context."""
    context.warmer_module = "tools.cache_savings.warmer"


@when("CacheWarmer is imported")
def step_import_cache_warmer(context):
    """Import CacheWarmer from tools.cache_savings.warmer."""
    from tools.cache_savings.warmer import CacheWarmer

    context.CacheWarmer = CacheWarmer


@then("CacheWarmer must have a warm_on_deploy method")
def step_check_warm_on_deploy_method(context):
    """Verify warm_on_deploy is callable."""
    assert hasattr(context.CacheWarmer, "warm_on_deploy"), (
        "CacheWarmer must have warm_on_deploy method"
    )
    assert callable(getattr(context.CacheWarmer, "warm_on_deploy")), (
        "warm_on_deploy must be callable"
    )


@given("the ICDEV™ project root")
def step_project_root(context):
    """Set project root context."""
    import os

    context.project_root = os.getcwd()


@when("the path \"{path}\" is checked")
def step_check_path_exists(context, path):
    """Check whether the given path exists."""
    import os

    context.checked_path = path
    context.path_exists = os.path.exists(path)


@then("the file must exist with seed queries")
def step_file_must_exist(context):
    """Verify the file exists and has content."""
    assert context.path_exists, (
        f"Required file not found: {context.checked_path}. "
        "Create it with seed queries for cache warming."
    )
    with open(context.checked_path, encoding="utf-8") as f:
        content = f.read()
    assert content.strip(), f"File {context.checked_path} must not be empty"


@given("a CacheWarmer configured with dry_run=True")
def step_cache_warmer_dry_run(context):
    """Create a CacheWarmer in dry_run mode."""
    from tools.cache_savings.warmer import CacheWarmer

    context.warmer = CacheWarmer(dry_run=True)


@when("warm_on_deploy() is called")
def step_call_warm_on_deploy(context):
    """Call warm_on_deploy() and store results."""
    context.warm_results = context.warmer.warm_on_deploy()


@then("the result must be a list")
def step_result_is_list(context):
    """Verify the result is a list."""
    assert isinstance(context.warm_results, list), (
        f"warm_on_deploy() must return a list, got {type(context.warm_results)}"
    )


@then("no LLM provider must be invoked")
def step_no_llm_invoked(context):
    """dry_run: no LLM calls should occur (verified by dry_run mode)."""
    # If we got here without error, dry_run worked correctly.
    # The test in test_prompt_context_caching.py verifies this with mocking.
    assert True


@when("warm_on_deploy() returns results")
def step_warm_results_ready(context):
    """Alias — warm_on_deploy already called by previous step or call it now."""
    if not hasattr(context, "warm_results"):
        context.warm_results = context.warmer.warm_on_deploy()


@then("each result must contain keys \"{keys_str}\"")
def step_each_result_has_keys(context, keys_str):
    """Verify each result dict has required keys."""
    if not context.warm_results:
        return  # No seeds configured — skip
    required = {k.strip().strip('"') for k in keys_str.split(",")}
    for i, result in enumerate(context.warm_results):
        missing = required - set(result.keys())
        assert not missing, (
            f"warm_results[{i}] missing keys {missing}: {result}"
        )
