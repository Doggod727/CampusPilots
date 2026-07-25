BEGIN;

INSERT INTO platform.permissions (code, name, module, description) VALUES
('user:read', '查看用户', 'platform', '查看用户列表与详情'),
('user:write', '管理用户', 'platform', '创建、编辑和启停用户'),
('user:role:assign', '分配用户角色', 'platform', '全量替换用户角色'),
('role:read', '查看角色权限', 'platform', '查看角色和权限字典'),
('role:write', '管理角色', 'platform', '创建、编辑和删除自定义角色'),
('role:permission:assign', '分配角色权限', 'platform', '全量替换角色权限'),
('sensitive_word:read', '查看敏感词', 'platform', '查看敏感词规则'),
('sensitive_word:write', '管理敏感词', 'platform', '创建和删除敏感词规则'),
('moderation:read', '查看审核队列', 'platform', '查看授权范围内审核案件'),
('moderation:decide', '处理审核案件', 'platform', '批准、拒绝或升级审核案件'),
('audit:read', '查看审计日志', 'platform', '查看脱敏审计日志'),
('config:read', '查看系统配置', 'platform', '查看非密钥业务配置'),
('config:write', '修改系统配置', 'platform', '修改允许编辑的业务配置'),
('dashboard:read', '查看运营看板', 'platform', '查看基础运营指标'),
('knowledge:read', '查看知识库', 'ai_knowledge', '查看知识库、文档和任务'),
('knowledge:write', '管理知识库', 'ai_knowledge', '创建、编辑、上传和删除知识资产'),
('knowledge:publish', '发布知识文档', 'ai_knowledge', '发布或停用可检索文档'),
('work_order:read', '查看工单', 'campus_service', '按资源范围查看工单'),
('work_order:create', '创建工单', 'campus_service', '学生创建本人报修工单'),
('work_order:transition', '流转工单', 'campus_service', '处理员执行合法状态迁移'),
('community:read', '查看社区', 'community', '查看公开社区内容'),
('community:write', '发布社区内容', 'community', '创建帖子、评论、活动和失物信息'),
('community:moderate', '管理社区内容', 'community', '执行审核结果和运营操作'),
('community:anonymous_identity:read', '反查匿名身份', 'community', '基于明确事由反查匿名内容作者并强制审计')
ON CONFLICT (code) DO UPDATE SET
    name = EXCLUDED.name,
    module = EXCLUDED.module,
    description = EXCLUDED.description;

INSERT INTO platform.roles (code, name, description, is_system) VALUES
('super_admin', '超级管理员', '演示环境全权限账号', true),
('knowledge_admin', '知识库管理员', '维护和发布校园知识文档', true),
('service_staff', '服务处理员', '处理校园服务和报修工单', true),
('community_operator', '社区运营员', '社区审核与内容运营', true),
('student', '普通学生', '学生端基础功能', true)
ON CONFLICT (code) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    is_system = EXCLUDED.is_system;

INSERT INTO platform.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM platform.roles r
CROSS JOIN platform.permissions p
WHERE r.code = 'super_admin'
ON CONFLICT DO NOTHING;

INSERT INTO platform.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM platform.roles r
JOIN platform.permissions p
  ON p.code IN ('knowledge:read', 'knowledge:write', 'knowledge:publish',
                'config:read', 'dashboard:read')
WHERE r.code = 'knowledge_admin'
ON CONFLICT DO NOTHING;

INSERT INTO platform.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM platform.roles r
JOIN platform.permissions p
  ON p.code IN ('work_order:read', 'work_order:transition',
                'dashboard:read')
WHERE r.code = 'service_staff'
ON CONFLICT DO NOTHING;

INSERT INTO platform.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM platform.roles r
JOIN platform.permissions p
  ON p.code IN ('community:read', 'community:write', 'community:moderate',
                'moderation:read', 'moderation:decide', 'dashboard:read')
WHERE r.code = 'community_operator'
ON CONFLICT DO NOTHING;

INSERT INTO platform.role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM platform.roles r
JOIN platform.permissions p
  ON p.code IN ('work_order:read', 'work_order:create',
                'community:read', 'community:write')
WHERE r.code = 'student'
ON CONFLICT DO NOTHING;

INSERT INTO platform.app_configs
    (key, namespace, value, value_type, description, editable)
VALUES
('auth.max_failed_logins', 'auth', '5'::jsonb, 'integer', '触发临时锁定的连续失败次数', true),
('auth.lock_minutes', 'auth', '15'::jsonb, 'integer', '登录锁定分钟数', true),
('moderation.default_action', 'moderation', '"allow"'::jsonb, 'string', '社区内容未命中规则时的默认动作；MVP 为低风险直接发布', true),
('moderation.high_risk_auto_publish', 'moderation', 'false'::jsonb, 'boolean', '高风险内容是否允许自动发布，MVP 固定 false', false),
('dashboard.default_days', 'dashboard', '7'::jsonb, 'integer', '看板默认时间范围', true)
ON CONFLICT (key) DO UPDATE SET
    namespace = EXCLUDED.namespace,
    value = EXCLUDED.value,
    value_type = EXCLUDED.value_type,
    description = EXCLUDED.description,
    editable = EXCLUDED.editable;

-- 演示用户必须由 Python seed_demo 脚本创建，以便使用 Argon2 生成密码哈希。
-- 禁止在 SQL 文件中写入明文演示密码或预计算的共享生产密码。

COMMIT;
