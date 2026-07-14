BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS campus_service;

CREATE OR REPLACE FUNCTION campus_service.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

CREATE TABLE IF NOT EXISTS campus_service.campuses (
    code varchar(30) PRIMARY KEY,
    name varchar(100) NOT NULL,
    address varchar(300) NULL,
    enabled boolean NOT NULL DEFAULT true,
    sort_order integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_campuses_code CHECK (code ~ '^[a-z][a-z0-9_-]{1,29}$'),
    CONSTRAINT ck_campuses_sort_order CHECK (sort_order >= 0)
);

CREATE TABLE IF NOT EXISTS campus_service.departments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code varchar(50) NOT NULL UNIQUE,
    name varchar(100) NOT NULL,
    description varchar(500) NULL,
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_departments_code CHECK (code ~ '^[a-z][a-z0-9_]{2,49}$')
);

CREATE TABLE IF NOT EXISTS campus_service.department_contacts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    department_id uuid NOT NULL
        REFERENCES campus_service.departments(id) ON DELETE CASCADE,
    campus_code varchar(30) NOT NULL
        REFERENCES campus_service.campuses(code) ON DELETE RESTRICT,
    contact_name varchar(50) NULL,
    office_name varchar(100) NOT NULL,
    phone varchar(30) NULL,
    email varchar(254) NULL,
    location varchar(200) NOT NULL,
    office_hours varchar(200) NULL,
    valid_from date NOT NULL DEFAULT CURRENT_DATE,
    valid_until date NULL,
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_department_contacts_channel
        CHECK (phone IS NOT NULL OR email IS NOT NULL),
    CONSTRAINT ck_department_contacts_validity
        CHECK (valid_until IS NULL OR valid_until >= valid_from)
);

CREATE INDEX IF NOT EXISTS ix_department_contacts_active
    ON campus_service.department_contacts
       (department_id, campus_code, valid_until)
    WHERE enabled = true;

CREATE TABLE IF NOT EXISTS campus_service.guide_categories (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code varchar(50) NOT NULL UNIQUE,
    name varchar(100) NOT NULL,
    sort_order integer NOT NULL DEFAULT 0,
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_guide_categories_code CHECK (code ~ '^[a-z][a-z0-9_]{2,49}$'),
    CONSTRAINT ck_guide_categories_sort_order CHECK (sort_order >= 0)
);

CREATE TABLE IF NOT EXISTS campus_service.service_guides (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code varchar(60) NOT NULL UNIQUE,
    category_id uuid NOT NULL
        REFERENCES campus_service.guide_categories(id) ON DELETE RESTRICT,
    department_id uuid NOT NULL
        REFERENCES campus_service.departments(id) ON DELETE RESTRICT,
    title varchar(200) NOT NULL,
    summary varchar(500) NOT NULL,
    location varchar(300) NULL,
    service_hours varchar(200) NULL,
    source_url varchar(500) NULL,
    status varchar(16) NOT NULL DEFAULT 'published',
    published_at timestamptz NULL,
    valid_until date NULL,
    version integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_service_guides_code CHECK (code ~ '^[a-z][a-z0-9_]{2,59}$'),
    CONSTRAINT ck_service_guides_status
        CHECK (status IN ('draft', 'published', 'archived')),
    CONSTRAINT ck_service_guides_publish_state
        CHECK (status <> 'published' OR published_at IS NOT NULL),
    CONSTRAINT ck_service_guides_version CHECK (version >= 1)
);

