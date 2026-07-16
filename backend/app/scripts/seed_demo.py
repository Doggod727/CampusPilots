import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import delete, literal, select, text, true
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.modules.campus_service.models import (
    Campus,
    Department,
    DepartmentContact,
    ElectricityAccount,
    ElectricityAccountMember,
    GuideApplicability,
    GuideCategory,
    GuideMaterial,
    GuideStep,
    ServiceGuide,
)
from app.modules.platform.models import (
    AppConfig,
    Permission,
    Role,
    RolePermission,
    User,
    UserRole,
)
from app.modules.platform.passwords import PasswordHasher


@dataclass(frozen=True)
class PermissionSeed:
    code: str
    name: str
    module: str
    description: str


@dataclass(frozen=True)
class RoleSeed:
    code: str
    name: str
    description: str


@dataclass(frozen=True)
class DemoAccount:
    username: str
    display_name: str
    email: str
    department: str
    role_code: str


@dataclass(frozen=True)
class CampusSeed:
    code: str
    name: str
    address: str
    sort_order: int


@dataclass(frozen=True)
class DepartmentSeed:
    id: UUID
    code: str
    name: str
    description: str


@dataclass(frozen=True)
class ContactSeed:
    id: UUID
    department_code: str
    campus_code: str
    contact_name: str | None
    office_name: str
    phone: str | None
    email: str | None
    location: str
    office_hours: str


@dataclass(frozen=True)
class GuideCategorySeed:
    id: UUID
    code: str
    name: str
    sort_order: int


@dataclass(frozen=True)
class ServiceGuideSeed:
    id: UUID
    code: str
    category_code: str
    department_code: str
    title: str
    summary: str
    location: str
    service_hours: str
    source_url: str
    valid_until: date


@dataclass(frozen=True)
class GuideApplicabilitySeed:
    guide_code: str
    campus_code: str
    student_type: str
    notes: str


@dataclass(frozen=True)
class GuideMaterialSeed:
    id: UUID
    guide_code: str
    name: str
    description: str
    required: bool
    copies: int
    condition: dict[str, list[str]]
    sort_order: int


@dataclass(frozen=True)
class GuideStepSeed:
    id: UUID
    guide_code: str
    step_no: int
    title: str
    description: str
    location: str | None
    estimated_minutes: int


