BEGIN;

INSERT INTO campus_service.campuses (code, name, address, sort_order) VALUES
('main', '主校区', '示例市大学路 1 号', 10),
('east', '东校区', '示例市学府路 8 号', 20)
ON CONFLICT (code) DO UPDATE SET
    name = EXCLUDED.name,
    address = EXCLUDED.address,
    sort_order = EXCLUDED.sort_order,
    enabled = true;

INSERT INTO campus_service.departments (id, code, name, description) VALUES
('10000000-0000-4000-8000-000000000001', 'student_affairs', '学生事务中心', '学生证明、奖助与综合事务'),
('10000000-0000-4000-8000-000000000002', 'logistics', '后勤保障中心', '宿舍、维修与校园生活保障'),
('10000000-0000-4000-8000-000000000003', 'academic_affairs', '教务处', '学籍、课程与教学事务')
ON CONFLICT (code) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    enabled = true;

INSERT INTO campus_service.department_contacts
    (id, department_id, campus_code, contact_name, office_name, phone, email,
     location, office_hours, valid_from, valid_until, enabled)
VALUES
('20000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000001',
 'main', '王老师', '学生事务综合窗口', '010-55550001', 'student@example.edu.cn',
 '行政楼一层 101', '工作日 09:00-12:00，14:00-17:00', '2026-01-01', NULL, true),
('20000000-0000-4000-8000-000000000002', '10000000-0000-4000-8000-000000000002',
 'main', NULL, '后勤报修值班室', '010-55550002', NULL,
 '后勤楼 105', '每日 08:00-20:00', '2026-01-01', NULL, true),
('20000000-0000-4000-8000-000000000003', '10000000-0000-4000-8000-000000000003',
 'east', '李老师', '教务服务窗口', '010-55550003', 'academic@example.edu.cn',
 '东校区综合楼 203', '工作日 09:00-16:30', '2026-01-01', NULL, true)
ON CONFLICT (id) DO UPDATE SET
    office_name = EXCLUDED.office_name,
    phone = EXCLUDED.phone,
    email = EXCLUDED.email,
    location = EXCLUDED.location,
    office_hours = EXCLUDED.office_hours,
    valid_from = EXCLUDED.valid_from,
    valid_until = EXCLUDED.valid_until,
    enabled = EXCLUDED.enabled;

INSERT INTO campus_service.guide_categories (id, code, name, sort_order) VALUES
('30000000-0000-4000-8000-000000000001', 'student_certificate', '证明办理', 10),
('30000000-0000-4000-8000-000000000002', 'academic_record', '学籍教务', 20),
('30000000-0000-4000-8000-000000000003', 'campus_life', '校园生活', 30)
ON CONFLICT (code) DO UPDATE SET
    name = EXCLUDED.name,
    sort_order = EXCLUDED.sort_order,
    enabled = true;

INSERT INTO campus_service.service_guides
    (id, code, category_id, department_id, title, summary, location,
     service_hours, source_url, status, published_at, valid_until, version)
VALUES
('40000000-0000-4000-8000-000000000001', 'enrollment_certificate',
 '30000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000001',
 '在读证明办理', '面向在校学生开具中文或英文在读证明。',
 '行政楼一层 101', '工作日 09:00-12:00，14:00-17:00',
 'https://example.edu.cn/guides/enrollment-certificate', 'published',
 '2026-01-10T00:00:00Z', '2026-12-31', 1),
('40000000-0000-4000-8000-000000000002', 'student_card_replacement',
 '30000000-0000-4000-8000-000000000003', '10000000-0000-4000-8000-000000000001',
 '学生证补办', '学生证遗失或损坏后的挂失与补办流程。',
 '行政楼一层 101', '工作日 09:00-16:30',
 'https://example.edu.cn/guides/student-card', 'published',
 '2026-01-10T00:00:00Z', '2026-12-31', 1)
ON CONFLICT (code) DO UPDATE SET
    title = EXCLUDED.title,
    summary = EXCLUDED.summary,
    location = EXCLUDED.location,
    service_hours = EXCLUDED.service_hours,
    source_url = EXCLUDED.source_url,
    status = EXCLUDED.status,
    published_at = EXCLUDED.published_at,
    valid_until = EXCLUDED.valid_until;

INSERT INTO campus_service.guide_applicabilities
    (guide_id, campus_code, student_type, notes)
VALUES
('40000000-0000-4000-8000-000000000001', 'main', 'undergraduate', '主校区本科生'),
('40000000-0000-4000-8000-000000000001', 'main', 'postgraduate', '主校区研究生'),
('40000000-0000-4000-8000-000000000001', 'east', 'undergraduate', '东校区本科生可线上申请'),
('40000000-0000-4000-8000-000000000002', 'main', 'all', '主校区在校生')
ON CONFLICT (guide_id, campus_code, student_type) DO UPDATE SET
    notes = EXCLUDED.notes;

INSERT INTO campus_service.guide_materials
    (id, guide_id, name, description, required, copies, condition, sort_order)
VALUES
('50000000-0000-4000-8000-000000000001', '40000000-0000-4000-8000-000000000001',
 '本人有效学生证或校园卡', '用于线下核验身份。', true, 1, '{}'::jsonb, 10),
('50000000-0000-4000-8000-000000000002', '40000000-0000-4000-8000-000000000001',
 '英文姓名确认页', '仅申请英文证明时需要。', false, 1,
 '{"student_types":["international"]}'::jsonb, 20),
('50000000-0000-4000-8000-000000000003', '40000000-0000-4000-8000-000000000002',
 '证件照', '一寸近期证件照。', true, 1, '{}'::jsonb, 10),
('50000000-0000-4000-8000-000000000004', '40000000-0000-4000-8000-000000000002',
 '损坏的原学生证', '仅学生证损坏时提交。', false, 1, '{}'::jsonb, 20)
ON CONFLICT (id) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    required = EXCLUDED.required,
    copies = EXCLUDED.copies,
    condition = EXCLUDED.condition,
    sort_order = EXCLUDED.sort_order;

INSERT INTO campus_service.guide_steps
    (id, guide_id, step_no, title, description, location, estimated_minutes)
VALUES
('60000000-0000-4000-8000-000000000001', '40000000-0000-4000-8000-000000000001',
 1, '准备材料', '确认申请语言与份数，准备身份凭证。', NULL, 5),
('60000000-0000-4000-8000-000000000002', '40000000-0000-4000-8000-000000000001',
 2, '提交申请', '前往学生事务综合窗口提交申请。', '行政楼一层 101', 10),
('60000000-0000-4000-8000-000000000003', '40000000-0000-4000-8000-000000000001',
 3, '领取证明', '按受理回执约定时间领取。', '行政楼一层 101', 5),
('60000000-0000-4000-8000-000000000004', '40000000-0000-4000-8000-000000000002',
 1, '挂失', '先在学生事务窗口办理学生证挂失。', '行政楼一层 101', 10),
('60000000-0000-4000-8000-000000000005', '40000000-0000-4000-8000-000000000002',
 2, '提交补办材料', '提交证件照并核验本人身份。', '行政楼一层 101', 10)
ON CONFLICT (guide_id, step_no) DO UPDATE SET
    title = EXCLUDED.title,
    description = EXCLUDED.description,
    location = EXCLUDED.location,
    estimated_minutes = EXCLUDED.estimated_minutes;

-- 工单演示数据由 Python seed_demo 脚本创建，以便绑定当前演示用户 UUID。

COMMIT;
