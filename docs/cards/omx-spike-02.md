# omx-spike-02 — can a harness reach Claude through claude-cli WITH working tool calls?

Go/no-go spike, scratch code only. Measured 2026-10-11 on the acceptance host
`ssh omarchy` (Claude login: `claude auth status` → `"authMethod": "claude.ai"`,
`"apiProvider": "firstParty"`). Every harness call below ran with `ANTHROPIC_API_KEY`
and `ANTHROPIC_AUTH_TOKEN` stripped from the environment (`env -u ...`), and the
bridge strips them (plus `ANTHROPIC_BASE_URL`) from the environment of its own `claude` child process.

## Verdict

**GO for the mechanism, with one finding that changes the premise.**

- **Mechanism: works.** A 127.0.0.1-only OpenAI chat-completions endpoint that forwards each
  turn to `claude -p` (claude's own tools disabled, tool calls returned as schema-constrained
  JSON) carried a 3-step coding task end to end for **opencode** (15 s) and **pi** (10 s). Every
  tool call was executed by the harness itself, and both runs left correct files on disk.
- **Premise: weaker than assumed.** Press reports of Anthropic's own posts (§(c)) say that since
  **2026-06-15** `claude -p` and Agent-SDK usage no longer draw from the plan's interactive limits.
  They draw from a separate monthly **programmatic credit** (Max 20x: $200, Max 5x: $100, Pro: $20)
  that is deducted at API rates. When that credit runs out, usage either stops until the next reset
  or, if the operator has turned on "Usage Credits", is billed at API prices. A bridge built on
  `claude -p` therefore **does not bill an API key**, but it **is not "free under the plan"** either.
  It spends a bounded, included credit. That credit becomes real money only if Usage Credits is on.
  Pi's native subscription login hits the equivalent wall today (measured, §(a)).
- **Operator action required before building:** keep **Usage Credits OFF** in claude.ai settings,
  so that running out of credit fails closed instead of billing, and confirm the June change on an
  official Anthropic page. This spike could only reach press reports of X posts (§(c)). This also
  affects the existing `tools/llm/cli_bridge/` path (`cli_bridge.mode: prefer` on omarchy), which
  already spends this credit.
- **Terms:** the bridge runs the **unmodified** `claude` binary under the operator's **own** login,
  for the operator's **own** harnesses. It never touches or stores a claude.ai token. The docs prohibit
  routing a subscription "on behalf of their users" and collecting or intermediating credentials. A
  single operator driving their own CLI does neither. However, the docs also say the advertised limits
  "assume ordinary, individual usage", and Anthropic "reserves the right to take measures". Whether
  the bridge counts as ordinary use is the operator's decision, not this spike's.

### Per-harness route

| Harness | Version (omarchy) | Native claude.ai subscription login? | Evidence | Route |
|---|---|---|---|---|
| claude | 2.1.296 (Claude Code) | yes | `claude auth status` → `authMethod: claude.ai`; bridge calls succeeded | `native_subscription` (headless `-p` draws on the programmatic credit) |
| pi | 1.1.0 | has one, but **refused** | `pi auth check --provider anthropic --json` → `{"status":"ready","provider":"anthropic","authType":"oauth"}`; then `pi --provider anthropic --model claude-haiku-4-5 -p ...` → `400 invalid_request_error: "Third-party apps now draw from your extra usage, not your plan limits. Add more at claude.ai/settings/usage and keep going."` (request_id `req_011Cfw1Lbu5skLo3J5eN1gNg`), **exit 0** | `cli_bridge` (measured GO) |
| opencode | 1.18.35 | no | `opencode auth list` → only `Ollama Cloud api`; `opencode models anthropic` → `Provider not found: anthropic` | `cli_bridge` (measured GO) |
| codex | codex-cli 0.162.1 | no (its `login` is ChatGPT/OpenAI) | `codex --help` → `login  Manage login` (OpenAI) | `cli_bridge` (not run through the bridge; it speaks OpenAI-style APIs, so the same endpoint should apply, but this is **unmeasured**) |
| crush | v0.98.1 | no | `crush login --help` → "Available platforms are: hyper, copilot, openai (chatgpt), grok (xai)" | `cli_bridge` (**unmeasured**) |
| copilot | GitHub Copilot CLI 1.0.95 | no Anthropic login; Claude models through Copilot are billed by GitHub | `copilot --help` → `providers  Custom Model Providers (BYOK)` | `none` for the Anthropic subscription (`cli_bridge` via BYOK is possible but **unmeasured**) |
| goose | absent on omarchy (1.28.0 on the Windows dev host) | not measured | `command -v goose` → absent | `none` until installed and measured |

