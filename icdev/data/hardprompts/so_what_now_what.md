# So What / Now What — Actionable Insight Framing

> Pattern for making every analysis output actionable.
> Source: adapted from "Data Interpreter" (#42), 50 Mega-Prompts, 2026.
> "You do not just describe what the data shows — you explain what it MEANS
>  and what to DO about it. You think in 'so what?' and 'now what?' not just 'what.'"

---

## The Core Problem

Analysis that answers only "what" is incomplete. A finding becomes useful only when
it answers:
- **So what?** — Why does this matter? What is at stake?
- **Now what?** — What action should someone take as a result?

An insight without these two questions is a data dump, not an analysis.

---

## Three-Layer Insight Structure

Every finding must be expressed at three levels:

```
WHAT:     [The finding, stated in one sentence with specific numbers]
SO WHAT:  [Why it matters — consequence, risk, opportunity, impact on goals]
NOW WHAT: [The recommended action — specific, ownable, time-bound]
```

### Example

```
WHAT:     Tool error rate for ai_developer sessions is 34% over the last 7 days.
SO WHAT:  At this rate, roughly 1 in 3 tool calls fails, meaning agents waste ~2
          additional turns on recovery per session — 40% of max_iterations consumed
          on errors rather than progress.
NOW WHAT: Audit the top 3 failing tools (grep tool_call_log for error_type), patch
          the most common failure mode, and add a regression test. Target: error rate
          < 15% within 2 sprints.
```

---

## Executive Summary Rule

Every analysis must end with an executive summary that a decision-maker can read in
30 seconds:

```
EXECUTIVE SUMMARY:
  - [Most impactful finding — lead with the finding that demands immediate action]
  - [Second finding]
  - [Third finding]
  SINGLE MOST IMPORTANT ACTION: [one specific thing to do today]
```

The last bullet is mandatory. If you can't name one action, the analysis is incomplete.

---

## Applying the Pattern to Common ICDEV Outputs

### Eval Report
```
WHAT:     Session ace-abc123 efficiency_score = 0.22 (bottom decile for ai_developer).
SO WHAT:  Low efficiency means the agent completed only 22% of its available iterations
          productively. The remaining 78% were recovery, duplicate calls, or off-scope work.
NOW WHAT: Run GET /api/ace/sessions/ace-abc123/eval/suggestions; apply the top-severity
          suggestion to the system prompt; rerun with POST /api/ace/sessions/ace-abc123/rerun.
```

### Coherence Check Result
```
WHAT:     pg_portability_linter found 3 HIGH-severity json_extract calls in runtime tool.
SO WHAT:  These calls work against SQLite but silently fail or return wrong results on
          PostgreSQL — the production backend. Any tenant on PG is affected today.
NOW WHAT: Rewrite each json_extract to json.loads() in Python (see PGP remediation pattern
          in hardprompts/); run coherence gate to confirm zero HIGH findings before merge.
```

### Trend Finding
```
WHAT:     Reasoning coverage has dropped from 0.71 to 0.43 over 30 days for the
          agent_developer role.
SO WHAT:  Agents are producing less structured CoT reasoning, which correlates (MEDIUM
          confidence) with lower task completion rates and more scope violations.
NOW WHAT: Review the last 5 system prompt changes to agent_developer/SOUL.md; identify
          any change that removed explicit CoT/CoD guidance; restore + re-eval.
```

---

## Prompt Template

```
[SYSTEM]
For every finding or recommendation in your analysis, apply the three-layer structure:
1. WHAT: State the finding in one sentence with specific numbers (not "significant," not
   "notable" — actual values).
2. SO WHAT: Explain why it matters. What is the consequence? Who is affected? What goal
   is threatened or advanced?
3. NOW WHAT: State a specific, ownable action. Include who should do it, what tool or
   command to run, and a success indicator.

End every analysis with an EXECUTIVE SUMMARY of 3-5 bullet points and a single
"most important action" that a decision-maker can authorize in 30 seconds.
```

---

## RULES

- Never use "significant," "notable," or "interesting" without a number.
- Every "now what" must name a tool, command, person, or process — not a vague intent.
- If you cannot identify a "now what," state "NO ACTION REQUIRED — monitoring only"
  or "ESCALATE TO HUMAN — decision requires context not available to the agent."
- An executive summary is not a recap of everything you found — it's the 3 things
  a decision-maker needs to act on today, ranked by urgency.
