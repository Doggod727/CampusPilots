import asyncio
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete, literal, select, text, true
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import Database
from app.core.config import get_settings
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
    WorkOrder,
    WorkOrderEvent,
    WorkOrderRating,
)
from app.modules.community.encryption import CommunityCipher
from app.modules.community.models import (
    CampusEvent, EventRegistration, LostFoundClaim, LostFoundItem, LostFoundMatch, Topic,
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
    PermissionSeed("knowledge:read_all", "查看全部知识库", "ai_knowledge", "跨资源范围查看全部知识库"),
    PermissionSeed("knowledge:write_all", "管理全部知识库", "ai_knowledge", "跨资源范围管理全部知识库"),
    PermissionSeed("knowledge:publish", "发布知识文档", "ai_knowledge", "发布或停用可检索文档"),
    PermissionSeed("chat:use", "使用知识问答", "ai_knowledge", "创建本人会话并使用已授权知识库问答"),
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
        "knowledge:read_all",
        "knowledge:write_all",
        "knowledge:publish",
        "chat:use",
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
        "knowledge:read",
        "chat:use",
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
        "后勤保障部",
        "service_staff",
    ),
    DemoAccount(
        "community01",
        "社区运营员",
        "community01@example.edu",
        "党委学生工作部（处）",
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
    ("community.post_max_chars", "community", 5000, "integer", "帖子正文最大字符数"),
    ("community.comment_max_chars", "community", 1000, "integer", "评论正文最大字符数"),
    ("community.event_max_capacity", "community", 10000, "integer", "活动容量硬上限"),
    ("community.match.category_weight", "community", 0.35, "number", "失物匹配类别权重"),
    ("community.match.location_weight", "community", 0.25, "number", "失物匹配地点权重"),
    ("community.match.time_weight", "community", 0.20, "number", "失物匹配时间权重"),
    ("community.match.keyword_weight", "community", 0.20, "number", "失物匹配关键词权重"),
    ("community.match.threshold", "community", 0.55, "number", "失物候选最低匹配分"),
    ("community.match.time_window_days", "community", 30, "integer", "候选时间窗口天数"),
)

DEMO_TOPIC_SEEDS = (
    (UUID("74000000-0000-4000-8000-000000000001"), "campus-life", "校园生活", "校园生活交流", False, 10),
    (UUID("74000000-0000-4000-8000-000000000002"), "mutual-help", "互助问答", "同学互助与经验分享", False, 20),
    (UUID("74000000-0000-4000-8000-000000000003"), "tree-hole", "匿名树洞", "允许匿名发布的安全交流空间", True, 30),
)

DEMO_ELECTRICITY_ROOM_ID = "21000000-0000-4000-8000-000000000001"
DEMO_WORK_ORDER_IDS = (
    UUID("71000000-0000-4000-8000-000000000001"),
    UUID("71000000-0000-4000-8000-000000000002"),
    UUID("71000000-0000-4000-8000-000000000003"),
)
DEMO_WORK_ORDER_EVENT_IDS = tuple(
    UUID(f"72000000-0000-4000-8000-{number:012d}") for number in range(1, 9)
)
DEMO_WORK_ORDER_RATING_ID = UUID("73000000-0000-4000-8000-000000000001")
DEMO_EVENT_IDS = (
    UUID("75000000-0000-4000-8000-000000000001"),
    UUID("75000000-0000-4000-8000-000000000002"),
)
DEMO_LOST_FOUND_IDS = (
    UUID("76000000-0000-4000-8000-000000000001"),
    UUID("76000000-0000-4000-8000-000000000002"),
)
DEMO_LOST_FOUND_MATCH_ID = UUID("77000000-0000-4000-8000-000000000001")
DEMO_LOST_FOUND_CLAIM_ID = UUID("78000000-0000-4000-8000-000000000001")

_SCU_SEED_PATH = Path(__file__).resolve().parent / "data" / "scu" / "seed_data.json"


def _load_scu_seed_data(path: Path = _SCU_SEED_PATH) -> dict:
    """Load the Sichuan University public data snapshot (see data/scu/README.md)."""

    return json.loads(path.read_text(encoding="utf-8"))


