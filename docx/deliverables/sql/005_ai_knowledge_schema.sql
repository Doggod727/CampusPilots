BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS ai_knowledge;

CREATE OR REPLACE FUNCTION ai_knowledge.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

CREATE TABLE IF NOT EXISTS ai_knowledge.knowledge_bases (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name varchar(100) NOT NULL,
    description varchar(500) NULL,
    visibility varchar(16) NOT NULL DEFAULT 'private',
    owner_user_id uuid NULL,
    owner_department varchar(100) NULL,
    embedding_model varchar(100) NOT NULL DEFAULT 'bge-small-zh-v1.5',
    chunk_size integer NOT NULL DEFAULT 500,
    chunk_overlap integer NOT NULL DEFAULT 80,
    collection_name varchar(80) NOT NULL UNIQUE,
    version integer NOT NULL DEFAULT 1,
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz NULL,
    CONSTRAINT ck_knowledge_bases_visibility
        CHECK (visibility IN ('public', 'department', 'private')),
    CONSTRAINT ck_knowledge_bases_owner
        CHECK (owner_user_id IS NOT NULL OR owner_department IS NOT NULL),
    CONSTRAINT ck_knowledge_bases_chunk_size
        CHECK (chunk_size BETWEEN 100 AND 2000),
    CONSTRAINT ck_knowledge_bases_chunk_overlap
        CHECK (chunk_overlap BETWEEN 0 AND 500 AND chunk_overlap < chunk_size),
    CONSTRAINT ck_knowledge_bases_collection
        CHECK (collection_name ~ '^kb_[a-f0-9]{32}$'),
    CONSTRAINT ck_knowledge_bases_version CHECK (version >= 1)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_bases_name_active
    ON ai_knowledge.knowledge_bases (lower(name))
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_knowledge_bases_owner
    ON ai_knowledge.knowledge_bases (owner_department, owner_user_id)
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS ai_knowledge.knowledge_base_members (
    knowledge_base_id uuid NOT NULL
        REFERENCES ai_knowledge.knowledge_bases(id) ON DELETE CASCADE,
    user_id uuid NOT NULL,
    access_level varchar(16) NOT NULL,
    granted_by uuid NOT NULL,
    granted_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (knowledge_base_id, user_id),
    CONSTRAINT ck_knowledge_base_members_access
        CHECK (access_level IN ('viewer', 'editor', 'owner'))
);

CREATE INDEX IF NOT EXISTS ix_knowledge_base_members_user
    ON ai_knowledge.knowledge_base_members (user_id, access_level);

CREATE TABLE IF NOT EXISTS ai_knowledge.documents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    knowledge_base_id uuid NOT NULL
        REFERENCES ai_knowledge.knowledge_bases(id) ON DELETE RESTRICT,
    title varchar(200) NOT NULL,
    original_file_name varchar(255) NOT NULL,
    object_key varchar(500) NOT NULL UNIQUE,
    mime_type varchar(100) NOT NULL,
    file_size_bytes bigint NOT NULL,
    file_sha256 char(64) NOT NULL,
    status varchar(20) NOT NULL DEFAULT 'pending',
    document_version integer NOT NULL DEFAULT 1,
    index_version integer NULL,
    page_count integer NULL,
    chunk_count integer NOT NULL DEFAULT 0,
    published_at timestamptz NULL,
    inactive_at timestamptz NULL,
    expires_at timestamptz NULL,
    created_by uuid NOT NULL,
    version integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz NULL,
    CONSTRAINT ck_documents_file_size CHECK (file_size_bytes BETWEEN 1 AND 20971520),
    CONSTRAINT ck_documents_sha256 CHECK (file_sha256 ~ '^[a-f0-9]{64}$'),
    CONSTRAINT ck_documents_status
        CHECK (status IN ('pending', 'processing', 'ready', 'published', 'inactive', 'failed', 'deleted')),
    CONSTRAINT ck_documents_version CHECK (document_version >= 1 AND version >= 1),
    CONSTRAINT ck_documents_chunk_count CHECK (chunk_count >= 0),
    CONSTRAINT ck_documents_publish_state CHECK (
        (status = 'published' AND published_at IS NOT NULL AND index_version IS NOT NULL)
        OR status <> 'published'
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_hash_active
    ON ai_knowledge.documents (knowledge_base_id, file_sha256)
    WHERE deleted_at IS NULL AND status <> 'deleted';
CREATE INDEX IF NOT EXISTS ix_documents_kb_status
    ON ai_knowledge.documents (knowledge_base_id, status, updated_at DESC)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_documents_expiry
    ON ai_knowledge.documents (expires_at)
    WHERE status = 'published' AND expires_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS ai_knowledge.ingestion_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL
        REFERENCES ai_knowledge.documents(id) ON DELETE CASCADE,
    celery_task_id varchar(100) NULL UNIQUE,
    stage varchar(20) NOT NULL DEFAULT 'queued',
    progress smallint NOT NULL DEFAULT 0,
    attempt integer NOT NULL DEFAULT 1,
    max_attempts integer NOT NULL DEFAULT 3,
    error_code varchar(100) NULL,
    error_message varchar(1000) NULL,
    started_at timestamptz NULL,
    finished_at timestamptz NULL,
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_ingestion_jobs_stage
        CHECK (stage IN ('queued', 'parsing', 'cleaning', 'splitting', 'embedding', 'indexing', 'succeeded', 'failed')),
    CONSTRAINT ck_ingestion_jobs_progress CHECK (progress BETWEEN 0 AND 100),
    CONSTRAINT ck_ingestion_jobs_attempt CHECK (attempt BETWEEN 1 AND max_attempts),
    CONSTRAINT ck_ingestion_jobs_terminal CHECK (
        (stage IN ('succeeded', 'failed') AND finished_at IS NOT NULL)
        OR stage NOT IN ('succeeded', 'failed')
    )
);

CREATE INDEX IF NOT EXISTS ix_ingestion_jobs_document
    ON ai_knowledge.ingestion_jobs (document_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_ingestion_jobs_queue
    ON ai_knowledge.ingestion_jobs (stage, created_at)
    WHERE stage NOT IN ('succeeded', 'failed');

CREATE TABLE IF NOT EXISTS ai_knowledge.document_chunks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id uuid NOT NULL
        REFERENCES ai_knowledge.documents(id) ON DELETE CASCADE,
    document_version integer NOT NULL,
    chunk_index integer NOT NULL,
    content text NOT NULL,
    source_location varchar(500) NOT NULL,
    page_number integer NULL,
    token_count integer NOT NULL,
    content_sha256 char(64) NOT NULL,
    clean_status varchar(16) NOT NULL DEFAULT 'clean',
    vector_id varchar(100) NOT NULL UNIQUE,
    index_version integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, document_version, chunk_index),
    CONSTRAINT ck_document_chunks_index CHECK (chunk_index >= 0),
    CONSTRAINT ck_document_chunks_page CHECK (page_number IS NULL OR page_number >= 1),
    CONSTRAINT ck_document_chunks_token_count CHECK (token_count BETWEEN 1 AND 4000),
    CONSTRAINT ck_document_chunks_sha256 CHECK (content_sha256 ~ '^[a-f0-9]{64}$'),
    CONSTRAINT ck_document_chunks_clean_status
        CHECK (clean_status IN ('clean', 'redacted', 'excluded'))
);

