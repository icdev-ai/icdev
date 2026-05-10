# Agent Zero — Third-Party License Record

**Project:** Agent Zero AI framework
**Upstream:** https://github.com/agent0ai/agent-zero
**License:** MIT (Copyright (c) 2025 Agent Zero, s.r.o)
**ICDEV relationship:** Concepts referenced, **no code derived.**

## Why this directory exists even though no code was copied

ICDEV's NOTICE file lists Agent Zero in the "ARCHITECTURAL INSPIRATIONS"
section. During a license audit on 2026-04-11, the following facts were
established:

1. The NOTICE file previously claimed Agent Zero was GPL-3.0. This was
   factually incorrect. Both `frdel/agent-zero` (the URL originally
   cited) and the current `agent0ai/agent-zero` (which it redirects to)
   resolve to **MIT License**. This record corrects that.

2. Four ICDEV files had docstrings saying "adapted from Agent Zero's X
   pattern":
   - `tools/dashboard/chat_manager.py`
   - `tools/dashboard/state_tracker.py`
   - `tools/extensions/extension_manager.py`
   - `tools/extensions/__init__.py`

3. A structural comparison against Agent Zero's `helpers/defer.py`,
   `helpers/state_monitor.py`, and `helpers/extension.py` found **zero
   class-level, method-level, or architectural overlap**. ICDEV's
   implementations are independent — they use different concurrency
   primitives, different transport models, and different orchestration
   patterns. The docstrings were over-crediting the source.

4. The audit report is saved at `.tmp/agent_zero_audit_report.md` and
   is summarized in kanban task OPT-73.

## Why preserve the MIT text anyway

Belt-and-suspenders. If a future audit, acquirer, or open-source
review looks at ICDEV and notices the "Agent Zero" references in NOTICE
and in the 4 source files, this directory gives them a quick answer:

- The upstream license is preserved verbatim here (MIT requires that
  "substantial portions" retain the copyright notice; even though no
  substantial portion was copied, preserving it is defensive hygiene)
- The relationship is documented explicitly as "concepts only, no code"
- The structural-audit provenance is traceable via OPT-73 + NOTICE line
  item + this README

ICDEV itself is Apache-2.0. MIT and Apache-2.0 are fully compatible;
MIT-licensed code can be included in Apache-2.0 projects provided the
MIT notice is preserved. This directory satisfies that requirement
defensively even though the audit concluded no code was copied.
