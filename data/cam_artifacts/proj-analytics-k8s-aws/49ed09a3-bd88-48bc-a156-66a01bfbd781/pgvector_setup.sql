-- CUI // SP-CTI
-- pgvector setup: semantic search layer in Aurora PostgreSQL
-- Run AFTER DMS migration is complete and Aurora schema is stable.

CREATE EXTENSION IF NOT EXISTS vector;

-- Embedding column on an existing table (adjust table/column names)
ALTER TABLE example_entity
    ADD COLUMN IF NOT EXISTS embedding vector(1536);

-- HNSW index for fast approximate nearest-neighbour search
-- m=16, ef_construction=64 are good defaults for 1536-dim Bedrock Titan embeddings
CREATE INDEX IF NOT EXISTS idx_example_entity_embedding_hnsw
    ON example_entity
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- IVFFlat alternative (faster build, lower recall):
-- CREATE INDEX idx_example_entity_embedding_ivfflat
--     ON example_entity USING ivfflat (embedding vector_cosine_ops)
--     WITH (lists = 100);

-- Semantic search function
CREATE OR REPLACE FUNCTION semantic_search(
    query_embedding vector(1536),
    similarity_threshold FLOAT DEFAULT 0.7,
    max_results INT DEFAULT 10
)
RETURNS TABLE(id UUID, name VARCHAR, similarity FLOAT) AS $$
    SELECT id, name,
           1 - (embedding <=> query_embedding) AS similarity
    FROM   example_entity
    WHERE  embedding IS NOT NULL
      AND  1 - (embedding <=> query_embedding) >= similarity_threshold
    ORDER  BY embedding <=> query_embedding
    LIMIT  max_results;
$$ LANGUAGE sql STABLE;