PERMISSIONS = (
    PermissionSeed("user:read", "查看用户", "platform", "查看用户列表与详情"),
    PermissionSeed("user:write", "管理用户", "platform", "创建、编辑和启停用户"),
    PermissionSeed("user:role:assign", "分配用户角色", "platform", "全量替换用户角色"),
    PermissionSeed("role:read", "查看角色权限", "platform", "查看角色和权限字典"),
    PermissionSeed("role:write", "管理角色", "platform", "创建、编辑和删除自定义角色"),
    PermissionSeed("role:permission:assign", "分配角色权限", "platform", "全量替换角色权限"),
    PermissionSeed("sensitive_word:read", "查看敏感词", "platform", "查看敏感词规则"),
    PermissionSeed("sensitive_word:write", "管理敏感词", "platform", "创建和删除敏感词规则"),
    PermissionSeed("moderation:read", "查看审核队列", "platform", "查看授权范围内审核案件"),
    PermissionSeed("moderation:decide", "处理审核案件", "platform", "批准、拒绝或升级审核案件"),
    PermissionSeed("audit:read", "查看审计日志", "platform", "查看脱敏审计日志"),
    PermissionSeed("config:read", "查看系统配置", "platform", "查看非密钥业务配置"),
    PermissionSeed("config:write", "修改系统配置", "platform", "修改允许编辑的业务配置"),
    PermissionSeed("dashboard:read", "查看运营看板", "platform", "查看基础运营指标"),
    PermissionSeed("knowledge:read", "查看知识库", "ai_knowledge", "查看知识库、文档和任务"),
    PermissionSeed("knowledge:write", "管理知识库", "ai_knowledge", "创建、编辑、上传和删除知识资产"),
    PermissionSeed("knowledge:publish", "发布知识文档", "ai_knowledge", "发布或停用可检索文档"),
    PermissionSeed("work_order:read", "查看工单", "campus_service", "按资源范围查看工单"),
    PermissionSeed("work_order:create", "创建工单", "campus_service", "学生创建本人报修工单"),
    PermissionSeed("work_order:transition", "流转工单", "campus_service", "处理员执行合法状态迁移"),
    PermissionSeed("community:read", "查看社区", "community", "查看公开社区内容"),
    PermissionSeed("community:write", "发布社区内容", "community", "创建帖子、评论、活动和失物信息"),
    PermissionSeed("community:moderate", "管理社区内容", "community", "执行审核结果和运营操作"),
    PermissionSeed(
        "community:anonymous_identity:read",
        "反查匿名身份",
        "community",
        "基于明确事由反查匿名内容作者并强制审计",
    ),
    PermissionSeed("agent:run", "运行智能体", "agent_platform", "创建、取消和继续本人 Agent Run"),
    PermissionSeed("agent:run:read_own", "查看本人智能体运行", "agent_platform", "查看本人 Agent Run 与脱敏轨迹"),
    PermissionSeed("agent:run:read_all", "查看全部智能体运行", "agent_platform", "查看授权范围内全部 Agent Run"),
    PermissionSeed("agent:catalog:read", "查看智能体目录", "agent_platform", "查看启用 Agent 和公开版本信息"),
    PermissionSeed("tool:catalog:read", "查看工具目录", "agent_platform", "按权限查看 Tool Schema 和风险信息"),
    PermissionSeed("tool:catalog:write", "管理工具目录", "agent_platform", "启停或切换 Tool 版本"),
    PermissionSeed("dataset:read", "查看训练数据集", "modelops", "查看脱敏数据集和版本元数据"),
    PermissionSeed("dataset:write", "管理训练数据集", "modelops", "创建、校验、冻结和删除数据集版本"),
    PermissionSeed("training:run", "运行模型训练", "modelops", "创建和取消本地小模型训练任务"),
    PermissionSeed("training:read", "查看模型训练", "modelops", "查看训练状态、配置和脱敏日志"),
    PermissionSeed("model:read", "查看模型版本", "modelops", "查看模型注册表和评估指标"),
    PermissionSeed("model:write", "管理模型版本", "modelops", "注册、停用模型版本"),
    PermissionSeed("model:activate", "启用或回滚模型", "modelops", "经确认启用或回滚活动模型"),
    PermissionSeed("evaluation:run", "运行模型评估", "modelops", "运行 Agent、Tool、RAG 和模型评估"),
    PermissionSeed("evaluation:read", "查看模型评估", "modelops", "查看和比较评估报告"),
    PermissionSeed("moderation:execute", "执行内容治理", "platform", "供受信 Agent Runtime 执行输入输出治理"),
    PermissionSeed("audit:write", "写入审计事件", "platform", "供受信 Agent Runtime 写入结构化审计事件"),
    PermissionSeed("service:read", "查看校园服务", "campus_service", "查询有效办事指南"),
    PermissionSeed("electricity:read_own", "查看本人房间电费", "campus_service", "查看授权房间的 Mock 电费余额"),
    PermissionSeed(
        "electricity:topup_request:create",
        "创建电费模拟充值申请",
        "campus_service",
        "创建不涉及真实支付的模拟充值申请",
    ),
)

ROLES = (
    RoleSeed("super_admin", "超级管理员", "演示环境全权限账号"),
    RoleSeed("knowledge_admin", "知识库管理员", "维护和发布校园知识文档"),
    RoleSeed("service_staff", "服务处理员", "处理校园服务和报修工单"),
    RoleSeed("community_operator", "社区运营员", "社区审核与内容运营"),
    RoleSeed("student", "普通学生", "学生端基础功能"),
    RoleSeed("model_engineer", "模型工程管理员", "管理脱敏数据集、本地训练、模型版本和评估"),
    RoleSeed("agent_runtime", "智能体运行服务", "仅分配给受信服务身份，不分配给普通用户"),
)

