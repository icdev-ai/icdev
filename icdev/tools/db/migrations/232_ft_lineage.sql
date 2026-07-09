-- Fine-tuning dataset lineage: source documents → training pairs
-- Migration 230: ft_source_documents and ft_pair_lineage tables

CREATE TABLE IF NOT EXISTS ft_source_documents (
    id BIGSERIAL PRIMARY KEY,
    doc_id TEXT NOT NULL,
    title TEXT,
    source_type TEXT,
    content_hash TEXT NOT NULL,
    tenant_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(doc_id)
);

CREATE TABLE IF NOT EXISTS ft_pair_lineage (
    id BIGSERIAL PRIMARY KEY,
    example_id BIGINT NOT NULL,
    doc_id TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    generation_method TEXT DEFAULT 'llm_generated',
    generator_model_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ft_pair_lineage_doc ON ft_pair_lineage(doc_id);
CREATE INDEX IF NOT EXISTS idx_ft_pair_lineage_example ON ft_pair_lineage(example_id);
CREATE INDEX IF NOT EXISTS idx_ft_pair_lineage_dataset ON ft_pair_lineage(dataset_id);
