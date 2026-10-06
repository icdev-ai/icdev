# CUI // SP-CTI
"""chunker.py -- as merged from an AI-assisted PR (the BUGGY version).

Sliding-window chunker used by the RAG ingest path. Shown to players verbatim.
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
    return [items[i:i + size] for i in range(0, len(items) - size, step)]
