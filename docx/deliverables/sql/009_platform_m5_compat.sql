BEGIN;

-- M5 compatibility is additive: keep every existing value and constraint meaning.
ALTER TABLE platform.sensitive_words
    DROP CONSTRAINT IF EXISTS ck_sensitive_words_scope;
ALTER TABLE platform.sensitive_words
    ADD CONSTRAINT ck_sensitive_words_scope
    CHECK (scope IN (
        'user_input', 'ai_output', 'community', 'all',
        'tool_input', 'tool_output', 'agent_context'
    ));

ALTER TABLE platform.moderation_cases
    DROP CONSTRAINT IF EXISTS ck_moderation_cases_target_module;
ALTER TABLE platform.moderation_cases
    ADD CONSTRAINT ck_moderation_cases_target_module
    CHECK (target_module IN (
        'ai_knowledge', 'campus_service', 'community', 'agent_platform'
    ));

INSERT INTO platform.permissions (code, name, module, description) VALUES
('agent:run', '运行智能体', 'agent_platform', '创建、取消和继续本人 Agent Run'),
('agent:run:read_own', '查看本人智能体运行', 'agent_platform', '查看本人 Agent Run 与脱敏轨迹'),
('agent:run:read_all', '查看全部智能体运行', 'agent_platform', '查看授权范围内全部 Agent Run'),
('agent:catalog:read', '查看智能体目录', 'agent_platform', '查看启用 Agent 和公开版本信息'),
('tool:catalog:read', '查看工具目录', 'agent_platform', '按权限查看 Tool Schema 和风险信息'),
('tool:catalog:write', '管理工具目录', 'agent_platform', '启停或切换 Tool 版本'),
('dataset:read', '查看训练数据集', 'modelops', '查看脱敏数据集和版本元数据'),
('dataset:write', '管理训练数据集', 'modelops', '创建、校验、冻结和删除数据集版本'),
('training:run', '运行模型训练', 'modelops', '创建和取消本地小模型训练任务'),
('training:read', '查看模型训练', 'modelops', '查看训练状态、配置和脱敏日志'),
('model:read', '查看模型版本', 'modelops', '查看模型注册表和评估指标'),
('model:write', '管理模型版本', 'modelops', '注册、停用模型版本'),
('model:activate', '启用或回滚模型', 'modelops', '经确认启用或回滚活动模型'),
('evaluation:run', '运行模型评估', 'modelops', '运行 Agent、Tool、RAG 和模型评估'),
('evaluation:read', '查看模型评估', 'modelops', '查看和比较评估报告'),
('moderation:execute', '执行内容治理', 'platform', '供受信 Agent Runtime 执行输入输出治理'),
('audit:write', '写入审计事件', 'platform', '供受信 Agent Runtime 写入结构化审计事件'),
('service:read', '查看校园服务', 'campus_service', '查询有效办事指南'),
('electricity:read_own', '查看本人房间电费', 'campus_service', '查看授权房间的 Mock 电费余额'),
('electricity:topup_request:create', '创建电费模拟充值申请', 'campus_service', '创建不涉及真实支付的模拟充值申请')
ON CONFLICT (code) DO UPDATE SET
    name = EXCLUDED.name,
    module = EXCLUDED.module,
    description = EXCLUDED.description;

INSERT INTO platform.roles (code, name, description, is_system) VALUES
('model_engineer', '模型工程管理员', '管理脱敏数据集、本地训练、模型版本和评估', true),
('agent_runtime', '智能体运行服务', '仅分配给受信服务身份，不分配给普通用户', true)
ON CONFLICT (code) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    is_system = EXCLUDED.is_system;

-- Super admin receives all newly inserted permissions.
INSERT INTO platform.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM platform.roles r
CROSS JOIN platform.permissions p
WHERE r.code = 'super_admin'
ON CONFLICT DO NOTHING;

INSERT INTO platform.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM platform.roles r
JOIN platform.permissions p ON p.code IN (
    'agent:run:read_all', 'agent:catalog:read', 'tool:catalog:read',
    'dataset:read', 'dataset:write', 'training:run', 'training:read',
    'model:read', 'model:write', 'model:activate',
    'evaluation:run', 'evaluation:read'
)
WHERE r.code = 'model_engineer'
ON CONFLICT DO NOTHING;

INSERT INTO platform.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM platform.roles r
JOIN platform.permissions p ON p.code IN (
    'agent:run', 'moderation:execute', 'audit:write'
)
WHERE r.code = 'agent_runtime'
ON CONFLICT DO NOTHING;

INSERT INTO platform.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM platform.roles r
JOIN platform.permissions p ON p.code IN (
    'agent:run', 'agent:run:read_own', 'agent:catalog:read',
    'tool:catalog:read', 'service:read',
    'electricity:read_own', 'electricity:topup_request:create'
)
WHERE r.code = 'student'
ON CONFLICT DO NOTHING;

INSERT INTO platform.app_configs
    (key, namespace, value, value_type, description, editable)
VALUES
('agent.max_steps', 'agent', '6'::jsonb, 'integer', '单个 Agent Run 最大步骤数', true),
('agent.max_specialists', 'agent', '3'::jsonb, 'integer', '单次运行最多专业 Agent 数', true),
('agent.approval_ttl_seconds', 'agent', '600'::jsonb, 'integer', '写 Tool 确认有效期', true),
('agent.parallelism', 'agent', '3'::jsonb, 'integer', 'P1 并行 Agent 最大并发', true),
('modelops.router_confidence', 'modelops', '0.80'::jsonb, 'number', '本地路由模型直接采用阈值', true),
('modelops.reranker_enabled', 'modelops', 'false'::jsonb, 'boolean', '是否启用本地 RAG Reranker', true),
('mcp.enabled', 'agent', 'false'::jsonb, 'boolean', '是否启用 P1 MCP Server', true)
ON CONFLICT (key) DO UPDATE SET
    namespace = EXCLUDED.namespace,
    value = EXCLUDED.value,
    value_type = EXCLUDED.value_type,
    description = EXCLUDED.description,
    editable = EXCLUDED.editable;

COMMIT;
