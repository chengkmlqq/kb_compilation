-- Vector-store schema (PostgreSQL + pgvector) — VECTOR STORE ONLY.
-- Applied to KNOWLEDGE_DATABASE_URL on first boot of the pg container.
-- Idempotent: safe to re-run.
--
-- Storage split (multi-database, cross-dialect):
-- - The knowledge BUSINESS tables (kb_datasource / kb_document / doc_chunk /
--   wiki_* / kb_agent) live on the FRAMEWORK relational store (MySQL by
--   default, portable to any relational DB) — see
--   scripts/knowledge_business_schema.sql.
-- - This file creates ONLY the vector table `kb_embedding` (one row per
--   chunk, `chunk_id` mirrors doc_chunk.id in the business store). All
--   vector I/O goes through api.services.vector_store.VectorStore, so the
--   backend can later be swapped to Elasticsearch / Milvus without touching
--   callers (VECTOR_STORE_TYPE=es reserved).
--
-- NOTE: the `vector` extension lives in the `public` schema. When targeting a
-- non-public schema for the table, keep `public` on the search_path so the
-- `vector` type resolves:
--     SET search_path TO my_schema, public;

CREATE EXTENSION IF NOT EXISTS vector;

-- ----------------------------------------------------------------- embedding
CREATE TABLE IF NOT EXISTS kb_embedding (
    id         VARCHAR(64) PRIMARY KEY,
    kb_id      VARCHAR(64) NOT NULL,
    chunk_id   VARCHAR(64) NOT NULL UNIQUE,
    embedding  vector(1024) NOT NULL,
    enabled    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_kb_embedding_kb ON kb_embedding (kb_id);
-- HNSW index for ANN search; distance = cosine on the L2-normalized vectors.
CREATE INDEX IF NOT EXISTS idx_kb_embedding_hnsw
    ON kb_embedding USING hnsw (embedding vector_cosine_ops);