CREATE INDEX IF NOT EXISTS ix_service_guides_listing
    ON campus_service.service_guides (status, category_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS ix_service_guides_department
    ON campus_service.service_guides (department_id, status);

CREATE TABLE IF NOT EXISTS campus_service.guide_applicabilities (
    guide_id uuid NOT NULL
        REFERENCES campus_service.service_guides(id) ON DELETE CASCADE,
    campus_code varchar(30) NOT NULL
        REFERENCES campus_service.campuses(code) ON DELETE RESTRICT,
    student_type varchar(30) NOT NULL,
    notes varchar(300) NULL,
    PRIMARY KEY (guide_id, campus_code, student_type),
    CONSTRAINT ck_guide_applicabilities_student_type
        CHECK (student_type IN ('undergraduate', 'postgraduate', 'international', 'all'))
);

CREATE INDEX IF NOT EXISTS ix_guide_applicabilities_audience
    ON campus_service.guide_applicabilities (campus_code, student_type, guide_id);

CREATE TABLE IF NOT EXISTS campus_service.guide_materials (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    guide_id uuid NOT NULL
        REFERENCES campus_service.service_guides(id) ON DELETE CASCADE,
    name varchar(200) NOT NULL,
    description varchar(500) NULL,
    required boolean NOT NULL DEFAULT true,
    copies integer NOT NULL DEFAULT 1,
    condition jsonb NOT NULL DEFAULT '{}'::jsonb,
    sort_order integer NOT NULL DEFAULT 0,
    CONSTRAINT ck_guide_materials_copies CHECK (copies BETWEEN 0 AND 20),
    CONSTRAINT ck_guide_materials_condition CHECK (jsonb_typeof(condition) = 'object'),
    CONSTRAINT ck_guide_materials_sort_order CHECK (sort_order >= 0)
);

CREATE INDEX IF NOT EXISTS ix_guide_materials_guide
    ON campus_service.guide_materials (guide_id, sort_order, id);
CREATE INDEX IF NOT EXISTS ix_guide_materials_condition_gin
    ON campus_service.guide_materials USING gin (condition);

CREATE TABLE IF NOT EXISTS campus_service.guide_steps (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    guide_id uuid NOT NULL
        REFERENCES campus_service.service_guides(id) ON DELETE CASCADE,
    step_no integer NOT NULL,
    title varchar(200) NOT NULL,
    description text NOT NULL,
    location varchar(300) NULL,
    estimated_minutes integer NULL,
    UNIQUE (guide_id, step_no),
    CONSTRAINT ck_guide_steps_step_no CHECK (step_no >= 1),
    CONSTRAINT ck_guide_steps_estimated_minutes
        CHECK (estimated_minutes IS NULL OR estimated_minutes BETWEEN 1 AND 10080)
);

CREATE TABLE IF NOT EXISTS campus_service.work_orders (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    order_no varchar(32) NOT NULL UNIQUE,
    created_by uuid NOT NULL,
    campus_code varchar(30) NOT NULL
        REFERENCES campus_service.campuses(code) ON DELETE RESTRICT,
    dormitory_area varchar(100) NOT NULL,
    building varchar(50) NOT NULL,
    room varchar(30) NOT NULL,
    fault_category varchar(30) NOT NULL,
    description varchar(1000) NOT NULL,
    preferred_start_at timestamptz NOT NULL,
    preferred_end_at timestamptz NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'submitted',
    assigned_to uuid NULL,
    assigned_department_id uuid NULL
        REFERENCES campus_service.departments(id) ON DELETE SET NULL,
    rejection_reason varchar(500) NULL,
    completion_note varchar(1000) NULL,
    version integer NOT NULL DEFAULT 1,
    submitted_at timestamptz NOT NULL DEFAULT now(),
    accepted_at timestamptz NULL,
    processing_at timestamptz NULL,
    completed_at timestamptz NULL,
    cancelled_at timestamptz NULL,
    rejected_at timestamptz NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_work_orders_fault_category
        CHECK (fault_category IN ('electric', 'plumbing', 'network', 'furniture', 'door_window', 'other')),
    CONSTRAINT ck_work_orders_description_length
        CHECK (char_length(description) BETWEEN 10 AND 1000),
    CONSTRAINT ck_work_orders_preferred_window
        CHECK (preferred_end_at > preferred_start_at),
    CONSTRAINT ck_work_orders_status
        CHECK (status IN ('submitted', 'accepted', 'processing', 'completed', 'cancelled', 'rejected')),
    CONSTRAINT ck_work_orders_version CHECK (version >= 1),
    CONSTRAINT ck_work_orders_terminal_reason CHECK (
        (status <> 'rejected' OR rejection_reason IS NOT NULL)
        AND (status <> 'completed' OR completion_note IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS ix_work_orders_owner
    ON campus_service.work_orders (created_by, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_work_orders_staff_queue
    ON campus_service.work_orders
       (campus_code, dormitory_area, status, submitted_at ASC);
CREATE INDEX IF NOT EXISTS ix_work_orders_assignee
    ON campus_service.work_orders (assigned_to, status, updated_at DESC)
    WHERE assigned_to IS NOT NULL;

CREATE TABLE IF NOT EXISTS campus_service.work_order_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    work_order_id uuid NOT NULL
        REFERENCES campus_service.work_orders(id) ON DELETE CASCADE,
    sequence_no integer NOT NULL,
    event_type varchar(40) NOT NULL,
    from_status varchar(16) NULL,
    to_status varchar(16) NOT NULL,
    actor_user_id uuid NOT NULL,
    actor_role varchar(50) NOT NULL,
    reason varchar(500) NULL,
    snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (work_order_id, sequence_no),
    CONSTRAINT ck_work_order_events_sequence CHECK (sequence_no >= 1),
    CONSTRAINT ck_work_order_events_snapshot CHECK (jsonb_typeof(snapshot) = 'object'),
    CONSTRAINT ck_work_order_events_to_status
        CHECK (to_status IN ('submitted', 'accepted', 'processing', 'completed', 'cancelled', 'rejected')),
    CONSTRAINT ck_work_order_events_from_status
        CHECK (from_status IS NULL OR from_status IN ('submitted', 'accepted', 'processing', 'completed', 'cancelled', 'rejected'))
);

CREATE INDEX IF NOT EXISTS ix_work_order_events_timeline
    ON campus_service.work_order_events (work_order_id, sequence_no);

CREATE TABLE IF NOT EXISTS campus_service.work_order_ratings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    work_order_id uuid NOT NULL UNIQUE
        REFERENCES campus_service.work_orders(id) ON DELETE CASCADE,
    user_id uuid NOT NULL,
    score smallint NOT NULL,
    comment varchar(500) NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_work_order_ratings_score CHECK (score BETWEEN 1 AND 5)
);

DROP TRIGGER IF EXISTS trg_campuses_updated_at ON campus_service.campuses;
CREATE TRIGGER trg_campuses_updated_at
BEFORE UPDATE ON campus_service.campuses
FOR EACH ROW EXECUTE FUNCTION campus_service.set_updated_at();

DROP TRIGGER IF EXISTS trg_departments_updated_at ON campus_service.departments;
CREATE TRIGGER trg_departments_updated_at
BEFORE UPDATE ON campus_service.departments
FOR EACH ROW EXECUTE FUNCTION campus_service.set_updated_at();

DROP TRIGGER IF EXISTS trg_department_contacts_updated_at ON campus_service.department_contacts;
CREATE TRIGGER trg_department_contacts_updated_at
BEFORE UPDATE ON campus_service.department_contacts
FOR EACH ROW EXECUTE FUNCTION campus_service.set_updated_at();

DROP TRIGGER IF EXISTS trg_service_guides_updated_at ON campus_service.service_guides;
CREATE TRIGGER trg_service_guides_updated_at
BEFORE UPDATE ON campus_service.service_guides
FOR EACH ROW EXECUTE FUNCTION campus_service.set_updated_at();

DROP TRIGGER IF EXISTS trg_work_orders_updated_at ON campus_service.work_orders;
CREATE TRIGGER trg_work_orders_updated_at
BEFORE UPDATE ON campus_service.work_orders
FOR EACH ROW EXECUTE FUNCTION campus_service.set_updated_at();

COMMENT ON SCHEMA campus_service IS 'M2 校园服务中心数据域';
COMMENT ON TABLE campus_service.guide_materials IS 'condition 使用白名单条件：campus_codes/student_types，不执行动态表达式';
COMMENT ON TABLE campus_service.work_orders IS 'created_by/assigned_to 为 platform.users 的逻辑引用，不建立跨 Schema 外键';
COMMENT ON TABLE campus_service.work_order_events IS '工单不可变事件时间线；状态变更和事件必须在同一事务完成';
COMMENT ON TABLE campus_service.work_order_ratings IS '每个已完成工单只能由创建者评价一次';

COMMIT;