## (a) Native-subscription evidence

See the table. The decisive measurement is Pi. Pi does support Anthropic Pro/Max OAuth, and its
credential is present and refreshable (`authType: oauth`, `ready`). Even so, Anthropic answers its
requests with a 400 that sends third-party apps to *extra usage*. The native route is open only to
Claude Code itself. **Gotcha:** Pi exits **0** on that 400 (the same pattern recorded for a provider
404), so any adapter has to read the response body rather than trust the exit code.

## (b) Loopback bridge prototype — measured

Scratch code (not committed to `tools/`): `bridge.py`, about 150 lines, with Python stdlib only,
bound to `127.0.0.1:8765`. It accepts `POST /v1/chat/completions` and `GET /v1/models`.

**Mechanism B (built and measured): claude's tools disabled + structured tool-call output.**
Each request is rendered into one prompt: the harness's tool definitions as JSON, then the
conversation with `[ASSISTANT TOOL CALLS]` and `[TOOL RESULT id=...]` turns. It runs as

```
claude -p --tools "" --strict-mcp-config --setting-sources "" --no-session-persistence \
  --output-format json --model <m> --system-prompt <bridge prompt> \
  --json-schema '{"kind": "final"|"tool_calls", "text": str, "calls": [{"name", "arguments_json"}]}'
```

`structured_output` is mapped to an OpenAI `message.tool_calls[]` with `finish_reason: tool_calls`,
or to `content` with `finish_reason: stop`. Each turn is stateless, because the harness resends the
whole history every time.

**Mechanism A (MCP relay): analysed, not built.** In this design claude would call the harness's tools
through an MCP server inside **its own** agent loop. That inverts control. OpenAI and Anthropic clients
expect the model to *return* a tool call, then execute it themselves, then send a *new* request with the
result. A relay would have to hold the first HTTP request open while it re-enters the harness, and no
stock harness supports that. Mechanism B fits the API contract directly, so A was dropped.

Task given to both harnesses: *"(1) create calc.py defining add(a, b); (2) create test_calc.py
that asserts add(2, 3) == 5 then prints CALC-OK; (3) run python3 test_calc.py and tell me its exact output."*

| | opencode 1.18.35 | pi 1.1.0 |
|---|---|---|
| Config | throwaway `OPENCODE_CONFIG` with provider `@ai-sdk/openai-compatible`, baseURL `http://127.0.0.1:8765/v1` | throwaway `PI_CODING_AGENT_DIR` with `models.json` provider `api: openai-completions` |
| Result | **PASS**: files created; `python3 test_calc.py` → `CALC-OK`; model reported `CALC-OK` | **PASS**: same; correct final summary |
| Wall clock | 15 s | 10 s |
| Bridge turns | 4 (1 title-generation turn, then `[bash, bash]` → `[bash]` → final) | 3 (`[write, write]` → `[bash]` → final) |
| Per-turn latency (`claude -p`) | 1.95 / 6.45 / 2.47 / 1.92 s | 4.72 / 2.10 / 2.51 s |
| Parallel tool calls | yes (2 in one turn) | yes (2 in one turn) |
| Tools offered | **539** (opencode merges the global config, which includes the icdev-unified MCP server; the bridge truncated the definitions at 60 KB and the task still passed because `bash` comes first) | 4 |
| Streaming | `stream: true`, served **buffered**: one SSE delta carrying the whole turn, then finish and `[DONE]`. Accepted. | same, accepted |

Findings:
- **Tool-call round trip: correct for both harnesses.** No malformed arguments, and no claims of
  having run a tool without calling it.
- **Streaming is not token-level.** `--json-schema` output is only available once the turn completes,
  so time-to-first-token equals turn latency (about 2–6 s). True token streaming would need
  `--output-format stream-json --include-partial-messages` parsed incrementally. That is feasible for
  text turns but not for schema-constrained tool turns.
