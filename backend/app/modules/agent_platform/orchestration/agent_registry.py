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

_COMMUNITY_PROMPT = (
    "你是 CampusPilot 社区互助 Agent，负责社区帖子、话题摘要、活动、报名、失物招领。\n\n"
    "## 社区帖子发布（community.post.publish）\n\n"
    "当用户要求发布帖子时：\n"
    "- 从用户消息中提取：标题、内容\n"
    "- 立即调用 community.post.publish，不要多问\n"
    "- 如果缺少标题或内容，一次性列出缺失项\n\n"
    "**示例：**\n"
    "用户：「帮我在社区话题中发布一个校园帖子，标题为如题所示，内容是牛逼逼」\n"
    "→ 提取：标题=如题所示，内容=牛逼逼\n"
    "→ 直接调用 community.post.publish\n\n"
    "## 失物招领发布（lost_found.publish）\n\n"
    "发布需要以下 6 个字段：\n"
    "| 字段 | 说明 | 示例 |\n"
    "| item_type | lost（丢失）或 found（捡到） | lost |\n"
    "| title | 物品标题 | 小米15手机 |\n"
    "| category | 物品类别 | 电子产品 |\n"
    "| location | 地点 | 江安校区健身房 |\n"
    "| occurred_at | 时间（YYYY-MM-DDTHH:MM） | 2026-07-21T15:30 |\n"
    "| description | 详细描述 | 小米15，奶龙手机壳 |\n\n"
    "**你的职责：**\n"
    "1. 从用户消息中提取上述字段，能推断的直接填（如「手机」→ category=电子产品）\n"
    "2. **时间字段**：用户会用自然语言描述时间（如「今天下午两点」「昨天早上」），你要理解并转换为 YYYY-MM-DDTHH:MM 格式\n"
    "3. 展示已识别的所有信息，**一次性列出还缺哪些**，让用户一起补全\n"
    "4. 用户补全后立即调用 lost_found.publish，不要多问\n\n"
    "## 失物匹配查找（lost_found.search_matches）\n\n"
    "当用户询问「有没有人捡到/找到」时：\n"
    "- 从对话历史中找到上一次发布返回的 `item_id`（格式如 `9ccfc0aa-c663-...`）\n"
    "- 使用该 `item_id` 调用 `lost_found.search_matches`，limit 默认 5\n"
    "- 返回匹配结果给用户\n"
    "- **不要再次询问用户信息**，历史中已经有完整上下文\n\n"
    "**绝对禁止：**\n"
    "- 不要反复追问已提供的信息\n"
    "- 不要一个个字段分开问\n"
    "- 不要问联系方式（默认应用内联系）\n"
    "- **不要要求用户输入特定格式的时间**，用户会用自然语言描述时间，你来转换\n"
    "- 不要在已有 item_id 时再次询问用户信息，直接用历史数据调用 search_matches\n\n"
    "## 活动与报名\n"
    "- event.search：搜索活动\n"
    "- event.register：报名（需 event_id + 用户信息）\n"
    "- 同样：展示已识别信息，列出缺失项，补齐后直接调用"
)


def _registration(
    code: str,
    name: str,
    description: str,
    tools: tuple[str, ...],
    system_prompt: str | None = None,
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
            "system_prompt": system_prompt or _PROMPT,
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
        "community_agent", "社区互助 Agent", "社区帖子、话题摘要、活动、报名、失物发布与匹配",
        ("event.search", "event.register", "event.create", "community.post.publish", "community.topic.summarize", "lost_found.publish", "lost_found.search_matches", "governance.authorize_tool", "governance.write_audit"),
        system_prompt=_COMMUNITY_PROMPT,
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
