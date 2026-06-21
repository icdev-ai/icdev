---
name: addyosmani-ship
description: "Ship checklist: feature flags, rollback plan, staged rollout, and post-ship monitoring."
source: addyosmani/agent-skills
allowed-tools:
- Read
- Write
- Edit
- Bash
- Grep
- Glob
tags:
- addyosmani
- engineering-discipline
---

Invoke the addyosmani shipping-and-launch skill.

## What This Does
Runs the pre-ship and post-ship checklist: feature flags, rollback plan, staged rollout, RED metrics.

## Pre-Ship Checklist
- [ ] Feature gated behind flag (ICDEV_<FEATURE>_ENABLED)
- [ ] Rollback plan documented
- [ ] Tests passing (unit + integration + E2E)
- [ ] SIPA scan clean
- [ ] Ruff clean
- [ ] Companion sync complete
- [ ] ADR written if architecture changed

## Ship Steps
1. Deploy to staging (1% traffic)
2. Monitor RED metrics for 10 min
3. Ramp to 10%, 50%, 100%
4. Remove feature flag when stable at 100%

## Arguments
$ARGUMENTS — feature name or PR description

## Commands
```bash
python tools/testing/health_check.py --json
python tools/workflow/coherence_checker.py --all --gate
python tools/dx/companion.py --sync --write --json
```

## Source Skill
.agents/skills/addyosmani-shipping-and-launch/SKILL.md
