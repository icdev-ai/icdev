# CUI // SP-CTI
Feature: Prompt and Context Caching Enhancements
  As an ICDEV™ platform engineer
  I want RAG-injected chunks to leverage Anthropic cache_control breakpoints,
  a cache savings visibility dashboard, multi-breakpoint support up to 4,
  OpenAI cached token tracking parity, and cache warming on deploy
  So that token costs are minimized and savings are observable

  # NIST 800-53: SC-28 (Protection at Rest), SA-11 (Developer Testing),
  # AU-12 (Audit Record Generation), SI-7 (Software Integrity)

  # ===========================================================================
  # Feature 1: RAG Context Caching
  # ===========================================================================

  Scenario: RAG-augmented request preserves cache_control for provider
    Given the LLM router has RAG injection enabled with context_cache configured
    When a request with cache_control "ephemeral" is augmented with RAG chunks
    Then the augmented request must still have cache_control set to "ephemeral"

  Scenario: RAG augment inserts cache breakpoint marker when caching is active
    Given the LLM router has RAG injection enabled with context_cache configured
    When a request with cache_control "ephemeral" is augmented with RAG chunks
    Then the augmented system prompt must contain "<!-- cache_breakpoint -->"

  Scenario: No cache breakpoint marker inserted when caching is inactive
    Given the LLM router has RAG injection enabled without cache_control set
    When a request without cache_control is augmented with RAG chunks
    Then the augmented system prompt must NOT contain "<!-- cache_breakpoint -->"

  Scenario: Anthropic provider splits system prompt at breakpoint markers
    Given a request with system prompt containing one "<!-- cache_breakpoint -->" marker
    When the AnthropicLLMProvider invokes the request with cache_control "ephemeral"
    Then the system argument must be a list of 2 text blocks
    And each non-final block must have cache_control type "ephemeral"

  # ===========================================================================
  # Feature 2: Cache Savings Dashboard
  # ===========================================================================

  Scenario: Cache savings dashboard page is accessible
    Given the ICDEV™ dashboard application is running
    When a user navigates to "/cache-savings"
    Then the response status code must be 200
    And the page must display cache hit rate percentage
    And the page must display estimated token cost savings

  Scenario: Cache savings stats API returns required metrics
    Given the cache savings module is importable
    When get_savings_stats() is called
    Then the result must contain keys "hit_count", "miss_count", "hit_rate_pct", "tokens_saved", "cost_usd_saved"
    And all numeric values must be non-negative

  # ===========================================================================
  # Feature 3: Multi-Breakpoint Support
  # ===========================================================================

  Scenario: Two breakpoint markers produce three cache blocks
    Given a system prompt with 2 "<!-- cache_breakpoint -->" markers
    When AnthropicLLMProvider invokes with cache_control "ephemeral"
    Then the system argument must contain 3 blocks
    And the first 2 blocks must have cache_control type "ephemeral"
    And the last block must have no cache_control

  Scenario: More than 3 markers are capped at Anthropic limit of 4 blocks
    Given a system prompt with 5 or more "<!-- cache_breakpoint -->" markers
    When AnthropicLLMProvider invokes with cache_control "ephemeral"
    Then the number of blocks with cache_control must not exceed 4

  Scenario: AnthropicLLMProvider defines MAX_CACHE_BREAKPOINTS constant
    Given the AnthropicLLMProvider class is imported
    When the MAX_CACHE_BREAKPOINTS class attribute is checked
    Then its value must equal 4

  # ===========================================================================
  # Feature 4: OpenAI Cache Token Parity
  # ===========================================================================

  Scenario: OpenAI provider extracts cached_tokens from prompt_tokens_details
    Given an OpenAI completion response with prompt_tokens_details.cached_tokens = 75
    When OpenAICompatibleProvider parses the response
    Then LLMResponse.cache_read_input_tokens must equal 75

  Scenario: OpenAI provider returns zero cached tokens when details are absent
    Given an OpenAI completion response with no prompt_tokens_details
    When OpenAICompatibleProvider parses the response
    Then LLMResponse.cache_read_input_tokens must equal 0

  # ===========================================================================
  # Feature 5: Cache Warming on Deploy
  # ===========================================================================

  Scenario: CacheWarmer module is importable
    Given the tools.cache_savings.warmer module
    When CacheWarmer is imported
    Then CacheWarmer must have a warm_on_deploy method

  Scenario: Cache warming seed queries file exists
    Given the ICDEV™ project root
    When the path "context/cache_seeds/default_seeds.yaml" is checked
    Then the file must exist with seed queries

  Scenario: warm_on_deploy in dry_run mode returns results without LLM calls
    Given a CacheWarmer configured with dry_run=True
    When warm_on_deploy() is called
    Then the result must be a list
    And no LLM provider must be invoked

  Scenario: warm_on_deploy result entries have required structure
    Given a CacheWarmer configured with dry_run=True
    When warm_on_deploy() returns results
    Then each result must contain keys "function", "status", "query"
