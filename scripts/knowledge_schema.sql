-- Knowledge-domain schema (PostgreSQL + pgvector).
-- Applied to KNOWLEDGE_DATABASE_URL (the framework store is separate).
-- Idempotent: safe to re-run.
--
-- NOTE: the `vector` extension lives in the `public` schema. When targeting a
-- non-public schema for the tables, keep `public` on the search_path so the
-- `vector` type resolves:
--     SET search_path TO my_schema, public;

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
-- pg_trgm powers the CJK-tolerant keyword recall (word_similarity). Safe to
-- skip: keyword_search falls back to substring-only scoring without it.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------- knowledge base
CREATE TABLE IF NOT EXISTS kb_datasource (
    id                VARCHAR(64) PRIMARY KEY,
    name              VARCHAR(255) NOT NULL,
    label             VARCHAR(255),
    description       TEXT,
    team_name         VARCHAR(64),
    owner_user_id     VARCHAR(64),
    indexing_strategy JSONB NOT NULL DEFAULT '{"vector_enabled": true, "keyword_enabled": true, "wiki_enabled": true, "graph_enabled": true}',
    state             VARCHAR(16) NOT NULL DEFAULT '1',
    created_by        VARCHAR(64),
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_kb_datasource_team ON kb_datasource (team_name);

-- --------------------------------------------------------------------- document
CREATE TABLE IF NOT EXISTS kb_document (
    id           VARCHAR(64) PRIMARY KEY,
    kb_id        VARCHAR(64) NOT NULL REFERENCES kb_datasource (id) ON DELETE CASCADE,
    file_name    VARCHAR(255) NOT NULL,
    file_ext     VARCHAR(32),
    file_size    BIGINT,
    sys_file_id  VARCHAR(64),
    storage_path VARCHAR(1000),
    parse_engine VARCHAR(64),
    parse_state  VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    parse_error  TEXT,
    chunk_count  INTEGER NOT NULL DEFAULT 0,
    created_by   VARCHAR(64),
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_kb_document_kb ON kb_document (kb_id);

-- ----------------------------------------------------------------------- chunk
CREATE TABLE IF NOT EXISTS doc_chunk (
    id          VARCHAR(64) PRIMARY KEY,
    kb_id       VARCHAR(64) NOT NULL,
    document_id VARCHAR(64) NOT NULL REFERENCES kb_document (id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL DEFAULT 0,
    content     TEXT NOT NULL,
    meta        JSONB DEFAULT '{}',
    embedding   vector(1024),
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_doc_chunk_doc_seq ON doc_chunk (document_id, seq);
CREATE INDEX IF NOT EXISTS idx_doc_chunk_kb ON doc_chunk (kb_id);
-- HNSW index for ANN search; distance = cosine on the L2-normalized vectors.
CREATE INDEX IF NOT EXISTS idx_doc_chunk_embedding_hnsw
    ON doc_chunk USING hnsw (embedding vector_cosine_ops);

-- -------------------------------------------------------------------- wiki tree
CREATE TABLE IF NOT EXISTS wiki_folder (
    id         VARCHAR(64) PRIMARY KEY,
    kb_id      VARCHAR(64) NOT NULL,
    name       VARCHAR(255) NOT NULL,
    parent_id  VARCHAR(64) DEFAULT '',
    created_by VARCHAR(64),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_wiki_folder_kb ON wiki_folder (kb_id);

CREATE TABLE IF NOT EXISTS wiki_page (
    id          VARCHAR(64) PRIMARY KEY,
    kb_id       VARCHAR(64) NOT NULL,
    slug        VARCHAR(255) NOT NULL,
    title       VARCHAR(255) NOT NULL,
    page_type   VARCHAR(32) NOT NULL DEFAULT 'entity',
    content     TEXT DEFAULT '',
    summary     TEXT,
    source_refs JSONB DEFAULT '[]',
    folder_id   VARCHAR(64) DEFAULT '',
    status      VARCHAR(32) NOT NULL DEFAULT 'active',
    created_by  VARCHAR(64),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_wiki_page_kb ON wiki_page (kb_id);
CREATE INDEX IF NOT EXISTS idx_wiki_page_slug ON wiki_page (kb_id, slug);
-- Chinese-friendly lexical index: 'simple' config avoids stemming surprises.
CREATE INDEX IF NOT EXISTS idx_wiki_page_fts
    ON wiki_page USING GIN (to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(content, '')));

CREATE TABLE IF NOT EXISTS wiki_link (
    id           VARCHAR(64) PRIMARY KEY,
    kb_id        VARCHAR(64) NOT NULL,
    from_page_id VARCHAR(64) NOT NULL REFERENCES wiki_page (id) ON DELETE CASCADE,
    to_page_id   VARCHAR(64) NOT NULL REFERENCES wiki_page (id) ON DELETE CASCADE,
    link_type    VARCHAR(32) NOT NULL DEFAULT 'related',
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_wiki_link_from ON wiki_link (from_page_id);
CREATE INDEX IF NOT EXISTS idx_wiki_link_to ON wiki_link (to_page_id);
CREATE UNIQUE INDEX IF NOT EXISTS uk_wiki_link_pair ON wiki_link (from_page_id, to_page_id);
