# CUI // SP-CTI
"""Cache Savings — constants for cost modelling and UI."""

# Anthropic claude-sonnet-4-6 pricing (USD per token)
PRICE_INPUT_PER_TOKEN = 3.00 / 1_000_000        # $3.00/MTok
PRICE_OUTPUT_PER_TOKEN = 15.00 / 1_000_000       # $15.00/MTok
PRICE_CACHE_WRITE_PER_TOKEN = 3.75 / 1_000_000   # $3.75/MTok (25% premium)
PRICE_CACHE_READ_PER_TOKEN = 0.30 / 1_000_000    # $0.30/MTok (90% discount)

# Savings per read token vs. full input price
CACHE_READ_SAVINGS_PER_TOKEN = PRICE_INPUT_PER_TOKEN - PRICE_CACHE_READ_PER_TOKEN

IQE_EXAMPLES = [
    {"label": "Hit rate", "query": "SELECT function, hit_rate_pct FROM cache.stats ORDER BY hit_rate_pct DESC"},
    {"label": "Top functions", "query": "SELECT function, total_entries, total_hits FROM cache.stats ORDER BY total_hits DESC LIMIT 10"},
    {"label": "Cost saved", "query": "SELECT function, cost_usd_saved FROM cache.stats ORDER BY cost_usd_saved DESC"},
]
