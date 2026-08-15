# CUI // SP-CTI
"""Delta Review — the side-by-side HITL panel for TRUST deltas (trust-hitl-02).

The repo had no generic delta review UI. ``redline_drafter`` produces one
suggestion at a time into the DIC accept/edit/reject surface, and
``review_board.html`` lists findings without ever showing what changed. Neither
answers the question a reviewer actually has to answer: *what did this draft say
before, what does it say now, and did the revision resolve the defect that
blocked it?*

Modules:
  ``constants``  vocabularies, span CSS classes, IQE collections, seed examples
  ``review``     read-side assembly — pending queue, one delta's panel payload
  ``blueprint``  Flask routes (pages + JSON API + IQE)

The evidence model lives in :mod:`tools.quality.hitl_delta`; nothing here
re-implements the diff, the store, or the settle path.
"""
