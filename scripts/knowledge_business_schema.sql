-- Knowledge BUSINESS schema — portable relational DDL.
-- The knowledge-domain business tables (kb_datasource / kb_document /
-- doc_chunk / wiki_* / kb_agent) live on the FRAMEWORK relational store,
-- the same one that hosts the framework tables. This file is portable:
-- it runs unmodified on MySQL 8 (utf8mb4) and PostgreSQL; the column types
-- (VARCHAR/TEXT/BIGINT/INTEGER/BOOLEAN/JSON/TIMESTAMP) and the `JSON` type
-- (not PG JSONB) are supported by every mainstream relational database, so
-- the platform can switch to another relational DB without SQL changes.
--
-- Idempotency: table creation is idempotent (CREATE TABLE IF NOT EXISTS).
-- Index statements are plain CREATE INDEX (MySQL 8 has no IF NOT EXISTS for
-- indexes, so the syntax is the common portable subset) — the docker init
-- path always runs on a FRESH database, so they never collide; re-running
-- against an existing schema may report "Duplicate key name" for indexes,
-- which is safe to ignore.
--
-- NOTE on JSON columns: MySQL does not allow a DEFAULT on JSON columns, so
-- the ORM supplies values client-side (SQLAlchemy default=...). Raw inserts
-- must provide them. TEXT columns likewise carry no DEFAULT (MySQL 8
-- disallows literal TEXT defaults); the ORM writes '' / NULL client-side.
-- Embedding vectors do NOT live here — they live in the dedicated vector
-- store (scripts/knowledge_schema.sql on PG+pgvector today).