ROLE_PERMISSION_CODES = {
    "super_admin": tuple(permission.code for permission in PERMISSIONS),
    "knowledge_admin": (
        "knowledge:read",
        "knowledge:write",
        "knowledge:publish",
        "config:read",
        "dashboard:read",
    ),
    "service_staff": (
        "work_order:read",
        "work_order:transition",
        "dashboard:read",
    ),
    "community_operator": (
        "community:read",
        "community:write",
        "community:moderate",
        "moderation:read",
        "moderation:decide",
        "dashboard:read",
    ),
    "student": (
        "work_order:read",
        "work_order:create",
        "community:read",
        "community:write",
        "agent:run",
        "agent:run:read_own",
        "agent:catalog:read",
        "tool:catalog:read",
        "service:read",
        "electricity:read_own",
        "electricity:topup_request:create",
    ),
    "model_engineer": (
        "agent:run:read_all",
        "agent:catalog:read",
        "tool:catalog:read",
        "dataset:read",
        "dataset:write",
        "training:run",
        "training:read",
        "model:read",
        "model:write",
        "model:activate",
        "evaluation:run",
        "evaluation:read",
    ),
    "agent_runtime": (
        "agent:run",
        "moderation:execute",
        "audit:write",
    ),
}

DEMO_ACCOUNTS = (
    DemoAccount("admin01", "平台管理员", "admin01@example.edu", "平台管理", "super_admin"),
    DemoAccount(
        "knowledge01",
        "知识库管理员",
        "knowledge01@example.edu",
        "图书馆",
        "knowledge_admin",
    ),
    DemoAccount(
        "service01",
        "服务处理员",
        "service01@example.edu",
        "后勤保障处",
        "service_staff",
    ),
    DemoAccount(
        "community01",
        "社区运营员",
        "community01@example.edu",
        "学生工作处",
        "community_operator",
    ),
    DemoAccount("student01", "张同学", "student01@example.edu", "计算机学院", "student"),
    DemoAccount("student02", "李同学", "student02@example.edu", "计算机学院", "student"),
)

CONFIGS = (
    ("auth.max_failed_logins", "auth", 5, "integer", "触发临时锁定的连续失败次数"),
    ("auth.lock_minutes", "auth", 15, "integer", "登录锁定分钟数"),
    ("agent.max_steps", "agent", 6, "integer", "单个 Agent Run 最大步骤数"),
    ("agent.max_specialists", "agent", 3, "integer", "单次运行最多专业 Agent 数"),
    ("agent.approval_ttl_seconds", "agent", 600, "integer", "写 Tool 确认有效期"),
    ("agent.parallelism", "agent", 3, "integer", "P1 并行 Agent 最大并发"),
    ("modelops.router_confidence", "modelops", 0.80, "number", "本地路由模型直接采用阈值"),
    ("modelops.reranker_enabled", "modelops", False, "boolean", "是否启用本地 RAG Reranker"),
    ("mcp.enabled", "agent", False, "boolean", "是否启用 P1 MCP Server"),
)

DEMO_ELECTRICITY_ROOM_ID = "21000000-0000-4000-8000-000000000001"

CAMPUS_SEEDS = (
    CampusSeed("main", "主校区", "示例市大学路 1 号", 10),
    CampusSeed("east", "东校区", "示例市学府路 8 号", 20),
)

DEPARTMENT_SEEDS = (
    DepartmentSeed(
        UUID("10000000-0000-4000-8000-000000000001"),
        "student_affairs",
        "学生事务中心",
        "学生证明、奖助与综合事务",
    ),
    DepartmentSeed(
        UUID("10000000-0000-4000-8000-000000000002"),
        "logistics",
        "后勤保障中心",
        "宿舍、维修与校园生活保障",
    ),
    DepartmentSeed(
        UUID("10000000-0000-4000-8000-000000000003"),
        "academic_affairs",
        "教务处",
        "学籍、课程与教学事务",
    ),
)