CREATE INDEX IF NOT EXISTS ix_document_chunks_document
    ON ai_knowledge.document_chunks (document_id, document_version, chunk_index);

CREATE TABLE IF NOT EXISTS ai_knowledge.conversations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    title varchar(100) NOT NULL DEFAULT '新对话',
    status varchar(16) NOT NULL DEFAULT 'active',
    last_message_at timestamptz NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz NULL,
    CONSTRAINT ck_conversations_status CHECK (status IN ('active', 'deleted'))
);

CREATE INDEX IF NOT EXISTS ix_conversations_user
    ON ai_knowledge.conversations (user_id, updated_at DESC)
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS ai_knowledge.messages (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid NOT NULL
        REFERENCES ai_knowledge.conversations(id) ON DELETE CASCADE,
    sequence_no integer NOT NULL,
    role varchar(16) NOT NULL,
    status varchar(16) NOT NULL,
    content text NOT NULL DEFAULT '',
    reply_to_message_id uuid NULL
        REFERENCES ai_knowledge.messages(id) ON DELETE SET NULL,
    model varchar(100) NULL,
    finish_reason varchar(50) NULL,
    prompt_tokens integer NULL,
    completion_tokens integer NULL,
    retrieval_confidence numeric(6,5) NULL,
    fallback_reason varchar(200) NULL,
    request_id varchar(64) NOT NULL,
    error_code varchar(100) NULL,
    started_at timestamptz NULL,
    completed_at timestamptz NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, sequence_no),
    CONSTRAINT ck_messages_sequence CHECK (sequence_no >= 1),
    CONSTRAINT ck_messages_role CHECK (role IN ('user', 'assistant')),
    CONSTRAINT ck_messages_status
        CHECK (status IN ('pending', 'streaming', 'completed', 'failed', 'cancelled', 'fallback')),
    CONSTRAINT ck_messages_tokens CHECK (
        (prompt_tokens IS NULL OR prompt_tokens >= 0)
        AND (completion_tokens IS NULL OR completion_tokens >= 0)
    ),
    CONSTRAINT ck_messages_confidence CHECK (
        retrieval_confidence IS NULL OR retrieval_confidence BETWEEN 0 AND 1
    )
);

