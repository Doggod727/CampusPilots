BEGIN;

INSERT INTO platform.app_configs
    (key, namespace, value, value_type, description, editable)
VALUES
('rag.top_k', 'rag', '6'::jsonb, 'integer', '向量检索候选数量', true),
('rag.score_threshold', 'rag', '0.62'::jsonb, 'number', '低于该归一化分数时进入兜底', true),
('chat.max_history_turns', 'chat', '6'::jsonb, 'integer', '进入 Prompt 的最大历史轮数', true),
('chat.max_question_chars', 'chat', '2000'::jsonb, 'integer', '单次问题最大字符数', true),
('ingestion.max_file_mb', 'ingestion', '20'::jsonb, 'integer', '单个上传文件上限', true),
('ingestion.max_files_per_request', 'ingestion', '10'::jsonb, 'integer', '一次上传文件数量上限', true),
('ingestion.chunk_size', 'ingestion', '500'::jsonb, 'integer', '默认递归切分字符目标', true),
('ingestion.chunk_overlap', 'ingestion', '80'::jsonb, 'integer', '默认切分重叠字符数', true),
('llm.deepseek_thinking', 'llm', 'false'::jsonb, 'boolean', '校园 RAG 默认关闭 Thinking', false)
ON CONFLICT (key) DO UPDATE SET
    namespace = EXCLUDED.namespace,
    value = EXCLUDED.value,
    value_type = EXCLUDED.value_type,
    description = EXCLUDED.description,
    editable = EXCLUDED.editable;

-- 知识库、文档和会话演示数据由 Python seed_demo 创建，以绑定演示用户 UUID、
-- 生成真实文件 SHA-256，并调用同一入库服务创建 Chroma 索引。

COMMIT;