- **Prompt cache works across turns** (`cache_read` 32,688 then 699 tokens), so repeated history is
  not re-paid in full. The usage reported by this path is still **unpriced**
  (`args/cache_effectiveness.yaml usd_basis: unpriced`), even though the credit it spends is metered at API rates.
- **Concurrency:** 6 simultaneous requests (haiku) gave 6× HTTP 200 in 1.74–2.23 s each, 3 s wall,
  with no rate-limit errors. Each request is a separate `claude` process. Subscription rate limits did
  not bite at 6-way. With 539 tool definitions per turn, a fleet would spend the programmatic credit
  quickly, so a production bridge should cap concurrency and **trim tool definitions** rather than send
  all of them on every turn.
- **Gotcha (pi):** over a non-TTY stdin (ssh, subprocess), `pi -p` blocks forever waiting on stdin.
  The first run hung for 360 s and sent **zero** requests. Use `</dev/null`. Any ICDEV adapter that
  launches pi headless needs `stdin=DEVNULL`.
- Not built in this spike: an Anthropic Messages-API front (`/v1/messages`). It would use the same
  core, with `tool_use` and `tool_result` blocks in place of `tool_calls` and `role: tool`.

## (c) Terms and usage policy — quotes

1. **Claude Code docs, "Legal and compliance"**, https://code.claude.com/docs/en/legal-and-compliance (fetched 2026-10-11):
   - "**OAuth authentication** is intended exclusively for purchasers of Claude Free, Pro, Max, Team, and Enterprise subscription plans and is designed to support ordinary use of Claude Code and other native Anthropic applications."
   - "Anthropic does not permit third-party developers to offer Claude.ai login into their own applications, or to route requests through Free, Pro, or Max plan credentials on behalf of their users. Moreover, developers may not collect, store, or intermediate Claude.ai credentials or session tokens — sign-in to a Claude account must complete through Anthropic's own flow."
   - "Nor does it prevent an end user from signing in to the unmodified Claude Code binary with their own Claude subscription [...]"
   - "Advertised usage limits for Pro and Max plans assume ordinary, individual usage of Claude Code and the Agent SDK."
   - "Anthropic reserves the right to take measures to enforce these restrictions and may do so without prior notice."
   - Consumer use is governed by the Consumer Terms (https://www.anthropic.com/legal/consumer-terms) and the Usage Policy (https://www.anthropic.com/legal/aup).
2. **Programmatic credit (2026-06-15)**: secondary source, The Decoder,
   https://the-decoder.com/claude-subscriptions-get-separate-budgets-for-programmatic-use-billed-at-full-api-prices/ ,
   citing Anthropic's @ClaudeDevs posts https://x.com/ClaudeDevs/status/2054610152817619388 ,
   https://x.com/ClaudeDevs/status/2054610157364289906 , https://x.com/ClaudeDevs/status/2054610156214956455 :
   - Programmatic use covers "the Claude Agent SDK, the `claude -p` command-line tool, Claude Code GitHub Actions" and "any third-party apps built on the Agent SDK".
   - "it gets deducted from a separate credit pool at full API rates instead." Pro $20, Max 5x $100, Max 20x $200 per month; it resets each billing cycle. Once exhausted, "Usage Credits, which are billed at API prices" can be toggled on or off. With them off, the user waits for the reset.
   - **Not verified on an official Anthropic page by this spike.** The operator should confirm before relying on it.
3. **2026-04-04 third-party harness change**: press reports, e.g. https://engadget.com/ai/its-no-longer-free-to-use-claude-through-third-party-tools-like-openclaw-160912082.html ,
   https://www.techradar.com/pro/bad-news-claude-users-anthropic-says-youll-need-to-pay-to-use-openclaw-now . These say third-party
   harnesses using subscription OAuth must use extra usage or an API key. This matches the measured Pi 400 in §(a).

## Next card (if the operator accepts)

Productize mechanism B as a loopback front on `tools/llm/cli_bridge/`, with:
- OpenAI and Anthropic routes;
- 127.0.0.1 only;
- environment stripping, so the bridge never touches an API key;
- capped concurrency;
- tool-definition trimming;
- `stdin=DEVNULL` for pi;
- usage reported as `unpriced`;
- a health check that reads the 400 "extra usage" body, so a fail-closed state is visible rather than silent.