CREATE INDEX IF NOT EXISTS ix_messages_conversation
    ON ai_knowledge.messages (conversation_id, sequence_no);
CREATE INDEX IF NOT EXISTS ix_messages_request_id
    ON ai_knowledge.messages (request_id);

CREATE TABLE IF NOT EXISTS ai_knowledge.retrieval_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    assistant_message_id uuid NOT NULL UNIQUE
        REFERENCES ai_knowledge.messages(id) ON DELETE CASCADE,
    query_sha256 char(64) NOT NULL,
    knowledge_base_ids jsonb NOT NULL,
    top_k integer NOT NULL,
    score_threshold numeric(6,5) NOT NULL,
    result_summary jsonb NOT NULL DEFAULT '[]'::jsonb,
    duration_ms integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_retrieval_runs_sha256 CHECK (query_sha256 ~ '^[a-f0-9]{64}$'),
    CONSTRAINT ck_retrieval_runs_kbs CHECK (jsonb_typeof(knowledge_base_ids) = 'array'),
    CONSTRAINT ck_retrieval_runs_top_k CHECK (top_k BETWEEN 1 AND 50),
    CONSTRAINT ck_retrieval_runs_threshold CHECK (score_threshold BETWEEN 0 AND 1),
    CONSTRAINT ck_retrieval_runs_results CHECK (jsonb_typeof(result_summary) = 'array'),
    CONSTRAINT ck_retrieval_runs_duration CHECK (duration_ms >= 0)
);

CREATE TABLE IF NOT EXISTS ai_knowledge.message_citations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id uuid NOT NULL
        REFERENCES ai_knowledge.messages(id) ON DELETE CASCADE,
    citation_no integer NOT NULL,
    chunk_id uuid NOT NULL
        REFERENCES ai_knowledge.document_chunks(id) ON DELETE RESTRICT,
    document_id uuid NOT NULL,
    document_title varchar(200) NOT NULL,
    source_location varchar(500) NOT NULL,
    page_number integer NULL,
    quote_excerpt varchar(500) NOT NULL,
    relevance_score numeric(6,5) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (message_id, citation_no),
    UNIQUE (message_id, chunk_id),
    CONSTRAINT ck_message_citations_no CHECK (citation_no >= 1),
    CONSTRAINT ck_message_citations_page CHECK (page_number IS NULL OR page_number >= 1),
    CONSTRAINT ck_message_citations_score CHECK (relevance_score BETWEEN 0 AND 1)
);

CREATE INDEX IF NOT EXISTS ix_message_citations_message
    ON ai_knowledge.message_citations (message_id, citation_no);

CREATE TABLE IF NOT EXISTS ai_knowledge.message_feedback (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id uuid NOT NULL
        REFERENCES ai_knowledge.messages(id) ON DELETE CASCADE,
    user_id uuid NOT NULL,
    rating smallint NOT NULL,
    correction varchar(1000) NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (message_id, user_id),
    CONSTRAINT ck_message_feedback_rating CHECK (rating IN (-1, 1))
);

