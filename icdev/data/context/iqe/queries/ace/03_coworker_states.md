# In-Flight Co-Worker States

## Query

```iqe
foreach c in ace.coworkers
  where c.state != "done"
  select c.id, c.role, c.state, c.trust_tier, c.instance_id
```

## Description

Lists every ACE co-worker that has not yet finished its work — i.e. any
co-worker whose `state` is not `"done"` (valid co-worker states in
`icdev/tools/ace/db/init_db.py` are `idle`, `active`, `busy`, `offline`,
`suspended`, `working`, `hitl_pending`, `done`, `failed`). This includes
co-workers actively `working`, those blocked in `hitl_pending` awaiting human
approval, and any that `failed`. Use it to see the live roster of agents
across all teams and spot ones stuck waiting on HITL or stalled.
