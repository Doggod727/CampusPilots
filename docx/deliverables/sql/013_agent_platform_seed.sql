BEGIN;

-- M5 演示基线：6 个 Agent、14 个 Tool、固定模型路由。
-- 所有 UPSERT 均可重复执行；生产密钥不得写入数据库或本脚本。

INSERT INTO agent_platform.agent_definitions (code, name, description, enabled)
VALUES
    ('supervisor', '编排主管 Agent', '意图识别、任务拆分、路由、并行汇总与失败降级', true),
    ('knowledge_agent', '知识问答 Agent', '知识检索、RAG 重排、引用约束与复杂问答', true),
    ('service_agent', '校园服务 Agent', '办事指南、报修、电费余额与模拟充值申请', true),
    ('community_agent', '社区互助 Agent', '活动、报名、失物招领发布与匹配', true),
    ('governance_agent', '治理 Agent', '内容审核、权限判定、人工确认与审计', true),
    ('modelops_agent', '模型工程 Agent', '数据集、微调、评估与模型版本生命周期', true)
ON CONFLICT (code) DO UPDATE
SET name = EXCLUDED.name,
    description = EXCLUDED.description,
    enabled = EXCLUDED.enabled,
    updated_at = now();

INSERT INTO agent_platform.tool_definitions
    (name, module, description, risk_level, visibility, enabled)
VALUES
    ('knowledge.search', 'm1', '检索知识片段并执行本地 RAG 重排', 'r0', 'agent', true),
    ('knowledge.answer', 'm1', '基于证据生成带引用回答；复杂生成调用 DeepSeek', 'r1', 'agent', true),
    ('service.get_guide', 'm2', '查询办事指南及个性化材料清单', 'r0', 'agent', true),
    ('work_order.create', 'm2', '创建宿舍报修工单', 'r2', 'agent', true),
    ('work_order.get', 'm2', '查询本人可见的报修工单', 'r0', 'agent', true),
    ('electricity.get_balance', 'm2', '查询本人绑定房间的演示电费余额', 'r1', 'agent', true),
    ('electricity.create_topup_request', 'm2', '创建模拟电费充值申请，不接真实支付', 'r2', 'agent', true),
    ('event.search', 'm3', '搜索可见校园活动', 'r0', 'agent', true),
    ('event.register', 'm3', '报名校园活动并处理名额并发', 'r2', 'agent', true),
    ('lost_found.publish', 'm3', '发布脱敏后的失物或拾物信息', 'r2', 'agent', true),
    ('lost_found.search_matches', 'm3', '检索失物招领候选匹配', 'r1', 'agent', true),
    ('governance.check_content', 'm4', '对 Agent 输入输出执行敏感词与安全策略检查', 'r1', 'runtime_internal', true),
    ('governance.authorize_tool', 'm4', '校验用户、Agent、Tool 与资源范围权限', 'r1', 'runtime_internal', true),
    ('governance.write_audit', 'm4', '写入不可变更的结构化审计事件', 'r1', 'runtime_internal', true)
ON CONFLICT (name) DO UPDATE
SET module = EXCLUDED.module,
    description = EXCLUDED.description,
    risk_level = EXCLUDED.risk_level,
    visibility = EXCLUDED.visibility,
    enabled = EXCLUDED.enabled,
    updated_at = now();

