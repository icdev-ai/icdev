# Resilience (D146-D149)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Resilience (D146-D149)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Circuit Breaker | tools/resilience/circuit_breaker.py | 3-state circuit breaker with ABC + InMemory backend (D146) | (library) | CircuitBreakerBackend |
| Retry | tools/resilience/retry.py | Exponential backoff + full jitter decorator (D147) | (library) | @retry decorator |
| Errors | tools/resilience/errors.py | Structured exception hierarchy (D148) | (library) | ICDevError hierarchy |
| Correlation | tools/resilience/correlation.py | Request-scoped correlation ID middleware (D149) | (library) | Flask middleware + get_correlation_id |