_SCU = _load_scu_seed_data()

CAMPUS_SEEDS = tuple(
    CampusSeed(campus["code"], campus["name"], campus["address"], campus["sort_order"])
    for campus in _SCU["campuses"]
)

WORK_ORDER_SCOPES_CONFIG_KEY = "campus_service.work_order_service_scopes"

DEPARTMENT_SEEDS = tuple(
    DepartmentSeed(UUID(department["id"]), department["code"], department["name"], department["description"])
    for department in _SCU["departments"]
)

CONTACT_SEEDS = tuple(
    ContactSeed(
        UUID(contact["id"]),
        contact["department_code"],
        contact["campus_code"],
        contact["contact_name"],
        contact["office_name"],
        contact["phone"],
        contact["email"],
        contact["location"],
        contact["office_hours"],
    )
    for contact in _SCU["contacts"]
)

GUIDE_CATEGORY_SEEDS = tuple(
    GuideCategorySeed(UUID(category["id"]), category["code"], category["name"], category["sort_order"])
    for category in _SCU["guide_categories"]
)

SERVICE_GUIDE_SEEDS = tuple(
    ServiceGuideSeed(
        UUID(guide["id"]),
        guide["code"],
        guide["category_code"],
        guide["department_code"],
        guide["title"],
        guide["summary"],
        guide["location"],
        guide["service_hours"],
        guide["source_url"],
        date.fromisoformat(guide["valid_until"]),
    )
    for guide in _SCU["service_guides"]
)

GUIDE_APPLICABILITY_SEEDS = tuple(
    GuideApplicabilitySeed(
        applicability["guide_code"],
        applicability["campus_code"],
        applicability["student_type"],
        applicability["notes"],
    )
    for applicability in _SCU["guide_applicability"]
)

GUIDE_MATERIAL_SEEDS = tuple(
    GuideMaterialSeed(
        UUID(material["id"]),
        material["guide_code"],
        material["name"],
        material["description"],
        material["required"],
        material["copies"],
        material["condition"],
        material["sort_order"],
    )
    for material in _SCU["guide_materials"]
)