-- ---------------------------------------------------------------- knowledge base
CREATE TABLE IF NOT EXISTS kb_datasource (
    id                VARCHAR(64) PRIMARY KEY,
    name              VARCHAR(255) NOT NULL,
    label             VARCHAR(255),
    description       TEXT,
    team_name         VARCHAR(64),
    owner_user_id     VARCHAR(64),
    -- pipeline toggles: {"vector_enabled": bool, "keyword_enabled": bool,
    -- "wiki_enabled": bool, "graph_enabled": bool} (app-supplied)
    indexing_strategy JSON NOT NULL,
    custom_wiki_generation       TINYINT(1) DEFAULT 0,
    embedding_model_id           VARCHAR(64),
    summary_model_id             VARCHAR(64),
    vlm_config                   JSON,
    asr_config                   JSON,
    image_processing_config      JSON,
    extract_config               JSON,
    faq_config                   JSON,
    question_generation_config   JSON,
    wiki_config                  JSON,
    storage_provider_config      JSON,
    storage_backend_id           VARCHAR(36),
    vector_store_id              VARCHAR(36),
    state             VARCHAR(16) NOT NULL DEFAULT '1',
    created_by        VARCHAR(64),
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_kb_datasource_team ON kb_datasource (team_name);

-- --------------------------------------------------------------------- document
CREATE TABLE IF NOT EXISTS kb_document (
    id           VARCHAR(64) PRIMARY KEY,
    kb_id        VARCHAR(64) NOT NULL,
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
CREATE INDEX idx_kb_document_kb ON kb_document (kb_id);
-- FK via ALTER TABLE (NOT inline REFERENCES — MySQL parses but ignores
-- column-level REFERENCES; ALTER TABLE ADD CONSTRAINT works on MySQL and PG
-- and reuses the index created above).
ALTER TABLE kb_document
    ADD CONSTRAINT fk_kb_document_kb
    FOREIGN KEY (kb_id) REFERENCES kb_datasource (id) ON DELETE CASCADE;

-- ----------------------------------------------------------------------- chunk
-- Text + metadata only; the embedding vector lives in kb_embedding (vector
-- store) keyed by the same chunk id.
CREATE TABLE IF NOT EXISTS doc_chunk (
    id          VARCHAR(64) PRIMARY KEY,
    kb_id       VARCHAR(64) NOT NULL,
    document_id VARCHAR(64) NOT NULL,
    seq         INTEGER NOT NULL DEFAULT 0,
    content     TEXT NOT NULL,
    meta        JSON NOT NULL,  -- page/section metadata for citation (app-supplied)
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_doc_chunk_doc_seq ON doc_chunk (document_id, seq);
CREATE INDEX idx_doc_chunk_kb ON doc_chunk (kb_id);
ALTER TABLE doc_chunk
    ADD CONSTRAINT fk_doc_chunk_document
    FOREIGN KEY (document_id) REFERENCES kb_document (id) ON DELETE CASCADE;

-- -------------------------------------------------------------------- wiki tree
CREATE TABLE IF NOT EXISTS wiki_folder (
    id         VARCHAR(64) PRIMARY KEY,
    kb_id      VARCHAR(64) NOT NULL,
    name       VARCHAR(255) NOT NULL,
    parent_id  VARCHAR(64) DEFAULT '',
    created_by VARCHAR(64),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_wiki_folder_kb ON wiki_folder (kb_id);

CREATE TABLE IF NOT EXISTS wiki_page (
    id          VARCHAR(64) PRIMARY KEY,
    kb_id       VARCHAR(64) NOT NULL,
    slug        VARCHAR(255) NOT NULL,
    title       VARCHAR(255) NOT NULL,
    page_type   VARCHAR(32) NOT NULL DEFAULT 'entity',
    content     TEXT,  -- no DEFAULT: MySQL 8 disallows literal TEXT defaults; ORM supplies '' client-side
    summary     TEXT,
    source_refs JSON NOT NULL,  -- JSON array of source document ids (app-supplied)
    folder_id   VARCHAR(64) DEFAULT '',
    status      VARCHAR(32) NOT NULL DEFAULT 'active',
    created_by  VARCHAR(64),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_wiki_page_kb ON wiki_page (kb_id);
CREATE INDEX idx_wiki_page_slug ON wiki_page (kb_id, slug);

CREATE TABLE IF NOT EXISTS wiki_link (
    id           VARCHAR(64) PRIMARY KEY,
    kb_id        VARCHAR(64) NOT NULL,
    from_page_id VARCHAR(64) NOT NULL,
    to_page_id   VARCHAR(64) NOT NULL,
    link_type    VARCHAR(32) NOT NULL DEFAULT 'related',
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_wiki_link_from ON wiki_link (from_page_id);
CREATE INDEX idx_wiki_link_to ON wiki_link (to_page_id);
CREATE UNIQUE INDEX uk_wiki_link_pair ON wiki_link (from_page_id, to_page_id);
ALTER TABLE wiki_link
    ADD CONSTRAINT fk_wiki_link_from
    FOREIGN KEY (from_page_id) REFERENCES wiki_page (id) ON DELETE CASCADE;
ALTER TABLE wiki_link
    ADD CONSTRAINT fk_wiki_link_to
    FOREIGN KEY (to_page_id) REFERENCES wiki_page (id) ON DELETE CASCADE;

-- ------------------------------------------------------------------------ agent
CREATE TABLE IF NOT EXISTS kb_agent (
    id          VARCHAR(36) PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    description TEXT,
    avatar      VARCHAR(64),
    is_builtin  BOOLEAN NOT NULL DEFAULT FALSE,
    team_name   VARCHAR(64),
    created_by  VARCHAR(64),
    config      JSON NOT NULL,  -- agent config (app-supplied)
    state       VARCHAR(16) NOT NULL DEFAULT '1',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_kb_agent_team ON kb_agent (team_name);

-- -------------------------------------------------------------------- mcp server
CREATE TABLE IF NOT EXISTS kb_mcp_server (
    id             VARCHAR(36) PRIMARY KEY,
    scope          VARCHAR(16) NOT NULL,          -- personal | team | system
    name           VARCHAR(128) NOT NULL,         -- 网关侧 server name（唯一）
    type           VARCHAR(32) DEFAULT 'streamable_http',
    url            TEXT,
    headers        JSON,                          -- 额外请求头；敏感值加密存
    command        VARCHAR(255),                  -- stdio 启动命令
    args           JSON,                          -- stdio 参数
    env            JSON,                          -- stdio 环境变量
    owner_user_id  VARCHAR(64),
    owner_team_name VARCHAR(64),
    enabled        BOOLEAN NOT NULL DEFAULT TRUE,
    state          VARCHAR(8) NOT NULL DEFAULT '1',
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_kb_mcp_scope ON kb_mcp_server (scope);
CREATE INDEX idx_kb_mcp_name ON kb_mcp_server (name);
CREATE INDEX idx_kb_mcp_owner_user ON kb_mcp_server (owner_user_id);
CREATE INDEX idx_kb_mcp_owner_team ON kb_mcp_server (owner_team_name);

-- ---------------------------------------------------------------------- skill
CREATE TABLE IF NOT EXISTS kb_skill (
    id             VARCHAR(36) PRIMARY KEY,
    scope          VARCHAR(16) NOT NULL,          -- personal | team | system
    name           VARCHAR(128) NOT NULL,         -- 技能名（frontmatter name）
    description    TEXT,
    version        VARCHAR(32),
    package_zip    MEDIUMBLOB,                    -- ZIP 安装包（最大 16MB；PG 部署用 BYTEA，见 ORM LargeBinary）
    package_size   INTEGER,
    owner_user_id  VARCHAR(64),
    owner_team_name VARCHAR(64),
    state          VARCHAR(8) NOT NULL DEFAULT '1',
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_kb_skill_scope ON kb_skill (scope);
CREATE INDEX idx_kb_skill_name ON kb_skill (name);
CREATE INDEX idx_kb_skill_owner_user ON kb_skill (owner_user_id);
CREATE INDEX idx_kb_skill_owner_team ON kb_skill (owner_team_name);

-- ----------------------------------------------------------------- websearch (2026-10-06: 补建，接口曾因缺表 500)
CREATE TABLE IF NOT EXISTS kb_websearch_provider (
	id VARCHAR(36) NOT NULL,
	scope VARCHAR(16) NOT NULL,
	name VARCHAR(128) NOT NULL,
	description TEXT,
	provider_type VARCHAR(32) NOT NULL,
	api_key VARCHAR(512),
	base_url TEXT,
	extra_config JSON,
	owner_user_id VARCHAR(64),
	owner_team_name VARCHAR(64),
	enabled BOOL NOT NULL,
	state VARCHAR(8) NOT NULL,
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
	PRIMARY KEY (id)
);
