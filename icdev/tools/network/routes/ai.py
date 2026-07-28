# CUI // SP-CTI
"""ICDEV Network Design Canvas -- ai route group.

Extracted verbatim from tools/network/blueprint.py (cvx-net-01 monolith split).
Registered on the shared NDC blueprint via register_ai_routes(bp).
"""
from __future__ import annotations

import json
import os
import uuid as _uuid
from datetime import datetime
from flask import current_app, jsonify, request
from tools.network.routes._common import _nc_save_message, logger
from tools.db.storage import sql_placeholder
from tools.network.blueprint_helpers import _audit, nc_login_required
from tools.network.db.init_db import get_connection


def _route_llm(function, system_prompt, messages, max_tokens, temperature=None):
    """Invoke the configured LLM through LLMRouter (lpx-router-01).

    Replaces the previous direct provider POSTs so that provider selection, an
    optional proxy ``base_url``, budgets and audit all flow through the router
    instead of reading a provider API key from the environment and hardcoding a
    Claude model.

    The retired per-site model override is gone: the model now
    comes from the routing chains (``network_topology`` / ``network_qa`` /
    ``network_chat_prep``) in ``args/llm_config.yaml``. When no proxy is
    configured the ``claude-sonnet`` chain entry resolves to the real Anthropic
    API with the same key, preserving prior behaviour.

    Returns ``(content, error)`` — ``error`` is a string when the router could
    not serve the request, mirroring the old helpers' contract.
    """
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
    except Exception as exc:  # pragma: no cover - import guard
        return None, "LLM router unavailable: {}".format(exc)
    kwargs = {
        "messages": messages,
        "system_prompt": system_prompt,
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    try:
        resp = LLMRouter().invoke(function, LLMRequest(**kwargs))
    except Exception as exc:
        return None, str(exc)
    return (resp.content or ""), None


def register_ai_routes(bp):
    """Register ai routes on the NDC blueprint."""

    _AI_TOPO_SYSTEM_PROMPT = """You are a network topology generator for a dark-themed canvas (navy #1a1a2e background). Output ONLY a valid JSON object — no markdown, no explanation, no code fences.

FORMAT:
{"nodes": [...], "edges": [...]}
Each node: {"id": "unique-id", "label": "Display Name", "type": "device-type", "x": number, "y": number, "config": {}}
Each edge: {"id": "unique-id", "source": "node-id", "target": "node-id", "label": "link label", "protocol": "protocol or empty"}

═══ DEVICE TYPES (use ONLY these) ═══
Physical:     router, switch-l2, switch-l3, firewall, load-balancer, wap, server, patch-panel
Cisco:        cisco-router, cisco-switch-l2, cisco-switch-l3, cisco-firewall, cisco-lb
Juniper:      juniper-ptx10003, juniper-mx304
Endpoints:    endpoint-pc, endpoint-phone, endpoint-iot, endpoint-camera
Cloud:        cloud, aws-vpc, aws-tgw, aws-subnet, az-vnet, az-fw, gcp-vpc
Logical:      vrf, vlan, subnet, security-zone
Encryption:   kg-175d, kg-175g, kg-250, kg-340, type1-encryptor, fips-140-l2, fips-140-l3, hsm, macsec
Monitoring:   siem, sdwan-edge, sase-pop
SP/Carrier:   mpls-pe, mpls-p, route-reflector, pop, sonet-adm, roadm, oadm, edfa, transponder
Media:        media-fiber, media-ge, media-10ge, media-100ge
Colo:         meet-me-room, cross-connect
Drawing:      draw-rect, draw-rounded-rect, text-heading, text-label, text-badge
DoD JWICS:    dod-jwics-backbone, dod-jwics-gateway, dod-jwics-dns, dod-jwics-mail-relay, dod-type1-encryptor, dod-scif-lan
DoD C2S:      dod-c2s-direct-connect, dod-c2s-tgw, dod-c2s-vpc, dod-c2s-dns-phz
DoD C2E:      dod-c2e-expressroute, dod-c2e-vnet, dod-c2e-dns-private
DoD Shared:   dod-secret-bcap, dod-cds

Use vendor-specific types when the user names a vendor product (e.g., Juniper PTX10003 → juniper-ptx10003, Juniper MX304 → juniper-mx304, Cisco ASR → cisco-router, Cisco Catalyst → cisco-switch-l3, Cisco ASA → cisco-firewall).

═══ MANDATORY STRUCTURE — follow this exact order in nodes array ═══

1. ZONE BOXES (draw-rect) — placed FIRST so they render behind devices
2. ZONE HEADINGS (text-heading) — placed ABOVE their zone box (zone_y - 25px)
3. BADGES (text-badge) — top of diagram for topology type name
4. DEVICES — inside their zone boxes, with realistic labels
5. ANNOTATION LABELS (text-label) — protocol/spec notes in clear space
6. LEGEND PANEL — ALWAYS include, on the RIGHT side of the last diagram

═══ ZONE COLOR PALETTE (dark fills, bright borders) ═══
Blue:    {"_fill": "#0a1628", "_stroke": "#3498db", "_width": W, "_height": H}
Green:   {"_fill": "#0a180a", "_stroke": "#27ae60", "_width": W, "_height": H}
Orange:  {"_fill": "#1a1500", "_stroke": "#f39c12", "_width": W, "_height": H}
Red:     {"_fill": "#1a0a0a", "_stroke": "#e74c3c", "_width": W, "_height": H}
Purple:  {"_fill": "#120a20", "_stroke": "#9b59b6", "_width": W, "_height": H}
Teal:    {"_fill": "#0a1a1a", "_stroke": "#00cec9", "_width": W, "_height": H}
Silver:  {"_fill": "#111318", "_stroke": "#95a5a6", "_width": W, "_height": H}
Legend:  {"_fill": "#0f1520", "_stroke": "#636e72", "_width": 240, "_height": H}

text-heading config: {"_textColor": "<matching zone stroke color>"}
text-badge config:   {"_fill": "#0f3460", "_stroke": "#4a9eff"}
text-label config:   {"_textColor": "#7a8cb0"} (or matching color)

═══ LAYOUT RULES ═══
- Start at x=40, y=60. Leave 25px above zones for headings.
- Space devices 150-200px apart horizontally, 130-160px vertically between tiers.
- Zone boxes: 700-900px wide, 500-800px tall, with 40px padding around devices inside.
- Zone headings: positioned at (zone_x + 20, zone_y - 25) with _textColor matching zone _stroke.
- NEVER overlap text on text. Keep 30px vertical gap between text nodes.
- Use different zone colors for different functional areas.

═══ MIGRATION SCENARIOS (read carefully when user describes upgrades, replacements, parallel runs, cutover, or incremental migration) ═══
When the user describes migrating or replacing devices:

1. Create MULTIPLE labeled phase zones arranged LEFT-TO-RIGHT (each 820px wide, 120px gap between):
   • Phase 0 "AS-IS — Current State" at x=40 (Silver zone, _stroke #95a5a6)
   • Phase 1 "Phase 1 — [name]" at x=980 (Orange zone, _stroke #f39c12)
   • Phase 2 "Phase 2 — [name]" at x=1920 (Teal zone, _stroke #00cec9)
   • Phase N "TO-BE — Target State" at x=(N*940)+40 (Green zone, _stroke #27ae60)

2. AS-IS zone: show EXISTING devices and ALL connections exactly as the user described. Use port/interface labels on edges (e.g., "xe-0/0/0 ↔ Gi0/0/1"). Include VLANs and VRFs as logical nodes inside or below the device.

3. Each migration phase zone: show WHAT CHANGES — new device in parallel, which VLANs/VRFs move this phase. Devices being decommissioned should stay with "(retiring)" in label. New devices appear with "(new)" in label. Add text-label annotations for port mappings and BGP session status.

4. TO-BE zone: show final target state after full cutover — only target devices remain, no legacy equipment.

5. Add edges BETWEEN phases for the migration path: dashed edges showing BGP peer hand-off, uplink preservation, VLAN migration order.

6. For VLAN/VRF migration: create individual vlan/vrf nodes showing which phase they migrate. Label them "VLAN 10 (Phase 1)", "VRF MGMT (Phase 2)", etc.

7. Port mapping: use text-label nodes within each zone to show the interface mapping table.
   Example: "xe-0/0/0 (PTX) → et-0/0/1 (MX304)" as a text-label inside the phase zone.

8. BGP continuity: when uplinks must stay on old router until last phase, show this explicitly with a note text-label "BGP AS 1001 uplink retained on legacy until Phase N".

═══ LEGEND (MANDATORY — always include) ═══
Place a legend panel to the RIGHT of the rightmost diagram (rightmost_x + 120).
Structure:
- draw-rect background: {"_fill": "#0f1520", "_stroke": "#636e72", "_width": 240, "_height": <calculated>}
- text-heading "Legend" at top
- PROTOCOLS section: list only protocols used:
  OSPF=#27ae60, iBGP=#85c1e9, eBGP=#3498db, MPLS=#ff9800, IPSec=#f7dc6f, BGP=#5dade2
- DEVICES section: list device types used
- PHASES section (for migration): Silver=AS-IS, Orange=Phase 1, Teal=Phase 2, Green=TO-BE
Each legend entry: text-label with "• <description>" and appropriate _textColor.
Spacing: 22px between entries, 30px between sections.

═══ DoD SECRET / CLASSIFIED NETWORK TOPOLOGIES ═══
Use dod-* types when user mentions: JWICS, SCIF, C2S, C2E, SIPR, classified network, SECRET network, DISA, BCAP, SCCA, Type 1, CDS, cross-domain, IL6, DIA, or NSA encryption.

STANDARD JWICS AGENCY CONNECTION (left → right):
  dod-scif-lan → dod-type1-encryptor → dod-jwics-gateway → dod-jwics-backbone → [DIA hub: router] → dod-jwics-dns, dod-jwics-mail-relay, server (app)

JWICS → C2S (AWS Secret Region):
  dod-scif-lan → dod-type1-encryptor → dod-jwics-gateway → dod-jwics-backbone → dod-secret-bcap → dod-c2s-direct-connect → dod-c2s-tgw → dod-c2s-vpc → dod-c2s-dns-phz

JWICS → C2E (Azure Government Secret):
  dod-scif-lan → dod-type1-encryptor → dod-jwics-gateway → dod-jwics-backbone → dod-secret-bcap → dod-c2e-expressroute → dod-c2e-vnet → dod-c2e-dns-private

FULL DISA PANORAMA (3-row layout — stack vertically, 280px row spacing):
  TOP ROW (NIPR, y=80):   endpoint-pc → router → firewall → [dod-secret-bcap optional NIPR side] → aws-vpc / az-vnet
  MID ROW (DISN, y=360):  router (DISN backbone) → siem → server (ACAS/HBSS)
  BOT ROW (SECRET, y=640): dod-scif-lan → dod-type1-encryptor → dod-jwics-backbone → dod-secret-bcap → dod-c2s-vpc / dod-c2e-vnet
  CDS bridging MID ↔ BOT: place dod-cds node between MID row and BOT row (y=500)

CROSS-DOMAIN SOLUTION: place dod-cds between NIPR (unclassified) and JWICS (SECRET) segments.
DNS FLOW diagram: dod-scif-lan → endpoint-pc (SCIF user) → server (stub resolver) → dod-jwics-dns (JWICS recursive) → server (DIA authoritative)
EMAIL FLOW diagram: endpoint-pc (SCIF sender) → server (agency SMTP relay) → dod-jwics-mail-relay → server (DIA relay) → endpoint-pc (recipient)

ZONE COLORS for classified:
  SECRET zone: Red   {"_fill": "#1a0808", "_stroke": "#e74c3c"}
  JWICS zone:  Red   {"_fill": "#2b0808", "_stroke": "#ff4757"}
  C2S zone:    Amber {"_fill": "#1a0f00", "_stroke": "#e67e22"}
  C2E zone:    Purple{"_fill": "#0f0820", "_stroke": "#8e44ad"}
  CDS bridge:  Red   {"_fill": "#1a0a1a", "_stroke": "#ff7675"}
  NIPR zone:   Blue  (standard)

EDGE LABELS for classified: "Type 1 AES-256 HAIPE", "OSPF Area 0", "ClassifiedConnect 10G", "BGP eBGP MD5", "UDP/53 DNSSEC", "SMTP/S 587", "LDAPS/636"

═══ PROTOCOLS (use realistic ones) ═══
OSPF, BGP, iBGP, eBGP, MP-BGP, MPLS, LDP, RSVP, IPSec, STP, VXLAN, BGP EVPN, GRE, Type 1 AES-256, DNSSEC, S/MIME, HAIPE

Output ONLY the JSON object. No other text."""

    # Topology generation keywords — message contains one of these → likely a diagram request
    _TOPOLOGY_KEYWORDS = {
        "design", "create", "build", "draw", "generate", "diagram", "topology",
        "network", "configure", "connect", "setup", "set up", "show me", "map",
        "add router", "add switch", "add firewall", "add server", "add node",
        "wan", "lan", "dmz", "vlan", "vrf", "mpls", "bgp", "ospf", "ipsec",
        "data center", "datacenter", "cloud", "hub", "spoke", "mesh",
        "three tier", "three-tier", "two tier", "two-tier", "spine", "leaf",
        "core", "distribution", "access layer",
        # DoD / classified network keywords
        "jwics", "scif", "sipr", "niprnet", "c2s", "c2e",
        "classified", "secret network", "il6", "il5", "il4",
        "disa", "bcap", "scca", "vdss", "vdms", "tccm",
        "type 1", "type-1", "taclane", "kg-250", "kg-175",
        "cds", "cross-domain", "cross domain",
        "classifiedconnect", "classified connect",
        "dia hub", "dia network", "jwics backbone",
        "secret region", "aws secret", "azure secret",
        "agency connect", "dod agency", "dod network",
    }

    # Migration scenario keywords — trigger multi-phase layout + migration canvas session
    _MIGRATION_KEYWORDS = {
        "migrat", "replac", "cutover", "cut-over", "parallel run", "incremental",
        "as-is", "as is", "to-be", "to be", "phase ", "phased", "hand-off", "handoff",
        "decommission", "decomm", "retire", "swap", "upgrade router", "upgrade switch",
    }


    @bp.route("/api/ai-generate", methods=["POST"])
    @nc_login_required
    def nc_api_ai_generate():
        """Generate topology from natural language description using Claude or Ollama."""
        data = request.get_json(force=True, silent=True) or {}
        description = data.get("description", "").strip()
        if not description:
            return jsonify({"error": "description required"}), 400

        import re

        import requests as _req
        from tools.http.client import request as _req_request

        def _repair_json(s):
            """Best-effort repair for common LLM JSON mistakes."""
            # Missing comma between adjacent objects in an array: }...{
            s = re.sub(r"\}\s*\n(\s*)\{", r"},\n\1{", s)
            # Trailing comma before closing bracket/brace
            s = re.sub(r",(\s*[\]\}])", r"\1", s)
            # Truncated JSON: find last complete top-level object and close it
            if s.count("{") > s.count("}"):
                # Strip back to last complete object in nodes/edges
                last_close = s.rfind("}}")
                if last_close > 0:
                    s = s[: last_close + 2]
                    depth_b2 = s.count("[") - s.count("]")
                    depth_c2 = s.count("{") - s.count("}")
                    s += "]" * max(0, depth_b2) + "}" * max(0, depth_c2)
            return s

        def _parse_llm_response(content):
            """Extract and validate JSON from LLM response text."""
            text = content.strip()
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
            if text.startswith("```"):
                lines = text.split("\n")
                lines = [ln for ln in lines if not ln.strip().startswith("```")]
                text = "\n".join(lines).strip()
            start = text.find("{")
            end = text.rfind("}") + 1
            if start < 0 or end <= start:
                return None, text[:500]
            json_str = text[start:end]
            # Try clean parse first, fall back to repair
            try:
                graph_json = json.loads(json_str)
            except json.JSONDecodeError:
                json_str = _repair_json(json_str)
                graph_json = json.loads(json_str)
            if "nodes" not in graph_json or "edges" not in graph_json:
                return None, text[:500]
            for n in graph_json["nodes"]:
                n.setdefault("id", str(_uuid.uuid4())[:8])
                n.setdefault("label", "")
                n.setdefault("type", "server")
                n.setdefault("x", 100)
                n.setdefault("y", 100)
                n.setdefault("config", {})
            for e in graph_json["edges"]:
                e.setdefault("id", str(_uuid.uuid4())[:8])
                e.setdefault("source", "")
                e.setdefault("target", "")
                e.setdefault("label", "")
                e.setdefault("protocol", "")
            return graph_json, None

        # Detect migration scenario once — shared across helpers below
        desc_lower = description.lower()
        is_migration = any(kw in desc_lower for kw in _MIGRATION_KEYWORDS)
        architect_mode = data.get("architect_mode", False)

        # Prefix for architect / best-practices mode
        if architect_mode:
            description = (
                "Act as a senior network architect and engineer. "
                "Apply DISA STIG, NIST 800-53, and industry best practices for anything the user did not specify. "
                "Make sensible, production-grade design decisions. "
                "Original request: " + description
            )

        def _call_claude(desc, max_tokens=4096):
            """Generate a topology via the configured LLM (router-mediated).

            Kept its historical name to minimise the diff; it is the
            cloud/router branch (as opposed to the explicit ``_call_ollama``
            air-gap branch selected by ``NC_AI_PROVIDER``).
            """
            return _route_llm(
                "network_topology",
                _AI_TOPO_SYSTEM_PROMPT,
                [{"role": "user", "content": desc}],
                max_tokens,
                temperature=0.3,
            )

        def _call_ollama(desc, max_tokens=4096):
            """Call Ollama local LLM."""
            ollama_url = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
            ollama_model = os.environ.get("OLLAMA_TOPO_MODEL", "llama3.2:3b")
            r = _req_request(
                "POST",
                f"{ollama_url}/api/chat",
                json={
                    "model": ollama_model,
                    "messages": [
                        {"role": "system", "content": _AI_TOPO_SYSTEM_PROMPT},
                        {"role": "user", "content": desc},
                    ],
                    "stream": False,
                    "options": {"num_predict": max_tokens, "temperature": 0.3},
                },
                timeout=120,
            )
            r.raise_for_status()
            content = r.json().get("message", {}).get("content", "")
            return content, None

        # Migration diagrams need more tokens (multi-phase = many nodes)
        # 8192 baseline; migration scenarios with many phases get 16k
        token_budget = 16384 if is_migration else 8192

        try:
            # Try Claude first (fast, reliable), fall back to Ollama (air-gap)
            provider = os.environ.get("NC_AI_PROVIDER", "auto")  # auto | claude | ollama
            content = None
            used_provider = ""

            if provider in ("auto", "claude"):
                content, err = _call_claude(description, max_tokens=token_budget)
                if content:
                    used_provider = "claude"
                elif provider == "claude":
                    return jsonify({"error": f"Claude API failed: {err}"}), 503

            if not content and provider in ("auto", "ollama"):
                content, err = _call_ollama(description, max_tokens=token_budget)
                if content:
                    used_provider = "ollama"
                elif provider == "ollama":
                    return jsonify({"error": f"Ollama failed: {err}"}), 503

            if not content:
                return jsonify({"error": "No LLM provider available. Configure a provider in args/llm_config.yaml or start Ollama."}), 503

            graph_json, raw = _parse_llm_response(content)
            if graph_json is None:
                return jsonify({"error": "LLM did not return valid JSON", "raw": raw}), 422

            # Apply deterministic style rules (zone ordering, label deconfliction, legend)
            try:
                from tools.network.topology_styler import style_topology

                graph_json = style_topology(graph_json)
            except Exception as style_err:
                logger.warning("Topology styler failed (non-fatal): %s", style_err)

            # For migration scenarios: create a migration canvas session automatically
            migration_session_id = None
            migration_session_url = None
            if is_migration:
                try:
                    # Extract src/tgt device names from node labels
                    node_labels = [n.get("label", "") for n in graph_json["nodes"]]
                    src_candidates = [l for l in node_labels if any(
                        v in l.lower() for v in ("ptx", "mx", "juniper", "legacy", "current", "asis", "as-is")
                    )]
                    tgt_candidates = [l for l in node_labels if any(
                        v in l.lower() for v in ("cisco", "new", "tobe", "to-be", "target", "replace")
                    )]
                    src_model = src_candidates[0] if src_candidates else "Source Device"
                    tgt_model = tgt_candidates[0] if tgt_candidates else "Target Device"
                    migration_session_id = "nmig-" + _uuid.uuid4().hex[:12]
                    with get_connection() as _mc:
                        _ph = sql_placeholder(_mc)
                        _mc.execute(
                            f"INSERT INTO mc_net_sessions "
                            f"(id, src_model, tgt_model, src_device_name, tgt_device_name, created_at, updated_at) "
                            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
                            (migration_session_id, src_model, tgt_model,
                             src_model, tgt_model,
                             datetime.utcnow().isoformat(), datetime.utcnow().isoformat()),
                        )
                        _mc.commit()
                    migration_session_url = f"/migration-canvas/?session={migration_session_id}"
                except Exception as _mig_err:
                    logger.warning("Migration session create failed (non-fatal): %s", _mig_err)

            _audit("AI_GENERATE", "topology", "", f"[{used_provider}] Generated from: {description[:100]}")

            # Persist to AI history (non-fatal)
            try:
                _hist_id = "aih-" + _uuid.uuid4().hex[:12]
                _short = (description[:120] + "…") if len(description) > 120 else description
                _hist_gj = json.dumps(graph_json) if graph_json else None
                with get_connection() as _hc:
                    _ph = sql_placeholder(_hc)
                    _hc.execute(
                        f"INSERT INTO nc_ai_history "
                        f"(id, description, short_desc, node_count, edge_count, provider, is_migration, graph_json, created_at) "
                        f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
                        (_hist_id, description, _short,
                         len(graph_json["nodes"]), len(graph_json["edges"]),
                         used_provider, int(is_migration), _hist_gj,
                         datetime.utcnow().isoformat()),
                    )
                    _hc.commit()
            except Exception as _he:
                logger.warning("AI history save failed (non-fatal): %s", _he)

            return jsonify(
                {
                    "graph_json": graph_json,
                    "description": description,
                    "node_count": len(graph_json["nodes"]),
                    "edge_count": len(graph_json["edges"]),
                    "provider": used_provider,
                    "is_migration": is_migration,
                    "migration_session_id": migration_session_id,
                    "migration_session_url": migration_session_url,
                    "history_id": _hist_id,
                }
            )

        except _req.exceptions.ConnectionError:
            return jsonify({"error": "Cannot connect to LLM provider"}), 503
        except _req.exceptions.Timeout:
            return jsonify({"error": "LLM timed out — try a simpler description"}), 504
        except json.JSONDecodeError as exc:
            return jsonify({"error": f"Invalid JSON from LLM: {exc}"}), 422
        except Exception as exc:
            logger.exception("AI generate failed")
            return jsonify({"error": str(exc)}), 500

    # ══════════════════════════════════════════════════════════════════════
    # API: AI Context Creation — allocate a new conversation context id
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/ai-context", methods=["POST"])
    @nc_login_required
    def nc_api_ai_context_create():
        """Create a new AI chat context and return its id (nc-<uuid8>)."""
        ctx_id = "nc-" + str(_uuid.uuid4())[:8]
        return jsonify({"context_id": ctx_id}), 201

    # ══════════════════════════════════════════════════════════════════════
    # API: Unified AI Chat — topology generation + Q&A in one endpoint
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/ai-chat", methods=["POST"])
    @nc_login_required
    def nc_api_ai_chat():
        """Unified AI chat: route to topology generation or direct Q&A."""
        data = request.get_json(force=True, silent=True) or {}
        description = (data.get("description") or data.get("message") or "").strip()
        context_id = (data.get("context_id") or "").strip()  # noqa: F841
        architect_mode = data.get("architect_mode", False)
        mode = data.get("mode", "qa")

        if not description:
            return jsonify({"error": "message is required"}), 400

        phase_context = data.get("phase_context") or {}
        phase_header = ""
        if phase_context:
            ph_num = phase_context.get("phase_num", "?")
            ph_title = phase_context.get("title", "")
            ph_cls = phase_context.get("classification", "CUI")
            ph_il = phase_context.get("impact_level", "IL4")
            ph_status = phase_context.get("status", "planned")
            phase_header = (
                f"\n\n## ACTIVE MIGRATION PHASE CONTEXT\n"
                f"Phase {ph_num}: {ph_title}\n"
                f"Classification: {ph_cls} | Impact Level: {ph_il} | Status: {ph_status}\n"
                f"All responses must respect {ph_cls}/{ph_il} constraints, applicable STIG/RMF controls, "
                f"and DoD network migration best practices for this classification level.\n"
            )

        qa_system = (  # noqa: F841
            _AI_TOPO_SYSTEM_PROMPT
            + phase_header
            + "\n\nYou are also a network expert who can answer questions directly"
            " without generating JSON. When the user asks a question (rather than"
            " requesting a diagram), respond in plain English with a clear, concise"
            " explanation. Only output JSON when explicitly building a topology."
        )

        # Topology detection: keyword match with short-message Q&A override
        desc_lower = description.lower()
        word_count = len(description.split())
        keyword_hit = any(kw in desc_lower for kw in _TOPOLOGY_KEYWORDS) or any(
            kw in desc_lower for kw in _MIGRATION_KEYWORDS
        )
        # Explicit mode wins; auto-detect only when mode is unspecified (default "qa")
        if mode == "topology":
            is_topology = True
        elif mode == "qa":
            is_topology = False
        else:
            # ≤3 words → too short to be a topology request; treat as Q&A
            is_topology = keyword_hit and word_count > 3

        if is_topology:
            cookie_header = request.headers.get("Cookie", "")
            forward_data = {"description": description, "architect_mode": architect_mode}
            if context_id:
                forward_data["context_id"] = context_id
            with current_app.test_client() as tc:
                resp = tc.post(
                    "/network/api/ai-generate",
                    data=json.dumps(forward_data),
                    content_type="application/json",
                    headers={"Cookie": cookie_header} if cookie_header else {},
                )
            resp_data = json.loads(resp.data)
            resp_data["mode"] = "topology"
            return jsonify(resp_data), resp.status_code

        # Q&A mode — answer via the configured LLM (router-mediated) with history
        try:
            history_messages = []
            if context_id:
                try:
                    conn = get_connection()
                    _ph = sql_placeholder(conn)
                    rows = conn.execute(
                        f"SELECT role, content FROM chat_messages WHERE context_id={_ph} "
                        "ORDER BY turn_number DESC LIMIT 10",
                        (context_id,),
                    ).fetchall()
                    history_messages = [{"role": r[0], "content": r[1]} for r in reversed(rows)]
                except Exception as exc:
                    logger.warning("qa history load failed: %s", exc)

            messages = history_messages + [{"role": "user", "content": description}]
            answer, err = _route_llm("network_qa", qa_system, messages, 1024)
            if err:
                return jsonify({"error": f"LLM unavailable: {err}"}), 503
        except Exception as exc:
            logger.exception("Q&A chat failed")
            return jsonify({"error": str(exc)}), 500

        if context_id:
            _nc_save_message(context_id, "user", description)
            _nc_save_message(context_id, "assistant", answer)

        return jsonify({"ok": True, "mode": "qa", "answer": answer}), 200

    # ══════════════════════════════════════════════════════════════════════
    # API: AI Chat Pre-flight — Grilling / Clarifying Questions
    # ══════════════════════════════════════════════════════════════════════

    _CHAT_PREP_SYSTEM = """You are a senior network architect and engineer.
A user has described a network topology they want designed.
Your job: decide if you need more information to create an OPTIMAL design.

Rules:
- If the request already has enough detail (device types, counts, topology style, scale) → reply with {"needs_more_info": false}
- If key information is missing, ask 2-3 targeted questions. No more than 3. Be specific.
- Always suggest what a reasonable default assumption would be if the user doesn't know.

Respond with ONLY this JSON (no other text):
{
  "needs_more_info": true|false,
  "questions": ["question 1", "question 2"],
  "assumption_summary": "If user says that's all I have, I'll assume: ..."
}"""

    @bp.route("/api/ai-chat-prep", methods=["POST"])
    @nc_login_required
    def nc_api_ai_chat_prep():
        """Assess description completeness and return clarifying questions."""
        import re as _re2
        data = request.get_json(force=True, silent=True) or {}
        description = data.get("description", "").strip()
        if not description:
            return jsonify({"needs_more_info": False}), 200

        try:
            text, err = _route_llm(
                "network_chat_prep",
                _CHAT_PREP_SYSTEM,
                [{"role": "user", "content": description}],
                512,
                temperature=0.1,
            )
            if err:
                # Non-fatal: proceed without clarifying questions.
                return jsonify({"needs_more_info": False}), 200
            # Parse the JSON response
            text = _re2.sub(r"<think>.*?</think>", "", text, flags=_re2.DOTALL).strip()
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                result = json.loads(text[start:end])
                return jsonify(result), 200
        except Exception as _prep_err:
            logger.warning("AI chat prep failed (non-fatal): %s", _prep_err)

        return jsonify({"needs_more_info": False}), 200

    # ══════════════════════════════════════════════════════════════════════
    # API: AI Chat History
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/ai-history", methods=["GET"])
    @nc_login_required
    def nc_api_ai_history():
        """Return the last N AI generation history entries."""
        limit = min(int(request.args.get("limit", 30)), 100)
        with get_connection() as conn:
            _ph = sql_placeholder(conn)
            rows = conn.execute(
                "SELECT id, short_desc, node_count, edge_count, provider, is_migration, created_at "
                f"FROM nc_ai_history ORDER BY created_at DESC LIMIT {_ph}",
                (limit,),
            ).fetchall()
        entries = [
            {
                "id": r[0], "short_desc": r[1], "node_count": r[2],
                "edge_count": r[3], "provider": r[4],
                "is_migration": bool(r[5]), "created_at": r[6],
            }
            for r in rows
        ]
        return jsonify({"entries": entries})

    @bp.route("/api/ai-history/<hist_id>", methods=["GET"])
    @nc_login_required
    def nc_api_ai_history_get(hist_id):
        """Return full description + graph_json for a history entry."""
        with get_connection() as conn:
            _ph = sql_placeholder(conn)
            row = conn.execute(
                "SELECT id, description, node_count, edge_count, provider, is_migration, graph_json, created_at "
                f"FROM nc_ai_history WHERE id={_ph}",
                (hist_id,),
            ).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        return jsonify({
            "id": row[0], "description": row[1], "node_count": row[2],
            "edge_count": row[3], "provider": row[4],
            "is_migration": bool(row[5]),
            "graph_json": json.loads(row[6]) if row[6] else None,
            "created_at": row[7],
        })

    @bp.route("/api/ai-history/<hist_id>", methods=["DELETE"])
    @nc_login_required
    def nc_api_ai_history_delete(hist_id):
        """Delete a single history entry."""
        with get_connection() as conn:
            _ph = sql_placeholder(conn)
            conn.execute(f"DELETE FROM nc_ai_history WHERE id={_ph}", (hist_id,))
            conn.commit()
        return jsonify({"ok": True})

    # ══════════════════════════════════════════════════════════════════════
    # API: ATO Package Auto-Generator
    # ══════════════════════════════════════════════════════════════════════
