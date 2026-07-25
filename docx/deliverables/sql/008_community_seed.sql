BEGIN;

INSERT INTO platform.app_configs
    (key, namespace, value, value_type, description, editable)
VALUES
('community.post_max_chars', 'community', '5000'::jsonb, 'integer', '帖子正文最大字符数', true),
('community.comment_max_chars', 'community', '1000'::jsonb, 'integer', '评论正文最大字符数', true),
('community.event_max_capacity', 'community', '10000'::jsonb, 'integer', '活动容量硬上限', true),
('community.match.category_weight', 'community', '0.35'::jsonb, 'number', '失物匹配类别权重', true),
('community.match.location_weight', 'community', '0.25'::jsonb, 'number', '失物匹配地点权重', true),
('community.match.time_weight', 'community', '0.20'::jsonb, 'number', '失物匹配时间权重', true),
('community.match.keyword_weight', 'community', '0.20'::jsonb, 'number', '失物匹配关键词权重', true),
('community.match.threshold', 'community', '0.55'::jsonb, 'number', '失物候选最低匹配分', true),
('community.match.time_window_days', 'community', '30'::jsonb, 'integer', '候选时间窗口天数', true)
ON CONFLICT (key) DO UPDATE SET
    namespace = EXCLUDED.namespace,
    value = EXCLUDED.value,
    value_type = EXCLUDED.value_type,
    description = EXCLUDED.description,
    editable = EXCLUDED.editable;

-- 话题、帖子、活动、失物和认领演示数据由 Python seed_demo 创建：
-- 需要绑定 platform.users 的真实演示 UUID、调用 M4 内容扫描，并使用应用密钥加密联系方式。

COMMIT;
