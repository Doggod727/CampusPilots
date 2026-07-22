from collections.abc import Iterable

from app.modules.agent_platform.domain.contracts import (
    AgentCatalogItem,
    AgentDefinition,
    AgentRegistration,
    AgentVersion,
)
from app.modules.agent_platform.orchestration.errors import (
    AgentDisabled,
    AgentNotFound,
    DuplicateAgentRegistration,
)


AGENT_CATALOG_POLICY: dict[str, tuple[str, tuple[str, ...]]] = {
    "supervisor": ("runtime_internal", ()),
    "knowledge_agent": ("public", ("chat:use",)),
    "service_agent": ("public", ("service:read",)),
    "community_agent": ("public", ("community:read",)),
    "governance_agent": ("runtime_internal", ()),
    "modelops_agent": ("restricted", ("model:read",)),
}


def _version_key(value: str) -> tuple[int, int, int]:
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


class AgentRegistry:
    def __init__(self, registrations: Iterable[AgentRegistration] = ()) -> None:
        self._definitions: dict[str, AgentDefinition] = {}
        self._versions: dict[tuple[str, str], AgentVersion] = {}
        for registration in registrations:
            self.register(registration)

    def register(self, registration: AgentRegistration) -> None:
        definition = registration.definition
        version = registration.version
        key = (definition.code, version.version)
        if key in self._versions:
            raise DuplicateAgentRegistration("agent code and version already registered")
        existing = self._definitions.get(definition.code)
        if existing is not None and existing != definition:
            raise DuplicateAgentRegistration("agent definition conflicts with registered code")
        if version.status == "active" and any(
            item.agent_code == definition.code and item.status == "active"
            for item in self._versions.values()
        ):
            raise DuplicateAgentRegistration("agent already has an active version")
        self._definitions[definition.code] = definition
        self._versions[key] = version

    def get_active(self, code: str) -> AgentRegistration:
        definition = self._definitions.get(code)
        if definition is None:
            raise AgentNotFound()
        if not definition.enabled:
            raise AgentDisabled()
        versions = [
            version for version in self._versions.values()
            if version.agent_code == code and version.status == "active"
        ]
        if not versions:
            raise AgentDisabled()
        version = max(versions, key=lambda item: _version_key(item.version))
        return AgentRegistration(definition=definition, version=version)

    def list_active(self) -> tuple[AgentRegistration, ...]:
        registrations: list[AgentRegistration] = []
        for code in sorted(self._definitions):
            try:
                registrations.append(self.get_active(code))
            except AgentDisabled:
                continue
        return tuple(registrations)

    def list_catalog(self) -> tuple[AgentCatalogItem, ...]:
        items: list[AgentCatalogItem] = []
        for item in self.list_active():
            visibility, required_permissions = AGENT_CATALOG_POLICY.get(
                item.definition.code, ("runtime_internal", ())
            )
            items.append(AgentCatalogItem(
                code=item.definition.code,
                name=item.definition.name,
                description=item.definition.description,
                version=item.version.version,
                enabled=item.definition.enabled,
                tool_allowlist=item.version.tool_allowlist,
                visibility=visibility,
                required_permissions=required_permissions,
            ))
        return tuple(items)

    def list_visible_catalog(self, permissions: Iterable[str]) -> tuple[AgentCatalogItem, ...]:
        granted = frozenset(permissions)
        return tuple(
            item for item in self.list_catalog()
            if item.visibility != "runtime_internal"
            and set(item.required_permissions).issubset(granted)
        )


_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["status", "data"],
    "properties": {
        "status": {
            "enum": ["succeeded", "failed", "needs_approval"]
        },
        "data": {"type": "object"},
    },
}
_PROMPT = (
    "只执行职责范围内任务；不得绕过权限、人工确认和审计；"
    "输出必须符合结构化契约。"
)


def _registration(
    code: str,
    name: str,
    description: str,
    tools: tuple[str, ...],
) -> AgentRegistration:
    return AgentRegistration.model_validate({
        "definition": {
            "code": code,
            "name": name,
            "description": description,
            "enabled": True,
        },
        "version": {
            "agent_code": code,
            "version": "1.0.0",
            "system_prompt": _PROMPT,
            "output_schema": _OUTPUT_SCHEMA,
            "tool_allowlist": tools,
            "status": "active",
        },
    })


AGENT_REGISTRATIONS: tuple[AgentRegistration, ...] = (
    _registration(
        "supervisor", "编排主管 Agent", "意图识别、任务拆分、路由与失败降级",
        ("governance.check_content", "governance.authorize_tool", "governance.write_audit"),
    ),
    _registration(
        "knowledge_agent", "知识问答 Agent", "知识检索、引用约束与复杂问答",
        ("knowledge.search", "knowledge.answer", "governance.check_content", "governance.write_audit"),
    ),
    _registration(
        "service_agent", "校园服务 Agent", "办事指南、报修、电费与模拟充值申请",
        ("service.get_guide", "work_order.create", "work_order.get", "electricity.get_balance", "electricity.create_topup_request", "governance.authorize_tool", "governance.write_audit"),
    ),
    _registration(
        "community_agent", "社区互助 Agent", "活动、报名、失物发布与匹配",
        ("event.search", "event.register", "lost_found.publish", "lost_found.search_matches", "governance.authorize_tool", "governance.write_audit"),
    ),
    _registration(
        "governance_agent", "治理 Agent", "内容审核、权限判定、确认与审计",
        ("governance.check_content", "governance.authorize_tool", "governance.write_audit"),
    ),
    _registration(
        "modelops_agent", "模型工程 Agent", "数据集、训练、评估与模型版本生命周期",
        (),
    ),
)
