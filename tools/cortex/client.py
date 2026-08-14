# CUI // SP-CTI
"""Cortex service client SDK — stdlib-only, safe to vendor (ctx-expose-06).

ACCESS PATTERN — read this first if you are wiring a new consumer:
docs/features/cortex-child-app-access-pattern.md. Cortex is a PARENT-HOSTED
governed service reached over REST with an ``icdev_ctx_`` service key. It is
never copied into a child app, a canvas, or any descendant — `tools/cortex`
appears ZERO times in tools/builder/child_app_generator.py, whose DIRECTORY_TREE
is an allowlist. What you vendor is THIS FILE, not Cortex. That doc also spells
out the degradation contract below, why this file is stdlib-only, and the
standing decision that no in-repo apps/ consumer should call Cortex over REST.

CANONICAL SOURCE: icdev tools/cortex/client.py. Standalone apps (compass,
idea_lab) vendor this file verbatim into tools/integrations/cortex_client.py
with a provenance header — keep it importable with ZERO icdev dependencies
(urllib/json/os only) so copies never drift into needing the platform.

Those copies are declared in ``args/vendor_parity.yaml``. The drift is latent —
a method no consumer calls yet goes missing without breaking anything — so
adding or renaming a public member here is TWO steps:

    python tools/workflow/vendor_api_manifest.py --write   # record the new API
    # then re-vendor this file into compass / idea_lab (keep only their header)

Skipping the first fails ``check_vendor_parity`` and
``tests/workflow/test_vendor_api_manifest.py`` (ctx-enf-01). The manifest is
what enforces here, because ICDEV CI never checks out the consumer repos and so
the copy-vs-canonical comparison can only ever SKIP on a runner (cxo-doc-03).

Server surfaces this client speaks to (one ICDEV host):

    POST {host}/cortex/api/v1/{search,ask,complete,reason,classify,extract,govern}
    POST {host}/cortex/api/v1/agent                        (scope cortex:agent)
    POST {host}/cortex/api/v1/intake/{session,turn}       (RICOAS intake bridge)
    GET  {host}/cortex/api/v1/intake/session/<id>
    GET  {host}/cortex/api/v1/health                      (unauthenticated)
    GET/POST {host}/api/databridge/v1/<connector>/<table> (IRIS feeds)

Auth: an ``icdev_ctx_`` Cortex service key sent as ``Authorization: Bearer``.
The key row binds tenant/classification/scopes SERVER-SIDE
(tools/cortex/service_keys.py) — this client sends no identity fields; the
only caller-supplied context is ``domain`` (narrows, never widens).

Degradation contract (mirrors compass tools/integrations/icdev_client.py):
methods return the parsed JSON dict on success and ``None`` when Cortex is
UNREACHABLE (disabled config, missing key, refused connection, timeout, 5xx,
malformed JSON). They NEVER raise. 4xx responses with a JSON body — 400
validation, 401/403 auth/scope/governance-blocked, 422 analyst-unanswerable
— are returned as that body, so callers can distinguish "Cortex refused /
could not answer" (surface it) from "Cortex unavailable" (degrade silently).
Blocked responses carry ``{"blocked": True, "governance": {...}}``.

Usage::

    from tools.cortex.client import CortexClient

    client = CortexClient(
        base_url="http://icdev-host:5050",
        api_key=os.environ.get("COMPASS_CORTEX_API_KEY", ""),
    )
    result = client.ask("How many tasks are blocked?")   # dict | None
    if result and not result.get("error"):
        answer = result["text"]        # CortexResult.to_dict() shape
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional
from urllib import error, request as urlrequest

DEFAULT_TIMEOUT = 60          # seconds — complete/classify/extract/search/govern
DEFAULT_ASK_TIMEOUT = 120     # seconds — ask fans out across retrieval backends
_USER_AGENT = "ICDEV-CortexClient/1.0"

_CORTEX_PREFIX = "/cortex/api/v1"
_DATABRIDGE_PREFIX = "/api/databridge/v1"


class CortexClient:
    """HTTP client for the ICDEV Cortex service surface."""

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        *,
        api_key_env: str = "ICDEV_CORTEX_API_KEY",
        timeout: int = DEFAULT_TIMEOUT,
        ask_timeout: int = DEFAULT_ASK_TIMEOUT,
        enabled: bool = True,
    ) -> None:
        # base_url is the ICDEV host root, e.g. "http://icdev-host:5050".
        self.base_url = (base_url or os.environ.get("ICDEV_CORTEX_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get(api_key_env, "")
        self.timeout = timeout
        self.ask_timeout = ask_timeout
        self.enabled = enabled

    # -- plumbing --------------------------------------------------------------

    def _request(self, method: str, path: str, payload: Optional[dict],
                 timeout: Optional[int]) -> Optional[dict]:
        """One HTTP round trip. Parsed JSON on 2xx; 4xx JSON bodies returned
        (refusals are answers); None on any transport failure or 5xx."""
        if not self.enabled or not self.base_url:
            return None
        headers = {
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            try:
                body = json.dumps(payload).encode("utf-8")
            except (TypeError, ValueError):
                return None
        req = urlrequest.Request(
            f"{self.base_url}{path}", data=body, headers=headers, method=method
        )
        try:
            with urlrequest.urlopen(req, timeout=timeout or self.timeout) as resp:  # noqa: S310
                text = resp.read().decode("utf-8", errors="replace")
                return json.loads(text) if text.strip() else None
        except error.HTTPError as exc:
            if 400 <= exc.code < 500:
                try:
                    parsed = json.loads(exc.read().decode("utf-8", errors="replace"))
                    if isinstance(parsed, dict):
                        parsed.setdefault("http_status", exc.code)
                        return parsed
                except Exception:  # noqa: BLE001
                    return None
            return None
        except Exception:  # noqa: BLE001 — refused/timeout/DNS/malformed JSON
            return None

    def _post(self, name: str, payload: dict, timeout: Optional[int] = None) -> Optional[dict]:
        return self._request("POST", f"{_CORTEX_PREFIX}/{name}", payload, timeout)

    @staticmethod
    def _with_domain(payload: dict, domain: str) -> dict:
        if domain:
            payload["domain"] = domain
        return payload

    # -- the core REST operations ------------------------------------------------

    def search(self, query: str, *, top_k: int = 5, strategy: str = "auto",
               domain: str = "", timeout: Optional[int] = None) -> Optional[dict]:
        """Unified retrieval. Success shape: {"results": [...], "count": N}."""
        payload = {"query": query, "top_k": top_k, "strategy": strategy}
        return self._post("search", self._with_domain(payload, domain), timeout)

    def ask(self, question: str, *, mode: str = "auto", canvas: str = "",
            collections: Optional[List[str]] = None, summarize: bool = False,
            domain: str = "", timeout: Optional[int] = None) -> Optional[dict]:
        """Ask-your-data. Success shape: CortexResult.to_dict()."""
        payload: Dict[str, Any] = {"question": question, "mode": mode, "summarize": summarize}
        if canvas:
            payload["canvas"] = canvas
        if collections:
            payload["collections"] = collections
        return self._post("ask", self._with_domain(payload, domain), timeout or self.ask_timeout)

    def complete(self, prompt: str, *, system_prompt: str = "",
                 max_tokens: Optional[int] = None, temperature: Optional[float] = None,
                 domain: str = "", timeout: Optional[int] = None) -> Optional[dict]:
        """Governed free-form completion. Success shape: CortexResult.to_dict()."""
        payload: Dict[str, Any] = {"prompt": prompt}
        if system_prompt:
            payload["system_prompt"] = system_prompt
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if temperature is not None:
            payload["temperature"] = temperature
        return self._post("complete", self._with_domain(payload, domain), timeout)

    def reason(self, prompt: str, *, mode: str = "cot", system_prompt: str = "",
               max_tokens: Optional[int] = None, temperature: Optional[float] = None,
               domain: str = "", timeout: Optional[int] = None) -> Optional[dict]:
        """Governed multi-step reasoning: ``cot`` | ``debate`` | ``council``.

        The endpoint has existed since ctx-expose-02; this client had no method
        for it, so every consumer that wanted a reasoned answer either hand-rolled
        a POST or settled for ``complete``. Success shape: CortexResult.to_dict().

        The three modes are chain ORCHESTRATIONS, not models — which provider
        serves each step is ``args/llm_config.yaml``'s business, server-side.
        A chain that cannot be assembled degrades to a single pass rather than
        failing, so a caller always gets an answer or an explicit refusal.

        Defaults to ``ask_timeout``: debate and council run several passes, and a
        60-second ceiling would time out a working call and read as an outage.
        """
        payload: Dict[str, Any] = {"prompt": prompt, "mode": mode}
        if system_prompt:
            payload["system_prompt"] = system_prompt
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if temperature is not None:
            payload["temperature"] = temperature
        return self._post("reason", self._with_domain(payload, domain),
                          timeout or self.ask_timeout)

    def classify(self, text: str, labels: List[str], *, domain: str = "",
                 timeout: Optional[int] = None) -> Optional[dict]:
        """Single-label classification. CortexResult.to_dict(); text = label."""
        payload = {"text": text, "labels": labels}
        return self._post("classify", self._with_domain(payload, domain), timeout)

    def extract(self, text: str, schema: dict, *, domain: str = "",
                timeout: Optional[int] = None) -> Optional[dict]:
        """Structured extraction. CortexResult.to_dict(); text = JSON string."""
        payload = {"text": text, "schema": schema}
        return self._post("extract", self._with_domain(payload, domain), timeout)

    def govern(self, text: str, *, context_sources: Any = None, retrieval: bool = True,
               operation: str = "", domain: str = "",
               timeout: Optional[int] = None) -> Optional[dict]:
        """Run the TRUST chain over drafted text.

        Success shape: {"text": <governed/redacted>, "grounded": bool,
        "blocked": bool, "governance": GovernanceReport dict}.
        """
        payload: Dict[str, Any] = {"text": text, "retrieval": retrieval}
        if context_sources is not None:
            payload["context_sources"] = context_sources
        if operation:
            payload["operation"] = operation
        return self._post("govern", self._with_domain(payload, domain), timeout)

    # -- Agent launch (hgx-cx-02; scope cortex:agent — NEVER in the default grant)

    def agent(self, goal: str, *, mode: str = "auto",
              roles: Optional[List[str]] = None,
              workflow_id: str = "", project_id: str = "",
              inputs: Optional[dict] = None,
              max_iterations: Optional[int] = None,
              system_prompt: str = "", llm_function: str = "",
              trigger_ref: str = "", domain: str = "",
              timeout: Optional[int] = None) -> Optional[dict]:
        """Launch a goal through the multi-agent stack. Governed end to end.

        This is the only method on this client that makes the platform ACT rather
        than answer, and it needs the ``cortex:agent`` scope, which is never
        granted by default. Three modes:

        * ``team`` (or ``auto`` with ``roles``) — an ACE run. NON-BLOCKING:
          ``data["instance_id"]``; poll ``/coworker/<id>``.
        * ``single`` (or ``auto`` without ``roles``) — one agent loop.
          BLOCKING, and it runs with NO tools: a remote caller does not get to
          name the tools its agent may use, because that is choosing its own
          privileges. Over REST this is a completion with an iteration budget.
        * ``graph`` — a Studio workflow run on the platform's durable DAG
          runtime, with human gates, restart-safe resume and per-node tool
          authorization. Pass ``workflow_id`` (required for this mode) and
          optionally ``project_id`` / ``inputs``. NON-BLOCKING:
          ``data["run_id"]``. Never chosen by ``auto`` — a graph run names a
          workflow and that cannot be inferred.

        Tool-bearing work belongs in ``graph`` mode, against a workflow an
        operator wrote and whose nodes carry their own tool authorizations.

        READ ``launched`` FIRST. A provider that cannot serve native tool-use is
        a capability answer, not a fault: the call returns 200 with
        ``{"launched": False, "degraded": True, "reason": ...}`` rather than an
        error, precisely so it is not mistaken for Cortex being unreachable
        (which is ``None``, per this client's degradation contract).

        Defaults to ``ask_timeout``: team and graph launches return immediately,
        but a single-mode loop runs to completion.
        """
        payload: Dict[str, Any] = {"goal": goal, "mode": mode}
        if roles:
            payload["roles"] = roles
        if workflow_id:
            graph: Dict[str, Any] = {"workflow_id": workflow_id}
            if project_id:
                graph["project_id"] = project_id
            if inputs:
                graph["inputs"] = inputs
            payload["graph"] = graph
        if max_iterations is not None:
            payload["max_iterations"] = max_iterations
        if system_prompt:
            payload["system_prompt"] = system_prompt
        if llm_function:
            payload["llm_function"] = llm_function
        if trigger_ref:
            payload["trigger_ref"] = trigger_ref
        return self._post("agent", self._with_domain(payload, domain),
                          timeout or self.ask_timeout)

    # -- Slides (prem-msr-07; scope cortex:slides) -------------------------------

    def bom(self, documents: List[dict], *,
            timeout: Optional[int] = None) -> Optional[dict]:
        """Reconcile a pile of documents into one defensible bill of materials.

        Each document is ``{"filename", "content_base64", "role"?,
        "credibility_tier"?}``. Send the BYTES — there is deliberately no path
        parameter, because a remote endpoint that accepted one would be an
        arbitrary-file-read primitive wearing a convenience's clothes.

        ``role`` and ``credibility_tier`` are OPTIONAL and are the human's
        designation. Omit them and the engine proposes both, with a written
        rationale, and tells you it is proposing. Only what you pass is treated as
        binding — silence is never taken for confirmation.

        Runs with NO adjudicating model: the deterministic engine finds the
        double-counted licence, the subtotal that stopped tracking its own inputs,
        the line that looks costed and costs nothing, and the copy of a workbook
        that would otherwise have doubled every figure in it. It cannot
        hallucinate, because there is nothing in it that could.

        Success shape: ``{"is_a_total", "committed_total", "open_total", "lines",
        "findings", "sources", "pivots", "llm_calls"}``.

        Read ``is_a_total`` FIRST. When several of the documents each claim to
        price the same project, it is False and ``committed_total`` is a sum rather
        than a total — adding competing estimates of one project together is the
        arithmetic that produced the customer's problem in the first place, and the
        engine will not quietly do it on their behalf.

        Requires the ``cortex:bom`` scope, which is never in the default grant: the
        payload is the contents of somebody's bills of materials and quotes, which
        is the most commercially sensitive material they have.
        """
        return self._post("bom", {"documents": documents}, timeout=timeout)

    def slides_build(self, slides: List[dict], *, theme: str = "",
                     title: str = "", timeout: Optional[int] = None
                     ) -> Optional[dict]:
        """Render finished slide content as a themed ICDEV .pptx.

        Deterministic — you supply the content, ICDEV supplies the theme. No
        LLM runs. Success shape: {"pptx_base64", "filename", "content_type",
        "theme", "slide_count"}; decode ``pptx_base64`` to get the file bytes.

        Slide dicts are filtered server-side to content-only keys, so any
        ``image_path`` you set is dropped rather than honoured.
        """
        payload: Dict[str, Any] = {"slides": slides}
        if theme:
            payload["theme"] = theme
        if title:
            payload["title"] = title
        return self._post("slides", payload, timeout)

    # -- Win themes (prem-recomp-05; scope cortex:win_themes) --------------------

    def push_win_themes(self, opportunity_id: str, themes: List[dict], *,
                        timeout: Optional[int] = None) -> Optional[dict]:
        """Register CITED win themes against a proposal opportunity.

        Themes land in pg_win_themes, which the /proposals + /rfi drafting
        prompts read — so a theme pushed here actually shapes the draft.

        Every theme MUST carry evidence: ``{"statement", "evidence", ...}`` where
        evidence is rendered text or a list of {"claim", "source"} citations.
        Uncited themes are REFUSED server-side (returned in ``refused``), never
        stored, because an unproven claim in a generative prompt is exactly what
        the TRUST rules exist to prevent.
        """
        payload: Dict[str, Any] = {"opportunity_id": opportunity_id,
                                   "themes": themes}
        return self._post("win_themes", payload, timeout)

    # -- Staffing matrix (prem-pstaff-02; scope cortex:staffing_matrix) -----------

    def push_staffing_matrix(self, opportunity_id: str, people: List[dict], *,
                             timeout: Optional[int] = None) -> Optional[dict]:
        """Register EVIDENCED person -> LCAT mappings against a proposal opportunity.

        People land in ``proposal_key_personnel``, which is the bid side's first and
        only person->LCAT table. Before it, the Key Personnel volume was built by
        regex-scraping capitalised bigrams out of proposal prose — a pattern that
        matches "Program Manager" as readily as it matches a person.

        Each person is ``{"person_ref", "name", "proposed_lcat",
        "qualification_verdict", "evidence"}`` where the verdict is one of
        qualified / gap / exceeds (compass's tools/staffing/qualification.py) and
        evidence is rendered text or a list of ``{"claim", "source"}`` rows drawn
        from the resume.

        An UNEVIDENCED mapping is REFUSED server-side (returned in ``refused``), never
        stored: a person proposed for a labour category with nothing behind the claim
        reaches the customer as an assertion nobody can defend at debrief. Refusals do
        not fail the batch — 29 evidenced people still land if the 30th has a thin
        resume.
        """
        payload: Dict[str, Any] = {"opportunity_id": opportunity_id,
                                   "people": people}
        return self._post("staffing_matrix", payload, timeout)

    # -- Cost volume (prem-bid-02; scope cortex:cost_volume) ---------------------

    def price_cost_volume(self, opportunity_id: str, *, contract_type: str = "ffp",
                          allow_unrated: bool = False,
                          timeout: Optional[int] = None) -> Optional[dict]:
        """Price a bid from its LCAT allocations. Unrated LCATs are SURFACED, not guessed.

        Returns ``status: "unpriced"`` and an ``unrated[]`` list when any labour category
        has no rate — the volume is NOT priced, because a defaulted rate is a wrong price
        that looks exactly like a right one all the way through the wrap rates and the
        price-to-win band.

        ``allow_unrated=True`` prices the rated lines only and returns ``status:
        "partial"``. Partial is not ok: do not treat it as a complete price.
        """
        payload: Dict[str, Any] = {
            "opportunity_id": opportunity_id,
            "contract_type": contract_type,
        }
        if allow_unrated:
            payload["allow_unrated"] = True
        return self._post("cost_volume", payload, timeout)

    def push_priced_cost_volume(self, opportunity_id: str, priced: dict, *,
                                priced_by: str = "compass",
                                timeout: Optional[int] = None) -> Optional[dict]:
        """Record a cost volume priced ELSEWHERE (prem-bid-04).

        compass is the pricing authority: it merges the supplier rate cards and knows
        what an LCAT actually costs. ICDEV computing its own number would give two prices
        for one bid — worse than none, because somebody then has to decide which is real
        and they will decide it late.

        Accepting is not believing. The server reconciles the volume against its own line
        items and REFUSES one that declares itself partial or unpriced — a price with a
        hole in it must never reach the customer wearing the shape of a total.
        """
        payload: Dict[str, Any] = {
            "opportunity_id": opportunity_id,
            "priced": priced,
            "priced_by": priced_by,
        }
        return self._post("cost_volume", payload, timeout)

    def transition_won_opportunity(self, opportunity_id: str, *,
                                   created_by: str = "compass",
                                   timeout: Optional[int] = None) -> Optional[dict]:
        """A won bid becomes a PROPOSED delivery baseline in /cpmp (prem-bid-04).

        Returns the contract id, the total_value carried over from the priced volume, and
        `needs_attention` — what contracts staff must still supply. The contract lands as
        'draft': a won bid does not self-approve itself into an active contract.

        Scope `cortex:award`, separate from `cortex:cost_volume` on purpose: pricing a bid
        and declaring it won are different powers.
        """
        payload: Dict[str, Any] = {"opportunity_id": opportunity_id,
                                   "created_by": created_by}
        return self._post("award", payload, timeout)

    # -- Dashboard export (prem-rpt-02; scope cortex:dashboard) ------------------

    def export_dashboard(self, title: str, tiles: List[dict], *,
                         fmt: str = "html",
                         timeout: Optional[int] = None) -> Optional[dict]:
        """Render tiles into a customer-facing report: html, pptx or pdf.

        Each tile is ``{"spec": {...}}``. The server rebuilds every spec from a
        CONTENT-ONLY allowlist — path-bearing keys never reach a renderer, because on a
        remote surface those are an arbitrary-file-read primitive.

        The export carries the KEY's classification banner, not one you choose: an
        export leaves the platform by design, so the marking travels with it.
        """
        payload: Dict[str, Any] = {"title": title, "tiles": tiles, "format": fmt}
        return self._post("dashboard", payload, timeout)

    # -- RICOAS intake bridge (prem-ricoas-02; scope cortex:intake) --------------

    def intake_create(self, verbatim_ask: str, *, customer_name: str,
                      customer_org: str = "", goal: str = "build",
                      role: str = "developer", impact_level: str = "IL4",
                      origin: str = "", extra_context: Optional[dict] = None,
                      timeout: Optional[int] = None) -> Optional[dict]:
        """Create a real RICOAS intake session seeded with the verbatim ask.

        The verbatim_ask is persisted untouched (provenance) and processed as
        the first customer turn. Success shape: {"session_id", "welcome_message",
        "turn": {...}, "continue_url"} — continue_url is where a Task Lead
        resumes this SAME session in the full RICOAS UI.
        """
        payload: Dict[str, Any] = {
            "verbatim_ask": verbatim_ask,
            "customer_name": customer_name,
        }
        if customer_org:
            payload["customer_org"] = customer_org
        if goal:
            payload["goal"] = goal
        if role:
            payload["role"] = role
        if impact_level:
            payload["impact_level"] = impact_level
        if origin:
            payload["origin"] = origin
        if extra_context:
            payload["extra_context"] = extra_context
        return self._post("intake/session", payload, timeout or self.ask_timeout)

    def intake_turn(self, session_id: str, message: str, *,
                    timeout: Optional[int] = None) -> Optional[dict]:
        """Process one customer message turn. Success shape:
        {"session_id", "turn": {"analyst_response", "extracted_requirements",
        "readiness_update", "total_requirements", ...}, "continue_url"}."""
        payload = {"session_id": session_id, "message": message}
        return self._post("intake/turn", payload, timeout or self.ask_timeout)

    def intake_session(self, session_id: str, *,
                       timeout: Optional[int] = None) -> Optional[dict]:
        """Session status + conversation. Success shape:
        {"session": {...}, "messages": [...], "continue_url"}."""
        return self._request(
            "GET", f"{_CORTEX_PREFIX}/intake/session/{session_id}", None, timeout
        )

    # -- health / availability -------------------------------------------------

    def health(self, *, timeout: int = 10) -> Optional[dict]:
        return self._request("GET", f"{_CORTEX_PREFIX}/health", None, timeout)

    def is_available(self) -> bool:
        result = self.health()
        return bool(result and result.get("ok"))

    # -- DataBridge feeds (IRIS et al.) -----------------------------------------

    def feed_read(self, connector: str, table: str, *, limit: Optional[int] = None,
                  filters: Optional[dict] = None,
                  timeout: Optional[int] = None) -> Optional[dict]:
        """Read rows from an exposed DataBridge connector feed (e.g. iris)."""
        params = dict(filters or {})
        if limit is not None:
            params["limit"] = limit
        query = "&".join(f"{k}={v}" for k, v in params.items())
        path = f"{_DATABRIDGE_PREFIX}/{connector}/{table}" + (f"?{query}" if query else "")
        return self._request("GET", path, None, timeout)

    def feed_write(self, connector: str, table: str, payload: dict,
                   *, timeout: Optional[int] = None) -> Optional[dict]:
        """Write a payload to an exposed DataBridge connector feed."""
        return self._request("POST", f"{_DATABRIDGE_PREFIX}/{connector}/{table}",
                             payload, timeout)