CONTACT_SEEDS = (
    ContactSeed(
        UUID("20000000-0000-4000-8000-000000000001"),
        "student_affairs",
        "main",
        "王老师",
        "学生事务综合窗口",
        "010-55550001",
        "student@example.edu.cn",
        "行政楼一层 101",
        "工作日 09:00-12:00，14:00-17:00",
    ),
    ContactSeed(
        UUID("20000000-0000-4000-8000-000000000002"),
        "logistics",
        "main",
        None,
        "后勤报修值班室",
        "010-55550002",
        None,
        "后勤楼 105",
        "每日 08:00-20:00",
    ),
    ContactSeed(
        UUID("20000000-0000-4000-8000-000000000003"),
        "academic_affairs",
        "east",
        "李老师",
        "教务服务窗口",
        "010-55550003",
        "academic@example.edu.cn",
        "东校区综合楼 203",
        "工作日 09:00-16:30",
    ),
)

GUIDE_CATEGORY_SEEDS = (
    GuideCategorySeed(
        UUID("30000000-0000-4000-8000-000000000001"),
        "student_certificate",
        "证明办理",
        10,
    ),
    GuideCategorySeed(
        UUID("30000000-0000-4000-8000-000000000002"),
        "academic_record",
        "学籍教务",
        20,
    ),
    GuideCategorySeed(
        UUID("30000000-0000-4000-8000-000000000003"),
        "campus_life",
        "校园生活",
        30,
    ),
)

SERVICE_GUIDE_SEEDS = (
    ServiceGuideSeed(
        UUID("40000000-0000-4000-8000-000000000001"),
        "enrollment_certificate",
        "student_certificate",
        "student_affairs",
        "在读证明办理",
        "面向在校学生开具中文或英文在读证明。",
        "行政楼一层 101",
        "工作日 09:00-12:00，14:00-17:00",
        "https://example.edu.cn/guides/enrollment-certificate",
        date(2026, 12, 31),
    ),
    ServiceGuideSeed(
        UUID("40000000-0000-4000-8000-000000000002"),
        "student_card_replacement",
        "campus_life",
        "student_affairs",
        "学生证补办",
        "学生证遗失或损坏后的挂失与补办流程。",
        "行政楼一层 101",
        "工作日 09:00-16:30",
        "https://example.edu.cn/guides/student-card",
        date(2026, 12, 31),
    ),
)

GUIDE_APPLICABILITY_SEEDS = (
    GuideApplicabilitySeed(
        "enrollment_certificate", "main", "undergraduate", "主校区本科生"
    ),
    GuideApplicabilitySeed(
        "enrollment_certificate", "main", "postgraduate", "主校区研究生"
    ),
    GuideApplicabilitySeed(
        "enrollment_certificate", "east", "undergraduate", "东校区本科生可线上申请"
    ),
    GuideApplicabilitySeed(
        "student_card_replacement", "main", "all", "主校区在校生"
    ),
)

GUIDE_MATERIAL_SEEDS = (
    GuideMaterialSeed(
        UUID("50000000-0000-4000-8000-000000000001"),
        "enrollment_certificate",
        "本人有效学生证或校园卡",
        "用于线下核验身份。",
        True,
        1,
        {},
        10,
    ),
    GuideMaterialSeed(
        UUID("50000000-0000-4000-8000-000000000002"),
        "enrollment_certificate",
        "英文姓名确认页",
        "仅申请英文证明时需要。",
        False,
        1,
        {"student_types": ["international"]},
        20,
    ),
    GuideMaterialSeed(
        UUID("50000000-0000-4000-8000-000000000003"),
        "student_card_replacement",
        "证件照",
        "一寸近期证件照。",
        True,
        1,
        {},
        10,
    ),
    GuideMaterialSeed(
        UUID("50000000-0000-4000-8000-000000000004"),
        "student_card_replacement",
        "损坏的原学生证",
        "仅学生证损坏时提交。",
        False,
        1,
        {},
        20,
    ),
)

