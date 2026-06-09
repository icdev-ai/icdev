# Active Co-Worker Teams

## Query

```iqe
foreach i in ace.instances
  where i.state == "active"
  select i.id, i.problem_summary, i.trust_tier, i.state, i.created_at
```

## Description

Lists every ACE (ANVIL Co-Worker Engine) team instance that is currently
running. In the canonical ACE schema (`icdev/tools/ace/db/init_db.py`) a live
instance carries `state == "active"` (the running state; valid instance states
are `assembling`, `pending`, `active`, `paused`, `complete`, `failed`,
`cancelled`). Use this to see which co-worker teams are actively assembled and
executing work, along with each team's trust tier and the problem it was
spun up to solve.