WITH specs(name, input_schema, output_schema, permissions, timeout_ms, idempotent, approval, impl) AS (
    VALUES
    ('knowledge.search',
     '{"type":"object","required":["query"],"properties":{"query":{"type":"string","minLength":2,"maxLength":1000},"knowledge_base_ids":{"type":"array","items":{"type":"string","format":"uuid"}},"top_k":{"type":"integer","minimum":1,"maximum":20,"default":5}}}'::jsonb,
     '{"type":"object","required":["items"],"properties":{"items":{"type":"array"},"retrieval_ms":{"type":"integer"},"rerank_ms":{"type":"integer"}}}'::jsonb,
     '["knowledge:read"]'::jsonb, 8000, true, false, 'app.modules.m1.tools:knowledge_search'),
    ('knowledge.answer',
     '{"type":"object","required":["question"],"properties":{"question":{"type":"string","minLength":2,"maxLength":2000},"conversation_id":{"type":["string","null"],"format":"uuid"},"stream":{"type":"boolean","default":false}}}'::jsonb,
     '{"type":"object","required":["answer","citations","model_route"],"properties":{"answer":{"type":"string"},"citations":{"type":"array"},"model_route":{"type":"string"}}}'::jsonb,
     '["knowledge:read"]'::jsonb, 60000, false, false, 'app.modules.m1.tools:knowledge_answer'),
    ('service.get_guide',
     '{"type":"object","required":["query"],"properties":{"query":{"type":"string","minLength":2,"maxLength":200},"campus_code":{"type":["string","null"]}}}'::jsonb,
     '{"type":"object","required":["items"],"properties":{"items":{"type":"array"}}}'::jsonb,
     '["service:read"]'::jsonb, 5000, true, false, 'app.modules.m2.tools:service_get_guide'),
    ('work_order.create',
     '{"type":"object","required":["category_id","location","description"],"properties":{"category_id":{"type":"string","format":"uuid"},"location":{"type":"string","maxLength":200},"description":{"type":"string","minLength":5,"maxLength":2000},"attachments":{"type":"array","maxItems":5}}}'::jsonb,
     '{"type":"object","required":["work_order_id","status"],"properties":{"work_order_id":{"type":"string","format":"uuid"},"status":{"type":"string"}}}'::jsonb,
     '["work_order:create"]'::jsonb, 10000, false, true, 'app.modules.m2.tools:work_order_create'),
    ('work_order.get',
     '{"type":"object","required":["work_order_id"],"properties":{"work_order_id":{"type":"string","format":"uuid"}}}'::jsonb,
     '{"type":"object","required":["work_order"],"properties":{"work_order":{"type":"object"}}}'::jsonb,
     '["work_order:read"]'::jsonb, 5000, true, false, 'app.modules.m2.tools:work_order_get'),
    ('electricity.get_balance',
     '{"type":"object","required":["room_id"],"properties":{"room_id":{"type":"string","format":"uuid"}}}'::jsonb,
     '{"type":"object","required":["room_id","balance_cny","source","is_simulated","as_of"],"properties":{"room_id":{"type":"string","format":"uuid"},"balance_cny":{"type":"number"},"source":{"const":"mock"},"is_simulated":{"const":true},"as_of":{"type":"string","format":"date-time"}}}'::jsonb,
     '["electricity:read_own"]'::jsonb, 5000, true, false, 'app.modules.m2.tools:electricity_get_balance'),
    ('electricity.create_topup_request',
     '{"type":"object","required":["room_id","amount_cny"],"properties":{"room_id":{"type":"string","format":"uuid"},"amount_cny":{"type":"number","minimum":1,"maximum":500}}}'::jsonb,
     '{"type":"object","required":["request_id","status","source","is_simulated","notice"],"properties":{"request_id":{"type":"string","format":"uuid"},"status":{"const":"simulated"},"source":{"const":"mock"},"is_simulated":{"const":true},"notice":{"type":"string"}}}'::jsonb,
     '["electricity:topup_request:create"]'::jsonb, 10000, false, true, 'app.modules.m2.tools:electricity_create_topup'),
    ('event.search',
     '{"type":"object","properties":{"query":{"type":["string","null"],"maxLength":200},"from":{"type":["string","null"],"format":"date-time"},"to":{"type":["string","null"],"format":"date-time"}}}'::jsonb,
     '{"type":"object","required":["items"],"properties":{"items":{"type":"array"}}}'::jsonb,
     '["community:read"]'::jsonb, 5000, true, false, 'app.modules.m3.tools:event_search'),
    ('event.register',
     '{"type":"object","required":["event_id"],"properties":{"event_id":{"type":"string","format":"uuid"}}}'::jsonb,
     '{"type":"object","required":["registration_id","status"],"properties":{"registration_id":{"type":"string","format":"uuid"},"status":{"type":"string"}}}'::jsonb,
     '["community:write"]'::jsonb, 10000, false, true, 'app.modules.m3.tools:event_register'),
    ('lost_found.publish',
     '{"type":"object","required":["type","title","description","occurred_at","location"],"properties":{"type":{"enum":["lost","found"]},"title":{"type":"string","maxLength":100},"description":{"type":"string","maxLength":2000},"occurred_at":{"type":"string","format":"date-time"},"location":{"type":"string","maxLength":200}}}'::jsonb,
     '{"type":"object","required":["item_id","status"],"properties":{"item_id":{"type":"string","format":"uuid"},"status":{"type":"string"}}}'::jsonb,
     '["community:write"]'::jsonb, 10000, false, true, 'app.modules.m3.tools:lost_found_publish'),
    ('lost_found.search_matches',
     '{"type":"object","required":["item_id"],"properties":{"item_id":{"type":"string","format":"uuid"},"top_k":{"type":"integer","minimum":1,"maximum":20,"default":5}}}'::jsonb,
     '{"type":"object","required":["matches"],"properties":{"matches":{"type":"array"}}}'::jsonb,
     '["community:read"]'::jsonb, 8000, true, false, 'app.modules.m3.tools:lost_found_matches'),
    ('governance.check_content',
     '{"type":"object","required":["text","scope"],"properties":{"text":{"type":"string","maxLength":10000},"scope":{"enum":["tool_input","tool_output","agent_context"]}}}'::jsonb,
     '{"type":"object","required":["decision","hits"],"properties":{"decision":{"enum":["allow","review","block"]},"hits":{"type":"array"}}}'::jsonb,
     '["moderation:execute"]'::jsonb, 3000, true, false, 'app.modules.m4.tools:governance_check_content'),
    ('governance.authorize_tool',
     '{"type":"object","required":["user_id","agent_code","tool_name"],"properties":{"user_id":{"type":"string","format":"uuid"},"agent_code":{"type":"string"},"tool_name":{"type":"string"},"resource":{"type":"object"}}}'::jsonb,
     '{"type":"object","required":["allowed"],"properties":{"allowed":{"type":"boolean"},"reason_code":{"type":["string","null"]}}}'::jsonb,
     '["agent:run"]'::jsonb, 3000, true, false, 'app.modules.m4.tools:governance_authorize_tool'),
    ('governance.write_audit',
     '{"type":"object","required":["action","request_id","result"],"properties":{"action":{"type":"string"},"request_id":{"type":"string"},"result":{"enum":["success","failure","denied"]},"metadata":{"type":"object"}}}'::jsonb,
     '{"type":"object","required":["audit_id"],"properties":{"audit_id":{"type":"string","format":"uuid"}}}'::jsonb,
     '["audit:write"]'::jsonb, 3000, false, false, 'app.modules.m4.tools:governance_write_audit')
)
INSERT INTO agent_platform.tool_versions
    (tool_id, version, input_schema, output_schema, required_permissions, timeout_ms,
     idempotent, requires_approval, implementation_ref, status)
