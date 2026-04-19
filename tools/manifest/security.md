# Security (Additional)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Security (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Confabulation Detector | tools/security/confabulation_detector.py | Deterministic confabulation detection (D310) | --check-output, --summary, --json | Detection results |
| Endpoint Security Scanner | tools/security/endpoint_security_scanner.py | API endpoint security assessment | --scan, --json | Scan results |
| Sandbox Executor | tools/security/sandbox_executor.py | Container-isolated code execution with resource limits, network isolation, and audit logging (D-SEC-10) | --execute --code, --execute-file --path, --health, --gate, --language, --timeout, --memory, --json | SandboxResult JSON |
| HTTP Client (mTLS) | tools/http/client.py | Central outbound HTTP session with mTLS client cert, CA bundle, proxy, and default timeout applied via env. Env: ICDEV_MTLS_CLIENT_CERT, ICDEV_MTLS_CLIENT_KEY, ICDEV_MTLS_CA_BUNDLE, ICDEV_MTLS_VERIFY, ICDEV_HTTP_TIMEOUT, ICDEV_HTTP_PROXY, ICDEV_HTTPS_PROXY. | `get_session()` / `request(method, url, **kwargs)` | `requests.Session` / `requests.Response` |
| Caldera Adapter | tools/security_canvas/caldera_adapter.py | Read-only MITRE Caldera v2 REST adapter. Fetches adversary scenarios and abilities; builds cached ability→ATT&CK technique-ID map. Graceful degradation when Caldera unreachable (in-memory + on-disk cache). | `CalderaAdapter(url, api_key, timeout, cache_dir)` → `.fetch_scenarios()`, `.fetch_abilities()`, `.ability_technique_map`, `.health()` | list / dict |
| Attack Path Twin | tools/security_canvas/attackpath.py | SDC Attack Path Twin data helpers (dt-sdc-twin-07). Pure-function reads of `sdc_attack_snapshots` table; returns node/edge summary and BFS path enumeration. Feeds `/security/attackpath` dashboard page. | `get_attackpath_summary(conn)` → dict; `enumerate_paths(snapshots)` → list | dict / list |
| Attack Graph Builder | tools/security_canvas/attack_graph_builder.py | SDC attack graph builder. Converts STRIDE assessment results + attack_path_finder output into a normalized attack graph (nodes = assets, edges = exploitable transitions annotated with MITRE ATT&CK TTP IDs). Pure deterministic functions — no Flask, no LLM, no I/O. | Asset + STRIDE dicts | Attack graph dict with nodes/edges + TTP annotations |
| Attack Path Twin (BAS Replay) | tools/security_canvas/attack_path_twin.py | SDC Attack Path Twin — BAS-style replay engine over the formal attack graph. `build_attack_graph()` converts raw SDC graph_data; `replay_attack_paths()` runs Dijkstra min-cost path enumeration across classification-layered nodes + TTP-annotated edges; `query_attack_paths()` provides IQE-compatible predicate filtering. Pure deterministic; no Flask/LLM. | `build_attack_graph`, `replay_attack_paths`, `query_attack_paths` | Graph / paths / filtered paths |