GUIDE_STEP_SEEDS = (
    GuideStepSeed(
        UUID("60000000-0000-4000-8000-000000000001"),
        "enrollment_certificate",
        1,
        "准备材料",
        "确认申请语言与份数，准备身份凭证。",
        None,
        5,
    ),
    GuideStepSeed(
        UUID("60000000-0000-4000-8000-000000000002"),
        "enrollment_certificate",
        2,
        "提交申请",
        "前往学生事务综合窗口提交申请。",
        "行政楼一层 101",
        10,
    ),
    GuideStepSeed(
        UUID("60000000-0000-4000-8000-000000000003"),
        "enrollment_certificate",
        3,
        "领取证明",
        "按受理回执约定时间领取。",
        "行政楼一层 101",
        5,
    ),
    GuideStepSeed(
        UUID("60000000-0000-4000-8000-000000000004"),
        "student_card_replacement",
        1,
        "挂失",
        "先在学生事务窗口办理学生证挂失。",
        "行政楼一层 101",
        10,
    ),
    GuideStepSeed(
        UUID("60000000-0000-4000-8000-000000000005"),
        "student_card_replacement",
        2,
        "提交补办材料",
        "提交证件照并核验本人身份。",
        "行政楼一层 101",
        10,
    ),
)


def require_demo_seed_password(environ: Mapping[str, str] | None = None) -> str:
    password = (environ if environ is not None else os.environ).get(
        "DEMO_SEED_PASSWORD"
    )
    if not password:
        raise SystemExit("DEMO_SEED_PASSWORD must be set before seeding demo accounts.")
    return password


def _permission_upsert_statement():
    statement = insert(Permission).values(
        [
            {
                "code": permission.code,
                "name": permission.name,
                "module": permission.module,
                "description": permission.description,
            }
            for permission in PERMISSIONS
        ]
    )
    return statement.on_conflict_do_update(
        index_elements=[Permission.code],
        set_={
            "name": statement.excluded.name,
            "module": statement.excluded.module,
            "description": statement.excluded.description,
        },
    )


def _role_upsert_statement():
    statement = insert(Role).values(
        [
            {
                "code": role.code,
                "name": role.name,
                "description": role.description,
                "is_system": True,
            }
            for role in ROLES
        ]
    )
    return statement.on_conflict_do_update(
        index_elements=[Role.code],
        set_={
            "name": statement.excluded.name,
            "description": statement.excluded.description,
            "is_system": statement.excluded.is_system,
        },
    )


def _config_upsert_statement():
    statement = insert(AppConfig).values(
        [
            {
                "key": key,
                "namespace": namespace,
                "value": value,
                "value_type": value_type,
                "description": description,
                "editable": True,
            }
            for key, namespace, value, value_type, description in CONFIGS
        ]
    )
    return statement.on_conflict_do_update(
        index_elements=[AppConfig.key],
        set_={
            "namespace": statement.excluded.namespace,
            "value": statement.excluded.value,
            "value_type": statement.excluded.value_type,
            "description": statement.excluded.description,
            "editable": statement.excluded.editable,
        },
    )


def _role_permission_insert_statement(role_code: str, permission_codes: tuple[str, ...]):
    return (
        insert(RolePermission)
        .from_select(
            ["role_id", "permission_id"],
            select(Role.id, Permission.id)
            .join(Permission, true())
            .where(
                Role.code == role_code,
                Permission.code.in_(permission_codes),
            ),
        )
        .on_conflict_do_nothing()
    )


def _clear_role_permissions_statement(role_codes: tuple[str, ...]):
    return delete(RolePermission).where(
        RolePermission.role_id.in_(
            select(Role.id).where(Role.code.in_(role_codes))
        )
    )