SELECT td.id, '1.0.0', s.input_schema, s.output_schema, s.permissions, s.timeout_ms,
       s.idempotent, s.approval, s.impl, 'active'
FROM specs s
JOIN agent_platform.tool_definitions td ON td.name = s.name
ON CONFLICT (tool_id, version) DO UPDATE
SET input_schema = EXCLUDED.input_schema,
    output_schema = EXCLUDED.output_schema,
    required_permissions = EXCLUDED.required_permissions,
    timeout_ms = EXCLUDED.timeout_ms,
    idempotent = EXCLUDED.idempotent,
    requires_approval = EXCLUDED.requires_approval,
    implementation_ref = EXCLUDED.implementation_ref,
    status = EXCLUDED.status;

WITH specs(code, tools) AS (
    VALUES
    ('supervisor', '["governance.check_content","governance.authorize_tool","governance.write_audit"]'::jsonb),
    ('knowledge_agent', '["knowledge.search","knowledge.answer","governance.check_content","governance.write_audit"]'::jsonb),
    ('service_agent', '["service.get_guide","work_order.create","work_order.get","electricity.get_balance","electricity.create_topup_request","governance.authorize_tool","governance.write_audit"]'::jsonb),
    ('community_agent', '["event.search","event.register","lost_found.publish","lost_found.search_matches","governance.authorize_tool","governance.write_audit"]'::jsonb),
    ('governance_agent', '["governance.check_content","governance.authorize_tool","governance.write_audit"]'::jsonb),
    ('modelops_agent', '[]'::jsonb)
)
INSERT INTO agent_platform.agent_versions
    (agent_id, version, system_prompt, output_schema, tool_allowlist, status)
SELECT ad.id, '1.0.0',
       '只执行职责范围内任务；不得绕过权限、人工确认和审计；输出必须符合结构化契约。',
       '{"type":"object","required":["status","data"],"properties":{"status":{"enum":["succeeded","failed","needs_approval"]},"data":{"type":"object"}}}'::jsonb,
       s.tools, 'active'
FROM specs s
JOIN agent_platform.agent_definitions ad ON ad.code = s.code
ON CONFLICT (agent_id, version) DO UPDATE
SET system_prompt = EXCLUDED.system_prompt,
    output_schema = EXCLUDED.output_schema,
    tool_allowlist = EXCLUDED.tool_allowlist,
    status = EXCLUDED.status;

INSERT INTO agent_platform.model_versions
    (name, purpose, provider, base_model, version, quantization, config, metrics, status, activated_at)
VALUES
    ('deepseek-complex-generator', 'complex_generation', 'deepseek', 'deepseek-v4-pro', 'api-2026-07', NULL,
     '{"api_key_env":"DEEPSEEK_API_KEY","timeout_seconds":60,"max_retries":2}'::jsonb,
     '{}'::jsonb, 'active', now()),
    ('local-agent-router', 'agent_router', 'rule', 'rule-router-v1', '1.0.0', NULL,
     '{"fallback_provider":"deepseek","confidence_threshold":0.72}'::jsonb,
     '{"demo_intent_accuracy":1.0}'::jsonb, 'active', now()),
    ('local-rag-reranker', 'rag_reranker', 'local', 'BAAI/bge-reranker-base', 'demo-1', 'int8',
     '{"device":"cpu","max_candidates":20}'::jsonb,
     '{}'::jsonb, 'active', now()),
    ('local-embedding', 'embedding', 'local', 'BAAI/bge-small-zh-v1.5', 'demo-1', 'int8',
     '{"device":"cpu","dimensions":512}'::jsonb,
     '{}'::jsonb, 'active', now())
ON CONFLICT (name, version) DO UPDATE
SET config = EXCLUDED.config,
    metrics = EXCLUDED.metrics,
    status = EXCLUDED.status,
    activated_at = EXCLUDED.activated_at;

COMMIT;
