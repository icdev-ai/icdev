# Failed Co-Worker Instances

## Query

```iqe
foreach i in ace.instances
  where i.state == "failed"
  select i.id, i.problem_summary, i.trust_tier, i.state, i.created_at
```

## Description

Surfaces every ACE team instance that terminated due to an unrecoverable
error (`state == "failed"` in `icdev/tools/ace/db/init_db.py`). Use this to
triage which co-worker teams broke down, review the problem each was tackling,
and correlate failures with trust tier — a starting point for diagnosing
recurring failure modes in the ANVIL Co-Worker Engine.