CREATE TABLE IF NOT EXISTS ai_knowledge.llm_calls (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id uuid NOT NULL
        REFERENCES ai_knowledge.messages(id) ON DELETE CASCADE,
    attempt integer NOT NULL DEFAULT 1,
    provider varchar(30) NOT NULL DEFAULT 'deepseek',
    model varchar(100) NOT NULL,
    provider_request_id varchar(100) NULL,
    thinking_enabled boolean NOT NULL DEFAULT false,
    status varchar(16) NOT NULL,
    finish_reason varchar(50) NULL,
    prompt_tokens integer NULL,
    completion_tokens integer NULL,
    first_token_ms integer NULL,
    total_duration_ms integer NULL,
    error_code varchar(100) NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz NULL,
    UNIQUE (message_id, attempt),
    CONSTRAINT ck_llm_calls_attempt CHECK (attempt BETWEEN 1 AND 3),
    CONSTRAINT ck_llm_calls_status
        CHECK (status IN ('started', 'completed', 'failed', 'cancelled')),
    CONSTRAINT ck_llm_calls_metrics CHECK (
        (prompt_tokens IS NULL OR prompt_tokens >= 0)
        AND (completion_tokens IS NULL OR completion_tokens >= 0)
        AND (first_token_ms IS NULL OR first_token_ms >= 0)
        AND (total_duration_ms IS NULL OR total_duration_ms >= 0)
    )
);

CREATE INDEX IF NOT EXISTS ix_llm_calls_message
    ON ai_knowledge.llm_calls (message_id, attempt);

DROP TRIGGER IF EXISTS trg_knowledge_bases_updated_at ON ai_knowledge.knowledge_bases;
CREATE TRIGGER trg_knowledge_bases_updated_at
BEFORE UPDATE ON ai_knowledge.knowledge_bases
FOR EACH ROW EXECUTE FUNCTION ai_knowledge.set_updated_at();

DROP TRIGGER IF EXISTS trg_documents_updated_at ON ai_knowledge.documents;
CREATE TRIGGER trg_documents_updated_at
BEFORE UPDATE ON ai_knowledge.documents
FOR EACH ROW EXECUTE FUNCTION ai_knowledge.set_updated_at();

DROP TRIGGER IF EXISTS trg_ingestion_jobs_updated_at ON ai_knowledge.ingestion_jobs;
CREATE TRIGGER trg_ingestion_jobs_updated_at
BEFORE UPDATE ON ai_knowledge.ingestion_jobs
FOR EACH ROW EXECUTE FUNCTION ai_knowledge.set_updated_at();

DROP TRIGGER IF EXISTS trg_conversations_updated_at ON ai_knowledge.conversations;
CREATE TRIGGER trg_conversations_updated_at
BEFORE UPDATE ON ai_knowledge.conversations
FOR EACH ROW EXECUTE FUNCTION ai_knowledge.set_updated_at();

DROP TRIGGER IF EXISTS trg_messages_updated_at ON ai_knowledge.messages;
CREATE TRIGGER trg_messages_updated_at
BEFORE UPDATE ON ai_knowledge.messages
FOR EACH ROW EXECUTE FUNCTION ai_knowledge.set_updated_at();

DROP TRIGGER IF EXISTS trg_message_feedback_updated_at ON ai_knowledge.message_feedback;
CREATE TRIGGER trg_message_feedback_updated_at
BEFORE UPDATE ON ai_knowledge.message_feedback
FOR EACH ROW EXECUTE FUNCTION ai_knowledge.set_updated_at();

COMMENT ON SCHEMA ai_knowledge IS 'M1 AI 知识、RAG 与对话数据域';
COMMENT ON TABLE ai_knowledge.document_chunks IS 'PostgreSQL 保存可追溯 Chunk 文本；Chroma 仅为可重建检索索引';
COMMENT ON TABLE ai_knowledge.retrieval_runs IS '只保存查询哈希和脱敏结果摘要，不保存额外用户隐私';
COMMENT ON TABLE ai_knowledge.llm_calls IS '不保存 Prompt、API Key 或 reasoning_content';
COMMENT ON COLUMN ai_knowledge.knowledge_bases.collection_name IS '格式 kb_<32位无连字符UUID>';

COMMIT;