def _user_upsert_statement(account: DemoAccount, password_hash: str):
    statement = insert(User).values(
        username=account.username,
        password_hash=password_hash,
        display_name=account.display_name,
        email=account.email,
        department=account.department,
        status="active",
        failed_login_count=0,
        locked_until=None,
        deleted_at=None,
    )
    return statement.on_conflict_do_update(
        index_elements=[User.username],
        set_={
            "password_hash": statement.excluded.password_hash,
            "display_name": statement.excluded.display_name,
            "email": statement.excluded.email,
            "department": statement.excluded.department,
            "status": statement.excluded.status,
            "failed_login_count": statement.excluded.failed_login_count,
            "locked_until": statement.excluded.locked_until,
            "deleted_at": statement.excluded.deleted_at,
        },
    )


def _user_role_insert_statement(account: DemoAccount):
    return (
        insert(UserRole)
        .from_select(
            ["user_id", "role_id"],
            select(User.id, Role.id)
            .join(Role, true())
            .where(
                User.username == account.username,
                Role.code == account.role_code,
            ),
        )
        .on_conflict_do_nothing()
    )


def _clear_user_roles_statement(usernames: tuple[str, ...]):
    return delete(UserRole).where(
        UserRole.user_id.in_(
            select(User.id).where(User.username.in_(usernames))
        )
    )


def _campus_upsert_statement():
    statement = insert(Campus).values(
        [
            {
                "code": seed.code,
                "name": seed.name,
                "address": seed.address,
                "enabled": True,
                "sort_order": seed.sort_order,
            }
            for seed in CAMPUS_SEEDS
        ]
    )
    return statement.on_conflict_do_update(
        index_elements=[Campus.code],
        set_={
            "name": statement.excluded.name,
            "address": statement.excluded.address,
            "enabled": statement.excluded.enabled,
            "sort_order": statement.excluded.sort_order,
        },
    )


def _department_upsert_statement():
    statement = insert(Department).values(
        [
            {
                "id": seed.id,
                "code": seed.code,
                "name": seed.name,
                "description": seed.description,
                "enabled": True,
            }
            for seed in DEPARTMENT_SEEDS
        ]
    )
    return statement.on_conflict_do_update(
        index_elements=[Department.code],
        set_={
            "name": statement.excluded.name,
            "description": statement.excluded.description,
            "enabled": True,
        },
    )


def _contact_upsert_statement(seed: ContactSeed):
    source = select(
        literal(seed.id),
        Department.id,
        literal(seed.campus_code),
        literal(seed.contact_name),
        literal(seed.office_name),
        literal(seed.phone),
        literal(seed.email),
        literal(seed.location),
        literal(seed.office_hours),
        literal(date(2026, 1, 1)),
        literal(None),
        literal(True),
    ).where(Department.code == seed.department_code)
    statement = insert(DepartmentContact).from_select(
        [
            "id", "department_id", "campus_code", "contact_name", "office_name",
            "phone", "email", "location", "office_hours", "valid_from", "valid_until",
            "enabled",
        ],
        source,
    )
    return statement.on_conflict_do_update(
        index_elements=[DepartmentContact.id],
        set_={
            "department_id": statement.excluded.department_id,
            "campus_code": statement.excluded.campus_code,
            "contact_name": statement.excluded.contact_name,
            "office_name": statement.excluded.office_name,
            "phone": statement.excluded.phone,
            "email": statement.excluded.email,
            "location": statement.excluded.location,
            "office_hours": statement.excluded.office_hours,
            "valid_from": statement.excluded.valid_from,
            "valid_until": statement.excluded.valid_until,
            "enabled": statement.excluded.enabled,
        },
    )


def _guide_category_upsert_statement():
    statement = insert(GuideCategory).values(
        [
            {
                "id": seed.id,
                "code": seed.code,
                "name": seed.name,
                "sort_order": seed.sort_order,
                "enabled": True,
            }
            for seed in GUIDE_CATEGORY_SEEDS
        ]
    )
    return statement.on_conflict_do_update(
        index_elements=[GuideCategory.code],
        set_={
            "name": statement.excluded.name,
            "sort_order": statement.excluded.sort_order,
            "enabled": True,
        },
    )