GUIDE_STEP_SEEDS = tuple(
    GuideStepSeed(
        UUID(step["id"]),
        step["guide_code"],
        step["step_no"],
        step["title"],
        step["description"],
        step["location"],
        step["estimated_minutes"],
    )
    for step in _SCU["guide_steps"]
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


def _work_order_scope_config_upsert_statement(service_user_id: UUID):
    statement = insert(AppConfig).values(
        key=WORK_ORDER_SCOPES_CONFIG_KEY,
        namespace="campus_service",
        value={
            "users": {
                str(service_user_id): [
                    {
                        "campus_code": "jiangan",
                        "dormitory_areas": ["西园", "东园"],
                    }
                ]
            }
        },
        value_type="json",
        description="工单处理员授权校区与宿舍区域",
        editable=True,
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
        campus_code="jiangan",
        dormitory_area="西园",
        building="6舍",
        room="301",
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


def _demo_user_id(username: str):
    return select(User.id).where(User.username == username).scalar_subquery()


def _demo_department_id(code: str):
    return select(Department.id).where(Department.code == code).scalar_subquery()


def _demo_work_order_upsert_statements():
    base_time = datetime(2026, 7, 1, 1, 0, tzinfo=timezone.utc)
    rows = (
        {
            "id": DEMO_WORK_ORDER_IDS[0], "order_no": "WO-DEMO-SUBMITTED",
            "created_by": _demo_user_id("student01"), "status": "submitted",
            "assigned_to": None, "assigned_department_id": None, "version": 1,
            "accepted_at": None, "processing_at": None, "completed_at": None,
            "completion_note": None,
        },
        {
            "id": DEMO_WORK_ORDER_IDS[1], "order_no": "WO-DEMO-PROCESSING",
            "created_by": _demo_user_id("student02"), "status": "processing",
            "assigned_to": _demo_user_id("service01"),
            "assigned_department_id": _demo_department_id("logistics"), "version": 3,
            "accepted_at": base_time + timedelta(hours=1),
            "processing_at": base_time + timedelta(hours=2), "completed_at": None,
            "completion_note": None,
        },
        {
            "id": DEMO_WORK_ORDER_IDS[2], "order_no": "WO-DEMO-COMPLETED",
            "created_by": _demo_user_id("student01"), "status": "completed",
            "assigned_to": _demo_user_id("service01"),
            "assigned_department_id": _demo_department_id("logistics"), "version": 4,
            "accepted_at": base_time + timedelta(hours=1),
            "processing_at": base_time + timedelta(hours=2),
            "completed_at": base_time + timedelta(hours=4),
            "completion_note": "演示工单已完成。",
        },
    )
    statements = []
    for index, row in enumerate(rows):
        submitted_at = base_time + timedelta(days=index)
        statement = insert(WorkOrder).values(
            **row,
            campus_code="jiangan",
            dormitory_area="西园",
            building="6舍",
            room="301",
            fault_category=("network" if index == 1 else "electric"),
            description="用于校园服务中心验收的固定演示报修工单。",
            preferred_start_at=submitted_at + timedelta(days=1),
            preferred_end_at=submitted_at + timedelta(days=1, hours=2),
            rejection_reason=None,
            cancelled_at=None,
            rejected_at=None,
            submitted_at=submitted_at,
            created_at=submitted_at,
            updated_at=row["completed_at"] or row["processing_at"] or submitted_at,
        )
        statements.append(statement.on_conflict_do_update(
            index_elements=[WorkOrder.id],
            set_={column: getattr(statement.excluded, column) for column in (
                "order_no", "created_by", "campus_code", "dormitory_area", "building",
                "room", "fault_category", "description", "preferred_start_at",
                "preferred_end_at", "status", "assigned_to", "assigned_department_id",
                "rejection_reason", "completion_note", "version", "submitted_at",
                "accepted_at", "processing_at", "completed_at", "cancelled_at",
                "rejected_at", "created_at", "updated_at",
            )},
        ))
    return tuple(statements)


def _demo_work_order_event_upsert_statements():
    base_time = datetime(2026, 7, 1, 1, 0, tzinfo=timezone.utc)
    paths = (
        (DEMO_WORK_ORDER_IDS[0], "student01", ("submitted",)),
        (DEMO_WORK_ORDER_IDS[1], "student02", ("submitted", "accepted", "processing")),
        (DEMO_WORK_ORDER_IDS[2], "student01", ("submitted", "accepted", "processing", "completed")),
    )
    statements = []
    event_index = 0
    for order_id, owner, statuses in paths:
        previous = None
        for sequence_no, status in enumerate(statuses, 1):
            actor = owner if status == "submitted" else "service01"
            statement = insert(WorkOrderEvent).values(
                id=DEMO_WORK_ORDER_EVENT_IDS[event_index],
                work_order_id=order_id,
                sequence_no=sequence_no,
                event_type=status,
                from_status=previous,
                to_status=status,
                actor_user_id=_demo_user_id(actor),
                actor_role="student" if status == "submitted" else "service_staff",
                reason=None,
                snapshot={"work_order_id": str(order_id), "status": status, "version": sequence_no},
                created_at=base_time + timedelta(days=event_index),
            )
            statements.append(statement.on_conflict_do_update(
                index_elements=[WorkOrderEvent.id],
                set_={column: getattr(statement.excluded, column) for column in (
                    "work_order_id", "sequence_no", "event_type", "from_status", "to_status",
                    "actor_user_id", "actor_role", "reason", "snapshot", "created_at",
                )},
            ))
            event_index += 1
            previous = status
    return tuple(statements)


def _demo_work_order_rating_upsert_statement():
    statement = insert(WorkOrderRating).values(
        id=DEMO_WORK_ORDER_RATING_ID,
        work_order_id=DEMO_WORK_ORDER_IDS[2],
        user_id=_demo_user_id("student01"),
        score=5,
        comment="演示评价：处理及时。",
        created_at=datetime(2026, 7, 4, 1, 0, tzinfo=timezone.utc),
    )
    return statement.on_conflict_do_update(
        index_elements=[WorkOrderRating.id],
        set_={
            "work_order_id": statement.excluded.work_order_id,
            "user_id": statement.excluded.user_id,
            "score": statement.excluded.score,
            "comment": statement.excluded.comment,
            "created_at": statement.excluded.created_at,
        },
    )


def _demo_topic_upsert_statement(seed: tuple[UUID, str, str, str, bool, int]):
    topic_id, code, name, description, allow_anonymous, sort_order = seed
    statement = insert(Topic).values(
        id=topic_id,
        code=code,
        name=name,
        description=description,
        allow_anonymous=allow_anonymous,
        sort_order=sort_order,
        status="active",
        created_by=select(User.id).where(User.username == "community01").scalar_subquery(),
        version=1,
    )
    return statement.on_conflict_do_update(
        index_elements=[Topic.id],
        set_={
            "code": statement.excluded.code,
            "name": statement.excluded.name,
            "description": statement.excluded.description,
            "allow_anonymous": statement.excluded.allow_anonymous,
            "sort_order": statement.excluded.sort_order,
            "status": statement.excluded.status,
            "created_by": statement.excluded.created_by,
            "version": statement.excluded.version,
            "deleted_at": None,
        },
    )


def _demo_event_upsert_statements():
    values = (
        (DEMO_EVENT_IDS[0], "校园志愿服务日", "参与校园公共空间整理与志愿服务。", "volunteer", "江安校区青春广场", 40),
        (DEMO_EVENT_IDS[1], "社团开放体验", "面向全校同学的社团展示与体验活动。", "club", "江安校区学生活动中心", 80),
    )
    statements = []
    for index, (event_id, title, description, category, location, capacity) in enumerate(values):
        starts = datetime(2026, 8, 20 + index, 1, tzinfo=timezone.utc)
        statement = insert(CampusEvent).values(id=event_id,
            organizer_user_id=_demo_user_id("community01"), title=title,
            description_markdown=description, category=category, location=location,
            starts_at=starts, ends_at=starts + timedelta(hours=3),
            registration_deadline=starts - timedelta(days=1), capacity=capacity,
            registered_count=1 if index == 0 else 0, status="published", risk_level="low",
            moderation_case_id=None, moderation_policy_version="seed-v1",
            cancellation_reason=None, published_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
            version=1, created_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
            updated_at=datetime(2026, 7, 16, tzinfo=timezone.utc), deleted_at=None)
        statements.append(statement.on_conflict_do_update(index_elements=[CampusEvent.id],
            set_={column: getattr(statement.excluded, column) for column in (
                "organizer_user_id", "title", "description_markdown", "category", "location",
                "starts_at", "ends_at", "registration_deadline", "capacity", "registered_count",
                "status", "risk_level", "moderation_case_id", "moderation_policy_version",
                "cancellation_reason", "published_at", "version", "updated_at", "deleted_at") }))
    return tuple(statements)


def _demo_registration_upsert_statement():
    statement = insert(EventRegistration).values(event_id=DEMO_EVENT_IDS[0],
        user_id=_demo_user_id("student01"), status="registered",
        registered_at=datetime(2026, 7, 16, 2, tzinfo=timezone.utc),
        cancelled_at=None, updated_at=datetime(2026, 7, 16, 2, tzinfo=timezone.utc))
    return statement.on_conflict_do_update(
        index_elements=[EventRegistration.event_id, EventRegistration.user_id],
        set_={"status": statement.excluded.status, "registered_at": statement.excluded.registered_at,
              "cancelled_at": None, "updated_at": statement.excluded.updated_at})


def _demo_sensitive_lost_found_statements(key):
    cipher = CommunityCipher(key)
    occurred = datetime(2026, 7, 15, 6, tzinfo=timezone.utc)
    item_values = (
        (DEMO_LOST_FOUND_IDS[0], "student01", "lost", "黑色学生卡套", "claiming", "站内联系：student01"),
        (DEMO_LOST_FOUND_IDS[1], "student02", "found", "拾到黑色卡套", "published", "站内联系：student02"),
    )
    statements = []
    for item_id, username, item_type, title, status, contact in item_values:
        statement = insert(LostFoundItem).values(id=item_id, owner_user_id=_demo_user_id(username),
            item_type=item_type, title=title, category="card",
            description="黑色卡套，内有校园卡相关物品。", occurred_at=occurred,
            location="江安校区图书馆一楼", contact_type="other",
            contact_ciphertext=cipher.encrypt(contact), contact_hint=f"***{username[-4:]}",
            status=status, risk_level="low", moderation_case_id=None,
            moderation_policy_version="seed-v1", published_at=occurred,
            completed_at=None, version=1, created_at=occurred, updated_at=occurred,
            deleted_at=None)
        statements.append(statement.on_conflict_do_update(index_elements=[LostFoundItem.id],
            set_={column: getattr(statement.excluded, column) for column in (
                "owner_user_id", "item_type", "title", "category", "description", "occurred_at",
                "location", "contact_type", "contact_ciphertext", "contact_hint", "status",
                "risk_level", "moderation_case_id", "moderation_policy_version", "published_at",
                "completed_at", "version", "updated_at", "deleted_at") }))
    match = insert(LostFoundMatch).values(id=DEMO_LOST_FOUND_MATCH_ID,
        source_item_id=DEMO_LOST_FOUND_IDS[0], candidate_item_id=DEMO_LOST_FOUND_IDS[1],
        score=0.95, reasons=[
            {"factor": "category", "score": 1.0, "explanation": "类别一致度"},
            {"factor": "location", "score": 1.0, "explanation": "地点相似度"},
            {"factor": "time", "score": 1.0, "explanation": "发生时间接近度"},
            {"factor": "keyword", "score": 0.75, "explanation": "描述关键词相似度"}],
        algorithm_version="rule-v1", created_at=occurred)
    statements.append(match.on_conflict_do_update(index_elements=[LostFoundMatch.id],
        set_={"source_item_id": match.excluded.source_item_id,
              "candidate_item_id": match.excluded.candidate_item_id, "score": match.excluded.score,
              "reasons": match.excluded.reasons, "algorithm_version": match.excluded.algorithm_version}))
    claim = insert(LostFoundClaim).values(id=DEMO_LOST_FOUND_CLAIM_ID,
        target_item_id=DEMO_LOST_FOUND_IDS[0], claimant_item_id=DEMO_LOST_FOUND_IDS[1],
        claimant_user_id=_demo_user_id("student02"),
        evidence_ciphertext=cipher.encrypt("卡套内有本人姓名缩写和校园卡。"), status="pending",
        decision_reason=None, decided_by=None, decided_at=None,
        claimant_confirmed_at=None, owner_confirmed_at=None, completed_at=None,
        version=1, created_at=occurred, updated_at=occurred)
    statements.append(claim.on_conflict_do_update(index_elements=[LostFoundClaim.id],
        set_={column: getattr(claim.excluded, column) for column in (
            "target_item_id", "claimant_item_id", "claimant_user_id", "evidence_ciphertext",
            "status", "decision_reason", "decided_by", "decided_at", "claimant_confirmed_at",
            "owner_confirmed_at", "completed_at", "version", "updated_at") }))
    return tuple(statements)
async def seed_demo(
    session: AsyncSession,
    password: str,
    password_hasher: PasswordHasher | None = None,
    community_encryption_key=None,
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
        service_user_id = (
            await session.execute(select(User.id).where(User.username == "service01"))
        ).scalar_one()
        await session.execute(
            _work_order_scope_config_upsert_statement(service_user_id)
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
        for statement in _demo_work_order_upsert_statements():
            await session.execute(statement)
        for statement in _demo_work_order_event_upsert_statements():
            await session.execute(statement)
        await session.execute(_demo_work_order_rating_upsert_statement())
        for seed in DEMO_TOPIC_SEEDS:
            await session.execute(_demo_topic_upsert_statement(seed))
        for statement in _demo_event_upsert_statements():
            await session.execute(statement)
        await session.execute(_demo_registration_upsert_statement())
        if community_encryption_key is not None:
            for statement in _demo_sensitive_lost_found_statements(community_encryption_key):
                await session.execute(statement)

    return usernames


async def _seed_from_settings(password: str) -> tuple[str, ...]:
    settings = get_settings()
    database = Database.from_settings(settings)
    try:
        async with database.session() as session:
            return await seed_demo(session, password,
                community_encryption_key=settings.community_data_encryption_key)
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
