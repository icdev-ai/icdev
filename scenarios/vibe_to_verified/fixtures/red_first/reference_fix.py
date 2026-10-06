# CUI // SP-CTI
"""chunker.py -- the REFERENCE fix. Never shown to players.

The red-first scorer runs the team's test against this to prove the test is
not simply always-failing.
"""


def chunk(items, size, overlap=0):
    """Split ``items`` into windows of ``size`` that overlap by ``overlap``.

    Every element of ``items`` must appear in at least one window.
    """
    if size <= 0:
        raise ValueError("size must be positive")
    if overlap < 0 or overlap >= size:
        raise ValueError("overlap must be in [0, size)")
    step = size - overlap
    out = []
    for i in range(0, len(items), step):
        out.append(items[i:i + size])
        if i + size >= len(items):
            break
    return out
