# SPIKE — Discord + Email gateway adapters (sag-gw-02)

**Status:** Decided — **Email = GO** (built in this PR), **Discord = NO-GO** (deferred)
**Date:** 2026-07-25
**Surface:** `tools/gateway/adapters/` (Remote Command Gateway)

## Context

The Remote Command Gateway ships seven channel adapters — Telegram, Slack, Teams,
Mattermost, Skype, GitHub, GitLab — all webhook-based, all subclassing
`tools/gateway/adapters/base.py::BaseChannelAdapter` and feeding a channel-agnostic
`CommandEnvelope` through the shared 8-gate `run_security_chain`. This spike
evaluated adding **Discord** and **Email** before committing to build either.

Two standing constraints shape the decision:
- **Pure-Python / offline preference** (memory: "No npm — prefer pure-Python/offline
  tooling"): avoid heavyweight third-party dependencies; keep air-gap/IL5 viable.
- **Security chain must stay intact**: a new adapter may only produce envelopes and
  send text — never add a bypass around the identity / auth / classification / RBAC /
  rate-limit gates.

## (a) User demand

Email is the lowest-common-denominator channel that works in **every** enclave,
including disconnected IL5/IL6 sites with a self-hosted mail server where none of
the existing internet-dependent chat channels (Telegram/Slack/Teams/Discord) can
run. That is concrete, unmet demand: an operator on SIPR/JWICS can already run a
mail server but cannot reach a cloud chat API. Discord, by contrast, overlaps almost
entirely with the already-shipped Telegram/Slack adapters and targets community/
hobbyist rather than government-enclave users — no distinct demand was identified.

## (b) Discord — NO-GO

- The idiomatic client, `discord.py`, is a **heavyweight async gateway/websocket**
  dependency (aiohttp + a persistent event loop). It is a poor fit for ICDEV's
  synchronous, poll/webhook adapter contract and directly conflicts with the
  pure-Python/offline preference.
- Discord **requires an outbound websocket to Discord's cloud gateway** — it cannot
  run air-gapped, so it adds no coverage the existing internet channels lack.
- A dependency-free path (Discord *Interactions* over an HTTP webhook with Ed25519
  request-signature verification) is technically possible and would fit `base.py`
  cleanly, but with no demonstrated demand it is **deferred**, not built. If demand
  appears, implement the webhook-Interactions variant (stdlib + a single Ed25519
  verify) rather than `discord.py`.

**Decision: do not build now.** Recorded so a future request starts from the
webhook-Interactions design, not `discord.py`.

## (c) Email — GO (built here)

- **No new dependency.** Inbound uses stdlib `imaplib` (poll), outbound uses stdlib
  `smtplib` (send) + `email` (parse) — nothing added to `requirements.txt`.
- **Air-gap / IL5 viable.** Works against a self-hosted mail server inside an enclave
  (`requires_internet: false`), unlike every existing chat channel.
- **Fits the adapter contract.** `EmailAdapter(BaseChannelAdapter)` implements
  `verify_signature` / `parse_webhook` / `send_message`. Because email is *polled*,
  `poll_once()` fetches `UNSEEN` messages over an authenticated IMAP session,
  normalises each to the same dict shape a webhook body would have, and hands it to
  `parse_webhook` — so inbound email flows through the **identical** security chain
  as every other channel.

### Design notes / trade-offs

- **Poll, not webhook.** There is no inbound HTTP webhook and therefore no per-message
  HMAC. `verify_signature` returns `True` (not applicable) and is documented as such;
  authenticity is established two ways: (1) messages are only ever read from an
  **authenticated IMAP mailbox**, and (2) the sender's `From` address is the
  `channel_user_id` that the gateway's **identity-binding gate** must match to a bound
  ICDEV user — an unbound sender is rejected at gate 3 exactly like any other channel.
  This is a deliberate trade-off: full sender authentication (DKIM/SPF/ARC
  verification) is **out of scope for the spike** and noted as a follow-on hardening
  (see below).
- **Bot/loop protection.** RFC 3834 `Auto-Submitted` and `Precedence: bulk|list|auto_reply`
  headers set `is_bot=True`, so auto-responders and mailing-list traffic are dropped by
  the existing bot-detection gate — preventing mail loops.
- **Threading.** Replies set `In-Reply-To`/`References`; inbound thread grouping keys on
  `In-Reply-To` (then the `References` root) so an email conversation maps to one gateway
  thread.
- **Command extraction.** The command is read from the `Subject` first, falling back to
  the first body line that names an ICDEV command (`icdev-*` / `bind`), mirroring the
  other adapters' "only process ICDEV commands" filter.

### What this PR ships

- `tools/gateway/adapters/email_channel.py` — the adapter (+ `poll_once`).
- Registration in `tools/gateway/gateway_agent.py` (`adapter_classes["email"]`).
- `args/remote_gateway_config.yaml` — an `email` channel, **`enabled: false`** by default.
- `docs/security/sandbox-coverage.md` — Gap 34 (external-content ingress decision).
- DB-independent tests with IMAP/SMTP mocked.

### Follow-on (not in this spike)

- **DKIM/SPF verification** of inbound mail before binding, to harden against `From`
  spoofing (today the identity-binding gate + authenticated mailbox are the controls).
- **A poller driver.** `poll_once()` returns envelopes; wiring a scheduled poller
  (a Genesis reflex or gateway thread) that feeds them through the chain is a small
  follow-on — the envelope→chain processing is currently coupled to the Flask webhook
  route and would benefit from a extracted `process_envelope()` first.