def _service_guide_upsert_statement(seed: ServiceGuideSeed):
    source = (
        select(
            literal(seed.id),
            literal(seed.code),
            GuideCategory.id,
            Department.id,
            literal(seed.title),
            literal(seed.summary),
            literal(seed.location),
            literal(seed.service_hours),
            literal(seed.source_url),
            literal("published"),
            literal(datetime(2026, 1, 10, tzinfo=timezone.utc)),
            literal(seed.valid_until),
            literal(1),
        )
        .select_from(GuideCategory)
        .join(Department, true())
        .where(
            GuideCategory.code == seed.category_code,
            Department.code == seed.department_code,
        )
    )
    statement = insert(ServiceGuide).from_select(
        [
            "id", "code", "category_id", "department_id", "title", "summary", "location",
            "service_hours", "source_url", "status", "published_at", "valid_until", "version",
        ],
        source,
    )
    return statement.on_conflict_do_update(
        index_elements=[ServiceGuide.code],
        set_={
            "category_id": statement.excluded.category_id,
            "department_id": statement.excluded.department_id,
            "title": statement.excluded.title,
            "summary": statement.excluded.summary,
            "location": statement.excluded.location,
            "service_hours": statement.excluded.service_hours,
            "source_url": statement.excluded.source_url,
            "status": statement.excluded.status,
            "published_at": statement.excluded.published_at,
            "valid_until": statement.excluded.valid_until,
        },
    )


def _guide_applicability_upsert_statement(seed: GuideApplicabilitySeed):
    source = select(
        ServiceGuide.id,
        literal(seed.campus_code),
        literal(seed.student_type),
        literal(seed.notes),
    ).where(ServiceGuide.code == seed.guide_code)
    statement = insert(GuideApplicability).from_select(
        ["guide_id", "campus_code", "student_type", "notes"], source
    )
    return statement.on_conflict_do_update(
        index_elements=[
            GuideApplicability.guide_id,
            GuideApplicability.campus_code,
            GuideApplicability.student_type,
        ],
        set_={"notes": statement.excluded.notes},
    )


def _guide_material_upsert_statement(seed: GuideMaterialSeed):
    source = select(
        literal(seed.id),
        ServiceGuide.id,
        literal(seed.name),
        literal(seed.description),
        literal(seed.required),
        literal(seed.copies),
        literal(seed.condition, type_=JSONB()),
        literal(seed.sort_order),
    ).where(ServiceGuide.code == seed.guide_code)
    statement = insert(GuideMaterial).from_select(
        [
            "id", "guide_id", "name", "description", "required", "copies", "condition",
            "sort_order",
        ],
        source,
    )
    return statement.on_conflict_do_update(
        index_elements=[GuideMaterial.id],
        set_={
            "guide_id": statement.excluded.guide_id,
            "name": statement.excluded.name,
            "description": statement.excluded.description,
            "required": statement.excluded.required,
            "copies": statement.excluded.copies,
            "condition": statement.excluded.condition,
            "sort_order": statement.excluded.sort_order,
        },
    )


def _guide_step_upsert_statement(seed: GuideStepSeed):
    source = select(
        literal(seed.id),
        ServiceGuide.id,
        literal(seed.step_no),
        literal(seed.title),
        literal(seed.description),
        literal(seed.location),
        literal(seed.estimated_minutes),
    ).where(ServiceGuide.code == seed.guide_code)
    statement = insert(GuideStep).from_select(
        [
            "id", "guide_id", "step_no", "title", "description", "location",
            "estimated_minutes",
        ],
        source,
    )
    return statement.on_conflict_do_update(
        index_elements=[GuideStep.guide_id, GuideStep.step_no],
        set_={
            "title": statement.excluded.title,
            "description": statement.excluded.description,
            "location": statement.excluded.location,
            "estimated_minutes": statement.excluded.estimated_minutes,
        },
    )


