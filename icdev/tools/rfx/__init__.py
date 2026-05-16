# CUI // SP-PROPIN
"""RFX AI Proposal Engine — service layer for GovProposal.

Modules:
    document_processor  — upload, parse (PDF/DOCX), chunk, store
    rag_service         — embed chunks, cosine similarity search (numpy/SQLite)
    requirement_extractor — extract shall/should/must from RFI/RFP docs
    exclusion_service   — sensitive term masking and merge-back
    research_service    — web/gov search with SQLite TTL cache
    llm_bridge          — ICDEV LLM router wrapper for proposal generation
    finetune_runner     — Unsloth/LoRA job launcher (subprocess)
"""
