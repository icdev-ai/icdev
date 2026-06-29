# Expert Persona Framing

> Pattern for opening LLM system prompts with a credible expert identity.
> Source: adapted from 50 Mega-Prompts (Hyper Automation Labs, 2026).
> Elevates output quality by anchoring the model's prior on "what a real expert does."

---

## Why It Works

A bare "You are a helpful assistant" yields general-purpose output. An expert persona with
**specific credentials, scale, and a distinctive philosophy** shifts the model toward:

- Domain-specific reasoning patterns (e.g., a security reviewer thinks in OWASP categories)
- Appropriate rigor thresholds (a principal engineer demands reproducible evidence)
- Better-calibrated uncertainty (an analyst who has "analyzed billions of rows" knows when
  sample size is too small)

The philosophy line ("You think in X, not Y") is the highest-leverage element — it sets the
frame for *how* the expert approaches problems, not just what they know.

---

## Template

```
You are a [specific title] with [years/scale of experience] and deep expertise in
[domain 1], [domain 2], and [domain 3]. You [distinctive philosophy or approach].

[Optional second sentence expanding on the approach or a memorable credential.]
```

### Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `specific_title` | Role + seniority that sets the bar | "principal software engineer" |
| `years/scale` | Quantified experience | "15+ years" / "at companies serving millions of users" |
| `domain 1-3` | The most relevant sub-domains | "security (OWASP Top 10), performance optimization, clean code architecture" |
| `philosophy` | How they approach problems | "You review code like you are protecting a production system serving millions of users" |

---

## Examples by Domain

### Code Review
```
You are a principal software engineer with 15+ years of experience and deep expertise
in security (OWASP Top 10), performance optimization, and clean code architecture.
You review code like you are protecting a production system serving millions of users.
```

### Debugging / RCA
```
You are a senior software engineer who specializes in debugging complex production
issues. You think systematically, isolate variables, and find root causes — not just
symptoms. You have debugged systems at companies like Google, Stripe, and Netflix.
```

### Compliance / Security Audit
```
You are a compliance expert with certifications in SOC 2, ISO 27001, HIPAA, PCI-DSS,
and GDPR. You help businesses identify compliance gaps before auditors do. You are
thorough, practical, and prioritize by risk rather than creating overwhelming checklists.
```

### Data Analysis
```
You are a senior data analyst who transforms raw data into executive-ready insights.
You do not just describe what the data shows — you explain what it MEANS and what to
DO about it. You think in "so what?" and "now what?" not just "what."
```

### Strategic Planning
```
You are a strategic foresight analyst who combines data analysis, pattern recognition,
and systems thinking to forecast trends. You think in probabilities, not certainties.
```

---

## ICDEV Application

Use this pattern in:
- ACE role SOUL.md files (the opening identity statement)
- `hardprompts/` templates invoked by LLM tools
- System prompts in `context/` directories
- `run_agent_loop()` `system_prompt` parameter

**Anti-patterns to avoid:**
- "You are a helpful AI assistant" — no domain anchoring
- "You are an expert" — no specificity
- "You are very knowledgeable about X" — passive framing, doesn't set philosophy
- Credentials that don't match the actual task — mismatch confuses more than helps

---

## Enforcement

When authoring a new ACE role or LLM tool system prompt, the first paragraph must
include: (1) a specific title, (2) quantified experience or scale, (3) a philosophy
statement. Flag in code review if missing.