def _electricity_account_upsert_statement():
    statement = insert(ElectricityAccount).values(
        room_id=DEMO_ELECTRICITY_ROOM_ID,
        campus_code="main",
        dormitory_area="演示宿舍区",
        building="A",
        room="101",
        balance=88.50,
        currency="CNY",
        source="mock",
        is_simulated=True,
    )
    return statement.on_conflict_do_update(
        index_elements=[ElectricityAccount.room_id],
        set_={
            "campus_code": statement.excluded.campus_code,
            "dormitory_area": statement.excluded.dormitory_area,
            "building": statement.excluded.building,
            "room": statement.excluded.room,
            "balance": statement.excluded.balance,
            "currency": statement.excluded.currency,
            "source": statement.excluded.source,
            "is_simulated": statement.excluded.is_simulated,
        },
    )


def _electricity_members_insert_statement():
    return (
        insert(ElectricityAccountMember)
        .from_select(
            ["room_id", "user_id", "member_role"],
            select(
                ElectricityAccount.room_id,
                User.id,
                text("'resident'"),
            )
            .join(User, true())
            .where(
                ElectricityAccount.room_id == DEMO_ELECTRICITY_ROOM_ID,
                User.username.in_(("student01", "student02")),
            ),
        )
        .on_conflict_do_nothing()
    )


async def seed_demo(
    session: AsyncSession,
    password: str,
    password_hasher: PasswordHasher | None = None,
) -> tuple[str, ...]:
    """Seed the M4 identity and RBAC demo baseline in one transaction."""

    hasher = password_hasher if password_hasher is not None else PasswordHasher()
    password_hashes = {
        account.username: hasher.hash(password) for account in DEMO_ACCOUNTS
    }
    role_codes = tuple(role.code for role in ROLES)
    usernames = tuple(account.username for account in DEMO_ACCOUNTS)

    async with session.begin():
        await session.execute(_permission_upsert_statement())
        await session.execute(_role_upsert_statement())
        await session.execute(_config_upsert_statement())
        await session.execute(_clear_role_permissions_statement(role_codes))
        for role_code, permission_codes in ROLE_PERMISSION_CODES.items():
            await session.execute(
                _role_permission_insert_statement(role_code, permission_codes)
            )

        for account in DEMO_ACCOUNTS:
            await session.execute(
                _user_upsert_statement(account, password_hashes[account.username])
            )
        await session.execute(_clear_user_roles_statement(usernames))
        for account in DEMO_ACCOUNTS:
            await session.execute(_user_role_insert_statement(account))
        await session.execute(_campus_upsert_statement())
        await session.execute(_department_upsert_statement())
        for seed in CONTACT_SEEDS:
            await session.execute(_contact_upsert_statement(seed))
        await session.execute(_guide_category_upsert_statement())
        for seed in SERVICE_GUIDE_SEEDS:
            await session.execute(_service_guide_upsert_statement(seed))
        for seed in GUIDE_APPLICABILITY_SEEDS:
            await session.execute(_guide_applicability_upsert_statement(seed))
        for seed in GUIDE_MATERIAL_SEEDS:
            await session.execute(_guide_material_upsert_statement(seed))
        for seed in GUIDE_STEP_SEEDS:
            await session.execute(_guide_step_upsert_statement(seed))
        await session.execute(_electricity_account_upsert_statement())
        await session.execute(_electricity_members_insert_statement())

    return usernames


async def _seed_from_settings(password: str) -> tuple[str, ...]:
    database = Database.from_settings()
    try:
        async with database.session() as session:
            return await seed_demo(session, password)
    finally:
        await database.dispose()


def format_seed_result(usernames: tuple[str, ...]) -> str:
    return f"Seeded demo accounts: {', '.join(usernames)}"


def main() -> None:
    password = require_demo_seed_password()
    usernames = asyncio.run(_seed_from_settings(password))
    print(format_seed_result(usernames))


if __name__ == "__main__":
    main()
